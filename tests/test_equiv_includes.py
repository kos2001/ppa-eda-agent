"""Proof input staging controls; no physical flow or large proof required."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from equiv_check import check, stage_reference_sources


class IncludeStagingTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('PPA_FINAL_EQUIV_INTEGRATION') == '1', 'requires Docker')
    def test_actual_nested_include_positive_and_negative_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'src/nested').mkdir(parents=True)
            (root / 'src/dut.v').write_text('`include "nested/logic.vh"\n'
                'module dut(input a, output y); assign y=`LOGIC; endmodule\n')
            (root / 'src/nested/logic.vh').write_text('`include "../value.vh"\n')
            (root / 'src/value.vh').write_text('`define LOGIC a\n')
            (root / 'config.json').write_text('{"DESIGN_NAME":"dut"}')
            stage = root / '01-yosys-synthesis'
            stage.mkdir()
            lib = root / 'cells.lib'
            lib.write_text('library(L) {cell(BUF) {area: 1; pin(A) {direction: input;} '
                           'pin(Y) {direction: output; function: "A";}}}\n')
            for expression, expected in (('a', True), ('~a', False)):
                (stage / 'dut.nl.v').write_text(
                    'module dut(input a, output y); assign y=' + expression + '; endmodule\n')
                with patch('equiv_check.find_liberty', return_value=lib):
                    result = check(root, root, timeout_s=30)
                self.assertEqual(result['equivalent'], expected, result)
                self.assertFalse(result['timed_out'])
                self.assertEqual(len(result['reference_sources_sha256']), 3)
                if expected:
                    self.assertFalse(result['vacuous'])
                else:
                    self.assertGreater(result['unproven_points'], 0)

    def test_nested_relative_includes_and_duplicate_basenames_are_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src, work = root / "src", root / "work"
            (src / "a").mkdir(parents=True)
            (src / "b").mkdir()
            top = src / "dut.v"
            top.write_bytes(b'`include "a/defs.vh"\r\n`include "b/defs.vh"\r\n')
            (src / "a/defs.vh").write_text('`include "../shared.vh"\n`define A 1\n')
            (src / "b/defs.vh").write_text('`define B 2\n')
            (src / "shared.vh").write_text('`define SHARED 3\n')
            args, original, staged = stage_reference_sources([top], src, work)
            self.assertEqual(args, ['"/work/rtl/dut.v"'])
            self.assertEqual(len(original), 4)
            self.assertEqual(len(staged), 4)
            self.assertIn(b'"/work/rtl/a/defs.vh"\r\n', (work / "rtl/dut.v").read_bytes())
            self.assertIn('"/work/rtl/shared.vh"', (work / "rtl/a/defs.vh").read_text())
            self.assertEqual(original[str(top.resolve())], hashlib.sha256(top.read_bytes()).hexdigest())
            for name, digest in staged.items():
                self.assertEqual(digest, hashlib.sha256((work / name.removeprefix('/work/')).read_bytes()).hexdigest())

    def test_comments_strings_and_guarded_cycles(self):
        with tempfile.TemporaryDirectory() as directory:
            src = Path(directory) / "src"
            src.mkdir()
            top = src / "dut.v"
            top.write_text('// `include "missing1"\n/* `include "missing2" */\n'
                           'module dut; initial $display("`include missing3"); endmodule\n'
                           '`include "guard.vh"\n')
            (src / "guard.vh").write_text('`ifndef GUARD\n`define GUARD\n`include "guard.vh"\n`endif\n')
            _, original, _ = stage_reference_sources([top], src, Path(directory) / "work")
            self.assertEqual(len(original), 2)

    def test_missing_macro_and_escaping_includes_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "outside.vh").write_text('`define SECRET 1')
            top = src / "dut.v"
            for directive in ('`include "../outside.vh"', '`include "/outside.vh"',
                              '`include "C:/outside.vh"', '`include HEADER'):
                top.write_text(directive)
                with self.subTest(directive=directive), self.assertRaises(ValueError):
                    stage_reference_sources([top], src, root / "work")
            top.write_text('`include "missing.vh"')
            with self.assertRaises(FileNotFoundError):
                stage_reference_sources([top], src, root / "work")

    def test_symlink_outside_source_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            src = root / "src"
            src.mkdir()
            (root / "outside.vh").write_text('`define SECRET 1')
            try:
                (src / "alias.vh").symlink_to(root / "outside.vh")
            except OSError:
                self.skipTest('symlink creation unavailable')
            top = src / "dut.v"
            top.write_text('`include "alias.vh"')
            with self.assertRaises(ValueError):
                stage_reference_sources([top], src, root / "work")

    def test_check_stages_header_before_docker_and_hashes_consumed_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/dut.v").write_text('`include "timescale.vh"\nmodule dut; endmodule\n')
            header = root / "src/timescale.vh"
            header.write_bytes(b'`timescale 1ns/1ps\n')
            expected = hashlib.sha256(header.read_bytes()).hexdigest()
            (root / "config.json").write_text(json.dumps({'DESIGN_NAME': 'dut'}))
            (root / "01-yosys-synthesis").mkdir()
            (root / "01-yosys-synthesis/dut.nl.v").write_text('module dut; endmodule\n')
            lib = root / "cells.lib"
            lib.write_text('library(L) {}')

            def docker(command, **kwargs):
                work = Path(command[command.index('-v') + 1].removesuffix(':/work'))
                self.assertIn('"/work/rtl/dut.v"', (work / "eq.ys").read_text())
                self.assertIn('"/work/rtl/timescale.vh"', (work / "rtl/dut.v").read_text())
                self.assertEqual((work / "rtl/timescale.vh").read_bytes(), header.read_bytes())
                header.write_text('changed after staging')
                return subprocess.CompletedProcess(command, 0,
                    'Of those cells 1 are proven and 0 are unproven', '')

            with patch('equiv_check.find_liberty', return_value=lib), patch('equiv_check.subprocess.run', side_effect=docker):
                result = check(root, root)
            self.assertTrue(result['equivalent'])
            self.assertEqual(result['reference_sources_sha256'][str(header.resolve())], expected)
            self.assertEqual(result['staged_reference_sources_sha256']['/work/rtl/timescale.vh'], expected)


if __name__ == '__main__':
    unittest.main()
