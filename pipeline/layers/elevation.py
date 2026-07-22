"""Elevation — the Phase 0 dummy layer.

A static passthrough of cached CONUS elevation. It exists to prove the whole
skeleton end to end (source → canonical grid → COG → PMTiles → publish →
frontend) with unmistakable geography: coastlines, the Rockies, the
Appalachians. It stays useful later as the snow-line layer's elevation input.
"""
from __future__ import annotations

from typing import List

from ..core.sources.elevation import ensure_elevation
from .base import LayerOutput

LAYER = "elevation"
IMPLEMENTED = True

VMIN, VMAX = -100.0, 4300.0


def dates_for(run_date: str) -> List[str]:
    # Static layer — one snapshot per run date keeps the daily path exercised.
    return [run_date]


def score(date: str) -> LayerOutput:
    return LayerOutput(
        layer=LAYER,
        date=date,
        values=ensure_elevation(),
        colormap="terrain",
        vmin=VMIN,
        vmax=VMAX,
        units="m",
        description="CONUS elevation (ETOPO), Phase 0 verification layer",
    )
