import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import functional_oracles as oracle
import functional_regression as regression


def trace(seed=1):
    state = 0
    lines = []
    for index, (reset, enable) in enumerate(oracle.counter4_workload(seed)):
        state = 0 if reset else ((state + enable) & 15)
        lines.append(f"PPA_TRACE cycle={index} rst={reset} en={enable} count={state}")
    return "\n".join(lines)


class OracleTests(unittest.TestCase):
    def test_seeded_workload_passes(self):
        for seed in (1, 17, 101):
            self.assertEqual(oracle.counter4(trace(seed), 322, seed)["status"], "passed")

    def test_pass_marker_cannot_hide_wrong_output(self):
        output = trace().replace("cycle=1 rst=0 en=1 count=1", "cycle=1 rst=0 en=1 count=2")
        output += "\nPPA_REGRESSION_PASS checks=322"
        result = oracle.counter4(output, 322, 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["cycle"], 1)

    def test_truncated_reordered_or_unknown_trace_cannot_pass(self):
        for output in ("", trace().splitlines()[0], trace().replace("cycle=1 ", "cycle=0 "),
                       trace().replace("count=1\n", "count=x\n", 1)):
            self.assertEqual(oracle.counter4(output, 322, 1)["status"], "unverified")

    def test_skipping_required_stimulus_fails(self):
        output = trace().replace("cycle=65 rst=1 en=1", "cycle=65 rst=0 en=0")
        self.assertEqual(oracle.counter4(output, 322, 1)["status"], "failed")
        self.assertEqual(oracle.counter4(trace().splitlines()[0], 1, 1)["status"], "unverified")

    def test_post_route_missing_or_failed_blocks_rtl_pass(self):
        for status in ("failed", "unverified", "passed"):
            with tempfile.TemporaryDirectory() as temp:
                rtl = {"status": "passed", "artifact_dir": temp}
                with patch.object(regression, "_check_stage", side_effect=[rtl, {"status": status}]):
                    result = regression.check(Path(temp), Path(temp), {"post_route": True})
                self.assertEqual(result["status"], status)
                self.assertEqual(result["rtl_status"], "passed")

    def test_no_synthesis_fallback_for_missing_final_netlist(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "resolved.json").write_text(json.dumps({"DESIGN_NAME": "counter4", "CELL_VERILOG_MODELS": []}))
            (root / "06-yosys-synthesis").mkdir()
            (root / "06-yosys-synthesis/counter4.nl.v").write_text("module counter4; endmodule")
            with self.assertRaises(ValueError):
                regression.post_route_sources(root)


if __name__ == "__main__":
    unittest.main()
