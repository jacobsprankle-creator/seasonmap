"""Stitch one zoom level of a PMTiles archive into a PNG for eyeballing.

    python -m scripts.preview out/tiles/elevation/2026-07-21.pmtiles 5 preview.png

Prints the mercator-aligned lon/lat bounds of the stitched image (usable as
exact corner coordinates for a MapLibre image overlay).
"""
from __future__ import annotations

import sys

from pipeline.core.tiling import stitch_zoom_level


def main() -> int:
    if len(sys.argv) != 4:
        print(__doc__)
        return 2
    pmtiles_path, zoom, out_png = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    img, (west, south, east, north) = stitch_zoom_level(pmtiles_path, zoom)
    # Flatten onto a neutral background: transparent pixels carry the
    # colormap's index-0 RGB under alpha=0, which alpha-ignoring viewers
    # would otherwise display as solid color.
    from PIL import Image

    bg = Image.new("RGBA", img.size, (17, 20, 24, 255))
    img = Image.alpha_composite(bg, img)
    img.save(out_png)
    print(f"{out_png}: {img.width}x{img.height}")
    print(f"bounds W,S,E,N = {west:.6f},{south:.6f},{east:.6f},{north:.6f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
