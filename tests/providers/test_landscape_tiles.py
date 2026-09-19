from collections import OrderedDict
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest

from shapely.geometry import LineString

from model.fuel_fixture import bundle
from wildfire_data.providers.landscape.tiles import LandscapeTiles, tile_bounds


class LandscapeTileCacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = LandscapeTiles.__new__(LandscapeTiles)
        self.store.data_root = Path(directory.name)
        self.store.identity = 'test-verified-source'
        self.store.samplers = OrderedDict()
        self.store.readers, self.store.reader_stack = {}, ExitStack()
        self.addCleanup(self.store.close)
        self.path = self.store.path((0,0))
        self.original = bundle(self.path.parent, bounds=tile_bounds((0,0)))

    def test_repeat_load_reuses_verified_geometry_and_still_checks_pinned_identity(self):
        first = self.store.load((0,0))
        self.assertIs(first, self.store.load((0,0), expected=first.sha256))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.store.load((0,0), expected='0'*64)
        self.store.close()
        self.assertFalse(self.store.samplers)

    def test_changed_or_missing_assets_are_not_hidden_by_the_cache(self):
        self.store.load((0,0))
        asset = self.path.parent/'roads.json'
        asset.write_text('corrupted')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.store.load((0,0))
        asset.unlink()
        with self.assertRaises(OSError):
            self.store.load((0,0))

    def test_replaced_verified_bundle_has_a_new_graph_identity(self):
        first = self.store.load((0,0))
        bundle(self.path.parent, bounds=tile_bounds((0,0)), roads=[(LineString([(50,0),(50,3000)]),{})])
        replacement = self.store.load((0,0))
        self.assertIsNot(first, replacement)
        self.assertNotEqual(first.sha256, replacement.sha256)
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.store.load((0,0), expected=first.sha256)
