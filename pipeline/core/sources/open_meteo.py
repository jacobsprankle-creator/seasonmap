"""Open-Meteo daily forecast on a coarse CONUS point grid.

Rule from the build plan: NEVER per-cell requests. We fetch a coarse regular
lat/lon point grid (default 0.5°, env OPEN_METEO_SPACING_DEG), batched ~100
locations per call, then bilinearly resample each day's field onto the
canonical grid. One fetch serves every layer in a run (module-level cache).

Variables: temperature_2m_min (°C, freeze threshold math) and snowfall_sum
(cm). past_days=3 covers the slider's trailing dates.
"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Tuple

import numpy as np
import requests
from affine import Affine

from .. import grid
from .elevation import USER_AGENT

API = "https://api.open-meteo.com/v1/forecast"
DAILY_VARS = ["temperature_2m_min", "snowfall_sum"]
BATCH = 100
FORECAST_DAYS = 10
PAST_DAYS = 3

_run_cache: Dict[str, Tuple[List[str], Dict[str, np.ndarray]]] = {}
_current_cache: Dict[str, Dict[str, np.ndarray]] = {}

CURRENT_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "dew_point_2m",
    "wind_speed_10m",
]


def _coarse_grid(spacing: float) -> Tuple[np.ndarray, np.ndarray]:
    west, south, east, north = grid.BOUNDS
    lons = np.arange(west + spacing / 2, east, spacing)
    lats = np.arange(north - spacing / 2, south, -spacing)
    return lons, lats


def _fetch_points(session: requests.Session, lats: List[float], lons: List[float]) -> list:
    params = {
        "latitude": ",".join(f"{v:.3f}" for v in lats),
        "longitude": ",".join(f"{v:.3f}" for v in lons),
        "daily": ",".join(DAILY_VARS),
        "forecast_days": str(FORECAST_DAYS),
        "past_days": str(PAST_DAYS),
        "temperature_unit": "celsius",
        "timezone": "UTC",
    }
    backoffs = [15, 30, 60, 90, 120]  # minutely quota resets on the minute
    for attempt in range(len(backoffs) + 1):
        resp = session.get(API, params=params, timeout=120)
        if resp.status_code == 429 and attempt < len(backoffs):
            wait = float(resp.headers.get("Retry-After") or backoffs[attempt])
            time.sleep(wait)
            continue
        resp.raise_for_status()
        payload = resp.json()
        return payload if isinstance(payload, list) else [payload]
    raise RuntimeError("Open-Meteo retries exhausted")  # pragma: no cover


def fetch_current_fields(run_key: str) -> Dict[str, np.ndarray]:
    """Current conditions (°F, %, mph) on the canonical grid.

    Disk-cached keyed to the HOUR (run_key) — the layer's freshness contract
    is hourly, and the hourly workflow runs each variant as its own process,
    so an hour-keyed cache means one upstream fetch per hour, not four."""
    if run_key in _current_cache:
        return _current_cache[run_key]

    from .elevation import static_dir

    spacing = float(os.environ.get("OPEN_METEO_SPACING_DEG", "0.5"))
    disk = static_dir() / f"openmeteo_current_{run_key}_{spacing:g}.npz"
    if disk.exists():
        z = np.load(disk)
        fields = {var: z[var] for var in CURRENT_VARS}
        _current_cache[run_key] = fields
        return fields
    lons, lats = _coarse_grid(spacing)
    lon_g, lat_g = np.meshgrid(lons, lats)
    flat_lons, flat_lats = lon_g.ravel(), lat_g.ravel()

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    n = flat_lons.size
    coarse = {var: np.full(n, np.nan, dtype=np.float32) for var in CURRENT_VARS}
    backoffs = [15, 30, 60, 90, 120]
    for start in range(0, n, BATCH):
        sl = slice(start, min(start + BATCH, n))
        params = {
            "latitude": ",".join(f"{v:.3f}" for v in flat_lats[sl]),
            "longitude": ",".join(f"{v:.3f}" for v in flat_lons[sl]),
            "current": ",".join(CURRENT_VARS),
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "timezone": "UTC",
        }
        for attempt in range(len(backoffs) + 1):
            resp = session.get(API, params=params, timeout=120)
            if resp.status_code == 429 and attempt < len(backoffs):
                time.sleep(float(resp.headers.get("Retry-After") or backoffs[attempt]))
                continue
            resp.raise_for_status()
            break
        payload = resp.json()
        results = payload if isinstance(payload, list) else [payload]
        for k, res in enumerate(results):
            cur = res.get("current", {})
            for var in CURRENT_VARS:
                v = cur.get(var)
                if v is not None:
                    coarse[var][start + k] = v
        time.sleep(0.15)

    coarse_transform = Affine(
        spacing, 0.0, float(lons[0]) - spacing / 2, 0.0, -spacing, float(lats[0]) + spacing / 2
    )
    from rasterio.warp import Resampling

    fields: Dict[str, np.ndarray] = {}
    for var, flat in coarse.items():
        src = np.where(np.isnan(flat.reshape(lat_g.shape)), grid.NODATA, flat.reshape(lat_g.shape))
        # Cubic-spline: the coarse point-grid analysis renders as smooth
        # gradients instead of visible blobs on 4 km cells.
        fields[var] = grid.resample_to_grid(
            src,
            coarse_transform,
            grid.CRS_CANONICAL,
            resampling=Resampling.cubic_spline,
            src_nodata=grid.NODATA,
        )
    np.savez_compressed(disk, **fields)
    _current_cache[run_key] = fields
    return fields


MODEL_DAILY = ["temperature_2m_max", "precipitation_sum", "snowfall_sum", "wind_gusts_10m_max"]
MODEL_HOURLY = ["pressure_msl", "geopotential_height_500hPa", "wind_speed_250hPa", "cape"]


def fetch_model_fields(model_id: str, run_key: str):
    """Per-model forecast fields on the canonical grid, 8 lead days.

    Returns (dates, {param: (D, H, W)}) with params:
      tmax °F · precip_accum in (cumulative) · snow_accum in (cumulative) ·
      mslp hPa (12Z) · z500 dam (12Z) · w250 mph (12Z jet-level wind)
    One upstream fetch per model per hour (disk-cached); hourly vars pulled at
    6-hour resolution and snapshotted at 12Z per day.
    """
    from .elevation import static_dir

    cache_id = f"{model_id}:{run_key}"
    if cache_id in _current_cache:
        return _current_cache[cache_id]  # type: ignore[return-value]

    spacing = float(os.environ.get("OPEN_METEO_SPACING_DEG", "0.5"))
    params_keys = ["tmax", "precip_accum", "snow_accum", "mslp", "z500", "w250", "gusts", "cape"]
    disk = static_dir() / f"openmeteo_modelx_{model_id}_{run_key}_{spacing:g}.npz"
    if disk.exists():
        z = np.load(disk)
        result = ([str(s) for s in z["dates"]], {k: z[k] for k in params_keys})
        _current_cache[cache_id] = result  # type: ignore[assignment]
        return result

    lons, lats = _coarse_grid(spacing)
    lon_g, lat_g = np.meshgrid(lons, lats)
    flat_lons, flat_lats = lon_g.ravel(), lat_g.ravel()
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    def field(daily_or_hourly: dict, name: str):
        return daily_or_hourly.get(name) or daily_or_hourly.get(f"{name}_{model_id}") or []

    n = flat_lons.size
    dates: List[str] = []
    hours: List[str] = []
    raw: Dict[str, np.ndarray] = {}
    backoffs = [15, 30, 60, 90, 120]
    for start in range(0, n, BATCH):
        sl = slice(start, min(start + BATCH, n))
        req = {
            "latitude": ",".join(f"{v:.3f}" for v in flat_lats[sl]),
            "longitude": ",".join(f"{v:.3f}" for v in flat_lons[sl]),
            "daily": ",".join(MODEL_DAILY),
            "hourly": ",".join(MODEL_HOURLY),
            "temporal_resolution": "hourly_6",
            "models": model_id,
            "forecast_days": "8",
            "temperature_unit": "fahrenheit",
            "wind_speed_unit": "mph",
            "precipitation_unit": "inch",
            "timezone": "UTC",
        }
        for attempt in range(len(backoffs) + 1):
            resp = session.get(API, params=req, timeout=180)
            if resp.status_code == 429 and attempt < len(backoffs):
                time.sleep(float(resp.headers.get("Retry-After") or backoffs[attempt]))
                continue
            resp.raise_for_status()
            break
        payload = resp.json()
        results = payload if isinstance(payload, list) else [payload]
        for k, res in enumerate(results):
            daily = res.get("daily", {})
            hourly = res.get("hourly", {})
            if not dates:
                dates = daily.get("time", [])
                hours = hourly.get("time", [])
                for key in ("tmax", "precip_d", "snow_d", "gusts_d"):
                    raw[key] = np.full((len(dates), n), np.nan, dtype=np.float32)
                for key in ("mslp", "z500", "w250", "cape_h"):
                    raw[key] = np.full((len(hours), n), np.nan, dtype=np.float32)
            for key, name in (("tmax", "temperature_2m_max"), ("precip_d", "precipitation_sum"), ("snow_d", "snowfall_sum"), ("gusts_d", "wind_gusts_10m_max")):
                for d, v in enumerate(field(daily, name)[: len(dates)]):
                    if v is not None:
                        raw[key][d, start + k] = v
            for key, name in (("mslp", "pressure_msl"), ("z500", "geopotential_height_500hPa"), ("w250", "wind_speed_250hPa"), ("cape_h", "cape")):
                for h, v in enumerate(field(hourly, name)[: len(hours)]):
                    if v is not None:
                        raw[key][h, start + k] = v
        time.sleep(0.15)

    # 12Z snapshot index per lead day for the synoptic fields.
    idx12 = []
    for d in dates:
        want = f"{d}T12:00"
        idx12.append(hours.index(want) if want in hours else min(range(len(hours)), key=lambda i: abs(i - 2)))

    from rasterio.warp import Resampling

    coarse_transform = Affine(
        spacing, 0.0, float(lons[0]) - spacing / 2, 0.0, -spacing, float(lats[0]) + spacing / 2
    )

    def to_grid(flat_row: np.ndarray) -> np.ndarray:
        src = np.where(np.isnan(flat_row.reshape(lat_g.shape)), grid.NODATA, flat_row.reshape(lat_g.shape))
        return grid.resample_to_grid(
            src, coarse_transform, grid.CRS_CANONICAL,
            resampling=Resampling.cubic_spline, src_nodata=grid.NODATA,
        )

    D = len(dates)
    out: Dict[str, np.ndarray] = {k: np.zeros((D,) + grid.SHAPE, dtype=np.float32) for k in params_keys}
    precip_run = np.zeros(n, dtype=np.float32)
    snow_run = np.zeros(n, dtype=np.float32)
    for d in range(D):
        out["tmax"][d] = to_grid(raw["tmax"][d])
        precip_run = precip_run + np.nan_to_num(raw["precip_d"][d])
        snow_run = snow_run + np.nan_to_num(raw["snow_d"][d])  # API returns snowfall in cm even w/ inch precip unit? normalize below
        out["precip_accum"][d] = to_grid(precip_run.copy())
        out["snow_accum"][d] = to_grid(snow_run.copy() / 2.54)  # cm → in
        out["mslp"][d] = to_grid(raw["mslp"][idx12[d]])
        z = to_grid(raw["z500"][idx12[d]])
        valid = z != grid.NODATA
        z[valid] = z[valid] / 10.0  # m → dam
        out["z500"][d] = z
        out["w250"][d] = to_grid(raw["w250"][idx12[d]])
        out["gusts"][d] = to_grid(raw["gusts_d"][d])
        # CAPE: daily max across the day's 6-hourly values (peaks mid-afternoon).
        day_prefix = dates[d]
        day_idx = [i for i, h in enumerate(hours) if h.startswith(day_prefix)]
        cape_day = np.nanmax(raw["cape_h"][day_idx], axis=0) if day_idx else raw["cape_h"][idx12[d]]
        out["cape"][d] = to_grid(cape_day)

    np.savez_compressed(disk, dates=np.array(dates), **out)
    result = (dates, out)
    _current_cache[cache_id] = result  # type: ignore[assignment]
    return result


def fetch_daily_fields(run_date: str) -> Tuple[List[str], Dict[str, np.ndarray]]:
    """Returns (dates, {var: (D, H, W) canonical-grid stack}) for one run.

    Cached in-process AND on disk (static_cache), so frost + snowline share
    one upstream fetch even across separate pipeline invocations on the same
    run date.
    """
    if run_date in _run_cache:
        return _run_cache[run_date]

    from .elevation import static_dir

    spacing = float(os.environ.get("OPEN_METEO_SPACING_DEG", "0.5"))
    disk = static_dir() / f"openmeteo_{run_date}_{spacing:g}.npz"
    if disk.exists():
        z = np.load(disk, allow_pickle=False)
        dates = [str(s) for s in z["dates"]]
        fields = {var: z[var] for var in DAILY_VARS}
        _run_cache[run_date] = (dates, fields)
        return _run_cache[run_date]
    lons, lats = _coarse_grid(spacing)
    lon_g, lat_g = np.meshgrid(lons, lats)
    flat_lons, flat_lats = lon_g.ravel(), lat_g.ravel()

    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT

    n = flat_lons.size
    coarse: Dict[str, np.ndarray] = {}
    dates: List[str] = []
    for start in range(0, n, BATCH):
        sl = slice(start, min(start + BATCH, n))
        results = _fetch_points(session, list(flat_lats[sl]), list(flat_lons[sl]))
        if not dates:
            dates = results[0]["daily"]["time"]
            for var in DAILY_VARS:
                coarse[var] = np.full((len(dates), n), np.nan, dtype=np.float32)
        for k, res in enumerate(results):
            daily = res.get("daily", {})
            for var in DAILY_VARS:
                vals = daily.get(var) or []
                for d, v in enumerate(vals[: len(dates)]):
                    if v is not None:
                        coarse[var][d, start + k] = v
        time.sleep(0.15)  # rate-limit politeness

    coarse_transform = Affine(
        spacing, 0.0, float(lons[0]) - spacing / 2, 0.0, -spacing, float(lats[0]) + spacing / 2
    )
    fields: Dict[str, np.ndarray] = {}
    for var, flat in coarse.items():
        days = []
        for d in range(len(dates)):
            src = flat[d].reshape(lat_g.shape)
            src = np.where(np.isnan(src), grid.NODATA, src)
            days.append(
                grid.resample_to_grid(
                    src, coarse_transform, grid.CRS_CANONICAL, src_nodata=grid.NODATA
                )
            )
        fields[var] = np.stack(days)

    np.savez_compressed(disk, dates=np.array(dates), **fields)
    _run_cache[run_date] = (dates, fields)
    return _run_cache[run_date]
