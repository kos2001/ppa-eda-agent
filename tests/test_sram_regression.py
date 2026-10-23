import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import functional_regression as regression
import functional_oracles as oracles


class SRAMRegressionTests(unittest.TestCase):
    def test_every_macro_requires_explicit_model(self):
        with self.assertRaises(ValueError):
            regression.macro_sources({"MACROS": {"sram": {}}}, {})
        with self.assertRaises(ValueError):
            regression.macro_sources({}, {"macro_models": {"sram": "/pdk/sram.v"}})

    def test_blackbox_is_not_behavioral_evidence(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(regression, "PDK_ROOT", Path(tmp)):
            path = Path(tmp) / "sram.v"
            config = {"MACROS": {"sram": {}}}
            spec = {"macro_models": {"sram": "/pdk/sram.v"}}
            path.write_text("(* blackbox *) module sram(); endmodule")
            with self.assertRaises(ValueError):
                regression.macro_sources(config, spec)
            path.write_text("module other(); endmodule")
            with self.assertRaises(ValueError):
                regression.macro_sources(config, spec)
            path.write_text("module sram(); reg [31:0] mem[0:255]; endmodule")
            self.assertEqual(regression.macro_sources(config, spec), [path])

    def test_missing_or_incomplete_trace_is_not_pass(self):
        for output in ("PPA_REGRESSION_PASS checks=800", "PPA_SRAM cycle=0 rst=1 dout0=xxxxxxxx dout1=xxxxxxxx"):
            self.assertEqual(oracles.sram_wrapper(output, 800, 1)["status"], "unverified")

    def test_undefined_outputs_cannot_turn_all_cycles_into_a_pass(self):
        # The first defined read is at cycle 4. A blackbox producing only X
        # must fail there instead of having every sample silently skipped.
        lines = [f"PPA_SRAM cycle={i} rst={int(i < 2)} dout0=xxxxxxxx dout1=xxxxxxxx" for i in range(800)]
        result = oracles.sram_wrapper("\n".join(lines), 800, 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["cycle"], 4)
        self.assertEqual(result["port"], 1)


if __name__ == "__main__":
    unittest.main()
