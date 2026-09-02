"""Tests for pipeline/cdc_check.py and score()'s per-supply-net IR drop.

The CDC gate exists because a real two-clock design ran the full flow,
produced 279 metrics, reported zero setup and zero hold violations, and
scored PASS — with an unsynchronized crossing in it. The timing numbers
were not wrong; OpenLane's base SDC constrained only the first clock, so
nothing ever asked about the clk_b domain.

So the tests care about two opposite mistakes: missing that (the gate
must fire on a real multi-clock run) and inventing it (a single-clock
design, a custom SDC, or a run whose logs say nothing must stay silent).
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import cdc_check  # noqa: E402
import orchestrator  # noqa: E402

# The real wording from a run, ellipsis included — if OpenLane changes
# it, this fails here rather than silently disarming the gate.
REAL_LOG = (
    "[WARNING] Multi-clock files are not currently supported by the base "
    "SDC file. Only the first clock will be constrained.\n"
    "[INFO] Using clock clk_a…\n"
)
SINGLE_LOG = "[INFO] Using clock clk…\n"


def make_run(log_text: str) -> Path:
    d = Path(tempfile.mkdtemp())
    step = d / "13-openroad-floorplan"
    step.mkdir()
    (step / "openroad-floorplan.log").write_text(log_text, encoding="utf-8")
    return d


def make_design(**cfg) -> Path:
    d = Path(tempfile.mkdtemp())
    (d / "config.json").write_text(json.dumps({"DESIGN_NAME": "t", **cfg}))
    return d


class DeclaredClockTests(unittest.TestCase):
    def test_space_separated_string(self):
        d = make_design(CLOCK_PORT="clk_a clk_b")
        self.assertEqual(cdc_check.declared_clock_ports(d), ["clk_a", "clk_b"])

    def test_json_list(self):
        d = make_design(CLOCK_PORT=["clk_a", "clk_b"])
        self.assertEqual(cdc_check.declared_clock_ports(d), ["clk_a", "clk_b"])

    def test_single(self):
        self.assertEqual(
            cdc_check.declared_clock_ports(make_design(CLOCK_PORT="clk")), ["clk"])

    def test_absent(self):
        self.assertEqual(cdc_check.declared_clock_ports(make_design()), [])


class LogReadingTests(unittest.TestCase):
    def test_reads_the_clock_openlane_actually_used(self):
        self.assertEqual(cdc_check.constrained_clocks(make_run(REAL_LOG)), ["clk_a"])

    def test_quotes_the_warning_verbatim(self):
        w = cdc_check.multi_clock_warnings(make_run(REAL_LOG))
        self.assertEqual(len(w), 1)
        self.assertIn("Only the first clock will be constrained", w[0])

    def test_deduplicates_across_steps(self):
        # Every step that sources the SDC repeats both lines.
        d = make_run(REAL_LOG)
        second = d / "20-openroad-generatepdn"
        second.mkdir()
        (second / "openroad-generatepdn.log").write_text(REAL_LOG, encoding="utf-8")
        self.assertEqual(cdc_check.constrained_clocks(d), ["clk_a"])
        self.assertEqual(len(cdc_check.multi_clock_warnings(d)), 1)

    def test_no_warning_on_a_single_clock_run(self):
        self.assertEqual(cdc_check.multi_clock_warnings(make_run(SINGLE_LOG)), [])


class GateFiresTests(unittest.TestCase):
    def test_flags_the_unconstrained_domain(self):
        r = cdc_check.check(make_design(CLOCK_PORT="clk_a clk_b"), make_run(REAL_LOG))
        self.assertEqual(r["declared_clocks"], ["clk_a", "clk_b"])
        self.assertEqual(r["constrained_clocks"], ["clk_a"])
        self.assertEqual(r["unconstrained_clocks"], ["clk_b"])

    def test_produces_one_unverified_entry_naming_the_domain(self):
        r = cdc_check.check(make_design(CLOCK_PORT="clk_a clk_b"), make_run(REAL_LOG))
        entries = cdc_check.unverified_domains(r)
        self.assertEqual(len(entries), 1)
        self.assertIn("clk_b", entries[0])

    def test_never_claims_cdc_is_clean(self):
        # The result must carry its own limits: nothing here looks for
        # synchronizers, and a reader must not take a quiet result for a
        # CDC signoff.
        r = cdc_check.check(make_design(CLOCK_PORT="clk"), make_run(SINGLE_LOG))
        self.assertIn("no structural CDC analysis", r["note"])


class GateStaysQuietTests(unittest.TestCase):
    """A gate that fires on healthy runs gets switched off, not obeyed."""

    def test_single_clock_design_is_not_flagged(self):
        r = cdc_check.check(make_design(CLOCK_PORT="clk"), make_run(SINGLE_LOG))
        self.assertEqual(r["unconstrained_clocks"], [])
        self.assertEqual(cdc_check.unverified_domains(r), [])

    def test_custom_sdc_is_trusted(self):
        # A design bringing its own constraints may handle multiple
        # clocks properly; flagging it would be a false alarm.
        d = make_design(CLOCK_PORT="clk_a clk_b", PNR_SDC_FILE="dir::my.sdc")
        r = cdc_check.check(d, make_run(REAL_LOG))
        self.assertTrue(r["custom_sdc"])
        self.assertEqual(r["unconstrained_clocks"], [])

    def test_silence_in_the_logs_produces_no_finding(self):
        # Inventing a finding from "we saw nothing" is the same mistake
        # this module exists to catch.
        r = cdc_check.check(make_design(CLOCK_PORT="clk_a clk_b"), make_run("nothing\n"))
        self.assertEqual(r["constrained_clocks"], [])
        self.assertEqual(r["unconstrained_clocks"], [])

    def test_design_with_no_clock_port_is_not_flagged(self):
        r = cdc_check.check(make_design(), make_run(SINGLE_LOG))
        self.assertEqual(r["unconstrained_clocks"], [])


class SupplyRailTests(unittest.TestCase):
    """Per-supply-net IR drop — the actual power-domain view, which
    score() was collapsing into a single global number."""

    def _m(self):
        # Values from a real counter4_tinydie signoff.
        return {
            "design_powergrid__drop__worst__net:VPWR": 8.79888e-05,
            "design_powergrid__voltage__worst__net:VPWR": 1.79991,
            "design_powergrid__drop__worst__net:VGND": 6.49442e-05,
            "design_powergrid__voltage__worst__net:VGND": 6.49442e-05,
        }

    def test_one_entry_per_supply_net(self):
        rails = orchestrator.supply_rails(self._m())
        self.assertEqual([r["net"] for r in rails], ["VGND", "VPWR"])

    def test_nominal_is_derived_not_assumed(self):
        vpwr = next(r for r in orchestrator.supply_rails(self._m()) if r["net"] == "VPWR")
        self.assertAlmostEqual(vpwr["nominal_v"], 1.8, places=4)
        self.assertAlmostEqual(vpwr["drop_pct"], 0.00489, places=4)

    def test_ground_gets_no_percentage(self):
        # Nominal 0 V makes a percentage meaningless; the absolute bounce
        # is still reported rather than dropped.
        gnd = next(r for r in orchestrator.supply_rails(self._m()) if r["net"] == "VGND")
        self.assertIsNone(gnd["drop_pct"])
        self.assertAlmostEqual(gnd["drop_worst_v"], 6.49442e-05)

    def test_per_corner_keys_are_not_double_counted(self):
        m = self._m()
        m["design_powergrid__drop__worst__net:VPWR__corner:nom_tt_025C_1v80"] = 8.8e-05
        self.assertEqual(len(orchestrator.supply_rails(m)), 2)

    def test_no_powergrid_metrics_yields_no_rails(self):
        self.assertEqual(orchestrator.supply_rails({}), [])


class IrDropGateTests(unittest.TestCase):
    def _clean(self, **over):
        m = {k: 0 for k, _ in orchestrator.SIGNOFF_METRICS}
        m["timing__setup__wns__corner:tt"] = 0.0
        m["ir__voltage__worst"] = 1.8
        m.update(over)
        return m

    def _drooping(self, pct):
        # Build a rail with exactly `pct` droop off a 1.8 V nominal.
        drop = 1.8 * pct / 100.0
        return self._clean(**{
            "design_powergrid__drop__worst__net:VPWR": drop,
            "design_powergrid__voltage__worst__net:VPWR": 1.8 - drop,
        })

    def test_droop_over_target_fails(self):
        v = orchestrator.score(self._drooping(8.0), {"max_ir_drop_pct": 5.0})
        self.assertFalse(v["passed"])
        self.assertTrue(any("IR drop" in s for s in v["violations"]))

    def test_droop_under_target_passes(self):
        v = orchestrator.score(self._drooping(1.0), {"max_ir_drop_pct": 5.0})
        self.assertTrue(v["passed"], v["violations"])

    def test_not_gated_without_a_target(self):
        # What counts as too much droop is a design decision; gating
        # against a number this pipeline invented would be overreach.
        v = orchestrator.score(self._drooping(20.0), {})
        self.assertTrue(v["passed"], v["violations"])

    def test_rails_are_reported_even_when_ungated(self):
        v = orchestrator.score(self._drooping(20.0), {})
        self.assertEqual(len(v["power_domain"]["supplies"]), 1)


if __name__ == "__main__":
    unittest.main()
