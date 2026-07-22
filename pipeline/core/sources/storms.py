"""Storm track sources: NHC HURDAT2 (hurricanes) and SPC (tornadoes).

- HURDAT2: annual best-track archives for Atlantic (1851-) and NE Pacific
  (1949-). Filenames carry a revision date, so we scrape the index for the
  latest of each basin. Cached in static/ (annual product).
- SPC tornado database: one CSV, 1950-present, one row per tornado with
  start/end coordinates and (E)F rating. Cached in static/.
- NHC CurrentStorms.json: live active storms (tiny, never cached).
"""
from __future__ import annotations

import csv
import io
import re
from typing import Dict, List, Optional

import requests

from .elevation import USER_AGENT, static_dir

HURDAT_INDEX = "https://www.nhc.noaa.gov/data/hurdat/"
SPC_CSV = "https://www.spc.noaa.gov/wcm/data/1950-2024_actual_tornadoes.csv"
CURRENT_STORMS = "https://www.nhc.noaa.gov/CurrentStorms.json"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers["User-Agent"] = USER_AGENT
    return s


def _fetch_cached(url: str, cache_name: str, force: bool = False) -> str:
    cache = static_dir() / cache_name
    if cache.exists() and not force:
        return cache.read_text()
    resp = _session().get(url, timeout=300)
    resp.raise_for_status()
    cache.write_text(resp.text)
    return resp.text


def saffir_simpson(max_wind_kt: float) -> str:
    if max_wind_kt >= 137:
        return "5"
    if max_wind_kt >= 113:
        return "4"
    if max_wind_kt >= 96:
        return "3"
    if max_wind_kt >= 83:
        return "2"
    if max_wind_kt >= 64:
        return "1"
    if max_wind_kt >= 34:
        return "TS"
    return "TD"


def _parse_coord(tok: str) -> Optional[float]:
    tok = tok.strip()
    if not tok:
        return None
    hemi = tok[-1].upper()
    try:
        v = float(tok[:-1])
    except ValueError:
        return None
    if hemi in ("S", "W"):
        v = -v
    return v


def fetch_hurdat2(force: bool = False) -> List[dict]:
    """All storms, both basins: [{id, name, year, basin, max_wind, max_cat,
    track: [(lon, lat), ...]}]."""
    index = _session().get(HURDAT_INDEX, timeout=60).text
    storms: List[dict] = []
    for basin, pattern in (
        ("AL", r"hurdat2-1851[^\"']*?\.txt"),
        ("EP", r"hurdat2-nepac[^\"']*?\.txt"),
    ):
        names = sorted(set(re.findall(pattern, index)))
        if not names:
            continue
        latest = names[-1]
        text = _fetch_cached(HURDAT_INDEX + latest, f"hurdat2_{basin}.txt", force)
        storms.extend(_parse_hurdat2(text, basin))
    return storms


def _parse_hurdat2(text: str, basin: str) -> List[dict]:
    storms: List[dict] = []
    current: Optional[dict] = None
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if not parts or not parts[0]:
            continue
        if re.match(r"^[A-Z]{2}\d{6}$", parts[0]):  # header: AL011851, NAME, n
            current = {
                "id": parts[0],
                "name": parts[1].title() or "Unnamed",
                "year": int(parts[0][-4:]),
                "basin": basin,
                "max_wind": 0.0,
                "track": [],
            }
            storms.append(current)
            continue
        if current is None or len(parts) < 7:
            continue
        lat = _parse_coord(parts[4])
        lon = _parse_coord(parts[5])
        if lat is None or lon is None:
            continue
        # Dateline-crossing Pacific storms flip W→E notation (179.8W → 179.9E);
        # naive signs would draw a 360° line across the map. Normalize far-east
        # longitudes into continuous westward values.
        if lon > 100:
            lon -= 360.0
        try:
            wind = float(parts[6])
        except ValueError:
            wind = -999.0
        if wind > current["max_wind"]:
            current["max_wind"] = wind
        current["track"].append((round(lon, 2), round(lat, 2), max(wind, 0.0)))
    for s in storms:
        s["max_cat"] = saffir_simpson(s["max_wind"])
    return [s for s in storms if len(s["track"]) >= 2]


def fetch_forecast_cone(storm_id: str) -> Optional[dict]:
    """NHC 5-day forecast package for an active storm: cone polygon, forecast
    track line. Returns None when no advisory package exists."""
    import io as _io
    import zipfile

    import shapefile  # pyshp

    url = f"https://www.nhc.noaa.gov/gis/forecast/archive/{storm_id.lower()}_5day_latest.zip"
    resp = _session().get(url, timeout=60)
    if resp.status_code != 200:
        return None
    out: Dict[str, list] = {"cone": [], "track": []}
    with zipfile.ZipFile(_io.BytesIO(resp.content)) as zf:
        names = zf.namelist()
        for kind, suffix in (("cone", "_pgn"), ("track", "_lin")):
            shp = next((n for n in names if n.endswith(f"{suffix}.shp")), None)
            if not shp:
                continue
            base = shp[:-4]
            reader = shapefile.Reader(
                shp=_io.BytesIO(zf.read(base + ".shp")),
                dbf=_io.BytesIO(zf.read(base + ".dbf")),
                shx=_io.BytesIO(zf.read(base + ".shx")),
            )
            for shape in reader.shapes():
                pts = [[round(x, 3), round(y, 3)] for x, y in shape.points]
                out[kind].append(pts)
    return out if (out["cone"] or out["track"]) else None


def fetch_tornadoes(force: bool = False) -> List[dict]:
    """SPC database rows: [{year, date, state, ef, injuries, fatalities,
    start: (lon, lat), end: (lon, lat)|None, length_mi, width_yd}]."""
    text = _fetch_cached(SPC_CSV, "spc_tornadoes.csv", force)
    out: List[dict] = []
    for row in csv.DictReader(io.StringIO(text)):
        try:
            slat, slon = float(row["slat"]), float(row["slon"])
        except (KeyError, ValueError):
            continue
        if not (-130 < slon < -60 and 20 < slat < 55):
            continue
        try:
            elat, elon = float(row["elat"]), float(row["elon"])
            end = (round(elon, 3), round(elat, 3)) if (-130 < elon < -60 and 20 < elat < 55) else None
        except ValueError:
            end = None
        try:
            mag = int(row["mag"])
        except ValueError:
            mag = -9
        out.append(
            {
                "year": int(row["yr"]),
                "date": row["date"],
                "state": row["st"],
                "ef": mag,  # -9 = unknown
                "injuries": int(row.get("inj") or 0),
                "fatalities": int(row.get("fat") or 0),
                "start": (round(slon, 3), round(slat, 3)),
                "end": end,
                "length_mi": float(row.get("len") or 0),
                "width_yd": float(row.get("wid") or 0),
            }
        )
    return out


def fetch_active_storms() -> List[dict]:
    """Live NHC active storms (points): [{id, name, classification,
    intensity_kt, lat, lon}]. Never cached."""
    resp = _session().get(CURRENT_STORMS, timeout=30)
    resp.raise_for_status()
    out = []
    for s in resp.json().get("activeStorms", []):
        try:
            out.append(
                {
                    "id": s["id"],
                    "name": s["name"],
                    "classification": s.get("classification", "?"),
                    "intensity_kt": float(s.get("intensity") or 0),
                    "lat": float(s["latitudeNumeric"]),
                    "lon": float(s["longitudeNumeric"]),
                }
            )
        except (KeyError, ValueError, TypeError):
            continue
    return out
