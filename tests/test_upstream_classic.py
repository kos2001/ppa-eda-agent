import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from flows.upstream_classic import placement_step


class PlacementBackportTests(unittest.TestCase):
    def step(self, directory, pin_order):
        class Base:
            config_vars = []

            def run(self, state, **kwargs):
                self.received = (state, kwargs)
                return {"odb": "new-placement"}, {"wirelength": 17}

        corrected = placement_step(Base, SimpleNamespace(name="FP_PIN_ORDER_CFG"))
        step = corrected()
        step.step_dir = directory
        step.config = {"FP_PIN_ORDER_CFG": pin_order}
        return step

    def test_fixed_pin_order_skips_only_the_preliminary_placement(self):
        with tempfile.TemporaryDirectory() as directory:
            step = self.step(directory, "/design/pin_order.cfg")
            state = {"odb": "input"}
            self.assertEqual(step.run(state), ({}, {}))
            self.assertFalse(hasattr(step, "received"))
            self.assertEqual(state, {"odb": "input"})
            report = json.loads((Path(directory) / "upstream_backport.json").read_text())
            self.assertTrue(report["preliminary_placement_skipped"])

    def test_automatic_pins_preserve_upstream_placement_and_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            step = self.step(directory, None)
            state = {"odb": "input"}
            self.assertEqual(step.run(state, env={"TEST": "1"}),
                             ({"odb": "new-placement"}, {"wirelength": 17}))
            self.assertEqual(step.received, (state, {"env": {"TEST": "1"}}))
            self.assertFalse((Path(directory) / "upstream_backport.json").exists())

    def test_development_version_variable_is_not_registered_twice(self):
        variable = SimpleNamespace(name="FP_PIN_ORDER_CFG")
        base = type("Base", (), {"config_vars": [variable]})
        self.assertEqual(placement_step(base, variable).config_vars, [variable])
