/**
 * Click-to-value: sample the layer's published COG at a lon/lat.
 *
 * COGs are small (0.1–3.5 MB deflate) so v1 fetches the whole file once per
 * layer/date and caches the decoded GeoTIFF (LRU of 12 — a slider sweep stays
 * warm). The canonical-grid affine transform comes from meta.grid.
 */
import { fromArrayBuffer, type GeoTIFF } from "geotiff";
import type { LayerMeta } from "../types";

const cache = new Map<string, Promise<GeoTIFF>>();

function lru(key: string, make: () => Promise<GeoTIFF>): Promise<GeoTIFF> {
  const hit = cache.get(key);
  if (hit) return hit;
  const p = make();
  cache.set(key, p);
  if (cache.size > 12) {
    const oldest = cache.keys().next().value;
    if (oldest) cache.delete(oldest);
  }
  return p;
}

export async function sampleValue(
  cogUrl: string,
  meta: LayerMeta,
  lng: number,
  lat: number
): Promise<number | null> {
  const g = meta.grid;
  if (!g) return null;
  const [a, , c, , e, f] = g.transform;
  const col = Math.floor((lng - c) / a);
  const row = Math.floor((lat - f) / e);
  if (col < 0 || col >= g.width || row < 0 || row >= g.height) return null;

  const tiff = await lru(cogUrl, async () => {
    const resp = await fetch(cogUrl);
    if (!resp.ok) throw new Error(`COG HTTP ${resp.status}`);
    return fromArrayBuffer(await resp.arrayBuffer());
  });
  const image = await tiff.getImage(); // full-res IFD is first
  const rasters = (await image.readRasters({
    window: [col, row, col + 1, row + 1],
  })) as unknown as ArrayLike<number> | ArrayLike<number>[];
  const band0 = (Array.isArray(rasters) ? rasters[0] : rasters) as ArrayLike<number>;
  const value = Number(band0[0]);
  return value === g.nodata || !Number.isFinite(value) ? null : value;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function formatValue(value: number | null, meta: LayerMeta): string {
  if (value === null) {
    return meta.value_format === "doy_date" ? "rarely freezes" : "no data";
  }
  switch (meta.value_format) {
    case "probability":
      return `${Math.round(value * 100)}% chance a freeze has occurred`;
    case "doy_date": {
      const d = new Date(Date.UTC(2026, 0, 1) + (Math.round(value) - 1) * 86400000);
      return `first freeze ~${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}`;
    }
    case "snow_state":
      return value >= 2 ? "fresh snow expected" : value >= 1 ? "snow on the ground" : "no snow";
    case "foliage_stage": {
      const stages = ["green", "turning", "patchy color", "near peak", "PEAK color", "past peak"];
      return stages[Math.min(5, Math.max(0, Math.round(value)))];
    }
    default:
      return `${Math.round(value).toLocaleString()} ${meta.units}`;
  }
}
