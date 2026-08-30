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

CLOCK_PERIODS = (4, 6, 8, 12, 20)
UTILISATIONS = (25, 45, 65)


def declared(design: str) -> dict:
    path = DESIGNS / design / "config.json"
    return json.loads(path.read_text()) if path.is_file() else {}


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
            axes = [("CLOCK_PERIOD", c) for c in CLOCK_PERIODS]
            if not absolute:
                axes += [("FP_CORE_UTIL", u) for u in UTILISATIONS]
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
        spec["targets"] = json.loads(spec_path.read_text()).get("targets", {})
    started = time.time()
    result = orchestrator.run_candidate(design_dir, spec, item)
    result["design"] = item["design"]
    result["seconds"] = round(time.time() - started, 1)
    return result


def collect(designs: list[str], parallel: int, limit: int | None) -> dict:
    items = plan(designs)
    if limit:
        items = items[:limit]
    print(f"planned {len(items)} runs across {len(set(i['design'] for i in items))} "
          f"designs", file=sys.stderr, flush=True)

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

    written = []
    for design, results in sorted(by_design.items()):
        case_file = orchestrator.write_case(
            design, DESIGNS / design, [{"iteration": 1, "results": results}],
            orchestrator.pick_winner(results), "max_iterations_reached")
        written.append(str(case_file.relative_to(REPO_ROOT)))
    return {"planned": len(items), "cases_written": written}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--designs", nargs="*",
                    default=sorted(p.name for p in DESIGNS.iterdir()
                                   if (p / "config.json").is_file()))
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.dry_run:
        items = plan(args.designs)
        print(json.dumps({"planned": len(items), "items": items}, indent=2))
        return
    print(json.dumps(collect(args.designs, args.parallel, args.limit), indent=2))


if __name__ == "__main__":
    main()
