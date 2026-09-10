"""Tests for pipeline/ieda_metrics.py — iEDA feature JSON into score().

The fixtures are shaped from iEDA's writer source (feature_parser_*.cpp
at OSCC-Project/iEDA master, 2026-03-11), not captured from a run: no
iEDA binary is installed here. So the tests guard the two things that
can be guarded without one — that every key read exists in the writer,
and that the gate stays as strict on this source as on OpenLane's:
what iEDA never checks must read as unverified, never as clean.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import ieda_metrics  # noqa: E402
import orchestrator  # noqa: E402


def summary_json():
    """Root keys and fields from FeatureParser::buildSummary()."""
    return {
        "Design Information": {"design_name": "gcd", "flow_stage": "route"},
        "Design Layout": {"design_dbu": 1000, "die_area": 12000.0,
                          "core_area": 9800.0, "core_usage": 0.41},
        "Design Statis": {"num_instances": 1234, "num_nets": 1100},
        "Instances": {
            "total": {"num": 1234, "area": 4018.0, "core_usage": 0.41},
            "logic": {"num": 1200, "area": 3900.0, "core_usage": 0.398},
            "macros": {"num": 0, "area": 0.0, "core_usage": 0.0},
        },
        "Nets": {"num_total": 1100, "wire_len": 52340.5, "num_via": 8120},
        "Pins": {"max_fanout": 23},
    }


def route_json(dr_last=0, vr=None):
    """FeatureParser::buildTools(step="route") → {"route": {...}}."""
    route = {
        "DR": [
            {"iter": 1, "total_violation_num": 17, "total_wire_length": 52000.0},
            {"iter": 2, "total_violation_num": dr_last, "total_wire_length": 52340.5},
        ],
    }
    if vr is not None:
        route["VR"] = vr
    return {"route": route}


def cts_json(setup_wns=0.42, hold_wns=0.11):
    return {"CTS": {"buffer_num": 12, "clocks_timing": [
        {"clock_name": "clk", "setup_tns": 0.0, "setup_wns": setup_wns,
         "hold_tns": 0.0, "hold_wns": hold_wns, "suggest_freq": 210.0},
    ]}}


def timing_eval_json(setup_wns=0.35):
    """FeatureParser::buildSummaryTimingEval(): keyed by routing type."""
    return {
        "clocks_timing": {
            "HPWL": [{"clock_name": "clk", "setup_wns": 0.9, "hold_wns": 0.2,
                      "setup_tns": 0.0, "hold_tns": 0.0}],
            "DR": [{"clock_name": "clk", "setup_wns": setup_wns, "hold_wns": 0.15,
                    "setup_tns": 0.0, "hold_tns": 0.0}],
        },
        "power_info": {"DR": {"static_power": 1.2e-6, "dynamic_power": 4.4e-4}},
    }


def merged(*parts):
    out = {}
    for p in parts:
        out.update(p)
    return out


class MappingTests(unittest.TestCase):
    def test_summary_fields_land_on_openlane_keys(self):
        m = ieda_metrics.to_openlane_metrics(summary_json())
        self.assertEqual(m["design__instance__count"], 1234)
        self.assertEqual(m["design__instance__area"], 4018.0)
        # Standard-cell utilisation is the logic share, not total.
        self.assertEqual(m["design__instance__utilization__stdcell"], 0.398)
        self.assertEqual(m["route__wirelength"], 52340.5)
        self.assertEqual(m["route__vias"], 8120)
        self.assertEqual(m["metrics__source"], "iEDA feature JSON")

    def test_router_violations_come_from_the_last_dr_iteration(self):
        m = ieda_metrics.to_openlane_metrics(route_json(dr_last=3))
        self.assertEqual(m["route__drc_errors"], 3)

    def test_violation_repair_count_wins_over_dr_when_present(self):
        vr = {"within_net_total_violation_num": 1, "among_net_total_violation_num": 2}
        m = ieda_metrics.to_openlane_metrics(route_json(dr_last=9, vr=vr))
        self.assertEqual(m["route__drc_errors"], 3)

    def test_timing_is_design_level_never_a_corner(self):
        m = ieda_metrics.to_openlane_metrics(cts_json(setup_wns=-0.3, hold_wns=0.1))
        self.assertEqual(m["timing__setup__wns"], -0.3)
        self.assertEqual(m["timing__hold__wns"], 0.1)
        self.assertFalse(any("__corner:" in k for k in m))

    def test_timing_eval_prefers_detailed_route_wire_model(self):
        m = ieda_metrics.to_openlane_metrics(timing_eval_json(setup_wns=0.35))
        self.assertEqual(m["timing__setup__wns"], 0.35)
        self.assertAlmostEqual(m["power__total"], 1.2e-6 + 4.4e-4)

    def test_optimisation_step_reads_the_post_step_state(self):
        feat = {"optSetup": {"clocks_timing": [
            {"clock_name": "clk", "origin_wns": -1.0, "opt_wns": -0.2,
             "origin_tns": -9.0, "opt_tns": -0.4},
        ]}}
        m = ieda_metrics.to_openlane_metrics(feat)
        self.assertEqual(m["timing__setup__wns"], -0.2)
        self.assertEqual(m["timing__setup__tns"], -0.4)

    def test_nothing_is_defaulted_to_zero(self):
        m = ieda_metrics.to_openlane_metrics({})
        self.assertEqual(set(m), {"metrics__source"})


class GateTests(unittest.TestCase):
    """The verdict must not soften for a tool that reports less."""

    def test_a_clean_ieda_run_is_still_not_a_pass(self):
        feat = merged(summary_json(), route_json(dr_last=0), cts_json())
        verdict = orchestrator.score(ieda_metrics.to_openlane_metrics(feat), {})
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["violations"], [])
        # 22 of 23 signoff checks have no iEDA equivalent.
        self.assertEqual(len(verdict["unverified"]), len(orchestrator.SIGNOFF_METRICS) - 1)
        self.assertNotIn("routing DRC error(s)", verdict["unverified"])

    def test_router_violations_are_a_violation_not_unverified(self):
        feat = merged(summary_json(), route_json(dr_last=5), cts_json())
        verdict = orchestrator.score(ieda_metrics.to_openlane_metrics(feat), {})
        self.assertIn("5 routing DRC error(s)", verdict["violations"])

    def test_negative_setup_from_ieda_fails_the_gate(self):
        feat = merged(summary_json(), cts_json(setup_wns=-0.25))
        verdict = orchestrator.score(ieda_metrics.to_openlane_metrics(feat), {})
        self.assertTrue(any("setup WNS -0.25" in v for v in verdict["violations"]))

    def test_negative_hold_from_ieda_fails_the_gate(self):
        feat = merged(summary_json(), cts_json(hold_wns=-0.05))
        verdict = orchestrator.score(ieda_metrics.to_openlane_metrics(feat), {})
        self.assertTrue(any("hold WNS -0.05" in v for v in verdict["violations"]))

    def test_corner_keys_still_win_over_the_design_level_key(self):
        # OpenLane emits both; a positive design-level value must not
        # hide a negative corner.
        metrics = {k: 0 for k, _ in orchestrator.SIGNOFF_METRICS}
        metrics["timing__setup__wns"] = 0
        metrics["timing__setup__wns__corner:ss"] = -0.4
        verdict = orchestrator.score(metrics, {})
        self.assertTrue(any("-0.4" in v for v in verdict["violations"]))

    def test_utilization_target_applies_to_ieda_too(self):
        feat = summary_json()
        verdict = orchestrator.score(ieda_metrics.to_openlane_metrics(feat),
                                     {"max_core_utilization": 0.3})
        self.assertTrue(any("utilization 0.398" in v for v in verdict["violations"]))

    def test_coverage_names_every_unchecked_signoff_with_a_reason(self):
        m = ieda_metrics.to_openlane_metrics(merged(summary_json(), route_json()))
        cov = ieda_metrics.coverage(m, orchestrator.SIGNOFF_METRICS)
        self.assertEqual(cov["checked"], ["route__drc_errors"])
        self.assertEqual(set(cov["never_checked"]),
                         {k for k, _ in orchestrator.SIGNOFF_METRICS} - {"route__drc_errors"})
        for reason in cov["never_checked"].values():
            self.assertTrue(reason)
        self.assertIn("no real iEDA run", cov["not_yet"])

    def test_not_produced_list_matches_signoff_metrics(self):
        # If SIGNOFF_METRICS grows, the reason table must grow with it.
        signoff = {k for k, _ in orchestrator.SIGNOFF_METRICS}
        self.assertEqual(set(ieda_metrics.NOT_PRODUCED) | {"route__drc_errors"}, signoff)


class SignoffChecksTests(unittest.TestCase):
    """score() now records every check as a row, for the coverage strip."""

    def test_one_row_per_signoff_check_in_order(self):
        feat = merged(summary_json(), route_json(dr_last=4))
        verdict = orchestrator.score(ieda_metrics.to_openlane_metrics(feat), {})
        rows = verdict["signoff_checks"]
        self.assertEqual([r["key"] for r in rows], [k for k, _ in orchestrator.SIGNOFF_METRICS])
        by_key = {r["key"]: r for r in rows}
        self.assertEqual(by_key["route__drc_errors"]["count"], 4)
        self.assertIsNone(by_key["magic__drc_error__count"]["count"])
        self.assertEqual(verdict["metrics_source"], "iEDA feature JSON")

    def test_openlane_source_is_named_and_clean_rows_are_zero(self):
        metrics = {k: 0 for k, _ in orchestrator.SIGNOFF_METRICS}
        verdict = orchestrator.score(metrics, {})
        self.assertEqual(verdict["metrics_source"], "OpenLane metrics.json")
        self.assertTrue(all(r["count"] == 0 for r in verdict["signoff_checks"]))
        self.assertTrue(verdict["passed"])


class LoadTests(unittest.TestCase):
    def test_files_merge_at_root_and_unreadable_ones_are_named(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            (d / "summary.json").write_text(json.dumps(summary_json()))
            (d / "route.json").write_text(json.dumps(route_json(dr_last=2)))
            (d / "bad.json").write_text("{not json")
            feat = ieda_metrics.load_feature_files(
                [d / "summary.json", d / "route.json", d / "bad.json"])
        self.assertIn("Instances", feat)
        self.assertIn("route", feat)
        self.assertEqual(len(feat["_unreadable"]), 1)


if __name__ == "__main__":
    unittest.main()
