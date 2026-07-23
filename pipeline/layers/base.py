"""Shared layer contract.

Every layer module exposes:
    LAYER: str                        # slug used in storage keys
    IMPLEMENTED: bool                 # registry skips unimplemented layers
    def dates_for(run_date) -> list[str]   # dates to (re)compute this run
    def score(date: str) -> LayerOutput    # values on the canonical grid

Keeping the contract this small is the point: a new phenomenon layer is a new
scoring module, nothing else changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np

from ..core import colormaps, grid


@dataclass
class VectorOutput:
    """Vector layers (storm tracks, later county polygons) publish GeoJSON
    instead of COG+tiles. Modules declare KIND = "vector"."""

    layer: str
    date: str
    geojson: dict                  # FeatureCollection
    legend: dict                   # {"type": "categorical", "items": [{value,color,label}]}
    units: str
    description: str
    style: dict = field(default_factory=dict)  # frontend hints: color_property, width_property
    extra_meta: dict = field(default_factory=dict)

    def stats(self) -> dict:
        return {"features": len(self.geojson.get("features", []))}


@dataclass
class LayerOutput:
    layer: str
    date: str                 # YYYY-MM-DD
    values: np.ndarray        # float32, canonical grid, grid.NODATA = missing
    colormap: str             # key into colormaps.COLORMAPS
    vmin: float
    vmax: float
    units: str
    description: str
    extra_meta: dict = field(default_factory=dict)
    contour_interval: float | None = None  # burn contour lines every N units (synoptic fields)
    contour_values: np.ndarray | None = None  # composite: contour a DIFFERENT field over the fill

    def __post_init__(self) -> None:
        if self.values.shape != grid.SHAPE:
            raise ValueError(
                f"{self.layer}/{self.date}: shape {self.values.shape} != canonical {grid.SHAPE}"
            )
        self.values = grid.mask_invalid(self.values)

    @property
    def legend(self) -> dict:
        return colormaps.legend_json(self.colormap, self.vmin, self.vmax, self.units)

    def stats(self) -> dict:
        valid = self.values[self.values != grid.NODATA]
        if valid.size == 0:
            return {"min": None, "max": None, "mean": None, "valid_cells": 0}
        return {
            "min": float(valid.min()),
            "max": float(valid.max()),
            "mean": float(valid.mean()),
            "valid_cells": int(valid.size),
        }
