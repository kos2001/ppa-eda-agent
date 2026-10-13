import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import graph_loop


def result(tag, overrides, violations=(), passed=False):
    return {"tag": tag, "overrides": overrides,
            "verdict": {"passed": passed, "violations": list(violations),
                        "unverified": []}}


def case(design, rows, source):
    return {"design": design, "date": "2026-01-01", "_file": source,
            "iterations": [{"iteration": 1, "results": rows}]}


class GraphLoopTests(unittest.TestCase):
    def test_windows_drive_path_has_a_shell_free_wsl_translation(self):
        translated = graph_loop._wsl_path(Path(r"C:\work\한글\design"), None)
        self.assertEqual(translated, "/mnt/c/work/한글/design")

    def corpus(self):
        return [
            case("old", [
                result("bad", {"K": 0}, ["1 max-fanout (DRV) violation(s)"]),
                result("good", {"K": 1}, passed=True),
            ], "old.json"),
            case("dut", [
                result("now", {}, ["1 max-fanout (DRV) violation(s)"])
            ], "dut.json"),
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
            return [result(spec["candidates"][0]["tag"], {"K": 1}, passed=True)]
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
        finally:
            checkpoint.unlink(missing_ok=True)

    def test_previously_measured_configuration_is_not_repeated(self):
        corpus = self.corpus()
        corpus[-1]["iterations"][0]["results"].append(
            result("already", {"K": 1}, ["1 max-fanout (DRV) violation(s)"]))
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


if __name__ == "__main__":
    unittest.main()
