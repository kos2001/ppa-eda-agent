r"""Retrieval over reference-db, to ground a review in what happened before.

The human-in-the-loop review request contains the stuck design's own case
and nothing else. So every review starts cold: a reader — human or model
— is asked to judge an `RSZ-0090` failure with no sight of the four other
times this pipeline hit `RSZ-0090`, what was tried, and what the
measurement showed. The evidence exists in reference-db; it just never
reaches the prompt.

This is retrieval-augmented generation in the useful sense, and
deliberately *not* the embedding-and-vector-store sense. The corpus is
about ten structured JSON cases whose failures are labelled by the tools
themselves — `RSZ-0090`, `PDN-0185`, `GRT-`, `DRT-`. Exact matching on
those codes is more precise than a similarity score over prose, needs no
model, no index to rebuild and no dependency, and it can say *why* a case
was retrieved. An embedding index here would be slower to build, harder
to justify, and worse at the one thing that matters: finding the case
that failed the same way.

Ranking is by shared error codes first, then topology, because two runs
that produced the same tool error are related in a way that two runs of
similarly-shaped designs are not.

Usage:
    case_retrieval.py --design sram_wrapper [--top 3]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"

# Tool error codes as the tools themselves write them. Matching these
# rather than prose is the whole point: "RSZ-0090" means one specific
# failure, where "transition time problem" could mean several.
_CODE = re.compile(r"\b((?:RSZ|PDN|GRT|DRT|ANT|CTS|ORD|STA|DPL|GPL|MPL)-\d{4})\b")
# Some failures announce themselves by step name rather than a code.
_NAMED = (
    ("core_area", "floorplan-core-area"),
    ("Insufficient width", "pdn-strap-width"),
    ("unplaced macros", "unplaced-macros"),
    ("not connected to any power/ground nets", "macro-power-unconnected"),
    ("placed on top of itself", "magic-self-overlap"),
    ("no step(s) with ID", "unknown-step-id"),
)


class RetrievalError(RuntimeError):
    pass


def signatures(text: str) -> set[str]:
    """Failure fingerprints in a blob of real tool output."""
    if not text:
        return set()
    found = set(_CODE.findall(text))
    for needle, label in _NAMED:
        if needle in text:
            found.add(label)
    return found


# A run that completes the flow and fails the signoff gate has no tool
# error to fingerprint — the tools all exited 0; it is score() that said
# no. Measured on the store (2026-09-10): 18 cases across aes, gcd and
# riscv32i had no signature at all, so every review of them started
# cold, while gcd's own clk5 -> clk8 history was sitting there as the
# precedent for aes's setup problem. These name what the verdict
# counted, in the vocabulary score() writes; the matcher is a substring
# of the label so a reworded label around the same noun still matches.
_SIGNOFF_KINDS = (
    ("setup timing violation", "signoff:setup"),
    ("worst setup WNS", "signoff:setup"),
    ("hold timing violation", "signoff:hold"),
    ("worst hold WNS", "signoff:hold"),
    ("max-slew", "signoff:max-slew"),
    ("max-capacitance", "signoff:max-cap"),
    ("max-fanout", "signoff:max-fanout"),
    ("antenna violation", "signoff:antenna"),
    ("DRC error", "signoff:drc"),
    ("LVS", "signoff:lvs"),
    ("power-grid violation", "signoff:power-grid"),
    ("illegal layout overlap", "signoff:overlap"),
    ("XOR difference", "signoff:xor"),
    ("unmapped instance", "signoff:unmapped"),
    ("disconnected pin", "signoff:disconnected"),
    ("synthesis check error", "signoff:synth-check"),
    ("lint error", "signoff:lint"),
    ("utilization", "signoff:utilization"),
    ("IR drop", "signoff:ir-drop"),
)


def signoff_signatures(verdict: dict | None) -> set[str]:
    """Fingerprints of a gate rejection: one per kind of check the
    verdict counted against the candidate. A passing verdict, or one
    with no violations, yields nothing."""
    found = set()
    for v in (verdict or {}).get("violations", []) or []:
        for needle, label in _SIGNOFF_KINDS:
            if needle in v:
                found.add(label)
    return found


def case_signatures(case: dict) -> set[str]:
    """Every fingerprint anywhere in a case — candidate errors, gate
    rejections and the recorded diagnosis alike, since a diagnosis
    usually quotes the code that caused it."""
    found = set()
    for iteration in case.get("iterations", []):
        for result in iteration.get("results", []):
            found |= signatures(result.get("error", ""))
            found |= signoff_signatures(result.get("verdict"))
    found |= signatures(case.get("diagnosis", "") or "")
    return found


def load_cases(refdb: Path | str = REFDB) -> list[dict]:
    cases_dir = Path(refdb) / "cases"
    if not cases_dir.is_dir():
        raise RetrievalError(f"no cases directory at {cases_dir}")
    out = []
    for path in sorted(cases_dir.glob("*.json")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        case["_file"] = path.relative_to(Path(refdb).parent).as_posix()
        out.append(case)
    return out


def topology_distance(a: dict | None, b: dict | None) -> float | None:
    """How unlike two designs are structurally, 0 (same) to 1.

    None when either side has no topology recorded — an unknown is not a
    similarity, and scoring it as one would rank unlabelled cases above
    real matches.
    """
    if not a or not b:
        return None
    keys = ("module_count", "clock_domain_count", "port_count",
            "sequential_element_estimate", "power_domain_count")
    diffs = []
    for k in keys:
        x, y = a.get(k), b.get(k)
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            scale = max(abs(x), abs(y), 1)
            diffs.append(abs(x - y) / scale)
    if a.get("has_macros") is not None and b.get("has_macros") is not None:
        diffs.append(0.0 if a["has_macros"] == b["has_macros"] else 1.0)
    return sum(diffs) / len(diffs) if diffs else None


def similar(target: dict, corpus: list[dict], top: int = 3) -> list[dict]:
    """Prior cases most likely to inform this one, best first.

    Never returns the target itself, and never returns a case with
    nothing in common — an empty result is the honest answer to "has this
    happened before?" when it has not.
    """
    tgt_sigs = case_signatures(target)
    tgt_topo = target.get("topology")
    scored = []
    for case in corpus:
        if case.get("design") == target.get("design") and case.get("date") == target.get("date"):
            continue
        shared = tgt_sigs & case_signatures(case)
        topo = topology_distance(tgt_topo, case.get("topology"))
        # A shared tool error outranks any structural resemblance: two
        # runs that failed the same way are related in a way two
        # similarly-shaped designs are not.
        if not shared and (topo is None or topo > 0.35):
            continue
        scored.append({
            "design": case.get("design"),
            "date": case.get("date"),
            "file": case.get("_file"),
            "shared_signatures": sorted(shared),
            "topology_distance": None if topo is None else round(topo, 3),
            "outcome": case.get("outcome"),
            "winner_tag": case.get("winner_tag"),
            "stop_reason": case.get("stop_reason"),
            "diagnosis": case.get("diagnosis"),
            "reviews": [r.get("agent") for r in case.get("human_in_the_loop", []) or []],
            # Among cases that failed the same way, one that was
            # afterwards resolved is worth more than one still open: it
            # carries what actually worked, where the open one carries
            # only what was tried. Signature count still dominates —
            # a resolved case that failed differently is not precedent.
            "_rank": (
                -len(shared),
                0 if case.get("winner_tag") else 1,
                topo if topo is not None else 1.0,
            ),
        })
    scored.sort(key=lambda c: c["_rank"])
    for c in scored:
        c.pop("_rank", None)
    return scored[:top]

# How much of a prior diagnosis to inline. Whole diagnoses in this store
# run to 13 KB; pasting several would bury the current case's own
# evidence in the prompt, which is the opposite of grounding it.
EXCERPT_CHARS = 900


_COUNTED = re.compile(r"^(\d+)\s+(.*)$")


def signoff_counts(verdict: dict | None) -> dict[str, int]:
    """Count per signoff kind from a verdict's violations — the same
    kinds signoff_signatures() names, with the number the gate counted."""
    out: dict[str, int] = {}
    for v in (verdict or {}).get("violations", []) or []:
        m = _COUNTED.match(v)
        if not m:
            continue
        for needle, label in _SIGNOFF_KINDS:
            if needle in m.group(2):
                out[label] = out.get(label, 0) + int(m.group(1))
                break
    return out


def best_candidate(case: dict) -> dict | None:
    """The candidate a case is judged by: a passing one if any, else the
    one the gate counted fewest violations against."""
    best = None
    for iteration in case.get("iterations", []):
        for result in iteration.get("results", []):
            v = result.get("verdict")
            if not v:
                continue
            total = -1 if v.get("passed") else sum(signoff_counts(v).values())
            if best is None or total < best[0]:
                best = (total, result)
    return best[1] if best else None


def closures(target: dict, corpus: list[dict]) -> dict:
    """For each signoff kind the target still fails, every place in the
    store where that kind went from failing to clean between two runs
    of one design — and, when there is none, that fact.

    This is the question a stuck review actually asks: not "which case
    looks most like mine" but "has anyone ever made THIS go away". The
    two differ. On 2026-09-10 aes's top-3 precedent was three other
    aes runs sharing three failures, while gcd's max-fanout closure
    (MAX_FANOUT_CONSTRAINT 12, one shared failure) never ranked. Kept
    apart from similar() rather than folded into its ranking, because
    a case that shares more failures is still the better whole-case
    precedent; this answers the per-failure question beside it.

    A closure records the overrides of the run that closed the kind,
    not a diff: the store keeps overrides as the difference from the
    design's own config.json, so they already are the knobs that run
    turned. Nothing is inferred about which of them did it.
    """
    best = best_candidate(target)
    remaining = signoff_counts(best.get("verdict") if best else None)
    by_design: dict[str, list[dict]] = {}
    for case in corpus:
        by_design.setdefault(case.get("design"), []).append(case)
    out: dict = {"remaining": remaining, "closed_elsewhere": {}, "never_closed": []}
    for kind in remaining:
        found = []
        for design, cases in by_design.items():
            seq = sorted(cases, key=lambda c: c.get("_file") or "")
            prev = None
            for case in seq:
                cand = best_candidate(case)
                if not cand:
                    continue
                counts = signoff_counts(cand.get("verdict"))
                if prev is not None and prev[1].get(kind, 0) > 0 and counts.get(kind, 0) == 0:
                    found.append({
                        "design": design,
                        "from_case": prev[0].get("_file"),
                        "from_count": prev[1][kind],
                        "to_case": case.get("_file"),
                        "candidate": cand.get("tag"),
                        "overrides": cand.get("overrides") or {},
                        "still_failing": {k: n for k, n in counts.items() if n},
                    })
                prev = (case, counts)
        if found:
            out["closed_elsewhere"][kind] = found
        else:
            out["never_closed"].append(kind)
    return out


def closures_block(target: dict, corpus: list[dict]) -> str:
    """The closures, as markdown for the review request."""
    c = closures(target, corpus)
    if not c["remaining"]:
        return ""
    lines = ["## What closed each remaining failure, anywhere in the store", ""]
    lines.append(
        "Per kind of check this case's best candidate still fails: every "
        "recorded run of any design where that kind went from failing to "
        "clean, with the overrides that run used. A kind with no closure "
        "anywhere is named as such — the store cannot advise on it, and "
        "the next step is a real run, not a search.")
    lines.append("")
    for kind, n in c["remaining"].items():
        hits = c["closed_elsewhere"].get(kind)
        if not hits:
            lines.append(f"- **{kind}** ({n} now): never closed in any recorded run of any design.")
            continue
        lines.append(f"- **{kind}** ({n} now): closed {len(hits)} time(s)")
        for h in hits:
            ov = ", ".join(f"{k}={json.dumps(v)}" for k, v in h["overrides"].items()) or "no overrides"
            left = (", ".join(f"{k} {v}" for k, v in h["still_failing"].items())
                    or "nothing else")
            lines.append(
                f"  - {h['design']}: {h['from_count']} -> 0 in `{h['candidate']}` "
                f"({ov}); still failing after: {left}  "
                f"[{h['from_case']} -> {h['to_case']}]")
    lines.append("")
    return "\n".join(lines)


def precedent_block(target: dict, corpus: list[dict], top: int = 3) -> str:
    """The retrieved precedent, as markdown for the review request."""
    hits = similar(target, corpus, top)
    if not hits:
        return ("## Precedent from reference-db\n\n"
                "No prior case shares this one's failure signature or "
                "topology. This appears to be new — treat it as such "
                "rather than reaching for a familiar fix.\n"
                + closures_block(target, corpus))

    lines = ["## Precedent from reference-db (retrieved, not assumed)", ""]
    lines.append(
        f"{len(hits)} prior case(s) matched. Each is real recorded output; "
        f"the match reason is stated so it can be discounted if it does "
        f"not actually apply.")
    lines.append("")
    for hit in hits:
        why = (f"shares {', '.join(hit['shared_signatures'])}"
               if hit["shared_signatures"]
               else f"similar topology (distance {hit['topology_distance']})")
        # Stated because it changes how the precedent should be read: a
        # resolved case shows what worked, an open one only what was
        # tried and did not.
        why += (" — RESOLVED" if hit["winner_tag"] else " — still open")
        lines += [
            f"### {hit['design']} — {hit['date']}  ({why})",
            "",
            f"- outcome: {hit['outcome']}",
            f"- stop reason: {hit['stop_reason']}",
            f"- winner: {hit['winner_tag'] or 'none'}",
        ]
        if hit["reviews"]:
            lines.append(f"- reviewed by: {', '.join(hit['reviews'])}")
        diagnosis = (hit["diagnosis"] or "").strip()
        if diagnosis:
            excerpt = diagnosis[:EXCERPT_CHARS]
            if len(diagnosis) > EXCERPT_CHARS:
                excerpt += f"\n\n[...truncated; full text in {hit['file']}]"
            lines += ["", "```", excerpt, "```"]
        lines.append("")
    closing = closures_block(target, corpus)
    if closing:
        lines += [closing]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True)
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    corpus = load_cases()
    matches = [c for c in corpus if c.get("design") == args.design]
    if not matches:
        raise SystemExit(f"no case for design {args.design!r}")
    target = sorted(matches, key=lambda c: c.get("date", ""))[-1]

    if args.markdown:
        print(precedent_block(target, corpus, args.top))
    else:
        hits = similar(target, corpus, args.top)
        # Trim the diagnosis field rather than the serialized blob —
        # slicing the JSON string produces invalid JSON, which is a
        # silly way to lose a result.
        for hit in hits:
            if hit.get("diagnosis"):
                hit["diagnosis"] = hit["diagnosis"][:EXCERPT_CHARS]
        print(json.dumps({
            "design": target["design"],
            "date": target["date"],
            "signatures": sorted(case_signatures(target)),
            "similar": hits,
        }, indent=2))


if __name__ == "__main__":
    main()
