r"""Finds clock domains the run never actually constrained.

The gap this closes is OpenLane's own, and OpenLane states it plainly.
Its default SDC (openlane/scripts/base.sdc) contains:

    } elseif { $port_count != "1" } {
        puts "\[WARNING] Multi-clock files are not currently supported by
              the base SDC file. Only the first clock will be constrained."
    }
    set ::clock_port [lindex $::env(CLOCK_PORT) 0]

So a design declaring two clock ports gets exactly one `create_clock`.
Every path in the second domain — including any clock-domain crossing
into it — is analysed by nobody. STA then reports zero setup and zero
hold violations, entirely truthfully, because it was never asked about
those paths. A verdict that reads those zeros as a pass is signing off a
domain it never looked at.

That is the same shape as the absent-signoff-metric bug: not a wrong
number, a missing check wearing a clean number's clothes. So an
unconstrained clock domain is reported as *unverified* rather than as a
violation — nothing found it broken; nothing looked.

Deliberately narrow about what it claims. This is not structural CDC
analysis: it does not look for two-flop synchronizers, gray coding, or
metastability hazards, and it must never be read as "CDC clean". It
answers one question that is answerable from the run's own artifacts —
which declared clocks did the timing engine actually constrain — and
that question happens to be the one that silently invalidates the whole
timing signoff.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# OpenLane echoes the clock it settled on, once per step that sources the
# SDC: "[INFO] Using clock clk_a…" (note the non-ASCII ellipsis).
_USING_CLOCK = re.compile(r"\[INFO\]\s*Using clock\s+(\S+?)\s*(?:…|\.\.\.)\s*$", re.M)
_MULTI_CLOCK_WARNING = re.compile(
    r"\[WARNING\]\s*(Multi-clock files are not currently supported[^\n]*)", re.M
)


def declared_clock_ports(design_dir: Path | str) -> list[str]:
    """Clock ports the design asks OpenLane to constrain.

    CLOCK_PORT is whitespace-separated in OpenLane's config (base.sdc does
    `llength` on it), and may also be given as a real JSON list.
    """
    cfg_path = Path(design_dir) / "config.json"
    if not cfg_path.is_file():
        return []
    raw = json.loads(cfg_path.read_text(encoding="utf-8")).get("CLOCK_PORT")
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(p) for p in raw]
    return str(raw).split()


def constrained_clocks(run_dir: Path | str) -> list[str]:
    """Clocks the run really created, read from its own step logs.

    Taken from the logs rather than inferred from config, because the
    whole point is that what OpenLane did and what the design asked for
    can differ silently.
    """
    found: list[str] = []
    for log in sorted(Path(run_dir).glob("*/*.log")):
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name in _USING_CLOCK.findall(text):
            if name not in found:
                found.append(name)
    return found


def multi_clock_warnings(run_dir: Path | str) -> list[str]:
    """OpenLane's own warning text, quoted rather than paraphrased."""
    seen: list[str] = []
    for log in sorted(Path(run_dir).glob("*/*.log")):
        try:
            text = log.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for w in _MULTI_CLOCK_WARNING.findall(text):
            w = w.strip()
            if w not in seen:
                seen.append(w)
    return seen


def has_custom_sdc(design_dir: Path | str) -> bool:
    """Whether the design supplies its own SDC.

    A design that brings its own constraints may well handle multiple
    clocks correctly, and flagging it would be a false alarm — so the
    finding is reported but not treated as unverified in that case.
    """
    cfg_path = Path(design_dir) / "config.json"
    if not cfg_path.is_file():
        return False
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    return any(cfg.get(k) for k in ("PNR_SDC_FILE", "SIGNOFF_SDC_FILE",
                                     "FALLBACK_SDC_FILE", "BASE_SDC_FILE"))


def check(design_dir: Path | str, run_dir: Path | str) -> dict:
    """What the run constrained, against what the design declared."""
    declared = declared_clock_ports(design_dir)
    constrained = constrained_clocks(run_dir)
    custom_sdc = has_custom_sdc(design_dir)

    # Only count a declared clock as unconstrained when we positively saw
    # which clocks were used. If no log said "Using clock" at all — an
    # older OpenLane, a run that died early — we know nothing, and
    # inventing a finding from that silence would be the same mistake
    # this module exists to catch.
    unconstrained: list[str] = []
    if constrained and not custom_sdc:
        unconstrained = [c for c in declared if c not in constrained]

    return {
        "declared_clocks": declared,
        "constrained_clocks": constrained,
        "unconstrained_clocks": unconstrained,
        "custom_sdc": custom_sdc,
        "warnings": multi_clock_warnings(run_dir),
        # Stated explicitly so no reader mistakes a clean result here for
        # a CDC signoff. Nothing in this toolchain checks synchronizers.
        "note": ("constraint coverage only — no structural CDC analysis "
                 "(synchronizers, gray coding, metastability) is performed"),
    }


def unverified_domains(result: dict) -> list[str]:
    """Phrases for score()'s `unverified` list, one per unchecked domain."""
    return [
        f"timing for clock domain '{clk}' (declared but never constrained, "
        f"so no path in it was analysed)"
        for clk in result.get("unconstrained_clocks", [])
    ]
