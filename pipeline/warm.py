"""Warm the static-input cache: `python -m pipeline.warm`.

Downloads/derives the expensive one-time inputs into static_cache/ so the
parallel nightly matrix jobs all restore a warm cache instead of each
re-downloading:

  * elevation DEM (NCEI, ~1 min; ERDDAP fallback)
  * PRISM monthly tmin normals (12 zips)
  * frost survival climatologies for all three thresholds (36/32/28 °F —
    the expensive derived product; minutes each when cold, instant after)

Idempotent and near-instant when the cache is already populated. Exits
non-zero only if every step failed — a partial warm still helps.
"""
from __future__ import annotations

import sys
import time

from .core.sources.elevation import ensure_elevation, static_dir
from .core.sources.prism import ensure_tmin_normals
from .layers.frost import THRESHOLDS_C, ensure_climatology


def main() -> int:
    t0 = time.time()
    steps = [
        ("elevation DEM", ensure_elevation),
        ("PRISM tmin normals", ensure_tmin_normals),
    ]
    for name, thresh_c in THRESHOLDS_C.items():
        steps.append(
            (f"frost climatology '{name}'", lambda t=thresh_c: ensure_climatology(threshold_c=t))
        )

    failures = 0
    for label, fn in steps:
        s = time.time()
        try:
            fn()
            print(f"✓ {label} in {time.time() - s:.1f}s", flush=True)
        except Exception as exc:  # noqa: BLE001 — warm is best-effort per step
            failures += 1
            print(f"✗ {label}: {exc}", file=sys.stderr, flush=True)

    files = [f for f in static_dir().glob("*") if f.is_file()]
    total_mb = sum(f.stat().st_size for f in files) / 1e6
    print(
        f"static cache: {len(files)} file(s), {total_mb:.0f} MB · warm took {time.time() - t0:.1f}s",
        flush=True,
    )
    return 1 if failures == len(steps) else 0


if __name__ == "__main__":
    sys.exit(main())
