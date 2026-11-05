#!/usr/bin/env python
"""Trace the Gemeinde label "NAN" of the 100 % production run to its municipality (issue C5b, 2026-11-04).

Read-only. Four parts; each runs only when its inputs are given, so the parts that need the run's cache and
the part that needs the newer BBSR file can run on different machines.

A. (--cache, --vg250, --regiostar-2020) The fleet cars of the cached household output of ``prod100_20261031``
   that carry the label NAN, per home commune code: cars, official name (VG250) and whether the code is in
   the RegioStaR 2020 sheet.
B. (--cache, --vg250) The cached output of the RegioStaR stage: rows with an empty name.
C. (--regiostar-2020) The RegioStaR 2020 sheet, Kreis 03153: every code with its name and RegioStaR-7, and
   how many rows of the whole sheet have an empty name.
D. (--regiostar-2024) The newer BBSR file: Langelsheim under the old and the new code, per Gebietsstand sheet.

Usage (run isolated; the paths of the inputs are arguments, nothing is written)::

    python -I check_langelsheim_label.py --cache ~/prod100_20261031/cache \\
        --regiostar-2020 <regiostar_referenzdatei.xlsx> --vg250 <vg250-ew_12-31.utm32s.gpkg.ebenen.zip>
    python -I check_langelsheim_label.py --regiostar-2024 "<2024 RegioStaR-Referenzdateien.xlsx>"
"""

from __future__ import annotations

import argparse
import contextlib
import glob
import io
import pickle
import sys
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")


@contextlib.contextmanager
def _quiet():
    """Swallow the stray stdout print of openpyxl 3.1.0 (a sheet title) while a workbook is read.

    It says nothing about the cells; exceptions pass through unchanged.
    """
    with contextlib.redirect_stdout(io.StringIO()):
        yield

HOUSEHOLD = "braunschweig.synthesis.vehicles.cars.household__6b6784a39cfbf5cdf97abeb2ac00d4ce.p"
HOME = "braunschweig.synthesis.locations.home_cell__7d76184709d2b6fcaf0735a52d801a42.p"
SHEET_2020 = "ReferenzGebietsstand2020"
LANGELSHEIM_CODES = {"3153007", "03153007", "3153019", "03153019"}


def ags8_of(commune_id) -> str:
    """The 8-digit AGS of a 12-digit ARS (the RegioStaR key); an 8-digit input is returned unchanged."""
    text = str(commune_id)
    return text[0:5] + text[9:12] if len(text) == 12 else text


def official_names(vg250_zip: str) -> dict:
    import geopandas as gpd

    layer = "/vsizip/" + vg250_zip + "/vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg"
    gem = gpd.read_file(layer, layer="vg250_gem", columns=["AGS", "GEN"])
    return dict(zip(gem["AGS"].astype(str).str.zfill(8), gem["GEN"]))


def sheet_codes(path: str) -> set:
    with _quiet():
        sheet = pd.read_excel(path, sheet_name=SHEET_2020, header=0)
    return set(sheet["gem_20"].astype("Int64").astype(str).str.zfill(8))


def part_a(cache: Path, vg250: str, regiostar_2020: str) -> None:
    official = official_names(vg250)
    in_sheet = sheet_codes(regiostar_2020)
    with open(cache / HOUSEHOLD, "rb") as handle:
        _types, vehicles = pickle.load(handle)
    fleet = vehicles[vehicles["type_id"] != "default_car"]
    nan_cars = fleet[fleet["gemeinde"].astype(str) == "NAN"]
    print("A. fleet cars %d, cars labelled NAN: %d" % (len(fleet), len(nan_cars)))
    with open(cache / HOME, "rb") as handle:
        homes = pickle.load(handle)[["household_id", "commune_id"]].drop_duplicates("household_id")
    joined = nan_cars.merge(homes, on="household_id", how="left")
    joined["ags8"] = joined["commune_id"].map(ags8_of)
    for (commune_id, ags8), count in joined.groupby(["commune_id", "ags8"]).size().items():
        print("   home commune %s (ags8 %s): %d cars | official name (VG250) %r | ags8 in the RegioStaR 2020 sheet: %s"
              % (commune_id, ags8, count, official.get(ags8), ags8 in in_sheet))
    print("   NAN cars without a home commune in the home-cell output: %d" % int(joined["commune_id"].isna().sum()))


def part_b(cache: Path, vg250: str) -> None:
    official = official_names(vg250)
    stage_files = sorted(glob.glob(str(cache / "braunschweig.data.bbsr.regiostar__*.p")))
    with open(stage_files[-1], "rb") as handle:
        stage = pickle.load(handle)
    empty = stage[stage["name"].isna() | (stage["name"].astype(str).str.lower() == "nan")]
    print("B. cached RegioStaR stage output (%d file(s) in the cache): %d rows, %d with an empty name"
          % (len(stage_files), len(stage), len(empty)))
    for _, row in empty.iterrows():
        print("   ags8 %s: official name (VG250) %r, RegioStaR-7 %s" % (
            row["commune_id"], official.get(row["commune_id"]), row["regiostar7"]))


def part_c(regiostar_2020: str) -> None:
    with _quiet():
        raw = pd.read_excel(regiostar_2020, sheet_name=SHEET_2020, header=0)
    codes = raw["gem_20"].astype("Int64").astype(str).str.zfill(8)
    kreis = raw[codes.str.startswith("03153")]
    print("C. RegioStaR 2020 sheet, Kreis 03153: %d codes" % len(kreis))
    for index, row in kreis.iterrows():
        name = None if pd.isna(row["name_20"]) else row["name_20"]
        print("   %s: %r, RegioStaR-7 %s" % (codes[index], name, row["RegioStaR7"]))
    print("   rows of the whole sheet with an empty name_20: %d of %d" % (int(raw["name_20"].isna().sum()), len(raw)))


def part_d(regiostar_2024: str) -> None:
    with _quiet():
        book = pd.ExcelFile(regiostar_2024)
    print("D. newer BBSR file, Langelsheim per Gebietsstand sheet:")
    for sheet in book.sheet_names:
        if not sheet.startswith("ReferenzGebietsstand"):
            continue
        with _quiet():
            frame = pd.read_excel(regiostar_2024, sheet_name=sheet, header=0, dtype=str)
        code_columns = [c for c in frame.columns
                        if str(c).lower().startswith("gem") and "rs" not in str(c).lower()
                        and "name" not in str(c).lower()]
        if not code_columns:
            continue
        hits = frame[frame[code_columns].apply(lambda col: col.str.strip().isin(LANGELSHEIM_CODES)).any(axis=1)]
        name_columns = [c for c in frame.columns if "name" in str(c).lower()]
        type_columns = [c for c in frame.columns if str(c).lower() == "regiostar7"]
        for _, row in hits.iterrows():
            code = next(row[c] for c in code_columns if str(row[c]).strip() in LANGELSHEIM_CODES)
            print("   %s: code %s, name %r, RegioStaR-7 %s" % (
                sheet, code, row[name_columns[0]] if name_columns else None,
                row[type_columns[0]] if type_columns else "(no such column in this sheet)"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--vg250")
    parser.add_argument("--regiostar-2020", dest="regiostar_2020")
    parser.add_argument("--regiostar-2024", dest="regiostar_2024")
    args = parser.parse_args()
    if args.cache and args.vg250 and args.regiostar_2020:
        part_a(args.cache, args.vg250, args.regiostar_2020)
    if args.cache and args.vg250:
        part_b(args.cache, args.vg250)
    if args.regiostar_2020:
        part_c(args.regiostar_2020)
    if args.regiostar_2024:
        part_d(args.regiostar_2024)


if __name__ == "__main__":
    main()
