#!/usr/bin/env python3
r"""Does the SRAM macro's placement, relative to its own pin groups, matter?

sram_wrapper's macro is a hard block: its decoder, IO and control are inside
it, and OpenROAD sees only pin groups on its four sides (read from the LEF):

    port 0   addr0 / csb0 / web0 on the LEFT,  din0 / dout0 / wmask0 on the BOTTOM
    port 1   addr1 / csb1       on the RIGHT, dout1                 on the TOP

So a small controller (the wrapper's 8-bit counter) has to drive pins on
three sides of a 480 x 397 um block. What placement and routing can choose
is where that block sits in the die and which way it faces. The shipped
config puts it at (110, 150), facing N, in a 700 x 700 um die - 110 to 150 um
from every die edge, at 3.4% utilization. The max-slew violations are on the
addr pins, which is where those long nets end.

This runs the grid instead of arguing it: the same design, the same flow,
only the die and the macro's position and orientation changed. Each variant is
a copy of designs/sram_wrapper under a scratch name, scored by the same
run_candidate() path as any other candidate.

Usage:
    sram_floorplan_grid.py --parallel 4 --out ../reference-db/sram_floorplan_grid.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
DESIGNS = HERE / "designs"
BASE = DESIGNS / "sram_wrapper"

MACRO_W, MACRO_H = 479.78, 397.5

# (name, die, macro origin, orientation). `margin` is the space left around
# the macro; the compact die is the macro plus margin on every side.
def variants(margin: float = 30.0) -> list[dict]:
    cw, ch = round(MACRO_W + 2 * margin), round(MACRO_H + 2 * margin)
    out = [{"name": "shipped", "die": [0, 0, 700, 700], "loc": [110, 150], "orient": "N"}]
    for orient in ("MY", "MX", "S"):
        out.append({"name": f"big-{orient.lower()}", "die": [0, 0, 700, 700],
                    "loc": [110, 150], "orient": orient})
    for orient in ("N", "MY", "MX", "S"):
        out.append({"name": f"compact-{orient.lower()}", "die": [0, 0, cw, ch],
                    "loc": [margin, margin], "orient": orient})
    return out


def make_variant(v: dict) -> Path:
    """A throwaway design directory: sram_wrapper with one floorplan changed."""
    dest = DESIGNS / f"srv_{v['name'].replace('-', '_')}"
    # Reused, never deleted: a previous run leaves runs/ in here owned by root
    # (the container wrote it), so rmtree cannot remove it and a fresh mkdir
    # then collides. OpenLane's --overwrite replaces the run itself.
    dest.mkdir(parents=True, exist_ok=True)
    for sub in ("src", "lib", "pnr"):
        if (BASE / sub).exists():
            shutil.copytree(BASE / sub, dest / sub, dirs_exist_ok=True)
    cfg = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
    inst = cfg["MACROS"]["sky130_sram_1kbyte_1rw1r_32x256_8"]["instances"]["u_sram"]
    inst["location"] = v["loc"]
    inst["orientation"] = v["orient"]
    cfg["DIE_AREA"] = v["die"]
    (dest / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return dest


def run_variant(v: dict) -> dict:
    import orchestrator
    from run_stage import read_metrics
    import pnr_study

    d = make_variant(v)
    spec = {"design_name": d.name, "targets": {}}
    t0 = time.time()
    res = orchestrator.run_candidate(d, spec, {"tag": "grid", "overrides": {}})
    metrics = {}
    if res.get("run_dir"):
        try:
            metrics = read_metrics(Path(res["run_dir"]))
        except (OSError, ValueError):
            pass
    m = pnr_study.extract(res, metrics)
    verdict = res.get("verdict") or {}
    return {**v, "seconds": round(time.time() - t0, 1), "crashed": bool(res.get("error")),
            "error": (res.get("error") or "")[-200:] or None,
            "max_slew": metrics.get("design__max_slew_violation__count"),
            "max_fanout": metrics.get("design__max_fanout_violation__count"),
            "max_cap": metrics.get("design__max_cap_violation__count"),
            "drc_magic": metrics.get("magic__drc_error__count"),
            "drc_klayout": metrics.get("klayout__drc_error__count"),
            "lvs": metrics.get("design__lvs_error__count"),
            "wirelength": m["wirelength"], "vias": m["vias"], "setup_ws": m["setup_ws"],
            "hold_ws": m["hold_ws"], "core_area": metrics.get("design__core__area"),
            "violations": verdict.get("violations", [])}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--margin", type=float, default=30.0)
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    todo = [v for v in variants(args.margin) if not args.only or v["name"] in args.only]
    with ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        rows = list(ex.map(run_variant, todo))
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"{'variant':14s} {'die':>9s} {'orient':>6s} {'slew':>4s} {'fan':>4s} {'cap':>3s} "
          f"{'wire(um)':>9s} {'core um2':>9s}  {'magic/klayout/lvs'}")
    for r in rows:
        if r["crashed"]:
            print(f"{r['name']:14s} CRASHED: {r['error']}")
            continue
        die = f"{r['die'][2]}x{r['die'][3]}"
        print(f"{r['name']:14s} {die:>9s} {r['orient']:>6s} {r['max_slew']!s:>4s} {r['max_fanout']!s:>4s} "
              f"{r['max_cap']!s:>3s} {r['wirelength']!s:>9s} {r['core_area'] and round(r['core_area']):>9}  "
              f"{r['drc_magic']}/{r['drc_klayout']}/{r['lvs']}")


if __name__ == "__main__":
    sys.exit(main())
