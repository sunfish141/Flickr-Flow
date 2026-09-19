"""Training-only feature ablations; never select against the published holdouts."""
import argparse
import json
from pathlib import Path
import platform
import time

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from threadpoolctl import threadpool_limits

from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.estimators import DIRECTIONAL, GEOMETRY_COLUMNS, HISTORY, WEATHER_COLUMNS
from wildfire_data.model.features.experiment_features import (
    CONTEXT_COLUMNS, PROFILES, add_context, add_optional_weather,
)
from wildfire_data.model.training.dataset import TARGET, cohort, load_dataset
from wildfire_data.model.training.experiments import CANDIDATES, cohort_summary, fit_raw, group_folds, raw_predict
from wildfire_data.model.training.public_csv import write_json

MORPHOLOGY = CONTEXT_COLUMNS[:5]
SEASON = CONTEXT_COLUMNS[5:]
MOISTURE = ('weather_relative_humidity_2m', 'weather_precipitation',
            'weather_vpd_kpa', 'weather_rain_6h_mm')
WIND = ('weather_wind_speed_m_s',) + DIRECTIONAL + HISTORY[1:]
ABLATIONS = {
    'frontier': PROFILES['frontier'],
    'geometry': GEOMETRY_COLUMNS,
    'geometry_morphology': GEOMETRY_COLUMNS + MORPHOLOGY,
    'geometry_season': GEOMETRY_COLUMNS + SEASON,
    'context': PROFILES['context'],
    'geometry_moisture': GEOMETRY_COLUMNS + MOISTURE,
    'geometry_wind': GEOMETRY_COLUMNS + WIND,
    'context_moisture': PROFILES['context'] + MOISTURE,
    'context_wind': PROFILES['context'] + WIND,
    'context_weather': WEATHER_COLUMNS + CONTEXT_COLUMNS,
    'vegetation': PROFILES['vegetation'],
    'context_vegetation': PROFILES['vegetation'] + CONTEXT_COLUMNS,
}
TIME_EDGES = ('2026-06-20T00:00:00Z', '2026-06-28T00:00:00Z',
              '2026-07-06T00:00:00Z', '2026-07-20T00:00:00Z')


def require_training(frame):
    if frame.empty or set(frame.incident_split) != {'train'}:
        raise ValueError('Ablations require declared training rows only')
    if (frame.incident_group_id.isna() | frame.incident_group_id.eq('')).any():
        raise ValueError('Missing incident identity')
    if not frame.example_id.is_unique or frame.example_id.isna().any():
        raise ValueError('Duplicate or missing example identity')


def forward_folds(training, edges=TIME_EDGES, embargo_hours=12):
    """Earlier completed labels only, with validation incidents purged in full.

    These folds test later, unseen incidents, not continuation of known fires.
    The extra embargo is in addition to waiting for each 12-hour label to end.
    """
    require_training(training)
    boundaries = pd.to_datetime(list(edges), utc=True, format='mixed')
    if (len(boundaries) < 2 or boundaries.hasnans or not boundaries.is_monotonic_increasing
            or boundaries.has_duplicates or embargo_hours < 0):
        raise ValueError('Invalid forward fold boundaries or embargo')
    cutoff = pd.to_datetime(training.feature_cutoff_at, utc=True, format='mixed')
    label_end = pd.to_datetime(training.target_end_at, utc=True, format='mixed')
    if cutoff.isna().any() or label_end.isna().any() or (label_end <= cutoff).any():
        raise ValueError('A completed label must end after its feature cutoff')
    result = []
    for start, stop in zip(boundaries[:-1], boundaries[1:]):
        valid = cutoff.ge(start) & cutoff.lt(stop)
        incidents = training.loc[valid, 'incident_group_id'].unique()
        train = (label_end.le(start-pd.Timedelta(hours=embargo_hours))
                 & ~training.incident_group_id.isin(incidents))
        a, b = np.flatnonzero(train), np.flatnonzero(valid)
        if any(set(training.iloc[idx][TARGET]) != {0, 1} for idx in (a, b)):
            raise ValueError(f'Forward fold {start.isoformat()} needs both classes in each partition')
        if min(training.iloc[a].incident_group_id.nunique(),
               training.iloc[b].incident_group_id.nunique()) < 2:
            raise ValueError('Forward folds need at least two incidents per partition')
        result.append((a, b))
    return result


def budget_metrics(target, probabilities, fraction=.2):
    """Expected recall/precision for top-k rows, sharing cutoff ties equally.

    This is a ranking diagnostic over candidate rows, NOT an alerting policy or
    area guarantee for deduplicated maps. No threshold is tuned here.
    """
    y, p = np.asarray(target), np.asarray(probabilities)
    if (y.ndim != 1 or y.shape != p.shape or not len(y) or not np.isfinite(p).all()
            or not set(y).issubset({0, 1}) or not 0 < fraction <= 1):
        raise ValueError('Invalid budget metric inputs')
    k = int(np.ceil(fraction*len(y)))
    boundary = np.partition(p, len(p)-k)[len(p)-k]
    above, tied = p > boundary, p == boundary
    tie_weight = (k-int(above.sum())) / int(tied.sum())
    hits = float(y[above].sum() + tie_weight*y[tied].sum())
    return {'top_fraction': fraction, 'selected_row_equivalents': k,
            'precision': hits/k, 'recall': hits/float(y.sum()) if y.sum() else None,
            'tie_policy': 'expected score from uniform selection within cutoff ties'}


def describe_fold(frame, indices):
    selected = frame.iloc[indices]
    return {**cohort_summary(selected),
            'first_cutoff': pd.to_datetime(selected.feature_cutoff_at, utc=True, format='mixed').min().isoformat(),
            'last_cutoff': pd.to_datetime(selected.feature_cutoff_at, utc=True, format='mixed').max().isoformat(),
            'last_label_end': pd.to_datetime(selected.target_end_at, utc=True, format='mixed').max().isoformat(),
            'incident_ids': sorted(selected.incident_group_id.unique())}


def run_ablations(training, provenance, output, *, profiles=None, candidates=None,
                  time_edges=TIME_EDGES, folds=3, threads=2, seed=0):
    # Deliberately reject a whole release here. CLI must discard all non-training
    # rows BEFORE feature engineering, selection, evaluation or output.
    require_training(training)
    if threads < 1:
        raise ValueError('Positive thread count required')
    later = pd.Timestamp(provenance['later_test_at'])
    end = pd.to_datetime(training.target_end_at, utc=True, format='mixed')
    snapshot = pd.to_datetime(training.source_snapshot_time, utc=True, format='mixed')
    if end.isna().any() or snapshot.isna().any() or (end >= later).any() or (snapshot >= later).any():
        raise ValueError('Training labels or snapshots reach the later-time holdout')
    profiles = ABLATIONS if profiles is None else profiles
    candidates = ({n: CANDIDATES[n] for n in ('hgb_compact', 'hgb_reference')}
                  if candidates is None else candidates)
    if not profiles or not candidates:
        raise ValueError('At least one profile and candidate required')
    training = add_optional_weather(add_context(training)).reset_index(drop=True)
    for columns in profiles.values():
        if len(set(columns)) != len(columns) or not set(columns).issubset(training.columns):
            raise ValueError('Missing or duplicated ablation columns')
    partitions = {'grouped_incident': group_folds(training, folds, seed),
                  'forward_unseen_incident': forward_folds(training, time_edges)}
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new output directory; runs are immutable')
    output.mkdir(parents=True)
    protocol = {'kind': 'training-only-feature-ablation/v1', 'source': provenance,
        'profiles': {k: list(v) for k, v in profiles.items()}, 'candidates': candidates,
        'forward_edges': list(time_edges), 'embargo_hours_after_label_end': 12,
        'seed': seed, 'threads': threads, 'python': platform.python_version(),
        'folds': {name: [{'train': describe_fold(training, a), 'validation': describe_fold(training, b)}
                        for a, b in fs] for name, fs in partitions.items()},
        'input_support': {c: float(training[c].notna().mean())
                          for c in sorted(set(sum((tuple(v) for v in profiles.values()), ())))},
        'evaluation': 'raw uncalibrated ranking; fold mean AP and top-20%-row recall',
        'scope': 'development diagnostics only; calibration and published holdouts excluded',
        'selection': 'no automatic selection, threshold tuning, final fit or promotion'}
    write_json(output/'protocol.json', protocol)
    results = {}
    started = time.perf_counter()
    with threadpool_limits(limits=threads):
        for split, partitions_for_split in partitions.items():
            results[split] = {}
            for profile, columns in profiles.items():
                results[split][profile] = {}
                for candidate, spec in candidates.items():
                    fold_scores = []
                    for a, b in partitions_for_split:
                        before = time.perf_counter()
                        model, support, notices = fit_raw(training.iloc[a], columns, spec)
                        p = raw_predict(model, support, training.iloc[b], columns)
                        y = training.iloc[b][TARGET].to_numpy()
                        fold_scores.append({'average_precision': float(average_precision_score(y, p)),
                            'roc_auc': float(roc_auc_score(y, p)), 'budget': budget_metrics(y, p),
                            'fit_predict_seconds': time.perf_counter()-before, 'fit_warnings': notices})
                    scores = {'folds': fold_scores,
                        'mean_average_precision': float(np.mean([s['average_precision'] for s in fold_scores])),
                        'mean_budget_recall': float(np.mean([s['budget']['recall'] for s in fold_scores]))}
                    results[split][profile][candidate] = scores
                    print(f"{split} / {profile} / {candidate}: AP={scores['mean_average_precision']:.4f}", flush=True)
                write_json(output/'development.json', results)
    elapsed = time.perf_counter()-started
    lines = ['# Training-only ablations', '',
        'Raw ranking diagnostics. No calibration/test rows used; no model promoted.', '',
        '| Validation | Features | Model | Mean AP | Mean recall at top 20% of rows |',
        '| --- | --- | --- | ---: | ---: |']
    for split, profiles_scores in results.items():
        for profile, models in profiles_scores.items():
            for model, scores in models.items():
                lines.append(f"| {split} | {profile} | {model} | {scores['mean_average_precision']:.4f} | "
                             f"{scores['mean_budget_recall']:.4f} |")
    lines += ['', 'Forward folds purge validation incidents from earlier training and wait for labels plus a 12-hour embargo.',
        'Fold AP depends on prevalence; compare profiles within each fold, not between validation designs.',
        'Weather is retrospective analysis. Seasonality is evaluated within one summer, not across years.', '']
    (output/'summary.md').write_text('\n'.join(lines), encoding='utf-8')
    manifest = {'kind': protocol['kind'], 'status': 'complete', 'promoted': False,
        'source': provenance, 'fit_predict_report_seconds': elapsed,
        'fit_count': sum(len(f) for f in partitions.values())*len(profiles)*len(candidates),
        'artifacts': {p.name: {'sha256': sha256_file(p)} for p in output.iterdir() if p.is_file()}}
    write_json(output/'run_manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', default='htn_training')
    parser.add_argument('--output', required=True)
    parser.add_argument('--threads', type=int, default=2)
    args = parser.parse_args()
    print('Verifying release and loading projected columns...', flush=True)
    frame, source = load_dataset(args.dataset, weather=True, geometry=True, vegetation=True)
    training = cohort(frame, 'train', later_test_at=source['later_test_at']).copy()
    del frame
    # A source-snapshot split alone may retain labels that cross the test boundary.
    complete = pd.to_datetime(training.target_end_at, utc=True, format='mixed') < pd.Timestamp(source['later_test_at'])
    source['excluded_training_labels_crossing_test_boundary'] = int((~complete).sum())
    manifest = run_ablations(training.loc[complete], source, args.output, threads=args.threads)
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == '__main__':
    main()
