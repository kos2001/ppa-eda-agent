#!/usr/bin/env python3
r"""Would sampling candidates at random beat the sweep? Measured, offline.

The question arrived as "would Monte Carlo improve performance?" and
every word in it needs a number attached before it means anything here,
so this settles the one version the store can settle: given a budget of
k real OpenLane runs out of a design's recorded configuration space,
does drawing k at random find a smaller passing area than the first k of
the sweep the pipeline actually ran?

RETROSPECTIVE AND FREE. Every configuration this "runs" was really run —
the areas come from reference-db, not from a model — so the comparison
costs no OpenLane time and invents no numbers. Monte Carlo appears here
as the method of the *study* (repeated random draws over recorded
results), not as a new thing the pipeline does.

WHAT IT CANNOT DO, stated because the result is worthless without it:

  * It cannot evaluate a configuration nobody ran. Random search's real
    advantage in the literature is reaching parts of a space a grid
    never visits, and that advantage is invisible here by construction.
    This measures sample efficiency inside a space already enumerated,
    which is a strictly easier question and a strictly weaker claim.
  * The recorded space is itself a sweep, so it is dense and regular in
    exactly the way that flatters a grid.
  * An arm is one design on one technology. Areas differ 30x across
    designs, so regret is a percentage of that arm's own best; averaging
    micrometres would let spm decide the answer alone.

WHAT IT MEASURES is regret: how far the best configuration a budget
found sits above the best that existed, as a percentage. Zero means the
budget found the optimum. Lower is better.

A null result is a real answer. If drawing at random does no better than
running the sweep in order, that is worth knowing before anyone rewrites
candidate generation around a sampler.

Usage:
    search_strategy.py            # the comparison, as JSON
"""
from __future__ import annotations

import json
import random
import statistics
import sys
from pathlib import Path

import surrogate

# Below this an arm has no search to study: a budget is either most of
# the space or all of it, and both strategies trivially agree.
MIN_ARM = 8

# Budgets worth reporting. Each is a real cost — a candidate is minutes
# of OpenLane — so the interesting range is small, and 1 is included
# because "run one thing" is what a person short on time actually does.
BUDGETS = (1, 2, 3, 4, 6, 8)

# Enough draws that the third decimal stops moving; the whole study is
# arithmetic over a few hundred numbers, so there is no reason to be
# stingy.
TRIALS = 2000
SEED = 20260830


def arms(rows: list[dict], min_size: int = MIN_ARM) -> list[dict]:
    """One search space per (design, technology), in recorded order.

    Only candidates that passed signoff and have an area. A failing
    candidate often has the smallest area of all — being too small for
    what it had to fit is frequently why it failed — so grading a search
    against one would grade it against something nobody achieved.
    """
    groups: dict[tuple, list[float]] = {}
    for row in rows:
        if not row.get("passed") or row.get("area_um2") is None:
            continue
        key = (row.get("design"), row.get("scl"), row.get("pdk"))
        groups.setdefault(key, []).append(float(row["area_um2"]))

    out = []
    for (design, scl, pdk), areas in groups.items():
        if len(areas) < min_size:
            continue
        out.append({
            "design": design, "scl": scl, "pdk": pdk,
            # Recorded order is what a sweep would really have bought,
            # candidate by candidate.
            "areas": areas,
            "size": len(areas),
            "best": min(areas),
        })
    out.sort(key=lambda a: (-a["size"], a["design"]))
    return out


def regret(found: list[float], best: float) -> float | None:
    """How far the best of `found` sits above `best`, in percent.

    None when nothing was found: a budget that returned no passing
    candidate has not achieved zero regret, and scoring it as zero would
    reward failing to look.
    """
    if not found:
        return None
    return (min(found) - best) / best * 100.0


def random_regret(arm: dict, budget: int, trials: int = TRIALS,
                  seed: int = SEED) -> float:
    """Mean regret of drawing `budget` configurations uniformly at random.

    Without replacement, because running the same configuration twice
    buys nothing — the pipeline already dedupes on exactly that.
    """
    areas = arm["areas"]
    k = min(budget, len(areas))
    rng = random.Random(seed)
    seen = [regret(rng.sample(areas, k), arm["best"]) for _ in range(trials)]
    return round(statistics.fmean(seen), 4)


def sweep_regret(arm: dict, budget: int) -> float:
    """Regret of taking the first `budget` of the recorded order.

    The pipeline runs a sweep in the order its spec lists, so the first k
    is what a budget of k really bought. Deterministic — there is nothing
    to average.
    """
    taken = arm["areas"][:budget]
    return round(regret(taken, arm["best"]) or 0.0, 4)


def compare(trials: int = TRIALS, seed: int = SEED,
            refdb: Path | str = surrogate.REFDB) -> dict:
    """The whole study: both strategies at every budget, over every arm."""
    found = arms(surrogate.load_dataset(refdb))

    budgets = []
    for budget in BUDGETS:
        rand = [random_regret(a, budget, trials, seed) for a in found]
        sweep = [sweep_regret(a, budget) for a in found]
        budgets.append({
            "budget": budget,
            "random_regret_pct": round(statistics.fmean(rand), 4),
            "sweep_regret_pct": round(statistics.fmean(sweep), 4),
            # Per arm, so a single design cannot carry the mean unseen.
            "arms_where_random_wins": sum(1 for r, s in zip(rand, sweep) if r < s),
            "arms_where_sweep_wins": sum(1 for r, s in zip(rand, sweep) if s < r),
        })

    rand_mean = statistics.fmean(b["random_regret_pct"] for b in budgets)
    sweep_mean = statistics.fmean(b["sweep_regret_pct"] for b in budgets)
    margin = sweep_mean - rand_mean
    # A margin below this is noise dressed as a finding: these are area
    # percentages on a corpus whose own best-known improvements run
    # 0.15% to 6%, so a tenth of a percent is not a reason to rewrite
    # candidate generation.
    winner = ("tie" if abs(margin) < 0.1
              else "random" if margin > 0 else "sweep")

    return {
        "arms": [{k: a[k] for k in ("design", "scl", "pdk", "size", "best")}
                 for a in found],
        "budgets": budgets,
        "verdict": {
            "winner": winner,
            "margin_pct": round(margin, 4),
            "random_mean_regret_pct": round(rand_mean, 4),
            "sweep_mean_regret_pct": round(sweep_mean, 4),
        },
        "limits": [
            "Only configurations that were really run can be drawn — "
            "random search's advantage at reaching unvisited regions is "
            "invisible here by construction.",
            "The recorded space is itself a sweep: dense and regular in "
            "the way that flatters a grid.",
            "Regret is per-arm percent; arms are not weighted by how "
            "much OpenLane time they cost.",
        ],
    }


def main() -> None:
    print(json.dumps(compare(), indent=2))


if __name__ == "__main__":
    sys.exit(main())
