"""Models 3.0 — live frame streaming.  `python -m pipeline.stream`

Every ~30 min tick: for each model family, diff the frames Open-Meteo has
uploaded to S3 (data_spatial per-timestep files, landing near-real-time
WHILE the model runs) against R2 state, read only the NEW frames, render
the motion trio (temp / step precip / surface composite) and publish them
run-scoped:

    tiles/{layer}/{runKey}/{frameKey}.pmtiles
    query/{layer}/{runKey}/{frameKey}.json
    meta/{layer}/latest.json   — tiles template carries a {run} token
    state/models/{family}.json — ingest cursor (run + published frames)

Run boundary (Option A): a new run is adopted only once its uploaded frames
reach +24 h of lead (+12 h for HRRR's short hourly runs), so the map never
flips to a 3-frame run. Until then the previous run keeps serving.

Resilience: per-family isolation, per-chunk state saves, a global soft
deadline (self-heals next tick via the state diff), and per-frame attempt
caps so a permanently incomplete frame (e.g. F00 with no precip) can't
wedge the loop. Family order rotates per tick so no family starves.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .core import colormaps, grid, publish, querygrid, tiling
from .core.sources import spatial_frames as sf
from .core.sources.prism import ensure_tmin_normals
from .layers.base import LayerOutput

# slug, Open-Meteo family id, label, switch lead (hours)
FAMILIES: List[Tuple[str, str, str, float]] = [
    ("gfs", "gfs_seamless", "GFS", 24.0),
    ("hrrr", "gfs_hrrr", "HRRR", 12.0),  # most HRRR runs top out at +18 h
    ("euro", "ecmwf_ifs025", "ECMWF", 24.0),
    ("ukmet", "ukmo_seamless", "UKMET", 24.0),
    ("icon", "icon_seamless", "ICON", 24.0),
    ("gem", "gem_seamless", "GEM", 24.0),
    ("nbm", "ncep_nbm_conus", "NBM", 24.0),  # NWS blend — temp + precip only (no mslp)
]
MAX_ZOOM = 5
KEEP_RUNS = 2      # runs with tiles on R2 (active + previous)
CHUNK = 8          # frames per read batch / state save
MAX_ATTEMPTS = 2   # per-frame tries before it's skipped for good

_land_cache: Optional[np.ndarray] = None


def _land() -> np.ndarray:
    global _land_cache
    if _land_cache is None:
        _land_cache = ensure_tmin_normals()[0] != grid.NODATA
    return _land_cache


def _state_key(fam: str) -> str:
    return f"state/models/{fam}.json"


def _lead_h(frame_key: str, run_start: dt.datetime) -> float:
    t = dt.datetime.strptime(frame_key, "%Y-%m-%dT%H%M")
    return (t - run_start).total_seconds() / 3600.0


def _masked(values: np.ndarray, land: np.ndarray) -> np.ndarray:
    out = values.copy()
    out[~land] = grid.NODATA
    return out


def _products(fam: str, label: str, fk: str, run_stamp: str, vals: Dict[str, np.ndarray]) -> List[LayerOutput]:
    """The motion trio for one frame, palette/legend-identical to the old
    hourly products (slugs unchanged — the UI needs no new variants)."""
    land = _land()
    meta = {"value_format": "number", "opacity": 0.66, "model_run": run_stamp, "hourly": True}

    t2m = vals["temperature_2m"]
    temp = np.where(t2m == grid.NODATA, grid.NODATA, t2m * 9.0 / 5.0 + 32.0).astype(np.float32)

    pr = vals["precipitation"]
    precip = np.where(pr == grid.NODATA, grid.NODATA, np.maximum(pr, 0.0) / 25.4).astype(np.float32)

    outs = [
        LayerOutput(f"{fam}_tmax", fk, _masked(temp, land), "temp_f", -10, 110, "°F",
                    f"{label} 2-m temperature — live hourly", extra_meta=meta),
        LayerOutput(f"{fam}_precip3", fk, _masked(precip, land), "precip_in_step", 0, 1.5, "in",
                    f"{label} step precipitation — live hourly", extra_meta=meta),
    ]

    if vals.get("pressure_msl") is not None:  # blends (NBM) carry no mslp
        mslp = vals["pressure_msl"].astype(np.float32)
        valid = mslp != grid.NODATA
        if valid.any() and float(np.nanmean(mslp[valid])) > 10000.0:  # Pa, not hPa
            mslp[valid] = mslp[valid] / 100.0
        outs.append(
            LayerOutput(f"{fam}_sfc", fk, _masked(precip, land), "precip_in_step", 0, 1.5, "in",
                        f"{label} surface map — MSLP isobars over step precipitation (live)",
                        extra_meta=meta, contour_interval=4.0, contour_values=mslp))
    return outs


_cmap_cache: Dict[Tuple[str, float, float], dict] = {}


def _cmap(out: LayerOutput):
    key = (out.colormap, out.vmin, out.vmax)
    if key not in _cmap_cache:
        _cmap_cache[key] = colormaps.build_colormap(
            colormaps.COLORMAPS[out.colormap], out.vmin, out.vmax,
            stepped=out.colormap in getattr(colormaps, "STEPPED", set()),
        )
    return _cmap_cache[key]


def _publish_frame(publisher, workdir: Path, out: LayerOutput, run_key: str) -> int:
    cog = workdir / f"{out.layer}-{out.date}.tif"
    pmt = workdir / f"{out.layer}-{out.date}.pmtiles"
    tiling.write_cog(out.values, str(cog), second_band=getattr(out, "contour_values", None))
    n = tiling.render_pmtiles(
        str(cog), str(pmt), _cmap(out), out.vmin, out.vmax, max_zoom=MAX_ZOOM,
        metadata={"layer": out.layer, "date": out.date, "units": out.units, "run": run_key},
        contour_interval=getattr(out, "contour_interval", None),
    )
    publisher.put_file(str(pmt), f"tiles/{out.layer}/{run_key}/{out.date}.pmtiles")
    qg = querygrid.build_query_grid(out.values)
    publisher.put_bytes(json.dumps(qg).encode("utf-8"), f"query/{out.layer}/{run_key}/{out.date}.json")
    cog.unlink(missing_ok=True)
    pmt.unlink(missing_ok=True)
    return n


def _publish_meta(publisher, sample: LayerOutput, dates: List[str], run_key: str, runs: List[str],
                  runs_dates: Optional[Dict[str, List[str]]] = None) -> None:
    meta = publish.build_meta(
        layer=sample.layer, dates=dates, legend=sample.legend, stats=sample.stats(),
        min_zoom=tiling.DEFAULT_MIN_ZOOM, max_zoom=MAX_ZOOM,
        units=sample.units, description=sample.description,
    )
    meta.update(sample.extra_meta)
    meta["tiles"] = f"tiles/{sample.layer}/{{run}}/{{date}}.pmtiles"
    meta["query"] = f"query/{sample.layer}/{{run}}/{{date}}.json"
    meta.pop("data", None)
    meta["run"] = run_key
    meta["runs"] = runs[:4]
    if runs_dates:
        meta["runs_dates"] = runs_dates
    publish.publish_meta(publisher, sample.layer, meta)


def _touch_meta(publisher, layer: str, dates: List[str], run_key: str, runs: List[str],
                runs_dates: Optional[Dict[str, List[str]]] = None) -> None:
    """Refresh dates/run/heartbeat on existing meta without a sample frame."""
    raw = publisher.get_bytes(f"meta/{layer}/latest.json")
    if not raw:
        return
    meta = json.loads(raw)
    meta["dates"] = dates
    meta["latest"] = dates[-1] if dates else None
    meta["run"] = run_key
    meta["runs"] = runs[:4]
    if runs_dates:
        meta["runs_dates"] = runs_dates
    meta["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    publish.publish_meta(publisher, layer, meta)


def _fresh_state(run_key: str, run_start: dt.datetime, old: Optional[dict]) -> dict:
    """New-run cursor. Carries the previous run's frame list so meta can offer
    a run picker (runs_dates) while KEEP_RUNS retains its tiles."""
    state = {
        "run": run_key,
        "run_start": run_start.strftime("%Y-%m-%dT%H:%M"),
        "frames": [],
        "attempts": {},
        "prev_runs": (([old["run"]] + old.get("prev_runs", [])) if old else [])[:6],
    }
    if old and old.get("frames"):
        state["prev"] = {"run": old["run"], "frames": old["frames"]}
    return state


def process_family(publisher, fam: str, om_id: str, label: str, switch_lead: float,
                   deadline: float, max_frames: Optional[int] = None) -> str:
    cfg = sf.STREAM_DOMAINS[om_id]
    main_domain = cfg[0][0]
    cand_start, cand_run, cand_frames = sf.list_run_frames(main_domain)

    raw = publisher.get_bytes(_state_key(fam))
    state = json.loads(raw) if raw else None
    switched = False

    if state and state.get("run") == cand_run:
        active_run, active_start, main_frames = cand_run, cand_start, cand_frames
    else:
        max_lead = max((_lead_h(k, cand_start) for k in cand_frames), default=0.0)
        if state is None or max_lead >= switch_lead:
            active_run, active_start, main_frames = cand_run, cand_start, cand_frames
            state = _fresh_state(active_run, active_start, state)
            switched = True
        else:
            # Candidate too young (Option A) — keep serving/ingesting the old run.
            active_run = state["run"]
            active_start = dt.datetime.strptime(state["run_start"], "%Y-%m-%dT%H:%M")
            try:
                _, _, main_frames = sf.list_run_frames_at(main_domain, active_run)
            except Exception:
                # Old run dir vanished (7-day retention or listing hiccup) — adopt.
                active_run, active_start, main_frames = cand_run, cand_start, cand_frames
                state = _fresh_state(active_run, active_start, state)
                switched = True

    indexes: Dict[str, Dict[str, str]] = {main_domain: main_frames}
    for domain, _vars in cfg[1:]:
        try:
            _, _, indexes[domain] = sf.list_run_frames_at(domain, active_run)
        except Exception:
            indexes[domain] = {}

    common = set(main_frames)
    for domain, _vars in cfg[1:]:
        common &= set(indexes[domain])

    done = set(state["frames"])
    attempts: Dict[str, int] = state.get("attempts", {})
    new = sorted(
        k for k in common
        if k not in done and attempts.get(k, 0) < MAX_ATTEMPTS and _lead_h(k, active_start) > 0
    )
    if max_frames is not None:
        new = new[:max_frames]

    run_stamp = active_start.strftime("%Y-%m-%dT%H:%MZ")
    runs = [active_run] + state.get("prev_runs", [])

    def _runs_dates(dates_sorted: List[str]) -> Dict[str, List[str]]:
        rd = {active_run: dates_sorted}
        prev = state.get("prev")
        if prev and prev.get("frames"):
            rd[prev["run"]] = sorted(prev["frames"])
        return rd

    if switched:
        # Prune runs beyond KEEP_RUNS for all three layers (+ their query grids).
        for old in state.get("prev_runs", [])[KEEP_RUNS - 1:]:
            for slug in (f"{fam}_tmax", f"{fam}_precip3", f"{fam}_sfc"):
                for root in ("tiles", "query"):
                    try:
                        n = publisher.delete_prefix(f"{root}/{slug}/{old}/")
                        if n:
                            print(f"  {fam}: pruned {n} objects from {root}/{slug}/{old}/")
                    except Exception as exc:  # noqa: BLE001
                        print(f"  {fam}: prune {root}/{slug}/{old} failed: {exc}")
        state["prev_runs"] = state.get("prev_runs", [])[: KEEP_RUNS - 1 + 2]

    if not new:
        for slug in (f"{fam}_tmax", f"{fam}_precip3", f"{fam}_sfc"):
            _touch_meta(publisher, slug, sorted(state["frames"]), active_run, runs,
                        _runs_dates(sorted(state["frames"])))
        publisher.put_bytes(json.dumps(state).encode("utf-8"), _state_key(fam))
        return f"{fam}: up to date ({len(state['frames'])} frames, run {active_run})"

    print(f"  {fam}: run {active_run}{' (switched)' if switched else ''} — {len(new)} new frames")
    workdir = Path(tempfile.mkdtemp(prefix=f"stream-{fam}-"))
    published = 0
    last_outs: List[LayerOutput] = []
    try:
        for i in range(0, len(new), CHUNK):
            if time.monotonic() > deadline:
                print(f"  {fam}: deadline — {published} frames this tick, rest next tick")
                break
            chunk = new[i : i + CHUNK]
            frames = sf.read_frames(om_id, chunk, indexes, concurrency=6)
            for fk in chunk:
                attempts[fk] = attempts.get(fk, 0) + 1
                vals = frames.get(fk)
                if not vals or any(v is None for v in vals.values()):
                    continue
                try:
                    outs = _products(fam, label, fk, run_stamp, vals)
                    for out in outs:
                        _publish_frame(publisher, workdir, out, active_run)
                    last_outs = outs
                    state["frames"].append(fk)
                    attempts.pop(fk, None)
                    published += 1
                except Exception as exc:  # noqa: BLE001
                    print(f"  {fam} {fk}: render/publish failed: {exc}")
            state["attempts"] = attempts
            publisher.put_bytes(json.dumps(state).encode("utf-8"), _state_key(fam))
            print(f"  {fam}: +{published}/{len(new)} (total {len(state['frames'])})")
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    dates = sorted(state["frames"])
    if last_outs:
        for out in last_outs:
            _publish_meta(publisher, out, dates, active_run, runs, _runs_dates(dates))
    else:
        for slug in (f"{fam}_tmax", f"{fam}_precip3", f"{fam}_sfc"):
            _touch_meta(publisher, slug, dates, active_run, runs, _runs_dates(dates))
    publisher.put_bytes(json.dumps(state).encode("utf-8"), _state_key(fam))
    return f"{fam}: published {published} frames (run {active_run}, total {len(state['frames'])})"


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Stream new model frames to tiles")
    ap.add_argument("--families", default="all", help="comma-separated slugs or 'all'")
    ap.add_argument("--out", default=None, help="local output dir (skips R2)")
    ap.add_argument("--deadline-min", type=float, default=22.0)
    ap.add_argument("--max-frames", type=int, default=None, help="cap new frames per family (testing)")
    args = ap.parse_args(argv)

    publisher = publish.get_publisher(args.out)
    print(f"stream tick · publish={publisher.describe()}")
    publish.self_test(publisher)

    wanted = None if args.families == "all" else {s.strip() for s in args.families.split(",")}
    fams = [f for f in FAMILIES if wanted is None or f[0] in wanted]
    # Rotate start position each tick so a heavy family can't starve the rest.
    tick = int(time.time() // 1800)
    fams = fams[tick % len(fams):] + fams[: tick % len(fams)] if fams else fams

    deadline = time.monotonic() + args.deadline_min * 60.0
    failures = 0
    for i, (fam, om_id, label, switch_lead) in enumerate(fams):
        now = time.monotonic()
        if now > deadline:
            print(f"{fam}: skipped (deadline) — next tick")
            continue
        # Fair-share budget: one family's run-switch backfill (gem = 104
        # frames) must not starve the rest of the tick. Early finishers
        # donate their unused share to whoever comes next.
        fam_deadline = min(deadline, now + max(240.0, (deadline - now) / (len(fams) - i)))
        try:
            print(process_family(publisher, fam, om_id, label, switch_lead, fam_deadline, args.max_frames))
        except Exception:  # noqa: BLE001
            failures += 1
            print(f"{fam}: FAILED\n{traceback.format_exc()}", file=sys.stderr)
    sf.shutdown()
    return 1 if failures == len(fams) and fams else 0


if __name__ == "__main__":
    raise SystemExit(main())
