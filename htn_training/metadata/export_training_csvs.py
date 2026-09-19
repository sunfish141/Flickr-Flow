"""Export retained May–August 2026 training tables; run from repository root.

Usage: python export_training_csvs.py NEW_OUTPUT_DIRECTORY
Only the standard library is needed. Existing destinations are refused.
"""
import csv
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import sys
from collections import Counter

csv.field_size_limit(10_000_000)
ROOT = Path.cwd()
OUT = Path(sys.argv[1]).resolve()
OUT.mkdir(parents=True, exist_ok=False)
(OUT / 'metadata').mkdir()
BASE = Path('releases/wildfire-spread-firms-feds-boreal-no-weather-2026-05-11_to_2026-08-22')
IDENTITY = ['cell_id', 'source_snapshot_time', 'anchor_at', 'feature_cutoff_at',
            'target_end_at', 'target_newly_burned_12h', 'dataset_split']

def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def read(path):
    return json.loads(path.read_text())

def public_metadata(value):
    """Keep provenance paths relative to the repository, without account names."""
    if isinstance(value, dict):
        return {key: public_metadata(item) for key, item in value.items()}
    if isinstance(value, list):
        return [public_metadata(item) for item in value]
    if isinstance(value, str):
        return value.replace(ROOT.as_posix() + '/', '')
    return value

def copy_public_manifest(source, destination):
    original = source.read_text()
    public = public_metadata(json.loads(original))
    if public == json.loads(original):
        shutil.copyfile(source, destination)
    else:
        destination.write_text(json.dumps(public, indent=2) + '\n')

base_manifest = read(BASE / 'dataset_manifest.json')
base_sha = sha(BASE / 'dataset_manifest.json')
checks = {line.split()[1]: line.split()[0]
          for line in (BASE / 'SHA256SUMS').read_text().splitlines()}
assert base_sha == checks['dataset_manifest.json']
assert base_manifest['source_snapshot_start_date'] == '2026-05-11'
assert base_manifest['source_snapshot_end_date'] == '2026-08-22'
jobs = [
    ('candidate_examples.csv', BASE / 'candidate_examples.csv.gz',
     BASE / 'dataset_manifest.json', checks['candidate_examples.csv.gz'], 500364),
    ('unscored_positives.csv', BASE / 'unscored_positives.csv.gz',
     BASE / 'dataset_manifest.json', checks['unscored_positives.csv.gz'], 35265),
]
for output, directory, filename in [
    ('weather_features.csv', 'weather-training-20260916', 'weather_features.csv.gz'),
    ('weather_history.csv', 'weather-history-20260916', 'history.csv.gz'),
    ('directional_features.csv', 'directional-weather-20260916', 'directional_features.csv.gz'),
    ('vegetation_features.csv', 'vegetation-expanded-static-release', 'candidate_examples.csv.gz'),
    ('landscape_features.csv', 'landscape-incident-pilot-20260916', 'landscape_features.csv.gz'),
]:
    folder = Path('data/derived') / directory
    manifest = folder / 'dataset_manifest.json'
    data = read(manifest)
    assert data['status'] == 'complete'
    for key in ['base_release_manifest_sha256', 'base_manifest_sha256', 'source_release_manifest_sha256']:
        if key in data:
            assert data[key] == base_sha, (manifest, key)
    if 'weather_manifest_sha256' in data:
        assert data['weather_manifest_sha256'] == sha(Path('data/derived/weather-training-20260916/dataset_manifest.json'))
    digest = (data['candidate_examples_csv_sha256'] if output == 'vegetation_features.csv'
              else data['files'][filename])
    jobs.append((output, folder / filename, manifest, digest, 500364))
incident = Path('artifacts/incident-sequences-recovered-20260907-boreal/manifest.json')
assignment = read(incident)['assignment_artifact']
jobs.append(('incident_assignments.csv', Path(assignment['path']), incident,
             assignment['sha256'], assignment['row_count']))

identities = {}
inventory = []
for name, source, manifest, expected_sha, expected_count in jobs:
    print('Exporting', name, flush=True)
    assert sha(source) == expected_sha, f'Source checksum mismatch: {source}'
    metadata_name = 'metadata/' + name.removesuffix('.csv') + '_source_manifest.json'
    copy_public_manifest(manifest, OUT / metadata_name)
    dest = OUT / name
    projected = name == 'vegetation_features.csv'
    if not projected:
        with gzip.open(source, 'rb') as src, dest.open('wb') as dst:
            shutil.copyfileobj(src, dst, length=1024*1024)
    context = gzip.open(source, 'rt', newline='') if projected else dest.open(newline='')
    seen = set()
    dates = []
    time_ranges = {}
    splits = Counter()
    targets = Counter()
    availability = Counter()
    with context as f:
        reader = csv.DictReader(f)
        columns = reader.fieldnames
        if projected:
            columns = ['example_id', *IDENTITY] + [c for c in columns if c.startswith('vegetation_')]
            dst = dest.open('w', newline='')
            writer = csv.DictWriter(dst, fieldnames=columns, extrasaction='ignore')
            writer.writeheader()
        for row in reader:
            eid = row['example_id']
            assert eid not in seen, (name, 'duplicate example', eid)
            seen.add(eid)
            if name == 'candidate_examples.csv':
                identities[eid] = tuple(row[k] for k in IDENTITY)
            elif name != 'unscored_positives.csv':
                assert eid in identities, (name, 'unknown example', eid)
                for k, value in zip(IDENTITY, identities[eid]):
                    if k in row:
                        assert row[k] == value, (name, eid, k)
            else:
                assert eid not in identities, ('unscored overlaps candidates', eid)
            if 'source_snapshot_time' in row:
                date = row['source_snapshot_time'][:10]
                assert '2026-05-11' <= date <= '2026-08-22', (name, date)
            for key in ['source_snapshot_time', 'anchor_at', 'feature_cutoff_at', 'target_end_at']:
                if key in row:
                    value = row[key]
                    old = time_ranges.setdefault(key, [value, value])
                    old[0], old[1] = min(old[0], value), max(old[1], value)
            splits[row.get('incident_split', row.get('dataset_split', 'not_stored'))] += 1
            if 'target_newly_burned_12h' in row:
                targets[row['target_newly_burned_12h']] += 1
            for k in ['landscape_missing', 'landscape_road_missing', 'vegetation_land_cover_missing', 'vegetation_cover_missing']:
                if k in row:
                    availability[k + '=' + row[k]] += 1
            if projected:
                writer.writerow(row)
        if projected:
            dst.close()
    assert len(seen) == expected_count, (name, len(seen), expected_count)
    if name != 'unscored_positives.csv':
        assert seen == identities.keys(), (name, 'incomplete join')
    entry = dict(path=name, row_count=len(seen), columns=columns,
                 bytes=dest.stat().st_size, sha256=sha(dest),
                 source_path=str(source), source_sha256=expected_sha,
                 source_manifest=metadata_name,
                 source_manifest_sha256=sha(OUT / metadata_name),
                 original_source_manifest_sha256=sha(manifest),
                 transformation='identity and vegetation column projection' if projected else 'lossless gzip decompression',
                 time_ranges=time_ranges, split_counts=dict(splits),
                 target_counts=dict(targets), missing_flag_counts=dict(availability))
    inventory.append(entry)
    print(name, len(seen), 'rows;', entry['bytes'], 'bytes; validated', flush=True)

shutil.copyfile(BASE / 'schema.json', OUT / 'metadata/base_schema.json')
assert sha(OUT / 'metadata/base_schema.json') == checks['schema.json']
shutil.copyfile(Path(__file__), OUT / 'metadata/export_training_csvs.py')
result = dict(status='complete', kind='plain-training-csv-collection/v1',
              source_snapshot_start_date='2026-05-11', source_snapshot_end_date='2026-08-22',
              join_key='example_id', candidate_row_count=len(identities),
              total_csv_bytes=sum(e['bytes'] for e in inventory), files=inventory,
              validation='Source checksums, row counts, unique IDs, exact candidate ID sets, shared identity fields, date bounds, and disjoint unscored positives verified.')
(OUT / 'manifest.json').write_text(json.dumps(result, indent=2) + '\n')
print('COMPLETE', result['total_csv_bytes'], 'CSV bytes', flush=True)
