"""Hurricane track layers (HURDAT2 + live NHC). KIND = "vector".

Variants:
  hurricanes_majors — Cat 3+ at peak, full archive (1851-)
  hurricanes_modern — every tracked storm since 2000
  hurricanes_active — live NHC active storms (points, refreshed each run)
"""
from __future__ import annotations

from typing import List

from ..core.sources import storms
from .base import VectorOutput

CAT_COLORS = [
    {"value": "TD", "color": "#9aa5b1", "label": "TD"},
    {"value": "TS", "color": "#5ba7d1", "label": "TS"},
    {"value": "1", "color": "#f2d15c", "label": "Cat 1"},
    {"value": "2", "color": "#f0a13c", "label": "Cat 2"},
    {"value": "3", "color": "#e8642d", "label": "Cat 3"},
    {"value": "4", "color": "#d02c2c", "label": "Cat 4"},
    {"value": "5", "color": "#8e24aa", "label": "Cat 5"},
]
LEGEND = {"type": "categorical", "items": CAT_COLORS}
STYLE = {"geometry": "line", "color_property": "cat", "hover": ["name", "year", "cat", "peak_cat", "max_wind_kt"]}


def _dates_for(run_date: str) -> List[str]:
    return [run_date]


def _features(selected) -> dict:
    """Per-SEGMENT features so tracks are colored by intensity at the time,
    not one flat color for the storm's peak."""
    feats = []
    for s in selected:
        t = s["track"]
        for i in range(len(t) - 1):
            (x1, y1, w1), (x2, y2, _w2) = t[i], t[i + 1]
            if abs(x2 - x1) > 20:  # safety: never draw across the map
                continue
            feats.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "LineString", "coordinates": [[x1, y1], [x2, y2]]},
                    "properties": {
                        "name": s["name"],
                        "year": s["year"],
                        "cat": storms.saffir_simpson(w1),
                        "peak_cat": s["max_cat"],
                        "max_wind_kt": s["max_wind"],
                        "basin": s["basin"],
                    },
                }
            )
    return {"type": "FeatureCollection", "features": feats}


def _make(slug: str, description: str, select, active: bool = False):
    def score(date: str) -> VectorOutput:
        if active:
            pts = storms.fetch_active_storms()
            feats = []
            for p in pts:
                cat = storms.saffir_simpson(p["intensity_kt"])
                feats.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
                        "properties": {
                            "name": p["name"],
                            "cat": cat,
                            "classification": p["classification"],
                            "max_wind_kt": p["intensity_kt"],
                        },
                    }
                )
                cone = None
                try:
                    cone = storms.fetch_forecast_cone(p["id"])
                except Exception:  # noqa: BLE001 — cone optional
                    cone = None
                if not cone:
                    continue
                for ring in cone["cone"]:
                    feats.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "Polygon", "coordinates": [ring]},
                            "properties": {"name": f"{p['name']} forecast cone (NHC)", "cat": cat},
                        }
                    )
                for line in cone["track"]:
                    feats.append(
                        {
                            "type": "Feature",
                            "geometry": {"type": "LineString", "coordinates": line},
                            "properties": {"name": f"{p['name']} forecast track", "cat": cat},
                        }
                    )
            geojson = {"type": "FeatureCollection", "features": feats}
            style = {"geometry": "mixed", "color_property": "cat", "hover": ["name", "classification", "max_wind_kt"]}
        else:
            geojson = _features(select(storms.fetch_hurdat2()))
            style = STYLE
        extra = {"value_format": "feature"}
        if not active:
            extra["filters"] = {"year_range": [1851, 2026], "category_property": "cat"}
        return VectorOutput(
            layer=slug,
            date=date,
            geojson=geojson,
            legend=LEGEND,
            units="Saffir-Simpson",
            description=description,
            style=style,
            extra_meta=extra,
        )

    ns = type(slug, (), {})
    ns.LAYER = slug
    ns.IMPLEMENTED = True
    ns.KIND = "vector"
    ns.dates_for = staticmethod(_dates_for)
    ns.score = staticmethod(score)
    return ns


MAJORS = _make(
    "hurricanes_majors",
    "Major hurricane tracks (Cat 3+ at peak), Atlantic & E. Pacific, 1851-present (HURDAT2)",
    lambda all_storms: [s for s in all_storms if s["max_cat"] in ("3", "4", "5")],
)
MODERN = _make(
    "hurricanes_modern",
    "All tracked storms since 2000, Atlantic & E. Pacific (HURDAT2)",
    lambda all_storms: [s for s in all_storms if s["year"] >= 2000],
)
ACTIVE = _make(
    "hurricanes_active",
    "Active storms right now (NHC), refreshed nightly — positions and current intensity",
    None,
    active=True,
)
