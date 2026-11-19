"""Decision tests only: fixtures are not measured chip results."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import implementation_contract as contract
import throughput_score as ts


def run(tag, area=1000.0, ipc_cycles=2000, period=10.0, ws=0.0, **kw):
    r = {
        "tag": tag, "group": kw.pop("group", "g"), "functional": "passed",
        "cell_area_um2": area,
        "fmax": {"source": "post_route_sta", "clock_period_ns": period, "setup_ws_ns": ws},
        "features": {"counters": True},
        "workloads": {
            "hidden_a": {"held_out": True, "passed": True, "instructions": 1000, "cycles": ipc_cycles},
            "hidden_b": {"held_out": True, "passed": True, "instructions": 1000, "cycles": ipc_cycles * 4},
            "visible": {"held_out": False, "passed": True, "instructions": 1000, "cycles": 1},
        },
    }
    r.update(kw)
    return r


def evidence(*runs, **kw):
    e = {"design": "core", "baseline": {"cell_area_um2": 1000.0},
         "required_features": ["counters"], "runs": list(runs)}
    e.update(kw)
    return e


class ThroughputScoreTests(unittest.TestCase):
    def test_throughput_is_heldout_ipc_geomean_times_post_route_fmax(self):
        res = ts.score(evidence(run("a", ipc_cycles=2000, period=10.0, ws=2.0)))
        r = res["runs"][0]
        # IPCs 0.5 and 0.125 -> geomean 0.25; achieved period 8 ns -> 125 MHz.
        self.assertAlmostEqual(r["ipc_geomean_held_out"], 0.25)
        self.assertAlmostEqual(r["fmax_mhz"], 125.0)
        self.assertAlmostEqual(r["throughput_mips"], 31.25)
        self.assertNotIn("visible", r["ipc_by_workload"])  # visible IPC=1000 ignored

    def test_cycle_vs_frequency_tradeoff_is_ranked_by_throughput(self):
        # Fewer cycles but a slower clock vs. the reverse: only IPS decides.
        fast_clock = run("fast_clock", ipc_cycles=2000, period=5.0)
        low_cpi = run("low_cpi", ipc_cycles=1000, period=8.0)
        res = ts.score(evidence(fast_clock, low_cpi))
        self.assertEqual(res["runs"][0]["tag"], "low_cpi")
        self.assertEqual(res["runs"][0]["score"], 1.0)
        self.assertAlmostEqual(res["runs"][1]["score"], (0.25 * 200) / (0.5 * 125))

    def test_zero_score_gates(self):
        cases = {
            "functional": run("f", functional="failed"),
            "area": run("big", area=16001.0),
            "feature": run("nofeat", features={"counters": False}),
            "workload": run("wl", workloads={"h": {"held_out": True, "passed": False}}),
            "no_hidden": run("nh", workloads={"v": {"held_out": False, "passed": True,
                                                     "instructions": 1, "cycles": 1}}),
            "fmax": run("nofmax", fmax=None),
        }
        for name, r in cases.items():
            with self.subTest(name):
                out = ts.score(evidence(run("ok"), r))
                bad = next(x for x in out["runs"] if x["tag"] == r["tag"])
                self.assertEqual(bad["score"], 0.0)
                self.assertTrue(bad["zero_reason"])

    def test_area_exactly_at_cap_passes(self):
        out = ts.score(evidence(run("edge", area=16000.0)))
        self.assertIsNone(out["runs"][0]["zero_reason"])

    def test_mean_includes_zero_scored_runs(self):
        out = ts.score(evidence(run("ok"), run("broken", functional="failed")))
        self.assertEqual(out["overall"]["mean_score"], 0.5)
        self.assertEqual(out["overall"]["pass_rate"], 0.5)

    def test_groups_are_budget_matched_summaries(self):
        out = ts.score(evidence(run("a", group="small"), run("b", group="large", ipc_cycles=1000)))
        self.assertEqual(out["by_group"]["large"]["mean_score"], 1.0)
        self.assertAlmostEqual(out["by_group"]["small"]["mean_score"], 0.5)

    def test_reference_best_keeps_scores_comparable_across_batches(self):
        out = ts.score(evidence(run("a"), reference_best_mips=50.0))
        self.assertAlmostEqual(out["runs"][0]["score"], 25.0 / 50.0)

    def test_pre_layout_fmax_is_labelled_in_scope(self):
        out = ts.score(evidence(run("a", fmax={"source": "pre_layout_estimate", "mhz": 90.0})))
        self.assertIn("pre-layout", out["scope"])

    def test_unlabelled_fmax_source_is_rejected(self):
        out = ts.score(evidence(run("a", fmax={"mhz": 90.0})))
        self.assertEqual(out["runs"][0]["score"], 0.0)

    def test_baseline_area_is_required(self):
        with self.assertRaises(ValueError):
            ts.score({"runs": [run("a")], "baseline": {}})


class RelativeAreaBudgetTests(unittest.TestCase):
    def verdict(self):
        return {"passed": True, "violations": [], "unverified": []}

    def test_ratio_above_cap_fails(self):
        v = self.verdict()
        contract.apply_targets(v, contract.target_checks(
            {"design__instance__area": 1601},
            {"max_area_ratio_vs_baseline": 16, "baseline_cell_area_um2": 100}))
        self.assertFalse(v["passed"])
        self.assertAlmostEqual(v["target_checks"][0]["actual"], 16.01)

    def test_ratio_at_cap_passes_and_missing_area_is_unverified(self):
        targets = {"max_area_ratio_vs_baseline": 16, "baseline_cell_area_um2": 100}
        v = self.verdict()
        contract.apply_targets(v, contract.target_checks({"design__instance__area": 1600}, targets))
        self.assertTrue(v["passed"])
        v = self.verdict()
        contract.apply_targets(v, contract.target_checks({}, targets))
        self.assertTrue(v["unverified"])

    def test_ratio_requires_measured_baseline(self):
        for targets in ({"max_area_ratio_vs_baseline": 16},
                        {"max_area_ratio_vs_baseline": 16, "baseline_cell_area_um2": 0},
                        {"baseline_cell_area_um2": 100}):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                contract.validate_spec({"targets": targets})


if __name__ == "__main__":
    unittest.main()


class ArtifactAdapterTests(unittest.TestCase):
    def test_perf_lines_parse_and_duplicates_are_rejected(self):
        import functional_regression as fr
        log = ("PPA_PERF workload=coremark instructions=1000 cycles=1500\n"
               "noise\nPPA_PERF workload=dhry instructions=10 cycles=20\n")
        self.assertEqual(fr.perf_counters(log)["coremark"], {"instructions": 1000, "cycles": 1500})
        with self.assertRaises(ValueError):
            fr.perf_counters(log + "PPA_PERF workload=dhry instructions=1 cycles=1\n")

    def test_run_from_artifacts_sums_seeds_and_marks_missing_heldout(self):
        regression = {"status": "passed", "runs": [
            {"status": "passed", "perf": {"cm": {"instructions": 100, "cycles": 200},
                                          "vis": {"instructions": 1, "cycles": 1}}},
            {"status": "passed", "perf": {"cm": {"instructions": 100, "cycles": 200}}},
        ]}
        metrics = {"design__instance__area__stdcell": 1000.0, "timing__setup__ws": 2.0}
        r = ts.run_from_artifacts("t", regression, metrics, 10.0, ["cm"], {"counters": True})
        self.assertEqual(r["workloads"]["cm"], {"held_out": True, "passed": True,
                                                "instructions": 200, "cycles": 400})
        self.assertFalse(r["workloads"]["vis"]["held_out"])
        out = ts.score(evidence(r))
        self.assertAlmostEqual(out["runs"][0]["throughput_mips"], 0.5 * 125.0)
        missing = ts.run_from_artifacts("m", regression, metrics, 10.0, ["cm", "secret"],
                                        {"counters": True})
        self.assertEqual(ts.score(evidence(missing))["runs"][0]["score"], 0.0)
