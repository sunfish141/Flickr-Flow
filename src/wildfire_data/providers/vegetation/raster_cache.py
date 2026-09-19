"""Optional, checksummed temporary TIFF staging for large offline builds."""

import argparse
from contextlib import ExitStack
from functools import lru_cache
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import zipfile

from wildfire_data.core.hashing import sha256_file


def key(asset):
    return hashlib.sha256((asset['sha256'] + ':' + asset.get('member', '')).encode()).hexdigest()


@lru_cache(maxsize=16)
def _verified(path, size, modified, expected):
    if sha256_file(Path(path)) != expected:
        raise ValueError('Temporary vegetation raster checksum mismatch')
    return path


def cached_path(asset, manifest=None):
    manifest = manifest or os.getenv('WILDFIRE_VEGETATION_RASTER_CACHE')
    if not manifest:
        return None
    record = json.loads(Path(manifest).read_text()).get(key(asset))
    if record is None:
        return None
    path = Path(manifest).parent / record['path']
    stat = path.stat()
    return _verified(str(path), stat.st_size, stat.st_mtime_ns, record['sha256'])


def stage_sources(config_path, directory, *, data_root=None):
    config_path, directory = Path(config_path).resolve(), Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / 'manifest.json'
    records = json.loads(manifest.read_text()) if manifest.exists() else {}
    config = json.loads(config_path.read_text())
    checked, staged_bytes = set(), sum((directory / r['path']).stat().st_size for r in records.values())
    for source in config['sources']:
        if source['product'] != 'NALCMS':
            continue
        for asset in source['assets']:
            identity = key(asset)
            if identity in records:
                if sha256_file(directory / records[identity]['path']) != records[identity]['sha256']:
                    raise ValueError('Existing staged raster changed')
                continue
            path = (config_path.parent / asset['path']).resolve()
            if path not in checked and sha256_file(path) != asset['sha256']:
                raise ValueError('Source archive changed before staging')
            checked.add(path)
            with ExitStack() as stack:
                wrapped = stack.enter_context(gzip.open(path, 'rb')) if path.suffix == '.gz' else path
                archive = stack.enter_context(zipfile.ZipFile(wrapped))
                entry = archive.getinfo(asset['member'])
                if staged_bytes + entry.file_size > 3_500_000_000:
                    raise ValueError('Temporary NALCMS staging exceeds 3.5 GB')
                if data_root is not None:
                    from wildfire_data.providers.storage.storage_budget import load_storage_budget, require_admission, category_for_relative_path
                    relative = directory.relative_to(Path(data_root).resolve())
                    require_admission(load_storage_budget(), data_root,
                        category=category_for_relative_path((relative/'raster.tif').as_posix()),
                        requested_bytes=entry.file_size + 100_000)
                target = directory / (identity + '.tif')
                temporary = target.with_suffix('.tif.partial')
                with archive.open(entry) as src, temporary.open('wb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                temporary.replace(target)
            staged_bytes += target.stat().st_size
            records[identity] = {'path': target.name, 'sha256': sha256_file(target)}
            temporary_manifest = directory/'manifest.json.partial'
            temporary_manifest.write_text(json.dumps(records, indent=2))
            temporary_manifest.replace(manifest)
            print(f'Staged {asset["member"]}: {target.stat().st_size:,} bytes', flush=True)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-config', type=Path, required=True)
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--data-root', type=Path)
    args = parser.parse_args()
    print(stage_sources(args.source_config, args.cache_dir, data_root=args.data_root))
