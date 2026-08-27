"""Tests for pipeline/step_coverage.py.

A "successful" Classic run of counter4 creates 74 step directories while
the flow declares 78. No log line says which four were dropped, the flow
reports success, and metrics.json has no key for a check that never
happened — so the gap is invisible in exactly the way an absent DRC
metric was.

One of the four is Yosys.EQY, a formal equivalence check.

The distinction these tests protect is between a *signoff* step that did
not run (a claim the pipeline would otherwise make without evidence) and
an *optimisation* step that did not run (a slower design, not an
unverified one). Reporting both the same way would bury the first under
the second.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from step_coverage import (  # noqa: E402
    GATING_VARS, check, executed_steps, normalize, read_gating,
    unverified_steps,
)

# The four Classic really drops on a clean counter4 run, with the RUN_*
# values its own resolved.json recorded.
DECLARED = [
    "Verilator.Lint", "Yosys.Synthesis", "OpenROAD.CTS",
    "OpenROAD.DetailedRouting", "OpenROAD.RepairDesignPostGRT",
    "Odb.HeuristicDiodeInsertion", "OpenROAD.ResizerTimingPostGRT",
    "Magic.DRC", "KLayout.DRC", "Netgen.LVS", "Yosys.EQY",
]
RAN = [
    "01-verilator-lint", "06-yosys-synthesis", "34-openroad-cts",
    "43-openroad-detailedrouting", "65-magic-drc", "66-klayout-drc",
    "71-netgen-lvs",
]
RESOLVED = {
    "RUN_EQY": False,
    "RUN_POST_GRT_DESIGN_REPAIR": False,
    "RUN_POST_GRT_RESIZER_TIMING": False,
    "RUN_HEURISTIC_DIODE_INSERTION": False,
    "RUN_LVS": True,
    "DESIGN_NAME": "counter4",
}


def make_run(ran=RAN, resolved=RESOLVED) -> Path:
    d = Path(tempfile.mkdtemp())
    for name in ran:
        (d / name).mkdir()
    (d / "tmp").mkdir()  # not a step directory
    if resolved is not None:
        (d / "resolved.json").write_text(json.dumps(resolved))
    return d


class NormalizeTests(unittest.TestCase):
    def test_flow_id_and_directory_spelling_match(self):
        # "Yosys.EQY" vs "70-yosys-eqy" — the run directory is lowercase
        # and dot-free, so comparison has to be normalized or every step
        # reads as missing.
        self.assertEqual(normalize("Yosys.EQY"), normalize("yosys-eqy"))
        self.assertEqual(normalize("OpenROAD.CTS"), normalize("openroad-cts"))

    def test_distinct_steps_stay_distinct(self):
        self.assertNotEqual(normalize("Magic.DRC"), normalize("KLayout.DRC"))


class ExecutedStepTests(unittest.TestCase):
    def test_reads_numbered_directories_in_order(self):
        got = executed_steps(make_run())
        self.assertEqual([n for n, _ in got], [1, 6, 34, 43, 65, 66, 71])

    def test_ignores_non_step_directories(self):
        names = [name for _, name in executed_steps(make_run())]
        self.assertNotIn("tmp", names)

    def test_missing_run_dir_is_empty_not_an_error(self):
        self.assertEqual(executed_steps(Path("/nonexistent/run")), [])


class GatingTests(unittest.TestCase):
    def test_reads_the_runs_own_switches(self):
        got = read_gating(make_run())
        self.assertIs(got["RUN_EQY"], False)
        self.assertIs(got["RUN_LVS"], True)

    def test_ignores_non_run_keys(self):
        self.assertNotIn("DESIGN_NAME", read_gating(make_run()))

    def test_absent_resolved_json_is_empty(self):
        self.assertEqual(read_gating(make_run(resolved=None)), {})


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.result = check(make_run(), DECLARED)

    def test_counts_declared_against_executed(self):
        self.assertEqual(self.result["declared"], 11)
        self.assertEqual(self.result["executed"], 7)

    def test_names_every_missing_step(self):
        missing = {m["step"] for m in self.result["missing"]}
        self.assertEqual(missing, {
            "OpenROAD.RepairDesignPostGRT", "Odb.HeuristicDiodeInsertion",
            "OpenROAD.ResizerTimingPostGRT", "Yosys.EQY",
        })

    def test_attributes_each_miss_to_its_switch(self):
        by_step = {m["step"]: m for m in self.result["missing"]}
        self.assertEqual(by_step["Yosys.EQY"]["gated_by"], "RUN_EQY")
        self.assertIs(by_step["Yosys.EQY"]["value"], False)

    def test_only_signoff_steps_are_flagged_as_signoff(self):
        # The other three are optimisation: their absence makes a design
        # slower, not unverified.
        self.assertEqual(self.result["missing_signoff"], ["Yosys.EQY"])

    def test_a_complete_run_reports_nothing_missing(self):
        run = make_run(ran=[f"{i:02d}-{s.lower().replace('.', '-')}"
                            for i, s in enumerate(DECLARED, 1)])
        got = check(run, DECLARED)
        self.assertEqual(got["missing"], [])
        self.assertEqual(unverified_steps(got), [])

    def test_unattributable_miss_reports_no_switch_rather_than_guessing(self):
        got = check(make_run(), DECLARED + ["Some.UnknownStep"])
        unknown = next(m for m in got["missing"] if m["step"] == "Some.UnknownStep")
        self.assertIsNone(unknown["gated_by"])
        self.assertIsNone(unknown["value"])


class UnverifiedTests(unittest.TestCase):
    def test_reports_only_the_signoff_gap(self):
        got = unverified_steps(check(make_run(), DECLARED))
        self.assertEqual(len(got), 1)
        self.assertIn("Yosys.EQY", got[0])

    def test_names_the_switch_in_the_message(self):
        got = unverified_steps(check(make_run(), DECLARED))[0]
        self.assertIn("RUN_EQY=False", got)

    def test_optimisation_steps_are_not_reported_as_unverified(self):
        # Burying one real signoff gap under three optimisation notes is
        # how a warning stops being read.
        got = unverified_steps(check(make_run(), DECLARED))
        self.assertFalse(any("ResizerTimingPostGRT" in g for g in got))


class GatingTableTests(unittest.TestCase):
    def test_every_signoff_step_has_a_known_switch(self):
        from step_coverage import SIGNOFF_STEPS
        for step in SIGNOFF_STEPS:
            self.assertIn(step, GATING_VARS, step)


if __name__ == "__main__":
    unittest.main()
