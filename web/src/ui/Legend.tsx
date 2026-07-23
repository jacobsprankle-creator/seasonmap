import type { LayerMeta } from "../types";

export function Legend({ legend }: { legend: LayerMeta["legend"] }) {
  if (legend?.type === "categorical" && legend.items?.length) {
    return (
      <div className="legend legend-cat" aria-label="Legend">
        {legend.items.map((i) => (
          <span key={String(i.value)} className="legend-item">
            <span className="legend-swatch" style={{ background: i.color }} />
            {i.label}
          </span>
        ))}
      </div>
    );
  }
  if (!legend?.stops?.length) return null;
  const { vmin = 0, vmax = 1, stops, units } = legend;
  const pctOf = (v: number) =>
    Math.max(0, Math.min(100, ((v - vmin) / (vmax - vmin)) * 100));
  const gradient = legend.stepped
    ? // Hard bands: each color holds from its stop to the next stop.
      stops
        .map((s, i) => {
          const from = pctOf(s.value).toFixed(1);
          const to = pctOf(i + 1 < stops.length ? stops[i + 1].value : vmax).toFixed(1);
          return `${s.color} ${from}%, ${s.color} ${to}%`;
        })
        .join(", ")
    : stops.map((s) => `${s.color} ${pctOf(s.value).toFixed(1)}%`).join(", ");
  return (
    <div className="legend" aria-label="Legend">
      <div className="legend-bar" style={{ background: `linear-gradient(90deg, ${gradient})` }} />
      {legend.labels?.length ? (
        <div className="legend-labels">
          {legend.labels.map((l) => (
            <span key={l.value}>{l.label}</span>
          ))}
        </div>
      ) : (
        <div className="legend-labels">
          <span>
            {vmin.toLocaleString()} {units}
          </span>
          <span>
            {vmax.toLocaleString()} {units}
          </span>
        </div>
      )}
    </div>
  );
}
