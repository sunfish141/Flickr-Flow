import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from shapely.geometry import LineString, box

from wildfire_data.core.hashing import sha256_file
from wildfire_data.providers.landscape.road_archive import RoadArchive, ARCHIVE_KIND


class RegionalRoadPreloadTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        bounds = [-120., 49., -110., 60.]
        roads = [LineString([(-119., 51.), (-116., 51.)]),
                 LineString([(-114., 55.), (-112., 56.)]),
                 LineString([(-119., 53.), (-119., 54.), (-117., 54.)])]
        rows = []
        for i, g in enumerate(roads):
            w, s, e, n = g.bounds
            rows.append({'id': str(i), 'geometry': g.wkb,
                'bbox': {'xmin': w, 'ymin': s, 'xmax': e, 'ymax': n},
                'width_rules': [{'value': 7., 'between': [0., 1.]}],
                'road_surface': [{'value': 'paved', 'between': [0., 1.]}],
                'road_flags': None, 'sources': [{'dataset': 'test'}]})
        self.asset = self.root/'roads.parquet'
        pq.write_table(pa.Table.from_pylist(rows), self.asset, row_group_size=1)
        coverage = self.root/'coverage.wkb'
        coverage.write_bytes(box(*bounds).wkb)
        self.manifest = self.root/'manifest.json'
        self.manifest.write_text(json.dumps({'kind': ARCHIVE_KIND, 'status': 'complete',
            'bounds': bounds, 'row_count': len(rows),
            'coverage': {'path': coverage.name, 'sha256': sha256_file(coverage)},
            'partitions': [{'path': self.asset.name, 'sha256': sha256_file(self.asset),
                            'rows': len(rows), 'bounds': bounds}]}))
        self.archive = RoadArchive(self.manifest)
        self.addCleanup(self.archive.close)

    def test_preloaded_queries_match_files_and_never_reopen_parquet(self):
        queries = [(-119.5, 50.5, -118.5, 51.5), (-118.5, 53.1, -118., 53.5),
                   (-114.5, 54.5, -111.5, 56.5)]
        expected = [list(self.archive.features(b)) for b in queries]
        state = self.archive.preload('alberta', (-120.,49.,-110.,60.), max_bytes=100000)
        self.assertEqual(state['rows'], 3)
        self.assertEqual(expected[1], [])  # Bbox overlap is not a road intersection.
        dataset = ds.dataset
        def memory_only(source, **kw):
            self.assertIsInstance(source, pa.Table)
            return dataset(source, **kw)
        with patch('wildfire_data.providers.landscape.road_archive.ds.dataset', memory_only):
            self.assertEqual([list(self.archive.features(b)) for b in queries], expected)
        self.assertGreater(self.archive.memory_bytes, 0)
        self.archive.close()
        self.assertEqual(self.archive.memory_bytes, 0)

    def test_queries_crossing_or_outside_preload_boundary_fall_back_exactly(self):
        query = (-119.5,50.5,-116.,51.5)
        expected = list(self.archive.features(query))
        self.archive.preload('small', (-120.,49.,-118.,60.), max_bytes=100000)
        with patch('wildfire_data.providers.landscape.road_archive.ds.dataset', wraps=ds.dataset) as read:
            self.assertEqual(list(self.archive.features(query)), expected)
            self.assertIsInstance(read.call_args.args[0], list)
        with self.assertRaisesRegex(ValueError, 'coverage'):
            list(self.archive.features((-121.,50.,-119.,51.)))

    def test_budget_is_shared_and_failed_region_is_not_partially_published(self):
        first = self.archive.preload('first', (-120.,49.,-118.,60.), max_bytes=100000)
        with self.assertRaisesRegex(ValueError, 'budget'):
            self.archive.preload('second', (-120.,49.,-110.,60.), max_bytes=first['bytes'])
        self.assertEqual(list(self.archive.preloaded), ['first'])
        self.assertEqual(self.archive.memory_bytes, first['bytes'])
        self.assertTrue(list(self.archive.features((-119.5,50.5,-118.5,51.5))))

    def test_memory_hit_still_detects_corrupt_or_missing_source(self):
        self.archive.preload('all', (-120.,49.,-110.,60.), max_bytes=100000)
        stat = self.asset.stat()
        original = self.asset.read_bytes()
        self.asset.write_bytes(b'X' + original[1:])
        os.utime(self.asset, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'checksum'):
            list(self.archive.features((-119.5,50.5,-118.5,51.5)))
        self.asset.unlink()
        with self.assertRaises(OSError):
            list(self.archive.features((-119.5,50.5,-118.5,51.5)))
