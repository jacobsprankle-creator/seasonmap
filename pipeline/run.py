"""Pipeline CLI.

    python -m pipeline.run --layer elevation --date today
    python -m pipeline.run --layer all --date 2026-10-15 --max-zoom 6 --out ./out

Idempotent: re-running the same layer/date overwrites its outputs. Failure
isolated: one layer failing publishes an error status for that layer and the
run continues; exit code is non-zero only if *every* requested layer failed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import List

import json

from .core import colormaps, publish, querygrid, tiling
from .layers import IMPLEMENTED_LAYERS, LAYERS


def _resolve_date(raw: str) -> str:
    if raw in ("today", "now"):
        return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    dt.datetime.strptime(raw, "%Y-%m-%d")  # validate
    return raw


def run_vector_layer(layer_name: str, run_date: str, publisher) -> List[str]:
    mod = LAYERS[layer_name]
    published: List[str] = []
    out = None
    for date in mod.dates_for(run_date):
        out = mod.score(date)
        publisher.put_bytes(
            json.dumps(out.geojson, separators=(",", ":")).encode("utf-8"),
            f"data/{layer_name}/{date}.geojson",
        )
        published.append(date)
        print(f"  {layer_name} {date}: {out.stats()['features']} features published")
    assert out is not None
    meta = publish.build_meta(
        layer=layer_name,
        dates=published,
        legend=out.legend,
        stats=out.stats(),
        min_zoom=0,
        max_zoom=0,
        units=out.units,
        description=out.description,
    )
    meta["type"] = "vector"
    meta["data"] = f"data/{layer_name}/{{date}}.geojson"
    meta["style"] = out.style
    meta.pop("tiles", None)
    meta.pop("query", None)
    meta.update(out.extra_meta)
    publish.publish_meta(publisher, layer_name, meta)
    return published


def run_layer(layer_name: str, run_date: str, publisher, max_zoom: int) -> List[str]:
    mod = LAYERS[layer_name]
    if getattr(mod, "KIND", "raster") == "vector":
        return run_vector_layer(layer_name, run_date, publisher)
    dates = mod.dates_for(run_date)
    workdir = Path(tempfile.mkdtemp(prefix=f"seasonmap-{layer_name}-"))

    published: List[str] = []
    last_output = None
    for date in dates:
        out = mod.score(date)
        last_output = out
        cog_path = str(workdir / f"{date}.tif")
        pmt_path = str(workdir / f"{date}.pmtiles")
        tiling.write_cog(out.values, cog_path)
        cmap = colormaps.build_colormap(
            colormaps.COLORMAPS[out.colormap], out.vmin, out.vmax,
            stepped=out.colormap in getattr(colormaps, "STEPPED", set()),
        )
        n_tiles = tiling.render_pmtiles(
            cog_path,
            pmt_path,
            cmap,
            out.vmin,
            out.vmax,
            max_zoom=max_zoom,
            metadata={"layer": layer_name, "date": date, "units": out.units},
            contour_interval=getattr(out, "contour_interval", None),
        )
        publish.publish_layer_date(publisher, layer_name, date, cog_path, pmt_path)
        qg = querygrid.build_query_grid(out.values)
        publisher.put_bytes(
            json.dumps(qg).encode("utf-8"), f"query/{layer_name}/{date}.json"
        )
        published.append(date)
        print(f"  {layer_name} {date}: {n_tiles} tiles published")

    assert last_output is not None
    meta = publish.build_meta(
        layer=layer_name,
        dates=published,
        legend=last_output.legend,
        stats=last_output.stats(),
        min_zoom=tiling.DEFAULT_MIN_ZOOM,
        max_zoom=max_zoom,
        units=last_output.units,
        description=last_output.description,
    )
    meta.update(last_output.extra_meta)
    publish.publish_meta(publisher, layer_name, meta)
    return published


def main(argv: List[str] = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.run", description="seasonmap batch pipeline")
    parser.add_argument(
        "--layer",
        default="all",
        help="layer slug, comma-separated slugs, or 'all' (implemented layers)",
    )
    parser.add_argument("--date", default="today", help="run date YYYY-MM-DD or 'today'")
    parser.add_argument("--max-zoom", type=int, default=tiling.DEFAULT_MAX_ZOOM)
    parser.add_argument("--out", default=None, help="force local output dir (skips R2 even if configured)")
    args = parser.parse_args(argv)

    run_date = _resolve_date(args.date)
    publisher = publish.get_publisher(args.out)

    if args.layer == "all":
        targets = list(IMPLEMENTED_LAYERS)
    else:
        targets = [s.strip() for s in args.layer.split(",") if s.strip()]
        unknown = [s for s in targets if s not in LAYERS]
        if unknown:
            parser.error(f"unknown layer(s) {unknown} (known: {', '.join(LAYERS)})")

    print(
        f"seasonmap pipeline · date={run_date} · {len(targets)} layer(s): "
        f"{', '.join(targets)} · publish={publisher.describe()}",
        flush=True,
    )
    publish.self_test(publisher)
    failures = 0
    for name in targets:
        t0 = time.time()
        try:
            dates = run_layer(name, run_date, publisher, args.max_zoom)
            print(f"✓ {name}: {len(dates)} date(s) in {time.time() - t0:.1f}s")
        except Exception as exc:  # noqa: BLE001 — failure isolation by design
            failures += 1
            print(f"✗ {name} failed: {exc}", file=sys.stderr)
            traceback.print_exc()
            try:
                publish.publish_error_meta(publisher, name, str(exc))
            except Exception as meta_exc:  # noqa: BLE001
                print(f"  (error meta publish also failed: {meta_exc})", file=sys.stderr)

    return 1 if failures == len(targets) else 0


if __name__ == "__main__":
    sys.exit(main())
