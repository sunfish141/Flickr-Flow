"""Strict, portable JSON contracts; no executable serialization or file references."""
from datetime import datetime, timezone
from importlib.metadata import version
import hashlib
import json
import math
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from wildfire_data.model.local_spread import LOCAL_VERSION, TravelPolicy, VEGETATED
from wildfire_data.planning import VERSION

LIMITATIONS = [
    'RESEARCH ONLY — uncalibrated sensitivity model, not an operational forecast.',
    'Ignitions and constant wind are hypothetical inputs, not observed fire or issued weather.',
    'Historical land cover can be stale; source dates do not describe current fuel moisture.',
    'Urban mixtures, structures, unknown cover and unresolved road attributes are unsupported, not safe.',
    'Spread stops at pack boundaries; areas outside coverage are unsupported.',
    'Flat terrain and assumed spread rates; general ember crossing and structure ignition are not modeled.',
    'Differences are scenario sensitivity, not protection effectiveness or probabilities.',
]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def engine_identity():
    # A behavior change MUST bump VERSION or LOCAL_VERSION. Dependency changes
    # also block continuation; saved frames remain usable across platforms.
    import shapely
    import pyproj
    return ':'.join([VERSION, LOCAL_VERSION, *[f'{p}={version(p)}' for p in ('shapely', 'pyproj', 'numpy')],
                     f'GEOS={shapely.geos_version_string}', f'PROJ={pyproj.proj_version_str}'])


def wind_components(speed, from_degrees):
    angle = math.radians(from_degrees)
    return (-speed * math.sin(angle), -speed * math.cos(angle))


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Ignition(Strict):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-85, le=85)


class Wind(Strict):
    speed_m_s: float = Field(default=0, ge=0, le=60)
    from_degrees: float = Field(default=0, ge=0, lt=360)


class Policy(Strict):
    rates_m_min: dict[str, float] = Field(default_factory=lambda: dict(zip(VEGETATED, [.5, .5, .5, 1., 2., 1., .25, 1.])))
    residence_minutes: float = 120
    wind_coefficient: float = .08
    unknown_road_width_m: float = 6
    spotting_distance_per_wind_m_s: float = 0
    max_spotting_distance_m: float = 0

    @model_validator(mode='after')
    def valid_policy(self):
        self.travel(Wind())
        # V1 intentionally does not offer ember/treatment experimentation.
        if self.spotting_distance_per_wind_m_s or self.max_spotting_distance_m:
            raise ValueError('V1 supports no spotting; general ember crossing is unmodeled')
        return self

    def travel(self, wind):
        east, north = wind_components(wind.speed_m_s, wind.from_degrees)
        return TravelPolicy(**self.model_dump(), wind_east_m_s=east, wind_north_m_s=north)


class Definition(Strict):
    pack_id: str = Field(pattern=r'^[a-z0-9][a-z0-9-]{0,79}$')
    pack_digest: str = Field(pattern=r'^[a-f0-9]{64}$')
    engine_version: str = Field(min_length=1, max_length=300)
    origin_time: datetime
    ignitions: list[Ignition] = Field(default_factory=list, max_length=50)
    wind: Wind = Field(default_factory=Wind)
    policy: Policy = Field(default_factory=Policy)
    mesh_m: Literal[100] = 100
    horizon_hours: int = Field(default=24, ge=1, le=96)
    output_interval_minutes: Literal[60] = 60

    @field_validator('origin_time')
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None:
            raise ValueError('Origin time requires an explicit timezone')
        return value.astimezone(timezone.utc)


class Mutation(Strict):
    request_id: UUID
    expected_revision: int = Field(ge=1)


class Create(Strict):
    request_id: UUID
    name: str = Field(min_length=1, max_length=120)
    definition: Definition


class Update(Mutation):
    name: str = Field(min_length=1, max_length=120)
    definition: Definition


class Clone(Mutation):
    name: str = Field(min_length=1, max_length=120)


class Cursor(Mutation):
    playback_hour: int = Field(ge=0, le=96)


class Frame(Strict):
    hour: int = Field(ge=0, le=96)
    result: dict

    @field_validator('result')
    @classmethod
    def result_geometry(cls, value):
        from shapely.errors import ShapelyError
        try:
            return cls.checked_result(value)
        except (KeyError, TypeError, AttributeError, ShapelyError) as exc:
            raise ValueError('Malformed saved frame, policy or geometry') from exc

    @staticmethod
    def checked_result(value):
        from shapely.geometry import shape
        if set(value) != {'perimeters', 'cells', 'active_patch_count', 'burned_patch_count',
                          'boundary_reached', 'elapsed_minutes', 'future_arrivals', 'assumptions'}:
            raise ValueError('Unexpected frame fields')
        fc = value['perimeters']
        if set(fc) != {'type', 'features'} or fc['type'] != 'FeatureCollection' or len(fc['features']) > 2:
            raise ValueError('Invalid perimeter collection')
        statuses = set()
        for f in fc['features']:
            if set(f) != {'type', 'geometry', 'properties'} or f['type'] != 'Feature':
                raise ValueError('Invalid perimeter feature')
            g = shape(f['geometry'])
            if g.geom_type not in ('Polygon', 'MultiPolygon') or not g.is_valid or g.is_empty:
                raise ValueError('Invalid perimeter geometry')
            w, s, e, n = g.bounds
            if not -180 <= w <= e <= 180 or not -85 <= s <= n <= 85:
                raise ValueError('Perimeter coordinates out of range')
            if f['properties'].get('status') not in ('active', 'burned'):
                raise ValueError('Invalid perimeter status')
            if f['properties']['status'] in statuses:
                raise ValueError('Duplicate perimeter status')
            statuses.add(f['properties']['status'])
            area = f['properties'].get('area_m2')
            if not isinstance(area, (float, int)) or not math.isfinite(area) or area < 0:
                raise ValueError('Invalid perimeter area')
        for key in ('boundary_reached', 'future_arrivals'):
            if type(value[key]) is not bool:
                raise ValueError('Frame flags must be booleans')
        for key in ('active_patch_count', 'burned_patch_count'):
            if type(value[key]) is not int or not 0 <= value[key] <= 60000:
                raise ValueError('Invalid patch count')
        if not isinstance(value['cells'], dict) or len(value['cells']) > 10000:
            raise ValueError('Invalid frame cells')
        assumptions = value['assumptions']
        if not isinstance(assumptions, dict) or set(assumptions) != {'kind', 'policy', 'mesh_m', 'landscape_sha256', 'unsupported', 'weather_mode', 'terrain_mode'}:
            raise ValueError('Invalid assumptions snapshot')
        TravelPolicy(**assumptions['policy'])
        canonical(value)  # rejects nonfinite numbers even in nested unknown properties
        return value


class Portable(Strict):
    schema_version: Literal[1] = 1
    kind: Literal['wildfire-planning-scenario'] = 'wildfire-planning-scenario'
    id: UUID
    revision: int = Field(ge=1)
    parent_id: UUID | None = None
    name: str = Field(min_length=1, max_length=120)
    definition: Definition
    pack_snapshot: dict
    frames: list[Frame] = Field(default_factory=list, max_length=97)
    playback_hour: int = Field(ge=0, le=96)
    created_at: datetime
    exported_at: datetime
    limitations: list[str] = Field(max_length=30)

    @model_validator(mode='before')
    @classmethod
    def no_executable_references(cls, value):
        def inspect(item, depth=0):
            if depth > 40:
                raise ValueError('Imported document nesting is too deep')
            if isinstance(item, dict):
                for key, child in item.items():
                    if key.lower() in ('model_path', 'file_path', 'filesystem_path', 'pickle', 'joblib', 'api_key', 'access_token', 'password', 'credentials'):
                        raise ValueError('Executable/filesystem/credential references are not part of a portable scenario')
                    inspect(child, depth+1)
            elif isinstance(item, list):
                for child in item:
                    inspect(child, depth+1)
        inspect(value)
        return value

    @model_validator(mode='after')
    def ordered_frames(self):
        if [f.hour for f in self.frames] != list(range(len(self.frames))):
            raise ValueError('Frames must form an uninterrupted checkpoint sequence from hour zero')
        if len(self.frames) > self.definition.horizon_hours + 1 or self.playback_hour > max(0, len(self.frames)-1):
            raise ValueError('Checkpoint exceeds horizon or playback exceeds saved frames')
        for frame in self.frames:
            if frame.result['elapsed_minutes'] != frame.hour * 60:
                raise ValueError('Frame time does not match checkpoint')
            assumptions = frame.result['assumptions']
            if assumptions['mesh_m'] != self.definition.mesh_m or assumptions['landscape_sha256'] != self.definition.pack_digest:
                raise ValueError('Frame assumptions differ from scenario identity')
            from dataclasses import asdict
            expected_policy = asdict(self.definition.policy.travel(self.definition.wind))
            # Normalize new optional fields without modifying the saved
            # snapshot. v3 exports omitted residence_minutes_by_fuel; v5
            # explicitly stores None for the same uniform-residence policy.
            actual_policy = asdict(TravelPolicy(**assumptions['policy']))
            # Geographic trig implementations can differ by a final bit across
            # OS libraries; do not reject otherwise portable saved results.
            if set(actual_policy) != set(expected_policy) or any(
                actual_policy[k] != v if not isinstance(v, (int, float)) else not math.isclose(actual_policy[k], v, rel_tol=0, abs_tol=1e-12)
                for k, v in expected_policy.items()):
                raise ValueError('Frame policy differs from definition')
            bounds = self.pack_snapshot.get('bounds_wgs84')
            if not isinstance(bounds, list) or len(bounds) != 4 or not all(type(v) in (int, float) and math.isfinite(v) for v in bounds):
                raise ValueError('Invalid saved coverage bounds')
            w, s, e, n = bounds
            from shapely.geometry import shape
            for feature in frame.result['perimeters']['features']:
                fw, fs, fe, fn = shape(feature['geometry']).bounds
                if not w-1e-6 <= fw <= fe <= e+1e-6 or not s-1e-6 <= fs <= fn <= n+1e-6:
                    raise ValueError('Saved perimeter extends outside pack coverage')
        return self
