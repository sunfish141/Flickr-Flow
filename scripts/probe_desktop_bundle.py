"""Developer diagnostic: inspect bundled inference using PyInstaller's importer.

Run with the matching build interpreter and -S. No development site-packages
remain on the import path after the frozen importer is installed.
"""
import os
from pathlib import Path
import sys
import traceback

root = Path(__file__).resolve().parents[1]
bundle = root / 'dist/WildfireAtlas/_internal'
loader = root / '.venv-desktop/Lib/site-packages/PyInstaller/loader'
sys._MEIPASS = str(bundle)
sys._pyinstaller_pyz = str(root / 'build/desktop/WildfireAtlas/PYZ-00.pyz')
sys.path.insert(0, str(loader))
import pyimod02_importers
pyimod02_importers.install()
sys.frozen = True
sys.path = [str(bundle / 'base_library.zip'), str(bundle)]
sys.path_importer_cache.clear()
handles = [os.add_dll_directory(str(path)) for path in [bundle, *bundle.glob('*.libs')]] if os.name == 'nt' else []
try:
    from wildfire_data.model.loading import load_pass_model
    manifest = bundle / 'desktop_resources/artifacts/public-csv/run_manifest.json'
    model = load_pass_model(manifest)
    print('Trusted bundled classifier loaded:', type(model).__name__)
    from wildfire_data.model.spread import FireSpreadModel
    from wildfire_data.model.features.landscape import Landscape
    spread = FireSpreadModel.from_incident_model(model, Landscape())
    print('Bundled spread model and Natural Earth loaded')
    from wildfire_data.core.model_artifacts import public_artifact
    from wildfire_data.providers.terrain_csv import CSVTerrainProvider
    terrain, _ = public_artifact(manifest, 'terrain.csv')
    CSVTerrainProvider(terrain)
    print('Bundled CSV terrain loaded')
except Exception:
    traceback.print_exc()
    raise SystemExit(1)
