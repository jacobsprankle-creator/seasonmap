"""Publish pipeline outputs to object storage.

Layout (bucket-relative keys):
    data/{layer}/{YYYY-MM-DD}.tif        scored values (COG, canonical grid)
    tiles/{layer}/{YYYY-MM-DD}.pmtiles   rendered raster tiles
    meta/{layer}/latest.json             date list, legend, stats, status
    static/...                           one-time cached inputs

Two backends behind one interface:
  * R2Publisher    — Cloudflare R2 via the S3 API. Selected automatically when
                     R2_ACCOUNT_ID / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY /
                     R2_BUCKET are all set.
  * LocalPublisher — copies into a local directory (default ./out, override
                     with OUT_DIR). Used for dev and CI without secrets;
                     `web/` can serve this directory directly.
"""
from __future__ import annotations

import datetime as dt
import json
import mimetypes
import os
import shutil
from pathlib import Path
from typing import List, Optional

CONTENT_TYPES = {
    ".pmtiles": "application/octet-stream",
    ".tif": "image/tiff",
    ".json": "application/json",
    ".png": "image/png",
}


def _content_type(key: str) -> str:
    suffix = Path(key).suffix.lower()
    return CONTENT_TYPES.get(suffix) or mimetypes.guess_type(key)[0] or "application/octet-stream"


class LocalPublisher:
    def __init__(self, root: Optional[str] = None):
        self.root = Path(root or os.environ.get("OUT_DIR") or "out").resolve()

    def describe(self) -> str:
        return f"local:{self.root}"

    def put_file(self, local_path: str, key: str) -> str:
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local_path, dst)
        return str(dst)

    def put_bytes(self, data: bytes, key: str) -> str:
        dst = self.root / key
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        return str(dst)

    def get_bytes(self, key: str) -> Optional[bytes]:
        p = self.root / key
        return p.read_bytes() if p.exists() else None

    def list_keys(self, prefix: str) -> List[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return [str(p.relative_to(self.root)) for p in base.rglob("*") if p.is_file()]

    def delete_prefix(self, prefix: str) -> int:
        keys = self.list_keys(prefix)
        for k in keys:
            (self.root / k).unlink()
        return len(keys)


class R2Publisher:
    def __init__(self):
        import boto3  # deferred so local runs don't need it configured

        account = os.environ["R2_ACCOUNT_ID"]
        self.bucket = os.environ["R2_BUCKET"]
        self.client = boto3.client(
            "s3",
            endpoint_url=f"https://{account}.r2.cloudflarestorage.com",
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            region_name="auto",
        )

    def describe(self) -> str:
        return f"r2:{self.bucket}"

    def put_file(self, local_path: str, key: str) -> str:
        self.client.upload_file(
            local_path, self.bucket, key, ExtraArgs={"ContentType": _content_type(key)}
        )
        return f"r2://{self.bucket}/{key}"

    def put_bytes(self, data: bytes, key: str) -> str:
        self.client.put_object(
            Bucket=self.bucket, Key=key, Body=data, ContentType=_content_type(key)
        )
        return f"r2://{self.bucket}/{key}"

    def get_bytes(self, key: str) -> Optional[bytes]:
        try:
            resp = self.client.get_object(Bucket=self.bucket, Key=key)
            return resp["Body"].read()
        except self.client.exceptions.NoSuchKey:
            return None

    def list_keys(self, prefix: str) -> List[str]:
        keys: List[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            keys.extend(o["Key"] for o in page.get("Contents", []))
        return keys

    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under a prefix (batched). Used to prune old runs."""
        keys = self.list_keys(prefix)
        for i in range(0, len(keys), 1000):
            self.client.delete_objects(
                Bucket=self.bucket,
                Delete={"Objects": [{"Key": k} for k in keys[i : i + 1000]], "Quiet": True},
            )
        return len(keys)


def get_publisher(out_dir: Optional[str] = None):
    """R2 when fully configured, local directory otherwise.

    Set REQUIRE_R2=1 (CI does) to turn the silent local fallback into a hard
    error naming the missing secrets — publishing a nightly run to a CI
    runner's disk is worse than failing fast.
    """
    required = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET")
    missing = [k for k in required if not os.environ.get(k)]
    if not missing and out_dir is None:
        return R2Publisher()
    if os.environ.get("REQUIRE_R2") and out_dir is None:
        raise SystemExit(
            "REQUIRE_R2 is set but R2 is not fully configured — missing/empty: "
            f"{', '.join(missing)}. Fix the repo's Actions secrets (Settings → "
            "Secrets and variables → Actions → Secrets)."
        )
    return LocalPublisher(out_dir)


def self_test(publisher) -> None:
    """Prove the publish target is writable BEFORE spending compute.

    Uploads health/pipeline-ping.json; with a public R2 bucket this doubles as
    an externally checkable beacon that credentials work.
    """
    payload = json.dumps(
        {
            "ok": True,
            "at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "target": publisher.describe(),
        }
    ).encode("utf-8")
    try:
        publisher.put_bytes(payload, "health/pipeline-ping.json")
    except Exception as exc:  # noqa: BLE001 — we want ANY failure surfaced here
        raise SystemExit(
            f"publish self-test FAILED for {publisher.describe()}: {exc}\n"
            "Nothing was computed. Check R2 credentials/bucket name."
        ) from None
    print(f"publish self-test ok → {publisher.describe()} (health/pipeline-ping.json)", flush=True)


# ---------------------------------------------------------------------------
# meta/{layer}/latest.json
# ---------------------------------------------------------------------------

def build_meta(
    layer: str,
    dates: List[str],
    legend: dict,
    stats: dict,
    min_zoom: int,
    max_zoom: int,
    units: str,
    description: str,
    status_state: str = "ok",
    status_message: Optional[str] = None,
) -> dict:
    from . import grid  # local import to keep publish importable standalone

    dates = sorted(set(dates))
    return {
        "layer": layer,
        "description": description,
        "grid": {
            "width": grid.WIDTH,
            "height": grid.HEIGHT,
            "transform": list(grid.TRANSFORM)[:6],
            "crs": "EPSG:4269",
            "nodata": grid.NODATA,
        },
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "dates": dates,
        "latest": dates[-1] if dates else None,
        "tiles": f"tiles/{layer}/{{date}}.pmtiles",
        "data": f"data/{layer}/{{date}}.tif",
        "query": f"query/{layer}/{{date}}.json",
        "minzoom": min_zoom,
        "maxzoom": max_zoom,
        "units": units,
        "legend": legend,
        "stats": stats,
        "status": {
            "state": status_state,  # ok | degraded | error
            "message": status_message,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        },
    }


def publish_layer_date(publisher, layer: str, date: str, cog_path: str, pmtiles_path: str) -> None:
    publisher.put_file(cog_path, f"data/{layer}/{date}.tif")
    publisher.put_file(pmtiles_path, f"tiles/{layer}/{date}.pmtiles")


def publish_meta(publisher, layer: str, meta: dict) -> None:
    publisher.put_bytes(
        json.dumps(meta, indent=2).encode("utf-8"), f"meta/{layer}/latest.json"
    )


def publish_error_meta(publisher, layer: str, message: str) -> None:
    """Failure isolation: a broken layer publishes an error status instead of
    blocking the run; the UI shows a "data delayed" badge off this."""
    existing = publisher.get_bytes(f"meta/{layer}/latest.json")
    if existing:
        meta = json.loads(existing)
        meta["status"] = {
            "state": "error",
            "message": message,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
    else:
        meta = build_meta(
            layer, [], {}, {}, 0, 0, "", "", status_state="error", status_message=message
        )
    publish_meta(publisher, layer, meta)
