"""Median first frost/freeze date layers — the year-round hero view.

Three NWS-style threshold variants of the same hazard climatology (see
frost.py): frost (36°F, radiational frost possible), freeze (32°F), and hard
freeze (28°F, growing-season end). Each is its own published layer so the
frontend threshold toggle just swaps meta/tiles.

Cells where the event stays below 50% odds by Dec 31 render transparent.
"""
from __future__ import annotations

from typing import List

import numpy as np

from ..core import grid
from .base import LayerOutput
from .frost import SEASON_START_DOY, THRESHOLDS_C, ensure_climatology

VMIN, VMAX = float(SEASON_START_DOY), 365.0


def _dates_for(run_date: str) -> List[str]:
    return [run_date]  # snapshot per run; refreshed nightly


def _make(slug: str, threshold_key: str, label: str):
    threshold_c = THRESHOLDS_C[threshold_key]

    def score(date: str) -> LayerOutput:
        mu50, _ = ensure_climatology(threshold_c=threshold_c)
        values = np.where(np.isnan(mu50), grid.NODATA, mu50).astype(np.float32)
        return LayerOutput(
            layer=slug,
            date=date,
            values=values,
            colormap="frost_date",
            vmin=VMIN,
            vmax=VMAX,
            units="day of year",
            description=(
                f"Median first fall {label} date (PRISM 1991-2020 climatology, "
                "daily-hazard model); transparent = rarely occurs"
            ),
            extra_meta={"value_format": "doy_date", "threshold": threshold_key},
        )

    ns = type(slug, (), {})
    ns.LAYER = slug
    ns.IMPLEMENTED = True
    ns.dates_for = staticmethod(_dates_for)
    ns.score = staticmethod(score)
    return ns


# 32°F keeps the original "frost_date" slug (backward compatible).
FROST_DATE = _make("frost_date", "freeze", "freeze (32°F)")
FROST_DATE_36 = _make("frost_date_36", "frost", "frost (36°F)")
FROST_DATE_28 = _make("frost_date_28", "hard_freeze", "hard freeze (28°F)")

# Kept for imports that treat this module itself as the 32°F layer.
LAYER = FROST_DATE.LAYER
IMPLEMENTED = True
dates_for = FROST_DATE.dates_for
score = FROST_DATE.score
