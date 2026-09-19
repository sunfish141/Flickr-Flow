from dataclasses import replace
from datetime import datetime, timezone
import unittest

from wildfire_data.core.grid import GridCell
from wildfire_data.model.fuel import FuelPolicy, FUEL_HOURS
from wildfire_data.model.features.vegetation_features import empty_features
from wildfire_data.web.schemas import StateInput
from model.test_spread import model


def sample(fuel='needleleaf', fraction=1., *, density=None, valid=1.):
    result = empty_features()
    result.update({'vegetation_land_cover_' + k: fraction if k == fuel else 0. for k in FUEL_HOURS})
    result.update(vegetation_land_cover_missing=0., vegetation_land_cover_valid_fraction=valid)
    if density is not None:
        result.update(vegetation_cover_missing=0., vegetation_tree_cover=density, vegetation_non_tree_cover=0.)
    return result


class Sampler:
    def __init__(self, values):
        self.values, self.calls = values, []

    def sample_cell(self, cell_id, **context):
        self.calls.append((cell_id, context))
        return self.values.get(cell_id, empty_features())


class FuelTests(unittest.TestCase):
    def test_type_and_vegetated_area_control_duration(self):
        p = FuelPolicy()
        self.assertEqual(p.estimate(sample()).burn_duration_hours, 72)
        self.assertEqual(p.estimate(sample('grassland')).burn_duration_hours, 12)
        self.assertEqual(p.estimate(sample(fraction=.5)).burn_duration_hours, 36)
        self.assertEqual(p.estimate(sample(density=.25)).burn_duration_hours, 18)
        self.assertEqual(p.estimate(sample(fraction=0)).burn_duration_hours, 0)

    def test_missing_support_is_distinct_from_no_fuel(self):
        p = FuelPolicy()
        unknown = p.estimate(empty_features())
        self.assertEqual((unknown.burn_duration_hours, unknown.vegetation_fraction, unknown.fuel_basis),
                         (24, None, 'missing vegetation fallback'))
        partial = p.estimate(sample(fraction=.5, valid=.5))
        self.assertEqual(partial.burn_duration_hours, 36 + 12)
        canopy = empty_features()
        canopy.update(vegetation_cover_missing=0., vegetation_tree_cover=.5, vegetation_non_tree_cover=.25)
        self.assertEqual(p.estimate(canopy).burn_duration_hours, 33)
        self.assertEqual(p.estimate(canopy).fuel_basis, 'canopy density')

    def test_invalid_policy_durations_are_rejected(self):
        for kwargs in [{'fallback_hours': 0}, {'fallback_hours': float('nan')}, {'hours_at_full_cover': {}},
                       {'hours_at_full_cover': {**FUEL_HOURS, 'needleleaf': 169}}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FuelPolicy(**kwargs)

    def test_each_seed_uses_its_fuel_clock_and_replay_does_not_refill_it(self):
        forest, grass = GridCell(0,0).cell_id, GridCell(5,0).cell_id
        m = model({forest, grass}, probability=.01)
        m.fuel_sampler = Sampler({forest: sample(), grass: sample('grassland')})
        origin = datetime(2026,9,19,tzinfo=timezone.utc)
        state = m.initial_state({forest: .7, grass: .7}, origin_at=origin)
        self.assertEqual(sorted(c.burn_duration_hours for c in state.active_cells), [12,72])
        from dataclasses import asdict
        state = StateInput.model_validate(asdict(state)).to_state()
        calls = list(m.fuel_sampler.calls)
        previous = state
        state = m.step(state, terrain_provider=lambda _: {}, origin_at=origin).state
        self.assertEqual(state.burned_cell_ids, (grass,))
        self.assertAlmostEqual(state.active_cells[0].fuel_remaining, 5/6)
        self.assertEqual(m.step(previous, terrain_provider=lambda _: {}, origin_at=origin).state, state)
        for _ in range(5):
            state = m.step(state, terrain_provider=lambda _: {}, origin_at=origin).state
        self.assertFalse(state.active_cells)
        self.assertEqual(set(state.burned_cell_ids), {forest, grass})
        self.assertEqual(m.fuel_sampler.calls, calls)

    def test_new_ignition_uses_target_fuel_and_bare_ground_is_not_ignited(self):
        forest, grass, bare = (GridCell(x,0).cell_id for x in range(3))
        m = model({forest,grass,bare}, probability=1.)
        m.fuel_sampler = Sampler({forest: sample(), grass: sample('grassland'), bare: sample(fraction=0)})
        origin = datetime(2026,9,19,tzinfo=timezone.utc)
        state = m.initial_state({forest: 1}, origin_at=origin)
        state = m.step(state, terrain_provider=lambda _: {}, origin_at=origin).state
        self.assertEqual(next(c for c in state.active_cells if c.cell_id == grass).burn_duration_hours, 12)
        state = m.step(state, terrain_provider=lambda _: {}, origin_at=origin).state
        self.assertIn(grass, state.burned_cell_ids)
        self.assertNotIn(bare, state.burned_cell_ids)
        self.assertEqual([c.cell_id for c in state.active_cells], [forest])
        with self.assertRaisesRegex(ValueError, 'no mapped vegetated fuel'):
            m.initial_state({bare: 1}, origin_at=origin)

    def test_fuel_evidence_is_selected_at_origin_for_new_frontier_cells(self):
        a, b = GridCell(0,0).cell_id, GridCell(1,0).cell_id
        m = model({a,b}, probability=1.)
        m.fuel_sampler = Sampler({a: sample(), b: sample('grassland')})
        origin = datetime(2026,9,19,tzinfo=timezone.utc)
        state = m.initial_state({a: 1}, origin_at=origin)
        m.step(replace(state, step_index=2), terrain_provider=lambda _: {}, origin_at=origin)
        self.assertTrue(m.fuel_sampler.calls)
        self.assertTrue(all(context == {'cutoff_at': origin, 'simulation_at': origin} for _, context in m.fuel_sampler.calls))
