"""Explicit vegetation-based fuel-duration proxies, not measured fuel mass."""
from dataclasses import asdict, dataclass, field
import math

from wildfire_data.model.incident_transition import EvidenceCell
from wildfire_data.model.recursive_transition import ActiveFireCell

FUEL_HOURS = {'needleleaf': 72., 'broadleaf': 48., 'mixed_forest': 60.,
              'shrubland': 24., 'grassland': 12., 'wetland': 24.,
              'cropland': 12., 'other_vegetation': 24.}
FUEL_BASES = ('land cover', 'land cover and canopy density', 'canopy density',
              'missing vegetation fallback')


@dataclass(frozen=True)
class FuelEstimate:
    burn_duration_hours: float
    vegetation_fraction: float | None
    fuel_basis: str


@dataclass(frozen=True)
class FuelPolicy:
    hours_at_full_cover: dict = field(default_factory=lambda: dict(FUEL_HOURS))
    fallback_hours: float = 24.

    def __post_init__(self):
        if set(self.hours_at_full_cover) != set(FUEL_HOURS):
            raise ValueError('Supply a fuel duration for every vegetation class')
        if any(not math.isfinite(v) or not 1 <= v <= 168
               for v in [self.fallback_hours, *self.hours_at_full_cover.values()]):
            raise ValueError('Fuel durations must be between 1 and 168 hours')

    def estimate(self, sample=None):
        sample = sample or {}
        land = sample.get('vegetation_land_cover_missing', 1) == 0
        canopy = sample.get('vegetation_cover_missing', 1) == 0
        density = min(1., sample['vegetation_tree_cover'] + sample['vegetation_non_tree_cover']) if canopy else None
        if land:
            fractions = {k: sample['vegetation_land_cover_' + k] for k in self.hours_at_full_cover}
            vegetated = min(1., sum(fractions.values()))
            hours = sum(fractions[k] * v for k, v in self.hours_at_full_cover.items())
            if density is not None and vegetated > 0:
                hours *= min(1., density / vegetated)
                vegetated = min(vegetated, density)
            # Unmapped area is unknown, not an observation of bare ground.
            hours += max(0., 1 - sample['vegetation_land_cover_valid_fraction']) * self.fallback_hours
            return FuelEstimate(hours, vegetated, 'land cover and canopy density' if canopy else 'land cover')
        if canopy:
            hours = (sample['vegetation_tree_cover'] * self.hours_at_full_cover['mixed_forest']
                     + sample['vegetation_non_tree_cover'] * self.hours_at_full_cover['grassland'])
            return FuelEstimate(hours, density, 'canopy density')
        return FuelEstimate(self.fallback_hours, None, 'missing vegetation fallback')


class FuelState:
    def validate_fuel(self):
        if not math.isfinite(self.burn_duration_hours) or not 0 < self.burn_duration_hours <= 168:
            raise ValueError('Invalid fuel duration')
        if self.vegetation_fraction is not None and not 0 <= self.vegetation_fraction <= 1:
            raise ValueError('Invalid vegetation fraction')
        if self.fuel_basis not in FUEL_BASES:
            raise ValueError('Unknown fuel estimate basis')


@dataclass(frozen=True)
class FuelCell(ActiveFireCell, FuelState):
    burn_duration_hours: float = field(default=24., kw_only=True)
    vegetation_fraction: float | None = field(default=None, kw_only=True)
    fuel_basis: str = field(default='missing vegetation fallback', kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        self.validate_fuel()


@dataclass(frozen=True)
class FuelEvidenceCell(EvidenceCell, FuelState):
    burn_duration_hours: float = field(default=24., kw_only=True)
    vegetation_fraction: float | None = field(default=None, kw_only=True)
    fuel_basis: str = field(default='missing vegetation fallback', kw_only=True)

    def __post_init__(self):
        super().__post_init__()
        self.validate_fuel()


def with_fuel(cell, estimate):
    cls = FuelEvidenceCell if isinstance(cell, EvidenceCell) else FuelCell
    values = {**asdict(cell), **asdict(estimate)}
    values['remaining_active_steps'] = max(1, math.ceil(cell.fuel_remaining * estimate.burn_duration_hours / 12 - 1e-9))
    return cls(**values)
