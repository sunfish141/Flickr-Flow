"""Prepare verified local vegetation and road sources before serving requests.

Retained archives are imported once. Public NALCMS downloads are a fallback;
road networks and canopy stores are restored only from completed local archives.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import tempfile
from urllib.parse import urlparse

import requests

from wildfire_data.core.hashing import sha256_file
from wildfire_data.providers.storage.storage_budget import load_storage_budget, require_admission, category_for_relative_path
from wildfire_data.providers.vegetation.raster_cache import stage_sources

logger = logging.getLogger('uvicorn.error')


def data_relative(value):
    """Locate archived data without retaining the original machine's prefix."""
    parts = Path(value).parts
    for index, part in enumerate(parts):
        if part in ('raw', 'static', 'vegetation', 'landscape'):
            result = Path(*parts[index:])
            if '..' not in result.parts:
                return result
    raise ValueError('Source path must identify an archived data resource')


def adjacent(parent, value):
    path = (parent / value).resolve()
    if not path.is_relative_to(parent.resolve()):
        raise ValueError('Source artifact path escapes its directory')
    return path


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n'
    if path.exists() and path.read_text() == encoded:
        return
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(encoded)
    temporary.replace(path)


class StartupData:
    def __init__(self, data_root, source_root, *, budget_path, allow_downloads=True):
        self.root = Path(data_root).resolve()
        self.source = Path(source_root).resolve() if source_root else None
        self.policy = load_storage_budget(budget_path)
        self.allow_downloads = allow_downloads
        self.verified = {}
        self.imported = self.downloaded = 0
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir(exist_ok=True)

    @contextmanager
    def locked(self):
        with (self.runtime / 'prepare.lock').open('a') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(stream, fcntl.LOCK_UN)

    def admit(self, target, amount):
        relative = target.relative_to(self.root)
        require_admission(self.policy, self.root,
            category=category_for_relative_path(relative.as_posix()), requested_bytes=amount)
        if shutil.disk_usage(self.root).free < amount + 100_000_000:
            raise ValueError('Insufficient free disk for startup data preparation')

    def check(self, path, digest):
        stat = path.stat()
        identity = (stat.st_size, stat.st_mtime_ns, digest)
        if self.verified.get(path) != identity:
            if sha256_file(path) != digest:
                raise ValueError('Startup source checksum mismatch')
            self.verified[path] = identity
        return path

    def restore(self, relative, digest):
        target = adjacent(self.root, relative)
        if target.is_file():
            return self.check(target, digest)
        source = adjacent(self.source, relative) if self.source else None
        if source is None or not source.is_file():
            raise FileNotFoundError(f'Restore data/{relative} or set WILDFIRE_SOURCE_DATA_ROOT')
        self.admit(target, source.stat().st_size)
        logger.info('Preparing data: importing %s', relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, suffix='.partial', delete=False) as stream:
            temporary = Path(stream.name)
        try:
            # Separate copies keep the new app independent and its source immutable.
            shutil.copyfile(source, temporary)
            self.check(temporary, digest)
            temporary.replace(target)
            self.imported += 1
            return target
        finally:
            temporary.unlink(missing_ok=True)

    def bundle(self, value, digest, *, artifact_key='artifacts'):
        relative = data_relative(value)
        manifest = self.restore(relative, digest)
        document = json.loads(manifest.read_text())
        if document.get('status') != 'complete':
            raise ValueError('Startup requires a completed source manifest')
        artifacts = document[artifact_key]
        if isinstance(artifacts, dict):
            artifacts = artifacts.values()
        for artifact in artifacts:
            if artifact.get('path'):
                child = adjacent(manifest.parent, artifact['path']).relative_to(self.root)
                self.restore(child, artifact['sha256'])
        return manifest, document

    def download_land_cover(self, source, asset):
        identity = Path(asset['path']).stem
        evidence = next((item for item in source['availability_evidence']
                         if item.get('provider_sha256') == identity), None)
        if evidence is None:
            raise ValueError('Land-cover download lacks a pinned provider checksum')
        url = evidence['source_url']
        if urlparse(url).scheme != 'https' or urlparse(url).hostname != 'www.cec.org':
            raise ValueError('Land-cover download requires the configured CEC HTTPS source')
        target = self.root / 'raw/cec-nalcms-land-cover' / (identity + '.zip')
        receipt = target.with_suffix('.json')
        if not target.exists():
            if not self.allow_downloads:
                raise FileNotFoundError('Vegetation sources are missing and downloads are disabled')
            limit = 4_000_000_000
            self.admit(target, limit)
            target.parent.mkdir(parents=True, exist_ok=True)
            logger.info('Preparing data: downloading CEC %s land cover (first startup only)', asset.get('country', ''))
            temporary = target.with_suffix('.zip.partial')
            try:
                with requests.get(url, stream=True, timeout=(15, 120)) as response:
                    response.raise_for_status()
                    digest, size = hashlib.sha256(), 0
                    with temporary.open('wb') as stream:
                        for block in response.iter_content(1024 * 1024):
                            size += len(block)
                            if size > limit:
                                raise ValueError('Land-cover download exceeds 4 GB')
                            digest.update(block)
                            stream.write(block)
                    if digest.hexdigest() != identity:
                        raise ValueError('Downloaded land-cover checksum mismatch')
                temporary.replace(target)
                self.downloaded += 1
            finally:
                temporary.unlink(missing_ok=True)
        self.check(target, identity)
        if not receipt.exists():
            write_json(receipt, {'source_url': url, 'provider_sha256': identity,
                                'retrieved_at': datetime.now(timezone.utc).isoformat()})
        return target, json.loads(receipt.read_text())

    def land_cover(self, config_path):
        config = json.loads(Path(config_path).read_text())
        for source in config['sources']:
            receipts = []
            for asset in source['assets']:
                try:
                    path = self.restore(data_relative(asset['path']), asset['sha256'])
                except FileNotFoundError:
                    path, receipt = self.download_land_cover(source, asset)
                    asset['sha256'] = receipt['provider_sha256']
                    receipts.append(receipt)
                asset['path'] = os.path.relpath(path, self.runtime)
            if receipts:
                captured = max(r['retrieved_at'] for r in receipts)
                source.update(available_at=captured, retrieved_at=captured,
                              availability_basis='captured-response', availability_evidence=receipts)
        output = self.runtime / 'vegetation_sources.json'
        write_json(output, config)
        return output

    def canopy(self, reference):
        relative = data_relative(reference['manifest_path'])
        manifest = self.restore(relative, reference['manifest_sha256'])
        document = json.loads(manifest.read_text())
        if document.get('status') != 'complete':
            raise ValueError('Vegetation feature store is incomplete')
        artifact = document['artifact']
        self.restore(adjacent(manifest.parent, artifact['path']).relative_to(self.root), artifact['sha256'])
        for source in document['sources'].values():
            for asset in source['assets']:
                path = self.restore(data_relative(asset['path']), asset['sha256'])
                asset['path'] = os.path.relpath(path, manifest.parent)
        document['imported_manifest_sha256'] = reference['manifest_sha256']
        portable = manifest.with_name('portable-manifest.json')
        write_json(portable, document)
        return portable

    def raster_cache(self, sources):
        relative = Path('static/nalcms-runtime/manifest.json')
        # The cache is derived from verified sources. Reuse its checksummed TIFFs
        # when available; otherwise stage them directly from the source ZIPs.
        old = self.source / relative if self.source else None
        if old and old.exists() and not (self.root / relative).exists():
            records = json.loads(old.read_text())
            for record in records.values():
                self.restore(adjacent(old.parent, record['path']).relative_to(self.source), record['sha256'])
            write_json(self.root / relative, records)
        return stage_sources(sources, self.root / relative.parent, data_root=self.root)

    def prepare(self, local_config, vegetation_config):
        """Publish runtime-only configs; pinned repository configs stay unchanged."""
        result = {'local_config': Path(local_config), 'vegetation_config': Path(vegetation_config),
                  'raster_cache': None, 'errors': {}}
        reference = json.loads(Path(vegetation_config).read_text())
        with self.locked():
            logger.info('Preparing vegetation and polygon-spread data before startup')
            sources = None
            try:
                sources = self.land_cover(Path(vegetation_config).parent / reference['land_cover_sources_path'])
                reference['land_cover_sources_path'] = sources.name
                result['raster_cache'] = self.raster_cache(sources)
            except Exception:
                logger.exception('Vegetation land-cover preparation failed')
                result['errors']['land_cover'] = 'Land-cover preparation failed; check startup logs and retry.'
            try:
                manifest = self.canopy(reference)
                reference.update(manifest_path=os.path.relpath(manifest, self.runtime), manifest_sha256=sha256_file(manifest))
            except FileNotFoundError:
                logger.warning('Canopy archive unavailable; vegetation inspection will use mapped land cover')
                reference.pop('manifest_path', None)
                reference.pop('manifest_sha256', None)
            except Exception:
                logger.exception('Vegetation canopy preparation failed')
                reference.pop('manifest_path', None)
                reference.pop('manifest_sha256', None)
                result['errors']['canopy'] = 'Canopy preparation failed; check startup logs and retry.'
            if sources:
                output = self.runtime / 'vegetation_inspector.json'
                write_json(output, reference)
                result['vegetation_config'] = output
            local = json.loads(Path(local_config).read_text())
            regions = []
            for region in local['regions']:
                try:
                    manifest, _ = self.bundle(region['manifest'], region['sha256'])
                    regions.append({**region, 'manifest': os.path.relpath(manifest, self.runtime)})
                except FileNotFoundError:
                    logger.warning('Optional polygon pilot %s is not retained locally', region['id'])
            local['regions'] = regions
            expanding = local.get('expanding', {})
            if expanding.get('enabled'):
                try:
                    archive, document = self.bundle(expanding['road_archive'], expanding['road_archive_sha256'], artifact_key='partitions')
                    coverage = document['coverage']
                    self.restore(adjacent(archive.parent, coverage['path']).relative_to(self.root), coverage['sha256'])
                    if not sources or not result['raster_cache']:
                        raise ValueError('Polygon spread requires prepared land-cover rasters')
                    expanding.update(road_archive=os.path.relpath(archive, self.runtime),
                        source_config=sources.name, data_root='..',
                        raster_cache=os.path.relpath(result['raster_cache'], self.runtime))
                except Exception:
                    logger.exception('Expanding polygon data preparation failed')
                    expanding['enabled'] = False
                    result['errors']['roads'] = 'Polygon data preparation failed; restore the road archive and retry startup.'
            output = self.runtime / 'local_spread.json'
            write_json(output, local)
            result['local_config'] = output
            logger.info('Startup data prepared: %s imported files, %s downloads', self.imported, self.downloaded)
        return result
