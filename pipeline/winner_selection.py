"""Winner selection over scored candidates: the Pareto objective table and the
knee pick.

Pure functions over result dictionaries. One definition of "better" is shared
by winner selection and by polish (pnr_polish.improves). Split out of
orchestrator.py, which re-exports these names for its callers.
"""
import math

from pareto import ParetoPoint, pick_knee


def annotated_total_w(result: dict) -> float | None:
    """A candidate's measured total power, if it really was measured."""
    pa = (result.get("verdict") or {}).get("power_activity") or {}
    return (pa.get("annotated") or {}).get("total", {}).get("total_w")


def pick_winner(results: list[dict]) -> dict | None:
    """Picks the winner among passing candidates: the knee of the Pareto
    front over (cell area, power, core area, setup slack) — see
    pareto.pick_knee() and pareto_points(). Area, power, floorplan cost and
    timing margin are real, independent trade-offs among passing
    candidates (see reference-db/cases/*.json for real examples), not one
    axis to optimize alone. An objective that any passing candidate lacks
    is dropped for all of them rather than read as zero.
    """
    passing = [r for r in results if not r.get("not_evaluated") and not r.get("error")
               and r.get("verdict", {}).get("passed")]
    if not passing:
        return None
    if len(passing) == 1:
        return passing[0]

    winner_tag = pick_knee(pareto_points(passing))
    return next(r for r in passing if r["tag"] == winner_tag)


# The order objectives appear in a Pareto point, and the names polish
# applies its tolerances to. "margin" is minus the worst setup slack, so
# that every objective is something to minimise.
OBJECTIVES = ("area", "power", "core", "margin")


def pareto_points(passing: list[dict]) -> list[ParetoPoint]:
    """The objective vectors of passing candidates, all to be minimised.

    One definition of "better" shared by winner selection and by polish
    (pnr_polish.improves), so a move is judged on the same quantities a
    winner is chosen by. The optional objectives are decided over the
    given set, so comparing two candidates uses what both have.
    """
    return [ParetoPoint(key=tag, objs=tuple(o[n] for n in OBJECTIVES if n in o))
            for tag, o in objective_table(passing)]


def objective_table(passing: list[dict]) -> list[tuple[str, dict]]:
    """(tag, {objective name: value}) for each passing candidate."""
    # Rank on measured power when every passing candidate has it, and on
    # the estimate otherwise — never a mixture.
    #
    # This is the whole reason the choice is made here rather than
    # per-candidate. Annotated and vectorless numbers are not
    # interchangeable: on spm the same netlist reads 1.33e-03 W
    # estimated against 1.53e-03 W measured. Ranking a measured
    # candidate against an estimated one would compare a 15% offset
    # and call it a difference between designs, so a single candidate
    # whose simulation failed to compile would silently win the power
    # objective against candidates that are genuinely better.
    #
    # An all-or-nothing rule is safe because the testbench belongs to
    # the design, not the candidate: within one run_spec the candidates
    # either all have one or none do, and the mixed case only arises
    # when a simulation actually failed — exactly when the estimate is
    # the honest common basis.
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    use_annotated = all(finite(annotated_total_w(r)) for r in passing)
    use_power = use_annotated or all(finite((r["verdict"].get("power") or {}).get("total_w")) for r in passing)
    use_area = all(finite(r["verdict"].get("area_um2")) for r in passing)

    # Two objectives the ranking used to lack, each added only when every
    # passing candidate has it (the same all-or-nothing rule, for the same
    # reason: a missing number must not read as a good one).
    #
    # Core area: the floorplan's own cost. Utilization is the most swept
    # knob in the store and it moves instance area not at all (counter4:
    # 290.278 um^2 at FP_CORE_UTIL 25 and 35), so a ranking on instance
    # area is structurally blind to every placement-density decision.
    #
    # Setup slack: the old third objective was -worst_setup_wns, which is
    # 0 for all 159 passing candidates in the store — a constant, which
    # discriminates nothing. Real margin is the worst setup slack.
    use_core = all(finite(_core_area(r["verdict"])) for r in passing)
    use_slack = all(finite(_setup_slack(r["verdict"])) for r in passing)

    table = []
    for r in passing:
        v = r["verdict"]
        if use_annotated:
            power_total = annotated_total_w(r)
        else:
            power_total = (v.get("power") or {}).get("total_w")
        o = {}
        if use_area:
            o["area"] = v["area_um2"]
        if use_power:
            o["power"] = power_total
        if use_core:
            o["core"] = _core_area(v)
        if use_slack:
            o["margin"] = -_setup_slack(v)  # all objectives are minimised
        table.append((r["tag"], o))
    return table


def _core_area(verdict: dict) -> float | None:
    """Core area in um^2: recorded by score(), else rebuilt from instance
    area and utilization (design__instance__utilization__stdcell is
    exactly stdcell area over core area, checked on a gcd run:
    3004.13 / 0.452166 = 6643.9 = design__core__area)."""
    recorded = verdict.get("core_area_um2")
    if isinstance(recorded, (int, float)) and not isinstance(recorded, bool) and math.isfinite(recorded) and recorded > 0:
        return float(recorded)
    area, util = verdict.get("area_um2"), verdict.get("utilization")
    if (all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            for value in (area, util)) and area > 0 and util > 0):
        return area / util
    return None


def _setup_slack(verdict: dict) -> float | None:
    """Worst setup slack in ns: recorded by score(), else the minimum over
    the per-corner slacks of a stored operating point."""
    recorded = verdict.get("worst_setup_slack")
    if recorded is not None:
        return (float(recorded) if isinstance(recorded, (int, float)) and not isinstance(recorded, bool)
                and math.isfinite(recorded) else None)
    corners = (verdict.get("operating_point") or {}).get("corners") or []
    if any(not isinstance(c.get("setup_ws_ns"), (int, float)) or isinstance(c.get("setup_ws_ns"), bool)
           or not math.isfinite(c["setup_ws_ns"]) for c in corners):
        return None
    slacks = [c["setup_ws_ns"] for c in corners]
    return min(slacks) if slacks else None
