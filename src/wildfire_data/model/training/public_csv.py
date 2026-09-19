"""Train, evaluate and predict using a portable, verified CSV release.

Run python -m wildfire_data.model.training.public_csv --help.
"""
import argparse
from dataclasses import asdict
import importlib.metadata
import json
import os
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score, brier_score_loss, precision_score, recall_score

from wildfire_data.core.hashing import sha256_file
from wildfire_data.core.model_artifacts import PUBLIC_MODEL_KIND, public_artifact
from wildfire_data.model.features.schema import FRONTIER_BASELINE_COLUMNS
from wildfire_data.model.incident_transition import probability_logit
from wildfire_data.model.recursive_transition import SyntheticObservationCalibration
from wildfire_data.model.training.dataset import load_dataset, cohort, TARGET
from wildfire_data.model.estimators import (
    CalibratedEstimator, WeatherPolicy, GEOMETRY_COLUMNS, WEATHER_COLUMNS, derive_weather,
)

KIND = PUBLIC_MODEL_KIND
PARAMETERS = dict(max_iter=300, learning_rate=.04, max_leaf_nodes=7,
                  min_samples_leaf=200, l2_regularization=30., early_stopping=False, random_state=0)


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')
    temporary.replace(path)


def fit_component(train, calibration, columns, *, parameters=None):
    if train.empty or calibration.empty:
        raise ValueError('Training and calibration must both have rows')
    if set(train.incident_split) != {'train'} or set(calibration.incident_split) != {'calibration'}:
        raise ValueError('Fitting requires the declared training/calibration cohorts')
    if set(train.incident_group_id) & set(calibration.incident_group_id):
        raise ValueError('Training/calibration incidents overlap')
    if set(train[TARGET]) != {0, 1} or set(calibration[TARGET]) != {0, 1}:
        raise ValueError('Both classes are required for fitting and calibration')
    x = train[list(columns)].to_numpy(dtype=float)
    xc = calibration[list(columns)].to_numpy(dtype=float)
    if np.isinf(x).any() or np.isinf(xc).any():
        raise ValueError('Infinite model inputs are not supported')
    supported = tuple(np.flatnonzero(~np.isnan(x).all(axis=0)).tolist())
    if not supported:
        raise ValueError('No observed training feature values')
    estimator = HistGradientBoostingClassifier(**(parameters or PARAMETERS))
    estimator.fit(x[:, supported], train[TARGET].to_numpy(dtype=int))
    calibrator = LogisticRegression(C=1., random_state=0)
    calibrator.fit(probability_logit(estimator.predict_proba(xc[:, supported])[:, 1]),
                   calibration[TARGET].to_numpy(dtype=int))
    return CalibratedEstimator(estimator, calibrator, tuple(columns), supported)


def renderer_calibration(frame, release_sha):
    rows = frame.loc[frame.incident_split.eq('train') & frame.firms_center_has_detection.eq(1)]
    fields = ['firms_center_bright_ti4_max', 'firms_center_detection_count',
              'firms_center_platform_count', 'firms_center_hours_since_last_detection']
    values = rows[fields].to_numpy(dtype=float)
    if not len(rows) or not np.isfinite(values).all():
        raise ValueError('Finite training observations are required for the renderer')
    if ((values[:, 1] < 1) | (values[:, 2] < 1) | (values[:, 2] > 3)
            | (values[:, 2] > values[:, 1]) | (values[:, 3] < 3) | (values[:, 3] > 24)).any():
        raise ValueError('Training observations violate count/age eligibility')
    bins = np.minimum(4, (np.clip((values[:, 0]-305)/62, 0, 1)*5).astype(int))
    def means(column):
        return tuple(float(values[bins == i, column].mean()) if (bins == i).any()
                     else float(values[:, column].mean()) for i in range(5))
    times = tuple(sorted(rows.source_snapshot_time.unique()))
    return SyntheticObservationCalibration(means(1), means(2), len(rows), times, release_sha)


def metrics(frame, probabilities, threshold):
    if frame.empty:
        return {'rows': 0, 'status': 'empty-cohort'}
    y = frame[TARGET].to_numpy(dtype=int)
    p = np.asarray(probabilities, dtype=float)
    if p.shape != y.shape or not np.isfinite(p).all() or ((p < 0) | (p > 1)).any():
        raise ValueError('Invalid predicted probabilities')
    bins = np.minimum((p*10).astype(int), 9)
    ece = sum(float((bins == i).mean())*abs(float(p[bins == i].mean()-y[bins == i].mean()))
              for i in range(10) if (bins == i).any())
    return {'rows': len(frame), 'positive_rows': int(y.sum()), 'threshold': threshold,
            'pr_auc': float(average_precision_score(y, p)),
            'roc_auc': float(roc_auc_score(y, p)) if len(np.unique(y)) == 2 else None,
            'brier_score': float(brier_score_loss(y, p)), 'ece': ece,
            'precision': float(precision_score(y, p >= threshold, zero_division=0)),
            'recall': float(recall_score(y, p >= threshold, zero_division=0))}


def evaluate(frame, provenance, frontier, weather):
    result = {}
    for split in ('held_incident', 'held_region', 'later_time'):
        selected = cohort(frame, split, later_test_at=provenance['later_test_at'])
        result[split] = {'frontier': metrics(selected, frontier.predict_frame(selected) if len(selected) else [], .2),
                        'weather': metrics(selected, weather.predict_frame(selected) if len(selected) else [], weather.threshold)}
    return result


def train(dataset, output):
    output = Path(output)
    if output.exists():
        raise ValueError('Choose a new output directory; completed runs are immutable')
    print('Verifying the release and loading projected feature columns…', flush=True)
    frame, provenance = load_dataset(dataset)
    frame = derive_weather(frame)
    training = cohort(frame, 'train', later_test_at=provenance['later_test_at'])
    calibration = cohort(frame, 'calibration', later_test_at=provenance['later_test_at'])
    print(f'Fitting {len(training):,} frontier rows; calibrating {len(calibration):,} rows.', flush=True)
    output.mkdir(parents=True)
    protocol = {'kind': KIND, 'status': 'planned', 'parameters': PARAMETERS,
                'frontier_threshold': .2, 'weather_threshold': .15,
                'weather_probability_weight': .25, 'geometry_probability_weight': .75,
                'selection': 'fixed reference policy; no holdout tuning or model search',
                'source': provenance, 'training_rows': len(training), 'calibration_rows': len(calibration),
                'training_groups': sorted(training.incident_group_id.unique()),
                'calibration_groups': sorted(calibration.incident_group_id.unique()),
                'dependency_versions': {n: importlib.metadata.version(n) for n in ('numpy', 'pandas', 'scikit-learn', 'joblib')}}
    write_json(output / 'protocol.json', protocol)
    frontier = fit_component(training, calibration, FRONTIER_BASELINE_COLUMNS)
    print('Frontier classifier fitted; fitting geometry and weather components…', flush=True)
    geometry = fit_component(training, calibration, GEOMETRY_COLUMNS)
    weather = WeatherPolicy(geometry, fit_component(training, calibration, WEATHER_COLUMNS))
    observation = renderer_calibration(frame, provenance['manifest_sha256'])
    joblib.dump({'model': frontier, 'feature_columns': FRONTIER_BASELINE_COLUMNS,
                 'observation_calibration': asdict(observation), 'ignition_threshold': .2}, output/'frontier.joblib')
    joblib.dump(weather, output/'weather.joblib')
    # A compact static lookup supports CSV-only inference with honest missingness
    # beyond sampled training cells. It is not a substitute for source rasters.
    terrain_columns = [c for c in FRONTIER_BASELINE_COLUMNS if c.startswith('terrain_')]
    terrain = frame[['cell_id', *terrain_columns]].drop_duplicates()
    if not terrain.cell_id.is_unique:
        raise ValueError('Conflicting static terrain values for a candidate cell')
    terrain.to_csv(output/'terrain.csv', index=False)
    reloaded_frontier = joblib.load(output/'frontier.joblib')['model']
    reloaded_weather = joblib.load(output/'weather.joblib')
    probe = calibration.iloc[:256]
    np.testing.assert_array_equal(frontier.predict_frame(probe), reloaded_frontier.predict_frame(probe))
    np.testing.assert_array_equal(weather.predict_frame(probe), reloaded_weather.predict_frame(probe))
    report = evaluate(frame, provenance, reloaded_frontier, reloaded_weather)
    write_json(output/'evaluation.json', report)
    artifacts = {name: {'path': name, 'sha256': sha256_file(output/name)}
                 for name in ('frontier.joblib', 'weather.joblib', 'terrain.csv', 'protocol.json', 'evaluation.json')}
    manifest = {'kind': KIND, 'status': 'complete', 'source': provenance,
                'artifacts': artifacts, 'frontier_features': list(FRONTIER_BASELINE_COLUMNS),
                'weather_features': list(WEATHER_COLUMNS), 'reload_verified': True,
                'training_rows': len(training), 'calibration_rows': len(calibration),
                'terrain_cells': len(terrain), 'limitations': [
                    'Observed weak labels; this is a new fit, not identical legacy weights.',
                    'Offline historical weather does not supply recursive map weather.',
                    'CSV terrain covers only retained candidate cells; road training remains unsupported.']}
    write_json(output/'run_manifest.json', manifest)
    print(json.dumps(report, indent=2), flush=True)
    return manifest


def load_artifact(manifest_path, name):
    return public_artifact(manifest_path, name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['verify', 'train', 'evaluate', 'predict'])
    parser.add_argument('--dataset', default=os.getenv('TRAINING_CSV_DIR', 'htn_training'))
    parser.add_argument('--output')
    parser.add_argument('--run')
    parser.add_argument('--split', choices=['held_incident', 'held_region', 'later_time'], default='held_incident')
    args = parser.parse_args()
    if args.command == 'train':
        if not args.output:
            parser.error('train requires --output')
        train(args.dataset, args.output)
        return
    frame, provenance = load_dataset(args.dataset)
    if args.command == 'verify':
        print(json.dumps(provenance, indent=2))
        return
    if not args.run:
        parser.error('evaluate/predict requires --run')
    path, run = load_artifact(args.run, 'frontier.joblib')
    if run['source']['manifest_sha256'] != provenance['manifest_sha256']:
        raise ValueError('Evaluation dataset differs from the fitted release')
    frontier = joblib.load(path)['model']
    weather = joblib.load(load_artifact(args.run, 'weather.joblib')[0])
    frame = derive_weather(frame)
    if args.command == 'evaluate':
        report = evaluate(frame, provenance, frontier, weather)
        if args.output:
            path = Path(args.output)
            if path.exists():
                raise ValueError('Refusing to overwrite existing evaluation output')
            write_json(path, report)
        print(json.dumps(report, indent=2))
    else:
        if not args.output:
            parser.error('predict requires --output')
        selected = cohort(frame, args.split, later_test_at=provenance['later_test_at'])
        result = selected[['example_id', 'cell_id', 'source_snapshot_time', TARGET]].copy()
        result['frontier_probability'] = frontier.predict_frame(selected)
        result['weather_probability'] = weather.predict_frame(selected)
        result.to_csv(args.output, index=False, mode='x')
        print(f'Wrote {len(result):,} paired predictions.')


if __name__ == '__main__':
    main()
