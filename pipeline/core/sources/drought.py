"""U.S. Drought Monitor — weekly national drought classification polygons.

Free JSON from droughtmonitor.unl.edu (updated Thursdays). Raw file is ~19 MB
pretty-printed at survey precision; we round coordinates to 2 decimals (~1 km,
far finer than the product's own resolution) and strip to the DM class, which
compacts it ~6x.
"""
from __future__ import annotations

from typing import List

from .elevation import USER_AGENT

USDM_URL = "https://droughtmonitor.unl.edu/data/json/usdm_current.json"

DM_LABELS = {
    0: "D0 Abnormally dry",
    1: "D1 Moderate drought",
    2: "D2 Severe drought",
    3: "D3 Extreme drought",
    4: "D4 Exceptional drought",
}


def _round_coords(node, nd: int = 2):
    if isinstance(node, (list, tuple)):
        if node and isinstance(node[0], (int, float)) and len(node) >= 2:
            return [round(float(node[0]), nd), round(float(node[1]), nd)]
        return [_round_coords(c, nd) for c in node]
    return node


def fetch_usdm() -> List[dict]:
    """[{dm, label, geometry}] — one entry per drought class present."""
    import requests

    resp = requests.get(USDM_URL, headers={"User-Agent": USER_AGENT}, timeout=180)
    resp.raise_for_status()
    data = resp.json()
    out = []
    for f in data.get("features", []):
        dm = f.get("properties", {}).get("DM")
        if dm is None or dm not in DM_LABELS:
            continue
        geom = f.get("geometry") or {}
        out.append(
            {
                "dm": f"D{dm}",
                "label": DM_LABELS[dm],
                "geometry": {
                    "type": geom.get("type"),
                    "coordinates": _round_coords(geom.get("coordinates", [])),
                },
            }
        )
    return out
