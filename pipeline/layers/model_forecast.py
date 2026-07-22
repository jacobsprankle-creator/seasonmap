"""Forecast model layers — GFS and ECMWF with the common parameters.

Per model: daily-high temp, accumulated precip, accumulated snow, MSLP,
500 mb heights, 250 mb jet-level wind. The date slider is the lead time
(today … +7 days); synoptic fields are 12Z snapshots.
"""
from __future__ import annotations

import datetime as dt
from typing import List

from ..core import grid
from ..core.sources import open_meteo
from ..core.sources.prism import ensure_tmin_normals
from .base import LayerOutput

# (slug, open-meteo model id, label, param subset or None for all)
MODELS = [
    ("gfs", "gfs_seamless", "GFS", None),
    ("euro", "ecmwf_ifs025", "ECMWF", None),
    ("hrrr", "gfs_hrrr", "HRRR", {"tmax", "precip", "snow", "mslp", "gusts", "cape"}),  # 48h CONUS mesoscale
    ("ukmet", "ukmo_seamless", "UKMET", None),
    ("icon", "icon_seamless", "ICON (DWD)", None),
    ("gem", "gem_seamless", "GEM (Canadian)", None),
]
# (suffix, field, colormap, vmin, vmax, units, label, land_mask)
PARAMS = [
    ("tmax", "tmax", "temp_f", -10, 110, "°F", "daily high temperature", True),
    ("precip", "precip_accum", "precip_in", 0, 6, "in", "accumulated precipitation", True),
    ("snow", "snow_accum", "snow_in", 0, 24, "in", "accumulated snowfall", True),
    ("mslp", "mslp", "mslp_hpa", 980, 1040, "hPa", "mean sea-level pressure (12Z)", False),
    ("z500", "z500", "z500_dam", 522, 600, "dam", "500 mb heights (12Z)", False),
    ("w250", "w250", "wind250_mph", 0, 200, "mph", "250 mb jet wind (12Z)", False),
    ("gusts", "gusts", "gust_mph", 0, 80, "mph", "peak wind gusts", True),
    ("cape", "cape", "cape_jkg", 0, 4000, "J/kg", "CAPE (daily max instability)", True),
]


def _key() -> str:
    return dt.datetime.utcnow().strftime("%Y-%m-%dT%H")


def _make(model_slug: str, model_id: str, model_label: str, p):
    suffix, field, cmap, vmin, vmax, units, label, mask_land = p
    slug = f"{model_slug}_{suffix}"

    def dates_for(run_date: str) -> List[str]:
        dates, _ = open_meteo.fetch_model_fields(model_id, _key())
        return dates

    def score(date: str) -> LayerOutput:
        dates, fields = open_meteo.fetch_model_fields(model_id, _key())
        values = fields[field][dates.index(date)].copy()
        if mask_land:
            land = ensure_tmin_normals()[0] != grid.NODATA
            values[~land] = grid.NODATA
        return LayerOutput(
            layer=slug,
            date=date,
            values=values,
            colormap=cmap,
            vmin=float(vmin),
            vmax=float(vmax),
            units=units,
            description=f"{model_label} {label} — slider is forecast lead time",
            extra_meta={"value_format": "number", "opacity": 0.66},
        )

    ns = type(slug, (), {})
    ns.LAYER = slug
    ns.IMPLEMENTED = True
    ns.dates_for = staticmethod(dates_for)
    ns.score = staticmethod(score)
    return ns


MODULES = [
    _make(ms, mid, ml, p)
    for ms, mid, ml, subset in MODELS
    for p in PARAMS
    if subset is None or p[0] in subset
]
