"""Open-Meteo sibling APIs: air quality (CAMS) and marine (waves).

Same free/keyless terms and the same coarse-grid batching pattern as the main
forecast API; hour-keyed disk caches.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Tuple

import numpy as np
import requests
from affine import Affine
from rasterio.warp import Resampling

from .. import grid
from .elevation import USER_AGENT, static_dir
from .open_meteo import BATCH, _coarse_grid

AIR_API = "https://air-quality-api.open-meteo.com/v1/air-quality"
MARINE_API = "https://marine-api.open-meteo.com/v1/marine"
AIR_VARS = ["us_aqi", "pm2_5", "aerosol_optical_depth"]
_memo: Dict[str, object] = {}


def _batched(api: str, extra: Dict[str, str], parse):
    spacing = float(os.environ.get("OPEN_METEO_SPACING_DEG", "0.5"))
    lons, lats = _coarse_grid(spacing)
    lon_g, lat_g = np.meshgrid(lons, lats)
    flat_lons, flat_lats = lon_g.ravel(), lat_g.ravel()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    n = flat_lons.size
    backoffs = [15, 30, 60, 90, 120]
    state: dict = {"n": n}
    for start in range(0, n, BATCH):
        sl = slice(start, min(start + BATCH, n))
        params = {
            "latitude": ",".join(f"{v:.3f}" for v in flat_lats[sl]),
            "longitude": ",".join(f"{v:.3f}" for v in flat_lons[sl]),
            "timezone": "UTC",
            **extra,
        }
        for attempt in range(len(backoffs) + 1):
            resp = session.get(api, params=params, timeout=120)
            if resp.status_code == 429 and attempt < len(backoffs):
                time.sleep(float(resp.headers.get("Retry-After") or backoffs[attempt]))
                continue
            resp.raise_for_status()
            break
        payload = resp.json()
        results = payload if isinstance(payload, list) else [payload]
        for k, res in enumerate(results):
            parse(state, start + k, res)
        time.sleep(0.15)

    transform = Affine(
        spacing, 0.0, float(lons[0]) - spacing / 2, 0.0, -spacing, float(lats[0]) + spacing / 2
    )

    def to_grid(flat_row: np.ndarray) -> np.ndarray:
        src = np.where(np.isnan(flat_row.reshape(lat_g.shape)), grid.NODATA, flat_row.reshape(lat_g.shape))
        return grid.resample_to_grid(
            src, transform, grid.CRS_CANONICAL,
            resampling=Resampling.cubic_spline, src_nodata=grid.NODATA,
        )

    return state, to_grid


def fetch_air_quality(run_key: str) -> Dict[str, np.ndarray]:
    """Current us_aqi / pm2_5 / aerosol_optical_depth on the canonical grid."""
    if f"air:{run_key}" in _memo:
        return _memo[f"air:{run_key}"]  # type: ignore[return-value]
    disk = static_dir() / f"openmeteo_air_{run_key}.npz"
    if disk.exists():
        z = np.load(disk)
        fields = {v: z[v] for v in AIR_VARS}
        _memo[f"air:{run_key}"] = fields
        return fields

    try:
        from . import openmeteo_s3

        fields = openmeteo_s3.fetch_air_now()
        import numpy as _np

        _np.savez_compressed(disk, **fields)
        _memo[f"air:{run_key}"] = fields
        return fields
    except Exception as exc:  # noqa: BLE001
        print(f"  [air-source] s3 failed ({exc}); falling back to API", flush=True)

    def parse(state, idx, res):
        cur = res.get("current", {})
        if "vals" not in state:
            state["vals"] = {v: np.full(state["n"], np.nan, dtype=np.float32) for v in AIR_VARS}
        for v in AIR_VARS:
            x = cur.get(v)
            if x is not None:
                state["vals"][v][idx] = x

    state, to_grid = _batched(AIR_API, {"current": ",".join(AIR_VARS)}, parse)
    fields = {v: to_grid(state["vals"][v]) for v in AIR_VARS}
    np.savez_compressed(disk, **fields)
    _memo[f"air:{run_key}"] = fields
    return fields


def fetch_waves(run_key: str) -> Tuple[List[str], np.ndarray]:
    """Daily max wave height (ft) stacks, 8 lead days. Land points come back
    null from the marine API and stay NODATA."""
    if f"marine:{run_key}" in _memo:
        return _memo[f"marine:{run_key}"]  # type: ignore[return-value]
    disk = static_dir() / f"openmeteo_marine_{run_key}.npz"
    if disk.exists():
        z = np.load(disk)
        result = ([str(s) for s in z["dates"]], z["waves"])
        _memo[f"marine:{run_key}"] = result
        return result

    try:
        from . import openmeteo_s3

        result = openmeteo_s3.fetch_waves_daily()
        import numpy as _np

        _np.savez_compressed(disk, dates=_np.array(result[0]), waves=result[1])
        _memo[f"marine:{run_key}"] = result
        return result
    except Exception as exc:  # noqa: BLE001
        print(f"  [waves-source] s3 failed ({exc}); falling back to API", flush=True)

    def parse(state, idx, res):
        daily = res.get("daily", {})
        if "dates" not in state:
            state["dates"] = daily.get("time", []) or state.get("dates", [])
            if state["dates"]:
                state["vals"] = np.full((len(state["dates"]), state["n"]), np.nan, dtype=np.float32)
        vals = daily.get("wave_height_max") or []
        for d, v in enumerate(vals[: len(state.get("dates", []))]):
            if v is not None:
                state["vals"][d, idx] = v * 3.28084  # m → ft

    state, to_grid = _batched(
        MARINE_API, {"daily": "wave_height_max", "forecast_days": "8"}, parse
    )
    dates = state.get("dates", [])
    stack = (
        np.stack([to_grid(state["vals"][d]) for d in range(len(dates))])
        if dates
        else np.zeros((0,) + grid.SHAPE, dtype=np.float32)
    )
    np.savez_compressed(disk, dates=np.array(dates), waves=stack)
    result = (dates, stack)
    _memo[f"marine:{run_key}"] = result
    return result
