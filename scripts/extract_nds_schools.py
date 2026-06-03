"""Build the committed NDS school facilities CSV for the education gravity model.

One-time extraction (run on a machine with internet for the geocoding step). The
synpp pipeline consumes ONLY the committed CSV, never this script.

Pipeline:
  1. read ABS + BBS xlsx (LSN Niedersachsen)
  2. type + scope to ZGB-8 via braunschweig.data.schools.readers
  3. geocode each unique 'street, plz city' via OSM Nominatim (1 req/s, cached)
  4. validate offline against OSM education POIs (osm_pois.parquet): distance to
     nearest education feature; PLZ-centroid fallback + flag on geocode miss
  5. write nds_schools_zgb.csv (EPSG:25832) + README.md + DATA_FLOW.md

Sources:
  ABS: Schulverzeichnis_ABS_2025.xlsx (LSN, sheet "Schulverzeichnis_2025", header row 7)
  BBS: Verzeichnis_der_BBS_2024.xlsx (LSN, sheet "Verzeichnis BBS 2024", header row 2)

Column name notes (confirmed against real files 2026-06-03):
  ABS street column is "Straße" (umlaut); renamed to "Strasse" before passing to readers.
  BBS school number column is "Schul-\\nnummer" (embedded newline); renamed to "Schulnummer".
  BBS name column is "Schulbezeichnung (Teil 1)"; renamed to "Schulbezeichnung".
  BBS AGS column is "Allgemeiner Gemeinde-\\nschlüssel" (embedded newline + umlaut);
      renamed to "AGS". The AGS is 6 digits; first 3 chars = LSN Kreis code.
  BBS pupil columns: all columns whose name starts with "Anzahl" (handles the
      duplicated ".1", ".2", ... suffixes pandas appends for repeated headers).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from braunschweig.data.schools.readers import build_schools_long  # noqa: E402

# Eight ZGB Kreise (5-digit official AGS Kreis codes).
SCOPE = ["03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"]

# Projected CRS used throughout the eqasim-bs pipeline (UTM zone 32N, metre unit).
CRS_METRIC = "EPSG:25832"

# Maximum distance to the nearest OSM education feature for a geocoded point to
# be considered validated. Points beyond this threshold receive validated=False
# and should be reviewed manually before using the CSV in the pipeline.
VALIDATION_RADIUS_M = 750.0


def read_abs_raw(path):
    """Load the ABS school directory sheet and normalise column names.

    Drops header/blank rows that have no AGS or SNR value (which appear above
    and below the actual data block in the LSN xlsx layout).

    Real column labels confirmed against Schulverzeichnis_ABS_2025.xlsx:
      "Straße" (with umlaut) -> renamed to "Strasse" for ASCII-safe downstream use.
    All other columns used by readers.build_abs_long are present verbatim:
    Kreis, AGS, SNR, SGL1..SGL6, Sch1..Sch6, Schulname, PLZ, Ort.
    """
    raw = pd.read_excel(path, sheet_name="Schulverzeichnis_2025", header=7)
    rename = {
        # "Straße" is the literal xlsx header; rename to ASCII identifier used
        # by readers.build_abs_long (which expects column "Strasse").
        "Straße": "Strasse",
    }
    raw = raw.rename(columns=rename)
    raw = raw[raw["AGS"].notna() & raw["SNR"].notna()].copy()
    return raw


def read_bbs_raw(path):
    """Load the BBS school directory sheet and normalise column names.

    Real column labels confirmed against Verzeichnis_der_BBS_2024.xlsx:
      "Schul-\\nnummer"                       -> "Schulnummer"
      "Schulbezeichnung (Teil 1)"             -> "Schulbezeichnung"
      "Stra\\u00dfe"                           -> "Strasse"
      "Allgemeiner Gemeinde-\\nschl\\u00fcssel" -> "AGS"

    The AGS in the BBS file is 6 digits (LSN internal); the 5-digit official
    Kreis code is derived as '03' + AGS[:3] (Niedersachsen prefix).
    Pupil count columns are all columns whose pandas name starts with "Anzahl"
    (handles the ".1", ".2", ... deduplication suffixes).

    Returns (raw_df, pupil_cols).
    """
    raw = pd.read_excel(path, sheet_name="Verzeichnis BBS 2024", header=2)
    pupil_cols = [c for c in raw.columns if str(c).startswith("Anzahl")]
    rename = {
        # Embedded newline in the xlsx header cell.
        "Schul-\nnummer": "Schulnummer",
        # BBS uses "Teil 1" (not "Zeile 1") for the primary name field.
        "Schulbezeichnung (Teil 1)": "Schulbezeichnung",
        # Umlaut in street column, same as ABS.
        "Straße": "Strasse",
        # Embedded newline + umlaut in the AGS column header.
        "Allgemeiner Gemeinde-\nschlüssel": "AGS",
    }
    raw = raw.rename(columns=rename)
    # LSN 3-digit Kreis = first three characters of the zero-padded 6-digit AGS.
    raw["Kreis"] = raw["AGS"].astype(str).str.zfill(6).str[:3]
    raw = raw[raw["AGS"].notna() & raw["Schulnummer"].notna()].copy()
    return raw, pupil_cols


def geocode_addresses(addresses, cache_path):
    """Geocode unique address strings via Nominatim at 1 req/s with an on-disk
    JSON cache.

    Each address string should be of the form "street, plz city, Germany".
    Returns {address: (lon, lat) | None}.  Results are written to the cache
    after every request so a crashed run can be resumed without re-geocoding
    already resolved addresses.
    """
    from geopy.geocoders import Nominatim

    cache = {}
    if os.path.exists(cache_path):
        with open(cache_path, "r", encoding="utf-8") as fh:
            cache = json.load(fh)

    geolocator = Nominatim(user_agent="eqasim-bs-school-geocoder")
    for addr in addresses:
        if addr in cache:
            continue
        try:
            loc = geolocator.geocode(addr, country_codes="de", timeout=10)
            cache[addr] = [loc.longitude, loc.latitude] if loc else None
        except Exception as exc:
            print(f"[extract_nds_schools] geocode failed for {addr!r}: {exc}")
            cache[addr] = None
        time.sleep(1.0)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=0)
    return {a: (tuple(v) if v else None) for a, v in cache.items()}


def validate_against_osm(gdf_schools, osm_pois_path):
    """Add dist_to_osm_edu_m (nearest OSM education feature) and validated flag.

    Points within VALIDATION_RADIUS_M metres of any OSM education feature are
    marked validated=True. All others should be inspected before the CSV is
    used as a pipeline input.
    """
    pois = gpd.read_parquet(osm_pois_path)
    edu = pois[pois["location_type"] == "education"].to_crs(CRS_METRIC)
    joined = gpd.sjoin_nearest(
        gdf_schools.to_crs(CRS_METRIC), edu[["geometry"]],
        how="left", distance_col="dist_to_osm_edu_m",
    )
    joined = joined[~joined.index.duplicated(keep="first")]
    gdf_schools = gdf_schools.copy()
    gdf_schools["dist_to_osm_edu_m"] = joined["dist_to_osm_edu_m"].values
    gdf_schools["validated"] = gdf_schools["dist_to_osm_edu_m"] < VALIDATION_RADIUS_M
    return gdf_schools


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abs", required=True, type=Path,
                        help="Path to Schulverzeichnis_ABS_2025.xlsx")
    parser.add_argument("--bbs", required=True, type=Path,
                        help="Path to Verzeichnis_der_BBS_2024.xlsx")
    parser.add_argument("--osm-pois", required=True, type=Path,
                        help="Path to osm_pois.parquet (eqasim preprocessed cache)")
    parser.add_argument("--out-dir", required=True, type=Path,
                        help="Output directory (created if absent)")
    parser.add_argument("--offline-check", action="store_true",
                        help="Build and print the typed long frame only; "
                             "skip geocoding, OSM validation, and CSV write.")
    args = parser.parse_args()

    abs_raw = read_abs_raw(args.abs)
    bbs_raw, pupil_cols = read_bbs_raw(args.bbs)
    df = build_schools_long(abs_raw, bbs_raw, SCOPE, bbs_pupil_cols=pupil_cols)
    print(
        f"[extract_nds_schools] {len(df)} (school,level) rows in ZGB-8; "
        f"levels: {df['level'].value_counts().to_dict()}; "
        f"capacity by level: "
        f"{df.groupby('level')['capacity'].sum().round(0).astype(int).to_dict()}"
    )

    if args.offline_check:
        print("[extract_nds_schools] offline check only; skipping geocode.")
        return

    df["address"] = (
        df["street"] + ", " + df["plz"] + " " + df["city"] + ", Germany"
    )
    args.out_dir.mkdir(parents=True, exist_ok=True)
    coords = geocode_addresses(
        sorted(df["address"].unique()),
        cache_path=str(args.out_dir / "geocode_cache.json"),
    )
    df["lonlat"] = df["address"].map(coords)
    n_missing = int(df["lonlat"].isna().sum())
    print(f"[extract_nds_schools] {n_missing} address(es) not resolved by Nominatim; "
          "falling back to PLZ centroid.")

    if n_missing:
        plz_addr = (
            df.loc[df["lonlat"].isna(), "plz"]
            + " "
            + df.loc[df["lonlat"].isna(), "city"]
            + ", Germany"
        )
        plz_coords = geocode_addresses(
            sorted(plz_addr.unique()),
            cache_path=str(args.out_dir / "geocode_cache_plz.json"),
        )
        df.loc[df["lonlat"].isna(), "lonlat"] = plz_addr.map(plz_coords).values
        df["geocode_quality"] = "address"
        df.loc[df["address"].map(coords).isna(), "geocode_quality"] = "plz_centroid"
    else:
        df["geocode_quality"] = "address"

    # Rows that could not be geocoded even at PLZ level are dropped with a warning.
    n_still_missing = int(df["lonlat"].isna().sum())
    if n_still_missing:
        print(f"[extract_nds_schools] WARNING: {n_still_missing} row(s) could not "
              "be geocoded at PLZ level either; these are dropped from the output.")
    df = df[df["lonlat"].notna()].copy()

    df["geometry"] = df["lonlat"].apply(lambda t: Point(t[0], t[1]))
    gdf = gpd.GeoDataFrame(df, geometry="geometry", crs="EPSG:4326").to_crs(CRS_METRIC)
    gdf["x"] = gdf.geometry.x
    gdf["y"] = gdf.geometry.y

    gdf = validate_against_osm(gdf, args.osm_pois)
    n_validated = int(gdf["validated"].sum())
    print(
        f"[extract_nds_schools] geocode_quality: "
        f"{gdf['geocode_quality'].value_counts().to_dict()}; "
        f"validated (<{VALIDATION_RADIUS_M:.0f} m to OSM edu): "
        f"{n_validated}/{len(gdf)}"
    )
    if n_validated < len(gdf):
        n_unvalidated = len(gdf) - n_validated
        print(f"[extract_nds_schools] WARNING: {n_unvalidated} row(s) exceed the "
              f"OSM validation radius of {VALIDATION_RADIUS_M:.0f} m; inspect "
              "manually before treating these as pipeline-ready.")

    out_cols = [
        "school_id", "name", "level", "capacity", "ags8", "kreis5",
        "x", "y", "geocode_quality", "dist_to_osm_edu_m", "validated",
    ]
    out_path = args.out_dir / "nds_schools_zgb.csv"
    gdf[out_cols].to_csv(out_path, index=False)
    print(f"[extract_nds_schools] wrote {out_path}")


if __name__ == "__main__":
    main()
