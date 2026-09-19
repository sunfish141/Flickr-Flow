from threading import Lock
import unittest

from web import test_landscape_spread as landscape_tests
from model.test_weather_hybrid import Predictor, calibration, terrain
from providers.test_scenario_weather import snapshot
from wildfire_data.providers.scenario_weather import WeatherUnavailable
from wildfire_data.web.weather_landscape import WeatherLandscapes


class PinnedWeather:
    def __init__(self):
        self.cache={}
        self.captures=[]
        self.fail=False
        self.hours=3000

    def capture(self,longitude,latitude,origin,*,historical=False):
        if self.fail:
            raise WeatherUnavailable('Weather unavailable in test')
        mode='historical' if historical else 'forecast'
        self.captures.append(mode)
        value=snapshot(origin.replace(minute=0,second=0,microsecond=0),mode=mode,hours=self.hours)
        self.cache[value.sha256]=value
        return value

    def load(self,digest):
        if digest not in self.cache:
            raise WeatherUnavailable('Pinned weather is missing')
        return self.cache[digest]


class WeatherLandscapeApiTests(unittest.TestCase):
    def setUp(self):
        landscape_tests.ExpandingApiTests.setUp(self)
        service=WeatherLandscapes.__new__(WeatherLandscapes)
        service.estimator=Predictor()
        service.identity='a'*64
        service.calibration=calibration()
        service._terrain=terrain
        service.terrain_lock=Lock()
        service.weather=PinnedWeather()
        self.scenarios.hybrid=self.service=service

    def seed(self,kind='seed'):
        lon,lat=self.coordinates[0]
        body={'ignitions':[{'longitude':lon,'latitude':lat,'intensity':1.}]} if kind=='seed' else (
            {'date':'2026-05-11','bounds':self.bounds} if kind.endswith('historical') else self.bounds.copy())
        response=self.client.post('/api/landscape/'+kind,json={**body,'weather_ml':True})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    def body(self,frame):
        value={'state':dict(frame['state']),'origin_at':frame['origin_at']}
        if frame.get('historical'):
            value['historical']={'start_date':frame['historical']['start_date'],'bounds':self.bounds}
        return value

    def step(self,body):
        result=self.client.post('/api/landscape/step',json=body)
        self.assertEqual(result.status_code,200,result.text)
        return result.json()

    def test_placed_live_and_historical_select_their_weather_source(self):
        for kind in ['seed','firms','firms/historical']:
            with self.subTest(kind=kind):
                initial=self.seed(kind)
                expected='historical' if kind.endswith('historical') else 'forecast'
                self.assertTrue(initial['weather_ml'])
                self.assertEqual(initial['metadata']['hybrid']['weather']['mode'],expected)
                advanced=self.step(self.body(initial))
                self.assertEqual(advanced['elapsed_hours'],24 if expected=='historical' else 12)
                self.assertEqual(advanced['state']['weather_snapshot'],initial['state']['weather_snapshot'])
                self.assertEqual(advanced['state']['incident_id'],initial['state']['incident_id'])
                self.assertGreater(advanced['burned_area_m2'],0)
                self.assertEqual(advanced,self.step(self.body(initial)))
        self.assertEqual(self.service.weather.captures,['forecast','forecast','historical'])

    def test_expansion_cache_eviction_and_replay_keep_pinned_weather_and_burns(self):
        initial=self.seed()
        advanced=self.step(self.body(initial))
        self.assertGreater(len(advanced['state']['tiles']),1)
        later=self.step(self.body(advanced))
        self.assertGreaterEqual(later['burned_area_m2'],advanced['burned_area_m2'])
        self.scenarios.models.clear()
        self.scenarios.tile_models.clear()
        self.assertEqual(advanced,self.step(self.body(initial)))
        self.assertEqual(len(self.service.weather.captures),1)

    def test_missing_snapshot_or_changed_model_cannot_silently_change_a_scenario(self):
        initial=self.seed()
        body=self.body(initial)
        self.service.identity='b'*64
        result=self.client.post('/api/landscape/step',json=body)
        self.assertEqual(result.status_code,422)
        self.assertIn('identity',result.text)
        self.service.identity='a'*64
        self.service.weather.cache.clear()
        result=self.client.post('/api/landscape/step',json=body)
        self.assertEqual(result.status_code,422)
        self.assertIn('Pinned weather',result.text)

    def test_unavailable_weather_does_not_fall_back_to_fixed_wind(self):
        self.service.weather.fail=True
        result=self.client.post('/api/landscape/firms',json={**self.bounds,'weather_ml':True})
        self.assertEqual(result.status_code,422)
        self.assertIn('Weather unavailable',result.text)
        ordinary=self.client.post('/api/landscape/firms',json=self.bounds)
        self.assertEqual(ordinary.status_code,200)
        self.assertNotIn('weather_ml',ordinary.json())

    def test_origin_and_snapshot_identity_cannot_be_replaced(self):
        initial=self.seed('seed')
        body=self.body(initial)
        body['state']['weather_snapshot']='b'*64
        result=self.client.post('/api/landscape/step',json=body)
        self.assertEqual(result.status_code,422)
        self.assertIn('Pinned weather',result.text)
        body=self.body(initial)
        from datetime import datetime,timedelta
        body['origin_at']=(datetime.fromisoformat(initial['origin_at'])+timedelta(hours=1)).isoformat()
        result=self.client.post('/api/landscape/step',json=body)
        self.assertEqual(result.status_code,422)
        self.assertIn('origin',result.text)

    def test_manual_historical_weather_needs_no_satellite_archive(self):
        lon,lat=self.coordinates[0]
        result=self.client.post('/api/landscape/seed',json={'ignitions':[{'longitude':lon,'latitude':lat,'intensity':1.}],
                               'weather_ml':True,'weather_date':'2026-08-20'})
        self.assertEqual(result.status_code,200,result.text)
        first=result.json()
        self.assertNotIn('historical',first)
        self.assertEqual(first['origin_at'],'2026-08-20T00:00:00+00:00')
        self.assertEqual(first['metadata']['hybrid']['weather']['mode'],'historical')
        self.assertEqual(self.step(self.body(first))['elapsed_hours'],12)

    def test_hybrid_search_cache_has_bounded_ignition_sets(self):
        frame=self.seed()
        model=next(iter(self.scenarios.models.values()))
        weather=self.service.weather.load(frame['state']['weather_snapshot'])
        from datetime import datetime
        origin=datetime.fromisoformat(frame['origin_at'])
        for i in range(5):
            self.service.search(model,(i,),origin,weather)
        self.assertEqual(len(model.hybrid_searches),2)

    def test_manual_history_stops_at_weather_coverage_and_requires_the_hybrid_mode(self):
        from shapely.geometry import box
        self.store.cover=[(box(2800,1500,2900,1600),'grassland')]
        self.service.weather.hours=31
        lon,lat=self.coordinates[0]
        body={'ignitions':[{'longitude':lon,'latitude':lat,'intensity':1.}],'weather_date':'2026-08-20'}
        self.assertEqual(self.client.post('/api/landscape/seed',json=body).status_code,422)
        body['weather_ml']=True
        first=self.client.post('/api/landscape/seed',json=body).json()
        advanced=self.step(self.body(first))
        self.assertFalse(advanced['finished'])
        final=self.step(self.body(advanced))
        self.assertTrue(final['finished'])
        result=self.client.post('/api/landscape/step',json=self.body(final))
        self.assertEqual(result.status_code,422)
        self.assertIn('historical weather',result.text)
