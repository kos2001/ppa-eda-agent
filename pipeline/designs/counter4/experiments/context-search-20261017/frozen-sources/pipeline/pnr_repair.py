#!/usr/bin/env python3
r"""Repairs read off OpenROAD's own words.

propose_repairs() had four patterns, and every one of them was added
after a human read a failure and found the answer. Meanwhile 31 of the
484 recorded candidates died with a placement or timing-repair error that
no pattern matched — and in several of them the tool had already said what
to do:

    [GPL-0302] Use a higher -density or re-floorplan with a larger core area.
    Given target density: 0.40
    Suggested target density: 0.42

    [GPL-0307] RePlAce divergence detected.
    Re-run with a smaller max_phi_cof value.

    [GPL-0301] Utilization 122.025 % exceeds 100%.

That is a repair with its magnitude attached. Guessing a step size (the
existing 15-point utilization step) when the tool states the number it
wants is throwing information away, so this module reads it back.

Each rule is bounded and refuses to propose a candidate identical to the
one that failed. Rules cover only failures that appear in reference-db or
were reproduced by a real run; the evidence for each is in RULES, and
`validated` says whether a real repaired run has since confirmed it. A
rule whose repair has not been seen to work is still listed, but flagged,
so the table never claims more than the runs show (soul.md: promote a
pattern only once a real run has shown it working).

What this deliberately does not do: loosen a constraint to make a check
pass. Raising MAX_TRANSITION_CONSTRAINT closed aes's slew violations, but
only by changing what was being checked; a repair that edits the test is
not a repair.
"""
from __future__ import annotations

import math
import re

# One source of truth for the utilization floor and step; orchestrator
# re-exports these under its historical names.
UTIL_STEP_DOWN = 15
MIN_CORE_UTIL = 20

# GPL-0302: the tool's suggestion is the density at which global
# placement can start; a little above it leaves the legaliser and the
# resizer room instead of landing exactly on the edge.
DENSITY_HEADROOM_PCT = 2
MAX_TARGET_DENSITY_PCT = 95

# GPL-0301: grow a fixed die until the reported utilization would fall to
# this, then add margin because the core is smaller than the die.
GROW_TARGET_UTIL_PCT = 50.0
DIE_GROW_MARGIN = 1.1
MIN_DIE_GROWTH = 1.25

# GPL-0307: OpenROAD's -max_phi_coef documents 1.0-1.2 with a default of
# 1.05; the message asks for a smaller one. The step is 0.04 because a real
# replay of the one recorded divergence (counter4_tinydie on gf180 9t)
# still diverged at 1.03 and converged at 1.01: a 0.02 step spent a run
# learning that. The next step clamps to the documented floor of 1.0.
PHI_DEFAULT = 1.05
PHI_STEP = 0.04
PHI_FLOOR = 1.0

# RSZ-0060: the resizer stopped because it had inserted its allowance of
# buffers, expressed as a percentage of instances. OpenLane's default is
# 50. Cap well short of "unlimited" so a design that genuinely needs more
# buffers than cells still escalates.
BUFFER_PCT_DEFAULT = 50
BUFFER_PCT_STEP = 50
BUFFER_PCT_CAP = 150


def _fatal_text(result: dict) -> str:
    """Error text without WARNING lines.

    Same filter as orchestrator.classify_stage and for the same reason:
    the captured tail carries incidental warnings from earlier stages that
    can name a code the run did not actually die of.
    """
    return "\n".join(ln for ln in (result.get("error") or "").splitlines()
                     if "WARNING" not in ln)


def _num(*candidates):
    for c in candidates:
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            return c
    return None


def effective(result: dict, base_config: dict | None, key: str):
    """The value the failed run actually used: its own override if it had
    one, else the design's config.json. propose_repairs() used to read
    overrides only, so a candidate that failed on the design's default
    FP_CORE_UTIL had nothing to step down and escalated for no reason."""
    ov = result.get("overrides") or {}
    return ov[key] if key in ov else (base_config or {}).get(key)


def _with(result: dict, **delta) -> dict:
    return {**(result.get("overrides") or {}), **delta}


# --- rules ---------------------------------------------------------------
#
# Each returns {"code", "overrides", "why"} or None. `overrides` is the
# complete set for the repaired candidate, not just the change.

def gpl_density(result, base_config):
    """GPL-0302: target density below what the design needs to place."""
    text = _fatal_text(result)
    if "GPL-0302" not in text:
        return None
    m = re.search(r"Suggested target density:\s*([0-9.]+)", text)
    if not m:
        return None
    given = re.search(r"Given target density:\s*([0-9.]+)", text)
    needed = math.ceil(float(m.group(1)) * 100) + DENSITY_HEADROOM_PCT
    if needed > MAX_TARGET_DENSITY_PCT:
        return None  # the core is simply too small; density cannot fix that
    current = _num(effective(result, base_config, "PL_TARGET_DENSITY_PCT"))
    if current is not None and needed <= current:
        return None
    return {"code": "GPL-0302",
            "overrides": _with(result, PL_TARGET_DENSITY_PCT=needed),
            "why": (f"target density {float(given.group(1)) * 100:.0f}% " if given
                    else "target density ") +
                   f"is below the design's own utilization; the tool suggests "
                   f"{float(m.group(1)) * 100:.0f}%, so use {needed}%"}


def gpl_overfull(result, base_config):
    """GPL-0301: the cells do not fit in the core at all."""
    text = _fatal_text(result)
    m = re.search(r"GPL-0301\]\s+Utilization\s+([0-9.]+)\s*%", text)
    if not m:
        return None
    util = float(m.group(1))
    die = effective(result, base_config, "DIE_AREA")
    if not (isinstance(die, list) and len(die) == 4
            and all(_num(v) is not None for v in die)):
        # Relative sizing derives the core from FP_CORE_UTIL, so this error
        # cannot come from there; with no fixed die there is nothing here
        # to grow, and guessing a different knob would be a guess.
        return None
    x0, y0, x1, y1 = die
    scale = max(MIN_DIE_GROWTH, math.sqrt(util / GROW_TARGET_UTIL_PCT) * DIE_GROW_MARGIN)
    grown = [x0, y0, x0 + math.ceil((x1 - x0) * scale), y0 + math.ceil((y1 - y0) * scale)]
    return {"code": "GPL-0301", "overrides": _with(result, DIE_AREA=grown),
            "why": f"utilization {util:.1f}% > 100% on a fixed {x1 - x0}x{y1 - y0} um die; "
                   f"scaled each side by {scale:.2f} toward {GROW_TARGET_UTIL_PCT:.0f}%"}


def gpl_divergence(result, base_config):
    """GPL-0307: the Nesterov solver diverged; the tool asks for a smaller
    max_phi_cof."""
    if "GPL-0307" not in _fatal_text(result):
        return None
    current = _num(effective(result, base_config, "PL_MAX_PHI_COEFFICIENT"), PHI_DEFAULT)
    smaller = max(PHI_FLOOR, round(current - PHI_STEP, 4))
    if smaller >= current:
        return None
    return {"code": "GPL-0307",
            "overrides": _with(result, PL_MAX_PHI_COEFFICIENT=smaller),
            "why": f"RePlAce diverged; lower the phi upper bound {current} -> {smaller}"}


def _step_util(result, base_config, code, why):
    util = _num(effective(result, base_config, "FP_CORE_UTIL"))
    if util is None:
        return None
    # A fixed die ignores FP_CORE_UTIL, so stepping it would re-run the
    # same layout under a different name.
    die = effective(result, base_config, "DIE_AREA")
    sizing = effective(result, base_config, "FP_SIZING")
    if isinstance(die, list) and sizing == "absolute":
        return None
    repaired = max(MIN_CORE_UTIL, util - UTIL_STEP_DOWN)
    if repaired >= util:
        return None
    return {"code": code, "overrides": _with(result, FP_CORE_UTIL=repaired),
            "why": f"{why}; utilization {util} -> {repaired}"}


def legalization_failed(result, base_config):
    """DPL-0036: detailed placement could not legalise every instance —
    the rows are too full once CTS and timing repair have added cells."""
    if "DPL-0036" not in _fatal_text(result):
        return None
    return _step_util(result, base_config, "DPL-0036",
                      "legalisation failed for lack of free sites")


def pdn_default_util(result, base_config):
    """PDN-0185 on a run that never overrode utilization: the same repair
    as orchestrator's pattern 1, which only saw explicit overrides."""
    if "Insufficient width" not in _fatal_text(result):
        return None
    if "FP_CORE_UTIL" in (result.get("overrides") or {}):
        return None  # pattern 1 already handles this one
    return _step_util(result, base_config, "PDN-0185",
                      "power-strap width insufficient at the design's default density")


_STEP_PREFIX = {"ResizerTimingPostCTS": "PL_RESIZER", "ResizerTimingPostGRT": "GRT_RESIZER"}

# OpenLane's default hold margins (ns) for the two timing-repair steps.
HOLD_MARGIN_DEFAULT = {"PL_RESIZER": 0.1, "GRT_RESIZER": 0.05}


def hold_buffer_allowance(result, base_config):
    """RSZ-0060 during hold repair: the resizer ran out of buffers.

    The allowance is not the cause. Hold repair aims for `margin` ns of
    slack beyond zero (0.1 ns by default after CTS), and on a small design
    that over-fixes: it inserts hold buffers until the cap, then dies. The
    signoff check is hold slack >= 0 at every corner, which score() still
    enforces, so the margin is headroom this pipeline pays for in area.

    Measured on spm / sky130_fd_sc_hs at FP_CORE_UTIL 55, where the run
    died at RSZ-0060 after inserting 215 hold buffers:

        margin 0.10 (default)  dies, RSZ-0060
        margin 0.05            completes, 127 hold buffers, 5906 um^2
        margin 0.00            completes, 32 hold buffers, 5296 um^2, passes
        FP_CORE_UTIL 40        completes, 221 hold buffers, 6520 um^2, passes
        cap 100%               completes, ends at 6520 um^2 after a utilization step

    so the margin is tried first: it passes at the original utilization, in
    19% less cell area and a 28% smaller core than stepping utilization
    down. Only when the margin is already zero does the cap move.

    Hold only. A setup-side RSZ-0060 (RSZ-0062 precedes it) has never been
    observed in the store, and a rule is promoted by a failure that was
    seen, not by symmetry.
    """
    text = _fatal_text(result)
    if "RSZ-0060" not in text:
        return None
    step = re.search(r"OpenROAD\.(ResizerTimingPost(?:CTS|GRT))", text)
    # The warning naming the phase is a WARNING line, which _fatal_text
    # drops, so look for it in the raw output.
    if not step or "RSZ-0064" not in (result.get("error") or ""):
        return None
    prefix = _STEP_PREFIX[step.group(1)]
    margin_key = f"{prefix}_HOLD_SLACK_MARGIN"
    margin = _num(effective(result, base_config, margin_key),
                  HOLD_MARGIN_DEFAULT[prefix])
    if margin > 0:
        return {"code": "RSZ-0060",
                "overrides": _with(result, **{margin_key: 0.0}),
                "why": f"hold repair used up its buffer allowance chasing {margin} ns of "
                       f"margin past zero; {margin_key} -> 0 (signoff still requires "
                       f"hold slack >= 0 at every corner)"}
    cap_key = f"{prefix}_HOLD_MAX_BUFFER_PCT"
    current = _num(effective(result, base_config, cap_key), BUFFER_PCT_DEFAULT)
    raised = current + BUFFER_PCT_STEP
    if raised > BUFFER_PCT_CAP:
        return None
    return {"code": "RSZ-0060", "overrides": _with(result, **{cap_key: raised}),
            "why": f"hold repair hit its buffer allowance ({current}% of instances) "
                   f"with no margin left to give; {cap_key} -> {raised}"}


# `validated`: where a real run of the repaired candidate is recorded;
# None means the rule is reasoned from the tool's message and the store but
# not yet seen to work (PDN-0185 at the default utilization is the same
# step as the older proven pattern and only unit-tested). GPL-0301's replay
# gets past the error but the design then stops on its CDC check by design.
# See docs/pnr-algorithm-review-20261002.md.
RULES = [
    {"fn": gpl_density, "code": "GPL-0302", "validated": "reference-db/pnr_repair_checks.json, 2026-10-02",
     "seen_in": "counter4 and spm, PL_TARGET_DENSITY_PCT=40"},
    {"fn": gpl_overfull, "code": "GPL-0301", "validated": "reference-db/pnr_repair_checks.json, 2026-10-02",
     "seen_in": "cdc_twoclock on gf180mcuD, fixed 60x60 um die at 122%"},
    {"fn": gpl_divergence, "code": "GPL-0307", "validated": "reference-db/pnr_repair_checks.json, 2026-10-02",
     "seen_in": "counter4_tinydie on gf180 9t, 128x128 um die"},
    {"fn": legalization_failed, "code": "DPL-0036", "validated": "reference-db/pnr_repair_checks.json, 2026-10-02",
     "seen_in": "9 runs: counter4/gcd on gf180, gcd and counter4 on sky130 hs"},
    {"fn": pdn_default_util, "code": "PDN-0185", "validated": None,
     "seen_in": "candidates failing PDN at the design's own FP_CORE_UTIL"},
    {"fn": hold_buffer_allowance, "code": "RSZ-0060", "validated": "reference-db/pnr_repair_checks.json, 2026-10-02",
     "seen_in": "gcd and spm on sky130 hs"},
]


def repair(result: dict, base_config: dict | None = None) -> dict | None:
    """The first rule that recognises this failure, or None."""
    if not result.get("error"):
        return None
    for rule in RULES:
        got = rule["fn"](result, base_config)
        if got is not None:
            return got
    return None
