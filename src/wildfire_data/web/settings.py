"""Portable settings; importing the application never reads another app's secrets."""
from dataclasses import dataclass
import os
from pathlib import Path

from wildfire_data.core.paths import REPOSITORY_ROOT


@dataclass(frozen=True)
class Settings:
    run_manifest: Path
    data_root: Path
    local_config: Path
    pass_name: str = 'pass_2'
    allowed_hosts: tuple[str, ...] = ('localhost', '127.0.0.1')
    vegetation_manifest: str | None = None
    firms_key: str = ''

    @classmethod
    def from_environment(cls):
        root = REPOSITORY_ROOT
        # The sibling archive is an explicit deployment convenience, not a
        # dependency on the original application's source or environment file.
        default_assets = root.parent if (root.parent / 'data').is_dir() else root
        assets = Path(os.getenv('WILDFIRE_ASSET_ROOT', str(default_assets))).resolve()
        run = assets / 'artifacts/incident-two-pass-recovered-20260907-boreal/run_manifest.json'
        if (root / 'artifacts/public-csv/run_manifest.json').is_file():
            run = root / 'artifacts/public-csv/run_manifest.json'
        return cls(
            run_manifest=Path(os.getenv('WILDFIRE_RUN_MANIFEST', str(run))),
            data_root=Path(os.getenv('WILDFIRE_DATA_ROOT', str(assets / 'data'))),
            local_config=Path(os.getenv('WILDFIRE_LOCAL_CONFIG', str(root / 'config/local_spread.json'))),
            pass_name=os.getenv('WILDFIRE_MODEL_PASS', 'pass_2'),
            allowed_hosts=tuple(h.strip() for h in os.getenv('WILDFIRE_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()),
            vegetation_manifest=os.getenv('WILDFIRE_VEGETATION_MANIFEST'),
            firms_key=os.getenv('NASA_FIRMS_API_KEY') or os.getenv('MAP_KEY') or '',
        )
