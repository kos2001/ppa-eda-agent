r"""Which measurement answers this failure — retrieved, not remembered.

case_retrieval finds prior cases that share a failure signature. That
tells an agent what happened before. It does not tell it what to *run*,
and this session measured why that matters: replaying three recorded
cases through the configured model scored grounded 3/3 and root-cause
recall 1/10. The answers were not badly reasoned. They were reasoned
over the only thing available — text someone had already written — when
every real advance in those cases came from running something new.

The gap is concrete. None of the eight agent definitions in
.claude/agents/ mentions a single one of this pipeline's tools, so an
agent asked to take the human-in-the-loop step has no way to learn that
`report_checks` against a chosen pin exists, let alone that the pin must
come from the max-slew violator list rather than the timing report.

So this indexes measurements the way case_retrieval indexes cases: keyed
by the same failure signatures, returning the tool that answers each,
the question it answers, and the trap that wastes the attempt.

DELIBERATELY HAND-WRITTEN. Every entry names the recorded case where the
measurement actually settled something, and nothing is here on the
strength of sounding plausible. That makes it small and auditable rather
than comprehensive — a retrieval layer that returned confident guidance
for failures nobody has debugged would be the same mistake as a
confident ungrounded diagnosis, one level up.

WHAT IT DOES NOT DO. It does not rank by similarity or embed anything.
Signature overlap is exact-match, for the reason recorded in
case_retrieval: an error code is already a precise key, and fuzzy
matching over precise keys only adds ways to be wrong.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import case_retrieval

REPO_ROOT = Path(__file__).resolve().parent.parent


# Each entry: what it applies to, what to run, what it answers, and what
# goes wrong. `design` is the case that put it on the record, and it is
# a field rather than something parsed out of `evidence` — leave-one-out
# used to read the prose's first word, so an entry whose evidence began
# with a variable name instead of a design could never be excluded and
# would have leaked that design's answer back into its own evaluation.
MEASUREMENTS: list[dict] = [
    {
        "id": "slew-path-trace",
        "design": "sram_wrapper",
        "when": ["max_slew_violation", "RSZ-0090"],
        "tool": "ppa_sta_path",
        "cli": "python3 pipeline/sta_path.py --design D --run-dir R --pin P",
        "answers": "Why one specific pin's slew is what it is — the cell at "
                   "each stage of the chain and which one adds the most.",
        "trap": "This is usually NOT the critical path. sram_wrapper had "
                "+18.57 ns of setup slack on the very path whose slew was 22x "
                "over its limit, so a pin taken from the timing report shows "
                "nothing wrong. Take the pin from ppa_sta_report's max-slew "
                "violator list.",
        "evidence": "sram_wrapper: found repair_design fixing a slew violation "
                    "with dlymetal6s2s_1, a delay cell, after five config "
                    "variables had been tried and all were null.",
    },
    {
        "id": "liberty-ceiling",
        "design": "sram_wrapper",
        "when": ["RSZ-0090"],
        "tool": "read the macro's .lib directly",
        "cli": "grep -o 'index_1(\"[^\"]*\")' <macro>.lib | sort -u",
        "answers": "Whether a max_transition limit is an electrical "
                   "requirement or just where characterisation stopped.",
        "trap": "RSZ-0090 is a feasibility precheck, not a violation report — "
                "it aborts before doing any work, whether or not a net "
                "violates. Sweeping placement or repair knobs cannot move it.",
        "evidence": "sram_wrapper: max_transition 0.04 equals the top of "
                    "index_1(\"0.00125, 0.005, 0.04\"), and sits on "
                    "addr0/addr1/wmask0 — inputs — not on dout0/dout1 as this "
                    "project's own record claimed for several sessions.",
    },
    {
        "id": "macro-power",
        "design": "sram_wrapper",
        "when": ["PDN-0231"],
        "tool": "read PDN_MACRO_CONNECTIONS in config.json",
        "cli": "python3 -c \"import json;print(json.load(open('config.json'))"
               "['PDN_MACRO_CONNECTIONS'])\"",
        "answers": "Whether the macro is actually connected to the power grid.",
        "trap": "The format is <instance> <vdd_net> <gnd_net> <vdd_pin> "
                "<gnd_pin> — net before pin. Swapped, it names nets the design "
                "does not have, connects nothing, and OpenLane only WARNs.",
        "evidence": "sram_wrapper: carried the macro's pin names in the net "
                    "slots for the life of the case; the macro had no power "
                    "connection in the generated PDN.",
    },
    {
        "id": "override-took-effect",
        "design": "sram_wrapper",
        "when": ["override_changed_nothing"],
        "tool": "read resolved.json and the generated SDC",
        "cli": "grep -n set_driving_cell <run>/*floorplan*/*.sdc",
        "answers": "Whether a config change reached the tool at all, before "
                   "concluding anything about what it does.",
        "trap": "Identical metrics do not prove a knob is inert. Confirm the "
                "artefact changed, then judge the effect.",
        "evidence": "SYNTH_CLK_DRIVING_CELL was recorded as 'byte-identical "
                    "results, so it does not reach the SDC'. It does reach it "
                    "— the SDC goes from inv_2/Y to clkbuf_16/X. The inference "
                    "was wrong because the SDC was never opened.",
    },
    {
        "id": "per-net-placement",
        "design": "sram_wrapper",
        "when": ["long_net_suspected", "GRT-0097"],
        "tool": "ppa_odb_query",
        "cli": "python3 pipeline/odb_query.py --design D --tag T",
        "answers": "One net's real pin count, HPWL and max span in microns.",
        "trap": "metrics.json aggregates cannot answer a question about one "
                "net, and a span that looks long may still be inside what the "
                "driver can hold — check the driver before moving anything.",
        "evidence": "sram_wrapper: addr1[7] measured 138.6 um, inside buf_12's "
                    "reach, which retired 'keep addr drivers within 145 um' as "
                    "a constraint that was already satisfied.",
    },
    {
        "id": "pdn-does-not-fit",
        "design": "counter4_tinydie",
        "when": ["PDN-0185", "pdn-strap-width"],
        "tool": "ppa_run_stage with a larger DIE_AREA",
        "cli": "python3 pipeline/run_stage.py --design D --tag T "
               "--override DIE_AREA=0,0,W,H",
        "answers": "Whether the core is simply too small for the power straps "
                   "the PDN wants to place.",
        "trap": "Reach for FP_CORE_UTIL first and there may be nothing to step "
                "down — a design using the default utilisation has no override "
                "to lower, and DIE_AREA is the knob that exists instead.",
        "evidence": "counter4_tinydie: the 16x16 um candidate cleared Floorplan "
                    "Init and then hit PDN-0185 with no FP_CORE_UTIL override "
                    "present; orchestrator.propose_repairs grows DIE_AREA for "
                    "exactly this signature.",
    },
    {
        "id": "timing-trustworthy",
        "design": "sram_wrapper",
        "when": ["macro_present", "max_slew_violation"],
        "tool": "ppa_verify_diagnosis / model_validity",
        "cli": "python3 pipeline/model_validity.py --design D --run-dir R",
        "answers": "Whether the STA numbers are measurements or extrapolation "
                   "off the end of a liberty table.",
        "trap": "Clean setup and hold prove nothing if the slews sit past the "
                "model's characterisation ceiling. Judge such a run by the "
                "model_validity flag, not by WNS.",
        "evidence": "sram_wrapper reported WNS +9.39 ns with zero violations "
                    "while its addr pins sat 22x past where the model stops.",
    },
]

# Symptoms that are not error codes. Derived from the case rather than
# asserted, so a case that does not show one does not get its guidance.
def symptoms(case: dict) -> set[str]:
    """Signature-like labels for conditions no error code names."""
    found = set()
    text = json.dumps(case)
    if '"macro' in text.lower() or "MACROS" in text:
        found.add("macro_present")
    for iteration in case.get("iterations", []):
        results = iteration.get("results", [])
        errors = [r.get("error") or "" for r in results]
        if any("max slew" in e.lower() or "RSZ-0090" in e for e in errors):
            found.add("max_slew_violation")
        # Two candidates with different overrides and identical text is
        # the shape of a knob that did nothing.
        seen = {}
        for r in results:
            key = (r.get("error") or json.dumps(r.get("verdict") or {}))[:400]
            if key and key in seen and seen[key] != r.get("overrides"):
                found.add("override_changed_nothing")
            seen[key] = r.get("overrides")
    return found


def case_keys(case: dict) -> set[str]:
    """Everything this case can be matched on: real codes plus symptoms."""
    errors, _tags = _recorded_errors(case)
    return case_retrieval.signatures(errors) | symptoms(case)


def _recorded_errors(case: dict) -> tuple[str, set]:
    errors, tags = [], set()
    for iteration in case.get("iterations", []):
        for result in iteration.get("results", []):
            tags.add(result.get("tag"))
            if result.get("error"):
                errors.append(result["error"])
    tags.discard(None)
    return "\n".join(errors), tags


def retrieve(case: dict, exclude_design: str | None = None) -> list[dict]:
    """Measurements that apply to this case, most specific first.

    Specificity is how many of an entry's own keys the case matched — an
    entry that names two conditions and matched both is a better fit
    than one that names two and matched one.

    `exclude_design` drops entries whose evidence comes from that
    design. Every entry here is grounded in a case this pipeline
    actually resolved, which means for that case the entry *is* the
    answer — handing it back would measure reading comprehension. The
    same leave-one-out discipline surrogate.py already applies to its
    own training data, for the same reason.
    """
    keys = case_keys(case)
    hits = []
    for entry in MEASUREMENTS:
        if exclude_design and entry["design"] == exclude_design:
            continue
        shared = sorted(set(entry["when"]) & keys)
        if shared:
            hits.append({**entry, "matched": shared,
                         "specificity": len(shared) / len(entry["when"])})
    hits.sort(key=lambda e: (-e["specificity"], -len(e["matched"]), e["id"]))
    return hits


def guidance_block(case: dict, exclude_design: str | None = None) -> str:
    """The retrieved measurements as markdown for a review request."""
    hits = retrieve(case, exclude_design)
    if not hits:
        # Two different emptinesses, and conflating them would hide the
        # more interesting one. Nothing matched at all means this shape
        # of failure is new. Everything matched but came from this same
        # design means the index has learned only from here and has
        # nothing to transfer yet — the small-corpus limit surrogate.py
        # hits too, stated rather than shown as a blank.
        if exclude_design and retrieve(case):
            return ("## Measurements that apply\n\n"
                    f"Every recorded measurement matching this case's failure "
                    f"signature was learned from {exclude_design} itself, so "
                    f"none is offered here. The index has no transferable "
                    f"guidance for this failure yet: it becomes useful the "
                    f"first time a *different* design hits the same "
                    f"signature.\n")
        return ("## Measurements that apply\n\n"
                "No recorded measurement matches this case's failure "
                "signature. That is a real answer, not an empty one: nothing "
                "here has debugged this shape of failure before, so reach for "
                "a tool deliberately rather than by analogy.\n")

    lines = ["## Measurements that apply (retrieved from what actually worked)",
             "",
             f"{len(hits)} of the recorded measurements match this case's "
             f"failure signature. Each names the trap that wastes the attempt, "
             f"because in every instance below the trap is what a previous "
             f"session actually fell into.",
             ""]
    for hit in hits:
        lines += [
            f"### {hit['tool']}  (matched {', '.join(hit['matched'])})",
            "",
            f"- **answers**: {hit['answers']}",
            f"- **run**: `{hit['cli']}`",
            f"- **trap**: {hit['trap']}",
            f"- **on the record**: {hit['evidence']}",
            "",
        ]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", help="latest case for this design")
    ap.add_argument("--case", type=Path, help="a specific case file")
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    if args.case:
        path = args.case
    elif args.design:
        hits = sorted((REPO_ROOT / "reference-db" / "cases")
                      .glob(f"{args.design}__*.json"))
        if not hits:
            raise SystemExit(f"no recorded case for {args.design}")
        path = hits[-1]
    else:
        raise SystemExit("--design or --case required")

    case = json.loads(path.read_text())
    if args.markdown:
        print(guidance_block(case))
    else:
        print(json.dumps({"case": path.name,
                          "keys": sorted(case_keys(case)),
                          "measurements": retrieve(case)}, indent=2))


if __name__ == "__main__":
    main()
