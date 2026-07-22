"""Air quality layers — US AQI, PM2.5, wildfire smoke (aerosol optical depth)."""
from __future__ import annotations

import datetime as dt
from typing import List

from ..core import grid
from ..core.sources.openmeteo_extra import fetch_air_quality
from ..core.sources.prism import ensure_tmin_normals
from .base import LayerOutput

VARIANTS = [
    ("air_aqi", "us_aqi", "aqi", 0, 300, "AQI", "US Air Quality Index"),
    ("air_pm25", "pm2_5", "pm25", 0, 150, "µg/m³", "PM2.5 fine particulates"),
    ("air_smoke", "aerosol_optical_depth", "aod", 0, 2, "AOD", "Smoke / haze (aerosol optical depth)"),
]


def _make(slug, var, cmap, vmin, vmax, units, label):
    def dates_for(run_date: str) -> List[str]:
        return [run_date]

    def score(date: str) -> LayerOutput:
        fields = fetch_air_quality(dt.datetime.utcnow().strftime("%Y-%m-%dT%H"))
        values = fields[var].copy()
        land = ensure_tmin_normals()[0] != grid.NODATA
        values[~land] = grid.NODATA
        return LayerOutput(
            layer=slug, date=date, values=values, colormap=cmap,
            vmin=float(vmin), vmax=float(vmax), units=units,
            description=f"{label} right now (CAMS via Open-Meteo) — refreshed hourly when deployed",
            extra_meta={"value_format": "number", "opacity": 0.62},
        )

    ns = type(slug, (), {})
    ns.LAYER = slug
    ns.IMPLEMENTED = True
    ns.dates_for = staticmethod(dates_for)
    ns.score = staticmethod(score)
    return ns


MODULES = [_make(*v) for v in VARIANTS]
