"""NOHRSC / SNODAS daily snow depth (masked CONUS, ~1 km).

Daily tarballs from the NSIDC archive, no key:
    https://noaadata.apps.nsidc.org/NOAA/G02158/masked/{YYYY}/{MM_Mon}/SNODAS_{YYYYMMDD}.tar

Inside: gzipped flat binary per product. Snow depth is product 1036
(us_ssmv11036tS__T0001TTNATS{date}05HP001.dat.gz): int16 big-endian,
3351 × 6935 cells at 30 arc-sec, meters × 1000, -9999 = nodata. Georeference
is parsed from the accompanying .txt metadata when present, with documented
constants as fallback. Result is resampled (average) onto the canonical grid.
"""
from __future__ import annotations

import datetime as dt
import gzip
import io
import re
import tarfile
from typing import Optional, Tuple

import numpy as np
import requests
from affine import Affine

from .. import grid
from .elevation import USER_AGENT

BASE = "https://noaadata.apps.nsidc.org/NOAA/G02158/masked"
ROWS, COLS = 3351, 6935
CELL = 1.0 / 120.0
# Documented masked-grid corners (fallback if the .txt metadata is absent).
FALLBACK_ULX, FALLBACK_ULY = -124.733749999998, 52.874583333332


def _tar_url(date: str) -> str:
    d = dt.date.fromisoformat(date)
    return f"{BASE}/{d.year}/{d.strftime('%m_%b')}/SNODAS_{d.strftime('%Y%m%d')}.tar"


def _parse_georef(txt: str) -> Tuple[float, float]:
    def find(pattern: str) -> Optional[float]:
        m = re.search(pattern + r"\s*:\s*(-?\d+\.?\d*)", txt)
        return float(m.group(1)) if m else None

    ulx = find(r"Minimum x-axis coordinate")
    uly = find(r"Maximum y-axis coordinate")
    return (
        ulx if ulx is not None else FALLBACK_ULX,
        uly if uly is not None else FALLBACK_ULY,
    )


def fetch_snow_depth(date: str, max_lookback: int = 3) -> Tuple[np.ndarray, str]:
    """Snow depth (m) on the canonical grid. Falls back up to `max_lookback`
    days if the requested date's tarball isn't posted yet.

    Returns (depth_array, actual_date_used).
    """
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    last_err: Optional[Exception] = None
    for back in range(max_lookback + 1):
        d = (dt.date.fromisoformat(date) - dt.timedelta(days=back)).isoformat()
        try:
            resp = session.get(_tar_url(d), timeout=300)
            resp.raise_for_status()
            return _extract_depth(resp.content), d
        except Exception as exc:  # noqa: BLE001 — try earlier days
            last_err = exc
    raise RuntimeError(f"SNODAS unavailable for {date} (-{max_lookback}d): {last_err}")


def _extract_depth(tar_bytes: bytes) -> np.ndarray:
    depth_gz = None
    meta_txt = ""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar.getmembers():
            if "1036" in member.name and member.name.endswith(".dat.gz"):
                depth_gz = tar.extractfile(member).read()
            elif "1036" in member.name and member.name.endswith(".txt.gz"):
                meta_txt = gzip.decompress(tar.extractfile(member).read()).decode("latin-1")
            elif "1036" in member.name and member.name.endswith(".txt"):
                meta_txt = tar.extractfile(member).read().decode("latin-1")
    if depth_gz is None:
        raise RuntimeError("snow depth product (1036) missing from SNODAS tarball")

    raw = np.frombuffer(gzip.decompress(depth_gz), dtype=">i2")
    if raw.size != ROWS * COLS:
        raise RuntimeError(f"unexpected SNODAS size: {raw.size} != {ROWS * COLS}")
    arr = raw.reshape(ROWS, COLS).astype(np.float32)
    arr[arr == -9999] = grid.NODATA
    m = arr != grid.NODATA
    arr[m] = arr[m] / 1000.0  # mm-scaled ints → meters

    ulx, uly = _parse_georef(meta_txt)
    transform = Affine(CELL, 0.0, ulx, 0.0, -CELL, uly)
    from rasterio.warp import Resampling

    return grid.resample_to_grid(
        arr, transform, grid.CRS_CANONICAL, Resampling.average, src_nodata=grid.NODATA
    )
