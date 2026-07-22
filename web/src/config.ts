/**
 * Branding + environment in one place so it stays easily swappable
 * (placeholder name per the build plan).
 */
export const APP = {
  name: "seasonmap",
  tagline: "the seasons, mapped daily",
  accent: "#2e7d32",
};

/** Base URL for published pipeline output (R2 custom domain in prod). */
export const DATA_BASE: string =
  (import.meta.env.VITE_DATA_BASE as string | undefined) ?? "/data";

/** Chat API endpoint (Phase 2 — Cloudflare Worker). */
export const CHAT_API: string =
  (import.meta.env.VITE_CHAT_API as string | undefined) ?? "/api/chat";

export const CONUS_BOUNDS: [[number, number], [number, number]] = [
  [-125.0208333, 24.0625],
  [-66.4791667, 49.9375],
];
