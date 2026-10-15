import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import sram_active_learning as learning


class LinearFitTests(unittest.TestCase):
    def test_refuses_too_little_data(self):
        self.assertFalse(learning.linear_fit([(1, 2), (2, 3)])["supported"])

    def test_recovers_a_linear_relation(self):
        fit = learning.linear_fit([(0, 1), (1, 3), (2, 5)])
        self.assertAlmostEqual(fit["intercept_ns"], 1)
        self.assertAlmostEqual(fit["slope_ns_per_um"], 2)
        self.assertAlmostEqual(fit["r2"], 1)

    def test_pareto_does_not_hide_a_regression(self):
        def row(name, standoff, macro, antenna):
            return {"name": name, "placement_standoff_um": standoff,
                    "macro_pin_detail": {"p": {"slew_ns": .04 + standoff / 1000}},
                    "macro_pins_over_ceiling": macro, "max_slew": 1,
                    "max_fanout": 1, "max_cap": 0, "antenna": antenna}
        result = learning.analyse([row("near", 1, 0, 2), row("far", 2, 1, 0),
                                   row("bad", 3, 2, 2)])
        self.assertEqual({r["name"] for r in result["pareto"]}, {"near", "far"})


class MeasuredClosureTests(unittest.TestCase):
    """Pin the full-flow result that the learning loop converged on."""

    def test_round15_closed_every_recorded_gate(self):
        path = ROOT / "reference-db" / "sram_io_experiment_round15.json"
        row = json.loads(path.read_text(encoding="utf-8"))[0]
        self.assertTrue(row["passed"])
        self.assertEqual(row["macro_pins_measured"], 57)
        self.assertEqual(row["macro_pins_over_ceiling"], 0)
        self.assertLessEqual(row["worst_macro_slew_ns"], row["ceiling_ns"])
        for field in ("max_slew", "max_fanout", "max_cap", "antenna",
                      "drc_magic", "drc_klayout", "lvs"):
            self.assertEqual(row[field], 0, field)
        self.assertEqual(row["violations"], [])
        self.assertEqual(row["unverified"], [])


if __name__ == "__main__":
    unittest.main()
