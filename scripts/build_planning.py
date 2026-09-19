"""Build an unsigned per-OS onedir distribution with verified pilot packs."""
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    from wildfire_data.planning.packs import Packs
    root = ROOT / 'data' / 'planning-packs-v1'
    packs = Packs(root)
    if packs.errors or set(packs.entries) != {'hinton-alberta', 'black-hawk-colorado'}:
        raise SystemExit(f'Release gate failed: both real verified pilot packs are required. {packs.errors}')
    for _, snapshot in packs.entries.values():
        if snapshot['coverage']['area_km2'] != 81 or snapshot['coverage']['supported_vegetation_area_km2'] <= 0:
            raise SystemExit('Pilot must have aligned 9 × 9 km coverage and supported vegetation')
    static = ROOT / 'src' / 'wildfire_data' / 'planning' / 'static'
    if not (static / 'planning.js').is_file():
        raise SystemExit('Build the React interface first: npm run build:planning')
    from planning_notices import notices
    notice_path = ROOT / 'build' / 'PLANNING-NOTICES.txt'
    notice_path.parent.mkdir(parents=True, exist_ok=True)
    notice_path.write_text(notices(ROOT), encoding='utf-8')
    separator = ';' if sys.platform == 'win32' else ':'
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--name', 'WildfirePlanner',
        '--paths', str(ROOT / 'src'), '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build' / 'planning'),
        '--specpath', str(ROOT / 'build'), '--collect-data', 'pyproj', '--collect-data', 'certifi',
        '--copy-metadata', 'shapely', '--copy-metadata', 'pyproj', '--copy-metadata', 'numpy',
        '--hidden-import', 'uvicorn.logging', '--hidden-import', 'uvicorn.loops.auto', '--hidden-import', 'uvicorn.protocols.http.auto',
        '--add-data', f'{root}{separator}planning_resources',
        '--add-data', f'{static}{separator}wildfire_data/planning/static',
        '--add-data', f'{ROOT / "LICENSE"}{separator}.',
        '--add-data', f'{notice_path}{separator}.',
        '--exclude-module', 'rasterio', '--exclude-module', 'pandas', '--exclude-module', 'sklearn',
        '--exclude-module', 'pytest', '--exclude-module', 'httpx', str(ROOT / 'scripts' / 'planning_entry.py')]
    subprocess.run(command, cwd=ROOT, check=True)
    from wildfire_data.planning.contracts import engine_identity
    from wildfire_data.planning.store import tree_bytes
    target = ROOT / 'dist' / 'WildfirePlanner'
    report = {'kind': 'unsigned-internal-build', 'platform': platform.platform(), 'machine': platform.machine(),
              'python': platform.python_version(), 'engine_version': engine_identity(),
              'installed_bytes': tree_bytes(target), 'packs': packs.list(),
              'status': 'built; requires offline smoke test and target-device acceptance'}
    (target / 'BUILD-INFO.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'packs'}, indent=2))


if __name__ == '__main__':
    main()
