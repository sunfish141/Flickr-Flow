import tempfile
import unittest
from unittest.mock import Mock
from datetime import timedelta

import numpy as np
from shapely.geometry import LineString, box

from model.fuel_fixture import bundle, policy
from providers.test_scenario_weather import ORIGIN, snapshot
from wildfire_data.model.local_spread import LocalSpreadModel
from wildfire_data.model.weather_hybrid import HybridSearch, feature_rows
from wildfire_data.model.recursive_transition import SyntheticObservationCalibration
from wildfire_data.model.estimators import WEATHER_COLUMNS, derive_weather


def calibration():
    return SyntheticObservationCalibration((2.,)*5,(1.,)*5,1,(ORIGIN.isoformat(),),'a'*64)


def terrain(cell_id):
    return {'terrain_valid':1.,'terrain_elevation_m':500.,'terrain_slope_degrees':1.,
            'terrain_aspect_defined':1.,'terrain_aspect_sin':0.,'terrain_aspect_cos':1.}


class Predictor:
    threshold = .15
    def __init__(self,p=1.):
        self.p,self.frames = p,[]
    def predict_frame(self,frame):
        derive_weather(frame)[list(WEATHER_COLUMNS)].to_numpy(dtype=float)
        self.frames.append(frame.copy())
        return np.full(len(frame),self.p)


class WeatherHybridTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.sampler=bundle(self.temp.name,bounds=(0,0,2400,30))
        self.model=LocalSpreadModel(self.sampler,policy())
        self.seed=min(range(len(self.model.centers)),key=lambda i:self.model.centers[i].x)

    def search(self,*,p=1.,weather=None,model=None):
        return HybridSearch(model or self.model,(self.seed,),ORIGIN,weather or snapshot(),
                            Predictor(p),calibration(),terrain,'a'*64)

    def test_ml_probability_changes_reachable_cells_without_becoming_a_speed(self):
        accepted,blocked=self.search(),self.search(p=0.)
        accepted.advance(3000)
        blocked.advance(3000)
        self.assertTrue(any(self.model.centers[i].x>1000 for i in accepted.times))
        self.assertTrue(all(self.model.centers[i].x<1000 for i in blocked.times))
        # Within the seed cell both policies retain the configured travel times.
        for i,time in blocked.times.items():
            self.assertEqual(accepted.times[i],time)

    def test_wind_changes_arrivals_and_old_frames_replay_without_refilling_fuel(self):
        calm=self.search()
        wind=self.search(weather=snapshot(u=10.))
        early=wind.frame(720)
        later=wind.frame(1440)
        self.assertGreater(len(wind.times),len(calm.advance(1440)))
        self.assertEqual(wind.frame(720),early)
        self.assertEqual(wind.frame(1440),later)
        complete=wind.frame(365*24*60)
        self.assertEqual(complete['active_patch_count'],0)
        self.assertEqual(complete['burned_patch_count'],len(self.model.patches))
        self.assertTrue(complete['assumptions']['hybrid']['weather']['extended'])
        self.assertEqual(wind.frame(720),early)

    def test_road_and_water_remain_barriers_even_with_probability_one(self):
        for kind in ['road','water']:
            args={'roads':[(LineString([(1200,0),(1200,30)]),{})]} if kind=='road' else {
                'cover':[(box(0,0,1200,30),'grassland'),(box(1200,0,1230,30),'water'),(box(1230,0,2400,30),'grassland')]}
            sampler=bundle(self.temp.name+'/'+kind,bounds=(0,0,2400,30),**args)
            model=LocalSpreadModel(sampler,policy())
            search=self.search(model=model)
            search.advance(5000)
            self.assertTrue(all(model.centers[i].x<1200 for i in search.times))

    def test_feature_contract_matches_observed_extractors_and_uses_weather_history(self):
        active={self.model.patches[self.seed].cell_id:1.}
        weather,_=snapshot(u=5.).features(ORIGIN)
        target=self.model.patches[-1].cell_id
        rows=feature_rows([target],active,ORIGIN,weather,terrain,calibration())
        derived=__import__('wildfire_data.model.estimators',fromlist=['derive_weather']).derive_weather(rows)
        self.assertTrue(set(WEATHER_COLUMNS).issubset(derived.columns))
        self.assertEqual(rows.iloc[0].fire_5x5_detection_count,2)
        self.assertEqual(rows.iloc[0].fire_nearest_detection_age_hours,7.5)
        self.assertEqual(rows.iloc[0].weather_wind_u_mean_6h,5.)
        self.assertGreater(rows.iloc[0].wind_from_nearest_fire_m_s,0.)

    def test_estimator_failure_is_retryable_without_partially_installing_a_window(self):
        search=self.search()
        search.estimator.predict_frame=Mock(side_effect=ValueError('model failure'))
        with self.assertRaisesRegex(ValueError,'model failure'):
            search.frame(720)
        self.assertEqual(search.epoch,-1)
        self.assertEqual(search.times,{self.seed:0.})
        search.estimator=Predictor()
        self.assertEqual(search.frame(720),self.search().frame(720))

    def test_larger_horizon_and_fresh_search_produce_identical_results(self):
        incremental=self.search(weather=snapshot(u=4.))
        incremental.frame(0)
        incremental.frame(720)
        frame=incremental.frame(6000)
        self.assertEqual(frame,self.search(weather=snapshot(u=4.)).frame(6000))
        for record in incremental.estimator.frames:
            self.assertTrue(record.cell_id.is_unique)

    def test_nonfinite_probabilities_are_rejected(self):
        for p in [float('nan'),-1.,2.]:
            with self.subTest(p=p),self.assertRaisesRegex(ValueError,'invalid admission'):
                self.search(p=p).frame(720)

    def test_missing_csv_terrain_preserves_the_feature_contract_and_missingness(self):
        active={self.model.patches[self.seed].cell_id:1.}
        weather,_=snapshot().features(ORIGIN)
        rows=feature_rows([self.model.patches[-1].cell_id],active,ORIGIN,weather,
                         lambda cell_id:{'terrain_valid':False,'terrain_aspect_defined':False},calibration())
        self.assertTrue(np.isnan(rows.iloc[0].terrain_elevation_m))
        self.assertTrue(np.isnan(rows.iloc[0].terrain_slope_degrees))
        self.assertFalse(rows.iloc[0].terrain_valid)
        self.assertEqual(Predictor().predict_frame(rows).tolist(),[1.])

    def test_a_frame_straddling_the_forecast_end_is_already_labeled_as_an_assumption(self):
        # Coverage ends at origin + 89 h; the 84–96 h step straddles its end.
        frame=self.search().frame(96*60)
        weather=frame['assumptions']['hybrid']['weather']
        self.assertTrue(weather['extended'])
        self.assertEqual(weather['time_basis'],'window weather held beyond forecast coverage')
