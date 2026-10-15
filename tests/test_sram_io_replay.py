import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import sram_io_experiment as experiment


class ReplayTests(unittest.TestCase):
    def test_fresh_name_leaves_the_original_variant_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            original = [{"name": "r15-clock", "defines": ["IO_CHAIN4"]}]
            with patch.object(experiment, "DESIGNS", Path(directory)):
                result = experiment.replay_variants(original, "recheck")
            self.assertEqual(result[0]["name"], "r15-clock-recheck")
            self.assertEqual(original[0]["name"], "r15-clock")

    def test_existing_destination_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "srio_r15_clock_recheck").mkdir()
            with patch.object(experiment, "DESIGNS", root):
                with self.assertRaisesRegex(ValueError, "already exists"):
                    experiment.replay_variants([{"name": "r15-clock"}], "recheck")

    def test_unsafe_suffix_and_normalized_collisions_are_refused(self):
        with self.assertRaises(ValueError):
            experiment.replay_variants([{"name": "r15"}], "../escape")
        with self.assertRaisesRegex(ValueError, "collide"):
            experiment.replay_variants([{"name": "r-15"}, {"name": "r_15"}], "fresh")
