/**
 * Raw API passthrough tools — the "fully queryable" layer.
 *
 * Instead of pre-baked query shapes, the model composes requests itself:
 *   acis_query — POST to data.rcc-acis.org (RCC/NOAA station + grid climate
 *                data: any element, any server-side reduction, normals,
 *                records, degree days, seasonal groupings, period of record)
 *   nws_query  — GET api.weather.gov (live forecasts, alerts, observations)
 *
 * Guardrails: endpoint allowlists, timeouts, and hard response truncation so
 * an unaggregated period-of-record dump can't blow the context — the tool
 * tells the model to refine with server-side reductions instead.
 */
import type { Env, ToolOutcome } from "./query";

const ACIS_ENDPOINTS = new Set(["StnMeta", "StnData", "MultiStnData", "GridData", "General/state"]);
const NWS_PATH_PREFIXES = [
  "/points/",
  "/gridpoints/",
  "/alerts",
  "/stations",
  "/zones",
  "/products",
  "/offices/",
  "/glossary",
];
const MAX_RESULT_CHARS = 14000;
const USER_AGENT = "seasonmap-assistant/0.1 (seasonmap.example)";

function capped(obj: unknown): unknown {
  const s = JSON.stringify(obj);
  if (s.length <= MAX_RESULT_CHARS) return obj;
  return {
    truncated: true,
    total_chars: s.length,
    note:
      "Response too large — refine with server-side aggregation (reduce/smry/interval/groupby) or a narrower date range. Preview follows.",
    preview: s.slice(0, MAX_RESULT_CHARS),
  };
}

export async function acisQuery(
  _env: Env,
  args: { endpoint: string; params: Record<string, unknown> }
): Promise<ToolOutcome> {
  if (!ACIS_ENDPOINTS.has(args.endpoint)) {
    return {
      result: { error: `endpoint must be one of: ${[...ACIS_ENDPOINTS].join(", ")}` },
    };
  }
  const resp = await fetch(`https://data.rcc-acis.org/${args.endpoint}`, {
    method: "POST",
    headers: { "content-type": "application/json", "user-agent": USER_AGENT },
    body: JSON.stringify(args.params ?? {}),
    signal: AbortSignal.timeout(45000),
  });
  if (!resp.ok) {
    return { result: { error: `ACIS HTTP ${resp.status}`, body: (await resp.text()).slice(0, 400) } };
  }
  const data = await resp.json();
  return { result: capped(data) };
}

export async function nwsQuery(_env: Env, args: { path: string }): Promise<ToolOutcome> {
  const path = args.path.startsWith("/") ? args.path : `/${args.path}`;
  if (!NWS_PATH_PREFIXES.some((p) => path.startsWith(p))) {
    return {
      result: { error: `path must start with one of: ${NWS_PATH_PREFIXES.join(" ")}` },
    };
  }
  const resp = await fetch(`https://api.weather.gov${path}`, {
    headers: { "user-agent": USER_AGENT, accept: "application/geo+json" },
    signal: AbortSignal.timeout(30000),
  });
  if (!resp.ok) {
    return { result: { error: `NWS HTTP ${resp.status}`, body: (await resp.text()).slice(0, 400) } };
  }
  const data = await resp.json();
  return { result: capped(data) };
}
