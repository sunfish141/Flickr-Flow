"""CI-only import of a checksummed, data-only full-region build artifact."""
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

import requests

ALLOWED = {'index.json'} | {f'{region}/{name}' for region in ('alberta','colorado')
    for name in ('manifest.json','boundary.geojson','land-cover.tif','roads.parquet')}
MAX_BYTES = 2_000_000_000


def unpack(archive_path, target, checksum):
    from wildfire_data.providers.landscape.regional import RegionalTiles
    archive_path, target = Path(archive_path), Path(target)
    with archive_path.open('rb') as stream:
        if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
            raise ValueError('Full-region archive checksum mismatch')
    if target.exists():
        raise ValueError('Existing regional data is retained; choose a new destination')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='regional-import-', dir=target.parent) as temporary:
        staging = Path(temporary) / 'payload'
        staging.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if len(entries) != len(ALLOWED) or {e.filename for e in entries} != ALLOWED:
                raise ValueError('Unexpected or duplicate regional archive paths')
            if sum(e.file_size for e in entries) > MAX_BYTES:
                raise ValueError('Full-region archive exceeds 2 GB unpacked cap')
            for entry in entries:
                if entry.is_dir() or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError('Regional archive links/directories are not accepted')
                destination = staging / entry.filename
                destination.parent.mkdir(exist_ok=True)
                with archive.open(entry) as src, destination.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024*1024)
        regions = RegionalTiles(staging, Path(temporary)/'unused-cache', lambda amount: None)
        try:
            if set(regions.entries) != {'alberta','colorado'}:
                raise ValueError('Both full regions are required')
        finally:
            regions.close()
        staging.replace(target)


def main():
    url, checksum = os.environ.get('DESKTOP_REGIONS_URL',''), os.environ.get('DESKTOP_REGIONS_SHA256','')
    if not url.startswith('https://') or not re.fullmatch('[a-f0-9]{64}', checksum):
        raise SystemExit('Configure DESKTOP_REGIONS_URL (HTTPS) and DESKTOP_REGIONS_SHA256 for the reviewed full-region artifact')
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='regional-download-') as temporary:
        archive = Path(temporary)/'regions.zip'
        with requests.get(url, stream=True, timeout=(30,120)) as response:
            if response.status_code != 200 or not response.url.startswith('https://'):
                raise ValueError('Regional build-input download failed or redirected away from HTTPS')
            with archive.open('xb') as dst:
                size = 0
                for chunk in response.iter_content(1024*1024):
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise ValueError('Regional download exceeds the 2 GB cap')
                    dst.write(chunk)
        unpack(archive, root/'data/regional-inputs-v1', checksum)
    print('Verified Alberta and Colorado build inputs installed')


if __name__ == '__main__':
    main()
