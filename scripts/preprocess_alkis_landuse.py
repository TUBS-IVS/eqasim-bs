"""
Preprocess ALKIS buildings (LGLN Hausumringe) and ATKIS landuse (LGLN FS_LN)
for the Braunschweig region.

Reduces the raw Niedersachsen-wide deliveries (1.7 GB Shapefile + 3.2 GB
GeoPackage) to the subset intersecting the configured ZGB scope and
converts them to zstd-compressed GeoParquet. This is a one-off step that
runs outside the synpp pipeline so it doesn't re-run on every cache
invalidation.

Outputs (relative to ``--out-root``):
    alkis_buildings.parquet   — one row per building polygon with AGS, GFK,
                                 area (m²) and centroid (geometry is stored
                                 as polygon to allow downstream re-filtering
                                 on geometry type or footprint size).
    alkis_buildings.meta.json — lookup of AGS prefixes used and GFK code
                                 → activity-type semantic map.
    landuse.parquet           — one row per landuse polygon with AGS, the
                                 ATKIS layer name and the mapped activity
                                 category. Only polygons intersecting the
                                 ZGB Kreise are retained.

Typical invocation:
    python scripts/preprocess_alkis_landuse.py \
        --raw-root eqasim-data/data/braunschweig \
        --vg250 eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip \
        --prefix 03101,03102,03103,03151,03153,03154,03157,03158 \
        --out-root eqasim-data/data/braunschweig/preprocessed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pyogrio

# ---------------------------------------------------------------------------
# ALKIS GFK (Gebäudefunktion) semantic mapping
# ---------------------------------------------------------------------------
# ALKIS/AAA Schlüsselliste "Gebäudefunktion". Codes are prefixed by Objektart
# (31001 = AX_Gebaeude, 51009 = AX_BauwerkOderAnlageFuerIndustrieUndGewerbe,
#  51002 = AX_HistorischesBauwerkOderHistorischeEinrichtung,
#  51003 = AX_TurmBauwerk).
#
# We collapse the fine-grained AAA catalogue to six coarse activity classes
# that match the Bavaria pipeline's location types (home / work / education
# / shop / leisure / other) plus 'residential' for pure housing and
# 'ancillary' (garages, sheds) which is explicitly excluded from sampling.
# ---------------------------------------------------------------------------

ALKIS_READ_COLUMNS = ["AGS", "OI", "GFK"]

GFK_ACTIVITY = {
    # Pure residential (31001_1xxx)
    "31001_1000": "residential",   # Wohngebäude
    "31001_1110": "residential",   # Wohnhaus
    "31001_1120": "residential",   # Wohnhaus mit Handel/Dienstleistungen
    "31001_1130": "residential",   # Wohngebäude mit Gemeinbedarf
    "31001_1210": "residential",   # Wohngebäude mit Gewerbe
    # Garages / ancillary (31001_2720-2740, 51009_1610)
    "31001_2720": "ancillary",     # Garage
    "31001_2740": "ancillary",     # Carport
    "51009_1610": "ancillary",     # Garage / Nebengebäude
    # Business / commerce (31001_2xxx default)
    "31001_2000": "work",
    "31001_2010": "work",
    "31001_2100": "shop",          # Handel & Dienstleistungen
    "31001_2130": "shop",
    "31001_2500": "other",         # Öffentliche Zwecke
    "31001_2600": "other",         # Verkehrszwecke
    "31001_2310": "education",     # Gebäude für Bildung und Forschung
    "31001_2320": "education",     # Kinderkrippe / Kindergarten
    "31001_2321": "education",     # Allgemeinbildende Schule
    "31001_2322": "education",     # Berufsbildende Schule
    "31001_2340": "education",     # Hort / Kindertagesstätte
    "31001_2410": "education",     # Hochschulgebäude
    "31001_2463": "leisure",       # Kirche, Kapelle
    "31001_2513": "other",
    # Industry / production (31001_3xxx)
    "31001_3000": "work",          # Industrie
    "31001_3021": "work",
    "31001_3022": "work",
    "31001_3023": "work",
    "31001_3024": "work",
    "31001_3034": "work",
    "31001_3041": "work",
    "31001_3043": "work",
    "31001_3051": "work",
    "31001_3065": "work",
    "31001_3072": "work",
    "31001_3100": "work",
    "31001_3200": "work",
    "31001_3211": "work",
    "31001_3281": "work",
    # Historic buildings (51002_xxxx) — usually leisure/tourism
    "51002_1220": "leisure",
    "51002_1230": "leisure",
    "51002_1250": "leisure",
    "51002_1260": "leisure",
    "51002_1290": "leisure",
    # Towers (51003_xxxx)
    "51003_1201": "leisure",
    "51003_1205": "leisure",
}


# ---------------------------------------------------------------------------
# ATKIS landuse layer → activity mapping (LGLN FS_LN_03)
# ---------------------------------------------------------------------------
# Source: https://www.lgln.niedersachsen.de (Flächenschema Landesnutzung).
# We map each layer to 0-3 eqasim activity categories.  A 'None' value means
# the layer is kept for spatial-inference / reference but doesn't feed any
# location sampler directly.
# ---------------------------------------------------------------------------

LANDUSE_ACTIVITY = {
    "ln_wohnnutzung":               "residential",
    "ln_gewerblichedienstleistungen": "work",
    "ln_industrieundverarbeitendesgewerbe": "work",
    "ln_landwirtschaft":            "work",
    "ln_forstwirtschaft":           "work",
    "ln_aquakulturundfischereiwirtschaft": "work",
    "ln_abbau":                     "work",
    "ln_lagerung":                  "work",
    "ln_oeffentlicheeinrichtungen": "education",   # includes Schulen/Hochschulen/Kliniken
    "ln_kulturundunterhaltung":     "leisure",
    "ln_freizeitanlage":            "leisure",
    "ln_sportanlage":               "leisure",
    "ln_freiluftundnaherholung":    "leisure",
    "ln_bestattung":                "other",
    "ln_versorgungundentsorgung":   "other",
    "ln_schutzanlage":              "other",
    "ln_schiffsverkehr":            "other",
    "ln_flugverkehr":               "other",
    "ln_bahnverkehr":               "other",
    "ln_strassenundwegeverkehr":    "other",
    "ln_wasserwirtschaft":          "other",
    "ln_ohnenutzung":               None,
}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--raw-root", default="eqasim-data/data/braunschweig",
                   help="Directory containing buildings/ and landuse/ subfolders with the raw zips.")
    p.add_argument("--vg250", default="eqasim-data/data/germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
                   help="Path to the VG250 zip (used to clip landuse polygons).")
    p.add_argument("--prefix", default="03101,03102,03103,03151,03153,03154,03157,03158",
                   help="Comma-separated AGS prefixes to keep (default = ZGB-8).")
    p.add_argument("--out-root", default="eqasim-data/data/braunschweig/preprocessed")
    p.add_argument("--skip-alkis", action="store_true")
    p.add_argument("--skip-landuse", action="store_true")
    p.add_argument("--compression", default="zstd", choices=["zstd", "snappy", "gzip", "none"])
    return p.parse_args(argv)


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------------------------------------------------------------------
# ALKIS preprocessing
# ---------------------------------------------------------------------------

def preprocess_alkis(raw_root: Path, out_root: Path, prefixes: list[str], compression: str) -> None:
    raw_zip = raw_root / "buildings" / "gebaeude-ni.zip"
    if not raw_zip.exists():
        raise FileNotFoundError(raw_zip)

    out_path = out_root / "alkis_buildings.parquet"
    meta_path = out_root / "alkis_buildings.meta.json"

    # Build SQL WHERE clause for pyogrio. OGR supports LIKE on strings.
    conditions = [f"AGS LIKE '{pref}%'" for pref in prefixes]
    where = " OR ".join(conditions)

    log(f"ALKIS: reading {raw_zip.name} with WHERE ({where}) ...")
    path_vsi = f"/vsizip/{raw_zip.as_posix()}/gebaeude-ni.shp"
    df = pyogrio.read_dataframe(
        path_vsi,
        columns=ALKIS_READ_COLUMNS,
        where=where,
    )
    df = gpd.GeoDataFrame(df, crs="EPSG:25832")
    log(f"ALKIS: {len(df):,} buildings in scope, {df['AGS'].nunique()} unique AGS")

    # Attributes
    df["area_m2"] = df.geometry.area.astype("float32")
    df["activity"] = df["GFK"].map(GFK_ACTIVITY).fillna("unknown").astype("category")
    df["AGS"] = df["AGS"].astype("category")
    df["OI"] = df["OI"].astype("category")
    df["GFK"] = df["GFK"].astype("category")

    out_root.mkdir(parents=True, exist_ok=True)
    log(f"ALKIS: writing {out_path} (compression={compression}) ...")
    comp = None if compression == "none" else compression
    df.to_parquet(out_path, compression=comp, index=False)

    meta = {
        "source": str(raw_zip),
        "prefixes": prefixes,
        "crs": "EPSG:25832",
        "feature_count": int(len(df)),
        "ags_count": int(df["AGS"].nunique()),
        "gfk_activity_map": GFK_ACTIVITY,
        "activity_counts": df["activity"].value_counts().to_dict(),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    size_mb = out_path.stat().st_size / (1024 * 1024)
    log(f"ALKIS: done. {out_path.name} = {size_mb:.1f} MB")


# ---------------------------------------------------------------------------
# Landuse preprocessing
# ---------------------------------------------------------------------------

def build_scope_geometry(vg250_path: Path, prefixes: list[str]) -> gpd.GeoDataFrame:
    """Load VG250 Gemeinden and dissolve the ones in ``prefixes`` to a single polygon."""
    log(f"Landuse: loading VG250 from {vg250_path.name} ...")

    path_vsi = (
        f"/vsizip/{vg250_path.as_posix()}/"
        "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg"
    )

    # Push the ARS filter down to OGR so we only read 120 rows, not ~11k.
    where = " OR ".join(f"ARS LIKE '{p}%'" for p in prefixes)
    df_gem = pyogrio.read_dataframe(
        path_vsi, layer="vg250_gem", columns=["ARS"], where=where
    )
    df_scope = gpd.GeoDataFrame(df_gem, crs="EPSG:25832")
    log(f"Landuse: scope covers {len(df_scope)} Gemeinden")

    # Dissolve to one scope polygon (buffer slightly to absorb topology noise).
    scope_geom = df_scope.geometry.union_all().buffer(0)
    return gpd.GeoDataFrame(
        {"scope": ["ZGB-8"]},
        geometry=[scope_geom],
        crs=df_scope.crs,
    )


def preprocess_landuse(raw_root: Path, vg250_path: Path, out_root: Path,
                       prefixes: list[str], compression: str) -> None:
    raw_zip = raw_root / "landuse" / "FS_LN_03_NI_260101.zip"
    if not raw_zip.exists():
        raise FileNotFoundError(raw_zip)

    out_path = out_root / "landuse.parquet"
    meta_path = out_root / "landuse.meta.json"

    df_scope = build_scope_geometry(vg250_path, prefixes)
    scope_bbox = tuple(df_scope.total_bounds)  # xmin,ymin,xmax,ymax
    log(f"Landuse: scope bbox = {scope_bbox}")

    # /vsizip/ on a 3 GB GPKG triggers very slow SQLite page-access. Extract
    # the gpkg once to a sidecar next to the zip (only if missing) so every
    # layer read becomes local random-access I/O.
    gpkg_cache = raw_zip.with_suffix(".gpkg")
    if not gpkg_cache.exists() or gpkg_cache.stat().st_size < 1_000_000_000:
        import shutil
        log(f"Landuse: extracting GPKG from {raw_zip.name} -> {gpkg_cache.name} (~3 GB) ...")
        with zipfile.ZipFile(raw_zip) as zf:
            members = [n for n in zf.namelist() if n.endswith(".gpkg")]
            if not members:
                raise RuntimeError(f"No .gpkg inside {raw_zip}")
            with zf.open(members[0]) as src, open(gpkg_cache, "wb") as dst:
                shutil.copyfileobj(src, dst, length=16 * 1024 * 1024)
        log(f"Landuse: extracted {gpkg_cache.stat().st_size/1024/1024:.0f} MB")

    path_vsi = str(gpkg_cache)
    layers = pyogrio.list_layers(path_vsi)
    log(f"Landuse: scanning {len(layers)} layers ...")

    combined = []
    for (layer_name, _geom_type) in layers:
        activity = LANDUSE_ACTIVITY.get(layer_name)
        if activity is None and layer_name != "ln_ohnenutzung":
            log(f"  ! layer '{layer_name}' not in LANDUSE_ACTIVITY — kept with activity=None")

        try:
            df_lyr = pyogrio.read_dataframe(
                path_vsi,
                layer=layer_name,
                bbox=scope_bbox,
            )
        except Exception as exc:  # pragma: no cover
            log(f"  ! layer '{layer_name}' failed: {exc}")
            continue

        if len(df_lyr) == 0:
            continue

        df_lyr = gpd.GeoDataFrame(df_lyr, crs="EPSG:25832")

        # Precise clip via intersects with scope polygon (bbox is an over-filter).
        df_lyr = gpd.sjoin(
            df_lyr, df_scope[["geometry"]], predicate="intersects", how="inner"
        ).drop(columns=["index_right"])

        if len(df_lyr) == 0:
            continue

        df_lyr["layer"] = layer_name
        df_lyr["activity"] = activity

        # Keep only a small, homogenous subset of attributes to avoid layer-
        # specific column chaos in the combined parquet.  Any layer-specific
        # attributes are concatenated into a JSON string for forensic use.
        keep_cols = {"geometry", "layer", "activity"}
        extra_cols = [c for c in df_lyr.columns if c not in keep_cols]
        if extra_cols:
            df_lyr["attrs_json"] = df_lyr[extra_cols].apply(
                lambda r: json.dumps({k: (None if pd.isna(v) else str(v))
                                      for k, v in r.items()},
                                     ensure_ascii=False),
                axis=1,
            )
            df_lyr = df_lyr[list(keep_cols) + ["attrs_json"]]
        else:
            df_lyr["attrs_json"] = None

        df_lyr["area_m2"] = df_lyr.geometry.area.astype("float32")

        combined.append(df_lyr)
        log(f"  {layer_name:38s}  {len(df_lyr):>8,} polygons  activity={activity}")

    if not combined:
        raise RuntimeError("No landuse polygons remaining after clipping to scope")

    df_all = gpd.GeoDataFrame(pd.concat(combined, ignore_index=True), crs="EPSG:25832")
    df_all["layer"] = df_all["layer"].astype("category")
    df_all["activity"] = df_all["activity"].astype("category")

    out_root.mkdir(parents=True, exist_ok=True)
    log(f"Landuse: writing {out_path} (compression={compression}) ...")
    comp = None if compression == "none" else compression
    df_all.to_parquet(out_path, compression=comp, index=False)

    meta = {
        "source": str(raw_zip),
        "vg250": str(vg250_path),
        "prefixes": prefixes,
        "crs": "EPSG:25832",
        "feature_count": int(len(df_all)),
        "layer_activity_map": LANDUSE_ACTIVITY,
        "layer_counts": df_all["layer"].value_counts().to_dict(),
        "activity_counts": df_all["activity"].value_counts(dropna=False).to_dict(),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    size_mb = out_path.stat().st_size / (1024 * 1024)
    log(f"Landuse: done. {out_path.name} = {size_mb:.1f} MB")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    raw_root = Path(args.raw_root)
    out_root = Path(args.out_root)
    vg250 = Path(args.vg250)
    prefixes = [p.strip() for p in args.prefix.split(",") if p.strip()]

    if not args.skip_alkis:
        preprocess_alkis(raw_root, out_root, prefixes, args.compression)

    if not args.skip_landuse:
        preprocess_landuse(raw_root, vg250, out_root, prefixes, args.compression)

    return 0


if __name__ == "__main__":
    sys.exit(main())
