"""Tests for pipeline/macro_place.py's pure logic.

The OpenROAD invocation itself is not unit-testable without Docker, so
what is tested here is the part that turns two placements into a claim —
including the case that actually happened: a placer that moves a macro
167 um and makes the binding constraint worse. A helper that only knew
how to report improvement would have hidden that.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from macro_place import MacroPlaceError, autoplace, moved  # noqa: E402


def macro(name, x, y, orient="R0"):
    return {"instance": name, "master": "sram", "x_um": x, "y_um": y,
            "orient": orient, "placed": True, "status": "PLACED"}


class MovedTests(unittest.TestCase):
    def test_reports_the_real_move(self):
        # The measured sram_wrapper result: (110, 150) -> (10.35, 15.79).
        result = {"before": [macro("u_sram", 110.0, 150.0)],
                  "after": [macro("u_sram", 10.35, 15.79, "MY")]}
        got = moved(result)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["from_um"], [110.0, 150.0])
        self.assertEqual(got[0]["to_um"], [10.35, 15.79])
        self.assertAlmostEqual(got[0]["moved_um"], 167.16, places=1)

    def test_an_unmoved_macro_is_still_reported(self):
        # "The placer would pick what you already had" is a result, not
        # an absence of one.
        result = {"before": [macro("u_sram", 110.0, 150.0)],
                  "after": [macro("u_sram", 110.0, 150.0)]}
        got = moved(result)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["moved_um"], 0.0)

    def test_handles_several_macros(self):
        result = {
            "before": [macro("a", 0, 0), macro("b", 100, 100)],
            "after": [macro("a", 0, 0), macro("b", 100, 130)],
        }
        by = {m["instance"]: m for m in moved(result)}
        self.assertEqual(by["a"]["moved_um"], 0.0)
        self.assertAlmostEqual(by["b"]["moved_um"], 30.0)

    def test_a_failed_placement_yields_nothing_rather_than_zeros(self):
        # after=None means the placer errored; reporting 0 um moved would
        # read as "it chose to leave everything alone".
        result = {"before": [macro("u_sram", 110.0, 150.0)], "after": None,
                  "error": "RuntimeError: ..."}
        self.assertEqual(moved(result), [])

    def test_a_macro_only_in_the_after_list_is_skipped(self):
        result = {"before": [], "after": [macro("ghost", 1, 1)]}
        self.assertEqual(moved(result), [])

    def test_design_with_no_macros(self):
        self.assertEqual(moved({"before": [], "after": []}), [])


class PlacerSelectionTests(unittest.TestCase):
    def test_rejects_an_unknown_placer_before_starting_docker(self):
        # Failing fast beats a container start and an opaque Tcl error.
        with self.assertRaises(MacroPlaceError):
            autoplace(Path("/tmp"), placer="not_a_placer")

    def test_both_real_placers_are_accepted(self):
        # Not run here (needs Docker) — this asserts only that the name
        # check lets them through, so a typo in either is caught.
        for name in ("rtl_macro_placer", "macro_placement"):
            with self.assertRaises(Exception) as cm:
                autoplace(Path("/nonexistent/run"), placer=name)
            # It got past the placer-name check and failed on the path.
            self.assertNotIn("unknown placer", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
