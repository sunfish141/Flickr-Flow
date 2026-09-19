import unittest
from dataclasses import replace

import numpy as np

from wildfire_data.core.grid import GridCell
from wildfire_data.model.incident_transition import EvidenceCell, IncidentTransitionModel
from wildfire_data.model.features.landscape import Landscape
from wildfire_data.model.recursive_transition import RECURSIVE_MODEL_FEATURE_COLUMNS
from wildfire_data.model.spread import FireSpreadModel


class Classifier:
    def __init__(self, probability=.9):
        self.probability = probability

    def predict_proba(self, values):
        return np.tile([1 - self.probability, self.probability], (len(values), 1))


class FuelPatch:
    version = "test-fuel-patch"

    def __init__(self, cells=None):
        self.cells = cells

    def allows_cell(self, cell_id):
        return self.cells is None or cell_id in self.cells

    def allows_spread(self, source, target):
        return self.allows_cell(source) and self.allows_cell(target)


def model(cells=None, probability=.9):
    fitted = IncidentTransitionModel(Classifier(probability),
        feature_columns=RECURSIVE_MODEL_FEATURE_COLUMNS, ignition_threshold=.2)
    return FireSpreadModel.from_incident_model(fitted, FuelPatch(cells))


class FireSpreadTests(unittest.TestCase):
    def test_growth_is_not_capped_at_half_the_population_or_128_births(self):
        m = model()
        state = m.initial_state({GridCell(4 * x, 0).cell_id: 1. for x in range(20)})
        result = m.step(state, terrain_provider=lambda _: {})
        ignited = sum(p.will_ignite for p in result.predictions)
        self.assertGreater(ignited, 128)
        self.assertLess(ignited, 160)
        self.assertEqual(len(result.state.active_cells), 20 + ignited)
        self.assertEqual(result, m.step(state, terrain_provider=lambda _: {}))

    def test_finite_patch_expands_then_burns_out_without_reignition(self):
        cells = {GridCell(x, y).cell_id for x in range(-3, 4) for y in range(-3, 4)}
        m = model(cells, probability=1.)
        state = m.initial_state({GridCell(0, 0).cell_id: 1.})
        counts = [1]
        for _ in range(20):
            previous_burned = set(state.burned_cell_ids)
            state = m.step(state, terrain_provider=lambda _: {}).state
            self.assertTrue(previous_burned.issubset(state.burned_cell_ids))
            self.assertTrue({c.cell_id for c in state.active_cells}.issubset(cells))
            counts.append(len(state.active_cells))
        self.assertGreater(max(counts), 9)
        self.assertTrue(any(b < a for a, b in zip(counts, counts[1:])))
        self.assertEqual(counts[-1], 0)
        self.assertEqual(set(state.burned_cell_ids), cells)
        self.assertFalse(m.step(state, terrain_provider=lambda _: {}).predictions)

    def test_frontiers_larger_than_5000_candidates_are_not_truncated(self):
        m = model(probability=.01)
        state = m.initial_state({GridCell(4 * x, 0).cell_id: 1. for x in range(650)})
        result = m.step(state, terrain_provider=lambda _: {})
        self.assertEqual(len(result.predictions), 5200)
        self.assertFalse(any(p.will_ignite for p in result.predictions))

    def test_thirty_percent_probability_does_not_ignite_every_candidate(self):
        m = model(probability=.3)
        state = m.initial_state({GridCell(4 * x, 0).cell_id: .7 for x in range(650)})
        result = m.step(state, terrain_provider=lambda _: {})
        fraction = sum(p.will_ignite for p in result.predictions) / len(result.predictions)
        self.assertAlmostEqual(fraction, .3, delta=.025)
        self.assertTrue(any(not p.will_ignite for p in result.predictions))
        self.assertEqual(result, m.step(state, terrain_provider=lambda _: {}))

    def test_fresh_fuel_burns_out_in_two_steps_even_at_low_intensity(self):
        m = model(probability=.01)
        seed = GridCell(0, 0).cell_id
        for intensity in (1., .7, .5, .2, .1, .05):
            with self.subTest(intensity=intensity):
                state = m.initial_state({seed: intensity})
                self.assertEqual(state.active_cells[0].remaining_active_steps, 2)
                state = m.step(state, terrain_provider=lambda _: {}).state
                self.assertEqual(state.active_cells[0].fuel_remaining, .5)
                self.assertEqual(state.active_cells[0].remaining_active_steps, 1)
                self.assertFalse(state.burned_cell_ids)
                state = m.step(state, terrain_provider=lambda _: {}).state
                self.assertFalse(state.active_cells)
                self.assertEqual(state.burned_cell_ids, (seed,))

    def test_new_ignitions_use_the_same_budget_and_do_not_reignite(self):
        seed, target = GridCell(0, 0).cell_id, GridCell(1, 0).cell_id
        m = model({seed, target}, probability=1.)
        state = m.initial_state({seed: .1})
        state = m.step(state, terrain_provider=lambda _: {}).state
        new = next(c for c in state.active_cells if c.cell_id == target)
        self.assertEqual((new.fuel_remaining, new.remaining_active_steps), (1., 2))
        state = m.step(state, terrain_provider=lambda _: {}).state
        self.assertEqual(state.burned_cell_ids, (seed,))
        self.assertEqual([c.cell_id for c in state.active_cells], [target])
        state = m.step(state, terrain_provider=lambda _: {}).state
        self.assertFalse(state.active_cells)
        self.assertEqual(set(state.burned_cell_ids), {seed, target})
        self.assertFalse(m.step(state, terrain_provider=lambda _: {}).predictions)

    def test_partial_fuel_expires_despite_a_legacy_long_countdown(self):
        m = model(probability=.01)
        state = m.initial_state({GridCell(0, 0).cell_id: .1})
        state = replace(state, active_cells=(replace(state.active_cells[0],
            fuel_remaining=.4, remaining_active_steps=19),))
        state = m.step(state, terrain_provider=lambda _: {}).state
        self.assertFalse(state.active_cells)
        self.assertEqual(len(state.burned_cell_ids), 1)

    def test_feature_rendering_never_refreshes_observed_or_synthetic_age(self):
        m = model(probability=.01)
        seed = GridCell(0, 0).cell_id
        synthetic = m.initial_state({seed: .7}).active_cells[0]
        observed = EvidenceCell(seed, .7, 2, detection_count=7,
            bright_ti4_max=380., bright_ti4_mean=360., platform_count=2)
        for original in (synthetic, observed):
            for age in (2.9, 3., 7.5, 19.5, 24., 24.1, 36.):
                with self.subTest(kind=type(original).__name__, age=age):
                    cell = replace(original, observation_age_hours=age)
                    row = m._feature_row(GridCell(1, 0), active_by_id={seed: cell},
                                         terrain_provider=lambda _: {})
                    eligible = 3 <= age <= 24
                    self.assertEqual(row['firms_local_3x3_has_detection'], float(eligible))
                    self.assertEqual(row['firms_local_3x3_hours_since_last_detection'],
                                     age if eligible else None)
                    if original is observed and eligible:
                        self.assertEqual(row['firms_local_3x3_bright_ti4_max'], 380.)
                        self.assertEqual(row['firms_local_3x3_detection_count'], 7.)
                    if not eligible:
                        self.assertIsNone(row['firms_local_3x3_bright_ti4_max'])
                    self.assertEqual(cell.observation_age_hours, age)

    def test_empty_fuel_and_zero_intensity_cannot_ignite_neighbors(self):
        m = model()
        state = m.initial_state({GridCell(0, 0).cell_id: 1.})
        for parameters in ({"fuel_remaining": 0.}, {"intensity": 0.}):
            dead = replace(state, active_cells=(replace(state.active_cells[0], **parameters),))
            result = m.step(dead, terrain_provider=lambda _: {})
            self.assertFalse(result.predictions)
            self.assertFalse(result.state.active_cells)

    def test_water_is_checked_on_seeding_and_submitted_active_state(self):
        land = GridCell(0, 0).cell_id
        water = GridCell(1, 0).cell_id
        m = model({land})
        with self.assertRaisesRegex(ValueError, "mapped land"):
            m.initial_state({water: 1.})
        state = model().initial_state({water: 1.})
        result = m.step(state, terrain_provider=lambda _: {})
        self.assertFalse(result.predictions)
        self.assertFalse(result.state.active_cells)
        self.assertNotIn(water, result.state.burned_cell_ids)

    def test_lake_winnipeg_shore_blocks_water_even_with_certain_ignition(self):
        m = FireSpreadModel.from_incident_model(model(probability=1.), Landscape())
        shore = GridCell(-169, 1532).cell_id
        # Mapped water/mixed shoreline immediately east and south of this
        # west-shore seed; the unblocked frontier lies inland to the northwest.
        blocked = {GridCell(-168, 1532).cell_id, GridCell(-169, 1531).cell_id,
                   GridCell(-168, 1531).cell_id}
        state = m.initial_state({shore: .7})
        for _ in range(8):
            result = m.step(state, terrain_provider=lambda _: {})
            state = result.state
            self.assertTrue(blocked.isdisjoint(p.cell_id for p in result.predictions))
            self.assertTrue(blocked.isdisjoint(c.cell_id for c in state.active_cells))
            self.assertTrue(blocked.isdisjoint(state.burned_cell_ids))
        self.assertGreater(len(state.active_cells), 1)
        self.assertIn(shore, state.burned_cell_ids)


if __name__ == "__main__":
    unittest.main()
