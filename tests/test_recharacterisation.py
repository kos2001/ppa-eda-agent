"""Tests for where a macro's slew ceiling comes from, and what moves it.

sram_wrapper has never closed. Every run dies the same way:

    [RSZ-0090] Max transition time from SDC is 0.040ns.
               Best achievable transition time is 0.043ns with a load of 0.01pF

The project already knew the 0.040ns is the macro's own liberty pin
attribute and not an SDC value, and that no config override reaches it —
`model_validity.py` exists because of it. What was missing is where the
number comes from, and that turns out to be arithmetic anyone can check:

    OpenRAM default slew_scales   [0.25, 1, 8]
    sky130 tech.spice["rise_time"]  0.005 ns
    product                       [0.00125, 0.005, 0.04]
    the macro's own index_1        "0.00125, 0.005, 0.04"

So 0.04 ns is not a design limit the macro imposes. It is the top of the
input-slew axis it was characterised over — the table simply stops
there, and `max_transition` records where it stopped. Nothing was ever
measured above it.

That reframes the whole failure. Relaxing the constraint (the
`.relaxed.lib` in this design, 0.04 -> 0.05) asks the timer to trust
numbers nobody produced, and it is not even enough: the best this design
has reached is 0.209 ns, 5.2x the ceiling. The fix that produces real
numbers is to characterise the macro over the slew range it will
actually see, which OpenRAM exposes as `slew_scales` in its config.

These tests pin the arithmetic against the real liberty in this repo's
PDK, so the claim stays checkable if the macro or the PDK is ever
replaced.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import model_validity  # noqa: E402
import recharacterise  # noqa: E402

MACRO_LIB = (ROOT / "pdk" / "sky130A" / "libs.ref" / "sky130_sram_macros"
             / "lib" / "sky130_sram_1kbyte_1rw1r_32x256_8_TT_1p8V_25C.lib")


def _requires_pdk(test):
    if not MACRO_LIB.is_file():
        raise unittest.SkipTest("sky130 SRAM macro liberty not installed")


class CeilingOriginTests(unittest.TestCase):
    """The ceiling is the characterisation grid, not a design rule."""

    def setUp(self):
        _requires_pdk(self)

    def test_the_ceiling_is_the_product_of_openram_defaults(self):
        # [0.25, 1, 8] * 0.005ns. If this ever stops matching, either the
        # macro was regenerated with different settings or it did not
        # come from OpenRAM's defaults — both worth knowing.
        ceiling = model_validity.characterisation_ceiling(MACRO_LIB)
        self.assertAlmostEqual(
            ceiling, recharacterise.SKY130_RISE_TIME_NS
            * max(recharacterise.OPENRAM_DEFAULT_SLEW_SCALES), 6)

    def test_max_transition_equals_the_top_of_the_slew_axis(self):
        # The claim that makes this a characterisation limit rather than
        # a spec: the pin attribute and the table edge are the same
        # number, in the same file.
        text = MACRO_LIB.read_text(errors="ignore")
        declared = {float(v) for v in re.findall(
            r"max_transition\s*:\s*([\d.]+)\s*;", text)}
        ceiling = model_validity.characterisation_ceiling(MACRO_LIB)
        self.assertIn(ceiling, declared)

    def test_the_pdk_copy_matches_upstream_defaults(self):
        # Rules out "our PDK copy is stale": the number is what OpenRAM
        # produces from its own defaults, so upstream has it too.
        self.assertEqual(
            recharacterise.OPENRAM_DEFAULT_SLEW_SCALES, [0.25, 1, 8])
        self.assertEqual(recharacterise.SKY130_RISE_TIME_NS, 0.005)


class ScaleRecommendationTests(unittest.TestCase):
    """What slew_scales would cover a slew this design really produces."""

    def test_it_extends_rather_than_replaces_the_grid(self):
        # The existing points are real measurements. A recommendation
        # that dropped them would discard characterisation that exists
        # to add characterisation that does not.
        scales = recharacterise.slew_scales_for(0.209)
        for point in recharacterise.OPENRAM_DEFAULT_SLEW_SCALES:
            self.assertIn(point, scales)

    def test_the_top_of_the_grid_covers_the_observed_slew(self):
        top = max(recharacterise.slew_scales_for(0.209))
        self.assertGreaterEqual(top * recharacterise.SKY130_RISE_TIME_NS, 0.209)

    def test_a_slew_already_covered_needs_no_extension(self):
        # 0.03ns sits inside the existing grid. Recommending a wider one
        # would be recommending work with nothing to gain.
        self.assertEqual(recharacterise.slew_scales_for(0.03),
                         recharacterise.OPENRAM_DEFAULT_SLEW_SCALES)

    def test_scales_stay_sorted_and_unique(self):
        scales = recharacterise.slew_scales_for(0.209)
        self.assertEqual(scales, sorted(set(scales)))


class ReportTests(unittest.TestCase):
    """The report a person or agent reads."""

    def setUp(self):
        _requires_pdk(self)

    def test_it_reports_the_ceiling_and_where_it_came_from(self):
        report = recharacterise.analyse(MACRO_LIB, worst_slew_ns=0.209)
        self.assertAlmostEqual(report["ceiling_ns"], 0.04, 6)
        self.assertEqual(report["ceiling_source"], "openram_default_slew_scales")

    def test_it_says_how_far_past_the_ceiling_the_design_runs(self):
        report = recharacterise.analyse(MACRO_LIB, worst_slew_ns=0.209)
        self.assertAlmostEqual(report["over_ceiling_x"], 0.209 / 0.04, 3)

    def test_it_names_relaxing_the_lib_as_extrapolation(self):
        # The `.relaxed.lib` already in sram_wrapper raises the attribute
        # to 0.05 without adding a single measured point. The report has
        # to say that, or the cheap edit looks like the fix.
        report = recharacterise.analyse(MACRO_LIB, worst_slew_ns=0.209)
        self.assertFalse(report["relaxing_is_sufficient"])
        self.assertIn("extrapolat", " ".join(report["notes"]).lower())

    def test_a_design_inside_the_grid_is_reported_as_fine(self):
        report = recharacterise.analyse(MACRO_LIB, worst_slew_ns=0.02)
        self.assertFalse(report["needs_recharacterisation"])


if __name__ == "__main__":
    unittest.main()
