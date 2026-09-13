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
import re
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

    def test_the_plan_prefers_a_local_binary(self):
        self.assertEqual(custom_bridge.plan("/usr/bin/magic", "img", True), "local")

    def test_the_plan_reaches_for_the_container_when_the_binary_is_absent(self):
        self.assertEqual(custom_bridge.plan(None, "img", True), "docker")

    def test_no_binary_and_no_image_is_nowhere(self):
        self.assertIsNone(custom_bridge.plan(None, None, True))

    def test_an_image_is_no_use_without_docker(self):
        self.assertIsNone(custom_bridge.plan(None, "img", False))

    def test_the_image_backends_are_the_ones_openlane_actually_ships(self):
        # Asked of the real image on 2026-09-12: magic, klayout and
        # netgen resolve inside it; xschem and ngspice do not.
        in_image = {n for n, s in custom_bridge.BACKENDS.items()
                    if s["in_openlane_image"]}
        self.assertEqual(in_image, {"magic", "klayout", "netgen"})


class SchematicEntryPoint(unittest.TestCase):
    """The custom flow starts at a schematic, not at a deck.

    This module first shipped with run_spice() and a hand-written
    netlist, which is one step below where the flow actually starts.
    What that cost is measurable rather than stylistic: at tt the
    hand-written deck and the netlisted schematic agree on vtrip to six
    digits and differ by ~3% on delay, because the PDK's own symbols
    attach diffusion parasitics (ad/pd/as/ps, nrd/nrs) to every device
    and a hand-written deck has no reason to carry them.
    """

    DESIGN = Path(__file__).resolve().parent.parent / "pipeline" / "analog" / "inv"

    def test_the_cell_and_its_testbench_are_checked_in_as_schematics(self):
        self.assertTrue((self.DESIGN / "inv.sch").is_file())
        self.assertTrue((self.DESIGN / "inv_tb.sch").is_file())
        self.assertTrue((self.DESIGN / "inv.sym").is_file())

    def test_the_testbench_instantiates_the_cell_rather_than_inlining_it(self):
        # Hierarchy is the point of schematic capture: the testbench
        # holds the stimulus and the cell holds the circuit, so sweeping
        # one never edits the other.
        tb = (self.DESIGN / "inv_tb.sch").read_text(encoding="utf-8")
        self.assertIn("C {inv.sym}", tb)
        self.assertNotIn("sky130_fd_pr/nfet", tb)

    def test_the_schematic_keeps_the_corner_token(self):
        """Guards the first netlisted deck's real failure.

        xschem's own idiom writes `.lib $::SKYWATER_MODELS/...` at
        netlist time, which bakes in both one corner and the container
        path /pdk/... — ngspice on the host then died with "Could not
        find library file /pdk/sky130A/...".
        """
        tb = (self.DESIGN / "inv_tb.sch").read_text(encoding="utf-8")
        self.assertIn(custom_bridge.PDK_LIB_TOKEN, tb)
        self.assertNotIn("SKYWATER_MODELS", tb)

    def test_the_handwritten_deck_is_not_at_the_generated_path(self):
        # xschem writes inv.spice from inv.sch; a hand-written file at
        # that path is a generated file waiting to overwrite it.
        self.assertFalse((self.DESIGN / "inv.spice").exists())
        self.assertTrue((self.DESIGN / "inv_handwritten.spice").is_file())

    def test_a_missing_schematic_is_an_error_not_an_empty_netlist(self):
        r = custom_bridge.netlist_schematic(self.DESIGN / "nope.sch")
        self.assertEqual(r.status, ExecutionStatus.ERROR)
        self.assertEqual(r.metadata["reason"], "missing_schematic")

    def test_a_pdk_without_xschem_support_is_named_as_such(self):
        r = custom_bridge.netlist_schematic(self.DESIGN / "inv_tb.sch",
                                            pdk="not_a_pdk")
        self.assertEqual(r.status, ExecutionStatus.ERROR)
        self.assertEqual(r.metadata["reason"], "missing_pdk_xschemrc")


class SchematicRendering(unittest.TestCase):
    """A flow that starts at a schematic and never shows one asks to be
    taken on trust. The drawing is xschem's own SVG export, not a second
    renderer written here — a redrawing of the .sch would be a second
    thing that can disagree with the netlist."""

    DESIGN = Path(__file__).resolve().parent.parent / "pipeline" / "analog" / "inv"

    SVG = ('<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="700" '
           'version="1.1">\n'
           '<rect x="0" y="0" width="1000" height="700" class="l0"/>\n'
           '<path class="l4" d="M 400 300 L 500 300"/>\n'
           '<circle cx="450" cy="300" r="4"/>\n'
           '<text font-size="20" transform="translate(520, 305)">Z</text>\n'
           '</svg>')

    def test_the_viewbox_points_at_the_drawing_not_at_the_canvas(self):
        """Measured on the real renders: a small drawing fills 23-31% of
        xschem's fixed canvas, so at fit zoom most of the frame is margin
        and the schematic is tiny for no reason."""
        out = custom_bridge.fit_viewbox(self.SVG)
        x0, y0, w, h = [float(v) for v in
                        re.search(r'viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"',
                                  out).groups()]
        self.assertGreater(x0, 300)      # not the canvas origin
        self.assertLess(w, 1000)         # narrower than the canvas
        self.assertLess(h, 700)

    def test_content_past_the_canvas_edge_is_included_rather_than_clipped(self):
        """The worse half of the same problem: counter4 fills 120% of the
        canvas, gcd 142%, the riscv cone 170%. Everything past the edge
        was simply not in the file — the aes cone lost its leftmost
        column of net labels that way."""
        svg = self.SVG.replace('d="M 400 300 L 500 300"', 'd="M 400 300 L 1400 300"')
        out = custom_bridge.fit_viewbox(svg)
        width = float(re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+)', out).group(1))
        self.assertGreater(width, 1000)

    def test_the_background_follows_the_new_box(self):
        # Otherwise the drawing is served on a transparent ground and the
        # page shows through around it.
        out = custom_bridge.fit_viewbox(self.SVG)
        rect = re.search(r'<rect[^>]*width="([\d.]+)"', out).group(1)
        box = re.search(r'viewBox="[-\d.]+ [-\d.]+ ([\d.]+)', out).group(1)
        self.assertEqual(rect, box)

    def test_text_extent_is_allowed_for_since_svg_does_not_carry_it(self):
        """A <text> carries its anchor, not its width. Cropping to the
        anchor would cut every label in half."""
        out = custom_bridge.fit_viewbox(self.SVG)
        x0, w = [float(v) for v in
                 re.search(r'viewBox="([-\d.]+) [-\d.]+ ([\d.]+)', out).groups()]
        self.assertGreater(x0 + w, 520 + 20 * 0.6)

    def test_an_existing_viewbox_is_left_alone(self):
        svg = '<svg width="10" height="20" viewBox="0 0 10 20">'
        self.assertEqual(custom_bridge.fit_viewbox(svg), svg)

    def test_markup_it_does_not_recognise_is_returned_unchanged(self):
        # Returning something half-rewritten would be worse than
        # returning the file as it came.
        self.assertEqual(custom_bridge.fit_viewbox("<svg>"), "<svg>")

    def test_an_empty_drawing_keeps_the_canvas_rather_than_inventing_a_box(self):
        empty = ('<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="700">'
                 '<rect x="0" y="0" width="1000" height="700"/></svg>')
        out = custom_bridge.fit_viewbox(empty)
        self.assertIn('viewBox="0.00 0.00 1000.00 700.00"', out)

    def test_a_missing_schematic_is_an_error(self):
        r = custom_bridge.render_schematic(self.DESIGN / "nope.sch")
        self.assertEqual(r.status, ExecutionStatus.ERROR)
        self.assertEqual(r.metadata["reason"], "missing_schematic")

    def test_the_checked_in_drawing_scales(self):
        """The committed SVG is what the dashboard serves; if it lost its
        viewBox the panel would overflow instead of fitting."""
        svg = (self.DESIGN / "inv_tb.svg").read_text(encoding="utf-8")
        self.assertIn("viewBox=", svg)


class BackendImages(unittest.TestCase):
    def test_xschem_has_an_image_of_its_own(self):
        """It is in neither this host nor the OpenLane image: no Homebrew
        formula, and a macOS source build needs XQuartz plus an
        X11-linked Tk."""
        self.assertEqual(custom_bridge.image_for("xschem"),
                         custom_bridge.XSCHEM_IMAGE)
        self.assertTrue(custom_bridge.XSCHEM_DOCKERFILE.is_file())

    def test_the_openlane_backends_point_at_the_pinned_openlane_image(self):
        for name in ("magic", "klayout", "netgen"):
            self.assertEqual(custom_bridge.image_for(name),
                             custom_bridge.OPENLANE_IMAGE, name)

    def test_a_local_only_backend_claims_no_image(self):
        self.assertIsNone(custom_bridge.image_for("ngspice"))

    def test_the_xschem_invocation_is_headless(self):
        """Without -x, xschem in a container with no X11 socket fails at
        startup instead of netlisting."""
        self.assertIn("-x", custom_bridge.BACKENDS["xschem"]["argv"]("s"))


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
        # xschem's headless flag is -x; -n (netlist) lives in
        # netlist_schematic(), which is a different invocation.
        self.assertIn("-x", custom_bridge.BACKENDS["xschem"]["argv"]("s"))


if __name__ == "__main__":
    unittest.main()
