import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import surrogate
from surrogate_repair_features import SCHEMA_VERSION, featurize


def candidate():
    return {'flow': 'FanoutRepair', 'scl': 'sky130_fd_sc_hd', 'pdk': 'sky130A',
            'declared': {'CLOCK_PERIOD': 10, 'FP_CORE_UTIL': 25},
            'overrides': {'FANOUT_REPAIR_LIMIT': 8,
                          'FANOUT_REPAIR_SIGNAL_CELL': 'sky130_fd_sc_hd__buf_4',
                          'FANOUT_REPAIR_CLOCK_CELL': 'sky130_fd_sc_hd__clkbuf_8'}}


class RepairFeatureTests(unittest.TestCase):
    def test_explicit_base_axes_reject_malformed_values_for_both_flows(self):
        bad = [(key, value)
               for key in ('FP_CORE_UTIL', 'PL_TARGET_DENSITY_PCT', 'CLOCK_PERIOD')
               for value in (True, False, '35', None, float('nan'), float('inf'))]
        bad += [('DIE_AREA', value) for value in (
            [0, 0, 10], [0, 0, True, 10], [0, 0, float('inf'), 10],
            [0, 0, float('nan'), 10], [0, 0, '10', 10],
            [10, 0, 0, 10], [0, 0, 0, 10], [0, 10, 10, 0], None)]
        bad += [('SYNTH_STRATEGY', value) for value in ('', ' ', True, 1, None)]
        for flow in ('Classic', 'FanoutRepair'):
            for key, value in bad:
                with self.subTest(flow=flow, key=key, value=value):
                    row = candidate() if flow == 'FanoutRepair' else {
                        'flow': flow, 'declared': {}, 'overrides': {}}
                    row['declared'][key] = value
                    with self.assertRaises(ValueError):
                        featurize(row)

    def test_valid_base_axes_preserve_numeric_features(self):
        row = candidate()
        row['overrides'].update(FP_CORE_UTIL=40, PL_TARGET_DENSITY_PCT=55.5,
                                CLOCK_PERIOD=12.0, DIE_AREA=[-1, -2, 9, 8],
                                SYNTH_STRATEGY='AREA 0')
        features = featurize(row)
        self.assertEqual(features['FP_CORE_UTIL'], 40)
        self.assertEqual(features['PL_TARGET_DENSITY_PCT'], 55.5)
        self.assertEqual(features['die_area_um2'], 100)
        self.assertEqual(features['SYNTH_STRATEGY'], 'AREA 0')

    def test_absent_maps_differ_from_explicit_empty_and_null_is_invalid(self):
        row = candidate()
        unknown = featurize(row)
        for key in ('NET_LIMITS', 'DIRECT_SINKS', 'DRIVER_CELLS'):
            row['overrides']['FANOUT_REPAIR_' + key] = '{}'
        explicit = featurize(row)
        self.assertNotEqual(unknown['repair_topology_fingerprint'], explicit['repair_topology_fingerprint'])
        self.assertNotEqual(unknown['repair_net_limits'], explicit['repair_net_limits'])
        self.assertEqual(explicit['repair_net_count'], 0)
        self.assertEqual(explicit['repair_driver_count'], 0)
        row['overrides']['FANOUT_REPAIR_NET_LIMITS'] = None
        with self.assertRaises(ValueError):
            featurize(row)

    def test_headroom_uses_explicit_net_limit_not_global_or_guessed_default(self):
        row = candidate()
        row['overrides']['FANOUT_REPAIR_DIRECT_SINKS'] = {'n': ['a/A', 'b/A']}
        self.assertIsNone(featurize(row)['repair_direct_headroom_known'])
        row['overrides']['FANOUT_REPAIR_NET_LIMITS'] = {'n': 2}
        with self.assertRaises(ValueError):
            featurize(row)
        row['overrides']['FANOUT_REPAIR_NET_LIMITS'] = {'n': 3}
        self.assertEqual(featurize(row)['repair_direct_headroom_known'], 1)
        row['overrides'].update(DIODE_TRIM_KEEP=4)
        self.assertIsNone(featurize(row)['DIODE_TRIM_MIN_DIODES'])
        row['overrides']['DIODE_TRIM_MIN_DIODES'] = 4
        with self.assertRaises(ValueError):
            featurize(row)

    def test_absent_controls_unknown_and_override_precedence(self):
        row = candidate()
        row['overrides']['FP_CORE_UTIL'] = 35
        f = featurize(row)
        self.assertEqual(SCHEMA_VERSION, 2)
        self.assertEqual(f['FP_CORE_UTIL'], 35)
        self.assertEqual(f['CLOCK_PERIOD'], 10)
        self.assertIsNone(f['FANOUT_REPAIR_ROUNDS'])
        self.assertIsNone(f['DIODE_TRIM_KEEP'])
        self.assertIsNone(f['repair_driver_count'])

    def test_no_outcome_or_provenance_leakage_or_mutation(self):
        row = candidate()
        before = copy.deepcopy(row)
        f = featurize(row)
        self.assertEqual(row, before)
        row.update(verdict={'area_um2': 999}, targets={'seconds': 999},
                   evaluation_provenance={'complete': False}, topology={'buffer_count': 999},
                   data={'inserted_buffers': 999}, seconds=999, eligible=False)
        self.assertEqual(f, featurize(row))

    def test_exact_topology_distinguishes_equal_counts(self):
        first, second = candidate(), candidate()
        first['overrides']['FANOUT_REPAIR_NET_LIMITS'] = '{"netA":4}'
        second['overrides']['FANOUT_REPAIR_NET_LIMITS'] = '{"netB":4}'
        a, b = featurize(first), featurize(second)
        self.assertEqual(a['repair_net_count'], b['repair_net_count'])
        self.assertEqual(a['repair_net_limit_sum'], b['repair_net_limit_sum'])
        self.assertNotEqual(a['repair_topology_fingerprint'], b['repair_topology_fingerprint'])
        for key, one, two in [
            ('FANOUT_REPAIR_DIRECT_SINKS', {'net': ['a/A']}, {'net': ['b/A']}),
            ('FANOUT_REPAIR_DRIVER_CELLS', {'a': 'cell1'}, {'a': 'cell2'}),
        ]:
            first, second = candidate(), candidate()
            first['overrides'][key], second['overrides'][key] = one, two
            self.assertNotEqual(featurize(first)['repair_topology_fingerprint'],
                                featurize(second)['repair_topology_fingerprint'])

    def test_equivalent_json_order_and_direct_sets(self):
        row = candidate()
        row['overrides']['FANOUT_REPAIR_DIRECT_SINKS'] = '{"n":["b/A","a/A"]}'
        f = featurize(row)
        row['overrides']['FANOUT_REPAIR_DIRECT_SINKS'] = {'n': ['a/A', 'b/A']}
        self.assertEqual(f, featurize(row))
        self.assertEqual(f['repair_direct_sink_count'], 2)

    def test_invalid_switches_maps_and_headroom_fail_closed(self):
        bad = [
            ('FANOUT_REPAIR_LIMIT', True), ('FANOUT_REPAIR_LIMIT', 1),
            ('FANOUT_REPAIR_ROUNDS', 4), ('FANOUT_REPAIR_ROUNDS', '2'),
            ('FANOUT_REPAIR_ENABLED', True), ('FANOUT_REPAIR_SIGNAL_CELL', 'inverter'),
            ('FANOUT_REPAIR_NET_LIMITS', '{"n":2,"n":3}'),
            ('FANOUT_REPAIR_NET_LIMITS', {'n': True}),
            ('FANOUT_REPAIR_NET_LIMITS', []),
            ('FANOUT_REPAIR_DIRECT_SINKS', {'n': ['a/A', 'a/A']}),
            ('FANOUT_REPAIR_DIRECT_SINKS', {'n': []}),
            ('FANOUT_REPAIR_DIRECT_SINKS', {'n': ['bad']}),
            ('FANOUT_REPAIR_DIRECT_SINKS', {'n': [f'a{i}/A' for i in range(8)]}),
            ('FANOUT_REPAIR_DRIVER_CELLS', {'x': False}),
            ('DIODE_TRIM_KEEP', True), ('DIODE_TRIM_KEEP', -1),
            ('DIODE_TRIM_ENABLED', True), ('MACRO_BUFFER_PINS', '["u/clk"]'),
        ]
        for key, value in bad:
            with self.subTest(key=key, value=value):
                row = candidate()
                row['overrides'][key] = value
                if key == 'FANOUT_REPAIR_DIRECT_SINKS':
                    row['overrides']['FANOUT_REPAIR_NET_LIMITS'] = '{}'
                with self.assertRaises(ValueError):
                    featurize(row)

    def test_classic_and_unsupported_flow(self):
        row = {'flow': 'Classic', 'declared': {}, 'overrides': {'FP_CORE_UTIL': 30}}
        self.assertEqual(featurize(row), surrogate.featurize(row))
        for flow in ('MacroFanoutRepair', None, 'unknown'):
            with self.assertRaises(ValueError):
                featurize({**row, 'flow': flow})
        row['overrides']['FANOUT_REPAIR_ROUNDS'] = 1
        with self.assertRaises(ValueError):
            featurize(row)

    def test_numerical_knobs_do_not_change_structural_identity(self):
        row = candidate()
        initial = featurize(row)
        row['overrides'].update(FANOUT_REPAIR_LIMIT=12, FANOUT_REPAIR_ROUNDS=3,
                                DIODE_TRIM_KEEP=2, DIODE_TRIM_MIN_DIODES=10)
        changed = featurize(row)
        self.assertNotEqual(initial['FANOUT_REPAIR_LIMIT'], changed['FANOUT_REPAIR_LIMIT'])
        self.assertEqual(initial['repair_topology_fingerprint'], changed['repair_topology_fingerprint'])
        json.dumps(changed, allow_nan=False)


if __name__ == '__main__':
    unittest.main()
