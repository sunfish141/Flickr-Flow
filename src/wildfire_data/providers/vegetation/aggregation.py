"""Pixel-overlap aggregation on the canonical grid, without class-ID averaging."""

from contextlib import ExitStack
import hashlib
import math
from pathlib import Path
import zipfile

import numpy as np
from pyproj import Transformer
import rasterio
from rasterio.features import geometry_window
from rasterio.errors import WindowError
import shapely
from shapely.geometry import box, mapping
from shapely.ops import transform

from wildfire_data.core.grid import TRAINING_GRID_CRS, cell_from_id
from wildfire_data.model.features.schema import LAND_COVER_GROUPS
from wildfire_data.providers.vegetation.products import NALCMS_CROSSWALK, decode


def raster_path(asset, *, cache_manifest=None):
    from wildfire_data.providers.vegetation.raster_cache import cached_path
    cached = cached_path(asset, cache_manifest)
    if cached:
        return cached
    path = Path(asset["path"]).resolve()
    if path.suffix == ".gz":
        # Existing archival stores gzip-wrapped provider bytes. GDAL can
        # address the retained ZIP directly without extracting a national TIFF.
        if asset.get("member"):
            return f"/vsizip/{{/vsigzip/{path}}}/{asset['member']}"
        return f"/vsigzip/{path}"
    if zipfile.is_zipfile(path):
        member = asset.get("member")
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
        if member is None:
            tiffs = [n for n in names if n.lower().endswith((".tif", ".tiff"))]
            if len(tiffs) != 1:
                raise ValueError("ZIP requires an explicit TIFF member")
            member = tiffs[0]
        if member not in names or ".." in Path(member).parts:
            raise ValueError("invalid archive member")
        return f"zip://{path}!{member}"
    return str(path)


class SourceRaster:
    """Open one product mosaic once, read only candidate-cell windows."""

    def __init__(self, source):
        self.source = source
        self.stack = ExitStack()
        self.tiles = []
        self.crs_keys = {}
        try:
            for asset in sorted(source["assets"], key=lambda a: (a["sha256"], a.get("member", ""))):
                if asset.get("format") == "MODIS-HDF4":
                    from wildfire_data.providers.vegetation.hdf4 import open_bands
                    readers = open_bands(asset, self.stack)
                    first = next(iter(readers.values()))[0]
                    self.crs_keys[id(first)] = first.crs.to_wkt()
                    self.tiles.append((readers, first, Transformer.from_crs(TRAINING_GRID_CRS, first.crs, always_xy=True),
                                       Transformer.from_crs(first.crs, TRAINING_GRID_CRS, always_xy=True)))
                    continue
                # Readers can be used by a locked worker and closed by app
                # shutdown on another thread. Keep GDAL environment lifetimes
                # local to opening, instead of binding them to reader.close().
                with rasterio.Env():
                    root = rasterio.open(raster_path(asset))
                self.stack.callback(root.close)
                readers = {}
                for name, band in asset["bands"].items():
                    if isinstance(band, int):
                        if not 1 <= band <= root.count:
                            raise ValueError("raster band index outside source")
                        readers[name] = (root, band)
                    else:
                        matches = [s for s in root.subdatasets if s.split(":")[-1].strip('"') == band]
                        if len(matches) != 1:
                            raise ValueError(f"missing or ambiguous MODIS subdataset: {band}; check HDF4 driver support")
                        with rasterio.Env():
                            reader = rasterio.open(matches[0])
                        self.stack.callback(reader.close)
                        readers[name] = (reader, 1)
                first = next(iter(readers.values()))[0]
                if first.crs is None:
                    raise ValueError("vegetation raster requires a source CRS")
                self.crs_keys[id(first)] = first.crs.to_wkt()
                if any((r.crs, r.transform, r.width, r.height) != (first.crs, first.transform, first.width, first.height)
                       for r, _ in readers.values()):
                    raise ValueError("vegetation bands must share a source grid")
                self.tiles.append((readers, first, Transformer.from_crs(TRAINING_GRID_CRS, first.crs, always_xy=True),
                                   Transformer.from_crs(first.crs, TRAINING_GRID_CRS, always_xy=True)))
            self.tile_bounds = {}
            for _, dataset, _, _ in self.tiles:
                corners = [dataset.transform * (x, y) for x, y in
                           ((0, 0), (dataset.width, 0), (dataset.width, dataset.height), (0, dataset.height))]
                self.tile_bounds[id(dataset)] = (min(p[0] for p in corners), min(p[1] for p in corners),
                                                max(p[0] for p in corners), max(p[1] for p in corners))
        except Exception:
            self.close()
            raise

    def close(self):
        self.stack.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def sample(self, cell_id):
        footprint = box(*cell_from_id(cell_id).bounds_projected)
        remaining = footprint
        pixels = []
        source_cells = {}
        for readers, dataset, to_source, to_grid in self.tiles:
            if remaining.is_empty:
                break
            crs_key = self.crs_keys[id(dataset)]
            if crs_key not in source_cells:
                cell = transform(to_source.transform, shapely.segmentize(remaining, 125.))
                source_cells[crs_key] = (cell, cell.bounds)
            source_cell, bounds = source_cells[crs_key]
            west, south, east, north = self.tile_bounds[id(dataset)]
            if bounds[2] <= west or bounds[0] >= east or bounds[3] <= south or bounds[1] >= north:
                continue
            try:
                window = geometry_window(dataset, [mapping(source_cell)])
            except WindowError:
                continue
            if window.width * window.height > 100_000:
                raise ValueError("source window exceeds bounded pixel limit")
            raw = {name: reader.read(band, window=window, masked=True).astype(np.float64)
                   for name, (reader, band) in readers.items()}
            if 'valid_fraction' in raw and self.source['product'] == 'MOD44B':
                from wildfire_data.providers.vegetation.compact import decode_compact
                decoded = decode_compact(raw)
            else:
                decoded = decode(self.source["product"], raw, qa_policy=self.source['qa_policy'])
            if not any(np.isfinite(a).any() for a in decoded.values()):
                continue
            yy, xx = np.indices((int(window.height), int(window.width)))
            xx = xx.ravel() + window.col_off
            yy = yy.ravel() + window.row_off
            # Densify pixel boundaries before projection; area is measured in
            # the target equal-area CRS, never in longitude/latitude degrees.
            edge = np.array([(0, 0), (.5, 0), (1, 0), (1, .5), (1, 1), (.5, 1), (0, 1), (0, .5), (0, 0)])
            x, y = dataset.transform * (xx[:, None] + edge[:, 0], yy[:, None] + edge[:, 1])
            x, y = to_grid.transform(x, y)
            polygons = shapely.polygons(np.stack((x, y), axis=-1))
            if remaining.equals(footprint):
                west, south, east, north = footprint.bounds
                inside = (x.min(axis=1) >= west) & (x.max(axis=1) <= east) & (y.min(axis=1) >= south) & (y.max(axis=1) <= north)
                # Interior pixels need no polygon overlay. Clip only boundary
                # pixels, preserving the exact same overlap areas and QA.
                clipped = polygons.copy()
                clipped[~inside] = shapely.intersection(polygons[~inside], remaining)
            else:
                clipped = shapely.intersection(polygons, remaining)
            areas = shapely.area(clipped)
            flat = {name: a.ravel() for name, a in decoded.items()}
            valid = (areas > 1e-8) & np.logical_or.reduce([np.isfinite(a) for a in flat.values()])
            if self.source['product'] == 'NALCMS':
                # Class totals need no pixel identities or thousands of Python
                # dictionaries. Preserve exactly the same overlap-weighted areas.
                totals = np.bincount(flat['land_cover'][valid].astype(int), weights=areas[valid], minlength=20)
                pixels.extend({'area': float(totals[k]), 'land_cover': k} for k in NALCMS_CROSSWALK if totals[k] > 0)
            else:
                for i in np.flatnonzero(valid):
                    pixel = {'area': float(areas[i] * flat.get('valid_fraction', np.ones_like(areas))[i]),
                             **{name: float(a[i]) if np.isfinite(a[i]) else None for name, a in flat.items() if name != 'valid_fraction'}}
                    if self.source['product'] == 'MOD13Q1':
                        pixel['pixel_id'] = hashlib.sha256(shapely.to_wkb(shapely.normalize(clipped[i]))).hexdigest()
                    pixels.append(pixel)
            if valid.any():
                source_cells.clear()
                if abs(areas[valid].sum() - remaining.area) < 1e-5:
                    remaining = remaining.difference(remaining)
                else:
                    remaining = shapely.difference(remaining, shapely.union_all(clipped[valid]))
        return aggregate_pixels(cell_id, self.source["source_id"], self.source["product"], pixels)


def aggregate_pixels(cell_id, source_id, product, pixels):
    values = {}
    total = math.fsum(p["area"] for p in pixels)
    if total > 1_000_000 + .01 or any(p["area"] <= 0 for p in pixels):
        raise ValueError("overlapping or invalid cell pixel areas")
    if product == "NALCMS":
        fractions = {str(k): math.fsum(p["area"] for p in pixels if p["land_cover"] == k) / 1_000_000
                     for k in NALCMS_CROSSWALK}
        values = {"vegetation_land_cover_" + group: math.fsum(fractions[str(k)] for k, g in NALCMS_CROSSWALK.items() if g == group)
                  if total else None for group in LAND_COVER_GROUPS}
        values.update(vegetation_land_cover_valid_fraction=total / 1_000_000, vegetation_land_cover_missing=float(total == 0))
        return {"cell_id": cell_id, "source_id": source_id, "values": values,
                "class_fractions": fractions, "valid_fraction": total / 1_000_000}
    names = {"tree": "tree_cover", "non_tree": "non_tree_cover", "nonvegetated": "nonvegetated_cover"} if product == "MOD44B" else {"ndvi": "ndvi", "evi": "evi"}
    support = {}
    for band, name in names.items():
        area = math.fsum(p["area"] for p in pixels if p.get(band) is not None)
        support[band] = area / 1_000_000
        values["vegetation_" + name] = math.fsum(p["area"] * p[band] for p in pixels if p.get(band) is not None) / area if area else None
        if product == "MOD13Q1":
            values["vegetation_" + name + "_valid_fraction"] = area / 1_000_000
            values["vegetation_" + name + "_missing"] = float(area == 0)
    if product == "MOD44B":
        values.update(vegetation_cover_valid_fraction=total / 1_000_000, vegetation_cover_missing=float(total == 0))
        if any('caution' in p for p in pixels):
            values['vegetation_cover_caution_fraction'] = math.fsum(p['area'] * p.get('caution', 0.) for p in pixels) / total if total else 0.
    result = {"cell_id": cell_id, "source_id": source_id, "values": values, "valid_fraction": max(support.values())}
    if product == "MOD13Q1":
        result["pixels"] = pixels
    return result
