import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import graph_loop


FANOUT = "1 max-fanout (DRV) violation(s)"
ANTENNA = "1 routing antenna violation(s)"


def result(tag, overrides, violations=(), passed=False):
    return {"tag": tag, "overrides": overrides,
            "pdk": "sky130A", "scl": "sky130_fd_sc_hd",
            "verdict": {"passed": passed, "violations": list(violations),
                        "unverified": []}}


def case(design, rows, source, date="2026-01-01"):
    return {"design": design, "date": date, "_file": source,
            "toolchain": {"openlane_image": "openlane2:test"},
            "iterations": [{"iteration": 1, "results": rows}]}


class GraphLoopTests(unittest.TestCase):
    def test_windows_drive_path_has_a_shell_free_wsl_translation(self):
        translated = graph_loop._wsl_path(Path(r"C:\work\한글\design"), None)
        self.assertEqual(translated, "/mnt/c/work/한글/design")

    def corpus(self):
        # A same-design, same-technology controlled pair: K=1 closed the
        # fanout violation that the current run still has.
        return [
            case("dut", [
                result("bad", {"K": 0}, [FANOUT, ANTENNA]),
                result("better", {"K": 1}, [ANTENNA]),
            ], "old.json"),
            case("dut", [
                result("now", {}, [FANOUT])
            ], "dut.json", date="2026-01-02"),
        ]


    def test_dry_run_never_calls_executor(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("executor called during planning")
        answer = graph_loop.run_loop(Path("dut"), {"design_name": "dut"},
                                     self.corpus(), execute=False, runner=forbidden)
        self.assertFalse(answer["executed"])
        self.assertEqual(answer["stop_reason"], "candidates_proposed")
        self.assertEqual(answer["next_candidates"][0]["overrides"], {"K": 1})

    def test_measured_result_is_fed_back_and_checkpointed(self):
        calls = []
        def runner(design_dir, spec, max_parallel=1):
            calls.append(spec["candidates"])
            return [{**result(spec["candidates"][0]["tag"], {"K": 1}, passed=True),
                     "parent": spec["candidates"][0]["parent"]}]
        checkpoint = ROOT / "tests" / "graph_loop_checkpoint.test.json"
        try:
            answer = graph_loop.run_loop(
                Path("dut"), {"design_name": "dut"}, self.corpus(),
                execute=True, runner=runner, checkpoint=checkpoint)
            self.assertEqual(answer["stop_reason"], "winner_found")
            self.assertEqual(len(calls), 1)
            self.assertTrue(checkpoint.exists())
            measured = answer["iterations"][0]["results"][0]
            self.assertTrue(measured["produced_by_feedback"])
            self.assertEqual(measured["graph_action"], {"key": "K", "value": 1})
            self.assertEqual(measured["parent"], {"source": "dut.json", "tag": "now"})
        finally:
            checkpoint.unlink(missing_ok=True)

    def test_tags_are_unique_to_the_session(self):
        tags = []
        def runner(design_dir, spec, max_parallel=1):
            tags.extend(c["tag"] for c in spec["candidates"])
            return [result(c["tag"], c["overrides"], [FANOUT]) for c in spec["candidates"]]
        with tempfile.TemporaryDirectory() as directory:
            for _ in range(2):
                graph_loop.run_loop(Path(directory), {"design_name": "dut"},
                                    self.corpus(), execute=True, runner=runner,
                                    max_rounds=1)
        self.assertEqual(len(tags), 2)
        self.assertEqual(len(set(tags)), 2, tags)
        self.assertTrue(all(tag.startswith("graph-") and tag.endswith("-r1-1-k")
                            for tag in tags), tags)

    def test_an_existing_run_directory_is_never_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "runs" / "graph-s1-r1-1-k").mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "already exists"):
                graph_loop.run_loop(Path(directory), {"design_name": "dut"},
                                    self.corpus(), execute=True, session_id="s1",
                                    runner=lambda *a, **k: self.fail("executed"))

    def test_the_run_spec_evaluation_budget_is_enforced(self):
        seen = []
        def runner(design_dir, spec, max_parallel=1, budget=None):
            seen.append(budget)
            return [{"tag": c["tag"], "overrides": c["overrides"],
                     "not_evaluated": True, "budget_exhausted": "max_evaluations"}
                    for c in spec["candidates"]]
        answer = graph_loop.run_loop(
            Path("dut"), {"design_name": "dut",
                          "evaluation_budget": {"max_evaluations": 1}},
            self.corpus(), execute=True, runner=runner, max_rounds=3)
        self.assertEqual(len(seen), 1)
        self.assertIsNotNone(seen[0])
        self.assertEqual(answer["stop_reason"], "evaluation_budget_exhausted")
        self.assertFalse(answer["publish_case"])

    def test_the_checkpoint_records_how_the_session_ended(self):
        def runner(design_dir, spec, max_parallel=1):
            return [result(c["tag"], c["overrides"], [FANOUT]) for c in spec["candidates"]]
        with tempfile.TemporaryDirectory() as directory:
            checkpoint = Path(directory) / "ledger.json"
            answer = graph_loop.run_loop(
                Path(directory), {"design_name": "dut"}, self.corpus(),
                execute=True, runner=runner, max_rounds=1, checkpoint=checkpoint,
                session_id="s1")
            ledger = json.loads(checkpoint.read_text(encoding="utf-8"))
        self.assertEqual(answer["stop_reason"], "max_rounds_reached")
        self.assertEqual(ledger["stop_reason"], "max_rounds_reached")
        self.assertEqual(ledger["session"], "s1")
        self.assertEqual(len(ledger["iterations"]), 1)

    def test_every_exit_is_a_declared_stop_reason(self):
        self.assertEqual(len(set(graph_loop.STOP_REASONS)), len(graph_loop.STOP_REASONS))
        for reason in ("candidates_proposed", "actions_exhausted", "plateau",
                       "winner_found", "max_rounds_reached", "execution_blocked",
                       "evaluation_budget_exhausted", "expected_failure_control",
                       "historical_pass_requires_revalidation"):
            self.assertIn(reason, graph_loop.STOP_REASONS)

    def test_previously_measured_configuration_is_not_repeated(self):
        corpus = self.corpus()
        corpus[-1]["iterations"][0]["results"].append(
            result("already", {"K": 1}, [FANOUT]))
        answer = graph_loop.run_loop(Path("dut"), {"design_name": "dut"},
                                     corpus, execute=False)
        self.assertEqual(answer["stop_reason"], "actions_exhausted")
        self.assertEqual(answer["next_candidates"], [])

    def test_infrastructure_failure_is_not_learned_or_published(self):
        def runner(*args, **kwargs):
            return [{"tag": "failed", "overrides": {"K": 1},
                     "error": "CERTIFICATE_VERIFY_FAILED"}]
        answer = graph_loop.run_loop(Path("dut"), {"design_name": "dut"},
                                     self.corpus(), execute=True, runner=runner)
        self.assertEqual(answer["stop_reason"], "execution_blocked")
        self.assertFalse(answer["publish_case"])
        self.assertIn("remains untried", answer["why"])

    def test_remote_rate_limit_is_an_infrastructure_failure(self):
        self.assertTrue(graph_loop._infrastructure_failure({
            "error": "Failed to obtain PDK remotely: 403 rate limit exceeded"}))

    def test_expected_failure_control_is_not_optimized(self):
        def forbidden(*args, **kwargs):
            raise AssertionError("negative control must not execute")
        answer = graph_loop.run_loop(
            Path("dut"),
            {"design_name": "dut", "expected_outcome": "fail",
             "expected_outcome_reason": "CDC gate negative control"},
            self.corpus(), execute=True, runner=forbidden)
        self.assertEqual(answer["stop_reason"], "expected_failure_control")
        self.assertFalse(answer["executed"])
        self.assertIn("CDC", answer["why"])

    def test_existing_signoff_winner_prevents_regression_chasing(self):
        corpus = self.corpus()
        corpus[-1]["iterations"][0]["results"] = [
            result("good", {"K": 1}, passed=True),
            result("later-regression", {"K": 2}, [FANOUT]),
        ]
        answer = graph_loop.run_loop(Path("dut"), {"design_name": "dut"},
                                     corpus, execute=True,
                                     runner=lambda *args, **kwargs: self.fail())
        self.assertEqual(answer["stop_reason"], "historical_pass_requires_revalidation")
        self.assertIsNone(answer["winner"])
        self.assertEqual(answer["historical_winner"]["tag"], "good")
        self.assertFalse(answer["executed"])


if __name__ == "__main__":
    unittest.main()
