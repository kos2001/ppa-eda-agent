"""Regression tests for tech_compare.py's delta guard.

Same conventions as test_orchestrator_pure.py. These pin the guard that
exists because of a real, silent bug: selecting a standard-cell library
with `--override-config STD_CELL_LIBRARY=<x>` is accepted by OpenLane and
lands correctly in the run's resolved.json, but does not change what gets
built — the netlist still contained only the default library's cells. The
comparison then reported a perfect 0.00% delta on area, utilization and
power between two supposedly different technologies, which reads as a
finding rather than a bug. (The real fix is the `--scl` CLI flag, now
used by run_stage(); this guard is the belt to that braces, because a
silently meaningless comparison is worse than a failed one.)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import tech_compare  # noqa: E402


def _ok(variant, area=100.0, power=1e-4, cells=None):
    return {
        "variant": variant,
        "tag": f"tech-{variant}",
        "cells_used": cells if cells is not None else {variant: 66},
        "verdict": {"passed": True, "area_um2": area, "utilization": 0.5,
                     "worst_setup_wns": 0.0, "power": {"total_w": power}},
    }


class TestDeltaGuard(unittest.TestCase):
    def test_real_delta_is_computed(self):
        base = _ok("sky130_fd_sc_hd", area=100.0, power=1e-4)
        other = _ok("sky130_fd_sc_hs", area=125.0, power=1.5e-4)
        delta = tech_compare.delta_vs_baseline(base, other)
        self.assertEqual(delta["area_um2"]["pct_delta"], 25.0)
        self.assertAlmostEqual(delta["power_total_w"]["abs_delta"], 5e-5)

    def test_no_delta_when_a_run_failed(self):
        """A technology that fails to build has no numbers to compare;
        inventing a delta against a missing side would be fabrication."""
        base = _ok("sky130_fd_sc_hd")
        failed = {"variant": "sky130_fd_sc_hs", "tag": "t", "error": "21 DRC errors"}
        self.assertIsNone(tech_compare.delta_vs_baseline(base, failed))

    def test_no_delta_when_the_technology_was_not_actually_applied(self):
        """NEGATIVE CONTROL for the real bug. A run that requested hs but
        built hd must not produce a delta — that 0% would be reported as
        'these technologies are identical', which is a false finding."""
        base = _ok("sky130_fd_sc_hd")
        fake = _ok("sky130_fd_sc_hs", cells={"sky130_fd_sc_hd": 66})
        fake["technology_not_applied"] = "requested hs but netlist is hd"
        self.assertIsNone(tech_compare.delta_vs_baseline(base, fake))

    def test_missing_metric_is_skipped_not_zeroed(self):
        """An absent measurement must drop out of the comparison rather
        than being treated as zero, which would manufacture a -100%."""
        base = _ok("sky130_fd_sc_hd", area=100.0)
        other = _ok("sky130_fd_sc_hs", area=110.0)
        other["verdict"]["power"] = {}
        delta = tech_compare.delta_vs_baseline(base, other)
        self.assertIn("area_um2", delta)
        self.assertNotIn("power_total_w", delta)


class TestDesignInvariants(unittest.TestCase):
    """The comparison is only meaningful if the design really was held
    fixed, so the report records what that was."""

    def test_invariants_come_from_the_real_design_config(self):
        from pathlib import Path
        design = Path(tech_compare.REPO_ROOT) / "pipeline" / "designs" / "counter4"
        inv = tech_compare.design_invariants(design)
        self.assertEqual(inv["DESIGN_NAME"], "counter4")
        self.assertIn("CLOCK_PERIOD", inv)
        # The technology variable itself must never appear as an
        # "invariant" — it is the one thing the comparison varies.
        self.assertNotIn(tech_compare.TECH_VAR, inv)


if __name__ == "__main__":
    unittest.main()
