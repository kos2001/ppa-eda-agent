"""Tests for pipeline/custom_bridge.py — the open-source Virtuoso stack.

Every case here guards a failure that actually happened while bringing
this module up on 2026-09-12, against real ngspice-46 output, not
invented log text:

  - ngspice exited 0 with the one measurement the run existed to take
    printed as "failed!". Returncode is not a verdict.
  - the `.meas ... trig/targ` form prints trailing `targ=`/`trig=` fields,
    so an end-anchored parser returned the DC number and silently dropped
    every delay.
  - `ngspice -v` opens with a line of asterisks, which reported as the
    tool version reads like an answer.
  - gf180mcu's typical SPICE section is named `typical`, not `tt`; a deck
    bound with the wrong name still simulates, just not this silicon.
  - a backend that is not installed has to stay inside the four statuses
    virtuoso-bridge-lite defines, or a caller written against that bridge
    breaks on the fifth.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import custom_bridge  # noqa: E402
from custom_bridge import ExecutionStatus  # noqa: E402


# Verbatim tail of a real `ngspice -b` run of pipeline/analog/inv/inv.spice.
NGSPICE_OK = """No. of Data Rows : 832
vtrip               =  8.67162e-01
tphl                =  6.63198e-11 targ=  1.11632e-09 trig=  1.05000e-09
tplh                =  8.04744e-11 targ=  3.23047e-09 trig=  3.15000e-09
ivdd_avg            =  -4.92246e-06 from=  0.00000e+00 to=  8.00000e-09
ngspice-46 done
"""

# Verbatim, from the first attempt at the same deck: rc was 0.
NGSPICE_MEAS_FAILED = """Error: measure  vtrip  when(WHEN) : out of interval
 meas dc vtrip when v(out)=0.9 rise=1 failed!
Warning from checkvalid: vector vdd is not available or has zero length.
ngspice-46 done
"""


class MeasurementParsing(unittest.TestCase):
    def test_trig_targ_measurements_are_not_dropped(self):
        """Guards: only vtrip came back; tphl/tplh/ivdd_avg went missing."""
        meas = custom_bridge.parse_measurements(NGSPICE_OK)
        self.assertEqual(sorted(meas), ["ivdd_avg", "tphl", "tplh", "vtrip"])
        self.assertAlmostEqual(meas["tphl"], 6.63198e-11)
        self.assertAlmostEqual(meas["vtrip"], 0.867162)

    def test_trailing_context_fields_are_not_read_as_measurements(self):
        """`targ=`/`trig=`/`from=` are context on the same line, not results."""
        meas = custom_bridge.parse_measurements(NGSPICE_OK)
        for key in ("targ", "trig", "from", "to"):
            self.assertNotIn(key, meas)

    def test_prose_lines_yield_nothing(self):
        self.assertEqual(custom_bridge.parse_measurements("ngspice-46 done"), {})


class ProblemParsing(unittest.TestCase):
    def test_failed_measure_is_an_error_even_though_ngspice_exits_zero(self):
        errors, _ = custom_bridge.parse_problems(NGSPICE_MEAS_FAILED)
        self.assertTrue(any("failed!" in e for e in errors), errors)
        self.assertTrue(any(e.startswith("Error:") for e in errors), errors)

    def test_warning_is_not_counted_as_an_error(self):
        errors, warnings = custom_bridge.parse_problems(NGSPICE_MEAS_FAILED)
        self.assertTrue(any("checkvalid" in w for w in warnings), warnings)
        self.assertFalse(any("checkvalid" in e for e in errors), errors)

    def test_clean_log_has_neither(self):
        self.assertEqual(custom_bridge.parse_problems(NGSPICE_OK), ([], []))


class Classification(unittest.TestCase):
    def test_zero_exit_with_errors_is_partial_not_success(self):
        """The whole reason PARTIAL exists: a run that dropped a result."""
        errors, _ = custom_bridge.parse_problems(NGSPICE_MEAS_FAILED)
        self.assertEqual(custom_bridge.classify(0, errors),
                         ExecutionStatus.PARTIAL)

    def test_zero_exit_and_clean_log_is_success(self):
        self.assertEqual(custom_bridge.classify(0, []), ExecutionStatus.SUCCESS)

    def test_nonzero_exit_is_failure(self):
        self.assertEqual(custom_bridge.classify(1, []), ExecutionStatus.FAILURE)

    def test_statuses_match_virtuoso_bridge_lite_exactly(self):
        """A fifth status would break callers written against that bridge."""
        declared = {v for k, v in vars(ExecutionStatus).items()
                    if not k.startswith("_") and isinstance(v, str)}
        self.assertEqual(declared, {"success", "failure", "partial", "error"})


class CornerBinding(unittest.TestCase):
    def test_gf180_typical_section_is_not_called_tt(self):
        """Guards binding a gf180 deck to a section name that doesn't exist."""
        _, section = custom_bridge.spice_lib("gf180mcuD", "tt")
        self.assertEqual(section, "typical")

    def test_sky130_typical_section_is_tt(self):
        lib, section = custom_bridge.spice_lib("sky130A", "tt")
        self.assertEqual(section, "tt")
        self.assertEqual(lib.name, "sky130.lib.spice")

    def test_unknown_corner_raises_instead_of_simulating_wrong_silicon(self):
        with self.assertRaises(ValueError):
            custom_bridge.spice_lib("sky130A", "typ")

    def test_token_substitution_produces_a_real_lib_line(self):
        deck = custom_bridge.bind_corner("%PDK_LIB%\n.end\n", "sky130A", "ss")
        self.assertIn(".lib ", deck)
        self.assertTrue(deck.splitlines()[0].endswith(" ss"))
        self.assertNotIn(custom_bridge.PDK_LIB_TOKEN, deck)

    def test_deck_without_the_token_is_left_alone(self):
        """A deck naming its own models made a choice; don't shadow it."""
        deck = ".lib /my/own/models.spice tt\n.end\n"
        self.assertEqual(custom_bridge.bind_corner(deck, "sky130A", "ff"), deck)


class AbsentBackend(unittest.TestCase):
    def test_missing_tool_is_error_with_a_reason_not_a_new_status(self):
        result = custom_bridge.evaluate("definitely_not_a_backend", "x")
        self.assertEqual(result.status, ExecutionStatus.ERROR)
        self.assertEqual(result.metadata["reason"], "unknown_backend")

    def test_result_round_trips_to_json(self):
        """reference-db stores these; a result that can't serialise is lost."""
        result = custom_bridge.BridgeResult(status=ExecutionStatus.SUCCESS,
                                            output="x", metadata={"a": 1})
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "r.json"
            result.save_json(path)
            self.assertEqual(json.loads(path.read_text())["status"], "success")
        self.assertTrue(result.ok)


class DockerFallback(unittest.TestCase):
    """status() reported magic/klayout as reachable through the OpenLane
    image while evaluate() still refused them as not_installed. A bridge
    that advertises a backend and then won't run it is worse than one
    that never offered it."""

    def test_a_backend_outside_the_image_stays_not_installed(self):
        # xschem is in no image this pipeline pulls, so this is the one
        # case that must keep reporting absence rather than reaching for
        # a container that does not contain it.
        spec = custom_bridge.BACKENDS["xschem"]
        self.assertFalse(spec["in_openlane_image"])
        if custom_bridge.resolve("xschem") is None:
            result = custom_bridge.evaluate("xschem", "puts hi")
            self.assertEqual(result.status, ExecutionStatus.ERROR)
            self.assertEqual(result.metadata["reason"], "not_installed")

    def test_the_image_backends_are_the_ones_openlane_actually_ships(self):
        # Asked of the real image on 2026-09-12: magic, klayout and
        # netgen resolve inside it; xschem and ngspice do not.
        in_image = {n for n, s in custom_bridge.BACKENDS.items()
                    if s["in_openlane_image"]}
        self.assertEqual(in_image, {"magic", "klayout", "netgen"})


class BackendTable(unittest.TestCase):
    def test_every_backend_names_its_virtuoso_equivalent(self):
        """The mapping is the point of the module; an unmapped tool is a gap."""
        for name, spec in custom_bridge.BACKENDS.items():
            self.assertTrue(spec["virtuoso_equivalent"], name)
            self.assertIn(name, custom_bridge._SUFFIX)

    def test_netgen_is_invoked_with_source_not_a_bare_file(self):
        """`netgen -batch FILE` reads stdin and hangs — it hung this session."""
        argv = custom_bridge.BACKENDS["netgen"]["argv"]("/tmp/x.tcl")
        self.assertEqual(argv[:3], ["netgen", "-batch", "source"])

    def test_headless_flags_are_present_for_gui_tools(self):
        self.assertIn("-dnull", custom_bridge.BACKENDS["magic"]["argv"]("s"))
        self.assertIn("-b", custom_bridge.BACKENDS["klayout"]["argv"]("s"))
        self.assertIn("-n", custom_bridge.BACKENDS["xschem"]["argv"]("s"))


if __name__ == "__main__":
    unittest.main()
