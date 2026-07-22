# seasonmap

Year-round, map-first web app showing daily-updated, forecast-aware layers for
seasonal natural phenomena across CONUS — first/last frost, snow line,
leaf-out & bloom, wildflowers, fall foliage — plus an AI assistant that answers
natural-language questions against the map data.

**Architecture in one line:** the backend is a nightly batch job, not a live
API server. Everything is precomputed onto one canonical grid, rendered to
PMTiles, and served as static files from object storage. Zero-scale-cost
frontend.

```
gridded inputs → per-cell scoring → COG + PMTiles → R2 → MapLibre + date slider
```

See `docs/BUILD_PLAN.md` for the full plan; phase status below.

## Layout

| Path | What |
|---|---|
| `pipeline/` | Python batch jobs (nightly via GitHub Actions) |
| `pipeline/core/grid.py` | canonical PRISM 4km CONUS grid — everything resamples onto this |
| `pipeline/core/sources/` | one module per upstream data source |
| `pipeline/layers/` | one scoring module per phenomenon layer |
| `web/` | Vite + React + TS + MapLibre GL frontend |
| `worker/` | Cloudflare Worker: `/api/chat` AI assistant (Phase 2) |
| `scripts/preview.py` | stitch a PMTiles zoom level into a PNG for eyeballing |

## Quickstart (local)

```bash
# 1. Pipeline: compute a layer and publish to ./out
pip install -r pipeline/requirements-dev.txt
python -m pipeline.run --layer elevation --date today --out ./out

# 2. Tests
python -m pytest pipeline/tests -q

# 3. Frontend against local data
cd web
npm install
npm run link-data     # symlinks ../out → web/public/data
npm run dev           # http://localhost:5173/?layer=frost_date

# 4. AI assistant (Phase 2) — optional, needs an Anthropic key
cd ../worker
npm install
cp .dev.vars.example .dev.vars    # put your ANTHROPIC_API_KEY inside
npx wrangler dev                  # /api on :8787; the vite dev server proxies to it
```

With R2 configured (`R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`R2_BUCKET`), `pipeline.run` publishes to the bucket instead; the deployed
frontend points at it via `VITE_DATA_BASE`.

> **Corporate npm proxies (Socket et al.)**: security firewalls that quarantine
> recently-published packages will 403 fresh lockfile pins. The committed
> lockfile is generated with `--before` so every pin is ≥1 week old at commit
> time. If a future `npm install`/`npm update` hits a
> `Blocked by Security Policy … Recently published` error, resolve against a
> dated registry snapshot: `rm -rf node_modules package-lock.json && npm
> install --before=$(date -v-7d +%Y-%m-%d)` (GNU: `date -d '7 days ago'`).

## Storage layout (bucket-relative)

```
data/{layer}/{YYYY-MM-DD}.tif        # scored values on canonical grid (COG)
tiles/{layer}/{YYYY-MM-DD}.pmtiles   # rendered raster tiles for that date
meta/{layer}/latest.json             # date list, legend, stats, status
static/                              # one-time cached inputs (normals, elevation, …)
```

Forecast-aware layers write today + up to 7 forecast dates each night.
Every run is idempotent (same layer/date overwrites); one layer failing
publishes an `error` status for itself and never blocks the others.

## Deployment prerequisites (GitHub repo settings)

Secrets: `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`,
`R2_BUCKET`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`
(+ `ANTHROPIC_API_KEY` as a worker secret in Phase 2).
Variables: `DATA_BASE_URL` (public R2 custom domain),
`PIPELINE_USER_AGENT` (identify to upstream APIs; NWS requires it).

## Phase status

- [x] **Phase 0 — Skeleton**: canonical grid + tests, elevation dummy layer
      end-to-end (NOAA ETOPO → COG → PMTiles → publish), CI + nightly
      workflows, MapLibre frontend with layer switcher / date slider / legend /
      shareable URLs / status badge
- [x] **Phase 1 — Frost**: PRISM monthly normals → daily-hazard first-freeze
      CDF (σ_daily = 3.75 °C calibrated on the 5 acceptance cities, worst
      error 4 days vs. published climatology) + Open-Meteo forecast blend;
      `frost` probability (11-date slider window) + `frost_date` median-date
      layers; click popups sample real COG values via geotiff.js
- [x] **Phase 2 — AI assistant**: worker `/api/chat` with a server-side
      Anthropic tool loop (get_conditions, find_peak, drive_time_search,
      get_layer_summary) reading compact ~12 km query grids
      (`query/{layer}/{date}.json`, published by the pipeline); chat drawer UI
      with result pins on the map; `/api/query` for direct lookups; IP rate
      limiting. Needs `ANTHROPIC_API_KEY` (worker secret / `.dev.vars`) to
      answer — everything else runs without it.
      Also: frost/freeze/hard-freeze threshold toggle (36/32/28°F variants of
      the date layer) and the map hard-scoped to CONUS (maxBounds + world
      mask).
- [x] **Phase 3 — Snow line**: SNODAS depth ingest (existing snow) + forecast
      fresh snow from Open-Meteo `snowfall_sum` (v1 deviation, see
      docs/BUILD_PLAN.md) over an 8-date window
- [ ] **Phase 4 — Leaf-out & bloom (NPN), wildflower GDD stages**
- [ ] **Phase 5 — Foliage + polish**

Known v1.x refinements: alpine cells freeze too early in the hazard model
(elevation-dependent σ_daily), county-level vector tiles for frost hover
labels, mirrored last-spring-frost product (Feb–May), snowline freezing-level
method, nightly σ recalibration against NWS station normals.

## Operational rules

- One canonical grid (`pipeline/core/grid.py`); every layer resamples onto it.
- Respect upstream ToS: proper User-Agent, aggressive caching, never per-cell
  API requests (coarse point grids + interpolation).
- No secrets in the frontend bundle. Ever.
- Performance targets: nightly pipeline < 30 min · tile load < 300 ms from CDN
  · chat tool answers < 3 s p50.
