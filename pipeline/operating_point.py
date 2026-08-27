r"""Derives the operating point — Fmax and Vmin — from timing already measured.

Every number here comes out of metrics.json the pipeline was already
producing and discarding. score() read `timing__setup__wns`, which is
worst *negative* slack: OpenSTA floors it at 0, so a design with 6.85 ns
of margin and one with 0.01 ns both report exactly 0. The margin lives
in `timing__setup__ws`, which nothing in this pipeline had ever read.

That single unread key is the difference between "meets its 10 ns
constraint" and "this netlist's critical path is 3.15 ns, so it tops out
around 318 MHz at the nominal corner and 255 MHz at the slow one" — the
number a DTCO decision is actually made on.

Vmin comes from the corner names. OpenLane analyses each timing corner
as `<delay>_<process><temp>_<voltage>`, e.g. `nom_ss_100C_1v60`, so the
supply voltage of every corner is stated in the data: 1.60, 1.80, 1.95 V
here. The lowest of those where setup and hold both still pass is the
lowest voltage this design is shown to work at.

Two limits, stated because they are easy to overclaim past:

  * Fmax describes THIS netlist. Slack of +6.85 ns at a 10 ns period
    means the critical path takes 3.15 ns, so this placed-and-routed
    netlist can be clocked that fast. It does not predict what closing
    the design at 3.15 ns would produce — a tighter constraint changes
    synthesis, sizing and CTS, and generally yields a different (usually
    better) result. It is a measurement, not an extrapolation.

  * Vmin is the lowest *characterised* corner that passes, not a true
    Vmin. The PDK supplies three voltages; nothing here sweeps between
    or below them. If every corner passes, the honest statement is
    "≤ the lowest corner analysed", not "the design works at 1.6 V".
"""

from __future__ import annotations

import re

# nom_ss_100C_1v60 -> 1.60 V. The trailing "1v60" is the corner's supply.
_CORNER_VOLTAGE = re.compile(r"(\d+)v(\d+)")

_SETUP_WS = "timing__setup__ws__corner:"
_HOLD_WS = "timing__hold__ws__corner:"


def corner_voltage(corner: str) -> float | None:
    """Supply voltage encoded in a corner name, in volts."""
    m = _CORNER_VOLTAGE.search(corner)
    if not m:
        return None
    return float(f"{m.group(1)}.{m.group(2)}")


def corner_timing(metrics: dict) -> list[dict]:
    """Per-corner worst slack, with the achievable period each implies.

    Uses `__ws__` (worst slack) rather than `__wns__` (worst *negative*
    slack): the latter is clamped at 0, which erases exactly the margin
    this function exists to report.
    """
    out = []
    for key in sorted(metrics):
        if not key.startswith(_SETUP_WS):
            continue
        corner = key[len(_SETUP_WS):]
        setup_ws = metrics[key]
        hold_ws = metrics.get(f"{_HOLD_WS}{corner}")
        if not isinstance(setup_ws, (int, float)):
            continue
        out.append({
            "corner": corner,
            "voltage_v": corner_voltage(corner),
            "setup_ws_ns": setup_ws,
            "hold_ws_ns": hold_ws,
            # Both must hold for a corner to be usable. Hold failures do
            # not improve by slowing the clock, so a corner that fails
            # hold is unusable at any frequency — tracked separately
            # rather than folded into one boolean.
            "setup_ok": setup_ws >= 0,
            "hold_ok": hold_ws is None or hold_ws >= 0,
        })
    return out


def operating_point(metrics: dict, clock_period_ns: float | None) -> dict | None:
    """Fmax and Vmin for a completed run, or None when not derivable.

    Returns None rather than a guess when the design has no clock period
    (no CLOCK_PERIOD in config) or when the run produced no per-corner
    slack — an invented operating point is worse than none.
    """
    corners = corner_timing(metrics)
    if not corners:
        return None

    for c in corners:
        period = None
        if clock_period_ns is not None and c["setup_ws_ns"] is not None:
            period = clock_period_ns - c["setup_ws_ns"]
        c["min_period_ns"] = period
        c["fmax_mhz"] = (1000.0 / period) if period and period > 0 else None

    usable = [c for c in corners if c["setup_ok"] and c["hold_ok"]]

    # Signoff Fmax is the worst corner, not the best: the part has to
    # work everywhere it is specified to work.
    fmax_candidates = [c for c in usable if c["fmax_mhz"] is not None]
    limiting = min(fmax_candidates, key=lambda c: c["fmax_mhz"]) if fmax_candidates else None

    voltages = [c["voltage_v"] for c in usable if c["voltage_v"] is not None]
    analysed = [c["voltage_v"] for c in corners if c["voltage_v"] is not None]
    vmin = min(voltages) if voltages else None

    return {
        "clock_period_ns": clock_period_ns,
        "corners": corners,
        "fmax_mhz": limiting["fmax_mhz"] if limiting else None,
        "fmax_limiting_corner": limiting["corner"] if limiting else None,
        "vmin_v": vmin,
        # True when nothing failed, i.e. Vmin is a floor imposed by the
        # PDK's corner set rather than by this design. Saying "1.6 V"
        # flatly would claim a sweep nobody ran.
        "vmin_is_lowest_analysed": bool(
            vmin is not None and analysed and vmin == min(analysed)
        ),
        "failing_corners": [c["corner"] for c in corners
                            if not (c["setup_ok"] and c["hold_ok"])],
        "note": ("Fmax is this placed netlist's critical path, not a "
                 "prediction of re-closing at that period; Vmin is the "
                 "lowest characterised corner that passes, not a swept "
                 "minimum"),
    }
