/**
 * Historical climate data via ACIS (data.rcc-acis.org) — the Regional Climate
 * Centers' free, keyless API over GHCN station records. Gives the assistant
 * real answers for "what's normal / what's the record / when's the latest on
 * record" questions the map layers don't carry.
 *
 * Ported from the nws-climate-records skill (Python) — same station
 * selection, value parsing (T/M/A flags), and season bucketing.
 */
import type { Env, Pin, ToolOutcome } from "./query";
import { haversineMiles } from "./query";

const ACIS = "https://data.rcc-acis.org";

const ELEMENTS: Record<string, { units: string; seasonStart: string; agg: "sum" | "max" | "min" }> = {
  snow: { units: "in", seasonStart: "07-01", agg: "sum" },
  snwd: { units: "in", seasonStart: "07-01", agg: "max" },
  pcpn: { units: "in", seasonStart: "01-01", agg: "sum" },
  maxt: { units: "°F", seasonStart: "01-01", agg: "max" },
  mint: { units: "°F", seasonStart: "01-01", agg: "min" },
};

async function acis(endpoint: string, params: unknown): Promise<any> {
  const resp = await fetch(`${ACIS}/${endpoint}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(params),
  });
  if (!resp.ok) throw new Error(`ACIS ${endpoint}: HTTP ${resp.status}`);
  const data = (await resp.json()) as any;
  if (data.error) throw new Error(`ACIS ${endpoint}: ${data.error}`);
  return data;
}

interface Station {
  name: string;
  state: string;
  sid: string;
  ll: [number, number]; // [lon, lat]
  distance_miles: number;
  valid_daterange: [string, string];
}

async function nearestStation(lat: number, lon: number, element: string): Promise<Station> {
  const dlat = 0.5;
  const dlon = 0.5 / Math.max(0.1, Math.cos((lat * Math.PI) / 180));
  const data = await acis("StnMeta", {
    bbox: [lon - dlon, lat - dlat, lon + dlon, lat + dlat],
    elems: element,
    meta: ["name", "state", "sids", "ll", "valid_daterange"],
  });
  const candidates: Station[] = [];
  for (const m of data.meta ?? []) {
    if (!m.ll || !m.sids?.length || !m.valid_daterange?.[0]?.[0]) continue;
    const [start, end] = m.valid_daterange[0];
    const years = (new Date(end).getTime() - new Date(start).getTime()) / 3.156e10;
    if (years < 20) continue; // demand a usable period of record
    candidates.push({
      name: m.name,
      state: m.state,
      sid: String(m.sids[0]).split(" ")[0],
      ll: m.ll,
      distance_miles: +haversineMiles(lat, lon, m.ll[1], m.ll[0]).toFixed(1),
      valid_daterange: [start, end],
    });
  }
  if (!candidates.length) throw new Error("no long-record station near that point");
  // Nearest, with a nudge toward longer records among the close ones.
  candidates.sort((a, b) => a.distance_miles - b.distance_miles);
  const close = candidates.slice(0, 5);
  close.sort(
    (a, b) =>
      new Date(a.valid_daterange[0]).getTime() - new Date(b.valid_daterange[0]).getTime()
  );
  return close[0];
}

/** ACIS daily values: numbers, "T" (trace), "M" (missing), "12.0A" (accum). */
function parseValue(v: unknown): { value: number | null; trace: boolean } {
  if (v === null || v === undefined) return { value: null, trace: false };
  const s = String(v).trim();
  if (s === "M" || s === "") return { value: null, trace: false };
  if (s === "T") return { value: 0.0, trace: true };
  const num = parseFloat(s.replace(/[AS]$/i, ""));
  return Number.isFinite(num) ? { value: num, trace: false } : { value: null, trace: false };
}

function seasonKey(date: string, startMmdd: string): string {
  const y = +date.slice(0, 4);
  const md = date.slice(5, 10);
  const startYear = md >= startMmdd ? y : y - 1;
  return startMmdd === "01-01" ? String(startYear) : `${startYear}-${String(startYear + 1).slice(2)}`;
}

const dailyCache = new Map<string, [string, unknown][]>();

async function fetchDaily(sid: string, element: string): Promise<[string, unknown][]> {
  const key = `${sid}:${element}`;
  const hit = dailyCache.get(key);
  if (hit) return hit;
  const data = await acis("StnData", {
    sid,
    sdate: "por",
    edate: "por",
    elems: [{ name: element }],
    meta: [],
  });
  const rows = (data.data ?? []) as [string, unknown][];
  if (dailyCache.size > 6) dailyCache.delete(dailyCache.keys().next().value as string);
  dailyCache.set(key, rows);
  return rows;
}

export async function climateHistory(
  _env: Env,
  args: {
    lat: number;
    lon: number;
    element: string;
    mode: "monthly_normals" | "seasonal_records" | "latest_event" | "earliest_event";
    event_threshold?: number;
  }
): Promise<ToolOutcome> {
  const element = args.element in ELEMENTS ? args.element : "snow";
  const spec = ELEMENTS[element];
  const station = await nearestStation(args.lat, args.lon, element);
  const pin: Pin = {
    lat: station.ll[1],
    lon: station.ll[0],
    label: `${station.name} (${station.distance_miles} mi away)`,
  };
  const base = {
    station: station.name,
    state: station.state,
    station_distance_miles: station.distance_miles,
    period_of_record: station.valid_daterange,
    element,
    units: spec.units,
  };

  if (args.mode === "monthly_normals") {
    const data = await acis("StnData", {
      sid: station.sid,
      sdate: "1991-01",
      edate: "2020-12",
      elems: [{ name: element, interval: "mly", duration: "mly", reduce: spec.agg }],
      meta: [],
    });
    const byMonth: Record<string, { sum: number; n: number }> = {};
    for (const [date, val] of (data.data ?? []) as [string, unknown][]) {
      const { value } = parseValue(val);
      if (value === null) continue;
      const m = date.slice(5, 7);
      byMonth[m] = byMonth[m] ?? { sum: 0, n: 0 };
      byMonth[m].sum += value;
      byMonth[m].n++;
    }
    const normals: Record<string, number> = {};
    for (const [m, { sum, n }] of Object.entries(byMonth)) {
      normals[m] = +(sum / n).toFixed(element.endsWith("t") ? 1 : 2);
    }
    return {
      result: { ...base, mode: args.mode, normals_1991_2020_by_month: normals },
      pins: [pin],
    };
  }

  // Remaining modes need the daily period-of-record series.
  const rows = await fetchDaily(station.sid, element);
  const seasons = new Map<string, { total: number; peakVal: number; peakDate: string; first: string | null; last: string | null; obs: number }>();
  const threshold = args.event_threshold ?? (element === "snow" ? 0.1 : element === "pcpn" ? 0.01 : 0);

  for (const [date, val] of rows) {
    const { value } = parseValue(val);
    if (value === null) continue;
    const key = seasonKey(date, spec.seasonStart);
    let s = seasons.get(key);
    if (!s) {
      s = { total: 0, peakVal: -Infinity, peakDate: "", first: null, last: null, obs: 0 };
      seasons.set(key, s);
    }
    s.obs++;
    s.total += value;
    if (value > s.peakVal) {
      s.peakVal = value;
      s.peakDate = date;
    }
    if (value >= threshold && threshold > 0) {
      if (!s.first) s.first = date;
      s.last = date;
    }
  }
  // Drop threadbare seasons (station gaps).
  for (const [k, s] of seasons) if (s.obs < 120) seasons.delete(k);

  if (args.mode === "seasonal_records") {
    const list = [...seasons.entries()];
    const byTotal = [...list].sort((a, b) => b[1].total - a[1].total);
    const byPeak = [...list].sort((a, b) => b[1].peakVal - a[1].peakVal);
    return {
      result: {
        ...base,
        mode: args.mode,
        seasons_on_record: list.length,
        season_definition: `starts ${spec.seasonStart}`,
        top_seasons_by_total: byTotal.slice(0, 5).map(([k, s]) => ({
          season: k,
          total: +s.total.toFixed(1),
        })),
        record_single_day: byPeak.length
          ? { value: +byPeak[0][1].peakVal.toFixed(1), date: byPeak[0][1].peakDate }
          : null,
      },
      pins: [pin],
    };
  }

  // latest_event / earliest_event: distribution of first/last qualifying dates.
  const marks: { season: string; date: string }[] = [];
  for (const [k, s] of seasons) {
    const d = args.mode === "latest_event" ? s.last : s.first;
    if (d) marks.push({ season: k, date: d });
  }
  if (!marks.length) {
    return { result: { ...base, mode: args.mode, found: false }, pins: [pin] };
  }
  const dayOfSeason = (date: string) => {
    const key = seasonKey(date, spec.seasonStart);
    const startYear = +key.slice(0, 4);
    const start = new Date(`${startYear}-${spec.seasonStart}T00:00:00Z`);
    return (new Date(`${date}T00:00:00Z`).getTime() - start.getTime()) / 86400000;
  };
  marks.sort((a, b) =>
    args.mode === "latest_event"
      ? dayOfSeason(b.date) - dayOfSeason(a.date)
      : dayOfSeason(a.date) - dayOfSeason(b.date)
  );
  return {
    result: {
      ...base,
      mode: args.mode,
      event_threshold: threshold,
      seasons_with_event: marks.length,
      record_and_runners_up: marks.slice(0, 5),
      typical_mmdd: marks[Math.floor(marks.length / 2)]?.date.slice(5),
    },
    pins: [pin],
  };
}
