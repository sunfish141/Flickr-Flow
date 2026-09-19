"""Simple fuel-limited preview using the retained spread classifier.

Fuel is a vegetation-based duration proxy, not measured fuel mass. Historical
training/replay keeps its original transition; the map uses this transition.
"""

from dataclasses import asdict, replace
from functools import lru_cache
import math

from wildfire_data.core.grid import cell_from_id, cells_in_square_radius
from wildfire_data.core.hashing import stable_fraction
from wildfire_data.model.incident_transition import IncidentTransitionModel
from wildfire_data.model.fuel import FuelPolicy, with_fuel
from wildfire_data.model.recursive_transition import (
    ActiveFireCell, CellTransitionPrediction, RecursiveFireState, RecursiveStepResult,
)


SPREAD_VERSION = "water-barriers-vegetation-fuel/v4"
# Keep draws paired with v2 so changing fuel/evidence rules does not also
# change the random realization used to assess those rules.
IGNITION_SAMPLING_VERSION = "water-barriers-finite-fuel/v2"
MIN_INTENSITY = .05


class FireSpreadModel(IncidentTransitionModel):
    """No population quota: active count follows local ignition and burnout."""

    def __init__(self, *args, landscape, fuel_sampler=None, fuel_policy=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.landscape = landscape
        self.fuel_sampler = fuel_sampler
        self.fuel_policy = fuel_policy or FuelPolicy()
        self.fuel_estimate = lru_cache(maxsize=16384)(self._fuel_estimate)

    def _fuel_estimate(self, cell_id, origin_at):
        sample = (self.fuel_sampler.sample_cell(cell_id, cutoff_at=origin_at, simulation_at=origin_at)
                  if self.fuel_sampler is not None and origin_at is not None else None)
        return self.fuel_policy.estimate(sample)

    def prepare_cell(self, cell, origin_at):
        # Preserve the fuel assigned at ignition, including on stateless replay.
        if getattr(cell, 'burn_duration_hours', None) is not None:
            return cell
        estimate = self.fuel_estimate(cell.cell_id, origin_at)
        return with_fuel(cell, estimate) if estimate.burn_duration_hours > 1e-9 else None

    def prepare_state(self, state, origin_at):
        active = [self.prepare_cell(c, origin_at) for c in state.active_cells if self.landscape.allows_cell(c.cell_id)]
        return replace(state, active_cells=tuple(c for c in active if c is not None),
                       burned_cell_ids=tuple(c for c in state.burned_cell_ids if self.landscape.allows_cell(c)))

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
                "fuel_consumption_per_step": "12 / assigned burn_duration_hours",
                "minimum_intensity": MIN_INTENSITY,
                "ignition_policy": "probability-sampling; deterministic per cell and step",
                "ignition_sampling_version": IGNITION_SAMPLING_VERSION,
                "fuel_policy": {"kind": "vegetation-duration-proxy/v1", **asdict(self.fuel_policy)},
                "maximum_active_steps": math.ceil(max(self.fuel_policy.fallback_hours, *self.fuel_policy.hours_at_full_cover.values()) / 12),
                "observation_policy": "preserve-age; eligible-only-within-3-to-24-hours",
                "water_barrier": self.landscape.version}

    def initial_state(self, ignitions, *, origin_at=None, **kwargs):
        if any(not self.landscape.allows_cell(cell_id) for cell_id in ignitions):
            raise ValueError("Place fires on mapped land, away from lakes and the ocean.")
        state = super().initial_state(ignitions, **kwargs)
        state = replace(state, active_cells=tuple(c for c in state.active_cells if c.intensity >= MIN_INTENSITY))
        prepared = self.prepare_state(state, origin_at)
        if len(prepared.active_cells) != len(state.active_cells):
            raise ValueError('Place fires in supported vegetation; this cell has no mapped vegetated fuel.')
        return prepared

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
        state = self.prepare_state(state, origin_at)
        active = {c.cell_id: c for c in state.active_cells}
        burned = set(state.burned_cell_ids)
        for cell_id, cell in tuple(active.items()):
            if cell.intensity < MIN_INTENSITY or cell.fuel_remaining <= 1e-9:
                burned.add(cell_id)
                del active[cell_id]
        current = replace(state, active_cells=tuple(active.values()), burned_cell_ids=tuple(sorted(burned)))
        candidates = tuple(c for c in self.candidate_cells(current)
                           if self.fuel_estimate(c.cell_id, origin_at).burn_duration_hours > 1e-9)
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
                    ignitions.append(self.prepare_cell(ActiveFireCell(cell.cell_id, intensity,
                        2, self.new_ignition_age_hours), origin_at))
        survivors = []
        for cell in active.values():
            fuel = max(0., cell.fuel_remaining - 12 / cell.burn_duration_hours)
            if fuel <= 1e-9:
                burned.add(cell.cell_id)
            else:
                survivors.append(replace(cell, fuel_remaining=fuel, intensity=max(MIN_INTENSITY, min(cell.intensity, fuel)),
                    remaining_active_steps=math.ceil(fuel * cell.burn_duration_hours / 12 - 1e-9),
                    observation_age_hours=cell.observation_age_hours + 12))
        return RecursiveStepResult(SPREAD_VERSION, state.step_index,
            RecursiveFireState(state.step_index + 1,
                tuple(sorted(survivors + ignitions, key=lambda c: c.cell_id)), tuple(sorted(burned))),
            tuple(predictions))
