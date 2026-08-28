r"""A surrogate model over reference-db, and an honest test of whether it works.

The appeal is obvious: a full OpenLane flow costs 60-100 s per candidate,
so a model that predicts the outcome from the config would let the agent
rank candidates for free. That is the standard DTCO surrogate idea and it
is a good one — *given data*.

This module exists mainly to find out whether that condition holds here,
and to keep answering that question as the dataset grows. It is built so
that the answer can be "no": `evaluate()` scores any predictor against a
do-nothing baseline with leave-one-out cross-validation, and `predict()`
refuses to return a number when the evidence does not support one.

The measurement moves as cases accumulate, which is the point of keeping
it in code rather than in a comment. It began at 17 distinct
(design, overrides) pairs across 4 designs — from 50 recorded runs, the
rest being re-runs of identical configs — and reached 19 within the same
session when a synthesis-exploration run added two. Nothing is yet
evaluable. Within counter4, area is 290.278 um^2 at FP_CORE_UTIL 25 and
at 35: the parameter most swept does not move the target at all. A model
trained on that would be fitting noise, and reporting an accuracy for it
would be inventing a result.

So the deliberate choice here is a k-nearest-neighbour predictor over
normalized features rather than anything deeper. Not because kNN is
clever, but because with a dataset this size it is honest: it can only
repeat outcomes that were actually observed, its errors are traceable to
specific neighbours, and it cannot manufacture a confident answer for a
region nobody has run. A neural network on this many points would
produce smoother numbers and no more knowledge.

What already substitutes for a surrogate in this pipeline, and works
today, is cheap *measurement* rather than prediction: screening to
SCREEN_STEP (10 s vs 95 s) and OpenLane's SynthesisExploration (9 s for
nine strategies vs ~10 min of full flows). Those return real numbers at
surrogate-like cost, which beats a model fitted to a couple of dozen
samples.

Stdlib only, per soul.md — no numpy, no sklearn.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"

# Below this many distinct configurations for a design, predict() will
# not return a value. Chosen to be conservative rather than tuned: with
# fewer neighbours than this the "prediction" is a lookup of one or two
# runs, and presenting that as a model output overstates it.
MIN_SAMPLES = 8

# Neighbours to average. Measured, not assumed: k=3 was a guess and it
# was costing accuracy on both targets. Leave-one-out across k=1..5 on
# the real store gives area MAE 2.389 at k=1 against 2.730 at k=3, and
# completion 92% accurate at k=1 against 88%. Larger k degrades
# monotonically — the area win rate falls to 27% at k=5, which is what
# over-averaging a small neighbourhood looks like.
#
# This is a property of the dataset's size, not a permanent fact, so
# best_k() exists to re-derive it and the loop reports it every scan.
DEFAULT_K = 1


class SurrogateError(RuntimeError):
    pass


def load_dataset(refdb: Path | str = REFDB) -> list[dict]:
    """Every recorded candidate run, deduplicated by (design, overrides).

    Deduplication matters more than it looks: the raw case files contain
    50 rows and only 17 distinct configurations, because re-running a
    design re-records the same candidates. Counting those as independent
    samples would inflate any accuracy figure roughly threefold.
    """
    cases_dir = Path(refdb) / "cases"
    if not cases_dir.is_dir():
        raise SurrogateError(f"no cases directory at {cases_dir}")

    seen: dict[tuple[str, str], dict] = {}
    for path in sorted(cases_dir.glob("*.json")):
        try:
            case = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        design = case.get("design")
        for iteration in case.get("iterations", []):
            for result in iteration.get("results", []):
                overrides = result.get("overrides") or {}
                key = (design, json.dumps(overrides, sort_keys=True))
                verdict = result.get("verdict")
                # Later cases win: a re-run reflects the current
                # toolchain, and mixing outcomes from different OpenLane
                # versions under one key would be worse than either.
                seen[key] = {
                    "design": design,
                    "overrides": overrides,
                    "completed": verdict is not None,
                    "passed": bool(verdict and verdict.get("passed")),
                    "area_um2": (verdict or {}).get("area_um2"),
                    "utilization": (verdict or {}).get("utilization"),
                    "stage": result.get("stage"),
                }
    return list(seen.values())


# Numeric features a config can carry. Categorical values (SYNTH_STRATEGY)
# are handled by exact match rather than by inventing an ordering — "AREA
# 0" is not 0.0 on a scale with "DELAY 4".
_NUMERIC = ("FP_CORE_UTIL", "PL_TARGET_DENSITY_PCT")


def featurize(row: dict) -> dict:
    """Config -> features. Missing values stay missing rather than
    becoming zero, which would place an unspecified parameter at one end
    of its own range."""
    ov = row.get("overrides") or {}
    feats: dict[str, float | str | None] = {}
    for key in _NUMERIC:
        value = ov.get(key)
        feats[key] = float(value) if isinstance(value, (int, float)) else None
    die = ov.get("DIE_AREA")
    if isinstance(die, (list, tuple)) and len(die) == 4:
        try:
            # float(), like the numeric features above. Left as an int it
            # computed correctly and was then dropped by _ranges' float
            # check — so DIE_AREA, written [0, 0, 64, 64] in every design
            # here, was silently invisible to the distance function and
            # no config differing only by die size had any neighbour.
            feats["die_area_um2"] = float(
                abs((die[2] - die[0]) * (die[3] - die[1])))
        except TypeError:
            feats["die_area_um2"] = None
    else:
        feats["die_area_um2"] = None
    feats["SYNTH_STRATEGY"] = ov.get("SYNTH_STRATEGY")
    return feats


def _numeric(value) -> bool:
    """int or float, but not bool — True would otherwise pass as 1 and
    put a flag on a continuous axis."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ranges(rows: list[dict]) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for key in (*_NUMERIC, "die_area_um2"):
        vals = [f[key] for f in (featurize(r) for r in rows)
                if _numeric(f.get(key))]
        if len(vals) >= 2 and max(vals) > min(vals):
            out[key] = (min(vals), max(vals))
    return out


def distance(a: dict, b: dict, ranges: dict[str, tuple[float, float]]) -> float | None:
    """Normalized distance between two configs, or None when they share
    no comparable feature at all — in which case they are not neighbours
    and pretending otherwise would make every point equidistant."""
    fa, fb = featurize(a), featurize(b)
    total = 0.0
    compared = 0
    for key, (lo, hi) in ranges.items():
        va, vb = fa.get(key), fb.get(key)
        if _numeric(va) and _numeric(vb):
            total += ((va - vb) / (hi - lo)) ** 2
            compared += 1
    sa, sb = fa.get("SYNTH_STRATEGY"), fb.get("SYNTH_STRATEGY")
    if sa is not None or sb is not None:
        total += 0.0 if sa == sb else 1.0
        compared += 1
    return math.sqrt(total) if compared else None


def predict(target: dict, dataset: list[dict], field: str = "area_um2",
            k: int = DEFAULT_K) -> dict:
    """k-NN prediction, or a refusal with the reason.

    Only ever compares within the same design. Area for a 14-gate counter
    says nothing about area for an SRAM wrapper, and letting them into
    one neighbourhood is how a surrogate starts producing confident
    nonsense.
    """
    same = [r for r in dataset
            if r["design"] == target.get("design")
            and isinstance(r.get(field), (int, float))]
    if len(same) < MIN_SAMPLES:
        return {
            "value": None,
            "refused": True,
            "reason": (f"only {len(same)} recorded run(s) with {field} for "
                       f"design {target.get('design')!r}; need at least "
                       f"{MIN_SAMPLES} before a prediction means anything"),
            "n_samples": len(same),
        }

    ranges = _ranges(same)
    scored = []
    for row in same:
        d = distance(target, row, ranges)
        if d is not None:
            scored.append((d, row))
    if not scored:
        return {"value": None, "refused": True,
                "reason": "no recorded run shares a comparable parameter",
                "n_samples": len(same)}

    scored.sort(key=lambda dr: dr[0])
    top = scored[:k]
    # Inverse-distance weighting, with an exact match short-circuited so
    # a config that was actually run returns what it actually produced.
    if top[0][0] == 0:
        return {"value": top[0][1][field], "refused": False,
                "exact_match": True, "n_samples": len(same),
                "neighbours": [top[0][1]["overrides"]]}
    weights = [1.0 / (d + 1e-9) for d, _ in top]
    value = sum(w * r[field] for w, (_, r) in zip(weights, top)) / sum(weights)
    return {
        "value": value,
        "refused": False,
        "exact_match": False,
        "n_samples": len(same),
        "neighbours": [r["overrides"] for _, r in top],
        "neighbour_distances": [round(d, 4) for d, _ in top],
    }


# Targets worth predicting, and what each is for.
#
# "completed" was sitting unused. Every configuration ever run carries
# it, where only the ones that reached signoff carry an area — 22 rows
# against 13 in the current store. The designs that contribute nothing to
# an area model are precisely the ones with the most failures to learn
# from: sram_wrapper's three configurations are all crashes, invisible to
# the area target and pure signal for this one.
#
# It is also the more useful prediction. Knowing a config will crash
# saves the whole 60-100 s run; knowing its area to within 3 um^2 saves
# nothing, because you ran it to find out anyway.
TARGETS = ("area_um2", "completed")


def _is_boolean_target(field: str) -> bool:
    return field == "completed"


def evaluate(dataset: list[dict], field: str = "area_um2",
             k: int = DEFAULT_K) -> dict:
    """Leave-one-out cross-validation against a predict-the-mean baseline.

    A surrogate is only worth having if it beats doing nothing. This
    reports both errors so the comparison is visible rather than implied,
    and reports the sample count alongside so a flattering number on tiny
    data cannot be quoted without its context.
    """
    usable = [r for r in dataset if isinstance(r.get(field), (int, float))]
    errors, baseline_errors, refusals = [], [], 0
    predictions, truths = [], []

    for i, held_out in enumerate(usable):
        rest = usable[:i] + usable[i + 1:]
        same = [r for r in rest if r["design"] == held_out["design"]]
        if not same:
            refusals += 1
            continue
        got = predict(held_out, rest, field, k)
        if got["refused"]:
            refusals += 1
            continue
        errors.append(abs(got["value"] - held_out[field]))
        predictions.append(got["value"])
        truths.append(held_out[field])
        mean = sum(r[field] for r in same) / len(same)
        baseline_errors.append(abs(mean - held_out[field]))

    def mae(xs):
        return sum(xs) / len(xs) if xs else None

    model_mae, base_mae = mae(errors), mae(baseline_errors)

    # How often the model actually won, not just by how much on average.
    #
    # Added when the first evaluable dataset arrived and the mean-based
    # verdict called an 11 percent MAE improvement on 11 samples of one
    # design "useful". Both errors were about 1 percent of the values
    # being predicted, and a single lucky fold can move a mean over that
    # few points. A win *rate* cannot be carried by one fold, so a small
    # average gain with a coin-flip win rate now reads as what it is.
    wins = sum(1 for m, b in zip(errors, baseline_errors) if m < b)
    ties = sum(1 for m, b in zip(errors, baseline_errors) if m == b)
    win_rate = wins / len(errors) if errors else None

    # For a 0/1 target a mean absolute error is a real score but an
    # unreadable one. Accuracy at the obvious threshold says the thing a
    # person actually wants to know: how often would it have called this
    # run right?
    accuracy = base_accuracy = None
    if _is_boolean_target(field) and truths:
        accuracy = sum(1 for p, a in zip(predictions, truths)
                       if (p >= 0.5) == bool(a)) / len(truths)
        rate = sum(1 for a in truths if a) / len(truths)
        majority = rate >= 0.5
        base_accuracy = sum(1 for a in truths if bool(a) == majority) / len(truths)
    return {
        "field": field,
        "k": k,
        "n_total": len(usable),
        "n_scored": len(errors),
        "n_refused": refusals,
        "model_mae": model_mae,
        "baseline_mae": base_mae,
        "accuracy": accuracy,
        "baseline_accuracy": base_accuracy,
        "wins": wins,
        "ties": ties,
        "win_rate": win_rate,
        "mae_improvement_pct": (
            None if not base_mae else round(100 * (base_mae - model_mae) / base_mae, 1)
        ),
        "beats_baseline": (
            None if model_mae is None or base_mae is None
            else model_mae < base_mae
        ),
        # The honest headline. A model evaluated on a handful of points
        # has no accuracy worth quoting, whichever way the numbers fell.
        "verdict": _verdict(errors, model_mae, base_mae, win_rate,
                            field, accuracy, base_accuracy),
    }


def already_recorded(design: str, overrides: dict,
                     dataset: list[dict] | None = None) -> dict | None:
    """The recorded result for this exact configuration, if there is one.

    Matching is on the serialized overrides, the same key load_dataset()
    deduplicates by, so "already collected" means the same thing in both
    places.
    """
    if dataset is None:
        dataset = load_dataset()
    key = json.dumps(overrides or {}, sort_keys=True)
    for row in dataset:
        if row["design"] == design and json.dumps(
                row["overrides"] or {}, sort_keys=True) == key:
            return row
    return None


def missing_configs(design: str, candidates: list[dict],
                    dataset: list[dict] | None = None) -> dict:
    """Split a candidate list into what would be new and what is a repeat.

    Written after a nine-run collection sweep spent roughly two thirds of
    its time re-running configurations already in reference-db: of nine
    SYNTH_STRATEGY values, six had been run by an earlier sweep and only
    three were new. Nothing in the pipeline noticed, because a candidate
    list says what to run and the case store says what was run, and the
    two had never been compared.

    Reports rather than filters. Deciding to re-run a configuration is
    legitimate — a toolchain bump makes every stored result stale — so
    this makes the repeat visible and leaves the choice to the caller.
    """
    if dataset is None:
        dataset = load_dataset()
    new, repeats = [], []
    for cand in candidates:
        overrides = cand.get("overrides", {})
        prior = already_recorded(design, overrides, dataset)
        (repeats if prior else new).append({
            "tag": cand.get("tag"),
            "overrides": overrides,
            "recorded": prior,
        })
    return {
        "design": design,
        "new": new,
        "repeats": repeats,
        "n_new": len(new),
        "n_repeat": len(repeats),
    }


# A win rate this far from a coin flip on a small sample is what
# separates "the model is doing something" from "one fold went well".
# Not a significance test — with a dozen points there is nothing to be
# significant about — just a floor low enough to be reachable and high
# enough that a single fold cannot carry it.
MIN_WIN_RATE = 0.7


def _verdict(errors, model_mae, base_mae, win_rate,
             field="area_um2", accuracy=None, base_accuracy=None) -> str:
    if len(errors) < MIN_SAMPLES:
        return ("insufficient data — not enough distinct configurations to "
                "evaluate a surrogate at all")
    if model_mae is None or base_mae is None:
        return "not evaluated"
    # A classifier is judged on how often it is right, against always
    # guessing the commoner outcome — a baseline that is hard to beat
    # precisely when it matters least.
    if accuracy is not None and base_accuracy is not None:
        if accuracy <= base_accuracy:
            return (f"{accuracy:.0%} accurate on {len(errors)} samples, no "
                    f"better than always guessing the commoner outcome "
                    f"({base_accuracy:.0%})")
        return (f"{accuracy:.0%} accurate on {len(errors)} samples against "
                f"{base_accuracy:.0%} for always guessing the commoner "
                f"outcome — re-check as the dataset grows")
    if model_mae >= base_mae:
        return "no better than predicting the mean"
    if win_rate is not None and win_rate < MIN_WIN_RATE:
        return (f"marginally better on average but wins only "
                f"{win_rate:.0%} of folds on {len(errors)} samples — "
                f"too weak to rely on")
    return (f"beats predicting the mean on {len(errors)} samples "
            f"({win_rate:.0%} of folds) — worth re-checking as the "
            f"dataset grows, not yet worth trusting a prediction to")


def best_k(dataset: list[dict], field: str = "area_um2",
           candidates: tuple[int, ...] = (1, 2, 3, 4, 5)) -> dict:
    """Which k the data actually supports, rather than the one assumed.

    k=3 was a guess. With eight to eleven samples per design that is a
    third of the neighbourhood, which may be averaging away the signal it
    is meant to find — or may be the only thing keeping it from fitting
    noise. Both are plausible, so it is measured.

    Chooses on win rate first and mean error second: a k that wins more
    folds is more trustworthy on this little data than one that happens
    to have a lower average, which a single fold can move.
    """
    scored = []
    for k in candidates:
        ev = evaluate(dataset, field, k)
        if ev["n_scored"] == 0:
            continue
        scored.append({
            "k": k,
            "n_scored": ev["n_scored"],
            "win_rate": ev["win_rate"],
            "model_mae": ev["model_mae"],
            "baseline_mae": ev["baseline_mae"],
            "accuracy": ev["accuracy"],
            "verdict": ev["verdict"],
        })
    if not scored:
        return {"field": field, "tried": list(candidates), "best": None,
                "reason": "no k produced a scored fold"}
    ranked = sorted(
        scored,
        key=lambda r: (-(r["win_rate"] or 0), r["model_mae"] if r["model_mae"] is not None else 1e18),
    )
    return {"field": field, "tried": list(candidates), "results": scored,
            "best": ranked[0]}


def dataset_report(dataset: list[dict]) -> dict:
    """What the dataset actually contains, per design."""
    per: dict[str, dict] = {}
    for row in dataset:
        d = per.setdefault(row["design"], {"configs": 0, "completed": 0,
                                           "with_area": 0})
        d["configs"] += 1
        d["completed"] += 1 if row["completed"] else 0
        d["with_area"] += 1 if isinstance(row.get("area_um2"), (int, float)) else 0
    return {
        "distinct_configs": len(dataset),
        "designs": len(per),
        "per_design": per,
        # Needs MIN_SAMPLES + 1, not MIN_SAMPLES: leave-one-out holds a
        # sample back, so a design sitting exactly on the threshold has
        # MIN_SAMPLES - 1 left in every fold and predict() refuses all of
        # them. Reporting such a design as "trainable" claimed a
        # capability that could not be demonstrated — caught when
        # counter4 reached exactly 8 and the evaluation still scored 0 of
        # 10 folds. A model that cannot be validated is not one to use.
        "trainable": [name for name, d in per.items()
                      if d["with_area"] > MIN_SAMPLES],
        "evaluable_at": MIN_SAMPLES + 1,
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--field", default="area_um2")
    ap.add_argument("--k", type=int, default=DEFAULT_K)
    args = ap.parse_args()

    data = load_dataset()
    print(json.dumps({
        "dataset": dataset_report(data),
        "evaluation": evaluate(data, args.field, args.k),
    }, indent=2))


if __name__ == "__main__":
    main()
