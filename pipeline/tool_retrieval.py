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
        "id": "which-pins-violate",
        "design": "sram_wrapper",
        "when": ["max_slew_violation", "RSZ-0090", "max_cap_violation"],
        "tool": "ppa_sta_report",
        "cli": "python3 pipeline/sta_report.py --design D --tag T",
        "answers": "Which pins violate max_slew / max_cap / max_fanout, per "
                   "corner, from the reports the run already wrote.",
        "trap": "Read it before ppa_sta_path, not after — the path trace needs "
                "an endpoint, and this is where the endpoint comes from. Also "
                "read the worst corner: sram_wrapper's worst pin was 0.545 ns "
                "at nom_tt and 0.880 ns at max_ss.",
        "evidence": "sram_wrapper: its max-slew violator list is what named "
                    "addr1[7] and addr0[3], the pins whose path trace then "
                    "found delay cells inserted as slew repair.",
    },
    {
        "id": "ask-sta-directly",
        "design": "sram_wrapper",
        "when": ["max_slew_violation", "RSZ-0090", "override_changed_nothing",
                 "max_cap_violation"],
        "tool": "ppa_sta_query",
        "cli": "python3 -c \"import sys;sys.path.insert(0,'pipeline');"
               "import sta_path;print(sta_path.query(D,R,'report_power')['output'])\"",
        "answers": "Anything OpenSTA can report about a completed run that no "
                   "tool here wraps — power by group, check types, a pin's "
                   "properties, the units the numbers are in.",
        "trap": "Reach for it when a wrapped tool nearly answers the question "
                "but not quite. Five config sweeps were run on sram_wrapper "
                "before anyone asked STA directly, and the direct question "
                "settled it in one command. Commands that modify are refused, "
                "so this cannot repair anything — only ask.",
        "evidence": "sram_wrapper: `report_checks -to u_sram/addr0[3]` showed "
                    "repair_design fixing a slew violation with delay cells. "
                    "That query existed in no tool until it was added as one, "
                    "which is the argument for a general way to ask.",
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
        "id": "netlist-still-the-rtl",
        "design": "sram_wrapper",
        "when": ["equivalence_doubt", "resizer_changed_netlist"],
        "tool": "ppa_equiv_check",
        "cli": "python3 pipeline/equiv_check.py --design D --tag T",
        "answers": "Whether the netlist after resizing and repair still "
                   "implements the RTL.",
        "trap": "Do not reach for OpenLane's RUN_EQY instead. It is False by "
                "default and enabling it aborts inside EQY itself (\"This "
                "should not happen. Please report this bug.\"), so gating on it "
                "marks every candidate unverified forever.",
        "evidence": "sram_wrapper: this proves the same design at 4 points with "
                    "0 unproven, on the design where EQY crashes.",
    },
    {
        "id": "see-the-layout",
        "design": "sram_wrapper",
        "when": ["macro_present", "placement_suspected"],
        "tool": "ppa_render_layout",
        "cli": "python3 pipeline/render_layout.py --design D --tag T",
        "answers": "The run's real rendered GDS, for a question about where "
                   "things physically ended up.",
        "trap": "An image is not a measurement — use it to form the question, "
                "then answer it with ppa_odb_query's numbers rather than by "
                "eye.",
        "evidence": "arxiv.org/html/2605.06936v3 measured that a layout image "
                    "improves diagnosis of real post-flow violations over text "
                    "alone; this pipeline stores the render in reference-db so "
                    "it survives the run directory being cleaned up.",
    },
    {
        "id": "hs-library-magic-drc",
        "design": "counter4",
        "when": ["scl_sky130_fd_sc_hs", "magic_drc_only"],
        "tool": "compare the two DRC engines before blaming the design",
        "cli": "grep -c 'um ' <run>/*magic-drc*/reports/drc_violations.magic.rpt",
        "answers": "Whether a DRC failure is the design's or the library's.",
        "trap": "Magic and KLayout disagree here, and only one of them is "
                "wrong. Read both counts and the per-cell ratio before "
                "changing anything: violations that scale with instance count "
                "are inside the cells, and no placement or routing change will "
                "move them.",
        "evidence": "counter4, cdc_twoclock and spm all reach step 74 on "
                    "sky130_fd_sc_hs and all fail the same rule, licon.11 "
                    "(diffusion contact to gate < 0.055um): Magic reports "
                    "21/48/282 while KLayout reports 0/0/0 and routing DRC is "
                    "0/0/0. That is 0.60, 0.52 and 0.57 violations per cell "
                    "instance across three unrelated designs.",
    },
    {
        "id": "hs-needs-a-bigger-die",
        "design": "counter4_tinydie",
        "when": ["scl_sky130_fd_sc_hs", "PDN-0185", "pdn-strap-width"],
        "tool": "re-sweep DIE_AREA rather than reusing the hd floorplan",
        "cli": "python3 pipeline/run_stage.py --design D --tag T "
               "--override DIE_AREA=0,0,W,H",
        "answers": "Whether a floorplan that worked for one library is "
                   "big enough for another.",
        "trap": "A die size carried over from sky130_fd_sc_hd will fail on "
                "sky130_fd_sc_hs without saying why the library is the "
                "reason. Sweep the size on the new library before concluding "
                "anything about the design.",
        "evidence": "counter4_tinydie: the smallest die that completes is "
                    "48um on hd and 56um on hs, and at every size that "
                    "completes on both, hs is about 50% larger (56um: 310.3 "
                    "vs 468.3; 96um: 392.9 vs 532.3).",
    },
    {
        "id": "unreadable-macro-gds",
        "design": "sram_wrapper",
        "when": ["magic_read_failure", "unknown_layer_datatype"],
        "tool": "compare the two layer maps before reaching for a knob",
        "cli": "grep -E '<layer>[[:space:]]+<type>' "
               "$PDK/libs.tech/magic/sky130A.tech "
               "$PDK/libs.tech/klayout/tech/sky130A.map",
        "answers": "Whether a Magic read error is a tool quirk or geometry no "
                   "sky130 tool configuration defines.",
        "trap": "MAGIC_CAPTURE_ERRORS=false looks like the fix, and OpenLane's "
                "own description invites it by saying the fatal determination "
                "is heuristic and not guaranteed. It is not the fix. Magic then "
                "streams out a GDS built from a macro it could not read, and "
                "the run reaches DRC and reports 2,831,364 violations. The "
                "abort was correct.",
        "evidence": "sram_wrapper: five layers (22/21, 22/22, 33/42, 33/43, "
                    "235/0) in the SRAM macro's GDS appear in neither Magic's "
                    "techfile nor KLayout's map. KLayout ignores unmapped "
                    "layers and streams out fine at step 57; Magic fails at 59. "
                    "MAGIC_MACRO_STD_CELL_SOURCE=PDK only moved the failure "
                    "from 61 to 59.",
    },
    {
        "id": "library-blocked-by-a-missing-file",
        "design": "counter4",
        "when": ["pnr_excluded_cell_file_invalid", "pdk_load_failure"],
        "tool": "pdk_repair",
        "cli": "python3 pipeline/pdk_repair.py --pdk <family>",
        "answers": "Whether a library is unusable because the PDK omitted a "
                   "file OpenLane resolves by convention.",
        "trap": "The error names PNR_EXCLUDED_CELL_FILE, a variable nobody "
                "set, and passing --override-config for it does not help: the "
                "path is validated while loading the PDK, before overrides "
                "apply. There is also no run directory to inspect.",
        "evidence": "gf180mcu_fd_sc_mcu9t5v0 ships no drc_exclude.cells while "
                    "its 7-track sibling does, in all four metal-stack "
                    "variants; sky130_fd_sc_hvl and sky130_osu_sc_t18 have the "
                    "same gap. Eight libraries across two PDKs were unusable. "
                    "With the file created the 9-track library completes all "
                    "78 stages.",
    },
    {
        "id": "library-comparison",
        "design": "counter4_tinydie",
        "when": ["technology_question"],
        "tool": "ppa_tech_compare",
        "cli": "python3 pipeline/tech_compare.py --design D",
        "answers": "The same design through two or more standard-cell "
                   "libraries, run for real.",
        "trap": "Do not compare libraries with --override-config "
                "STD_CELL_LIBRARY. OpenLane accepts it into resolved.json and "
                "ignores it, so the netlist keeps the default library's cells "
                "and the comparison reports a perfectly plausible 0.00% delta. "
                "The library is chosen by --scl.",
        "evidence": "run_stage.py's docstring records this as one of two "
                    "ignored-override false conclusions this project has "
                    "actually published.",
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
    for result in [r for it in case.get("iterations", []) for r in it.get("results", [])]:
        scl = result.get("scl")
        if scl and scl != "sky130_fd_sc_hd":
            found.add(f"scl_{scl}")
        err = result.get("error") or ""
        if "Unknown layer/datatype" in err:
            found.add("unknown_layer_datatype")
        if "fatal errors while running Magic" in err:
            found.add("magic_read_failure")
        if "PNR_EXCLUDED_CELL_FILE" in err and "invalid" in err:
            found.add("pnr_excluded_cell_file_invalid")
        if "Errors have occurred while loading the PDK" in err:
            found.add("pdk_load_failure")
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
