import type { LayerDef } from "../types";

interface Props {
  layers: LayerDef[];
  active: string; // active base-layer id
  onChange: (id: string) => void;
}

const GROUP_ORDER = ["Now", "Models", "Storms", "Seasons", "Water"];

/** Two-tier nav: category tabs, then the layers of the active category. */
export function LayerSwitcher({ layers, active, onChange }: Props) {
  const activeDef = layers.find((l) => l.id === active);
  const activeGroup = activeDef?.group ?? GROUP_ORDER[0];
  const groups = GROUP_ORDER.filter((g) => layers.some((l) => l.group === g));
  const inGroup = layers.filter((l) => l.group === activeGroup);

  return (
    <div className="nav">
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
      <nav className="layer-switcher" aria-label="Map layers">
        {inGroup.map((l) => (
          <button
            key={l.id}
            className={l.id === active ? "chip chip-active" : "chip"}
            disabled={!l.available}
            title={l.available ? l.label : `${l.label} — coming in Phase ${l.phase}`}
            onClick={() => onChange(l.id)}
          >
            {l.label}
          </button>
        ))}
      </nav>
    </div>
  );
}
