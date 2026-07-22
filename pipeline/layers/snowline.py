"""Snow line — current snow cover + forecast fresh snow (Phase 3).

Values: 0 = none · 1 = existing snow (SNODAS depth > 2.5 cm) · 2 = fresh snow
expected. For a date D in the slider window, "fresh" is the union of forecast
snowfall days ≤ D (snowfall_sum ≥ 1 cm from the shared Open-Meteo fetch) —
answering "will there be snow to see by Saturday", which is the product
question. The existing-snow mask is today's SNODAS state carried forward (no
melt model in v1).

v1 deviation from the original spec, recorded in docs/BUILD_PLAN.md: fresh
snow uses the model's own snowfall_sum rather than freezing-level − 300 m ∧
precip ≥ 2 mm. The model already resolves phase against its terrain; the
freezing-level method (finer 4 km terrain intersection) is the v1.1 upgrade.
"""
from __future__ import annotations

import datetime as dt
from typing import List

import numpy as np

from ..core import grid
from ..core.sources import nohrsc, open_meteo
from .base import LayerOutput

LAYER = "snowline"
IMPLEMENTED = True

DEPTH_THRESHOLD_M = 0.025
SNOWFALL_THRESHOLD_CM = 1.0

_run_memo: dict = {}


def dates_for(run_date: str) -> List[str]:
    base = dt.date.fromisoformat(run_date)
    return [(base + dt.timedelta(days=k)).isoformat() for k in range(0, 8)]


def _inputs():
    today = dt.date.today().isoformat()
    if today not in _run_memo:
        depth, snodas_date = nohrsc.fetch_snow_depth(today)
        fc_dates, fields = open_meteo.fetch_daily_fields(today)
        _run_memo[today] = (depth, snodas_date, fc_dates, fields["snowfall_sum"])
    return _run_memo[today]


def score(date: str) -> LayerOutput:
    depth, snodas_date, fc_dates, snowfall = _inputs()

    existing = (depth != grid.NODATA) & (depth > DEPTH_THRESHOLD_M)
    fresh = np.zeros(grid.SHAPE, dtype=bool)
    for k, dk in enumerate(fc_dates):
        if dk > date:
            break
        day = snowfall[k]
        fresh |= (day != grid.NODATA) & (day >= SNOWFALL_THRESHOLD_CM)

    values = np.zeros(grid.SHAPE, dtype=np.float32)
    values[existing] = 1.0
    values[fresh] = 2.0  # fresh wins the pixel — it's the actionable signal
    values[depth == grid.NODATA] = grid.NODATA  # outside SNODAS masked domain

    return LayerOutput(
        layer=LAYER,
        date=date,
        values=values,
        colormap="snow_state",
        vmin=0.0,
        vmax=2.0,
        units="state",
        description="Snow on the ground (SNODAS) and forecast fresh snow by this date",
        extra_meta={"value_format": "snow_state", "snodas_date": snodas_date},
    )
