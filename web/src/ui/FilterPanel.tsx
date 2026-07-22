/**
 * Client-side vector filters: year range + category/EF toggles. Builds a
 * MapLibre expression fragment — filtering happens on the GPU against the
 * already-loaded GeoJSON, no refetch.
 */
import { useEffect, useState } from "react";
import type { LayerMeta } from "../types";

export type FilterExpr = unknown[];

interface Props {
  meta: LayerMeta;
  onChange: (fragments: FilterExpr[] | null, description: string | null) => void;
}

export function FilterPanel({ meta, onChange }: Props) {
  const spec = (meta as any).filters as
    | { year_range: [number, number]; category_property: string }
    | undefined;
  const items = meta.legend.items ?? [];
  const isSeason = meta.layer.startsWith("hurricanes");
  const word = isSeason ? "season" : "year";
  const [year, setYear] = useState<number | null>(null); // null = all
  const [off, setOff] = useState<Set<string>>(new Set());

  useEffect(() => {
    setYear(null);
    setOff(new Set());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meta.layer]);

  useEffect(() => {
    if (!spec) {
      onChange(null, null);
      return;
    }
    const parts: FilterExpr[] = [];
    const words: string[] = [];
    if (year !== null) {
      parts.push(["==", ["to-number", ["get", "year"]], year]);
      words.push(`${word} ${year}`);
    }
    if (off.size > 0 && off.size < items.length) {
      const keep = items.map((i) => String(i.value)).filter((v) => !off.has(v));
      parts.push(["in", ["get", spec.category_property], ["literal", keep]]);
      words.push(`hiding ${[...off].join(", ")}`);
    }
    onChange(parts.length ? parts : null, words.length ? words.join("; ") : null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, off, meta.layer]);

  if (!spec) return null;
  const [rMin, rMax] = spec.year_range;

  return (
    <div className="filter-panel">
      <button
        className={year === null ? "chip chip-active" : "chip"}
        onClick={() => setYear(null)}
      >
        All {word}s
      </button>
      <input
        type="range"
        min={rMin}
        max={rMax}
        step={1}
        value={year ?? rMax}
        onChange={(e) => setYear(Number(e.target.value))}
        aria-label={`Filter by ${word}`}
        className="year-slider"
      />
      <output className="year-out">{year === null ? "—" : `${year} ${word}`}</output>
      <span className="filter-sep" />
      {items.map((i) => {
        const v = String(i.value);
        const disabled = off.has(v);
        return (
          <button
            key={v}
            className="filter-chip"
            style={{
              borderColor: i.color,
              background: disabled ? "transparent" : i.color,
              color: disabled ? "inherit" : "#fff",
              opacity: disabled ? 0.5 : 1,
            }}
            onClick={() =>
              setOff((prev) => {
                const next = new Set(prev);
                if (next.has(v)) next.delete(v);
                else next.add(v);
                return next;
              })
            }
            title={disabled ? `Show ${i.label}` : `Hide ${i.label}`}
          >
            {i.label}
          </button>
        );
      })}
    </div>
  );
}
