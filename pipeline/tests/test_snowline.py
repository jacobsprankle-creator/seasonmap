import numpy as np
import pytest

from pipeline.core import grid
from pipeline.layers import snowline


@pytest.fixture()
def fake_inputs(monkeypatch):
    depth = np.zeros(grid.SHAPE, dtype=np.float32)
    depth[100:120, 100:120] = 0.5      # existing snow patch
    depth[0:10, 0:10] = grid.NODATA    # outside SNODAS domain
    fc_dates = ["2026-07-21", "2026-07-22", "2026-07-23"]
    snowfall = np.zeros((3, grid.HEIGHT, grid.WIDTH), dtype=np.float32)
    snowfall[1, 200:210, 200:210] = 5.0  # fresh snow forecast on day 2
    monkeypatch.setattr(
        snowline, "_inputs", lambda: (depth, "2026-07-21", fc_dates, snowfall)
    )
    return depth


def test_existing_vs_fresh_vs_none(fake_inputs):
    day1 = snowline.score("2026-07-21").values
    assert day1[110, 110] == 1.0          # existing snow
    assert day1[205, 205] == 0.0          # fresh not yet forecast by this date
    assert day1[300, 300] == 0.0          # bare ground
    assert day1[5, 5] == grid.NODATA      # outside masked domain

    day2 = snowline.score("2026-07-22").values
    assert day2[205, 205] == 2.0          # fresh snow union kicks in
    assert day2[110, 110] == 1.0

    day3 = snowline.score("2026-07-23").values
    assert day3[205, 205] == 2.0          # union persists for later dates


def test_dates_for_window():
    dates = snowline.dates_for("2026-07-21")
    assert len(dates) == 8 and dates[0] == "2026-07-21" and dates[-1] == "2026-07-28"
