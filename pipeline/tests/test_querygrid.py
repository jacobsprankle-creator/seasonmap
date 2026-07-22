import numpy as np

from pipeline.core import grid, querygrid


def test_roundtrip_preserves_block_means_and_nodata():
    values = grid.empty(fill=grid.NODATA)
    values[0:60, 0:60] = 100.0
    values[0:3, 0:3] = 40.0  # one distinct block
    qg = querygrid.build_query_grid(values)
    dec = querygrid.decode_query_grid(qg)

    assert dec.shape == tuple(qg["shape"])
    assert abs(dec[0, 0] - 40.0) < 0.01          # pure block preserved
    assert abs(dec[5, 5] - 100.0) < 0.01
    assert np.isnan(dec[100, 100])               # NODATA region → NaN

    # transform maps block (0,0) center near canonical grid origin
    a, _, c, _, e, f = qg["transform"]
    assert abs(a - grid.CELLSIZE * 3) < 1e-9
    assert abs(c - grid.WEST) < 1e-9 and abs(f - grid.NORTH) < 1e-9


def test_frost_threshold_ordering_synthetic(monkeypatch, tmp_path):
    from pipeline.core.sources import prism
    from pipeline.layers import frost
    from pipeline.tests.test_frost import synthetic_normals

    monkeypatch.setenv("STATIC_DIR", str(tmp_path))
    monkeypatch.setattr(prism, "ensure_tmin_normals", lambda force=False: synthetic_normals())
    frost._climo_memo.clear()

    mu36, _ = frost.ensure_climatology(force=True, threshold_c=frost.THRESHOLDS_C["frost"])
    mu32, _ = frost.ensure_climatology(force=True, threshold_c=frost.THRESHOLDS_C["freeze"])
    mu28, _ = frost.ensure_climatology(force=True, threshold_c=frost.THRESHOLDS_C["hard_freeze"])
    # warmer threshold happens earlier in fall, colder later
    assert float(np.nanmedian(mu36)) < float(np.nanmedian(mu32)) < float(np.nanmedian(mu28))
