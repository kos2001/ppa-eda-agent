r"""Reading OpenROAD's own progress lines back out of a step log.

Every parser takes the text of one step's log (or its tail) and returns a
small dict, never raising: a line that does not match is simply absent, so
a log from a different OpenROAD build degrades to "no telemetry" instead of
breaking the view. The formats here were read from real OpenLane 2.3.10
runs (gcd on sky130hd), not recalled.
"""
from __future__ import annotations

import re

# ---- global placement ---------------------------------------------------
_NESTEROV = re.compile(
    r"\[NesterovSolve\]\s+Iter:\s+(\d+)\s+overflow:\s+([0-9.]+)\s+HPWL:\s+(\d+)")


def parse_gpl(text: str) -> dict:
    """Nesterov global placement: overflow falls toward the stop value
    (0.1 by default) while wirelength settles. Both series are returned so
    the view can draw how the solve is converging."""
    series = [(int(i), float(o), int(h)) for i, o, h in _NESTEROV.findall(text)]
    done = re.search(r"Finished with Overflow:\s*([0-9.]+)", text)
    out = {"series": series,
           "timing_reweights": len(re.findall(r"Timing-driven: executing resizer", text)),
           "finished": float(done.group(1)) if done else None}
    if series:
        out.update(iter=series[-1][0], overflow=series[-1][1], hpwl=series[-1][2])
    return out


# ---- timing repair (resizer) -------------------------------------------
_ROW = re.compile(r"^\s*(\d+\*?|final)\s*\|(.+)$", re.M)


def parse_resizer(text: str) -> dict:
    """The last row of the repair_timing / repair_design table, with its
    column names, plus the hold-buffer count the resizer reports."""
    out: dict = {}
    header = None
    for line in text.splitlines():
        if "Iteration" in line and "|" in line and "WNS" in line:
            header = [c.strip() for c in line.split("|")]
    if header:
        rows = [m for m in _ROW.finditer(text)]
        if rows:
            m = rows[-1]
            cells = [m.group(1)] + [c.strip() for c in m.group(2).split("|")]
            out["row"] = dict(zip(header, cells))
    hold = re.search(r"Found (\d+) endpoints with hold violations", text)
    ins = re.search(r"Inserted (\d+) hold buffers", text)
    if hold:
        out["hold_endpoints"] = int(hold.group(1))
    if ins:
        out["hold_buffers"] = int(ins.group(1))
    if "No setup violations found" in text:
        out["setup_clean"] = True
    if re.search(r"RSZ-0060", text):
        out["buffer_cap_hit"] = True
    return out


# ---- clock tree ---------------------------------------------------------
def parse_cts(text: str) -> dict:
    out: dict = {}
    for key, pat in (("sinks", r"Clock net \"[^\"]+\" has (\d+) sinks"),
                     ("buffers", r"Created (\d+) clock buffers"),
                     ("levels", r"Max level of the clock tree: (\d+)")):
        m = re.search(pat, text)
        if m:
            out[key] = int(m.group(1))
    return out


# ---- global routing -----------------------------------------------------
def parse_grt(text: str) -> dict:
    out: dict = {}
    m = re.search(r"^Total\s+\d+\s+\d+\s+([0-9.]+)%\s+(\d+)\s*/\s*(\d+)\s*/\s*(\d+)", text, re.M)
    if m:
        out["usage_pct"] = float(m.group(1))
        out["overflow"] = int(m.group(4))
    w = re.search(r"Total wirelength:\s*(\d+)\s*um", text)
    if w:
        out["wirelength_um"] = int(w.group(1))
    if re.search(r"GRT-0101", text):
        out["extra_iterations"] = True
    return out


# ---- detailed routing ---------------------------------------------------
def parse_drt(text: str) -> dict:
    """TritonRoute's optimisation iterations. `history` holds the violation
    count each completed iteration ended on - the curve that shows whether
    routing is converging (73 -> 28 -> 41 -> 0 on gcd)."""
    history = [int(v) for v in re.findall(r"Number of violations\s*=\s*(\d+)", text)]
    starts = re.findall(r"Start (\d+)(?:st|nd|rd|th) optimization iteration", text)
    out: dict = {"history": history}
    if starts:
        out["iteration"] = int(starts[-1])
    tail = text[text.rfind("Start "):] if "Start " in text else text
    m = list(re.finditer(r"Completing (\d+)% with (\d+) violations", tail))
    if m and len(history) <= (int(starts[-1]) if starts else 0):
        out["pct"] = int(m[-1].group(1))
        out["violations"] = int(m[-1].group(2))
    elif history:
        out["violations"] = history[-1]
    return out


# step-directory slug -> parser. Matched on the slug's start so that
# "openroad-resizertimingpostcts" and "...postgrt" share one entry.
PARSERS = (
    ("openroad-globalplacement", "GPL", parse_gpl),   # not ...skipio
    ("openroad-repairdesign", "RSZ", parse_resizer),
    ("openroad-resizertiming", "RSZ", parse_resizer),
    ("openroad-cts", "CTS", parse_cts),
    ("openroad-globalrouting", "GRT", parse_grt),
    ("openroad-detailedrouting", "DRT", parse_drt),
)


def parser_for(slug: str):
    """(label, parser) for a step slug such as 'openroad-cts', or None."""
    if slug.startswith("openroad-globalplacementskipio"):
        return None
    for prefix, label, fn in PARSERS:
        if slug.startswith(prefix):
            return label, fn
    return None


# ---- placement density from a DEF --------------------------------------
_SKIP_MASTER = re.compile(r"(tapvpwrvgnd|decap|fill|phy_|endcap|lpflow_)", re.I)


def density_grid(def_text: str, cols: int = 48, rows: int = 12) -> dict | None:
    """Coarse map of where the movable standard cells sit.

    Counts instance origins of PLACED components (taps and decaps are
    FIXED and uninformative) into a cols x rows grid over the die. Returns
    None for a DEF with no die area or no placed cells, which is what the
    floorplan-stage DEFs look like.
    """
    units = re.search(r"UNITS DISTANCE MICRONS\s+(\d+)", def_text)
    die = re.search(r"DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", def_text)
    if not die:
        return None
    x0, y0, x1, y1 = (int(g) for g in die.groups())
    if x1 <= x0 or y1 <= y0:
        return None
    grid = [[0] * cols for _ in range(rows)]
    n = 0
    section = def_text.split("COMPONENTS", 1)[-1].split("END COMPONENTS", 1)[0]
    for chunk in section.split(";"):
        m = re.search(r"-\s+\S+\s+(\S+).*?\+\s+PLACED\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)", chunk, re.S)
        if not m or _SKIP_MASTER.search(m.group(1)):
            continue
        cx = min(cols - 1, max(0, int((int(m.group(2)) - x0) / (x1 - x0) * cols)))
        cy = min(rows - 1, max(0, int((int(m.group(3)) - y0) / (y1 - y0) * rows)))
        grid[rows - 1 - cy][cx] += 1  # y grows upward on a die, downward on a screen
        n += 1
    if not n:
        return None
    return {"grid": grid, "cells": n, "die_um": [(x1 - x0) / int(units.group(1)) if units else x1 - x0,
                                                 (y1 - y0) / int(units.group(1)) if units else y1 - y0]}


_SHADE_UNICODE = " ·░▒▓█"
_SHADE_ASCII = " .:+#@"


def render_grid(info: dict, ascii_only: bool = False) -> list[str]:
    """The grid as text lines, shaded by cells-per-bin relative to the
    busiest bin."""
    shades = _SHADE_ASCII if ascii_only else _SHADE_UNICODE
    peak = max((max(r) for r in info["grid"]), default=0) or 1
    lines = []
    for row in info["grid"]:
        lines.append("".join(
            shades[0] if c == 0 else shades[1 + min(len(shades) - 2, (c * (len(shades) - 1)) // (peak + 1))]
            for c in row))
    return lines


# ---- small drawing helpers ---------------------------------------------
_BARS_UNICODE = "▁▂▃▄▅▆▇█"
_BARS_ASCII = "_.-~=+*#"


def sparkline(values: list[float], width: int = 24, ascii_only: bool = False) -> str:
    """The last `width` values as a one-line chart, scaled to their own
    range. A flat series draws as the lowest bar rather than dividing by
    zero."""
    vals = list(values)[-width:]
    if not vals:
        return ""
    bars = _BARS_ASCII if ascii_only else _BARS_UNICODE
    lo, hi = min(vals), max(vals)
    span = hi - lo
    return "".join(bars[0] if span == 0 else bars[min(len(bars) - 1, int((v - lo) / span * (len(bars) - 1) + 0.5))]
                   for v in vals)


def bar(fraction: float, width: int = 24, ascii_only: bool = False) -> str:
    fraction = max(0.0, min(1.0, fraction))
    full, empty = ("#", "-") if ascii_only else ("█", "░")
    n = int(round(fraction * width))
    return full * n + empty * (width - n)
