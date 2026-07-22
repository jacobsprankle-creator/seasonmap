"""Canonical CONUS grid definition.

Every layer's daily output is a float32 array on this one grid — the PRISM 4km
CONUS grid (EPSG:4269 / NAD83, 2.5 arc-minute cells, 1405 x 621). All upstream
sources get resampled onto it; all COGs/tiles are produced from it.

Grid facts (matches PRISM .bil headers):
    ncols=1405, nrows=621, cellsize=1/24 deg (0.0416666...),
    west edge  = -125.0208333..., east edge = -66.4791666...
    north edge =   49.9375,      south edge =  24.0625
"""
from __future__ import annotations

from typing import Optional, Tuple

import numpy as np
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling, reproject

CELLSIZE: float = 1.0 / 24.0  # 2.5 arc-minutes, ~4 km
WIDTH: int = 1405
HEIGHT: int = 621

WEST: float = -125.0 - CELLSIZE / 2.0   # -125.020833...
NORTH: float = 49.9375                  # top edge
EAST: float = WEST + WIDTH * CELLSIZE   # -66.479166...
SOUTH: float = NORTH - HEIGHT * CELLSIZE  # 24.0625

BOUNDS: Tuple[float, float, float, float] = (WEST, SOUTH, EAST, NORTH)
CRS_CANONICAL: CRS = CRS.from_epsg(4269)
TRANSFORM: Affine = Affine(CELLSIZE, 0.0, WEST, 0.0, -CELLSIZE, NORTH)
NODATA: float = -9999.0
DTYPE = np.float32
SHAPE: Tuple[int, int] = (HEIGHT, WIDTH)


def empty(fill: float = NODATA) -> np.ndarray:
    """A canonical-grid float32 array pre-filled with `fill`."""
    return np.full(SHAPE, fill, dtype=DTYPE)


def lon_lat_arrays() -> Tuple[np.ndarray, np.ndarray]:
    """Cell-center coordinate arrays. Returns (lons[WIDTH], lats[HEIGHT])."""
    lons = WEST + (np.arange(WIDTH) + 0.5) * CELLSIZE
    lats = NORTH - (np.arange(HEIGHT) + 0.5) * CELLSIZE
    return lons.astype(np.float64), lats.astype(np.float64)


def index_for(lon: float, lat: float) -> Tuple[int, int]:
    """(row, col) of the cell containing a lon/lat point.

    Raises ValueError if the point is outside the grid.
    """
    col = int(np.floor((lon - WEST) / CELLSIZE))
    row = int(np.floor((NORTH - lat) / CELLSIZE))
    if not (0 <= col < WIDTH and 0 <= row < HEIGHT):
        raise ValueError(f"point ({lon}, {lat}) is outside the canonical grid")
    return row, col


def value_at(arr: np.ndarray, lon: float, lat: float) -> float:
    row, col = index_for(lon, lat)
    return float(arr[row, col])


def profile(dtype: str = "float32", nodata: float = NODATA) -> dict:
    """rasterio profile for a canonical-grid single-band GeoTIFF."""
    return {
        "driver": "GTiff",
        "width": WIDTH,
        "height": HEIGHT,
        "count": 1,
        "dtype": dtype,
        "crs": CRS_CANONICAL,
        "transform": TRANSFORM,
        "nodata": nodata,
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
        "compress": "deflate",
    }


def resample_to_grid(
    src: np.ndarray,
    src_transform: Affine,
    src_crs: CRS,
    resampling: Resampling = Resampling.bilinear,
    src_nodata: Optional[float] = None,
) -> np.ndarray:
    """Reproject/resample any source raster onto the canonical grid.

    Cells with no source coverage come back as NODATA.
    """
    dst = empty()
    reproject(
        source=src.astype(np.float32),
        destination=dst,
        src_transform=src_transform,
        src_crs=src_crs,
        src_nodata=src_nodata,
        dst_transform=TRANSFORM,
        dst_crs=CRS_CANONICAL,
        dst_nodata=NODATA,
        resampling=resampling,
    )
    return dst


def mask_invalid(arr: np.ndarray) -> np.ndarray:
    """Replace NaN/inf with NODATA (returns a copy)."""
    out = arr.astype(DTYPE, copy=True)
    out[~np.isfinite(out)] = NODATA
    return out
