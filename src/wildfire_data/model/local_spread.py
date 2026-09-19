"""Experimental local fuel-patch travel model, independent of coarse probabilities.

This is a reproducible scenario engine, not a calibrated fire forecast. Explicit
rates and residence times describe assumptions. Unknown/urban mixtures are
unsupported fuel, not evidence of zero fire risk. Roads split mesh elements.
"""

from dataclasses import dataclass, asdict
from functools import cached_property, lru_cache
import hashlib
import heapq
import json
import math

import numpy as np
import shapely
from shapely.geometry import box, Point, LineString, mapping
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from wildfire_data.core.grid import GridCell, cell_from_id
from wildfire_data.model.features.fuel_barrier_features import polygons, projected_wind

LOCAL_VERSION = 'local-fuel-patch-travel/v3'
VEGETATED = ('needleleaf', 'broadleaf', 'mixed_forest', 'shrubland', 'grassland',
             'other_vegetation', 'wetland', 'cropland')


@dataclass(frozen=True)
class TravelPolicy:
    """All behavior parameters must be supplied; there are no fitted defaults."""
    rates_m_min: dict
    residence_minutes: float
    wind_coefficient: float
    wind_east_m_s: float
    wind_north_m_s: float
    unknown_road_width_m: float = 0.
    spotting_distance_per_wind_m_s: float = 0.
    max_spotting_distance_m: float = 0.

    def __post_init__(self):
        if set(self.rates_m_min) != set(VEGETATED):
            raise ValueError('Supply explicit spread rates for every vegetation group')
        if any(not math.isfinite(v) or not 0 <= v <= 100 for v in self.rates_m_min.values()):
            raise ValueError('Scenario rates must be finite and between 0 and 100 m/min')
        fields = asdict(self)
        fields.pop('rates_m_min')
        if any(not math.isfinite(v) for v in fields.values()):
            raise ValueError('Nonfinite scenario parameter')
        if not 1 <= self.residence_minutes <= 1440 or not 0 <= self.wind_coefficient <= .5:
            raise ValueError('Invalid residence time or wind response')
        if math.hypot(self.wind_east_m_s, self.wind_north_m_s) > 60:
            raise ValueError('Scenario wind exceeds 60 m/s')
        if not 0 <= self.unknown_road_width_m <= 50:
            raise ValueError('Inferred road width must be 0–50 metres')
        if not 0 <= self.spotting_distance_per_wind_m_s <= 100 or not 0 <= self.max_spotting_distance_m <= 300:
            raise ValueError('Spotting scenario exceeds local support')

    @property
    def identity(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class Patch:
    geometry: object
    fuel: str
    cell_id: str


class LocalSpreadModel:
    def __init__(self, sampler, policy, *, mesh_m=30, max_patches=60000, allow_empty=False):
        if mesh_m not in (30, 100):
            raise ValueError('Local mesh must be 30 or 100 metres')
        self.sampler, self.policy, self.mesh_m = sampler, policy, mesh_m
        self.identity = hashlib.sha256(f'{LOCAL_VERSION}:{sampler.sha256}:{policy.identity}:{mesh_m}'.encode()).hexdigest()
        surfaces = []
        for road, props in sampler.roads:
            if not props.get('at_grade'):
                continue
            # Unknown road surfaces are not silently classified as pavement.
            if props.get('surface') not in ('paved', 'unpaved'):
                continue
            width = props.get('width_m')
            if width is None:
                width = policy.unknown_road_width_m
            if width:
                surfaces.append(road.buffer(width/2, cap_style=2))
        self.road_surface = unary_union(surfaces)
        surface_tree = STRtree(surfaces)
        self.patches = []
        domains = getattr(sampler, "domains", [sampler.bounds])
        if sum(math.ceil((g.bounds[2]-g.bounds[0])/mesh_m+1)*math.ceil((g.bounds[3]-g.bounds[1])/mesh_m+1) for g in domains) > max_patches:
            raise ValueError('Local domain exceeds patch budget; use a smaller region or coarser mesh')
        for domain in domains:
            w, s, e, n = domain.bounds
            x, y = np.meshgrid(np.arange(math.floor(w/mesh_m), math.ceil(e/mesh_m)),
                               np.arange(math.floor(s/mesh_m), math.ceil(n/mesh_m)), indexing='ij')
            x, y = x.ravel()*mesh_m, y.ravel()*mesh_m
            tiles = shapely.intersection(shapely.box(x, y, x+mesh_m, y+mesh_m), domain)
            # Cut roads and land-cover boundaries in bulk, keeping exact GEOS
            # polygons. Only canonical 1 km boundary splits need a scalar loop.
            road_groups = {}
            for tile_id, road_id in surface_tree.query(tiles, predicate='intersects').T:
                road_groups.setdefault(int(tile_id), []).append(surfaces[road_id])
            road_masks = np.empty(len(tiles), dtype=object)
            road_masks.fill(shapely.GeometryCollection())
            for tile_id, group in road_groups.items():
                road_masks[tile_id] = unary_union(group)
            tile_ids, cover_ids = sampler.cover_tree.query(tiles, predicate='intersects')
            vegetated = np.array([p['fuel'] in VEGETATED for _, p in sampler.cover], dtype=bool)
            tile_ids, cover_ids = tile_ids[vegetated[cover_ids]], cover_ids[vegetated[cover_ids]]
            pieces = shapely.difference(shapely.intersection(sampler.cover_tree.geometries[cover_ids], tiles[tile_ids]), road_masks[tile_ids])
            parts, owners = shapely.get_parts(pieces, return_index=True)
            while np.any(shapely.get_type_id(parts) > 3):
                parts, indices = shapely.get_parts(parts, return_index=True)
                owners = owners[indices]
            keep = (shapely.get_type_id(parts) == 3) & (shapely.area(parts) > .01)
            parts, owners = parts[keep], owners[keep]
            bounds = np.floor(shapely.bounds(parts)/1000).astype(np.int64)
            for piece, owner, (cx0, cy0, cx1, cy1) in zip(parts, owners, bounds):
                fuel = sampler.cover[int(cover_ids[owner])][1]['fuel']
                if cx0 == cx1 and cy0 == cy1:
                    self.patches.append(Patch(piece, fuel, GridCell(int(cx0), int(cy0)).cell_id))
                    continue
                for cx in range(cx0, cx1+1):
                    for cy in range(cy0, cy1+1):
                        cell = GridCell(cx, cy)
                        for part in polygons(piece.intersection(box(*cell.bounds_projected))):
                            if part.area > .01:
                                self.patches.append(Patch(part, fuel, cell.cell_id))
        if len(self.patches) > max_patches:
            raise ValueError('Road-cut patches exceed local mesh budget')
        if not self.patches and not allow_empty:
            raise ValueError('Landscape contains no supported vegetation patches')
        self.tree = STRtree([p.geometry for p in self.patches])
        self.centers = [p.geometry.representative_point() for p in self.patches]
        self.adjacency = [[] for _ in self.patches]
        self._connect(self.tree.query(self.tree.geometries, predicate='intersects'))
        self._runtime()

    def _runtime(self):
        sampler, policy = self.sampler, self.policy
        lon, lat = sampler.to_geo.transform(*sampler.bounds.centroid.coords[0])
        self.wind = projected_wind(sampler.to_grid, (lat, lon), policy.wind_east_m_s, policy.wind_north_m_s)
        self.arrivals = lru_cache(maxsize=8)(self._arrivals)

    def _connect(self, pairs):
        """Vectorized shared gates; identical road/hole/corner rules to scalar construction."""
        left, right = pairs
        keep = right > left
        left, right = left[keep], right[keep]
        if not len(left):
            return
        geometry = self.tree.geometries
        boundaries = shapely.intersection(geometry[left], geometry[right])
        keep = (shapely.length(boundaries) > 1e-6) & (shapely.area(boundaries) <= 1e-5)
        left, right, boundaries = left[keep], right[keep], boundaries[keep]
        # Multi-part boundaries can offer more than one traversable gate.
        parts, owners = shapely.get_parts(boundaries, return_index=True)
        keep = shapely.length(parts) > 1e-6
        parts, owners = parts[keep], owners[keep]
        if not len(parts):
            return
        gates = shapely.line_interpolate_point(parts, .5, normalized=True)
        centers = shapely.get_coordinates(self.centers)
        gate_xy = shapely.get_coordinates(gates)
        first = shapely.linestrings(np.stack((centers[left[owners]], gate_xy), axis=1))
        second = shapely.linestrings(np.stack((gate_xy, centers[right[owners]]), axis=1))
        indices = np.unique(np.concatenate((left, right)))
        traversable = np.empty(len(geometry), dtype=object)
        traversable[indices] = shapely.buffer(geometry[indices], 1e-7, quad_segs=16)
        shapely.prepare(traversable[indices])
        keep = shapely.covers(traversable[left[owners]], first) & shapely.covers(traversable[right[owners]], second)
        routes = {}
        for owner, a, b in zip(owners[keep], shapely.length(first[keep]), shapely.length(second[keep])):
            candidate = (float(a), float(b))
            if owner not in routes or sum(candidate) < sum(routes[owner]):
                routes[owner] = candidate
        for owner, (a, b) in routes.items():
            i, j = int(left[owner]), int(right[owner])
            self.adjacency[i].append((j, a, b))
            self.adjacency[j].append((i, b, a))

    @classmethod
    def join(cls, sampler, policy, components, *, mesh_m=30, max_patches=250000):
        """Reuse immutable tile patches/edges and calculate only cross-tile gates."""
        if any(c.mesh_m != mesh_m or c.policy.identity != policy.identity for c in components):
            raise ValueError('Prepared tile policy or mesh changed')
        count = sum(len(c.patches) for c in components)
        if not count or count > max_patches:
            raise ValueError('Landscape contains no supported vegetation patches' if not count else 'Road-cut patches exceed local mesh budget')
        model = cls.__new__(cls)
        model.sampler, model.policy, model.mesh_m = sampler, policy, mesh_m
        model.identity = hashlib.sha256(f'{LOCAL_VERSION}:{sampler.sha256}:{policy.identity}:{mesh_m}'.encode()).hexdigest()
        model.road_surface = unary_union([c.road_surface for c in components])
        model.patches, model.centers, model.adjacency = [], [], []
        owners, boundary_ids = [], []
        for owner, component in enumerate(components):
            offset = len(model.patches)
            model.patches.extend(component.patches)
            model.centers.extend(component.centers)
            model.adjacency.extend([[(j+offset, a, b) for j, a, b in edges] for edges in component.adjacency])
            owners.extend([owner]*len(component.patches))
            boundary_ids.extend(offset + component.tree.query(component.sampler.bounds.boundary, predicate='intersects'))
        model.tree = STRtree([p.geometry for p in model.patches])
        if boundary_ids:
            ids = np.array(boundary_ids, dtype=np.intp)
            left, right = model.tree.query(model.tree.geometries[ids], predicate='intersects')
            left = ids[left]
            owners = np.array(owners)
            cross = owners[left] != owners[right]
            model._connect((left[cross], right[cross]))
        model._runtime()
        return model

    @cached_property
    def satellite_representatives(self):
        representatives, centers = {}, {}
        for i, patch in enumerate(self.patches):
            if patch.cell_id not in centers:
                w, s, e, n = cell_from_id(patch.cell_id).bounds_projected
                centers[patch.cell_id] = ((w+e)/2, (s+n)/2)
            x, y = centers[patch.cell_id]
            center = self.centers[i]
            distance = math.hypot(center.x-x, center.y-y)
            if patch.cell_id not in representatives or distance < representatives[patch.cell_id][0]:
                representatives[patch.cell_id] = (distance, i)
        return representatives

    @cached_property
    def roads_geojson(self):
        return {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': mapping(transform(self.sampler.to_geo.transform, g)),
             'properties': p} for g, p in self.sampler.roads]}

    def seed_ids(self, ignitions):
        ids = set()
        for longitude, latitude in ignitions:
            point = Point(*self.sampler.to_grid.transform(longitude, latitude))
            found = [int(i) for i in self.tree.query(point, predicate='intersects')]
            if len(found) != 1:
                raise ValueError('Place the ignition inside one supported vegetation patch, away from roads and unknown cover')
            ids.add(found[0])
        if not ids or len(ids) > 500:
            raise ValueError('One to 500 local ignitions required')
        return tuple(sorted(ids))

    def _projection(self, i, j):
        a, b = self.centers[i], self.centers[j]
        dx, dy = b.x-a.x, b.y-a.y
        return (self.wind[0]*dx+self.wind[1]*dy)/max(math.hypot(dx, dy), 1e-9)

    def _arrivals(self, seeds):
        if not seeds or any(type(i) is not int or not 0 <= i < len(self.patches) for i in seeds):
            raise ValueError('Invalid local ignition identifiers')
        times = {i: 0. for i in seeds}
        queue = [(0., i) for i in seeds]
        heapq.heapify(queue)
        while queue:
            time, i = heapq.heappop(queue)
            if time != times[i] or time > 96*60:
                continue
            edges = [(j, first, second, False) for j, first, second in self.adjacency[i]]
            if self.policy.max_spotting_distance_m and self.policy.spotting_distance_per_wind_m_s:
                # Optional worst-case road-crossing scenario, not an ignition
                # probability or a general long-range ember model.
                for j in self.tree.query(self.centers[i].buffer(self.policy.max_spotting_distance_m)):
                    j = int(j)
                    if j == i:
                        continue
                    line = LineString([self.centers[i], self.centers[j]])
                    downwind = self._projection(i, j)
                    if line.length > max(0., downwind)*self.policy.spotting_distance_per_wind_m_s:
                        continue
                    unsupported = line.difference(self.patches[i].geometry.union(self.patches[j].geometry))
                    if not unsupported.is_empty and self.road_surface.covers(unsupported):
                        edges.append((j, 0., line.length, True))
            for j, first, second, spot in edges:
                source_rate = self.policy.rates_m_min[self.patches[i].fuel]
                target_rate = self.policy.rates_m_min[self.patches[j].fuel]
                if min(source_rate, target_rate) == 0:
                    continue
                if spot:
                    delay = max(1., second/(60*max(self._projection(i, j), .01)))
                else:
                    response = math.exp(max(-3., min(3., self.policy.wind_coefficient*self._projection(i, j))))
                    departure = first/(source_rate*response)
                    if departure > self.policy.residence_minutes:
                        continue
                    delay = departure + second/(target_rate*response)
                arrival = time + delay
                if arrival <= 96*60 and arrival < times.get(j, math.inf):
                    times[j] = arrival
                    heapq.heappush(queue, (arrival, j))
        return times

    def frame(self, seeds, elapsed_minutes):
        if not math.isfinite(elapsed_minutes) or not 0 <= elapsed_minutes <= 96*60:
            raise ValueError('Local playback supports 0–96 hours')
        times = self.arrivals(tuple(sorted(set(seeds))))
        active, burned, cells = [], [], {}
        boundary_reached = False
        for i, arrival in times.items():
            if arrival > elapsed_minutes:
                continue
            patch = self.patches[i]
            remaining = max(0., 1-(elapsed_minutes-arrival)/self.policy.residence_minutes)
            (active if remaining > 0 else burned).append(patch.geometry)
            record = cells.setdefault(patch.cell_id, {'active_area_m2': 0., 'burned_area_m2': 0.})
            record['active_area_m2' if remaining > 0 else 'burned_area_m2'] += patch.geometry.area
            boundary_reached |= patch.geometry.distance(self.sampler.bounds.boundary) < 1e-6
        features = []
        for status, geometries in (('active', active), ('burned', burned)):
            if geometries:
                geometry = unary_union(geometries)
                features.append({'type': 'Feature', 'geometry': mapping(transform(self.sampler.to_geo.transform, geometry)),
                    'properties': {'status': status, 'area_m2': geometry.area, 'basis': 'scenario simulation'}})
        return {'perimeters': {'type': 'FeatureCollection', 'features': features},
            'cells': cells, 'active_patch_count': len(active), 'burned_patch_count': len(burned),
            'boundary_reached': boundary_reached, 'elapsed_minutes': elapsed_minutes,
            'future_arrivals': any(t > elapsed_minutes for t in times.values()),
            'assumptions': {'kind': 'uncalibrated scenario', 'policy': asdict(self.policy),
                'mesh_m': self.mesh_m, 'landscape_sha256': self.sampler.sha256,
                'unsupported': 'urban mixtures, structures, unknown land, unresolved roads',
                'weather_mode': 'constant scenario wind; not an issued forecast',
                'terrain_mode': 'flat travel model; coarse terrain is not downscaled'}}
