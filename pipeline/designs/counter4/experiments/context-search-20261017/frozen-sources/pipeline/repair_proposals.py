"""Mechanical repair proposals: turns real failures into the next candidate set.

Only failure signatures this pipeline has observed and verified a repair for
are handled; everything else is left to a person or the feedback-optimizer.
Pure functions over result dictionaries. Split out of orchestrator.py, which
re-exports these names for its callers.
"""
import math

import pnr_repair


# Known, real failure signatures this pipeline has actually observed and
# verified a repair for — see reference-db/cases/*.json for each one's
# full evidence. propose_repairs() stays deliberately narrow: anything
# not listed here is left for a human or the feedback-optimizer /
# placement-strategist subagents to diagnose, rather than guessed at
# (see the "Known limitations" section of the design spec).

# 1. counter4__2026-08-21: OpenROAD's PDN generator errors out rather
#    than degrading gracefully when core utilization is pushed too high
#    for the die's power-strap geometry.
PDN_STRAP_ERROR = "Insufficient width"
UTIL_STEP_DOWN = pnr_repair.UTIL_STEP_DOWN  # 15 percentage points;
                      # conservative, matches the gap that separated the
                      # one passing candidate (35) from the first failing
                      # one (55) in that case. Defined in pnr_repair so the
                      # DPL-0036 repair steps by the same amount.
MIN_CORE_UTIL = pnr_repair.MIN_CORE_UTIL

# 2. counter4_tinydie__2026-08-21: OpenROAD's Floorplan Init step
#    rejects a DIE_AREA whose core area (after subtracting core margins)
#    is zero or negative — the die is structurally too small to fit
#    even the margins, before any cell placement is attempted. Distinct
#    from #1: this fails at a much earlier stage (Floorplan Init, before
#    placement/PDN), and the repair is DIE_AREA itself, not utilization.
DIE_TOO_SMALL_ERROR = "core_area"
DIE_AREA_GROWTH_FACTOR = 2  # doubles width/height each iteration; simple
                            # and matches the real counter4_tinydie case
                            # (8x8um -> 16x16um converged in one step)

# 3. aes__2026-08-30__145637: a candidate that completes the flow and
#    fails setup — and only setup. The repair is not a guess: the run
#    itself measured the period it needs. operating_point() derives
#    min_period per corner from the run's own slack, and aes showed the
#    relationship holds end to end: 481 setup violations at 6 ns, 3 at
#    11.2 ns (ss min_period 11.45), 0 at 12 ns. The margin covers the
#    fact that re-closing at a new period changes synthesis and sizing,
#    so the measured min_period is a floor rather than an exact answer
#    (operating_point.py's first stated limit).
#
#    Fires only when setup is the *whole* failure. Hold does not move
#    with the period (aes at 12 ns: setup 0, hold 264), and DRV or
#    antenna counts have their own causes — proposing a period change
#    against those would burn a run to learn what the case already says.
SETUP_PERIOD_MARGIN = 0.05
SETUP_PERIOD_STEP_NS = 0.1  # OpenLane takes any float; rounded up to a
                            # tenth so tags stay readable and two runs a
                            # rounding error apart are not both made.


def setup_period_repair(result: dict) -> float | None:
    """The CLOCK_PERIOD a setup-only failure measured itself needing,
    or None when this result is not that case."""
    if result.get("error"):
        return None
    verdict = result.get("verdict") or {}
    violations = verdict.get("violations") or []
    if not violations or any("setup" not in v for v in violations):
        return None
    op = verdict.get("operating_point") or {}
    period = op.get("clock_period_ns")
    needed = [c["min_period_ns"] for c in op.get("corners", [])
              if isinstance(c.get("min_period_ns"), (int, float))]
    if not isinstance(period, (int, float)) or not needed:
        return None
    worst = max(needed)
    if worst <= period:
        return None  # slack says it fits; the violation is not a period problem
    repaired = worst * (1 + SETUP_PERIOD_MARGIN)
    repaired = math.ceil(repaired / SETUP_PERIOD_STEP_NS) * SETUP_PERIOD_STEP_NS
    return round(repaired, 3)


def _repaired(result: dict, iteration: int, overrides: dict) -> dict:
    """A repair candidate built from the failed one — same technology.

    The repair used to carry only the tag and the overrides, and the
    first non-sky130 candidate to reach this function showed what that
    loses: gcd on gf180mcuD failed setup at 12 ns, the repair proposed
    13.2 ns with no `pdk`/`scl`, run_candidate() defaulted to
    sky130A/sky130_fd_sc_hd, and the sky130 run passed under a tag that
    said gf180. Area 12,133 -> 3,458 um^2 for a 1.2 ns period change was
    the tell. The technology is part of the candidate, not of the
    override set, so it has to be copied explicitly.
    """
    cand = {"tag": f"{result['tag']}-iter{iteration}", "overrides": overrides}
    for key in ("pdk", "scl"):
        if result.get(key):
            cand[key] = result[key]
    return cand


def propose_repairs(results: list[dict], iteration: int,
                    base_config: dict | None = None) -> list[dict]:
    """Mechanically proposes a repaired candidate set from real failures.

    `base_config` is the design's config.json. Patterns 1-4 read only the
    candidate's own overrides; the pnr_repair rules also consult it, so a
    candidate that failed on the design's default FP_CORE_UTIL or fixed
    DIE_AREA still has a value to repair.
    """
    next_candidates = []
    for r in results:
        error = r.get("error", "")
        overrides = r["overrides"]

        util_override = overrides.get("FP_CORE_UTIL")
        # The die the run actually used: its own override, else the design's
        # config.json. counter4_tinydie declares DIE_AREA [0,0,8,8] there, so
        # every candidate that only varied CLOCK_PERIOD (35 of them) died at
        # STA-0572 with a negative core area and was never repaired: both
        # die patterns below read the override alone and saw nothing to grow.
        die_area_override = overrides.get("DIE_AREA", (base_config or {}).get("DIE_AREA"))

        # A candidate that completed the whole flow but missed a target
        # this pipeline set (see score()) has no `error` at all, so every
        # pattern below — all of which read error text — was structurally
        # blind to it. That whole class of failure escalated straight to
        # a human despite being the most mechanically repairable kind:
        # the violation literally states the measured value and the
        # target it exceeded.
        #
        # Only the utilization violation is handled, and only because it
        # is not a guess: the repair is the same FP_CORE_UTIL step-down
        # already proven for PDN_STRAP_ERROR, and "utilization above
        # target" is definitionally addressed by asking for less of it.
        # The step is the existing conservative constant rather than one
        # scaled by the overshoot — requested FP_CORE_UTIL and achieved
        # stdcell utilization are different quantities (35 -> 0.604 in
        # counter4's real runs), so scaling by their ratio would assume a
        # relationship this pipeline has never measured. The bounded loop
        # re-measures instead.
        violations = (r.get("verdict") or {}).get("violations", [])
        overshoot = any(v.startswith("utilization ") for v in violations)
        if overshoot and isinstance(util_override, (int, float)):
            repaired = max(MIN_CORE_UTIL, util_override - UTIL_STEP_DOWN)
            if repaired == util_override:
                continue  # already at floor, no repair to propose
            new_overrides = dict(overrides)
            new_overrides["FP_CORE_UTIL"] = repaired
            next_candidates.append(_repaired(r, iteration, new_overrides))
        elif PDN_STRAP_ERROR in error and isinstance(util_override, (int, float)):
            repaired = max(MIN_CORE_UTIL, util_override - UTIL_STEP_DOWN)
            if repaired == util_override:
                continue  # already at floor, no repair to propose
            new_overrides = dict(overrides)
            new_overrides["FP_CORE_UTIL"] = repaired
            next_candidates.append(_repaired(r, iteration, new_overrides))
        elif DIE_TOO_SMALL_ERROR in error and isinstance(die_area_override, list) \
                and len(die_area_override) == 4:
            x0, y0, x1, y1 = die_area_override
            new_overrides = dict(overrides)
            new_overrides["DIE_AREA"] = [
                x0, y0,
                x0 + (x1 - x0) * DIE_AREA_GROWTH_FACTOR,
                y0 + (y1 - y0) * DIE_AREA_GROWTH_FACTOR,
            ]
            cand = _repaired(r, iteration, new_overrides)
            old_width, old_height = x1 - x0, y1 - y0
            new_width = new_overrides["DIE_AREA"][2] - new_overrides["DIE_AREA"][0]
            new_height = new_overrides["DIE_AREA"][3] - new_overrides["DIE_AREA"][1]
            cand["repair"] = {
                "code": "STA-0572",
                "why": (
                    f"absolute {old_width:g}x{old_height:g} um die produced a "
                    "non-positive core after floorplan margins; grow DIE_AREA "
                    f"to {new_width:g}x{new_height:g} um"
                ),
            }
            next_candidates.append(cand)
        elif PDN_STRAP_ERROR in error and isinstance(die_area_override, list) \
                and len(die_area_override) == 4:
            # Same PDN strap failure as pattern #1, but this candidate has
            # no FP_CORE_UTIL to step down (it's using the default) — an
            # explicit DIE_AREA is the knob available here instead. Real
            # case: counter4_tinydie's 16x16um candidate got past
            # Floorplan Init (pattern #2 fixed that) only to hit this
            # same PDN-0185 error with no FP_CORE_UTIL override present.
            x0, y0, x1, y1 = die_area_override
            new_overrides = dict(overrides)
            new_overrides["DIE_AREA"] = [
                x0, y0,
                x0 + (x1 - x0) * DIE_AREA_GROWTH_FACTOR,
                y0 + (y1 - y0) * DIE_AREA_GROWTH_FACTOR,
            ]
            next_candidates.append(_repaired(r, iteration, new_overrides))
        elif (period := setup_period_repair(r)) is not None:
            # Pattern #3 above: setup is the only failure and the run
            # measured the period it needs.
            new_overrides = dict(overrides)
            new_overrides["CLOCK_PERIOD"] = period
            next_candidates.append(_repaired(r, iteration, new_overrides))
        elif (fix := pnr_repair.repair(r, base_config)) is not None:
            # Placement / timing-repair failures whose error text carries
            # the answer (GPL-0302 even states the density to use). See
            # pnr_repair.RULES for each rule's evidence.
            cand = _repaired(r, iteration, fix["overrides"])
            cand["repair"] = {"code": fix["code"], "why": fix["why"]}
            next_candidates.append(cand)
        # Other failure/violation modes (DRC/LVS errors, hold or DRV
        # violations, unrecognized run errors) are not auto-repaired —
        # flagged in the iteration summary instead so a person or
        # feedback-optimizer can look at them.
    return next_candidates
