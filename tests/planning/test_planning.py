"""Offline contract, durability and adversarial-input regression tests."""
import copy
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import box
from model.fuel_fixture import bundle
from wildfire_data.planning.app import create_app
from wildfire_data.planning.contracts import Definition, LIMITATIONS, Policy, wind_components, engine_identity
from wildfire_data.planning.packs import Packs
from wildfire_data.planning.store import Conflict, StorageFull, Store


@pytest.fixture
def pack_root(tmp_path):
    from pyproj import Transformer
    from wildfire_data.core.hashing import sha256_file
    root = tmp_path / 'packs'
    sampler = bundle(root / 'test-pack', bounds=(0, 0, 1000, 1000),
                     cover=[(box(0, 0, 800, 1000), 'grassland'), (box(800, 0, 900, 1000), 'urban')])
    m = sampler.manifest
    points = [sampler.to_geo.transform(x, y) for x, y in [(0, 0), (0, 1000), (1000, 0), (1000, 1000)]]
    m['bounds_wgs84'] = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
    m['label'] = 'Synthetic test fixture — never shipped'
    sampler.path.write_text(json.dumps(m))
    (root / 'index.json').write_text(json.dumps({'schema_version': 1, 'packs': [{'id': 'test-pack', 'manifest_sha256': sha256_file(sampler.path)}]}))
    return root


@pytest.fixture
def client(tmp_path, pack_root):
    with TestClient(create_app(data_root=tmp_path / 'user data ü', pack_root=pack_root), base_url='http://127.0.0.1') as client:
        yield client


def definition(client):
    config = client.get('/api/config').json()
    pack = config['packs'][0]
    service = client.app.state.service
    sampler = service.packs.entries[pack['id']][0]
    lon, lat = sampler.to_geo.transform(150, 150)
    return {'pack_id': pack['id'], 'pack_digest': pack['digest'], 'engine_version': config['engine_version'],
            'origin_time': '2026-08-01T12:00:00Z', 'ignitions': [{'longitude': lon, 'latitude': lat}],
            'horizon_hours': 2, 'wind': {'speed_m_s': 0, 'from_degrees': 0}}


def create(client, **changes):
    body = {'request_id': str(uuid4()), 'name': 'Baseline', 'definition': definition(client), **changes}
    response = client.post('/api/scenarios', json=body)
    assert response.status_code == 200, response.text
    return response.json(), body


def mutation(scenario):
    return {'request_id': str(uuid4()), 'expected_revision': scenario['revision']}


def finish(client, scenario):
    response = client.post(f'/api/scenarios/{scenario["id"]}/run', json=mutation(scenario))
    assert response.status_code == 200, response.text
    deadline = time.monotonic()+30
    while time.monotonic() < deadline:
        result = client.get(f'/api/scenarios/{scenario["id"]}').json()
        if result['status'] != 'running':
            assert result['status'] == 'complete', result
            return result
        time.sleep(.05)
    pytest.fail('Worker did not finish within 30 seconds')


def test_offline_config_without_ml_or_secrets(client):
    config = client.get('/api/config').json()
    assert config['offline'] and not config['outbound_providers_enabled']
    assert config['modes'] == ['local-planning'] and not config['model_ready']
    assert 'RESEARCH ONLY' in config['limitations'][0]
    assert client.get('/api/firms').status_code == 404
    assert 'tile.openstreetmap' not in client.get('/api/config').headers['content-security-policy']


@pytest.mark.parametrize('direction,expected', [(0, (0, -5)), (90, (-5, 0)), (180, (0, 5)), (270, (5, 0))])
def test_wind_from_convention(direction, expected):
    assert wind_components(5, direction) == pytest.approx(expected, abs=1e-12)
    assert wind_components(0, direction) == (0, 0)


def test_create_idempotence_and_conflict(client):
    scenario, body = create(client)
    assert client.post('/api/scenarios', json=body).json()['id'] == scenario['id']
    body['name'] = 'Altered request'
    assert client.post('/api/scenarios', json=body).status_code == 409
    assert len(client.get('/api/scenarios').json()['scenarios']) == 1


def test_stale_tab_update_is_rejected(client):
    scenario, _ = create(client)
    update = {**mutation(scenario), 'name': 'New name', 'definition': scenario['definition']}
    assert client.put(f'/api/scenarios/{scenario["id"]}', json=update).status_code == 200
    update['request_id'] = str(uuid4())
    update['name'] = 'Stale name'
    assert client.put(f'/api/scenarios/{scenario["id"]}', json=update).status_code == 409
    assert client.get(f'/api/scenarios/{scenario["id"]}').json()['name'] == 'New name'


def test_worker_deterministic_clone_and_immutable_results(client):
    first, _ = create(client)
    first = finish(client, first)
    frames = client.get(f'/api/scenarios/{first["id"]}/frames').json()['frames']
    assert [f['hour'] for f in frames] == [0, 1, 2]
    assert all(f['result']['assumptions']['weather_mode'].startswith('constant') for f in frames)
    assert client.put(f'/api/scenarios/{first["id"]}', json={**mutation(first), 'name': 'Changed', 'definition': first['definition']}).status_code == 409
    clone = client.post(f'/api/scenarios/{first["id"]}/clone', json={**mutation(first), 'name': 'Variant'}).json()
    second = finish(client, clone)
    assert client.get(f'/api/scenarios/{second["id"]}/frames').json()['frames'] == frames
    assert client.get(f'/api/scenarios/{first["id"]}/frames').json()['frames'] == frames


def test_checkpoint_generation_rejects_late_and_duplicate_frames(client):
    scenario, _ = create(client)
    store = client.app.state.service.store
    with store.transaction():
        store.change(scenario['id'], status='running', generation=1)
    assert store.commit_frame(scenario['id'], 1, 0, {'value': 'acknowledged'})
    with pytest.raises(Conflict):
        store.commit_frame(scenario['id'], 1, 0, {'value': 'duplicate'})
    current = store.get(scenario['id'])
    assert client.post(f'/api/scenarios/{scenario["id"]}/pause', json=mutation(current)).status_code == 200
    assert not store.commit_frame(scenario['id'], 1, 1, {'value': 'late'})
    assert store.get(scenario['id'])['checkpoint'] == 0


def test_crash_restart_pauses_and_preserves_origin(tmp_path):
    root = tmp_path / 'durable'
    store = Store(root)
    with store.transaction():
        identity = store.insert('Test', {'origin_time': '2026-01-01T00:00:00Z'}, {})
        store.change(identity, status='running', generation=1)
    store.commit_frame(identity, 1, 0, {'x': 1})
    store.db.close()  # no service shutdown/recovery callback
    recovered = Store(root)
    assert recovered.get(identity)['status'] == 'paused'
    assert recovered.get(identity)['checkpoint'] == 0
    assert recovered.get(identity)['definition']['origin_time'] == '2026-01-01T00:00:00Z'
    assert recovered.frames(identity)[0]['result'] == {'x': 1}
    assert not recovered.commit_frame(identity, 1, 1, {'x': 2})
    recovered.close()


def test_disk_full_rolls_back_without_acknowledging(client, monkeypatch):
    scenario, _ = create(client)
    store = client.app.state.service.store
    original = store.get(scenario['id'])
    def full(*_args):
        raise sqlite3.OperationalError('database or disk is full')
    monkeypatch.setattr(store, 'admit', full)
    response = client.put(f'/api/scenarios/{scenario["id"]}', json={**mutation(scenario), 'name': 'Lost', 'definition': scenario['definition']})
    assert response.status_code == 507 and not response.json()['saved']
    assert store.get(scenario['id']) == original


def test_quota_does_not_delete_existing_data(client):
    scenario, _ = create(client)
    store = client.app.state.service.store
    store.cap_bytes = store.storage()['used_bytes']
    response = client.post('/api/scenarios', json={'request_id': str(uuid4()), 'name': 'Over cap', 'definition': definition(client)})
    assert response.status_code == 507
    assert store.get(scenario['id'])['name'] == 'Baseline'


def test_unknown_database_backup_before_refusal(tmp_path):
    path = tmp_path / 'scenarios.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE precious (value TEXT)')
        db.execute("INSERT INTO precious VALUES ('retained')")
        db.execute('PRAGMA user_version=9')
    with pytest.raises(ValueError, match='Unsupported database version'):
        Store(tmp_path)
    backups = list(tmp_path.glob('pre-migration-*.sqlite3'))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute('SELECT value FROM precious').fetchone()[0] == 'retained'


def test_pack_tampering_blocks_run_but_keeps_view_export(client):
    scenario, _ = create(client)
    scenario = finish(client, scenario)
    sampler = client.app.state.service.packs.entries['test-pack'][0]
    asset = sampler.path.parent / 'cover.json'
    asset.write_text(asset.read_text() + ' ')
    assert client.get(f'/api/scenarios/{scenario["id"]}').json()['continuation_blocked']
    assert client.post(f'/api/scenarios/{scenario["id"]}/run', json=mutation(scenario)).status_code == 409
    assert client.get(f'/api/scenarios/{scenario["id"]}/export').status_code == 200
    assert len(client.get(f'/api/scenarios/{scenario["id"]}/frames').json()['frames']) == 3


def test_missing_pack_and_engine_mismatch_retains_results(client):
    scenario, _ = create(client)
    service = client.app.state.service
    service.packs.entries.clear()
    assert 'pack' in service.get(scenario['id'])['continuation_blocked'].lower()
    with service.store.transaction():
        spec = scenario['definition']; spec['engine_version'] = 'old-engine'
        from wildfire_data.planning.contracts import canonical
        service.store.change(scenario['id'], definition=canonical(spec))
    assert 'Engine version' in service.get(scenario['id'])['continuation_blocked']


def test_import_export_and_report_injection(client):
    scenario, _ = create(client, name='<script>alert(1)</script>')
    scenario = finish(client, scenario)
    document = client.get(f'/api/scenarios/{scenario["id"]}/export').json()
    imported = client.post('/api/scenarios/import', json={'request_id': str(uuid4()), 'document': document})
    assert imported.status_code == 200, imported.text
    assert imported.json()['id'] != scenario['id']
    assert imported.json()['imported'] and 'view-only' in imported.json()['continuation_blocked']
    html = client.get(f'/api/scenarios/{scenario["id"]}/export?format=html').text
    assert '<script>' not in html and '&lt;script&gt;' in html
    assert '<svg ' in html
    for warning in ('general ember crossing', 'unsupported', 'historical', 'not an operational forecast'):
        assert warning.lower() in html.lower()
    geo = client.get(f'/api/scenarios/{scenario["id"]}/export?format=geojson').json()
    assert geo['type'] == 'FeatureCollection' and geo['metadata']['limitations'] == LIMITATIONS
    document['frames'][0]['result']['perimeters']['features'][0]['properties'] = None
    rejected = client.post('/api/scenarios/import', json={'request_id': str(uuid4()), 'document': document})
    assert rejected.status_code == 422


def test_older_policy_snapshot_remains_importable_but_cannot_resume(client):
    scenario, _ = create(client)
    scenario = finish(client, scenario)
    document = client.get(f'/api/scenarios/{scenario["id"]}/export').json()
    document['definition']['engine_version'] = 'saved-older-engine/v3'
    for frame in document['frames']:
        frame['result']['assumptions']['policy'].pop('residence_minutes_by_fuel', None)
    response = client.post('/api/scenarios/import', json={'request_id': str(uuid4()), 'document': document})
    assert response.status_code == 200, response.text
    imported = response.json()
    assert imported['continuation_blocked']
    retained = client.get(f'/api/scenarios/{imported["id"]}/export').json()
    assert retained['definition']['engine_version'] == 'saved-older-engine/v3'
    assert retained['frames'] == document['frames']


@pytest.mark.parametrize('attack', ['path', 'schema', 'geometry', 'checkpoint', 'coverage', 'nested_path', 'missing_layer'])
def test_malformed_import_rejected(client, attack):
    scenario, _ = create(client)
    document = client.get(f'/api/scenarios/{scenario["id"]}/export').json()
    if attack == 'path': document['definition']['model_path'] = '../../.env'
    if attack == 'schema': document['schema_version'] = 999
    if attack == 'geometry': document['pack_snapshot']['layers']['cover']['features'][0]['geometry']['coordinates'] = [[[999, 0], [999, 1], [1000, 1], [999, 0]]]
    if attack == 'checkpoint': document['playback_hour'] = 5
    if attack == 'coverage': document['pack_snapshot']['coverage']['area_km2'] = 'not a number'
    if attack == 'nested_path': document['pack_snapshot']['sources'][0]['file_path'] = '../../.env'
    if attack == 'missing_layer': del document['pack_snapshot']['layers']['cover']
    assert client.post('/api/scenarios/import', json={'request_id': str(uuid4()), 'document': document}).status_code == 422


def test_origin_and_host_security(client):
    for headers in ({'Origin': 'https://evil.example'}, {'Sec-Fetch-Site': 'cross-site'}):
        assert client.get('/api/scenarios', headers=headers).status_code == 403
    assert client.get('/api/config', headers={'Host': 'evil.example'}).status_code == 400
    assert client.post('/api/scenarios/import', content='x'* (8*1024*1024+1), headers={'Content-Type': 'application/json'}).status_code == 413


def test_no_packs_does_not_trigger_preparation(tmp_path):
    with TestClient(create_app(data_root=tmp_path/'user', pack_root=tmp_path/'missing'), base_url='http://127.0.0.1') as client:
        config = client.get('/api/config').json()
        assert not config['modes'] and config['pack_errors']
        assert not (tmp_path/'missing').exists()


def test_invalid_ignition_leaves_checkpoint_intact(client):
    spec = definition(client)
    sampler = client.app.state.service.packs.entries['test-pack'][0]
    lon, lat = sampler.to_geo.transform(850, 150)
    spec['ignitions'] = [{'longitude': lon, 'latitude': lat}]
    scenario, _ = create(client, definition=spec)
    response = client.post(f'/api/scenarios/{scenario["id"]}/run', json=mutation(scenario))
    assert response.status_code == 200
    for _ in range(300):
        result = client.get(f'/api/scenarios/{scenario["id"]}').json()
        if result['status'] != 'running': break
        time.sleep(.05)
    assert result['status'] == 'failed' and result['checkpoint'] == -1
    assert 'supported vegetation' in result['error']


def test_actual_worker_cancellation_retains_checkpoint(client):
    scenario, _ = create(client)
    running = client.post(f'/api/scenarios/{scenario["id"]}/run', json=mutation(scenario)).json()
    paused = client.post(f'/api/scenarios/{scenario["id"]}/pause', json=mutation(running))
    assert paused.status_code == 200, paused.text
    before = client.get(f'/api/scenarios/{scenario["id"]}/frames').json()
    time.sleep(.15)
    assert client.get(f'/api/scenarios/{scenario["id"]}/frames').json() == before
    assert client.app.state.service.worker.available()


def test_failed_checkpoint_update_rolls_back_insert(client):
    scenario, _ = create(client)
    store = client.app.state.service.store
    with store.transaction():
        store.change(scenario['id'], status='running', generation=1)
        store.db.execute("CREATE TRIGGER simulate_interrupted_checkpoint BEFORE UPDATE OF checkpoint ON scenarios BEGIN SELECT RAISE(ABORT, 'simulated interrupted write'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.commit_frame(scenario['id'], 1, 0, {'complete': True})
    assert store.frames(scenario['id']) == []
    assert store.get(scenario['id'])['checkpoint'] == -1


def test_second_instance_cannot_recover_a_live_database(tmp_path, pack_root):
    root = tmp_path / 'same'
    with TestClient(create_app(data_root=root, pack_root=pack_root), base_url='http://127.0.0.1'):
        with pytest.raises(RuntimeError, match='already open'):
            with TestClient(create_app(data_root=root, pack_root=pack_root), base_url='http://127.0.0.1'):
                pass


def test_outbound_guard_denies_remote_connect_and_dns():
    import subprocess
    import sys
    code = """
from wildfire_data.planning.network import deny_outbound
import socket
deny_outbound()
for action in (lambda: socket.getaddrinfo('example.com', 443), lambda: socket.socket().connect(('1.1.1.1', 443))):
    try: action()
    except PermissionError: pass
    else: raise RuntimeError('Outbound access was allowed')
"""
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('entry', ['../escape.json', '/absolute.json', 'C:escape.json', 'a/model.joblib'])
def test_build_archive_rejects_paths_and_executables(tmp_path, entry):
    import hashlib
    import importlib.util
    import io
    import zipfile
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'obtain_planning_build_input.py'
    spec = importlib.util.spec_from_file_location('pack_input', script)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr(entry, '{}')
    payload = buffer.getvalue()
    with pytest.raises(ValueError):
        module.extract_verified(payload, hashlib.sha256(payload).hexdigest(), tmp_path/'extract')
    assert not (tmp_path/'extract').exists()
