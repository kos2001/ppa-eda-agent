import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from case_storage import compact_text, compact_cases, write_case_json


class CaseStorageTests(unittest.TestCase):
    def test_preserves_strings_escapes_and_exact_number_spellings(self):
        original = r'''{
  "message": "한글  two spaces \"quoted\" \\ path \n newline",
  "numbers": [1.000000000000000001, 1e-123, -0.0, 9007199254740993],
  "nested": { "yes": true, "nothing": null }
}'''
        compact = compact_text(original)
        self.assertEqual(json.loads(compact), json.loads(original))
        self.assertIn("1.000000000000000001,1e-123,-0.0,9007199254740993", compact)
        self.assertIn(r'한글  two spaces \"quoted\" \\ path \n newline', compact)
        self.assertEqual(compact_text(compact), compact)

    def test_existing_nonfinite_metrics_are_preserved(self):
        self.assertEqual(compact_text('{ "metric": Infinity, "other": NaN }'),
                         '{"metric":Infinity,"other":NaN}\n')

    def test_audit_is_read_only_and_write_is_lossless_and_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "case.json"
            original = '{\n  "metric": 1.2,\n  "diagnosis": "two  spaces"\n}\n'
            path.write_text(original)
            mode = path.stat().st_mode
            preview = compact_cases(root)
            self.assertEqual(path.read_text(), original)
            applied = compact_cases(root, write=True)
            self.assertEqual(preview["saved_bytes"], applied["saved_bytes"])
            self.assertEqual(json.loads(path.read_text()), json.loads(original))
            self.assertEqual(path.stat().st_mode, mode)
            self.assertEqual(compact_cases(root, write=True)["saved_bytes"], 0)
            self.assertEqual(list(root.iterdir()), [path])

    def test_invalid_case_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.json"
            path.write_text('{ "incomplete":')
            with self.assertRaises(json.JSONDecodeError):
                compact_cases(Path(directory), write=True)
            self.assertEqual(path.read_text(), '{ "incomplete":')

    def test_future_writes_stay_compact_and_readable_by_existing_json_readers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "case.json"
            case = {"design": "테스트", "iterations": [{"area": 123.45}]}
            write_case_json(path, case)
            self.assertEqual(json.loads(path.read_text()), case)
            self.assertEqual(path.read_text().count("\n"), 1)


if __name__ == "__main__":
    unittest.main()
