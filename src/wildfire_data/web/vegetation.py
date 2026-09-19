"""Small, display-only vegetation summaries from prediction-time evidence."""

import json
import logging
from collections import OrderedDict
from pathlib import Path
from threading import RLock

from wildfire_data.core.hashing import sha256_file
from wildfire_data.core.paths import REPOSITORY_ROOT
from wildfire_data.model.features.vegetation_features import (
    VegetationFeatureSampler, select_features, validate_policy, validate_source, utc,
)
from wildfire_data.providers.vegetation.aggregation import SourceRaster

LAND_TYPES = {
    'needleleaf': 'Needleleaf forest', 'broadleaf': 'Broadleaf forest',
    'mixed_forest': 'Mixed forest', 'shrubland': 'Shrubland', 'grassland': 'Grassland',
    'wetland': 'Wetland', 'cropland': 'Cropland', 'other_vegetation': 'Other vegetation',
    'barren': 'Barren ground', 'urban': 'Urban land', 'water': 'Water', 'snow_ice': 'Snow / ice',
}


class InspectorSampler:
    """Supplement sparse model evidence with bounded, on-demand national land cover."""

    def __init__(self, primary, source_config, *, max_cached_cells=8192):
        self.primary = primary
        config_path = Path(source_config)
        config = json.loads(config_path.read_text())
        self.policy = config['policy']
        validate_policy(self.policy)
        self.land_source, = config['sources']
        validate_source(self.land_source)
        if self.land_source['product'] != 'NALCMS':
            raise ValueError('Inspector fallback requires NALCMS land cover')
        checked = {}
        for asset in self.land_source['assets']:
            path = (config_path.parent / asset['path']).resolve()
            asset['path'] = str(path)
            if path not in checked:
                checked[path] = sha256_file(path)
            if checked[path] != asset['sha256']:
                raise ValueError('Inspector land-cover asset checksum mismatch')
        self.sources = {**getattr(primary, 'sources', {}), self.land_source['source_id']: self.land_source}
        self._raster = None
        self._cache = OrderedDict()
        self._max_cached_cells = max_cached_cells
        self._lock = RLock()

    def sample_cell(self, cell_id, *, cutoff_at, simulation_at):
        context = dict(cutoff_at=cutoff_at, simulation_at=simulation_at)
        sample = (self.primary.sample_cell(cell_id, **context) if self.primary else
                  select_features([], policy=self.policy, **context))
        if sample['vegetation_land_cover_missing'] == 0:
            return sample
        # Playback never makes sources published after the original scenario
        # eligible. Check before opening the national archives.
        eligibility = select_features([{'source': self.land_source, 'valid_fraction': 1., 'values': {}}],
                                      policy=self.policy, **context)
        if 'NALCMS' not in eligibility['vegetation_lineage']['selected']:
            return sample
        with self._lock:
            if cell_id not in self._cache:
                if self._raster is None:
                    self._raster = SourceRaster(self.land_source)
                self._cache[cell_id] = {**self._raster.sample(cell_id), 'source': self.land_source}
                if len(self._cache) > self._max_cached_cells:
                    self._cache.popitem(last=False)
            self._cache.move_to_end(cell_id)
            record = self._cache[cell_id]
        land = select_features([record], policy=self.policy, **context)
        lineage = sample['vegetation_lineage']
        return {**sample, **{k: v for k, v in land.items() if k.startswith('vegetation_land_cover_')},
                'vegetation_lineage': {**lineage,
                    'selected': {**lineage['selected'], **land['vegetation_lineage']['selected']},
                    'rejected': lineage['rejected'] + land['vegetation_lineage']['rejected']}}

    def close(self):
        with self._lock:
            if self._raster is not None:
                self._raster.close()
                self._raster = None
            self._cache.clear()
            if hasattr(self.primary, 'close'):
                self.primary.close()


def load_inspector_sampler(manifest_path=None, *, primary=None):
    config = REPOSITORY_ROOT / 'config/vegetation_inspector.json'
    reference = json.loads(config.read_text())
    if primary is None:
        try:
            primary = (VegetationFeatureSampler(Path(manifest_path)) if manifest_path else
                       VegetationFeatureSampler(config.parent / reference['manifest_path'],
                                                expected_sha256=reference['manifest_sha256']))
        except Exception:
            logging.getLogger(__name__).warning('Canopy inspector store unavailable', exc_info=True)
    try:
        return InspectorSampler(primary, config.parent / reference['land_cover_sources_path'])
    except Exception:
        logging.getLogger(__name__).warning('National inspector land cover unavailable', exc_info=True)
        return primary


def vegetation_summary(sampler, cell_id, *, origin_at, simulation_at):
    result = {'cell_id': cell_id, 'status': 'unavailable', 'density_fraction': None,
              'tree_fraction': None, 'other_vegetation_fraction': None, 'nonvegetated_fraction': None,
              'land_cover': None, 'mapped_vegetation_fraction': None, 'ndvi': None, 'observations': [],
              'cover_caution_fraction': None, 'cover_valid_fraction': None, 'retrospective_context': False}
    if sampler is None:
        return result
    sample = sampler.sample_cell(cell_id, cutoff_at=origin_at, simulation_at=simulation_at)
    result['cover_valid_fraction'] = sample['vegetation_cover_valid_fraction']
    if sample['vegetation_cover_missing'] == 0:
        tree, other = sample['vegetation_tree_cover'], sample['vegetation_non_tree_cover']
        result.update(density_fraction=min(1., tree + other), tree_fraction=tree,
                      other_vegetation_fraction=other, nonvegetated_fraction=sample['vegetation_nonvegetated_cover'])
        result['cover_caution_fraction'] = sample.get('vegetation_cover_caution_fraction', 0.)
    if sample['vegetation_land_cover_missing'] == 0:
        dominant = max(LAND_TYPES, key=lambda key: sample['vegetation_land_cover_' + key])
        result['land_cover'] = LAND_TYPES[dominant]
        result['mapped_vegetation_fraction'] = min(1., sum(
            sample['vegetation_land_cover_' + key] for key in LAND_TYPES
            if key not in ('barren', 'urban', 'water', 'snow_ice')))
    if sample['vegetation_ndvi_missing'] == 0:
        result['ndvi'] = sample['vegetation_ndvi']
    labels = {'MOD44B': ('Canopy cover', 'vegetation_cover_valid_fraction'),
              'NALCMS': ('Land cover', 'vegetation_land_cover_valid_fraction'),
              'MOD13Q1': ('Greenness', 'vegetation_ndvi_valid_fraction')}
    for product, identities in sample['vegetation_lineage']['selected'].items():
        if product == 'MOD13Q1' and result['ndvi'] is None:
            continue
        source = sampler.sources[identities[0]]
        if (source['available_at'] is None or utc(source['available_at']) > utc(origin_at)):
            result['retrospective_context'] = True
        label, coverage = labels[product]
        result['observations'].append({'label': label, 'product': product,
            'start': source['observation_start'], 'end': source['observation_end'],
            'valid_fraction': sample[coverage]})
    if any(result[key] is not None for key in ('density_fraction', 'land_cover', 'ndvi')):
        result['status'] = 'available'
    return result
