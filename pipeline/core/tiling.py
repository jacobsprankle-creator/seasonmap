"""COG writing and PMTiles rendering.

One pipeline for every layer:
    scored canonical-grid array → COG (data/{layer}/{date}.tif)
                                → raster PMTiles (tiles/{layer}/{date}.pmtiles)

Raster tiles are 256px PNGs in WebMercator (z0..MAX_ZOOM), colored with the
layer's colormap; NODATA renders transparent. PMTiles archives are served
straight from object storage via HTTP range requests — no tile server.
"""
from __future__ import annotations

import io
import math
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import morecantile
import numpy as np
import rasterio
from PIL import Image
from pmtiles.tile import Compression, TileType, zxy_to_tileid
from pmtiles.writer import Writer as PMTilesWriter
from rasterio.io import MemoryFile
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles
from rio_tiler.errors import TileOutsideBounds
from rio_tiler.io import Reader

from . import grid

DEFAULT_MIN_ZOOM = 0
DEFAULT_MAX_ZOOM = 7  # plenty for ~4 km cells
TILE_SIZE = 256
WEB_MERCATOR = morecantile.tms.get("WebMercatorQuad")


# ---------------------------------------------------------------------------
# COG
# ---------------------------------------------------------------------------

def write_cog(arr: np.ndarray, dst_path: str, second_band: "np.ndarray | None" = None) -> None:
    """Write a canonical-grid array as a Cloud-Optimized GeoTIFF.

    second_band: optional companion field (e.g. MSLP under a precip fill) —
    band 2 drives contour lines in composite renders."""
    if arr.shape != grid.SHAPE:
        raise ValueError(f"array shape {arr.shape} != canonical {grid.SHAPE}")
    Path(dst_path).parent.mkdir(parents=True, exist_ok=True)

    src_profile = dict(grid.profile())
    if second_band is not None:
        src_profile["count"] = 2
    cog_profile = cog_profiles.get("deflate")
    with MemoryFile() as mem:
        with mem.open(**src_profile) as tmp:
            tmp.write(grid.mask_invalid(arr), 1)
            if second_band is not None:
                tmp.write(grid.mask_invalid(second_band), 2)
        with mem.open() as src:
            cog_translate(
                src,
                dst_path,
                cog_profile,
                in_memory=True,
                quiet=True,
                web_optimized=False,
            )


# ---------------------------------------------------------------------------
# Tiles
# ---------------------------------------------------------------------------

def _render_tile(
    reader: Reader,
    x: int,
    y: int,
    z: int,
    colormap: Dict[int, Tuple[int, int, int, int]],
    vmin: float,
    vmax: float,
    contour_interval: Optional[float] = None,
) -> Optional[bytes]:
    try:
        try:
            img = reader.tile(
                x, y, z, tilesize=TILE_SIZE, reproject_method="bilinear"
            )
        except TypeError:  # older rio-tiler signature
            img = reader.tile(x, y, z, tilesize=TILE_SIZE)
    except TileOutsideBounds:
        return None
    if not img.mask.any():  # fully masked / no data in tile
        return None
    edge = None
    if contour_interval:
        # Traditional synoptic presentation: contour lines burned over the
        # fill wherever the field crosses an interval boundary (isohypses /
        # isobars / isotachs).
        import numpy as _np

        band = 1 if img.data.shape[0] > 1 else 0  # composite: contour the 2nd band
        vals = img.data[band].astype("float64")
        valid = img.mask.astype(bool)
        q = _np.floor(vals / float(contour_interval))
        edge = _np.zeros(q.shape, dtype=bool)
        edge[1:, :] |= (q[1:, :] != q[:-1, :]) & valid[1:, :] & valid[:-1, :]
        edge[:, 1:] |= (q[:, 1:] != q[:, :-1]) & valid[:, 1:] & valid[:, :-1]
    if img.data.shape[0] > 1:
        # Composite tile: fill renders from band 0 only.
        from rio_tiler.models import ImageData as _ImageData

        img = _ImageData(img.data[:1], img.mask, assets=img.assets, crs=img.crs, bounds=img.bounds)
    img.rescale(in_range=((vmin, vmax),))
    if edge is not None and edge.any():
        try:
            rgba = img.apply_colormap(colormap)
            rgba.data[0][edge] = 35
            rgba.data[1][edge] = 38
            rgba.data[2][edge] = 48
            if rgba.data.shape[0] > 3:
                rgba.data[3][edge] = 235
                return rgba.render(img_format="PNG", add_mask=False)
            # 3-band RGB + alpha-in-mask (rio-tiler folds colormap alpha into
            # the mask): keep the mask so transparency survives, and mark the
            # contour pixels visible in it.
            rgba.mask[edge] = 255
            return rgba.render(img_format="PNG")
        except Exception:
            pass  # older rio-tiler without apply_colormap — fill only
    return img.render(img_format="PNG", colormap=colormap)


def render_pmtiles(
    cog_path: str,
    pmtiles_path: str,
    colormap: Dict[int, Tuple[int, int, int, int]],
    vmin: float,
    vmax: float,
    min_zoom: int = DEFAULT_MIN_ZOOM,
    max_zoom: int = DEFAULT_MAX_ZOOM,
    metadata: Optional[dict] = None,
    contour_interval: Optional[float] = None,
) -> int:
    """Render a COG into a raster PMTiles archive. Returns tile count."""
    Path(pmtiles_path).parent.mkdir(parents=True, exist_ok=True)
    west, south, east, north = grid.BOUNDS

    tiles: List[Tuple[int, bytes]] = []
    with Reader(cog_path) as reader:
        for z in range(min_zoom, max_zoom + 1):
            for t in WEB_MERCATOR.tiles(west, south, east, north, zooms=[z]):
                data = _render_tile(reader, t.x, t.y, z, colormap, vmin, vmax, contour_interval)
                if data is not None:
                    tiles.append((zxy_to_tileid(z, t.x, t.y), data))

    if not tiles:
        raise RuntimeError(f"no tiles rendered from {cog_path}")

    tiles.sort(key=lambda kv: kv[0])
    center_lon = (west + east) / 2.0
    center_lat = (south + north) / 2.0
    header = {
        "tile_type": TileType.PNG,
        "tile_compression": Compression.NONE,
        "min_zoom": min_zoom,
        "max_zoom": max_zoom,
        "min_lon_e7": int(west * 1e7),
        "min_lat_e7": int(south * 1e7),
        "max_lon_e7": int(east * 1e7),
        "max_lat_e7": int(north * 1e7),
        "center_zoom": 4,
        "center_lon_e7": int(center_lon * 1e7),
        "center_lat_e7": int(center_lat * 1e7),
    }
    with open(pmtiles_path, "wb") as f:
        writer = PMTilesWriter(f)
        for tileid, data in tiles:
            writer.write_tile(tileid, data)
        writer.finalize(header, metadata or {})
    return len(tiles)


# ---------------------------------------------------------------------------
# Preview / golden-test helper
# ---------------------------------------------------------------------------

def stitch_zoom_level(pmtiles_path: str, zoom: int) -> Tuple[Image.Image, Tuple[float, float, float, float]]:
    """Stitch every tile at one zoom into a single RGBA image.

    Returns (image, mercator-aligned lon/lat bounds of the stitched image).
    Used for visual verification and golden-file tests.
    """
    from pmtiles.reader import MmapSource
    from pmtiles.reader import Reader as PMTilesReader

    west, south, east, north = grid.BOUNDS
    xs, ys = set(), set()
    for t in WEB_MERCATOR.tiles(west, south, east, north, zooms=[zoom]):
        xs.add(t.x)
        ys.add(t.y)
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    w = (x1 - x0 + 1) * TILE_SIZE
    h = (y1 - y0 + 1) * TILE_SIZE
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    with open(pmtiles_path, "rb") as f:
        reader = PMTilesReader(MmapSource(f))
        for ty in range(y0, y1 + 1):
            for tx in range(x0, x1 + 1):
                data = reader.get(zoom, tx, ty)
                if data:
                    tile_img = Image.open(io.BytesIO(data)).convert("RGBA")
                    canvas.paste(tile_img, ((tx - x0) * TILE_SIZE, (ty - y0) * TILE_SIZE))

    n = 2 ** zoom

    def tile_lon(x: int) -> float:
        return x / n * 360.0 - 180.0

    def tile_lat(y: int) -> float:
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))

    bounds = (tile_lon(x0), tile_lat(y1 + 1), tile_lon(x1 + 1), tile_lat(y0))
    return canvas, bounds
