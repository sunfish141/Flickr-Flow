"""Prepare lightweight display geometry from the already bundled, pinned source.

No downloads and no changes to the model's higher-detail barrier geometry.
"""
import gzip
import hashlib
import json
from pathlib import Path

from shapely.geometry import mapping, shape

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT / 'src/wildfire_data/model/features/resources/north-america-water.geojson.gz'
    with gzip.open(source, 'rt', encoding='utf-8') as stream:
        original = json.load(stream)
    features = []
    for feature in original['features']:
        geometry = shape(feature['geometry']).simplify(.015, preserve_topology=True)
        if geometry.is_empty:
            continue
        features.append({'type': 'Feature', 'properties': {'kind': feature['properties']['kind']},
                         'geometry': mapping(geometry)})
    output = {'type': 'FeatureCollection', 'features': features,
        'provenance': {'source': 'Natural Earth 5.1.2 land and lakes', 'license': 'Public domain',
            'attribution_url': 'https://www.naturalearthdata.com/about/terms-of-use/',
            'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'bounds': [-179, 24, -50, 84], 'simplification_degrees': .015,
            'purpose': 'Generalized orientation map only, not simulation coverage or current fuel data'}}
    target = ROOT / 'src/wildfire_data/web/static/offline-overview.geojson'
    target.write_text(json.dumps(output, separators=(',', ':')), encoding='utf-8')
    print(f'Offline overview: {target.stat().st_size:,} bytes; {len(features)} land/lake features')


if __name__ == '__main__':
    main()
