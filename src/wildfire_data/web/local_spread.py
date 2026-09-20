"""Explicit local scenario endpoints, isolated from the fitted 1 km model."""

from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Annotated

from fastapi import HTTPException
from pydantic import Field, model_validator
from shapely.geometry import mapping, Point
from shapely.ops import transform
from wildfire_data.core.grid import cell_from_id
from wildfire_data.model.features.fuel_barrier_features import FuelBarrierSampler
from wildfire_data.model.local_spread import LocalSpreadModel, TravelPolicy
from wildfire_data.web.schemas import Input, SeedInput, StepInput, simulation_time


class LocalSeedInput(SeedInput):
    region: str = Field(max_length=80)


class LocalStateInput(Input):
    region: str = Field(max_length=80)
    model_sha256: str = Field(pattern=r'^[0-9a-f]{64}$')
    incident_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    seed_ids: list[Annotated[int, Field(ge=0, le=60000, strict=True)]] = Field(min_length=1, max_length=500)
    step_index: int = Field(ge=0, strict=True)


class LocalStepInput(Input):
    state: LocalStateInput
    origin_at: datetime

    @model_validator(mode='after')
    def representable_time(self):
        self.origin_at = StepInput.aware(self.origin_at)
        simulation_time(self.origin_at, self.state.step_index + 1)
        return self


class LocalScenarios:
    def __init__(self, config_path):
        self.regions = {}
        self.region_metadata = {}
        self.default_region = None
        self.region_errors = []
        self.presets = []
        self.expanding = None
        self.expanding_error = None
        self.lock = Lock()
        if config_path and Path(config_path).exists():
            path = Path(config_path).resolve()
            config = json.loads(path.read_text(encoding='utf-8'))
            self.policy = TravelPolicy(**config['policy'])
            self.mesh_m = config.get('mesh_m', 30)
            for region in config['regions']:
                manifest = (path.parent / region['manifest']).resolve()
                if not manifest.exists():
                    self.region_errors.append(f"Prepared region unavailable: {region['label']}")
                    continue
                try:
                    sampler = FuelBarrierSampler(manifest, expected_sha256=region['sha256'])
                except (OSError, ValueError, KeyError):
                    self.region_errors.append(f"Prepared region failed verification: {region['label']}")
                    continue
                self.regions[region['id']] = (region['label'], sampler)
                self.region_metadata[region['id']] = {
                    'example_ignition': region.get('example_ignition'),
                    'coverage': self.coverage(sampler), 'area_km2': sampler.bounds.area / 1e6,
                    'sources': sampler.manifest.get('sources', []),
                    'unknown_width_count': sum(p.get('width_m') is None for _, p in sampler.roads),
                    'unknown_grade_count': sum(p.get('at_grade') is None for _, p in sampler.roads)}
            if config.get('default_region') in self.regions:
                self.default_region = config['default_region']
            elif config.get('default_region') and self.regions:
                self.default_region = next(iter(self.regions))
            if config.get('expanding', {}).get('enabled'):
                from wildfire_data.web.landscape_spread import ExpandingScenarios
                try:
                    self.expanding = ExpandingScenarios(config['expanding'], path, self)
                    # Regional map views use the national tile archive, not the
                    # fixed pilot bundles. Bounds frame the map; they do not clip fire.
                    self.presets = config['expanding'].get('presets', [])
                except (OSError, ValueError):
                    import logging
                    logging.getLogger(__name__).exception('Offline landscape archive is unavailable')
                    self.expanding_error = 'Offline road archive unavailable. Complete collection and verify the configured manifest.'
        self.model = lru_cache(maxsize=2)(self.model)
        self.display_layers = lru_cache(maxsize=2)(self.display_layers)

    def region_for_ignitions(self, ignitions):
        matches = []
        for identity, (_, sampler) in sorted(self.regions.items()):
            if all(sampler.bounds.covers(Point(*sampler.to_grid.transform(p.longitude, p.latitude))) for p in ignitions):
                matches.append(identity)
        if not matches:
            raise ValueError('Regional data is not installed for this location, or the ignitions span different packs. Choose an outlined installed region; reset before starting a different region.')
        return matches[0]

    def display_layers(self, region):
        if region not in self.regions:
            raise ValueError('Regional data is not installed')
        _, sampler = self.regions[region]
        def fc(items):
            return {'type': 'FeatureCollection', 'features': [
                {'type': 'Feature', 'geometry': mapping(transform(sampler.to_geo.transform, g)), 'properties': p}
                for g, p in items if not g.is_empty]}
        unknown = sampler.bounds.difference(sampler.cover_union)
        return {'id': region, 'digest': sampler.sha256, 'layers': {
            'cover': fc(sampler.cover), 'roads': fc(sampler.roads),
            'unknown': fc([(unknown, {'fuel': 'unknown'})])}}

    def model(self, region):
        if region not in self.regions:
            raise ValueError('Local landscape region is unavailable')
        return LocalSpreadModel(self.regions[region][1], self.policy, mesh_m=self.mesh_m)

    def close(self):
        if self.expanding:
            self.expanding.close()

    @staticmethod
    def coverage(sampler):
        return {'type': 'Feature', 'geometry': mapping(transform(sampler.to_geo.transform, sampler.bounds)),
                'properties': {'coverage': 'fixed prepared pack; outside unsupported'}}

    def configuration(self):
        return {'available': bool(self.regions) or bool(self.expanding), 'expanding': bool(self.expanding), 'kind': 'uncalibrated landscape scenario',
            'max_steps': None,
            'weather_ml': self.expanding.hybrid.configuration() if self.expanding and self.expanding.hybrid else {'available': False},
            'expanding_error': self.expanding_error, 'default_region': self.default_region,
            'region_errors': self.region_errors,
            'limits': self.expanding.configuration() if self.expanding else None,
            'preload': self.expanding.preload_status() if self.expanding else None,
            'presets': self.presets,
            'regions': self.expanding.store.regions() if getattr(getattr(self.expanding, 'store', None), 'regional', False) is True else [{'id': key, 'label': label, 'bounds': s.manifest['bounds_wgs84'],
                'road_count': len(s.roads), 'known_width_count': sum(p.get('width_m') is not None for _, p in s.roads),
                **self.region_metadata[key]}
                for key, (label, s) in self.regions.items()],
            'mesh_m': self.mesh_m if self.regions or self.expanding else None,
            'policy': __import__('dataclasses').asdict(self.policy) if self.regions or self.expanding else None}

    def response(self, region, model, seeds, step, origin, *, frame=None):
        origin = StepInput.aware(origin)
        valid_at = simulation_time(origin, step)
        if frame is None:
            frame = model.frame(seeds, step*720)
        incident = hashlib.sha256(f'{model.identity}:{tuple(seeds)}:{origin.isoformat()}'.encode()).hexdigest()
        points = []
        for cell_id, areas in sorted(frame['cells'].items()):
            cell = cell_from_id(cell_id)
            lat, lon = cell.center_wgs84
            west, south, east, north = cell.bounds_projected
            longitudes, latitudes = model.sampler.to_geo.transform(
                [west,east,east,west,west], [south,south,north,north,south])
            active = areas['active_area_m2'] > 0
            points.append({'cell_id': cell_id, 'latitude': lat, 'longitude': lon,
                'cell_geometry': {'type': 'Polygon', 'coordinates': [list(zip(longitudes,latitudes))]},
                'status': 'active' if active else 'burned', 'intensity': min(1., areas['active_area_m2']/1e6),
                'fuel_remaining': None, 'ignition_probability': None, 'new_ignition': False,
                'source': 'Local fuel-patch scenario', 'observation_age_hours': None,
                'detection_count': None, 'bright_ti4_max': None, 'remaining_active_steps': None,
                'landscape': model.sampler.sample_cell(cell_id), **areas})
        return {'local': True, 'state': {'region': region, 'model_sha256': model.identity,
                'incident_id': incident, 'seed_ids': seeds, 'step_index': step},
            'origin_at': origin.isoformat(), 'valid_at': valid_at.isoformat(), 'elapsed_hours': step*12,
            'points': points, 'perimeters': frame['perimeters'],
            'roads': model.roads_geojson,
            'coverage': self.coverage(model.sampler),
            'active_count': sum(p['status'] == 'active' for p in points),
            'burned_count': sum(p['burned_area_m2'] > 0 for p in points),
            'active_area_m2': sum(p['active_area_m2'] for p in points),
            'burned_area_m2': sum(p['burned_area_m2'] for p in points),
            'active_patch_count': frame['active_patch_count'], 'burned_patch_count': frame['burned_patch_count'],
            'new_ignition_count': None, 'terrain_missing_count': 0,
            'finished': frame['boundary_reached'],
            'extinct': not frame['future_arrivals'] and frame['active_patch_count'] == 0,
            'boundary_reached': frame['boundary_reached'], 'metadata': frame['assumptions']}


def register_local_routes(app):
    def run(operation):
        scenarios = app.state.local_scenarios
        if not scenarios.lock.acquire(blocking=False):
            raise HTTPException(503, 'Local simulation is busy. Try again shortly.')
        try:
            return operation(scenarios)
        except (ValueError, OverflowError) as exc:
            raise HTTPException(422, str(exc)) from None
        finally:
            scenarios.lock.release()

    @app.post('/api/local/seed')
    def seed(body: LocalSeedInput):
        def perform(s):
            model = s.model(body.region)
            seeds = model.seed_ids([(p.longitude, p.latitude) for p in body.ignitions])
            return s.response(body.region, model, seeds, 0, datetime.now(timezone.utc))
        return run(perform)

    @app.get('/api/local/regions/{region}/layers')
    def layers(region: str):
        try:
            return app.state.local_scenarios.display_layers(region)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None

    @app.post('/api/map/seed')
    def map_seed(body: SeedInput):
        def perform(s):
            if s.expanding and hasattr(s.expanding.store, 'region_for'):
                return s.expanding.initialize([(p.longitude, p.latitude) for p in body.ignitions], datetime.now(timezone.utc))
            region = s.region_for_ignitions(body.ignitions)
            model = s.model(region)
            seeds = model.seed_ids([(p.longitude, p.latitude) for p in body.ignitions])
            return s.response(region, model, seeds, 0, datetime.now(timezone.utc))
        return run(perform)

    @app.post('/api/local/step')
    def step(body: LocalStepInput):
        def perform(s):
            state = body.state
            model = s.model(state.region)
            if state.model_sha256 != model.identity:
                raise ValueError('Local model or landscape changed; start a new scenario')
            seeds = tuple(sorted(set(state.seed_ids)))
            origin = StepInput.aware(body.origin_at)
            incident = hashlib.sha256(f'{model.identity}:{seeds}:{origin.isoformat()}'.encode()).hexdigest()
            if state.incident_id != incident:
                raise ValueError('Local incident identity changed')
            if model.frame(seeds, state.step_index*720)['boundary_reached']:
                raise ValueError('Local simulation reached the boundary of collected evidence')
            return s.response(state.region, model, seeds, state.step_index+1, origin)
        return run(perform)
