"""Small, versioned feature ablations derived only from available past inputs.

These contracts describe observed-row experiments. Geometry still needs a
validated renderer before a selected model can replace the recursive map model.
"""
import numpy as np
import pandas as pd

from wildfire_data.model.estimators import GEOMETRY_COLUMNS, WEATHER_COLUMNS
from wildfire_data.model.features.schema import (
    FRONTIER_BASELINE_COLUMNS, STATIC_VEGETATION_COLUMNS, COVER_DIAGNOSTIC_COLUMNS,
)

VERSION = 'compact-observed-context/v1'
CONTEXT_COLUMNS = (
    'context_local_detection_fraction', 'context_outer_detection_count',
    'context_detections_per_active_cell', 'context_brightness_max_minus_mean',
    'context_old_detection_count', 'context_year_sin', 'context_year_cos',
)
PROFILES = {
    'frontier': FRONTIER_BASELINE_COLUMNS,
    'geometry': GEOMETRY_COLUMNS,
    'context': GEOMETRY_COLUMNS + CONTEXT_COLUMNS,
    'weather': WEATHER_COLUMNS,
    'vegetation': GEOMETRY_COLUMNS + STATIC_VEGETATION_COLUMNS + COVER_DIAGNOSTIC_COLUMNS,
}


def add_context(frame):
    result = frame.copy()
    local = result.firms_local_3x3_detection_count
    total = result.fire_5x5_detection_count
    active = result.fire_5x5_active_cell_count
    recent = result.fire_5x5_recent_12h_count
    if ((local < 0) | (total < local) | (active < 0) | (recent < 0) | (recent > total)).any():
        raise ValueError('Inconsistent nested fire counts')
    # No detections means the fraction is undefined, not a measured zero.
    result['context_local_detection_fraction'] = local / total.where(total > 0)
    result['context_outer_detection_count'] = total - local
    result['context_detections_per_active_cell'] = total / active.where(active > 0)
    result['context_brightness_max_minus_mean'] = (
        result.firms_local_3x3_bright_ti4_max - result.firms_local_3x3_bright_ti4_mean)
    result['context_old_detection_count'] = total - recent
    cutoff = pd.to_datetime(result.feature_cutoff_at, utc=True, format='mixed', errors='raise')
    if cutoff.isna().any():
        raise ValueError('Context requires the feature cutoff timestamp')
    phase = 2 * np.pi * (cutoff.dt.dayofyear - 1) / np.where(cutoff.dt.is_leap_year, 366, 365)
    result['context_year_sin'] = np.sin(phase)
    result['context_year_cos'] = np.cos(phase)
    return result


def add_optional_weather(frame):
    """Preserve missing weather; reject finite but physically invalid inputs."""
    result = frame.copy()
    names = ('weather_temperature_2m', 'weather_relative_humidity_2m',
             'weather_precipitation', 'weather_wind_u_10m', 'weather_wind_v_10m')
    values = result[list(names)].to_numpy(dtype=float)
    if np.isinf(values).any():
        raise ValueError('Infinite weather values')
    t, rh, rain, u, v = values.T
    if ((t < -100) | (t > 70) | (rh < 0) | (rh > 100) | (rain < 0)).any():
        raise ValueError('Weather outside supported physical ranges')
    result['weather_wind_speed_m_s'] = np.hypot(u, v)
    result['weather_vpd_kpa'] = .6108 * np.exp(17.27*t/(t+237.3)) * (1-rh/100)
    return result


def profile_contract(name):
    return {
        'profile': name, 'columns': list(PROFILES[name]), 'feature_version': VERSION,
        'network_required_for_prediction': False,
        'local_inputs': (['fire state', 'terrain pack', 'cached weather and history'] if name == 'weather'
                         else ['fire state', 'terrain pack', 'vegetation pack'] if name == 'vegetation'
                         else ['fire state', 'terrain pack']),
        'weather_evidence': 'historical_analysis_only' if name == 'weather' else None,
        'vegetation_evidence': 'retrospective_static_context' if name == 'vegetation' else None,
        'recursive_map_ready': False,
    }
