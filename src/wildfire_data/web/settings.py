"""Portable settings; importing the application never reads another app's secrets."""
from dataclasses import dataclass, field
import os
from pathlib import Path

from dotenv import dotenv_values

from wildfire_data.core.paths import REPOSITORY_ROOT


@dataclass(frozen=True)
class Settings:
    run_manifest: Path
    data_root: Path
    local_config: Path
    pass_name: str = 'pass_2'
    allowed_hosts: tuple[str, ...] = ('localhost', '127.0.0.1')
    vegetation_manifest: str | None = None
    firms_key: str = field(default='', repr=False)
    prepare_data: bool = False
    download_vegetation: bool = True
    source_data_root: Path | None = None
    fuel_policy: Path | None = None

    @classmethod
    def from_environment(cls):
        root = REPOSITORY_ROOT
        # Read only this repository's optional credential file. Do not search
        # parent directories or mutate the process environment.
        firms_key = os.getenv('NASA_FIRMS_API_KEY') or os.getenv('MAP_KEY')
        if not firms_key:
            credentials = dotenv_values(root / 'config/.env', interpolate=False)
            firms_key = credentials.get('NASA_FIRMS_API_KEY') or credentials.get('MAP_KEY') or ''
        # The sibling archive is an explicit deployment convenience, not a
        # dependency on the original application's source or environment file.
        default_assets = root.parent if (root.parent / 'data').is_dir() else root
        assets = Path(os.getenv('WILDFIRE_ASSET_ROOT', str(default_assets))).resolve()
        run = assets / 'artifacts/incident-two-pass-recovered-20260907-boreal/run_manifest.json'
        if (root / 'artifacts/public-csv/run_manifest.json').is_file():
            run = root / 'artifacts/public-csv/run_manifest.json'
        prepared_config = root / 'config/local_spread_prepared.json'
        have_prepared_packs = prepared_config.is_file() and (root / 'data/planning-packs-v1/index.json').is_file()
        default_local = prepared_config if have_prepared_packs else root / 'config/local_spread.json'
        local_config = Path(os.getenv('WILDFIRE_LOCAL_CONFIG', str(default_local)))
        # Prepared packs are runtime inputs, never a reason to trigger national
        # downloads or conversion. Explicit legacy configurations remain supported.
        default_preparation = '0' if local_config.resolve() == prepared_config.resolve() else '1'
        return cls(
            run_manifest=Path(os.getenv('WILDFIRE_RUN_MANIFEST', str(run))),
            data_root=Path(os.getenv('WILDFIRE_DATA_ROOT', str(assets / 'data'))),
            local_config=local_config,
            pass_name=os.getenv('WILDFIRE_MODEL_PASS', 'pass_2'),
            allowed_hosts=tuple(h.strip() for h in os.getenv('WILDFIRE_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',') if h.strip()),
            vegetation_manifest=os.getenv('WILDFIRE_VEGETATION_MANIFEST'),
            firms_key=firms_key,
            prepare_data=os.getenv('WILDFIRE_PREPARE_DATA', default_preparation) != '0',
            download_vegetation=os.getenv('WILDFIRE_DOWNLOAD_VEGETATION', '1') != '0',
            source_data_root=Path(os.getenv('WILDFIRE_SOURCE_DATA_ROOT', str(root.parent / 'wildfiredetection/data'))),
        )
