import csv
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from export_macro_loads import compare_inputs


class ReplayStateTests(unittest.TestCase):
    def test_input_drift_missing_duplicate_and_unknown_fail_closed(self):
        fields = ['pin', 'max_rise_ns', 'max_fall_ns', 'min_rise_ns', 'min_fall_ns']
        rows = [dict(zip(fields, ['u_sram/clk0', '.1', '.2', '.3', '.4']))]
        with tempfile.TemporaryDirectory() as folder:
            original, replay = (Path(folder) / name for name in ('old.csv', 'new.csv'))
            def write(path, data):
                with path.open('w', newline='') as stream:
                    writer = csv.DictWriter(stream, fieldnames=fields)
                    writer.writeheader()
                    writer.writerows(data)
            write(original, rows)
            write(replay, rows)
            self.assertTrue(compare_inputs(original, replay)['matches'])
            for data in ([], rows * 2,
                         [{**rows[0], 'min_fall_ns': '.40001'}],
                         [{**rows[0], 'max_rise_ns': 'nan'}],
                         [{**rows[0], 'pin': 'u_sram/clk1'}]):
                with self.subTest(data=data):
                    write(replay, data)
                    self.assertFalse(compare_inputs(original, replay)['matches'])
