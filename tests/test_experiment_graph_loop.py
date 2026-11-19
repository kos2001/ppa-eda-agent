import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import closed_loop_search
import experiment_graph


def result(tag, overrides, violations=(), passed=False, **extra):
    return {"tag": tag, "overrides": overrides, "stage": "signoff",
            "pdk": "sky130A", "scl": "sky130_fd_sc_hd",
            "verdict": {"passed": passed, "violations": list(violations),
                        "unverified": []}, **extra}


def case(design, rows, source, *later, image="openlane2:test"):
    return {"design": design, "_file": source,
            "topology": {"has_macros": False},
            "toolchain": {"openlane_image": image} if image else {},
            "iterations": [{"results": rows}] + [{"results": r} for r in later]}


def associations(graph):
    return [e for e in graph["edges"] if e["relation"].startswith("associated_with")]


FANOUT = "1 max-fanout (DRV) violation(s)"
ANTENNA = "1 routing antenna violation(s)"


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

    def test_siblings_changing_two_knobs_are_not_a_transition(self):
        # Parallel siblings of one iteration were never run one after the
        # other; their difference here is two knobs, so no single action
        # can be credited.
        graph = experiment_graph.build([case("d", [
            result("a", {"K": 1}, [FANOUT]),
            result("b", {"J": 1}, [ANTENNA]),
        ], "d.json")])
        self.assertEqual(associations(graph), [])
        self.assertNotIn("followed_by", {e["relation"] for e in graph["edges"]})

    def test_only_one_knob_sibling_pairs_are_controlled_comparisons(self):
        graph = experiment_graph.build([case("d", [
            result("base", {}, [FANOUT]),
            result("a", {"K": 1}, passed=True),
            result("b", {"J": 1}, [ANTENNA]),
        ], "d.json")])
        runs = {n["tag"]: node_id for node_id, n in graph["nodes"].items()
                if n["kind"] == "run"}
        pairs = {(e["before_run"], e["after_run"]) for e in associations(graph)}
        self.assertTrue(pairs)
        for before, after in pairs:
            self.assertIn(runs["base"], (before, after))
        self.assertEqual({e["basis"] for e in associations(graph)},
                         {"controlled_sibling"})

    def test_repair_iteration_is_linked_to_the_run_it_repaired(self):
        graph = experiment_graph.build([case(
            "d", [result("x", {"K": 1}, [FANOUT]), result("y", {"K": 9}, [FANOUT, ANTENNA])],
            "d.json", [result("x-iter1", {"K": 1, "M": 2}, passed=True)])])
        runs = {n["tag"]: node_id for node_id, n in graph["nodes"].items()
                if n["kind"] == "run"}
        lineage = [e for e in associations(graph) if e["basis"] == "lineage"]
        self.assertTrue(lineage)
        self.assertEqual({(e["before_run"], e["after_run"]) for e in lineage},
                         {(runs["x"], runs["x-iter1"])})
        follows = {(e["source"], e["target"]) for e in graph["edges"]
                   if e["relation"] == "followed_by"}
        self.assertEqual(follows, {(runs["x"], runs["x-iter1"])})

    def test_an_explicit_parent_in_another_case_is_a_transition(self):
        seed = case("d", [result("seed", {}, [FANOUT])], "seed.json")
        child = case("d", [result("next", {"K": 1}, passed=True,
                                  parent={"source": "seed.json", "tag": "seed"})],
                     "next.json")
        closures = [e for e in associations(experiment_graph.build([seed, child]))
                    if e["relation"] == "associated_with_closure"]
        self.assertEqual(len(closures), 1)
        self.assertEqual(closures[0]["basis"], "lineage")

    def test_unevaluated_or_cross_technology_pairs_are_not_evidence(self):
        deferred = result("b", {"K": 1}, passed=False, not_evaluated=True)
        deferred["verdict"] = None
        other_scl = result("c", {"J": 1}, passed=True, scl="sky130_fd_sc_hs")
        graph = experiment_graph.build([case("d", [
            result("a", {}, [FANOUT]), deferred, other_scl], "d.json")])
        self.assertEqual(associations(graph), [])

    def test_run_nodes_carry_the_provenance_used_for_comparison(self):
        graph = experiment_graph.build([case("d", [result("a", {})], "d.json")])
        run = next(n for n in graph["nodes"].values() if n["kind"] == "run")
        self.assertEqual((run["pdk"], run["scl"], run["image"]),
                         ("sky130A", "sky130_fd_sc_hd", "openlane2:test"))


class ClosedLoopTests(unittest.TestCase):
    def setUp(self):
        precedent = case("new", [
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
        history = case("current", [
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
        history = case("current", [
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

    def test_other_design_evidence_needs_an_explicit_opt_in(self):
        precedent = case("other", [
            result("bad", {"MAX_FANOUT_CONSTRAINT": 10}, [FANOUT]),
            result("fixed", {"MAX_FANOUT_CONSTRAINT": 16}, passed=True),
        ], "other.json")
        graph = experiment_graph.build([precedent, self.current])
        plan = closed_loop_search.propose(self.current, graph)
        self.assertEqual(plan["stop_reason"], "actions_exhausted")
        self.assertEqual(plan["excluded_evidence"]["other_design"], 2)
        allowed = closed_loop_search.propose(self.current, graph, allow_cross_design=True)
        self.assertEqual(allowed["stop_reason"], "candidates_proposed")
        self.assertEqual(allowed["candidates"][0]["evidence"]["scope"], "cross_design")

    def test_unknown_or_different_toolchain_is_not_compatible_evidence(self):
        for image, reason in ((None, "provenance_unknown"),
                              ("openlane2:other", "toolchain_mismatch")):
            with self.subTest(image=image):
                precedent = case("new", [
                    result("bad", {"MAX_FANOUT_CONSTRAINT": 10}, [FANOUT]),
                    result("fixed", {"MAX_FANOUT_CONSTRAINT": 16}, passed=True),
                ], "old.json", image=image)
                graph = experiment_graph.build([precedent, self.current])
                plan = closed_loop_search.propose(self.current, graph)
                self.assertEqual(plan["stop_reason"], "actions_exhausted")
                self.assertEqual(plan["excluded_evidence"][reason], 2)

    def test_tool_default_technology_matches_only_the_same_default(self):
        def default_tech(row):
            row["pdk"] = row["scl"] = None
            return row
        precedent = case("new", [
            default_tech(result("bad", {"MAX_FANOUT_CONSTRAINT": 10}, [FANOUT])),
            default_tech(result("fixed", {"MAX_FANOUT_CONSTRAINT": 16}, passed=True)),
        ], "old.json")
        current = case("new", [default_tech(result("now", {}, [FANOUT]))], "new.json")
        plan = closed_loop_search.propose(current, experiment_graph.build([precedent, current]))
        self.assertEqual(plan["stop_reason"], "candidates_proposed")
        # Explicit sky130A/hd against an unrecorded default is not assumed equal.
        plan = closed_loop_search.propose(self.current,
                                          experiment_graph.build([precedent, self.current]))
        self.assertEqual(plan["stop_reason"], "actions_exhausted")
        self.assertEqual(plan["excluded_evidence"]["technology_unresolved"], 2)

    def test_relaxing_the_timing_objective_is_never_proposed(self):
        for key in ("CLOCK_PERIOD", "IO_DELAY_CONSTRAINT"):
            with self.subTest(key=key):
                setup = "1 setup timing violation(s)"
                precedent = case("new", [
                    result("tight", {key: 10}, [setup]),
                    result("relaxed", {key: 20}, passed=True),
                ], "old.json")
                current = case("new", [result("now", {key: 10, "K": 1}, [setup])],
                               "new.json")
                plan = closed_loop_search.propose(
                    current, experiment_graph.build([precedent, current]))
                self.assertEqual(plan["candidates"], [])
                self.assertEqual(plan["excluded_actions"], {"objective_constraint": 1})

    def test_a_real_winner_is_terminal(self):
        self.current["iterations"][0]["results"].append(result("pass", {}, passed=True))
        self.assertEqual(closed_loop_search.propose(self.current, self.graph)["stop_reason"],
                         "winner_found")


if __name__ == "__main__":
    unittest.main()
