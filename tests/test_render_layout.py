"""Guards the layout renderer's use of the PDK's own layer properties.

The renderer shipped for a while without loading sky130A.lyp, so every
image it produced used KLayout's auto-assigned default colours. That is
not cosmetic: met1, met2, poly and diff all came out as indistinguishable
green/blue, which makes the image unreadable by layer — and the whole
reason the image exists is that a *readable* layout helps diagnose
physical problems (see the module docstring). These pin the fix.
"""
import os
import sys
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE))

import render_layout  # noqa: E402


class TestLayerProperties(unittest.TestCase):
    def test_the_pdk_lyp_actually_exists_where_we_point_at_it(self):
        """The container path is only meaningful if the host file is
        really there — pdk/ is mounted at /pdk, so this is the same file
        KLayout will open."""
        host = (Path(render_layout.PDK_ROOT)
                / render_layout.LYP_IN_CONTAINER.replace("/pdk/", "", 1))
        if not Path(render_layout.PDK_ROOT).exists():
            self.skipTest("no local PDK (fresh checkout)")
        self.assertTrue(host.exists(), f"{host} missing — renders would silently "
                                        f"fall back to KLayout default colours")

    def test_render_script_loads_layer_props_and_reports_it(self):
        script = render_layout._KLAYOUT_SCRIPT
        self.assertIn("load_layer_props", script)
        # Must report back, so a fallback render is distinguishable from a
        # correct one rather than both looking equally authoritative.
        self.assertIn("LYP_LOADED=", script)

    def test_pdk_is_mounted_into_the_render_container(self):
        """Loading the .lyp is impossible if the PDK isn't mounted — the
        original bug was exactly that the render container saw only its
        temp work directory."""
        src = (PIPELINE / "render_layout.py").read_text()
        self.assertIn('f"{PDK_ROOT}:/pdk:ro"', src)


if __name__ == "__main__":
    unittest.main()
