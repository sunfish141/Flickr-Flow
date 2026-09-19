"""Trusted weather-model resources for opt-in expanding polygon scenarios."""
import hashlib

import joblib

from wildfire_data.core.model_artifacts import public_artifact
from wildfire_data.model.estimators import GEOMETRY_COLUMNS, WEATHER_COLUMNS, WeatherPolicy
from wildfire_data.model.weather_hybrid import HybridSearch, VERSION
from wildfire_data.providers.scenario_weather import ScenarioWeather


class WeatherLandscapes:
    def __init__(self, manifest, data_root, terrain, calibration, terrain_lock):
        path, run = public_artifact(manifest, 'weather.joblib')
        self.estimator = joblib.load(path)
        if (not isinstance(self.estimator, WeatherPolicy)
                or tuple(self.estimator.geometry.columns) != GEOMETRY_COLUMNS
                or tuple(self.estimator.weather.columns) != WEATHER_COLUMNS
                or self.estimator.threshold != .15 or calibration is None):
            raise ValueError('Weather ML artifact or observation feature contract differs')
        self.identity = hashlib.sha256((VERSION+run['artifacts']['weather.joblib']['sha256']
                                       +run['artifacts']['frontier.joblib']['sha256']).encode()).hexdigest()
        self.calibration = calibration
        self._terrain, self.terrain_lock = terrain, terrain_lock
        self.weather = ScenarioWeather(data_root/'runtime/scenario-weather')

    def terrain(self, cell_id):
        with self.terrain_lock:
            return self._terrain(cell_id)

    def configuration(self):
        return {'available': True, 'kind': VERSION, 'experimental': True,
                'sources': ['forecast', 'historical'], 'probability_resolution_m': 1000,
                'weather_provider': 'Open-Meteo / ECMWF IFS',
                'weather_spatial_mode': 'one location per scenario',
                'after_forecast': 'hold last weather; explicitly labeled scenario extrapolation'}

    def search(self, model, seeds, origin, snapshot):
        key = (self.identity, snapshot.sha256, origin.isoformat(), tuple(seeds))
        if key not in model.hybrid_searches:
            model.hybrid_searches[key] = HybridSearch(model, seeds, origin, snapshot, self.estimator,
                                                    self.calibration, self.terrain, self.identity)
            while len(model.hybrid_searches) > 2:
                model.hybrid_searches.popitem(last=False)
        model.hybrid_searches.move_to_end(key)
        return model.hybrid_searches[key]
