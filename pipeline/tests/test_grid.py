import numpy as np
import pytest
from rasterio.warp import Resampling

from pipeline.core import grid


def test_canonical_shape_and_transform():
    assert grid.SHAPE == (621, 1405)
    assert grid.TRANSFORM.a == pytest.approx(1 / 24)
    assert grid.TRANSFORM.e == pytest.approx(-1 / 24)
    assert grid.TRANSFORM.c == pytest.approx(-125.0208333333, abs=1e-8)
    assert grid.TRANSFORM.f == pytest.approx(49.9375, abs=1e-8)


def test_bounds_match_prism_headers():
    west, south, east, north = grid.BOUNDS
    assert west == pytest.approx(-125.0208333333, abs=1e-8)
    assert south == pytest.approx(24.0625, abs=1e-8)
    assert east == pytest.approx(-66.4791666667, abs=1e-8)
    assert north == pytest.approx(49.9375, abs=1e-8)


def test_lon_lat_arrays_are_cell_centers():
    lons, lats = grid.lon_lat_arrays()
    assert lons.shape == (grid.WIDTH,) and lats.shape == (grid.HEIGHT,)
    assert lons[0] == pytest.approx(-125.0, abs=1e-9)          # PRISM ULXMAP
    assert lats[0] == pytest.approx(49.9166666667, abs=1e-8)   # PRISM ULYMAP
    assert np.all(np.diff(lons) > 0) and np.all(np.diff(lats) < 0)


@pytest.mark.parametrize(
    "lon,lat",
    [(-80.8431, 35.2271), (-104.99, 39.74), (-93.26, 44.98), (-84.39, 33.75)],
)
def test_index_for_known_cities_in_range(lon, lat):
    row, col = grid.index_for(lon, lat)
    assert 0 <= row < grid.HEIGHT and 0 <= col < grid.WIDTH
    lons, lats = grid.lon_lat_arrays()
    assert abs(lons[col] - lon) <= grid.CELLSIZE / 2 + 1e-9
    assert abs(lats[row] - lat) <= grid.CELLSIZE / 2 + 1e-9


def test_index_for_rejects_out_of_bounds():
    with pytest.raises(ValueError):
        grid.index_for(-160.0, 21.3)  # Honolulu — not CONUS


def test_resample_identity_roundtrip():
    rng = np.random.default_rng(42)
    src = rng.uniform(0, 100, size=grid.SHAPE).astype(np.float32)
    out = grid.resample_to_grid(
        src, grid.TRANSFORM, grid.CRS_CANONICAL, resampling=Resampling.nearest
    )
    assert out.shape == grid.SHAPE
    np.testing.assert_allclose(out, src, rtol=1e-6)


def test_mask_invalid_replaces_nonfinite():
    arr = grid.empty(fill=1.0)
    arr[0, 0] = np.nan
    arr[1, 1] = np.inf
    out = grid.mask_invalid(arr)
    assert out[0, 0] == grid.NODATA and out[1, 1] == grid.NODATA
    assert out[2, 2] == 1.0
