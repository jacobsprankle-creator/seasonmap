"""Water surface temperature layer — oceans + Great Lakes (MUR SST)."""
from __future__ import annotations

from typing import List

from ..core.sources.sst import fetch_water_temp_f
from .base import LayerOutput

LAYER = "water_temp"
IMPLEMENTED = True

VMIN, VMAX = 32.0, 90.0


def dates_for(run_date: str) -> List[str]:
    return [run_date]


def score(date: str) -> LayerOutput:
    return LayerOutput(
        layer=LAYER,
        date=date,
        values=fetch_water_temp_f(),
        colormap="sst_f",
        vmin=VMIN,
        vmax=VMAX,
        units="°F",
        description="Water surface temperature — oceans & Great Lakes (NASA JPL MUR, daily)",
        extra_meta={"value_format": "number"},
    )
