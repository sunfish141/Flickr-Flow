"""Pinned NALCMS and MODIS decoding; source QA is applied before aggregation.

References and policy interpretation: docs/vegetation-data.md.
"""

from datetime import datetime, timedelta, timezone

import numpy as np
from wildfire_data.model.features.schema import VEGETATION_PRODUCT_VERSIONS, VEGETATION_QA_POLICY, VEGETATION_COVER_QA_POLICY

NALCMS_CROSSWALK = {
    1: "needleleaf", 2: "needleleaf", 3: "broadleaf", 4: "broadleaf", 5: "broadleaf",
    6: "mixed_forest", 7: "shrubland", 8: "shrubland", 9: "grassland", 10: "grassland",
    11: "other_vegetation", 12: "other_vegetation", 13: "other_vegetation", 14: "wetland",
    15: "cropland", 16: "barren", 17: "urban", 18: "water", 19: "snow_ice",
}
PRODUCT_VERSIONS = VEGETATION_PRODUCT_VERSIONS
REQUIRED_BANDS = {
    "NALCMS": ("land_cover",),
    "MOD44B": ("tree", "non_tree", "nonvegetated", "quality", "cloud"),
    "MOD13Q1": ("ndvi", "evi", "quality", "reliability"),
}
QA_POLICY = VEGETATION_QA_POLICY
_BIT_COUNTS = np.array([value.bit_count() for value in range(256)], dtype=np.uint8)


def modis_observation_period(product, year, day):
    start = datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1)
    if start.year != year or day < 1:
        raise ValueError("invalid MODIS day of year")
    if product == "MOD44B":
        if day != 65:
            raise ValueError("MOD44B annual periods start on day 65")
        end = datetime(year + 1, 1, 1, tzinfo=timezone.utc) + timedelta(days=64)
    elif product == "MOD13Q1":
        if (day - 1) % 16:
            raise ValueError("MOD13Q1 requires a 16-day period anchor")
        end = min(start + timedelta(days=16), datetime(year + 1, 1, 1, tzinfo=timezone.utc))
    else:
        raise ValueError("unsupported MODIS product")
    return start, end  # end is exclusive; eligible once the entire interval ends


def decode(product, bands, *, qa_policy=QA_POLICY):
    if set(bands) != set(REQUIRED_BANDS[product]):
        raise ValueError("source bands differ from the pinned product contract")
    if len({np.shape(v) for v in bands.values()}) != 1:
        raise ValueError("source bands must share a pixel grid")
    b = {k: np.ma.asarray(v, dtype=float) for k, v in bands.items()}
    masks = {k: ~np.ma.getmaskarray(v) for k, v in b.items()}
    b = {k: v.filled(-32768).astype(float) for k, v in b.items()}
    if product == "NALCMS":
        a = b["land_cover"]
        return {"land_cover": np.where(masks["land_cover"] & np.isin(a, list(NALCMS_CROSSWALK)), a, np.nan)}
    if product == "MOD44B":
        good = masks["quality"] & masks["cloud"] & (b["quality"] == 0) & (b["cloud"] == 0)
        if qa_policy == VEGETATION_COVER_QA_POLICY:
            # Annual input-period bits, not a binary retrieval flag. Require
            # five of eight periods clear; flag >=2 poor periods as caution.
            legal = np.logical_and.reduce([masks[k] & (b[k] >= 0) & (b[k] <= 255) & (b[k] == np.floor(b[k]))
                                           for k in ('quality', 'cloud')])
            q = np.clip(b['quality'], 0, 255).astype(np.uint8)
            cloud = np.clip(b['cloud'], 0, 255).astype(np.uint8)
            bad = _BIT_COUNTS[q | cloud]
            good = legal & (bad <= 3)
        good &= np.logical_and.reduce([masks[k] & (b[k] >= 0) & (b[k] <= 100) for k in ("tree", "non_tree", "nonvegetated")])
        good &= np.abs(b["tree"] + b["non_tree"] + b["nonvegetated"] - 100) <= 1
        result = {k: np.where(good, b[k] / 100, np.nan) for k in ("tree", "non_tree", "nonvegetated")}
        if qa_policy == VEGETATION_COVER_QA_POLICY:
            result['caution'] = np.where(good, (bad >= 2).astype(float), np.nan)
        return result
    q = np.where(masks["quality"], b["quality"], 65535).astype(np.uint16)
    good = masks["quality"] & masks["reliability"] & (b["reliability"] == 0)
    good &= (q & 3) == 0
    good &= ((q >> 2) & 15) <= 2
    good &= ((q >> 6) & 3) != 3
    good &= (q & ((1 << 8) | (1 << 10) | (1 << 14) | (1 << 15))) == 0
    good &= ((q >> 11) & 7) == 1  # land only
    return {k: np.where(good & masks[k] & (b[k] >= -2000) & (b[k] <= 10000), b[k] * .0001, np.nan)
            for k in ("ndvi", "evi")}
