import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from dataclasses import asdict

from planning.test_planning import pack_root
from model.fuel_fixture import policy as travel_policy
from web.test_web_app import SpreadEstimator, terrain
from model.test_spread import FuelPatch
from wildfire_data.model.incident_transition import IncidentTransitionModel
from wildfire_data.model.recursive_transition import RECURSIVE_MODEL_FEATURE_COLUMNS
from wildfire_data.desktop.app import create_app, desktop_settings
from wildfire_data.desktop.network import NetworkPolicy
from wildfire_data.desktop.window import permitted_request
from wildfire_data.web.app import create_app as web_app
from wildfire_data.web.live_firms import LiveFirmsError


@pytest.fixture
def combined(tmp_path, pack_root):
    resources = tmp_path / 'resources'
    packs = resources / 'data/planning-packs-v1'
    shutil.copytree(pack_root, packs)
    metadata = json.loads((packs / 'index.json').read_text())['packs'][0]
    config = resources / 'local.json'
    config.write_text(json.dumps({'policy': asdict(travel_policy()), 'mesh_m': 100,
        'default_region': 'test-pack', 'regions': [{'id': 'test-pack', 'label': 'Synthetic',
            'manifest': str(packs / 'test-pack/manifest.json'), 'sha256': metadata['manifest_sha256']}]}))
    calls = []
    def provider(*args, **kwargs):
        calls.append(True)
        raise LiveFirmsError('Synthetic provider failure')
    model = IncidentTransitionModel(SpreadEstimator(), feature_columns=RECURSIVE_MODEL_FEATURE_COLUMNS)
    explorer = web_app(model=model, terrain_provider=terrain, landscape=FuelPatch(),
                       vegetation_sampler=object(), local_config=config, firms_loader=provider)
    app = create_app(data_root=tmp_path / 'user data ü', resources=resources, explorer=explorer)
    with TestClient(app, base_url='http://127.0.0.1') as client:
        yield client, calls


def test_single_map_shell_preserves_legacy_data_services(combined):
    client, _ = combined
    assert client.get('/').status_code == 200
    assert client.get('/explore/').status_code == 200
    assert client.get('/planning/').status_code == 200
    assert client.get('/planning/static/planning.js').status_code == 200
    assert client.get('/static/app.js').status_code == 200
    assert 'id="planner"' not in client.get('/').text
    assert 'network-toggle' not in client.get('/').text
    assert client.get('/static/offline-overview.geojson').status_code == 200
    config = client.get('/api/config').json()
    assert config['model_ready'] and config['local_spread']['available']
    assert config['desktop']['connection_mode'] == 'automatic'
    planning = client.get('/planning/api/config').json()
    assert planning['packs'] and planning['offline']
    assert 'tile.openstreetmap' not in client.get('/planning/').headers['content-security-policy']
    assert client.get('/planning/').headers['x-frame-options'] == 'SAMEORIGIN'


@pytest.mark.parametrize('path', ['/api/desktop/connectivity', '/api/desktop/firms-key', '/planning/api/scenarios'])
def test_mounted_and_root_mutations_reject_cross_origin(combined, path):
    client, _ = combined
    assert client.post(path, json={}, headers={'Origin': 'https://evil.example'}).status_code == 403


def test_link_state_gates_provider_and_key_never_echoed(combined):
    client, calls = combined
    client.post('/api/desktop/connectivity', json={'enabled': False})
    assert client.post('/api/firms', json={}).status_code in (422, 503)
    assert client.post('/api/firms', content='null', headers={'Content-Type': 'application/json'}).status_code == 503
    assert calls == []
    response = client.post('/api/desktop/firms-key', json={'key': 'test-secret'})
    assert response.status_code == 200 and 'test-secret' not in response.text
    invalid = client.post('/api/desktop/firms-key', json={'key': {'secret': 'test-secret'}})
    assert invalid.status_code == 422 and 'test-secret' not in invalid.text
    assert client.post('/api/desktop/connectivity', json={'enabled': 'true'}).status_code == 422
    assert client.post('/api/desktop/connectivity', json={'enabled': True}).json()['online_enabled']
    assert client.post('/api/firms', content='null', headers={'Content-Type': 'application/json'}).status_code == 502
    assert calls == [True]
    assert not client.post('/api/desktop/connectivity', json={'enabled': False}).json()['online_enabled']
    assert client.post('/api/firms', content='null', headers={'Content-Type': 'application/json'}).status_code == 503
    assert calls == [True]


def test_nested_scenario_create_and_export(combined):
    client, _ = combined
    config = client.get('/planning/api/config').json()
    pack = config['packs'][0]
    sampler = client.app.state.planner.state.service.packs.entries[pack['id']][0]
    lon, lat = sampler.to_geo.transform(150, 150)
    body = {'request_id': str(uuid4()), 'name': 'Combined app case', 'definition': {
        'pack_id': pack['id'], 'pack_digest': pack['digest'], 'engine_version': config['engine_version'],
        'origin_time': '2026-08-01T12:00:00Z', 'ignitions': [{'longitude': lon, 'latitude': lat}],
        'horizon_hours': 2, 'wind': {'speed_m_s': 0, 'from_degrees': 0}}}
    response = client.post('/planning/api/scenarios', json=body)
    assert response.status_code == 200, response.text
    identity = response.json()['id']
    assert client.get(f'/planning/api/scenarios/{identity}/export').status_code == 200
    assert len(client.get('/planning/api/scenarios').json()['scenarios']) == 1


def test_network_enforcement_and_browser_allowlist():
    policy = NetworkPolicy(online=False)
    with pytest.raises(PermissionError):
        policy.audit('socket.getaddrinfo', ('example.com', 443))
    policy.audit('socket.getaddrinfo', ('127.0.0.1', 8000))
    with socket.socket() as sock, pytest.raises(PermissionError):
        policy.audit('socket.connect', (sock, ('1.1.1.1', 443)))
    policy.set_online(True)
    policy.audit('socket.getaddrinfo', ('example.com', 443))
    base = 'http://127.0.0.1:8000'
    assert permitted_request(base + '/planning/', base, False)
    assert not permitted_request('http://127.0.0.1:9000/', base, False)
    assert not permitted_request('file:///C:/secret', base, True)
    assert not permitted_request('https://evil.example', base, True)
    assert not permitted_request('https://tile.openstreetmap.org/1/1/1.png', base, False)
    assert permitted_request('https://tile.openstreetmap.org/1/1/1.png', base, True)
    forced = NetworkPolicy(forced_offline=True)
    forced.set_online(True)
    assert not forced.online


def test_desktop_settings_do_not_prepare_or_discover_external_archives(tmp_path):
    settings = desktop_settings(tmp_path)
    assert not settings.prepare_data and not settings.download_vegetation
    assert settings.local_config.is_relative_to(tmp_path)
    assert settings.run_manifest.is_relative_to(tmp_path)
    assert settings.fuel_policy == tmp_path / 'config/fuel_policy.json'


def test_desktop_import_does_not_load_development_dotenv():
    subprocess.run([sys.executable, '-c',
        "import dotenv\n"
        "def forbidden(*args, **kwargs): raise AssertionError('Unexpected dotenv discovery')\n"
        "dotenv.dotenv_values = forbidden\n"
        "dotenv.load_dotenv = forbidden\n"
        "import wildfire_data.desktop.app\n"], check=True, timeout=60)
