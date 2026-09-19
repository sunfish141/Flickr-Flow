"""Inspector summaries preserve source support, time cutoffs, and fire behavior."""

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import rasterio
from rasterio.transform import from_origin
from fastapi.testclient import TestClient

from wildfire_data.core.hashing import sha256_file
from wildfire_data.web.app import create_app
from wildfire_data.web.vegetation import InspectorSampler, vegetation_summary
from wildfire_data.model.features.vegetation_features import select_features
from wildfire_data.model.incident_transition import IncidentTransitionModel
from wildfire_data.model.recursive_transition import RECURSIVE_MODEL_FEATURE_COLUMNS
from wildfire_data.providers.vegetation.aggregation import aggregate_pixels
from providers.vegetation_fixture import source, POLICY, CELL
from web.test_web_app import SpreadEstimator, terrain
from model.test_spread import FuelPatch

ORIGIN = '2026-07-01T00:00:00Z'


class FixtureSampler:
    def __init__(self, *, tree=.4, other=.3, area=750000):
        self.calls = []
        cover = aggregate_pixels(CELL, 'cover', 'MOD44B', [
            {'area': area, 'tree': tree, 'non_tree': other, 'nonvegetated': 1-tree-other}])
        cover['source'] = source('MOD44B', 'cover', '2024-03-05T00:00:00Z',
                                 '2025-03-06T00:00:00Z', '2025-06-01T00:00:00Z')
        self.records = [cover]
        self.sources = {'cover': cover['source']}

    def sample_cell(self, cell_id, *, cutoff_at, simulation_at):
        self.calls.append((cell_id, cutoff_at, simulation_at))
        return select_features(self.records if cell_id == CELL else [], cutoff_at=cutoff_at,
                               simulation_at=simulation_at, policy=POLICY)


class VegetationSummaryTests(unittest.TestCase):
    def sample(self, sampler, origin=ORIGIN, simulation=ORIGIN):
        return vegetation_summary(sampler, CELL, origin_at=origin, simulation_at=simulation)

    def test_density_uses_observed_cover_not_full_cell_or_fuel(self):
        result = self.sample(FixtureSampler())
        self.assertEqual(result['status'], 'available')
        self.assertAlmostEqual(result['density_fraction'], .7)
        self.assertEqual(result['observations'][0]['valid_fraction'], .75)
        self.assertEqual(result['observations'][0]['start'], '2024-03-05T00:00:00Z')
        self.assertIsNone(result['land_cover'])
        self.assertNotIn('fuel_remaining', result)

    def test_bare_land_is_zero_density_and_missing_is_not_zero(self):
        self.assertEqual(self.sample(FixtureSampler(tree=0, other=0))['density_fraction'], 0)
        for sampler in (None, FixtureSampler(area=100000)):
            result = self.sample(sampler)
            self.assertEqual(result['status'], 'unavailable')
            self.assertIsNone(result['density_fraction'])

    def test_prediction_origin_freezes_publication_eligibility(self):
        sampler = FixtureSampler()
        before = self.sample(sampler, '2025-05-31T00:00:00Z', '2025-06-02T00:00:00Z')
        self.assertEqual(before['status'], 'unavailable')
        self.assertEqual(self.sample(sampler)['density_fraction'], self.sample(sampler, simulation='2026-07-03T00:00:00Z')['density_fraction'])

    def test_land_cover_summary_uses_measured_type(self):
        sampler = FixtureSampler()
        land = aggregate_pixels(CELL, 'land', 'NALCMS', [
            {'area': 600000, 'land_cover': 1}, {'area': 400000, 'land_cover': 9}])
        land['source'] = source()
        sampler.sources['land'] = land['source']
        sampler.records.append(land)
        result = self.sample(sampler)
        self.assertEqual(result['land_cover'], 'Needleleaf forest')
        self.assertEqual(result['mapped_vegetation_fraction'], 1)
        self.assertAlmostEqual(result['density_fraction'], .7)

    def test_quality_caution_accompanies_measured_cover(self):
        sampler = FixtureSampler()
        sampler.records[0]['values']['vegetation_cover_caution_fraction'] = .8
        result = self.sample(sampler)
        self.assertAlmostEqual(result['density_fraction'], .7)
        self.assertEqual(result['cover_caution_fraction'], .8)


class InspectorRasterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        raster = root / 'land.tif'
        # Three arbitrary adjacent cells: mixed vegetation/water/fill, urban,
        # and unsupported fill. None needs to exist in the training pilot.
        with rasterio.open(raster, 'w', driver='GTiff', width=6, height=2, count=1,
                           dtype='uint8', crs='ESRI:102008', transform=from_origin(0, 1000, 500, 500)) as ds:
            ds.write(np.array([[1, 6, 17, 17, 0, 0], [18, 0, 17, 17, 0, 0]], dtype='uint8'), 1)
        land = source()
        land['assets'] = [{'path': 'land.tif', 'sha256': sha256_file(raster), 'bands': {'land_cover': 1}}]
        self.config = root / 'sources.json'
        self.config.write_text(json.dumps({'sources': [land], 'policy': POLICY}))
        self.sampler = InspectorSampler(FixtureSampler(), self.config, max_cached_cells=2)
        self.addCleanup(self.sampler.close)

    def summary(self, cell=CELL, origin=ORIGIN, simulation=ORIGIN):
        return vegetation_summary(self.sampler, cell, origin_at=origin, simulation_at=simulation)

    def test_national_raster_supplements_sparse_pilot_and_preserves_canopy(self):
        rural = self.summary()
        self.assertAlmostEqual(rural['density_fraction'], .7)
        self.assertEqual(rural['mapped_vegetation_fraction'], .5)
        self.assertEqual(rural['observations'][1]['valid_fraction'], .75)
        urban = self.summary('naea-1km:x=1:y=0')
        self.assertEqual(urban['status'], 'available')
        self.assertEqual(urban['land_cover'], 'Urban land')
        self.assertEqual(urban['mapped_vegetation_fraction'], 0)
        self.assertIsNone(urban['density_fraction'])  # Urban class is not zero canopy.
        self.assertEqual(self.summary('naea-1km:x=2:y=0')['status'], 'unavailable')
        self.assertEqual(self.summary('naea-1km:x=50:y=0')['status'], 'unavailable')

    def test_historical_playback_cannot_unlock_later_capture_or_read_raster(self):
        with patch('wildfire_data.web.vegetation.SourceRaster') as raster:
            result = self.summary(origin='2024-12-31T00:00:00Z')
            self.assertEqual(result['status'], 'unavailable')
            raster.assert_not_called()

    def test_cache_is_bounded_and_reuses_cell_evidence_across_times(self):
        self.summary()
        with patch.object(self.sampler._raster, 'sample', wraps=self.sampler._raster.sample) as read:
            self.summary(simulation='2026-07-03T00:00:00Z')
            read.assert_not_called()
            for x in (1, 2):
                self.summary(f'naea-1km:x={x}:y=0')
            self.assertEqual(len(self.sampler._cache), 2)
            self.assertNotIn(CELL, self.sampler._cache)
            self.summary()
            self.assertEqual(read.call_count, 3)
        self.sampler.close()
        self.assertIsNone(self.sampler._raster)

    def test_worker_opened_rasters_close_cleanly_on_application_shutdown(self):
        with ThreadPoolExecutor(max_workers=1) as worker:
            self.assertEqual(worker.submit(self.summary).result()['status'], 'available')
        self.sampler.close()
        self.assertIsNone(self.sampler._raster)

    def test_raster_can_serve_without_a_canopy_store_and_rejects_changed_bytes(self):
        sampler = InspectorSampler(None, self.config)
        self.addCleanup(sampler.close)
        result = vegetation_summary(sampler, CELL, origin_at=ORIGIN, simulation_at=ORIGIN)
        self.assertEqual(result['status'], 'available')
        self.assertIsNone(result['density_fraction'])
        self.assertEqual(result['mapped_vegetation_fraction'], .5)
        (self.config.parent / 'land.tif').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            InspectorSampler(None, self.config)


class VegetationApiTests(unittest.TestCase):
    def setUp(self):
        self.sampler = FixtureSampler()
        self.model = IncidentTransitionModel(SpreadEstimator(), feature_columns=RECURSIVE_MODEL_FEATURE_COLUMNS)
        self.client = TestClient(create_app(allowed_hosts=['testserver'], model=self.model, terrain_provider=terrain,
            landscape=FuelPatch(), vegetation_sampler=self.sampler))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def test_lookup_is_display_only_and_preserves_replay(self):
        seed = self.client.post('/api/seed', json={'ignitions': [
            {'latitude': 53.02, 'longitude': -117.31, 'intensity': .8}]}).json()
        request = {'origin_at': seed['origin_at'], 'state': seed['state']}
        before = self.client.post('/api/step', json=request).json()
        response = self.client.post('/api/vegetation', json={'cell_id': CELL, 'origin_at': ORIGIN, 'simulation_at': ORIGIN})
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.json()['density_fraction'], .7)
        self.assertEqual(self.sampler.calls[-1][1], datetime(2026, 7, 1, tzinfo=timezone.utc))
        self.assertEqual(self.client.post('/api/step', json=request).json(), before)
        with patch.object(self.sampler, 'sample_cell', side_effect=OSError('private file path')):
            response = self.client.post('/api/vegetation', json={'cell_id': CELL, 'origin_at': ORIGIN, 'simulation_at': ORIGIN})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private file path', response.text)
            self.assertEqual(self.client.post('/api/step', json=request).json(), before)

    def test_missing_store_does_not_disable_model(self):
        with patch('wildfire_data.web.runtime.load_inspector_sampler', side_effect=FileNotFoundError()):
            with TestClient(create_app(allowed_hosts=['testserver'], model=self.model, terrain_provider=terrain, landscape=FuelPatch())) as client:
                self.assertTrue(client.get('/api/config').json()['model_ready'])
                response = client.post('/api/vegetation', json={'cell_id': CELL, 'origin_at': ORIGIN, 'simulation_at': ORIGIN})
                self.assertEqual(response.json()['status'], 'unavailable')

    def test_invalid_cells_and_temporal_context_are_rejected(self):
        body = {'cell_id': CELL, 'origin_at': ORIGIN, 'simulation_at': ORIGIN}
        for change in ({'cell_id': '../../secret'}, {'origin_at': '2026-07-01'},
                       {'simulation_at': '2026-06-30T00:00:00Z'}):
            self.assertEqual(self.client.post('/api/vegetation', json={**body, **change}).status_code, 422)


if __name__ == '__main__':
    unittest.main()
