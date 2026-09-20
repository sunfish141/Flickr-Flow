from datetime import datetime, timezone
import asyncio
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import Transformer
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import box, mapping, LineString, shape
from shapely.ops import transform

from wildfire_data.core.hashing import sha256_file
from wildfire_data.providers.landscape.regional import RegionalTiles
from wildfire_data.web.landscape_spread import ExpandingScenarios
from wildfire_data.web.local_spread import LocalScenarios
from model.fuel_fixture import policy as travel_policy


@pytest.fixture
def regional_inputs(tmp_path):
    root = tmp_path / 'installed ü' / 'data'
    folder = root / 'alberta'
    folder.mkdir(parents=True)
    to_geo = Transformer.from_crs('ESRI:102008', 'EPSG:4326', always_xy=True)
    domain = box(0, 0, 4500, 3000)
    geographic = transform(to_geo.transform, domain)
    boundary = {'type': 'Feature', 'geometry': mapping(geographic), 'properties': {'name': 'Synthetic only'}}
    (folder / 'boundary.geojson').write_text(json.dumps(boundary), encoding='utf-8')
    values = np.full((200, 300), 10, dtype='uint8')
    values[10:20, 10:20] = 255
    with rasterio.open(folder / 'land-cover.tif', 'w', driver='GTiff', width=300, height=200,
                       count=1, dtype='uint8', crs='ESRI:102008', transform=from_origin(-1500,4500,30,30), nodata=255) as dst:
        dst.write(values, 1)
    road = transform(to_geo.transform, LineString([(1500,0), (1500,3000)]))
    w,s,e,n = road.bounds
    rows = [{'id': 'test-road', 'geometry': road.wkb, 'bbox': {'xmin': w, 'ymin': s, 'xmax': e, 'ymax': n},
             'class': 'track', 'width_rules': None, 'road_surface': None, 'level_rules': None, 'sources': None}]
    pq.write_table(pa.Table.from_pylist(rows), folder / 'roads.parquet')
    assets = {name: {'path': filename, 'sha256': sha256_file(folder / filename), 'product': name,
                     'rows': 1 if name == 'roads' else None}
              for name, filename in [('boundary','boundary.geojson'), ('cover','land-cover.tif'), ('roads','roads.parquet')]}
    document = {'kind': 'offline-regional-inputs/v1', 'status': 'complete', 'id': 'alberta', 'label': 'Synthetic Alberta',
                'assets': assets, 'area_km2': domain.area/1e6, 'example_ignition': dict(zip(('longitude','latitude'), to_geo.transform(3900,1500)))}
    (folder / 'manifest.json').write_text(json.dumps(document), encoding='utf-8')
    (root / 'index.json').write_text(json.dumps({'kind': 'offline-regional-catalog/v1', 'regions': [
        {'id': 'alberta', 'sha256': sha256_file(folder / 'manifest.json')}]}), encoding='utf-8')
    return root, to_geo


def test_region_reads_tiles_from_install_and_writes_only_user_cache(regional_inputs, tmp_path):
    root, to_geo = regional_inputs
    admission = []
    store = RegionalTiles(root, tmp_path / 'user cache', admission.append)
    try:
        assert store.region_for([to_geo.transform(3900,1500)]) == 'alberta'
        with pytest.raises(ValueError, match='installed'):
            store.region_for([to_geo.transform(5000,1500)])
        tile = store.load((1,0))
        assert tile.bounds.area == pytest.approx(4_500_000, rel=1e-4)
        assert all(tile.bounds.buffer(.001).covers(g) for g, _ in tile.cover)
        assert store.load((1,0), tile.sha256) is tile
        with pytest.raises(ValueError, match='differs'):
            store.load((1,0), '0'*64)
        assert admission and not list(root.rglob('cover.json'))
        assert not store.supports((2,0))
    finally:
        store.close()


def test_offline_map_png_and_bad_tile_coordinates(regional_inputs, tmp_path):
    root, _ = regional_inputs
    store = RegionalTiles(root, tmp_path / 'cache', lambda amount: None)
    try:
        # The synthetic domain is near -96,40. Its world tile must be valid RGBA.
        with MemoryFile(store.map_tile('alberta', 0, 0, 0)) as mem, mem.open() as png:
            assert (png.width, png.height, png.count) == (256,256,4)
        for identity,z,x,y in [('missing',0,0,0), ('alberta',17,0,0), ('alberta',0,-1,0), ('alberta',0,1,0)]:
            with pytest.raises(ValueError):
                store.map_tile(identity,z,x,y)
    finally:
        store.close()


def test_boundary_stops_expansion_and_inputs_reproduce(regional_inputs, tmp_path):
    root, to_geo = regional_inputs
    local = LocalScenarios(None)
    local.policy, local.mesh_m = travel_policy(), 100
    store = RegionalTiles(root, tmp_path / 'cache', lambda amount: None)
    engine = ExpandingScenarios({'max_tiles': 16, 'max_patches': 50000}, root/'config.json', local, store=store)
    origin = datetime(2026,8,1,tzinfo=timezone.utc)
    try:
        seed = [to_geo.transform(3900,1500)]
        first = engine.initialize(seed, origin)
        samplers = {(t['x'],t['y']): store.load((t['x'],t['y'])) for t in first['state']['tiles']}
        frame = engine.frame(seed, samplers, 1, origin)
        assert frame['boundary_reached'] and frame['finished']
        assert frame['region_id'] == 'alberta'
        assert frame == engine.frame(seed, samplers, 1, origin)
        for feature in frame['perimeters']['features']:
            assert store.entries['alberta']['geographic'].buffer(1e-7).covers(shape(feature['geometry']))
    finally:
        engine.close()


def test_quota_failure_retains_installation(regional_inputs, tmp_path):
    root, _ = regional_inputs
    def reject(amount):
        raise ValueError('Storage full')
    store = RegionalTiles(root, tmp_path / 'cache', reject)
    try:
        with pytest.raises(ValueError, match='Storage full'):
            store.load((0,0))
        assert not (tmp_path / 'cache').exists()
        assert (root / 'alberta/roads.parquet').exists()
    finally:
        store.close()


def test_changed_installed_asset_is_not_accepted(regional_inputs, tmp_path):
    root, _ = regional_inputs
    (root / 'alberta/boundary.geojson').write_text('{}')
    with pytest.raises(ValueError, match='checksum'):
        RegionalTiles(root, tmp_path/'cache', lambda amount: None)


def test_local_map_admission_does_not_starve_simulation_or_bypass_origin_checks():
    from wildfire_data.web.security import RequestBoundary, MAX_INFLIGHT_MAP_REQUESTS
    async def endpoint(scope, receive, send):
        await send({'type': 'http.response.start', 'status': 200, 'headers': []})
        await send({'type': 'http.response.body', 'body': b'ok'})
    boundary = RequestBoundary(endpoint)
    boundary.inflight_maps = MAX_INFLIGHT_MAP_REQUESTS
    async def query(path, origin=None):
        messages = []
        async def receive():
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        async def send(message):
            messages.append(message)
        headers = [(b'host', b'127.0.0.1')]
        if origin:
            headers.append((b'origin', origin))
        await boundary({'type': 'http', 'scheme': 'http', 'path': path, 'method': 'GET', 'headers': headers}, receive, send)
        return messages[0]['status']
    assert asyncio.run(query('/api/config')) == 200
    assert asyncio.run(query('/api/regional/alberta/tiles/1/0/0.png')) == 503
    assert asyncio.run(query('/api/regional/alberta/tiles/1/0/0.png', b'https://evil.example')) == 403
    assert boundary.inflight == 0 and boundary.inflight_maps == MAX_INFLIGHT_MAP_REQUESTS
