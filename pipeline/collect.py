r"""Bulk sample collection: every design, across every axis that moves a target.

The corpus grew one hand-written sweep at a time, which is why its
coverage is lopsided — 52 rows of FP_CORE_UTIL, which moves counter4's
area 4.3%, against 19 of CLOCK_PERIOD, which moves its power 2.5x. This
enumerates the cross-product instead, so what gets collected is decided
by which axes were measured to matter rather than by which sweep someone
wrote last.

Axes, in the order they were shown to matter:

  technology   50-73% area between sky130 libraries, 3.7-4.4x between
               foundries. The single largest separator in the corpus.
  CLOCK_PERIOD 2.5-3.3x power, and a completion boundary. The only axis
               that separates the two targets instead of moving both.
  DIE_AREA     65% on the tiny-die design, and the axis that decides
               whether a new technology fits at all.
  FP_CORE_UTIL 3.8-4.3%. Included because it is cheap, not because it
               teaches much.

WHAT IT KNOWS NOT TO TRY, from what previous runs measured:

  - sram_wrapper produces no completed rows at all (Magic cannot read
    its macro's GDS), so running it collects nothing.
  - gf180mcu needs a larger die than sky130: counter4_tinydie needed
    256um where sky130 needed 8, cdc_twoclock 128x128 where its config
    fixes 60x60. A design with an absolute die gets one sized for the
    technology, or the run fails on utilisation and teaches nothing.
  - the 9-track gf180 library needs an exclusion file the PDK omits, and
    sky130_fd_sc_hs needs delay cells excluded or it fails Magic DRC.

Runs are real OpenLane flows through orchestrator.run_candidate, so
every row is measured the same way every other row was. Resumable: a
config already in reference-db is skipped, because re-running it
produces a duplicate that deduplication then discards.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import orchestrator
import surrogate

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGNS = REPO_ROOT / "pipeline" / "designs"
REFDB = REPO_ROOT / "reference-db"

# Designs that produce nothing, and why. Kept as data rather than a
# comment so the reason travels with the decision.
SKIP = {
    "sram_wrapper": "Magic cannot read the SRAM macro's GDS (layers 22/21, "
                    "22/22, 33/42, 33/43, 235/0 are in no sky130 tool "
                    "configuration); no run completes, so none collects a row",
    "sram_wrapper_autoplace": "same macro, same blocker — it exists to hold "
                              "the automatic-macro-placement experiment, which "
                              "is recorded and closed",
}

# Designs whose declared die cannot be floorplanned, so no axis acting
# before the floorplan can change the outcome.
#
# Not the same thing as SKIP above. These designs do produce rows —
# counter4_tinydie has 44 — but only from the one axis that can move the
# result, which is the die area itself, and that is swept by the
# orchestrator rather than here. Everything this collector varies acts
# earlier: SYNTH_STRATEGY and CLOCK_PERIOD change synthesis, and
# FP_CORE_UTIL is ignored outright by an absolute die.
#
# Measured rather than assumed. A batch swept nine synthesis strategies
# across four technologies on counter4_tinydie and all 36 runs failed,
# each of them at the floorplan and none of them at synthesis. Both
# families fail, with different errors and the same cause: sky130 gets
# STA-0572, `-core_area '-2.88' is not a positive float`, and gf180 —
# with its die already scaled 4x — gets PDN-0185, no room for power
# straps on Metal4.
#
# The reason to refuse them is not that they are wrong. Each row is a
# true observation. It is that 36 rows carrying one fact are 36 easy
# rows: added to the store they move completion's win-rate from 0.824 to
# 0.849, which is a better number and not a better model. A gain that
# comes from redundant samples is the kind of accuracy this pipeline is
# built to refuse.
NO_PRE_FLOORPLAN_AXIS = {
    "counter4_tinydie": "its declared die fails the floorplan on every "
                        "technology, so synthesis and clock axes all "
                        "produce the same row; its informative axis is "
                        "DIE_AREA, which is swept elsewhere",
}

# A technology, and what a design needs before it will build there.
TECHNOLOGIES = [
    {"name": "sky130_fd_sc_hd", "pdk": None, "scl": None, "extra": {}},
    {"name": "sky130_fd_sc_hs", "pdk": None, "scl": "sky130_fd_sc_hs",
     "extra": {"PNR_EXCLUDED_CELL_FILE": "/design/pnr/hs_exclude.cells"},
     "needs": "pnr/hs_exclude.cells"},
    {"name": "gf180mcu_7t", "pdk": "gf180mcuD",
     "scl": "gf180mcu_fd_sc_mcu7t5v0", "extra": {}, "die_scale": 4.0},
    {"name": "gf180mcu_9t", "pdk": "gf180mcuD",
     "scl": "gf180mcu_fd_sc_mcu9t5v0",
     "extra": {"PNR_EXCLUDED_CELL_FILE": "/design/pnr/gf180_9t_exclude.cells"},
     "needs": "pnr/gf180_9t_exclude.cells", "die_scale": 4.0},

]

# The metal stack was tried here as a fourth axis and is deliberately not
# one. All four gf180mcu variants are installed and only D is used, which
# looked like three free technologies: read from their tech-LEFs, A ships
# Metal1-3, B adds Metal4, and C and D both reach Metal5 differing only
# in top-metal thickness. Same cells, same netlist, so any difference in
# the result would have been the stack and nothing else.
#
# Two measurements closed it, in the order they were made.
#
# A and B cannot run at all. Neither ships any OpenRCX ruleset —
# `rules.openrcx.<variant>.{min,nom,max}` exist for C and D and do not
# exist for A and B — and OpenLane validates PDK paths while loading the
# PDK, so the flow quits before it creates a run directory. 34 runs of a
# batch were spent discovering this. Unlike the drc_exclude.cells gap
# pdk_repair fills, this one must not be stubbed: an empty exclusion list
# is a truthful statement that nothing is excluded, while an empty or
# invented extraction ruleset would produce parasitic values that are
# wrong and indistinguishable from measured ones.
#
# C runs, and is D. Measured on counter4 at its declared config: area
# 1029.55 against D's 1029.55 — identical to the last digit — and power
# 0.0018737 against 0.0018649, 0.47% apart. The layer they differ on is
# the top one, which a 421-cell design never routes on. Collecting the
# cross-product there would add ~70 rows that a k-NN can predict
# perfectly from their D twins, which is the tinydie lesson again in a
# different costume: an easy row raises the score without adding a fact.
# Eight C rows reached the store before this was measured, recovered
# from the interrupted batch that found the A/B failure. Three had a D
# twin with an identical area to the last digit and were removed; the
# other five sweep a clock period D has not run, so they measure
# something no other row measures and were kept. The distinction is
# duplication, not provenance — the metric barely moves either way
# (area 0.9922 to 0.9920, completion 0.8805 to 0.8774), so the reason to
# drop them is that they are the same measurement twice, not that the
# number improves.
#
# What survived is the feature, not the axis. routing_layers stays in
# surrogate.py because sky130 and gf180 genuinely differ there (6 against
# 5), and it earns its place by measurement: removing it takes area from
# 0.992 to 0.929 and power from 0.971 to 0.895, with intervals that do
# not overlap. SCL already separates the four libraries, but it makes all
# four equidistant; routing_layers is what makes two gf180 libraries
# nearer each other than either is to sky130.

CLOCK_PERIODS = (4, 6, 8, 12, 20)
UTILISATIONS = (25, 45, 65)

# Nine ABC scripts, and the axis with the thinnest coverage in the
# corpus at 7.8% of rows — it was swept once, on counter4, on one
# library. It moves area only ~4% on that design, but it is nearly free
# on the fast designs (6-70 s a run) and it is the only axis that
# changes what synthesis produces rather than how it is placed.
SYNTH_STRATEGIES = ("AREA 0", "AREA 1", "AREA 2", "AREA 3",
                    "DELAY 0", "DELAY 1", "DELAY 2", "DELAY 3", "DELAY 4")

# Designs whose runs are cheap enough to sweep exhaustively. aes and
# riscv32i are 9,731 and 14,705 cells and take twenty to thirty-five
# minutes a run under contention, against six to seventy seconds for
# everything else — a 30x spread, so an axis worth adding everywhere is
# not worth adding there.
FAST_ONLY_AXES = {"SYNTH_STRATEGY"}
SLOW_DESIGNS = {"aes", "riscv32i"}


def recorded_seconds() -> dict:
    """Median wall-clock per design, from runs already recorded.

    The collector had no idea what a run costs. Measured across the
    corpus the small designs land at 6-70 s, while aes (14,705 cells)
    and riscv32i (9,731) take twenty minutes each under three-way
    contention — a 20x spread planned as if it were uniform, which is
    how a 64-run batch took the machine to a load average of 55.

    Read from the same `seconds` field run_one already writes, so this
    sharpens itself every time anything is collected.
    """
    out: dict[str, float] = {}
    cases = REFDB / "cases"
    if not cases.is_dir():
        return out
    per: dict[str, list] = {}
    for path in sorted(cases.glob("*.json")):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for iteration in case.get("iterations", []):
            for result in iteration.get("results", []):
                if isinstance(result.get("seconds"), (int, float)):
                    per.setdefault(case.get("design", ""), []).append(result["seconds"])
    for design, values in per.items():
        values.sort()
        out[design] = values[len(values) // 2]
    return out


def estimate(items: list[dict], parallel: int) -> dict:
    """What a plan will cost, before it is started.

    Reported rather than enforced: a long batch may be exactly what
    someone wants. What they should not have to do is discover the
    length by watching it.
    """
    known = recorded_seconds()
    timed = [i for i in items if i["design"] in known]
    untimed = sorted({i["design"] for i in items if i["design"] not in known})
    total = sum(known[i["design"]] for i in timed)

    per_design: dict[str, dict] = {}
    for item in items:
        row = per_design.setdefault(item["design"], {"runs": 0})
        row["runs"] += 1
        row["seconds_each"] = round(known[item["design"]], 1) \
            if item["design"] in known else None

    # No number is given for a design nobody has timed, and none is
    # guessed from the timed ones either. Measured across this corpus
    # the small designs land at 6-70 s while aes (14,705 cells) takes
    # twenty minutes under contention — a 20x spread, so a fallback
    # drawn from the small ones would have reported 19 minutes for a batch
    # that ran for hours. An estimate that confident and that wrong is
    # worse than saying it does not know.
    return {
        "runs": len(items),
        "runs_with_a_timing": len(timed),
        "minutes_for_timed_runs": round(total / 60, 1),
        "wall_minutes_at_parallel": round(total / 60 / max(parallel, 1), 1),
        "untimed_designs": untimed,
        "estimate_covers_everything": not untimed,
        "per_design": per_design,
    }


def declared(design: str) -> dict:
    path = DESIGNS / design / "config.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def already_have(design: str) -> set:
    """Configurations reference-db already holds, so they are not re-run."""
    have = set()
    for row in surrogate.load_dataset():
        if row["design"] != design:
            continue
        have.add((json.dumps(row.get("overrides") or {}, sort_keys=True),
                  row.get("scl") or surrogate.DEFAULT_SCL,
                  row.get("pdk") or surrogate.DEFAULT_PDK))
    return have


def scaled_die(cfg: dict, factor: float) -> list | None:
    """A die sized for a technology whose cells are `factor` times larger.

    Only for designs that fix an absolute die. FP_CORE_UTIL is inert when
    FP_SIZING is absolute, so overriding it there fails identically to
    doing nothing — measured on cdc_twoclock.
    """
    die = cfg.get("DIE_AREA")
    if cfg.get("FP_SIZING") != "absolute" or not isinstance(die, list):
        return None
    x0, y0, x1, y1 = die
    grow = factor ** 0.5
    return [x0, y0, round(x0 + (x1 - x0) * grow), round(y0 + (y1 - y0) * grow)]


def plan(designs: list[str]) -> list[dict]:
    """Every candidate worth running that is not already recorded."""
    out = []
    for design in designs:
        if design in SKIP:
            continue
        cfg = declared(design)
        have = already_have(design)
        absolute = cfg.get("FP_SIZING") == "absolute"
        for tech in TECHNOLOGIES:
            need = tech.get("needs")
            if need and not (DESIGNS / design / need).is_file():
                continue
            base = dict(tech["extra"])
            if tech.get("die_scale"):
                grown = scaled_die(cfg, tech["die_scale"])
                if grown:
                    base["DIE_AREA"] = grown
            # A design with an absolute die ignores FP_CORE_UTIL, so
            # sweeping it there produces identical runs.
            # Every axis this collector varies acts before the floorplan,
            # so a design that cannot floorplan has nothing to learn from
            # any of them — the same argument as the FP_CORE_UTIL guard
            # below, one stage earlier.
            if design in NO_PRE_FLOORPLAN_AXIS:
                continue
            axes = [("CLOCK_PERIOD", c) for c in CLOCK_PERIODS]
            if not absolute:
                axes += [("FP_CORE_UTIL", u) for u in UTILISATIONS]
            if design not in SLOW_DESIGNS:
                axes += [("SYNTH_STRATEGY", v) for v in SYNTH_STRATEGIES]
            for key, value in axes:
                overrides = {**base, key: value}
                sig = (json.dumps(overrides, sort_keys=True),
                       tech["scl"] or surrogate.DEFAULT_SCL,
                       tech["pdk"] or surrogate.DEFAULT_PDK)
                if sig in have:
                    continue
                out.append({
                    "design": design,
                    "tag": f"c-{tech['name'].split('_sc_')[-1]}-{key.lower()}{value}",
                    "overrides": overrides,
                    "scl": tech["scl"], "pdk": tech["pdk"],
                })
    return out


def run_one(item: dict) -> dict:
    design_dir = (DESIGNS / item["design"]).resolve()
    spec = {"targets": {}}
    spec_path = design_dir / "run_spec.json"
    if spec_path.is_file():
        spec["targets"] = json.loads(spec_path.read_text(encoding="utf-8")).get("targets", {})
    started = time.time()
    result = orchestrator.run_candidate(design_dir, spec, item)
    result["design"] = item["design"]
    result["seconds"] = round(time.time() - started, 1)
    return result


def collect(designs: list[str], parallel: int, limit: int | None) -> dict:
    items = plan(designs)
    if limit:
        items = items[:limit]
    cost = estimate(items, parallel)
    note = (f"about {cost['wall_minutes_at_parallel']} min at {parallel} parallel"
            if cost["estimate_covers_everything"]
            else f"{cost['runs_with_a_timing']} of {cost['runs']} runs have a "
                 f"timing ({cost['wall_minutes_at_parallel']} min at {parallel} "
                 f"parallel); no estimate for "
                 f"{', '.join(cost['untimed_designs'])}")
    print(f"planned {len(items)} runs across "
          f"{len(set(i['design'] for i in items))} designs — {note}",
          file=sys.stderr, flush=True)

    # How many runs of each design are still outstanding, so a design's
    # case can be written the moment its last run lands instead of at
    # the end of the batch.
    #
    # The batch used to write everything once, after all of it finished.
    # A batch that finishes is fine; an interrupted one lost every
    # completed run it held. That is not hypothetical — a 171-run batch
    # was killed at 104 and a second at 2, and the store stayed exactly
    # where it started while 85 finished runs sat on disk unrecorded.
    #
    # This does not make a batch un-loseable; the design still being run
    # when the process dies is still unwritten here. What makes those
    # recoverable is recover_runs.py, which reads the run directories
    # OpenLane wrote on its own. This just means the interruption costs
    # one design instead of all of them.
    outstanding: dict[str, int] = {}
    for item in items:
        outstanding[item["design"]] = outstanding.get(item["design"], 0) + 1

    written: list[str] = []

    def flush(design: str, results: list) -> None:
        case_file = orchestrator.write_case(
            design, DESIGNS / design, [{"iteration": 1, "results": results}],
            orchestrator.pick_winner(results), "max_iterations_reached")
        written.append(case_file.relative_to(REPO_ROOT).as_posix())
        print(f"  wrote {case_file.name} ({len(results)} runs)",
              file=sys.stderr, flush=True)

    by_design: dict[str, list] = {}
    with ThreadPoolExecutor(max_workers=parallel) as pool:
        futures = {pool.submit(run_one, item): item for item in items}
        done = 0
        for future in as_completed(futures):
            item = futures[future]
            done += 1
            try:
                result = future.result()
            except Exception as e:  # noqa: BLE001 - one failure is a data point
                result = {"design": item["design"], "tag": item["tag"],
                          "overrides": item["overrides"], "scl": item.get("scl"),
                          "pdk": item.get("pdk"), "error": str(e)}
            by_design.setdefault(item["design"], []).append(result)
            ok = "ok" if (result.get("verdict") or {}).get("area_um2") else "no verdict"
            print(f"[{done}/{len(items)}] {item['design']:18s} {item['tag']:28s} {ok}",
                  file=sys.stderr, flush=True)
            design = item["design"]
            outstanding[design] -= 1
            if outstanding[design] == 0:
                flush(design, by_design[design])

    # Anything still unflushed — only reachable if a design's count and
    # its results disagree, which would mean the accounting above is
    # wrong. Writing it is better than dropping it silently.
    for design, results in sorted(by_design.items()):
        if outstanding.get(design):
            flush(design, results)
    return {"planned": len(items), "cases_written": written}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--designs", nargs="*",
                    default=sorted(p.name for p in DESIGNS.iterdir()
                                   if (p / "config.json").is_file()))
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--estimate", action="store_true",
                    help="what the plan will cost, without running it")
    args = ap.parse_args()

    if args.estimate:
        items = plan(args.designs)
        print(json.dumps(estimate(items, args.parallel), indent=2))
        return
    if args.dry_run:
        items = plan(args.designs)
        print(json.dumps({"planned": len(items), "items": items}, indent=2))
        return
    print(json.dumps(collect(args.designs, args.parallel, args.limit), indent=2))


if __name__ == "__main__":
    main()
