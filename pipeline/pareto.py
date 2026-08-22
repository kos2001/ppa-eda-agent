"""Constrained Pareto-front ranking for multi-objective candidate selection.

Adapted from github.com/kos2001/analog-layout-optimizer's `layout_opt/ppa.py`
(NSGA-II for op-amp PPA — power/area/GBW trade-offs, no single optimum).
This pipeline doesn't need the evolutionary half of NSGA-II (no crossover/
mutation/population generations — candidates here come from `expand_sweeps()`
and `propose_repairs()`, not a genetic search), but the *ranking* half —
constrained non-dominated sorting plus crowding distance, so "smallest area
wins" isn't the only voice when a design has genuine area/power/timing-margin
trade-offs among several real passing candidates — is directly reusable, and
kept dependency-free (pure Python) the same way the source does.
"""
from dataclasses import dataclass, field


@dataclass
class ParetoPoint:
    key: str          # candidate tag, or any identifier
    objs: tuple        # objectives, all to be MINIMIZED
    violation: float = 0.0   # 0 == feasible; already-passing candidates only
                              # use this pipeline, so it's always 0 here, but
                              # kept for fidelity with the source's
                              # constrained-domination logic.


def _dominates(a: ParetoPoint, b: ParetoPoint) -> bool:
    """Constrained domination: feasibility first, then Pareto on objectives."""
    if a.violation <= 1e-9 and b.violation > 1e-9:
        return True
    if a.violation > 1e-9 and b.violation <= 1e-9:
        return False
    if a.violation > 1e-9 and b.violation > 1e-9:
        return a.violation < b.violation
    le = all(x <= y for x, y in zip(a.objs, b.objs))
    lt = any(x < y for x, y in zip(a.objs, b.objs))
    return le and lt


def fast_nondominated_sort(pop: list[ParetoPoint]) -> list[list[int]]:
    n = len(pop)
    S = [[] for _ in range(n)]
    ndom = [0] * n
    fronts: list[list[int]] = [[]]
    for p in range(n):
        for q in range(n):
            if p == q:
                continue
            if _dominates(pop[p], pop[q]):
                S[p].append(q)
            elif _dominates(pop[q], pop[p]):
                ndom[p] += 1
        if ndom[p] == 0:
            fronts[0].append(p)
    i = 0
    while fronts[i]:
        nxt = []
        for p in fronts[i]:
            for q in S[p]:
                ndom[q] -= 1
                if ndom[q] == 0:
                    nxt.append(q)
        i += 1
        fronts.append(nxt)
    return fronts[:-1]


def crowding_distance(pop: list[ParetoPoint], idx: list[int]) -> dict[int, float]:
    dist = {i: 0.0 for i in idx}
    if len(idx) <= 2:
        return {i: float("inf") for i in idx}
    n_obj = len(pop[idx[0]].objs)
    for m in range(n_obj):
        idx.sort(key=lambda i: pop[i].objs[m])
        dist[idx[0]] = dist[idx[-1]] = float("inf")
        lo, hi = pop[idx[0]].objs[m], pop[idx[-1]].objs[m]
        span = hi - lo or 1.0
        for k in range(1, len(idx) - 1):
            dist[idx[k]] += (pop[idx[k + 1]].objs[m] - pop[idx[k - 1]].objs[m]) / span
    return dist


def pick_best(points: list[ParetoPoint]) -> str | None:
    """Rank-0 (Pareto-optimal) front, tie-broken by crowding distance
    (prefers the point that's most "distinct" from its front-mates — i.e.
    not clustered with near-identical trade-offs) — returns that point's key,
    or None if points is empty.
    """
    if not points:
        return None
    if len(points) == 1:
        return points[0].key
    fronts = fast_nondominated_sort(points)
    best_front = fronts[0]
    if len(best_front) == 1:
        return points[best_front[0]].key
    dist = crowding_distance(points, list(best_front))
    winner_idx = max(best_front, key=lambda i: dist[i])
    return points[winner_idx].key
