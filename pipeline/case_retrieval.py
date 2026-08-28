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


def case_signatures(case: dict) -> set[str]:
    """Every fingerprint anywhere in a case — candidate errors and the
    recorded diagnosis alike, since a diagnosis usually quotes the code
    that caused it."""
    found = set()
    for iteration in case.get("iterations", []):
        for result in iteration.get("results", []):
            found |= signatures(result.get("error", ""))
    found |= signatures(case.get("diagnosis", "") or "")
    return found


def load_cases(refdb: Path | str = REFDB) -> list[dict]:
    cases_dir = Path(refdb) / "cases"
    if not cases_dir.is_dir():
        raise RetrievalError(f"no cases directory at {cases_dir}")
    out = []
    for path in sorted(cases_dir.glob("*.json")):
        try:
            case = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        case["_file"] = str(path.relative_to(Path(refdb).parent))
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
            "_rank": (-len(shared), topo if topo is not None else 1.0),
        })
    scored.sort(key=lambda c: c["_rank"])
    for c in scored:
        c.pop("_rank", None)
    return scored[:top]

# How much of a prior diagnosis to inline. Whole diagnoses in this store
# run to 13 KB; pasting several would bury the current case's own
# evidence in the prompt, which is the opposite of grounding it.
EXCERPT_CHARS = 900


def precedent_block(target: dict, corpus: list[dict], top: int = 3) -> str:
    """The retrieved precedent, as markdown for the review request."""
    hits = similar(target, corpus, top)
    if not hits:
        return ("## Precedent from reference-db\n\n"
                "No prior case shares this one's failure signature or "
                "topology. This appears to be new — treat it as such "
                "rather than reaching for a familiar fix.\n")

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
