import { useState } from "react";
import { APP } from "../config";
import { OVERLAY_DEFS, type LayerDef } from "../types";

interface Props {
  layers: LayerDef[];
  /** base-layer definition id (variant group) */
  activeBase: string;
  /** concrete layer/variant id currently shown */
  activeVariant: string;
  onSelectBase: (defId: string) => void;
  onSelectVariant: (variantId: string) => void;
  overlayIds: string[];
  onToggleOverlay: (id: string) => void;
}

const GROUP_ORDER = ["Now", "Models", "Storms", "Seasons", "Water"];

/**
 * The map owns the middle; navigation lives on the border. One base fill at
 * a time (radio rows, variants inline under the active row) + a stackable
 * overlay checklist — fills fight, sparse stacks.
 */
export function LeftNav({
  layers,
  activeBase,
  activeVariant,
  onSelectBase,
  onSelectVariant,
  overlayIds,
  onToggleOverlay,
}: Props) {
  const [open, setOpen] = useState<boolean>(
    () => typeof window === "undefined" || window.innerWidth > 860
  );
  const groups = GROUP_ORDER.filter((g) => layers.some((l) => l.group === g));

  if (!open) {
    return (
      <button className="leftnav-fab" onClick={() => setOpen(true)} aria-label="Open layers">
        ☰ Layers
        {overlayIds.length > 0 && <span className="leftnav-fab-badge">{overlayIds.length}</span>}
      </button>
    );
  }

  return (
    <aside className="leftnav" aria-label="Layers">
      <div className="leftnav-head">
        <div className="brand">
          <span className="brand-name">{APP.name}</span>
          <span className="brand-tag">{APP.tagline}</span>
        </div>
        <button className="leftnav-collapse" onClick={() => setOpen(false)} aria-label="Collapse">
          ‹
        </button>
      </div>

      <div className="leftnav-scroll">
        <div className="leftnav-section">
          <div className="leftnav-title">
            Overlays <span className="leftnav-hint">stack any</span>
          </div>
          {OVERLAY_DEFS.map((o) => {
            const on = overlayIds.includes(o.id);
            return (
              <label key={o.id} className={on ? "ovl-row ovl-row-on" : "ovl-row"}>
                <input
                  type="checkbox"
                  checked={on}
                  onChange={() => onToggleOverlay(o.id)}
                />
                <span className="ovl-swatch" style={{ background: o.swatch }} />
                {o.label}
              </label>
            );
          })}
        </div>

        {groups.map((g) => (
          <div key={g} className="leftnav-section">
            <div className="leftnav-title">{g}</div>
            {layers
              .filter((l) => l.group === g)
              .map((l) => (
                <div key={l.id}>
                  <button
                    className={l.id === activeBase ? "nav-row nav-row-active" : "nav-row"}
                    disabled={!l.available}
                    title={l.available ? l.label : `${l.label} — coming soon`}
                    onClick={() => onSelectBase(l.id)}
                  >
                    {l.label}
                  </button>
                  {l.id === activeBase && l.variants && (
                    <div className="nav-variants" role="tablist" aria-label="Sub-layer">
                      {l.variants.map((v) => (
                        <button
                          key={v.id}
                          role="tab"
                          aria-selected={v.id === activeVariant}
                          className={v.id === activeVariant ? "chip chip-active" : "chip"}
                          onClick={() => onSelectVariant(v.id)}
                        >
                          {v.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              ))}
          </div>
        ))}
      </div>
    </aside>
  );
}
