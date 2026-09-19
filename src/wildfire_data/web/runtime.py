"""Application resources, independent of routes and browser scenario state."""
from contextlib import contextmanager
from functools import lru_cache
import logging
from threading import Lock

from fastapi import HTTPException

from wildfire_data.model.loading import load_pass_model
from wildfire_data.model.features.landscape import Landscape
from wildfire_data.model.features.terrain_features import TerrainFeatureSampler
from wildfire_data.model.spread import FireSpreadModel
from wildfire_data.web.historical_firms import HistoricalFirmsStore
from wildfire_data.web.local_spread import LocalScenarios
from wildfire_data.web.vegetation import load_inspector_sampler

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
        self.local_scenarios = LocalScenarios(None)

    def start(self):
        try:
            self.local_scenarios = LocalScenarios(self.settings.local_config if self.load_defaults else None)
        except Exception:
            logger.warning('Optional landscape sources could not be initialized')
        if self.historical is None and self.load_defaults:
            try:
                self.historical = HistoricalFirmsStore(self.settings.data_root)
            except Exception:
                logger.warning('Historical archive unavailable')
        try:
            fitted = self.model or load_pass_model(self.settings.run_manifest, self.settings.pass_name)
            self.model = FireSpreadModel.from_incident_model(fitted, self.landscape or Landscape())
            if self.terrain is None:
                sampler = TerrainFeatureSampler(self.settings.data_root, max_cached_blocks=4)
                self.terrain = lru_cache(maxsize=8192)(sampler.sample_cell)
        except Exception:
            logger.error('Model initialization failed; verify configured artifacts')
            self.model_error = 'Model unavailable. Check WILDFIRE_RUN_MANIFEST and WILDFIRE_DATA_ROOT on the server.'
        if self.vegetation is None and self.load_defaults:
            try:
                self.vegetation = load_inspector_sampler(self.settings.vegetation_manifest,
                    primary=getattr(self.model, 'vegetation_sampler', None))
            except Exception:
                logger.warning('Optional vegetation sources unavailable')

    def close(self):
        self.local_scenarios.close()
        if self.owns_vegetation and hasattr(self.vegetation, 'close'):
            self.vegetation.close()
        if hasattr(self.terrain, 'cache_clear'):
            self.terrain.cache_clear()
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
