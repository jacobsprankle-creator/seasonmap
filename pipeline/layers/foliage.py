"""Fall foliage progression — climatology-anchored outlook (v1).

Peak leaf color reliably leads the first hard freeze: color onset tracks
cooling nights, and the freeze climatology already encodes exactly that
gradient (latitude + elevation + continentality). v1 maps days-until-expected-
first-freeze (μ50 from the frost hazard model) to a color stage:

    stage 0 green      > 42 days before expected freeze
    stage 1 turning    42–28 days
    stage 2 patchy     28–14 days
    stage 3 near peak  14–5 days
    stage 4 PEAK       5 days before → 10 days after
    stage 5 past       > 10 days after

Elevation term: terrain is already in the freeze climatology (PRISM encodes
lapse-rate cooling), but at high elevation the freeze→color relationship
itself shifts — aspen country freezes weeks before it colors, and the hazard
model's alpine early-bias compounds it. Above 1,500 m the expected peak is
delayed by 4.5 days per 100 m (capped at +70 d), which puts Colorado aspens
in the observed mid/late-September window instead of August while leaving
everything below 1,500 m (the entire East) untouched.

Labeled an outlook, not an observation: the original chilling/photoperiod/
drought model (build plan Phase 5) replaces this once fall observations exist
to calibrate against. Weekly slider dates Sep 1 – Nov 30.
"""
from __future__ import annotations

import datetime as dt
from typing import List

import numpy as np

from ..core import grid
from ..core.sources.elevation import ensure_elevation
from .base import LayerOutput
from .frost import _season_year, ensure_climatology

LAYER = "foliage"
IMPLEMENTED = True

ELEV_THRESHOLD_M = 1500.0
ELEV_DELAY_D_PER_100M = 4.5  # calibrated so 2,900 m aspen country peaks ~Sep 20-25
ELEV_DELAY_CAP_D = 70.0


def dates_for(run_date: str) -> List[str]:
    year = _season_year(run_date)
    d = dt.date(year, 9, 1)
    end = dt.date(year, 11, 30)
    out = []
    while d <= end:
        out.append(d.isoformat())
        d += dt.timedelta(days=7)
    return out


def score(date: str) -> LayerOutput:
    mu50, _ = ensure_climatology()
    doy = float((dt.date.fromisoformat(date) - dt.date(dt.date.fromisoformat(date).year, 1, 1)).days + 1)

    elev = ensure_elevation()
    delay = np.clip(
        (np.where(elev == grid.NODATA, 0.0, elev) - ELEV_THRESHOLD_M)
        / 100.0
        * ELEV_DELAY_D_PER_100M,
        0.0,
        ELEV_DELAY_CAP_D,
    ).astype(np.float32)

    lead = (mu50 + delay) - doy  # days until expected peak-anchor (negative = after)

    stage = np.full(grid.SHAPE, np.nan, dtype=np.float32)
    stage[lead > 42] = 0
    stage[(lead <= 42) & (lead > 28)] = 1
    stage[(lead <= 28) & (lead > 14)] = 2
    stage[(lead <= 14) & (lead > 5)] = 3
    stage[(lead <= 5) & (lead > -10)] = 4
    stage[lead <= -10] = 5
    values = np.where(np.isnan(mu50) | np.isnan(stage), grid.NODATA, stage).astype(np.float32)

    return LayerOutput(
        layer=LAYER,
        date=date,
        values=values,
        colormap="foliage",
        vmin=0.0,
        vmax=5.0,
        units="stage",
        description=(
            "Fall color outlook (v1, climatology-anchored to expected first freeze) — "
            "slide Sep–Nov to watch peak sweep south; observation-calibrated model to follow"
        ),
        extra_meta={"value_format": "foliage_stage"},
    )
