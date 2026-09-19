"""The observed-row weather contract, independent of recursive serving."""

import numpy as np

from wildfire_data.model.features.schema import FRONTIER_BASELINE_COLUMNS


WEATHER_UNITS = {
    "weather_temperature_2m": "°C",
    "weather_relative_humidity_2m": "%",
    "weather_precipitation": "mm",
    "weather_wind_u_10m": "m/s",
    "weather_wind_v_10m": "m/s",
}
WEATHER_COLUMNS = tuple(WEATHER_UNITS)
WEATHER_FRONTIER_COLUMNS = FRONTIER_BASELINE_COLUMNS + WEATHER_COLUMNS


def weather_contract():
    return {
        "feature_set": "offline-frontier-weather-hourly/v1",
        "feature_columns": list(WEATHER_FRONTIER_COLUMNS),
        "weather_units": WEATHER_UNITS.copy(),
        "weather_mode": "historical_analysis",
        "weather_model": "ecmwf_ifs",
        "weather_hour_policy": "floor-anchor-to-utc-hour/v1",
        "weather_missing_policy": "reject missing or nonfinite core weather",
        "prediction_scope": "observed-row next-12-hour newly-burned probability",
    }


def validate_weather_frame(frame):
    values = frame[list(WEATHER_COLUMNS)].to_numpy(dtype=float)
    if not np.isfinite(values).all():
        raise ValueError("all five weather features must be finite and present")
    if not frame.weather_relative_humidity_2m.between(0, 100).all():
        raise ValueError("weather relative humidity must be between 0 and 100 percent")
    if not frame.weather_precipitation.ge(0).all():
        raise ValueError("weather precipitation must be nonnegative")
