"""Experimental local fuel-patch travel model, independent of coarse probabilities.

This is a reproducible scenario engine, not a calibrated fire forecast. Explicit
rates and residence times describe assumptions. Unknown/urban mixtures are
unsupported fuel, not evidence of zero fire risk. Roads split mesh elements.
"""

from dataclasses import dataclass, asdict
from functools import lru_cache
import hashlib
import heapq
import json
import math

from shapely.geometry import box, Point, LineString, mapping
from shapely.ops import transform, unary_union
from shapely.strtree import STRtree

from wildfire_data.core.grid import GridCell, cell_from_id
from wildfire_data.model.features.fuel_barrier_features import polygons, projected_wind

LOCAL_VERSION = 'local-fuel-patch-travel/v2'
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
    def __init__(self, sampler, policy, *, mesh_m=30, max_patches=60000):
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
            for x in range(math.floor(w/mesh_m), math.ceil(e/mesh_m)):
                for y in range(math.floor(s/mesh_m), math.ceil(n/mesh_m)):
                    tile = box(x*mesh_m, y*mesh_m, (x+1)*mesh_m, (y+1)*mesh_m).intersection(domain)
                    nearby = surface_tree.query(tile, predicate='intersects')
                    roads = unary_union([surfaces[i] for i in nearby])
                    for i in sampler.cover_tree.query(tile, predicate='intersects'):
                        cover, props = sampler.cover[i]
                        if props['fuel'] not in VEGETATED:
                            continue
                        for piece in polygons(cover.intersection(tile).difference(roads)):
                            if piece.area < .01:
                                continue
                            # Split at canonical boundaries as well, so aggregation
                            # cannot assign a patch's entire area to the wrong cell.
                            pw, ps, pe, pn = piece.bounds
                            for cx in range(math.floor(pw/1000), math.floor(pe/1000)+1):
                                for cy in range(math.floor(ps/1000), math.floor(pn/1000)+1):
                                    cell = GridCell(cx, cy)
                                    for part in polygons(piece.intersection(box(*cell.bounds_projected))):
                                        if part.area > .01:
                                            self.patches.append(Patch(part, props['fuel'], cell.cell_id))
        if len(self.patches) > max_patches:
            raise ValueError('Road-cut patches exceed local mesh budget')
        if not self.patches:
            raise ValueError('Landscape contains no supported vegetation patches')
        self.tree = STRtree([p.geometry for p in self.patches])
        self.centers = [p.geometry.representative_point() for p in self.patches]
        self.adjacency = [[] for _ in self.patches]
        # Each patch participates in several edges. Buffer it once, rather than
        # repeating the same GEOS operation for every candidate gate.
        from shapely import prepare
        traversable = [p.geometry.buffer(1e-7) for p in self.patches]
        prepare(traversable)
        for i, patch in enumerate(self.patches):
            for j in self.tree.query(patch.geometry, predicate='intersects'):
                j = int(j)
                if j <= i:
                    continue
                other = self.patches[j]
                boundary = patch.geometry.intersection(other.geometry)
                if boundary.length <= 1e-6 or boundary.area > 1e-5:
                    continue  # No diagonal corner leakage or overlapping source polygons.
                # A shared boundary gate gives an interior route around a cut.
                lines = list(boundary.geoms) if hasattr(boundary, 'geoms') else [boundary]
                gates = [g.interpolate(.5, normalized=True) for g in lines if g.length > 1e-6]
                routes = []
                for gate in gates:
                    a, b = LineString([self.centers[i], gate]), LineString([gate, self.centers[j]])
                    if traversable[i].covers(a) and traversable[j].covers(b):
                        routes.append((a.length, b.length))
                if routes:
                    first, second = min(routes, key=sum)
                    self.adjacency[i].append((j, first, second))
                    self.adjacency[j].append((i, second, first))
        lon, lat = sampler.to_geo.transform(*sampler.bounds.centroid.coords[0])
        self.wind = projected_wind(sampler.to_grid, (lat, lon), policy.wind_east_m_s, policy.wind_north_m_s)
        self.arrivals = lru_cache(maxsize=8)(self._arrivals)

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
