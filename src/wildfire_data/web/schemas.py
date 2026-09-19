"""Validation and conversion of browser requests into model state."""

from datetime import date as Date, datetime, timedelta, timezone
import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wildfire_data.core.grid import cell_from_id
from wildfire_data.model.incident_transition import EvidenceCell
from wildfire_data.model.recursive_transition import ActiveFireCell, RecursiveFireState
from wildfire_data.model.fuel import FuelCell, FuelEvidenceCell


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class CellInput(Input):
    cell_id: str = Field(max_length=80)
    intensity: float = Field(ge=0, le=1)
    remaining_active_steps: int = Field(ge=1, le=100000, strict=True)
    observation_age_hours: float = Field(ge=0, le=100000000)
    fuel_remaining: float = Field(default=1., ge=0, le=1)
    burn_duration_hours: float | None = Field(default=None, gt=0, le=168)
    vegetation_fraction: float | None = Field(default=None, ge=0, le=1)
    fuel_basis: str | None = Field(default=None, max_length=80)
    detection_count: int | None = Field(default=None, ge=1, le=100000)
    bright_ti4_max: float | None = Field(default=None, ge=0, le=10000)
    bright_ti4_mean: float | None = Field(default=None, ge=0, le=10000)
    platform_count: int | None = Field(default=None, ge=1, le=3)

    @field_validator("cell_id")
    @classmethod
    def valid_cell(cls, value):
        cell = cell_from_id(value)
        if cell.cell_id != value or max(abs(cell.x_index), abs(cell.y_index)) > 20000:
            raise ValueError('cell_id must use canonical, bounded grid coordinates')
        if not all(math.isfinite(v) for v in cell.center_wgs84):
            raise ValueError('cell_id must have a finite geographic location')
        return value

    def to_cell(self):
        fields = self.model_dump(exclude_none=True)
        if self.burn_duration_hours is None and (self.vegetation_fraction is not None or self.fuel_basis is not None):
            raise ValueError('Fuel estimates require their assigned duration')
        evidence = [self.detection_count, self.bright_ti4_max, self.bright_ti4_mean, self.platform_count]
        if any(v is not None for v in evidence):
            if any(v is None for v in evidence):
                raise ValueError("FIRMS cells require all observation aggregates")
            return (FuelEvidenceCell if self.burn_duration_hours is not None else EvidenceCell)(**fields)
        return (FuelCell if self.burn_duration_hours is not None else ActiveFireCell)(**fields)


class StateInput(Input):
    step_index: int = Field(ge=0, le=7300000, strict=True)
    active_cells: list[CellInput] = Field(max_length=10000)
    burned_cell_ids: list[Annotated[str, Field(max_length=80)]] = Field(default_factory=list, max_length=100000)

    def to_state(self):
        for cell_id in self.burned_cell_ids:
            CellInput.valid_cell(cell_id)
        return RecursiveFireState(self.step_index, tuple(c.to_cell() for c in self.active_cells), tuple(self.burned_cell_ids))


class BoundsInput(Input):
    west: float = Field(ge=-179, le=-50)
    south: float = Field(ge=24, le=84)
    east: float = Field(ge=-179, le=-50)
    north: float = Field(ge=24, le=84)

    @model_validator(mode="after")
    def bounded(self):
        if self.east <= self.west or self.north <= self.south:
            raise ValueError("Bounds must have east > west and north > south")
        return self


class HistoricalBounds(BoundsInput):
    east: float = Field(ge=-179, le=-52)


class HistoricalFirmsInput(Input):
    date: Date = Field(ge=Date(2026, 5, 11), le=Date(2026, 8, 21))
    bounds: HistoricalBounds


class HistoricalContext(Input):
    start_date: Date = Field(ge=Date(2026, 5, 11), le=Date(2026, 8, 21))
    bounds: HistoricalBounds


class StepInput(Input):
    state: StateInput
    origin_at: datetime
    historical: HistoricalContext | None = None

    @model_validator(mode='after')
    def representable_time(self):
        try:
            self.origin_at + timedelta(hours=12 * (self.state.step_index + (2 if self.historical else 1)))
            if self.historical:
                expected = datetime.combine(self.historical.start_date + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
                if self.origin_at != expected or self.state.step_index % 2:
                    raise ValueError('Historical playback requires its original UTC day cutoff and whole-day steps')
                next_day = self.historical.start_date + timedelta(days=self.state.step_index // 2 + 1)
                if next_day > Date(2026, 8, 21):
                    raise ValueError('Historical playback ends on August 21, 2026')
        except OverflowError:
            raise ValueError('The next simulation time exceeds the supported calendar') from None
        return self

    @field_validator("origin_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("origin_at must include a timezone")
        try:
            return value.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            raise ValueError('The UTC time exceeds the supported calendar') from None


class IgnitionInput(Input):
    latitude: float = Field(ge=24, le=84)
    longitude: float = Field(ge=-179, le=-50)
    intensity: float = Field(ge=0, le=1)


class VegetationInput(Input):
    cell_id: str = Field(max_length=80)
    origin_at: datetime
    simulation_at: datetime

    @field_validator('cell_id')
    @classmethod
    def valid_cell(cls, value):
        return CellInput.valid_cell(value)

    @field_validator('origin_at', 'simulation_at')
    @classmethod
    def aware(cls, value):
        return StepInput.aware(value)

    @model_validator(mode='after')
    def ordered(self):
        if self.simulation_at < self.origin_at:
            raise ValueError('simulation_at cannot precede origin_at')
        return self


class SeedInput(Input):
    ignitions: list[IgnitionInput] = Field(min_length=1, max_length=500)
