import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from evaluation_provenance import digest
from surrogate_dataset import load_dataset

H = "a" * 64


def candidate():
    context = {"design": "dut_top", "inputs": {"complete": True, "config_sha256": H,
               "rtl_sha256": {"dut.v": H}, "verification_sha256": {}},
               "evaluator_sha256": {k: H for k in ("orchestrator.py", "power_activity.py", "equiv_check.py", "evaluation_provenance.py")}, "sdc_sha256": [H],
               "liberty_sha256": [H], "macro_views_sha256": [], "pdk": "sky130A",
               "scl": "hd", "image": "image:1", "pdk_version": "b" * 40,
               "openroad_revision": "c" * 40, "flow": "Classic", "semantic_overrides": {},
               "verification_policy": {"rtl_netlist_equivalence_requested": False}}
    return {"tag": "same", "pdk": "sky130A", "scl": "hd", "flow": "Classic",
            "overrides": {"FP_CORE_UTIL": 40}, "seconds": 10,
            "evaluation_fidelity": "full_flow",
            "verdict": {"passed": True, "area_um2": 123, "power": {"total_w": .2}, "worst_setup_slack": -.1},
            "evaluation_provenance": {"complete": True, "context": context, "compatibility_key": digest(context)}}


def case(rows=None, date="2026-10-05"):
    return {"design": "dut", "date": date,
            "toolchain": {"host": {"arch": "x86_64", "system": "Linux", "cpu_count": 4, "docker_platform": "native"}},
            "constraints": {"design": {"settings": [{"key": "CLOCK_PERIOD", "value": 10}]}},
            "iterations": [{"results": rows if rows is not None else [candidate()]}]}


class DatasetTests(unittest.TestCase):
    def load(self, cases):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / "cases").mkdir()
            for name, value in cases.items():
                (root / "cases" / name).write_text(json.dumps(value), encoding="utf-8")
            return load_dataset(root)

    def test_failed_verification_is_measured_but_errors_and_timeouts_are_not(self):
        rows = [candidate() for _ in range(4)]
        rows[0]["verdict"].update(passed=False, violations=["timing"])
        rows[1]["error"] = "tool failed"
        rows[2]["error"] = "TimeoutExpired: timed out"
        rows[3].update(not_evaluated=True, evaluation_fidelity="not_evaluated")
        got = self.load({"a.json": case(rows)})["rows"]
        self.assertEqual([r["status"] for r in got], ["verification_failed", "flow_error", "timeout", "budget_deferred"])
        self.assertTrue(got[0]["eligible"])
        self.assertEqual(got[0]["targets"]["worst_setup_slack"], -.1)
        for row in got[1:]:
            self.assertFalse(row["eligible"])
            self.assertTrue(all(v is None for v in row["targets"].values()))

    def test_duplicate_measurements_preserved_and_case_group_kept(self):
        c = case([candidate(), candidate()])
        got = self.load({"a.json": c, "b.json": copy.deepcopy(c)})
        self.assertEqual(len(got["rows"]), 4)
        self.assertEqual(len({r["id"] for r in got["rows"]}), 4)
        self.assertEqual(len({r["candidate_key"] for r in got["rows"]}), 1)
        self.assertEqual(len({r["group_id"] for r in got["rows"]}), 2)
        expected = hashlib.sha256(json.dumps(c).encode()).hexdigest()
        self.assertEqual(got["source_files"][0]["sha256"], expected)
        self.assertEqual(got["rows"][0]["source_sha256"], expected)

    def test_provenance_flag_alone_and_bad_hash_shapes_do_not_qualify(self):
        rows = []
        for field in ("rtl", "sdc", "evaluator", "policy", "revision", "digest", "flag"):
            row = candidate(); p = row["evaluation_provenance"]; c = p["context"]
            if field == "rtl": c["inputs"]["rtl_sha256"] = {}
            if field == "sdc": c["sdc_sha256"] = ["bad"]
            if field == "evaluator": c["evaluator_sha256"] = {"x": True}
            if field == "policy": c["verification_policy"]["rtl_netlist_equivalence_requested"] = 1
            if field == "revision": c["pdk_version"] = "unknown"
            p["compatibility_key"] = digest(c)
            if field == "digest": p["compatibility_key"] = "f" * 64
            if field == "flag": p["complete"] = 1
            rows.append(row)
        self.assertTrue(all(not r["eligible"] for r in self.load({"a.json": case(rows)})["rows"]))

    def test_candidate_context_consistency_and_custom_flow_hashes(self):
        good = candidate(); ctx = good["evaluation_provenance"]["context"]
        ctx.update(flow="FanoutRepair", flow_sources={"repair.py": H})
        good["flow"] = "FanoutRepair"; good["evaluation_provenance"]["compatibility_key"] = digest(ctx)
        bad = copy.deepcopy(good); bad["scl"] = "other"
        rows = self.load({"a.json": case([good, bad])})["rows"]
        self.assertTrue(rows[0]["eligible"])
        self.assertFalse(rows[1]["eligible"])
        self.assertIn("candidate_context_mismatch:scl", rows[1]["reasons"])

    def test_dates_are_conservative_and_filename_cannot_supply_time(self):
        values = {"z.json": case(date="2026-10-05"), "a.json": case(date="2026-10-05T12:00:00Z"),
                  "2026-10-01.json": case(date=None), "bad.json": case(date="2026-99-01"),
                  "naive.json": case(date="2026-10-05T01:00:00")}
        got = self.load(values)["rows"]
        valid = [r for r in got if r["eligible"]]
        self.assertEqual(len(valid), 2)
        self.assertEqual({r["timestamp"] for r in valid}, {"2026-10-05T00:00:00Z"})
        self.assertTrue(all(r["timestamp"] is None for r in got if not r["eligible"]))

    def test_rescored_case_uses_later_availability_date_and_missing_revision_is_unknown(self):
        a = case(); a["rescored"] = {"date": "2026-10-12"}
        b = case(); b["rescored"] = {"what": "changed metrics"}
        got = self.load({"a.json": a, "b.json": b})["rows"]
        self.assertEqual(got[0]["timestamp"], "2026-10-12T00:00:00Z")
        self.assertFalse(got[1]["eligible"])

    def test_lineage_parents_and_explicit_roots_stay_together(self):
        a, b = case(), case()
        b["graph_loop"] = {"parent_case": "a.json"}
        rows = self.load({"a.json": a, "b.json": b})["rows"]
        self.assertEqual(rows[0]["group_id"], rows[1]["group_id"])
        a["lineage_root"] = b["lineage_root"] = "root-experiment"
        rows = self.load({"a.json": a, "b.json": b})["rows"]
        self.assertEqual({r["group_id"] for r in rows}, {"lineage:root-experiment"})

    def test_invalid_targets_stay_unknown_and_host_is_part_of_timing_cohort(self):
        row = candidate(); row["verdict"].update(area_um2=True, worst_setup_slack=float("nan"))
        row["verdict"]["power"]["total_w"] = float("inf")
        a = case([row]); a.pop("toolchain")
        got = self.load({"a.json": a})["rows"][0]
        self.assertFalse(got["eligible"])
        self.assertTrue(all(v is None for v in got["targets"].values()))
        a,b = case(),case(); b["toolchain"]["host"]["cpu_count"] = 16
        rows = self.load({"a.json": a, "b.json": b})["rows"]
        self.assertNotEqual(rows[0]["cohort_key"], rows[1]["cohort_key"])

    def test_context_keys_are_not_removed_and_unknown_fidelity_is_quarantined(self):
        a,b,c = candidate(),candidate(),candidate()
        ctx=b["evaluation_provenance"]["context"];ctx["semantic_overrides"]={"CLOCK_PERIOD": 20}
        b["overrides"]["CLOCK_PERIOD"]=20;b["evaluation_provenance"]["compatibility_key"]=digest(ctx)
        c["evaluation_fidelity"] = ["full_flow"]
        rows=self.load({"a.json":case([a,b,c])})["rows"]
        self.assertNotEqual(rows[0]["cohort_key"],rows[1]["cohort_key"])
        self.assertFalse(rows[2]["eligible"])

    def test_unqualified_macro_keeps_area_but_quarantines_model_dependent_targets(self):
        c = case(); c["topology"] = {"has_macros": True}
        c["iterations"][0]["results"][0]["verdict"]["model_validity"] = {"macro_arc_audit": {"model_qualified": False}}
        row = self.load({"a.json": c})["rows"][0]
        self.assertTrue(row["eligible"])
        self.assertEqual(row["targets"]["area_um2"], 123)
        self.assertIsNone(row["targets"]["power_w"])
        self.assertIsNone(row["targets"]["worst_setup_slack"])

    def test_flags_cannot_use_numeric_truth_values_and_lineage_cycles_are_quarantined(self):
        row = candidate(); row["timed_out"] = 1
        self.assertFalse(self.load({"a.json": case([row])})["rows"][0]["eligible"])
        row = candidate(); row["verdict"]["passed"] = 1
        self.assertFalse(self.load({"a.json": case([row])})["rows"][0]["eligible"])
        a,b = case(),case()
        a["graph_loop"] = {"parent_case": "b.json"}; b["graph_loop"] = {"parent_case": "a.json"}
        self.assertTrue(all("lineage_cycle" in r["reasons"] for r in self.load({"a.json": a, "b.json": b})["rows"]))


if __name__ == "__main__":
    unittest.main()
