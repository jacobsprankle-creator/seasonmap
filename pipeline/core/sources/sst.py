"""Water surface temperature — NASA JPL MUR SST (GHRSST), daily, via ERDDAP.

One CONUS-window subset at 0.04° (server-side stride 4, ~canonical
resolution), using the dataset's OWN sea/land/lake mask: analysed_sst is
extrapolated over land (plausible-looking junk), so the companion `mask`
variable is the truth — 1 = open sea, 5 = open lake (8/9 = ice variants).
That gives oceans, the Great Lakes, AND resolvable inland lakes (Tahoe,
Okeechobee, Champlain, Great Salt Lake…). Rivers are below the product's
resolution — station data (USGS NWIS) is the planned path for those.
"""
from __future__ import annotations

import os
import tempfile

import numpy as np
import rasterio
import requests
from affine import Affine
from rasterio.warp import Resampling

from .. import grid
from .elevation import USER_AGENT

_WINDOW = "%5B(last)%5D%5B(24.06):4:(49.94)%5D%5B(-125.02):4:(-66.48)%5D"
MUR_URL = (
    "https://coastwatch.pfeg.noaa.gov/erddap/griddap/jplMURSST41.nc"
    f"?analysed_sst{_WINDOW},mask{_WINDOW}"
)
WATER_CODES = (1, 5, 8, 9)  # open sea, open lake, sea+ice, lake+ice


def _read_var(path: str, var: str):
    try:
        src = rasterio.open(f"NETCDF:{path}:{var}")
    except rasterio.errors.RasterioIOError:
        src = rasterio.open(path)
    with src:
        arr = src.read(1).astype(np.float32)
        transform = src.transform
        crs = src.crs or grid.CRS_CANONICAL
    if transform.e > 0:  # bottom-up netCDF
        arr = np.flipud(arr)
        transform = Affine(
            transform.a, transform.b, transform.c,
            transform.d, -transform.e, transform.f + transform.e * arr.shape[0],
        )
    return arr, transform, crs


def fetch_water_temp_f() -> np.ndarray:
    """Latest water surface temperature in °F on the canonical grid; anything
    the GHRSST mask doesn't call water is NODATA."""
    resp = requests.get(MUR_URL, headers={"User-Agent": USER_AGENT}, timeout=300)
    resp.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
        tmp.write(resp.content)
        path = tmp.name
    try:
        sst, transform, crs = _read_var(path, "analysed_sst")
        mask, _, _ = _read_var(path, "mask")
    finally:
        os.unlink(path)

    water = np.isin(mask.astype(np.int16), WATER_CODES)
    sst[~water] = grid.NODATA
    sst[~np.isfinite(sst)] = grid.NODATA
    sst[(sst != grid.NODATA) & ((sst < -50) | (sst > 60))] = grid.NODATA

    sst_c = grid.resample_to_grid(sst, transform, crs, Resampling.bilinear, src_nodata=grid.NODATA)
    out = grid.empty()
    valid = sst_c != grid.NODATA
    out[valid] = sst_c[valid] * 9.0 / 5.0 + 32.0
    return out
