"""Tests for recording runs that finished but were never collected.

collect.py writes its case files once, after the whole batch finishes.
An interrupted batch therefore loses every completed run in it — which
happened: a 171-run batch was killed at 104 and a second at 2, and the
store stayed at 219 samples while 85 complete run directories sat on
disk with nobody to read them.

The runs were never the fragile part. OpenLane writes each one into
`<design>/runs/<tag>/` as it goes, with the resolved config it used and
its own metrics.json. Recovery reads those.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import recover_runs  # noqa: E402


def fake_design(tmp: Path, name: str, declared: dict,
                runs: dict[str, tuple[dict, bool]]) -> Path:
    """A design tree; each run maps to (resolved config, completed)."""
    d = tmp / name
    d.mkdir(parents=True)
    (d / "config.json").write_text(json.dumps(declared))
    for tag, (resolved, complete) in runs.items():
        r = d / "runs" / tag
        r.mkdir(parents=True)
        (r / "resolved.json").write_text(json.dumps(resolved))
        if complete:
            (r / "final").mkdir()
            (r / "final" / "metrics.json").write_text(
                json.dumps({"design__instance__area": 100.0}))
    return d


class CompletenessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.real = recover_runs.DESIGNS
        recover_runs.DESIGNS = self.tmp

    def tearDown(self):
        recover_runs.DESIGNS = self.real

    def test_a_finished_run_is_recoverable(self):
        fake_design(self.tmp, "d", {}, {"t": ({"PDK": "sky130A"}, True)})
        self.assertEqual(len(recover_runs.recoverable("d")), 1)

    def test_a_crashed_run_is_not(self):
        # The distinction that matters most here. A crashed run has
        # per-step metrics but no final ones, and recovering it as
        # though it completed would turn a crash into a result.
        fake_design(self.tmp, "d", {}, {"t": ({"PDK": "sky130A"}, False)})
        self.assertEqual(recover_runs.recoverable("d"), [])

    def test_a_run_without_a_resolved_config_is_not(self):
        # Nothing can be said about what it was configured to do.
        fake_design(self.tmp, "d", {}, {"t": ({}, True)})
        (self.tmp / "d" / "runs" / "t" / "resolved.json").unlink()
        self.assertEqual(recover_runs.recoverable("d"), [])

    def test_a_design_that_never_ran_is_empty_not_an_error(self):
        self.assertEqual(recover_runs.recoverable("never_existed"), [])


class OverrideReconstructionTests(unittest.TestCase):
    """`overrides` must come out as the collector would have written it.

    resolved.json holds the full config — several hundred keys, almost
    all PDK defaults nobody chose. Recording that as overrides would make
    every recovered row unique, so the same configuration would be
    counted twice and every accuracy figure built on it inflated.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.real = recover_runs.DESIGNS
        recover_runs.DESIGNS = self.tmp
        fake_design(self.tmp, "d", {"CLOCK_PERIOD": 10, "FP_CORE_UTIL": 35}, {})

    def tearDown(self):
        recover_runs.DESIGNS = self.real

    def test_only_the_difference_from_the_declared_config_is_an_override(self):
        got = recover_runs.overrides_from(
            {"CLOCK_PERIOD": 10, "FP_CORE_UTIL": 35,
             "SYNTH_STRATEGY": "AREA 0"}, "d")
        self.assertEqual(got, {"SYNTH_STRATEGY": "AREA 0"})

    def test_a_changed_declared_value_is_an_override(self):
        got = recover_runs.overrides_from(
            {"CLOCK_PERIOD": 4, "FP_CORE_UTIL": 35}, "d")
        self.assertEqual(got, {"CLOCK_PERIOD": 4})

    def test_the_hundreds_of_pdk_defaults_are_not_overrides(self):
        # The negative control for the whole reconstruction: a resolved
        # config stuffed with keys no candidate chose must yield nothing.
        noise = {f"SOME_PDK_VAR_{i}": i for i in range(300)}
        noise.update({"CLOCK_PERIOD": 10, "FP_CORE_UTIL": 35})
        self.assertEqual(recover_runs.overrides_from(noise, "d"), {})

    def test_a_pdk_resolved_cell_file_is_not_an_override(self):
        # OpenLane resolves PNR_EXCLUDED_CELL_FILE to an absolute /pdk
        # path even when nobody passed one. The collector passes a
        # /design-relative path, so only that form is a real override.
        self.assertEqual(
            recover_runs.overrides_from(
                {"PNR_EXCLUDED_CELL_FILE": "/pdk/volare/x/drc_exclude.cells"},
                "d"), {})
        self.assertEqual(
            recover_runs.overrides_from(
                {"PNR_EXCLUDED_CELL_FILE": "/design/pnr/hs_exclude.cells"},
                "d"), {"PNR_EXCLUDED_CELL_FILE": "/design/pnr/hs_exclude.cells"})


class ConfigurationSourceTests(unittest.TestCase):
    """The configuration is read from the run, not from its tag.

    A tag is a name we chose; resolved.json is what OpenLane used. They
    have disagreed before — a tag built from an override OpenLane
    silently ignored looks exactly like one that worked.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.real = recover_runs.DESIGNS
        recover_runs.DESIGNS = self.tmp

    def tearDown(self):
        recover_runs.DESIGNS = self.real

    def test_library_and_pdk_come_from_the_resolved_config(self):
        fake_design(self.tmp, "d", {}, {"c-hs-whatever": (
            {"PDK": "gf180mcuD", "STD_CELL_LIBRARY": "gf180mcu_fd_sc_mcu7t5v0"},
            True)})
        got = recover_runs.recoverable("d")[0]
        self.assertEqual(got["pdk"], "gf180mcuD")
        self.assertEqual(got["scl"], "gf180mcu_fd_sc_mcu7t5v0")

    def test_the_tag_does_not_decide_the_technology(self):
        # The tag says hs; the run used gf180. The run wins.
        fake_design(self.tmp, "d", {}, {"c-hs-x": (
            {"PDK": "gf180mcuD", "STD_CELL_LIBRARY": "gf180mcu_fd_sc_mcu7t5v0"},
            True)})
        got = recover_runs.recoverable("d")[0]
        self.assertNotIn("sky130", str(got["scl"]))


class ScoringPathTests(unittest.TestCase):
    def test_recovery_uses_the_collectors_own_scoring_function(self):
        # The property that keeps a recovered row and a collected row the
        # same row. A second scoring path that drifted from the first
        # would be worse than losing the runs, because the disagreement
        # would be invisible.
        import inspect

        import orchestrator
        src = inspect.getsource(recover_runs.recover)
        self.assertIn("orchestrator.score_run_dir", src)
        self.assertTrue(callable(orchestrator.score_run_dir))

    def test_run_candidate_uses_the_same_function(self):
        import inspect

        import orchestrator
        src = inspect.getsource(orchestrator.run_candidate)
        self.assertIn("score_run_dir", src)


if __name__ == "__main__":
    sys.exit(unittest.main())
