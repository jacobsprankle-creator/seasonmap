import numpy as np
import pytest

from pipeline.core import grid
from pipeline.layers import elevation as elevation_layer


@pytest.fixture()
def fake_elevation(monkeypatch):
    """Keep unit tests offline: synthetic ridge instead of the real fetch."""
    lons, lats = grid.lon_lat_arrays()
    lon_grid = np.tile(lons, (grid.HEIGHT, 1))
    arr = (2000.0 * np.exp(-((lon_grid + 105.0) ** 2) / 20.0)).astype(np.float32)
    monkeypatch.setattr(elevation_layer, "ensure_elevation", lambda: arr)
    return arr


def test_score_returns_canonical_layer_output(fake_elevation):
    out = elevation_layer.score("2026-07-21")
    assert out.layer == "elevation"
    assert out.values.shape == grid.SHAPE
    assert out.values.dtype == np.float32
    stats = out.stats()
    assert stats["valid_cells"] == grid.HEIGHT * grid.WIDTH
    assert 0 <= stats["min"] and stats["max"] <= 2000.0


def test_legend_matches_colormap_contract(fake_elevation):
    out = elevation_layer.score("2026-07-21")
    legend = out.legend
    assert legend["colormap"] == "terrain"
    assert legend["units"] == "m"
    assert legend["stops"][0]["color"].startswith("#")
    assert legend["vmin"] < legend["vmax"]


def test_dates_for_is_single_snapshot():
    assert elevation_layer.dates_for("2026-07-21") == ["2026-07-21"]
