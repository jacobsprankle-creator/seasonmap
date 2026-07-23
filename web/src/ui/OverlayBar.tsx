import { OVERLAY_DEFS } from "../types";

interface Props {
  active: string[];
  onToggle: (id: string) => void;
}

/** Stackable overlay pills — radar / alerts / storms over any base layer. */
export function OverlayBar({ active, onToggle }: Props) {
  return (
    <div className="overlay-bar" role="group" aria-label="Map overlays">
      <span className="overlay-bar-label">overlay</span>
      {OVERLAY_DEFS.map((o) => {
        const on = active.includes(o.id);
        return (
          <button
            key={o.id}
            className={on ? "chip chip-active" : "chip"}
            aria-pressed={on}
            onClick={() => onToggle(o.id)}
          >
            <span className={on ? "ovl-dot ovl-dot-on" : "ovl-dot"} />
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
