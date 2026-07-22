"""Current-conditions layers — temperature, humidity, dew point, wind.

One shared Open-Meteo current-weather fetch per run feeds four variants. Meant
to run on the hourly workflow (.github/workflows/hourly.yml) so "current"
stays honest; the nightly run refreshes them too.
"""
from __future__ import annotations

import datetime as dt
from typing import List

from ..core.sources import open_meteo
from .base import LayerOutput

VARIANTS = [
    # (slug, open-meteo var, colormap, vmin, vmax, units, label)
    ("conditions_temp", "temperature_2m", "temp_f", -10.0, 110.0, "°F", "Air temperature"),
    ("conditions_humidity", "relative_humidity_2m", "humidity", 0.0, 100.0, "%", "Relative humidity"),
    ("conditions_dewpoint", "dew_point_2m", "dewpoint_f", 20.0, 80.0, "°F", "Dew point"),
    ("conditions_wind", "wind_speed_10m", "wind_mph", 0.0, 40.0, "mph", "Wind speed"),
]


def _dates_for(run_date: str) -> List[str]:
    return [run_date]


def _make(slug: str, var: str, cmap: str, vmin: float, vmax: float, units: str, label: str):
    def score(date: str) -> LayerOutput:
        import numpy as np

        from ..core import grid
        from ..core.sources.prism import ensure_tmin_normals

        fields = open_meteo.fetch_current_fields(dt.datetime.utcnow().strftime("%Y-%m-%dT%H"))
        # Mask to CONUS land — unmasked, the coarse analysis blankets Canada,
        # Mexico, and both oceans in a hard-edged rectangle.
        values = fields[var].copy()
        land = ensure_tmin_normals()[0] != grid.NODATA
        values[~land] = grid.NODATA
        _ = np
        return LayerOutput(
            layer=slug,
            date=date,
            values=values,
            colormap=cmap,
            vmin=vmin,
            vmax=vmax,
            units=units,
            description=f"{label} right now (Open-Meteo analysis) — refreshed hourly when deployed",
            extra_meta={"value_format": "number", "opacity": 0.62},
        )

    ns = type(slug, (), {})
    ns.LAYER = slug
    ns.IMPLEMENTED = True
    ns.dates_for = staticmethod(_dates_for)
    ns.score = staticmethod(score)
    return ns


MODULES = [_make(*spec) for spec in VARIANTS]
