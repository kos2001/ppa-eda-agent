"""Guards the single source of truth for the EDA toolchain.

The image string was previously hardcoded independently in four modules.
Changing the pin meant editing four files, and missing one would not fail
loudly — it would run part of the pipeline on a different OpenLane build
while writing results into the same reference-db as if they were
comparable. For a project whose value is that its measurements are real
and comparable, two silently different toolchains is a correctness
hazard.
"""
import os
import re
import sys
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent.parent / "pipeline"
sys.path.insert(0, str(PIPELINE))

import toolchain  # noqa: E402


class TestSingleSourceOfTruth(unittest.TestCase):
    def test_only_toolchain_pins_the_image(self):
        """No module may hardcode an image reference of its own again."""
        offenders = []
        for py in PIPELINE.glob("*.py"):
            if py.name == "toolchain.py":
                continue
            if re.search(r'["\']ghcr\.io/\S+openlane', py.read_text(encoding="utf-8")):
                offenders.append(py.name)
        self.assertEqual(offenders, [], f"{offenders} pin the image directly")

    def test_provenance_is_recorded_with_a_real_image_reference(self):
        info = toolchain.toolchain_info()
        self.assertTrue(info["openlane_image"].startswith("ghcr.io/"))
        self.assertIn(":", info["openlane_image"], "image must be version-pinned")
        self.assertIn("openlane_upstream", info)

    def test_image_namespace_is_not_naively_renamed(self):
        """OpenLane 2 moved to github.com/chipfoundry/openlane2 (same repo
        id as the old efabless path), but no ghcr.io/chipfoundry image is
        published — verified, it returns not-found. This pins the reference
        so a future 'consistency' fix doesn't point at an image that does
        not exist."""
        self.assertIn("efabless", toolchain.OPENLANE_IMAGE)
        self.assertIn("chipfoundry", toolchain.OPENLANE_UPSTREAM)


if __name__ == "__main__":
    unittest.main()
