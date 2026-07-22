/**
 * Click-anywhere NWS point info. api.weather.gov is CORS-open, so the browser
 * talks to it directly: /points/{lat},{lon} carries BOTH the nearest place
 * name (relativeLocation) and the forecast URL; we surface the first forecast
 * period. Cached on a ~0.1° snap so repeat clicks in an area are instant.
 */
export interface ForecastPeriod {
  name: string; // "This Afternoon", "Tonight", …
  short: string;
  temp: string; // "91°F"
}

export interface PointInfo {
  place: string | null; // "Huntersville, NC"
  forecast: string | null; // one-line summary (first period)
  periods: ForecastPeriod[]; // first three periods for the mini card
}

const cache = new Map<string, Promise<PointInfo>>();

async function lookup(lat: number, lng: number): Promise<PointInfo> {
  const none: PointInfo = { place: null, forecast: null, periods: [] };
  const p = await fetch(`https://api.weather.gov/points/${lat.toFixed(4)},${lng.toFixed(4)}`, {
    headers: { accept: "application/geo+json" },
  });
  if (!p.ok) return none;
  const pj = (await p.json()) as any;
  const rel = pj.properties?.relativeLocation?.properties;
  const place = rel?.city ? `${rel.city}, ${rel.state}` : null;
  const url = pj.properties?.forecast;
  if (!url) return { ...none, place };
  const f = await fetch(url, { headers: { accept: "application/geo+json" } });
  if (!f.ok) return { ...none, place };
  const raw = (await f.json())?.properties?.periods ?? [];
  const periods: ForecastPeriod[] = raw.slice(0, 3).map((per: any) => ({
    name: per.name,
    short: per.shortForecast,
    temp: `${per.temperature}°${per.temperatureUnit}`,
  }));
  const first = raw[0];
  return {
    place,
    forecast: first
      ? `${first.name}: ${first.shortForecast}, ${first.temperature}°${first.temperatureUnit}, wind ${first.windDirection} ${first.windSpeed}`
      : null,
    periods,
  };
}

export function nwsPointInfo(lat: number, lng: number): Promise<PointInfo> {
  const key = `${lat.toFixed(1)},${lng.toFixed(1)}`;
  const hit = cache.get(key);
  if (hit) return hit;
  const p = lookup(lat, lng).catch(
    (): PointInfo => ({ place: null, forecast: null, periods: [] })
  );
  cache.set(key, p);
  if (cache.size > 60) cache.delete(cache.keys().next().value as string);
  return p;
}
