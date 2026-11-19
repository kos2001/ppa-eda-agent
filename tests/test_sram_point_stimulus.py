import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from sram_point_stimulus import assess_measurements, validate_decks

EVIDENCE = (Path(__file__).resolve().parents[1] / "pipeline/designs/sram_wrapper/experiments"
            / "evidence-20261004/characterization-algorithm/completed-ss-port0-20261004")


class SRAMPointTests(unittest.TestCase):
    def setUp(self):
        self.values = json.loads((EVIDENCE / "measurement-summary.json").read_text())["measurement_values_SI"]
        self.names = list(self.values)

    def assess(self):
        return assess_measurements(self.values, self.names, 0, 1.6, 10)

    def test_retained_point_has_all_independent_checks(self):
        result = self.assess()
        self.assertTrue(result["point_checks_passed"], result)
        self.assertFalse(result["model_qualified"])
        names = validate_decks((EVIDENCE / "delay_stim.sp").read_text(),
                               (EVIDENCE / "delay_meas.sp").read_text(), 0)
        self.assertEqual(set(names), set(self.names))

    def test_rejects_wrong_early_storage_value_even_when_last_is_correct(self):
        self.values["v_q_a11111111_b31_read_zero0"] = 1.6
        self.assertFalse(self.assess()["point_checks_passed"])

    def test_missing_nonfinite_sense_and_output_fail(self):
        for name, value in (("write1_power0", float("nan")), ("delay_sen0", 5.1e-9),
                            ("v_delay_lh0", .8), ("delay_hl0", 5.1e-9)):
            with self.subTest(name=name):
                old = self.values[name]
                self.values[name] = value
                self.assertFalse(self.assess()["point_checks_passed"])
                self.values[name] = old
        del self.values["read0_power0"]
        self.assertFalse(self.assess()["point_checks_passed"])

    def test_port0_deck_cannot_be_claimed_as_port1(self):
        with self.assertRaises(ValueError):
            validate_decks((EVIDENCE / "delay_stim.sp").read_text(),
                           (EVIDENCE / "delay_meas.sp").read_text(), 1)

    def test_wrong_probe_and_missing_measurement_rejected(self):
        stim = (EVIDENCE / "delay_stim.sp").read_text()
        meas = (EVIDENCE / "delay_meas.sp").read_text()
        with self.assertRaises(ValueError):
            validate_decks(stim, meas.replace("v(dout0_31)", "v(dout1_31)"), 0)
        with self.assertRaises(ValueError):
            validate_decks(stim, meas.replace(".meas tran delay_lh0", "* removed delay_lh0"), 0)
        inactive = "\n".join(line.replace("0.0v", "1.6v") if line.startswith("VCSB0 ") else line
                             for line in stim.splitlines())
        with self.assertRaises(ValueError):
            validate_decks(inactive, meas, 0)


if __name__ == "__main__":
    unittest.main()
