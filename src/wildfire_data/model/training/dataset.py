"""Checksum-verified, projected CSV loading without the collection pipeline."""
import csv
import json
from pathlib import Path

import pandas as pd

from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.features.schema import DEFAULT_MODEL_FEATURE_COLUMNS

TARGET = 'target_newly_burned_12h'
IDENTITY = ('cell_id', 'source_snapshot_time', 'anchor_at', 'feature_cutoff_at',
            'target_end_at', TARGET, 'dataset_split')
COHORTS = ('train', 'calibration', 'held_incident', 'held_region', 'later_time', 'unassigned')
REQUIRED_CSVS = {'candidate_examples.csv', 'incident_assignments.csv', 'weather_features.csv',
                 'weather_history.csv', 'directional_features.csv', 'vegetation_features.csv',
                 'landscape_features.csv', 'unscored_positives.csv'}


class DatasetError(ValueError):
    pass


def contained(root, name):
    path = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts or path.is_symlink():
        raise DatasetError('Dataset path must be local and relative')
    if not path.resolve().is_relative_to(root.resolve()):
        raise DatasetError('Dataset path escapes the release')
    return path


def verify_release(root):
    root = Path(root).resolve()
    checks = {}
    inventory = []
    for line in (root / 'SHA256SUMS').read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        if name in checks or len(digest) != 64:
            raise DatasetError('Invalid checksum inventory')
        inventory.append((contained(root, name), name, digest))
        checks[name] = digest
    missing = [name for path, name, _ in inventory if not path.is_file()]
    if missing:
        raise DatasetError('Missing release files: ' + ', '.join(missing))
    for path, name, digest in inventory:
        if sha256_file(path) != digest:
            raise DatasetError(f'Checksum mismatch: {name}')
    if 'manifest.json' not in checks:
        raise DatasetError('The completion manifest is not checksummed')
    manifest = json.loads((root / 'manifest.json').read_text())
    if manifest.get('status') != 'complete' or manifest.get('kind') != 'plain-training-csv-collection/v1':
        raise DatasetError('A completed plain CSV release is required')
    seen = set()
    for item in manifest['files']:
        name = item['path']
        if name in seen or checks.get(name) != item['sha256']:
            raise DatasetError('Manifest/checksum inventory mismatch')
        seen.add(name)
        path = contained(root, name)
        if path.stat().st_size != item['bytes']:
            raise DatasetError(f'File size mismatch: {name}')
        with path.open(newline='') as stream:
            if next(csv.reader(stream)) != item['columns']:
                raise DatasetError(f'CSV schema mismatch: {name}')
        metadata = item['source_manifest']
        if checks.get(metadata) != item['source_manifest_sha256']:
            raise DatasetError('Packaged source manifest checksum mismatch')
    if not REQUIRED_CSVS.issubset(seen) or manifest.get('candidate_row_count', 0) < 1:
        raise DatasetError('Incomplete public CSV inventory')
    return manifest, checks['manifest.json']


def join_exact(base, sidecar, name):
    for frame in (base, sidecar):
        if frame.example_id.isna().any() or not frame.example_id.is_unique:
            raise DatasetError(f'Duplicate or missing example IDs: {name}')
    if set(base.example_id) != set(sidecar.example_id):
        raise DatasetError(f'Incomplete example ID coverage: {name}')
    left = base.set_index('example_id')
    right = sidecar.set_index('example_id').loc[left.index]
    shared = set(left.columns) & set(right.columns)
    for column in shared:
        a, b = left[column], right[column]
        if not ((a == b) | (a.isna() & b.isna())).all():
            raise DatasetError(f'Identity mismatch in {name}: {column}')
    return base.merge(sidecar.drop(columns=list(shared)), on='example_id', validate='one_to_one', how='left')


def load_dataset(root, *, weather=True, geometry=False, vegetation=False):
    root = Path(root)
    manifest, digest = verify_release(root)
    entries = {item['path']: item for item in manifest['files']}

    def read(name, columns):
        entry = entries[name]
        columns = list(dict.fromkeys(columns))
        if not set(columns).issubset(entry['columns']):
            raise DatasetError(f'Missing required columns: {name}')
        frame = pd.read_csv(root / name, usecols=columns)
        if len(frame) != entry['row_count'] or not frame.example_id.is_unique or frame.example_id.isna().any():
            raise DatasetError(f'Invalid row count or IDs: {name}')
        return frame

    base_columns = ['example_id', *IDENTITY, *DEFAULT_MODEL_FEATURE_COLUMNS,
                    'binary_training_eligible', 'cell_center_latitude', 'cell_center_longitude']
    base = read('candidate_examples.csv', base_columns)
    if len(base) != manifest['candidate_row_count'] or not base[TARGET].isin([0, 1]).all():
        raise DatasetError('Invalid candidates or binary target')
    if not base.binary_training_eligible.isin([True, False, 0, 1]).all():
        raise DatasetError('Invalid training eligibility flag')
    dates = pd.to_datetime(base.source_snapshot_time, utc=True, format='mixed')
    days = dates.dt.strftime('%Y-%m-%d')
    if not days.between(manifest['source_snapshot_start_date'], manifest['source_snapshot_end_date']).all():
        raise DatasetError('Candidate snapshot outside the declared date range')
    annotations = read('incident_assignments.csv', ['example_id', 'incident_group_id', 'incident_split', 'region_key'])
    annotations.incident_group_id = annotations.incident_group_id.fillna('')
    base = join_exact(base, annotations, 'incident assignments')
    if not base.incident_split.isin(COHORTS).all():
        raise DatasetError('Unknown incident split')
    assigned = base.loc[base.incident_split.ne('unassigned')]
    if assigned.incident_group_id.eq('').any() or (assigned.groupby('incident_group_id').incident_split.nunique() > 1).any():
        raise DatasetError('Missing or overlapping incident groups')
    incident = json.loads((root / entries['incident_assignments.csv']['source_manifest']).read_text())
    later = pd.Timestamp(incident['policy']['later_test_at'])
    for group_id, group in assigned.groupby('incident_group_id'):
        expected = incident['groups'].get(group_id)
        if not expected or set(group.incident_split) != {expected['split']}:
            raise DatasetError('Incident assignments disagree with their source manifest')
    if (dates[base.incident_split.isin(['train', 'calibration'])] >= later).any():
        raise DatasetError('Training/calibration reaches the later-time holdout')
    sidecars = []
    if geometry or weather:
        sidecars.append('directional_features.csv')
    if weather:
        sidecars.extend(['weather_features.csv', 'weather_history.csv'])
    if sidecars:
        for name in sidecars:
            # Keep numeric features and shared identity, not large source arrays.
            cols = [c for c in entries[name]['columns'] if c == 'example_id' or c in IDENTITY
                    or c.startswith('fire_') or (weather and
                        (c.startswith('wind_') or c in WEATHER_INPUTS or c in HISTORY_INPUTS))]
            base = join_exact(base, read(name, cols), name)
    if vegetation:
        from wildfire_data.model.features.schema import STATIC_VEGETATION_COLUMNS, COVER_DIAGNOSTIC_COLUMNS
        base = join_exact(base, read('vegetation_features.csv',
            ['example_id', *STATIC_VEGETATION_COLUMNS, *COVER_DIAGNOSTIC_COLUMNS,
             *[c for c in IDENTITY if c in entries['vegetation_features.csv']['columns']]]), 'vegetation')
    return base, {'manifest_sha256': digest, 'later_test_at': later.isoformat(),
                  'row_count': len(base), 'files': {n: e['sha256'] for n, e in entries.items()}}


WEATHER_INPUTS = ('weather_temperature_2m', 'weather_relative_humidity_2m',
                  'weather_precipitation', 'weather_wind_u_10m', 'weather_wind_v_10m')
HISTORY_INPUTS = ('weather_rain_6h_mm', 'weather_wind_u_mean_6h', 'weather_wind_v_mean_6h',
                 'weather_wind_speed_mean_6h', 'weather_wind_speed_max_6h', 'weather_wind_steadiness_6h')


def cohort(frame, split, *, later_test_at):
    mask = frame.incident_split.eq(split) & frame.firms_center_has_detection.eq(0)
    mask &= frame.binary_training_eligible.isin([True, 1])
    if split == 'later_time':
        mask &= pd.to_datetime(frame.source_snapshot_time, utc=True, format='mixed') >= pd.Timestamp(later_test_at)
    return frame.loc[mask]
