"""Immutable, globally aligned landscape tiles for expanding scenarios."""
import hashlib
from collections import OrderedDict
from contextlib import ExitStack
import json
import math
from pathlib import Path

from rasterio.warp import transform_bounds
from shapely.geometry import box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from wildfire_data.core.grid import TRAINING_GRID_CRS
from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.features.fuel_barrier_features import FuelBarrierSampler
from wildfire_data.providers.landscape.collect import build_bundle
from wildfire_data.providers.landscape.road_archive import RoadArchive

TILE_METRES = 3000


def tile_key(x, y):
    return (math.floor(x/TILE_METRES), math.floor(y/TILE_METRES))


def tile_bounds(key):
    x, y = key
    return (x*TILE_METRES, y*TILE_METRES, (x+1)*TILE_METRES, (y+1)*TILE_METRES)


class LandscapeTiles:
    def __init__(self, source_config, data_root, release, *, road_archive=None, road_archive_sha256=None, raster_cache=None):
        self.source_config, self.data_root, self.release = Path(source_config), Path(data_root), release
        if not road_archive:
            raise ValueError('Configure a completed offline road archive; simulation-time road downloads are disabled')
        self.archive = RoadArchive(road_archive, expected_sha256=road_archive_sha256)
        if self.archive.manifest['release'] != release:
            raise ValueError('Road archive release does not match landscape configuration')
        self.identity = hashlib.sha256(f'aligned-landscape-offline/v1:{sha256_file(self.source_config)}:{self.archive.sha256}'.encode()).hexdigest()
        self.readers, self.reader_stack = {}, ExitStack()
        self.raster_cache = raster_cache
        self.samplers = OrderedDict()

    def close(self):
        self.reader_stack.close()
        self.readers.clear()
        self.samplers.clear()

    @staticmethod
    def signature(path, manifest):
        paths = [path, *(path.parent / manifest['artifacts'][name]['path'] for name in ('cover', 'roads'))]
        return tuple((s.st_size, s.st_mtime_ns, s.st_ctime_ns) for s in (p.stat() for p in paths))

    def path(self, key):
        x, y = key
        return self.data_root/'landscape'/'tiles'/self.identity/f'{x}_{y}'/'manifest.json'

    def load(self, key, expected=None):
        path = self.path(key)
        if key in self.samplers:
            signature, sampler = self.samplers[key]
            if signature == self.signature(path, sampler.manifest):
                if expected and sampler.sha256 != expected:
                    raise ValueError('Landscape manifest checksum mismatch')
                self.samplers.move_to_end(key)
                return sampler
            del self.samplers[key]
        if not path.exists():
            if expected:
                raise ValueError('Pinned landscape tile is missing; restore it or start a new scenario')
            projected = tile_bounds(key)
            geographic = transform_bounds(TRAINING_GRID_CRS, 'EPSG:4326', *projected, densify_pts=21)
            # Road centerlines outside the tile can have pavement inside it.
            halo = box(*projected).buffer(250).bounds
            roads_bounds = transform_bounds(TRAINING_GRID_CRS, 'EPSG:4326', *halo, densify_pts=21)
            roads_bounds = (roads_bounds[0]-.001, roads_bounds[1]-.001, roads_bounds[2]+.001, roads_bounds[3]+.001)
            raw = self.data_root/'raw/overture-landscape'/'tiles'/self.identity/f'{key[0]}_{key[1]}'
            road_manifest = self.archive.extract(roads_bounds, raw, self.data_root)
            build_bundle(source_config=self.source_config, roads_manifest=road_manifest,
                bounds=geographic, output=path.parent, data_root=self.data_root,
                projected_bounds=projected, road_halo_m=250,
                readers=self.readers, stack=self.reader_stack, raster_cache=self.raster_cache)
        sampler = FuelBarrierSampler(path, expected_sha256=expected)
        self.samplers[key] = (self.signature(path, sampler.manifest), sampler)
        while len(self.samplers) > 48:
            self.samplers.popitem(last=False)
        return sampler


class LandscapeMosaic(FuelBarrierSampler):
    """In-memory sampler over disjoint aligned tiles; no world-sized raster."""
    def __init__(self, samplers):
        first = samplers[0]
        self.domains = [s.bounds for s in samplers]
        self.bounds = unary_union(self.domains)
        self.sha256 = hashlib.sha256(json.dumps([s.sha256 for s in samplers]).encode()).hexdigest()
        self.to_geo, self.to_grid = first.to_geo, first.to_grid
        self.manifest = {'roads_coverage': 'complete-extract', 'distance_radius_m': 5000.}
        groups, roads = {}, {}
        for s in samplers:
            for geom, props in s.cover:
                groups.setdefault(props['fuel'], []).append(geom)
            for geom, props in s.roads:
                key = json.dumps(props, sort_keys=True)
                roads.setdefault(key, []).append(geom)
        self.cover = [(unary_union(geoms), {'fuel': fuel}) for fuel, geoms in sorted(groups.items())]
        # Halo lines are duplicates across tiles. Dissolve by source attributes.
        self.roads = [(unary_union(geoms), json.loads(key)) for key, geoms in sorted(roads.items())]
        self.cover_union = unary_union([g for g, _ in self.cover])
        self.cover_tree = STRtree([g for g, _ in self.cover])
        self.road_tree = STRtree([g for g, _ in self.roads])
        from functools import lru_cache
        self._sample = lru_cache(maxsize=8192)(self._sample)
