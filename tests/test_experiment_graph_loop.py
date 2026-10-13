import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import closed_loop_search
import experiment_graph


def result(tag, overrides, violations=(), passed=False):
    return {"tag": tag, "overrides": overrides, "stage": "signoff",
            "verdict": {"passed": passed, "violations": list(violations),
                        "unverified": []}}


def case(design, rows, source):
    return {"design": design, "_file": source,
            "topology": {"has_macros": False},
            "iterations": [{"results": rows}]}


class ExperimentGraphTests(unittest.TestCase):
    def test_records_closure_and_regression_without_claiming_causality(self):
        rows = [
            result("a", {"K": 1}, ["3 max-fanout (DRV) violation(s)"]),
            result("b", {"K": 2}, ["1 routing antenna violation(s)"]),
        ]
        graph = experiment_graph.build([case("d", rows, "d.json")])
        relations = {e["relation"] for e in graph["edges"]}
        self.assertIn("associated_with_closure", relations)
        self.assertIn("associated_with_regression", relations)
        self.assertNotIn("causes", relations)
        experiment_graph.validate(graph)

    def test_every_edge_has_typed_endpoints(self):
        graph = experiment_graph.build([case("d", [result("a", {}, passed=True)], "d.json")])
        for edge in graph["edges"]:
            self.assertIn(edge["source"], graph["nodes"])
            self.assertIn(edge["target"], graph["nodes"])
            self.assertIn("kind", graph["nodes"][edge["source"]])

    def test_separate_case_files_are_not_linked_as_a_transition(self):
        first = case("d", [result("a", {"K": 0}, ["1 max-fanout (DRV) violation(s)"])],
                     "first.json")
        second = case("d", [result("b", {"K": 1}, passed=True)], "second.json")
        graph = experiment_graph.build([first, second])
        relations = [edge["relation"] for edge in graph["edges"]]
        self.assertNotIn("followed_by", relations)
        self.assertNotIn("associated_with_closure", relations)


class ClosedLoopTests(unittest.TestCase):
    def setUp(self):
        precedent = case("old", [
            result("bad", {"MAX_FANOUT_CONSTRAINT": 10},
                   ["3 max-fanout (DRV) violation(s)"]),
            result("fixed", {"MAX_FANOUT_CONSTRAINT": 16}, passed=True),
        ], "old.json")
        self.current = case("new", [
            result("now", {}, ["5 max-fanout (DRV) violation(s)"])
        ], "new.json")
        self.graph = experiment_graph.build([precedent, self.current])

    def test_proposes_an_observed_untried_closure(self):
        plan = closed_loop_search.propose(self.current, self.graph)
        self.assertEqual(plan["stop_reason"], "candidates_proposed")
        self.assertEqual(plan["candidates"][0]["overrides"]["MAX_FANOUT_CONSTRAINT"], 16)
        self.assertTrue(plan["candidates"][0]["advisory_only"])

    def test_never_repeats_an_identical_configuration(self):
        self.current["iterations"][0]["results"].append(
            result("tried", {"MAX_FANOUT_CONSTRAINT": 16},
                   ["5 max-fanout (DRV) violation(s)"]))
        plan = closed_loop_search.propose(self.current, self.graph)
        self.assertEqual(plan["stop_reason"], "actions_exhausted")

    def test_unrelated_regression_still_penalizes_action(self):
        history = case("history", [
            result("a", {"K": 0}, ["1 max-fanout (DRV) violation(s)"]),
            result("b", {"K": 1}, ["1 routing antenna violation(s)"]),
        ], "history.json")
        current = case("current", [
            result("now", {}, ["1 max-fanout (DRV) violation(s)"])
        ], "current.json")
        graph = experiment_graph.build([history, current])
        plan = closed_loop_search.propose(current, graph)
        self.assertFalse(any(c["action"] == {"key": "K", "value": 1}
                             for c in plan["candidates"]))

    def test_removed_override_is_omitted_instead_of_serialized_as_null(self):
        history = case("history", [
            result("a", {"K": 1}, ["1 max-fanout (DRV) violation(s)"]),
            result("b", {}, passed=True),
        ], "history.json")
        current = case("current", [
            result("now", {"K": 1, "KEEP": 2},
                   ["1 max-fanout (DRV) violation(s)"])
        ], "current.json")
        plan = closed_loop_search.propose(current,
                                           experiment_graph.build([history, current]))
        candidate = plan["candidates"][0]
        self.assertNotIn("K", candidate["overrides"])
        self.assertEqual(candidate["overrides"]["KEEP"], 2)

    def test_plateau_requires_distinct_configs_with_same_state(self):
        rows = [result(str(i), {"K": i}, ["1 max-fanout (DRV) violation(s)"])
                for i in range(3)]
        self.assertTrue(closed_loop_search.plateau(rows))
        rows[-1]["verdict"]["violations"] = ["1 routing antenna violation(s)"]
        self.assertFalse(closed_loop_search.plateau(rows))

    def test_a_real_winner_is_terminal(self):
        self.current["iterations"][0]["results"].append(result("pass", {}, passed=True))
        self.assertEqual(closed_loop_search.propose(self.current, self.graph)["stop_reason"],
                         "winner_found")


if __name__ == "__main__":
    unittest.main()
