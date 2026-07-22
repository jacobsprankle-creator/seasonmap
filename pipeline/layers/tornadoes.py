"""Tornado track layers (SPC 1950-present database). KIND = "vector".

Variants:
  tornadoes_violent — EF4-EF5, full archive
  tornadoes_strong  — EF2-EF3 since 1980
"""
from __future__ import annotations

from typing import List

from ..core.sources import storms
from .base import VectorOutput

EF_COLORS = [
    {"value": "0", "color": "#9aa5b1", "label": "EF0"},
    {"value": "1", "color": "#e8d24c", "label": "EF1"},
    {"value": "2", "color": "#f0a13c", "label": "EF2"},
    {"value": "3", "color": "#e8642d", "label": "EF3"},
    {"value": "4", "color": "#d02c2c", "label": "EF4"},
    {"value": "5", "color": "#8e24aa", "label": "EF5"},
]
LEGEND = {"type": "categorical", "items": EF_COLORS}
STYLE = {"geometry": "line", "color_property": "ef", "hover": ["date", "state", "ef", "fatalities", "length_mi"]}


def _dates_for(run_date: str) -> List[str]:
    return [run_date]


def _feature(t: dict) -> dict:
    start = t["start"]
    end = t["end"] or (start[0] + 0.01, start[1] + 0.01)  # tiny stub for point-only reports
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [list(start), list(end)]},
        "properties": {
            "date": t["date"],
            "year": t["year"],
            "state": t["state"],
            "ef": str(t["ef"]),
            "fatalities": t["fatalities"],
            "injuries": t["injuries"],
            "length_mi": t["length_mi"],
        },
    }


def _make(slug: str, description: str, select, year_min: int = 1950):
    def score(date: str) -> VectorOutput:
        rows = [t for t in storms.fetch_tornadoes() if select(t)]
        geojson = {"type": "FeatureCollection", "features": [_feature(t) for t in rows]}
        return VectorOutput(
            layer=slug,
            date=date,
            geojson=geojson,
            legend=LEGEND,
            units="EF scale",
            description=description,
            style=STYLE,
            extra_meta={
                "value_format": "feature",
                "filters": {"year_range": [year_min, 2026], "category_property": "ef"},
            },
        )

    ns = type(slug, (), {})
    ns.LAYER = slug
    ns.IMPLEMENTED = True
    ns.KIND = "vector"
    ns.dates_for = staticmethod(_dates_for)
    ns.score = staticmethod(score)
    return ns


VIOLENT = _make(
    "tornadoes_violent",
    "Violent tornado tracks (EF4-EF5), 1950-present (SPC database)",
    lambda t: t["ef"] >= 4,
)
STRONG = _make(
    "tornadoes_strong",
    "Strong tornado tracks (EF2-EF3) since 1980 (SPC database)",
    lambda t: 2 <= t["ef"] <= 3 and t["year"] >= 1980,
    year_min=1980,
)
WEAK = _make(
    "tornadoes_weak",
    "Weak tornado tracks (EF0-EF1) since 2000 (SPC database)",
    lambda t: 0 <= t["ef"] <= 1 and t["year"] >= 2000,
    year_min=2000,
)
