#!/usr/bin/env python3
r"""Watch a placement-and-routing run as it happens, in the terminal.

The dashboard polls a tail of stdout. This reads what the tools themselves
are writing, so it shows the algorithms converging rather than only which
step is running:

  global placement   overflow falling toward its stop value, HPWL settling
  timing repair      the resizer's last table row (WNS, TNS, buffers)
  clock tree         sinks, buffers, tree depth
  global routing     congestion and wirelength
  detailed routing   violations per optimisation iteration (73 -> 28 -> 41 -> 0)

plus a coarse map of where the cells sit, redrawn from the DEF each placement
step writes, so you can watch them spread, legalise and get buffered. The
orchestrator's own decisions (which repair made which candidate, which
polish move was adopted) come from <design>/.events.jsonl.

Read-only and dependency-free. Point it at the machine that is running the
flow; for the WSL runner:

    wsl -d Ubuntu -- python3 /home/kos2001/claude_work/ppa-pnr/pipeline/live_view.py -f

Usage:
    live_view.py [-f] [--design DIR ...] [--interval 1] [--recent 900]
                 [--all] [--map | --no-map] [--ascii] [--no-color]
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import time
from pathlib import Path

import live_events
import live_parse as lp

HERE = Path(__file__).resolve().parent
TOTAL_STEPS = 78  # Classic declares 78; a normal run writes 74 directories
_STEP = re.compile(r"^(\d+)-(.+)$")
_TAIL_BYTES = 400_000

# Names for the stages a reader actually watches; anything else shows its
# directory slug.
PRETTY = {
    "openroad-floorplan": "Floorplan", "openroad-generatepdn": "Power grid",
    "openroad-globalplacementskipio": "Global placement (no IO)",
    "openroad-ioplacement": "IO placement", "openroad-globalplacement": "Global placement",
    "openroad-repairdesignpostgpl": "Repair design", "openroad-detailedplacement": "Detailed placement",
    "openroad-cts": "Clock tree synthesis", "openroad-resizertimingpostcts": "Timing repair (post-CTS)",
    "openroad-globalrouting": "Global routing", "openroad-repairantennas": "Antenna repair",
    "openroad-detailedrouting": "Detailed routing", "openroad-fillinsertion": "Fill insertion",
    "openroad-stapostpnr": "Signoff STA", "magic-drc": "Magic DRC", "klayout-drc": "KLayout DRC",
    "netgen-lvs": "LVS", "yosys-synthesis": "Synthesis",
}


def read_tail(path: Path, limit: int = _TAIL_BYTES) -> str:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > limit:
                f.seek(size - limit)
            return f.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def step_dirs(run_dir: Path) -> list[tuple[int, str, Path]]:
    out = []
    try:
        for p in run_dir.iterdir():
            m = _STEP.match(p.name)
            if m and p.is_dir():
                out.append((int(m.group(1)), m.group(2), p))
    except OSError:
        pass
    return sorted(out)


def newest_mtime(paths: list[Path]) -> float:
    best = 0.0
    for p in paths:
        try:
            best = max(best, p.stat().st_mtime)
        except OSError:
            pass
    return best


def fmt_time(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


def final_metrics(run_dir: Path) -> dict | None:
    f = run_dir / "final" / "metrics.json"
    try:
        m = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ws = [v for k, v in m.items() if k.startswith("timing__setup__ws__corner:")
          and isinstance(v, (int, float))]
    return {"area": m.get("design__instance__area"), "power": m.get("power__total"),
            "core": m.get("design__core__area"), "setup_ws": min(ws) if ws else None,
            "wirelength": m.get("route__wirelength"), "vias": m.get("route__vias"),
            "drc": m.get("route__drc_errors"), "hold_buffers": m.get("design__instance__count__hold_buffer")}


def run_snapshot(run_dir: Path, now: float, stale_after: float = 180.0) -> dict:
    """What can be known about one run from its directory alone."""
    steps = step_dirs(run_dir)
    snap = {"tag": run_dir.name, "run_dir": str(run_dir), "step_no": 0, "step": None,
            "slug": None, "label": None, "telemetry": None, "map": None}
    try:
        started = (run_dir / "resolved.json").stat().st_mtime
    except OSError:
        started = steps and newest_mtime([steps[0][2]]) or now
    metrics = final_metrics(run_dir)
    log = run_dir / "flow.log"
    err = run_dir / "error.log"
    failed = err.exists() and err.stat().st_size > 0
    snap["error"] = read_tail(err, 4000).strip()[-600:] if failed else None
    if steps:
        no, slug, path = steps[-1]
        snap.update(step_no=no, slug=slug, step=path.name)
        snap["label"] = PRETTY.get(slug, slug)
        done = (path / "state_out.json").exists()
        idle = now - newest_mtime([log, path / f"{slug}.log", path])
        snap["step_done"] = done
        snap["idle"] = idle
        parsed = lp.parser_for(slug)
        if parsed:
            label, fn = parsed
            snap["telemetry"] = {"kind": label, **fn(read_tail(path / f"{slug}.log"))}
        else:
            tail = [ln for ln in read_tail(path / f"{slug}.log", 8000).splitlines() if ln.strip()]
            snap["telemetry"] = {"kind": "LOG", "last": tail[-1][:100]} if tail else None
    else:
        snap["idle"] = now - started
    if metrics is not None:
        snap["status"], snap["finished"] = "done", metrics
        end = newest_mtime([run_dir / "final" / "metrics.json"])
        snap["elapsed"] = max(0.0, end - started)
    elif failed:
        snap["status"], snap["elapsed"] = "failed", max(0.0, newest_mtime([err]) - started)
    else:
        snap["status"] = "running" if snap["idle"] < stale_after else "stalled"
        snap["elapsed"] = max(0.0, now - started)
    snap["started"] = started
    return snap


def latest_placement_map(run_dir: Path, cols: int, rows: int) -> dict | None:
    """Density map from the newest step directory that wrote a DEF with
    placed cells."""
    for no, slug, path in reversed(step_dirs(run_dir)):
        defs = sorted(path.glob("*.def"))
        if not defs:
            continue
        try:
            info = lp.density_grid(defs[0].read_text(encoding="utf-8", errors="replace"), cols, rows)
        except OSError:
            continue
        if info:
            info["from_step"] = f"{no}-{slug}"
            return info
    return None


def discover(design_dirs: list[Path], now: float, recent: float, show_all: bool) -> list[Path]:
    runs = []
    for d in design_dirs:
        root = d / "runs"
        if not root.is_dir():
            continue
        for r in root.iterdir():
            if r.is_dir() and (show_all or now - newest_mtime([r, r / "flow.log"]) <= recent):
                runs.append(r)
    runs.sort(key=lambda p: newest_mtime([p, p / "flow.log"]))
    return runs


def collect(design_dirs: list[Path], now: float, recent: float, show_all: bool,
            want_map: bool, cols: int, rows: int) -> dict:
    snaps = []
    for r in discover(design_dirs, now, recent, show_all):
        s = run_snapshot(r, now)
        s["design"] = r.parent.parent.name
        snaps.append(s)
    running = [s for s in snaps if s["status"] == "running"]
    if want_map and snaps:
        focus = (running or snaps)[-1]
        focus["map"] = latest_placement_map(Path(focus["run_dir"]), cols, rows)
    events = {}
    for d in design_dirs:
        events[d.name] = live_events.read(d)
    return {"now": now, "runs": snaps, "events": events}


def main() -> None:
    import live_render
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-f", "--follow", action="store_true", help="keep refreshing")
    ap.add_argument("--design", action="append", type=Path,
                    help="design directory (repeatable); default: every design")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--recent", type=float, default=900.0,
                    help="show runs touched in the last N seconds")
    ap.add_argument("--all", action="store_true", help="show every run directory")
    ap.add_argument("--map", dest="map", action="store_true", default=True)
    ap.add_argument("--no-map", dest="map", action="store_false")
    ap.add_argument("--ascii", action="store_true", help="plain ASCII, no box-drawing glyphs")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        args.ascii = True
    designs = args.design or sorted(p for p in (HERE / "designs").iterdir() if p.is_dir())
    tty = sys.stdout.isatty()
    color = tty and not args.no_color
    while True:
        width = shutil.get_terminal_size((100, 30)).columns
        data = collect(designs, time.time(), args.recent, args.all, args.map, 48, 10)
        lines = live_render.render(data, width, color, args.ascii)
        if args.follow and tty:
            sys.stdout.write("\x1b[H\x1b[J")
        sys.stdout.write("\n".join(lines) + "\n")
        sys.stdout.flush()
        if not args.follow:
            return
        if not tty:
            sys.stdout.write("\n")
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            return


if __name__ == "__main__":
    main()
