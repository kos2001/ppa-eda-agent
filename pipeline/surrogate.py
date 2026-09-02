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
import random
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
# This is a property of the dataset's size, not a permanent fact, and it
# has already moved once: adding SPM took the store from 30 rows to 40
# and the area target's best k from 1 to 3, while completion stayed at 1.
# So k is per target rather than one constant serving both — there was
# never a reason the same neighbourhood should suit a continuous target
# with 21 samples and a boolean one with 36.
#
# best_k() re-derives these, the loop reports current beside best every
# scan, and a test fails when they drift apart rather than letting a
# stale default quietly cost accuracy.
# Re-measured whenever the corpus changes, never assumed. area_um2 has
# moved 3 -> 4 -> 5 -> 2 -> 5 -> 3 -> 4 -> 2 -> 1 as the corpus grew and
# thinned out again; power_w has moved 2 -> 1 and completed has stayed
# at 1 throughout. Several of those area moves were between values that
# tie — k=1, 2 and 3 all score 0.9921 on the current store, and best_k
# breaks the tie toward the smaller neighbourhood. So the constant is
# tracking a real measurement, but the last digit of it is arbitrary
# and no conclusion should rest on which of a tied set is chosen. One of those moves was not new data at all — adding
# routing_layers as a feature rearranged the neighbourhoods and k
# followed, on a margin of one fold in 155. The next was 85 recovered
# runs arriving at once.
#
# The pattern across all of them: the margins between adjacent k are
# small for the continuous targets (0.987/0.992/0.992 across k=1,2,3 for
# area) and large for completion, which falls monotonically from 0.873
# at k=1 to 0.740 at k=5. Averaging neighbours helps a number and hurts
# a boolean, which is why they were split apart in the first place.
# The number tracks how densely a row's own technology is populated
# around it, which is why it moves in both directions rather than only
# up — and why a new feature can move it without a single new row.
#
# A test asserts these against best_k() on the real store, which is why
# none of them has gone stale while the data moved underneath.
DEFAULT_K_BY_TARGET = {
    "area_um2": 1,
    "power_w": 1,
    "completed": 1,
}
DEFAULT_K = 1


def default_k(field: str) -> int:
    return DEFAULT_K_BY_TARGET.get(field, DEFAULT_K)


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
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        design = case.get("design")
        for iteration in case.get("iterations", []):
            for result in iteration.get("results", []):
                overrides = result.get("overrides") or {}
                # The library is part of the identity of a run. Without
                # it two candidates that differ only by technology
                # collapse into one and the later silently replaces the
                # earlier — verified: recording counter4 at hd (290.3
                # um2) and hs (444.4 um2) left one sample.
                key = (design, json.dumps(overrides, sort_keys=True),
                       result.get("scl") or DEFAULT_SCL,
                       result.get("pdk") or DEFAULT_PDK)
                verdict = result.get("verdict")
                # Later cases win: a re-run reflects the current
                # toolchain, and mixing outcomes from different OpenLane
                # versions under one key would be worse than either.
                seen[key] = {
                    "design": design,
                    # Carried so a feature can relate a configuration to
                    # the circuit it configures. Without it every row is
                    # just "some numbers for some design", and the model
                    # has no choice but to stay inside one design.
                    "topology": case.get("topology") or {},
                    # The design's own declared settings, so a feature
                    # can see a DIE_AREA a candidate did not override.
                    # sram_wrapper fixes its die in config.json rather
                    # than per-candidate, and without this it looked
                    # like a design with no die area at all.
                    "declared": {
                        st["key"]: st["value"]
                        for st in ((case.get("constraints") or {})
                                   .get("design") or {}).get("settings", [])
                    },
                    "overrides": overrides,
                    # Carried onto the row, not only into the dedup key.
                    # It was in the key alone at first, which kept two
                    # technologies from collapsing but left featurize
                    # unable to see the difference — the feature existed
                    # and was always None.
                    "scl": result.get("scl"),
                    # Carried for the same reason as scl, and because the
                    # same omission happened twice: the field was
                    # recorded on the result and never copied here, so
                    # every gf180mcu row reported as sky130A. The SCL
                    # already separates the two families for distance
                    # purposes (a gf180mcu_fd_sc_* name exists in no
                    # other PDK), so this is identity and reporting, not
                    # a second feature.
                    "pdk": result.get("pdk"),
                    "completed": verdict is not None,
                    "passed": bool(verdict and verdict.get("passed")),
                    "area_um2": (verdict or {}).get("area_um2"),
                    # The metric CLOCK_PERIOD actually moves. Measured on
                    # counter4: 10ns -> 4ns takes area up 6.9% and power
                    # up 152%, so a dataset that records only area sees
                    # almost nothing of what tightening a clock costs.
                    "power_w": ((verdict or {}).get("power") or {}).get("total_w"),
                    "utilization": (verdict or {}).get("utilization"),
                    "stage": result.get("stage"),
                }
    return list(seen.values())


# What every recorded run used before the library became a candidate
# axis. Stated so an old row and a new explicit one compare equal
# instead of looking like different technologies.
DEFAULT_SCL = "sky130_fd_sc_hd"

# What every run used before the PDK became a candidate axis.
DEFAULT_PDK = "sky130A"

# Routing layers per PDK variant, counted from the tech-LEF each one
# actually ships. A PDK absent from this map yields None, which keeps it
# out of the distance calculation rather than placing it at zero — an
# unknown stack is not a stack with no metal.
ROUTING_LAYERS = {
    "sky130A": 6,   # li1 + met1..met5
    "sky130B": 6,
    "gf180mcuA": 3,  # Metal1..Metal3
    "gf180mcuB": 4,
    "gf180mcuC": 5,
    "gf180mcuD": 5,  # same five layers as C; thicker top metal
}

# How many rows a technology needs before it is allowed to separate
# neighbourhoods. Measured, not chosen: with 42 hd rows and 3 hs rows,
# switching the feature on took area's win-rate from 0.91 to 0.88 and
# completion's from 0.68 to 0.65. A categorical with a near-empty
# category adds a full unit of distance to every cross-technology pair,
# which pushes away the only neighbours a sparse category has. The
# feature is right and was premature; this makes it turn itself on.
#
# Same discipline as MIN_SAMPLES above — refuse to use data too thin to
# support the thing being asked of it, rather than reporting a number
# that came from noise.
MIN_SAMPLES_PER_SCL = 8

# Numeric features a config can carry. Categorical values (SYNTH_STRATEGY)
# are handled by exact match rather than by inventing an ordering — "AREA
# 0" is not 0.0 on a scale with "DELAY 4".
# PL_TARGET_DENSITY_PCT has been in this list from the start and appears
# in zero recorded rows — a feature nobody ever gave data to, the mirror
# of the scl problem where data arrived for a feature that did not exist.
#
# CLOCK_PERIOD is the axis with the most leverage the dataset had never
# seen: on counter4 it moves power 2.5x across 10ns -> 4ns and stops
# completing at 3ns, so it carries information about both targets.
_NUMERIC = ("FP_CORE_UTIL", "PL_TARGET_DENSITY_PCT", "CLOCK_PERIOD")


def featurize(row: dict) -> dict:
    """Config -> features. Missing values stay missing rather than
    becoming zero, which would place an unspecified parameter at one end
    of its own range."""
    ov = row.get("overrides") or {}
    feats: dict[str, float | str | None] = {}
    for key in _NUMERIC:
        value = ov.get(key)
        feats[key] = float(value) if isinstance(value, (int, float)) else None
    # An override wins; otherwise the design's own declared die area.
    # Both are known before the run, so neither leaks an outcome.
    # Like DIE_AREA below: an override wins, otherwise the design's own
    # declared value. Most designs fix the clock in config.json and
    # never override it, so without the fallback every such row looks
    # like a design with no clock at all.
    if feats.get("CLOCK_PERIOD") is None:
        declared_clk = (row.get("declared") or {}).get("CLOCK_PERIOD")
        if isinstance(declared_clk, (int, float)):
            feats["CLOCK_PERIOD"] = float(declared_clk)

    die = ov.get("DIE_AREA") or (row.get("declared") or {}).get("DIE_AREA")
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
    # Technology, the axis this dataset was blind to. Every recorded
    # sample was sky130_fd_sc_hd and it was not even a field, while on
    # counter4 all eleven design-knob samples span 4.3% of area and the
    # library alone moves it 53.1%. Predicting area from knobs that
    # barely move it, with the one that does left out, is a large part
    # of why win-rate sat at 0.67.
    # Stored raw, not defaulted. Defaulting it here made every pair of
    # configs share a feature, so distance() stopped returning None for
    # two rows with nothing in common and returned 0.0 — every point
    # equidistant, which is the exact failure its docstring warns about.
    # An existing test caught it. The default is applied where it is
    # actually needed: comparing a row that declares the library against
    # one recorded before the field existed.
    feats["SCL"] = row.get("scl")

    # How many layers place-and-route has to route on. Counted from each
    # installed PDK's own tech-LEF rather than guessed: gf180mcuA ships
    # Metal1-3, B adds Metal4, C and D both reach Metal5 and differ only
    # in top-metal thickness; sky130A and sky130B both offer li1 plus
    # met1-met5. Routing layers are the resource place-and-route runs out
    # of, so this is the feature that lets a run which did not fit on
    # three layers inform one being asked to fit on four.
    #
    # Numeric rather than categorical, because the stacks are ordered and
    # a category would make 3LM exactly as far from 4LM as from 5LM. The
    # cost of the ordering is that gf180mcuC and sky130A both read as 5
    # despite being unrelated processes — SCL already separates those
    # categorically, so the pair is never judged similar on this alone.
    #
    # It needs no gate of its own. _ranges drops any numeric feature
    # whose values are all equal, so while every row is one stack this
    # contributes nothing and turns itself on when a second arrives.
    feats["routing_layers"] = ROUTING_LAYERS.get(row.get("pdk") or DEFAULT_PDK)

    # A density feature (sequential cells per um^2) was tried here and
    # removed. The reasoning was sound — what decides whether a floorplan
    # survives is how much circuit is being asked to fit, not how many
    # microns there are — and the measurement did not support it. Within
    # a design the cell count is constant, so the ratio is a monotonic
    # transform of die area and adds no discrimination where the model
    # actually operates; across designs it scored 50% against a 64%
    # majority baseline, worse than guessing. Carrying a feature that
    # earns nothing is the kind of unearned complexity this pipeline
    # avoids, so it is a comment rather than code.
    return feats


def _numeric(value) -> bool:
    """int or float, but not bool — True would otherwise pass as 1 and
    put a flag on a continuous axis."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _ranges(rows: list[dict]) -> dict[str, tuple[float, float]]:
    out: dict[str, tuple[float, float]] = {}
    for key in (*_NUMERIC, "die_area_um2", "routing_layers"):
        vals = [f[key] for f in (featurize(r) for r in rows)
                if _numeric(f.get(key))]
        if len(vals) >= 2 and max(vals) > min(vals):
            out[key] = (min(vals), max(vals))
    return out


def scl_is_informative(dataset: list[dict]) -> bool:
    """Whether the technology axis has enough rows on both sides to help.

    False keeps distance() blind to it, which is what a 42-to-3 split
    wants. It flips on its own once a second technology is properly
    represented.
    """
    counts: dict[str, int] = {}
    for row in dataset:
        key = row.get("scl") or DEFAULT_SCL
        counts[key] = counts.get(key, 0) + 1
    big = [n for n in counts.values() if n >= MIN_SAMPLES_PER_SCL]
    return len(big) >= 2


def distance(a: dict, b: dict, ranges: dict[str, tuple[float, float]],
             use_scl: bool = True) -> float | None:
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
    for key in ("SYNTH_STRATEGY", "SCL"):
        if key == "SCL" and not use_scl:
            continue
        va, vb = fa.get(key), fb.get(key)
        if va is None and vb is None:
            continue
        if key == "SCL":
            # A row from before the field existed used the default, so
            # it is the same technology as one that says so explicitly.
            va, vb = va or DEFAULT_SCL, vb or DEFAULT_SCL
        total += 0.0 if va == vb else 1.0
        compared += 1
    return math.sqrt(total) if compared else None


def predict(target: dict, dataset: list[dict], field: str = "area_um2",
            k: int | None = None) -> dict:
    """k-NN prediction, or a refusal with the reason.

    Only ever compares within the same design. Area for a 14-gate counter
    says nothing about area for an SRAM wrapper, and letting them into
    one neighbourhood is how a surrogate starts producing confident
    nonsense.
    """
    same = [r for r in dataset
            if r["design"] == target.get("design")
            and isinstance(r.get(field), (int, float))]
    k = default_k(field) if k is None else k
    if len(same) < MIN_SAMPLES:
        return {
            "value": None,
            "refused": True,
            "reason": (f"only {len(same)} recorded run(s) with {field} for "
                       f"design {target.get('design')!r}; need at least "
                       f"{MIN_SAMPLES} before a prediction means anything"),
            "n_samples": len(same),
        }

    use_scl = scl_is_informative(dataset)
    ranges = _ranges(same)
    scored = []
    for row in same:
        d = distance(target, row, ranges, use_scl=use_scl)
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
TARGETS = ("area_um2", "power_w", "completed")


def _is_boolean_target(field: str) -> bool:
    return field == "completed"


def evaluate(dataset: list[dict], field: str = "area_um2",
             k: int | None = None) -> dict:
    """Leave-one-out cross-validation against a predict-the-mean baseline.

    A surrogate is only worth having if it beats doing nothing. This
    reports both errors so the comparison is visible rather than implied,
    and reports the sample count alongside so a flattering number on tiny
    data cannot be quoted without its context.
    """
    k = default_k(field) if k is None else k
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
        # The per-fold outcomes the rate is computed from. Exposed so a
        # resampler can put an interval on it: a rate is a point, and a
        # point on 31 folds looks far more settled than it is.
        "fold_wins": [1 if m < b else 0
                      for m, b in zip(errors, baseline_errors)],
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
    ap.add_argument("--k", type=int, default=None,
                    help="neighbours; default is per-target (see DEFAULT_K_BY_TARGET)")
    args = ap.parse_args()

    data = load_dataset()
    print(json.dumps({
        "dataset": dataset_report(data),
        "evaluation": evaluate(data, args.field, args.k),
    }, indent=2))


if __name__ == "__main__":
    main()


# --- how settled is a win-rate? -------------------------------------

# Resamples per run. 2000 is where the 5th percentile stopped moving in
# the third decimal across seeds on this corpus; more only costs time.
BOOTSTRAP_RESAMPLES = 2000

# Fixed so a reported interval is reproducible. This project treats a
# number nobody else can regenerate as an opinion.
BOOTSTRAP_SEED = 20260830


def win_rate_interval(fold_wins: list[int], resamples: int = BOOTSTRAP_RESAMPLES,
                      seed: int = BOOTSTRAP_SEED,
                      confidence: float = 0.90) -> dict | None:
    """A percentile interval on a leave-one-out win-rate.

    evaluate() reports the rate as a single number, which reads as
    settled. It is not: 0.97 on 31 folds and 0.97 on 3100 folds are the
    same figure and completely different claims, and this pipeline
    decides whether to trust a surrogate by comparing that figure to
    MIN_WIN_RATE.

    Resampling the folds with replacement is the cheapest honest answer.
    It needs no new runs — the folds already exist — and it says how far
    the rate could reasonably sit from where it landed.

    What it does NOT do: account for the folds being correlated (they
    share a corpus and 21 of the rows come from two sweeps of one
    design), or for the corpus being unrepresentative of designs nobody
    has run. It bounds sampling noise, not the choice of samples.
    """
    n = len(fold_wins)
    if n < 2:
        return None
    rng = random.Random(seed)
    rates = []
    for _ in range(resamples):
        rates.append(sum(fold_wins[rng.randrange(n)] for _ in range(n)) / n)
    rates.sort()
    tail = (1.0 - confidence) / 2.0
    lo = rates[int(tail * resamples)]
    hi = rates[min(int((1.0 - tail) * resamples), resamples - 1)]
    point = sum(fold_wins) / n

    # The percentile bootstrap collapses when every fold agrees: resample
    # all-wins and you get all-wins, so three perfect folds report
    # [1.00, 1.00] and "settled" — the exact false confidence this
    # function exists to prevent, produced by the function itself.
    #
    # Falls back to the rule of three, which is what you can say about
    # zero observed failures in n trials: the rate could plausibly be as
    # low as 1 - 3/n. At n=3 that is 0.0, which is the honest answer.
    degenerate = point in (0.0, 1.0)
    if degenerate:
        if point == 1.0:
            lo, hi = max(0.0, 1.0 - 3.0 / n), 1.0
        else:
            lo, hi = 0.0, min(1.0, 3.0 / n)
    return {
        "degenerate": degenerate,
        "win_rate": point,
        "lo": lo,
        "hi": hi,
        "confidence": confidence,
        "folds": n,
        "resamples": resamples,
        # The decision this feeds. A point estimate over the threshold
        # with an interval straddling it is not evidence the surrogate
        # clears the bar, and that distinction is the reason to compute
        # any of this.
        "clears_threshold": lo >= MIN_WIN_RATE,
    }
