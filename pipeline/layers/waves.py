"""Marine waves layer — daily max wave height forecast (ft), 8 lead days."""
from __future__ import annotations

import datetime as dt
from typing import List

from ..core.sources.openmeteo_extra import fetch_waves
from .base import LayerOutput

LAYER = "waves"
IMPLEMENTED = True


def _key() -> str:
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H")


def dates_for(run_date: str) -> List[str]:
    dates, _ = fetch_waves(_key())
    return dates


def score(date: str) -> LayerOutput:
    dates, stack = fetch_waves(_key())
    return LayerOutput(
        layer=LAYER,
        date=date,
        values=stack[dates.index(date)].copy(),
        colormap="wave_ft",
        vmin=0.0,
        vmax=20.0,
        units="ft",
        description="Max wave height forecast (Open-Meteo Marine) — slider is lead time",
        extra_meta={"value_format": "number", "opacity": 0.75},
    )
