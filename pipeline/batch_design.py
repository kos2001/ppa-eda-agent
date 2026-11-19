#!/usr/bin/env python3
r"""Space-filling batch design for a run budget: Latin hypercube + maximin.

Borrowed from the two open-source tuners for this exact flow:

  * OpenROAD-flow-scripts' AutoTuner (tools/AutoTuner) seeds its Bayesian
    search with space-filling samples over declared parameter ranges.
  * ORFS-agent (Ghose, Kahng, Kundu, Wang; arXiv 2506.08332v3, 2026;
    github.com/ABKGroup/ORFS-Agent) gives its LLM a LatinHypercube tool and
    diversity selectors (EntropySelect / GraphSelect) that cut a large
    candidate pool down to exactly the K runs one parallel batch can
    afford.

What is borrowed is the *batch* step, not the Gaussian-process step.
surrogate.py measured that the store cannot yet support a fitted model:
almost nothing is evaluable, and the parameter swept most often does not
move area at all. A GP + Expected Improvement fitted to that would produce
confident numbers from noise. Space-filling design needs no model. It
spends a fixed budget so that the runs cover the declared space as evenly
as possible, and so that no run lands next to a configuration that is
already recorded. Exact repeats are never proposed. This also answers
search_strategy.py's caveat, "random search's real advantage ... is
reaching parts of a space a grid never visits": these candidates go
exactly there.

run_spec entry (expanded by candidate_plan.expand_sweeps):

    "design_of_experiments": {
      "space": {
        "FP_CORE_UTIL": {"low": 30, "high": 60, "type": "int"},
        "PL_TARGET_DENSITY_PCT": {"low": 40, "high": 80, "type": "int"},
        "CLOCK_PERIOD": {"low": 18.0, "high": 26.0, "type": "float", "digits": 1},
        "SYNTH_STRATEGY": {"values": ["AREA 0", "AREA 3", "DELAY 1"]}
      },
      "batch": 6, "pool": 200, "seed": 7, "tag_prefix": "doe",
      "overrides": {}
    }

Stdlib only, per soul.md.
"""
from __future__ import annotations

import json
import math
import random

DEFAULT_POOL = 200


def validate(space: dict) -> None:
    if not isinstance(space, dict) or not space:
        raise ValueError("design_of_experiments.space must be a non-empty object")
    for name, dim in space.items():
        if "values" in dim:
            if not isinstance(dim["values"], list) or not dim["values"]:
                raise ValueError(f"{name}: values must be a non-empty list")
            continue
        lo, hi = dim.get("low"), dim.get("high")
        if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (lo, hi)) \
                or not lo < hi:
            raise ValueError(f"{name}: needs numeric low < high")
        if dim.get("type", "float") not in ("int", "float"):
            raise ValueError(f"{name}: type must be int or float")


def _decode(dim: dict, u: float):
    """Map u in [0, 1) to a concrete parameter value."""
    if "values" in dim:
        vals = dim["values"]
        return vals[min(int(u * len(vals)), len(vals) - 1)]
    lo, hi = dim["low"], dim["high"]
    if dim.get("type", "float") == "int":
        return min(int(lo + u * (hi - lo + 1)), hi)
    return round(lo + u * (hi - lo), dim.get("digits", 3))


def latin_hypercube(space: dict, n: int, seed: int = 0) -> list[dict]:
    """n samples; each dimension's [0,1) range is cut into n strata, and
    every stratum is used exactly once per dimension."""
    validate(space)
    rng = random.Random(seed)
    cols = {}
    for name in space:
        perm = list(range(n))
        rng.shuffle(perm)
        cols[name] = [(p + rng.random()) / n for p in perm]
    return [{name: _decode(space[name], cols[name][i]) for name in space}
            for i in range(n)]


def _coord(space: dict, name: str, value) -> float | None:
    dim = space[name]
    if "values" in dim:
        try:
            return dim["values"].index(value) / max(len(dim["values"]) - 1, 1)
        except ValueError:
            return None
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return (value - dim["low"]) / (dim["high"] - dim["low"])


def distance(space: dict, a: dict, b: dict) -> float:
    """Normalized Euclidean distance over the declared dimensions.
    Categorical dimensions are 0 if the values are equal and 1 otherwise;
    a missing or out-of-space value counts as maximally far."""
    total = 0.0
    for name, dim in space.items():
        if "values" in dim:
            d = 0.0 if a.get(name) == b.get(name) else 1.0
        else:
            ca, cb = _coord(space, name, a.get(name)), _coord(space, name, b.get(name))
            d = 1.0 if ca is None or cb is None else abs(ca - cb)
        total += d * d
    return math.sqrt(total)


def _key(overrides: dict) -> str:
    return json.dumps(overrides, sort_keys=True)


def maximin_select(space: dict, pool: list[dict], k: int,
                   recorded: list[dict] | None = None) -> list[dict]:
    """Greedy farthest-point selection.

    Each pick maximizes its distance to the nearest point that is already
    chosen OR already recorded. Exact repeats of recorded or chosen
    configurations are dropped. Ties go to pool order, so a fixed seed
    gives a fixed result.
    """
    recorded = [r for r in (recorded or []) if isinstance(r, dict)]
    seen = {_key(r) for r in recorded}
    anchors = list(recorded)
    chosen: list[dict] = []
    candidates = []
    for p in pool:
        if _key(p) not in seen:
            seen.add(_key(p))
            candidates.append(p)
    while candidates and len(chosen) < k:
        if anchors:
            best_i = max(range(len(candidates)),
                         key=lambda i: min(distance(space, candidates[i], a) for a in anchors))
        else:
            # No anchor yet: start from the point nearest the centre, which is
            # deterministic, rather than an arbitrary corner.
            centre = {n: (d["values"][len(d["values"]) // 2] if "values" in d
                          else (d["low"] + d["high"]) / 2) for n, d in space.items()}
            best_i = min(range(len(candidates)),
                         key=lambda i: distance(space, candidates[i], centre))
        pick = candidates.pop(best_i)
        chosen.append(pick)
        anchors.append(pick)
    return chosen


def min_spacing(space: dict, points: list[dict]) -> float:
    """Smallest pairwise distance: the maximin criterion, for reporting."""
    if len(points) < 2:
        return float("inf")
    return min(distance(space, a, b) for i, a in enumerate(points) for b in points[i + 1:])


def expand(run_spec: dict, recorded_overrides: list[dict] | None = None,
           safe_tag=lambda s: s) -> list[dict]:
    """run_spec["design_of_experiments"] -> concrete candidates."""
    doe = run_spec.get("design_of_experiments")
    if not doe:
        return []
    space = doe["space"]
    validate(space)
    k = doe.get("batch")
    if not isinstance(k, int) or isinstance(k, bool) or k <= 0:
        raise ValueError("design_of_experiments.batch must be a positive integer")
    n = max(doe.get("pool", DEFAULT_POOL), k)
    base = doe.get("overrides") or {}
    pool = [{**base, **p} for p in latin_hypercube(space, n, doe.get("seed", 0))]
    recorded = [r for r in (recorded_overrides or [])]
    picks = maximin_select(space, pool, k, recorded)
    prefix = doe.get("tag_prefix", "doe")
    return [{"tag": safe_tag(f"{prefix}-{i + 1}"), "overrides": p,
             "origin": "design_of_experiments"} for i, p in enumerate(picks)]
