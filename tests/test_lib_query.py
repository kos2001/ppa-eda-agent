"""Tests for pipeline/lib_query.py.

Uses a small hand-written liberty fixture rather than the real 12 MB
sky130 file so the numbers are known exactly and the suite stays fast
and offline. The real-PDK path is exercised separately and skipped when
the PDK is absent.
"""
import unittest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import lib_query  # noqa: E402
from lib_query import (  # noqa: E402
    LibertyError, cells_meeting, load_library, max_wire_um,
    pin_capacitance, transition_at, wire_cap_per_um,
)

# Two cells with deliberately different behaviour: "fast" beats a 40 ps
# limit at light load, "slow" never does. Values are in ns, loads in pF.
LIB = """
library (fixture) {
    time_unit : "1ns" ;
    capacitive_load_unit(1, pf) ;
    default_max_transition : 0.5 ;

    cell ("fast") {
        pin(A) { direction : input; capacitance : 0.002; }
        pin(X) {
            direction : output;
            timing() {
                rise_transition (t) {
                    index_1("0.010, 1.000");
                    index_2("0.000, 0.100");
                    values("0.020, 0.120", \\
                           "0.030, 0.130");
                }
            }
        }
    }
    cell ("slow") {
        pin(A) { direction : input; capacitance : 0.004; }
        pin(X) {
            direction : output;
            timing() {
                rise_transition (t) {
                    index_1("0.010, 1.000");
                    index_2("0.000, 0.100");
                    values("0.060, 0.260", \\
                           "0.080, 0.280");
                }
            }
        }
    }
}
"""


def write(text: str, suffix: str = ".lib") -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False)
    f.write(text)
    f.close()
    return Path(f.name)


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.path = write(LIB)
        self.lib = load_library(self.path)

    def test_units_are_read_not_assumed(self):
        self.assertEqual(self.lib.time_unit_ns, 1.0)
        self.assertEqual(self.lib.cap_unit_pf, 1.0)

    def test_finds_both_drive_cells(self):
        self.assertEqual(self.lib.drive_cells(), ["fast", "slow"])

    def test_default_max_transition(self):
        self.assertAlmostEqual(self.lib.default_max_transition, 0.5)

    def test_pin_capacitance(self):
        self.assertAlmostEqual(pin_capacitance(self.path, "fast/A"), 0.002)

    def test_missing_file_raises(self):
        with self.assertRaises(LibertyError):
            load_library(Path("/nonexistent/nope.lib"))

    def test_unknown_pin_raises_rather_than_returning_zero(self):
        with self.assertRaises(LibertyError):
            pin_capacitance(self.path, "fast/NOPE")

    def test_library_with_no_tables_raises(self):
        # A gate that cannot fail is not a gate: a liberty file we cannot
        # parse must not read as "no cells meet the limit".
        bad = write('library(x){ time_unit : "1ns" ;\n'
                    'capacitive_load_unit(1, pf) ;\n}')
        with self.assertRaises(LibertyError):
            load_library(bad)


class UnitConversionTests(unittest.TestCase):
    def test_ps_and_ff_are_converted(self):
        # Same cell as "fast", declared in ps/fF instead of ns/pF. If
        # units were assumed rather than read, the answers would be off
        # by 1000x — the exact mistake this module exists to prevent.
        lib = load_library(write(LIB.replace('time_unit : "1ns"',
                                             'time_unit : "1ps"')
                                    .replace("capacitive_load_unit(1, pf)",
                                             "capacitive_load_unit(1, ff)")))
        self.assertAlmostEqual(lib.time_unit_ns, 1e-3)
        self.assertAlmostEqual(lib.cap_unit_pf, 1e-3)
        # 0.020 ps at zero load, not 0.020 ns.
        self.assertAlmostEqual(transition_at(lib, "fast", 0.0), 2e-5)


class InterpolationTests(unittest.TestCase):
    def setUp(self):
        self.lib = load_library(write(LIB))

    def test_corner_value_is_exact(self):
        self.assertAlmostEqual(transition_at(self.lib, "fast", 0.0), 0.020)

    def test_midpoint_interpolates(self):
        # halfway along the load axis at the fastest slew: (0.020+0.120)/2
        self.assertAlmostEqual(transition_at(self.lib, "fast", 0.05), 0.070)

    def test_input_slew_matters(self):
        # The whole sram_wrapper reconciliation turned on this: the same
        # cell at the same load is slower with a degraded input.
        clean = transition_at(self.lib, "fast", 0.0, input_slew_ns=0.010)
        dirty = transition_at(self.lib, "fast", 0.0, input_slew_ns=1.000)
        self.assertLess(clean, dirty)

    def test_beyond_table_clamps_rather_than_extrapolating(self):
        edge = transition_at(self.lib, "fast", 0.100)
        beyond = transition_at(self.lib, "fast", 10.0)
        self.assertAlmostEqual(edge, beyond)

    def test_unknown_cell_raises(self):
        with self.assertRaises(LibertyError):
            transition_at(self.lib, "nonexistent", 0.0)


class LimitTests(unittest.TestCase):
    def setUp(self):
        self.lib = load_library(write(LIB))

    def test_cells_meeting_selects_only_the_fast_one(self):
        got = cells_meeting(self.lib, limit_ns=0.040, load_pf=0.0)
        self.assertEqual([c for c, _ in got], ["fast"])

    def test_cells_meeting_is_empty_when_truly_unmeetable(self):
        # The evidence that would have justified "physically floored".
        self.assertEqual(cells_meeting(self.lib, 0.001, 0.0), [])

    def test_a_loose_limit_admits_everything(self):
        got = cells_meeting(self.lib, limit_ns=1.0, load_pf=0.0)
        self.assertEqual(len(got), 2)

    def test_results_are_sorted_fastest_first(self):
        got = cells_meeting(self.lib, limit_ns=1.0, load_pf=0.0)
        self.assertEqual(got, sorted(got, key=lambda kv: kv[1]))


class MaxWireTests(unittest.TestCase):
    def setUp(self):
        self.lib = load_library(write(LIB))

    def test_zero_when_cell_misses_at_no_wire(self):
        self.assertEqual(
            max_wire_um(self.lib, "slow", limit_ns=0.040, pin_cap_pf=0.0,
                        cap_per_um_pf=1e-4),
            0.0,
        )

    def test_positive_length_for_a_cell_with_headroom(self):
        # fast: 0.020 at 0 load, 0.120 at 0.1 pF -> 0.040 ns at 0.02 pF.
        # At 1e-4 pF/um that is 200 um.
        um = max_wire_um(self.lib, "fast", limit_ns=0.040, pin_cap_pf=0.0,
                         cap_per_um_pf=1e-4)
        self.assertAlmostEqual(um, 200.0, places=1)

    def test_pin_capacitance_eats_into_the_budget(self):
        bare = max_wire_um(self.lib, "fast", 0.040, 0.0, 1e-4)
        loaded = max_wire_um(self.lib, "fast", 0.040, 0.010, 1e-4)
        self.assertLess(loaded, bare)


TLEF = """
LAYER met2
  TYPE ROUTING ;
  WIDTH 0.14 ;
  EDGECAPACITANCE 37.759E-6 ;
  CAPACITANCE CPERSQDIST 16.9423E-6 ;
END met2
LAYER via2
  TYPE CUT ;
END via2
"""


class WireCapTests(unittest.TestCase):
    def setUp(self):
        self.path = write(TLEF, ".tlef")

    def test_matches_hand_computation(self):
        got = wire_cap_per_um(self.path, "met2")
        self.assertAlmostEqual(got, 0.14 * 16.9423e-6 + 2 * 37.759e-6)

    def test_unknown_layer_raises(self):
        with self.assertRaises(LibertyError):
            wire_cap_per_um(self.path, "met9")

    def test_non_routing_layer_raises(self):
        # A cut layer has no per-length capacitance; returning one would
        # be a fabricated number.
        with self.assertRaises(LibertyError):
            wire_cap_per_um(self.path, "via2")


PDK = (Path(__file__).resolve().parent.parent / "pdk" / "volare" / "sky130"
       / "versions")


def _sky130_hd_lib() -> Path | None:
    if not PDK.is_dir():
        return None
    for v in PDK.iterdir():
        p = (v / "sky130A" / "libs.ref" / "sky130_fd_sc_hd" / "lib"
             / "sky130_fd_sc_hd__tt_025C_1v80.lib")
        if p.is_file():
            return p
    return None


class RealPdkTests(unittest.TestCase):
    """The claim that closed sram_wrapper, pinned as a test."""

    def setUp(self):
        self.path = _sky130_hd_lib()
        if self.path is None:
            self.skipTest("sky130 PDK not present")

    def test_the_040ns_spec_is_meetable(self):
        lib = load_library(self.path)
        # Driving the SRAM addr pin's own capacitance and nothing else.
        meeting = cells_meeting(lib, limit_ns=0.040, load_pf=0.00689)
        self.assertTrue(meeting, "no cell meets 40 ps — the 'physically "
                                 "floored' claim would be correct")
        self.assertLess(meeting[0][1], 0.025)

    def test_tristate_tables_do_not_report_a_zero_floor(self):
        # A naive scan picks up degenerate three-state groups and reports
        # 0.0 ps, which would make any limit look meetable.
        lib = load_library(self.path)
        for cell, t in cells_meeting(lib, limit_ns=1.0, load_pf=0.00689):
            self.assertGreater(t, 0.0, f"{cell} reported a zero transition")


if __name__ == "__main__":
    unittest.main()
