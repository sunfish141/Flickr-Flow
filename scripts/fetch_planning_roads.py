"""Build-time bounded road retrieval, independently resumable per pilot."""
import argparse
import math
from pathlib import Path

import duckdb
from pyproj import Transformer
from shapely import from_wkb
from shapely.geometry import mapping
from prepare_planning_packs import PILOTS, RELEASE, save, sha
from datetime import datetime, timezone


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--archive', type=Path, default=Path('data/preparation-archives'))
    args = parser.parse_args()
    to_grid = Transformer.from_crs('EPSG:4326', 'ESRI:102008', always_xy=True)
    to_geo = Transformer.from_crs('ESRI:102008', 'EPSG:4326', always_xy=True)
    db = duckdb.connect()
    db.execute("SET threads=2; SET memory_limit='2GB'; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
    url = f's3://overturemaps-us-west-2/release/{RELEASE}/theme=transportation/type=segment/*'
    for identity, label, lon, lat, _ in PILOTS:
        target = args.archive / f'{identity}-roads-{RELEASE}.json'
        if target.exists():
            print(f'Retaining existing {target}', flush=True)
            continue
        x, y = to_grid.transform(lon, lat)
        w, s = math.floor(x/100)*100-4500, math.floor(y/100)*100-4500
        bw, bs, be, bn = to_geo.transform_bounds(w-250, s-250, w+9250, s+9250, densify_pts=41)
        print(f'Reading {label} roads from pinned release {RELEASE}', flush=True)
        query = f"SELECT * FROM read_parquet('{url}', hive_partitioning=true) WHERE subtype='road' AND bbox.xmin <= ? AND bbox.xmax >= ? AND bbox.ymin <= ? AND bbox.ymax >= ?"
        cursor = db.execute(query, [be, bw, bn, bs])
        names = [d[0] for d in cursor.description]
        features = []
        for row in cursor.fetchall():
            props = dict(zip(names, row))
            geom = from_wkb(props.pop('geometry'))
            features.append({'type': 'Feature', 'geometry': mapping(geom), 'properties': props})
        save(target, {'type': 'FeatureCollection', 'features': features})
        save(target.with_suffix('.receipt.json'), {'url': url, 'release': RELEASE,
            'bounds_wgs84': [bw, bs, be, bn], 'predicate': 'bbox intersection; subtype=road',
            'sha256': sha(target), 'retrieved_at': datetime.now(timezone.utc).isoformat()})
        print(f'Saved {len(features)} raw segments', flush=True)
    db.close()


if __name__ == '__main__':
    main()
