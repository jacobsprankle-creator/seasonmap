"""Spring leaf-out & bloom — USA-NPN Spring Index passthrough (Phase 4 v1).

Two variants (leafout = first-leaf date, bloom = first-bloom date), values are
the day-of-year the index was reached. Progressive Jan–Jun; final for the year
by summer. v2 (own GDD model for forecast extension) per the build plan.
"""
from __future__ import annotations

from typing import List

import numpy as np

from ..core import grid
from ..core.sources.npn import fetch_spring_index
from .base import LayerOutput

VMIN, VMAX = 1.0, 200.0


def _dates_for(run_date: str) -> List[str]:
    """Weekly slider dates Feb 1 – Jun 15: each date progressively reveals
    cells whose index has arrived by then — spring sweeps north as you slide.
    The final date shows the complete map."""
    import datetime as dt

    year = int(run_date[:4])
    d = dt.date(year, 2, 1)
    end = dt.date(year, 6, 15)
    out = []
    while d <= end:
        out.append(d.isoformat())
        d += dt.timedelta(days=7)
    return out


def _make(slug: str, coverage: str, label: str):
    def score(date: str) -> LayerOutput:
        import datetime as dt

        values = fetch_spring_index(coverage).copy()
        doy = (dt.date.fromisoformat(date) - dt.date(int(date[:4]), 1, 1)).days + 1
        values[(values != grid.NODATA) & (values > doy)] = grid.NODATA
        return LayerOutput(
            layer=slug,
            date=date,
            values=values.astype(np.float32),
            colormap="spring_doy",
            vmin=VMIN,
            vmax=VMAX,
            units="day of year",
            description=f"{label} date, current year (USA-NPN Spring Index) — progressive in spring, final by summer",
            extra_meta={"value_format": "doy_date"},
        )

    ns = type(slug, (), {})
    ns.LAYER = slug
    ns.IMPLEMENTED = True
    ns.dates_for = staticmethod(_dates_for)
    ns.score = staticmethod(score)
    return ns


LEAF = _make("leafout", "average_leaf_prism", "First leaf (spring leaf-out)")
BLOOM = _make("leafout_bloom", "average_bloom_prism", "First bloom")

LAYER = LEAF.LAYER
IMPLEMENTED = True
dates_for = LEAF.dates_for
score = LEAF.score
_ = grid  # keep import explicit for the contract
