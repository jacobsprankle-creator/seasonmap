import type { ReactNode } from "react";
import type { LayerDef } from "../types";

interface Props {
  layers: LayerDef[];
  active: string; // active base-layer id
  onChange: (id: string) => void;
  /** Brand block, rendered leftmost on row 1 — keeps header geometry stable. */
  brand?: ReactNode;
  /** Variant chips for the active layer, docked at the end of row 2. */
  variantSlot?: ReactNode;
}

const GROUP_ORDER = ["Now", "Models", "Storms", "Seasons", "Water"];

/**
 * Two fixed header rows — identical geometry on every tab:
 *   row 1: brand · category tabs
 *   row 2: layers of the active category · variant dock (when present)
 */
export function LayerSwitcher({ layers, active, onChange, brand, variantSlot }: Props) {
  const activeDef = layers.find((l) => l.id === active);
  const activeGroup = activeDef?.group ?? GROUP_ORDER[0];
  const groups = GROUP_ORDER.filter((g) => layers.some((l) => l.group === g));
  const inGroup = layers.filter((l) => l.group === activeGroup);

  return (
    <>
      <div className="topbar-row">
        {brand}
        <nav className="nav-groups" aria-label="Layer categories">
          {groups.map((g) => (
            <button
              key={g}
              className={g === activeGroup ? "chip chip-active" : "chip"}
              onClick={() => {
                const first = layers.find((l) => l.group === g && l.available);
                if (first) onChange(first.id);
              }}
            >
              {g}
            </button>
          ))}
        </nav>
      </div>
      <div className="topbar-row">
        <nav className="layer-switcher" aria-label="Map layers">
          {inGroup.map((l) => (
            <button
              key={l.id}
              className={l.id === active ? "chip chip-active" : "chip"}
              disabled={!l.available}
              title={l.available ? l.label : `${l.label} — coming soon`}
              onClick={() => onChange(l.id)}
            >
              {l.label}
            </button>
          ))}
        </nav>
        {variantSlot}
      </div>
    </>
  );
}
