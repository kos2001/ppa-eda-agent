"""Connectivity and activation gates for the connected CMOS review layout."""
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import schematic_inspect as si
import schematic_layout as sl


class ConnectedLayoutTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        folder = self.root / 'pipeline/analog/test'
        folder.mkdir(parents=True)
        self.source = folder / 'sheet.sch'
        lib = self.root / 'pdk/sky130A/libs.tech/xschem/test'
        lib.mkdir(parents=True)
        for typ in ['nmos', 'pmos']:
            symbol = f'K {{type={typ}}}\n'
            for name, x, y in [('D', 20, 30), ('G', -20, 0), ('S', 20, -30), ('B', 20, 0)]:
                symbol += f'B 5 {x-1} {y-1} {x+1} {y+1} {{name={name} dir=inout}}\n'
            (lib / f'{typ}.sym').write_text(symbol)
        # Independent NAND topology: parallel PMOS, series NMOS, isolated wells.
        devices = [('P1', 'pmos', ['Y', 'A', 'VPWR', 'VPB']),
                   ('P2', 'pmos', ['Y', 'B', 'VPWR', 'VPB']),
                   ('N1', 'nmos', ['Y', 'A', 'mid', 'VNB']),
                   ('N2', 'nmos', ['mid', 'B', 'VGND', 'VNB'])]
        text = 'G {}\nK {}\nV {}\nS {}\nE {}\n'
        for i, (name, typ, nets) in enumerate(devices):
            x, y = 0, i * 200
            text += f'C {{test/{typ}}} {x} {y} 0 0 {{name={name} W=0.65 L=0.15 m=2}}\n'
            for j, ((px, py), net) in enumerate(zip([(20, 30), (-20, 0), (20, -30), (20, 0)], nets)):
                text += f'C {{devices/lab_pin}} {px} {y+py} 0 0 {{name=l{i}_{j} lab={net}}}\n'
        for i, (name, typ) in enumerate([('A', 'ipin'), ('B', 'ipin'), ('Y', 'opin')]):
            text += f'C {{devices/{typ}}} -200 {i*60} 0 0 {{name=p{i} lab={name}}}\n'
        self.source.write_text(text)

    def test_parallel_and_series_nand_keep_ordered_pins_and_parameters(self):
        before = si.inspect_source(self.source, self.root)
        original = self.source.read_text()
        candidate = self.source.with_name('draft.sch')
        candidate.write_text(sl.plan(self.source, self.root))
        after = si.inspect_source(candidate, self.root)
        self.assertEqual(self.source.read_text(), original)
        self.assertEqual(sl.topology(before), sl.topology(after))
        self.assertEqual(before['ports'], after['ports'])
        self.assertFalse(after['issues'])
        placements = {si.attributes(f[-1]).get('name'): tuple(map(float, f[2:4]))
                      for _, f in si.records(candidate.read_text()) if f[0] == 'C'}
        self.assertEqual(placements['P1'][1], placements['P2'][1])
        self.assertLess(placements['P1'][1], placements['N1'][1])
        self.assertEqual(placements['N1'][0], placements['N2'][0])
        self.assertLess(placements['N1'][1], placements['N2'][1])
        # Supply segments retain one electrical name and no duplicated edges.
        supply = [f for _, f in si.records(candidate.read_text()) if f[0] == 'N' and f[-1] == 'lab=VPWR']
        edges = [tuple(sorted([tuple(f[1:3]), tuple(f[3:5])])) for f in supply]
        self.assertEqual(len(edges), len(set(edges)))

    def native(self, statements=None, errors=None, returncode=0):
        calls = []
        def netlist(source, out_dir):
            out_dir.mkdir()
            path = out_dir / 'sheet.spice'
            index = len(calls)
            path.write_text((statements or ['.subckt sheet A B Y\nX1 Y A mid VNB nfet W=0.65\n.ends\n'] * 2)[index])
            calls.append(source)
            return SimpleNamespace(metadata={'netlist': str(path), 'returncode': returncode},
                                   errors=(errors or [[], []])[index])
        return netlist

    def test_new_native_errors_and_terminal_changes_leave_original_untouched(self):
        original = self.source.read_text()
        attempts = [self.native(errors=[[], ['Error: undriven node: surprise']]),
                    self.native(statements=['X1 Y A VGND VNB nfet W=0.65', 'X1 A Y VGND VNB nfet W=0.65']),
                    self.native(returncode=2)]
        for attempt in attempts:
            with patch.object(sl.custom_bridge, 'netlist_schematic', attempt):
                with self.assertRaises(ValueError): sl.redraft(self.source, self.root)
            self.assertEqual(self.source.read_text(), original)
            self.assertFalse(self.source.with_suffix('.layout.json').exists())

    def test_existing_well_diagnostic_is_recorded_and_invalidated_on_source_change(self):
        error = 'Error: undriven node: VNB'
        with patch.object(sl.custom_bridge, 'netlist_schematic', self.native(errors=[[error], [error]])):
            result = sl.redraft(self.source, self.root)
        self.assertTrue(result['native_netlist_identical'])
        self.assertFalse(result['native_erc_clean'])
        review = si.inspect('analog/test/sheet', self.root)
        self.assertIn(error, review['issues'][0]['message'])
        self.assertEqual(review['layout'], json.loads(self.source.with_suffix('.layout.json').read_text()))
        self.source.write_text(self.source.read_text() + '\n')
        changed = si.inspect('analog/test/sheet', self.root)
        self.assertNotIn('layout', changed)
        self.assertFalse(changed['issues'])

    def test_symbol_change_invalidates_native_provenance(self):
        with patch.object(sl.custom_bridge, 'netlist_schematic', self.native()): sl.redraft(self.source, self.root)
        symbol = self.root / 'pdk/sky130A/libs.tech/xschem/test/nmos.sym'
        symbol.write_text(symbol.read_text() + '\n')
        self.assertNotIn('layout', si.inspect('analog/test/sheet', self.root))

    def test_activation_without_native_verification_is_refused(self):
        original = self.source.read_text()
        with self.assertRaisesRegex(ValueError, 'native verification'):
            sl.redraft(self.source, self.root, verify_native=False)
        self.assertEqual(self.source.read_text(), original)

    def test_native_comparison_preserves_terminal_order_and_symbolic_parameters(self):
        self.assertEqual(sl.native_statements('* comment\nX1 D G S B model W={wp}\n+ L=0.15\n'),
                         sl.native_statements('X1 D G S B model W={wp} L=0.15\n'))
        self.assertNotEqual(sl.native_statements('X1 D G S B model'), sl.native_statements('X1 S G D B model'))


if __name__ == '__main__': unittest.main()
