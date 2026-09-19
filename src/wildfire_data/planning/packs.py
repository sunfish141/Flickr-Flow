"""Verified immutable packs and offline vector layers."""
import json
import math
from pathlib import Path
import re

from shapely.geometry import mapping, shape
from shapely.ops import transform
from shapely.errors import ShapelyError
from wildfire_data.model.features.fuel_barrier_features import FUEL_GROUPS
from wildfire_data.model.features.fuel_barrier_features import FuelBarrierSampler
from wildfire_data.model.local_spread import VEGETATED
from wildfire_data.planning.contracts import LIMITATIONS, canonical


def validate_snapshot(value, definition):
    from shapely.errors import ShapelyError
    try:
        return _validate_snapshot(value, definition)
    except (KeyError, TypeError, AttributeError, ShapelyError) as exc:
        raise ValueError('Malformed pack snapshot or geometry') from exc


def _validate_snapshot(value, definition):
    if set(value) != {'id', 'digest', 'label', 'bounds_projected', 'bounds_wgs84', 'crs',
                     'sources', 'limitations', 'coverage', 'layers'}:
        raise ValueError('Unexpected pack snapshot fields; filesystem references are not accepted')
    if value['id'] != definition.pack_id or value['digest'] != definition.pack_digest:
        raise ValueError('Pack snapshot identity mismatch')
    if value['crs'] != 'ESRI:102008' or len(value['bounds_projected']) != 4:
        raise ValueError('Unsupported pack coordinates')
    if not isinstance(value['label'], str) or not 1 <= len(value['label']) <= 120:
        raise ValueError('Invalid pack label')
    if not isinstance(value['sources'], list) or len(value['sources']) > 20:
        raise ValueError('Invalid pack provenance')
    if not all(isinstance(s, dict) and isinstance(s.get('product'), str) for s in value['sources']):
        raise ValueError('Each source needs a product name')
    expected_coverage = {'area_km2', 'unknown_area_km2', 'urban_and_unknown_area_km2', 'supported_vegetation_area_km2',
                        'road_piece_count', 'unknown_width_road_pieces', 'unknown_grade_road_pieces'}
    coverage = value['coverage']
    if set(coverage) != expected_coverage or any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in coverage.values()):
        raise ValueError('Invalid numeric coverage statistics')
    if not 0 < coverage['area_km2'] <= 81.000001:
        raise ValueError('V1 supports bounded packs up to 81 square kilometres')
    if set(value['layers']) != {'cover', 'roads', 'unknown', 'boundary'}:
        raise ValueError('All four local map layers are required')
    w, s, e, n = value['bounds_wgs84']
    if not -180 <= w < e <= 180 or not -85 <= s < n <= 85:
        raise ValueError('Invalid coverage bounds')
    for name, fc in value['layers'].items():
        if name not in ('cover', 'roads', 'unknown', 'boundary') or fc.get('type') != 'FeatureCollection':
            raise ValueError('Unexpected map layer')
        if len(fc['features']) > 50000:
            raise ValueError('Too many map features')
        for f in fc['features']:
            if f.get('type') != 'Feature':
                raise ValueError('Invalid map feature')
            g = shape(f['geometry'])
            if name in ('cover', 'unknown'):
                if f['properties'].get('fuel') not in (*FUEL_GROUPS, 'unknown') or g.geom_type not in ('Polygon', 'MultiPolygon'):
                    raise ValueError('Invalid cover class/geometry')
            if not g.is_valid or g.is_empty or g.geom_type not in ('Polygon', 'MultiPolygon', 'LineString', 'MultiLineString'):
                raise ValueError('Invalid map geometry')
            gw, gs, ge, gn = g.bounds
            if not w-.01 <= gw <= ge <= e+.01 or not s-.01 <= gs <= gn <= n+.01:
                raise ValueError('Map feature outside declared coverage')
    canonical(value)
    return value


class Packs:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.entries, self.errors = {}, []
        index = self.root / 'index.json'
        if not index.is_file():
            self.errors.append('No verified regional packs installed. Preparation is never automatic.')
            return
        try:
            content = json.loads(index.read_text(encoding='utf-8'))
            if content['schema_version'] != 1:
                raise ValueError('Unsupported pack index')
            for entry in content['packs']:
                try:
                    identity = entry['id']
                    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,79}', identity) or identity in self.entries:
                        raise ValueError('Invalid or duplicate pack ID')
                    path = self.root / identity / 'manifest.json'
                    if path.resolve().parent.parent != self.root:
                        raise ValueError('Pack path escapes installation')
                    sampler = FuelBarrierSampler(path, expected_sha256=entry['manifest_sha256'])
                    self.entries[identity] = (sampler, self._snapshot(identity, sampler))
                except (ValueError, OSError, KeyError, TypeError, AttributeError, ShapelyError) as exc:
                    self.errors.append(f'Pack rejected: {exc}')
        except (ValueError, OSError, KeyError, TypeError, AttributeError, ShapelyError) as exc:
            self.errors.append(f'Pack index rejected: {exc}')

    def _snapshot(self, identity, sampler):
        def fc(items):
            return {'type': 'FeatureCollection', 'features': [
                {'type': 'Feature', 'geometry': mapping(transform(sampler.to_geo.transform, g)), 'properties': p}
                for g, p in items if not g.is_empty]}
        m = sampler.manifest
        unknown = sampler.bounds.difference(sampler.cover_union)
        unsupported = sum(g.area for g, p in sampler.cover if p['fuel'] == 'urban') + unknown.area
        return {'id': identity, 'digest': sampler.sha256, 'label': m.get('label', identity),
            'bounds_projected': m['bounds_projected'], 'bounds_wgs84': m['bounds_wgs84'], 'crs': m['crs'],
            'sources': m['sources'], 'limitations': LIMITATIONS,
            'coverage': {'area_km2': sampler.bounds.area / 1e6, 'unknown_area_km2': unknown.area / 1e6,
                'urban_and_unknown_area_km2': unsupported / 1e6,
                'supported_vegetation_area_km2': sum(g.area for g, p in sampler.cover if p['fuel'] in VEGETATED) / 1e6,
                'road_piece_count': len(sampler.roads),
                'unknown_width_road_pieces': sum(p.get('width_m') is None for _, p in sampler.roads),
                'unknown_grade_road_pieces': sum(p.get('at_grade') is None for _, p in sampler.roads)},
            'layers': {'cover': fc(sampler.cover), 'roads': fc(sampler.roads),
                       'unknown': fc([(unknown, {'fuel': 'unknown'})]), 'boundary': fc([(sampler.bounds, {})])}}

    def list(self):
        return [{k: v for k, v in snapshot.items() if k != 'layers'} for _, snapshot in self.entries.values()]

    def require(self, definition, *, verify=False):
        entry = self.entries.get(definition.pack_id)
        if entry is None or entry[0].sha256 != definition.pack_digest:
            raise ValueError('Required pack digest is not installed; saved results are view/export only')
        if verify:
            FuelBarrierSampler(entry[0].path, expected_sha256=definition.pack_digest)
        return entry
