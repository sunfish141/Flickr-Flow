"""Collect a pinned Overture extract and build a bounded NALCMS landscape.

Runtime preparation only; collection commands are excluded from this application.
Native source bytes stay in the existing archive. The local 30 m classification
is a nearest-neighbor reprojection, not a new high-resolution measurement.
"""

import argparse
from contextlib import ExitStack
from functools import lru_cache
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shutil

import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.features import shapes
from rasterio.transform import from_origin
from rasterio.warp import reproject, Resampling, transform_bounds
from shapely import from_wkb
from shapely.geometry import box, mapping, shape
from shapely.ops import transform, substring, unary_union

from wildfire_data.core.grid import TRAINING_GRID_CRS
from wildfire_data.core.hashing import sha256_file
from wildfire_data.core.model_artifacts import write_model_json
from wildfire_data.model.features.fuel_barrier_features import LANDSCAPE_VERSION
from wildfire_data.providers.storage.storage_budget import load_storage_budget, require_admission
from wildfire_data.providers.vegetation.aggregation import raster_path
from wildfire_data.providers.vegetation.products import NALCMS_CROSSWALK


@lru_cache(maxsize=32)
def verify_source(path, size, modified_ns, checksum):
    if sha256_file(Path(path)) != checksum:
        raise ValueError('NALCMS source checksum mismatch')


def checked_bounds(bounds):
    if len(bounds) != 4 or not all(math.isfinite(x) for x in bounds):
        raise ValueError('Four finite bounds required')
    w, s, e, n = bounds
    if not -179 <= w < e <= -50 or not 24 <= s < n <= 84:
        raise ValueError('Bounds must be a nonempty North American rectangle')
    return tuple(bounds)


def save_json(path, document, data_root, *, category='static_cell_features'):
    path = Path(path)
    encoded = json.dumps(document, allow_nan=False, separators=(',', ':')).encode()
    require_admission(load_storage_budget(), data_root, category=category, requested_bytes=len(encoded))
    if shutil.disk_usage(data_root).free < len(encoded) + 100_000_000:
        raise ValueError('Insufficient free disk for atomic landscape write')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_bytes(encoded)
    temporary.replace(path)




def rule_value(rules, position, default=None):
    if not isinstance(rules, list):
        return default
    values = []
    for rule in rules:
        interval = rule.get('between', [0., 1.])
        if interval is None:
            interval = [0., 1.]
        if interval[0] <= position <= interval[1]:
            values.append(rule.get('value'))
    # Overlapping contradictory rules are unknown, not an arbitrary first value.
    return values[0] if values and all(v == values[0] for v in values) else default


def normalize_roads(features, projected_bounds, *, preserve_unknown_grade=False):
    project = Transformer.from_crs('EPSG:4326', TRAINING_GRID_CRS, always_xy=True)
    result, seen = [], set()
    for feature in features:
        p, geometry = feature['properties'], shape(feature['geometry'])
        identity = p.get('id')
        if not identity:
            raise ValueError('Road source requires a stable ID')
        if identity in seen:
            continue
        seen.add(identity)
        if geometry.geom_type != 'LineString' or not geometry.is_valid:
            raise ValueError('Expected valid Overture road LineString')
        road = transform(project.transform, geometry)
        breaks = {0., 1.}
        for name in ('width_rules', 'road_surface', 'level_rules', 'road_flags'):
            for rule in p.get(name) or []:
                if isinstance(rule, dict):
                    breaks.update(rule.get('between') or (0., 1.))
        breaks = sorted(breaks)
        if not all(0 <= b <= 1 for b in breaks):
            raise ValueError('Invalid road linear referencing')
        for start, end in zip(breaks, breaks[1:]):
            mid = (start + end) / 2
            width = rule_value(p.get('width_rules'), mid)
            width = float(width) if isinstance(width, (float, int)) and 0 < width < 200 else None
            flags = sorted({flag for rule in p.get('road_flags') or []
                if (rule.get('between') or [0., 1.])[0] <= mid <= (rule.get('between') or [0., 1.])[1]
                for flag in rule.get('values', rule.get('value')) or []})
            level = rule_value(p.get('level_rules'), mid, None if preserve_unknown_grade else 0)
            elevated = any(f in flags for f in ('is_bridge', 'is_tunnel')) or level is not None and level != 0
            at_grade = False if elevated else None if level is None else True
            piece = substring(road, start, end, normalized=True).intersection(projected_bounds)
            parts = list(piece.geoms) if piece.geom_type == 'MultiLineString' else [piece]
            for j, part in enumerate(parts):
                if part.is_empty or part.geom_type != 'LineString' or part.length == 0:
                    continue
                result.append({'type': 'Feature', 'geometry': mapping(part), 'properties': {
                    'id': f'{identity}:{start}:{j}', 'source_id': identity, 'class': p.get('class'),
                    'width_m': width, 'width_basis': 'provider' if width is not None else 'unknown',
                    'surface': rule_value(p.get('road_surface'), mid), 'at_grade': at_grade,
                    'sources': p.get('sources'), 'flags': flags}})
    return {'type': 'FeatureCollection', 'features': result}


def collect_cover(config_path, bounds_projected, resolution, *, readers=None, stack=None, raster_cache=None):
    """Nearest reprojected NALCMS classes, with deterministic first-valid seams."""
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text())
    w, s, e, n = bounds_projected
    width, height = math.ceil((e-w)/resolution), math.ceil((n-s)/resolution)
    if width * height > 2_000_000:
        raise ValueError('Landscape raster exceeds two million pixels; split the region')
    affine = from_origin(w, n, resolution, resolution)
    values = np.zeros((height, width), dtype='uint8')
    sources, checked = [], set()
    for source in config['sources']:
        if source['product'] != 'NALCMS':
            continue
        sources.append({k: v for k, v in source.items() if k != 'assets'})
        for original in sorted(source['assets'], key=lambda a: (a['sha256'], a.get('member', ''))):
            asset = {**original, 'path': str((config_path.parent / original['path']).resolve())}
            if asset['path'] not in checked:
                stat = Path(asset['path']).stat()
                verify_source(asset['path'], stat.st_size, stat.st_mtime_ns, asset['sha256'])
                checked.add(asset['path'])
            with ExitStack() as local:
                key = (asset['path'], asset['sha256'], asset.get('member'))
                if readers is None:
                    raster = local.enter_context(rasterio.open(raster_path(asset, cache_manifest=raster_cache)))
                else:
                    if key not in readers:
                        readers[key] = stack.enter_context(rasterio.open(raster_path(asset, cache_manifest=raster_cache)))
                    raster = readers[key]
                rb = transform_bounds(raster.crs, TRAINING_GRID_CRS, *raster.bounds)
                if not box(*rb).intersects(box(*bounds_projected)):
                    continue
                tile = np.full_like(values, 255)
                reproject(rasterio.band(raster, 1), tile, dst_transform=affine,
                    dst_crs=TRAINING_GRID_CRS, dst_nodata=255, resampling=Resampling.nearest,
                    num_threads=2)
                valid = np.isin(tile, list(NALCMS_CROSSWALK)) & (values == 0)
                values[valid] = tile[valid]
    parts = {}
    for geom, code in shapes(values, mask=values != 0, transform=affine):
        fuel = NALCMS_CROSSWALK[int(code)]
        clipped = shape(geom).intersection(box(*bounds_projected))
        parts.setdefault(fuel, []).append(clipped)
    features = [{'type': 'Feature', 'geometry': mapping(unary_union(geometries)),
                 'properties': {'fuel': fuel, 'basis': 'NALCMS-nearest-reprojection'}}
                for fuel, geometries in sorted(parts.items())]
    return {'type': 'FeatureCollection', 'features': features}, sources


def build_bundle(*, source_config, roads_manifest, bounds, output, data_root='data', resolution=30, readers=None, stack=None,
                 projected_bounds=None, road_halo_m=0, raster_cache=None):
    bounds = checked_bounds(bounds)
    if resolution not in (30, 100):
        raise ValueError('Supported landscape resolutions are 30 and 100 metres')
    output = Path(output).resolve()
    root = Path(data_root).resolve()
    if not output.is_relative_to(root / 'landscape'):
        raise ValueError('Store bundles under data-root/landscape')
    road_path = Path(roads_manifest).resolve()
    road_manifest = json.loads(road_path.read_text())
    identity = {'source_config_sha256': sha256_file(Path(source_config)),
        'roads_manifest_sha256': sha256_file(road_path), 'bounds': list(bounds), 'resolution_m': resolution,
        'transform_version': 'nearest-nalcms-preserve-invalid/v2'}
    if road_manifest.get('archive_sha256'):
        identity.update(road_archive_sha256=road_manifest['archive_sha256'],
                        road_normalization_version='scoped-road-flag-sets/v2')
    if projected_bounds is not None:
        if len(projected_bounds) != 4 or not all(math.isfinite(v) for v in projected_bounds) or box(*projected_bounds).area <= 0:
            raise ValueError('Invalid projected tile bounds')
        if not 0 <= road_halo_m <= 250:
            raise ValueError('Invalid road halo')
        identity.update(projected_bounds=list(projected_bounds), road_halo_m=road_halo_m)
    manifest_path = output / 'manifest.json'
    if manifest_path.exists():
        from wildfire_data.model.features.fuel_barrier_features import FuelBarrierSampler
        sampler = FuelBarrierSampler(manifest_path)
        if sampler.manifest['build'] != identity:
            raise ValueError('Existing landscape has a different build identity')
        return manifest_path
    if road_manifest.get('status') != 'complete' or not box(*road_manifest['bounds']).covers(box(*bounds)):
        raise ValueError('Road extract does not completely cover the requested region')
    raw = road_path.parent / road_manifest['path']
    if sha256_file(raw) != road_manifest['sha256']:
        raise ValueError('Road source checksum mismatch')
    projected = projected_bounds if projected_bounds is not None else transform_bounds('EPSG:4326', TRAINING_GRID_CRS, *bounds, densify_pts=21)
    # Keep the projected rectangle inside the geographic road extract by
    # collecting roads over its inverse-projected bounding box in the CLI.
    requested = transform(Transformer.from_crs('EPSG:4326', TRAINING_GRID_CRS, always_xy=True).transform,
                          __import__('shapely').segmentize(box(*road_manifest['bounds']), .001))
    road_domain = box(*box(*projected).buffer(road_halo_m).bounds)
    if not requested.covers(road_domain):
        raise ValueError('Road extract needs a projection halo around the landscape bounds')
    cover, sources = collect_cover(source_config, projected, resolution, readers=readers, stack=stack, raster_cache=raster_cache)
    with raw.open() as stream:
        roads = normalize_roads((json.loads(line) for line in stream), road_domain)
    save_json(output / 'cover.json', cover, root)
    save_json(output / 'roads.json', roads, root)
    artifacts = {k: {'path': f'{k}.json', 'sha256': sha256_file(output / f'{k}.json')} for k in ('cover', 'roads')}
    sources.append({'product': 'Overture-roads', **{k: road_manifest.get(k) for k in
        ('release', 'available_at', 'observation_end', 'observation_basis', 'retrieved_at', 'license', 'source', 'historical_geometry')}})
    doc = {'kind': LANDSCAPE_VERSION, 'status': 'complete', 'crs': TRAINING_GRID_CRS,
        'bounds_projected': projected, 'bounds_wgs84': bounds, 'build': identity,
        'resolution_m': resolution, 'sources': sources, 'artifacts': artifacts,
        'roads_coverage': 'complete-extract', 'distance_radius_m': 5000.,
        'cover_method': 'nearest-neighbor reprojection; mixed urban remains unknown fuel',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'retained_bytes': sum((output / a['path']).stat().st_size for a in artifacts.values()),
        'road_count': len(roads['features']), 'road_width_known_count': sum(f['properties']['width_m'] is not None for f in roads['features'])}
    save_json(manifest_path, doc, root)
    return manifest_path






