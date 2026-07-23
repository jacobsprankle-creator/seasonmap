"""Forecast model fields from Open-Meteo's AWS Open Data mirror (S3).

Replaces the quota-limited HTTP API for model layers. Uses the `data_run/`
layout of s3://openmeteo (us-west-2, anonymous): ONE file per variable per
model run containing EVERY timestep, with explicit `time`, `unit`, and grid
metadata inside. Strategy: download the handful of variable files a family
needs (~0.3–1.5 GB, seconds on CI), slice the CONUS window locally
(instant), aggregate on the source grid, and resample only the 8 final
daily fields to the canonical 4 km grid.

No API keys, no quotas, no rate limits. Retention: 3 months.

Aggregations per param:
  dmax — daily max over the run's native steps (tmax, gusts, cape)
  acc  — cumulative sum through each lead day (precip; snow = SWE mm x0.7
         -> cm -> inches, matching the HTTP API's derivation)
  s12  — the 12Z step of each lead day (mslp, z500, w250)
Projected domains (HRRR Lambert) are rejected here; the caller falls back
to the HTTP API for that family.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import os
import shutil
import tempfile
from typing import Dict, List, Optional, Tuple

import numpy as np
from affine import Affine

from .. import grid

BUCKET = "openmeteo"
REGION = "us-west-2"
LAT0, LAT1 = 22.0, 52.0     # CONUS window
LONW, LONE = -127.0, -64.0  # CONUS window (degrees east, -180..180)
ACC_HOURS = 192             # 8 lead days
PARAM_KEYS = ["tmax", "precip_accum", "snow_accum", "mslp", "z500", "w250", "gusts", "cape"]

# param -> (role, variable-file(s), agg). Tuple variable = u/v pair -> hypot.
FAMILIES: Dict[str, dict] = {
    "gfs_seamless": {
        "domains": {"sfc": "ncep_gfs013", "prs": "ncep_gfs025"},
        "vars": [
            ("tmax", "sfc", "temperature_2m", "dmax"),
            ("precip_accum", "sfc", "precipitation", "acc"),
            ("snow_accum", "sfc", "snowfall_water_equivalent", "acc"),
            ("gusts", "prs", "wind_gusts_10m", "dmax"),
            ("cape", "prs", "cape", "dmax"),
            ("mslp", "prs", "pressure_msl", "s12"),
            ("z500", "prs", "geopotential_height_500hPa", "s12"),
            ("w250", "prs", ("wind_u_component_250hPa", "wind_v_component_250hPa"), "s12"),
        ],
    },
    "ecmwf_ifs025": {
        "domains": {"sfc": "ecmwf_ifs025"},
        "vars": [
            ("tmax", "sfc", "temperature_2m", "dmax"),
            ("precip_accum", "sfc", "precipitation", "acc"),
            ("snow_accum", "sfc", "snowfall_water_equivalent", "acc"),
            ("gusts", "sfc", "wind_gusts_10m", "dmax"),
            ("cape", "sfc", "cape", "dmax"),
            ("mslp", "sfc", "pressure_msl", "s12"),
            ("z500", "sfc", "geopotential_height_500hPa", "s12"),
            ("w250", "sfc", ("wind_u_component_250hPa", "wind_v_component_250hPa"), "s12"),
        ],
    },
    "gfs_hrrr": {  # projected grid — raises; wrapper falls back to HTTP API
        "domains": {"sfc": "ncep_hrrr_conus"},
        "vars": [
            ("tmax", "sfc", "temperature_2m", "dmax"),
            ("precip_accum", "sfc", "precipitation", "acc"),
            ("snow_accum", "sfc", "snowfall_water_equivalent", "acc"),
            ("gusts", "sfc", "wind_gusts_10m", "dmax"),
            ("cape", "sfc", "cape", "dmax"),
            ("mslp", "sfc", "pressure_msl", "s12"),
        ],
    },
    "ukmo_seamless": {
        "domains": {"sfc": "ukmo_global_deterministic_10km"},
        "vars": [
            ("tmax", "sfc", "temperature_2m", "dmax"),
            ("precip_accum", "sfc", "precipitation", "acc"),
            ("snow_accum", "sfc", "snowfall_water_equivalent", "acc"),
            ("gusts", "sfc", "wind_gusts_10m", "dmax"),
            ("cape", "sfc", "cape", "dmax"),
            ("mslp", "sfc", "pressure_msl", "s12"),
            ("z500", "sfc", "geopotential_height_500hPa", "s12"),
            ("w250", "sfc", "wind_speed_250hPa", "s12"),
        ],
    },
    "icon_seamless": {
        "domains": {"sfc": "dwd_icon"},
        "vars": [
            ("tmax", "sfc", "temperature_2m", "dmax"),
            ("precip_accum", "sfc", "precipitation", "acc"),
            ("snow_accum", "sfc", "snowfall_water_equivalent", "acc"),
            ("gusts", "sfc", "wind_gusts_10m", "dmax"),
            ("cape", "sfc", "cape", "dmax"),
            ("mslp", "sfc", "pressure_msl", "s12"),
            ("z500", "sfc", "geopotential_height_500hPa", "s12"),
            ("w250", "sfc", ("wind_u_component_250hPa", "wind_v_component_250hPa"), "s12"),
        ],
    },
    "gem_seamless": {
        "domains": {"sfc": "cmc_gem_gdps_15km", "prs": "cmc_gem_gdps_15km_upper_level"},
        "vars": [
            ("tmax", "sfc", "temperature_2m", "dmax"),
            ("precip_accum", "sfc", "precipitation", "acc"),
            ("snow_accum", "sfc", "snowfall_water_equivalent", "acc"),
            ("gusts", "sfc", "wind_gusts_10m", "dmax"),
            ("cape", "prs", "cape", "dmax"),
            ("mslp", "sfc", "pressure_msl", "s12"),
            ("z500", "prs", "geopotential_height_500hPa", "s12"),
            ("w250", "prs", ("wind_u_component_250hPa", "wind_v_component_250hPa"), "s12"),
        ],
    },
}

# unit string -> multiplier/function into OUR units per param family
def _convert(param: str, unit: str, arr: np.ndarray) -> np.ndarray:
    u = (unit or "").lower()
    if param == "tmax":
        if "f" in u and "°f" in u:
            return arr
        return arr * 9 / 5 + 32  # °C -> °F
    if param in ("gusts", "w250"):
        if "km" in u:
            return arr * 0.621371
        if "mph" in u or "mi/h" in u:
            return arr
        return arr * 2.23694  # m/s -> mph
    if param == "z500":
        if "gpm" in u or u in ("m", "meter", "metre", "meters"):
            return arr / 10.0  # m -> dam
        return arr / 10.0
    if param == "precip_accum":
        if "inch" in u:
            return arr
        return arr / 25.4  # mm -> in
    if param == "snow_accum":
        # SWE mm -> snowfall cm x0.7 (API parity) -> inches
        return arr * 0.7 / 2.54
    return arr  # mslp hPa, cape J/kg pass through


def _s3():
    import boto3
    from botocore import UNSIGNED
    from botocore.config import Config

    return boto3.client(
        "s3", region_name=REGION, config=Config(signature_version=UNSIGNED, max_pool_connections=24)
    )


def _needed_files(cfg: dict) -> Dict[str, List[str]]:
    """domain -> unique variable filenames needed from it."""
    out: Dict[str, List[str]] = {}
    for _, role, var, _agg in cfg["vars"]:
        domain = cfg["domains"][role]
        for v in (var if isinstance(var, tuple) else (var,)):
            out.setdefault(domain, [])
            if v not in out[domain]:
                out[domain].append(v)
    return out


def _pick_run(s3c, domain: str, want_vars: List[str]) -> Tuple[dt.datetime, str, set]:
    """Newest run (up to 3 days back) that has ALL wanted variable files."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for day_back in range(3):
        day = now - dt.timedelta(days=day_back)
        prefix = f"data_run/{domain}/{day:%Y/%m/%d}/"
        resp = s3c.list_objects_v2(Bucket=BUCKET, Prefix=prefix, Delimiter="/")
        runs = sorted((p["Prefix"] for p in resp.get("CommonPrefixes", [])), reverse=True)
        for run in runs:
            resp2 = s3c.list_objects_v2(Bucket=BUCKET, Prefix=run, MaxKeys=1000)
            have = {o["Key"].rsplit("/", 1)[-1][:-3] for o in resp2.get("Contents", []) if o["Key"].endswith(".om")}
            present = [v for v in want_vars if v in have]
            if len(present) >= max(1, len(want_vars) - 2):  # tolerate e.g. missing snow var
                run_name = run.rstrip("/").rsplit("/", 1)[-1]  # "1800Z"
                run_start = dt.datetime(day.year, day.month, day.day, int(run_name[:2]), int(run_name[2:4]))
                return run_start, run, set(present)
    raise RuntimeError(f"no recent complete run for {domain} (wanted {want_vars})")


class _Var:
    """One downloaded variable file: (lat, lon, T) array + time axis + unit.

    Latitude row ORDER is detected empirically (July hemisphere asymmetry)
    on a reliable variable per domain (t2m / z500) and shared with the
    domain's other files via `ascending`.
    """

    def __init__(self, path: str, ascending: Optional[bool] = None):
        import omfiles

        self.reader = omfiles.OmFileReader.from_path(path)
        if len(self.reader.shape) != 3:
            raise RuntimeError(f"expected (y,x,T), got {self.reader.shape}")
        self.shape = self.reader.shape  # (lat, lon, time)
        t = self.reader.get_child_by_name("time")
        tvals = np.asarray(t.read_array((slice(0, t.shape[0]),)))
        self.times = tvals.astype("datetime64[s]") if tvals.dtype.kind in "iu" else tvals
        try:
            self.unit = str(self.reader.get_child_by_name("unit").read_scalar())
        except Exception:
            self.unit = ""
        nlat, nlon = self.shape[0], self.shape[1]
        self.nlat = nlat
        # Authoritative geography from the file's own crs_wkt: GEOGCRS means
        # a regular lat/lon grid (any cell aspect — UKMO is 0.09°x0.14°);
        # PROJCRS (e.g. HRRR Lambert) is rejected -> caller falls back to API.
        import re as _re

        south, west, north, east = -90.0, -180.0, 90.0, 180.0
        try:
            wkt = str(self.reader.get_child_by_name("crs_wkt").read_scalar())
        except Exception:
            wkt = ""
        if wkt and not wkt.lstrip().startswith("GEOGCRS"):
            raise RuntimeError(f"projected grid ({wkt[:40]!r}) — S3 path handles lat/lon only")
        m = _re.search(r"BBOX\[([^\]]+)\]", wkt) if wkt else None
        if m:
            south, west, north, east = (float(x) for x in m.group(1).split(","))
        self.south, self.west = south, west
        self.dlat = (north - south) / (nlat - 1)
        self.dlon = (east - west) / (nlon - 1)
        self.c0 = max(0, int((LONW - west) / self.dlon))
        self.c1 = min(nlon, int((LONE - west) / self.dlon) + 2)
        if ascending is None:
            # Row ORDER probe (BBOX doesn't state it): under the ascending
            # assumption the +40° row is warmer/higher than −40° in NH summer.
            rowA = int((40.0 - south) / self.dlat)
            rowB = nlat - 1 - rowA
            a = np.nanmean(np.asarray(self.reader[rowA : rowA + 1, self.c0 : self.c1, 0:2], dtype=np.float32))
            b = np.nanmean(np.asarray(self.reader[rowB : rowB + 1, self.c0 : self.c1, 0:2], dtype=np.float32))
            ascending = bool(a > b)
        self.ascending = ascending
        r0a = max(0, int((LAT0 - south) / self.dlat))
        r1a = min(nlat, int((LAT1 - south) / self.dlat) + 2)
        if ascending:
            self.r0, self.r1 = r0a, r1a
        else:  # rows run north -> south; mirror the window
            self.r0, self.r1 = nlat - r1a, nlat - r0a

    def conus(self) -> np.ndarray:
        """(T, y, x) CONUS cube — time axis moved to front."""
        block = np.asarray(
            self.reader[self.r0 : self.r1, self.c0 : self.c1, 0 : self.shape[2]], dtype=np.float32
        )
        return np.moveaxis(block, -1, 0)

    def to_canonical(self, field: np.ndarray) -> np.ndarray:
        from rasterio.warp import Resampling

        oriented = np.flipud(field) if self.ascending else field  # ensure north-down
        if self.ascending:
            north_edge = self.south + self.r1 * self.dlat
        else:
            north_edge = (self.south + (self.nlat - 1) * self.dlat) - self.r0 * self.dlat
        west_edge = self.west + self.c0 * self.dlon
        transform = Affine(self.dlon, 0.0, west_edge, 0.0, -self.dlat, north_edge)
        arr = np.where(np.isnan(oriented), grid.NODATA, oriented)
        return grid.resample_to_grid(
            arr, transform, grid.CRS_CANONICAL,
            resampling=Resampling.cubic_spline, src_nodata=grid.NODATA,
        )


def fetch_models_batch(model_ids: List[str], run_key: str) -> Dict[str, tuple]:
    """Same contract as open_meteo.fetch_models_batch, sourced from S3."""
    from .elevation import static_dir

    results: Dict[str, tuple] = {}
    for mid in model_ids:
        disk = static_dir() / f"openmeteo_s3_{mid}_{run_key}.npz"
        if disk.exists():
            z = np.load(disk)
            results[mid] = ([str(s) for s in z["dates"]], {k: z[k] for k in PARAM_KEYS})
            continue
        dates, fields = _fetch_family(mid)
        np.savez_compressed(disk, dates=np.array(dates), **fields)
        results[mid] = (dates, fields)
    return results


def _fetch_family(mid: str) -> Tuple[List[str], Dict[str, np.ndarray]]:
    cfg = FAMILIES[mid]
    s3c = _s3()
    needed = _needed_files(cfg)
    runs: Dict[str, Tuple[dt.datetime, str, set]] = {
        d: _pick_run(s3c, d, vs) for d, vs in needed.items()
    }
    run_start = min(r[0] for r in runs.values())
    # A 12Z/18Z run has no 12Z step (and only a sliver of daily stats) for its
    # own calendar day — start the 8-day window at the next day for late runs.
    first = run_start.date() + dt.timedelta(days=0 if run_start.hour < 12 else 1)
    dates = [(first + dt.timedelta(days=d)).isoformat() for d in range(8)]
    date_objs = [dt.date.fromisoformat(d) for d in dates]

    tmpdir = tempfile.mkdtemp(prefix=f"oms3-{mid.split('_')[0]}-")
    try:
        # Download every needed variable file (parallel).
        jobs = []
        for domain, vs in needed.items():
            _, run_prefix, present = runs[domain]
            for v in vs:
                if v in present:
                    jobs.append((f"{run_prefix}{v}.om", os.path.join(tmpdir, f"{domain}__{v}.om")))
        def dl(job):
            key, path = job
            s3c.download_file(BUCKET, key, path)
            return path
        with cf.ThreadPoolExecutor(max_workers=4) as ex:
            list(ex.map(dl, jobs))

        loaded: Dict[Tuple[str, str], _Var] = {}
        orientation: Dict[str, bool] = {}

        def var_of(role: str, name: str) -> Optional[_Var]:
            domain = cfg["domains"][role]
            k = (domain, name)
            if k not in loaded:
                path = os.path.join(tmpdir, f"{domain}__{name}.om")
                if not os.path.exists(path):
                    return None
                loaded[k] = _Var(path, ascending=orientation.get(domain))
                orientation.setdefault(domain, loaded[k].ascending)
            return loaded[k]

        # Decide each domain's latitude orientation on a July-reliable
        # variable (t2m warm-north / z500 high-north) before anything else.
        for domain in cfg["domains"].values():
            for probe in ("temperature_2m", "geopotential_height_500hPa", "cape"):
                path = os.path.join(tmpdir, f"{domain}__{probe}.om")
                if os.path.exists(path):
                    role = next(r for r, d in cfg["domains"].items() if d == domain)
                    var_of(role, probe)
                    break

        out: Dict[str, np.ndarray] = {}
        for param in PARAM_KEYS:
            spec = next(((r, v, a) for p, r, v, a in cfg["vars"] if p == param), None)
            frames = np.full((8,) + grid.SHAPE, grid.NODATA, dtype=np.float32)
            if spec is None:
                out[param] = frames
                continue
            role, var, agg = spec
            if isinstance(var, tuple):
                vu, vv = var_of(role, var[0]), var_of(role, var[1])
                if vu is None or vv is None:
                    out[param] = frames
                    continue
                cube = np.hypot(vu.conus(), vv.conus())
                ref = vu
            else:
                ref = var_of(role, var)
                if ref is None:
                    out[param] = frames
                    continue
                cube = ref.conus()
            times = ref.times  # datetime64[s]
            lead_ok = (times > np.datetime64(run_start)) & (
                times <= np.datetime64(run_start + dt.timedelta(hours=ACC_HOURS))
            )
            cube, times = cube[lead_ok], times[lead_ok]
            if cube.shape[0] == 0:
                out[param] = frames
                continue
            days = times.astype("datetime64[D]")
            if agg == "acc":
                run_sum = np.cumsum(np.nan_to_num(cube), axis=0)
                for i, d in enumerate(date_objs):
                    idx = np.where(days <= np.datetime64(d))[0]
                    if idx.size:
                        frames[i] = ref.to_canonical(_convert(param, ref.unit, run_sum[idx[-1]]))
            elif agg == "dmax":
                for i, d in enumerate(date_objs):
                    idx = np.where(days == np.datetime64(d))[0]
                    if idx.size:
                        field = np.nanmax(cube[idx], axis=0)
                        frames[i] = ref.to_canonical(_convert(param, ref.unit, field))
            else:  # s12
                hours = (times.astype("datetime64[h]") - days).astype(int)
                for i, d in enumerate(date_objs):
                    idx = np.where((days == np.datetime64(d)) & (hours == 12))[0]
                    if idx.size:
                        frames[i] = ref.to_canonical(_convert(param, ref.unit, cube[idx[0]]))
            out[param] = frames
        return dates, out
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Air quality (CAMS) + waves (GFS-Wave) — same S3 machinery, no HTTP API.
# ---------------------------------------------------------------------------

_PM25_C = [0.0, 12.0, 35.4, 55.4, 150.4, 250.4, 500.4]
_PM25_I = [0.0, 50.0, 100.0, 150.0, 200.0, 300.0, 500.0]
_O3_C = [0.0, 54.0, 70.0, 85.0, 105.0, 200.0]  # ppb
_O3_I = [0.0, 50.0, 100.0, 150.0, 200.0, 300.0]


def _now_field(v: "_Var") -> np.ndarray:
    """The timestep nearest to now (UTC) from a variable cube."""
    now = np.datetime64(dt.datetime.utcnow().replace(microsecond=0))
    idx = int(np.abs(v.times - now).argmin())
    cube = v.conus()
    return cube[min(idx, cube.shape[0] - 1)]


def _fetch_domain(domain: str, want: List[str]) -> Dict[str, "_Var"]:
    s3c = _s3()
    _, run_prefix, present = _pick_run(s3c, domain, want)
    tmpdir = tempfile.mkdtemp(prefix=f"oms3-{domain[:8]}-")
    out: Dict[str, _Var] = {}
    ascending: Optional[bool] = None
    for v in want:
        if v not in present:
            continue
        path = os.path.join(tmpdir, f"{v}.om")
        s3c.download_file(BUCKET, f"{run_prefix}{v}.om", path)
        out[v] = _Var(path, ascending=ascending)
        ascending = out[v].ascending
    out["__tmpdir__"] = tmpdir  # type: ignore[assignment]
    return out


def fetch_air_now() -> Dict[str, np.ndarray]:
    """Current us_aqi / pm2_5 / aerosol_optical_depth on the canonical grid.

    AQI = max of the EPA PM2.5 and ozone sub-indices (piecewise-linear via
    np.interp on the official breakpoints; instantaneous concentrations, the
    same approximation the HTTP API's `us_aqi` makes).
    """
    vs = _fetch_domain("cams_global", ["pm2_5", "aerosol_optical_depth", "ozone"])
    try:
        pm_v = vs["pm2_5"]
        pm = _now_field(pm_v)
        aod = _now_field(vs["aerosol_optical_depth"])
        oz_ppb = _now_field(vs["ozone"]) / 1.962 if "ozone" in vs else None
        aqi = np.interp(pm, _PM25_C, _PM25_I)
        if oz_ppb is not None:
            aqi = np.fmax(aqi, np.interp(oz_ppb, _O3_C, _O3_I))
        return {
            "us_aqi": pm_v.to_canonical(aqi.astype(np.float32)),
            "pm2_5": pm_v.to_canonical(pm),
            "aerosol_optical_depth": pm_v.to_canonical(aod),
        }
    finally:
        shutil.rmtree(vs.pop("__tmpdir__"), ignore_errors=True)  # type: ignore[arg-type]


def fetch_waves_daily() -> Tuple[List[str], np.ndarray]:
    """(dates8, (8,H,W) daily-max wave height in feet). Land stays NODATA."""
    vs = _fetch_domain("ncep_gfswave025", ["wave_height"])
    try:
        v = vs["wave_height"]
        cube = v.conus() * 3.28084  # m -> ft
        days = v.times.astype("datetime64[D]")
        first = v.times[0].astype("datetime64[D]")
        dates = [str(first + np.timedelta64(d, "D")) for d in range(8)]
        stack = np.full((8,) + grid.SHAPE, grid.NODATA, dtype=np.float32)
        for i, d in enumerate(dates):
            idx = np.where(days == np.datetime64(d))[0]
            if idx.size:
                stack[i] = v.to_canonical(np.nanmax(cube[idx], axis=0))
        return dates, stack
    finally:
        shutil.rmtree(vs.pop("__tmpdir__"), ignore_errors=True)  # type: ignore[arg-type]
