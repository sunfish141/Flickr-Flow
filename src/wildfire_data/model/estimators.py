"""Portable calibrated estimators; no training or web imports during unpickling."""
from dataclasses import dataclass
import numpy as np

from wildfire_data.model.features.schema import FRONTIER_BASELINE_COLUMNS
from wildfire_data.model.incident_transition import probability_logit

GEOMETRY = ('fire_5x5_detection_count', 'fire_5x5_active_cell_count', 'fire_5x5_recent_12h_count',
            'fire_nearest_detection_km', 'fire_nearest_cell_km', 'fire_nearest_detection_age_hours',
            'fire_distance_weighted_detection_count', 'fire_recent_fraction')
DIRECTIONAL = ('wind_from_nearest_fire_m_s', 'wind_from_fire_weighted_m_s', 'wind_nearest_alignment')
HISTORY = ('weather_rain_6h_mm', 'weather_wind_u_mean_6h', 'weather_wind_v_mean_6h',
           'weather_wind_speed_mean_6h', 'weather_wind_speed_max_6h', 'weather_wind_steadiness_6h')
GEOMETRY_COLUMNS = FRONTIER_BASELINE_COLUMNS + GEOMETRY
WEATHER_COLUMNS = GEOMETRY_COLUMNS + ('weather_relative_humidity_2m', 'weather_precipitation',
                   'weather_wind_speed_m_s', 'weather_vpd_kpa') + DIRECTIONAL + HISTORY


def derive_weather(frame):
    result = frame.copy()
    inputs = result[['weather_temperature_2m', 'weather_relative_humidity_2m',
                     'weather_precipitation', 'weather_wind_u_10m', 'weather_wind_v_10m']].to_numpy(dtype=float)
    if not np.isfinite(inputs).all():
        raise ValueError('Current weather must be finite; missing weather is not calm/dry')
    t, rh = inputs[:, 0], inputs[:, 1]
    if ((t < -100) | (t > 70) | (rh < 0) | (rh > 100) | (inputs[:, 2] < 0)).any():
        raise ValueError('Weather outside supported physical ranges')
    result['weather_wind_speed_m_s'] = np.hypot(inputs[:, 3], inputs[:, 4])
    result['weather_vpd_kpa'] = .6108 * np.exp(17.27*t/(t+237.3)) * (1-rh/100)
    return result


@dataclass
class CalibratedEstimator:
    estimator: object
    calibrator: object
    columns: tuple[str, ...]
    supported: tuple[int, ...]

    def predict_proba(self, values):
        values = np.asarray(values, dtype=float)
        if values.ndim != 2 or values.shape[1] != len(self.columns) or np.isinf(values).any():
            raise ValueError('Prediction feature matrix differs from the fitted contract')
        raw = self.estimator.predict_proba(values[:, self.supported])[:, 1]
        return self.calibrator.predict_proba(probability_logit(raw))

    def predict_frame(self, frame):
        return self.predict_proba(frame[list(self.columns)].to_numpy(dtype=float))[:, 1]


@dataclass
class WeatherPolicy:
    geometry: CalibratedEstimator
    weather: CalibratedEstimator
    threshold: float = .15

    def predict_frame(self, frame):
        derived = derive_weather(frame)
        return .75*self.geometry.predict_frame(derived) + .25*self.weather.predict_frame(derived)
