"""Tests for the dashboard's grouping of the case store.

The "기록 — every real run, newest first" list rendered 54 cases as one
flat column ordered by `date`, and two things about that were untrue.

  - It was not newest first. 41 of the 54 cases carry the same date,
    2026-08-30, because the store writes at most one dated file per
    design per day and a re-run appends a timestamped one beside it.
    Sorting on `date` alone left 76% of the list in whatever order
    index.json happened to list, presented as chronology.

  - Its React keys were `${design}__${date}`, which collided up to
    eleven times (cdc_twoclock on 2026-08-30). Colliding keys let React
    carry one card's open/closed state onto a different case when the
    list changes.

Both have the same cause: the timestamp that tells same-day cases apart
lives in the filename and the API never sent it. The server now sends
`file`, and these tests hold it to being enough.

The third thing here is the axis label. Grouping by design alone leaves
counter4 as thirteen indistinguishable rows; what makes them different
is which knob each one swept. That is recoverable from the candidates —
it is the set of override keys whose values actually differ within the
case — so it is derived rather than recorded, and these tests pin the
derivation against the real store.
"""
import json
import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "case_grouping_check.mjs"
CASES = ROOT / "reference-db" / "cases"


def _harness() -> dict:
    try:
        out = subprocess.run(
            ["npx", "tsx", str(HARNESS), str(CASES)],
            cwd=ROOT / "dashboard", capture_output=True, text=True,
            encoding="utf-8", timeout=300,
            # npx is npx.cmd on Windows, which subprocess can't exec
            # directly without a shell -- shell=True there actually runs
            # it instead of just being a more elaborate way to skip.
            shell=(os.name == "nt"))
    except FileNotFoundError as e:
        # Belt and suspenders: still the right verdict (toolchain
        # absent, not code-under-test broken) if shell=True's own
        # resolution ever fails too.
        raise unittest.SkipTest(f"npx unavailable: {e}")
    if out.returncode == 0:
        return json.loads(out.stdout)
    # Skip only when the toolchain is absent, never when the code under
    # test is. Skipping on any non-zero exit is what a missing module
    # looks like too, and this suite reported OK (skipped=3) while
    # caseGrouping.ts did not exist at all.
    if "tsx" in out.stderr and "not found" in out.stderr.lower():
        raise unittest.SkipTest(f"tsx unavailable: {out.stderr[-300:]}")
    raise AssertionError(f"harness failed:\n{out.stderr[-1500:]}")


def _case(name: str) -> dict:
    return json.loads((CASES / name).read_text(encoding="utf-8"))


class RecordedAtTests(unittest.TestCase):
    """The ordering key, against the real store."""

    @classmethod
    def setUpClass(cls):
        cls.got = _harness()

    def test_every_case_gets_a_distinct_key(self):
        # The property the React key needs and `${design}__${date}` did
        # not have. Asserted over the whole store rather than over the
        # known collisions, so a new same-second case fails here rather
        # than in a rendering nobody is watching.
        keys = [f"{name.split('__')[0]}::{v['recordedAt']}"
                for name, v in self.got["perCase"].items()]
        self.assertEqual(len(keys), len(set(keys)), "recordedAt collided")

    def test_a_timestamped_file_keeps_its_time(self):
        got = self.got["perCase"]["spm__2026-08-30__235231.json"]["recordedAt"]
        self.assertEqual(got, "2026-08-30T23:52:31")

    def test_a_dated_file_sorts_to_the_start_of_its_day(self):
        # These predate the timestamped naming, so there is no time to
        # read. Midnight is a choice, not a measurement: it is stable
        # across machines, unlike mtime, and it puts the file the batch
        # wrote first before the re-runs that followed it that day.
        got = self.got["perCase"]["counter4__2026-08-21.json"]["recordedAt"]
        self.assertEqual(got, "2026-08-21T00:00:00")

    def test_the_newest_case_in_the_store_sorts_first(self):
        first = self.got["groups"][0]["files"][0]
        newest = max(
            self.got["perCase"].items(), key=lambda kv: kv[1]["recordedAt"])
        # Groups are ordered by their own newest case, so the very first
        # row on screen is the newest run in the store.
        self.assertEqual(first, newest[0])

    def test_each_group_is_ordered_newest_first(self):
        at = {k: v["recordedAt"] for k, v in self.got["perCase"].items()}
        for group in self.got["groups"]:
            stamps = [at[f] for f in group["files"]]
            self.assertEqual(stamps, sorted(stamps, reverse=True),
                             f"{group['design']} is not newest first")


class SweptAxisTests(unittest.TestCase):
    """What distinguishes one case from another of the same design."""

    @classmethod
    def setUpClass(cls):
        cls.got = _harness()["perCase"]

    def test_a_single_knob_sweep_names_that_knob(self):
        self.assertEqual(
            self.got["counter4__2026-08-21.json"]["axis"], ["FP_CORE_UTIL"])

    def test_a_two_knob_sweep_names_both(self):
        self.assertEqual(
            sorted(self.got["counter4__2026-08-30__004913.json"]["axis"]),
            ["CLOCK_PERIOD", "PL_TARGET_DENSITY_PCT"])

    def test_the_technology_is_an_axis_though_it_is_not_an_override(self):
        # PDK and SCL are recorded beside `overrides`, not inside it,
        # because OpenLane takes them as flags. They are still the thing
        # the cross-product varied, and a label that omitted them would
        # call a 36-run PDK sweep "합성전략" and nothing else.
        self.assertEqual(
            sorted(self.got["counter4__2026-08-30__041801.json"]["axis"]),
            ["PDK", "PNR_EXCLUDED_CELL_FILE", "SCL", "SYNTH_STRATEGY"])

    def test_a_constant_knob_is_not_an_axis(self):
        # Every candidate in this case declares SYNTH_STRATEGY; only
        # three values of it were tried and nothing else moved. Reporting
        # every key present rather than every key that varies was the
        # first attempt, and it labelled this case with knobs it held
        # fixed.
        self.assertEqual(
            self.got["counter4__2026-08-30__045617.json"]["axis"],
            ["SYNTH_STRATEGY"])

    def test_a_case_with_one_candidate_has_no_axis(self):
        # A single run varies nothing. An empty axis is the truthful
        # answer; naming its lone configuration would read as a sweep.
        self.assertEqual(self.got["aes__2026-08-30.json"]["axis"], [])

    def test_the_axis_is_derived_from_what_the_case_records(self):
        # Guards the derivation against drift: recompute it here from
        # the JSON, independently of the TypeScript, for every case.
        for name, value in self.got.items():
            case = _case(name)
            seen: dict[str, set[str]] = {}
            rows = [r for it in case.get("iterations", [])
                    for r in it.get("results", [])]
            for row in rows:
                knobs = dict(row.get("overrides") or {})
                for field in ("pdk", "scl"):
                    if row.get(field):
                        knobs[field.upper()] = row[field]
                for key, val in knobs.items():
                    seen.setdefault(key, set()).add(json.dumps(val))
            expected = sorted(k for k, v in seen.items() if len(v) > 1)
            self.assertEqual(sorted(value["axis"]), expected, name)


class GroupTests(unittest.TestCase):
    """The grouping itself."""

    @classmethod
    def setUpClass(cls):
        cls.got = _harness()

    def test_every_case_lands_in_exactly_one_group(self):
        placed = [f for g in self.got["groups"] for f in g["files"]]
        self.assertEqual(len(placed), self.got["total"])
        self.assertEqual(len(set(placed)), self.got["total"])

    def test_a_group_holds_one_design(self):
        for group in self.got["groups"]:
            for name in group["files"]:
                self.assertTrue(name.startswith(group["design"] + "__"), name)

    def test_the_passed_count_matches_the_cases_in_the_group(self):
        for group in self.got["groups"]:
            expected = sum(1 for f in group["files"]
                           if _case(f).get("outcome") == "passed")
            self.assertEqual(group["passed"], expected, group["design"])

    def test_groups_are_ordered_by_their_newest_case(self):
        at = {k: v["recordedAt"] for k, v in self.got["perCase"].items()}
        newest = [max(at[f] for f in g["files"]) for g in self.got["groups"]]
        self.assertEqual(newest, sorted(newest, reverse=True))


if __name__ == "__main__":
    unittest.main()
