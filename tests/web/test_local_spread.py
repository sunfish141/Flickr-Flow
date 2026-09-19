import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from dataclasses import asdict
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from model.fuel_fixture import bundle, policy
from wildfire_data.web.app import create_app
from wildfire_data.web.local_spread import LocalScenarios
from web.test_web_app import SpreadEstimator, terrain
from model.test_spread import FuelPatch
from wildfire_data.model.incident_transition import IncidentTransitionModel
from wildfire_data.model.recursive_transition import RECURSIVE_MODEL_FEATURE_COLUMNS


class LocalApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sampler = bundle(Path(self.temp.name)/'landscape', bounds=(0, 0, 900, 900))
        self.config = Path(self.temp.name)/'config.json'
        self.config.write_text(json.dumps({'mesh_m': 30, 'policy': asdict(policy()),
            'regions': [{'id': 'fixture', 'label': 'Fixture', 'manifest': str(self.sampler.path), 'sha256': self.sampler.sha256}]}))
        m = IncidentTransitionModel(SpreadEstimator(), feature_columns=RECURSIVE_MODEL_FEATURE_COLUMNS)
        self.client = TestClient(create_app(model=m, terrain_provider=terrain, landscape=FuelPatch(),
            allowed_hosts=['testserver'], local_config=self.config))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)

    def seed(self):
        lon, lat = self.sampler.to_geo.transform(450, 455)
        response = self.client.post('/api/local/seed', json={'region': 'fixture',
            'ignitions': [{'longitude': lon, 'latitude': lat, 'intensity': .7}]})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_seed_state_roundtrip_and_polygons(self):
        frame = self.seed()
        self.assertTrue(frame['local'])
        self.assertEqual(frame['perimeters']['features'][0]['properties']['status'], 'active')
        request = {'state': frame['state'], 'origin_at': frame['origin_at']}
        a = self.client.post('/api/local/step', json=request)
        self.assertEqual(a.status_code, 200, a.text)
        self.assertEqual(a.json(), self.client.post('/api/local/step', json=request).json())
        self.assertEqual(a.json()['elapsed_hours'], 12)
        self.assertEqual(a.json()['state']['incident_id'], frame['state']['incident_id'])
        self.assertEqual(a.json()['metadata']['kind'], 'uncalibrated scenario')

    def test_old_grid_endpoint_remains_independent(self):
        self.seed()
        response = self.client.post('/api/seed', json={'ignitions': [{'latitude': 53.02, 'longitude': -117.31, 'intensity': .7}]})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('local', response.json())
        self.assertTrue(self.client.get('/api/config').json()['local_spread']['available'])

    def test_fixed_polygon_endpoint_advances_past_96_hours_without_restarting(self):
        from wildfire_data.model.local_spread import VEGETATED
        scenarios = self.client.app.state.local_scenarios
        scenarios.policy = policy(rates_m_min=dict.fromkeys(VEGETATED,.02), residence_minutes=1440)
        scenarios.model.cache_clear()
        frame = self.seed()
        incident = frame['state']['incident_id']
        for step in range(1,13):
            body = {'state':frame['state'], 'origin_at':frame['origin_at']}
            response = self.client.post('/api/local/step', json=body)
            self.assertEqual(response.status_code,200,response.text)
            frame = response.json()
            self.assertEqual(frame['elapsed_hours'], step*12)
            self.assertFalse(frame['finished'])
            self.assertEqual(frame['state']['incident_id'],incident)
        self.assertGreater(frame['active_patch_count'],0)
        self.assertEqual(self.client.post('/api/local/step',json=body).json(),frame)
        self.assertIsNone(self.client.get('/api/config').json()['local_spread']['max_steps'])

    def test_calendar_overflow_is_rejected_before_loading_a_model(self):
        frame = self.seed()
        for change in [{'state':{**frame['state'],'step_index':10**30}}, {'origin_at':'9999-12-31T23:00:00Z'}]:
            body = {'state':frame['state'], 'origin_at':frame['origin_at'], **change}
            with patch.object(self.client.app.state.local_scenarios, 'model', side_effect=AssertionError('Must validate time first')):
                response = self.client.post('/api/local/step',json=body)
            self.assertEqual(response.status_code,422,response.text)
            self.assertIn('calendar', response.text)

    def test_invalid_region_state_and_time_are_rejected(self):
        frame = self.seed()
        for key, value in [('model_sha256', '0'*64), ('seed_ids', [60000]), ('incident_id', '0'*64)]:
            response = self.client.post('/api/local/step', json={'state': {**frame['state'], key: value}, 'origin_at': frame['origin_at']})
            self.assertEqual(response.status_code, 422)
        response = self.client.post('/api/local/step', json={'state': frame['state'], 'origin_at': '2026-01-01T00:00:00'})
        self.assertEqual(response.status_code, 422)

    def test_optional_config_missing_does_not_claim_availability(self):
        configuration = LocalScenarios('/nonexistent-landscape-config').configuration()
        self.assertFalse(configuration['available'])
        self.assertEqual(configuration['presets'], [])

    def test_regional_presets_depend_on_expanding_archive_not_pilot_bundles(self):
        config = json.loads(self.config.read_text())
        presets = json.loads((Path(__file__).resolve().parents[2]/'config/local_spread.json').read_text())['expanding']['presets']
        config.update(regions=[], expanding={'enabled': True, 'presets': presets})
        self.config.write_text(json.dumps(config))
        with patch('wildfire_data.web.landscape_spread.ExpandingScenarios') as expanding:
            scenarios = LocalScenarios(self.config)
        self.addCleanup(scenarios.close)
        configuration = scenarios.configuration()
        self.assertTrue(configuration['available'])
        self.assertTrue(configuration['expanding'])
        self.assertEqual(configuration['regions'], [])
        self.assertEqual(configuration['presets'], presets)
        self.assertEqual({p['id'] for p in presets}, {'colorado', 'alberta'})
        for preset in presets:
            west, south, east, north = preset['bounds']
            example = preset['example_ignition']
            self.assertTrue(west < example['longitude'] < east)
            self.assertTrue(south < example['latitude'] < north)
        self.assertIs(scenarios.expanding, expanding.return_value)

    def test_missing_offline_archive_keeps_fixed_pilots_available(self):
        config=json.loads(self.config.read_text())
        config['expanding']={'enabled':True,'source_config':'missing-source.json',
                             'data_root':'data','release':'2026-08-19.0','road_archive':'missing-archive.json',
                             'presets':[{'id':'alberta','label':'Alberta'}]}
        self.config.write_text(json.dumps(config))
        scenarios=LocalScenarios(self.config)
        self.assertTrue(scenarios.configuration()['available'])
        self.assertFalse(scenarios.configuration()['expanding'])
        self.assertEqual(scenarios.configuration()['presets'], [])
        self.assertIn('Offline road archive unavailable',scenarios.configuration()['expanding_error'])
