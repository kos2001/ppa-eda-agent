import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from closure_review import SPECIALISTS, inspect


class ClosureReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.log = self.root / "proof.log"
        self.log.write_bytes(b"fixture, not actual design evidence\n")
        self.manifest = {
            "schema_version": 1, "run_id": "fixture", "design": "fixture",
            "toolchain": "fixture-build", "target_identity": "original-target",
            "required_gates": ["rtl_to_synthesis", "synthesis_to_final"],
            "gates": {name: {"state": "pass", "evidence": ["proof"]}
                      for name in ("rtl_to_synthesis", "synthesis_to_final")},
            "artifacts": {"proof": {"path": "proof.log", "sha256":
                hashlib.sha256(self.log.read_bytes()).hexdigest()}},
        }

    def check(self, manifest=None):
        return inspect(self.manifest if manifest is None else manifest, self.root)

    def test_intact_declared_evidence_needs_independent_review(self):
        result = self.check()
        self.assertTrue(result["all_declared_gates_pass"])
        self.assertIn("verification-ppa-evaluator", result["recommended_agents"])
        self.assertNotIn("verdict", result)

    def test_missing_or_changed_file_blocks_handoff(self):
        for change in (lambda: self.log.write_bytes(b"changed"), self.log.unlink):
            change()
            result = self.check()
            self.assertFalse(result["handoff_valid"])
            self.assertIn("evidence-environment-auditor", result["recommended_agents"])

    def test_physical_proof_cannot_discharge_rtl_proof(self):
        self.manifest["gates"]["rtl_to_synthesis"] = {"state": "unverified"}
        result = self.check()
        self.assertTrue(result["handoff_valid"])
        self.assertFalse(result["all_declared_gates_pass"])
        self.assertIn("equivalence-specialist", result["recommended_agents"])
        self.assertEqual(result["unresolved"],
                         [{"gate": "rtl_to_synthesis", "state": "unverified"}])

    def test_missing_required_gate(self):
        del self.manifest["gates"]["rtl_to_synthesis"]
        self.assertFalse(self.check()["handoff_valid"])

    def test_route_model_infra_and_budget_without_measured_failure(self):
        for state, role in (("unverified", "sram-model-qualifier"),
                            ("infrastructure_failure", "evidence-environment-auditor"),
                            ("budget_deferred", "feedback-optimizer")):
            with self.subTest(state=state):
                self.manifest["gates"]["macro_model"] = {"state": state}
                result = self.check()
                self.assertIn(role, result["recommended_agents"])
                self.assertEqual(result["unresolved"], [{"gate": "macro_model", "state": state}])
                self.assertFalse(result["all_declared_gates_pass"])

    def test_measured_verdict_requires_named_evidence(self):
        for evidence in ([], ["unknown"], "proof"):
            with self.subTest(evidence=evidence):
                self.manifest["gates"]["rtl_to_synthesis"]["evidence"] = evidence
                self.assertFalse(self.check()["handoff_valid"])

    def test_reject_escape_and_absolute_paths(self):
        for path in ("../proof.log", "/tmp/proof.log", "C:/proof.log", "C:proof.log", "..\\proof.log"):
            with self.subTest(path=path):
                self.manifest["artifacts"]["proof"]["path"] = path
                self.assertFalse(self.check()["handoff_valid"])

    def test_empty_or_malformed_contract_never_passes(self):
        for key, value in (("required_gates", []), ("required_gates", [{}]),
                           ("required_gates", ["x", "x"]), ("gates", []),
                           ("artifacts", {}), ("target_identity", ""),
                           ("schema_version", 2)):
            with self.subTest(key=key, value=value):
                manifest = copy.deepcopy(self.manifest)
                manifest[key] = value
                self.assertFalse(self.check(manifest)["all_declared_gates_pass"])

    def test_invalid_state_types_are_diagnostics_not_crashes(self):
        for state in ([], {}, None, "PASS"):
            self.manifest["gates"]["rtl_to_synthesis"]["state"] = state
            self.assertFalse(self.check()["handoff_valid"])

    def test_failure_preserved_and_no_input_mutation(self):
        self.manifest["gates"]["rtl_to_synthesis"]["state"] = "fail"
        original = copy.deepcopy(self.manifest)
        result = self.check()
        self.assertTrue(result["handoff_valid"])
        self.assertFalse(result["all_declared_gates_pass"])
        self.assertEqual(self.manifest, original)

    def test_cli_exit_codes(self):
        path = self.root / "manifest.json"
        for payload, expected in ((json.dumps(self.manifest), 0), ("{}", 1), ("{", 2), ("[]", 2)):
            path.write_text(payload, encoding="utf-8")
            process = subprocess.run([sys.executable, str(ROOT / "pipeline/closure_review.py"), str(path)],
                                     capture_output=True, text=True, timeout=10)
            self.assertEqual(process.returncode, expected, process.stderr)
            self.assertIn("handoff_valid", json.loads(process.stdout))

    def test_recommended_roles_resolve_to_readable_agent_definitions(self):
        roles = set(SPECIALISTS.values()) | {
            "evidence-environment-auditor", "feedback-optimizer", "verification-ppa-evaluator"}
        for role in roles:
            path = ROOT / ".claude" / "agents" / f"{role}.md"
            with self.subTest(role=role):
                content = path.read_text(encoding="utf-8")
                frontmatter = content.split("---", 2)[1]
                fields = dict(line.split(":", 1) for line in frontmatter.strip().splitlines())
                self.assertEqual(fields["name"].strip(), role)

    def test_bad_hash_and_non_file_rejected(self):
        self.manifest["artifacts"]["proof"]["sha256"] = "not-a-hash"
        self.assertFalse(self.check()["handoff_valid"])
        self.manifest["artifacts"]["proof"]["path"] = "."
        self.assertFalse(self.check()["handoff_valid"])


if __name__ == "__main__":
    unittest.main()
