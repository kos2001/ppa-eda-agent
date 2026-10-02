"""Regression tests for pnr_repair.py — repairs read off OpenROAD's own words.

Every error text below is verbatim from a failed run in reference-db (or
from a real run made while writing the rule), trimmed to the lines the
rule reads. The point of testing against the real wording rather than a
paraphrase is that these rules are string parsers: a paraphrase proves the
parser handles the paraphrase.

    python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import orchestrator  # noqa: E402
import pnr_repair  # noqa: E402

# counter4 with PL_TARGET_DENSITY_PCT=40 (reference-db, run tag dens40).
GPL_0302 = """\
[INFO GPL-0019] Util:                    41.390 %
[INFO GPL-0020] StdInstsArea:           171.414 um^2
[INFO GPL-0021] MacroInstsArea:           0.000 um^2
[00:48:47] ERROR    [GPL-0302] Use a higher -density or          openroad.py:233
                    re-floorplan with a larger core area.
Given target density: 0.40
Suggested target density: 0.42
Error: gpl.tcl, 79 GPL-0302
"""

# cdc_twoclock on gf180mcuD, a fixed 60x60 um die (run tag gf180-7t).
GPL_0301 = """\
[INFO GPL-0021] MacroInstsArea:           0.000 um^2
[01:50:59] ERROR    [GPL-0301] Utilization 122.025 % exceeds     openroad.py:233
                    100%.
Error: gpl.tcl, 79 GPL-0301
"""

# counter4_tinydie on gf180 9t, 128x128 um die (run tag gf180-9t).
GPL_0307 = """\
[NesterovSolve] Iter:  320 overflow: 0.211 HPWL: 165356
[01:50:52] ERROR    [GPL-0307] RePlAce divergence detected.      openroad.py:233
                    Re-run with a smaller max_phi_cof value.
Error: gpl.tcl, 79 GPL-0307
"""

# counter4 on gf180mcuD at FP_CORE_UTIL 45 (run tag gf180-7t-util45).
DPL_0036 = """\
[INFO] Legalizing…
[INFO DPL-0034] Detailed placement failed on the following 2 instances:
[INFO DPL-0035]  _13_
[00:10:56] ERROR    [DPL-0036] Detailed placement failed.        openroad.py:233
Error: dpl.tcl, 19 DPL-0036
"""

# spm on sky130_fd_sc_hs (run tag hs-util55): hold repair ran out of buffers.
RSZ_0060_HOLD = """\
[INFO RSZ-0032] Inserted 215 hold buffers.
[23:52:08] ERROR    [RSZ-0060] Max buffer count reached.         openroad.py:233
[23:52:09] WARNING  [OpenROAD.ResizerTimingPostCTS] [RSZ-0064]       flow.py:675
                    Unable to repair all hold checks within margin.
[23:52:09] ERROR    The following error was encountered while    __main__.py:187
                    running the flow:
                    OpenROAD.ResizerTimingPostCTS failed with
                    the following errors:
                    [RSZ-0060] Max buffer count reached.
"""


def failed(error, **overrides):
    return {"tag": "t", "overrides": dict(overrides), "error": error}


class TestTargetDensity(unittest.TestCase):
    def test_uses_the_density_the_tool_suggests(self):
        """The message states the number it wants. Guessing a step instead
        would discard it: 0.42 -> 42, plus headroom for the legaliser."""
        got = pnr_repair.repair(failed(GPL_0302, PL_TARGET_DENSITY_PCT=40, FP_CORE_UTIL=35))
        self.assertEqual(got["code"], "GPL-0302")
        self.assertEqual(got["overrides"]["PL_TARGET_DENSITY_PCT"],
                         42 + pnr_repair.DENSITY_HEADROOM_PCT)
        self.assertEqual(got["overrides"]["FP_CORE_UTIL"], 35)  # untouched

    def test_a_suggestion_beyond_the_ceiling_is_not_chased(self):
        """Density cannot exceed what a core can hold; at that point the
        die is the problem and this rule must say so by declining."""
        text = GPL_0302.replace("0.42", "0.97")
        self.assertIsNone(pnr_repair.repair(failed(text, PL_TARGET_DENSITY_PCT=40)))

    def test_no_repeat_of_a_density_already_tried(self):
        text = GPL_0302.replace("Suggested target density: 0.42",
                                "Suggested target density: 0.30")
        self.assertIsNone(pnr_repair.repair(failed(text, PL_TARGET_DENSITY_PCT=40)))

    def test_message_without_a_suggestion_is_not_guessed_at(self):
        text = GPL_0302.replace("Suggested target density: 0.42\n", "")
        self.assertIsNone(pnr_repair.repair(failed(text, PL_TARGET_DENSITY_PCT=40)))


class TestOverfullDie(unittest.TestCase):
    BASE = {"FP_SIZING": "absolute", "DIE_AREA": [0, 0, 60, 60]}

    def test_a_fixed_die_is_grown_by_the_reported_utilization(self):
        got = pnr_repair.repair(failed(GPL_0301), self.BASE)
        self.assertEqual(got["code"], "GPL-0301")
        x0, y0, x1, y1 = got["overrides"]["DIE_AREA"]
        self.assertEqual((x0, y0), (0, 0))
        # 122% on 60 um: sqrt(122.025/50) * 1.1 = 1.72 -> 104 um
        self.assertEqual((x1, y1), (104, 104))
        # and the grown die really brings 122% under the 50% target
        self.assertLess(122.025 * (60 / x1) * (60 / y1), pnr_repair.GROW_TARGET_UTIL_PCT)

    def test_an_override_wins_over_the_base_config(self):
        got = pnr_repair.repair(failed(GPL_0301, DIE_AREA=[0, 0, 80, 80]), self.BASE)
        self.assertGreater(got["overrides"]["DIE_AREA"][2], 80)

    def test_relative_sizing_has_nothing_to_grow(self):
        """FP_CORE_UTIL derives the core, so this error cannot be repaired
        by guessing a die. Declining is the honest answer."""
        self.assertIsNone(pnr_repair.repair(failed(GPL_0301), {"FP_CORE_UTIL": 50}))

    def test_without_the_config_there_is_no_die_to_scale(self):
        self.assertIsNone(pnr_repair.repair(failed(GPL_0301)))


class TestDivergence(unittest.TestCase):
    def test_lowers_the_phi_bound_from_its_default(self):
        """Replayed for real on counter4_tinydie/gf180 9t: 1.03 still
        diverged, 1.01 converged and the candidate passed."""
        got = pnr_repair.repair(failed(GPL_0307))
        self.assertEqual(got["code"], "GPL-0307")
        self.assertAlmostEqual(got["overrides"]["PL_MAX_PHI_COEFFICIENT"], 1.01)

    def test_the_second_step_clamps_to_the_documented_floor(self):
        got = pnr_repair.repair(failed(GPL_0307, PL_MAX_PHI_COEFFICIENT=1.01))
        self.assertAlmostEqual(got["overrides"]["PL_MAX_PHI_COEFFICIENT"],
                               pnr_repair.PHI_FLOOR)

    def test_stops_at_the_floor_rather_than_repeating_it(self):
        self.assertIsNone(pnr_repair.repair(
            failed(GPL_0307, PL_MAX_PHI_COEFFICIENT=pnr_repair.PHI_FLOOR)))


class TestLegalisation(unittest.TestCase):
    def test_steps_utilization_down_from_an_override(self):
        got = pnr_repair.repair(failed(DPL_0036, FP_CORE_UTIL=65))
        self.assertEqual(got["code"], "DPL-0036")
        self.assertEqual(got["overrides"]["FP_CORE_UTIL"], 65 - pnr_repair.UTIL_STEP_DOWN)

    def test_falls_back_to_the_design_default(self):
        """A candidate that never overrode utilization used to have no
        value to step, so the whole class escalated."""
        got = pnr_repair.repair(failed(DPL_0036, SYNTH_STRATEGY="AREA 0"),
                                {"FP_CORE_UTIL": 45})
        self.assertEqual(got["overrides"]["FP_CORE_UTIL"], 30)
        self.assertEqual(got["overrides"]["SYNTH_STRATEGY"], "AREA 0")

    def test_floor_stops_the_loop(self):
        self.assertIsNone(pnr_repair.repair(
            failed(DPL_0036, FP_CORE_UTIL=pnr_repair.MIN_CORE_UTIL)))

    def test_a_fixed_die_ignores_utilization(self):
        """Stepping FP_CORE_UTIL under FP_SIZING=absolute re-runs the same
        layout under a different name."""
        self.assertIsNone(pnr_repair.repair(
            failed(DPL_0036, FP_CORE_UTIL=65),
            {"FP_SIZING": "absolute", "DIE_AREA": [0, 0, 60, 60]}))


class TestHoldBufferAllowance(unittest.TestCase):
    def test_the_hold_margin_is_given_up_before_the_cap_is_raised(self):
        """Replayed for real on spm/sky130_fd_sc_hs at FP_CORE_UTIL 55:
        margin 0 passes at the original utilization with 32 hold buffers
        and 5296 um^2; the cap raise ended at 221 buffers and 6520 um^2."""
        got = pnr_repair.repair(failed(RSZ_0060_HOLD, FP_CORE_UTIL=55))
        self.assertEqual(got["code"], "RSZ-0060")
        self.assertEqual(got["overrides"]["PL_RESIZER_HOLD_SLACK_MARGIN"], 0.0)
        self.assertEqual(got["overrides"]["FP_CORE_UTIL"], 55)  # not stepped
        self.assertNotIn("PL_RESIZER_HOLD_MAX_BUFFER_PCT", got["overrides"])

    def test_the_cap_moves_only_once_the_margin_is_spent(self):
        got = pnr_repair.repair(failed(RSZ_0060_HOLD, PL_RESIZER_HOLD_SLACK_MARGIN=0.0))
        self.assertEqual(got["overrides"]["PL_RESIZER_HOLD_MAX_BUFFER_PCT"],
                         pnr_repair.BUFFER_PCT_DEFAULT + pnr_repair.BUFFER_PCT_STEP)

    def test_post_grt_repair_uses_the_grt_variables(self):
        text = RSZ_0060_HOLD.replace("PostCTS", "PostGRT")
        got = pnr_repair.repair(failed(text))
        self.assertEqual(got["overrides"]["GRT_RESIZER_HOLD_SLACK_MARGIN"], 0.0)
        self.assertNotIn("PL_RESIZER_HOLD_SLACK_MARGIN", got["overrides"])

    def test_the_design_config_can_already_have_spent_the_margin(self):
        got = pnr_repair.repair(failed(RSZ_0060_HOLD),
                                {"PL_RESIZER_HOLD_SLACK_MARGIN": 0})
        self.assertIn("PL_RESIZER_HOLD_MAX_BUFFER_PCT", got["overrides"])

    def test_cap_is_bounded(self):
        self.assertIsNone(pnr_repair.repair(failed(
            RSZ_0060_HOLD, PL_RESIZER_HOLD_SLACK_MARGIN=0.0,
            PL_RESIZER_HOLD_MAX_BUFFER_PCT=pnr_repair.BUFFER_PCT_CAP)))

    def test_a_setup_side_failure_is_not_this_rule(self):
        """Never observed in the store, so not promoted by symmetry. With
        RSZ-0062 (setup) instead of RSZ-0064 (hold) the rule declines."""
        text = RSZ_0060_HOLD.replace("RSZ-0064", "RSZ-0062")
        self.assertIsNone(pnr_repair.repair(failed(text)))

    def test_unknown_phase_is_not_guessed(self):
        text = RSZ_0060_HOLD.replace("RSZ-0064", "RSZ-9999")
        self.assertIsNone(pnr_repair.repair(failed(text)))


class TestWarningsDoNotDrive(unittest.TestCase):
    def test_a_code_that_only_appears_in_a_warning_is_ignored(self):
        """Same trap as classify_stage's GRT-0097: the captured tail holds
        warnings from earlier stages that name codes the run did not die of."""
        text = ("[00:01:00] WARNING  [GPL-0302] Use a higher -density\n"
                "Suggested target density: 0.42\n"
                "[00:02:00] ERROR    something else entirely\n")
        self.assertIsNone(pnr_repair.repair(failed(text)))

    def test_a_run_without_an_error_is_never_repaired(self):
        self.assertIsNone(pnr_repair.repair({"tag": "t", "overrides": {}}))


class TestProposeRepairsIntegration(unittest.TestCase):
    def test_density_failure_becomes_a_candidate_with_its_reason(self):
        results = [{"tag": "dens40", "overrides": {"PL_TARGET_DENSITY_PCT": 40},
                    "error": GPL_0302}]
        got = orchestrator.propose_repairs(results, 1)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["overrides"]["PL_TARGET_DENSITY_PCT"], 44)
        self.assertEqual(got[0]["repair"]["code"], "GPL-0302")
        self.assertEqual(got[0]["tag"], "dens40-iter1")

    def test_the_repair_keeps_the_technology(self):
        results = [{"tag": "x", "overrides": {"FP_CORE_UTIL": 45}, "error": DPL_0036,
                    "pdk": "gf180mcuD", "scl": "gf180mcu_fd_sc_mcu7t5v0"}]
        got = orchestrator.propose_repairs(results, 1)
        self.assertEqual((got[0]["pdk"], got[0]["scl"]),
                         ("gf180mcuD", "gf180mcu_fd_sc_mcu7t5v0"))

    def test_existing_patterns_still_win_where_they_applied(self):
        """The PDN strap pattern is older and proven; the new rules are a
        fallthrough and must not change what it proposes."""
        results = [{"tag": "u55", "overrides": {"FP_CORE_UTIL": 55},
                    "error": "[PDN-0185] Insufficient width (17.48 um)"}]
        got = orchestrator.propose_repairs(results, 1)
        self.assertEqual(got[0]["overrides"]["FP_CORE_UTIL"], 40)
        self.assertNotIn("repair", got[0])

    def test_pdn_failure_on_the_default_utilization_is_now_repairable(self):
        results = [{"tag": "dflt", "overrides": {"SYNTH_STRATEGY": "AREA 0"},
                    "error": "[PDN-0185] Insufficient width (17.48 um)"}]
        self.assertEqual(orchestrator.propose_repairs(results, 1), [])
        got = orchestrator.propose_repairs(results, 1, {"FP_CORE_UTIL": 45})
        self.assertEqual(got[0]["overrides"]["FP_CORE_UTIL"], 30)

    def test_unknown_failure_still_proposes_nothing(self):
        results = [{"tag": "x", "overrides": {"FP_CORE_UTIL": 40},
                    "error": "some novel tool crash"}]
        self.assertEqual(orchestrator.propose_repairs(results, 1, {"FP_CORE_UTIL": 40}), [])

    def test_historic_names_still_resolve(self):
        self.assertEqual(orchestrator.UTIL_STEP_DOWN, 15)
        self.assertEqual(orchestrator.MIN_CORE_UTIL, 20)


class TestEvidence(unittest.TestCase):
    def test_every_validated_rule_has_a_recorded_replay_that_fixed_it(self):
        """A rule is marked validated only when reference-db holds a real
        replay in which its failure reproduced and the repair got past it."""
        import json
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "reference-db", "pnr_repair_checks.json")
        replays = json.load(open(path, encoding="utf-8"))
        for rule in pnr_repair.RULES:
            if not rule["validated"]:
                continue
            mine = [r for r in replays if r["code"] == rule["code"] and r.get("expect_fixed", True)]
            self.assertTrue(mine, f"{rule['code']} is marked validated with no replay")
            self.assertTrue(all(r["reproduced"] and r["fixed"] for r in mine), rule["code"])


if __name__ == "__main__":
    unittest.main()
