"""Build-time collection of complete Alberta/Colorado inputs; never run by the app.

Keeps original national ZIPs, pins Overture's release, streams raster blocks and
Parquet writes, and publishes a manifest only when every component is complete.
Completed outputs are verified on retry; changed inputs are not substituted.
"""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
import zipfile

import duckdb
import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from shapely import from_wkb
from shapely.geometry import mapping, shape
from shapely.ops import transform

from prepare_planning_packs import SOURCES, RELEASE, sha, save

REGIONS = {
    'alberta': {'label': 'Alberta', 'code': 'CA-AB', 'country': 'can',
                'bounds': [-120.2, 48.8, -109.8, 60.2],
                'example_ignition': {'latitude': 53.39, 'longitude': -117.64}},
    'colorado': {'label': 'Colorado', 'code': 'US-CO', 'country': 'usa',
                 'bounds': [-109.3, 36.8, -101.8, 41.3],
                 'example_ignition': {'latitude': 39.83, 'longitude': -105.54}},
}
CRS = 'ESRI:102008'
ROAD_COLUMNS = 'id, geometry, bbox, version, subtype, class, subclass, width_rules, road_surface, level_rules, road_flags, sources'
BASE = f's3://overturemaps-us-west-2/release/{RELEASE}'
MAX_PACK_BYTES = 4_000_000_000


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def retained(path):
    receipt = path.with_suffix(path.suffix + '.receipt.json')
    if path.exists() != receipt.exists():
        raise ValueError(f'Incomplete unacknowledged output retained for inspection: {path}')
    if not path.exists():
        return None
    info = json.loads(receipt.read_text(encoding='utf-8'))
    if info['sha256'] != sha(path):
        raise ValueError(f'Existing output changed; not overwritten: {path}')
    return info


def publish(partial, path, **metadata):
    if path.exists():
        raise ValueError(f'Refusing to overwrite {path}')
    if partial.stat().st_size > MAX_PACK_BYTES:
        raise ValueError('Regional artifact exceeds 4 GB preparation cap')
    checksum = sha(partial)
    partial.replace(path)
    info = {'path': path.name, 'sha256': checksum, 'bytes': path.stat().st_size,
            'retrieved_at': timestamp(), **metadata}
    save(path.with_suffix(path.suffix + '.receipt.json'), info)
    return info


def boundary(db, region, out):
    path = out / 'boundary.geojson'
    if info := retained(path):
        return info
    query = f"SELECT id, geometry, names.primary AS name, sources, is_territorial FROM read_parquet('{BASE}/theme=divisions/type=division_area/*') WHERE subtype='region' AND region=?"
    rows = db.execute(query, [region['code']]).fetchall()
    territorial = [row for row in rows if row[-1]]
    rows = territorial or rows
    if len(rows) != 1:
        raise ValueError(f'Expected one full territorial boundary for {region["code"]}; found {len(rows)}')
    identity, geometry, name, sources, _ = rows[0]
    geometry = from_wkb(geometry)
    if not geometry.is_valid or geometry.geom_type not in ('Polygon', 'MultiPolygon'):
        raise ValueError('Invalid region boundary')
    if name != region['label']:
        raise ValueError(f'Unexpected region name: {name}')
    part = out / 'boundary.geojson.partial'
    save(part, {'type': 'Feature', 'geometry': mapping(geometry),
                'properties': {'id': identity, 'name': name, 'sources': sources}})
    return publish(part, path, source=f'{BASE}/theme=divisions/type=division_area/*',
                   release=RELEASE, region=region['code'], product='Overture-divisions')


def cover(region, out, archive):
    path = out / 'land-cover.tif'
    if info := retained(path):
        return info
    filename, checksum, suffix, year = SOURCES[region['country']]
    source = archive / filename
    if not source.is_file() or sha(source) != checksum:
        raise ValueError(f'Pinned NALCMS source missing or changed: {source}')
    with zipfile.ZipFile(source) as z:
        members = [n for n in z.namelist() if n.endswith('/' + suffix)]
        if len(members) != 1:
            raise ValueError('Raster member not found uniquely')
        # Regular ZIP members have expensive random seeks. Retain a verified
        # preparation-only TIFF so blockwise reprojection stays bounded/fast.
        staging = archive / 'regional-raster-staging'
        staging.mkdir(exist_ok=True)
        staged = staging / suffix
        staged_info = retained(staged)
        if staged_info is None:
            staged_part = staged.with_suffix('.tif.partial')
            if staged_part.exists():
                raise ValueError(f'Interrupted staging retained: {staged_part}')
            print(f'Staging {suffix} outside the installed-app budget', flush=True)
            with z.open(members[0]) as src, staged_part.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024*1024)
            publish(staged_part, staged, source_sha256=checksum, member=members[0])
        elif staged_info.get('source_sha256') != checksum or staged_info.get('member') != members[0]:
            raise ValueError('Staged raster source identity changed')
    if len(members) != 1:
        raise ValueError('Raster member not found uniquely')
    w, s, e, n = transform_bounds('EPSG:4326', CRS, *region['bounds'], densify_pts=101)
    w, s, e, n = math.floor(w/30)*30, math.floor(s/30)*30, math.ceil(e/30)*30, math.ceil(n/30)*30
    width, height = round((e-w)/30), round((n-s)/30)
    affine = from_origin(w, n, 30, 30)
    part = out / 'land-cover.tif.partial'
    if part.exists():
        raise ValueError(f'Interrupted output retained; choose a new destination: {part}')
    with rasterio.Env(GDAL_CACHEMAX=128 * 1024**2, GDAL_NUM_THREADS='2'):
        with rasterio.open(staged) as src, \
                WarpedVRT(src, crs=CRS, transform=affine, width=width, height=height,
                          nodata=255, resampling=Resampling.nearest, warp_mem_limit=64) as vrt, \
                rasterio.open(part, 'w', driver='GTiff', width=width, height=height, count=1,
                              dtype='uint8', crs=CRS, transform=affine, nodata=255,
                              tiled=True, blockxsize=512, blockysize=512, compress='DEFLATE',
                              predictor=1, BIGTIFF='IF_SAFER') as dst:
            for index, (_, window) in enumerate(dst.block_windows(1)):
                dst.write(vrt.read(1, window=window), 1, window=window)
                if index % 1000 == 0:
                    print(f'{region["label"]}: raster block {index}', flush=True)
            dst.build_overviews([2, 4, 8, 16, 32, 64], Resampling.nearest)
    return publish(part, path, product='NALCMS', component_year=year, source_archive=filename,
                   source_sha256=checksum, member=members[0], resolution_m=30,
                   crs=CRS, bounds_projected=[w, s, e, n],
                   transformation='Nearest-neighbor reprojection; unknown codes and nodata retained. Not a new measurement.')


def roads(db, region, out):
    path = out / 'roads.parquet'
    if info := retained(path):
        if info.get('release') != RELEASE:
            raise ValueError('Road release differs')
        return info
    part = out / 'roads.parquet.partial'
    if part.exists():
        raise ValueError(f'Interrupted output retained; choose a new destination: {part}')
    w, s, e, n = region['bounds']
    url = f'{BASE}/theme=transportation/type=segment/*'
    query = f"SELECT {ROAD_COLUMNS} FROM read_parquet('{url}') WHERE subtype='road' AND bbox.xmin <= {e} AND bbox.xmax >= {w} AND bbox.ymin <= {n} AND bbox.ymax >= {s} ORDER BY bbox.xmin, bbox.ymin, id"
    destination = str(part.resolve()).replace("'", "''")
    print(f'Collecting all {region["label"]} roads from release {RELEASE}', flush=True)
    db.execute(f"COPY ({query}) TO '{destination}' (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 8192)")
    count = db.execute('SELECT count(*) FROM read_parquet(?)', [str(part)]).fetchone()[0]
    if count < 10000:
        raise ValueError('Suspiciously small full-region road extract; not publishing')
    return publish(part, path, product='Overture-roads', release=RELEASE, source=url,
                   bounds_wgs84=region['bounds'], rows=count, predicate='bbox intersection; subtype=road',
                   attribution='Overture Maps Foundation and upstream contributors; per-feature sources retained',
                   license='ODbL-1.0; consult retained per-feature sources')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=Path('data/preparation-archives'))
    parser.add_argument('--output', type=Path, default=Path('data/regional-inputs-v1'))
    parser.add_argument('--region', choices=[*REGIONS, 'all'], default='all')
    parser.add_argument('--stage', choices=['boundary', 'cover', 'roads', 'verify', 'all'], default='all')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(args.output).free < 10_000_000_000:
        raise ValueError('Need 10 GB free preparation headroom; no files are deleted automatically')
    db = duckdb.connect()
    db.execute("SET threads=2; SET memory_limit='1GB'; SET preserve_insertion_order=false; SET max_temp_directory_size='4GB';")
    scratch = str((args.output / 'query-scratch').resolve()).replace("'", "''")
    db.execute(f"SET temp_directory='{scratch}'")
    if args.stage not in ('cover', 'verify'):
        db.execute("LOAD httpfs; SET s3_region='us-west-2'; SET http_timeout=120000;")
    for identity, region in REGIONS.items():
        if args.region not in ('all', identity):
            continue
        out = args.output / identity
        out.mkdir(exist_ok=True)
        steps = {'boundary': lambda: boundary(db, region, out),
                 'cover': lambda: cover(region, out, args.archive), 'roads': lambda: roads(db, region, out)}
        for name, execute in steps.items():
            if args.stage in ('all', name):
                print(f'{region["label"]}: preparing {name}', flush=True)
                print(json.dumps(execute()), flush=True)
        assets = {name: retained(out / filename) for name, filename in
                  [('boundary', 'boundary.geojson'), ('cover', 'land-cover.tif'), ('roads', 'roads.parquet')]}
        if all(assets.values()):
            geometry = shape(json.loads((out / 'boundary.geojson').read_text())['geometry'])
            projected = transform(Transformer.from_crs('EPSG:4326', CRS, always_xy=True).transform, geometry)
            document = {'kind': 'offline-regional-inputs/v1', 'id': identity, 'label': region['label'],
                'status': 'complete', 'assets': assets, 'area_km2': projected.area / 1e6,
                'bounds': list(geometry.bounds), 'example_ignition': region['example_ignition'],
                'created_at': timestamp(), 'limitations': ['Historical land cover, not current fuel conditions.',
                    'Unknown road attributes remain unknown.', 'Research-only uncalibrated spread.']}
            manifest = out / 'manifest.json'
            if manifest.exists():
                previous = json.loads(manifest.read_text(encoding='utf-8'))
                document['created_at'] = previous['created_at']
                if previous != document:
                    raise ValueError('Published regional inputs changed; choose a new version directory')
            else:
                save(manifest, document)
    if all((args.output / identity / 'manifest.json').is_file() for identity in REGIONS):
        save(args.output / 'index.json', {'kind': 'offline-regional-catalog/v1', 'regions': [
            {'id': identity, 'sha256': sha(args.output / identity / 'manifest.json')} for identity in REGIONS]})
    db.close()


if __name__ == '__main__':
    main()
