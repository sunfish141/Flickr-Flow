"""Simple fuel-limited preview using the retained spread classifier.

Fuel is a dimensionless, uniform budget, not measured vegetation. Historical
training/replay keeps its original transition; the map uses this transition.
"""

from dataclasses import replace
import math

from wildfire_data.core.grid import cell_from_id, cells_in_square_radius
from wildfire_data.core.hashing import stable_fraction
from wildfire_data.model.incident_transition import IncidentTransitionModel
from wildfire_data.model.recursive_transition import (
    ActiveFireCell, CellTransitionPrediction, RecursiveFireState, RecursiveStepResult,
)


SPREAD_VERSION = "water-barriers-finite-fuel/v3"
# Keep draws paired with v2 so changing fuel/evidence rules does not also
# change the random realization used to assess those rules.
IGNITION_SAMPLING_VERSION = "water-barriers-finite-fuel/v2"
MIN_INTENSITY = .05
FUEL_CONSUMPTION = .5  # Uniform budget consumed per 12 h; intensity is not fuel mass.


class FireSpreadModel(IncidentTransitionModel):
    """No population quota: active count follows local ignition and burnout."""

    def __init__(self, *args, landscape, **kwargs):
        super().__init__(*args, **kwargs)
        self.landscape = landscape

    @classmethod
    def from_incident_model(cls, model, landscape):
        return cls(model.estimator, feature_columns=model.feature_columns,
                   ignition_threshold=model.ignition_threshold,
                   observation_calibration=model.observation_calibration,
                   vegetation_sampler=model.vegetation_sampler,
                   landscape=landscape)

    def transition_contract(self):
        return {"transition_version": SPREAD_VERSION, "time_step_hours": 12,
                "ignition_threshold": self.ignition_threshold,
                "fuel_consumption_per_step": FUEL_CONSUMPTION,
                "minimum_intensity": MIN_INTENSITY,
                "ignition_policy": "probability-sampling; deterministic per cell and step",
                "ignition_sampling_version": IGNITION_SAMPLING_VERSION,
                "fuel_policy": "uniform-unit-budget; constant-consumption; heuristic",
                "maximum_active_steps": math.ceil(1 / FUEL_CONSUMPTION),
                "observation_policy": "preserve-age; eligible-only-within-3-to-24-hours",
                "water_barrier": self.landscape.version}

    def initial_state(self, ignitions, **kwargs):
        if any(not self.landscape.allows_cell(cell_id) for cell_id in ignitions):
            raise ValueError("Place fires on mapped land, away from lakes and the ocean.")
        state = super().initial_state(ignitions, **kwargs)
        return replace(state, active_cells=tuple(
            replace(c, remaining_active_steps=math.ceil(c.fuel_remaining / FUEL_CONSUMPTION))
            for c in state.active_cells if c.intensity >= MIN_INTENSITY))

    def _neighbors(self, cell, active_by_id):
        return [active_by_id[n.cell_id] for n in cells_in_square_radius(cell, radius_cells=1)
                if n.cell_id in active_by_id
                and self.landscape.allows_spread(n.cell_id, cell.cell_id)]

    def candidate_cells(self, state):
        excluded = {c.cell_id for c in state.active_cells} | set(state.burned_cell_ids)
        candidates = {}
        for active in state.active_cells:
            for cell in cells_in_square_radius(cell_from_id(active.cell_id), radius_cells=1):
                if (cell.cell_id not in excluded
                        and self.landscape.allows_spread(active.cell_id, cell.cell_id)):
                    candidates[cell.cell_id] = cell
        return tuple(candidates[key] for key in sorted(candidates))

    def _feature_row(self, cell, *, active_by_id, terrain_provider):
        # The parent renderer applies the observation lookback to both observed
        # and synthetic cells. Activity must not renew their evidence age.
        nearby = {active.cell_id: active for active in self._neighbors(cell, active_by_id)}
        return super()._feature_row(cell, active_by_id=nearby, terrain_provider=terrain_provider)

    def step(self, state, *, terrain_provider, origin_at=None):
        # Also enforce barriers on submitted state, not only on new candidates.
        active = {c.cell_id: c for c in state.active_cells
                  if self.landscape.allows_cell(c.cell_id)}
        burned = set(state.burned_cell_ids)
        for cell_id, cell in tuple(active.items()):
            if cell.intensity < MIN_INTENSITY or cell.fuel_remaining < MIN_INTENSITY:
                burned.add(cell_id)
                del active[cell_id]
        current = replace(state, active_cells=tuple(active.values()), burned_cell_ids=tuple(sorted(burned)))
        candidates = self.candidate_cells(current)
        predictions, ignitions = [], []
        # Batch inference without pruning the frontier or imposing a birth quota.
        for start in range(0, len(candidates), 1024):
            batch = candidates[start:start + 1024]
            rows = [self._complete_feature_row(c.cell_id,
                    self._feature_row(c, active_by_id=active, terrain_provider=terrain_provider),
                    origin_at=origin_at, step_index=state.step_index) for c in batch]
            probabilities = self._probabilities(rows)
            for cell, probability in zip(batch, probabilities, strict=True):
                # A 30% score is a chance of ignition, not a command to ignite.
                # Stable draws make retries and saved-state replay identical.
                draw = stable_fraction(f"{IGNITION_SAMPLING_VERSION}:{state.step_index}:{cell.cell_id}")
                will_ignite = bool(probability >= self.ignition_threshold and draw < probability)
                # Fresh fuel sustains ignition; intensity is not multiplied by
                # 0.85 down every generation of the fire front.
                intensity = max(c.intensity for c in self._neighbors(cell, active)) if will_ignite else 0.
                lat, lon = cell.center_wgs84
                predictions.append(CellTransitionPrediction(cell.cell_id, lat, lon,
                    float(probability), will_ignite, intensity))
                if will_ignite:
                    ignitions.append(ActiveFireCell(cell.cell_id, intensity,
                        math.ceil(1 / FUEL_CONSUMPTION), self.new_ignition_age_hours))
        survivors = []
        for cell in active.values():
            # A fading brightness proxy must not slow depletion and extend its
            # own lifetime. Until residence time is learned, use a two-step
            # uniform budget, matching the retained model's default duration.
            fuel = max(0., cell.fuel_remaining - FUEL_CONSUMPTION)
            intensity = min(cell.intensity, fuel)
            if intensity < MIN_INTENSITY:
                burned.add(cell.cell_id)
            else:
                survivors.append(replace(cell, fuel_remaining=fuel, intensity=intensity,
                    remaining_active_steps=math.ceil(fuel / FUEL_CONSUMPTION),
                    observation_age_hours=cell.observation_age_hours + 12))
        return RecursiveStepResult(SPREAD_VERSION, state.step_index,
            RecursiveFireState(state.step_index + 1,
                tuple(sorted(survivors + ignitions, key=lambda c: c.cell_id)), tuple(sorted(burned))),
            tuple(predictions))
