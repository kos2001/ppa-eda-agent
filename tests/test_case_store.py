"""Tests for pipeline/case_store.py.

The cache is only safe if it can never serve a case that no longer
matches the file on disk, and never hands a writer a case with its
layouts missing. These pin the first; the second is the module's rule
that writers keep reading the full file.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import case_store  # noqa: E402


def _case(area: float) -> dict:
    return {"design": "d", "iterations": [{"results": [{
        "tag": "a", "area_um2": area,
        "layout": {"cells": [1, 2, 3]}, "netlist": {"cells": [4]},
    }]}]}


class LoadLightTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.path = root / "cases" / "d__2026-01-01.json"
        self.path.parent.mkdir()
        self.cache = root / "cache"
        self.path.write_text(json.dumps(_case(1.0)), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_heavy_fields_are_removed_and_the_rest_kept(self):
        case = case_store.load_light(self.path, cache_dir=self.cache)
        result = case["iterations"][0]["results"][0]
        self.assertNotIn("layout", result)
        self.assertNotIn("netlist", result)
        self.assertEqual(result["area_um2"], 1.0)
        self.assertEqual(result["tag"], "a")
        # Flagged the way the dashboard's lightCase flags them, so a
        # reader can tell "not loaded" from "never recorded".
        self.assertTrue(result["layout_deferred"])
        self.assertTrue(result["netlist_deferred"])

    def test_absent_or_failed_heavy_fields_are_not_flagged(self):
        case = _case(1.0)
        result = case["iterations"][0]["results"][0]
        del result["layout"]
        result["netlist"] = {"error": "no netlist"}
        self.path.write_text(json.dumps(case), encoding="utf-8")
        got = case_store.load_light(self.path, cache_dir=self.cache)
        result = got["iterations"][0]["results"][0]
        self.assertFalse(result["layout_deferred"])
        self.assertFalse(result["netlist_deferred"])

    def test_second_read_is_served_from_the_cache(self):
        case_store.load_light(self.path, cache_dir=self.cache)
        entry = self.cache / self.path.name
        self.assertTrue(entry.is_file())
        # Poison the cached copy under the same key: if the second read
        # came from the file it would not see this.
        cached = json.loads(entry.read_text(encoding="utf-8"))
        cached["case"]["design"] = "from-cache"
        entry.write_text(json.dumps(cached), encoding="utf-8")
        self.assertEqual(
            case_store.load_light(self.path, cache_dir=self.cache)["design"],
            "from-cache")

    def test_a_rewritten_case_is_reread(self):
        case_store.load_light(self.path, cache_dir=self.cache)
        self.path.write_text(json.dumps(_case(22.5)), encoding="utf-8")
        st = self.path.stat()
        os.utime(self.path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
        case = case_store.load_light(self.path, cache_dir=self.cache)
        self.assertEqual(case["iterations"][0]["results"][0]["area_um2"], 22.5)

    def test_a_corrupt_cache_entry_falls_back_to_the_file(self):
        self.cache.mkdir()
        (self.cache / self.path.name).write_text("{not json", encoding="utf-8")
        case = case_store.load_light(self.path, cache_dir=self.cache)
        self.assertEqual(case["design"], "d")

    def test_stores_outside_the_repo_are_not_cached(self):
        self.assertIsNone(case_store._default_cache_dir(self.path))
        case_store.load_light(self.path)
        self.assertFalse(self.cache.exists())

    def test_unreadable_case_raises_like_a_plain_read(self):
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(json.JSONDecodeError):
            case_store.load_light(self.path, cache_dir=self.cache)


if __name__ == "__main__":
    unittest.main()
