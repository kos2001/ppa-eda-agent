"""Tests for pipeline/case_retrieval.py.

Retrieval that returns something plausible for everything is worse than
none: a review grounded in an irrelevant precedent is a review pointed
the wrong way with extra confidence. So these tests care as much about
what is *not* retrieved — a case with nothing in common, the target
itself — as about what is.

The ranking rule under test: a shared tool error code outranks any
structural resemblance, because two runs that failed the same way are
related in a way two similarly-shaped designs are not.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from case_retrieval import (  # noqa: E402
    RetrievalError, case_signatures, load_cases, precedent_block, signatures,
    similar, topology_distance,
)

TOPO_SMALL = {"module_count": 1, "has_macros": False, "clock_domain_count": 1,
              "port_count": 4, "sequential_element_estimate": 8,
              "power_domain_count": 1}
TOPO_MACRO = {"module_count": 2, "has_macros": True, "clock_domain_count": 1,
              "port_count": 4, "sequential_element_estimate": 72,
              "power_domain_count": 1}


def case(design, date, error=None, diagnosis="", topo=None, outcome="x",
         winner=None, reviews=()):
    return {
        "design": design, "date": date, "outcome": outcome,
        "winner_tag": winner, "stop_reason": "max_iterations_reached",
        "topology": topo, "diagnosis": diagnosis,
        "human_in_the_loop": [{"agent": a} for a in reviews],
        "iterations": [{"iteration": 1, "results": [
            {"tag": "c", "overrides": {}, **({"error": error} if error else {})}
        ]}],
        "_file": f"cases/{design}__{date}.json",
    }


class SignatureTests(unittest.TestCase):
    def test_extracts_tool_error_codes(self):
        self.assertEqual(
            signatures("[ERROR] [RSZ-0090] Max transition time from SDC"),
            {"RSZ-0090"})

    def test_extracts_several(self):
        got = signatures("[GRT-0097] no routing\n[PDN-0185] strap problem")
        self.assertEqual(got, {"GRT-0097", "PDN-0185"})

    def test_recognises_failures_that_carry_no_code(self):
        self.assertIn("floorplan-core-area", signatures("core_area is too small"))
        self.assertIn("pdn-strap-width", signatures("Insufficient width for straps"))

    def test_empty_text_yields_nothing(self):
        self.assertEqual(signatures(""), set())
        self.assertEqual(signatures(None), set())

    def test_reads_both_candidate_errors_and_the_diagnosis(self):
        # A diagnosis usually quotes the code that caused it, and older
        # cases may carry the code only there.
        c = case("d", "1", error="[GRT-0097] x", diagnosis="root cause was RSZ-0090")
        self.assertEqual(case_signatures(c), {"GRT-0097", "RSZ-0090"})


class SignoffSignatureTests(unittest.TestCase):
    """A run the tools finished and the gate rejected has no error code.

    Measured on the store on 2026-09-10: 18 cases (every aes, gcd and
    riscv32i run) had no signature, so no review of them could be
    grounded, while gcd's own clk5 -> clk8 setup history was precedent
    for aes's setup failures. The verdict's violations are the
    fingerprint those runs do have.
    """

    def _case_with(self, violations, **kw):
        c = case("d", "1", **kw)
        c["iterations"][0]["results"][0]["verdict"] = {
            "passed": False, "violations": violations}
        return c

    def test_each_kind_of_rejection_is_one_signature(self):
        from case_retrieval import signoff_signatures
        got = signoff_signatures({"violations": [
            "481 setup timing violation(s)", "122 hold timing violation(s)",
            "15 routing antenna violation(s)", "544 max-slew (DRV) violation(s)",
            "worst setup WNS -1.18 (timing violation)",
        ]})
        self.assertEqual(got, {"signoff:setup", "signoff:hold",
                               "signoff:antenna", "signoff:max-slew"})

    def test_a_slack_alone_still_names_setup(self):
        from case_retrieval import signoff_signatures
        self.assertEqual(signoff_signatures({"violations": ["worst setup WNS -0.2 (timing violation)"]}),
                         {"signoff:setup"})

    def test_a_pass_yields_nothing(self):
        from case_retrieval import signoff_signatures
        self.assertEqual(signoff_signatures({"passed": True, "violations": []}), set())
        self.assertEqual(signoff_signatures(None), set())

    def test_a_gate_rejected_case_now_has_a_signature(self):
        c = self._case_with(["135 setup timing violation(s)"])
        self.assertEqual(case_signatures(c), {"signoff:setup"})

    def test_two_designs_rejected_the_same_way_are_precedent_for_each_other(self):
        aes = self._case_with(["481 setup timing violation(s)"])
        aes["design"], aes["_file"] = "aes", "cases/aes__1.json"
        gcd = self._case_with(["135 setup timing violation(s)"], winner="clk8")
        gcd["design"], gcd["_file"] = "gcd", "cases/gcd__1.json"
        hits = similar(aes, [aes, gcd])
        self.assertEqual([h["design"] for h in hits], ["gcd"])
        self.assertEqual(hits[0]["shared_signatures"], ["signoff:setup"])

    def test_closures_find_where_a_remaining_kind_went_to_zero(self):
        from case_retrieval import closures, closures_block
        # aes still fails antenna and max-fanout. gcd once had fanout
        # violations and then none, with MAX_FANOUT_CONSTRAINT 12. Nobody
        # ever closed antenna.
        aes = self._case_with(["15 routing antenna violation(s)", "4 max-fanout (DRV) violation(s)"])
        aes["design"], aes["_file"] = "aes", "cases/aes__3.json"
        gcd1 = self._case_with(["9 max-fanout (DRV) violation(s)"])
        gcd1["design"], gcd1["_file"] = "gcd", "cases/gcd__1.json"
        gcd2 = self._case_with([], winner="c")
        gcd2["iterations"][0]["results"][0]["verdict"]["passed"] = True
        gcd2["iterations"][0]["results"][0]["overrides"] = {"MAX_FANOUT_CONSTRAINT": 12}
        gcd2["design"], gcd2["_file"] = "gcd", "cases/gcd__2.json"
        c = closures(aes, [aes, gcd1, gcd2])
        self.assertEqual(c["remaining"], {"signoff:antenna": 15, "signoff:max-fanout": 4})
        self.assertEqual(c["never_closed"], ["signoff:antenna"])
        hit = c["closed_elsewhere"]["signoff:max-fanout"][0]
        self.assertEqual((hit["design"], hit["from_count"], hit["overrides"]),
                         ("gcd", 9, {"MAX_FANOUT_CONSTRAINT": 12}))
        text = closures_block(aes, [aes, gcd1, gcd2])
        self.assertIn("never closed in any recorded run", text)
        self.assertIn('MAX_FANOUT_CONSTRAINT=12', text)

    def test_closures_are_appended_to_the_precedent_block(self):
        aes = self._case_with(["15 routing antenna violation(s)"])
        aes["design"], aes["_file"] = "aes", "cases/aes__3.json"
        other = self._case_with(["3 routing antenna violation(s)"])
        other["design"], other["_file"] = "riscv32i", "cases/riscv32i__1.json"
        text = precedent_block(aes, [aes, other])
        self.assertIn("riscv32i", text)
        self.assertIn("What closed each remaining failure", text)
        self.assertIn("signoff:antenna", text)

    def test_a_case_that_passes_has_no_closures_block(self):
        from case_retrieval import closures_block
        ok = case("d", "9", winner="c")
        ok["iterations"][0]["results"][0]["verdict"] = {"passed": True, "violations": []}
        self.assertEqual(closures_block(ok, [ok]), "")

    def test_a_tool_error_and_a_gate_rejection_do_not_match_each_other(self):
        tool = case("d", "2", error="[RSZ-0090] max transition")
        gate = self._case_with(["544 max-slew (DRV) violation(s)"])
        # Same underlying physics, different evidence; without a shared
        # code or a near topology they stay apart, as before.
        self.assertEqual(similar(gate, [gate, tool]), [])


class TopologyTests(unittest.TestCase):
    def test_identical_topology_is_zero(self):
        self.assertEqual(topology_distance(TOPO_SMALL, TOPO_SMALL), 0.0)

    def test_macro_and_non_macro_are_far_apart(self):
        self.assertGreater(topology_distance(TOPO_SMALL, TOPO_MACRO), 0.3)

    def test_unknown_topology_is_none_not_similar(self):
        # Scoring an unknown as 0 would rank unlabelled cases above real
        # matches.
        self.assertIsNone(topology_distance(None, TOPO_SMALL))
        self.assertIsNone(topology_distance(TOPO_SMALL, None))


class SimilarityTests(unittest.TestCase):
    def setUp(self):
        self.target = case("sram_wrapper", "2026-08-27",
                           error="[RSZ-0090] max transition", topo=TOPO_MACRO)
        self.corpus = [
            self.target,
            case("sram_wrapper", "2026-08-21", error="[RSZ-0090] same failure",
                 topo=TOPO_MACRO),
            case("counter4", "2026-08-21", error="[PDN-0185] unrelated",
                 topo=TOPO_SMALL),
        ]

    def test_finds_the_case_that_failed_the_same_way(self):
        got = similar(self.target, self.corpus)
        self.assertEqual(got[0]["date"], "2026-08-21")
        self.assertEqual(got[0]["shared_signatures"], ["RSZ-0090"])

    def test_never_returns_the_target_itself(self):
        for hit in similar(self.target, self.corpus):
            self.assertFalse(hit["design"] == "sram_wrapper"
                             and hit["date"] == "2026-08-27")

    def test_excludes_a_case_with_nothing_in_common(self):
        # counter4 shares no code and is structurally far away.
        designs = {h["design"] for h in similar(self.target, self.corpus)}
        self.assertNotIn("counter4", designs)

    def test_shared_code_outranks_topology(self):
        # A same-shaped design with no shared failure must rank below a
        # differently-shaped one that failed identically.
        twin = case("other_macro", "2026-08-20", error="[DRT-0001] different",
                    topo=TOPO_MACRO)
        same_failure = case("small_thing", "2026-08-19",
                            error="[RSZ-0090] same failure", topo=TOPO_SMALL)
        got = similar(self.target, [self.target, twin, same_failure])
        self.assertEqual(got[0]["design"], "small_thing")

    def test_cross_design_precedent_is_found(self):
        # The real case this exists for: counter4_tinydie and counter4
        # both hit PDN-0185.
        tgt = case("counter4_tinydie", "2026-08-27",
                   error="[PDN-0185] strap", topo=TOPO_SMALL)
        other = case("counter4", "2026-08-21", error="[PDN-0185] strap",
                     topo=TOPO_SMALL)
        got = similar(tgt, [tgt, other])
        self.assertEqual(got[0]["design"], "counter4")

    def test_a_resolved_case_outranks_an_open_one(self):
        """Among cases that failed the same way, the resolved one shows
        what actually worked; the open one shows only what was tried and
        did not. counter4_tinydie's PDN-0185 should reach counter4's
        resolved PDN-0185 cases, not sit on its own open ones."""
        tgt = case("t", "2026-08-28", error="[PDN-0185] strap", topo=TOPO_SMALL)
        still_open = case("a", "2026-08-20", error="[PDN-0185] strap",
                          topo=TOPO_SMALL)
        resolved = case("b", "2026-08-19", error="[PDN-0185] strap",
                        topo=TOPO_SMALL, winner="cand-x")
        got = similar(tgt, [tgt, still_open, resolved])
        self.assertEqual(got[0]["design"], "b")

    def test_signature_count_still_beats_resolution(self):
        """A resolved case that failed differently is not precedent —
        resolution breaks ties, it does not create matches."""
        tgt = case("t", "2026-08-28",
                   error="[PDN-0185] strap [RSZ-0090] transition",
                   topo=TOPO_SMALL)
        two_shared_open = case("a", "2026-08-20",
                               error="[PDN-0185] x [RSZ-0090] y",
                               topo=TOPO_SMALL)
        one_shared_resolved = case("b", "2026-08-19", error="[PDN-0185] x",
                                   topo=TOPO_SMALL, winner="cand-x")
        got = similar(tgt, [tgt, two_shared_open, one_shared_resolved])
        self.assertEqual(got[0]["design"], "a")

    def test_precedent_block_says_whether_it_was_resolved(self):
        tgt = case("t", "2026-08-28", error="[PDN-0185] x", topo=TOPO_SMALL)
        resolved = case("b", "2026-08-19", error="[PDN-0185] x",
                        topo=TOPO_SMALL, winner="cand-x", diagnosis="how it was fixed")
        text = precedent_block(tgt, [tgt, resolved])
        self.assertIn("RESOLVED", text)

    def test_respects_the_top_limit(self):
        corpus = [self.target] + [
            case("d%d" % i, "2026-08-%02d" % (i + 1), error="[RSZ-0090] x",
                 topo=TOPO_MACRO) for i in range(6)
        ]
        self.assertEqual(len(similar(self.target, corpus, top=2)), 2)


class PrecedentBlockTests(unittest.TestCase):
    def test_says_so_when_there_is_no_precedent(self):
        tgt = case("brand_new", "2026-08-27", error="[XYZ-9999] novel",
                   topo=TOPO_MACRO)
        text = precedent_block(tgt, [tgt])
        self.assertIn("No prior case", text)
        # A new failure must not be quietly matched to a familiar one.
        self.assertIn("treat it as such", text)

    def test_states_why_each_case_was_retrieved(self):
        tgt = case("sram_wrapper", "2026-08-27", error="[RSZ-0090] x",
                   topo=TOPO_MACRO)
        prior = case("sram_wrapper", "2026-08-21", error="[RSZ-0090] x",
                     topo=TOPO_MACRO, diagnosis="the earlier finding")
        text = precedent_block(tgt, [tgt, prior])
        self.assertIn("shares RSZ-0090", text)
        self.assertIn("the earlier finding", text)

    def test_truncates_a_long_diagnosis_and_points_at_the_file(self):
        # Diagnoses here reach 13 KB; inlining several would bury the
        # current case's own evidence in the prompt.
        prior = case("sram_wrapper", "2026-08-21", error="[RSZ-0090] x",
                     topo=TOPO_MACRO, diagnosis="y" * 5000)
        tgt = case("sram_wrapper", "2026-08-27", error="[RSZ-0090] x",
                   topo=TOPO_MACRO)
        text = precedent_block(tgt, [tgt, prior])
        self.assertIn("truncated", text)
        self.assertLess(len(text), 3000)


class LoadTests(unittest.TestCase):
    def test_reads_a_case_directory(self):
        d = Path(tempfile.mkdtemp())
        (d / "cases").mkdir()
        (d / "cases" / "x__1.json").write_text(json.dumps(case("x", "1")))
        self.assertEqual(len(load_cases(d)), 1)

    def test_skips_unreadable_files_rather_than_failing(self):
        d = Path(tempfile.mkdtemp())
        (d / "cases").mkdir()
        (d / "cases" / "bad.json").write_text("{not json")
        (d / "cases" / "ok.json").write_text(json.dumps(case("x", "1")))
        self.assertEqual(len(load_cases(d)), 1)

    def test_missing_directory_raises(self):
        with self.assertRaises(RetrievalError):
            load_cases(Path("/nonexistent/refdb"))

    def test_the_real_reference_db_retrieves_precedent_for_sram_wrapper(self):
        refdb = Path(__file__).resolve().parent.parent / "reference-db"
        if not (refdb / "cases").is_dir():
            self.skipTest("no reference-db")
        corpus = load_cases(refdb)
        sram = [c for c in corpus if c["design"] == "sram_wrapper"]
        if len(sram) < 2:
            self.skipTest("need at least two sram_wrapper cases")
        # The case that failed at the resizer precheck, not simply the
        # newest: sram_wrapper's 2026-09-10 case ran the Magic ladder on
        # the relaxed liberty and never hit RSZ-0090, so "latest" is no
        # longer the failure this test is about.
        with_rsz = [c for c in sram if "RSZ-0090" in case_signatures(c)]
        if len(with_rsz) < 2:
            self.skipTest("need two sram_wrapper cases that hit RSZ-0090")
        target = sorted(with_rsz, key=lambda c: c["date"])[-1]
        got = similar(target, corpus)
        self.assertTrue(got, "expected prior sram_wrapper cases to match")
        self.assertIn("RSZ-0090", got[0]["shared_signatures"])


if __name__ == "__main__":
    unittest.main()
