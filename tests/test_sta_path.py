"""Tests for the stage-by-stage slew trace, and for the eval that says
whether a model swap would change anything.

Both exist because of one question — "would a better LLM solve the cases
that are stuck?" — and both are attempts to answer it with a number
rather than an opinion.

The measured answer, on the three recorded cases replayed through the
configured model: grounded 3/3, root-cause recall 1/10. The grounding
gate passed every answer, including one that invented a causal chain
("an unpowered SRAM cannot drive its input loads, so wrapper cells
inherit the slow edge") between two unrelated errors. That is what
verify_diagnosis's own docstring warns it cannot catch, demonstrated.

What actually cracked the case was a query no tool exposed: report_checks
against a chosen pin. sta_path exposes it, so the constraint the
measurement found — the agent could not run the measurement — is the one
being lifted, rather than the model being swapped and hoped over.
"""
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import agent_eval  # noqa: E402
import sta_path  # noqa: E402

# A real report_checks table, as OpenSTA emitted it for sram_wrapper.
REPORT = """
###PATH###
Startpoint: _093_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: u_sram (falling edge-triggered flip-flop clocked by clk)
Path Type: max

      Cap      Slew     Delay      Time   Description
-------------------------------------------------------------------------------
                       0.0000    0.0000   clock clk (rise edge)
             0.0977    0.0000    1.0025 ^ _093_/CLK (sky130_fd_sc_hd__dfxtp_1)
   0.0044    0.0539    0.3301    1.3326 ^ _093_/Q (sky130_fd_sc_hd__dfxtp_1)
   0.0138    0.1576    0.1725    1.5051 ^ load_slew85/X (sky130_fd_sc_hd__dlymetal6s2s_1)
   0.0221    0.2350    0.2560    1.7614 ^ load_slew84/X (sky130_fd_sc_hd__dlymetal6s2s_1)
   0.0474    0.2585    0.3076    2.0695 ^ load_slew83/X (sky130_fd_sc_hd__clkbuf_2)
             0.3453    0.0091    2.0786 ^ u_sram/addr0[3] (sky130_sram_1kbyte_1rw1r_32x256_8)
                                 2.0786   data arrival time
"""


class ParseTests(unittest.TestCase):
    def setUp(self):
        self.stages = sta_path.parse_path(REPORT)

    def test_reads_the_chain_in_order(self):
        self.assertEqual([s["pin"] for s in self.stages][-2:],
                         ["load_slew83/X", "u_sram/addr0[3]"])

    def test_reads_the_cell_at_each_stage(self):
        # The cell name is the finding. "load_slew84" says nothing;
        # "dlymetal6s2s_1" says a delay cell is repairing slew.
        cells = [s["cell"] for s in self.stages if s["cell"]]
        self.assertIn("sky130_fd_sc_hd__dlymetal6s2s_1", cells)

    def test_rows_without_a_slew_still_parse(self):
        # A pin that is only a load has blank Cap and Slew columns.
        # Dropping those rows would lose the endpoint itself.
        endpoint = self.stages[-1]
        self.assertEqual(endpoint["pin"], "u_sram/addr0[3]")
        self.assertAlmostEqual(endpoint["slew_ns"], 0.3453)

    def test_a_report_with_no_table_yields_nothing(self):
        self.assertEqual(sta_path.parse_path("No paths found."), [])


class DegraderTests(unittest.TestCase):
    def test_names_the_stage_that_adds_the_most_slew(self):
        # Taking the differences is the whole point: every stage looks
        # unremarkable until you see which one grew.
        got = sta_path.worst_degrader(sta_path.parse_path(REPORT))
        self.assertEqual(got["pin"], "load_slew85/X")
        self.assertEqual(got["cell"], "sky130_fd_sc_hd__dlymetal6s2s_1")
        self.assertAlmostEqual(got["added_slew_ns"], 0.1037, places=3)

    def test_it_is_the_added_slew_not_the_largest_slew(self):
        # The largest absolute slew is the endpoint, which is a symptom.
        # The cause is whichever stage added most.
        stages = sta_path.parse_path(REPORT)
        largest = max(s["slew_ns"] for s in stages if s["slew_ns"] is not None)
        self.assertNotEqual(sta_path.worst_degrader(stages)["to_ns"], largest)

    def test_an_empty_or_single_stage_path_has_no_degrader(self):
        self.assertIsNone(sta_path.worst_degrader([]))
        self.assertIsNone(sta_path.worst_degrader(sta_path.parse_path(REPORT)[:1]))


class InputDiscoveryTests(unittest.TestCase):
    """The SDC lookup, which got this wrong once in a way that lied.

    `*/[!s]*.sdc` was meant to skip the synthesis SDC. It also skipped
    sram_wrapper.sdc, so OpenSTA ran with no clock, said "No paths
    found", and the trace came back empty — indistinguishable from a
    clean path.
    """

    def _run(self, *names):
        import tempfile
        d = Path(tempfile.mkdtemp())
        for name in names:
            p = d / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x")
        return d

    def test_finds_an_sdc_whose_name_starts_with_s(self):
        got = sta_path.inputs_for(
            self._run("33-place/sram_wrapper.nl.v", "34-cts/sram_wrapper.sdc"))
        self.assertIsNotNone(got["sdc"])
        self.assertEqual(got["sdc"].name, "sram_wrapper.sdc")

    def test_skips_the_synthesis_abc_sdc(self):
        got = sta_path.inputs_for(
            self._run("33-place/x.nl.v", "06-yosys/synthesis.abc.sdc"))
        self.assertIsNone(got["sdc"])

    def test_prefers_final_when_the_run_completed(self):
        got = sta_path.inputs_for(
            self._run("final/nl/x.nl.v", "33-place/x.nl.v"))
        self.assertIn("final", str(got["netlist"]))

    def test_a_run_without_a_netlist_raises(self):
        with self.assertRaises(sta_path.StaPathError):
            sta_path.inputs_for(self._run("06-yosys/synthesis.abc.sdc"))


class EvalScoringTests(unittest.TestCase):
    """The scoring in agent_eval, which is what makes the model question
    answerable at all."""

    CASE = {
        "design": "sram_wrapper",
        "iterations": [{"results": [
            {"tag": "cand-baseline", "error": "[RSZ-0090] Max transition"},
        ]}],
    }

    def test_an_answer_citing_a_real_code_is_grounded(self):
        got = agent_eval.score_case(self.CASE, "RSZ-0090 is the blocker.")
        self.assertTrue(got["grounded"])

    def test_an_invented_code_is_caught(self):
        # The failure this exists for: a confident answer citing an
        # error the run never produced.
        got = agent_eval.score_case(self.CASE, "The cause is PDN-9999.")
        self.assertFalse(got["grounded"])
        self.assertIn("PDN-9999", got["ungrounded"])

    def test_recall_counts_root_cause_terms(self):
        hit, total, terms = agent_eval.recall(
            "The max_transition limit on the addr pins comes from the liberty.",
            "sram_wrapper")
        self.assertEqual(total, 4)
        self.assertEqual(hit, 3)
        self.assertNotIn("slew", terms)

    def test_grounded_and_wrong_is_representable(self):
        # The measured result: every replayed answer was grounded and
        # nearly all missed the cause. A scoring scheme that could not
        # express that would have reported the model as fine.
        answer = "RSZ-0090 fails because the PDN is unconnected."
        got = agent_eval.score_case(self.CASE, answer)
        self.assertTrue(got["grounded"])
        self.assertEqual(got["recall"], "0/4")

    def test_the_prompt_withholds_the_answer(self):
        # The case's own diagnosis must never reach the model, or the
        # eval measures reading comprehension.
        case = dict(self.CASE, diagnosis="the real answer is max_transition")
        self.assertNotIn("max_transition", agent_eval.prompt_for(case))


class McpExposureTests(unittest.TestCase):
    def test_the_trace_is_offered_to_agents(self):
        # The point of the exercise. An agent asked to take over the
        # human-in-the-loop step could previously see which pins
        # violated and not why — the one query that settled the case was
        # not a tool it had.
        import mcp_server
        names = {t["name"] for t in mcp_server.TOOLS}
        self.assertIn("ppa_sta_path", names)
        self.assertIn("ppa_sta_path", mcp_server._TOOL_IMPL)

    def test_its_description_says_it_is_not_the_critical_path(self):
        # An agent that takes the pin from the timing report gets a
        # clean path and concludes nothing is wrong: the case that
        # motivated this had +18.57 ns of setup slack on the very path
        # whose slew was 22x over.
        import mcp_server
        desc = next(t["description"] for t in mcp_server.TOOLS
                    if t["name"] == "ppa_sta_path")
        self.assertIn("NOT the critical path", desc)
        self.assertIn("violator", desc)


if __name__ == "__main__":
    sys.exit(unittest.main())


class ReadOnlyQueryTests(unittest.TestCase):
    """An open-ended question, without an open-ended shell.

    Borrowed in shape from github.com/The-OpenROAD-Project/OpenROAD-MCP,
    whose interactive_openroad_exec runs arbitrary commands in a
    persistent session. The value in that design is that an agent can
    ask something nobody shipped a tool for — this project's own
    breakthrough on sram_wrapper was `report_checks -to <pin>`, which
    existed in no tool until it was added as one. The value is not the
    ability to write, so this allows reporting commands and nothing
    else.
    """

    def test_reporting_commands_are_allowed(self):
        for good in ("report_power", "report_checks -path_delay max",
                     "get_property [get_pins a/b] slew_max_rise"):
            self.assertEqual(sta_path.check_query(good), good)

    def test_anything_that_modifies_is_refused(self):
        for bad in ("delete_cell x", "write_verilog out.v", "exec rm -rf /",
                    "set_max_transition 0.1 [current_design]", "source evil.tcl"):
            with self.assertRaises(sta_path.StaPathError, msg=bad):
                sta_path.check_query(bad)

    def test_the_refusal_says_what_is_allowed(self):
        # A refusal that does not name the alternative sends the caller
        # back to guessing, which is what the tool exists to stop.
        with self.assertRaises(sta_path.StaPathError) as ctx:
            sta_path.check_query("write_def out.def")
        self.assertIn("report_checks", str(ctx.exception))

    def test_every_line_is_checked_not_just_the_first(self):
        # A allow-listed first line must not carry the rest in behind it.
        with self.assertRaises(sta_path.StaPathError):
            sta_path.check_query("report_power\nwrite_verilog out.v")

    def test_comments_and_blank_lines_are_skipped(self):
        sta_path.check_query("# what the slack is\n\nreport_worst_slack -max")


class ImageContentTests(unittest.TestCase):
    """An image the agent can actually see.

    ppa_render_layout returned {"png_path": ...} and every MCP result was
    forced to type "text", so a tool whose own description cites the
    measured finding that layout images improve diagnosis handed back a
    string. OpenROAD-MCP exposes read_report_image for exactly this.
    """

    PNG = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
           "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

    def _png(self):
        import base64
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / "x.png").write_bytes(base64.b64decode(self.PNG))
        return d / "x.png"

    def test_an_image_path_is_attached_as_an_image(self):
        import mcp_server
        blocks = mcp_server._content({"png_path": str(self._png())})
        self.assertEqual([b["type"] for b in blocks], ["text", "image"])
        self.assertEqual(blocks[1]["mimeType"], "image/png")

    def test_the_path_is_still_reported(self):
        # A human, and a later Bash call, both need it.
        import mcp_server
        path = self._png()
        blocks = mcp_server._content({"png_path": str(path)})
        self.assertIn(str(path), blocks[0]["text"])

    def test_a_non_image_path_is_not_attached(self):
        import mcp_server
        blocks = mcp_server._content({"odb": "/tmp/x.odb", "spef_path": "/tmp/x.spef"})
        self.assertEqual([b["type"] for b in blocks], ["text"])

    def test_a_missing_file_is_not_attached(self):
        import mcp_server
        blocks = mcp_server._content({"png_path": "/nonexistent/x.png"})
        self.assertEqual([b["type"] for b in blocks], ["text"])

    def test_an_oversized_image_says_so_rather_than_vanishing(self):
        # Silently dropping it would leave the agent waiting for a
        # picture that never comes and no reason why.
        import mcp_server
        import tempfile
        d = Path(tempfile.mkdtemp())
        big = d / "big.png"
        big.write_bytes(b"\x89PNG" + b"0" * (mcp_server.MAX_INLINE_IMAGE_BYTES + 1))
        blocks = mcp_server._content({"png_path": str(big)})
        self.assertEqual([b["type"] for b in blocks], ["text", "text"])
        self.assertIn("inline", blocks[1]["text"])

    def test_the_render_tool_is_still_registered(self):
        import mcp_server
        self.assertIn("ppa_render_layout", mcp_server._TOOL_IMPL)
        self.assertIn("ppa_sta_query", mcp_server._TOOL_IMPL)
