#!/usr/bin/env python3
r"""Statistical recovery memory: P(an override change closes a violation).

Adapted from ChipMEM's statistical memory ("ChipMEM: Verification-Grounded
Memory for EDA Agents", AlRabah et al., arXiv 2609.27067, 2026;
github.com/aalrabah/ChipMEM). ChipMEM ranks recovery actions for an error
class with a hierarchy of Beta-binomial estimates. It starts from Beta(1,1)
and adds one condition per level, shrinking each level toward its parent:

    p_j = (s_j + K * p_{j-1}) / (s_j + f_j + K),   K = 8

Here the conditions are what reference-db actually records:

    level 0  prior                     Beta(1,1) = 0.5
    level 1  violation signature e     how closable e is at all
    level 2  (e, override key)         e.g. (signoff:setup, CLOCK_PERIOD)
    level 3  (e, key, value)           e.g. (..., CLOCK_PERIOD=23.6)

One observation is one recorded transition within a case. An override
changed between two consecutive measured runs, and a signature present
before either closed (success) or persisted (failure). A separate
hierarchy estimates P(the change introduced a signature that was not
there before) for the same key/value.

Write policy, after ChipMEM: only MEASURED transitions count. Both runs
must exist in the store with real verdicts. Nothing an LLM said is an
observation. Read policy: estimates are advisory. closed_loop_search
still refuses repeats and keeps proposals to one knob.

Not causal (see experiment_graph.py). When several overrides changed in
one transition, each changed key is credited, which confounds them.
`single_change_only=True` drops those transitions, and `evaluate()`
reports both settings.

Honest test: `evaluate()` holds out one design at a time and predicts its
transitions from the others only. It compares the Brier score against the
global base rate and against flat Laplace estimates per (e, key, value).
If the hierarchy does not beat both, `closed_loop_search` should not
trust it, and the report says so.

Stdlib only, per soul.md.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

import case_retrieval
import experiment_graph

K_SHRINK = 8.0
# From the ChipMEM repository (chipmem/statistical/model.py).
K_GRID = (1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 64.0)   # PARENT_STRENGTH_GRID
NUDGE_THRESHOLD = 0.20                             # advise only below this


def _bad(state: dict) -> set[str]:
    return set(state["violations"]) | set(state["unverified"]) | set(state["errors"])


def observations(cases: list[dict], single_change_only: bool = False) -> list[dict]:
    obs = []
    for case in cases:
        results = [r for it in case.get("iterations", []) or []
                   for r in it.get("results", []) or []]
        for before, after in zip(results, results[1:]):
            changed = experiment_graph._changed_overrides(before, after)
            if not changed or (single_change_only and len(changed) != 1):
                continue
            bad_b = _bad(experiment_graph.result_state(before))
            bad_a = _bad(experiment_graph.result_state(after))
            regressed = bool(bad_a - bad_b)
            for key, value in changed.items():
                vkey = json.dumps(value, sort_keys=True)
                for sig in sorted(bad_b):
                    obs.append({"design": case.get("design"),
                                "case": case.get("_file"), "signature": sig,
                                "stage": str(before.get("stage") or ""),
                                "key": key, "value": vkey, "closed": sig not in bad_a,
                                "regressed": regressed, "n_changed": len(changed),
                                "after_passed": bool((after.get("verdict") or {}).get("passed"))})
    return obs


class RecoveryMemory:
    def __init__(self, obs: list[dict], k: float = K_SHRINK):
        self.k = k
        self.close = defaultdict(lambda: [0, 0])     # level key -> [s, f]
        self.regress = defaultdict(lambda: [0, 0])
        for o in obs:
            s = int(o["closed"])
            for lvl in self._close_levels(o["signature"], o["key"], o["value"]):
                self.close[lvl][0] += s
                self.close[lvl][1] += 1 - s
            r = int(o["regressed"])
            for lvl in self._regress_levels(o["key"], o["value"]):
                self.regress[lvl][0] += r
                self.regress[lvl][1] += 1 - r

    @staticmethod
    def _close_levels(sig, key, value):
        return [("e", sig), ("ek", sig, key), ("ekv", sig, key, value)]

    @staticmethod
    def _regress_levels(key, value):
        return [("k", key), ("kv", key, value)]

    def _chain(self, table, levels) -> tuple[float, int]:
        p, support = 0.5, 0
        for lvl in levels:
            s, f = table.get(lvl, (0, 0))
            p = (s + self.k * p) / (s + f + self.k)
            support = s + f
        return p, support

    def p_close(self, sig: str, key: str, value) -> tuple[float, int]:
        """Posterior P(change closes sig) and the observation count at the deepest level."""
        return self._chain(self.close, self._close_levels(sig, key, _vkey(value)))

    def p_regress(self, key: str, value) -> tuple[float, int]:
        return self._chain(self.regress, self._regress_levels(key, _vkey(value)))

    def estimate(self, signatures: set[str], key: str, value) -> dict:
        """For an action against a state with several open signatures."""
        per_sig = {s: round(self.p_close(s, key, value)[0], 4) for s in sorted(signatures)}
        p_any = 1.0 - math.prod(1.0 - p for p in per_sig.values()) if per_sig else 0.0
        p_reg, reg_support = self.p_regress(key, value)
        support = sum(self.close.get(("ek", s, key), (0, 0))[0]
                      + self.close.get(("ek", s, key), (0, 0))[1] for s in signatures)
        return {"p_close": per_sig, "p_close_any": round(p_any, 4),
                "p_regress": round(p_reg, 4), "support": support,
                "net": round(p_any - p_reg, 4)}


def _vkey(value) -> str:
    return json.dumps(value, sort_keys=True)


def skills(obs: list[dict], min_support: int = 3, k: float = K_SHRINK) -> dict:
    """ChipMEM-style procedural memory, distilled from measured transitions.

    Write gate (ChipMEM: "only after it passes synthesis, simulation, or
    formal checks"): a DO rule needs `min_support` closures whose AFTER run
    passed the full verdict. A closure that only traded one violation for
    another is not a pass. An AVOID rule needs `min_support` observed
    regressions with a shrunken regression probability above 0.5. Rules
    name signatures and knobs, never a design, so they can transfer.
    """
    mem = RecoveryMemory(obs, k)
    verified = defaultdict(int)
    sources = defaultdict(set)
    regressions = defaultdict(int)
    for o in obs:
        if o["closed"] and o["after_passed"]:
            verified[(o["signature"], o["key"], o["value"])] += 1
            # ChipMEM's store appends at most one skill per source task
            # (learning.py). Here: several closures inside one recorded
            # case are one piece of evidence, not several.
            sources[(o["signature"], o["key"], o["value"])].add(o.get("case"))
        regressions[(o["key"], o["value"])] += int(o["regressed"])
    do = []
    for (sig, key, value), n in sorted(verified.items()):
        if len(sources[(sig, key, value)]) >= min_support:
            p, support = mem._chain(mem.close, mem._close_levels(sig, key, value))
            do.append({"when": sig, "set": {key: json.loads(value)},
                       "p_close": round(p, 3), "verified_closures": n,
                       "source_cases": len(sources[(sig, key, value)]),
                       "observations": support})
    avoid = []
    for (key, value), n in sorted(regressions.items()):
        p, support = mem._chain(mem.regress, mem._regress_levels(key, value))
        if n >= min_support and p > 0.5:
            avoid.append({"set": {key: json.loads(value)}, "p_regress": round(p, 3),
                          "regressions": n, "observations": support})
    do.sort(key=lambda r: -r["p_close"])
    avoid.sort(key=lambda r: -r["p_regress"])
    return {"DO": do, "AVOID": avoid,
            "gate": (f"DO needs verified closures (next run passed the full verdict) "
                     f"from >= {min_support} distinct recorded cases"),
            "scope": "Associations from recorded transitions, not causal rules."}


# ── honest evaluation ──────────────────────────────────

def _brier(pairs):
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def _auc(pairs):
    pos = [p for p, y in pairs if y]
    neg = [p for p, y in pairs if not y]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def evaluate(cases: list[dict], single_change_only: bool = False,
             k: float = K_SHRINK) -> dict:
    """Leave-one-design-out: predict each design's transitions from the others."""
    obs = observations(cases, single_change_only)
    designs = sorted({o["design"] for o in obs})
    hier, flat, base, heur = [], [], [], []
    for held in designs:
        train = [o for o in obs if o["design"] != held]
        test = [o for o in obs if o["design"] == held]
        if not train or not test:
            continue
        mem = RecoveryMemory(train, k)
        rate = sum(o["closed"] for o in train) / len(train)
        counts = defaultdict(lambda: [0, 0])
        regress_counts = defaultdict(int)
        for o in train:
            counts[(o["signature"], o["key"], o["value"])][o["closed"] ^ 1] += 1
            regress_counts[(o["key"], o["value"])] += int(o["regressed"])
        for o in test:
            # closed_loop_search's production score: closures - 2 * regressions.
            closes = counts.get((o["signature"], o["key"], o["value"]), (0, 0))[0]
            heur.append((closes - 2 * regress_counts.get((o["key"], o["value"]), 0),
                         int(o["closed"])))
            y = int(o["closed"])
            hier.append((mem._chain(mem.close, mem._close_levels(
                o["signature"], o["key"], o["value"]))[0], y))
            s, f = counts.get((o["signature"], o["key"], o["value"]), (0, 0))
            flat.append(((s + 1) / (s + f + 2), y))
            base.append((rate, y))
    if not hier:
        return {"verdict": "not evaluable", "observations": len(obs)}
    result = {
        "observations": len(obs), "designs": len(designs),
        "single_change_only": single_change_only, "k": k,
        "brier": {"hierarchical": round(_brier(hier), 4), "flat_laplace": round(_brier(flat), 4),
                  "base_rate": round(_brier(base), 4)},
        "auc": {"hierarchical": _auc(hier), "flat_laplace": _auc(flat),
                "current_heuristic": _auc(heur)},
    }
    b = result["brier"]
    result["beats_baselines"] = b["hierarchical"] < min(b["flat_laplace"], b["base_rate"])
    result["verdict"] = ("hierarchical estimate beats both baselines on held-out designs"
                         if result["beats_baselines"] else
                         "hierarchical estimate does NOT beat the baselines; treat as unsupported")
    return result


def retune_k(obs: list[dict], current: float = K_SHRINK, grid=K_GRID,
             holdout_fraction: float = 0.2, minimum_rows: int = 40) -> float:
    """ChipMEM's retune_parent_strength, ported: hold out every 5th
    observation OF THE TRAINING DATA, fit on the rest, keep the K with the
    highest held-out log-likelihood. Unchanged below `minimum_rows`.
    Selection never sees the designs being evaluated."""
    if len(obs) < minimum_rows:
        return current
    stride = int(1 / holdout_fraction)
    held = [o for i, o in enumerate(obs) if i % stride == 0]
    train = [o for i, o in enumerate(obs) if i % stride != 0]
    if not held or not train:
        return current
    best, best_ll = current, -math.inf
    for k in grid:
        mem = RecoveryMemory(train, k)
        ll = 0.0
        for o in held:
            p = mem._chain(mem.close, mem._close_levels(
                o["signature"], o["key"], o["value"]))[0]
            p = min(max(p, 1e-9), 1 - 1e-9)
            ll += math.log(p) if o["closed"] else math.log(1 - p)
        if ll > best_ll:
            best, best_ll = k, ll
    return float(best)


def evaluate_retuned(cases: list[dict]) -> dict:
    """Leave-one-design-out with K chosen inside each training fold."""
    obs = observations(cases)
    pairs, chosen = [], {}
    for held in sorted({o["design"] for o in obs}):
        train = [o for o in obs if o["design"] != held]
        k = retune_k(train)
        chosen[held] = k
        mem = RecoveryMemory(train, k)
        for o in obs:
            if o["design"] == held:
                pairs.append((mem._chain(mem.close, mem._close_levels(
                    o["signature"], o["key"], o["value"]))[0], int(o["closed"])))
    return {"brier": round(_brier(pairs), 4), "auc": _auc(pairs), "k_by_held_out_design": chosen}


def compare_hierarchies(cases: list[dict], k: float = K_SHRINK) -> dict:
    """This module's hierarchy against the one in the ChipMEM repository.

    ChipMEM's RecoveryAdvisor starts from an empirical global rate and
    conditions strategy -> (error, strategy) -> (error, stage, strategy).
    Here "strategy" is the override key and "stage" is where the failing
    run stopped. Same leave-one-design-out protocol as evaluate().
    """
    obs = observations(cases)
    variants = {
        "this module: e > e,k > e,k,v; prior 0.5": (False, lambda o: [
            ("e", o["signature"]), ("ek", o["signature"], o["key"]),
            ("ekv", o["signature"], o["key"], o["value"])]),
        "this module + empirical global level": (True, lambda o: [
            ("e", o["signature"]), ("ek", o["signature"], o["key"]),
            ("ekv", o["signature"], o["key"], o["value"])]),
        "ChipMEM repo: global > k > e,k > e,stage,k": (True, lambda o: [
            ("k", o["key"]), ("ek", o["signature"], o["key"]),
            ("esk", o["signature"], o["stage"], o["key"])]),
        "ChipMEM order with value: global > k > e,k > e,k,v": (True, lambda o: [
            ("k", o["key"]), ("ek", o["signature"], o["key"]),
            ("ekv", o["signature"], o["key"], o["value"])]),
    }
    out = {}
    for name, (use_global, levels) in variants.items():
        pairs = []
        for held in sorted({o["design"] for o in obs}):
            counts = defaultdict(lambda: [0, 0])
            g = [0, 0]
            for o in obs:
                if o["design"] == held:
                    continue
                g[0] += o["closed"]
                g[1] += 1
                for lvl in levels(o):
                    counts[lvl][0] += o["closed"]
                    counts[lvl][1] += 1
            for o in obs:
                if o["design"] != held:
                    continue
                p = (1 + g[0]) / (2 + g[1]) if use_global else 0.5
                for lvl in levels(o):
                    s, n = counts.get(lvl, (0, 0))
                    p = (s + k * p) / (n + k)
                pairs.append((p, int(o["closed"])))
        out[name] = {"brier": round(_brier(pairs), 4), "auc": round(_auc(pairs), 3)}
    return out


def k_sensitivity(cases: list[dict], ks=(1, 2, 4, 8, 16, 32)) -> dict:
    """Brier by K. Reported, not used to pick K: choosing K on these same
    held-out designs would be selection on the test set."""
    return {str(k): evaluate(cases, k=k)["brier"]["hierarchical"] for k in ks}


def main() -> None:
    cases = case_retrieval.load_cases()
    print(json.dumps({"all_transitions": evaluate(cases),
                      "single_change_only": evaluate(cases, single_change_only=True),
                      "k_sensitivity_brier": k_sensitivity(cases),
                      "retuned_k_nested": evaluate_retuned(cases),
                      "hierarchy_comparison": compare_hierarchies(cases),
                      "skills": skills(observations(cases))},
                     indent=2))


if __name__ == "__main__":
    main()
