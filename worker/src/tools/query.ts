/**
 * Data access + tools for the seasonmap assistant.
 *
 * Everything reads the pipeline's published static files over HTTP
 * (DATA_BASE_URL): meta/{layer}/latest.json and the compact ~12 km query
 * grids at query/{layer}/{date}.json — never the full COGs. Point/region
 * answers stay well under a second.
 */

export interface Env {
  ANTHROPIC_API_KEY: string;
  DATA_BASE_URL: string;
}

interface QueryGrid {
  shape: [number, number];
  transform: [number, number, number, number, number, number];
  scale: number;
  offset: number;
  nodata: number;
  data: string; // base64 int16le
}

export interface LayerMeta {
  layer: string;
  description: string;
  dates: string[];
  latest: string | null;
  units: string;
  value_format?: string;
  stats: Record<string, number | null>;
  status: { state: string; message: string | null };
}

const cache = new Map<string, unknown>();

async function getJson<T>(env: Env, path: string): Promise<T> {
  const key = path;
  if (cache.has(key)) return cache.get(key) as T;
  const resp = await fetch(`${env.DATA_BASE_URL}/${path}`);
  if (!resp.ok) throw new Error(`data fetch ${path}: HTTP ${resp.status}`);
  const body = (await resp.json()) as T;
  if (cache.size > 40) cache.delete(cache.keys().next().value as string);
  cache.set(key, body);
  return body;
}

export const getMeta = (env: Env, layer: string) =>
  getJson<LayerMeta>(env, `meta/${layer}/latest.json`);

function decodeGrid(qg: QueryGrid): Int16Array {
  const bin = atob(qg.data);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new Int16Array(bytes.buffer);
}

async function getGrid(env: Env, layer: string, date: string) {
  const qg = await getJson<QueryGrid>(env, `query/${layer}/${date}.json`);
  const key = `dec:${layer}:${date}`;
  let raw = cache.get(key) as Int16Array | undefined;
  if (!raw) {
    raw = decodeGrid(qg);
    cache.set(key, raw);
  }
  return { qg, raw };
}

export function resolveDate(meta: LayerMeta, date?: string): string {
  if (date && meta.dates.includes(date)) return date;
  if (date && meta.dates.length) {
    const sorted = [...meta.dates].sort();
    if (date < sorted[0]) return sorted[0];
    const before = sorted.filter((d) => d <= date);
    return before[before.length - 1] ?? sorted[sorted.length - 1];
  }
  return meta.latest ?? meta.dates[meta.dates.length - 1];
}

function cellValue(qg: QueryGrid, raw: Int16Array, row: number, col: number): number | null {
  const [h, w] = qg.shape;
  if (row < 0 || row >= h || col < 0 || col >= w) return null;
  const v = raw[row * w + col];
  return v === qg.nodata ? null : qg.offset + qg.scale * v;
}

function cellFor(qg: QueryGrid, lat: number, lon: number): [number, number] {
  const [a, , c, , e, f] = qg.transform;
  return [Math.floor((lat - f) / e), Math.floor((lon - c) / a)];
}

function centerOf(qg: QueryGrid, row: number, col: number): [number, number] {
  const [a, , c, , e, f] = qg.transform;
  return [f + (row + 0.5) * e, c + (col + 0.5) * a]; // [lat, lon]
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatValue(value: number | null, meta: LayerMeta): string {
  if (value === null) {
    return meta.value_format === "doy_date" ? "rarely occurs" : "no data";
  }
  switch (meta.value_format) {
    case "probability":
      return `${Math.round(value * 100)}% probability`;
    case "doy_date": {
      const d = new Date(Date.UTC(2026, 0, 1) + (Math.round(value) - 1) * 86400000);
      return `~${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
    }
    case "snow_state":
      return value >= 1.5 ? "fresh snow expected" : value >= 0.5 ? "snow on the ground" : "no snow";
    default:
      return `${Math.round(value).toLocaleString()} ${meta.units}`;
  }
}

const R_EARTH_MI = 3958.8;
export function haversineMiles(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const s =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * R_EARTH_MI * Math.asin(Math.sqrt(s));
}

// ---------------------------------------------------------------------------
// Tools
// ---------------------------------------------------------------------------

export interface Pin {
  lat: number;
  lon: number;
  label: string;
}

export interface Highlight {
  layer: string;
  where: { prop: string; equals?: string | number; min?: number; max?: number }[];
}

export interface ToolOutcome {
  result: unknown;
  pins?: Pin[];
  highlight?: Highlight;
}

export async function getConditions(
  env: Env,
  args: { lat: number; lon: number; layer: string; date?: string }
): Promise<ToolOutcome> {
  const meta = await getMeta(env, args.layer);
  const date = resolveDate(meta, args.date);
  const { qg, raw } = await getGrid(env, args.layer, date);
  const [row, col] = cellFor(qg, args.lat, args.lon);
  let value = cellValue(qg, raw, row, col);
  let note: string | undefined;
  let pinLat = args.lat;
  let pinLon = args.lon;

  // Nearshore/masked-cell fallback: coastal points (beaches!) often land in a
  // masked cell while real data sits a cell or two away. Spiral outward to
  // the nearest valid cell (~up to 75 km) rather than reporting "no data".
  if (value === null) {
    outer: for (let r = 1; r <= 6; r++) {
      for (let dr = -r; dr <= r; dr++) {
        for (let dc = -r; dc <= r; dc++) {
          if (Math.max(Math.abs(dr), Math.abs(dc)) !== r) continue;
          const v = cellValue(qg, raw, row + dr, col + dc);
          if (v !== null) {
            value = v;
            const [nlat, nlon] = centerOf(qg, row + dr, col + dc);
            const miles = haversineMiles(args.lat, args.lon, nlat, nlon);
            note = `nearest data ${miles.toFixed(0)} mi away (requested point has no coverage — e.g. land-masked shoreline)`;
            pinLat = nlat;
            pinLon = nlon;
            break outer;
          }
        }
      }
    }
  }

  const text = formatValue(value, meta);
  return {
    result: { layer: args.layer, date, value, text, units: meta.units, note },
    pins: [{ lat: pinLat, lon: pinLon, label: text }],
  };
}

export async function findPeak(
  env: Env,
  args: {
    layer: string;
    bbox?: [number, number, number, number]; // west, south, east, north
    date?: string;
    mode?: "max" | "min";
  }
): Promise<ToolOutcome> {
  const meta = await getMeta(env, args.layer);
  const date = resolveDate(meta, args.date);
  const { qg, raw } = await getGrid(env, args.layer, date);
  const [h, w] = qg.shape;
  const mode = args.mode ?? "max";
  const [bw, bs, be, bn] = args.bbox ?? [-125.1, 24, -66.4, 50];

  let best: { v: number; row: number; col: number } | null = null;
  let sum = 0;
  let n = 0;
  for (let row = 0; row < h; row++) {
    for (let col = 0; col < w; col++) {
      const [lat, lon] = centerOf(qg, row, col);
      if (lon < bw || lon > be || lat < bs || lat > bn) continue;
      const v = cellValue(qg, raw, row, col);
      if (v === null) continue;
      sum += v;
      n++;
      if (!best || (mode === "max" ? v > best.v : v < best.v)) best = { v, row, col };
    }
  }
  if (!best) return { result: { layer: args.layer, date, found: false } };
  const [lat, lon] = centerOf(qg, best.row, best.col);
  const text = formatValue(best.v, meta);
  return {
    result: {
      layer: args.layer,
      date,
      found: true,
      mode,
      value: best.v,
      text,
      lat,
      lon,
      region_mean: n ? sum / n : null,
      cells_considered: n,
    },
    pins: [{ lat, lon, label: `${mode === "max" ? "Peak" : "Low"}: ${text}` }],
  };
}

export async function driveTimeSearch(
  env: Env,
  args: {
    origin_lat: number;
    origin_lon: number;
    layer: string;
    max_hours: number;
    date?: string;
    min_value?: number;
    max_value?: number;
  }
): Promise<ToolOutcome> {
  const meta = await getMeta(env, args.layer);
  const date = resolveDate(meta, args.date);
  const { qg, raw } = await getGrid(env, args.layer, date);
  const [h, w] = qg.shape;

  // Sensible per-layer defaults for "worth driving to".
  let minV = args.min_value;
  const maxV = args.max_value;
  if (minV === undefined && maxV === undefined) {
    if (meta.value_format === "snow_state") minV = 0.75;
    else if (meta.value_format === "probability") minV = 0.5;
  }

  const AVG_MPH = 45; // v1: great-circle distance at 45 mph — approximate!
  const maxMiles = args.max_hours * AVG_MPH;
  const hits: { lat: number; lon: number; v: number; miles: number }[] = [];
  for (let row = 0; row < h; row++) {
    for (let col = 0; col < w; col++) {
      const v = cellValue(qg, raw, row, col);
      if (v === null) continue;
      if (minV !== undefined && v < minV) continue;
      if (maxV !== undefined && v > maxV) continue;
      const [lat, lon] = centerOf(qg, row, col);
      const miles = haversineMiles(args.origin_lat, args.origin_lon, lat, lon);
      if (miles <= maxMiles) hits.push({ lat, lon, v, miles });
    }
  }
  hits.sort((x, y) => x.miles - y.miles);
  // De-cluster: keep hits at least ~25 mi apart so pins spread usefully.
  const picked: typeof hits = [];
  for (const hLoc of hits) {
    if (picked.every((p) => haversineMiles(p.lat, p.lon, hLoc.lat, hLoc.lon) > 25)) {
      picked.push(hLoc);
    }
    if (picked.length >= 5) break;
  }
  return {
    result: {
      layer: args.layer,
      date,
      approximation: "great-circle distance at 45 mph average — not road routing",
      matches: picked.map((p) => ({
        lat: p.lat,
        lon: p.lon,
        drive_hours_estimate: +(p.miles / AVG_MPH).toFixed(1),
        value: p.v,
        text: formatValue(p.v, meta),
      })),
      total_matching_cells: hits.length,
    },
    pins: picked.map((p) => ({
      lat: p.lat,
      lon: p.lon,
      label: `${formatValue(p.v, meta)} · ~${(p.miles / AVG_MPH).toFixed(1)}h drive`,
    })),
  };
}

const geoCache = new Map<string, any>();

export async function layerFeatures(
  env: Env,
  args: {
    layer: string;
    date?: string;
    where?: { prop: string; equals?: string | number; min?: number; max?: number }[];
    limit?: number;
  }
): Promise<ToolOutcome> {
  const meta = await getMeta(env, args.layer);
  const date = resolveDate(meta, args.date);
  const key = `${args.layer}:${date}`;
  let geo = geoCache.get(key);
  if (!geo) {
    const resp = await fetch(`${env.DATA_BASE_URL}/data/${args.layer}/${date}.geojson`);
    if (!resp.ok) throw new Error(`layer data HTTP ${resp.status}`);
    geo = await resp.json();
    if (geoCache.size > 3) geoCache.delete(geoCache.keys().next().value as string);
    geoCache.set(key, geo);
  }
  const match = (p: Record<string, any>) =>
    (args.where ?? []).every((w) => {
      const v = p?.[w.prop];
      if (w.equals !== undefined) return String(v) === String(w.equals);
      const n = Number(v);
      if (w.min !== undefined && !(n >= w.min)) return false;
      if (w.max !== undefined && !(n <= w.max)) return false;
      return true;
    });
  const hits = (geo.features ?? []).filter((f: any) => match(f.properties ?? {}));
  const limit = Math.min(args.limit ?? 10, 25);
  const firstCoord = (g: any): [number, number] | null => {
    const c = g?.coordinates;
    if (!c) return null;
    if (g.type === "Point") return c;
    if (g.type === "LineString") return c[0];
    if (g.type === "Polygon") return c[0]?.[0] ?? null;
    if (g.type === "MultiPolygon") return c[0]?.[0]?.[0] ?? null;
    return null;
  };
  const sample = hits.slice(0, limit);
  return {
    result: {
      layer: args.layer,
      date,
      total_matches: hits.length,
      features: sample.map((f: any) => f.properties),
    },
    highlight: args.where?.length ? { layer: args.layer, where: args.where } : undefined,
    pins: sample
      .map((f: any) => {
        const c = firstCoord(f.geometry);
        const name = f.properties?.name ?? f.properties?.label ?? f.properties?.date ?? "feature";
        return c ? { lat: c[1], lon: c[0], label: String(name) } : null;
      })
      .filter(Boolean)
      .slice(0, 8) as Pin[],
  };
}

export async function getLayerSummary(env: Env, args: { layer: string }): Promise<ToolOutcome> {
  const meta = await getMeta(env, args.layer);
  const date = resolveDate(meta);
  const { qg, raw } = await getGrid(env, args.layer, date);
  const [h, w] = qg.shape;
  const regions: Record<string, { sum: number; n: number }> = {};
  for (let row = 0; row < h; row++) {
    for (let col = 0; col < w; col++) {
      const v = cellValue(qg, raw, row, col);
      if (v === null) continue;
      const [lat, lon] = centerOf(qg, row, col);
      const ns = lat >= 39 ? "north" : "south";
      const ew = lon <= -100 ? "west" : "east";
      const k = `${ns}${ew}`;
      regions[k] = regions[k] ?? { sum: 0, n: 0 };
      regions[k].sum += v;
      regions[k].n++;
    }
  }
  const quadrants: Record<string, string> = {};
  for (const [k, { sum, n }] of Object.entries(regions)) {
    quadrants[k] = formatValue(sum / n, meta);
  }
  return {
    result: {
      layer: args.layer,
      date,
      description: meta.description,
      status: meta.status,
      dates_available: meta.dates,
      stats: meta.stats,
      quadrant_means: quadrants,
    },
  };
}
