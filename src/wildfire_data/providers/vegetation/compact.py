"""Bounded 4×4 native-grid summaries, with retained valid area and QA caution.

The ~927 m pixels describe mean fractional cover over accepted native pixels.
At target-cell boundaries their within-pixel support is assumed uniform.
This is a declared spatial approximation, not a native-resolution archive.
"""

from contextlib import ExitStack

import numpy as np
import rasterio
from rasterio.windows import Window
from affine import Affine

from wildfire_data.model.features.schema import VEGETATION_COVER_QA_POLICY
from wildfire_data.providers.vegetation.hdf4 import open_bands
from wildfire_data.providers.vegetation.products import decode

COMPACT_FORMAT = 'MOD44B-4x4-summary/v1'
COMPACT_BANDS = {'tree': 1, 'non_tree': 2, 'nonvegetated': 3, 'valid_fraction': 4, 'caution': 5}


def compact_mod44b(asset, destination):
    with ExitStack() as stack:
        readers = open_bands(asset, stack)
        first = readers['tree'][0]
        if first.width % 4 or first.height % 4:
            raise ValueError('Compact MOD44B requires complete 4x4 native groups')
        with rasterio.open(destination, 'w', driver='GTiff', width=first.width // 4,
                           height=first.height // 4, count=5, dtype='float32',
                           crs=first.crs, transform=first.transform * Affine.scale(4),
                           tiled=True, blockxsize=128, blockysize=128, compress='deflate', predictor=3) as out:
            for row in range(0, first.height, 128):
                height = min(128, first.height-row)
                # Native scientific-data strips are bounded to 614,400 pixels
                # per band. Avoid thousands of redundant tiny HDF reads.
                raw = {}
                for name, (reader, _) in readers.items():
                    values = np.asarray(reader.dataset.get(start=(row, 0), count=(height, first.width)))
                    raw[name] = np.ma.masked_equal(values, reader.fill) if reader.fill is not None else values
                decoded = decode('MOD44B', raw, qa_policy=VEGETATION_COVER_QA_POLICY)
                shape = (height // 4, 4, first.width // 4, 4)
                valid = np.isfinite(decoded['tree'])
                count = valid.reshape(shape).sum(axis=(1, 3))
                means = [np.divide(np.nan_to_num(decoded[k]).reshape(shape).sum(axis=(1, 3)), count,
                                   out=np.zeros(count.shape, dtype=float), where=count > 0)
                         for k in ('tree', 'non_tree', 'nonvegetated', 'caution')]
                values = np.stack([*means[:3], count / 16., means[3]]).astype('float32')
                out.write(values, window=Window(0, row // 4, first.width // 4, height // 4))
            for key, band in COMPACT_BANDS.items():
                out.set_band_description(band, key)
            out.update_tags(format=COMPACT_FORMAT, qa_policy=VEGETATION_COVER_QA_POLICY,
                            aggregation='4x4 accepted native pixels; uniform within-summary-pixel support')


def decode_compact(bands):
    if set(bands) != set(COMPACT_BANDS):
        raise ValueError('Compact cover bands differ from the declared format')
    b = {k: np.ma.asarray(v, dtype=float).filled(np.nan) for k, v in bands.items()}
    good = np.logical_and.reduce([np.isfinite(v) & (v >= 0) & (v <= 1) for v in b.values()])
    good &= (b['valid_fraction'] > 0) & (np.abs(b['tree'] + b['non_tree'] + b['nonvegetated'] - 1) <= .01)
    return {k: np.where(good, v, np.nan) for k, v in b.items()}
