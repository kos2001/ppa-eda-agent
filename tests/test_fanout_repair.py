import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import orchestrator
import run_stage
from flows.fanout_buffer import spatial_groups
from flows.fanout_repair import repair_rounds


class PhysicalFanoutTests(unittest.TestCase):
    def test_repair_rounds_are_bounded_and_explicit(self):
        self.assertEqual(repair_rounds([]), 1)
        self.assertEqual(repair_rounds(["FANOUT_REPAIR_ROUNDS=2"]), 2)
        for value in ("0", "4", "invalid"):
            with self.assertRaises(ValueError):
                repair_rounds([f"FANOUT_REPAIR_ROUNDS={value}"])

    def test_flow_sources_are_immutable_during_run_and_retained_with_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            design = Path(tmp)
            (design / "config.json").write_text("{}")
            run = design / "runs/probe"
            run.mkdir(parents=True)
            proc = Mock(stdout=io.StringIO(""))
            proc.wait.return_value = 0

            def started(command, **kwargs):
                source = Path(next(x for x in command if x.endswith(":/flows:ro")).removesuffix(":/flows:ro"))
                self.assertNotEqual(source, ROOT / "pipeline/flows")
                self.assertEqual((source / "fanout_buffer.py").read_bytes(),
                                 (ROOT / "pipeline/flows/fanout_buffer.py").read_bytes())
                return proc

            with patch.object(run_stage.subprocess, "Popen", side_effect=started), \
                    patch.object(run_stage, "claim_run_dir"):
                run_stage.run_stage(design, "probe", None, [], flow="FanoutRepair")
            provenance = json.loads((run / "custom_flow_provenance.json").read_text())
            self.assertEqual(provenance["flow"], "FanoutRepair")
            self.assertIn("fanout_buffer.py", provenance["sources_sha256"])
            self.assertTrue((run / "flow_sources/fanout_buffer.py").is_file())

    def test_spatial_partition_keeps_every_sink_once_and_respects_limit(self):
        # Includes repeated locations: distinct colocated pins must survive.
        points = [(i // 2, i % 3) for i in range(257)]
        sinks = list(range(len(points)))
        groups = spatial_groups(sinks, 10, lambda i: points[i])
        self.assertEqual(sorted(x for group in groups for x in group), sinks)
        self.assertTrue(all(1 <= len(group) <= 10 for group in groups))
        self.assertEqual(groups, spatial_groups(sinks, 10, lambda i: points[i]))
        with self.assertRaises(ValueError):
            spatial_groups(sinks, 1, lambda i: points[i])

    def test_flow_is_an_experiment_axis_and_unknown_flow_fails_before_execution(self):
        candidates = [{"tag": "stock", "flow": "Classic"},
                      {"tag": "buffered", "flow": "FanoutRepair"}]
        self.assertEqual(len(orchestrator.validate_candidates(candidates)), 2)
        with self.assertRaisesRegex(ValueError, "unsupported"):
            orchestrator.validate_candidates([{"tag": "bad", "flow": "typo"}])
        with self.assertRaisesRegex(ValueError, "same overrides"):
            orchestrator.validate_candidates([{"tag": "a", "flow": "FanoutRepair"},
                                              {"tag": "b", "flow": "FanoutRepair"}])

    def test_macro_repair_keeps_concrete_geometry_entrypoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            design = Path(tmp)
            (design / "config.json").write_text(json.dumps({}))
            proc = Mock(stdout=io.StringIO(""))
            proc.wait.return_value = 0
            with patch.object(run_stage.subprocess, "Popen", return_value=proc) as popen, \
                    patch.object(run_stage, "claim_run_dir"):
                run_stage.run_stage(design, "probe", None, [], flow="MacroFanoutRepair")
            command = popen.call_args.args[0]
            self.assertIn("/flows/fanout_repair.py", command)
            self.assertIn("--concrete-magic", command)
            self.assertTrue(any(x.endswith(":/flows:ro") for x in command))


if __name__ == "__main__":
    unittest.main()
