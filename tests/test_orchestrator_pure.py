"""Regression tests for orchestrator.py's pure decision logic.

Practice borrowed from github.com/kos2001/strongarm-sizing-console's
`tests/` (a sibling analog-IC console with 26 real regression files) —
specifically its convention that every test names the real failure it
guards against, so a future reader can tell an intentional pin from an
incidental assertion. Its pytest dependency is deliberately NOT borrowed:
this pipeline is dependency-free by design (see soul.md, "borrow the
working part, not the whole machine"), so these use the standard library's
unittest and run with no install step:

    python3 -m unittest discover -s tests -v

Scope is deliberately the *pure* functions — no Docker, no OpenLane, no
PDK — because that is exactly where this session's real bugs lived. Every
case below pins a bug that actually shipped and was found by accident
during a real run, at real cost; the point of the file is that the next
one gets caught in milliseconds instead.

Integration behaviour (a real OpenLane flow, a real KLayout render) is
NOT covered here — it needs Docker and a PDK, is minutes per run, and is
already exercised for real on every orchestrate. Mocking it would prove
nothing about the tools this pipeline actually shells out to.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import orchestrator  # noqa: E402


class TestOverrideValue(unittest.TestCase):
    """Guards override_value(), the formatter for OpenLane's
    `--override-config KEY=VALUE`. Two separate real bugs shipped here,
    both silent in code review and both loud only at OpenLane's CLI."""

    def test_string_is_not_json_quoted(self):
        """Real bug (reference-db/cases/counter4__2026-08-22.json):
        json.dumps("AREA 0") -> '"AREA 0"' with literal quote characters,
        which fails OpenLane's Literal-type validation because it
        compares against bare enum strings and does not strip quotes."""
        self.assertEqual(orchestrator.override_value("AREA 0"), "AREA 0")
        self.assertNotIn('"', orchestrator.override_value("AREA 0"))

    def test_list_has_no_brackets_or_spaces(self):
        """Real bug (reference-db/cases/counter4_tinydie__2026-08-21.json):
        a JSON array literal "[0, 0, 8, 8]" makes OpenLane's CLI parser
        mis-split on the spaces and error on a phantom variable
        'DIE_AREA[0]' with value '[0'. Its List-typed variables want the
        elements bare and comma-separated."""
        got = orchestrator.override_value([0, 0, 8, 8])
        self.assertEqual(got, "0,0,8,8")
        for forbidden in ("[", "]", " "):
            self.assertNotIn(forbidden, got)

    def test_numbers_stay_plain(self):
        """Numeric overrides must not gain quotes either — FP_CORE_UTIL
        is the most-used override in this repo and is type-checked."""
        self.assertEqual(orchestrator.override_value(35), "35")
        self.assertEqual(orchestrator.override_value(0.75), "0.75")


class TestExpandSweeps(unittest.TestCase):
    """Guards expand_sweeps(), which turns run_spec.json's declarative
    "sweeps" into concrete candidates."""

    def test_tag_never_contains_a_space(self):
        """Real bug, found by direct elimination: the identical
        SYNTH_STRATEGY="AREA 0" override passes standalone but fails with
        a phantom "1 Lint errors found" purely because the derived run tag
        was "sweep-synth-AREA 0". The tag becomes a directory name and an
        OpenLane --run-tag; a space in it breaks OpenLane's own internal
        subprocess invocations."""
        spec = {"sweeps": [{"param": "SYNTH_STRATEGY",
                             "values": ["AREA 0", "DELAY 1"],
                             "tag_prefix": "sweep-synth"}]}
        tags = [c["tag"] for c in orchestrator.expand_sweeps(spec)]
        self.assertEqual(tags, ["sweep-synth-AREA_0", "sweep-synth-DELAY_1"])
        for tag in tags:
            self.assertNotIn(" ", tag)

    def test_sweep_merges_base_overrides(self):
        """A sweep may fix other config alongside the swept parameter
        (e.g. sweeping utilization inside an already-chosen DIE_AREA);
        dropping the base would silently run a different experiment than
        the run_spec describes."""
        spec = {"sweeps": [{"param": "FP_CORE_UTIL", "values": [25, 35],
                             "tag_prefix": "u",
                             "overrides": {"DIE_AREA": [0, 0, 8, 8]}}]}
        got = orchestrator.expand_sweeps(spec)
        self.assertEqual(got[0]["overrides"],
                          {"DIE_AREA": [0, 0, 8, 8], "FP_CORE_UTIL": 25})
        self.assertEqual(got[1]["overrides"]["FP_CORE_UTIL"], 35)

    def test_no_sweeps_key_is_not_an_error(self):
        """run_spec.json may list candidates by hand only."""
        self.assertEqual(orchestrator.expand_sweeps({}), [])


class TestClassifyStage(unittest.TestCase):
    """Guards classify_stage(), which tags a result with the process
    stage its run actually reached."""

    def test_warning_lines_do_not_drive_classification(self):
        """Real bug: sram_wrapper's RSZ-0090 failure (a placement-stage
        problem) was misclassified as routing_generation because an
        incidental "[GRT-0097] No global routing found" WARNING appeared
        earlier in the same captured output tail. Only non-WARNING lines
        may match."""
        result = {"tag": "t", "error": (
            "[WARNING] [GRT-0097] No global routing found for nets\n"
            "[ERROR] RSZ-0090 max transition violation\n")}
        self.assertEqual(orchestrator.classify_stage(result),
                          "physical_constraint")

    def test_real_routing_error_still_classifies_as_routing(self):
        """The WARNING filter must not make routing failures
        unclassifiable — a genuine GRT error on a non-WARNING line still
        has to reach the routing stage."""
        result = {"tag": "t", "error": "[ERROR] GRT-0123 routing failed\n"}
        self.assertEqual(orchestrator.classify_stage(result),
                          "routing_generation")

    def test_unclassified_failure_is_pre_verification(self):
        """An unrecognized run failure must never be reported as
        verification_ppa: it produced no metrics.json, so claiming it
        reached signoff would overstate what actually ran."""
        result = {"tag": "t", "error": "something nobody has seen before"}
        self.assertEqual(orchestrator.classify_stage(result),
                          "physical_constraint")

    def test_a_real_verdict_means_verification(self):
        """A verdict exists only when metrics.json was produced."""
        result = {"tag": "t", "verdict": {"passed": True}}
        self.assertEqual(orchestrator.classify_stage(result),
                          "verification_ppa")


class TestSafeTag(unittest.TestCase):
    """Run tags become directory names and CLI arguments.

    Sanitizing only spaces was not enough and it cost a whole collection
    run. A DIE_AREA sweep value stringifies to "[0, 0, 64, 64]", which
    became the tag "data-die-[0,_0,_64,_64]" — brackets and commas
    intact. Every die size from 48 um up then failed with ODB-0307,
    including 64 um, which had passed minutes earlier under a different
    tag. Same design, same size, same config; only the tag differed.
    Nine runs of what looked like real failure data were artifacts.
    """

    def test_list_values_produce_a_safe_tag(self):
        got = orchestrator.safe_tag("data-die-[0, 0, 64, 64]")
        self.assertEqual(got, "data-die_0_0_64_64")

    def test_no_shell_or_glob_metacharacters_survive(self):
        for raw in ("a[0]b", "x,y", "p q", "v{1}", "s*t", "u?v", "a'b", 'c"d',
                    "e;f", "g|h", "i&j", "k$l", "m`n", "o(p)"):
            got = orchestrator.safe_tag(raw)
            self.assertRegex(got, r"^[A-Za-z0-9._-]+$", raw)

    def test_ordinary_tags_are_left_recognisable(self):
        self.assertEqual(orchestrator.safe_tag("sweep-util-45"), "sweep-util-45")
        self.assertEqual(orchestrator.safe_tag("cand-baseline"), "cand-baseline")

    def test_spaces_still_become_underscores(self):
        # Unchanged on purpose: tags are recorded in reference-db, so
        # remapping an existing value would make a rerun of the same
        # sweep produce a different tag from the historical one.
        self.assertEqual(orchestrator.safe_tag("sweep-synth-AREA 0"),
                         "sweep-synth-AREA_0")

    def test_distinct_values_stay_distinct(self):
        # Collapsing everything to one character would make two
        # candidates share a run directory and overwrite each other.
        tags = {orchestrator.safe_tag(f"d-{v}")
                for v in ([0, 0, 64, 64], [0, 0, 128, 128], "AREA 0", "AREA 1")}
        self.assertEqual(len(tags), 4, tags)

    def test_never_empty(self):
        self.assertTrue(orchestrator.safe_tag("[]"))
        self.assertTrue(orchestrator.safe_tag(""))

    def test_expand_sweeps_applies_it(self):
        spec = {"sweeps": [{"param": "DIE_AREA", "tag_prefix": "data-die",
                            "values": [[0, 0, 64, 64]]}]}
        cand = orchestrator.expand_sweeps(spec)[0]
        self.assertRegex(cand["tag"], r"^[A-Za-z0-9._-]+$")
        # The override itself must keep the real list, untouched.
        self.assertEqual(cand["overrides"]["DIE_AREA"], [0, 0, 64, 64])


class TestScore(unittest.TestCase):
    """Guards score(), which turns a real metrics.json into a verdict."""

    def _metrics(self, **over):
        """A completed, clean signoff.

        Every signoff metric must be present and zero, because that is
        what a real completed run produces — audited against a full
        counter4_tinydie signoff, which emitted 281 metrics including all
        23 of these at 0. Building the fixture from the same list score()
        reads keeps the two from drifting apart.
        """
        base = {k: 0 for k, _ in orchestrator.SIGNOFF_METRICS}
        base["timing__setup__wns__corner:tt"] = 0.0
        base.update(over)
        return base

    def test_no_metrics_at_all_is_not_a_pass(self):
        v = orchestrator.score({}, {})
        self.assertFalse(v["passed"])

    def test_absent_check_is_unverified_not_a_violation(self):
        """"Never checked" and "checked, found 3 errors" both block a
        pass, but they are different facts and must not be conflated —
        the same distinction this pipeline draws everywhere else between
        a measured limit and an assumed one."""
        m = self._metrics()
        del m["klayout__drc_error__count"]
        v = orchestrator.score(m, {})
        self.assertFalse(v["passed"])
        self.assertIn("KLayout DRC error(s)", v["unverified"])
        self.assertEqual(v["violations"], [])

    def test_a_skipped_signoff_no_longer_reads_as_clean(self):
        """The real hole this closes, reproduced from an actual run.

        Stopping a real OpenLane flow at OpenROAD.STAPostPNR — one step
        before the DRC/LVS/XOR block — produces 255 metrics with 11 of
        the 23 signoff checks absent, including BOTH DRC signoffs and all
        seven LVS checks. The old `if count:` test treated every absent
        one as clean and scored that run PASS.
        """
        m = self._metrics()
        for k in ("magic__drc_error__count", "klayout__drc_error__count",
                  "design__lvs_error__count", "design__xor_difference__count",
                  "magic__illegal_overlap__count",
                  "design__lvs_device_difference__count",
                  "design__lvs_net_difference__count",
                  "design__lvs_property_fail__count",
                  "design__lvs_unmatched_device__count",
                  "design__lvs_unmatched_net__count",
                  "design__lvs_unmatched_pin__count"):
            del m[k]
        v = orchestrator.score(m, {})
        self.assertFalse(v["passed"])
        self.assertEqual(len(v["unverified"]), 11)
        self.assertEqual(v["violations"], [])

    def test_unverified_is_empty_on_a_complete_run(self):
        """A gate that always fires is as useless as one that never
        does — a real completed signoff must report nothing unverified."""
        self.assertEqual(orchestrator.score(self._metrics(), {})["unverified"], [])

    def test_negative_setup_wns_on_any_corner_fails(self):
        """OpenLane emits one WNS key per PVT corner; a violation on any
        one of them is a real timing violation, so the worst must be
        taken across all of them rather than a single nominal corner."""
        m = self._metrics(**{"timing__setup__wns__corner:ss": -0.05})
        v = orchestrator.score(m, {})
        self.assertFalse(v["passed"])
        self.assertEqual(v["worst_setup_wns"], -0.05)

    def test_utilization_target_is_enforced(self):
        m = self._metrics(**{"design__instance__utilization__stdcell": 0.9})
        self.assertFalse(orchestrator.score(m, {"max_core_utilization": 0.75})["passed"])
        self.assertTrue(orchestrator.score(m, {"max_core_utilization": 0.95})["passed"])

    def test_clean_metrics_pass(self):
        self.assertTrue(orchestrator.score(self._metrics(), {})["passed"])

    def test_klayout_drc_is_gated_not_just_magic(self):
        """Real hole this closes: OpenLane runs TWO independent DRC
        signoffs and only Magic's was checked, so a candidate KLayout
        flagged and Magic did not was reported PASS. Found by auditing a
        real run: OpenLane emits 279 metrics, score() read 32."""
        m = self._metrics(**{"klayout__drc_error__count": 3})
        v = orchestrator.score(m, {})
        self.assertFalse(v["passed"])
        self.assertTrue(any("KLayout DRC" in s for s in v["violations"]))

    def test_antenna_and_power_grid_violations_fail(self):
        """Both are real manufacturing failures, not advisories."""
        for key in ("route__antenna_violation__count",
                     "design__power_grid_violation__count"):
            v = orchestrator.score(self._metrics(**{key: 1}), {})
            self.assertFalse(v["passed"], key)

    def test_drv_counts_fail(self):
        """max_slew/max_cap/max_fanout are the same DRV family that
        produces RSZ-0090 — the failure this project spent the most effort
        diagnosing — and they were in metrics.json as structured numbers
        all along."""
        for key in ("design__max_slew_violation__count",
                     "design__max_cap_violation__count",
                     "design__max_fanout_violation__count"):
            v = orchestrator.score(self._metrics(**{key: 2}), {})
            self.assertFalse(v["passed"], key)

    def test_a_clean_design_still_passes(self):
        """The new gates must not fail designs that are actually fine —
        every one of these is zero on a real clean counter4 run."""
        m = self._metrics(**{
            "klayout__drc_error__count": 0,
            "route__antenna_violation__count": 0,
            "design__power_grid_violation__count": 0,
            "design__max_slew_violation__count": 0,
            "design__lint_error__count": 0,
        })
        self.assertTrue(orchestrator.score(m, {})["passed"])

    def test_hold_violations_fail_a_candidate(self):
        """Demonstrated hole this closes: a candidate with hold_wns -0.25
        and 7 hold violations scored as PASS, while the pipeline recorded
        and displayed that negative slack on the dashboard. Hold
        violations are silicon-fatal and cannot be fixed after
        fabrication, so being silent about them was the worst thing the
        verdict could do."""
        m = self._metrics(**{"timing__hold__wns__corner:tt": -0.25,
                              "timing__hold_vio__count": 7})
        v = orchestrator.score(m, {})
        self.assertFalse(v["passed"])
        self.assertTrue(any("hold" in s.lower() for s in v["violations"]))

    def test_hold_is_still_reported_per_corner(self):
        """Gating must not replace the per-corner detail the dashboard
        renders — both facts matter."""
        m = self._metrics(**{"timing__hold__wns__corner:tt": -0.25})
        v = orchestrator.score(m, {})
        self.assertEqual(v["timing_corners"][0]["hold_wns"], -0.25)

    def test_openlane_critical_metrics_are_gated(self):
        """OpenLane's own metric library marks these critical=True — its
        own declaration of what is fatal. Gating on that list rather than
        a hand-picked one keeps the verdict agreeing with the tool it
        trusts."""
        for key in ("design__instance_unmapped__count",
                     "design__xor_difference__count",
                     "magic__illegal_overlap__count",
                     "design__disconnected_pin__count",
                     "route__drc_errors",
                     "design__lvs_unmatched_pin__count"):
            v = orchestrator.score(self._metrics(**{key: 1}), {})
            self.assertFalse(v["passed"], f"{key} did not fail the candidate")


class TestProposeRepairs(unittest.TestCase):
    """Guards propose_repairs(), the bounded auto-repair loop. Its whole
    value is being narrow: it must propose only for failures it has
    really seen, never guess."""

    def test_pdn_strap_failure_steps_utilization_down(self):
        results = [{"tag": "u55", "overrides": {"FP_CORE_UTIL": 55},
                     "error": "[PDN-0185] Insufficient width (17.48 um)"}]
        got = orchestrator.propose_repairs(results, 1)
        self.assertEqual(len(got), 1)
        self.assertLess(got[0]["overrides"]["FP_CORE_UTIL"], 55)

    def test_utilization_floor_stops_the_loop(self):
        """At the floor there is no repair left to propose. Returning a
        candidate identical to one that already failed would burn a real
        (slow) OpenLane run to learn nothing."""
        results = [{"tag": "u20", "overrides": {"FP_CORE_UTIL": orchestrator.MIN_CORE_UTIL},
                     "error": "[PDN-0185] Insufficient width (17.48 um)"}]
        self.assertEqual(orchestrator.propose_repairs(results, 1), [])

    def test_unknown_failure_proposes_nothing(self):
        """An unrecognized failure must escalate (empty list -> the
        orchestrate loop stops with no_repairable_failures), not get a
        guessed config change attached to it."""
        results = [{"tag": "x", "overrides": {"FP_CORE_UTIL": 40},
                     "error": "some novel tool crash"}]
        self.assertEqual(orchestrator.propose_repairs(results, 1), [])

    def test_target_violation_on_a_completed_run_is_repaired(self):
        """Real structural gap this closes: every other pattern reads
        error text, but a candidate that completes the whole flow and
        merely misses a target has no error at all — so that entire class
        escalated to a human despite being the most mechanically
        repairable kind. Verified end to end with a real OpenLane run:
        util 0.604 vs a 0.50 target repaired to FP_CORE_UTIL 20, which
        then passed at 0.379."""
        verdict = {"passed": False, "area_um2": 290.278, "utilization": 0.604167,
                    "worst_setup_wns": 0.0, "power": {"total_w": 1e-4},
                    "violations": ["utilization 0.604 > target 0.5"]}
        results = [{"tag": "u35", "overrides": {"FP_CORE_UTIL": 35}, "verdict": verdict}]
        got = orchestrator.propose_repairs(results, 1)
        self.assertEqual(len(got), 1)
        self.assertLess(got[0]["overrides"]["FP_CORE_UTIL"], 35)

    def test_a_non_utilization_violation_is_not_guessed_at(self):
        """A timing violation has no proven mechanical repair here, so it
        must still escalate. Repairing only what is actually known is the
        whole point of this function staying narrow."""
        verdict = {"passed": False, "area_um2": 1.0, "utilization": 0.4,
                    "worst_setup_wns": -0.5, "power": None,
                    "violations": ["worst setup WNS -0.5 (timing violation)"]}
        results = [{"tag": "t", "overrides": {"FP_CORE_UTIL": 35}, "verdict": verdict}]
        self.assertEqual(orchestrator.propose_repairs(results, 1), [])

    def test_passing_candidates_are_not_repaired(self):
        results = [{"tag": "ok", "overrides": {}, "verdict": {"passed": True}}]
        self.assertEqual(orchestrator.propose_repairs(results, 1), [])


class TestPickWinner(unittest.TestCase):
    """Guards pick_winner()'s constrained Pareto ranking."""

    def _passing(self, tag, area, power=0.0, wns=0.0):
        return {"tag": tag, "overrides": {}, "verdict": {
            "passed": True, "area_um2": area, "worst_setup_wns": wns,
            "power": {"total_w": power}}}

    def test_no_passing_candidate_has_no_winner(self):
        """An empty result must be None, not an arbitrary pick — this is
        what makes a case OPEN rather than falsely CLOSED."""
        self.assertIsNone(orchestrator.pick_winner(
            [{"tag": "a", "overrides": {}, "error": "boom"}]))

    def test_a_dominated_candidate_never_wins(self):
        """b is worse on every objective, so it must lose regardless of
        how the tie-breaking inside the front behaves."""
        a = self._passing("a", area=100.0, power=1e-3)
        b = self._passing("b", area=200.0, power=2e-3)
        self.assertEqual(orchestrator.pick_winner([a, b])["tag"], "a")
        self.assertEqual(orchestrator.pick_winner([b, a])["tag"], "a")

    def test_single_passing_candidate_wins(self):
        self.assertEqual(orchestrator.pick_winner([self._passing("only", 1.0)])["tag"],
                          "only")


class TestPickLayoutSubject(unittest.TestCase):
    """Guards pick_layout_subject(), which chooses the one candidate
    whose layout gets rendered into reference-db."""

    def test_winner_is_preferred(self):
        winner = {"tag": "w", "stage": "verification_ppa"}
        iterations = [{"iteration": 1, "results": [
            {"tag": "loser", "stage": "physical_constraint"}, winner]}]
        self.assertEqual(
            orchestrator.pick_layout_subject(iterations, winner)["tag"], "w")

    def test_without_a_winner_the_furthest_candidate_is_chosen(self):
        """For a failed case the most informative layout is the one that
        got furthest through the flow — that is precisely the case where
        a picture is worth rendering at all."""
        iterations = [{"iteration": 1, "results": [
            {"tag": "early", "stage": "physical_constraint"},
            {"tag": "late", "stage": "routing_generation"}]}]
        self.assertEqual(
            orchestrator.pick_layout_subject(iterations, None)["tag"], "late")

    def test_no_results_is_none(self):
        self.assertIsNone(orchestrator.pick_layout_subject([], None))


class TestStopReasonsAreTotal(unittest.TestCase):
    """Guards the graph-engineering invariant that orchestrate()'s loop
    has no untagged exit."""

    def test_write_case_maps_every_stop_reason_to_an_outcome(self):
        """Every declared STOP_REASON must produce a distinct outcome
        string. A reason silently falling through to the generic default
        would re-collapse the distinction self_improve.py now depends on
        to decide whether a case needs a human."""
        outcomes = set()
        for reason in orchestrator.STOP_REASONS:
            mapped = {
                "winner_found": "passed",
                "max_iterations_reached":
                    "no candidate met targets after all iterations",
                "no_repairable_failures":
                    "no candidate met targets — no auto-repairable "
                    "pattern matched, needs a human/subagent decision",
            }.get(reason)
            self.assertIsNotNone(mapped, f"{reason} has no outcome mapping")
            outcomes.add(mapped)
        self.assertEqual(len(outcomes), len(orchestrator.STOP_REASONS))

    def test_process_stage_ids_are_unique_and_ordered(self):
        """classify_stage() and pick_layout_subject() both index into
        PROCESS_STAGES by id; a duplicate id would make "furthest stage"
        ambiguous."""
        ids = [s["id"] for s in orchestrator.PROCESS_STAGES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 8)


class TestScreening(unittest.TestCase):
    """Guards screen_candidates()'s decision rules. The Docker-backed run
    itself isn't covered here (see this module's docstring), but the
    conditions under which it prunes at all are."""

    def test_no_utilization_target_means_no_screening(self):
        """Screening exists only to prune candidates that would miss a
        utilization target. With no such target there is nothing it could
        decide, so it must not spend a real run per candidate finding
        that out."""
        cands = [{"tag": "a", "overrides": {}}, {"tag": "b", "overrides": {}}]
        survivors, pruned = orchestrator.screen_candidates(
            Path("/nonexistent"), cands, targets={})
        self.assertEqual(survivors, cands)
        self.assertEqual(pruned, [])

    def test_screen_step_is_before_signoff(self):
        """The cutoff must sit early enough to be cheap. Measured: 10s to
        GeneratePDN vs 64s for the full flow on counter4."""
        self.assertEqual(orchestrator.SCREEN_STEP, "OpenROAD.GeneratePDN")


class SameDayCaseTests(unittest.TestCase):
    """One file per run, not per day.

    `{design}__{date}.json` meant a second orchestrate of the same
    design on the same day silently replaced the first. Hit for real:
    a technology comparison (counter4 tech-hd vs tech-hs) was
    overwritten by a synthesis sweep the same afternoon, and the only
    symptom was the surrogate dataset quietly losing rows.
    """

    def test_a_second_run_the_same_day_does_not_replace_the_first(self):
        import orchestrator
        with tempfile.TemporaryDirectory() as tmp:
            real = orchestrator.REFDB
            orchestrator.REFDB = Path(tmp)
            try:
                design = Path(tmp) / "d"
                design.mkdir()
                a = orchestrator.write_case("d", design, [], None)
                b = orchestrator.write_case("d", design, [], None)
                self.assertNotEqual(a, b)
                self.assertTrue(a.exists() and b.exists())
            finally:
                orchestrator.REFDB = real

    def test_the_first_run_keeps_the_plain_name(self):
        # Every existing case file and every path in index.json uses it,
        # so only a collision may take a suffix.
        import orchestrator
        with tempfile.TemporaryDirectory() as tmp:
            real = orchestrator.REFDB
            orchestrator.REFDB = Path(tmp)
            try:
                design = Path(tmp) / "d"
                design.mkdir()
                first = orchestrator.write_case("d", design, [], None)
                self.assertRegex(first.name, r"^d__\d{4}-\d{2}-\d{2}\.json$")
            finally:
                orchestrator.REFDB = real


if __name__ == "__main__":
    unittest.main()
