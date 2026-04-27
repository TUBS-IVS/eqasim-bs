"""
Preprocess OSM POIs for the configured ZGB scope.

Fast path (~1-2 min instead of ~15 min):

  1. Load VG250 Gemeinden whose ARS starts with one of the configured
     prefixes (default ZGB-8).  Dissolve to one polygon.
  2. Use osmconvert to clip the 800 MB Niedersachsen PBF down to the
     ZGB polygon.  Result (~30 MB) is cached next to the original PBF
     so repeat runs skip this step.
  3. Read the scope PBF with pyrosm **once** (single pass, no filter).
     Apply the four location_type filters as pandas boolean masks.
  4. Reproject to UTM-32N, compute area on polygons, switch to
     centroid, sjoin to VG250 Gemeinden for commune_id/iris_id,
     deduplicate, write parquet.

A background heartbeat thread prints a status line to stderr every
few seconds during the long pyrosm parse so you can see progress.

Typical invocation:
    python scripts/preprocess_osm_pois.py \
        --pbf eqasim-data/data/osm/niedersachsen-latest.osm.pbf \
        --vg250 eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip \
        --prefix 03101,03102,03103,03151,03153,03154,03157,03158 \
        --out eqasim-data/data/braunschweig/preprocessed/osm_pois.parquet
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import time
import warnings
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import pyrosm


# ---------------------------------------------------------------------------
# Filter lists — identical to ``eqasim_common.data.osm.locations``.
# ---------------------------------------------------------------------------

OSM_FILTERS = [
    {
        "location_type": "education",
        "filter": {
            "building": {"school", "university", "kindergarten"},
            "amenity": {"school", "university", "kindergarten"},
        },
    },
    {
        "location_type": "shop",
        "filter": {
            "building": {
                "retails", "apartments;commerical", "mixd_use", "mixed",
                "kiosk", "supermarket", "mixed_use", "mall", "commercial",
                "shop", "retail",
            },
            "amenity": {
                "pharmacy", "convenience_store", "commercial",
                "marketplace", "winery", "food_court", "convenience",
            },
        },
    },
    {
        "location_type": "leisure",
        "filter": {
            "amenity": {
                "social_facility", "theatre", "swimming_pool",
                "place_of_worship", "library", "science_park",
                "social_centre", "arts_centre", "community_centre",
                "restaurant", "events_centre", "pub", "cafe",
                "commercial", "cinema", "winery", "bar", "amphitheatre",
                "concert_hall", "studio", "nightclub", "food_court",
                "bbq", "music_venue", "senior_center", "pool", "casino",
                "events_venue", "spa", "boat_rental", "senior_centre",
                "music_venue;bar", "community_center", "ice_cream",
                "church", "park", "stripclub", "swingerclub",
                "biergarten", "music_rehearsal_place", "cafeteria",
                "meditation_centre", "gym", "planetarium", "clubhouse",
                "dive_centre", "community_hall", "event_hall",
                "bicycle_rental", "club", "gambling",
            },
        },
    },
    {
        "location_type": "work",
        "filter": {
            "building": {
                "hotel", "tower", "police_station", "retail", "shop",
                "arena", "transportation", "office", "commercial",
                "hangar", "industrial", "terminal", "mall", "warehouse",
                "multi_level_parking", "university", "dormitory",
                "museum", "theatre", "stadium", "fire_station",
                "control_tower", "manufacture", "sports_centre",
                "hospital", "train_station", "civic", "church",
                "gymnasium", "temple", "mixed_use", "central_office",
                "amphitheatre", "business", "barn", "data_center",
                "cinema", "service", "supermarket", "weapon_armory",
                "cathedral", "farm_auxiliary", "factory", "station",
                "library", "farm", "mosque", "stable",
                "historic_building", "carousel", "synagogue", "convent",
                "mortuary", "prison", "brewery", "office", "monastery",
                "clinic", "kiosk", "carpark", "mixed", "mixd_use",
                "motel", "community_center", "research", "charity",
                "medical", "offices", "community_centre", "synogogue",
                "Athletic_field_house", "depot", "Laundry", "chapel",
                "lighthouse", "clubhouse", "guardhouse", "bungalow",
                "retails", "tech_cab", "commerical", "gasstation",
                "yes;offices", "castle",
            },
            "amenity": {
                "school", "bank", "hospital", "social_facility", "police",
                "pharmacy", "theatre", "university", "college",
                "swimming_pool", "place_of_worship", "library", "clinic",
                "science_park", "conference_centre", "trailer_park",
                "social_centre", "arts_centre", "courthouse",
                "post_office", "community_centre", "car_rental",
                "restaurant", "ranger_station", "events_centre",
                "convenience_store", "townhall", "mortuary", "fuel",
                "car_wash", "fast_food", "pub", "fire_station", "cafe",
                "doctors", "commercial", "nursing_home", "marketplace",
                "cinema", "public_building", "winery", "dentist", "bar",
                "amphitheatre", "ferry_terminal", "concert_hall",
                "studio", "nightclub", "kindergarten", "civic",
                "food_court", "childcare", "prison", "caravan_rental",
                "monastery", "dialysis", "veterinary", "music_venue",
                "senior_center", "pool", "casino", "events_venue",
                "preschool", "animal_shelter", "spa", "boat_rental",
                "senior_centre", "brokerage", "vehicle_inspection",
                "healthcare", "music_venue;bar", "community_center",
                "embassy", "ice_cream", "tailor", "coworking_space",
                "church", "storage_rental", "stripclub", "swingerclub",
                "office", "biergarten", "music_rehearsal_place",
                "cafeteria", "truck_rental", "sperm_bank",
                "meditation_centre", "funeral_parlor", "cruise_terminal",
                "crematorium", "gym", "planetarium", "clubhouse",
                "language_school", "convenience", "music_school",
                "dive_centre", "community_hall", "event_hall",
                "research_institute", "club", "gambling",
                "retirement_village",
            },
        },
    },
]

DEFAULT_FLOORS = 2
DEFAULT_OSMCONVERT = Path(
    r"C:/Users/bienzeisler/tools/osmconvert/osmconvert.exe"
)


# ---------------------------------------------------------------------------
# Heartbeat printer
# ---------------------------------------------------------------------------

class Heartbeat:
    """Print a status line to stderr every ``interval`` seconds."""

    def __init__(self, label: str, interval: float = 5.0):
        self.label = label
        self.interval = interval
        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)
        self._start = 0.0

    def _run(self):
        while not self._stop.wait(self.interval):
            elapsed = time.time() - self._start
            sys.stderr.write(
                f"\r  .. {self.label} running … {elapsed:6.1f}s"
            )
            sys.stderr.flush()

    def __enter__(self):
        self._start = time.time()
        self._t.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._t.join(timeout=1.0)
        sys.stderr.write("\r" + " " * 70 + "\r")
        sys.stderr.flush()


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--pbf",
                   default="eqasim-data/data/osm/niedersachsen-latest.osm.pbf")
    p.add_argument("--vg250",
                   default="eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
    p.add_argument("--prefix",
                   default="03101,03102,03103,03151,03153,03154,03157,03158",
                   help="Comma-separated AGS/ARS prefixes (default = ZGB-8).")
    p.add_argument("--out",
                   default="eqasim-data/data/braunschweig/preprocessed/osm_pois.parquet")
    p.add_argument("--osmconvert", default=str(DEFAULT_OSMCONVERT),
                   help="Path to osmconvert.exe (used to clip PBF).")
    p.add_argument("--no-clip", action="store_true",
                   help="Skip osmconvert clipping and read the full PBF.")
    p.add_argument("--crs-out", default="EPSG:25832")
    p.add_argument("--compression", default="zstd",
                   choices=["zstd", "snappy", "gzip", "none"])
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Scope loading
# ---------------------------------------------------------------------------

def load_scope(vg250_path: Path, prefixes: list[str]) -> gpd.GeoDataFrame:
    """Return per-Gemeinde polygons (UTM-32N) for the configured scope."""
    path_vsi = (
        f"/vsizip/{vg250_path.as_posix()}/"
        "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg"
    )
    where = " OR ".join(f"ARS LIKE '{p}%'" for p in prefixes)
    df = pyogrio.read_dataframe(
        path_vsi, layer="vg250_gem", columns=["ARS"], where=where,
    )
    df = gpd.GeoDataFrame(df, crs="EPSG:25832")
    df["commune_id"] = df["ARS"].astype(str)
    df["iris_id"] = df["commune_id"]
    return df[["commune_id", "iris_id", "geometry"]]


def write_polyfile(scope_wgs84, poly_path: Path) -> None:
    """osmconvert .poly format (same as bavaria/data/osm/chunked.py)."""
    geom = scope_wgs84
    if not hasattr(geom, "exterior"):
        geom = geom.convex_hull
    lines = ["polyfile", "polygon"]
    for x, y in geom.exterior.coords:
        lines.append(f"    {x:e}    {y:e}")
    lines += ["END", "END"]
    poly_path.write_text("\n".join(lines), encoding="utf-8")


def clip_pbf(pbf: Path, scope_wgs84, osmconvert: Path) -> Path:
    """Clip PBF to scope bbox via osmconvert. Cached result."""
    clipped = pbf.with_suffix(".zgb.osm.pbf")

    if clipped.exists() and clipped.stat().st_mtime > pbf.stat().st_mtime \
            and clipped.stat().st_size > 1_000_000:
        size_mb = clipped.stat().st_size / (1024 * 1024)
        log(f"PBF: using cached clip {clipped.name} ({size_mb:.1f} MB)")
        return clipped

    if not osmconvert.exists():
        log(f"PBF: osmconvert not found at {osmconvert} — reading full PBF")
        return pbf

    # Use plain bbox (minx,miny,maxx,maxy) — simpler and more reliable
    # than osmconvert's polyfile format. Small over-capture is fine
    # because the downstream sjoin clips to the precise Gemeinde polygons.
    minx, miny, maxx, maxy = scope_wgs84.bounds
    # Small buffer (~1 km) to avoid clipping buildings right on the border.
    buf = 0.01
    bbox_arg = f"-b={minx - buf},{miny - buf},{maxx + buf},{maxy + buf}"

    cmd = [str(osmconvert), str(pbf), bbox_arg,
           f"-o={clipped}", "--complete-ways"]
    log(f"PBF: clipping with osmconvert ({bbox_arg}) → {clipped.name} ...")
    t0 = time.time()
    with Heartbeat("osmconvert"):
        r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"PBF: osmconvert failed ({r.returncode}); "
            f"stderr: {r.stderr[:400]}")
        return pbf

    size_mb = clipped.stat().st_size / (1024 * 1024)
    if size_mb < 1.0:
        log(f"PBF: clip is only {size_mb:.2f} MB — likely failed, "
            f"falling back to full PBF")
        return pbf
    log(f"PBF: clipped to {size_mb:.1f} MB in {time.time() - t0:.1f}s")
    return clipped


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_all_buildings(pbf: Path) -> gpd.GeoDataFrame:
    """Single-pass pyrosm read: all buildings + their tags."""
    log(f"OSM: parsing {pbf.name} (single pass, no filter) ...")
    t0 = time.time()
    with Heartbeat("pyrosm.get_buildings"):
        osm = pyrosm.OSM(str(pbf))
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=UserWarning)
            warnings.filterwarnings("ignore", category=FutureWarning)
            df = osm.get_buildings()
    n = 0 if df is None else len(df)
    log(f"OSM: {n:,} buildings parsed in {time.time() - t0:.1f}s")
    if df is None or len(df) == 0:
        raise RuntimeError("pyrosm returned no buildings")
    return df


def apply_filters(df_raw: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Apply the 4 location_type filters in-memory."""
    for col in ("building", "amenity"):
        if col not in df_raw.columns:
            df_raw[col] = pd.NA

    frames = []
    for spec in OSM_FILTERS:
        mask = pd.Series(False, index=df_raw.index)
        for tag_col, allowed in spec["filter"].items():
            if tag_col in df_raw.columns:
                mask |= df_raw[tag_col].isin(allowed)
        sub = df_raw.loc[mask].copy()
        sub["location_type"] = spec["location_type"]
        frames.append(sub)
        log(f"  {spec['location_type']:9s}  {len(sub):>7,} features")

    df_all = pd.concat(frames, ignore_index=True)
    df_all = gpd.GeoDataFrame(df_all, geometry="geometry",
                              crs=df_raw.crs or "EPSG:4326")

    if "building:levels" in df_all.columns:
        df_all["floors"] = pd.to_numeric(df_all["building:levels"],
                                          errors="coerce")
    else:
        df_all["floors"] = np.nan

    df_all = df_all[["geometry", "building", "amenity", "floors",
                     "location_type"]].copy()
    df_all["floors"] = (
        df_all["floors"].fillna(DEFAULT_FLOORS).clip(lower=1).astype(int)
    )
    return df_all


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    pbf = Path(args.pbf)
    vg250 = Path(args.vg250)
    out = Path(args.out)
    prefixes = [p.strip() for p in args.prefix.split(",") if p.strip()]

    if not pbf.exists():
        sys.stderr.write(f"PBF not found: {pbf}\n")
        return 2
    if not vg250.exists():
        sys.stderr.write(f"VG250 not found: {vg250}\n")
        return 2

    out.parent.mkdir(parents=True, exist_ok=True)

    log(f"Scope: loading VG250 Gemeinden with prefixes {prefixes} ...")
    df_zones = load_scope(vg250, prefixes)
    log(f"Scope: {len(df_zones)} Gemeinden in scope")

    scope_wgs84 = (
        df_zones.to_crs("EPSG:4326").geometry.union_all().buffer(0)
    )
    log(f"Scope: dissolved polygon envelope = {scope_wgs84.bounds}")

    # 1) Clip PBF (fast path; cached)
    working_pbf = pbf if args.no_clip else clip_pbf(
        pbf, scope_wgs84, Path(args.osmconvert)
    )

    # 2) Single pyrosm pass on clipped PBF
    df_raw = extract_all_buildings(working_pbf)

    # 3) In-memory tag filtering
    df = apply_filters(df_raw)

    # 4) Reproject → area on polygon → centroid
    df = df.to_crs(args.crs_out)
    df["area"] = np.abs(df.geometry.area).astype(float)
    df["geometry"] = df.geometry.centroid

    # 5) Precise polygon clip + commune_id attachment
    log("OSM: spatial join to VG250 Gemeinden ...")
    df = gpd.sjoin(
        df,
        df_zones.to_crs(args.crs_out)[["geometry", "commune_id", "iris_id"]],
        how="inner",
        predicate="within",
    ).drop(columns=["index_right"]).reset_index(drop=True)
    log(f"OSM: {len(df):,} after precise polygon clip")

    before = len(df)
    df = df.drop_duplicates(subset=["geometry", "location_type"])
    log(f"OSM: deduplicated {before - len(df)} rows")

    df = df[["geometry", "building", "amenity", "area", "floors",
             "location_type", "commune_id", "iris_id"]]

    comp = None if args.compression == "none" else args.compression
    log(f"OSM: writing {out} (compression={comp}) ...")
    df.to_parquet(out, compression=comp, index=False)

    meta = out.with_suffix(".meta.json")
    counts = df["location_type"].value_counts().to_dict()
    meta.write_text(json.dumps({
        "source": str(pbf),
        "clipped_from": (str(working_pbf) if working_pbf != pbf else None),
        "vg250": str(vg250),
        "prefixes": prefixes,
        "crs": args.crs_out,
        "feature_count": int(len(df)),
        "location_type_counts": {k: int(v) for k, v in counts.items()},
    }, indent=2), encoding="utf-8")

    size_mb = out.stat().st_size / (1024 * 1024)
    log(f"OSM: done. {out.name} = {size_mb:.1f} MB, counts = {counts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
