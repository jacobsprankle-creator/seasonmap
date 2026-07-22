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
  const gradient = stops
    .map((s) => {
      const pct = ((s.value - vmin) / (vmax - vmin)) * 100;
      return `${s.color} ${Math.max(0, Math.min(100, pct)).toFixed(1)}%`;
    })
    .join(", ");
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
