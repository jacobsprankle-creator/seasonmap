"""River gauges layer — USGS NWIS current water temp + streamflow points."""
from __future__ import annotations

from typing import List, Optional

from ..core.sources import nwis
from .base import VectorOutput

LAYER = "rivers"
IMPLEMENTED = True
KIND = "vector"

TEMP_CLASSES = [
    {"value": "lt50", "color": "#5e4fa2", "label": "<50°F"},
    {"value": "50s", "color": "#3288bd", "label": "50s"},
    {"value": "60s", "color": "#66c2a5", "label": "60s"},
    {"value": "70s", "color": "#fee08b", "label": "70s"},
    {"value": "80plus", "color": "#d53e4f", "label": "80°F+"},
    {"value": "flow_only", "color": "#9aa5b1", "label": "flow only"},
]


def _temp_class(temp_f: Optional[float]) -> str:
    if temp_f is None:
        return "flow_only"
    if temp_f < 50:
        return "lt50"
    if temp_f < 60:
        return "50s"
    if temp_f < 70:
        return "60s"
    if temp_f < 80:
        return "70s"
    return "80plus"


def dates_for(run_date: str) -> List[str]:
    return [run_date]


def score(date: str) -> VectorOutput:
    sites = nwis.fetch_river_sites()
    features = []
    for s in sites:
        props = {
            "name": s["name"],
            "temp_class": _temp_class(s.get("temp_f")),
            "as_of": s["datetime"],
        }
        if "temp_f" in s:
            props["temp_f"] = s["temp_f"]
        if "flow_cfs" in s:
            props["flow_cfs"] = s["flow_cfs"]
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [s["lon"], s["lat"]]},
                "properties": props,
            }
        )
    return VectorOutput(
        layer=LAYER,
        date=date,
        geojson={"type": "FeatureCollection", "features": features},
        legend={"type": "categorical", "items": TEMP_CLASSES},
        units="°F / ft³s",
        description="River gauges — current water temperature & streamflow (USGS NWIS), refreshed hourly when deployed",
        style={
            "geometry": "point",
            "color_property": "temp_class",
            "hover": ["name", "temp_f", "flow_cfs", "as_of"],
            "circle_radius": 4,
        },
        extra_meta={"value_format": "feature"},
    )
