"""USGS NWIS river gauges — current water temperature and streamflow.

Instantaneous Values API, one call per CONUS state (the bBox variant caps at
25 sq-degrees). Parameters: 00010 water temp (°C), 00060 discharge (ft³/s).
~10k active gauges; the layer refreshes hourly when deployed.
"""
from __future__ import annotations

import time
from typing import Dict, List

import requests

from .elevation import USER_AGENT

NWIS_URL = "https://waterservices.usgs.gov/nwis/iv/"
CONUS_STATES = (
    "AL AZ AR CA CO CT DE DC FL GA ID IL IN IA KS KY LA ME MD MA MI MN MS MO "
    "MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY"
).split()
NODATA_SENTINELS = {"-999999", "-999999.0", ""}


def fetch_river_sites() -> List[dict]:
    """[{site_no, name, lon, lat, temp_f?, flow_cfs?, datetime}]"""
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    sites: Dict[str, dict] = {}
    for state in CONUS_STATES:
        try:
            resp = session.get(
                NWIS_URL,
                params={
                    "format": "json",
                    "stateCd": state,
                    "parameterCd": "00010,00060",
                    "siteStatus": "active",
                },
                timeout=90,
            )
            resp.raise_for_status()
            series = resp.json().get("value", {}).get("timeSeries", [])
        except Exception:  # noqa: BLE001 — one state failing shouldn't kill the layer
            series = []
        for ts in series:
            try:
                info = ts["sourceInfo"]
                loc = info["geoLocation"]["geogLocation"]
                site_no = info["siteCode"][0]["value"]
                var = ts["variable"]["variableCode"][0]["value"]
                vals = ts["values"][0]["value"]
                if not vals:
                    continue
                raw = vals[0]["value"]
                if raw in NODATA_SENTINELS:
                    continue
                v = float(raw)
                s = sites.setdefault(
                    site_no,
                    {
                        "site_no": site_no,
                        "name": info["siteName"].title(),
                        "lon": round(float(loc["longitude"]), 4),
                        "lat": round(float(loc["latitude"]), 4),
                        "datetime": vals[0]["dateTime"][:16],
                    },
                )
                if var == "00010" and -5 <= v <= 45:
                    s["temp_f"] = round(v * 9 / 5 + 32, 1)
                elif var == "00060":
                    s["flow_cfs"] = v
            except (KeyError, IndexError, ValueError, TypeError):
                continue
        time.sleep(0.15)
    return [
        s for s in sites.values()
        if ("temp_f" in s or "flow_cfs" in s) and -125.1 < s["lon"] < -66.4 and 24 < s["lat"] < 50
    ]
