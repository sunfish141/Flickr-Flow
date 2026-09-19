from datetime import date
import gzip
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from wildfire_data.model.incident_transition import IncidentTransitionModel
from wildfire_data.model.recursive_transition import RECURSIVE_MODEL_FEATURE_COLUMNS
from wildfire_data.web.app import create_app
from wildfire_data.web.historical_firms import HistoricalFirmsStore, HistoricalFirmsError
from wildfire_data.web.live_firms import PRODUCTS
from model.test_spread import FuelPatch
from web.test_web_app import SpreadEstimator, terrain

BOUNDS = dict(west=-120, south=50, east=-110, north=60)


def write_day(root, day, detections=(), *, missing_product=None, status='complete'):
    for product in PRODUCTS:
        if product == missing_product:
            continue
        rows = [{'record_type': 'firms_detection', 'latitude': lat, 'longitude': lon,
                 'bright_ti4': 340., 'acquired_at': f'{day}T{hour}:00:00Z',
                 'provenance': {'product': product}} for lat, lon, hour in detections]
        hashes = []
        if rows:
            payload = ''.join(json.dumps(row) + '\n' for row in rows).encode()
            identity = hashlib.sha256(payload).hexdigest()
            path = root / f'normalized/fire-detections/acq-date={day}/{identity}.jsonl.gz'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(gzip.compress(payload))
            hashes = [identity]
        coverage = root / f'manifests/coverage/{day}-{product}.json'
        coverage.parent.mkdir(parents=True, exist_ok=True)
        coverage.write_text(json.dumps({'append_order': 1, 'scope': {'source': 'NASA FIRMS',
            'product': product, 'region': 'United States and Canada', 'tile': None,
            'coverage_start': day, 'coverage_end': day},
            'status': status if rows else 'empty-confirmed',
            'detail': {'normalized_artifact_ids': hashes}}))


class HistoricalArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_filter_deduplicate_and_compact_observations(self):
        write_day(self.root, '2026-05-11', [(53.02, -117.31, '12')] * 2 + [(40, -100, '12')])
        store = HistoricalFirmsStore(self.root)
        rows, display = store.load(date(2026, 5, 11), tuple(BOUNDS.values()))
        self.assertEqual(len(rows), 3)
        self.assertEqual(display['detection_count'], 3)
        self.assertEqual(display['cell_count'], 1)
        self.assertEqual(set(display['points'][0]), {'cell_id', 'latitude', 'longitude', 'status', 'detection_count'})
        self.assertEqual(display['bounds'], BOUNDS)

    def test_missing_product_and_missing_file_are_not_empty_days(self):
        write_day(self.root, '2026-05-11', missing_product=PRODUCTS[0])
        with self.assertRaisesRegex(HistoricalFirmsError, 'incomplete'):
            HistoricalFirmsStore(self.root).load(date(2026, 5, 11), tuple(BOUNDS.values()))
        write_day(self.root, '2026-05-11', [(53.02, -117.31, '12')])
        next((self.root / 'normalized').rglob('*.gz')).unlink()
        with self.assertRaisesRegex(HistoricalFirmsError, 'missing or unreadable'):
            HistoricalFirmsStore(self.root).load(date(2026, 5, 11), tuple(BOUNDS.values()))

    def test_explicit_empty_and_corrupt_artifact(self):
        write_day(self.root, '2026-05-11')
        self.assertEqual(HistoricalFirmsStore(self.root).load(date(2026, 5, 11), tuple(BOUNDS.values()))[1]['points'], [])
        write_day(self.root, '2026-05-12', [(53.02, -117.31, '12')])
        path = next((self.root / 'normalized').rglob('*.gz'))
        payload = gzip.decompress(path.read_bytes()).replace(b'340.0', b'341.0')
        path.write_bytes(gzip.compress(payload))
        with self.assertRaisesRegex(HistoricalFirmsError, 'checksum'):
            HistoricalFirmsStore(self.root).load(date(2026, 5, 12), tuple(BOUNDS.values()))

    def test_cache_is_bounded_and_failed_latest_coverage_is_respected(self):
        for day in range(11, 17):
            write_day(self.root, f'2026-05-{day}')
        store = HistoricalFirmsStore(self.root)
        for day in range(11, 17):
            store.load(date(2026, 5, day), tuple(BOUNDS.values()))
        self.assertEqual(len(store.cache), 4)
        path = self.root / f'manifests/coverage/2026-05-11-{PRODUCTS[0]}.json'
        entry = json.loads(path.read_text())
        entry.update(append_order=2, status='failed')
        (path.parent / 'latest.json').write_text(json.dumps(entry))
        with self.assertRaisesRegex(HistoricalFirmsError, 'incomplete'):
            HistoricalFirmsStore(self.root).load(date(2026, 5, 11), tuple(BOUNDS.values()))


class HistoricalApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        write_day(root, '2026-05-11', [(53.02, -117.31, '12'), (54, -116, '23')])
        # Different later observations must never replace the simulated state.
        write_day(root, '2026-05-12', [(55, -115, '12')])
        write_day(root, '2026-08-20', [(53.02, -117.31, '12')])
        write_day(root, '2026-08-21')
        model = IncidentTransitionModel(SpreadEstimator(), feature_columns=RECURSIVE_MODEL_FEATURE_COLUMNS, ignition_threshold=.2)
        self.client = TestClient(create_app(model=model, terrain_provider=terrain, landscape=FuelPatch(),
            vegetation_sampler=object(), historical_store=HistoricalFirmsStore(root)), base_url='http://localhost')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def load(self, day='2026-05-11'):
        response = self.client.post('/api/firms/historical', json={'date': day, 'bounds': BOUNDS})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def step(self, frame):
        return self.client.post('/api/step', json={'state': frame['state'], 'origin_at': frame['origin_at'],
            'historical': {key: frame['historical'][key] for key in ('start_date', 'bounds')}})

    def test_daily_alignment_seed_cutoff_and_no_reseeding(self):
        frame = self.load()
        self.assertEqual(frame['origin_at'], '2026-05-12T00:00:00+00:00')
        self.assertEqual(frame['active_count'], 1)  # 23:00 observations excluded by 3h lag.
        self.assertEqual(frame['historical']['cell_count'], 2)
        expected = frame
        for _ in range(2):
            response = self.client.post('/api/step', json={'state': expected['state'], 'origin_at': expected['origin_at']})
            self.assertEqual(response.status_code, 200, response.text)
            expected = response.json()
        response = self.step(frame)
        self.assertEqual(response.status_code, 200, response.text)
        actual = response.json()
        self.assertEqual(actual['state'], expected['state'])
        self.assertEqual(actual['elapsed_hours'], 24)
        self.assertEqual(actual['historical']['date'], '2026-05-12')
        self.assertEqual(actual['historical']['bounds'], BOUNDS)
        self.assertEqual(actual['historical']['detection_count'], 3)
        self.assertEqual(actual, self.step(frame).json())
        self.assertEqual(self.step(actual).status_code, 503)  # Missing May 13.

    def test_end_date_and_context_validation(self):
        frame = self.load('2026-08-20')
        final = self.step(frame).json()
        self.assertEqual(final['historical']['date'], '2026-08-21')
        self.assertEqual(final['historical']['points'], [])
        self.assertTrue(final['finished'])
        self.assertEqual(self.step(final).status_code, 422)
        self.assertTrue(self.load('2026-08-21')['finished'])
        frame['state']['step_index'] = 1
        self.assertEqual(self.step(frame).status_code, 422)
        frame['state']['step_index'] = 0
        frame['origin_at'] = '2026-08-20T12:00:00Z'
        self.assertEqual(self.step(frame).status_code, 422)

    def test_dates_and_bounds_are_server_validated(self):
        for day in ('2026-05-10', '2026-08-22', '2025-06-01', '../data', '2026-06-31'):
            self.assertEqual(self.client.post('/api/firms/historical', json={'date': day, 'bounds': BOUNDS}).status_code, 422)
        self.assertEqual(self.client.post('/api/firms/historical', json={'date': '2026-05-11', 'bounds': {**BOUNDS, 'east': -50}}).status_code, 422)
        self.assertTrue(self.client.get('/api/config').json()['historical_firms']['available'])


if __name__ == '__main__':
    unittest.main()
