from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import zipfile

from wildfire_data.core.hashing import sha256_file
from wildfire_data.core.paths import REPOSITORY_ROOT
from wildfire_data.model.features.vegetation_features import VegetationFeatureSampler
from wildfire_data.providers.startup_data import StartupData, adjacent, data_relative
from wildfire_data.providers.vegetation.raster_cache import cached_path
from providers.vegetation_fixture import source, POLICY


class StartupDataTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = Path(tmp.name)
        self.old = self.base / 'old/data'
        self.old.mkdir(parents=True)
        self.root = self.base / 'new/data'
        self.preparer = self.make_preparer()

    def make_preparer(self, **kwargs):
        return StartupData(self.root, self.old,
            budget_path=REPOSITORY_ROOT / 'config/storage_budget.json', **kwargs)

    def retained(self, relative, data):
        path = self.old / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path, hashlib.sha256(data).hexdigest()

    def land_fixture(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('land.tif', b'fixture raster bytes')
        payload = stream.getvalue()
        digest = hashlib.sha256(payload).hexdigest()
        land = source()
        land['assets'] = [{'path': f'../data/raw/cec-nalcms-land-cover/{digest}.gz',
                           'sha256': 'a' * 64, 'member': 'land.tif', 'bands': {'land_cover': 1}}]
        land['availability_evidence'] = [{'source_url': 'https://www.cec.org/fixture.zip', 'provider_sha256': digest}]
        return land, payload

    def response(self, payload):
        response = MagicMock()
        response.__enter__.return_value = response
        response.iter_content.return_value = [payload[:7], payload[7:]]
        return response

    def reference(self, land, *, expanding=None):
        config = self.base / 'config'
        config.mkdir(exist_ok=True)
        (config / 'sources.json').write_text(json.dumps({'sources': [land], 'policy': POLICY}))
        vegetation = config / 'vegetation.json'
        vegetation.write_text(json.dumps({'land_cover_sources_path': 'sources.json',
            'manifest_path': '../data/vegetation/absent/manifest.json', 'manifest_sha256': '0' * 64}))
        local = config / 'local.json'
        local.write_text(json.dumps({'regions': [], 'policy': {}, 'expanding': expanding or {'enabled': False}}))
        return local, vegetation

    def test_import_is_atomic_independent_and_reused(self):
        origin, digest = self.retained('raw/nasa-vegetation/example.bin', b'original bytes')
        target = self.preparer.restore('raw/nasa-vegetation/example.bin', digest)
        self.assertEqual(target.read_bytes(), b'original bytes')
        self.assertNotEqual(target.stat().st_ino, origin.stat().st_ino)
        self.assertEqual(self.preparer.restore('raw/nasa-vegetation/example.bin', digest), target)
        self.assertEqual(self.preparer.imported, 1)
        origin.unlink()
        self.assertEqual(self.make_preparer().restore('raw/nasa-vegetation/example.bin', digest), target)

    def test_corrupt_copy_or_existing_file_is_never_published_or_replaced(self):
        _, digest = self.retained('raw/nasa-vegetation/example.bin', b'original')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.preparer.restore('raw/nasa-vegetation/example.bin', '0' * 64)
        self.assertFalse((self.root / 'raw/nasa-vegetation/example.bin').exists())
        self.assertFalse(list((self.root / 'raw').rglob('*.partial')))
        target = self.preparer.restore('raw/nasa-vegetation/example.bin', digest)
        target.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.make_preparer().restore('raw/nasa-vegetation/example.bin', digest)
        self.assertEqual(target.read_bytes(), b'corrupt')

    def test_archive_paths_cannot_escape_data_or_manifest_directory(self):
        self.assertEqual(data_relative('/old/machine/data/raw/map.tif'), Path('raw/map.tif'))
        for value in ('../secret', '/absolute/secret'):
            with self.assertRaises(ValueError):
                adjacent(self.root, value)
        with self.assertRaises(ValueError):
            data_relative('../raw/../../secret')

    def test_download_checks_provider_bytes_and_does_not_backdate_capture(self):
        land, payload = self.land_fixture()
        with patch('wildfire_data.providers.startup_data.requests.get', return_value=self.response(payload)) as get:
            target, receipt = self.preparer.download_land_cover(land, land['assets'][0])
            again, repeated = self.preparer.download_land_cover(land, land['assets'][0])
        self.assertEqual(target.read_bytes(), payload)
        self.assertEqual(target, again)
        self.assertEqual(receipt, repeated)
        self.assertGreater(receipt['retrieved_at'], land['available_at'])
        self.assertEqual(get.call_count, 1)

    def test_bad_download_is_rejected_without_publishing(self):
        land, _ = self.land_fixture()
        with patch('wildfire_data.providers.startup_data.requests.get', return_value=self.response(b'wrong')):
            with self.assertRaisesRegex(ValueError, 'checksum'):
                self.preparer.download_land_cover(land, land['assets'][0])
        self.assertFalse(list(self.root.rglob('*.zip')))
        self.assertFalse(list(self.root.rglob('*.partial')))

    def test_disabled_downloads_leave_network_untouched(self):
        land, _ = self.land_fixture()
        with patch('wildfire_data.providers.startup_data.requests.get') as get:
            with self.assertRaisesRegex(FileNotFoundError, 'disabled'):
                self.make_preparer(allow_downloads=False).download_land_cover(land, land['assets'][0])
            get.assert_not_called()

    def test_cold_start_downloads_stages_and_warm_start_works_without_source(self):
        land, payload = self.land_fixture()
        local, vegetation = self.reference(land)
        original = vegetation.read_bytes()
        with patch('wildfire_data.providers.startup_data.requests.get', return_value=self.response(payload)) as get:
            result = self.preparer.prepare(local, vegetation)
            self.assertEqual(get.call_count, 1)
        self.assertEqual(result['errors'], {})
        self.assertEqual(vegetation.read_bytes(), original)
        sources = json.loads((self.root / 'runtime/vegetation_sources.json').read_text())
        asset = sources['sources'][0]['assets'][0]
        self.assertFalse(Path(asset['path']).is_absolute())
        cached = cached_path(asset, result['raster_cache'])
        self.assertEqual(Path(cached).read_bytes(), b'fixture raster bytes')
        before = (self.root / 'runtime/vegetation_sources.json').read_bytes()
        with patch('wildfire_data.providers.startup_data.requests.get', side_effect=AssertionError('warm startup downloaded')):
            repeated = self.make_preparer().prepare(local, vegetation)
        self.assertEqual(repeated['errors'], {})
        self.assertEqual((self.root / 'runtime/vegetation_sources.json').read_bytes(), before)

    def test_canopy_import_rebases_assets_and_keeps_sqlite_readable(self):
        raster, digest = self.retained('raw/nasa-vegetation/tile.tif', b'fixture')
        directory = self.old / 'vegetation/store'
        directory.mkdir(parents=True)
        database = directory / 'cells.sqlite'
        with sqlite3.connect(database) as connection:
            connection.execute('CREATE TABLE cells (cell_id TEXT, source_id TEXT, payload TEXT)')
        connection.close()
        land = source()
        land['assets'] = [{'path': str(raster), 'sha256': digest, 'bands': {'land_cover': 1}}]
        document = {'kind': 'vegetation-cell-evidence/v1', 'status': 'complete',
            'grid': 'ESRI:102008/1000m', 'policy': POLICY, 'sources': {'land': land},
            'artifact': {'path': 'cells.sqlite', 'sha256': sha256_file(database)}}
        manifest = directory / 'manifest.json'
        manifest.write_text(json.dumps(document))
        portable = self.preparer.canopy({'manifest_path': str(manifest), 'manifest_sha256': sha256_file(manifest)})
        sampler = VegetationFeatureSampler(portable, expected_sha256=sha256_file(portable))
        self.addCleanup(sampler.close)
        self.assertTrue(Path(sampler.sources['land']['assets'][0]['path']).is_relative_to(self.root))
        self.assertEqual(sampler.sample_cell('naea-1km:x=0:y=0', cutoff_at='2026-07-01T00:00:00Z',
            simulation_at='2026-07-01T00:00:00Z')['vegetation_land_cover_missing'], 1)
        self.assertEqual(json.loads(manifest.read_text()), document)

    def test_incomplete_road_archive_cannot_enable_polygon_spread(self):
        land, payload = self.land_fixture()
        path, digest = self.retained('raw/overture-road-archive/fixture/manifest.json', b'{"status":"partial","partitions":[]}')
        local, vegetation = self.reference(land, expanding={'enabled': True,
            'road_archive': str(path), 'road_archive_sha256': digest})
        with patch('wildfire_data.providers.startup_data.requests.get', return_value=self.response(payload)):
            result = self.preparer.prepare(local, vegetation)
        self.assertIn('roads', result['errors'])
        self.assertFalse(json.loads(result['local_config'].read_text())['expanding']['enabled'])

    def test_storage_admission_applies_before_copying(self):
        _, digest = self.retained('raw/nasa-vegetation/oversize.bin', b'x' * 200)
        self.preparer.policy = replace(self.preparer.policy, whole_data_cap_bytes=100)
        with self.assertRaises(RuntimeError):
            self.preparer.restore('raw/nasa-vegetation/oversize.bin', digest)
        self.assertFalse((self.root / 'raw/nasa-vegetation/oversize.bin').exists())
