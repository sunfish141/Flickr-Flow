import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from wildfire_data.model.training.weather_history_probe import UNITS, derive_history, parse_hourly, request_url, run_probe


class WeatherHistoryProbeTests(unittest.TestCase):
    def payload(self):
        dates = pd.date_range('2026-06-01T00:00:00Z', periods=240, freq='h')
        return {'utc_offset_seconds': 0, 'hourly_units': {'time': 'iso8601', **UNITS},
            'hourly': {'time': [d.strftime('%Y-%m-%dT%H:%M') for d in dates],
                       **{key: [0.2]*len(dates) for key in UNITS}}}

    def test_windows_exclude_future_and_first_accumulation_outside_window(self):
        frame = parse_hourly(self.payload())
        cutoff = pd.Timestamp('2026-06-09T12:45:00Z')
        first = derive_history(frame, cutoff)
        self.assertAlmostEqual(first['features']['history_precipitation_168h_mm'], 33.6)
        frame.loc[frame.index > cutoff, :] = 999.
        frame.loc[cutoff.floor('h')-pd.Timedelta(hours=168), 'precipitation'] = 999.
        self.assertEqual(first, derive_history(frame, cutoff))

    def test_missing_hour_and_missing_value_do_not_become_dry(self):
        frame = parse_hourly(self.payload())
        cutoff = pd.Timestamp('2026-06-09T12:00:00Z')
        frame = frame.drop(cutoff-pd.Timedelta(hours=2))
        result = derive_history(frame, cutoff)
        self.assertIsNone(result['features']['history_precipitation_24h_mm'])
        self.assertEqual(result['coverage']['history_precipitation_24h_mm']['present_hours'], 23)
        self.assertEqual(result['features']['history_soil_moisture_0_to_7cm_m3_m3'], .2)
        frame.loc[cutoff, 'soil_moisture_0_to_7cm'] = np.nan
        self.assertIsNone(derive_history(frame, cutoff)['features']['history_soil_moisture_0_to_7cm_m3_m3'])

    def test_bad_units_duplicates_ranges_and_naive_cutoff_rejected(self):
        payload = self.payload()
        payload['hourly_units']['precipitation'] = 'inch'
        with self.assertRaisesRegex(ValueError, 'units'):
            parse_hourly(payload)
        payload = self.payload()
        payload['hourly']['time'][1] = payload['hourly']['time'][0]
        with self.assertRaisesRegex(ValueError, 'timestamps'):
            parse_hourly(payload)
        payload = self.payload()
        payload['hourly']['soil_moisture_0_to_7cm'][0] = 1.1
        with self.assertRaisesRegex(ValueError, 'soil moisture'):
            parse_hourly(payload)
        with self.assertRaisesRegex(ValueError, 'timezone-aware'):
            derive_history(parse_hourly(self.payload()), '2026-06-09T12:00:00')

    def test_request_budget_is_bounded_and_model_is_pinned(self):
        url = request_url(54., -75., '2026-06-01', '2026-06-14')
        self.assertIn('models=ecmwf_ifs', url)
        self.assertIn('wind_speed_unit=ms', url)
        with self.assertRaisesRegex(ValueError, '14 days'):
            request_url(54., -75., '2026-06-01', '2026-06-15')
        with self.assertRaisesRegex(ValueError, 'coordinates'):
            request_url(float('nan'), -75., '2026-06-01', '2026-06-14')

    def test_interrupted_cache_resumes_without_redownloading_or_overwriting(self):
        payload = {**self.payload(), 'latitude': 54., 'longitude': -75.}
        raw = json.dumps(payload).encode()
        locations = [(54., -75.), (55., -76.)]
        args = (locations, '2026-06-01', '2026-06-10', '2026-06-09T12:00:00Z')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'cache'
            with patch('wildfire_data.model.training.weather_history_probe.fetch_raw',
                       side_effect=[raw, TimeoutError('synthetic timeout')]):
                with self.assertRaises(TimeoutError):
                    run_probe(*args, output)
            self.assertEqual((output/'location-00.json').read_bytes(), raw)
            with patch('wildfire_data.model.training.weather_history_probe.fetch_raw', return_value=raw) as fetch:
                manifest = run_probe(*args, output, resume=True)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual(manifest['complete_feature_sets'], 2)
                self.assertFalse(manifest['accuracy_tested'])
            with self.assertRaisesRegex(ValueError, 'immutable'):
                run_probe(*args, output, resume=True)

    def test_resume_rejects_changed_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'cache'
            args = ([(54., -75.)], '2026-06-01', '2026-06-10', '2026-06-09T12:00:00Z')
            with patch('wildfire_data.model.training.weather_history_probe.fetch_raw', side_effect=TimeoutError):
                with self.assertRaises(TimeoutError):
                    run_probe(*args, output)
            with self.assertRaisesRegex(ValueError, 'parameters differ'):
                run_probe([(55., -75.)], *args[1:], output, resume=True)


if __name__ == '__main__':
    unittest.main()
