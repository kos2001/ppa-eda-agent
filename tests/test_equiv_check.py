"""Regression tests for the functional-equivalence gate.

Same conventions as test_orchestrator_pure.py. The Yosys proof itself
needs Docker and is exercised for real on every `--verify-function` run;
what is pinned here is the decision logic around it, which is where a
mistake would silently turn "not verified" into "passed".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import equiv_check  # noqa: E402
import orchestrator  # noqa: E402


def _verdict():
    return {"passed": True, "violations": [], "area_um2": 1.0,
            "utilization": 0.5, "worst_setup_wns": 0.0}


class TestVerifyFunctionGate(unittest.TestCase):
    """Guards orchestrator.verify_function(): a functional mismatch must
    fail a candidate no matter how clean its DRC/LVS/timing were. A wrong
    circuit that meets timing is still wrong."""

    def _patched(self, fake):
        original = equiv_check.check
        equiv_check.check = fake
        self.addCleanup(lambda: setattr(equiv_check, "check", original))

    def test_non_equivalent_fails_an_otherwise_clean_candidate(self):
        self._patched(lambda d, r: {"equivalent": False, "vacuous": False,
                                     "unproven_points": 4})
        v = _verdict()
        orchestrator.verify_function(object(), object(), v)
        self.assertFalse(v["passed"])
        self.assertTrue(any("NOT functionally equivalent" in s for s in v["violations"]))

    def test_a_vacuous_pass_is_not_a_pass(self):
        """Yosys exiting 0 having compared nothing must not read as
        proof. This is the failure mode that would make the whole gate
        decorative — it would go green forever and catch nothing."""
        self._patched(lambda d, r: {"equivalent": True, "vacuous": True,
                                     "unproven_points": 0})
        v = _verdict()
        orchestrator.verify_function(object(), object(), v)
        self.assertFalse(v["passed"])
        self.assertTrue(any("vacuous" in s for s in v["violations"]))

    def test_being_unable_to_check_is_not_a_pass(self):
        """If the checker itself errors — missing netlist, Docker down —
        the honest verdict is 'not verified', never 'fine'."""
        def boom(d, r):
            raise FileNotFoundError("no gate netlist")
        self._patched(boom)
        v = _verdict()
        orchestrator.verify_function(object(), object(), v)
        self.assertFalse(v["passed"])
        self.assertTrue(any("not verified" in s for s in v["violations"]))

    def test_a_real_proof_leaves_the_verdict_alone(self):
        self._patched(lambda d, r: {"equivalent": True, "vacuous": False,
                                     "proven_points": 4, "unproven_points": 0})
        v = _verdict()
        orchestrator.verify_function(object(), object(), v)
        self.assertTrue(v["passed"])
        self.assertEqual(v["violations"], [])

    def test_verification_is_off_by_default(self):
        """Enabling it changes what a verdict means, so it must be a
        deliberate choice rather than something that appears silently."""
        import inspect
        sig = inspect.signature(orchestrator.run_candidate)
        self.assertIs(sig.parameters["verify_fn"].default, False)
        self.assertIs(
            inspect.signature(orchestrator.orchestrate).parameters["verify_fn"].default,
            False)


if __name__ == "__main__":
    unittest.main()
