import datetime as dt

import numpy as np
import pytest

from pipeline.core import grid
from pipeline.core.sources import prism
from pipeline.layers import frost


def synthetic_normals(mean_crossing_doy: float = 290.0) -> np.ndarray:
    """Monthly tmin stack: sinusoid whose MEAN curve crosses 0°C in fall at
    `mean_crossing_doy`. The hazard-model median freeze lands ~2-3 weeks
    earlier — that gap is the point of the model."""
    mids = prism.MONTH_MID_DOY
    phase = 2 * np.pi * (mids - 201.0) / 365.0
    amp = 14.0
    off = -amp * np.cos(2 * np.pi * (mean_crossing_doy - 201.0) / 365.0)
    curve = amp * np.cos(phase) + off
    return np.tile(curve[:, None, None], (1, grid.HEIGHT, grid.WIDTH)).astype(np.float32)


@pytest.fixture()
def climo(monkeypatch, tmp_path):
    monkeypatch.setenv("STATIC_DIR", str(tmp_path))
    monkeypatch.setattr(prism, "ensure_tmin_normals", lambda force=False: synthetic_normals())
    monkeypatch.setattr(frost, "_forecast_frozen_by", lambda date: None)
    frost._forecast_memo.clear()
    frost._climo_memo.clear()
    return frost.ensure_climatology(force=True)


def test_median_precedes_mean_curve_crossing(climo):
    mu50, sigma_eff = climo
    assert np.isfinite(mu50).all()
    med = float(np.nanmedian(mu50))
    # first-freeze median must land WEEKS before the mean-curve 0°C crossing
    assert 235.0 <= med <= 285.0
    assert (sigma_eff >= frost.SIGMA_EFF_MIN).all()


def test_probability_monotonic_bounded_and_half_at_median(climo):
    mu50, _ = climo
    med_doy = int(round(float(np.nanmedian(mu50))))
    med_date = (dt.date(2026, 1, 1) + dt.timedelta(days=med_doy - 1)).isoformat()

    dates = ["2026-09-05", med_date, "2026-11-20"]
    means = [float(np.mean(frost.score(d).values[frost.score(d).values != grid.NODATA])) for d in dates]
    assert means[0] < means[1] < means[2]
    assert abs(means[1] - 0.5) < 0.08

    out = frost.score(med_date).values
    valid = out[out != grid.NODATA]
    assert valid.min() >= 0.0 and valid.max() <= 1.0


def test_never_freezing_cells_probability_zero(monkeypatch, tmp_path):
    monkeypatch.setenv("STATIC_DIR", str(tmp_path))
    warm = synthetic_normals() + 30.0  # tmin never near 0°C
    monkeypatch.setattr(prism, "ensure_tmin_normals", lambda force=False: warm)
    monkeypatch.setattr(frost, "_forecast_frozen_by", lambda date: None)
    frost._climo_memo.clear()
    mu50, _ = frost.ensure_climatology(force=True)
    assert np.isnan(mu50).all()
    out = frost.score("2026-11-15").values
    assert float(out[out != grid.NODATA].max()) == 0.0


def test_forecast_blend_overrides_climo(climo, monkeypatch):
    frozen = np.ones(grid.SHAPE, dtype=bool)
    w = np.full(grid.SHAPE, 1.0, dtype=np.float32)
    monkeypatch.setattr(frost, "_forecast_frozen_by", lambda date: (frozen, w))
    out = frost.score("2026-09-01").values  # far before μ50 → climo ≈ 0
    valid = out[out != grid.NODATA]
    assert valid.min() >= 1.0 - 1e-6


def test_dates_for_window():
    dates = frost.dates_for("2026-10-15")
    # 11 daily forecast-window dates …
    assert dates[0] == "2026-10-12" and dates[10] == "2026-10-22"
    # … then weekly climatology checkpoints through the end of the season.
    assert dates[11] == "2026-10-29"
    assert dates[-1] <= "2026-12-31"
    assert dates == sorted(dates)
    assert len(dates) > 11
