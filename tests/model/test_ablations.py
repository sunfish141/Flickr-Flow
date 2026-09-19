"""Leakage/budget contracts for training-only development diagnostics."""
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd
from threadpoolctl import threadpool_limits

from wildfire_data.model.training.ablations import (
    ABLATIONS, budget_metrics, forward_folds, run_ablations,
)
from wildfire_data.model.training.dataset import TARGET
from tests.model.test_experiments import FAST_CANDIDATES, synthetic_rows

EDGES = ('2026-06-10T00:00:00Z', '2026-06-20T00:00:00Z', '2026-06-30T00:00:00Z')


def time_fixture():
    frame = synthetic_rows().query("incident_split == 'train'").copy()
    for group in range(6):
        mask = frame.incident_group_id.eq(f'train-{group}')
        date = pd.Timestamp('2026-06-01T00:00:00Z') + pd.Timedelta(days=10*(group//2))
        frame.loc[mask, 'feature_cutoff_at'] = date.isoformat()
        frame.loc[mask, 'source_snapshot_time'] = date.isoformat()
    frame['target_end_at'] = (pd.to_datetime(frame.feature_cutoff_at, utc=True)+pd.Timedelta(hours=12)).astype(str)
    return frame


class AblationTests(unittest.TestCase):
    def test_feature_groups_are_unique_and_season_is_separate(self):
        for columns in ABLATIONS.values():
            self.assertEqual(len(set(columns)), len(columns))
        self.assertFalse(any('year_' in c for c in ABLATIONS['geometry_morphology']))
        self.assertEqual(len(ABLATIONS['context_weather']), 41)

    def test_forward_split_purges_incidents_and_waits_for_labels(self):
        frame = time_fixture()
        # An incident represented before and during validation must be purged.
        earlier = frame.loc[frame.incident_group_id.eq('train-2')].iloc[:2].copy()
        earlier['example_id'] += '-earlier'
        earlier['feature_cutoff_at'] = '2026-06-01T00:00:00Z'
        earlier['target_end_at'] = '2026-06-01T12:00:00Z'
        frame = pd.concat([frame, earlier], ignore_index=True)
        for i, (a, b) in enumerate(forward_folds(frame, EDGES)):
            train, valid = frame.iloc[a], frame.iloc[b]
            self.assertFalse(set(train.incident_group_id) & set(valid.incident_group_id))
            self.assertTrue((pd.to_datetime(train.target_end_at, utc=True, format='mixed')
                             <= pd.Timestamp(EDGES[i])-pd.Timedelta(hours=12)).all())
        frame.loc[0, 'target_end_at'] = '2026-06-09T23:00:00Z'
        self.assertNotIn(0, forward_folds(frame, EDGES)[0][0])

    def test_invalid_times_and_mixed_cohorts_are_rejected(self):
        frame = time_fixture()
        with self.assertRaisesRegex(ValueError, 'training rows only'):
            forward_folds(frame.assign(incident_split='calibration'), EDGES)
        with self.assertRaisesRegex(ValueError, 'boundaries'):
            forward_folds(frame, EDGES[::-1])
        with self.assertRaisesRegex(ValueError, 'label must end'):
            forward_folds(frame.assign(target_end_at=frame.feature_cutoff_at), EDGES)

    def test_budget_ties_are_order_independent(self):
        y = np.array([0, 1, 0, 1, 1])
        p = np.array([.9, .9, .9, .1, .1])
        score = budget_metrics(y, p, .4)
        self.assertAlmostEqual(score['precision'], 1/3)
        self.assertAlmostEqual(score['recall'], 2/9)
        self.assertEqual(score, budget_metrics(y[::-1], p[::-1], .4))
        self.assertEqual(budget_metrics(y, p, 1)['recall'], 1.)
        with self.assertRaises(ValueError):
            budget_metrics(y, np.full(5, np.nan))

    def test_synthetic_run_excludes_holdouts_and_does_not_save_models(self):
        frame = time_fixture()
        source = {'later_test_at': '2026-08-02T12:00:00Z', 'kind': 'synthetic_not_accuracy_evidence'}
        with tempfile.TemporaryDirectory() as directory, threadpool_limits(limits=1):
            output = Path(directory)/'run'
            result = run_ablations(frame, source, output, profiles={'geometry': ABLATIONS['geometry']},
                                   candidates=FAST_CANDIDATES, time_edges=EDGES, folds=2, threads=1)
            self.assertEqual(result['status'], 'complete')
            self.assertFalse(result['promoted'])
            self.assertEqual(list(output.glob('*.joblib')), [])
            scores = json.loads((output/'development.json').read_text())
            self.assertEqual(set(scores), {'grouped_incident', 'forward_unseen_incident'})
            with self.assertRaisesRegex(ValueError, 'immutable'):
                run_ablations(frame, source, output, time_edges=EDGES, folds=2, threads=1)
            with self.assertRaisesRegex(ValueError, 'later-time'):
                run_ablations(frame.assign(target_end_at='2026-08-04T00:00:00Z'), source,
                               Path(directory)/'bad', time_edges=EDGES, folds=2, threads=1)


if __name__ == '__main__':
    unittest.main()
