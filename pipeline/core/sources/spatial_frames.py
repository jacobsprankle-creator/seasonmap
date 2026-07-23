"""Per-timestep frame reading over Open-Meteo's `data_spatial/` mirror.

The streaming-ingest source: one .om file per model timestep, landing on S3
in near-real-time WHILE the model runs. This module lists a run's available
frames and reads the motion variables (t2m / precipitation / mslp) for
single timesteps via the async omfiles reader (~2 s per CONUS variable,
byte-ranged — no bulk downloads).

Grids are fully self-describing:
  * GEOGCRS (regular lat/lon) — georeference from the crs_wkt BBOX, row
    order probed empirically (NH-summer warm-north test).
  * PROJCRS (HRRR Lambert) — rasterio CRS from the embedded WKT; the affine
    is derived by projecting the BBOX's geographic corners. No constants.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import re
from typing import Dict, List, Optional, Tuple

import numpy as np
from affine import Affine

from .. import grid

BUCKET = "openmeteo"
REGION = "us-west-2"
LAT0, LAT1 = 22.0, 52.0
LONW, LONE = -127.0, -64.0
MOTION_VARS = ("temperature_2m", "precipitation", "pressure_msl")

# family -> ordered (domain, vars-from-that-domain) pairs. A frame exists
# only when every needed domain has that timestep's file.
STREAM_DOMAINS: Dict[str, List[Tuple[str, Tuple[str, ...]]]] = {
    "gfs_seamless": [
        ("ncep_gfs013", ("temperature_2m", "precipitation")),
        ("ncep_gfs025", ("pressure_msl",)),
    ],
    "ecmwf_ifs025": [("ecmwf_ifs025", MOTION_VARS)],
    "gfs_hrrr": [("ncep_hrrr_conus", MOTION_VARS)],
    "ukmo_seamless": [("ukmo_global_deterministic_10km", MOTION_VARS)],
    "icon_seamless": [("dwd_icon", MOTION_VARS)],
    "gem_seamless": [("cmc_gem_gdps_15km", MOTION_VARS)],
}

_TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{4})\.om$")


def _fs_sync():
    import s3fs

    return s3fs.S3FileSystem(anon=True, client_kwargs={"region_name": REGION})


def _fs_async():
    import s3fs

    return s3fs.S3FileSystem(
        anon=True, asynchronous=True,
        client_kwargs={"region_name": REGION},
        config_kwargs={"max_pool_connections": 64},  # HRRR full-grid reads = hundreds of ranged GETs/var
    )


def _scan_run_dir(fs, run_dir: str) -> Dict[str, str]:
    """{frame_key: s3_key} for one run directory (excludes *-level files)."""
    frames: Dict[str, str] = {}
    for key in fs.ls(run_dir, detail=False):
        name = key.rsplit("/", 1)[-1]
        m = _TS_RE.search(name)
        if m and "-level" not in name[:-3]:
            frames[m.group(1)] = key
    return frames


def list_run_frames(domain: str) -> Tuple[dt.datetime, str, Dict[str, str]]:
    """Newest run of a domain -> (run_start, run_key, {frame_key: s3_key}).

    frame_key = "YYYY-MM-DDTHHMM" (valid time) · run_key = "YYYYMMDDTHHZ".
    """
    fs = _fs_sync()
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for day_back in range(2):
        day = now - dt.timedelta(days=day_back)
        base = f"{BUCKET}/data_spatial/{domain}/{day:%Y/%m/%d}"
        try:
            runs = sorted(fs.ls(base, detail=False), reverse=True)
        except FileNotFoundError:
            continue
        for run in runs:
            run_name = run.rstrip("/").rsplit("/", 1)[-1]  # "1300Z"
            frames = _scan_run_dir(fs, run)
            if frames:
                run_start = dt.datetime(day.year, day.month, day.day, int(run_name[:2]), int(run_name[2:4]))
                return run_start, f"{day:%Y%m%d}T{run_name[:2]}Z", frames
    raise RuntimeError(f"no recent run with frames for {domain}")


def list_run_frames_at(domain: str, run_key: str) -> Tuple[dt.datetime, str, Dict[str, str]]:
    """Frames of one SPECIFIC run ("YYYYMMDDTHHZ"). FileNotFoundError if gone."""
    day = dt.datetime.strptime(run_key[:8], "%Y%m%d")
    hh = run_key[9:11]
    run_dir = f"{BUCKET}/data_spatial/{domain}/{day:%Y/%m/%d}/{hh}00Z"
    frames = _scan_run_dir(_fs_sync(), run_dir)
    return day.replace(hour=int(hh)), run_key, frames


def _probe_plan(shape: Tuple[int, int], wkt: str):
    """(rowA, rowB, c0, c1, projected) — where to strip-read for orientation."""
    nlat, nlon = shape
    projected = not wkt.lstrip().startswith("GEOGCRS")
    if projected:
        return nlat // 8, nlat - nlat // 8, 0, nlon, True
    m = re.search(r"BBOX\[([^\]]+)\]", wkt)
    south, west, north, east = (float(x) for x in m.group(1).split(",")) if m else (-90, -180, 90, 180)
    dlat = (north - south) / (nlat - 1)
    dlon = (east - west) / (nlon - 1)
    wrap = west >= -1.0
    lw = LONW + 360.0 if wrap else LONW
    le = LONE + 360.0 if wrap else LONE
    c0 = max(0, int((lw - west) / dlon))
    c1 = min(nlon, int((le - west) / dlon) + 2)
    rowA = int((40.0 - south) / dlat)
    return rowA, nlat - 1 - rowA, c0, c1, False


class _Geo:
    """Georeference for one domain. probe_field = two row strips (A, B)."""

    def __init__(self, shape: Tuple[int, int], wkt: str, probe_field: np.ndarray):
        self.shape = shape
        nlat, nlon = shape
        self.projected = not wkt.lstrip().startswith("GEOGCRS")
        m = re.search(r"BBOX\[([^\]]+)\]", wkt)
        south, west, north, east = (float(x) for x in m.group(1).split(",")) if m else (-90, -180, 90, 180)
        if self.projected:
            from rasterio.crs import CRS
            from pyproj import Transformer

            try:
                self.crs = CRS.from_wkt(wkt)
            except Exception:
                # Open-Meteo's WKT is slightly non-standard — rebuild the CRS
                # from its parsed parameters (self-consistent with the BBOX).
                params = dict(re.findall(r'PARAMETER\["([^"]+)",([-\d.]+)', wkt))
                rm = re.search(r'ELLIPSOID\["[^"]+",([\d.]+)', wkt)
                radius = rm.group(1) if rm else "6371229"
                self.crs = CRS.from_proj4(
                    "+proj=lcc"
                    f" +lat_1={params.get('Latitude of 1st standard parallel', '38.5')}"
                    f" +lat_2={params.get('Latitude of 2nd standard parallel', '38.5')}"
                    f" +lat_0={params.get('Latitude of false origin', '0')}"
                    f" +lon_0={params.get('Longitude of false origin', '-97.5')}"
                    f" +x_0={params.get('Easting at false origin', '0')}"
                    f" +y_0={params.get('Northing at false origin', '0')}"
                    f" +R={radius} +units=m +no_defs"
                )
            tr = Transformer.from_crs("EPSG:4326", self.crs, always_xy=True)
            x0, y0 = tr.transform(west, south)   # SW corner
            x1, y1 = tr.transform(east, north)   # NE corner
            dx = (x1 - x0) / (nlon - 1)
            dy = (y1 - y0) / (nlat - 1)
            # probe strips: strip0 = low row index, strip1 = high row index.
            # In NH summer the SOUTH is warmer; row0-is-south => strip0 warmer.
            self.rows_ascending = float(np.nanmean(probe_field[0])) > float(np.nanmean(probe_field[1]))
            self.transform_north = Affine(dx, 0.0, x0 - dx / 2, 0.0, -dy, y1 + dy / 2)
            self.r0, self.r1, self.c0, self.c1 = 0, nlat, 0, nlon  # HRRR = CONUS already
        else:
            from rasterio.crs import CRS

            self.crs = CRS.from_epsg(4326)
            self.dlat = (north - south) / (nlat - 1)
            self.dlon = (east - west) / (nlon - 1)
            self.south, self.west = south, west
            # data_spatial regular grids use the 0..360 longitude convention
            # (data_run uses -180..180) — detect from the BBOX west value.
            self.lon_wrap = west >= -1.0
            lw = LONW + 360.0 if self.lon_wrap else LONW
            le = LONE + 360.0 if self.lon_wrap else LONE
            self.c0 = max(0, int((lw - west) / self.dlon))
            self.c1 = min(nlon, int((le - west) / self.dlon) + 2)
            r0a = max(0, int((LAT0 - south) / self.dlat))
            r1a = min(nlat, int((LAT1 - south) / self.dlat) + 2)
            # warm-north probe (NH summer): +40° strip vs its mirror.
            rowA = int((40.0 - south) / self.dlat)
            rowB = nlat - 1 - rowA
            a = float(np.nanmean(probe_field[0]))
            b = float(np.nanmean(probe_field[1]))
            self.rows_ascending = a > b
            self._probe_rows = (rowA, rowB)
            if self.rows_ascending:
                self.r0, self.r1 = r0a, r1a
            else:
                self.r0, self.r1 = nlat - r1a, nlat - r0a

    def window(self) -> Tuple[slice, slice]:
        return slice(self.r0, self.r1), slice(self.c0, self.c1)

    def to_canonical(self, sliced: np.ndarray) -> np.ndarray:
        from rasterio.warp import Resampling

        if self.projected:
            field = np.flipud(sliced) if self.rows_ascending else sliced
            arr = np.where(np.isnan(field), grid.NODATA, field)
            return grid.resample_to_grid(
                arr, self.transform_north, self.crs,
                resampling=Resampling.cubic_spline, src_nodata=grid.NODATA,
            )
        oriented = np.flipud(sliced) if self.rows_ascending else sliced
        if self.rows_ascending:
            north_edge = self.south + self.r1 * self.dlat
        else:
            nlat = self.shape[0]
            north_edge = (self.south + (nlat - 1) * self.dlat) - self.r0 * self.dlat
        west_edge = self.west + self.c0 * self.dlon - (360.0 if self.lon_wrap else 0.0)
        transform = Affine(self.dlon, 0.0, west_edge, 0.0, -self.dlat, north_edge)
        arr = np.where(np.isnan(oriented), grid.NODATA, oriented)
        return grid.resample_to_grid(
            arr, transform, self.crs,
            resampling=Resampling.cubic_spline, src_nodata=grid.NODATA,
        )


_geo_cache: Dict[str, _Geo] = {}
# domain -> {child name: index}. get_child_by_name linear-scans the metadata
# tree with a ranged GET per child (57 s to reach crs_wkt in the 321-child
# gfs025 file). Layouts are stable per domain, so: enumerate ALL children
# concurrently ONCE (~1-3.5 s), cache the indexes, and every later frame is
# a couple of direct indexed fetches (~0.2 s each), name-verified.
_child_idx: Dict[str, Dict[str, int]] = {}


async def _get_children(r, domain: str, names: List[str]) -> Dict[str, object]:
    """{name: node-or-None} using the per-domain child-index cache."""
    cache = _child_idx.get(domain)
    if cache is not None and all(n in cache or n == "crs_wkt" for n in names):
        idxs = [cache.get(n) for n in names]
        if all(i is not None for i in idxs):
            nodes = await asyncio.gather(*(r.get_child_by_index(i) for i in idxs))
            if all(nd.name == n for nd, n in zip(nodes, names)):
                return dict(zip(names, nodes))
            # layout changed under us — fall through to re-enumerate
    kids = await asyncio.gather(*(r.get_child_by_index(i) for i in range(r.num_children)))
    _child_idx[domain] = {k.name: i for i, k in enumerate(kids)}
    by_name = {k.name: k for k in kids}
    return {n: by_name.get(n) for n in names}


async def _read_frame_async(fs, domain: str, s3_key: str, varnames: Tuple[str, ...]) -> Dict[str, np.ndarray]:
    """Read + canonicalize the requested vars from one timestep file."""
    import omfiles

    r = await omfiles.OmFileReaderAsync.from_fsspec(fs, s3_key)
    out: Dict[str, np.ndarray] = {}
    geo = _geo_cache.get(domain)
    want = list(varnames) + (["crs_wkt"] if geo is None else [])
    nodes = await _get_children(r, domain, want)
    if geo is None:
        probe_name = "temperature_2m" if nodes.get("temperature_2m") is not None else varnames[0]
        node = nodes[probe_name]
        shape = node.shape
        wkt = ""
        try:
            wnode = nodes.get("crs_wkt")
            if wnode is not None:
                wkt = str(wnode.read_scalar())  # read_scalar is sync on both readers
        except Exception:
            pass
        rowA, rowB, pc0, pc1, _proj = _probe_plan(shape, wkt)
        stripA = np.asarray(await node.read_array((slice(rowA, rowA + 1), slice(pc0, pc1))), dtype=np.float32)
        stripB = np.asarray(await node.read_array((slice(rowB, rowB + 1), slice(pc0, pc1))), dtype=np.float32)
        geo = _Geo(shape, wkt, np.stack([stripA[0], stripB[0]]))
        _geo_cache[domain] = geo
    rs, cs = geo.window()
    for v in varnames:
        try:
            node = nodes.get(v)
            if node is None:
                out[v] = None
                continue
            raw = np.asarray(await node.read_array((rs, cs)), dtype=np.float32)
            out[v] = geo.to_canonical(raw)
        except Exception:
            out[v] = None  # var absent at this step — caller decides
    return out


def read_frames(family: str, frame_keys: List[str], indexes: Dict[str, Dict[str, str]], concurrency: int = 4) -> Dict[str, Dict[str, np.ndarray]]:
    """{frame_key: {var: canonical}} for a batch of frames of one family.

    `indexes` = {domain: {frame_key: s3_key}} from list_run_frames.
    A frame is returned only when every domain had its file and every
    motion var decoded.
    """
    cfg = STREAM_DOMAINS[family]

    async def run() -> Dict[str, Dict[str, np.ndarray]]:
        fs = _fs_async()
        sem = asyncio.Semaphore(concurrency)
        results: Dict[str, Dict[str, np.ndarray]] = {}

        async def one(fk: str):
            async with sem:
                merged: Dict[str, np.ndarray] = {}
                for domain, varnames in cfg:
                    key = indexes.get(domain, {}).get(fk)
                    if not key:
                        return
                    part = await _read_frame_async(fs, domain, key, varnames)
                    for k, v in part.items():
                        if v is None:
                            return
                        merged[k] = v
                results[fk] = merged

        await asyncio.gather(*(one(fk) for fk in frame_keys))
        return results

    return _run_coro(run())


_loop: Optional[asyncio.AbstractEventLoop] = None


def _run_coro(coro):
    """One persistent event loop — s3fs caches its async client per loop, so
    repeated asyncio.run() calls would hand later batches a dead session."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = asyncio.new_event_loop()
    return _loop.run_until_complete(coro)


def shutdown() -> None:
    """Close cached async S3 clients + the loop (silences aiohttp exit noise).

    Call once at process end (pipeline.stream does). Safe to call twice."""
    global _loop
    if _loop is None or _loop.is_closed():
        _loop = None
        return

    async def _close():
        import s3fs

        for fs in list(getattr(s3fs.S3FileSystem, "_cache", {}).values()):
            if getattr(fs, "asynchronous", False):
                s3 = getattr(fs, "_s3", None)
                if s3 is not None:
                    try:
                        await s3.close()
                    except Exception:
                        pass

    try:
        _loop.run_until_complete(_close())
    except Exception:
        pass
    _loop.close()
    _loop = None
