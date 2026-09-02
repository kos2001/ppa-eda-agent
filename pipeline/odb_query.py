#!/usr/bin/env python3
"""Queries a run's real OpenROAD database (.odb) for measured placement
facts — net wire length and driver/sink distance — using OpenROAD
directly rather than reading OpenLane's summary metrics.

Why this exists, concretely. OpenLane already runs OpenROAD for every
`OpenROAD.*` step, but it only surfaces the aggregate numbers its own
metrics.json reports. Anything about *one specific net* — how long it
actually got routed, how far its driver really landed from its sink — is
in the .odb and nowhere else, and this pipeline had no way to ask.

That gap blocked a real conclusion. `physical-constraint-evaluator`,
reviewing sram_wrapper's RSZ-0090 failure, wrote:

    "The diagnosis's adjacency claim is inferred entirely from
     capacitance arithmetic, never from an actual placed net length —
     there's no .odb/placement report on disk to check whether the
     counter/subtractor really did land pin-adjacent to
     addr0/addr1/wmask0 ... I can't verify this either way without run
     artifacts, and none exist."

So the case's central claim rested on inference that nobody could check.
This makes it checkable: run the design to placement, then measure.

Usage:
    odb_query.py --run-dir designs/sram_wrapper/runs/<tag> \\
        [--net-filter addr0] [--top 20]

Requires the same Docker + OpenLane image as run_stage.py.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE, platform_args

REPO_ROOT = Path(__file__).resolve().parent.parent

# Runs inside OpenROAD's own Python interpreter against the real .odb.
# Reports per-net: the half-perimeter bounding box of all its connected
# pins (a real measured span, not an estimate) and the pin count, which
# together answer "did these land next to each other or not".
#
# Uses the block's dbu-per-micron rather than assuming 1000: it is a real
# property of the technology and reading it costs nothing.
_OR_SCRIPT = r'''
import odb, json, sys

db = odb.dbDatabase.create()
odb.read_db(db, "/work/design.odb")
block = db.getChip().getBlock()
dbu = block.getDbUnitsPerMicron()

rows = []
for net in block.getNets():
    if net.isSpecial():
        continue                       # power/ground, not signal routing
    iterms = list(net.getITerms())
    bterms = list(net.getBTerms())
    pts = []
    for it in iterms:
        x, y = it.getAvgXY()[1:3] if it.getAvgXY()[0] else (None, None)
        if x is not None:
            pts.append((x, y))
    for bt in bterms:
        box = bt.getBBox()
        pts.append(((box.xMin() + box.xMax()) // 2,
                    (box.yMin() + box.yMax()) // 2))
    if len(pts) < 2:
        continue
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    hpwl_um = ((max(xs) - min(xs)) + (max(ys) - min(ys))) / dbu
    span_um = max(max(xs) - min(xs), max(ys) - min(ys)) / dbu
    rows.append({
        "net": net.getName(),
        "pins": len(pts),
        "hpwl_um": round(hpwl_um, 4),
        "max_span_um": round(span_um, 4),
    })

rows.sort(key=lambda r: -r["hpwl_um"])
print("###JSON###" + json.dumps({"dbu_per_micron": dbu,
                                 "signal_nets": len(rows),
                                 "nets": rows}))
'''


def find_odb(run_dir: Path) -> Path:
    """The most advanced .odb this run produced. Prefers final/, else the
    highest-numbered step directory — a run that failed partway still has
    a real placed database from the last step that completed, which is
    exactly the case worth querying."""
    final = run_dir / "final" / "odb"
    if final.is_dir():
        for candidate in final.glob("*.odb"):
            return candidate
    numbered = sorted(
        (p for p in run_dir.glob("*/*.odb")),
        key=lambda p: p.parent.name,
    )
    if not numbered:
        raise FileNotFoundError(
            f"no .odb under {run_dir} — the run never reached a step that "
            f"writes one (floorplan or later)")
    return numbered[-1]


def query(run_dir: Path) -> dict:
    odb_path = find_odb(run_dir)
    work = odb_path.parent
    cmd = [
        "docker", "run", "--rm", *platform_args(),
        "-v", f"{work}:/work",
        IMAGE,
        "openroad", "-no_init", "-exit", "-python", "/work/_odb_query.py",
    ]
    script_path = work / "_odb_query.py"
    script_path.write_text(_OR_SCRIPT, encoding="utf-8")
    linked = work / "design.odb"
    created_link = False
    if not linked.exists():
        linked.write_bytes(odb_path.read_bytes())
        created_link = True
    try:
        print(f"$ {' '.join(cmd)}", file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        marker = "###JSON###"
        if marker not in result.stdout:
            raise RuntimeError(
                f"openroad produced no result (exit {result.returncode}); "
                f"stderr tail: {result.stderr[-600:]}")
        data = json.loads(result.stdout.split(marker, 1)[1].splitlines()[0])
        data["odb"] = str(odb_path)
        return data
    finally:
        script_path.unlink(missing_ok=True)
        if created_link:
            linked.unlink(missing_ok=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--net-filter", default=None,
                     help="regex; only report nets whose name matches")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args()

    data = query(args.run_dir.resolve())
    nets = data["nets"]
    if args.net_filter:
        pattern = re.compile(args.net_filter)
        nets = [n for n in nets if pattern.search(n["net"])]

    print(f"\nodb: {data['odb']}")
    print(f"signal nets: {data['signal_nets']}  (dbu/um = {data['dbu_per_micron']})")
    if args.net_filter:
        print(f"filter: {args.net_filter} -> {len(nets)} net(s)")
    print(f"\n{'net':<52} {'pins':>5} {'hpwl_um':>10} {'max_span_um':>12}")
    for n in nets[:args.top]:
        print(f"{n['net'][:52]:<52} {n['pins']:>5} {n['hpwl_um']:>10.4f} "
              f"{n['max_span_um']:>12.4f}")


if __name__ == "__main__":
    main()
