import gc
from pathlib import Path
import tempfile
import unittest
import weakref

from shapely.geometry import LineString, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

from model.fuel_fixture import bundle
from wildfire_data.core.grid import GridCell
from wildfire_data.providers.landscape.tiles import LandscapeMosaic


class IndexedLandscapeTests(unittest.TestCase):
    def test_indexed_distance_matches_exhaustive_distance_and_censoring(self):
        with tempfile.TemporaryDirectory() as tmp:
            bounds = (0, 0, 6000, 6000)
            urban = box(4000,3000,4500,4000)
            roads = [(LineString([(x,0),(x+100,6000)]), {}) for x in (200,1700,5000)]
            sampler = bundle(Path(tmp)/'tile', bounds=bounds, roads=roads,
                cover=[(box(*bounds).difference(urban), 'grassland'), (urban, 'urban')])
            for candidate in (sampler, LandscapeMosaic([sampler])):
                for x, y in ((0,0),(2,2),(4,3),(5,5)):
                    cell = GridCell(x,y)
                    center = box(*cell.bounds_projected).centroid
                    features = candidate.sample_cell(cell.cell_id)
                    limit = min(5000., candidate.bounds.boundary.distance(center))
                    for kind, objects in [('road',[g for g,_ in candidate.roads]),('urban',[urban])]:
                        distance = min(g.distance(center) for g in objects)
                        self.assertAlmostEqual(features[f'landscape_{kind}_distance_m'], min(distance,limit))
                        self.assertEqual(features[f'landscape_{kind}_distance_censored'], float(distance>limit))
                    features['landscape_road_distance_m'] = -1
                    self.assertGreaterEqual(candidate.sample_cell(cell.cell_id)['landscape_road_distance_m'], 0)

    def test_empty_sources_and_missing_cover_retain_unknown_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            sampler = bundle(tmp, bounds=(0,0,3000,3000), cover=[(box(0,0,2500,3000),'grassland')])
            f = sampler.sample_cell(GridCell(1,1).cell_id)
            self.assertEqual(f['landscape_road_distance_m'], 1500)
            self.assertEqual(f['landscape_road_distance_censored'], 1)
            self.assertAlmostEqual(f['landscape_urban_distance_m'], 1000)
            self.assertEqual(f['landscape_urban_distance_censored'], 1)
            self.assertIsNone(sampler.sample_cell(GridCell(2,1).cell_id)['landscape_urban_distance_m'])

    def test_sampled_mosaic_releases_without_waiting_for_cyclic_gc(self):
        with tempfile.TemporaryDirectory() as tmp:
            tile = bundle(tmp, bounds=(0,0,3000,3000))
            mosaic = LandscapeMosaic([tile])
            mosaic.sample_cell(GridCell(1,1).cell_id)
            reference = weakref.ref(mosaic)
            enabled = gc.isenabled()
            gc.disable()
            try:
                del mosaic
                self.assertIsNone(reference())
            finally:
                if enabled:
                    gc.enable()

    def test_tiled_cover_sampling_matches_dissolved_cover_across_seams_and_gaps(self):
        import copy
        from collections import OrderedDict
        with tempfile.TemporaryDirectory() as tmp:
            samplers = []
            for x in (0,1,4):
                domain = box(x*3000,0,(x+1)*3000,3000)
                urban = box(x*3000+700,1500,x*3000+1400,2500)
                hole = box(x*3000+1500,500,x*3000+1600,600)
                samplers.append(bundle(Path(tmp)/str(x), bounds=domain.bounds,
                    cover=[(domain.difference(urban).difference(hole),'grassland'),(urban,'urban')]))
            tiled = LandscapeMosaic(samplers)
            dissolved = copy.copy(tiled)
            dissolved._sample_cache = OrderedDict()
            dissolved.cover = [(unary_union([g for g,p in tiled.cover if p['fuel']==fuel]), {'fuel':fuel})
                              for fuel in ('grassland','urban')]
            dissolved.cover_tree = STRtree([g for g,_ in dissolved.cover])
            dissolved.urban_tree = STRtree([g for g,p in dissolved.cover if p['fuel']=='urban'])
            for x in range(15):
                for y in range(3):
                    cell = GridCell(x,y).cell_id
                    actual, expected = tiled.sample_cell(cell), dissolved.sample_cell(cell)
                    for key in actual:
                        if expected[key] is None:
                            self.assertIsNone(actual[key])
                        else:
                            self.assertAlmostEqual(actual[key], expected[key], places=8)
