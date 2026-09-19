"""Pure local model-routing policy; connectivity is not data validity.

Providers must validate the cached weather's spatial coverage, units, actual
feature values and history before marking it complete. No fetching occurs here.
This policy is not wired into the map until weather/geometry rollout is validated.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class WeatherEvidence:
    issued_at: datetime
    downloaded_at: datetime
    valid_from: datetime
    valid_until: datetime
    complete: bool
    source_kind: str = 'issued_forecast'


def choose_input_mode(*, origin_at, prediction_at, weather=None,
                      weather_model_validated=False, max_issue_age_hours=24,
                      horizon_hours=12):
    """Choose weather only if locally held evidence covers the requested horizon.

origin_at is when the scenario began (information cutoff); prediction_at advances
with simulation time. Data obtained after origin cannot leak into that scenario.
The issue-age limit is a configurable product assumption, not a learned fact.
"""
    times = [origin_at, prediction_at]
    if weather is not None:
        times += [weather.issued_at, weather.downloaded_at, weather.valid_from, weather.valid_until]
    if any(not isinstance(t, datetime) or t.tzinfo is None or t.utcoffset() is None for t in times):
        raise ValueError('Availability times must be timezone-aware')
    if (prediction_at < origin_at or not 0 < max_issue_age_hours < float('inf')
            or not 0 < horizon_hours < float('inf')):
        raise ValueError('Invalid prediction time or weather limits')
    if not weather_model_validated:
        return 'offline', 'weather_model_not_validated'
    if weather is None:
        return 'offline', 'weather_not_cached'
    if weather.source_kind != 'issued_forecast':
        return 'offline', 'weather_not_issued_forecast'
    if not weather.complete:
        return 'offline', 'weather_incomplete'
    if not weather.issued_at <= weather.downloaded_at <= origin_at:
        return 'offline', 'weather_unavailable_at_origin'
    if not weather.valid_from <= prediction_at or weather.valid_until < prediction_at + timedelta(hours=horizon_hours):
        return 'offline', 'weather_outside_valid_window'
    if prediction_at - weather.issued_at > timedelta(hours=max_issue_age_hours):
        return 'offline', 'weather_stale'
    return 'cached_weather', 'valid_local_forecast'
