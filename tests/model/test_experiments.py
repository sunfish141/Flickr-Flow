"""Small synthetic contract tests, never evidence of wildfire prediction skill."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from wildfire_data.model.estimators import GEOMETRY_COLUMNS, WEATHER_COLUMNS
from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.features.schema import DEFAULT_MODEL_FEATURE_COLUMNS
from wildfire_data.model.features.experiment_features import (
    PROFILES, add_context, add_optional_weather, profile_contract,
)
from wildfire_data.model.input_availability import WeatherEvidence, choose_input_mode
from wildfire_data.model.training.dataset import (
    TARGET, WEATHER_INPUTS, HISTORY_INPUTS, REQUIRED_CSVS, load_dataset, verify_release, DatasetError,
)
from wildfire_data.model.training.experiments import (
    CANDIDATES, calibrated_fit, fit_raw, group_folds, raw_predict,
    run_experiments, search, paired_interval,
)


def synthetic_rows():
    rng = np.random.default_rng(811)
    pieces = []
    for split in ('train', 'calibration', 'held_incident', 'held_region', 'later_time'):
        for group in range(6):
            n = 16
            values = {c: rng.normal(size=n) for c in sorted(set(sum(PROFILES.values(), ())))}
            frame = pd.DataFrame(values)
            frame['example_id'] = [f'{split}-{group}-{i}' for i in range(n)]
            frame[TARGET] = (frame.terrain_elevation_m > 0).astype(int)
            # Guaranteed two classes in every incident.
            frame.loc[0, TARGET], frame.loc[1, TARGET] = 0, 1
            frame['incident_split'] = split
            frame['incident_group_id'] = f'{split}-{group}'
            frame['firms_center_has_detection'] = 0
            frame['binary_training_eligible'] = True
            date = '2026-08-04T12:00:00Z' if split == 'later_time' else '2026-06-01T12:00:00Z'
            frame['source_snapshot_time'] = date
            frame['feature_cutoff_at'] = date
            frame['target_end_at'] = (pd.Timestamp(date)+pd.Timedelta(hours=12)).isoformat()
            frame['firms_local_3x3_detection_count'] = 3.
            frame['fire_5x5_detection_count'] = 6.
            frame['fire_5x5_active_cell_count'] = 2.
            frame['fire_5x5_recent_12h_count'] = 4.
            frame['firms_local_3x3_bright_ti4_max'] = 320.
            frame['firms_local_3x3_bright_ti4_mean'] = 310.
            frame['weather_temperature_2m'] = 20.
            frame['weather_relative_humidity_2m'] = 50.
            frame['weather_precipitation'] = 0.
            frame['weather_wind_u_10m'] = 3.
            frame['weather_wind_v_10m'] = 4.
            pieces.append(frame)
    return pd.concat(pieces, ignore_index=True)


def write_synthetic_release(root):
    """Exercise the real checksum/CSV join path without implying real observations."""
    frame = synthetic_rows()
    for column in DEFAULT_MODEL_FEATURE_COLUMNS:
        if column not in frame:
            frame[column] = 0.
    frame['cell_id'] = frame.example_id
    frame['cell_center_latitude'], frame['cell_center_longitude'] = 50., -110.
    frame['anchor_at'] = frame.feature_cutoff_at
    frame['target_end_at'] = frame.feature_cutoff_at
    frame['dataset_split'] = 'train'
    frame['region_key'] = 'synthetic-region'
    identity = ['example_id', 'cell_id', 'source_snapshot_time', 'anchor_at',
                'feature_cutoff_at', 'target_end_at', TARGET, 'dataset_split']
    tables = {
        'candidate_examples.csv': frame[identity + list(DEFAULT_MODEL_FEATURE_COLUMNS) +
            ['binary_training_eligible', 'cell_center_latitude', 'cell_center_longitude']],
        'incident_assignments.csv': frame[['example_id', 'incident_group_id', 'incident_split', 'region_key']],
        'directional_features.csv': frame[['example_id'] +
            [c for c in frame if c.startswith(('fire_', 'wind_'))]],
        'weather_features.csv': frame[['example_id', *WEATHER_INPUTS]],
        'weather_history.csv': frame[['example_id', *HISTORY_INPUTS]],
        'vegetation_features.csv': frame[['example_id']+[c for c in frame if c.startswith('vegetation_')]],
        'landscape_features.csv': frame[['example_id']],
        'unscored_positives.csv': pd.DataFrame({'example_id': ['synthetic-diagnostic']})}
    assignments = {'policy': {'later_test_at': '2026-08-02T12:00:00Z'},
        'groups': {group: {'split': rows.incident_split.iloc[0]} for group, rows in frame.groupby('incident_group_id')}}
    (root/'source.json').write_text('{}')
    (root/'incidents.json').write_text(json.dumps(assignments))
    entries = []
    for name, table in tables.items():
        table.to_csv(root/name, index=False)
        source = 'incidents.json' if name == 'incident_assignments.csv' else 'source.json'
        entries.append({'path': name, 'sha256': sha256_file(root/name), 'bytes': (root/name).stat().st_size,
            'row_count': len(table), 'columns': list(table.columns),
            'source_manifest': source, 'source_manifest_sha256': sha256_file(root/source)})
    manifest = {'kind': 'plain-training-csv-collection/v1', 'status': 'complete',
        'candidate_row_count': len(frame), 'files': entries,
        'source_snapshot_start_date': '2026-06-01', 'source_snapshot_end_date': '2026-08-04'}
    (root/'manifest.json').write_text(json.dumps(manifest))
    (root/'SHA256SUMS').write_text(''.join(f'{sha256_file(p)}  {p.name}\n' for p in root.iterdir()))


FAST_CANDIDATES = {
    'hgb_reference': {'family': 'hgb', 'parameters': {
        'max_iter': 3, 'max_leaf_nodes': 3, 'min_samples_leaf': 2,
        'early_stopping': False, 'random_state': 0}},
    'logistic': CANDIDATES['logistic'],
}


class ExperimentLoaderTests(unittest.TestCase):
    def test_verified_geometry_loading_is_independent_of_weather(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_synthetic_release(root)
            offline, _ = load_dataset(root, weather=False, geometry=True)
            self.assertTrue(set(GEOMETRY_COLUMNS).issubset(offline))
            self.assertFalse(any(c.startswith(('weather_', 'wind_', 'vegetation_')) for c in offline))
            all_inputs, _ = load_dataset(root, weather=True, vegetation=True)
            self.assertTrue(set(PROFILES['vegetation']).issubset(all_inputs))
            self.assertTrue(set(WEATHER_INPUTS).issubset(all_inputs))
            self.assertEqual(offline.example_id.tolist(), all_inputs.example_id.tolist())

    def test_missing_release_reports_every_absent_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'SHA256SUMS').write_text(''.join('0'*64+f'  {name}\n' for name in sorted(REQUIRED_CSVS)))
            with self.assertRaises(DatasetError) as caught:
                verify_release(root)
            for name in REQUIRED_CSVS:
                self.assertIn(name, str(caught.exception))


class FeatureTests(unittest.TestCase):
    def test_context_is_label_independent_and_preserves_undefined_ratios(self):
        frame = synthetic_rows().iloc[:3].copy()
        result = add_context(frame)
        self.assertEqual(result.context_outer_detection_count.iloc[0], 3.)
        self.assertEqual(result.context_local_detection_fraction.iloc[0], .5)
        self.assertEqual(result.context_detections_per_active_cell.iloc[0], 3.)
        pd.testing.assert_frame_equal(result.drop(columns=TARGET),
            add_context(frame.assign(**{TARGET: 1-frame[TARGET]})).drop(columns=TARGET))
        zero = frame.assign(firms_local_3x3_detection_count=0., fire_5x5_detection_count=0.,
                            fire_5x5_active_cell_count=0., fire_5x5_recent_12h_count=0.)
        self.assertTrue(add_context(zero).context_local_detection_fraction.isna().all())
        with self.assertRaisesRegex(ValueError, 'Inconsistent'):
            add_context(frame.assign(fire_5x5_detection_count=1.))

    def test_missing_weather_does_not_block_offline_or_become_calm(self):
        frame = synthetic_rows().iloc[:3].copy()
        frame.loc[0, 'weather_wind_u_10m'] = np.nan
        frame.loc[1, 'weather_temperature_2m'] = np.nan
        result = add_optional_weather(frame)
        self.assertTrue(np.isnan(result.weather_wind_speed_m_s.iloc[0]))
        self.assertTrue(np.isnan(result.weather_vpd_kpa.iloc[1]))
        self.assertEqual(result.weather_wind_speed_m_s.iloc[2], 5.)
        self.assertFalse(set(PROFILES['context']) & set(WEATHER_INPUTS))
        self.assertFalse(profile_contract('weather')['network_required_for_prediction'])
        with self.assertRaises(ValueError):
            add_optional_weather(frame.assign(weather_relative_humidity_2m=101.))


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.origin = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
        self.weather = WeatherEvidence(self.origin-timedelta(hours=2), self.origin-timedelta(hours=1),
            self.origin-timedelta(hours=2), self.origin+timedelta(hours=48), True)

    def route(self, weather=None, **kwargs):
        return choose_input_mode(origin_at=self.origin, prediction_at=self.origin,
            weather=weather, weather_model_validated=True, **kwargs)

    def test_downloaded_forecast_works_without_connection(self):
        self.assertEqual(self.route(self.weather), ('cached_weather', 'valid_local_forecast'))
        self.assertEqual(self.route()[1], 'weather_not_cached')

    def test_expired_incomplete_retrospective_and_future_weather_fall_back(self):
        from dataclasses import replace
        for changed, reason in [
            (replace(self.weather, valid_until=self.origin+timedelta(hours=11)), 'weather_outside_valid_window'),
            (replace(self.weather, complete=False), 'weather_incomplete'),
            (replace(self.weather, source_kind='historical_analysis'), 'weather_not_issued_forecast'),
            (replace(self.weather, downloaded_at=self.origin+timedelta(hours=1)), 'weather_unavailable_at_origin'),
            (replace(self.weather, issued_at=self.origin-timedelta(hours=30)), 'weather_stale'),
        ]:
            self.assertEqual(self.route(changed), ('offline', reason))

    def test_analysis_model_cannot_be_promoted_by_having_weather(self):
        mode = choose_input_mode(origin_at=self.origin, prediction_at=self.origin, weather=self.weather)
        self.assertEqual(mode, ('offline', 'weather_model_not_validated'))

    def test_later_rollout_uses_validity_and_original_cutoff(self):
        mode = choose_input_mode(origin_at=self.origin, prediction_at=self.origin+timedelta(hours=24),
            weather=self.weather, weather_model_validated=True)
        self.assertEqual(mode[1], 'weather_stale')
        with self.assertRaises(ValueError):
            choose_input_mode(origin_at=self.origin.replace(tzinfo=None), prediction_at=self.origin)


class ExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.limits = threadpool_limits(limits=1)

    @classmethod
    def tearDownClass(cls):
        cls.limits.restore_original_limits()

    def test_group_folds_prohibit_calibration_and_holdout_leakage(self):
        frame = synthetic_rows()
        train = frame.loc[frame.incident_split.eq('train')]
        folds = group_folds(train, 3, 0)
        seen = []
        for a, b in folds:
            self.assertFalse(set(train.iloc[a].incident_group_id) & set(train.iloc[b].incident_group_id))
            seen.extend(b)
        self.assertEqual(sorted(seen), list(range(len(train))))
        with self.assertRaisesRegex(ValueError, 'only use declared'):
            group_folds(frame, 3, 0)

    def test_search_rejects_holdout_rows_even_with_external_folds(self):
        frame = synthetic_rows().query("incident_split == 'held_incident'")
        with self.assertRaisesRegex(ValueError, 'training rows only'):
            search(frame, GEOMETRY_COLUMNS, [], FAST_CANDIDATES)

    def test_training_cannot_reach_later_time(self):
        frame = synthetic_rows()
        frame.loc[frame.incident_split.eq('train'), 'source_snapshot_time'] = '2026-08-05T12:00:00Z'
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'later-time holdout'):
                run_experiments(frame, {'later_test_at': '2026-08-02T12:00:00Z'}, Path(directory)/'run',
                    profiles=('frontier',), candidates=FAST_CANDIDATES)

    def test_all_missing_support_and_preprocessing_are_fit_on_training_only(self):
        train = synthetic_rows().query("incident_split == 'train'").copy()
        columns = ('terrain_elevation_m', 'terrain_slope_degrees')
        train['terrain_slope_degrees'] = np.nan
        model, support, _ = fit_raw(train, columns, CANDIDATES['logistic'])
        self.assertEqual(support, (0,))
        first = raw_predict(model, support, train, columns)
        second = raw_predict(model, support, train.assign(terrain_slope_degrees=1e9), columns)
        np.testing.assert_array_equal(first, second)
        mean = model.named_steps['standardscaler'].mean_[0]
        self.assertAlmostEqual(mean, train.terrain_elevation_m.mean())

    def test_label_windows_crossing_later_test_boundary_are_excluded(self):
        frame = synthetic_rows()
        for split in ('train', 'calibration'):
            row = frame.index[frame.incident_split.eq(split)][0]
            frame.loc[row, 'target_end_at'] = '2026-08-02T12:00:00Z'
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'run'
            run_experiments(frame, {'later_test_at': '2026-08-02T12:00:00Z'}, output,
                            profiles=('frontier',), candidates=FAST_CANDIDATES, folds=2, bootstrap=0)
            protocol = json.loads((output/'protocol.json').read_text())
            self.assertEqual(protocol['excluded_development_labels_crossing_test_boundary'],
                             {'train': 1, 'calibration': 1})
            self.assertEqual(protocol['cohorts']['frontier']['train']['rows'], 95)
            self.assertEqual(protocol['cohorts']['frontier']['calibration']['rows'], 95)

    def test_binned_logistic_handles_missing_values(self):
        train = synthetic_rows().query("incident_split == 'train'").copy()
        train.loc[0, 'terrain_elevation_m'] = np.nan
        fitted, support, _ = fit_raw(train, GEOMETRY_COLUMNS, CANDIDATES['binned_logistic'])
        p = raw_predict(fitted, support, train, GEOMETRY_COLUMNS)
        self.assertTrue(np.isfinite(p).all())

    def test_calibration_rejects_shared_incidents(self):
        frame = synthetic_rows()
        train = frame.query("incident_split == 'train'")
        cal = frame.query("incident_split == 'calibration'").copy()
        cal['incident_group_id'] = train.incident_group_id.iloc[0]
        with self.assertRaisesRegex(ValueError, 'overlap'):
            calibrated_fit(train, cal, GEOMETRY_COLUMNS, FAST_CANDIDATES['hgb_reference'])

    def test_end_to_end_synthetic_run_exports_all_profiles_and_missing_weather_fallback(self):
        frame = synthetic_rows()
        frame.loc[frame.incident_split.eq('held_region').idxmax(), 'weather_wind_u_10m'] = np.nan
        provenance = {'kind': 'synthetic_contract_fixture_not_accuracy_evidence',
                      'later_test_at': '2026-08-02T12:00:00Z'}
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'run'
            manifest = run_experiments(frame, provenance, output, profiles=tuple(PROFILES),
                candidates=FAST_CANDIDATES, folds=2, threads=1, bootstrap=3)
            self.assertEqual(manifest['status'], 'complete')
            self.assertFalse(manifest['promoted'])
            evaluation = json.loads((output/'evaluation.json').read_text())
            self.assertEqual(evaluation['held_region']['weather']['excluded_unavailable_rows'], 1)
            self.assertEqual(evaluation['held_region']['availability']
                ['historical_weather_when_present_else_geometry']['fallback_rows'], 1)
            self.assertTrue((output/'selection.json').exists())
            resources = json.loads((output/'resources.json').read_text())
            self.assertTrue(resources['frontier']['hgb_reference']['reload_equal'])
            self.assertGreater(resources['frontier']['hgb_reference']['artifact_bytes'], 0)
            with self.assertRaisesRegex(ValueError, 'immutable'):
                run_experiments(frame, provenance, output)

    def test_paired_incident_interval_is_zero_for_identical_models(self):
        frame = synthetic_rows().query("incident_split == 'train'")
        p = np.linspace(.1, .9, len(frame))
        result = paired_interval(frame, p, p, 10, 0)
        self.assertEqual(result['pr_auc_delta_95_percentile'], [0., 0.])


if __name__ == '__main__':
    unittest.main()
