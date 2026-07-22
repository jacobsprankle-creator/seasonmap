"""USA-NPN Spring Index grids (Geoserver WCS).

average_leaf_prism / average_bloom_prism arrive natively ON the canonical
grid (both are PRISM-derived: 621×1405, EPSG:4269, -9999 nodata), so this is
a true passthrough. Values are the day-of-year the index was reached; the
current year's grid is progressive during spring and final by summer.
"""
from __future__ import annotations

import numpy as np
import rasterio
import requests
from rasterio.io import MemoryFile
from rasterio.warp import Resampling

from .. import grid
from .elevation import USER_AGENT

WCS = (
    "https://geoserver.usanpn.org/geoserver/si-x/wcs"
    "?service=WCS&version=2.0.1&request=GetCoverage"
    "&coverageId=si-x__{coverage}&format=image/geotiff"
)


_memo: dict = {}


def fetch_spring_index(coverage: str) -> np.ndarray:
    """coverage: 'average_leaf_prism' or 'average_bloom_prism' → canonical DOY grid."""
    if coverage in _memo:
        return _memo[coverage]
    resp = requests.get(
        WCS.format(coverage=coverage), headers={"User-Agent": USER_AGENT}, timeout=180
    )
    resp.raise_for_status()
    with MemoryFile(resp.content) as mem:
        with mem.open() as src:
            arr = src.read(1).astype(np.float32)
            if (src.height, src.width) == grid.SHAPE and src.crs == grid.CRS_CANONICAL:
                arr[arr == (src.nodata if src.nodata is not None else -9999.0)] = grid.NODATA
                out = arr
            else:
                out = grid.resample_to_grid(
                    arr, src.transform, src.crs, Resampling.bilinear, src_nodata=src.nodata
                )
    out[(out != grid.NODATA) & ((out < 1) | (out > 366))] = grid.NODATA
    if (out != grid.NODATA).sum() < 0.2 * out.size:
        raise RuntimeError(f"NPN {coverage} looks empty — likely out of season")
    _memo[coverage] = out
    return out
