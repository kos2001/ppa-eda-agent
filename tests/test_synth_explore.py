"""Tests for pipeline/synth_explore.py.

The value of this module is that it replaces nine full OpenLane flows
(~10 min) with one 9-second exploration plus three chosen runs. That is
only sound if the choosing is right — picking the wrong strategies would
mean paying for full flows on candidates the exploration already showed
were worse.

Results are read from each strategy's own state_out.json rather than
scraped from the terminal table, so these tests exercise that parsing
against the real on-disk shape.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from synth_explore import (  # noqa: E402
    SynthExploreError, rank, read_results, strategy_name, suggest_candidates,
)

# Real numbers from a counter4 exploration.
REAL = [
    ("1-sta-area-0", 14, 171.4144, 6.570217889046306),
    ("1-sta-area-3", 24, 255.2448, 6.482785158849049),
    ("1-sta-delay-0", 16, 203.9456, 6.6752525396629965),
    ("1-sta-delay-3", 16, 195.1872, 6.676214436918736),
    ("1-sta-delay-4", 19, 242.7328, 6.154137817983149),
]


def make_run(rows=REAL, extra_dirs=()) -> Path:
    d = Path(tempfile.mkdtemp())
    for name, gates, area, ws in rows:
        step = d / name
        step.mkdir()
        (step / "state_out.json").write_text(json.dumps({"metrics": {
            "design__instance__count": gates,
            "design__instance__area": area,
            "timing__setup__ws": ws,
            "timing__setup__tns": 0,
        }}))
    for name in extra_dirs:
        (d / name).mkdir()
    return d


class StrategyNameTests(unittest.TestCase):
    def test_matches_synth_strategy_spelling(self):
        self.assertEqual(strategy_name("1-sta-delay-3"), "DELAY 3")
        self.assertEqual(strategy_name("1-sta-area-0"), "AREA 0")

    def test_ignores_the_sdc_and_other_directories(self):
        # Only the STA directories carry timing metrics; the SDC ones are
        # inputs and would double every strategy if counted.
        for name in ("1-sdc-area-0", "final", "tmp", "01-verilator-lint"):
            self.assertIsNone(strategy_name(name), name)


class ReadResultsTests(unittest.TestCase):
    def test_one_row_per_strategy(self):
        got = read_results(make_run())
        self.assertEqual([r["strategy"] for r in got],
                         ["AREA 0", "AREA 3", "DELAY 0", "DELAY 3", "DELAY 4"])

    def test_reads_the_real_metric_values(self):
        got = {r["strategy"]: r for r in read_results(make_run())}
        self.assertEqual(got["AREA 0"]["gates"], 14)
        self.assertAlmostEqual(got["AREA 0"]["area_um2"], 171.4144)
        self.assertAlmostEqual(got["DELAY 3"]["setup_ws_ns"], 6.676214436918736)

    def test_fmax_derived_from_the_clock_period(self):
        got = {r["strategy"]: r for r in read_results(make_run(), 10.0)}
        # 10 ns constraint, 6.57 ns slack -> 3.43 ns path -> ~291.6 MHz
        self.assertAlmostEqual(got["AREA 0"]["fmax_mhz"], 1000 / (10 - 6.570217889046306),
                               places=2)

    def test_no_fmax_without_a_period(self):
        # A fabricated period would produce a frequency indistinguishable
        # from a measured one.
        self.assertIsNone(read_results(make_run())[0]["fmax_mhz"])

    def test_sdc_directories_do_not_become_strategies(self):
        got = read_results(make_run(extra_dirs=("1-sdc-area-0", "1-sdc-delay-3")))
        self.assertEqual(len(got), len(REAL))

    def test_missing_directory_raises(self):
        with self.assertRaises(SynthExploreError):
            read_results(Path("/nonexistent/run"))

    def test_a_run_with_no_strategy_results_raises(self):
        # Silently returning [] would read as "no strategy is any good".
        with self.assertRaises(SynthExploreError):
            read_results(make_run(rows=[], extra_dirs=("final",)))


class RankTests(unittest.TestCase):
    def setUp(self):
        self.results = read_results(make_run(), 10.0)

    def test_area_and_slack_disagree(self):
        # The whole reason to explore rather than assume: the smallest
        # design is not the fastest one.
        self.assertEqual(rank(self.results, "area")[0]["strategy"], "AREA 0")
        self.assertEqual(rank(self.results, "fmax")[0]["strategy"], "DELAY 3")

    def test_unknown_objective_raises(self):
        with self.assertRaises(SynthExploreError):
            rank(self.results, "power")


class SuggestTests(unittest.TestCase):
    def setUp(self):
        self.results = read_results(make_run(), 10.0)

    def test_picks_both_ends_of_the_tradeoff(self):
        picks = suggest_candidates(self.results, 3)
        chosen = {p["overrides"]["SYNTH_STRATEGY"] for p in picks}
        self.assertIn("AREA 0", chosen)
        self.assertIn("DELAY 3", chosen)

    def test_respects_the_count(self):
        self.assertEqual(len(suggest_candidates(self.results, 2)), 2)

    def test_no_duplicate_strategies(self):
        picks = suggest_candidates(self.results, 5)
        tags = [p["overrides"]["SYNTH_STRATEGY"] for p in picks]
        self.assertEqual(len(tags), len(set(tags)))

    def test_every_pick_records_why(self):
        # A candidate that cost a full flow should say what earned it.
        for p in suggest_candidates(self.results, 3):
            self.assertTrue(p["why"])
            self.assertIn("exploration", p["why"])

    def test_tags_are_safe_run_directory_names(self):
        # Tags become OpenLane --run-tag values and real directory names;
        # a space breaks the run.
        for p in suggest_candidates(self.results, 3):
            self.assertNotIn(" ", p["tag"])

    def test_carries_the_explored_numbers_with_the_pick(self):
        p = suggest_candidates(self.results, 1)[0]
        self.assertIn("area_um2", p["explored"])


if __name__ == "__main__":
    unittest.main()
