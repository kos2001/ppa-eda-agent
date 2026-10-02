r"""Turning a live_view snapshot into terminal lines.

Pure: a snapshot dict in, a list of strings out, so the layout can be
tested without a terminal or a run.
"""
from __future__ import annotations

import time

import live_parse as lp

RESET, DIM, BOLD = "\x1b[0m", "\x1b[2m", "\x1b[1m"
GREEN, RED, YELLOW, CYAN = "\x1b[32m", "\x1b[31m", "\x1b[33m", "\x1b[36m"


def paint(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{RESET}" if color else text


def telemetry_line(snap: dict, ascii_only: bool) -> str | None:
    t = snap.get("telemetry")
    if not t:
        return None
    kind = t["kind"]
    if kind == "GPL":
        if "iter" not in t:
            return "GPL  initialising"
        spark = lp.sparkline([s[1] for s in t["series"]], 22, ascii_only)
        hp = lp.sparkline([s[2] for s in t["series"]], 22, ascii_only)
        stop = f"  stopped at {t['finished']:.3f}" if t.get("finished") else "  stop at 0.100"
        return (f"GPL  iter {t['iter']}  overflow {t['overflow']:.3f}{stop}  {spark}"
                f"   HPWL {t['hpwl'] / 1e6:.2f}M {hp}  (timing reweights {t['timing_reweights']})")
    if kind == "DRT":
        hist = " -> ".join(str(v) for v in t.get("history", []))
        now = ""
        if "pct" in t:
            now = f"iteration {t.get('iteration', 0)} at {t['pct']}%  violations {t['violations']}"
        elif "violations" in t:
            now = f"violations {t['violations']}"
        return f"DRT  {now}  {('history ' + hist) if hist else ''}".rstrip()
    if kind == "GRT":
        if "usage_pct" not in t:
            return "GRT  routing"
        return (f"GRT  congestion {t['usage_pct']:.1f}%  overflow {t['overflow']}"
                f"  wirelength {t.get('wirelength_um', '?')} um")
    if kind == "CTS":
        return "CTS  " + "  ".join(f"{k} {v}" for k, v in t.items() if k != "kind")
    if kind == "RSZ":
        row = t.get("row", {})
        bits = [f"{k} {v}" for k, v in row.items() if k in ("Iteration", "Buffers", "WNS", "TNS")]
        extra = []
        if "hold_endpoints" in t:
            extra.append(f"hold endpoints {t['hold_endpoints']}")
        if "hold_buffers" in t:
            extra.append(f"hold buffers {t['hold_buffers']}")
        if t.get("buffer_cap_hit"):
            extra.append("BUFFER CAP HIT")
        return "RSZ  " + "  ".join(bits + extra) if (bits or extra) else "RSZ  repairing"
    if kind == "LOG":
        return "     " + t["last"]
    return None


def run_lines(snap: dict, width: int, color: bool, ascii_only: bool, verdicts: dict) -> list[str]:
    icon = {"running": ">", "done": "+", "failed": "x", "stalled": "?"} if ascii_only \
        else {"running": "▶", "done": "✔", "failed": "✘", "stalled": "…"}
    col = {"running": CYAN, "done": GREEN, "failed": RED, "stalled": YELLOW}[snap["status"]]
    head = f"{icon[snap['status']]} {snap['tag']}"
    v = verdicts.get(snap["tag"])
    tail = f"{snap.get('label') or 'starting'} step {snap['step_no']}/78"
    dot = "-" if ascii_only else "·"
    lines = [paint(head, col, color) + paint(f"  {snap['status']} {dot} {tail}", DIM, color)]
    if snap["status"] == "running":
        lines.append("    " + lp.bar(snap["step_no"] / 78, 28, ascii_only)
                     + f" {100 * snap['step_no'] // 78}%  elapsed {_t(snap['elapsed'])}"
                     + (f"  idle {int(snap['idle'])}s" if snap.get("idle", 0) > 8 else ""))
    else:
        lines.append(f"    elapsed {_t(snap['elapsed'])}")
    tl = telemetry_line(snap, ascii_only)
    if tl and snap["status"] in ("running", "stalled"):
        lines.append("    " + tl[: width - 6])
    f = snap.get("finished")
    if f:
        verdict = ""
        if v is not None:
            verdict = paint("PASS  ", GREEN, color) if v.get("passed") else paint("FAIL  ", RED, color)
        lines.append("    " + verdict + f"area {_n(f['area'])} um2  power {_n(f['power'], 1e6)} uW  "
                     f"core {_n(f['core'])}  slack {_n(f['setup_ws'], 1, 2)} ns  "
                     f"wire {_n(f['wirelength'])}  vias {_n(f['vias'])}  hold-buf {_n(f['hold_buffers'])}")
    if snap.get("error"):
        lines.append("    " + paint(snap["error"].splitlines()[-1][: width - 6], RED, color))
    return lines


def _t(seconds: float) -> str:
    s = int(max(0, seconds))
    return f"{s // 60}:{s % 60:02d}"


def _n(v, scale: float = 1, digits: int = 0) -> str:
    if v is None:
        return "?"
    return f"{v * scale:.{digits}f}"


def event_line(ev: dict, t0: float) -> str | None:
    at = f"{int(ev.get('t', t0) - t0):>4}s"
    k = ev.get("type")
    if k == "run_start":
        return f"{at}  run started (max {ev.get('max_iterations')} iterations{', polish' if ev.get('polish') else ''})"
    if k == "iteration_start":
        return f"{at}  iteration {ev.get('iteration')}: {', '.join(ev.get('candidates', []))}"
    if k == "candidate_start":
        return f"{at}  start {ev.get('tag')}"
    if k == "candidate_done":
        if ev.get("passed"):
            return f"{at}  {ev.get('tag')}: PASS  area {_n(ev.get('area_um2'))}  power {_n(ev.get('power_w'), 1e6)} uW"
        why = ev.get("died_with") or "; ".join(ev.get("violations") or []) or "unverified checks"
        return f"{at}  {ev.get('tag')}: FAIL  {why}"
    if k == "repair":
        ch = ", ".join(f"{a}: {b[0]} -> {b[1]}" for a, b in (ev.get("changes") or {}).items())
        return f"{at}  REPAIR {ev.get('from_tag')} -> {ev.get('to_tag')}  [{ev.get('code')}] {ch}"
    if k == "polish_start":
        return f"{at}  polish {ev.get('winner')}: trying {', '.join(ev.get('moves', []))}"
    if k == "polish_trial":
        d = ev.get("deltas") or {}
        ds = " ".join(f"{n} {v:+.1f}%" for n, v in d.items() if n in ("area", "power", "core"))
        return f"{at}  polish {ev.get('move')}: {'ADOPTED' if ev.get('adopted') else 'rejected'}  {ds}"
    if k == "polish_combine":
        return f"{at}  polish combining {', '.join(ev.get('moves', []))}"
    if k == "polish_done":
        return f"{at}  polish finished: winner {ev.get('winner')}"
    if k == "stop":
        return f"{at}  stop: {ev.get('reason')}  winner {ev.get('winner')}"
    return None


def render(data: dict, width: int = 100, color: bool = True, ascii_only: bool = False,
           max_events: int = 10) -> list[str]:
    runs = data["runs"]
    stamp = time.strftime("%H:%M:%S", time.localtime(data["now"]))
    n_run = sum(1 for r in runs if r["status"] == "running")
    out = [paint(f"PPA live  {stamp}   {len(runs)} run(s), {n_run} running", BOLD, color),
           "-" * min(width, 100)]
    verdicts = {}
    for evs in data["events"].values():
        for e in evs:
            if e.get("type") == "candidate_done":
                verdicts[e["tag"]] = e
    if not runs:
        out.append("no recent runs found (try --all or point --design at a design directory)")
    for r in runs[-8:]:
        out += run_lines(r, width, color, ascii_only, verdicts)
        if r.get("map"):
            m = r["map"]
            out.append(paint(f"    cells after {m['from_step']}  ({m['cells']} placed, "
                             f"die {m['die_um'][0]:.0f}x{m['die_um'][1]:.0f} um)", DIM, color))
            out += ["    |" + line + "|" for line in lp.render_grid(m, ascii_only)]
    for name, evs in data["events"].items():
        shown = [e for e in evs if e.get("type") not in ("candidate_start",)]
        if not shown:
            continue
        out += ["-" * min(width, 100), paint(f"decisions ({name})", BOLD, color)]
        t0 = evs[0].get("t", data["now"])
        lines = [event_line(e, t0) for e in shown]
        out += ["  " + ln[: width - 2] for ln in [x for x in lines if x][-max_events:]]
    return out
