"""Bounded, cached feasibility probe for additional historical weather inputs.

This collects retrospective analysis, NOT weather known at a past issue time.
It does not modify the release, train a classifier, or enable live weather use.
"""
import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.training.public_csv import write_json

ENDPOINT = 'https://archive-api.open-meteo.com/v1/archive'
UNITS = {'precipitation': 'mm', 'relative_humidity_2m': '%',
         'vapour_pressure_deficit': 'kPa', 'soil_moisture_0_to_7cm': 'm\u00b3/m\u00b3',
         'soil_moisture_7_to_28cm': 'm\u00b3/m\u00b3', 'wind_gusts_10m': 'm/s'}
MAX_BYTES = 4*1024*1024


def request_url(latitude, longitude, start, end):
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if not (math.isfinite(latitude) and math.isfinite(longitude)
            and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError('Invalid coordinates')
    if not 0 <= (last-first).days < 14:
        raise ValueError('Probe requests must span 1 to 14 days')
    return ENDPOINT + '?' + urlencode({'latitude': latitude, 'longitude': longitude,
        'start_date': start, 'end_date': end, 'hourly': ','.join(UNITS), 'models': 'ecmwf_ifs',
        'timezone': 'GMT', 'wind_speed_unit': 'ms', 'precipitation_unit': 'mm',
        'cell_selection': 'nearest', 'elevation': 'nan'})


def parse_hourly(payload):
    if payload.get('utc_offset_seconds') != 0:
        raise ValueError('Probe requires UTC weather')
    units = payload.get('hourly_units', {})
    if units.get('time') != 'iso8601' or any(units.get(k) != v for k, v in UNITS.items()):
        raise ValueError('Weather units differ from the feature contract')
    hourly = payload.get('hourly', {})
    timestamps = pd.to_datetime(hourly.get('time', []), utc=True, format='ISO8601')
    if (not len(timestamps) or timestamps.hasnans or timestamps.has_duplicates
            or not timestamps.is_monotonic_increasing or not timestamps.equals(timestamps.floor('h'))):
        raise ValueError('Weather timestamps must be unique ordered UTC hours')
    frame = pd.DataFrame({key: hourly[key] for key in UNITS}, index=timestamps).astype(float)
    if np.isinf(frame.to_numpy()).any() or (frame < 0).any().any():
        raise ValueError('Invalid nonnegative weather values')
    if (frame.relative_humidity_2m > 100).any() or (frame.filter(like='soil_moisture') > 1).any().any():
        raise ValueError('Invalid humidity or volumetric soil moisture')
    return frame


def derive_history(frame, cutoff):
    """Full hourly windows ending no later than cutoff; gaps stay unavailable.

    Precipitation at hour H covers the preceding hour. Daily API totals must
    not be used here, as an afternoon origin would then see future evening rain.
    Soil moisture is a coarse model proxy, not directly measured fuel moisture.
    """
    origin = pd.Timestamp(cutoff)
    if origin.tzinfo is None or pd.isna(origin):
        raise ValueError('Feature cutoff must be timezone-aware')
    anchor = origin.tz_convert('UTC').floor('h')
    features, support = {}, {}

    def aggregate(name, column, hours, operation):
        expected = pd.date_range(end=anchor, periods=hours, freq='h')
        values = frame[column].reindex(expected)
        count = int(values.notna().sum())
        support[name] = {'expected_hours': hours, 'present_hours': count}
        features[name] = float(getattr(values, operation)()) if count == hours else None

    for hours in (24, 72, 168):
        aggregate(f'history_precipitation_{hours}h_mm', 'precipitation', hours, 'sum')
    aggregate('history_rh_min_24h_percent', 'relative_humidity_2m', 24, 'min')
    aggregate('history_vpd_mean_24h_kpa', 'vapour_pressure_deficit', 24, 'mean')
    aggregate('history_gust_max_6h_m_s', 'wind_gusts_10m', 6, 'max')
    for column in ('soil_moisture_0_to_7cm', 'soil_moisture_7_to_28cm'):
        aggregate(f'history_{column}_m3_m3', column, 1, 'mean')
    return {'feature_cutoff_at': origin.isoformat(), 'last_weather_hour': anchor.isoformat(),
            'features': features, 'coverage': support,
            'evidence': 'retrospective_analysis_not_operational_asof_evidence'}


def fetch_raw(url):
    # One retry for transient transport/5xx failures. Never bypass TLS or retry
    # permission/rate-limit responses. Each response is byte- and timeout-bounded.
    for attempt in range(2):
        try:
            request = Request(url, headers={'User-Agent': 'Flickr-Flow-research-weather-probe/1.0'})
            with urlopen(request, timeout=30) as response:
                raw = response.read(MAX_BYTES+1)
            if len(raw) > MAX_BYTES:
                raise ValueError('Weather response exceeds the bounded cache budget')
            return raw
        except HTTPError as exc:
            if attempt or not 500 <= exc.code < 600:
                raise
        except (URLError, TimeoutError, ConnectionError):
            if attempt:
                raise
        time.sleep(1)


def run_probe(locations, start, end, cutoff, output, *, resume=False):
    if not 1 <= len(locations) <= 8:
        raise ValueError('A feasibility probe permits 1 to 8 locations')
    urls = [request_url(lat, lon, start, end) for lat, lon in locations]
    origin = pd.Timestamp(cutoff)
    if origin.tzinfo is None or pd.isna(origin):
        raise ValueError('Feature cutoff must be timezone-aware')
    if not date.fromisoformat(start) <= origin.tz_convert('UTC').date() <= date.fromisoformat(end):
        raise ValueError('Cutoff must lie inside the requested dates')
    output = Path(output)
    protocol = {'kind': 'bounded-historical-weather-probe/v1', 'status': 'planned',
        'source': 'Open-Meteo historical ECMWF IFS', 'source_kind': 'historical_analysis',
        'documentation': 'https://open-meteo.com/en/docs/historical-weather-api',
        'requests': urls, 'feature_cutoff_at': origin.isoformat(), 'max_bytes_per_response': MAX_BYTES,
        'notes': ['No training labels or user credentials are sent.',
                  'Reanalysis retrieved now is not evidence of availability at the historical cutoff.',
                  'No model training, original-release edits or production promotion.']}
    outcomes = []
    if output.exists():
        if not resume or (output/'run_manifest.json').exists():
            raise ValueError('Completed caches are immutable; use --resume only for an incomplete run')
        if json.loads((output/'protocol.json').read_text()) != protocol:
            raise ValueError('Resume parameters differ from the cached protocol')
        if (output/'features.json').exists():
            outcomes = json.loads((output/'features.json').read_text())
        if len(outcomes) > len(locations):
            raise ValueError('Unexpected cached locations')
        for index, outcome in enumerate(outcomes):
            path = output/f'location-{index:02d}.json'
            if (outcome['raw_path'] != path.name or outcome['bytes'] != path.stat().st_size
                    or outcome['sha256'] != sha256_file(path)):
                raise ValueError('Cached response failed integrity checks')
            if derive_history(parse_hourly(json.loads(path.read_bytes())), cutoff)['features'] != outcome['features']:
                raise ValueError('Cached features disagree with the raw response')
    else:
        output.mkdir(parents=True)
        write_json(output/'protocol.json', protocol)
    for index, (location, url) in enumerate(zip(locations, urls)):
        if index < len(outcomes):
            continue
        print(f'Historical weather probe {index+1}/{len(locations)}', flush=True)
        path = output/f'location-{index:02d}.json'
        if path.exists():
            raise ValueError('Unindexed raw response exists; preserve it and use a new output directory')
        raw = fetch_raw(url)
        path.write_bytes(raw)
        retrieved = datetime.now(timezone.utc).isoformat()
        payload = json.loads(raw)
        frame = parse_hourly(payload)
        outcome = {'requested_latitude': location[0], 'requested_longitude': location[1],
            'source_latitude': payload['latitude'], 'source_longitude': payload['longitude'],
            'retrieved_at': retrieved, 'raw_path': path.name, 'bytes': len(raw),
            'sha256': hashlib.sha256(raw).hexdigest(), 'returned_hours': len(frame),
            'first_returned_hour': frame.index.min().isoformat(),
            'last_returned_hour': frame.index.max().isoformat(),
            **derive_history(frame, cutoff)}
        outcomes.append(outcome)
        write_json(output/'features.json', outcomes)
    manifest = {'kind': protocol['kind'], 'status': 'complete', 'source_kind': 'historical_analysis',
        'locations': len(outcomes), 'raw_cache_bytes': sum(r['bytes'] for r in outcomes),
        'complete_feature_sets': sum(all(v is not None for v in r['features'].values()) for r in outcomes),
        'promoted': False, 'accuracy_tested': False,
        'artifacts': {p.name: {'sha256': sha256_file(p)} for p in output.iterdir() if p.is_file()}}
    write_json(output/'run_manifest.json', manifest)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--latitude', type=float, nargs='+', required=True)
    parser.add_argument('--longitude', type=float, nargs='+', required=True)
    parser.add_argument('--start-date', required=True)
    parser.add_argument('--end-date', required=True)
    parser.add_argument('--cutoff', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--resume', action='store_true', help='Resume an incomplete matching cache, verifying saved responses')
    args = parser.parse_args()
    if len(args.latitude) != len(args.longitude):
        parser.error('Latitude/longitude counts must match')
    manifest = run_probe(list(zip(args.latitude, args.longitude)), args.start_date,
                         args.end_date, args.cutoff, args.output, resume=args.resume)
    print(json.dumps(manifest, indent=2), flush=True)


if __name__ == '__main__':
    main()
