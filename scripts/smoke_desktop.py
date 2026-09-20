"""Cold-start, real UI, checkpoint restart and native-window checks for desktop."""
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from uuid import uuid4

import httpx
import psutil
from smoke_planning import stop_tree
from desktop_bundle_identity import bundle_digest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frozen', action='store_true')
    parser.add_argument('--dist-dir', type=Path, default=ROOT / 'dist')
    args = parser.parse_args()
    artifact = ROOT / 'artifacts/desktop-smoke' / ('frozen' if args.frozen else 'source')
    artifact.mkdir(parents=True, exist_ok=True)
    executable = args.dist_dir.resolve() / 'WildfireAtlas' / ('WildfireAtlas.exe' if os.name == 'nt' else 'WildfireAtlas')
    entry = [str(executable)] if args.frozen else [sys.executable, '-m', 'wildfire_data.desktop.launcher']
    environment = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), NASA_FIRMS_API_KEY='', MAP_KEY='', WILDFIRE_FORCE_OFFLINE='1',
                       OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    roots, peak, done = [], [0], threading.Event()
    def monitor():
        while not done.wait(.1):
            found = {}
            for pid in list(roots):
                try:
                    root = psutil.Process(pid)
                    for process in [root, *root.children(recursive=True)]: found[process.pid] = process
                except psutil.Error:
                    pass
            total = 0
            for process in found.values():
                try: total += process.memory_info().rss
                except psutil.Error: pass
            peak[0] = max(peak[0], total)
    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    with tempfile.TemporaryDirectory(prefix='Wildfire Desktop ü ') as data:
        # A frozen app must find its own packs, not accidentally fall back to
        # relative files in the development checkout.
        working_directory = Path(data) if args.frozen else ROOT
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        command = [*entry, '--no-ui', '--port', str(port), '--data-dir', data]
        def launch(log):
            process = subprocess.Popen(command, cwd=working_directory, env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
            roots.append(process.pid)
            return process
        def ready(client, process):
            started = time.monotonic()
            while time.monotonic() - started < 45:
                if process.poll() is not None: raise RuntimeError('Desktop service exited; check logs')
                try:
                    response = client.get('/api/desktop')
                    if response.status_code == 200:
                        assert response.json()['model_ready']
                        assert {p['id'] for p in response.json()['packs']} == {'hinton-alberta', 'black-hawk-colorado'}
                        assert not response.json()['pack_errors']
                        assert not response.json()['online_enabled']
                        return time.monotonic() - started
                except httpx.TransportError:
                    pass
                time.sleep(.1)
            raise RuntimeError('Desktop startup exceeded 45-second test ceiling')
        with (artifact / 'process.log').open('w', encoding='utf-8') as log:
            process = launch(log)
            try:
                with httpx.Client(base_url=base, timeout=3, trust_env=False) as client:
                    startup = ready(client, process)
                with httpx.Client(base_url=base, timeout=60, trust_env=False) as client:
                    seed_response = client.post('/api/seed', json={'ignitions': [
                        {'latitude': 58.947698, 'longitude': -113.623403, 'intensity': .7}]})
                    seed_response.raise_for_status()
                    seed = seed_response.json()
                    coarse_response = client.post('/api/step', json={'state': seed['state'], 'origin_at': seed['origin_at']})
                    coarse_response.raise_for_status()
                    coarse = coarse_response.json()
                    assert coarse['elapsed_hours'] == 12 and coarse['terrain_missing_count'] == 0
                    from verify_regional_service import verify
                    regional_report = verify(client)
                node = shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
                browser = subprocess.Popen([node, 'tests/unified-map-browser.mjs', base, str(artifact)], cwd=ROOT / 'frontend',
                    env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
                roots.append(browser.pid)
                if browser.wait(timeout=300): raise RuntimeError('Combined browser test failed; check process.log')
                with httpx.Client(base_url=base, timeout=10, trust_env=False) as client:
                    # Legacy storage stays intact behind the API, but is no
                    # longer a second workspace in the normal desktop UI.
                    config = client.get('/planning/api/config').json()
                    pack = next(p for p in config['packs'] if p['id'] == 'black-hawk-colorado')
                    web = client.get('/api/config').json()
                    # Legacy saved cases still pin the original 81 km² pack;
                    # it is no longer a selectable full-region Explorer entry.
                    region = next((p for p in web['local_spread']['regions'] if p['id'] == pack['id']),
                        {'example_ignition': {'latitude': 39.830298010446704, 'longitude': -105.54139072178096}})
                    created = client.post('/planning/api/scenarios', json={'request_id': str(uuid4()),
                        'name': 'Legacy compatibility check', 'definition': {
                            'pack_id': pack['id'], 'pack_digest': pack['digest'], 'engine_version': config['engine_version'],
                            'origin_time': '2026-08-01T12:00:00Z', 'ignitions': [region['example_ignition']],
                            'horizon_hours': 24, 'wind': {'speed_m_s': 0, 'from_degrees': 0}}})
                    created.raise_for_status()
                    case = created.json()
                    started = time.monotonic()
                    client.post(f'/planning/api/scenarios/{case["id"]}/run', json={
                        'request_id': str(uuid4()), 'expected_revision': case['revision']}).raise_for_status()
                    for _ in range(1200):
                        case = client.get(f'/planning/api/scenarios/{case["id"]}').json()
                        if case['status'] == 'complete': break
                        if case['status'] == 'failed': raise RuntimeError(case['error'])
                        time.sleep(.1)
                    assert case['status'] == 'complete'
                    planner_run_seconds = time.monotonic() - started
                    assert client.get(f'/planning/api/scenarios/{case["id"]}/export').status_code == 200
                    saved = client.get('/planning/api/scenarios').json()['scenarios'][0]
                    identity = saved['id']
                    original = client.get(f'/planning/api/scenarios/{identity}').json()
                    frames = client.get(f'/planning/api/scenarios/{identity}/frames').json()
                    clone = client.post(f'/planning/api/scenarios/{identity}/clone', json={
                        'request_id': str(uuid4()), 'expected_revision': original['revision'], 'name': 'Desktop interruption check'}).json()
                    response = client.post(f'/planning/api/scenarios/{clone["id"]}/run', json={
                        'request_id': str(uuid4()), 'expected_revision': clone['revision']})
                    response.raise_for_status()
                    for _ in range(1200):
                        acknowledged = client.get(f'/planning/api/scenarios/{clone["id"]}').json()
                        if acknowledged['checkpoint'] >= 0: break
                        if acknowledged['status'] == 'failed': raise RuntimeError(acknowledged['error'])
                        time.sleep(.05)
                    assert 0 <= acknowledged['checkpoint'] < 24
                stop_tree(process)
                process = launch(log)
                with httpx.Client(base_url=base, timeout=5, trust_env=False) as client:
                    restart = ready(client, process)
                    recovered = client.get(f'/planning/api/scenarios/{clone["id"]}').json()
                    assert recovered['status'] == 'paused' and recovered['playback_paused']
                    assert recovered['checkpoint'] >= acknowledged['checkpoint']
                    assert recovered['definition'] == clone['definition']
                    assert client.get(f'/planning/api/scenarios/{identity}/frames').json() == frames
                    assert client.get(f'/planning/api/scenarios/{identity}').json()['definition'] == original['definition']
            finally:
                stop_tree(process)
                if (Path(data) / 'desktop.log').exists(): shutil.copy2(Path(data) / 'desktop.log', artifact / 'service.log')
            native_env = dict(environment, QT_QPA_PLATFORM='offscreen', QTWEBENGINE_CHROMIUM_FLAGS='--disable-gpu')
            native_started = time.monotonic()
            native = subprocess.Popen([*entry, '--data-dir', data, '--smoke-window', str(artifact)], cwd=working_directory,
                env=native_env, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
            roots.append(native.pid)
            try:
                if native.wait(timeout=90): raise RuntimeError('Native window smoke check failed')
                native_seconds = time.monotonic() - native_started
            finally:
                if native.poll() is None: stop_tree(native)
    done.set()
    watcher.join(timeout=2)
    browser_report = json.loads((artifact / 'report.json').read_text())
    browser_report['planner_run_seconds'] = planner_run_seconds
    from wildfire_data.planning.store import tree_bytes
    report = {'frozen': args.frozen, 'isolated_working_directory': args.frozen, 'startup_seconds': startup, 'restart_seconds': restart,
              # Conservative upper bound: includes first paint, a deliberate
              # 2.5-second screenshot delay, and clean shutdown, not just HTTP.
              'native_window_check_seconds': native_seconds,
              'bundle_sha256': bundle_digest(executable.parent) if args.frozen else None,
              'peak_process_tree_rss_bytes': peak[0], 'installed_bytes': tree_bytes(executable.parent) if args.frozen else None,
              'interruption_recovered': True, 'native_window': json.loads((artifact / 'native-window.json').read_text()),
              'regional_offline': regional_report,
              'coarse_classifier_step': {'elapsed_hours': coarse['elapsed_hours'], 'terrain_missing_count': coarse['terrain_missing_count'],
                                        'new_ignition_count': coarse['new_ignition_count']},
              'browser': browser_report,
              'targets_met': {'startup_under_30s': startup < 30, 'combined_peak_under_4gb': peak[0] < 4*1024**3,
                              'native_window_check_under_30s': native_seconds < 30,
                              'regional_24h_runs_under_120s': all(p['run_seconds_including_replay'] < 120
                                  for p in regional_report['locations']) if regional_report['installed'] else None,
                              '24h_run_under_120s': browser_report['planner_run_seconds'] < 120}}
    (artifact / 'acceptance.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
