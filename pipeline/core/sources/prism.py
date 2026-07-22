"""PRISM 30-yr (1991-2020) monthly tmin normals — frost climatology input.

One-time ingest of 12 monthly GeoTIFFs (~36 MB total), cached as a single
stacked npz in static/. PRISM's 4 km grid IS the canonical grid (same shape,
CRS, and transform), verified at ingest; units are °C.

v1 uses monthly normals + smooth daily interpolation, which puts the derived
first-freeze crossing date within a few days of the daily-normals answer at
1/30th the download. The daily-normals URL pattern is below for the Phase 1.5
upgrade if calibration demands it:
    normals/us/4km/tmin/daily/prism_tmin_us_25m_2020MMDD_avg_30y.zip
"""
from __future__ import annotations

import io
import time
import zipfile

import numpy as np
import requests
from rasterio.io import MemoryFile
from rasterio.warp import Resampling

from .. import grid
from .elevation import USER_AGENT, static_dir

MONTHLY_URL = (
    "https://ftp.prism.oregonstate.edu/normals/us/4km/tmin/monthly/"
    "prism_tmin_us_25m_2020{month:02d}_avg_30y.zip"
)
CACHE_NAME = "prism_tmin_normals_monthly.npz"


def _fetch_month(session: requests.Session, month: int) -> np.ndarray:
    resp = session.get(MONTHLY_URL.format(month=month), timeout=180)
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        tif_name = next(n for n in zf.namelist() if n.endswith(".tif"))
        data = zf.read(tif_name)
    with MemoryFile(data) as mem:
        with mem.open() as src:
            arr = src.read(1).astype(np.float32)
            if (src.height, src.width) == grid.SHAPE and src.crs == grid.CRS_CANONICAL:
                arr[arr == (src.nodata if src.nodata is not None else -9999.0)] = grid.NODATA
                return arr
            return grid.resample_to_grid(
                arr, src.transform, src.crs, Resampling.bilinear, src_nodata=src.nodata
            )


def ensure_tmin_normals(force: bool = False) -> np.ndarray:
    """(12, H, W) float32 stack of monthly tmin normals in °C (NODATA-filled)."""
    cache = static_dir() / CACHE_NAME
    if cache.exists() and not force:
        return np.load(cache)["tmin"]

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    months = []
    for m in range(1, 13):
        months.append(_fetch_month(session, m))
        time.sleep(0.2)  # be polite to the PRISM mirror
    stack = np.stack(months)

    valid = stack != grid.NODATA
    if valid.mean() < 0.3:
        raise RuntimeError("PRISM normals ingest looks degenerate (mostly NODATA)")
    np.savez_compressed(cache, tmin=stack)
    return stack


# Mid-month day-of-year anchors for daily interpolation (non-leap DOYs).
MONTH_MID_DOY = np.array(
    [16, 45, 75, 105, 136, 166, 197, 228, 258, 289, 319, 350], dtype=np.float64
)


def daily_tmin_normal(stack: np.ndarray, doy: int) -> np.ndarray:
    """Interpolate the monthly normal stack to one day-of-year (periodic)."""
    d = float((doy - 1) % 365 + 1)
    anchors = MONTH_MID_DOY
    if d < anchors[0] or d >= anchors[-1]:
        i, j = 11, 0
        span = (365 - anchors[11]) + anchors[0]
        t = ((d - anchors[11]) % 365) / span
    else:
        j = int(np.searchsorted(anchors, d, side="right"))
        i = j - 1
        t = (d - anchors[i]) / (anchors[j] - anchors[i])
    out = (1.0 - t) * stack[i] + t * stack[j]
    out[(stack[i] == grid.NODATA) | (stack[j] == grid.NODATA)] = grid.NODATA
    return out.astype(np.float32)
