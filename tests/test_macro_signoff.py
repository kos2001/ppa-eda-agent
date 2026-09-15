import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
from flows.macro_signoff import concrete_cell_script
import run_stage


class ConcreteGeometryTests(unittest.TestCase):
    def test_concrete_geometry_is_read_before_macro_lef_and_routing(self):
        original = "    read_tech_lef\n    read_pdk_lef\n    read_macro_lef\n    read_def\ndrc check\n"
        result = concrete_cell_script(original)
        self.assertLess(result.index("read_pdk_lef"), result.index("read_pdk_gds"))
        self.assertLess(result.index("read_pdk_gds"), result.index("read_macro_lef"))
        self.assertTrue(result.endswith("read_def\ndrc check\n"))

    def test_unrecognized_upstream_script_does_not_silently_use_abstracts(self):
        for script in ("read_def\n", "    read_pdk_lef\n    read_pdk_lef\n"):
            with self.assertRaises(ValueError):
                concrete_cell_script(script)


class FlowSelectionTests(unittest.TestCase):
    def command(self, meta):
        with tempfile.TemporaryDirectory() as tmp:
            design = Path(tmp)
            (design / "config.json").write_text(json.dumps({"meta": meta}))
            proc = Mock(stdout=io.StringIO(""))
            proc.wait.return_value = 0
            with patch.object(run_stage.subprocess, "Popen", return_value=proc) as popen, \
                    patch.object(run_stage, "claim_run_dir"):
                run_stage.run_stage(design, "test", "Magic.DRC", ["CLOCK_PERIOD=25"],
                                    scl="sky130_fd_sc_hd", pdk="sky130A")
                return popen.call_args.args[0]

    def test_standard_design_keeps_the_openlane_cli(self):
        cmd = self.command({"flow": "Classic"})
        self.assertIn("openlane", cmd)
        self.assertNotIn("/flows/macro_signoff.py", cmd)

    def test_macro_flow_preserves_overrides_and_step_selection(self):
        cmd = self.command({"flow": "MacroSignoff"})
        self.assertIn("/flows/macro_signoff.py", cmd)
        self.assertIn(str(ROOT / "pipeline/flows") + ":/flows:ro", cmd)
        for flag, value in (("--override-config", "CLOCK_PERIOD=25"),
                            ("--to", "Magic.DRC"), ("--pdk", "sky130A"),
                            ("--scl", "sky130_fd_sc_hd")):
            self.assertEqual(cmd[cmd.index(flag) + 1], value)

    def test_upstream_backport_is_loaded_inside_the_selected_toolchain(self):
        cmd = self.command({"flow": "UpstreamClassic"})
        self.assertIn("/flows/upstream_classic.py", cmd)
        self.assertIn(run_stage.IMAGE, cmd)
        self.assertIn(str(ROOT / "pipeline/flows") + ":/flows:ro", cmd)
