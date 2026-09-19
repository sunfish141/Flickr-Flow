"""Small, exact metre geometries for landscape behavior tests."""
import json
from pathlib import Path
from shapely.geometry import box, mapping
from wildfire_data.core.hashing import sha256_file
from wildfire_data.model.features.fuel_barrier_features import FuelBarrierSampler, LANDSCAPE_VERSION
from wildfire_data.model.local_spread import TravelPolicy, VEGETATED


def bundle(directory, *, bounds=(0, 0, 120, 120), cover=None, roads=()):
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    cover = [(box(*bounds), 'grassland')] if cover is None else cover
    payloads = {'cover': [{'type': 'Feature', 'geometry': mapping(g), 'properties': {'fuel': fuel}} for g, fuel in cover],
        'roads': [{'type': 'Feature', 'geometry': mapping(g), 'properties': {'width_m': 6., 'surface': 'paved', 'at_grade': True, **props}} for g, props in roads]}
    assets = {}
    for name, features in payloads.items():
        target = path / f'{name}.json'
        target.write_text(json.dumps({'type': 'FeatureCollection', 'features': features}))
        assets[name] = {'path': target.name, 'sha256': sha256_file(target)}
    m = {'kind': LANDSCAPE_VERSION, 'status': 'complete', 'crs': 'ESRI:102008',
         'bounds_projected': bounds, 'bounds_wgs84': [-97, 39, -95, 41], 'artifacts': assets,
         'roads_coverage': 'complete-extract', 'sources': [
             {'product': p, 'observation_end': '2020-01-01T00:00:00Z', 'available_at': '2021-01-01T00:00:00Z'}
             for p in ('NALCMS', 'Overture-roads')]}
    target = path / 'manifest.json'
    target.write_text(json.dumps(m))
    return FuelBarrierSampler(target)


def policy(**changes):
    return TravelPolicy(**{'rates_m_min': dict.fromkeys(VEGETATED, 1.), 'residence_minutes': 120.,
        'wind_coefficient': .1, 'wind_east_m_s': 0., 'wind_north_m_s': 0., **changes})
