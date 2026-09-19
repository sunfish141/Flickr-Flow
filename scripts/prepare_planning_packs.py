"""Build-only collector. Never imported by the offline app.

Pinned national source downloads are retained outside the installed application.
Run with PYTHONPATH=src in the separate preparation environment. No OSM tiles.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import zipfile

import requests

RELEASE = '2026-08-19.0'
BASE = 'https://www.cec.org/files/atlas_layers/1_terrestrial_ecosystems/1_01_0_land_cover_2020_30m/'
SOURCES = {
    # Audited 2026-09-19 official v2 snapshots. Not recovered legacy archives.
    'can': ('can_land_cover_2020v2_30m_tif.zip', '464471954603d57bc5c1498538a360cda81c838aa6f5aec8d10ef432d531416a', 'CAN_NALCMS_landcover_2020v2_30m.tif', 2020),
    'usa': ('usa_land_cover_2020v2_30m_tif.zip', 'f8308b40a2b0ced8fda2759f5b4066c4e8d80bf23dcf90b99da16e8557fa1508', 'USA_NALCMS_landcover_2020v2_30m.tif', 2021),
}
LEGACY_HASHES = {'can': 'd735d0f587b20a9bedbb2446669b29966f2c927c43925fe9c9d9802287bd04a4',
                 'usa': '2eefdae72cf3728f60c1efa14bdd984abc2d671664340aeaea51a85a589275e4'}
PILOTS = [('hinton-alberta', 'Hinton, Alberta', -117.64, 53.39, 'can'),
          ('black-hawk-colorado', 'Black Hawk, Colorado', -105.54, 39.83, 'usa')]


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + '.partial')
    part.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    part.replace(path)


def download(country, archive):
    filename, checksum, member, year = SOURCES[country]
    target = archive / filename
    if target.exists():
        if sha(target) != checksum:
            raise ValueError(f'{target}: existing source checksum mismatch; retained without overwrite')
        return target
    archive.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix('.zip.partial')
    if partial.exists():
        # A complete, explicitly pinned retained download can be promoted.
        # Partial/mismatched bytes are never overwritten on an automatic retry.
        if sha(partial) != checksum:
            raise ValueError(f'{partial}: incomplete/mismatched retained download. Choose a new archive directory to retry.')
        size = partial.stat().st_size
    else:
        with requests.get(BASE + filename, stream=True, timeout=(30, 180), headers={'Accept-Encoding': 'identity'}) as response:
            response.raise_for_status()
            with partial.open('wb') as handle:
                size = 0
                for chunk in response.iter_content(1024 * 1024):
                    size += len(chunk)
                    if size > 4_000_000_000:
                        raise ValueError('Source exceeds explicit 4 GB preparation limit')
                    handle.write(chunk)
    actual = sha(partial)
    if actual != checksum:
        raise ValueError(f'{filename}: pinned checksum mismatch ({actual}); no silent release substitution')
    partial.replace(target)
    save(target.with_suffix('.receipt.json'), {'url': BASE + filename, 'sha256': actual,
         'bytes': size, 'retrieved_at': datetime.fromtimestamp(target.stat().st_mtime, timezone.utc).isoformat(),
         'verified_at': datetime.now(timezone.utc).isoformat(), 'snapshot': 'official-v2-audited-2026-09-19',
         'legacy_archive_sha256_not_recovered': LEGACY_HASHES[country],
         'change_notice': 'Official ZIP differs from legacy reference. V2 metadata audited; no equivalence with missing original archive is claimed.'})
    print(f'Verified {filename}: {size:,} bytes', flush=True)
    return target


def build(pilot, archive, destination):
    import duckdb
    import numpy as np
    from pyproj import Transformer
    import rasterio
    from rasterio.features import shapes
    from rasterio.transform import from_origin
    from rasterio.warp import reproject, Resampling, transform_bounds
    from shapely import from_wkb
    from shapely.geometry import box, mapping, shape
    from shapely.ops import unary_union
    from wildfire_data.providers.landscape.collect import normalize_roads
    from wildfire_data.providers.vegetation.products import NALCMS_CROSSWALK

    identity, label, lon, lat, country = pilot
    out = destination / identity
    if (out / 'manifest.json').exists():
        raise ValueError(f'{out} already exists; verify it or choose a new destination. No implicit overwrites.')
    raw = download(country, archive)
    to_grid = Transformer.from_crs('EPSG:4326', 'ESRI:102008', always_xy=True)
    x, y = to_grid.transform(lon, lat)
    w, s = math.floor(x/100)*100-4500, math.floor(y/100)*100-4500
    bounds = [w, s, w+9000, s+9000]
    geo_bounds = list(transform_bounds('ESRI:102008', 'EPSG:4326', *bounds, densify_pts=41))
    # Buffer the extraction rectangle for road widths at the edge. We clip
    # prepared centerlines to the domain, and expose their unknown attributes.
    bw, bs, be, bn = transform_bounds('ESRI:102008', 'EPSG:4326', w-250, s-250, w+9250, s+9250, densify_pts=41)
    roads_file = archive / f'{identity}-roads-{RELEASE}.json'
    if not roads_file.exists():
        db = duckdb.connect()
        db.execute("SET threads=2; SET memory_limit='2GB'; INSTALL httpfs; LOAD httpfs; SET s3_region='us-west-2';")
        url = f's3://overturemaps-us-west-2/release/{RELEASE}/theme=transportation/type=segment/*'
        query = f"SELECT * FROM read_parquet('{url}', hive_partitioning=true) WHERE subtype='road' AND bbox.xmin <= ? AND bbox.xmax >= ? AND bbox.ymin <= ? AND bbox.ymax >= ?"
        cursor = db.execute(query, [be, bw, bn, bs])
        names = [d[0] for d in cursor.description]
        features = []
        for row in cursor.fetchall():
            props = dict(zip(names, row))
            geom = from_wkb(props.pop('geometry'))
            features.append({'type': 'Feature', 'geometry': mapping(geom), 'properties': props})
        db.close()
        save(roads_file, {'type': 'FeatureCollection', 'features': features})
        save(roads_file.with_suffix('.receipt.json'), {'url': url, 'release': RELEASE,
            'bounds_wgs84': [bw, bs, be, bn], 'predicate': 'bbox intersection; subtype=road',
            'sha256': sha(roads_file), 'retrieved_at': datetime.now(timezone.utc).isoformat()})
    roads_receipt = json.loads(roads_file.with_suffix('.receipt.json').read_text())
    if roads_receipt['sha256'] != sha(roads_file) or roads_receipt['release'] != RELEASE:
        raise ValueError('Cached road extract checksum/release mismatch')
    with zipfile.ZipFile(raw) as z:
        members = [n for n in z.namelist() if n.endswith('/' + SOURCES[country][2])]
        if len(members) != 1:
            raise ValueError('Pinned raster member not found uniquely')
    affine = from_origin(w, s+9000, 100, 100)
    values = np.full((90, 90), 255, dtype='uint8')
    with rasterio.open(f'zip://{raw.resolve()}!{members[0]}') as raster:
        reproject(rasterio.band(raster, 1), values, dst_transform=affine, dst_crs='ESRI:102008',
                  dst_nodata=255, resampling=Resampling.nearest, num_threads=2)
    parts = {}
    for geom, code in shapes(values, mask=np.isin(values, list(NALCMS_CROSSWALK)), transform=affine):
        parts.setdefault(NALCMS_CROSSWALK[int(code)], []).append(shape(geom))
    cover = {'type': 'FeatureCollection', 'features': [{'type': 'Feature', 'geometry': mapping(unary_union(gs)),
        'properties': {'fuel': fuel, 'basis': 'NALCMS nearest-neighbor reprojection at 100 m; not new measurement'}}
        for fuel, gs in sorted(parts.items())]}
    raw_features = json.loads(roads_file.read_text())['features']
    roads = normalize_roads(raw_features, box(*bounds), preserve_unknown_grade=True)
    artifacts = {}
    for name, content in [('cover', cover), ('roads', roads)]:
        asset = out / f'{name}.json'
        save(asset, content)
        artifacts[name] = {'path': asset.name, 'sha256': sha(asset)}
    nalcms_receipt = json.loads(raw.with_suffix('.receipt.json').read_text())
    sources = [
        {'product': 'NALCMS', 'version': '2020 v2', 'component_year': SOURCES[country][3],
         'observation_start': '2019-01-01T00:00:00Z' if country == 'can' else '2021-01-01T00:00:00Z',
         'observation_end': '2022-01-01T00:00:00Z', 'source': nalcms_receipt,
         'attribution': 'CEC (2024), NALCMS Ed. 2.0; Canada Centre for Remote Sensing / NRCan, USGS, CONABIO, CONAFOR, INEGI',
         'license': 'CC-BY-4.0', 'license_url': 'https://creativecommons.org/licenses/by/4.0/',
         'metadata_basis': 'Included v2 TIFF XML identifies Canada 2020 (some 2019/2021 imagery), CONUS 2021; not the 2019 CONUS version-1 map',
         'transformation': 'Nearest-neighbor classification, ESRI:102008, aligned 100 m; unknown pixels preserved as gaps'},
        {'product': 'Overture-roads', 'version': RELEASE, 'source': roads_receipt,
         'observation_end': None, 'source_date_basis': 'release date, not survey date',
         'attribution': 'Overture Maps Foundation; OpenStreetMap contributors and named feature sources',
         'license': 'ODbL-1.0', 'license_url': 'https://docs.overturemaps.org/attribution/',
         'transformation': 'Bounding-box extract, projected and clipped; scoped attributes; missing grade/width/surface remain unknown'},
    ]
    save(out / 'manifest.json', {'kind': 'fuel-barrier-landscape/v1', 'status': 'complete', 'crs': 'ESRI:102008',
         'id': identity, 'label': label, 'center_wgs84': [lon, lat], 'bounds_projected': bounds,
         'bounds_wgs84': geo_bounds, 'resolution_m': 100, 'artifacts': artifacts, 'sources': sources,
         'roads_coverage': 'complete-extract', 'created_at': datetime.now(timezone.utc).isoformat()})
    print(f'Built {label}: {len(roads["features"])} road pieces, {len(cover["features"])} cover classes', flush=True)
    return {'id': identity, 'manifest_sha256': sha(out / 'manifest.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=Path('data/preparation-archives'))
    parser.add_argument('--output', type=Path, default=Path('data/planning-packs-v1'))
    parser.add_argument('--downloads-only', action='store_true')
    args = parser.parse_args()
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda c: download(c, args.archive), SOURCES))
    if not args.downloads_only:
        entries = [build(p, args.archive, args.output) for p in PILOTS]
        save(args.output / 'index.json', {'schema_version': 1, 'packs': entries})


if __name__ == '__main__':
    main()
