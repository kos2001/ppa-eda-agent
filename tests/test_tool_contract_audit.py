import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
import tool_contract_audit as audit
import orchestrator
import implementation_contract
import run_stage


class ContractAuditTests(unittest.TestCase):
    def make_run(self, directory, derate=True):
        run = Path(directory)
        (run / 'final/sdc').mkdir(parents=True)
        (run / '06-yosys-synthesis').mkdir()
        (run / '06-yosys-synthesis/tool.log').write_text('ABC: mapped successfully\n')
        cfg = {'CLOCK_PERIOD': 13.2, 'TIME_DERATING_CONSTRAINT': 5,
               'IO_DELAY_CONSTRAINT': 20, 'STA_CORNERS': ['ss', 'ff']}
        (run / 'resolved.json').write_text(json.dumps(cfg))
        metrics = {f'timing__{kind}__ws__corner:{corner}': 0.1
                   for kind in ['setup', 'hold'] for corner in ['ss', 'ff']}
        (run / 'final/metrics.json').write_text(json.dumps(metrics))
        sdc = 'create_clock -name clk -period 13.2000 [get_ports {clk}]\n'
        sdc += 'set_input_delay 2.6400 -clock clk [get_ports d]\n'
        if derate:
            sdc += 'set_timing_derate -early 0.9500\nset_timing_derate -late 1.0500\n'
        (run / 'final/sdc/design.sdc').write_text(sdc)
        return run

    def test_effective_values_not_json_number_spelling_determine_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.make_run(directory)
            self.assertTrue(audit.audit_run(run)['verified'])

    def test_old_integer_division_is_detected_and_blocks_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.make_run(directory, derate=False)
            result = audit.audit_run(run)
            self.assertTrue(any('late derating' in e for e in result['errors']))
            verdict = {'passed': True, 'violations': [], 'unverified': []}
            audit.attach(verdict, result)
            self.assertFalse(verdict['passed'])

    def test_missing_corner_cannot_hide_behind_positive_aggregate(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.make_run(directory)
            path = run / 'final/metrics.json'
            data = json.loads(path.read_text())
            del data['timing__hold__ws__corner:ss']
            data['timing__hold__ws'] = 0.1
            path.write_text(json.dumps(data))
            self.assertFalse(audit.audit_run(run)['verified'])

    def test_wrong_io_delay_and_driver_diagnostic_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            run = self.make_run(directory)
            p = run / 'final/sdc/design.sdc'
            p.write_text(p.read_text().replace('2.6400', '0.0000'))
            (run / '06-yosys-synthesis/tool.log').write_text('ABC: Cannot find the default PI driving cell (inv/ZN) in the library.')
            result = audit.audit_run(run)
            self.assertEqual(len(result['errors']), 2)

    def test_missing_artifacts_are_unknown_not_clean(self):
        with tempfile.TemporaryDirectory() as directory:
            result = audit.audit_run(Path(directory))
            self.assertFalse(result['verified'])
            self.assertTrue(result['unverified'])


class MetricContractTests(unittest.TestCase):
    def test_integer_inputs_are_decimal_at_the_tcl_boundary(self):
        actual = run_stage.decimal_constraint_overrides(
            {'CLOCK_PERIOD': 13, 'TIME_DERATING_CONSTRAINT': 5},
            ['CTS_DELAY_BUFFER_DERATE_PCT=25', 'IO_DELAY_CONSTRAINT=20', 'SYNTH_STRATEGY=DELAY 2'])
        self.assertIn('CLOCK_PERIOD=13.0', actual)
        self.assertIn('TIME_DERATING_CONSTRAINT=5.0', actual)
        self.assertIn('CTS_DELAY_BUFFER_DERATE_PCT=25.0', actual)
        self.assertIn('IO_DELAY_CONSTRAINT=20.0', actual)
        self.assertIn('SYNTH_STRATEGY=DELAY 2', actual)

    def test_override_wins_and_inherited_defaults_are_not_invented(self):
        self.assertEqual(run_stage.decimal_constraint_overrides(
            {'CLOCK_PERIOD': 13}, ['CLOCK_PERIOD=12.5']), ['CLOCK_PERIOD=12.5'])
        self.assertEqual(run_stage.decimal_constraint_overrides({}, []), [])

    def clean(self):
        return {**{key: 0 for key, _ in orchestrator.SIGNOFF_METRICS},
                'timing__setup__wns': 0, 'timing__hold__wns': 0}

    def test_nan_none_and_bool_slack_cannot_pass(self):
        for value in [float('nan'), float('inf'), None, True, '0']:
            with self.subTest(value=value):
                metrics = self.clean()
                metrics['timing__setup__wns__corner:ss'] = value
                self.assertFalse(orchestrator.score(metrics, {})['passed'])

    def test_boolean_zero_count_cannot_pass(self):
        metrics = self.clean()
        metrics['magic__drc_error__count'] = False
        self.assertFalse(orchestrator.score(metrics, {})['passed'])

    def test_negative_ws_cannot_hide_behind_zero_wns(self):
        metrics = self.clean()
        metrics['timing__setup__ws__corner:ss'] = -0.5
        self.assertFalse(orchestrator.score(metrics, {})['passed'])

    def test_filler_area_does_not_change_cell_area_budget(self):
        metrics = {**self.clean(), 'design__instance__area': 17763.6,
                   'design__instance__area__stdcell': 10187.9}
        verdict = orchestrator.score(metrics, {'max_cell_area_um2': 11000})
        self.assertTrue(verdict['passed'])
        self.assertEqual(verdict['area_um2'], 10187.9)
        self.assertEqual(verdict['raw_instance_area_um2'], 17763.6)

    def test_invalid_explicit_stdcell_does_not_fall_back_to_total(self):
        metrics = {'design__instance__area__stdcell': None, 'design__instance__area': 10}
        self.assertEqual(implementation_contract.target_checks(metrics, {'max_cell_area_um2': 20})[0]['status'], 'unverified')
