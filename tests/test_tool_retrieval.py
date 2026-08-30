"""Tests for retrieving which measurement answers a failure.

case_retrieval tells an agent what happened before. It does not tell it
what to run, and that gap was measured: replaying three recorded cases
through the configured model scored grounded 3/3 and root-cause recall
1/10, while every real advance in those cases came from running
something new. None of the eight agent definitions in .claude/agents/
mentions a single tool of this pipeline's.

So this indexes measurements by the same failure signatures — the tool,
the question it answers, and the trap that wastes the attempt.

The load-bearing test here is the leave-one-out one. Every entry is
grounded in a case this pipeline actually resolved, which means for that
case the entry IS the answer. Feeding it back would measure reading
comprehension, exactly what agent_eval's prompt is built to avoid, and
would have produced a flattering A/B that meant nothing.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import agent_eval  # noqa: E402
import tool_retrieval  # noqa: E402

CASES = ROOT / "reference-db" / "cases"


def case(name: str) -> dict | None:
    hits = sorted(CASES.glob(f"{name}__*.json"))
    return json.loads(hits[-1].read_text()) if hits else None


class EntryShapeTests(unittest.TestCase):
    """Every entry has to carry its own justification."""

    def test_each_entry_names_the_case_it_came_from(self):
        # Nothing is here for sounding plausible. An entry with no
        # recorded evidence is the same failure as an ungrounded
        # diagnosis, one level up.
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertTrue(entry["evidence"].strip(), entry["id"])
            self.assertTrue(entry["design"].strip(), entry["id"])

    def test_the_source_design_is_a_field_not_prose(self):
        # Leave-one-out reads this. It used to read the first word of
        # `evidence`, so an entry whose evidence opened with a variable
        # name rather than a design could never be excluded — it would
        # have leaked that design's answer into its own evaluation
        # while every test still passed.
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertIn("design", entry, entry["id"])
            self.assertNotIn(" ", entry["design"], entry["id"])

    def test_each_entry_names_a_trap(self):
        # The trap is the transferable part. "Run report_checks" is
        # obvious once you know it exists; "the pin does not come from
        # the timing report" is what cost this project several sessions.
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertGreater(len(entry["trap"]), 40, entry["id"])

    def test_each_entry_is_runnable(self):
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertTrue(entry["cli"].strip(), entry["id"])
            self.assertTrue(entry["when"], entry["id"])

    def test_evidence_designs_exist(self):
        # An entry citing a design with no recorded case is stale.
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertIsNotNone(case(entry["design"]),
                                 f"{entry['id']} cites {entry['design']}")


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.sram = case("sram_wrapper")
        if self.sram is None:
            self.skipTest("no sram_wrapper case")

    def test_it_matches_on_real_error_codes(self):
        keys = tool_retrieval.case_keys(self.sram)
        self.assertIn("RSZ-0090", keys)
        self.assertIn("PDN-0231", keys)

    def test_it_derives_symptoms_the_codes_do_not_name(self):
        # "there is a macro" and "something violated max slew" are
        # conditions no error code states, and both select measurements.
        keys = tool_retrieval.case_keys(self.sram)
        self.assertIn("macro_present", keys)
        self.assertIn("max_slew_violation", keys)

    def test_the_path_trace_is_retrieved_for_this_failure(self):
        ids = [h["id"] for h in tool_retrieval.retrieve(self.sram)]
        self.assertIn("slew-path-trace", ids)

    def test_more_specific_entries_come_first(self):
        hits = tool_retrieval.retrieve(self.sram)
        self.assertEqual(hits[0]["specificity"], 1.0)
        self.assertGreaterEqual(hits[0]["specificity"], hits[-1]["specificity"])

    def test_a_case_with_no_matching_signature_gets_nothing(self):
        # Silence is the honest answer for a failure nobody has
        # debugged. Returning the nearest-looking entry would be
        # guidance invented on the spot.
        self.assertEqual(tool_retrieval.retrieve({"iterations": []}), [])


class LeaveOneOutTests(unittest.TestCase):
    """The guard that keeps the A/B honest."""

    def setUp(self):
        self.sram = case("sram_wrapper")
        if self.sram is None:
            self.skipTest("no sram_wrapper case")

    def test_entries_from_the_same_design_are_dropped(self):
        kept = tool_retrieval.retrieve(self.sram, exclude_design="sram_wrapper")
        for hit in kept:
            self.assertNotEqual(hit["design"], "sram_wrapper")

    def test_the_eval_prompt_never_carries_the_answer(self):
        # The specific leak: sram_wrapper's entries say "dlymetal6s2s_1,
        # a delay cell" and "0.04 equals the top of index_1" — its whole
        # root cause. Measuring a model against that measures reading.
        prompt = agent_eval.prompt_for(self.sram, guidance=True)
        for leak in ("dlymetal", "index_1", "addr0/addr1/wmask0"):
            self.assertNotIn(leak, prompt, leak)

    def test_guidance_still_reaches_a_reviewer(self):
        # The exclusion is for the eval only. A human or agent working
        # the real case should get everything, including what that case
        # itself established.
        block = tool_retrieval.guidance_block(self.sram)
        self.assertIn("ppa_sta_path", block)
        self.assertIn("dlymetal6s2s_1", block)

    def test_nothing_transferable_says_so_rather_than_going_blank(self):
        # With four designs and one debugged failure shape, everything
        # matching sram_wrapper was learned from sram_wrapper. That is a
        # finding about the corpus, not an empty result.
        block = tool_retrieval.guidance_block(self.sram, exclude_design="sram_wrapper")
        self.assertIn("learned from sram_wrapper itself", block)
        self.assertIn("no transferable guidance", block)

    def test_that_is_distinguished_from_no_match_at_all(self):
        empty = tool_retrieval.guidance_block({"iterations": []},
                                              exclude_design="whatever")
        self.assertIn("No recorded measurement matches", empty)
        self.assertNotIn("transferable", empty)


# Tools that drive the loop rather than answer a failure. They are how
# a run is started, a case is read and a review is filed — no failure
# signature should select them, so they are exempt from the coverage
# check below rather than being given invented entries.
WORKFLOW_TOOLS = {
    "ppa_orchestrate", "ppa_run_stage", "ppa_get_case", "ppa_request_review",
    "ppa_apply_review", "ppa_self_improve_scan",
}


class CoverageTests(unittest.TestCase):
    """A tool nobody can retrieve is a tool nobody reaches for.

    Measured before this check existed: 13 MCP tools, 4 reachable from
    any failure signature. Worst of them was ppa_sta_report — the trap
    text on the path trace says "take the pin from ppa_sta_report's
    violator list", while nothing routed an agent to ppa_sta_report at
    all. It was told to use the output of a tool it was never pointed
    at.
    """

    def _mcp(self):
        import mcp_server
        return {t["name"] for t in mcp_server.TOOLS}

    def _reachable(self):
        names = self._mcp()
        return {n for e in tool_retrieval.MEASUREMENTS for n in names
                if n in e["tool"] or n in e["cli"]}

    def test_every_diagnostic_tool_is_reachable(self):
        missing = sorted(self._mcp() - WORKFLOW_TOOLS - self._reachable())
        self.assertEqual(missing, [],
                         f"no failure signature reaches: {missing}")

    def test_the_workflow_exemption_names_real_tools(self):
        # An exemption list that drifts is a way to make the check pass
        # by listing whatever fails it.
        self.assertEqual(sorted(WORKFLOW_TOOLS - self._mcp()), [])

    def test_the_path_trace_prerequisite_is_reachable(self):
        # The specific gap. ppa_sta_path is useless without an endpoint,
        # and ppa_sta_report is where endpoints come from.
        self.assertIn("ppa_sta_report", self._reachable())


class TransferabilityTests(unittest.TestCase):
    """The thing the index did not have when it was first written.

    Every entry then came from sram_wrapper, so leave-one-out left
    sram_wrapper with nothing and the layer had no transferable
    guidance. Sweeping the technology axis produced a signature from a
    different source: counter4, cdc_twoclock and spm all reach step 74
    on sky130_fd_sc_hs and all fail the same rule, with Magic reporting
    21/48/282 violations while KLayout reports 0/0/0.
    """

    NEW_DESIGN = {
        "design": "some_new_design",
        "iterations": [{"results": [
            {"tag": "t", "scl": "sky130_fd_sc_hs",
             "error": "21 Magic DRC errors found"},
        ]}],
    }

    def test_a_non_default_library_is_a_retrievable_signature(self):
        self.assertIn("scl_sky130_fd_sc_hs",
                      tool_retrieval.case_keys(self.NEW_DESIGN))

    def test_the_default_library_is_not_a_signature(self):
        # Every case uses hd; making that a key would match everything
        # and select nothing.
        case = {"design": "d", "iterations": [{"results": [
            {"tag": "t", "scl": "sky130_fd_sc_hd"}]}]}
        self.assertNotIn("scl_sky130_fd_sc_hd", tool_retrieval.case_keys(case))

    def test_a_new_design_retrieves_guidance_it_did_not_produce(self):
        # The point. This survives leave-one-out because the evidence
        # comes from somewhere else.
        hits = tool_retrieval.retrieve(self.NEW_DESIGN,
                                       exclude_design="some_new_design")
        self.assertTrue(hits)
        self.assertNotIn("some_new_design", {h["design"] for h in hits})

    def test_a_foundry_change_retrieves_the_floorplan_warning(self):
        # GPL-0301 says "Utilization 122%" and nothing about the library.
        # A design hitting it on a foundry it has never used should be
        # told that first, not left to sweep utilisation.
        case = {"design": "brand_new", "iterations": [{"results": [
            {"tag": "t", "scl": "gf180mcu_fd_sc_mcu7t5v0",
             "error": "[GPL-0301] Utilization 122.025 % exceeds"}]}]}
        ids = {h["id"] for h in tool_retrieval.retrieve(
            case, exclude_design="brand_new")}
        self.assertIn("new-technology-needs-a-new-floorplan", ids)

    def test_the_inert_override_trap_is_recorded(self):
        # FP_CORE_UTIL does nothing when FP_SIZING is absolute, so the
        # obvious fix fails identically to no fix at all. This project
        # has published two wrong conclusions from ignored overrides.
        entry = next(e for e in tool_retrieval.MEASUREMENTS
                     if e["id"] == "new-technology-needs-a-new-floorplan")
        self.assertIn("FP_SIZING", entry["trap"])
        self.assertIn("DIE_AREA", entry["trap"])

    def test_a_new_library_retrieves_both_kinds_of_failure(self):
        # Two different things go wrong on a new library and they need
        # different answers: the cells may carry a DRC rule the shipped
        # exclusion list misses, and they are larger — 50% for
        # sky130_fd_sc_hs, 3.7-4.4x for gf180mcu — so a die carried over
        # is too small. A generic entry-shape test
        # does not notice either going missing.
        case = {"design": "brand_new", "iterations": [{"results": [
            {"tag": "t", "scl": "sky130_fd_sc_hs",
             "error": "[PDN-0185] strap does not fit"},
        ]}]}
        ids = {h["id"] for h in tool_retrieval.retrieve(case,
                                                        exclude_design="brand_new")}
        self.assertIn("new-technology-needs-a-new-floorplan", ids)
        self.assertIn("hs-library-magic-drc", ids)

    def test_guidance_comes_from_more_than_one_source_design(self):
        # The measure of whether retrieval is worth anything: a design
        # that has produced nothing still gets help, from somewhere else.
        case = {"design": "brand_new", "iterations": [{"results": [
            {"tag": "t", "scl": "sky130_fd_sc_hs",
             "error": "[PDN-0185] strap does not fit"},
        ]}]}
        sources = {h["design"] for h in tool_retrieval.retrieve(
            case, exclude_design="brand_new")}
        self.assertGreater(len(sources), 1, sources)

    def test_an_error_message_alone_is_enough_to_retrieve(self):
        # Neither of these carries an EDA error code, so signature
        # matching on codes finds nothing. Both are cases where the
        # message names something the user never set and there is no run
        # directory to inspect — exactly when guidance is worth most.
        for err, expect in [
            ("Encountered one or more fatal errors while running Magic",
             "unreadable-macro-gds"),
            ("Path provided for variable 'PNR_EXCLUDED_CELL_FILE' is "
             "invalid: Errors have occurred while loading the PDK",
             "library-blocked-by-a-missing-file"),
        ]:
            case = {"design": "brand_new", "iterations": [{"results": [
                {"tag": "t", "error": err}]}]}
            ids = {h["id"] for h in tool_retrieval.retrieve(
                case, exclude_design="brand_new")}
            self.assertIn(expect, ids, err[:40])

    def test_each_symptom_is_derived_from_the_real_message(self):
        # Asserted per symptom, not through retrieval. Each entry lists
        # two `when` keys, so dropping one extraction still matched via
        # the other and a mutation removing it passed — the retrieval
        # kept working while the key it was supposed to derive silently
        # stopped existing.
        for err, key in [
            ("Unknown layer/datatype in boundary, layer=33 type=42",
             "unknown_layer_datatype"),
            ("Encountered one or more fatal errors while running Magic",
             "magic_read_failure"),
            ("Path provided for variable 'PNR_EXCLUDED_CELL_FILE' is invalid",
             "pnr_excluded_cell_file_invalid"),
            ("Errors have occurred while loading the PDK",
             "pdk_load_failure"),
        ]:
            case = {"design": "d", "iterations": [{"results": [
                {"tag": "t", "error": err}]}]}
            self.assertIn(key, tool_retrieval.case_keys(case), err[:40])

    def test_an_unrelated_error_derives_none_of_them(self):
        # A key that fires on anything selects nothing.
        case = {"design": "d", "iterations": [{"results": [
            {"tag": "t", "error": "[STA-1140] library already exists"}]}]}
        keys = tool_retrieval.case_keys(case)
        for key in ("unknown_layer_datatype", "magic_read_failure",
                    "pnr_excluded_cell_file_invalid", "pdk_load_failure"):
            self.assertNotIn(key, keys)

    def test_the_suppression_trap_is_recorded(self):
        # The most expensive kind of guidance: a knob that looks like the
        # fix and is not. MAGIC_CAPTURE_ERRORS=false takes sram_wrapper
        # from an abort to 2,831,364 DRC errors on a GDS built from a
        # macro Magic could not read.
        entry = next(e for e in tool_retrieval.MEASUREMENTS
                     if e["id"] == "unreadable-macro-gds")
        self.assertIn("MAGIC_CAPTURE_ERRORS", entry["trap"])
        self.assertIn("2,831,364", entry["trap"])

    def test_the_index_is_no_longer_single_source(self):
        # With one source design, leave-one-out empties the index for
        # that design and the layer cannot help anyone.
        self.assertGreater(len({e["design"] for e in tool_retrieval.MEASUREMENTS}), 1)


class ReviewRequestTests(unittest.TestCase):
    def test_the_request_carries_the_measurement_block(self):
        # Where an agent's context actually comes from. Without this the
        # block exists and nothing reads it.
        src = (ROOT / "pipeline" / "request_review.py").read_text()
        self.assertIn("tool_retrieval.guidance_block", src)

    def test_a_generated_request_contains_it(self):
        hits = sorted((ROOT / "reference-db" / "reviews")
                      .glob("sram_wrapper__*__request.md"))
        if not hits:
            self.skipTest("no generated request")
        text = hits[-1].read_text()
        self.assertIn("Measurements that apply", text)
        self.assertIn("trap", text)


if __name__ == "__main__":
    sys.exit(unittest.main())
