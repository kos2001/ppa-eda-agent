import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import custom_bridge as bridge
import schematic_service as service

SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M0 0L1 1"/></svg>'


class AtomicDrawingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sheet = Path(self.tmp.name) / "input.sch"
        self.sheet.write_text("original source")
        self.output = self.sheet.with_suffix(".svg")
        self.output.write_text("previous drawing")

    def run_renderer(self, content, status="success"):
        def fake(sheet, pdk, out, timeout, circuit_only):
            if content is not None:
                out.write_text(content)
            return bridge.BridgeResult(status=status)
        with patch.object(bridge, "_render_schematic_to_file", side_effect=fake):
            return bridge.render_schematic(self.sheet, out=self.output)

    def test_stale_file_never_counts_as_new_success(self):
        result = self.run_renderer(None)
        self.assertFalse(result.ok)
        self.assertIsNone(result.metadata["svg"])
        self.assertEqual(self.output.read_text(), "previous drawing")

    def test_failure_and_malformed_or_empty_svg_preserve_previous_drawing(self):
        for content, status in [(SVG, "failure"), ("<svg>", "success"),
                                ('<svg xmlns="http://www.w3.org/2000/svg"/>', "success"),
                                (SVG.replace("0 0 10 10", "0 0 nan 10"), "success")]:
            with self.subTest(content=content, status=status):
                self.assertFalse(self.run_renderer(content, status).ok)
                self.assertEqual(self.output.read_text(), "previous drawing")

    def test_only_valid_new_output_is_published_and_source_is_unchanged(self):
        self.assertTrue(self.run_renderer(SVG).ok)
        self.assertEqual(self.output.read_text(), SVG)
        self.assertEqual(self.sheet.read_text(), "original source")
        self.assertEqual(list(self.sheet.parent.glob(".xschem-*")), [])

    def test_nonzero_renderer_exit_rejects_an_existing_svg(self):
        self.output.write_text(SVG)
        with patch.object(bridge, "resolve", return_value="xschem"), \
             patch.object(bridge, "rcfile_for", return_value=(self.sheet, "/rc")), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(bridge.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "", "")):
            result = bridge._render_schematic_to_file(self.sheet, out=self.output)
        self.assertFalse(result.ok)
        self.assertIn("xschem exited with code 1", result.errors)


class ContentCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sheet = self.root / "input.sch"
        self.sheet.write_text("source")
        self.symbol = self.root / "device.sym"
        self.symbol.write_text("symbol A")
        self.identity = {"image": "test", "id": "image-a"}
        for name, value in [("PDK_ROOT", self.root / "pdk"), ("ANALOG_ROOT", self.root)]:
            patcher = patch.object(bridge, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_fingerprint_tracks_symbols_renderer_and_view(self):
        first = service.input_key(self.sheet, False, self.identity)
        stamp = self.symbol.stat()
        self.symbol.write_text("symbol B")
        import os
        os.utime(self.symbol, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        self.assertNotEqual(first, service.input_key(self.sheet, False, self.identity))
        second = service.input_key(self.sheet, False, self.identity)
        self.assertNotEqual(second, service.input_key(self.sheet, True, self.identity))
        self.assertNotEqual(second, service.input_key(self.sheet, False, {"id": "image-b"}))

    def test_cache_hit_and_corrupted_output_recovery(self):
        def render(sheet, out, circuit_only):
            out.write_text(SVG)
            return bridge.BridgeResult(status="success")
        with patch.object(service, "sheet_path", return_value=self.sheet), \
             patch.object(service, "renderer_identity", return_value=self.identity), \
             patch.object(bridge, "render_schematic", side_effect=render) as renderer:
            self.assertFalse(service.cached_render("analog/test/test", True)["cached"])
            self.assertTrue(service.cached_render("analog/test/test", True)["cached"])
            self.assertEqual(renderer.call_count, 1)
            self.sheet.with_suffix(".circuit.svg").write_text("broken")
            self.assertFalse(service.cached_render("analog/test/test", True)["cached"])
            self.assertEqual(renderer.call_count, 2)
            self.sheet.with_suffix(".circuit.svg.cache.json").write_text("[]")
            self.assertFalse(service.cached_render("analog/test/test", True)["cached"])
            self.assertEqual(renderer.call_count, 3)

    def test_inputs_changed_during_render_are_not_cached(self):
        def render(sheet, out, circuit_only):
            out.write_text(SVG)
            self.symbol.write_text("changed")
            return bridge.BridgeResult(status="success")
        with patch.object(service, "sheet_path", return_value=self.sheet), \
             patch.object(service, "renderer_identity", return_value=self.identity), \
             patch.object(bridge, "render_schematic", side_effect=render):
            result = service.cached_render("analog/test/test", True)
        self.assertEqual(result["metadata"]["reason"], "inputs_changed")
        self.assertFalse(self.sheet.with_suffix(".circuit.svg.cache.json").exists())


if __name__ == "__main__":
    unittest.main()
