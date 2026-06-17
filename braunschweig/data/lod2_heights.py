"""Pure helpers for the LoD2 height preprocessing: parse 3D-Shape DBF attributes
into OI->height, and select tiles intersecting a region."""
from __future__ import annotations
import re
import pandas as pd
from shapely.geometry import shape

OI_RE = re.compile(r"DENIAL[0-9A-Za-z]+")


def _oi(extern_ref) -> str | None:
    m = OI_RE.search(str(extern_ref))
    return m.group(0) if m else None


def extract_heights(df: pd.DataFrame) -> pd.DataFrame:
    """From a LoD2 3D-Shape attribute frame -> one row per building [OI, height_m, roofType].

    The 3D-Shape stores one row per building surface (wall/roof), all sharing the
    building's gml_id + measHeight, so we dedup by gml_id. OI is parsed from externRef.
    """
    out = df.copy()
    out["OI"] = out["externRef"].map(_oi)
    out["height_m"] = pd.to_numeric(out["measHeight"], errors="coerce")
    out = out.dropna(subset=["OI"]).drop_duplicates("gml_id")
    return out[["OI", "height_m", "roofType"]].reset_index(drop=True)


def tiles_for_region(index: dict, region_4326) -> list[dict]:
    """Return index features (the `properties` dict) whose geometry intersects region (EPSG:4326)."""
    hits = []
    for f in index.get("features", []):
        if shape(f["geometry"]).intersects(region_4326):
            hits.append(f["properties"])
    return hits


import os, time, logging, urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)


def download_tiles(tiles, cache_dir, *, downloader=urllib.request.urlretrieve,
                   max_workers=8, retries=3):
    os.makedirs(cache_dir, exist_ok=True)
    ok, failed = [], []

    def fetch(t):
        dest = os.path.join(cache_dir, f"{t['tile_id']}.zip")
        dest_fwd = dest.replace(os.sep, "/")
        if os.path.exists(dest) and os.path.getsize(dest) > 0:
            return ("ok", dest_fwd)                   # resume: skip existing
        for attempt in range(retries):
            try:
                downloader(t["shp"], dest)
                return ("ok", dest_fwd)
            except Exception as exc:                  # transient network/IO
                if attempt == retries - 1:
                    return ("fail", t["tile_id"])
                time.sleep(0.5 * (attempt + 1))
        return ("fail", t["tile_id"])

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = [ex.submit(fetch, t) for t in tiles]
        for i, fut in enumerate(as_completed(futures), 1):
            status, val = fut.result()
            (ok if status == "ok" else failed).append(val)
            if i % 100 == 0:
                logger.info("[lod2] downloaded %d/%d tiles (%d failed)", i, len(tiles), len(failed))
    if failed:
        logger.warning("[lod2] %d/%d tiles FAILED to download: %s", len(failed), len(tiles), failed[:10])
    return ok, failed
