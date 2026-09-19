from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from shapely.geometry import box, LineString, shape
from shapely.ops import unary_union

from model.fuel_fixture import bundle, policy
from wildfire_data.providers.landscape.tiles import tile_bounds, tile_key, LandscapeMosaic
from wildfire_data.web.landscape_spread import ExpandingScenarios, ExpandingStep
from wildfire_data.web.local_spread import LocalScenarios
from wildfire_data.model.local_spread import LocalSpreadModel


class FakeTiles:
    identity = 'a'*64

    def __init__(self, root, roads=(), cover=None):
        self.root, self.roads, self.cover = Path(root), roads, cover
        self.loaded = []
        self.fail = False
        self.closed = False

    def close(self):
        self.closed = True

    def load(self, key, expected=None):
        if self.fail:
            raise OSError('offline')
        self.loaded.append(key)
        bounds = tile_bounds(key)
        cover = [(g.intersection(box(*bounds)), fuel) for g, fuel in self.cover if g.intersects(box(*bounds)) and g.intersection(box(*bounds)).area] if self.cover is not None else None
        sampler = bundle(self.root/f'{key[0]}_{key[1]}', bounds=bounds, roads=self.roads, cover=cover)
        if expected and expected != sampler.sha256:
            raise ValueError('Landscape manifest checksum mismatch')
        return sampler


class ExpandingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = FakeTiles(self.temp.name)
        self.local = LocalScenarios(None)
        self.local.policy, self.local.mesh_m = policy(), 100
        with patch('wildfire_data.web.landscape_spread.LandscapeTiles', return_value=self.store):
            self.scenarios = ExpandingScenarios({'source_config': 'sources.json', 'data_root': 'data', 'release': 'test'}, Path(self.temp.name)/'config.json', self.local)
        self.origin = datetime(2026,9,17,tzinfo=timezone.utc)
        self.first = self.store.load((0,0))
        self.coordinates = [self.first.to_geo.transform(2855,1555)]

    def seed(self):
        return self.scenarios.initialize(self.coordinates, self.origin)

    def step(self, frame):
        return self.scenarios.advance(ExpandingStep.model_validate({'state':frame['state'],'origin_at':frame['origin_at']}))

    def test_expands_across_tile_seam_without_reset_and_replays(self):
        seed = self.seed()
        self.assertEqual(len(seed['state']['tiles']),1)
        advanced = self.step(seed)
        self.assertGreater(len(advanced['state']['tiles']),1)
        self.assertFalse(advanced['boundary_reached'])
        self.assertFalse(advanced['finished'])
        self.assertEqual(advanced['state']['ignitions'],seed['state']['ignitions'])
        self.assertEqual(advanced['state']['incident_id'],seed['state']['incident_id'])
        self.assertEqual(advanced,self.step(seed))
        samplers = [self.store.load((t['x'],t['y'])) for t in advanced['state']['tiles']]
        model = LocalSpreadModel(LandscapeMosaic(samplers),self.local.policy,mesh_m=100)
        direct = model.frame(model.seed_ids(self.coordinates),720)
        self.assertEqual(advanced['active_patch_count'],direct['active_patch_count'])
        self.assertEqual(advanced['burned_patch_count'],direct['burned_patch_count'])
        initial = shape(seed['perimeters']['features'][0]['geometry'])
        burned = unary_union([shape(f['geometry']) for f in advanced['perimeters']['features'] if f['properties']['status']=='burned'])
        self.assertLess(initial.difference(burned).area,1e-12)

    def test_road_on_seam_still_blocks_spread(self):
        self.store.roads = [(LineString([(3000,0),(3000,3000)]),{})]
        advanced = self.step(self.seed())
        self.assertEqual(len(advanced['state']['tiles']),1)
        self.assertNotIn((1,0),self.store.loaded)

    def test_missing_tile_is_failure_not_invisible_barrier(self):
        seed = self.seed()
        self.store.fail = True
        with self.assertRaises(OSError):
            self.step(seed)
        self.store.fail = False
        self.assertGreater(self.step(seed)['burned_area_m2'],0)

    def test_profile_and_manifest_changes_rejected(self):
        seed = self.seed()
        seed['state']['profile']='0'*64
        with self.assertRaisesRegex(ValueError,'identity'):
            self.step(seed)
        seed = self.seed()
        seed['state']['tiles'][0]['sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'checksum'):
            self.step(seed)

    def test_tile_budget_does_not_drop_requested_locations(self):
        with self.assertRaisesRegex(ValueError,'budget'):
            self.scenarios.load([(x,0) for x in range(25)],{})

    def test_empty_and_oversized_satellite_requests_fail_before_loading_tiles(self):
        loaded = list(self.store.loaded)
        with self.assertRaisesRegex(ValueError, 'No eligible FIRMS'):
            self.scenarios.initialize([], self.origin, satellite=True)
        with self.assertRaisesRegex(ValueError, '501 starting fire cells'):
            self.scenarios.initialize(self.coordinates * 501, self.origin, satellite=True)
        self.assertEqual(self.store.loaded, loaded)

    def test_satellite_seeds_one_patch_and_reports_unsupported_cells(self):
        self.store.cover = [(box(0,0,1000,3000),'grassland'), (box(1000,0,3000,3000),'urban')]
        coords = [self.first.to_geo.transform(500,1500),self.first.to_geo.transform(1500,1500)]
        result = self.scenarios.initialize(coords,self.origin,satellite=True)
        self.assertEqual(result['active_patch_count'],1)
        self.assertEqual(result['metadata']['unsupported_observed_cells'],1)
        self.assertEqual(result['metadata']['mapped_starting_cells'],1)

    def test_sparse_far_apart_domains_do_not_allocate_the_intervening_world(self):
        a,b = self.store.load((0,0)),self.store.load((300,300))
        model=LocalSpreadModel(LandscapeMosaic([a,b]),self.local.policy,mesh_m=100,max_patches=2500)
        self.assertEqual(len(model.patches),1800)

    def test_negative_coordinate_alignment(self):
        self.assertEqual(tile_key(-.01,-3000.01),(-1,-2))

    def test_shutdown_closes_landscape_readers(self):
        self.local.expanding = self.scenarios
        self.local.close()
        self.assertTrue(self.store.closed)
        self.assertFalse(self.scenarios.models)
        self.assertFalse(self.scenarios.tile_models)

    def test_switching_regions_and_expansion_reuse_prepared_tile_graphs(self):
        builds = []
        original = LocalSpreadModel.__init__
        def counted(model, *args, **kwargs):
            original(model, *args, **kwargs)
            builds.append(model.sampler.sha256)
        with patch.object(LocalSpreadModel, '__init__', counted):
            first = self.seed()
            other = [self.first.to_geo.transform(31555, 31555)]
            self.scenarios.initialize(other, self.origin)
            self.assertEqual(self.seed(), first)
            self.assertEqual(len(builds), 2)
            advanced = self.step(first)
            self.assertEqual(len(builds), 3)  # Only the new neighbor tile is constructed.
            self.assertEqual(self.step(first), advanced)
            self.assertEqual(len(builds), 3)

    def test_cache_eviction_respects_patch_and_entry_budgets(self):
        from collections import OrderedDict
        from types import SimpleNamespace
        cache = OrderedDict()
        for i in range(4):
            self.scenarios.retain(cache, i, SimpleNamespace(patches=[None]*3), max_entries=3, max_patches=7)
        self.assertEqual(list(cache), [2,3])
        self.scenarios.retain(cache, 4, SimpleNamespace(patches=[]), max_entries=2, max_patches=7)
        self.assertEqual(list(cache), [3,4])

    def test_startup_warms_examples_without_creating_an_incident(self):
        self.store.data_root = Path(self.temp.name)
        self.scenarios.prewarm_tiles = 2
        lon, lat = self.coordinates[0]
        self.scenarios.warm([{'example_ignition': {'longitude': lon, 'latitude': lat}}])
        self.assertEqual(len(self.scenarios.tile_models), 1)
        self.assertEqual(len(self.scenarios.models), 0)
        with patch.object(LocalSpreadModel, '__init__', side_effect=AssertionError('Already prepared')):
            self.assertEqual(self.seed()['active_patch_count'], 1)


class ExpandingApiTests(unittest.TestCase):
    def setUp(self):
        ExpandingTests.setUp(self)
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from wildfire_data.web.landscape_spread import register_expanding_routes
        app = FastAPI()
        self.local.expanding = self.scenarios
        app.state.local_scenarios = self.local
        lon, lat = self.first.to_geo.transform(2500,1500)
        def observed(body):
            origin = '2026-05-12T00:00:00+00:00' if hasattr(body,'date') else self.origin.isoformat()
            result = {'points':[{'status':'active','longitude':lon,'latitude':lat}], 'origin_at':origin,
                      'metadata':{'eligible_detection_count':1},'finished':False}
            if hasattr(body,'date'):
                result['historical']={'date':'2026-05-11','start_date':'2026-05-11','bounds':body.bounds.model_dump(), 'points':[]}
            return result
        def historical_day(day,bounds):
            return [],{'date':day.isoformat(),'bounds':bounds.model_dump(), 'points':[{'cell_id':'later-observation','status':'historical'}]}
        register_expanding_routes(app,observed,observed,historical_day)
        self.client=TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.client.__exit__,None,None,None)
        self.bounds={'west':-97,'south':39,'east':-95,'north':41}

    def test_placed_and_live_firms_use_expanding_endpoints(self):
        lon,lat=self.coordinates[0]
        placed=self.client.post('/api/landscape/seed',json={'ignitions':[{'longitude':lon,'latitude':lat,'intensity':.7}]})
        self.assertEqual(placed.status_code,200,placed.text)
        response=self.client.post('/api/landscape/firms',json=self.bounds)
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        self.assertEqual(result['active_patch_count'],1)
        self.assertEqual(result['metadata']['satellite']['eligible_detection_count'],1)
        step=self.client.post('/api/landscape/step',json={'state':result['state'],'origin_at':result['origin_at']})
        self.assertEqual(step.status_code,200,step.text)
        self.assertEqual(step.json()['elapsed_hours'],12)

    def test_historical_observations_do_not_reseed_the_continuing_fire(self):
        response=self.client.post('/api/landscape/firms/historical',json={'date':'2026-05-11','bounds':self.bounds})
        self.assertEqual(response.status_code,200,response.text)
        result=response.json()
        body={'state':result['state'],'origin_at':result['origin_at'],'historical':{'start_date':'2026-05-11','bounds':self.bounds}}
        step=self.client.post('/api/landscape/step',json=body)
        self.assertEqual(step.status_code,200,step.text)
        self.assertEqual(step.json()['elapsed_hours'],24)
        self.assertEqual(step.json()['state']['ignitions'],result['state']['ignitions'])
        self.assertEqual(step.json()['historical']['date'],'2026-05-12')
        self.assertIn('current retained',step.json()['metadata']['landscape_time_basis'])
        self.assertEqual(step.json(),self.client.post('/api/landscape/step',json=body).json())

    def test_busy_and_failed_preparation_are_explicit_and_retryable(self):
        self.local.lock.acquire()
        try:
            response = self.client.post('/api/landscape/firms',json=self.bounds)
            self.assertEqual(response.status_code,503)
            self.assertEqual(response.headers['retry-after'], '2')
        finally:
            self.local.lock.release()
        self.store.fail=True
        response = self.client.post('/api/landscape/firms',json=self.bounds)
        self.assertEqual(response.status_code,503)
        self.assertNotIn('retry-after', response.headers)
        self.store.fail=False
        self.assertEqual(self.client.post('/api/landscape/firms',json=self.bounds).status_code,200)

    def test_real_app_landscape_firms_does_not_require_fitted_model(self):
        from fastapi.testclient import TestClient
        from wildfire_data.web.app import create_app
        from wildfire_data.web.live_firms import aggregate_current_firms
        from wildfire_data.web.settings import Settings
        from datetime import timedelta
        lon,lat=self.first.to_geo.transform(2500,1500)
        def loader(api_key,bounds,*,now):
            return aggregate_current_firms([{'longitude':lon,'latitude':lat,'bright_ti4':340.,
                'acquired_at':(now-timedelta(hours=6)).isoformat(),'provenance':{'product':'VIIRS_SNPP_NRT'}}],tuple(self.bounds.values()),now=now)
        settings=Settings(run_manifest=Path(self.temp.name)/'missing-model.json',
            data_root=Path(self.temp.name)/'data',local_config=Path(self.temp.name)/'missing-local.json')
        app=create_app(firms_loader=loader,allowed_hosts=['testserver'], settings=settings, vegetation_sampler=object())
        with TestClient(app) as client:
            app.state.local_scenarios=self.local
            app.state.runtime.model=None
            app.state.runtime.model_error='Fitted model intentionally unavailable'
            result=client.post('/api/landscape/firms',json=self.bounds)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['active_patch_count'],1)
            self.assertEqual(client.post('/api/firms',json=self.bounds).status_code,503)
