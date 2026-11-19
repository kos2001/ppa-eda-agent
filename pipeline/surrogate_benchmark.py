"""Offline, provenance-isolated forward evaluation; never selects EDA candidates."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import statistics

import surrogate
from surrogate_dataset import load_dataset
from surrogate_repair_features import featurize, SCHEMA_VERSION as FEATURE_SCHEMA_VERSION

MODELS = ('mean', 'knn', 'feature_knn', 'ridge', 'extra_trees', 'gaussian_process')
TARGETS = ('area_um2', 'power_w', 'worst_setup_slack', 'seconds')


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def availability():
    result = {'mean': 'stdlib', 'knn': 'existing fixed k=1', 'feature_knn': 'stdlib encoded features k=1'}
    try:
        import sklearn
        result.update({name: 'scikit-learn ' + sklearn.__version__ for name in MODELS[3:]})
    except ImportError:
        result.update({name: None for name in MODELS[3:]})
    return result


def temporal_folds(rows, field, min_train=8):
    """First observations as tests; training repeats use latest prior day.

    Exclude the entire held-out experiment and repeated target configuration.
    Equal timestamps never train each other (date-only records share midnight).
    Repeats tied within a prior day use lexicographically last id, not an
    inferred intraday measurement order.
    """
    if min_train < 2:
        raise ValueError('min_train must be at least 2')
    usable = sorted((r for r in rows if r['eligible'] and finite(r['targets'].get(field))),
                    key=lambda r: (r['timestamp'], r['id']))
    tested = set()
    for test in usable:
        identity = (test['cohort_key'], test['candidate_key'])
        if identity in tested:
            continue
        tested.add(identity)
        prior = {}
        for row in usable:
            if (row['timestamp'] < test['timestamp']
                    and row['cohort_key'] == test['cohort_key']
                    and row['group_id'] != test['group_id']
                    and row['candidate_key'] != test['candidate_key']):
                prior[row['candidate_key']] = row
        train = list(prior.values())
        yield test, train, None if len(train) >= min_train else 'insufficient_prior_compatible_configs'


def encode(train, test):
    """Fit numeric scales and categorical vocabulary using training inputs only."""
    features = [featurize(row) for row in train]
    query = featurize(test)
    matrix, vector = [[] for _ in train], []
    outside = []
    for key in sorted(set().union(*(f.keys() for f in features))):
        values = [f.get(key) for f in features]
        value = query.get(key)
        numbers = [v for v in values if finite(v)]
        if numbers:
            center = statistics.mean(numbers)
            scale = statistics.pstdev(numbers) or 1.0
            for row, v in zip(matrix, values):
                row.extend([(v - center) / scale if finite(v) else 0.0, float(not finite(v))])
            vector.extend([(value - center) / scale if finite(value) else 0.0,
                           float(not finite(value))])
            if finite(value) and not min(numbers) <= value <= max(numbers):
                outside.append(key)
        else:
            categories = sorted({str(v) for v in values if v is not None})
            if value is not None and str(value) not in categories:
                raise ValueError('unseen_categorical_feature:' + key)
            for row, v in zip(matrix, values):
                row.extend([float(v is not None and str(v) == c) for c in categories] + [float(v is None)])
            vector.extend([float(value is not None and str(value) == c) for c in categories] + [float(value is None)])
    return matrix, vector, outside


def predict_model(name, train, test, field):
    values = [r['targets'][field] for r in train]
    if name == 'mean':
        return {'value': statistics.mean(values)}
    if name == 'knn':
        if test.get('flow') != 'Classic' or any(r.get('flow') != 'Classic' for r in train):
            raise ValueError('legacy_knn_unsupported_flow')
        # Explicit k avoids selecting a tuned default using future corpus data.
        rows = [{**r, field: r['targets'][field]} for r in train]
        result = surrogate.predict(test, rows, field, k=1)
        return {'value': result.get('value'), 'reason': result.get('reason'),
                'uncertainty': 'neighbour distance is not a calibrated interval'}
    x, query, outside = encode(train, test)
    if name == 'feature_knn':
        nearest = min(range(len(x)), key=lambda i: sum((a-b)**2 for a, b in zip(x[i], query)))
        return {'value': values[nearest], 'outside_training_ranges': outside,
                'uncertainty': 'distance is not a calibrated interval'}
    if name == 'ridge':
        from sklearn.linear_model import Ridge
        model = Ridge(alpha=1.0)
    elif name == 'extra_trees':
        from sklearn.ensemble import ExtraTreesRegressor
        model = ExtraTreesRegressor(n_estimators=64, min_samples_leaf=2, random_state=0, n_jobs=1)
    elif name == 'gaussian_process':
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import Matern, WhiteKernel
        # Fixed prior: no hyperparameter selection using held-out labels.
        model = GaussianProcessRegressor(kernel=Matern(nu=2.5) + WhiteKernel(noise_level=1e-5),
                                         normalize_y=True, optimizer=None, random_state=0)
    else:
        raise ValueError('unknown model: ' + name)
    model.fit(x, values)
    result = {'value': float(model.predict([query])[0]), 'outside_training_ranges': outside}
    if name == 'gaussian_process':
        _, std = model.predict([query], return_std=True)
        result['posterior_std_uncalibrated'] = float(std[0])
    return result


def evaluate(rows, models=MODELS, targets=TARGETS, min_train=8):
    if set(models) - set(MODELS) or set(targets) - set(TARGETS):
        raise ValueError('unknown model or target')
    if min_train < surrogate.MIN_SAMPLES:
        raise ValueError('comparison requires at least the existing kNN minimum of 8')
    available = availability()
    reports = {}
    for field in targets:
        predictions, refused = {m: [] for m in models}, {m: Counter() for m in models}
        folds = list(temporal_folds(rows, field, min_train))
        for test, train, reason in folds:
            try:
                for row in [test, *train]:
                    featurize(row)
            except ValueError as error:
                reason = ('unsupported_flow_features' if str(error).startswith('unsupported_flow_features:')
                          else 'invalid_features:' + str(error))
            for name in models:
                if reason or available[name] is None:
                    refused[name][reason or 'optional_dependency_unavailable'] += 1
                    continue
                try:
                    prediction = predict_model(name, train, test, field)
                except (ImportError, ValueError, ArithmeticError) as error:
                    refused[name][str(error)] += 1
                    continue
                value = prediction.get('value')
                if not finite(value):
                    refused[name][prediction.get('reason') or 'nonfinite_prediction'] += 1
                    continue
                truth = test['targets'][field]
                baseline = statistics.mean(r['targets'][field] for r in train)
                predictions[name].append({
                    'test_id': test['id'], 'timestamp': test['timestamp'],
                    'cohort_key': test['cohort_key'], 'training_ids': [r['id'] for r in train],
                    'truth': truth, 'prediction': prediction,
                    'absolute_error': abs(value - truth),
                    'mean_baseline_absolute_error': abs(baseline - truth)})
        metrics = {}
        for name, scored in predictions.items():
            mae = statistics.mean(r['absolute_error'] for r in scored) if scored else None
            baseline = statistics.mean(r['mean_baseline_absolute_error'] for r in scored) if scored else None
            metrics[name] = {'scored': len(scored), 'refused': dict(refused[name]),
                             'mae': mae, 'mean_baseline_mae_same_folds': baseline,
                             'mae_improvement_pct': 100 * (baseline - mae) / baseline if baseline else None}
        comparisons = {}
        knn = {r['test_id']: r for r in predictions.get('knn', [])}
        for name in models:
            if name in ('mean', 'knn'):
                continue
            common = [(r, knn[r['test_id']]) for r in predictions[name] if r['test_id'] in knn]
            comparisons[name] = {'paired_folds': len(common),
                'mae': statistics.mean(a['absolute_error'] for a, b in common) if common else None,
                'knn_mae': statistics.mean(b['absolute_error'] for a, b in common) if common else None}
        reports[field] = {'test_configurations': len(folds), 'models': metrics,
                          'paired_against_knn': comparisons, 'predictions': predictions}
        feature_knn = {r['test_id']: r for r in predictions.get('feature_knn', [])}
        repair_comparisons = {}
        for name in models:
            if name in ('mean', 'knn', 'feature_knn'):
                continue
            common = [(r, feature_knn[r['test_id']]) for r in predictions[name]
                      if r['test_id'] in feature_knn]
            repair_comparisons[name] = {'paired_folds': len(common),
                'mae': statistics.mean(a['absolute_error'] for a, b in common) if common else None,
                'feature_knn_mae': statistics.mean(b['absolute_error'] for a, b in common) if common else None}
        reports[field]['paired_against_feature_knn'] = repair_comparisons
    return {'mode': 'offline_shadow_only', 'selection_policy_changed': False,
            'model_qualified': False, 'feature_schema_version': FEATURE_SCHEMA_VERSION, 'model_backends': available, 'min_prior_configurations': min_train,
            'split': 'strictly earlier timestamps; disjoint experiment and candidate identity; same cohort only',
            'status': 'evaluated' if any(m['scored'] for r in reports.values() for m in r['models'].values())
                      else 'insufficient_compatible_history',
            'targets': reports,
            'limitations': ['No calibrated prediction intervals or signoff probability',
                           'Encoded-feature models refuse unseen repair topology; mean baseline ignores topology; live kNN unchanged',
                           'Offline errors do not establish wall-time savings or Pareto improvement',
                           'No automatic model promotion; no hyperparameter search on test folds']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refdb', type=Path, default=surrogate.REFDB)
    parser.add_argument('--out', type=Path)
    parser.add_argument('--models', nargs='+', choices=MODELS, default=list(MODELS))
    args = parser.parse_args()
    dataset = load_dataset(args.refdb)
    report = {'schema_version': 1, 'dataset': {k: v for k, v in dataset.items() if k != 'rows'},
              'evaluation': evaluate(dataset['rows'], models=args.models)}
    report['source_sha256'] = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
        for name in ('surrogate_benchmark.py', 'surrogate_dataset.py', 'surrogate.py', 'surrogate_repair_features.py')}
    report['environment'] = {'python': __import__('platform').python_version(),
        'packages': {name: importlib.metadata.version(name) for name in ('scikit-learn', 'numpy', 'scipy')
                     if name in {d.metadata['Name'] for d in importlib.metadata.distributions()}}}
    text = json.dumps(report, indent=2, allow_nan=False) + '\n'
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding='utf-8')
    else:
        print(text, end='')


if __name__ == '__main__':
    main()
