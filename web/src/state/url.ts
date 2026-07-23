/** Shareable URLs: /?layer=frost&date=2026-10-15&z=7&ll=35.2,-80.9 */

export interface UrlState {
  layer?: string;
  date?: string;
  zoom?: number;
  center?: [number, number]; // [lat, lon]
  overlays?: string[];
}

export function readUrlState(): UrlState {
  const p = new URLSearchParams(window.location.search);
  const state: UrlState = {};
  const layer = p.get("layer");
  if (layer) state.layer = layer;
  const date = p.get("date");
  if (date && /^\d{4}-\d{2}-\d{2}$/.test(date)) state.date = date;
  const z = p.get("z");
  if (z && !Number.isNaN(Number(z))) state.zoom = Number(z);
  const ov = p.get("ov");
  if (ov) state.overlays = ov.split(",").filter(Boolean);
  const ll = p.get("ll");
  if (ll) {
    const [lat, lon] = ll.split(",").map(Number);
    if (Number.isFinite(lat) && Number.isFinite(lon)) state.center = [lat, lon];
  }
  return state;
}

export function writeUrlState(s: Required<Pick<UrlState, "layer" | "date">> & UrlState): void {
  const p = new URLSearchParams();
  p.set("layer", s.layer);
  p.set("date", s.date);
  if (s.zoom !== undefined) p.set("z", s.zoom.toFixed(1));
  if (s.center) p.set("ll", `${s.center[0].toFixed(4)},${s.center[1].toFixed(4)}`);
  if (s.overlays?.length) p.set("ov", s.overlays.join(","));
  window.history.replaceState(null, "", `?${p.toString()}`);
}
