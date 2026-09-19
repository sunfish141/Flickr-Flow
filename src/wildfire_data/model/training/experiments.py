"""CPU-bounded model/feature experiments over the verified public CSV release.

python -m wildfire_data.model.training.experiments --help
"""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import time
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.exceptions import ConvergenceWarning
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import KBinsDiscretizer, StandardScaler
from threadpoolctl import threadpool_limits

from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.estimators import CalibratedEstimator, GEOMETRY_COLUMNS
from wildfire_data.model.features.experiment_features import (
    PROFILES, add_context, add_optional_weather, profile_contract,
)
from wildfire_data.model.incident_transition import probability_logit
from wildfire_data.model.training.dataset import load_dataset, cohort, TARGET, WEATHER_INPUTS
from wildfire_data.model.training.public_csv import PARAMETERS, metrics, write_json

KIND = 'lightweight-observed-row-experiments/v1'
HOLDOUTS = ('held_incident', 'held_region', 'later_time')
DEFAULT_PROFILES = ('frontier', 'geometry', 'context', 'weather')
CANDIDATES = {
    'hgb_reference': {'family': 'hgb', 'parameters': PARAMETERS},
    'hgb_compact': {'family': 'hgb', 'parameters': {
        **PARAMETERS, 'max_iter': 100, 'learning_rate': .06}},
    'hgb_flexible': {'family': 'hgb', 'parameters': {
        **PARAMETERS, 'max_iter': 200, 'max_leaf_nodes': 15,
        'min_samples_leaf': 100, 'l2_regularization': 10.}},
    'logistic': {'family': 'logistic', 'parameters': {'C': 1., 'max_iter': 2000, 'random_state': 0}},
    'binned_logistic': {'family': 'binned_logistic', 'parameters': {'C': 1., 'max_iter': 2000, 'random_state': 0}},
}


def make_estimator(spec):
    if spec['family'] == 'hgb':
        return HistGradientBoostingClassifier(**spec['parameters'])
    # Imputation is internal, fitted on training folds, and accompanied by flags.
    # Missing rain/terrain in the source table never becomes an observed zero.
    steps = [SimpleImputer(strategy='median', add_indicator=True)]
    if spec['family'] == 'binned_logistic':
        steps.append(KBinsDiscretizer(n_bins=8, encode='onehot', strategy='quantile',
                                     quantile_method='averaged_inverted_cdf', subsample=None))
    elif spec['family'] == 'logistic':
        steps.append(StandardScaler())
    else:
        raise ValueError('Unknown estimator family')
    return make_pipeline(*steps, LogisticRegression(**spec['parameters']))


def fit_raw(frame, columns, spec):
    x = frame[list(columns)].to_numpy(dtype=float)
    if not len(x) or np.isinf(x).any() or set(frame[TARGET]) != {0, 1}:
        raise ValueError('Fitting requires nonempty binary rows without infinite inputs')
    supported = tuple(np.flatnonzero(~np.isnan(x).all(axis=0)).tolist())
    if not supported:
        raise ValueError('No supported features')
    estimator = make_estimator(spec)
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter('always')
        estimator.fit(x[:, supported], frame[TARGET].to_numpy(dtype=int))
    if any(issubclass(w.category, ConvergenceWarning) for w in captured):
        raise ValueError('Estimator did not converge; candidate is not eligible for selection')
    return estimator, supported, sorted({str(w.message) for w in captured})


def raw_predict(model, supported, frame, columns):
    x = frame[list(columns)].to_numpy(dtype=float)
    if np.isinf(x).any():
        raise ValueError('Infinite feature value')
    return model.predict_proba(x[:, supported])[:, 1]


def group_folds(training, count, seed):
    if set(training.incident_split) != {'train'}:
        raise ValueError('Development folds may only use declared training incidents')
    if training.incident_group_id.isna().any() or training.incident_group_id.eq('').any():
        raise ValueError('Missing incident groups')
    if count < 2 or training.incident_group_id.nunique() < count:
        raise ValueError('Insufficient incident groups for development folds')
    splitter = StratifiedGroupKFold(n_splits=count, shuffle=True, random_state=seed)
    folds = list(splitter.split(training, training[TARGET], training.incident_group_id))
    for train_idx, valid_idx in folds:
        a, b = training.iloc[train_idx], training.iloc[valid_idx]
        if set(a.incident_group_id) & set(b.incident_group_id):
            raise ValueError('Incident leakage in development split')
        if set(a[TARGET]) != {0, 1} or set(b[TARGET]) != {0, 1}:
            raise ValueError('Each development partition needs both classes; change fold count')
    return folds


def search(training, columns, folds, candidates):
    if set(training.incident_split) != {'train'}:
        raise ValueError('Model search requires declared training rows only')
    results = []
    for name, spec in candidates.items():
        scores, notices = [], set()
        started = time.perf_counter()
        try:
            for train_idx, valid_idx in folds:
                train, valid = training.iloc[train_idx], training.iloc[valid_idx]
                fitted, supported, fit_notices = fit_raw(train, columns, spec)
                notices.update(fit_notices)
                p = raw_predict(fitted, supported, valid, columns)
                scores.append(float(average_precision_score(valid[TARGET], p)))
            result = {'candidate': name, 'status': 'ok', 'fold_pr_auc': scores,
                      'mean_pr_auc': float(np.mean(scores)), 'std_pr_auc': float(np.std(scores)),
                      'fit_warnings': sorted(notices)}
        except ValueError as exc:
            result = {'candidate': name, 'status': 'failed', 'reason': str(exc)}
        result['cv_seconds'] = time.perf_counter() - started
        results.append(result)
        print(f"  {name}: {result.get('mean_pr_auc', result.get('reason'))}", flush=True)
    valid = [r for r in results if r['status'] == 'ok']
    if not valid:
        raise ValueError('All candidates failed')
    # Only development AP selects architecture. Calibration/test metrics are unavailable here.
    winner = min(valid, key=lambda r: (-r['mean_pr_auc'], r['candidate']))['candidate']
    return winner, results


def calibrated_fit(train, calibration, columns, spec):
    if set(train.incident_split) != {'train'} or set(calibration.incident_split) != {'calibration'}:
        raise ValueError('Final fit requires training and separate calibration cohorts')
    if set(train.incident_group_id) & set(calibration.incident_group_id):
        raise ValueError('Training/calibration incidents overlap')
    if set(calibration[TARGET]) != {0, 1}:
        raise ValueError('Calibration needs both classes')
    fitted, supported, notices = fit_raw(train, columns, spec)
    calibration_p = raw_predict(fitted, supported, calibration, columns)
    calibrator = LogisticRegression(C=1., random_state=0)
    calibrator.fit(probability_logit(calibration_p), calibration[TARGET])
    return CalibratedEstimator(fitted, calibrator, tuple(columns), supported), notices


def cohort_summary(frame):
    return {'rows': len(frame), 'incidents': int(frame.incident_group_id.nunique()),
            'positive_rows': int(frame[TARGET].sum()),
            'prevalence': float(frame[TARGET].mean()) if len(frame) else None}


def eligible(frame, profile):
    if profile == 'weather':
        # Historical six-hour windows may be missing as in the reference policy.
        return frame.loc[np.isfinite(frame[list(WEATHER_INPUTS)].to_numpy(dtype=float)).all(axis=1)]
    return frame


def paired_interval(frame, candidate_p, reference_p, repeats, seed):
    """Paired bootstrap of whole incidents, preserving their within-group rows."""
    if repeats <= 0 or frame.empty or frame.incident_group_id.nunique() < 2:
        return {'status': 'not_estimated'}
    groups = [np.asarray(idx) for idx in frame.groupby('incident_group_id', sort=True).indices.values()]
    rng, deltas = np.random.default_rng(seed), []
    y = frame[TARGET].to_numpy()
    for _ in range(repeats):
        idx = np.concatenate([groups[i] for i in rng.integers(0, len(groups), size=len(groups))])
        if len(np.unique(y[idx])) != 2:
            continue
        deltas.append(average_precision_score(y[idx], candidate_p[idx]) - average_precision_score(y[idx], reference_p[idx]))
    return ({'status': 'estimated', 'resamples': len(deltas),
             'pr_auc_delta_95_percentile': np.quantile(deltas, [.025, .975]).tolist()}
            if deltas else {'status': 'not_estimated'})


def benchmark(model, frame, path):
    if frame.empty:
        raise ValueError('Benchmark requires calibration rows')
    probe = frame.iloc[np.arange(1024) % len(frame)]
    x = probe[list(model.columns)].to_numpy(dtype=float)
    model.predict_proba(x)
    timings = []
    for _ in range(20):
        start = time.perf_counter()
        model.predict_proba(x)
        timings.append((time.perf_counter() - start) * 1000)
    start = time.perf_counter()
    reloaded = joblib.load(path)
    reload_ms = (time.perf_counter() - start) * 1000
    np.testing.assert_array_equal(model.predict_proba(x), reloaded.predict_proba(x))
    return {'artifact_bytes': path.stat().st_size, 'batch_rows': len(x),
            'median_batch_ms': float(np.median(timings)), 'p95_batch_ms': float(np.quantile(timings, .95)),
            'warm_filesystem_load_ms': reload_ms, 'reload_equal': True,
            'input_matrix_bytes': x.nbytes, 'peak_ram_bytes': None,
            'scope': 'local CPU estimator+calibration only; excludes features, maps, GIS and mobile runtime'}


def write_summary(path, manifest, selection, report, resources):
    lines = ['# Lightweight model experiment', '',
        f"Source manifest: `{manifest['source'].get('manifest_sha256', 'not supplied')}`", '',
        'Research results on observed rows; no model was promoted to the map.', '',
        '| Profile | Selected model | Development PR-AUC | Model KB | Local 1024-row p95 ms |',
        '| --- | --- | ---: | ---: | ---: |']
    for profile, selected in selection.items():
        name = selected['selected']
        score = next(s['mean_pr_auc'] for s in selected['development'] if s['candidate'] == name)
        cost = resources[profile][name]
        lines.append(f"| {profile} | {name} | {score:.4f} | {cost['artifact_bytes']/1000:.1f} | {cost['p95_batch_ms']:.2f} |")
    lines += ['', '| Holdout | Profile | Selected PR-AUC | Same-profile reference PR-AUC | Brier | Precision | Recall |',
              '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for split, profiles in report.items():
        for profile, selected in selection.items():
            result = profiles[profile]['models'][selected['selected']]
            if not result['rows']:
                continue
            baseline = profiles[profile]['models']['hgb_reference']
            lines.append(f"| {split} | {profile} | {result['pr_auc']:.4f} | {baseline['pr_auc']:.4f} | "
                         f"{result['brier_score']:.4f} | {result['precision']:.4f} | {result['recall']:.4f} |")
    lines += ['', 'Threshold 0.20 is fixed for individual models. The reference weather blend uses 0.15.',
        'See evaluation.json for prevalence, excluded rows, matched weather comparisons,',
        'missing-weather fallback, calibration metrics and paired incident bootstrap intervals.',
        'Local latency excludes feature construction and GIS. Phone latency, RAM and battery are unmeasured.',
        'Weather inputs are retrospective analysis; cached issued-forecast deployment requires separate validation.', '']
    path.write_text('\n'.join(lines), encoding='utf-8')


def run_experiments(frame, provenance, output, *, profiles=DEFAULT_PROFILES,
                    candidates=None, folds=3, seed=0, threads=2, bootstrap=200):
    candidates = CANDIDATES if candidates is None else candidates
    if threads < 1 or bootstrap < 0 or len(set(profiles)) != len(profiles) or not profiles:
        raise ValueError('Invalid thread/bootstrap/profile configuration')
    if any(name not in PROFILES for name in profiles) or 'hgb_reference' not in candidates:
        raise ValueError('Unknown profile or missing reference candidate')
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new output directory; experiment runs are immutable')
    frame = frame.copy()
    if 'context' in profiles:
        frame = add_context(frame)
    if 'weather' in profiles:
        frame = add_optional_weather(frame)
    assigned = frame.loc[frame.incident_split.ne('unassigned')]
    if assigned.incident_group_id.isna().any() or assigned.incident_group_id.eq('').any():
        raise ValueError('Missing assigned incident identity')
    if (assigned.groupby('incident_group_id').incident_split.nunique() > 1).any():
        raise ValueError('Incident groups overlap declared cohorts')
    training = cohort(frame, 'train', later_test_at=provenance['later_test_at'])
    calibration = cohort(frame, 'calibration', later_test_at=provenance['later_test_at'])
    development_dates = pd.to_datetime(frame.loc[frame.incident_split.isin(['train', 'calibration']),
                                               'source_snapshot_time'], utc=True, format='mixed')
    if development_dates.isna().any() or (development_dates >= pd.Timestamp(provenance['later_test_at'])).any():
        raise ValueError('Training/calibration reaches the later-time holdout or has missing dates')
    excluded_boundary_labels = {}
    complete_cohorts = []
    for name, partition in (('train', training), ('calibration', calibration)):
        ends = pd.to_datetime(partition.target_end_at, utc=True, format='mixed')
        cutoffs = pd.to_datetime(partition.feature_cutoff_at, utc=True, format='mixed')
        if ends.isna().any() or cutoffs.isna().any() or (ends <= cutoffs).any():
            raise ValueError('Development labels must end after their feature cutoffs')
        complete = ends < pd.Timestamp(provenance['later_test_at'])
        excluded_boundary_labels[name] = int((~complete).sum())
        complete_cohorts.append(partition.loc[complete])
    training, calibration = complete_cohorts
    # Require successful contracts/folds before publishing a planned run.
    partitions = {}
    for profile in profiles:
        missing = set(PROFILES[profile]) - set(frame.columns)
        if missing:
            raise ValueError(f'Missing {profile} columns: {sorted(missing)}')
        train, cal = eligible(training, profile), eligible(calibration, profile)
        if set(cal[TARGET]) != {0, 1}:
            raise ValueError(f'{profile} has insufficient calibration support')
        partitions[profile] = (train, cal, group_folds(train, folds, seed))
    output.mkdir(parents=True)
    protocol = {'kind': KIND, 'status': 'planned', 'source': provenance,
        'profiles': {p: profile_contract(p) for p in profiles}, 'candidates': candidates,
        'folds': folds, 'seed': seed, 'threads': threads, 'bootstrap_incidents': bootstrap,
        'selection': 'highest mean raw PR-AUC across incident-grouped training folds, separately per profile',
        'calibration': 'sigmoid on separate declared calibration incidents, after selection',
        'decision_threshold': .2, 'weather_reference_blend_threshold': .15,
        'threshold_policy': 'fixed in advance; no test tuning',
        'excluded_development_labels_crossing_test_boundary': excluded_boundary_labels,
        'deployment': 'research artifacts only; no automatic promotion or map substitution',
        'environment': {'python': platform.python_version(), 'platform': platform.platform(),
            'dependencies': {n: importlib.metadata.version(n) for n in
                ('numpy', 'pandas', 'scikit-learn', 'scipy', 'joblib', 'threadpoolctl')}},
        'cohorts': {p: {'train': cohort_summary(t), 'calibration': cohort_summary(c),
            'fold_validation_groups': [sorted(t.iloc[v].incident_group_id.unique()) for _, v in fs]}
            for p, (t, c, fs) in partitions.items()}}
    write_json(output/'protocol.json', protocol)
    selection, models, resources = {}, {}, {}
    with threadpool_limits(limits=threads):
        for profile, (train, cal, dev_folds) in partitions.items():
            print(f'{profile}: {len(train):,} training rows, {len(cal):,} calibration rows', flush=True)
            winner, scores = search(train, PROFILES[profile], dev_folds, candidates)
            selection[profile] = {'selected': winner, 'development': scores}
            write_json(output/'selection.json', selection)
            models[profile], resources[profile] = {}, {}
            for name in dict.fromkeys(['hgb_reference', winner]):
                started = time.perf_counter()
                fitted, notices = calibrated_fit(train, cal, PROFILES[profile], candidates[name])
                fit_seconds = time.perf_counter() - started
                path = output/f'{profile}-{name}.joblib'
                joblib.dump(fitted, path, compress=0)
                models[profile][name] = fitted
                resources[profile][name] = {**benchmark(fitted, cal, path),
                    'fit_seconds': fit_seconds, 'fit_warnings': notices,
                    'supported_features': [PROFILES[profile][i] for i in fitted.supported]}
        # All architecture choices are now written. Only now read holdout targets.
        report = {}
        for split in HOLDOUTS:
            held = cohort(frame, split, later_test_at=provenance['later_test_at'])
            report[split] = {}
            for profile in profiles:
                selected = eligible(held, profile)
                winner = selection[profile]['selected']
                probabilities = {n: m.predict_frame(selected) if len(selected) else np.empty(0)
                                 for n, m in models[profile].items()}
                report[split][profile] = {
                    'cohort': cohort_summary(selected), 'excluded_unavailable_rows': len(held)-len(selected),
                    'models': {n: metrics(selected, p, .2) for n, p in probabilities.items()},
                    'selected_vs_same_profile_reference': paired_interval(selected, probabilities[winner],
                        probabilities['hgb_reference'], bootstrap, seed)}
            if 'geometry' in models and 'weather' in models:
                complete = eligible(held, 'weather')
                if len(complete):
                    geometry = models['geometry']['hgb_reference'].predict_frame(complete)
                    weather = models['weather']['hgb_reference'].predict_frame(complete)
                    report[split]['reference_weather_blend'] = metrics(complete, .75*geometry + .25*weather, .15)
                    # Matched rows distinguish weather benefit from larger geometry features.
                    report[split]['geometry_reference_on_weather_rows'] = metrics(complete, geometry, .2)
                offline = models['geometry'][selection['geometry']['selected']]
                weather = models['weather'][selection['weather']['selected']]
                p = offline.predict_frame(held) if len(held) else np.empty(0)
                report[split]['availability'] = {'no_weather': metrics(held, p, .2)}
                mask = np.isfinite(held[list(WEATHER_INPUTS)].to_numpy(dtype=float)).all(axis=1)
                if mask.any():
                    p[mask] = weather.predict_frame(held.loc[mask])
                report[split]['availability']['historical_weather_when_present_else_geometry'] = {
                    **metrics(held, p, .2), 'weather_rows': int(mask.sum()),
                    'fallback_rows': int((~mask).sum()),
                    'scope': 'retrospective analysis experiment, not a validated issued-forecast policy'}
    write_json(output/'evaluation.json', report)
    write_json(output/'resources.json', resources)
    manifest = {'kind': KIND, 'status': 'complete', 'source': provenance,
        'selected': {p: s['selected'] for p, s in selection.items()},
        'promoted': False,
        'limitations': ['Observed satellite-dependent weak labels; reused test cohorts.',
            'Device latency, peak RAM, battery and recursive rollout accuracy remain unmeasured.',
            'Weather analysis and retrospective vegetation do not prove operational availability.']}
    write_summary(output/'summary.md', manifest, selection, report, resources)
    manifest['artifacts'] = {p.name: {'path': p.name, 'sha256': sha256_file(p)}
                             for p in output.iterdir() if p.is_file()}
    write_json(output/'run_manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default=os.getenv('TRAINING_CSV_DIR', 'htn_training'))
    parser.add_argument('--output', required=True)
    parser.add_argument('--profiles', nargs='+', choices=tuple(PROFILES), default=list(DEFAULT_PROFILES))
    parser.add_argument('--folds', type=int, default=3)
    parser.add_argument('--threads', type=int, default=2)
    parser.add_argument('--bootstrap', type=int, default=200)
    args = parser.parse_args()
    try:
        print('Verifying dataset checksums and loading selected columns...', flush=True)
        frame, source = load_dataset(args.dataset, weather='weather' in args.profiles,
            geometry=any(p != 'frontier' for p in args.profiles), vegetation='vegetation' in args.profiles)
        manifest = run_experiments(frame, source, args.output, profiles=tuple(args.profiles),
            folds=args.folds, threads=args.threads, bootstrap=args.bootstrap)
    except (FileNotFoundError, ValueError) as exc:
        parser.exit(2, f'Experiment could not run: {exc}\nFull training CSVs and their checksummed metadata are required.\n')
    print(json.dumps(manifest['selected'], indent=2), flush=True)
    print(f'Completed research run: {args.output}', flush=True)


if __name__ == '__main__':
    main()
