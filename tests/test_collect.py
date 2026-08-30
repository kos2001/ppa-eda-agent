"""Tests for bulk collection, and for knowing what a batch will cost.

The corpus grew one hand-written sweep at a time, which left it
lopsided: 52 rows of FP_CORE_UTIL, which moves counter4's area 4.3%,
against 19 of CLOCK_PERIOD, which moves its power 2.5x. collect.py
enumerates the cross-product instead.

The cost half exists because a batch was mis-sized in exactly the way
the code could not see. aes and riscv32i are 14,705 and 9,731 cells and
take twenty to thirty-five minutes a run under contention; every other
design here takes six to seventy seconds. 64 runs planned as if uniform
took the machine to a load average of 55 and finished nothing in
thirty-five minutes.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import collect  # noqa: E402


class SkipTests(unittest.TestCase):
    def test_a_design_that_never_completes_is_skipped_with_a_reason(self):
        # sram_wrapper produces no completed rows at all, so running it
        # collects nothing. The reason travels with the decision rather
        # than living in a comment.
        self.assertIn("sram_wrapper", collect.SKIP)
        self.assertIn("Magic", collect.SKIP["sram_wrapper"])
        for reason in collect.SKIP.values():
            self.assertGreater(len(reason), 40)

    def test_skipped_designs_produce_no_plan(self):
        items = collect.plan(["sram_wrapper"])
        self.assertEqual(items, [])


class PlanTests(unittest.TestCase):
    def test_it_plans_across_technologies(self):
        items = collect.plan(["counter4"])
        if not items:
            self.skipTest("counter4 fully collected")
        self.assertGreater(len({(i.get("scl"), i.get("pdk")) for i in items}), 1)

    def test_an_absolute_die_is_not_swept_on_utilisation(self):
        # FP_CORE_UTIL is inert when FP_SIZING is absolute — measured on
        # cdc_twoclock, where the override was accepted and the run
        # failed identically to no override at all.
        cfg = collect.declared("cdc_twoclock")
        if cfg.get("FP_SIZING") != "absolute":
            self.skipTest("cdc_twoclock no longer uses an absolute die")
        for item in collect.plan(["cdc_twoclock"]):
            self.assertNotIn("FP_CORE_UTIL", item["overrides"])

    def test_a_bigger_technology_gets_a_bigger_die(self):
        # gf180mcu's cells are 3.7-4.4x sky130's, so a die sized for
        # sky130 fails on utilisation and teaches nothing.
        cfg = {"FP_SIZING": "absolute", "DIE_AREA": [0, 0, 100, 100]}
        grown = collect.scaled_die(cfg, 4.0)
        self.assertEqual(grown, [0, 0, 200, 200])

    def test_a_relative_die_is_left_alone(self):
        self.assertIsNone(collect.scaled_die({"FP_CORE_UTIL": 35}, 4.0))

    def test_the_slow_designs_skip_the_cheap_only_axis(self):
        # SYNTH_STRATEGY is nine values. Nine more runs is nothing on a
        # design that takes 30 s and half a day on one that takes 30 min.
        for design in collect.SLOW_DESIGNS:
            for item in collect.plan([design]):
                self.assertNotIn("SYNTH_STRATEGY", item["overrides"], design)


class EstimateTests(unittest.TestCase):
    """Saying "I don't know" instead of a confident wrong number."""

    ITEMS = [{"design": "fast", "tag": "t", "overrides": {}},
             {"design": "unknown_design", "tag": "t", "overrides": {}}]

    def test_an_untimed_design_is_named_not_guessed(self):
        # The first version filled untimed designs with the slowest
        # timed one and reported 19 minutes for a batch that ran for
        # hours. A 30x spread makes any such fallback wrong.
        got = collect.estimate(self.ITEMS, parallel=2)
        self.assertIn("unknown_design", got["untimed_designs"])
        self.assertFalse(got["estimate_covers_everything"])
        self.assertIsNone(got["per_design"]["unknown_design"]["seconds_each"])

    def test_the_minutes_cover_only_what_was_timed(self):
        got = collect.estimate(self.ITEMS, parallel=2)
        self.assertEqual(got["runs"], 2)
        self.assertLessEqual(got["runs_with_a_timing"], got["runs"])

    def test_a_fully_timed_plan_says_so(self):
        got = collect.estimate([], parallel=2)
        self.assertTrue(got["estimate_covers_everything"])
        self.assertEqual(got["untimed_designs"], [])

    def test_parallelism_divides_the_wall_time(self):
        timed = collect.recorded_seconds()
        if not timed:
            self.skipTest("nothing timed yet")
        design = next(iter(timed))
        items = [{"design": design, "tag": "t", "overrides": {}}] * 8
        one = collect.estimate(items, parallel=1)
        four = collect.estimate(items, parallel=4)
        self.assertAlmostEqual(one["wall_minutes_at_parallel"],
                               four["wall_minutes_at_parallel"] * 4, places=0)

    def test_timings_come_from_recorded_runs(self):
        # Read from the `seconds` field run_one already writes, so the
        # estimate sharpens itself every time anything is collected.
        timed = collect.recorded_seconds()
        if not timed:
            self.skipTest("nothing timed yet")
        for design, seconds in timed.items():
            self.assertGreater(seconds, 0, design)


class PreFloorplanAxisTests(unittest.TestCase):
    """A design that cannot floorplan learns nothing from synthesis.

    Every axis this collector varies — SYNTH_STRATEGY, CLOCK_PERIOD, and
    FP_CORE_UTIL where it applies — acts before the floorplan. A batch
    proved the point by sweeping nine strategies across four
    technologies on counter4_tinydie: all 36 failed, none at synthesis.
    """

    def test_a_design_that_cannot_floorplan_is_not_swept(self):
        planned = collect.plan(["counter4_tinydie"])
        self.assertEqual(planned, [])

    def test_every_other_design_is_still_swept(self):
        # A guard that swallows the healthy designs would be worse than
        # the redundant rows it exists to prevent.
        #
        # "Planned nothing" is not by itself the failure, which this test
        # originally got wrong: a design whose whole cross-product is
        # already recorded correctly plans nothing, and counter4 reached
        # that state. The failure is planning nothing *before* dedup —
        # that is the guard eating a design it should not.
        names = [p.name for p in collect.DESIGNS.iterdir()
                 if (p / "config.json").exists()
                 and p.name not in collect.SKIP
                 and p.name not in collect.NO_PRE_FLOORPLAN_AXIS]
        if not names:
            self.skipTest("no designs")
        real = collect.already_have
        collect.already_have = lambda design: set()
        try:
            for name in names:
                with self.subTest(design=name):
                    self.assertTrue(collect.plan([name]),
                                    f"{name} planned nothing before dedup")
        finally:
            collect.already_have = real

    def test_a_fully_collected_design_plans_nothing(self):
        # The other half of the distinction above: dedup, not the guard,
        # is what empties a finished design. counter4 is in that state.
        names = [p.name for p in collect.DESIGNS.iterdir()
                 if (p / "config.json").exists()
                 and p.name not in collect.SKIP
                 and p.name not in collect.NO_PRE_FLOORPLAN_AXIS]
        if not names:
            self.skipTest("no designs")

        class Everything:
            def __contains__(self, item):
                return True

        real = collect.already_have
        collect.already_have = lambda design: Everything()
        try:
            self.assertEqual(collect.plan([names[0]]), [])
        finally:
            collect.already_have = real

    def test_the_reason_travels_with_the_decision(self):
        # Kept as data, like SKIP, so nobody has to guess later whether
        # the design is broken or merely uninformative here. It is not
        # broken: it has 44 recorded rows from the axis that does move it.
        reason = collect.NO_PRE_FLOORPLAN_AXIS["counter4_tinydie"]
        self.assertIn("DIE_AREA", reason)

    def test_it_is_kept_apart_from_designs_that_produce_nothing(self):
        # SKIP means "no run completes". This means "runs complete, but
        # not along these axes". Merging them would lose that.
        self.assertNotIn("counter4_tinydie", collect.SKIP)


class IncrementalWriteTests(unittest.TestCase):
    """A design's case is written when its last run lands, not at the end.

    The batch used to write everything once, after all of it finished. A
    171-run batch was killed at 104 and wrote nothing; 85 finished runs
    sat on disk unrecorded. Losing one design to an interruption is not
    the same as losing the batch.
    """

    def test_each_design_is_written_as_it_finishes(self):
        seen = []

        def fake_run_one(item):
            return {"design": item["design"], "tag": item["tag"],
                    "overrides": item["overrides"],
                    "verdict": {"area_um2": 1.0, "passed": True}}

        def fake_write_case(design, *a, **kw):
            seen.append(design)
            return collect.REPO_ROOT / "reference-db" / "cases" / f"{design}.json"

        items = [{"design": "a", "tag": "t1", "overrides": {}},
                 {"design": "a", "tag": "t2", "overrides": {}},
                 {"design": "b", "tag": "t3", "overrides": {}}]
        real_plan, real_run, real_write, real_winner = (
            collect.plan, collect.run_one,
            collect.orchestrator.write_case, collect.orchestrator.pick_winner)
        collect.plan = lambda designs: items
        collect.run_one = fake_run_one
        collect.orchestrator.write_case = fake_write_case
        collect.orchestrator.pick_winner = lambda rows: rows[0]
        try:
            got = collect.collect(["a", "b"], parallel=1, limit=None)
        finally:
            (collect.plan, collect.run_one, collect.orchestrator.write_case,
             collect.orchestrator.pick_winner) = (
                real_plan, real_run, real_write, real_winner)

        self.assertEqual(sorted(seen), ["a", "b"])
        self.assertEqual(len(got["cases_written"]), 2)

    def test_a_design_is_written_exactly_once(self):
        # Flushing per result rather than per design would rewrite the
        # same case on every run and fill reference-db with duplicates.
        seen = []

        def fake_run_one(item):
            return {"design": item["design"], "tag": item["tag"],
                    "overrides": item["overrides"],
                    "verdict": {"area_um2": 1.0, "passed": True}}

        items = [{"design": "a", "tag": f"t{i}", "overrides": {}}
                 for i in range(5)]
        real_plan, real_run, real_write, real_winner = (
            collect.plan, collect.run_one,
            collect.orchestrator.write_case, collect.orchestrator.pick_winner)
        collect.plan = lambda designs: items
        collect.run_one = fake_run_one
        collect.orchestrator.write_case = lambda design, *a, **kw: (
            seen.append(design)
            or collect.REPO_ROOT / "reference-db" / "cases" / "a.json")
        collect.orchestrator.pick_winner = lambda rows: rows[0]
        try:
            collect.collect(["a"], parallel=1, limit=None)
        finally:
            (collect.plan, collect.run_one, collect.orchestrator.write_case,
             collect.orchestrator.pick_winner) = (
                real_plan, real_run, real_write, real_winner)
        self.assertEqual(seen, ["a"])


if __name__ == "__main__":
    sys.exit(unittest.main())
