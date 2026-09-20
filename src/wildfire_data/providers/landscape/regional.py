"""Prepared full-region evidence with bounded, local-only display and tile reads.

Immutable sources live in the installation. Derived 3 km fuel tiles live in the
OS user-data directory. Neither path collects data or opens a national ZIP.
"""
from collections import OrderedDict
from contextlib import ExitStack
import hashlib
import json
import math
from io import BytesIO
from pathlib import Path
from threading import RLock

import numpy as np
import pyarrow.dataset as ds
from pyproj import Transformer
import rasterio
from rasterio.features import shapes, geometry_mask, rasterize
from PIL import Image
from rasterio.transform import from_bounds, from_origin
from rasterio.warp import reproject, Resampling, transform_bounds
from rasterio.windows import from_bounds as window_from_bounds, Window
from affine import Affine
from shapely import from_wkb, segmentize
from shapely.geometry import box, mapping, shape, Point
from shapely.ops import transform, unary_union

from wildfire_data.core.grid import TRAINING_GRID_CRS as CRS
from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.features.fuel_barrier_features import FuelBarrierSampler, LANDSCAPE_VERSION, polygons
from wildfire_data.providers.landscape.collect import normalize_roads
from wildfire_data.providers.landscape.road_archive import bbox_filter
from wildfire_data.providers.landscape.tiles import tile_bounds
from wildfire_data.providers.vegetation.products import NALCMS_CROSSWALK


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


class RegionalTiles:
    regional = True
    def __init__(self, root, cache_root, admit, *, max_cached_tiles=32):
        self.root, self.cache_root = Path(root).resolve(), Path(cache_root).resolve()
        self.admit = admit
        self.storage_lock = getattr(getattr(admit, '__self__', None), 'lock', RLock())
        self.max_cached_tiles = max_cached_tiles
        self.samplers, self.entries = OrderedDict(), {}
        self.map_cache = OrderedDict()
        self.stack, self.lock = ExitStack(), RLock()
        self.to_grid = Transformer.from_crs('EPSG:4326', CRS, always_xy=True)
        self.to_geo = Transformer.from_crs(CRS, 'EPSG:4326', always_xy=True)
        self.to_map = Transformer.from_crs('EPSG:4326', 'EPSG:3857', always_xy=True)
        index_path = self.root / 'index.json'
        index = json.loads(index_path.read_text(encoding='utf-8'))
        if index.get('kind') != 'offline-regional-catalog/v1':
            raise ValueError('Unsupported full-region catalog')
        self.identity = hashlib.sha256(('regional-tiles/v1:' + sha256_file(index_path)).encode()).hexdigest()
        try:
            for record in index['regions']:
                identity = record['id']
                if identity not in ('alberta', 'colorado') or identity in self.entries:
                    raise ValueError('Invalid or duplicate installed region')
                directory = self.root / identity
                manifest = directory / 'manifest.json'
                if sha256_file(manifest) != record['sha256']:
                    raise ValueError(f'{identity}: regional manifest changed')
                doc = json.loads(manifest.read_text(encoding='utf-8'))
                if doc.get('kind') != 'offline-regional-inputs/v1' or doc.get('status') != 'complete' or doc.get('id') != identity:
                    raise ValueError('Incomplete regional evidence')
                paths = {}
                for name in ('boundary', 'cover', 'roads'):
                    asset = doc['assets'][name]
                    path = (directory / asset['path']).resolve()
                    if path.parent != directory or sha256_file(path) != asset['sha256']:
                        raise ValueError(f'{identity}: {name} checksum/path mismatch')
                    paths[name] = path
                boundary = json.loads(paths['boundary'].read_text(encoding='utf-8'))
                geographic = shape(boundary['geometry'])
                if geographic.is_empty or not geographic.is_valid or geographic.geom_type not in ('Polygon', 'MultiPolygon'):
                    raise ValueError('Invalid installed boundary')
                domain = transform(self.to_grid.transform, segmentize(geographic, .01))
                raster = self.stack.enter_context(rasterio.open(paths['cover']))
                if raster.crs != rasterio.crs.CRS.from_string(CRS) or not box(*raster.bounds).covers(domain):
                    raise ValueError('Regional raster does not cover the full declared boundary')
                self.entries[identity] = {'document': doc, 'digest': record['sha256'], 'geographic': geographic,
                    'domain': domain, 'map_domain': transform(self.to_map.transform, segmentize(geographic, .01)),
                    'boundary': boundary, 'raster': raster, 'roads': ds.dataset(paths['roads'], format='parquet')}
            if not self.entries:
                raise ValueError('No complete full-region inputs installed')
            self.domain = unary_union([e['domain'] for e in self.entries.values()])
        except Exception:
            self.stack.close()
            raise

    def regions(self):
        return [{'id': identity, 'label': e['document']['label'], 'tiled': True,
                 'bounds': list(e['geographic'].bounds), 'coverage': e['boundary'],
                 'area_km2': e['document']['area_km2'], 'example_ignition': e['document']['example_ignition'],
                 'road_count': e['document']['assets']['roads']['rows'], 'digest': e['digest'],
                 'sources': list(e['document']['assets'].values()),
                 'map_tiles': f'/api/regional/{identity}/tiles/{{z}}/{{x}}/{{y}}.png'}
                for identity, e in self.entries.items()]

    def region_for(self, coordinates):
        matches = [identity for identity, e in self.entries.items()
                   if all(e['geographic'].covers(Point(lon, lat)) for lon, lat in coordinates)]
        if len(matches) != 1:
            raise ValueError('Starting fires must be inside one installed province/state. Reset before changing regions.')
        return matches[0]

    def supports(self, key):
        return self.domain.intersection(box(*tile_bounds(key))).area > .01

    def road_features(self, entry, bounds, *, display=False):
        columns = ['geometry'] if display else None
        count = 0
        for batch in entry['roads'].to_batches(columns=columns, filter=bbox_filter(bounds), batch_size=2048,
                                               batch_readahead=1, fragment_readahead=1, use_threads=False):
            for row in batch.to_pylist():
                count += 1
                if count > 30000:
                    raise ValueError('Local road detail exceeds the bounded tile workload')
                geometry = from_wkb(row.pop('geometry'))
                yield {'type': 'Feature', 'geometry': mapping(geometry), 'properties': row}

    def load(self, key, expected=None):
        with self.lock:
            return self._load(key, expected)

    def _load(self, key, expected):
        if key in self.samplers:
            sampler = self.samplers[key]
            if expected and sampler.sha256 != expected:
                raise ValueError('Pinned regional tile differs')
            self.samplers.move_to_end(key)
            return sampler
        extent = box(*tile_bounds(key))
        domain = self.domain.intersection(extent)
        if domain.area <= .01:
            raise ValueError('Tile is outside installed province/state coverage')
        path = self.cache_root / self.identity / f'{key[0]}_{key[1]}' / 'manifest.json'
        if not path.exists():
            if expected:
                raise ValueError('Pinned regional tile is missing; start a new scenario')
            self.admit(24_000_000)
            w, s, e, n = extent.bounds
            affine = from_origin(w, n, 30, 30)
            values = np.full((100, 100), 255, dtype='uint8')
            sources, raw_roads = [], []
            bounds = transform_bounds(CRS, 'EPSG:4326', *extent.buffer(250).bounds, densify_pts=21)
            for entry in self.entries.values():
                if not entry['domain'].intersects(extent):
                    continue
                reproject(rasterio.band(entry['raster'], 1), values, dst_transform=affine, dst_crs=CRS,
                          dst_nodata=255, resampling=Resampling.nearest, num_threads=2)
                sources.extend(entry['document']['assets'].values())
                raw_roads.extend(self.road_features(entry, bounds))
            parts = {}
            for geometry, code in shapes(values, mask=np.isin(values, list(NALCMS_CROSSWALK)), transform=affine):
                geometry = unary_union(polygons(shape(geometry).intersection(domain)))
                if geometry.area > .01:
                    parts.setdefault(NALCMS_CROSSWALK[int(code)], []).append(geometry)
            cover = {'type': 'FeatureCollection', 'features': [
                {'type': 'Feature', 'geometry': mapping(unary_union(gs)), 'properties': {'fuel': fuel}}
                for fuel, gs in sorted(parts.items())]}
            roads = normalize_roads(raw_roads, extent.buffer(250), preserve_unknown_grade=True)
            assets = {}
            for name, data in [('cover', cover), ('roads', roads)]:
                content = encoded(data)
                if len(content) > 10_000_000:
                    raise ValueError('Prepared tile exceeds 10 MB component budget')
                target = path.parent / f'{name}.json'
                self._write(target, content)
                assets[name] = {'path': target.name, 'sha256': hashlib.sha256(content).hexdigest()}
            self._write(path, encoded({'kind': LANDSCAPE_VERSION, 'status': 'complete', 'crs': CRS,
                'bounds_projected': list(extent.bounds), 'bounds_wgs84': list(transform_bounds(CRS, 'EPSG:4326', *extent.bounds)),
                'domain': mapping(domain), 'resolution_m': 30, 'artifacts': assets, 'sources': sources,
                'roads_coverage': 'complete-extract', 'regional_identity': self.identity, 'distance_radius_m': 5000.}))
        sampler = FuelBarrierSampler(path, expected_sha256=expected)
        if sampler.manifest.get('regional_identity') != self.identity:
            raise ValueError('Regional tile source identity changed')
        sampler.bounds = shape(sampler.manifest['domain'])
        self.samplers[key] = sampler
        while len(self.samplers) > self.max_cached_tiles:
            self.samplers.popitem(last=False)
        return sampler

    def _write(self, path, content):
        with self.storage_lock:
            self.admit(len(content))
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(path.suffix + '.partial')
            temporary.write_bytes(content)
            temporary.replace(path)

    def map_tile(self, identity, z, x, y):
        if identity not in self.entries or not 0 <= z <= 16 or not 0 <= x < 2**z or not 0 <= y < 2**z:
            raise ValueError('Invalid regional map tile')
        key = (identity,z,x,y)
        with self.lock:
            if key not in self.map_cache:
                self.map_cache[key] = self._map_tile(identity,z,x,y)
                while len(self.map_cache) > 32:
                    self.map_cache.popitem(last=False)
            self.map_cache.move_to_end(key)
            return self.map_cache[key]

    def _map_tile(self, identity, z, x, y):
        extent = 20037508.342789244
        size = 2*extent/2**z
        bounds = (-extent+x*size, extent-(y+1)*size, -extent+(x+1)*size, extent-y*size)
        affine = from_bounds(*bounds, 256, 256)
        values = np.full((256, 256), 255, dtype='uint8')
        entry = self.entries[identity]
        mask = geometry_mask([mapping(entry['map_domain'])], (256, 256), affine, invert=True)
        rgba = np.zeros((4, 256, 256), dtype='uint8')
        if mask.any():
            with self.lock:
                raster = entry['raster']
                visible = entry['map_domain'].intersection(box(*bounds))
                source_bounds = transform_bounds('EPSG:3857', CRS, *visible.bounds, densify_pts=41)
                window = window_from_bounds(*source_bounds, raster.transform).intersection(Window(0,0,raster.width,raster.height))
                rows, cols = min(512,max(1,math.ceil(window.height))), min(512,max(1,math.ceil(window.width)))
                # Select stored overviews for map display; never read/reproject
                # an entire province's native-resolution raster into memory.
                sampled = raster.read(1, window=window, out_shape=(rows,cols), resampling=Resampling.nearest)
                sampled_affine = raster.window_transform(window) * Affine.scale(window.width/cols, window.height/rows)
                reproject(sampled, values, src_transform=sampled_affine, src_crs=CRS, src_nodata=255,
                          dst_transform=affine, dst_crs='EPSG:3857', dst_nodata=255,
                          resampling=Resampling.nearest, num_threads=2)
                palette = {'needleleaf': (81,116,92), 'broadleaf': (112,147,91), 'mixed_forest': (100,129,107),
                    'shrubland': (167,180,129), 'grassland': (195,205,151), 'wetland': (131,174,166),
                    'cropland': (216,207,150), 'water': (150,200,221), 'urban': (176,139,170),
                    'barren': (199,191,179), 'snow_ice': (225,234,237), 'other_vegetation': (154,175,136)}
                rgba[:3, mask] = np.array([176,139,170])[:, None]  # Unknown is not safe/nonfuel.
                rgba[3, mask] = 255
                for code, fuel in NALCMS_CROSSWALK.items():
                    selected = mask & (values == code)
                    rgba[:3, selected] = np.array(palette[fuel])[:, None]
                if z >= 12:
                    geo_bounds = transform_bounds('EPSG:3857', 'EPSG:4326', *bounds)
                    roads = [transform(self.to_map.transform, shape(f['geometry'])) for f in self.road_features(entry, geo_bounds, display=True)]
                    if roads:
                        road_mask = rasterize(roads, out_shape=(256,256), transform=affine, dtype='uint8').astype(bool) & mask
                        rgba[:3, road_mask] = np.array([81,93,102])[:, None]
        output = BytesIO()
        Image.fromarray(np.moveaxis(rgba, 0, -1)).save(output, format='PNG')
        return output.getvalue()

    def close(self):
        self.stack.close()
        self.samplers.clear()
        self.entries.clear()
        self.map_cache.clear()
