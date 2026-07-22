# Seasonal Map — Build Plan & Agent Instructions

Year-round, map-first web app showing daily-updated, forecast-aware layers for
seasonal natural phenomena across CONUS, plus an AI assistant (Claude API with
tool use) that answers natural-language questions against the map data and
drops results onto the map. (Original product, original voice — no borrowed
taglines; existing fall-foliage sites are competitors, not templates.)

## Design principles

- The backend is a **nightly batch job**, not a live API server. All layer data
  is precomputed into static tiles served from object storage. Zero-scale-cost
  frontend.
- Every layer is the same pipeline: gridded inputs → per-cell scoring function
  → colored tiles → date slider. Build the skeleton once; each layer is a new
  scoring module.
- Free data sources and free/cheap infra only.

## Tech stack

| Component | Choice |
|---|---|
| Frontend | Vite + React + TypeScript + MapLibre GL JS (no Mapbox token) |
| Basemap | Protomaps PMTiles or free OSM raster tiles (simple in v1) |
| Layer tiles | PMTiles (raster for continuous fields, vector for county polygons) served via range requests |
| Tile generation | Python: rasterio, rio-tiler → pmtiles; tippecanoe for vector |
| Batch | Python 3.11+, GitHub Actions cron (nightly ~06:00 UTC) |
| Storage/CDN | Cloudflare R2 public bucket + custom domain (free egress) |
| AI chat | Cloudflare Worker proxying Anthropic Messages API (claude-sonnet-4-6), key server-side |
| Query data for AI | compact per-layer Zarr/COG grids in R2 + small precomputed JSON summaries (<1s answers) |

## Canonical grid

**PRISM 4km CONUS grid** (EPSG:4269/NAD83, 2.5 arc-min cells, 1405×621).
Every layer's daily output is float32 on this grid:

```
r2://data/{layer}/{YYYY-MM-DD}.tif        # scored values
r2://tiles/{layer}/{YYYY-MM-DD}.pmtiles   # rendered tiles
r2://meta/{layer}/latest.json             # dates, legend, stats, colormap
```

Forecast-aware layers write today + up to 7 forecast dates nightly.

## Data sources (all free)

| Source | Used for | Notes |
|---|---|---|
| Open-Meteo | tmin/tmax/precip forecast (16d) + archive, GDD | no key; coarse ~25km point grid + interpolate — never per-cell |
| NWS API | gridpoint forecasts, freezing level, alerts | no key; **requires User-Agent** |
| PRISM | 30-yr daily tmin normals (frost climatology) | one-time ingest, cache in static/ |
| USA-NPN Geoserver | Spring Leaf & Bloom Index (2.5km) | WCS/WMS, no key; seasonal availability |
| NOHRSC/SNODAS | snow depth/cover ~1km | daily tarballs, no key |
| ETOPO/3DEP | elevation | one-time ingest, cached (Phase 0 uses NCEI `DEM_global_mosaic`; `DEM_all` returns zeros over land at CONUS scale — do not use) |
| Anthropic API | chat assistant | worker secret |

## Layer scoring models (v1: simple + explainable)

1. **Frost (launch layer)** — per-cell CDF of first fall freeze (tmin ≤ 32°F)
   from PRISM normals; next-10-day forecast override; blend
   `P = max(P_climo, P_forecast)` with forecast confidence decaying after
   day 7. Outputs: probability raster per slider date, expected-first-frost
   raster, county vector tiles. Mirrored last-spring-frost (Feb–May).
   *As-built (Phase 1):* the CDF is a daily-hazard survival model over the
   PRISM-normal tmin curve — mean-curve crossing biases 3-4 weeks late because
   first freeze is an extreme-value event. σ_daily = 3.75 °C calibrated
   against published city medians (worst error 4 days). County vector tiles
   and the spring mirror are Phase 1.5.
2. **Snow line** — SNODAS depth > 2.5cm mask (existing snow) + forecast fresh
   snow where elevation ≥ freezing level − 300m AND precip ≥ 2mm.
   Values 0/1/2; per-region snow-line elevation for AI tools.
   *As-built (Phase 3):* fresh snow v1 uses Open-Meteo `snowfall_sum ≥ 1 cm`
   (the model resolves precipitation phase against its own terrain); the
   freezing-level ∧ precip method against 4 km terrain is the v1.1 upgrade.
   "Fresh by date D" is the union of forecast snowfall days ≤ D.
3. **Leaf-out & bloom** — v1 NPN passthrough (two sub-layers); v2 own GDD
   model (base 32°F, Jan 1) for forecast extension.
4. **Wildflower** — Appalachian GDD stages (base 40°F, Feb 1, elevation-aware
   calibration table); west superbloom potential 0–100 from Oct–Mar precip
   percentile vs PRISM normals (weekly).
5. **Foliage (last)** — chilling + photoperiod + drought stress → stage 0–5;
   ship after the app has users, calibrate against archived reports.

## Frontend requirements

Full-bleed MapLibre, CONUS default, geolocate button; one active thematic
layer + date slider (today−3 … today+7); hover/tap popup with human phrasing
("First frost: ~Oct 26 · 68% by Nov 1"); legend driven by meta JSON; collapsible
chat drawer that renders tool-result pins and flies to them; mobile-first;
shareable URLs `/?layer=frost&date=2026-10-15&z=7&ll=35.2,-80.9`.

## AI assistant (worker, Phase 2)

`POST /api/chat` → Anthropic Messages API (claude-sonnet-4-6), server-side tool
loop (cap 6 iterations, rate-limit by IP, never expose the key):

```
get_conditions(lat, lon, layer, date)
find_peak(layer, bbox_or_region, date_range)
drive_time_search(origin, layer, threshold, max_hours, date)   # v1: great-circle @45mph, say so
get_layer_summary(layer)
```

Tools read per-date COGs / downsampled ~12km query grids from R2. Return
`{answer_markdown, pins: [{lat, lon, label}], layer, date}` so the frontend
can act.

## Build phases & acceptance criteria

- **Phase 0 — Skeleton** ✅: canonical grid + unit tests; dummy layer
  (elevation) end-to-end source → COG → PMTiles → publish → MapLibre;
  scheduled + manual GitHub Action. *Accept: dummy layer visible with correct
  geography.*
- **Phase 1 — Frost + core UI**: PRISM CDF + forecast blend, 10-date window;
  switcher/slider/popup/legend/URLs. *Accept: 5 spot-check cities within ±10
  days of published climatology; slider changes map; popup matches COG.*
- **Phase 2 — AI assistant**: worker tool loop, chat drawer, pins. *Accept:
  the three canonical questions return values matching the map.*
- **Phase 3 — Snow line**: SNODAS + forecast; find_peak/drive_time work.
  *Accept: visual match with NOHRSC public map; 7 forecast days.*
- **Phase 4 — Leaf-out & bloom (NPN), then Wildflower**. *Accept: visual match
  with usanpn.org; GDD verified against hand-computed values for 3 stations.*
- **Phase 5 — Foliage + polish**: archive page, OG images, analytics.

## Operational notes

- **Idempotency**: re-running a layer/date overwrites outputs safely.
- **Failure isolation**: one layer failing never blocks others; per-layer
  status in meta → "data delayed" badge in UI.
- **ToS**: proper User-Agent (NWS requires it), aggressive caching, coarse
  point grids only.
- **Secrets**: R2 + Anthropic keys as GitHub/worker secrets; never in the
  frontend bundle.
- **Testing**: unit tests per scoring function (tiny synthetic grids), golden
  tiling test, CI smoke test fetching one real Open-Meteo point (Phase 1).
- **Targets**: nightly < 30 min; tile load < 300ms; chat p50 < 3s.
- **Branding**: placeholder `seasonmap`, swappable via `web/src/config.ts`.

## Explicit non-goals for v1

Accounts/saved locations/notifications; AK/HI/global coverage; crowdsourced
photo reports; true isochrone routing (great-circle approximation is fine).
