import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'pipeline'))
from surrogate_benchmark import availability, encode, evaluate, predict_model, temporal_folds


def history(count=16):
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    return [{'id': str(i), 'group_id': 'case-' + str(i),
             'timestamp': (start + timedelta(days=i)).isoformat(),
             'cohort_key': 'same', 'candidate_key': 'candidate-' + str(i),
             'eligible': True, 'design': 'test', 'flow': 'Classic',
             'scl': 'sky130_fd_sc_hd', 'pdk': 'sky130A',
             'overrides': {'FP_CORE_UTIL': 20 + i}, 'declared': {},
             'targets': {'area_um2': 100 + 3 * i}} for i in range(count)]


class TemporalBenchmarkTests(unittest.TestCase):
    def test_strictly_prior_day_cohort_group_and_novel_config(self):
        rows = history()
        rows[1]['timestamp'] = rows[-1]['timestamp']
        rows[2]['cohort_key'] = 'other'
        rows[3]['group_id'] = rows[-1]['group_id']
        rows[4]['candidate_key'] = rows[5]['candidate_key']
        test, train, reason = list(temporal_folds(rows, 'area_um2'))[-1]
        self.assertEqual(test['id'], '15')
        self.assertIsNone(reason)
        self.assertNotIn('1', [r['id'] for r in train])
        self.assertNotIn('2', [r['id'] for r in train])
        self.assertNotIn('3', [r['id'] for r in train])
        self.assertNotIn('4', [r['id'] for r in train])
        self.assertIn('5', [r['id'] for r in train])
        self.assertTrue(all(r['timestamp'] < test['timestamp'] for r in train))

    def test_future_labels_do_not_change_earlier_predictions(self):
        rows = history()
        before = evaluate(rows, models=['mean', 'knn'], targets=['area_um2'])
        rows[-1]['targets']['area_um2'] = 1e9
        later = history(17)[-1]
        later['targets']['area_um2'] = 1e12
        after = evaluate(rows + [later], models=['mean', 'knn'], targets=['area_um2'])
        for name in ['mean', 'knn']:
            old = before['targets']['area_um2']['predictions'][name]
            new = after['targets']['area_um2']['predictions'][name]
            self.assertEqual(old[:-1], new[:len(old)-1])
            self.assertEqual(old[-1]['prediction'], new[len(old)-1]['prediction'])

    def test_repeated_test_config_only_scores_first_observation(self):
        rows = history()
        repeat = copy.deepcopy(rows[10])
        repeat.update(id='repeat', timestamp='2025-03-01T00:00:00+00:00')
        folds = list(temporal_folds(rows + [repeat], 'area_um2'))
        self.assertEqual(len(folds), 16)

    def test_unknown_nonfinite_and_unsupported_rows_never_become_success(self):
        rows = history()
        rows[0]['eligible'] = False
        rows[1]['targets']['area_um2'] = None
        rows[2]['targets']['area_um2'] = float('nan')
        rows[3]['targets']['area_um2'] = True
        for r in rows[4:]:
            r['flow'] = 'MacroFanoutRepair'
        report = evaluate(rows, models=['mean', 'knn'], targets=['area_um2'])
        self.assertEqual(report['status'], 'insufficient_compatible_history')
        self.assertFalse(report['selection_policy_changed'])
        self.assertEqual(report['targets']['area_um2']['models']['knn']['refused'],
                         {'unsupported_flow_features': 12})
        json.dumps(report, allow_nan=False)

    def test_empty_history_is_explicit_abstention(self):
        report = evaluate([], models=['knn'], targets=['area_um2'])
        self.assertIsNone(report['targets']['area_um2']['models']['knn']['mae'])
        self.assertEqual(report['status'], 'insufficient_compatible_history')

    def test_repair_features_score_without_enabling_legacy_knn(self):
        rows = history()
        for row in rows:
            row['flow'] = 'FanoutRepair'
            row['overrides'].update(FANOUT_REPAIR_LIMIT=8, FANOUT_REPAIR_ROUNDS=1,
                FANOUT_REPAIR_SIGNAL_CELL='sky130_fd_sc_hd__buf_4',
                FANOUT_REPAIR_CLOCK_CELL='sky130_fd_sc_hd__clkbuf_8')
        report = evaluate(rows, models=['mean', 'knn', 'feature_knn'], targets=['area_um2'])
        models = report['targets']['area_um2']['models']
        self.assertEqual(models['feature_knn']['scored'], 8)
        self.assertEqual(models['knn']['scored'], 0)
        self.assertEqual(models['knn']['refused']['legacy_knn_unsupported_flow'], 8)
        changed = copy.deepcopy(rows[-1])
        changed['overrides']['FANOUT_REPAIR_NET_LIMITS'] = '{"new_net":4}'
        with self.assertRaisesRegex(ValueError, 'unseen_categorical'):
            predict_model('feature_knn', rows[:-1], changed, 'area_um2')
        self.assertFalse(report['selection_policy_changed'])

    def test_encoder_does_not_learn_scale_or_categories_from_query(self):
        rows = history()
        first = encode(rows[:8], rows[8])
        extreme = copy.deepcopy(rows[8])
        extreme['overrides']['FP_CORE_UTIL'] = 100000
        second = encode(rows[:8], extreme)
        self.assertEqual(first[0], second[0])
        self.assertIn('FP_CORE_UTIL', second[2])
        extreme['overrides']['SYNTH_STRATEGY'] = 'unknown'
        with self.assertRaisesRegex(ValueError, 'unseen_categorical'):
            encode(rows[:8], extreme)

    @unittest.skipUnless(availability()['ridge'], 'optional scikit-learn backend')
    def test_alternatives_fit_and_score_only_paired_temporal_folds(self):
        report = evaluate(history(), targets=['area_um2'])
        target = report['targets']['area_um2']
        for name in ['ridge', 'extra_trees', 'gaussian_process']:
            self.assertEqual(target['models'][name]['scored'], 8)
            self.assertEqual(target['paired_against_knn'][name]['paired_folds'], 8)
            self.assertEqual(target['paired_against_feature_knn'][name]['paired_folds'], 8)
        self.assertLess(target['models']['ridge']['mae'], target['models']['mean']['mae'])
        gp = target['predictions']['gaussian_process'][0]['prediction']
        self.assertGreaterEqual(gp['posterior_std_uncalibrated'], 0)
        self.assertFalse(report['model_qualified'])

    def test_invalid_policy_inputs_rejected(self):
        with self.assertRaises(ValueError):
            evaluate(history(), models=['made_up'])
        with self.assertRaises(ValueError):
            evaluate(history(), min_train=2)


if __name__ == '__main__':
    unittest.main()
