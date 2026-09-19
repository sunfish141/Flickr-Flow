from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from wildfire_data.providers.scenario_weather import ScenarioWeather, WeatherSnapshot, WeatherUnavailable, FIELDS, UNITS, VERSION


ORIGIN = datetime(2026,8,20,tzinfo=timezone.utc)


def snapshot(origin=ORIGIN, *, mode='forecast', hours=96, u=0., v=0.):
    return WeatherSnapshot({'version': VERSION, 'mode': mode, 'origin_hour': origin.isoformat(),
        'start': (origin-timedelta(hours=6)).isoformat(), 'captured_at': origin.isoformat(),
        'location': {'latitude': 40., 'longitude': -96.}, 'rows': [[25.,30.,0.,u,v] for _ in range(hours)]})


def response(origin=ORIGIN):
    start = origin-timedelta(hours=6)
    values = {'time': [int((start+timedelta(hours=i)).timestamp()) for i in range(96)]}
    for name,value in zip(FIELDS,(25.,30.,0.,10.,270.)):
        values[name] = [value]*96
    return {'hourly_units': dict(zip(FIELDS,UNITS)), 'hourly': values}


class ScenarioWeatherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.payload = response()
        self.http = Mock()
        self.http.return_value.json.side_effect = lambda: self.payload
        self.provider = ScenarioWeather(self.temp.name,get=self.http)

    def test_capture_validates_units_and_converts_meteorological_wind(self):
        captured = self.provider.capture(-96,40,ORIGIN)
        features,metadata = captured.features(ORIGIN)
        self.assertAlmostEqual(features['weather_wind_u_10m'],10.)
        self.assertAlmostEqual(features['weather_wind_v_10m'],0.)
        self.assertEqual(metadata['mode'],'forecast')
        self.assertFalse(metadata['extended'])
        self.assertEqual(self.http.call_args.kwargs['params']['models'],'ecmwf_ifs')
        self.assertEqual(self.http.call_args.kwargs['params']['wind_speed_unit'],'ms')

    def test_snapshot_survives_memory_eviction_and_never_refreshes_replay(self):
        first = self.provider.capture(-96,40,ORIGIN)
        self.provider.cache.clear()
        self.assertEqual(self.provider.capture(-96,40,ORIGIN).sha256,first.sha256)
        self.assertEqual(self.provider.load(first.sha256).features(ORIGIN),first.features(ORIGIN))
        self.assertEqual(self.http.call_count,1)
        path = Path(self.temp.name)/(first.sha256+'.json')
        path.write_text('{}')
        self.provider.cache.clear()
        with self.assertRaisesRegex(WeatherUnavailable,'missing or invalid'):
            self.provider.load(first.sha256)

    def test_historical_uses_analysis_and_refuses_extrapolation(self):
        first = self.provider.capture(-96,40,ORIGIN,historical=True)
        self.assertIn('archive-api',self.http.call_args.args[0])
        self.assertEqual(first.features(ORIGIN)[1]['time_basis'],'retrospective analysis')
        with self.assertRaisesRegex(WeatherUnavailable,'does not cover'):
            first.features(ORIGIN+timedelta(days=10))

    def test_six_hour_history_uses_only_anchor_and_previous_five_hours(self):
        value = snapshot()
        value.rows = [[25.,30.,float(i),float(i),0.] for i in range(96)]
        features,_ = value.features(ORIGIN+timedelta(minutes=59))
        self.assertEqual(features['weather_rain_6h_mm'],sum(range(1,7)))
        self.assertEqual(features['weather_wind_u_mean_6h'],3.5)
        self.assertEqual(features['weather_wind_speed_max_6h'],6.)
        self.assertEqual(features['weather_wind_steadiness_6h'],1.)

    def test_beyond_forecast_is_explicit_and_keeps_clock_unbounded(self):
        value = snapshot(u=4.)
        features,metadata = value.features(ORIGIN+timedelta(days=365))
        self.assertTrue(metadata['extended'])
        self.assertEqual(metadata['time_basis'],'last forecast conditions held constant')
        self.assertEqual(features['weather_wind_u_mean_6h'],4.)
        self.assertEqual(features['weather_rain_6h_mm'],0.)

    def test_missing_hours_nulls_wrong_units_and_nonphysical_values_fail(self):
        for change in ['gap','null','unit','wind','humidity','short']:
            self.payload = response()
            if change == 'gap': self.payload['hourly']['time'][8] += 1
            if change == 'null': self.payload['hourly']['precipitation'][8] = None
            if change == 'unit': self.payload['hourly_units']['wind_speed_10m'] = 'km/h'
            if change == 'wind': self.payload['hourly']['wind_speed_10m'][8] = -1
            if change == 'humidity': self.payload['hourly']['relative_humidity_2m'][8] = 150
            if change == 'short': self.payload['hourly']['temperature_2m'].pop()
            with self.subTest(change=change),self.assertRaises(WeatherUnavailable):
                self.provider.capture(-96,40,ORIGIN)
        self.assertFalse(list(Path(self.temp.name).glob('*.json')))

    def test_traversal_digest_and_missing_antecedent_hours_fail(self):
        with self.assertRaises(WeatherUnavailable):
            self.provider.load('../outside')
        with self.assertRaisesRegex(WeatherUnavailable,'Six complete'):
            snapshot().features(ORIGIN-timedelta(hours=2))
