import io

import numpy as np
import pytest
from PIL import Image
from pmtiles.reader import MmapSource
from pmtiles.reader import Reader as PMTilesReader

from pipeline.core import colormaps, grid, tiling


@pytest.fixture(scope="module")
def synthetic_pmtiles(tmp_path_factory):
    """West→east gradient over CONUS rendered to a small PMTiles archive."""
    tmp = tmp_path_factory.mktemp("tiling")
    lons, lats = grid.lon_lat_arrays()
    arr = np.tile(np.linspace(0, 100, grid.WIDTH, dtype=np.float32), (grid.HEIGHT, 1))
    arr[:40, :40] = grid.NODATA  # a nodata corner to prove transparency

    cog = str(tmp / "synthetic.tif")
    pmt = str(tmp / "synthetic.pmtiles")
    tiling.write_cog(arr, cog)
    cmap = colormaps.build_colormap(colormaps.COLORMAPS["terrain"], 0, 100)
    n = tiling.render_pmtiles(cog, pmt, cmap, 0, 100, min_zoom=0, max_zoom=3)
    return cog, pmt, n


def test_render_produces_tiles(synthetic_pmtiles):
    _, _, n = synthetic_pmtiles
    # z0:1 + z1:(1-2) + z2:(2-4) + z3 CONUS coverage — at least a handful
    assert n >= 8


def test_pmtiles_header_and_bounds(synthetic_pmtiles):
    _, pmt, _ = synthetic_pmtiles
    with open(pmt, "rb") as f:
        reader = PMTilesReader(MmapSource(f))
        header = reader.header()
        assert header["min_zoom"] == 0 and header["max_zoom"] == 3
        assert header["min_lon_e7"] == int(grid.WEST * 1e7)
        assert header["max_lat_e7"] == int(grid.NORTH * 1e7)


def test_z0_tile_is_valid_rgba_png(synthetic_pmtiles):
    _, pmt, _ = synthetic_pmtiles
    with open(pmt, "rb") as f:
        reader = PMTilesReader(MmapSource(f))
        data = reader.get(0, 0, 0)
        assert data is not None and data[:8] == b"\x89PNG\r\n\x1a\n"
        img = Image.open(io.BytesIO(data))
        assert img.size == (256, 256) and img.mode == "RGBA"
        alpha = np.array(img)[:, :, 3]
        assert alpha.max() == 255      # CONUS pixels are opaque
        assert alpha.min() == 0        # non-CONUS pixels are transparent


def test_stitch_zoom_level(synthetic_pmtiles):
    _, pmt, _ = synthetic_pmtiles
    img, bounds = tiling.stitch_zoom_level(pmt, 3)
    west, south, east, north = bounds
    assert img.width % 256 == 0 and img.height % 256 == 0
    assert west <= grid.WEST and east >= grid.EAST
    assert south <= grid.SOUTH and north >= grid.NORTH
