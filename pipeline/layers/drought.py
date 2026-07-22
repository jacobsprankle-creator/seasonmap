"""Drought layer — U.S. Drought Monitor weekly classification. KIND = "vector".

Standard USDM colors; refreshed by the nightly run (the product itself updates
Thursdays).
"""
from __future__ import annotations

from typing import List

from ..core.sources import drought as usdm
from .base import VectorOutput

LAYER = "drought"
IMPLEMENTED = True
KIND = "vector"

DM_COLORS = [
    {"value": "D0", "color": "#FFFF54", "label": "D0 Abnormally dry"},
    {"value": "D1", "color": "#FCD37F", "label": "D1 Moderate"},
    {"value": "D2", "color": "#FFAA00", "label": "D2 Severe"},
    {"value": "D3", "color": "#E60000", "label": "D3 Extreme"},
    {"value": "D4", "color": "#730000", "label": "D4 Exceptional"},
]


def dates_for(run_date: str) -> List[str]:
    return [run_date]


def score(date: str) -> VectorOutput:
    classes = usdm.fetch_usdm()
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": c["geometry"],
                "properties": {"dm": c["dm"], "label": c["label"]},
            }
            for c in classes
        ],
    }
    return VectorOutput(
        layer=LAYER,
        date=date,
        geojson=geojson,
        legend={"type": "categorical", "items": DM_COLORS},
        units="USDM class",
        description="U.S. Drought Monitor (updated weekly, Thursdays) — official national drought classification",
        style={"geometry": "fill", "color_property": "dm", "hover": ["label"], "fill_opacity": 0.5},
        extra_meta={"value_format": "feature"},
    )
