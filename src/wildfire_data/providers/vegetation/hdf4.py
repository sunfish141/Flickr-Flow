"""Bounded native MODIS sinusoidal HDF4 windows via the scientific-data API."""

import gzip
from collections import OrderedDict
from pathlib import Path
import re
import tempfile

import numpy as np
from pyhdf.SD import SD, SDC
from rasterio.crs import CRS
from rasterio.transform import from_bounds

MAX_NATIVE_BYTES = 512 * 1024 * 1024


def grid_metadata(attributes):
    keys = sorted((k for k in attributes if k.startswith("StructMetadata.")), key=lambda k: int(k.split(".")[-1]))
    text = "".join(attributes[k] for k in keys)
    grids = re.findall(r"GROUP=(GRID_\d+)\s+(.*?)END_GROUP=\1", text, re.DOTALL)
    if len(grids) != 1:
        raise ValueError("native MODIS source requires one explicit HDF-EOS grid")
    grid = grids[0][1]
    def scalar(name):
        found = re.findall(r"\b" + name + r"=([^\r\n]+)", grid)
        if len(found) != 1:
            raise ValueError("missing or ambiguous HDF grid field: " + name)
        return found[0].strip()
    def point(name):
        value = scalar(name).strip("()")
        result = tuple(float(v) for v in value.split(","))
        if len(result) != 2 or not np.isfinite(result).all():
            raise ValueError("invalid HDF grid corner")
        return result
    if scalar("Projection") != "GCTP_SNSOID":
        raise ValueError("unsupported native MODIS projection")
    parameters = [float(v) for v in scalar("ProjParams").strip("()").split(",")]
    if not parameters or abs(parameters[0] - 6371007.181) > .001 or any(parameters[1:]):
        raise ValueError("unsupported MODIS sinusoidal parameters")
    width, height = int(scalar("XDim")), int(scalar("YDim"))
    west, north = point("UpperLeftPointMtrs")
    east, south = point("LowerRightMtrs")
    if width <= 0 or height <= 0 or west >= east or south >= north:
        raise ValueError("invalid HDF grid dimensions")
    crs = CRS.from_string("+proj=sinu +R=6371007.181 +nadgrids=@null +wktext")
    return width, height, crs, from_bounds(west, south, east, north, width, height)


class HdfBand:
    def __init__(self, dataset, grid):
        self.dataset = dataset
        self.width, self.height, self.crs, self.transform = grid
        self.count = 1
        if tuple(dataset.info()[2]) != (self.height, self.width):
            raise ValueError("HDF band dimensions differ from its grid")
        self.fill = dataset.attributes().get("_FillValue")
        # MOD44B declares _FillValue=0 on these bitfields, but zero also
        # means all eight inputs are clear. Science-band fill/water codes
        # disambiguate missing pixels; masking zero here erases every pixel
        # accepted by the strict QA policy.
        if dataset.info()[0] in ("Quality", "Cloud") and self.fill == 0:
            self.fill = None
        self.blocks = OrderedDict()
        self.block_size = 128
        self.max_cached_blocks = 32

    def read(self, band, *, window, masked):
        if band != 1:
            raise ValueError("HDF scientific-data band index must be one")
        start = (int(window.row_off), int(window.col_off))
        count = (int(window.height), int(window.width))
        if (min(start) < 0 or min(count) <= 0 or count[0] * count[1] > 100_000
                or start[0] + count[0] > self.height or start[1] + count[1] > self.width):
            raise ValueError("invalid or oversized HDF window")
        # Native compressed SDS reads are expensive even for tiny windows.
        # Cache a fixed number of small spatial blocks, never full granules.
        values = None
        size = self.block_size
        bottom, right = start[0] + count[0], start[1] + count[1]
        for row in range(start[0] // size * size, bottom, size):
            for column in range(start[1] // size * size, right, size):
                key = (row, column)
                block = self.blocks.pop(key, None)
                if block is None:
                    block = np.asarray(self.dataset.get(start=key,
                        count=(min(size, self.height-row), min(size, self.width-column))))
                self.blocks[key] = block
                while len(self.blocks) > self.max_cached_blocks:
                    self.blocks.popitem(last=False)
                if values is None:
                    values = np.empty(count, dtype=block.dtype)
                top, left = max(row, start[0]), max(column, start[1])
                end_row, end_column = min(row+size, bottom), min(column+size, right)
                values[top-start[0]:end_row-start[0], left-start[1]:end_column-start[1]] = (
                    block[top-row:end_row-row, left-column:end_column-column])
        return np.ma.masked_equal(values, self.fill) if masked and self.fill is not None else np.ma.asarray(values)


def open_bands(asset, stack):
    path = Path(asset["path"])
    if path.suffix == ".gz":
        staging = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="vegetation-hdf4-")))
        native = staging / "source.hdf"
        size = 0
        with gzip.open(path, "rb") as source, native.open("wb") as destination:
            while block := source.read(1024 * 1024):
                size += len(block)
                if size > MAX_NATIVE_BYTES:
                    raise ValueError("MODIS granule exceeds bounded native staging limit")
                destination.write(block)
        path = native
    elif path.stat().st_size > MAX_NATIVE_BYTES:
        raise ValueError("MODIS granule exceeds bounded native reader limit")
    document = SD(str(path), SDC.READ)
    stack.callback(document.end)
    grid = grid_metadata(document.attributes())
    readers = {}
    for name, selector in asset["bands"].items():
        if not isinstance(selector, str):
            raise ValueError("native MODIS bands require exact scientific-data names")
        dataset = document.select(selector)
        stack.callback(dataset.endaccess)
        readers[name] = (HdfBand(dataset, grid), 1)
    return readers
