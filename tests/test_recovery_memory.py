"""ChipMEM-style statistical recovery memory. Decision tests on synthetic cases."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import closed_loop_search
import experiment_graph
import recovery_memory as rm


TEXT = {"signoff:setup": "3 setup timing violation(s)",
        "signoff:hold": "2 hold timing violation(s)"}


def result(overrides, violations=(), passed=False):
    # Real verdicts carry counted text; case_retrieval maps it to signatures.
    return {"overrides": overrides, "error": "",
            "verdict": {"passed": passed, "violations": [TEXT[v] for v in violations]}}


def case(design, *results):
    # closed_loop_search only uses evidence whose toolchain image is known.
    return {"design": design, "_file": f"{design}.json",
            "toolchain": {"openlane_image": "img"},
            "iterations": [{"results": list(results)}]}


def obs(sig, key, value, closed, regressed=False, design="d", passed=None):
    return {"design": design, "signature": sig, "key": key, "value": value,
            "case": None, "stage": "",
            "closed": closed, "regressed": regressed, "n_changed": 1,
            "after_passed": closed if passed is None else passed}


class ShrinkageTests(unittest.TestCase):
    def test_empty_memory_is_the_beta11_prior(self):
        self.assertEqual(rm.RecoveryMemory([]).p_close("x", "K", 1)[0], 0.5)

    def test_chipmem_update_formula(self):
        # One closure at every level, K=8: p1=(1+8*.5)/9, p2=(1+8*p1)/9, p3=(1+8*p2)/9.
        mem = rm.RecoveryMemory([obs("s", "K", "1", True)])
        p = 0.5
        for _ in range(3):
            p = (1 + 8 * p) / 9
        self.assertAlmostEqual(mem.p_close("s", "K", 1)[0], p)

    def test_sparse_specific_evidence_is_pulled_toward_the_parent(self):
        many = [obs("s", "K", "1", False) for _ in range(40)]
        mem = rm.RecoveryMemory(many + [obs("s", "K", "2", True)])
        # K=2 closed once, but the signature/key parent says it rarely closes.
        self.assertLess(mem.p_close("s", "K", 2)[0], 0.4)

    def test_regression_risk_is_separate(self):
        mem = rm.RecoveryMemory([obs("s", "K", "1", True, regressed=True)] * 5)
        est = mem.estimate({"s"}, "K", 1)
        self.assertGreater(est["p_regress"], 0.5)
        self.assertAlmostEqual(est["net"], est["p_close_any"] - est["p_regress"], places=3)


class ObservationTests(unittest.TestCase):
    def test_transition_records_closure_and_regression(self):
        c = case("d", result({"A": 1}, ["signoff:setup"]),
                 result({"A": 2}, ["signoff:hold"]))
        o = rm.observations([c])
        self.assertEqual(len(o), 1)
        self.assertTrue(o[0]["closed"])
        self.assertTrue(o[0]["regressed"])
        self.assertEqual((o[0]["key"], o[0]["value"]), ("A", "2"))

    def test_single_change_only_drops_confounded_transitions(self):
        c = case("d", result({"A": 1, "B": 1}, ["signoff:setup"]),
                 result({"A": 2, "B": 2}))
        self.assertEqual(len(rm.observations([c])), 2)
        self.assertEqual(rm.observations([c], single_change_only=True), [])


class SkillGateTests(unittest.TestCase):
    def test_do_rules_need_verified_passes_from_distinct_cases(self):
        unverified = [dict(obs("s", "K", "1", True, passed=False), case=f"c{i}")
                      for i in range(5)]
        self.assertEqual(rm.skills(unverified)["DO"], [])
        # Three verified closures inside ONE case are one piece of evidence.
        one_case = [dict(obs("s", "K", "1", True), case="c0") for _ in range(3)]
        self.assertEqual(rm.skills(one_case)["DO"], [])
        verified = [dict(obs("s", "K", "1", True), case=f"c{i}") for i in range(3)]
        rule = rm.skills(verified)["DO"][0]
        self.assertEqual((rule["set"], rule["source_cases"]), ({"K": 1}, 3))

    def test_avoid_rules_need_repeated_regressions(self):
        o = [obs("s", "K", "1", False, regressed=True) for _ in range(3)]
        self.assertEqual(rm.skills(o)["AVOID"][0]["set"], {"K": 1})
        self.assertEqual(rm.skills(o[:2])["AVOID"], [])


class RetuneTests(unittest.TestCase):
    def test_too_few_rows_keeps_current_k(self):
        self.assertEqual(rm.retune_k([obs("s", "K", "1", True)] * 39, current=8.0), 8.0)

    def test_picks_k_by_heldout_likelihood_from_the_chipmem_grid(self):
        # Specific evidence is reliable and plentiful -> weak shrinkage wins.
        rows = ([obs("s", "A", "1", True) for _ in range(60)]
                + [obs("s", "B", "1", False) for _ in range(60)])
        k = rm.retune_k(rows)
        self.assertIn(k, rm.K_GRID)
        self.assertEqual(k, 1.0)


class NudgeTests(unittest.TestCase):
    def test_low_probability_candidate_carries_a_nudge(self):
        history = case("h", result({"A": 1}, ["signoff:setup"]), result({"A": 2}))
        target = case("t", result({"A": 1}, ["signoff:setup"]))
        graph = experiment_graph.build([history, target])
        mem = rm.RecoveryMemory([obs("signoff:setup", "A", "2", False) for _ in range(60)])
        cand = closed_loop_search.propose(target, graph, 4, True, mem)["candidates"][0]
        self.assertLess(cand["recovery_estimate"]["p_close_any"], rm.NUDGE_THRESHOLD)
        self.assertIn("nudge", cand)
        good = rm.RecoveryMemory([obs("signoff:setup", "A", "2", True) for _ in range(60)])
        self.assertNotIn("nudge", closed_loop_search.propose(target, graph, 4, True, good)["candidates"][0])


class EvaluationTests(unittest.TestCase):
    def test_leave_one_design_out_reports_baselines(self):
        cases = [case(d, result({"A": 1}, ["signoff:setup"]), result({"A": 2}))
                 for d in ("a", "b", "c")]
        r = rm.evaluate(cases)
        self.assertEqual(r["designs"], 3)
        self.assertEqual(set(r["brier"]), {"hierarchical", "flat_laplace", "base_rate"})
        self.assertIn("current_heuristic", r["auc"])


class ProposeIntegrationTests(unittest.TestCase):
    def test_memory_reorders_candidates_and_attaches_estimates(self):
        # Two actions both have closure evidence; memory knows B closes setup far
        # more reliably across other designs.
        history = case("h",
                       result({"A": 1, "B": 1}, ["signoff:setup"]),
                       result({"A": 2, "B": 1}),
                       result({"A": 2, "B": 1}, ["signoff:setup"]),
                       result({"A": 2, "B": 2}))
        target = case("t", result({"A": 1, "B": 1}, ["signoff:setup"]))
        graph = experiment_graph.build([history, target])
        strong = [obs("signoff:setup", "B", "2", True) for _ in range(20)]
        weak = [obs("signoff:setup", "A", "2", False) for _ in range(20)]
        mem = rm.RecoveryMemory(strong + weak)
        out = closed_loop_search.propose(target, graph, 4, True, mem)
        self.assertEqual(out["candidates"][0]["action"]["key"], "B")
        self.assertIn("recovery_estimate", out["candidates"][0])
        plain = closed_loop_search.propose(target, graph, 4, True)
        self.assertNotIn("recovery_estimate", plain["candidates"][0])


if __name__ == "__main__":
    unittest.main()
