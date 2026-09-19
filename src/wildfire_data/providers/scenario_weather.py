"""Pinned, validated Open-Meteo weather for experimental landscape scenarios."""
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re

import requests


FIELDS = ('temperature_2m', 'relative_humidity_2m', 'precipitation', 'wind_speed_10m', 'wind_direction_10m')
UNITS = ('°C', '%', 'mm', 'm/s', '°')
VERSION = 'pinned-scenario-weather/v1'


class WeatherUnavailable(ValueError):
    pass


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class WeatherSnapshot:
    def __init__(self, document):
        self.document = document
        if document['version'] != VERSION or document['mode'] not in ('forecast', 'historical'):
            raise WeatherUnavailable('Unsupported scenario weather snapshot')
        self.origin_hour = datetime.fromisoformat(document['origin_hour'])
        self.start = datetime.fromisoformat(document['start'])
        self.rows = document['rows']
        if self.start.tzinfo is None or self.origin_hour.tzinfo is None or not 7 <= len(self.rows) <= 3000:
            raise WeatherUnavailable('Invalid weather time coverage')
        for row in self.rows:
            if len(row) != 5 or any(type(v) not in (int, float) or not math.isfinite(v) for v in row):
                raise WeatherUnavailable('Weather contains missing or nonfinite measurements')
            t, rh, rain, u, v = row
            if not (-100 <= t <= 70 and 0 <= rh <= 100 and rain >= 0 and math.hypot(u, v) <= 60):
                raise WeatherUnavailable('Weather measurements exceed supported physical ranges')
        self.end = self.start + timedelta(hours=len(self.rows)-1)
        self.sha256 = hashlib.sha256(encoded(document)).hexdigest()

    def features(self, when):
        hour = when.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        index = int((hour-self.start).total_seconds()//3600)
        if index < 5:
            raise WeatherUnavailable('Six complete antecedent weather hours are required')
        extended = index >= len(self.rows)
        if extended and self.document['mode'] != 'forecast':
            raise WeatherUnavailable('Historical weather does not cover this simulation time')
        # Deliberate scenario extrapolation only after the last forecast hour.
        # Interior missing hours are rejected when creating the snapshot.
        recent = [self.rows[min(i, len(self.rows)-1)] for i in range(index-5, index+1)]
        t, rh, rain, u, v = recent[-1]
        mean_u = sum(r[3] for r in recent)/6
        mean_v = sum(r[4] for r in recent)/6
        speeds = [math.hypot(r[3], r[4]) for r in recent]
        mean_speed = sum(speeds)/6
        features = dict(zip(('weather_temperature_2m', 'weather_relative_humidity_2m', 'weather_precipitation',
                             'weather_wind_u_10m', 'weather_wind_v_10m'), (t, rh, rain, u, v)))
        features.update(weather_rain_6h_mm=sum(r[2] for r in recent),
                        weather_wind_u_mean_6h=mean_u, weather_wind_v_mean_6h=mean_v,
                        weather_wind_speed_mean_6h=mean_speed, weather_wind_speed_max_6h=max(speeds),
                        weather_wind_steadiness_6h=min(1., math.hypot(mean_u, mean_v)/mean_speed) if mean_speed else 0.)
        return features, {'mode': self.document['mode'], 'extended': extended, 'hour': hour.isoformat(),
                          'available_through': self.end.isoformat(), 'snapshot': self.sha256,
                          'location': self.document['location'], 'temperature_c': t, 'humidity_percent': rh,
                          'precipitation_mm': rain, 'wind_east_m_s': u, 'wind_north_m_s': v,
                          'source': 'Open-Meteo / ECMWF IFS', 'captured_at': self.document['captured_at'],
                          'spatial_assumption': 'one weather location applied to the entire scenario',
                          'time_basis': 'last forecast conditions held constant' if extended else
                              'retrospective analysis' if self.document['mode'] == 'historical' else 'captured forecast'}


class ScenarioWeather:
    def __init__(self, directory, *, get=requests.get):
        self.directory = Path(directory)
        self.get = get
        self.cache = OrderedDict()

    def remember(self, snapshot):
        self.cache[snapshot.sha256] = snapshot
        self.cache.move_to_end(snapshot.sha256)
        while len(self.cache) > 32:
            self.cache.popitem(last=False)
        return snapshot

    def load(self, digest):
        if not re.fullmatch('[0-9a-f]{64}', digest):
            raise WeatherUnavailable('Invalid weather snapshot identity')
        if digest in self.cache:
            return self.remember(self.cache[digest])
        try:
            raw = (self.directory/(digest+'.json')).read_bytes()
            if len(raw) > 512_000 or hashlib.sha256(raw).hexdigest() != digest:
                raise ValueError('Weather checksum mismatch')
            snapshot = WeatherSnapshot(json.loads(raw))
            if snapshot.sha256 != digest:
                raise ValueError('Weather identity mismatch')
            return self.remember(snapshot)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise WeatherUnavailable('Pinned weather is missing or invalid; start a new scenario') from exc

    def capture(self, longitude, latitude, origin, *, historical=False):
        origin = origin.astimezone(timezone.utc)
        hour = origin.replace(minute=0, second=0, microsecond=0)
        mode = 'historical' if historical else 'forecast'
        # Repeated placements in the same source grid/hour reuse one capture.
        location = {'latitude': round(latitude, 3), 'longitude': round(longitude, 3)}
        key = hashlib.sha256(encoded([VERSION, mode, location, hour.isoformat()])).hexdigest()
        index_path = self.directory/(key+'.ref')
        if index_path.is_file():
            return self.load(index_path.read_text())
        params = {**location, 'hourly': ','.join(FIELDS), 'timezone': 'UTC', 'timeformat': 'unixtime',
                  'wind_speed_unit': 'ms', 'temperature_unit': 'celsius', 'precipitation_unit': 'mm',
                  'cell_selection': 'nearest', 'elevation': 'nan'}
        if historical:
            url = 'https://archive-api.open-meteo.com/v1/archive'
            params.update(models='ecmwf_ifs', start_date=(hour-timedelta(hours=6)).date().isoformat(),
                          end_date='2026-08-22')
        else:
            url = 'https://api.open-meteo.com/v1/ecmwf'
            params.update(models='ecmwf_ifs', past_days=2, forecast_days=15)
        try:
            response = self.get(url, params=params, timeout=(10, 45))
            response.raise_for_status()
            payload = response.json()
            if any(payload['hourly_units'][f] != unit for f, unit in zip(FIELDS, UNITS)):
                raise ValueError('Unexpected weather units')
            hourly = payload['hourly']
            times = hourly['time']
            if not 7 <= len(times) <= 3000 or any(b-a != 3600 for a,b in zip(times, times[1:])):
                raise ValueError('Incomplete hourly weather series')
            if any(len(hourly[f]) != len(times) for f in FIELDS):
                raise ValueError('Mismatched weather columns')
            rows = []
            for values in zip(*(hourly[f] for f in FIELDS)):
                if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
                    raise ValueError('Missing weather measurement')
                t,rh,rain,speed,direction = values
                if not 0 <= speed <= 60 or not 0 <= direction <= 360:
                    raise ValueError('Invalid weather wind')
                # Meteorological direction is where wind comes FROM.
                angle = math.radians(direction)
                rows.append([t,rh,rain,-speed*math.sin(angle),-speed*math.cos(angle)])
            document = {'version': VERSION, 'mode': mode, 'location': location,
                        'origin_hour': hour.isoformat(), 'captured_at': datetime.now(timezone.utc).isoformat(),
                        'start': datetime.fromtimestamp(times[0],timezone.utc).isoformat(), 'rows': rows,
                        'request': {'url': url, 'parameters': params}}
            snapshot = WeatherSnapshot(document)
            snapshot.features(origin)
            if snapshot.end < hour + timedelta(hours=12):
                raise ValueError('Weather does not cover the first simulation step')
        except (requests.RequestException, ValueError, KeyError, TypeError, OverflowError) as exc:
            raise WeatherUnavailable('Weather is unavailable or incomplete. Retry loading this scenario; no calm/dry defaults were substituted.') from exc
        self.directory.mkdir(parents=True, exist_ok=True)
        raw = encoded(document)
        if len(raw) > 512_000 or sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file()) + len(raw) > 256_000_000:
            raise WeatherUnavailable('The scenario weather cache is full; free old weather snapshots on the server')
        path = self.directory/(snapshot.sha256+'.json')
        temporary = path.with_suffix('.partial')
        temporary.write_bytes(raw)
        temporary.replace(path)
        temporary = index_path.with_suffix('.partial')
        temporary.write_text(snapshot.sha256)
        temporary.replace(index_path)
        return self.remember(snapshot)
