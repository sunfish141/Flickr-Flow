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
        self.allows_cell = lru_cache(maxsize=32768)(self._allows_cell)
        self.allows_spread = lru_cache(maxsize=65536)(self._allows_spread)

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
        return self._on_land(footprint)

    def _allows_spread(self, source_id, target_id):
        # Checking the entire segment prevents jumps across a mapped narrow
        # channel even when both cell centres are on land (including diagonals).
        if not self.allows_cell(source_id) or not self.allows_cell(target_id):
            return False
        source = cell_from_id(source_id).center_wgs84
        target = cell_from_id(target_id).center_wgs84
        return self._on_land(LineString([source[::-1], target[::-1]]))
