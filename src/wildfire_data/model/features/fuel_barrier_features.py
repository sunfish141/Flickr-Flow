"""Shared offline landscape sampling. Unknown land is never declared nonfuel.

Coordinates and distances inside a bundle use the canonical equal-area metre CRS.
Road widths are source attributes or explicit scenario estimates, never facts
inferred silently from a road's class. Urban cover remains a distinct mixture.
"""

from collections import OrderedDict
import json
import math
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import box, shape, Point, LineString
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from wildfire_data.core.grid import TRAINING_GRID_CRS, cell_from_id
from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.features.vegetation_features import utc
from wildfire_data.providers.vegetation.products import NALCMS_CROSSWALK

LANDSCAPE_VERSION = 'fuel-barrier-landscape/v1'
FUEL_GROUPS = tuple(dict.fromkeys(NALCMS_CROSSWALK.values()))
LANDSCAPE_COLUMNS = tuple('landscape_' + name for name in (
    *('fraction_' + name for name in FUEL_GROUPS), 'valid_fraction',
    'road_length_m', 'road_known_width_fraction', 'known_road_surface_fraction',
    'road_distance_m', 'road_distance_censored', 'urban_distance_m',
    'urban_distance_censored', 'missing', 'road_missing',
))
EDGE_COLUMNS = tuple('landscape_' + n for n in (
    'crossing_count', 'maximum_road_gap_m', 'road_crossing_missing_width',
    'crosswind_m_s', 'toward_target_wind_m_s',
))


def empty_features():
    return {k: 1. if k.endswith('missing') else None for k in LANDSCAPE_COLUMNS}


def polygons(geometry):
    if geometry.is_empty:
        return []
    if geometry.geom_type == 'Polygon':
        return [geometry]
    return [p for g in geometry.geoms for p in polygons(g)] if hasattr(geometry, 'geoms') else []


class FuelBarrierSampler:
    """Immutable, checksummed local bundle shared by joins and simulations."""

    def __init__(self, manifest_path, *, expected_sha256=None):
        self.path = Path(manifest_path).resolve()
        self.sha256 = sha256_file(self.path)
        if expected_sha256 and self.sha256 != expected_sha256:
            raise ValueError('Landscape manifest checksum mismatch')
        self.manifest = json.loads(self.path.read_text(encoding='utf-8'))
        m = self.manifest
        if m.get('kind') != LANDSCAPE_VERSION or m.get('status') != 'complete' or m.get('crs') != TRAINING_GRID_CRS:
            raise ValueError('Requires a complete supported landscape bundle')
        self.bounds = box(*m['bounds_projected'])
        self.cover, self.roads = [], []
        for name in ('cover', 'roads'):
            asset = m['artifacts'][name]
            path = (self.path.parent / asset['path']).resolve()
            if path.parent != self.path.parent or sha256_file(path) != asset['sha256']:
                raise ValueError('Landscape asset checksum/path mismatch')
            for f in json.loads(path.read_text(encoding='utf-8'))['features']:
                g = shape(f['geometry'])
                if not g.is_valid or g.is_empty:
                    raise ValueError('Invalid landscape geometry')
                if name == 'cover' and (g.geom_type not in ('Polygon', 'MultiPolygon') or f['properties'].get('fuel') not in FUEL_GROUPS):
                    raise ValueError('Invalid landscape fuel class/geometry')
                getattr(self, name).append((g, f['properties']))
        self.cover_union = unary_union([g for g, _ in self.cover])
        if sum(g.area for g, _ in self.cover) - self.cover_union.area > .01:
            raise ValueError('Overlapping land-cover evidence')
        self.cover_tree = STRtree([g for g, _ in self.cover])
        self.road_tree = STRtree([g for g, _ in self.roads])
        self.urban_tree = STRtree([g for g, p in self.cover if p['fuel'] == 'urban'])
        self.to_grid = Transformer.from_crs('EPSG:4326', TRAINING_GRID_CRS, always_xy=True)
        self.to_geo = Transformer.from_crs(TRAINING_GRID_CRS, 'EPSG:4326', always_xy=True)
        self._sample_cache = OrderedDict()

    def eligible(self, cutoff_at, mode='as_of', *, roads=None):
        if mode not in ('as_of', 'retrospective'):
            raise ValueError('Unknown landscape time policy')
        cutoff = utc(cutoff_at)
        # Retrospective permits publication latency, never later observations.
        for source in self.manifest['sources']:
            if roads is not None and (source['product'] == 'Overture-roads') != roads:
                continue
            if source.get('observation_end') is None or utc(source['observation_end']) > cutoff:
                return False
            if mode == 'as_of' and (not source.get('available_at') or utc(source['available_at']) > cutoff):
                return False
        return True

    def sample_cell(self, cell_id, *, cutoff_at=None, mode='as_of'):
        # An instance-owned bound-method lru_cache retains a reference cycle
        # and can keep a large evicted mosaic alive until cyclic collection.
        if cell_id not in self._sample_cache:
            self._sample_cache[cell_id] = self._sample(cell_id)
            if len(self._sample_cache) > 8192:
                self._sample_cache.popitem(last=False)
        self._sample_cache.move_to_end(cell_id)
        values = dict(self._sample_cache[cell_id])
        if cutoff_at is not None:
            missing = empty_features()
            for roads in (False, True):
                if not self.eligible(cutoff_at, mode, roads=roads):
                    for key in values:
                        if (key.startswith('landscape_road_') or key == 'landscape_known_road_surface_fraction') == roads:
                            values[key] = missing[key]
        return values

    def _sample(self, cell_id):
        cell = box(*cell_from_id(cell_id).bounds_projected)
        result = empty_features()
        if not self.bounds.covers(cell):
            return result
        areas = dict.fromkeys(FUEL_GROUPS, 0.)
        for i in self.cover_tree.query(cell, predicate='intersects'):
            g, p = self.cover[i]
            areas[p['fuel']] += g.intersection(cell).area
        valid = sum(areas.values()) / cell.area
        if valid > 1 + 1e-6:
            raise ValueError('Overlapping land-cover evidence')
        result.update({'landscape_fraction_' + k: v / cell.area for k, v in areas.items()})
        result.update(landscape_valid_fraction=min(1., valid), landscape_missing=float(valid == 0))
        length = known = 0.
        surfaces = []
        for i in self.road_tree.query(cell.buffer(100), predicate='intersects'):
            g, p = self.roads[i]
            n = g.intersection(cell).length
            length += n
            if p.get('width_m') is not None:
                known += n
                if p.get('at_grade') and p.get('surface') == 'paved':
                    surfaces.append(g.buffer(p['width_m'] / 2, cap_style=2).intersection(cell))
        roads_available = self.manifest.get('roads_coverage') == 'complete-extract'
        if roads_available:
            result.update(landscape_road_length_m=length,
                landscape_road_known_width_fraction=known / length if length else None,
                landscape_known_road_surface_fraction=unary_union(surfaces).area / cell.area,
                landscape_road_missing=0.)
        center = cell.centroid
        radius = self.manifest.get('distance_radius_m', 5000.)
        for kind in ('road', 'urban'):
            tree = self.road_tree if kind == 'road' else self.urban_tree
            nearest = tree.nearest(center)
            distance = tree.geometries[nearest].distance(center) if nearest is not None else math.inf
            # An extract edge limits the distance we can establish. A mapped
            # object closer than that edge is still a valid nearest object.
            supported = min(radius, self.bounds.boundary.distance(center))
            if kind == 'road' and not roads_available:
                continue
            if kind == 'urban' and valid < .999:
                continue
            if kind == 'urban':
                unknown = center.buffer(supported).difference(self.cover_union)
                if not unknown.is_empty:
                    supported = min(supported, center.distance(unknown))
            result[f'landscape_{kind}_distance_m'] = min(distance, supported)
            result[f'landscape_{kind}_distance_censored'] = float(distance > supported)
        return result

    def sample_edge(self, source_id, target_id, *, wind_east_m_s=None, wind_north_m_s=None):
        source, target = (cell_from_id(c) for c in (source_id, target_id))
        line = LineString([source.center_projected, target.center_projected])
        if not self.bounds.covers(line) or self.manifest.get('roads_coverage') != 'complete-extract':
            return dict.fromkeys(EDGE_COLUMNS)
        gaps, missing, normals = [], False, []
        count = 0
        for i in self.road_tree.query(line, predicate='intersects'):
            road, p = self.roads[i]
            if not p.get('at_grade'):
                continue
            count += 1
            width = p.get('width_m')
            if width is None:
                missing = True
            else:
                gaps.append(line.intersection(road.buffer(width / 2, cap_style=2)).length)
            coords = list(road.coords)
            dx, dy = coords[-1][0]-coords[0][0], coords[-1][1]-coords[0][1]
            if math.hypot(dx, dy):
                normals.append((-dy/math.hypot(dx, dy), dx/math.hypot(dx, dy)))
        wx, wy = wind_east_m_s, wind_north_m_s
        if wx is not None and wy is not None:
            wx, wy = projected_wind(self.to_grid, source.center_wgs84, wx, wy)
        dx = target.center_projected[0] - source.center_projected[0]
        dy = target.center_projected[1] - source.center_projected[1]
        norm = math.hypot(dx, dy)
        return dict(zip(EDGE_COLUMNS, (float(count), max(gaps, default=0.) if not missing else None,
            float(missing), max((abs(wx*nx+wy*ny) for nx, ny in normals), default=0.) if wx is not None and wy is not None else None,
            (wx*dx+wy*dy)/norm if norm and wx is not None and wy is not None else None), strict=True))


def projected_wind(to_grid, latlon, east, north):
    """Rotate geographic east/north into the local projected grid basis."""
    lat, lon = latlon
    x, y = to_grid.transform(lon, lat)
    ex, ey = to_grid.transform(lon + .0001, lat)
    nx, ny = to_grid.transform(lon, lat + .0001)
    e, n = math.hypot(ex-x, ey-y), math.hypot(nx-x, ny-y)
    return east*(ex-x)/e+north*(nx-x)/n, east*(ey-y)/e+north*(ny-y)/n
