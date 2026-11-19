import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import context_search_benchmark as benchmark
import evaluation_provenance


def measured(tag, area=100, power=0.001, core=200, slack=1):
    context = {"design": "counter4", "pdk": "sky130A", "scl": "hd", "pdk_version": "pinned",
               "image": "pinned", "openroad_revision": "revision", "inputs": {"complete": True},
               "sdc_sha256": ["sdc"], "liberty_sha256": ["lib"], "flow": "Classic",
               "verification_policy": {"rtl_netlist_equivalence_requested": True}}
    return {"tag": tag, "overrides": {"FP_CORE_UTIL": 35, "SYNTH_STRATEGY": "AREA 0"},
            "pdk": "sky130A", "scl": "hd", "flow": "Classic",
            "evaluation_provenance": {"complete": True, "context": context,
                                      "compatibility_key": evaluation_provenance.digest(context)},
            "equivalence": {"equivalent": True, "vacuous": False},
            "final_equivalence": {"equivalent": True, "vacuous": False},
            "verdict": {"passed": True, "violations": [], "unverified": [],
                "area_um2": area, "core_area_um2": core, "worst_setup_slack": slack,
                "timing_corners": [{"corner": "ss", "setup_wns": 0, "hold_wns": 0}],
                "signoff_checks": [{"key": k, "count": 0} for k in [
                    "magic__drc_error__count", "klayout__drc_error__count", "design__lvs_error__count"]],
                "power_activity": {"annotated_pins": 10, "parasitics": "actual.spef",
                    "simulation_tail": "counter4_tb: 0 error(s)",
                    "annotated": {"total": {"total_w": power}}}}}


class ContextSearchTests(unittest.TestCase):
    def test_missing_or_failed_gates_cannot_rank(self):
        row = measured("a")
        self.assertAlmostEqual(benchmark.objective(row), 0.1)
        changes = [lambda r: r.pop("final_equivalence"),
                   lambda r: r["equivalence"].update(vacuous=True),
                   lambda r: r["verdict"]["timing_corners"][0].update(hold_wns=-0.1),
                   lambda r: r["verdict"]["power_activity"].pop("annotated"),
                   lambda r: r["verdict"]["power_activity"].update(simulation_tail="counter4_tb: 1 error(s)")]
        for change in changes:
            current = copy.deepcopy(row)
            change(current)
            self.assertIsNone(benchmark.objective(current))

    def test_frontier_preserves_tradeoff_and_rejects_incompatible_cohort(self):
        a = measured("area", 90, 0.001, 220, 1)
        b = measured("core", 100, 0.001, 180, 2)
        dominated = measured("dominated", 110, 0.002, 250, 0.5)
        self.assertEqual([r["tag"] for r in benchmark.select_seeds([a, b, dominated])], ["area", "core"])
        b["evaluation_provenance"]["context"]["image"] = "different"
        b["evaluation_provenance"]["compatibility_key"] = evaluation_provenance.digest(b["evaluation_provenance"]["context"])
        with self.assertRaisesRegex(ValueError, "provenance"):
            benchmark.select_seeds([a, b])

    def test_proposal_delta_uses_its_visible_seed_and_cannot_change_clock(self):
        row = measured("seed")
        protocol = {"allowed_domains": {"FP_CORE_UTIL": [25, 35]}}
        response = {"diagnosis": "test", "candidates": [{"seed_tag": "seed",
                    "overrides": {"FP_CORE_UTIL": 25}, "hypothesis": "test density"}]}
        candidate = benchmark.proposal_candidate(response, [row], protocol, "trial")
        self.assertEqual(candidate["overrides"], {"FP_CORE_UTIL": 25, "SYNTH_STRATEGY": "AREA 0"})
        response["candidates"][0]["overrides"] = {"CLOCK_PERIOD": 20}
        with self.assertRaises(ValueError):
            benchmark.proposal_candidate(response, [row], protocol, "trial")
        response["candidates"][0].update(seed_tag="hidden", overrides={"FP_CORE_UTIL": 25})
        with self.assertRaisesRegex(ValueError, "visible"):
            benchmark.proposal_candidate(response, [row], protocol, "trial")

    def test_tool_use_or_incomplete_turn_invalidates_model_isolation(self):
        response = {"diagnosis": "done", "candidates": []}
        events = [{"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(response)}},
                  {"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 5}}]
        raw = lambda: "\n".join(json.dumps(e) for e in events)
        self.assertEqual(benchmark.parse_events(raw())[0], response)
        events.insert(0, {"type": "item.completed", "item": {"type": "command_execution"}})
        with self.assertRaisesRegex(ValueError, "isolation"):
            benchmark.parse_events(raw())
        with self.assertRaisesRegex(ValueError, "complete"):
            benchmark.parse_events('{}')


if __name__ == "__main__":
    unittest.main()
