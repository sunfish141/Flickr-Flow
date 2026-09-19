"""Offline water barriers from bundled Natural Earth land and lake polygons.

The map is generalized: it blocks mapped lakes/coastlines, not every stream
or small pond. Land means possible fuel, not a measured vegetation class.
"""

from functools import lru_cache
import gzip
import json
from pathlib import Path

from pyproj import Transformer
from shapely.geometry import LineString, Polygon, box, shape
from shapely.strtree import STRtree

from wildfire_data.core.grid import TRAINING_GRID_CRS, cell_from_id


class Landscape:
    version = "natural-earth-5.1.2-north-america/whole-cell-v2"

    def __init__(self, *, land=None, lakes=None, bounds=(-179., 24., -50., 84.)):
        if land is None or lakes is None:
            path = Path(__file__).with_name("resources") / "north-america-water.geojson.gz"
            with gzip.open(path, "rt") as source:
                features = json.load(source)["features"]
            land = [shape(f["geometry"]) for f in features if f["properties"]["kind"] == "land"]
            lakes = [shape(f["geometry"]) for f in features if f["properties"]["kind"] == "lake"]
        self.land = STRtree(land)
        self.lakes = STRtree(lakes)
        self.bounds = box(*bounds)
        self.to_wgs84 = Transformer.from_crs(TRAINING_GRID_CRS, "EPSG:4326", always_xy=True)
        self.land_cover = None
        self.allows_cell = lru_cache(maxsize=32768)(self._allows_cell)
        self.allows_spread = lru_cache(maxsize=65536)(self._allows_spread)

    def use_land_cover(self, sampler, identity):
        self.land_cover = sampler
        self.version = f'natural-earth+nalcms-whole-cell/v3:{identity}'
        self.allows_cell.cache_clear()
        self.allows_spread.cache_clear()

    def _on_land(self, geometry):
        return (self.bounds.covers(geometry)
                and len(self.land.query(geometry, predicate="covered_by")) > 0
                and len(self.lakes.query(geometry, predicate="intersects")) == 0)

    def _allows_cell(self, cell_id):
        xmin, ymin, xmax, ymax = cell_from_id(cell_id).bounds_projected
        footprint = Polygon([self.to_wgs84.transform(x, y) for x, y in
                             ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))])
        # A land centre does not establish that the rest of its 1 km cell is
        # land. Keep mixed shoreline cells out of this coarse-grid simulation.
        if not self._on_land(footprint):
            return False
        if self.land_cover is not None:
            record = self.land_cover(cell_id)
            # Any measured water excludes the entire coarse cell. Missing
            # raster support falls back to the bundled coast/lake geometry.
            if (record['values'].get('vegetation_land_cover_water') or 0.) > 1e-9:
                return False
        return True

    def _allows_spread(self, source_id, target_id):
        # Checking the entire segment prevents jumps across a mapped narrow
        # channel even when both cell centres are on land (including diagonals).
        if not self.allows_cell(source_id) or not self.allows_cell(target_id):
            return False
        source_cell, target_cell = cell_from_id(source_id), cell_from_id(target_id)
        if source_cell.x_index != target_cell.x_index and source_cell.y_index != target_cell.y_index:
            # A diagonal must not slip between two excluded shoreline cells.
            from wildfire_data.core.grid import GridCell
            if not all(self.allows_cell(GridCell(x, y).cell_id) for x, y in
                       ((source_cell.x_index, target_cell.y_index), (target_cell.x_index, source_cell.y_index))):
                return False
        source = cell_from_id(source_id).center_wgs84
        target = cell_from_id(target_id).center_wgs84
        return self._on_land(LineString([source[::-1], target[::-1]]))
