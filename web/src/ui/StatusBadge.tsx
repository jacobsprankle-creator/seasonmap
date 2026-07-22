import type { LayerMeta } from "../types";

export function StatusBadge({ status }: { status: LayerMeta["status"] }) {
  return (
    <div className={`status-badge status-${status.state}`} role="status">
      {status.state === "error" ? "Data delayed" : "Data degraded"}
      {status.message ? ` — ${status.message}` : ""}
    </div>
  );
}
