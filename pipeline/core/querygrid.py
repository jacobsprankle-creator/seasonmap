"""Compact per-layer/date query grids for the AI assistant worker.

The worker must answer point/region questions in <1s without parsing COGs, so
each pipeline run also publishes query/{layer}/{date}.json: the canonical grid
block-averaged 3× (~12 km cells, 469×207), quantized to int16 with a
scale/offset header, base64-encoded little-endian. ~60-150 KB gzipped.
"""
from __future__ import annotations

import base64
import math

import numpy as np

from . import grid

FACTOR = 3
NODATA_I16 = -32768


def build_query_grid(values: np.ndarray) -> dict:
    if values.shape != grid.SHAPE:
        raise ValueError(f"expected canonical shape, got {values.shape}")
    v = values.astype(np.float64)
    v[values == grid.NODATA] = np.nan

    h = math.ceil(grid.HEIGHT / FACTOR)
    w = math.ceil(grid.WIDTH / FACTOR)
    padded = np.full((h * FACTOR, w * FACTOR), np.nan)
    padded[: grid.HEIGHT, : grid.WIDTH] = v
    with np.errstate(invalid="ignore"):
        blocks = np.nanmean(
            padded.reshape(h, FACTOR, w, FACTOR).transpose(0, 2, 1, 3).reshape(h, w, -1),
            axis=2,
        )

    valid = np.isfinite(blocks)
    if valid.any():
        vmin = float(blocks[valid].min())
        vmax = float(blocks[valid].max())
    else:
        vmin, vmax = 0.0, 0.0
    scale = (vmax - vmin) / 32000.0 or 1e-9
    raw = np.full((h, w), NODATA_I16, dtype="<i2")
    raw[valid] = np.round((blocks[valid] - vmin) / scale).astype("<i2")

    t = grid.TRANSFORM
    return {
        "shape": [h, w],
        "transform": [t.a * FACTOR, 0.0, t.c, 0.0, t.e * FACTOR, t.f],
        "scale": scale,
        "offset": vmin,
        "nodata": NODATA_I16,
        "dtype": "int16le",
        "data": base64.b64encode(raw.tobytes()).decode("ascii"),
    }


def decode_query_grid(qg: dict) -> np.ndarray:
    """Inverse of build_query_grid (used by tests; the worker mirrors this)."""
    h, w = qg["shape"]
    raw = np.frombuffer(base64.b64decode(qg["data"]), dtype="<i2").reshape(h, w)
    out = qg["offset"] + qg["scale"] * raw.astype(np.float64)
    out[raw == qg["nodata"]] = np.nan
    return out
