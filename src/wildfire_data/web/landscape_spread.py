"""Expanding landscape playback for placed and satellite-seeded scenarios."""
from datetime import datetime, timedelta, timezone
from collections import OrderedDict
import hashlib
import json
import math
from pathlib import Path

from fastapi import HTTPException
from pydantic import Field
from pyproj import Transformer
from shapely.geometry import box, mapping
from shapely.ops import transform

from wildfire_data.core.grid import TRAINING_GRID_CRS
from wildfire_data.model.local_spread import LocalSpreadModel, LOCAL_VERSION
from wildfire_data.model.features.fuel_barrier_features import projected_wind
from wildfire_data.providers.landscape.tiles import LandscapeTiles, LandscapeMosaic, TILE_METRES, tile_key, tile_bounds
from wildfire_data.web.schemas import Input, SeedInput, BoundsInput, HistoricalFirmsInput, HistoricalContext, StepInput
from wildfire_data.web.historical_firms import day_cutoff, LAST_DAY


MAX_REQUEST_TILES = 512


class LandscapeLimits(Input):
    max_tiles: int = Field(default=128, ge=1, le=MAX_REQUEST_TILES, strict=True)
    max_patches: int = Field(default=1500000, ge=1, le=5000000, strict=True)


class TileInput(Input):
    x: int = Field(ge=-4000, le=4000, strict=True)
    y: int = Field(ge=-4000, le=4000, strict=True)
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


class GeographicSeed(Input):
    longitude: float = Field(ge=-179, le=-50)
    latitude: float = Field(ge=24, le=84)


class ExpandingState(Input):
    profile: str = Field(pattern=r'^[0-9a-f]{64}$')
    incident_id: str = Field(pattern=r'^[0-9a-f]{64}$')
    ignitions: list[GeographicSeed] = Field(min_length=1, max_length=500)
    tiles: list[TileInput] = Field(min_length=1, max_length=MAX_REQUEST_TILES)
    step_index: int = Field(ge=0, le=8, strict=True)


class ExpandingStep(Input):
    state: ExpandingState
    origin_at: datetime
    historical: HistoricalContext | None = None


class ExpandingScenarios:
    def __init__(self, config, config_path, local):
        self.local = local
        self.limits = LandscapeLimits(**{key: config[key] for key in LandscapeLimits.model_fields if key in config})
        parent = Path(config_path).resolve().parent
        archive = parent/config['road_archive'] if config.get('road_archive') else None
        self.store = LandscapeTiles(parent/config['source_config'], parent/config['data_root'], config['release'],
            road_archive=archive, road_archive_sha256=config.get('road_archive_sha256'),
            raster_cache=parent/config['raster_cache'] if config.get('raster_cache') else None,
            max_cached_tiles=max(48, self.limits.max_tiles))
        self.profile = hashlib.sha256(f'expanding/v2:{LOCAL_VERSION}:{self.store.identity}:{local.policy.identity}:{local.mesh_m}'.encode()).hexdigest()
        self.to_grid = Transformer.from_crs('EPSG:4326', TRAINING_GRID_CRS, always_xy=True)
        self.tile_models, self.models = OrderedDict(), OrderedDict()
        self.prewarm_tiles = min(24, max(0, int(config.get('prewarm_tiles', 0))))

    def configuration(self):
        return {**self.limits.model_dump(), 'max_area_km2': self.limits.max_tiles * (TILE_METRES / 1000)**2}

    @staticmethod
    def retain(cache, key, model, *, max_entries, max_patches):
        cache[key] = model
        cache.move_to_end(key)
        while len(cache) > max_entries or sum(len(m.patches) for m in cache.values()) > max_patches:
            cache.popitem(last=False)

    def tile_model(self, sampler):
        key = sampler.sha256
        if key not in self.tile_models:
            model = LocalSpreadModel(sampler, self.local.policy, mesh_m=self.local.mesh_m,
                                     max_patches=min(250000, self.limits.max_patches), allow_empty=True)
            self.retain(self.tile_models, key, model, max_entries=max(48, self.limits.max_tiles),
                        max_patches=self.limits.max_patches)
            return model
        self.tile_models.move_to_end(key)
        return self.tile_models[key]

    def model(self, samplers):
        ordered = [samplers[k] for k in sorted(samplers)]
        key = tuple(s.sha256 for s in ordered)
        if key not in self.models:
            components, patches = [], 0
            for sampler in ordered:
                component = self.tile_model(sampler)
                patches += len(component.patches)
                if patches > self.limits.max_patches:
                    raise ValueError(f'Landscape scenario reached its {self.limits.max_patches:,} fuel-patch budget. '
                                     'Use a smaller area or increase expanding.max_patches on a server with sufficient memory; the previous frame is retained.')
                components.append(component)
            model = LocalSpreadModel.join(LandscapeMosaic(ordered), self.local.policy,
                components, mesh_m=self.local.mesh_m, max_patches=self.limits.max_patches)
            self.retain(self.models, key, model, max_entries=8, max_patches=2*self.limits.max_patches)
            return model
        self.models.move_to_end(key)
        return self.models[key]

    def warm(self, presets):
        """Prepare examples and recent retained tiles before accepting requests."""
        if not self.prewarm_tiles:
            return
        import logging
        logger = logging.getLogger('uvicorn.error')
        keys = []
        for preset in presets:
            point = preset.get('example_ignition')
            if point:
                keys.append(tile_key(*self.to_grid.transform(point['longitude'], point['latitude'])))
        directory = self.store.data_root/'landscape/tiles'/self.store.identity
        for path in sorted(directory.glob('*_*/manifest.json'), key=lambda p: p.stat().st_mtime_ns, reverse=True):
            x, y = path.parent.name.split('_')
            key = (int(x), int(y))
            if key not in keys:
                keys.append(key)
        logger.info('Warming %d landscape tiles in memory', min(len(keys), self.prewarm_tiles))
        for key in reversed(keys[:self.prewarm_tiles]):
            try:
                self.tile_model(self.store.load(key))
            except (ValueError, OSError):
                logger.exception('Could not prewarm landscape tile %s', key)
        logger.info('Prepared %d landscape tile graphs in memory', len(self.tile_models))

    def close(self):
        for model in [*self.models.values(), *self.tile_models.values()]:
            model.arrivals.cache_clear()
        self.models.clear()
        self.tile_models.clear()
        self.store.close()

    def check_tile_budget(self, count):
        if count > self.limits.max_tiles:
            area = self.configuration()['max_area_km2']
            raise ValueError(f'Landscape scenario reached its {self.limits.max_tiles}-tile ({area:,.0f} km²) budget. '
                             'Use a smaller area or increase expanding.max_tiles on a server with sufficient memory; the previous frame is retained.')

    def load(self, keys, samplers):
        keys = set(keys)-samplers.keys()
        self.check_tile_budget(len(keys)+len(samplers))
        for key in sorted(keys):
            samplers[key] = self.store.load(key)

    def initialize(self, coordinates, origin, *, satellite=False):
        if satellite and not coordinates:
            raise ValueError('No eligible FIRMS fire cells were found in this area. Try another map area or date; starting observations must be 3–24 hours old.')
        if not 1 <= len(coordinates) <= 500:
            raise ValueError(f'This request contains {len(coordinates)} starting fire cells; detailed landscapes support at most 500. Zoom in and load a smaller visible map area, or use the Existing 1 km model for broad FIRMS coverage.')
        samplers = {}
        self.load([tile_key(*self.to_grid.transform(*point)) for point in coordinates], samplers)
        model = self.model(samplers)
        unmatched = 0
        if satellite:
            # One supported patch per observed 1 km cell, not ignition of every
            # fine element in an uncertain satellite footprint.
            representatives = model.satellite_representatives
            mapped = []
            for lon, lat in coordinates:
                x, y = self.to_grid.transform(lon, lat)
                from wildfire_data.core.grid import GridCell
                cell_id = GridCell(math.floor(x/1000), math.floor(y/1000)).cell_id
                if cell_id not in representatives:
                    unmatched += 1
                    continue
                i = representatives[cell_id][1]
                mapped.append(model.sampler.to_geo.transform(*model.centers[i].coords[0]))
            coordinates = mapped
            if not coordinates:
                raise ValueError('No observed cells contain supported vegetation; satellite observations cannot seed this landscape model')
        result = self.frame(coordinates, samplers, 0, origin)
        if satellite:
            result['metadata'].update(satellite_seed_policy='one nearest-center supported vegetation patch per observed 1 km cell; location within the cell is a scenario assumption',
                                      unsupported_observed_cells=unmatched, mapped_starting_cells=len(coordinates))
        return result

    def frame(self, coordinates, samplers, step, origin):
        # Recompute from the same geographic ignitions when evidence grows.
        # Aligned tiles preserve existing fuel geometry, arrival paths and burns.
        while True:
            model = self.model(samplers)
            lon, lat = sorted(coordinates)[0]
            wind = projected_wind(model.sampler.to_grid, (lat, lon), self.local.policy.wind_east_m_s, self.local.policy.wind_north_m_s)
            if wind != model.wind:
                model.wind = wind
                model.arrivals.cache_clear()
            seeds = model.seed_ids(coordinates)
            frame = model.frame(seeds, step*720)
            keys = set()
            reach = self.local.policy.max_spotting_distance_m if self.local.policy.spotting_distance_per_wind_m_s else 0
            if frame['boundary_reached'] or reach:
                for i, arrival in model.arrivals(seeds).items():
                    if arrival > step*720:
                        continue
                    g = model.patches[i].geometry
                    if g.distance(model.sampler.bounds.boundary) > reach + 1e-6:
                        continue
                    key = tile_key(*model.centers[i].coords[0])
                    for dx, dy in ((-1,0),(1,0),(0,-1),(0,1),(-1,-1),(-1,1),(1,-1),(1,1)):
                        other = (key[0]+dx,key[1]+dy)
                        if other not in samplers and g.distance(box(*tile_bounds(other))) <= reach + 1e-6:
                            keys.add(other)
            if not keys:
                break
            self.load(keys, samplers)
        result = self.local.response('auto', model, seeds, step, origin)
        ignition_dicts = [{'longitude': lon, 'latitude': lat} for lon, lat in sorted(set(coordinates))]
        incident = self.incident(ignition_dicts, origin)
        result['state'] = {'profile': self.profile, 'incident_id': incident, 'ignitions': ignition_dicts,
            'tiles': [{'x': k[0], 'y': k[1], 'sha256': samplers[k].sha256} for k in sorted(samplers)], 'step_index': step}
        result.update(expanding=True, finished=step >= 8 or result['extinct'], boundary_reached=False,
            coverage={'type': 'Feature', 'geometry': mapping(transform(model.sampler.to_geo.transform, model.sampler.bounds)),
                      'properties': {'tiles': len(samplers)}})
        result['metadata'].update(tile_count=len(samplers), limits=self.configuration(), coverage_mode='expands from offline road archive and retained NALCMS; no road network requests',
                                  landscape_time_basis='current retained landscape, including for historical satellite scenarios')
        return result

    def incident(self, ignitions, origin):
        return hashlib.sha256(json.dumps([self.profile, ignitions, origin.isoformat()], sort_keys=True).encode()).hexdigest()

    def advance(self, body):
        state = body.state
        origin = StepInput.aware(body.origin_at)
        if state.profile != self.profile or state.incident_id != self.incident([p.model_dump() for p in state.ignitions], origin):
            raise ValueError('Landscape scenario identity changed; start a new scenario')
        if state.step_index >= 8:
            raise ValueError('Landscape simulation ends at 96 hours')
        if len({(t.x,t.y) for t in state.tiles}) != len(state.tiles):
            raise ValueError('Duplicate landscape tile state')
        self.check_tile_budget(len(state.tiles))
        samplers = {(t.x,t.y): self.store.load((t.x,t.y), expected=t.sha256) for t in state.tiles}
        if body.historical and (origin != day_cutoff(body.historical.start_date) or state.step_index % 2):
            raise ValueError('Historical landscape origin or step changed')
        step = state.step_index + (2 if body.historical else 1)
        return self.frame([(p.longitude,p.latitude) for p in state.ignitions], samplers, step, origin)


def register_expanding_routes(app, firms, historical_firms, historical_day):
    def run(operation):
        local = app.state.local_scenarios
        if not getattr(local, 'expanding', None):
            raise HTTPException(503, 'Expanding landscape data is unavailable on this server')
        if not local.lock.acquire(blocking=False):
            raise HTTPException(503, 'Landscape preparation is finishing another request. Waiting to retry.',
                                headers={'Retry-After': '2'})
        try:
            return operation(local.expanding)
        except (ValueError, OverflowError) as exc:
            import logging
            logging.getLogger(__name__).warning('Landscape request rejected: %s', exc)
            raise HTTPException(422, str(exc)) from None
        except HTTPException:
            raise
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Landscape preparation failed')
            raise HTTPException(503, 'Could not prepare landscape data from local files. Check the offline road archive and source rasters, then retry. The previous frame is retained.') from None
        finally:
            local.lock.release()

    @app.post('/api/landscape/seed')
    def seed(body: SeedInput):
        return run(lambda s: s.initialize([(p.longitude,p.latitude) for p in body.ignitions], datetime.now(timezone.utc)))

    @app.post('/api/landscape/step')
    def step(body: ExpandingStep):
        def perform(s):
            historical = None
            if body.historical:
                day = body.historical.start_date + timedelta(days=body.state.step_index//2+1)
                if day > LAST_DAY:
                    raise ValueError('End of the historical date range')
                _, historical = historical_day(day, body.historical.bounds)
                historical['start_date'] = body.historical.start_date.isoformat()
            result = s.advance(body)
            if historical:
                result['historical'] = historical
                result['finished'] |= historical['date'] == LAST_DAY.isoformat()
            return result
        return run(perform)

    def convert(s, observed):
        points = [p for p in observed['points'] if p['status'] == 'active']
        result = s.initialize([(p['longitude'],p['latitude']) for p in points],
                              datetime.fromisoformat(observed['origin_at']), satellite=True)
        result['metadata']['satellite'] = observed['metadata']
        if observed.get('historical'):
            result['historical'] = observed['historical']
            result['finished'] |= observed.get('finished', False)
        return result

    @app.post('/api/landscape/firms')
    def live(body: BoundsInput):
        return run(lambda s: convert(s, firms(body)))

    @app.post('/api/landscape/firms/historical')
    def historical(body: HistoricalFirmsInput):
        return run(lambda s: convert(s, historical_firms(body)))
