"""One-time elevation ingest (dummy/verification layer + snow-line input).

Fetches CONUS elevation once, resamples to the canonical grid, and caches it
(static/elevation.tif — in prod that cache lives at r2://static/). Two upstream
strategies, tried in order:

  1. NOAA NCEI DEM mosaic ImageServer `exportImage` — server resamples ETOPO/
     coastal DEMs directly onto our bbox at our exact size. One request.
  2. NOAA CoastWatch ERDDAP `etopo180` — ETOPO1 (1 arc-min) GeoTIFF subset,
     resampled locally onto the canonical grid.

Both are free, keyless services. We identify with a proper User-Agent and hit
each at most once per environment thanks to the cache.
"""
from __future__ import annotations

import io
import os
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import rasterio
import requests
from rasterio.io import MemoryFile
from rasterio.warp import Resampling

from .. import grid

USER_AGENT = os.environ.get(
    "PIPELINE_USER_AGENT", "seasonmap-pipeline/0.1 (+https://seasonmap.example)"
)

# DEM_global_mosaic = ETOPO 2022 global DEM. (Do NOT use DEM_all here — at
# CONUS-wide scale it returns zeros over land where no coastal DEM exists.)
NCEI_IMAGESERVER = (
    "https://gis.ngdc.noaa.gov/arcgis/rest/services/DEM_mosaics/DEM_global_mosaic/ImageServer/exportImage"
)
# netCDF, not .geotif: ERDDAP's .geotif renders scaled display bytes (1..255),
# not physical elevations.
ERDDAP_ETOPO = "https://coastwatch.pfeg.noaa.gov/erddap/griddap/etopo180.nc"


def static_dir() -> Path:
    d = Path(os.environ.get("STATIC_DIR") or Path(__file__).resolve().parents[3] / "static_cache")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _read_geotiff_bytes(data: bytes) -> Tuple[np.ndarray, "rasterio.Affine", "rasterio.crs.CRS", Optional[float]]:
    with MemoryFile(data) as mem:
        with mem.open() as src:
            return src.read(1).astype(np.float32), src.transform, src.crs, src.nodata


def _fetch_ncei_imageserver(session: requests.Session) -> np.ndarray:
    """ETOPO 2022 mosaic exported straight onto the canonical bbox/size."""
    west, south, east, north = grid.BOUNDS
    params = {
        "bbox": f"{west},{south},{east},{north}",
        "bboxSR": "4326",
        "imageSR": "4326",
        "size": f"{grid.WIDTH},{grid.HEIGHT}",
        "format": "tiff",
        "pixelType": "F32",
        "interpolation": "RSP_BilinearInterpolation",
        "f": "image",
    }
    resp = session.get(NCEI_IMAGESERVER, params=params, timeout=180)
    resp.raise_for_status()
    if "json" in resp.headers.get("Content-Type", ""):
        raise RuntimeError(f"NCEI exportImage error: {resp.text[:300]}")
    arr, transform, crs, nodata = _read_geotiff_bytes(resp.content)
    return grid.resample_to_grid(arr, transform, crs, Resampling.bilinear, src_nodata=nodata)


def _fetch_erddap_etopo(session: requests.Session) -> np.ndarray:
    """ETOPO1 (1 arc-min) netCDF subset from ERDDAP, resampled locally
    (~average of ~6 source cells per canonical cell)."""
    import tempfile

    west, south, east, north = grid.BOUNDS
    pad = 0.05
    query = (
        f"altitude%5B({south - pad}):({north + pad})%5D"
        f"%5B({west - pad}):({east + pad})%5D"
    )
    resp = session.get(f"{ERDDAP_ETOPO}?{query}", timeout=300)
    resp.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".nc", delete=False) as tmp:
        tmp.write(resp.content)
        tmp_path = tmp.name
    try:
        try:
            src_ds = rasterio.open(tmp_path)
        except rasterio.errors.RasterioIOError:
            src_ds = rasterio.open(f"NETCDF:{tmp_path}:altitude")
        with src_ds as src:
            arr = src.read(1).astype(np.float32)
            transform, crs, nodata = src.transform, src.crs or grid.CRS_CANONICAL, src.nodata
        # netCDF grids are often stored bottom-up; GDAL reports that as a
        # positive e-term. Normalize to north-up before resampling.
        if transform.e > 0:
            arr = np.flipud(arr)
            transform = rasterio.Affine(
                transform.a, transform.b, transform.c,
                transform.d, -transform.e, transform.f + transform.e * arr.shape[0],
            )
        return grid.resample_to_grid(arr, transform, crs, Resampling.average, src_nodata=nodata)
    finally:
        os.unlink(tmp_path)


# Plausibility anchors: a fetch that "succeeds" with rendered bytes or an
# all-zero mosaic must fail the chain, not ship broken tiles.
_SANITY_POINTS = (
    ("Denver CO", -104.99, 39.74, 1300.0, 2200.0),
    ("Leadville CO", -106.29, 39.25, 2500.0, 4000.0),
    ("Atlantic offshore", -70.0, 38.0, -6000.0, -500.0),
)


def _validate(arr: np.ndarray, source_name: str) -> None:
    valid = arr != grid.NODATA
    if valid.sum() < 0.5 * arr.size:
        raise RuntimeError(f"{source_name}: too little coverage ({int(valid.sum())} cells)")
    nontrivial = np.abs(arr[valid]) > 10.0
    if nontrivial.mean() < 0.4:
        raise RuntimeError(f"{source_name}: values look degenerate (mostly ~0)")
    for name, lon, lat, lo, hi in _SANITY_POINTS:
        v = grid.value_at(arr, lon, lat)
        if not (lo <= v <= hi):
            raise RuntimeError(f"{source_name}: {name} = {v:.0f} m, expected [{lo}, {hi}]")


def ensure_elevation(force: bool = False) -> np.ndarray:
    """Elevation (meters) on the canonical grid, cached after first fetch."""
    cache = static_dir() / "elevation.tif"
    if cache.exists() and not force:
        with rasterio.open(cache) as src:
            return src.read(1).astype(np.float32)

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    errors = []
    arr: Optional[np.ndarray] = None
    for fetch in (_fetch_ncei_imageserver, _fetch_erddap_etopo):
        try:
            candidate = fetch(session)
            _validate(candidate, fetch.__name__)
            arr = candidate
            break
        except Exception as exc:  # noqa: BLE001 — strategy chain, keep trying
            errors.append(f"{fetch.__name__}: {exc}")
            arr = None
    if arr is None:
        raise RuntimeError("all elevation sources failed: " + " | ".join(errors))

    profile = grid.profile()
    with rasterio.open(cache, "w", **profile) as dst:
        dst.write(arr, 1)
    return arr
