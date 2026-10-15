import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "pipeline/designs/riscv32i/experiments/evidence-20261015-closure"
spec = importlib.util.spec_from_file_location("closure_archive", EVIDENCE / "archive.py")
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class ClosureArchiveTests(unittest.TestCase):
    def test_committed_artifact_bytes_match_manifests(self):
        manifests = sorted(EVIDENCE.glob("closure-*/sources.json"))
        self.assertGreaterEqual(len(manifests), 2)
        for manifest in manifests:
            for relative, record in json.loads(manifest.read_text())["sources"].items():
                with self.subTest(run=manifest.parent.name, artifact=relative):
                    data = (manifest.parent / relative).read_bytes()
                    if record["gzip"]:
                        data = gzip.decompress(data)
                    self.assertEqual(len(data), record["uncompressed_bytes"])
                    self.assertEqual(hashlib.sha256(data).hexdigest(), record["uncompressed_sha256"])

    def test_archived_custom_sources_match_run_provenance(self):
        manifests = sorted(EVIDENCE.glob("closure-*/custom_flow_provenance.json"))
        self.assertTrue(manifests)
        for manifest in manifests:
            for name, expected in json.loads(manifest.read_text())["sources_sha256"].items():
                with self.subTest(run=manifest.parent.name, source=name):
                    data = (manifest.parent / "flow_sources" / name).read_bytes()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), expected)

    def test_incomplete_run_is_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "expected one"):
                archive.archive(directory)

    def test_changed_flow_source_is_rejected_before_output_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            for relative in ("final/metrics.json", "resolved.json", "final/def/d.def",
                             "final/nl/d.nl.v", "flow_sources/flow.py"):
                target = run / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}")
            (run / "custom_flow_provenance.json").write_text(json.dumps(
                {"sources_sha256": {"flow.py": "wrong"}}))
            with self.assertRaisesRegex(ValueError, "flow source missing or changed"):
                archive.archive(run)


if __name__ == "__main__":
    unittest.main()
