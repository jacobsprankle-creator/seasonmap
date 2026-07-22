"""Layer registry. `run.py --layer all` iterates implemented layers only."""
from __future__ import annotations

from . import (
    air_quality,
    conditions,
    drought,
    model_forecast,
    waves,
    elevation,
    foliage,
    frost,
    frost_date,
    hurricanes,
    leafout,
    rivers,
    snowline,
    tornadoes,
    water_temp,
    wildflower,
)

LAYERS = {
    mod.LAYER: mod
    for mod in (
        *conditions.MODULES,
        *model_forecast.MODULES,
        *air_quality.MODULES,
        waves,
        elevation,
        frost,
        frost_date.FROST_DATE,
        frost_date.FROST_DATE_36,
        frost_date.FROST_DATE_28,
        snowline,
        water_temp,
        rivers,
        drought,
        foliage,
        leafout.LEAF,
        leafout.BLOOM,
        hurricanes.MAJORS,
        hurricanes.MODERN,
        hurricanes.ACTIVE,
        tornadoes.VIOLENT,
        tornadoes.STRONG,
        tornadoes.WEAK,
        leafout,
        wildflower,
        foliage,
    )
}

IMPLEMENTED_LAYERS = {name: mod for name, mod in LAYERS.items() if mod.IMPLEMENTED}
