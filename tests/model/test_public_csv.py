import csv
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

import joblib
import numpy as np
import pandas as pd

from wildfire_data.core.hashing import sha256_file
from wildfire_data.core.model_artifacts import PUBLIC_MODEL_KIND, public_artifact
from wildfire_data.model.features.schema import FRONTIER_BASELINE_COLUMNS
from wildfire_data.model.loading import load_pass_model
from wildfire_data.model.recursive_transition import SyntheticObservationCalibration
from wildfire_data.model.training.dataset import DatasetError, join_exact, verify_release, cohort, TARGET, REQUIRED_CSVS
from wildfire_data.model.training.public_csv import fit_component, fitting_cohort, metrics
from wildfire_data.model.estimators import WEATHER_COLUMNS, derive_weather
from wildfire_data.providers.terrain_csv import CSVTerrainProvider


def rows(split, n=50):
    rng = np.random.default_rng(123)
    frame = pd.DataFrame(rng.normal(size=(n, len(FRONTIER_BASELINE_COLUMNS))), columns=FRONTIER_BASELINE_COLUMNS)
    frame[TARGET] = np.arange(n) % 2
    frame['incident_split'] = split
    frame['incident_group_id'] = split + '-group'
    return frame


class DatasetContractTests(unittest.TestCase):
    def test_join_checks_identity_and_complete_ids_without_relying_on_row_order(self):
        base = pd.DataFrame({'example_id': ['a', 'b'], 'cell_id': ['cell-a', 'cell-b'], TARGET: [0, 1]})
        side = pd.DataFrame({'example_id': ['b', 'a'], 'cell_id': ['cell-b', 'cell-a'], 'value': [2., np.nan]})
        result = join_exact(base, side, 'sidecar')
        self.assertEqual(result.example_id.tolist(), ['a', 'b'])
        self.assertTrue(pd.isna(result.value.iloc[0]))
        self.assertEqual(result.value.iloc[1], 2.)
        for invalid in [side.iloc[:1], pd.concat([side, side.iloc[:1]]), side.assign(cell_id='wrong')]:
            with self.assertRaises(DatasetError):
                join_exact(base, invalid, 'sidecar')

    def test_later_cohort_has_time_frontier_and_eligibility_filters(self):
        frame = pd.DataFrame({'incident_split': ['later_time']*4,
            'firms_center_has_detection': [0, 0, 1, 0], 'binary_training_eligible': [True, True, True, False],
            'source_snapshot_time': ['2026-07-01T12:00Z', '2026-08-02T12:00Z', '2026-08-03T12:00Z', '2026-08-04T12:00Z']})
        self.assertEqual(cohort(frame, 'later_time', later_test_at='2026-08-02T12:00Z').index.tolist(), [1])

    def test_checksum_tampering_and_traversal_fail_before_loading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'source.json').write_text('{}')
            entries = []
            for name in sorted(REQUIRED_CSVS):
                (root/name).write_text('example_id\na\n')
                entries.append({'path': name, 'bytes': (root/name).stat().st_size,
                    'sha256': sha256_file(root/name), 'columns': ['example_id'],
                    'source_manifest': 'source.json', 'source_manifest_sha256': sha256_file(root/'source.json')})
            manifest = {'kind': 'plain-training-csv-collection/v1', 'status': 'complete',
                        'candidate_row_count': 1, 'files': entries}
            (root/'manifest.json').write_text(json.dumps(manifest))
            checks = ''.join(f'{sha256_file(p)}  {p.name}\n' for p in root.iterdir())
            (root/'SHA256SUMS').write_text(checks)
            self.assertEqual(verify_release(root)[0]['status'], 'complete')
            (root/'manifest.json').write_text('{}')
            with self.assertRaisesRegex(DatasetError, 'Checksum'):
                verify_release(root)
            (root/'SHA256SUMS').write_text('0'*64 + '  ../outside\n')
            with self.assertRaisesRegex(DatasetError, 'relative'):
                verify_release(root)


class TrainingTests(unittest.TestCase):
    def test_reference_fit_excludes_labels_reaching_later_test(self):
        frame = pd.DataFrame({'incident_split': ['train']*3,
            'firms_center_has_detection': [0]*3, 'binary_training_eligible': [True]*3,
            'feature_cutoff_at': ['2026-08-01T00:00Z']*3,
            'target_end_at': ['2026-08-01T12:00Z', '2026-08-02T00:00Z', '2026-08-02T12:00Z']})
        kept, excluded = fitting_cohort(frame, 'train', '2026-08-02T00:00Z')
        self.assertEqual(kept.index.tolist(), [0])
        self.assertEqual(excluded, 2)
        for invalid in [None, '2026-07-31T00:00Z']:
            with self.assertRaisesRegex(ValueError, 'end after'):
                fitting_cohort(frame.assign(target_end_at=invalid), 'train', '2026-08-02T00:00Z')

    def test_train_only_support_and_calibration_rejects_overlap(self):
        train, cal = rows('train'), rows('calibration')
        train[FRONTIER_BASELINE_COLUMNS[-1]] = np.nan
        params = dict(max_iter=3, min_samples_leaf=2, early_stopping=False, random_state=0)
        fitted = fit_component(train, cal, FRONTIER_BASELINE_COLUMNS, parameters=params)
        self.assertNotIn(12, fitted.supported)
        changed = cal.copy()
        changed[FRONTIER_BASELINE_COLUMNS[-1]] = 1e9
        np.testing.assert_array_equal(fitted.predict_frame(cal), fitted.predict_frame(changed))
        with self.assertRaisesRegex(ValueError, 'overlap'):
            fit_component(train, cal.assign(incident_group_id='train-group'), FRONTIER_BASELINE_COLUMNS, parameters=params)
        with self.assertRaisesRegex(ValueError, 'declared'):
            fit_component(train.assign(incident_split='held_incident'), cal, FRONTIER_BASELINE_COLUMNS, parameters=params)

    def test_weather_transform_has_34_columns_and_does_not_fill_missing(self):
        self.assertEqual(len(WEATHER_COLUMNS), 34)
        frame = pd.DataFrame({'weather_temperature_2m': [20.], 'weather_relative_humidity_2m': [50.],
            'weather_precipitation': [0.], 'weather_wind_u_10m': [3.], 'weather_wind_v_10m': [4.]})
        derived = derive_weather(frame)
        self.assertEqual(derived.weather_wind_speed_m_s.iloc[0], 5.)
        self.assertAlmostEqual(derived.weather_vpd_kpa.iloc[0], 1.16914, places=4)
        with self.assertRaises(ValueError):
            derive_weather(frame.assign(weather_wind_u_10m=np.nan))

    def test_probability_metrics_and_empty_cohort(self):
        frame = pd.DataFrame({TARGET: [0, 1]})
        result = metrics(frame, [.1, .9], .5)
        self.assertEqual(result['precision'], 1.)
        self.assertAlmostEqual(result['brier_score'], .01)
        self.assertAlmostEqual(result['ece'], .1)
        self.assertEqual(metrics(frame.iloc[:0], [], .5)['status'], 'empty-cohort')
        with self.assertRaises(ValueError):
            metrics(frame, [0, float('nan')], .5)

    def test_new_frontier_bundle_relocates_and_tampering_is_rejected(self):
        train, cal = rows('train'), rows('calibration')
        fitted = fit_component(train, cal, FRONTIER_BASELINE_COLUMNS,
            parameters=dict(max_iter=2, min_samples_leaf=2, early_stopping=False, random_state=0))
        renderer = SyntheticObservationCalibration((1.,)*5, (1.,)*5, 1, ('2026-05-11T12:00:00Z',), 'a'*64)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root/'frontier.joblib'
            joblib.dump({'model': fitted, 'feature_columns': FRONTIER_BASELINE_COLUMNS,
                'observation_calibration': asdict(renderer), 'ignition_threshold': .2}, path)
            manifest = {'kind': PUBLIC_MODEL_KIND, 'status': 'complete',
                'frontier_features': list(FRONTIER_BASELINE_COLUMNS),
                'artifacts': {'frontier.joblib': {'path': path.name, 'sha256': sha256_file(path)}}}
            (root/'run_manifest.json').write_text(json.dumps(manifest))
            model = load_pass_model(root/'run_manifest.json')
            np.testing.assert_array_equal(model.estimator.predict_proba(cal[list(FRONTIER_BASELINE_COLUMNS)].to_numpy()),
                fitted.predict_proba(cal[list(FRONTIER_BASELINE_COLUMNS)].to_numpy()))
            path.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                public_artifact(root/'run_manifest.json', 'frontier.joblib')

    def test_csv_terrain_never_fabricates_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'terrain.csv'
            columns = [c for c in FRONTIER_BASELINE_COLUMNS if c.startswith('terrain_')]
            pd.DataFrame([{'cell_id': 'cell-a', **{c: 1. for c in columns}}]).to_csv(path, index=False)
            provider = CSVTerrainProvider(path)
            self.assertEqual(provider('cell-a')['terrain_coverage_status'], 'sampled')
            self.assertFalse(provider('cell-b')['terrain_valid'])
            self.assertNotIn('terrain_elevation_m', provider('cell-b'))


if __name__ == '__main__':
    unittest.main()
