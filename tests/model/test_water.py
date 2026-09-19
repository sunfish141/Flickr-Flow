from dataclasses import replace
import unittest
from shapely.geometry import box

from wildfire_data.core.grid import GridCell
from wildfire_data.model.features.landscape import Landscape
from model.test_spread import model


class RasterWaterTests(unittest.TestCase):
    def landscape(self, water):
        landscape = Landscape(land=[box(-179,24,-50,84)], lakes=[])
        landscape.use_land_cover(lambda key: {'values': {'vegetation_land_cover_water': water.get(key)}}, 'fixture')
        return landscape

    def test_raster_river_splits_land_even_when_absent_from_generalized_map(self):
        west, river, east = (GridCell(x,0).cell_id for x in (-1,0,1))
        landscape = self.landscape({GridCell(0,y).cell_id: .7 for y in range(-6,7)})
        self.assertTrue(landscape.allows_cell(west))
        self.assertFalse(landscape.allows_cell(river))
        self.assertTrue(landscape.allows_cell(east))
        m = model({west, river, east}, probability=1.)
        m.landscape = landscape
        state = m.initial_state({west: 1})
        for _ in range(5):
            state = m.step(state, terrain_provider=lambda _: {}).state
            self.assertNotIn(river, [c.cell_id for c in state.active_cells])
            self.assertNotIn(river, state.burned_cell_ids)
            self.assertNotIn(east, [c.cell_id for c in state.active_cells])
            self.assertNotIn(east, state.burned_cell_ids)
        with self.assertRaisesRegex(ValueError, 'mapped land'):
            m.initial_state({river: 1})

    def test_diagonal_cannot_cut_a_water_corner_and_missing_is_not_water(self):
        a, b, c = GridCell(0,0).cell_id, GridCell(1,1).cell_id, GridCell(1,0).cell_id
        landscape = self.landscape({c: .001})
        self.assertTrue(landscape.allows_cell(a))
        self.assertTrue(landscape.allows_cell(b))
        self.assertFalse(landscape.allows_spread(a,b))
        landscape.use_land_cover(lambda _: {'values': {}}, 'missing')
        self.assertTrue(landscape.allows_spread(a,b))

    def test_previously_submitted_burned_water_is_removed(self):
        land, water = GridCell(0,0).cell_id, GridCell(1,0).cell_id
        m = model(probability=.01)
        state = replace(m.initial_state({land: 1}), burned_cell_ids=(water,))
        m.landscape = self.landscape({water: 1.})
        state = m.step(state, terrain_provider=lambda _: {}).state
        self.assertNotIn(water, state.burned_cell_ids)
