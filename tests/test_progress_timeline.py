"""Tests for the two curves the progress page draws.

The page answers "are the attempts making this better?", and the first
honest answer the store gives is that the obvious chart lies. Plotting
recorded area against time shows counter4 going 290um2 -> 1029um2, which
looks like a bad regression and is not one: the later runs are on
gf180mcu, a 5-metal process whose cells are simply larger than sky130's
6-metal. Nothing got worse. The axis changed underneath the line.

So there are two curves and neither is that one.

  - Coverage. How much of the space has been measured: distinct
    configurations, designs, and technologies, cumulative over time.
    This is where the real growth is — 40 samples on 2026-08-29 and 400
    the next day — and it is monotone by construction, which is honest
    because a measurement once taken is not untaken.

  - Frontier. The best area found so far, per design AND technology, so
    the comparison is between runs that are actually comparable. The
    improvements it shows are small (0.9% on counter4's 46 sky130-hd
    attempts), and the page has to show them at that size rather than
    scale them into looking like more.

Both are derived from the case store at read time. Nothing is recorded
to support this page.
"""
import json
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "progress_timeline_check.mjs"
CASES = ROOT / "reference-db" / "cases"

STAMP = re.compile(r"__(\d{4}-\d{2}-\d{2})(?:__(\d{6}))?\.json$")

# What a row recorded before the technology became a candidate axis ran
# on. Read from surrogate.py rather than repeated here: the dashboard
# has its own copy of these two strings, and the point of reading them
# from the pipeline is that the test fails if the two ever disagree
# about what an unlabelled row means.
_surrogate = (ROOT / "pipeline" / "surrogate.py").read_text()
DEFAULT_SCL = re.search(r'^DEFAULT_SCL = "([^"]+)"', _surrogate, re.M).group(1)
DEFAULT_PDK = re.search(r'^DEFAULT_PDK = "([^"]+)"', _surrogate, re.M).group(1)


def _harness() -> dict:
    out = subprocess.run(
        ["npx", "tsx", str(HARNESS), str(CASES)],
        cwd=ROOT / "dashboard", capture_output=True, text=True, timeout=300)
    if out.returncode == 0:
        return json.loads(out.stdout)
    # Skip only when the toolchain is absent, never when the code under
    # test is — a missing module also exits non-zero.
    if "tsx" in out.stderr and "not found" in out.stderr.lower():
        raise unittest.SkipTest(f"tsx unavailable: {out.stderr[-300:]}")
    raise AssertionError(f"harness failed:\n{out.stderr[-1500:]}")


def _rows() -> list[dict]:
    """Every recorded candidate, with the time its case was written."""
    out = []
    for path in sorted(CASES.glob("*.json")):
        stamp = STAMP.search(path.name)
        at = f"{stamp.group(1)}T{stamp.group(2) or '000000'}"
        case = json.loads(path.read_text())
        for iteration in case.get("iterations", []):
            for result in iteration.get("results", []):
                out.append({
                    "at": at,
                    "design": case.get("design"),
                    "overrides": result.get("overrides") or {},
                    "scl": result.get("scl") or DEFAULT_SCL,
                    "pdk": result.get("pdk") or DEFAULT_PDK,
                    "verdict": result.get("verdict") or {},
                })
    return out


class CoverageTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.curve = _harness()["coverage"]

    def test_the_curve_is_in_time_order(self):
        stamps = [p["at"] for p in self.curve]
        self.assertEqual(stamps, sorted(stamps))

    def test_nothing_ever_decreases(self):
        # A measurement once taken is not untaken. If any of these fall,
        # the curve is counting something other than what it says.
        for field in ("samples", "designs", "technologies"):
            values = [p[field] for p in self.curve]
            self.assertEqual(values, sorted(values), field)

    def test_it_ends_at_the_whole_store(self):
        rows = _rows()
        samples = {(r["design"], json.dumps(r["overrides"], sort_keys=True),
                    r["scl"], r["pdk"]) for r in rows}
        self.assertEqual(self.curve[-1]["samples"], len(samples))
        self.assertEqual(self.curve[-1]["designs"],
                         len({r["design"] for r in rows}))

    def test_a_repeated_configuration_does_not_count_twice(self):
        # 441 recorded rows, 400 distinct configurations. Counting rows
        # would show 10% growth that is re-measurement, not coverage.
        self.assertLess(self.curve[-1]["samples"], len(_rows()))

    def test_the_day_the_store_grew_is_visible(self):
        # The shape, not the totals. This pinned 400 for 2026-08-30 and
        # broke the next time the pipeline ran — the store is append-only
        # and a test that fails when it grows is a test that punishes the
        # thing the page exists to show. 40 on 2026-08-29 is fixed
        # because that day is finished; the last day is only required to
        # be much larger, which is the claim being made.
        by_day: dict[str, int] = {}
        for point in self.curve:
            by_day[point["at"][:10]] = point["samples"]
        self.assertEqual(by_day["2026-08-29"], 40)
        self.assertGreaterEqual(by_day["2026-08-30"], 400)
        self.assertEqual(by_day["2026-08-30"], self.curve[-1]["samples"])


class FrontierTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.frontiers = _harness()["frontiers"]

    def test_a_frontier_never_rises(self):
        # It is the best found SO FAR. A rise would mean it is plotting
        # each run's own best, which is the chart that reads as a
        # regression whenever a harder configuration is tried.
        for series in self.frontiers:
            areas = [p["area"] for p in series["points"]]
            self.assertEqual(areas, sorted(areas, reverse=True),
                             f"{series['design']} {series['scl']}")

    def test_each_series_holds_one_technology(self):
        # The whole point. counter4 on sky130-hd settles at 287.8um2 and
        # on gf180 at 1029.55um2; one series spanning both would show a
        # 3.5x "regression" caused by nothing the agent did.
        keys = {(s["design"], s["scl"], s["pdk"]) for s in self.frontiers}
        self.assertEqual(len(keys), len(self.frontiers))

    def test_only_passing_candidates_set_the_frontier(self):
        # A candidate that failed signoff has an area, and it is often
        # the smallest one — being too small is why it failed. Letting it
        # set the record would draw a frontier nothing achieved.
        best: dict[tuple, float] = {}
        for row in _rows():
            verdict = row["verdict"]
            if not verdict.get("passed") or verdict.get("area_um2") is None:
                continue
            key = (row["design"], row["scl"], row["pdk"])
            area = verdict["area_um2"]
            best[key] = min(best.get(key, area), area)
        for series in self.frontiers:
            key = (series["design"], series["scl"], series["pdk"])
            self.assertAlmostEqual(series["points"][-1]["area"], best[key], 3,
                                   str(key))

    def test_the_improvements_are_reported_at_their_real_size(self):
        # Guards the number the page puts next to each series. counter4
        # on sky130-hd moved 290.278 -> 287.776 across 46 attempts, which
        # is 0.86% — small, and the page says so rather than rounding it
        # into a story.
        series = next(s for s in self.frontiers
                      if s["design"] == "counter4"
                      and s["scl"] == "sky130_fd_sc_hd")
        first, last = series["points"][0]["area"], series["points"][-1]["area"]
        self.assertAlmostEqual(first, 290.278, 3)
        self.assertAlmostEqual(last, 287.776, 3)
        self.assertAlmostEqual(series["improvedPct"],
                               (first - last) / first * 100, 6)

    def test_a_search_that_improved_inside_one_case_still_reports_it(self):
        # The bug this pins. Points sharing a timestamp were collapsed
        # into one, keeping the newest area — which overwrote the value
        # the series STARTED at. Any search whose improvements all landed
        # in a single case then read first == last and reported 0%.
        #
        # spm on sky130-hd is exactly that: six records, 3757.35 down to
        # 3618.47, every one of them inside the 2026-08-29 case. The page
        # showed it as "no improvement yet" while the store held a 3.7%
        # one. Collapsing is fine for the coverage curve, where the last
        # value at an instant IS the total; it is wrong here, where the
        # first value is half the measurement.
        series = next(s for s in self.frontiers
                      if s["design"] == "spm" and s["scl"] == "sky130_fd_sc_hd")
        self.assertAlmostEqual(series["points"][0]["area"], 3757.35, 2)
        self.assertAlmostEqual(series["points"][-1]["area"], 3618.47, 2)
        self.assertGreater(series["improvedPct"], 3.0)

    def test_the_first_point_is_the_first_record_not_the_first_instant(self):
        # The general form of the above, over every series: a frontier
        # starts where its first passing candidate landed.
        best_first: dict[tuple, float] = {}
        for row in _rows():
            verdict = row["verdict"]
            if not verdict.get("passed") or verdict.get("area_um2") is None:
                continue
            key = (row["design"], row["scl"], row["pdk"])
            if key not in best_first:
                best_first[key] = verdict["area_um2"]
        for series in self.frontiers:
            key = (series["design"], series["scl"], series["pdk"])
            self.assertAlmostEqual(series["points"][0]["area"], best_first[key], 3,
                                   str(key))

    def test_attempts_counts_every_comparable_run_not_just_the_records(self):
        # 46 attempts produced 2 records. Showing only the records would
        # make a long search look like a short one.
        series = next(s for s in self.frontiers
                      if s["design"] == "counter4"
                      and s["scl"] == "sky130_fd_sc_hd")
        self.assertGreater(series["attempts"], len(series["points"]))


if __name__ == "__main__":
    unittest.main()
