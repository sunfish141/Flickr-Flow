"""CI-only retrieval of a reviewed pack artifact; strict ZIP admission."""
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import urllib.request
import zipfile


def extract_verified(payload, expected, target):
    if hashlib.sha256(payload).hexdigest() != expected:
        raise ValueError('Reviewed pack archive checksum mismatch')
    root = Path(target).resolve()
    if root.exists():
        raise ValueError('Build input directory already exists; refusing overwrite')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files = archive.infolist()
        if len(files) > 100 or sum(f.file_size for f in files) > 100_000_000:
            raise ValueError('Pack archive exceeds bounded size')
        seen = set()
        for item in files:
            if '\\' in item.orig_filename or '\x00' in item.orig_filename:
                raise ValueError('Unsafe raw archive path')
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in item.filename or ':' in item.filename:
                raise ValueError('Unsafe archive path')
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('Symlinks are not accepted')
            if item.filename in seen:
                raise ValueError('Duplicate archive path')
            seen.add(item.filename)
            if not item.is_dir() and (path.suffix != '.json' or len(path.parts) > 2):
                raise ValueError('Only pack JSON documents are accepted')
        for item in files:
            destination = root.joinpath(*PurePosixPath(item.filename).parts)
            if not destination.resolve().is_relative_to(root):
                raise ValueError('Archive path escapes destination')
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(item))
    from wildfire_data.planning.packs import Packs
    packs = Packs(root)
    if packs.errors or len(packs.entries) != 2:
        raise ValueError(f'Invalid reviewed pack archive: {packs.errors}')


def main():
    url, checksum = os.environ.get('PLANNING_PACKS_URL', ''), os.environ.get('PLANNING_PACKS_SHA256', '')
    if not url.startswith('https://') or not re.fullmatch('[a-f0-9]{64}', checksum):
        raise SystemExit('Configure reviewed PLANNING_PACKS_URL and PLANNING_PACKS_SHA256 repository variables. No secrets required.')
    with urllib.request.urlopen(url, timeout=60) as response:
        if not response.url.startswith('https://'):
            raise ValueError('Build input redirected away from HTTPS')
        payload = response.read(100_000_001)
    if len(payload) > 100_000_000:
        raise ValueError('Pack archive exceeds 100 MB build-input cap')
    extract_verified(payload, checksum, Path('data/planning-packs-v1'))


if __name__ == '__main__':
    main()
