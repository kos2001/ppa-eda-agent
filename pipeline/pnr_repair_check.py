#!/usr/bin/env python3
r"""Does each pnr_repair rule fix the failure it was written for? Run it.

A rule that parses an error message is only a hypothesis until a repaired
candidate has been run. This replays, through the live orchestrate() loop,
the candidate that originally failed with each error, and records whether
the loop's own repair got it past that error.

Each SCENARIO is a candidate taken from reference-db (same design, same
overrides, same technology) that died with the named code. The replay is
the real flow: the failure is reproduced first, the repair is whatever
propose_repairs() proposes, and the repaired candidate is a full OpenLane
run. Nothing here is simulated, so a scenario that no longer reproduces
its failure says so instead of passing.

"Fixed" means only: the repaired candidate no longer dies with the code it
was repaired for. Whether it then passes signoff is reported separately
(`final_passed`) because getting past a placement error and meeting every
signoff check are different claims, and the rule is only responsible for
the first.

Usage (Docker + PDK; minutes per scenario):
    pnr_repair_check.py --out ../reference-db/pnr_repair_checks.json
    pnr_repair_check.py --only GPL-0302
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent

HS = {"PNR_EXCLUDED_CELL_FILE": "/design/pnr/hs_exclude.cells"}
GF180_9T = {"PNR_EXCLUDED_CELL_FILE": "/design/pnr/gf180_9t_exclude.cells"}
GF180 = {"pdk": "gf180mcuD"}

SCENARIOS = [
    {"code": "GPL-0302", "design": "counter4",
     "cand": {"tag": "dens40", "overrides": {"PL_TARGET_DENSITY_PCT": 40}}},
    {"code": "GPL-0307", "design": "counter4_tinydie",
     "cand": {"tag": "gf180-9t", **GF180, "scl": "gf180mcu_fd_sc_mcu9t5v0",
              "overrides": {"DIE_AREA": [0, 0, 128, 128], **GF180_9T}}},
    {"code": "GPL-0301", "design": "cdc_twoclock",
     "cand": {"tag": "gf180-7t", **GF180, "scl": "gf180mcu_fd_sc_mcu7t5v0",
              "overrides": {}}},
    {"code": "DPL-0036", "design": "counter4",
     "cand": {"tag": "gf180-7t-util45", **GF180, "scl": "gf180mcu_fd_sc_mcu7t5v0",
              "overrides": {"FP_CORE_UTIL": 45}}},
    {"code": "DPL-0036", "design": "gcd",
     "cand": {"tag": "hs-util65", "scl": "sky130_fd_sc_hs",
              "overrides": {"FP_CORE_UTIL": 65, **HS}}},
    {"code": "RSZ-0060", "design": "spm",
     "cand": {"tag": "hs-util55", "scl": "sky130_fd_sc_hs",
              "overrides": {"FP_CORE_UTIL": 55, **HS}}},
    # A negative control, kept because a table of only the rules that
    # worked would not show where the rule stops. At 4 ns gcd on the hs
    # library cannot close setup at all (WNS -0.29 ns after repair, RSZ-0062),
    # so the hold margin is not the cause: with it spent the run dies again
    # at RSZ-0060 with RSZ-0066 (hold unrepairable) instead of RSZ-0064, which
    # the rule correctly declines to answer with a bigger buffer cap.
    {"code": "RSZ-0060", "design": "gcd", "expect_fixed": False,
     "note": "4 ns is infeasible for gcd on sky130_fd_sc_hs; escalation is the right outcome",
     "cand": {"tag": "c-hs-clock_period4", "scl": "sky130_fd_sc_hs",
              "overrides": {"CLOCK_PERIOD": 4, **HS}}},
]

_CODE = re.compile(r"\b(?:GPL|DPL|RSZ|PDN|GRT|DRT|STA)-\d{4}\b")


def error_codes(error: str | None) -> list[str]:
    """Tool codes named on non-WARNING lines of a failure's output tail.
    The last one is the code the run died of."""
    return _CODE.findall("\n".join(
        ln for ln in (error or "").splitlines() if "WARNING" not in ln))


def judge(code: str, iterations: list[dict]) -> dict:
    """Reads an orchestrate() transcript for one scenario.

    reproduced   iteration 1 died with `code` (otherwise the scenario no
                 longer tests anything and nothing else here is evidence)
    repaired     a later iteration exists, i.e. a rule proposed something
    fixed        a repaired candidate did not die with `code`
    """
    steps = []
    for it in iterations:
        for r in it["results"]:
            v = r.get("verdict") or {}
            codes = error_codes(r.get("error"))
            steps.append({
                "iteration": it["iteration"], "tag": r["tag"],
                "overrides": r["overrides"], "repair": r.get("repair"),
                "died_with": codes[-1] if codes else None,
                "crashed": bool(r.get("error")),
                "passed": bool(v.get("passed")),
                "violations": v.get("violations", []),
                "unverified": v.get("unverified", []),
                "area_um2": v.get("area_um2"),
            })
    first = [s for s in steps if s["iteration"] == 1]
    later = [s for s in steps if s["iteration"] > 1]
    reproduced = bool(first) and all(s["died_with"] == code for s in first)
    return {
        "code": code,
        "reproduced": reproduced,
        "repaired": bool(later),
        "fixed": bool(later) and any(s["died_with"] != code for s in later),
        "final_passed": any(s["passed"] for s in later),
        "steps": steps,
    }


def run_scenario(sc: dict, max_iterations: int = 3) -> dict:
    import orchestrator
    design_dir = HERE / "designs" / sc["design"]
    spec = {"design_name": sc["design"], "targets": {"max_core_utilization": 0.75},
            "candidates": [sc["cand"]]}
    iterations, _winner, stop, _ = orchestrator.orchestrate(
        design_dir, spec, max_iterations, max_parallel=1)
    # Heavy per-run fields are not evidence for this question.
    for it in iterations:
        for r in it["results"]:
            r.pop("layout", None)
            r.pop("netlist", None)
    return {"design": sc["design"], "stop_reason": stop,
            "expect_fixed": sc.get("expect_fixed", True), "note": sc.get("note"),
            **judge(sc["code"], iterations)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="run only scenarios for this code")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--max-iterations", type=int, default=3)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()

    todo = [s for s in SCENARIOS if not args.only or s["code"] == args.only]
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        out = list(ex.map(lambda s: run_scenario(s, args.max_iterations), todo))
    text = json.dumps(out, indent=1)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    for o in out:
        verdict = "as expected" if o["fixed"] == o["expect_fixed"] else "UNEXPECTED"
        print(f"{o['code']:9s} {o['design']:18s} reproduced={o['reproduced']} "
              f"repaired={o['repaired']} fixed={o['fixed']} final_passed={o['final_passed']} "
              f"({verdict})")
        for s in o["steps"]:
            tail = s["died_with"] or ("PASS" if s["passed"] else "; ".join(s["violations"])[:80])
            print(f"    it{s['iteration']} {s['tag']}: {tail}"
                  + (f"  <- {s['repair']['why']}" if s.get("repair") else ""))


if __name__ == "__main__":
    sys.exit(main())
