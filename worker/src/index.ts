/**
 * seasonmap API worker — Phase 2.
 *
 * POST /api/chat  — Anthropic Messages proxy with a server-side tool loop
 *                   (≤6 iterations) over the published query grids.
 * GET  /api/query — direct single-point lookup (debug / cheap integrations).
 * GET  /api/health
 *
 * The API key lives in a worker secret; responses return
 * {answer_markdown, pins, layer, date} so the frontend can act on the map.
 */
import { climateHistory } from "./tools/climate";
import { acisQuery, nwsQuery } from "./tools/raw";
import {
  driveTimeSearch,
  type Env,
  findPeak,
  getConditions,
  getLayerSummary,
  type Highlight,
  layerFeatures,
  type Pin,
} from "./tools/query";

const MODEL = "claude-sonnet-4-6";
const MAX_TOOL_ITERATIONS = 8; // raw-API composition needs self-correction room
const RATE_LIMIT = { max: 20, windowMs: 10 * 60 * 1000 };

const LAYER_CATALOG = `
EVERY layer below is queryable — gridded layers via get_conditions/find_peak/
drive_time_search, vector layers via layer_features. Never claim a layer can't
be queried; if a point returns no data the tool now reports the nearest valid
cell instead.

Gridded (get_conditions etc.):
- water_temp — water surface temperature °F, oceans + Great Lakes (MUR SST,
  daily). Land is masked; beach questions resolve to the nearest water cell.
- conditions_temp / conditions_humidity / conditions_dewpoint /
  conditions_wind — current conditions (°F, %, °F, mph), refreshed hourly.
- frost_date_36 / frost_date / frost_date_28 — median first fall frost (36°F),
  freeze (32°F), hard freeze (28°F) dates from 30-yr climatology; null =
  rarely occurs.
- frost — probability the first fall freeze (32°F) has occurred by a given
  date; daily dates (today−3…+7) carry the forecast, weekly dates run to
  season end on climatology.
- snowline — 0 none / 1 snow on ground (SNODAS) / 2 fresh snow expected;
  today…+7.
- foliage — fall color stage 0-5 (0 green, 1 turning, 2 patchy, 3 near peak,
  4 PEAK, 5 past), weekly dates Sep–Nov. "When is peak at X" → call
  get_conditions on foliage for several of its dates (check meta via
  get_layer_summary) and report the window where stage reaches 4.
- leafout / leafout_bloom — spring first-leaf / first-bloom day-of-year
  (USA-NPN, current year); weekly Feb–Jun dates progressively reveal arrival.
- forecast models — prefixes gfs_, euro_, hrrr_ (48h CONUS mesoscale),
  ukmet_, icon_, gem_; fields {model}_tmax °F, {model}_precip accumulated in,
  {model}_snow accumulated in, {model}_mslp hPa 12Z, and (except hrrr)
  {model}_z500 dam 12Z + {model}_w250 jet mph 12Z. Dates are lead times.
  Model-comparison questions → query several models at the same point/date.
- {model}_gusts peak gusts mph and {model}_cape daily-max CAPE J/kg exist for
  every model too.
- air_aqi / air_pm25 / air_smoke — current US AQI, PM2.5 µg/m³, aerosol
  optical depth (smoke/haze), refreshed hourly.
- waves — max wave height forecast ft (marine model), dates are lead times.
- elevation — meters.

Vector (layer_features; properties listed):
- hurricanes_majors / hurricanes_modern — track segments {name, year, cat,
  peak_cat, max_wind_kt, basin}. hurricanes_active — live storms + NHC cones.
- tornadoes_violent / tornadoes_strong / tornadoes_weak — {date, year, state,
  ef, fatalities, injuries, length_mi}.
- drought — U.S. Drought Monitor {dm: D0-D4, label}.
- rivers — USGS gauge points {name, temp_f, flow_cfs, temp_class, as_of},
  refreshed hourly. River temperature/flow questions ("how warm is the French
  Broad", "is the Colorado running high") → layer_features with where on
  name (partial names won't match — prefer numeric ranges or fetch nearby by
  min/max on temp_f/flow_cfs) or use get_conditions on water_temp for lakes.

Live point lookups beyond layers: active alerts at a point via
nws_query "/alerts/active?point={lat},{lon}".`;

const SYSTEM_PROMPT = `You are the seasonmap assistant — a sharp, friendly guide to
seasonal natural phenomena across the US: first frost and freeze, snow, and (soon)
bloom and fall color. Users are planning real trips and garden decisions.

Answer from tool data, never from guesses. Always name the layer and date your
answer describes. The user's active tab is CONTEXT, not a constraint: answer
from whichever layer holds the answer — the map follows you to that layer
automatically — and NEVER refuse or hedge because a different tab is open. Keep answers tight: lead with the answer, one or two supporting
details, no filler. When a question implies a place ("near Denver"), you know
common US city coordinates — use them. When drive_time_search returns its
great-circle approximation, say estimates assume ~45 mph straight-line driving.
If a layer has no signal (July frost questions), say what the climatology expects
instead of leaving the user empty-handed.

For history, normals, and records, climate_history covers the common shapes
(nearest long-record station: monthly normals, seasonal records, record
latest/earliest event dates). For ANYTHING beyond those shapes, compose the
query yourself with acis_query (full NOAA/RCC climate archive) or nws_query
(live NWS forecasts/alerts/observations) — you are a fully queryable climate
interface, not a fixed menu. If a raw query errors, read the error and fix the
params; ACIS errors are informative. Always aggregate server-side (reduce/
smry/interval/groupby) — raw daily period-of-record dumps get truncated.
Name stations and their distance; station records are point truth.

FORMAT for a small chat panel: lead with the answer in one bold phrase, then a
few short lines or a compact list. No big headers, no horizontal rules, no
emoji walls — this renders in a 380px drawer next to a map.

INTERACTIVITY: whenever your answer references specific storms, tornadoes, or
drought areas that exist in the map layers, ALSO call highlight_on_map so the
map lights up exactly what you're talking about (e.g. Hugo 1989 →
{layer: "hurricanes_majors", where: [{prop:"name",equals:"Hugo"},{prop:"year",equals:1989}]}).
One call per referenced feature. The map switches to that layer and shows only
the highlighted features.

ACIS cheat-sheet (acis_query):
- endpoints: StnMeta (find stations), StnData (one station), MultiStnData
  (bbox/state), GridData (gridded, loc:"lon,lat", grid:1 NRCC or 21 PRISM).
- elements: maxt mint avgt pcpn snow snwd, degree days cdd/hdd/gdd with
  "base" (e.g. {"name":"gdd","base":40}).
- elems entries: {"name":"snow","interval":"dly|mly|yly" or [y,m,d] step array,
  "duration": window length ("dly|mly|yly" units matching interval string, or
  DAYS when interval is an array), "reduce":"max|min|sum|mean" or
  {"reduce":"max","add":"date"}, "smry": same shapes + "smry_only":1,
  "normal":"1" for 1991-2020 normals, "maxmissing":N}.
- dates: "sdate"/"edate", "por" = full period of record.
- StnMeta: {"bbox":[w,s,e,n],"elems":"snow","meta":["name","state","sids","ll","valid_daterange"]}.
- sid = first token of a sids entry.
- VERIFIED custom-season pattern (e.g. snowiest Jul–Jun season on record):
  {"sid":"310301","sdate":"1939-07-01","edate":"2026-06-30","elems":[{"name":"snow",
  "interval":[1,0,0],"duration":365,"reduce":"sum","maxmissing":30}]}
  → one row per season labeled by its start date; sort client-side or add
  "smry":{"reduce":"max","add":"date"},"smry_only":1 for just the record.
  Align sdate to the season start; interval [1,0,0] steps yearly; duration is
  in DAYS here (365).

NWS cheat-sheet (nws_query): GET paths — /points/{lat},{lon} returns the
office/grid + forecast URLs; /gridpoints/{office}/{x},{y}/forecast (7-day) and
/forecast/hourly; /alerts/active?point={lat},{lon}; /stations/{id}/observations/latest.
${LAYER_CATALOG}`;

const TOOLS = [
  {
    name: "get_conditions",
    description: "Value at a point for a layer/date, formatted for humans.",
    input_schema: {
      type: "object",
      properties: {
        lat: { type: "number" },
        lon: { type: "number" },
        layer: { type: "string" },
        date: { type: "string", description: "YYYY-MM-DD; omit for latest" },
      },
      required: ["lat", "lon", "layer"],
    },
  },
  {
    name: "find_peak",
    description: "Best (max or min) cell for a layer within an optional bbox.",
    input_schema: {
      type: "object",
      properties: {
        layer: { type: "string" },
        bbox: {
          type: "array",
          items: { type: "number" },
          description: "[west, south, east, north] degrees; omit for CONUS",
        },
        date: { type: "string" },
        mode: { type: "string", enum: ["max", "min"] },
      },
      required: ["layer"],
    },
  },
  {
    name: "drive_time_search",
    description:
      "Destinations within a drive-time budget meeting a value threshold (great-circle @45mph approximation).",
    input_schema: {
      type: "object",
      properties: {
        origin_lat: { type: "number" },
        origin_lon: { type: "number" },
        layer: { type: "string" },
        max_hours: { type: "number" },
        date: { type: "string" },
        min_value: { type: "number" },
        max_value: { type: "number" },
      },
      required: ["origin_lat", "origin_lon", "layer", "max_hours"],
    },
  },
  {
    name: "get_layer_summary",
    description: "National/regional summary stats and available dates for a layer.",
    input_schema: {
      type: "object",
      properties: { layer: { type: "string" } },
      required: ["layer"],
    },
  },
  {
    name: "climate_history",
    description:
      "Historical station climate data (ACIS/GHCN): 1991-2020 monthly normals, seasonal records, or record latest/earliest event dates (e.g. latest measurable snowfall on record) at the nearest long-record station.",
    input_schema: {
      type: "object",
      properties: {
        lat: { type: "number" },
        lon: { type: "number" },
        element: { type: "string", enum: ["snow", "snwd", "pcpn", "maxt", "mint"] },
        mode: {
          type: "string",
          enum: ["monthly_normals", "seasonal_records", "latest_event", "earliest_event"],
        },
        event_threshold: {
          type: "number",
          description: "qualifying daily amount for *_event modes (default 0.1 in for snow)",
        },
      },
      required: ["lat", "lon", "element", "mode"],
    },
  },
  {
    name: "layer_features",
    description:
      "Query the map's own vector layers (hurricanes_majors/modern/active, tornadoes_violent/strong/weak, drought) by feature properties. where clauses filter on properties like name, year, cat, ef, dm, state. Returns matching feature properties + drops pins.",
    input_schema: {
      type: "object",
      properties: {
        layer: { type: "string" },
        date: { type: "string" },
        where: {
          type: "array",
          items: {
            type: "object",
            properties: {
              prop: { type: "string" },
              equals: { type: ["string", "number"] },
              min: { type: "number" },
              max: { type: "number" },
            },
            required: ["prop"],
          },
        },
        limit: { type: "number" },
      },
      required: ["layer"],
    },
  },
  {
    name: "highlight_on_map",
    description:
      "Highlight specific features on the user's map (switches to the layer and filters to matching features). Call once per storm/tornado/area your answer references.",
    input_schema: {
      type: "object",
      properties: {
        layer: { type: "string" },
        where: {
          type: "array",
          items: {
            type: "object",
            properties: {
              prop: { type: "string" },
              equals: { type: ["string", "number"] },
              min: { type: "number" },
              max: { type: "number" },
            },
            required: ["prop"],
          },
        },
      },
      required: ["layer", "where"],
    },
  },
  {
    name: "acis_query",
    description:
      "Raw NOAA/RCC climate archive query (data.rcc-acis.org). Compose any params the ACIS API supports — elements, server-side reductions, normals, degree days, seasonal groupby, grid or station. See the ACIS cheat-sheet in your instructions. Aggregate server-side; huge responses get truncated.",
    input_schema: {
      type: "object",
      properties: {
        endpoint: { type: "string", enum: ["StnMeta", "StnData", "MultiStnData", "GridData"] },
        params: { type: "object", description: "raw ACIS request params JSON" },
      },
      required: ["endpoint", "params"],
    },
  },
  {
    name: "nws_query",
    description:
      "Raw live National Weather Service API GET (api.weather.gov): forecasts, hourly forecasts, active alerts, station observations. Start with /points/{lat},{lon} to discover forecast URLs.",
    input_schema: {
      type: "object",
      properties: { path: { type: "string", description: "path beginning with / e.g. /points/35.23,-80.84" } },
      required: ["path"],
    },
  },
];

const rateBuckets = new Map<string, { count: number; reset: number }>();

function rateLimited(ip: string): boolean {
  const now = Date.now();
  const bucket = rateBuckets.get(ip);
  if (!bucket || now > bucket.reset) {
    rateBuckets.set(ip, { count: 1, reset: now + RATE_LIMIT.windowMs });
    return false;
  }
  bucket.count++;
  return bucket.count > RATE_LIMIT.max;
}

async function runTool(env: Env, name: string, input: any): Promise<import("./tools/query").ToolOutcome> {
  switch (name) {
    case "get_conditions":
      return getConditions(env, input);
    case "find_peak":
      return findPeak(env, input);
    case "drive_time_search":
      return driveTimeSearch(env, input);
    case "get_layer_summary":
      return getLayerSummary(env, input);
    case "climate_history":
      return climateHistory(env, input);
    case "layer_features":
      return layerFeatures(env, input);
    case "highlight_on_map":
      return {
        result: { ok: true, highlighted: input.layer },
        highlight: { layer: input.layer, where: input.where ?? [] },
      };
    case "acis_query":
      return acisQuery(env, input);
    case "nws_query":
      return nwsQuery(env, input);
    default:
      return { result: { error: `unknown tool ${name}` } };
  }
}

async function chat(request: Request, env: Env): Promise<Response> {
  if (!env.ANTHROPIC_API_KEY) {
    return Response.json({ error: "assistant not configured (missing API key)" }, { status: 503 });
  }
  const body = (await request.json()) as {
    messages: { role: "user" | "assistant"; content: string }[];
    context?: {
      layer?: string;
      layer_label?: string;
      date?: string | null;
      filters?: string | null;
      view?: { lat: number; lng: number; zoom?: number } | null;
      click?: { lat: number; lng: number; feature?: Record<string, unknown> | null } | null;
    };
  };
  if (!Array.isArray(body.messages) || body.messages.length === 0 || body.messages.length > 40) {
    return Response.json({ error: "bad messages" }, { status: 400 });
  }

  const c = body.context;
  const ctxLines: string[] = [];
  if (c?.layer) {
    ctxLines.push(
      `Active layer: "${c.layer_label ?? c.layer}" (id: ${c.layer})${c.date ? ` for ${c.date}` : ""}.`
    );
  }
  if (c?.filters) ctxLines.push(`Active filters: ${c.filters}.`);
  if (c?.view) {
    ctxLines.push(
      `Map is centered near ${c.view.lat.toFixed(2)}, ${c.view.lng.toFixed(2)}${c.view.zoom ? ` at zoom ${c.view.zoom.toFixed(1)}` : ""}.`
    );
  }
  if (c?.click) {
    ctxLines.push(
      c.click.feature
        ? `The user last clicked a map feature at ${c.click.lat.toFixed(3)}, ${c.click.lng.toFixed(3)} with properties: ${JSON.stringify(c.click.feature).slice(0, 500)}. Questions like "this storm" / "this one" / "here" refer to it.`
        : `The user last clicked the map at ${c.click.lat.toFixed(3)}, ${c.click.lng.toFixed(3)}. Questions like "here" refer to that point.`
    );
  }
  const contextNote = ctxLines.length
    ? "\n\nWHAT THE USER IS LOOKING AT RIGHT NOW:\n" + ctxLines.join("\n")
    : "";

  const messages: any[] = body.messages.map((m) => ({ role: m.role, content: m.content }));
  const pins: Pin[] = [];
  const highlights: Highlight[] = [];
  let lastLayer: string | undefined = body.context?.layer;
  let lastDate: string | undefined;

  for (let i = 0; i <= MAX_TOOL_ITERATIONS; i++) {
    const resp = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "x-api-key": env.ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
      },
      body: JSON.stringify({
        model: MODEL,
        max_tokens: 1024,
        system: SYSTEM_PROMPT + contextNote,
        tools: TOOLS,
        messages,
      }),
    });
    if (!resp.ok) {
      const detail = await resp.text();
      return Response.json(
        { error: `model call failed (${resp.status})`, detail: detail.slice(0, 300) },
        { status: 502 }
      );
    }
    const data = (await resp.json()) as any;

    if (data.stop_reason === "tool_use" && i < MAX_TOOL_ITERATIONS) {
      messages.push({ role: "assistant", content: data.content });
      const results: any[] = [];
      for (const block of data.content) {
        if (block.type !== "tool_use") continue;
        try {
          const outcome = await runTool(env, block.name, block.input);
          if (outcome.pins) pins.push(...outcome.pins);
          if ((outcome as any).highlight) highlights.push((outcome as any).highlight);
          const r = outcome.result as any;
          if (r?.layer) lastLayer = r.layer;
          if (r?.date) lastDate = r.date;
          results.push({
            type: "tool_result",
            tool_use_id: block.id,
            content: JSON.stringify(outcome.result),
          });
        } catch (err) {
          results.push({
            type: "tool_result",
            tool_use_id: block.id,
            content: JSON.stringify({ error: String(err) }),
            is_error: true,
          });
        }
      }
      messages.push({ role: "user", content: results });
      continue;
    }

    const text = (data.content ?? [])
      .filter((b: any) => b.type === "text")
      .map((b: any) => b.text)
      .join("\n")
      .trim();
    return Response.json({
      answer_markdown: text || "(no answer)",
      pins: pins.slice(0, 12),
      highlights: highlights.slice(0, 10),
      layer: lastLayer ?? null,
      date: lastDate ?? null,
    });
  }
  return Response.json({ error: "tool loop exceeded" }, { status: 502 });
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/api/health") {
      return Response.json({ ok: true, service: "seasonmap-worker", phase: 2 });
    }
    if (url.pathname === "/api/query" && request.method === "GET") {
      const layer = url.searchParams.get("layer") ?? "frost_date";
      const lat = Number(url.searchParams.get("lat"));
      const lon = Number(url.searchParams.get("lon"));
      const date = url.searchParams.get("date") ?? undefined;
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
        return Response.json({ error: "lat/lon required" }, { status: 400 });
      }
      try {
        const outcome = await getConditions(env, { lat, lon, layer, date });
        return Response.json(outcome.result);
      } catch (err) {
        return Response.json({ error: String(err) }, { status: 502 });
      }
    }
    if (url.pathname === "/api/chat" && request.method === "POST") {
      const ip = request.headers.get("CF-Connecting-IP") ?? "local";
      if (rateLimited(ip)) {
        return Response.json({ error: "rate limited — try again in a few minutes" }, { status: 429 });
      }
      try {
        return await chat(request, env);
      } catch (err) {
        return Response.json({ error: String(err) }, { status: 500 });
      }
    }
    return new Response("Not found", { status: 404 });
  },
};
