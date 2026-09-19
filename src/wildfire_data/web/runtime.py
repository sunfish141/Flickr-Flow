"""Application resources, independent of routes and browser scenario state."""
from contextlib import contextmanager
from functools import lru_cache
import logging
from threading import Lock
import json

from fastapi import HTTPException

from wildfire_data.model.loading import load_pass_model
from wildfire_data.model.features.landscape import Landscape
from wildfire_data.model.features.terrain_features import TerrainFeatureSampler
from wildfire_data.model.spread import FireSpreadModel
from wildfire_data.model.fuel import FuelPolicy
from wildfire_data.core.model_artifacts import PUBLIC_MODEL_KIND, public_artifact
from wildfire_data.providers.terrain_csv import CSVTerrainProvider
from wildfire_data.web.historical_firms import HistoricalFirmsStore
from wildfire_data.web.local_spread import LocalScenarios
from wildfire_data.web.vegetation import load_inspector_sampler
from wildfire_data.core.paths import REPOSITORY_ROOT

logger = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings, *, model=None, terrain_provider=None, firms_loader=None,
                 landscape=None, vegetation_sampler=None, historical_store=None, load_defaults=True):
        self.settings = settings
        self.load_defaults = load_defaults
        self.injected_model = model
        self.model = model
        self.terrain = terrain_provider
        self.firms_loader = firms_loader
        self.landscape = landscape
        self.vegetation = vegetation_sampler
        self.owns_vegetation = vegetation_sampler is None
        self.historical = historical_store
        self.locks = {name: Lock() for name in ('inference', 'firms', 'historical', 'vegetation')}
        self.firms_cache = {}
        self.firms_last_fetch = None
        self.model_error = None
        self.public_model = False
        self.local_scenarios = LocalScenarios(None)
        self.data_preparation = {'enabled': False, 'errors': {}}

    def start(self):
        local_config = self.settings.local_config
        vegetation_config = REPOSITORY_ROOT / 'config/vegetation_inspector.json'
        raster_cache = None
        if self.load_defaults and self.settings.prepare_data:
            from wildfire_data.providers.startup_data import StartupData
            self.data_preparation['enabled'] = True
            try:
                prepared = StartupData(self.settings.data_root, self.settings.source_data_root,
                    budget_path=REPOSITORY_ROOT / 'config/storage_budget.json',
                    allow_downloads=self.settings.download_vegetation).prepare(local_config, vegetation_config)
                local_config, vegetation_config = prepared['local_config'], prepared['vegetation_config']
                raster_cache = prepared['raster_cache']
                self.data_preparation['errors'] = prepared['errors']
            except Exception:
                logger.exception('Startup data preparation failed; existing sources will still be tried')
                self.data_preparation['errors']['startup'] = 'Data preparation failed; check startup logs and restart to retry.'
        try:
            self.local_scenarios = LocalScenarios(local_config if self.load_defaults else None)
            if self.local_scenarios.expanding:
                self.local_scenarios.expanding.warm(self.local_scenarios.presets)
        except Exception:
            logger.warning('Optional landscape sources could not be initialized')
        if self.historical is None and self.load_defaults:
            try:
                self.historical = HistoricalFirmsStore(self.settings.data_root)
            except Exception:
                logger.warning('Historical archive unavailable')
        try:
            if self.model is None:
                self.public_model = json.loads(self.settings.run_manifest.read_text()).get('kind') == PUBLIC_MODEL_KIND
            fitted = self.model or load_pass_model(self.settings.run_manifest, self.settings.pass_name)
            self.model = FireSpreadModel.from_incident_model(fitted, self.landscape or Landscape())
            if self.terrain is None:
                if self.public_model and not (self.settings.data_root / 'static/etopo-2022-15s').exists():
                    path, _ = public_artifact(self.settings.run_manifest, 'terrain.csv')
                    self.terrain = CSVTerrainProvider(path)
                else:
                    sampler = TerrainFeatureSampler(self.settings.data_root, max_cached_blocks=4)
                    self.terrain = lru_cache(maxsize=8192)(sampler.sample_cell)
        except Exception:
            logger.exception('Model initialization failed; verify configured artifacts')
            self.model_error = 'Model unavailable. Check WILDFIRE_RUN_MANIFEST and WILDFIRE_DATA_ROOT on the server.'
        if self.vegetation is None and self.load_defaults:
            try:
                self.vegetation = load_inspector_sampler(self.settings.vegetation_manifest,
                    primary=getattr(self.model, 'vegetation_sampler', None),
                    config_path=vegetation_config, raster_cache=raster_cache)
            except Exception:
                logger.warning('Optional vegetation sources unavailable')
        if self.model_error is None:
            policy_path = self.settings.fuel_policy or REPOSITORY_ROOT / 'config/fuel_policy.json'
            self.model.fuel_policy = FuelPolicy(**json.loads(policy_path.read_text()))
            self.model.fuel_sampler = self.vegetation if callable(getattr(self.vegetation, 'sample_cell', None)) else None
            if isinstance(self.model.landscape, Landscape) and hasattr(self.vegetation, 'land_cover_cell'):
                self.model.landscape.use_land_cover(self.vegetation.land_cover_cell, self.vegetation.land_source['revision'])
                if self.settings.prepare_data:
                    from datetime import datetime, timezone
                    from wildfire_data.core.grid import cell_from_wgs84
                    origin = datetime.now(timezone.utc)
                    for preset in self.local_scenarios.presets:
                        point = preset.get('example_ignition')
                        if point:
                            cell = cell_from_wgs84(**point)
                            self.model.landscape.allows_cell(cell.cell_id)
                            self.model.fuel_estimate(cell.cell_id, origin)

        if self.local_scenarios.expanding and self.model_error is None and self.public_model:
            try:
                from wildfire_data.web.weather_landscape import WeatherLandscapes
                self.local_scenarios.expanding.hybrid = WeatherLandscapes(self.settings.run_manifest,
                    self.settings.data_root, self.terrain, self.model.observation_calibration, self.locks['inference'])
            except Exception:
                logger.exception('Optional weather ML polygon model could not be initialized')

    def close(self):
        self.local_scenarios.close()
        if self.owns_vegetation and hasattr(self.vegetation, 'close'):
            self.vegetation.close()
        if hasattr(self.terrain, 'cache_clear'):
            self.terrain.cache_clear()
        if hasattr(self.model, 'fuel_estimate'):
            self.model.fuel_estimate.cache_clear()
        self.firms_cache.clear()

    def ready_model(self):
        if self.model_error:
            raise HTTPException(503, self.model_error)
        return self.model

    @contextmanager
    def available(self, name):
        lock = self.locks[name]
        if not lock.acquire(blocking=False):
            raise HTTPException(503, 'This operation is busy. Try again shortly.', headers={'Retry-After': '3'})
        try:
            yield
        finally:
            lock.release()
