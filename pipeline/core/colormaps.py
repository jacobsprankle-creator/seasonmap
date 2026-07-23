"""Colormap + legend definitions.

The pipeline owns styling: each layer names a colormap here, tiles are rendered
with it, and the same stops are written into meta/{layer}/latest.json so the
frontend legend always matches the pixels.

A colormap is a list of (value, "#rrggbb") stops. `build_colormap` converts it
into the 256-entry uint8 lookup rio-tiler needs, anchored to (vmin, vmax).
"""
from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

Stop = Tuple[float, str]

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

COLORMAPS: Dict[str, List[Stop]] = {
    # Elevation (dummy/verification layer). Blue below sea level, green
    # lowlands through tan/brown mountains to white peaks.
    "terrain": [
        (-100, "#4c6a92"),
        (0, "#7ba7c9"),
        (1, "#2d6a34"),
        (250, "#4e8c47"),
        (600, "#8fb35a"),
        (1000, "#c9c26b"),
        (1600, "#b08d57"),
        (2400, "#8a6a4f"),
        (3200, "#a9a9a9"),
        (4300, "#ffffff"),
    ],
    # First-frost probability (Phase 1): 0 = green (no freeze yet) → 1 = purple.
    "frost_prob": [
        (0.0, "#2e7d32"),
        (0.25, "#c0ca33"),
        (0.5, "#f9a825"),
        (0.75, "#e64a19"),
        (1.0, "#6a1b9a"),
    ],
    # Snow state raster (Phase 3): 0 none / 1 existing snow / 2 fresh forecast.
    "snow_state": [
        (0.0, "#00000000"),
        (1.0, "#9fc5e8"),
        (2.0, "#3d5afe"),
    ],
    # Current conditions.
    "temp_f": [
        (-10, "#313695"), (10, "#4575b4"), (30, "#74add1"), (50, "#abd9e9"),
        (65, "#fee090"), (75, "#fdae61"), (85, "#f46d43"), (95, "#d73027"), (110, "#a50026"),
    ],
    "humidity": [
        (0, "#c9b27c"), (30, "#e8d8a0"), (50, "#cfe3c0"), (70, "#7fc6b6"),
        (85, "#3f9bb8"), (100, "#2166ac"),
    ],
    "dewpoint_f": [
        (20, "#b08d57"), (40, "#d8c690"), (55, "#a8d5a2"), (65, "#5ab4ac"),
        (72, "#3690c0"), (80, "#0570b0"),
    ],
    "wind_mph": [
        (0, "#f7f7f7"), (5, "#d9f0d3"), (10, "#a6dba0"), (15, "#5aae61"),
        (20, "#9970ab"), (30, "#762a83"), (40, "#40004b"),
    ],
    # Spring arrival dates, chronological: Deep-South January violets → June alpine greens.
    "spring_doy": [
        (1, "#6d28d9"), (32, "#3b82f6"), (60, "#22c1a3"),
        (91, "#facc15"), (121, "#f59e0b"), (152, "#c2410c"), (200, "#7f1d1d"),
    ],
    # Fall foliage stages 0-5: green → turning → patchy → near peak → peak → past.
    "foliage": [
        (0, "#2d6a34"), (1, "#7fb254"), (2, "#e8c542"),
        (3, "#f0942d"), (4, "#d64520"), (5, "#7a5540"),
    ],
    # Air quality (EPA AQI bands), particulates, smoke, waves, gusts, CAPE.
    "aqi": [
        (0, "#00e400"), (50, "#ffff00"), (100, "#ff7e00"),
        (150, "#ff0000"), (200, "#8f3f97"), (300, "#7e0023"),
    ],
    "pm25": [
        (0, "#f7f7f7"), (12, "#a1d99b"), (35, "#ffff00"), (55, "#ff7e00"),
        (100, "#ff0000"), (150, "#8f3f97"),
    ],
    "aod": [
        (0, "#f7f7f7"), (0.2, "#d9d9d9"), (0.5, "#bdbdbd"), (1.0, "#969696"),
        (1.5, "#f16913"), (2.0, "#7f2704"),
    ],
    "wave_ft": [
        (0, "#f7fbff"), (2, "#c6dbef"), (4, "#6baed6"), (8, "#2171b5"),
        (12, "#08306b"), (16, "#54278f"), (20, "#3f007d"),
    ],
    "gust_mph": [
        (0, "#f7f7f7"), (20, "#c6dbef"), (35, "#6baed6"), (50, "#fd8d3c"),
        (65, "#e31a1c"), (80, "#800026"),
    ],
    "cape_jkg": [
        (0, "#f7f7f7"), (250, "#c7e9c0"), (1000, "#ffff00"), (2000, "#ff7e00"),
        (3000, "#ff0000"), (4000, "#8f3f97"),
    ],
    # Model parameters.
    "precip_in": [
        (0, "#f7f7f7"), (0.1, "#c7e9c0"), (0.5, "#74c476"), (1, "#238b45"),
        (2, "#2171b5"), (3, "#6a51a3"), (4.5, "#ce1256"), (6, "#67001f"),
    ],
    "snow_in": [
        (0, "#f7f7f7"), (1, "#deebf7"), (3, "#9ecae1"), (6, "#4292c6"),
        (12, "#08519c"), (18, "#54278f"), (24, "#3f007d"),
    ],
    "mslp_hpa": [
        (980, "#5e4fa2"), (996, "#3288bd"), (1008, "#abdda4"), (1016, "#ffffbf"),
        (1024, "#fdae61"), (1040, "#d53e4f"),
    ],
    "z500_dam": [
        (522, "#313695"), (540, "#4575b4"), (552, "#74add1"), (564, "#fee090"),
        (576, "#f46d43"), (588, "#d73027"), (600, "#a50026"),
    ],
    "wind250_mph": [
        (0, "#f7f7f7"), (60, "#c6dbef"), (90, "#6baed6"), (120, "#2171b5"),
        (150, "#6a51a3"), (200, "#3f007d"),
    ],
    # Water surface temperature (°F).
    "sst_f": [
        (32, "#5e4fa2"), (45, "#3288bd"), (55, "#66c2a5"), (65, "#abdda4"),
        (72, "#fee08b"), (78, "#fdae61"), (84, "#f46d43"), (90, "#d53e4f"),
    ],
    # Expected first-frost date, chronological: alpine August reds → December blues.
    "frost_date": [
        (196, "#7f1d1d"),
        (214, "#c2410c"),
        (245, "#f59e0b"),
        (275, "#facc15"),
        (306, "#22c1a3"),
        (336, "#3b82f6"),
        (365, "#6d28d9"),
    ],
}

# Optional human tick labels rendered by the frontend legend.
LEGEND_LABELS: Dict[str, List[dict]] = {
    "spring_doy": [
        {"value": 32, "label": "Feb"},
        {"value": 60, "label": "Mar"},
        {"value": 91, "label": "Apr"},
        {"value": 121, "label": "May"},
        {"value": 152, "label": "Jun"},
    ],
    "foliage": [
        {"value": 0, "label": "green"},
        {"value": 1, "label": "turning"},
        {"value": 2, "label": "patchy"},
        {"value": 3, "label": "near peak"},
        {"value": 4, "label": "peak"},
        {"value": 5, "label": "past"},
    ],
    "frost_date": [
        {"value": 214, "label": "Aug"},
        {"value": 245, "label": "Sep"},
        {"value": 275, "label": "Oct"},
        {"value": 306, "label": "Nov"},
        {"value": 336, "label": "Dec"},
    ],
    "snow_state": [
        {"value": 1, "label": "snow on ground"},
        {"value": 2, "label": "fresh snow expected"},
    ],
}


def _hex_to_rgba(color: str) -> Tuple[int, int, int, int]:
    c = color.lstrip("#")
    if len(c) == 6:
        r, g, b = (int(c[i : i + 2], 16) for i in (0, 2, 4))
        return r, g, b, 255
    if len(c) == 8:
        r, g, b, a = (int(c[i : i + 2], 16) for i in (0, 2, 4, 6))
        return r, g, b, a
    raise ValueError(f"bad hex color: {color}")



# ---------------------------------------------------------------------------
# Stepped (banded) model palettes — classic weather-map bins, no smoothing.
# Model layers point here; swap back to the smooth names to revert.
# ---------------------------------------------------------------------------

COLORMAPS.update({
    "temp_f_step": [
        (-10, "#7b2fbe"), (0, "#5e4fa2"), (10, "#3c6fc4"), (20, "#4393c3"),
        (30, "#74c7d8"), (40, "#a6dba0"), (50, "#5aae61"), (60, "#c8e26a"),
        (65, "#ffe066"), (70, "#ffc44d"), (75, "#ffa733"), (80, "#ff7f2a"),
        (85, "#f4501e"), (90, "#d62f1f"), (95, "#b01c30"), (100, "#8e0f4d"),
        (105, "#c231a1"), (110, "#f06ac5"),
    ],
    "precip_in_step": [
        (0.0, "#00000000"), (0.01, "#b7e4b0"), (0.1, "#6cc069"), (0.25, "#2e9448"),
        (0.5, "#f6ee54"), (1.0, "#f2b13c"), (1.5, "#ee7a30"), (2.0, "#e33f24"),
        (3.0, "#b81d54"), (4.0, "#8f2f97"), (6.0, "#5c5cc9"),
    ],
    "snow_in_step": [
        (0.0, "#00000000"), (0.1, "#bdd7e7"), (1.0, "#6baed6"), (2.0, "#3182bd"),
        (4.0, "#08519c"), (6.0, "#5e3c99"), (8.0, "#8b4bab"), (12.0, "#b25fbc"),
        (18.0, "#d977c8"), (24.0, "#f79fd6"),
    ],
    "mslp_step": [
        (980, "#5e4fa2"), (988, "#3288bd"), (996, "#66a5cc"), (1004, "#99c9a3"),
        (1012, "#e6e2b8"), (1016, "#e0c07a"), (1024, "#cf8c4e"), (1032, "#b25636"),
        (1040, "#8c2d1e"),
    ],
    "z500_step": [
        (522, "#5e4fa2"), (534, "#3f61c4"), (546, "#3288bd"), (552, "#59a8b8"),
        (558, "#79c5a4"), (564, "#a8d888"), (570, "#dbe98a"), (576, "#ffe066"),
        (582, "#fdae42"), (588, "#f2652f"), (594, "#d62f1f"), (600, "#a01327"),
    ],
    "w250_step": [
        (0, "#00000000"), (50, "#c8c8d8"), (70, "#95a8d8"), (90, "#5f7fd8"),
        (110, "#4b4fc4"), (130, "#7a3fbe"), (150, "#b23fb2"), (175, "#e35fc4"),
        (200, "#ff9fd8"),
    ],
    "gust_step": [
        (0, "#00000000"), (10, "#cfe3c0"), (20, "#8fcf8a"), (30, "#f6ee54"),
        (40, "#f2b13c"), (50, "#ee7a30"), (60, "#e33f24"), (70, "#b81d54"),
        (80, "#8f2f97"),
    ],
    "cape_step": [
        (0, "#00000000"), (250, "#b7e4b0"), (500, "#6cc069"), (1000, "#f6ee54"),
        (1500, "#f2b13c"), (2000, "#ee7a30"), (2500, "#e33f24"), (3000, "#b81d54"),
        (4000, "#8f2f97"),
    ],
})

# Colormaps rendered as DISCRETE bands (no interpolation between stops).
STEPPED = {
    "temp_f_step", "precip_in_step", "snow_in_step", "mslp_step",
    "z500_step", "w250_step", "gust_step", "cape_step",
}


def build_colormap(
    stops: Sequence[Stop], vmin: float, vmax: float, stepped: bool = False
) -> Dict[int, Tuple[int, int, int, int]]:
    """Expand stops into a rio-tiler colormap: {0..255: (r, g, b, a)}.

    Data is expected to be rescaled so vmin→0 and vmax→255 before the lookup
    is applied.
    """
    if vmax <= vmin:
        raise ValueError("vmax must be > vmin")
    xs = [(v - vmin) / (vmax - vmin) * 255.0 for v, _ in stops]
    cs = [_hex_to_rgba(c) for _, c in stops]

    cmap: Dict[int, Tuple[int, int, int, int]] = {}
    for i in range(256):
        if i <= xs[0]:
            cmap[i] = cs[0]
            continue
        if i >= xs[-1]:
            cmap[i] = cs[-1]
            continue
        if stepped:
            k = 0
            for j, x in enumerate(xs):
                if i >= x:
                    k = j
            cmap[i] = cs[k]
            continue
        for k in range(len(xs) - 1):
            if xs[k] <= i <= xs[k + 1]:
                span = xs[k + 1] - xs[k]
                t = 0.0 if span == 0 else (i - xs[k]) / span
                cmap[i] = tuple(
                    int(round(cs[k][j] + t * (cs[k + 1][j] - cs[k][j])))
                    for j in range(4)
                )  # type: ignore[assignment]
                break
    return cmap


def legend_json(name: str, vmin: float, vmax: float, units: str) -> dict:
    """Legend block for meta/{layer}/latest.json — frontend renders this."""
    stops = COLORMAPS[name]
    out = {
        "type": "gradient",
        "colormap": name,
        "vmin": vmin,
        "vmax": vmax,
        "units": units,
        "stops": [{"value": v, "color": c} for v, c in stops],
    }
    if name in STEPPED:
        out["stepped"] = True
    if name in LEGEND_LABELS:
        out["labels"] = LEGEND_LABELS[name]
    return out
