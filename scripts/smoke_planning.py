"""Exercise source/frozen app and browser; record process-tree RSS and run times."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
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

ROOT = Path(__file__).resolve().parents[1]


def stop_tree(process):
    # Windows venv launchers can have a separate base-Python child. Killing
    # only the wrapper is not an application-termination test.
    try:
        root = psutil.Process(process.pid)
        processes = root.children(recursive=True) + [root]
        for item in reversed(processes):
            try: item.kill()
            except psutil.Error: pass
        psutil.wait_procs(processes, timeout=10)
    except psutil.Error:
        pass
    process.wait(timeout=10)


def ignitions(pack_root):
    from shapely.geometry import Point
    from wildfire_data.planning.packs import Packs
    from wildfire_data.model.local_spread import VEGETATED
    result = {}
    packs = Packs(pack_root)
    if packs.errors or not packs.entries:
        raise ValueError(f'Verified packs required: {packs.errors}')
    for identity, (sampler, _) in packs.entries.items():
        w, s, e, n = sampler.bounds.bounds
        points = [Point(x+51.3, y+47.7) for x in range(int(w), int(e), 100) for y in range(int(s), int(n), 100)]
        for point in sorted(points, key=lambda p: p.distance(sampler.bounds.centroid)):
            covered = [sampler.cover[i] for i in sampler.cover_tree.query(point, predicate='intersects')]
            if len(covered) == 1 and covered[0][1]['fuel'] in VEGETATED:
                if any(g.distance(point) < 30 for g, _ in sampler.roads):
                    continue
                lon, lat = sampler.to_geo.transform(point.x, point.y)
                result[identity] = {'longitude': lon, 'latitude': lat}
                break
        if identity not in result:
            raise ValueError(f'No unambiguous test ignition in {identity}')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frozen', action='store_true')
    parser.add_argument('--packs', type=Path, default=ROOT / 'data' / 'planning-packs-v1')
    args = parser.parse_args()
    artifact = ROOT / 'artifacts' / 'planning-smoke' / ('frozen' if args.frozen else 'source')
    artifact.mkdir(parents=True, exist_ok=True)
    chosen = ignitions(args.packs)
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    peak, done, roots = [0], threading.Event(), []
    def monitor():
        while not done.wait(.1):
            processes = {}
            for pid in list(roots):
                try:
                    p = psutil.Process(pid)
                    for item in [p, *p.children(recursive=True)]: processes[item.pid] = item
                except psutil.Error: pass
            total = 0
            for p in processes.values():
                try: total += p.memory_info().rss
                except psutil.Error: pass
            peak[0] = max(peak[0], total)
    watcher = threading.Thread(target=monitor, daemon=True); watcher.start()
    with tempfile.TemporaryDirectory(prefix='Wildfire offline ü ') as data:
        executable = ROOT / 'dist' / 'WildfirePlanner' / ('WildfirePlanner.exe' if os.name == 'nt' else 'WildfirePlanner')
        command = [str(executable)] if args.frozen else [sys.executable, '-m', 'wildfire_data.planning.launcher']
        command += ['--no-browser', '--port', str(port), '--data-dir', data]
        if not args.frozen: command += ['--packs-dir', str(args.packs)]
        environment = dict(os.environ, PYTHONPATH=str(ROOT/'src'), PROJ_NETWORK='OFF')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        with (artifact/'server.log').open('w', encoding='utf-8') as log:
            start = time.monotonic()
            server = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
            roots.append(server.pid)
            browser = None
            try:
                with httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
                    while time.monotonic()-start < 30:
                        if server.poll() is not None: raise RuntimeError('Server exited; inspect server.log')
                        try:
                            if client.get('/api/config').status_code == 200: break
                        except httpx.TransportError: pass
                        time.sleep(.1)
                    else: raise RuntimeError('Startup exceeded 30-second target')
                    startup = time.monotonic()-start
                node = shutil.which('node') or r'C:\Program Files\nodejs\node.exe'
                with (artifact/'browser.log').open('w', encoding='utf-8') as browser_log:
                    browser = subprocess.Popen([node, 'tests/planning-browser.mjs', base, json.dumps(chosen), str(artifact)],
                        cwd=ROOT/'frontend', env=environment, creationflags=flags, stdout=browser_log, stderr=subprocess.STDOUT)
                    roots.append(browser.pid)
                    code = browser.wait(timeout=900)
                if code: raise RuntimeError(f'Browser acceptance failed ({code}); inspect browser.log')
                with httpx.Client(base_url=base, trust_env=False, timeout=10) as client:
                    baseline = client.get('/api/scenarios').json()['scenarios'][-1]
                    original = client.get(f'/api/scenarios/{baseline["id"]}').json()
                    original_frames = client.get(f'/api/scenarios/{baseline["id"]}/frames').json()['frames']
                    request = {'request_id': str(uuid4()), 'expected_revision': original['revision'], 'name': 'Interrupted 96-hour recovery check'}
                    interrupted = client.post(f'/api/scenarios/{baseline["id"]}/clone', json=request).json()
                    definition = interrupted['definition']; definition['horizon_hours'] = 96
                    interrupted = client.put(f'/api/scenarios/{interrupted["id"]}', json={
                        'request_id': str(uuid4()), 'expected_revision': interrupted['revision'], 'name': interrupted['name'], 'definition': definition}).json()
                    client.post(f'/api/scenarios/{interrupted["id"]}/run', json={
                        'request_id': str(uuid4()), 'expected_revision': interrupted['revision']}).raise_for_status()
                    deadline = time.monotonic()+120
                    while time.monotonic() < deadline:
                        acknowledged = client.get(f'/api/scenarios/{interrupted["id"]}').json()
                        if acknowledged['checkpoint'] >= 0: break
                        if acknowledged['status'] == 'failed': raise RuntimeError(acknowledged['error'])
                        time.sleep(.05)
                    else: raise RuntimeError('No checkpoint before interruption timeout')
                    assert acknowledged['checkpoint'] < 96
                # Kill without graceful shutdown, then prove durable recovery in
                # the SAME Unicode/space-containing user-data directory.
                stop_tree(server)
                server = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
                roots.append(server.pid)
                with httpx.Client(base_url=base, trust_env=False, timeout=5) as client:
                    for _ in range(300):
                        try:
                            response = client.get('/api/scenarios')
                            if response.status_code == 200: break
                        except httpx.TransportError: pass
                        time.sleep(.1)
                    recovered = response.json()['scenarios']
                    assert len(recovered) == 2*len(chosen)+1
                    paused = client.get(f'/api/scenarios/{interrupted["id"]}').json()
                    assert paused['status'] == 'paused' and paused['playback_paused']
                    assert paused['checkpoint'] >= acknowledged['checkpoint'] and paused['checkpoint'] < 96
                    assert paused['definition'] == interrupted['definition']
                    client.post(f'/api/scenarios/{paused["id"]}/run', json={'request_id': str(uuid4()), 'expected_revision': paused['revision']}).raise_for_status()
                    deadline = time.monotonic()+180
                    while time.monotonic() < deadline:
                        resumed = client.get(f'/api/scenarios/{paused["id"]}').json()
                        if resumed['status'] == 'complete': break
                        if resumed['status'] == 'failed': raise RuntimeError(resumed['error'])
                        time.sleep(.2)
                    else: raise RuntimeError('Resumption exceeded acceptance timeout')
                    assert resumed['checkpoint'] == 96
                    resumed_frames = client.get(f'/api/scenarios/{paused["id"]}/frames').json()['frames']
                    assert resumed_frames[:25] == original_frames
                from wildfire_data.planning.store import tree_bytes
                from package_planning_release import tree_digest
                report = {'platform': platform.platform(), 'machine': platform.machine(), 'frozen': args.frozen,
                    'completed_at': datetime.now(timezone.utc).isoformat(),
                    'installed_tree_sha256': tree_digest(executable.parent) if args.frozen else None,
                    'startup_seconds': startup, 'combined_app_browser_peak_rss_bytes': peak[0],
                    'machine_ram_bytes': psutil.virtual_memory().total, 'managed_user_data_bytes': tree_bytes(data),
                    'installed_bytes': tree_bytes(executable.parent) if args.frozen else None,
                    'restart_recovered_cases': len(recovered),
                    'interrupted_checkpoint_observed': acknowledged['checkpoint'],
                    'interrupted_checkpoint_recovered': paused['checkpoint'],
                    'resumed_to_hour': resumed['checkpoint'], 'deterministic_prefix_matches_original': True,
                    'measurement_note': 'Sum of sampled process-tree RSS (may double-count shared pages). This is not the reference 8 GB field test.'}
                (artifact/'resource-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps(report, indent=2), flush=True)
            finally:
                if browser and browser.poll() is None: stop_tree(browser)
                if server.poll() is None: stop_tree(server)
                done.set(); watcher.join(timeout=2)


if __name__ == '__main__':
    main()
