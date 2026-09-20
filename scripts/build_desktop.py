"""Allowlisted complete-app build. Never package credentials or training archives."""
from importlib.metadata import distributions
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pilot-only', action='store_true', help='Explicit development fallback; not full Alberta/Colorado coverage')
    parser.add_argument('--dist-dir', type=Path, default=ROOT / 'dist', help='Build beside a running desktop app without replacing it')
    args = parser.parse_args()
    subprocess.run([sys.executable, str(ROOT / 'scripts/build_offline_overview.py')], cwd=ROOT, check=True)
    from wildfire_data.planning.packs import Packs
    from wildfire_data.core.model_artifacts import public_artifact
    from wildfire_data.planning.store import tree_bytes
    from planning_notices import notices
    packs = ROOT / 'data/planning-packs-v1'
    verified = Packs(packs)
    if verified.errors or set(verified.entries) != {'hinton-alberta', 'black-hawk-colorado'}:
        raise SystemExit('Both verified real packs are required for this internal desktop build')
    regional_root = ROOT / 'data/regional-inputs-v1'
    regional_metadata = []
    if not args.pilot_only:
        from wildfire_data.providers.landscape.regional import RegionalTiles
        if not (regional_root / 'index.json').is_file():
            raise SystemExit('Prepare full Alberta/Colorado inputs with scripts/prepare_regional_inputs.py before this build')
        regional = RegionalTiles(regional_root, ROOT / 'build/unused-regional-cache', lambda amount: None)
        try:
            regional_metadata = regional.regions()
            if set(regional.entries) != {'alberta', 'colorado'}:
                raise SystemExit('Both full-region datasets are required')
        finally:
            regional.close()
    manifest = ROOT / 'artifacts/public-csv/run_manifest.json'
    for name in ('frontier.joblib', 'terrain.csv'):
        public_artifact(manifest, name)
    static = [(ROOT / 'src/wildfire_data' / name / 'static', f'wildfire_data/{name}/static')
              for name in ('web', 'planning', 'desktop')]
    for path, _ in static:
        if not (path / 'index.html').is_file():
            raise SystemExit(f'Missing compiled interface: {path}')
    stage = ROOT / 'build/desktop-notices'
    stage.mkdir(parents=True, exist_ok=True)
    parts = [notices(ROOT), '\nFull desktop dependency notices (including Qt/Chromium):\n']
    versions = {}
    for package in sorted(distributions(), key=lambda d: d.metadata['Name'].lower()):
        name = package.metadata['Name']
        versions[name] = package.version
        parts.append(f'\n--- {name} {package.version} ---\n')
        for entry in package.files or ():
            filename = Path(str(entry)).name.lower()
            if any(word in filename for word in ('license', 'notice', 'copying', 'copyright')):
                path = Path(package.locate_file(entry))
                if path.is_file() and path.suffix.lower() not in ('.dll', '.exe', '.pyd', '.pyc'):
                    parts.append(path.read_text(encoding='utf-8', errors='replace'))
    notice = stage / 'DESKTOP-NOTICES.txt'
    notice.write_text('\n'.join(parts), encoding='utf-8')
    separator = ';' if sys.platform == 'win32' else ':'
    assets = [*static, (packs, 'desktop_resources/data/planning-packs-v1'),
        (ROOT / 'config/local_spread_prepared.json', 'desktop_resources/config'),
        (ROOT / 'config/fuel_policy.json', 'desktop_resources/config'),
        (ROOT / 'src/wildfire_data/model/features/resources', 'wildfire_data/model/features/resources'),
        (manifest, 'desktop_resources/artifacts/public-csv'),
        (ROOT / 'LICENSE', '.'), (notice, '.'),
        (ROOT / 'docs/desktop-quickstart.md', '.')]
    if regional_metadata:
        # Exclude query scratch, preparation receipts and original national ZIPs.
        assets.append((regional_root / 'index.json', 'desktop_resources/data/regional-inputs-v1'))
        for identity in ('alberta', 'colorado'):
            for name in ('manifest.json', 'boundary.geojson', 'land-cover.tif', 'roads.parquet'):
                assets.append((regional_root / identity / name, f'desktop_resources/data/regional-inputs-v1/{identity}'))
    # Include trusted inference artifacts and provenance, not the training CSVs.
    for name in ('frontier.joblib', 'terrain.csv', 'protocol.json', 'evaluation.json'):
        artifact, _ = public_artifact(manifest, name)
        assets.append((artifact, 'desktop_resources/artifacts/public-csv'))
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed', '--name', 'WildfireAtlas',
        '--paths', str(ROOT / 'src'), '--distpath', str(args.dist_dir.resolve()), '--workpath', str(ROOT / 'build/desktop'),
        '--specpath', str(ROOT / 'build'), '--collect-data', 'pyproj', '--collect-data', 'certifi',
        '--collect-data', 'rasterio', '--collect-submodules', 'rasterio',
        '--copy-metadata', 'scikit-learn', '--copy-metadata', 'shapely',
        '--copy-metadata', 'numpy', '--copy-metadata', 'pyproj', '--hidden-import', 'uvicorn.logging',
        '--hidden-import', 'uvicorn.loops.auto', '--hidden-import', 'uvicorn.protocols.http.auto',
        # Trusted joblib artifacts refer to these classes by name; static import
        # analysis cannot discover their inference implementations from pickle.
        '--hidden-import', 'wildfire_data.model.estimators',
        '--hidden-import', 'sklearn.ensemble', '--hidden-import', 'sklearn.linear_model',
        # SciPy's vendored Array API wrapper generates these imports at runtime.
        '--hidden-import', 'scipy._external.array_api_compat.numpy.fft',
        '--hidden-import', 'scipy._external.array_api_compat.numpy.linalg',
        '--exclude-module', 'pytest', '--exclude-module', 'IPython', '--exclude-module', 'matplotlib',
        '--exclude-module', 'tkinter']
    for source, destination in assets:
        command += ['--add-data', f'{source}{separator}{destination}']
    subprocess.run([*command, str(ROOT / 'scripts/desktop_entry.py')], cwd=ROOT, check=True)
    target = args.dist_dir.resolve() / 'WildfireAtlas'
    report = {'kind': 'unsigned-internal-full-desktop', 'platform': platform.platform(),
              'python': platform.python_version(), 'dependencies': versions, 'packs': verified.list(),
              'full_regions': [{k: r[k] for k in ('id', 'label', 'area_km2', 'digest')} for r in regional_metadata],
              'installed_bytes': tree_bytes(target), 'status': 'built; smoke acceptance required',
              'model_manifest_sha256': __import__('hashlib').sha256(manifest.read_bytes()).hexdigest()}
    (target / 'BUILD-INFO.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k not in ('dependencies', 'packs')}, indent=2))


if __name__ == '__main__':
    main()
