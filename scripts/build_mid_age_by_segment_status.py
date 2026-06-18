"""Build the MiD 2023 vehicle-age-by-segment-status derived table.

Source: MiD 2023 Autos micro-data CSV (``MiD2023_Autos.csv``, 290930 rows).

Output:
    eqasim-data/data/braunschweig/kba/derived/mid2023_age_by_segment_status.csv
    columns: segment, status, age_band, share, base_weighted

where ``share = P(age_band | segment, status)`` (within each (segment, status)
cell the shares sum to 1.0) and ``base_weighted`` is the A_GEW-weighted vehicle
base of that cell.

seg_kba (fine code) -> SEGMENT_LABELS mapping
---------------------------------------------
Derived from MiD2023_Codepläne_B1_Standard_v1.1.xlsx, sheet "Autos", variable
"seg_kba" (Pkw-Segmentierung nach KBA), cross-checked against
kba_segment_powertrain.csv national segment shares:

    1  -> minis
    2  -> kleinwagen
    3  -> kompaktklasse
    4  -> mittelklasse
    5  -> obere_mittelklasse
    6  -> oberklasse
    7  -> suv           (MiD label: "Sportgeländewagen", KBA label: "SUVs")
    8  -> gelaendewagen (MiD label: "Geländewagen")
    9  -> sportwagen
   10  -> mini_vans     (MiD label: "Mini-Vans")
   11  -> grossraum_vans (MiD label: "Großraum-Vans")
   12  -> utilities
   95  -> sonstige      (MiD label: "nicht zuzuordnen")

Note: "wohnmobile" has no seg_kba code in the MiD Autos file (MiD does not
report Wohnmobile), so that segment has no rows in this table.

oek_status (1..5) -> STATUS_LABELS mapping (MiD Codeplan, variable "oek_status"):
    1 -> very_low  (sehr niedrig)
    2 -> low       (niedrig)
    3 -> medium    (mittel)
    4 -> high      (hoch)
    5 -> very_high (sehr hoch)

Age bands (KBA FZ 27.7 scheme, vehicle age = 2023 - A_BAUJ):
    under_5    :  0 ..  4 yr  (built 2019-2023)
    5_to_9     :  5 ..  9 yr  (built 2014-2018)
    10_to_14   : 10 .. 14 yr  (built 2009-2013)
    15_to_19   : 15 .. 19 yr  (built 2004-2008)
    20_to_24   : 20 .. 24 yr  (built 1999-2003)
    25_to_29   : 25 .. 29 yr  (built 1994-1998)
    30_plus    : 30+  yr       (built <= 1993)

Rows with A_BAUJ outside [1980, 2023] (e.g. 9999 = unknown) are dropped.
The lower bound 1980 is conservative; only 1453 rows with A_BAUJ in [1900,1979]
exist (0.5 %) — keeping them would require an 8th "40_plus" band not in the
KBA scheme; dropping them is logged.

Regenerate with:
    python scripts/build_mid_age_by_segment_status.py [--mid-path PATH] [--git-add]

Provenance: MiD 2023 (BMDV / infas), B1 (person-level) Datensatzpaket,
Autos micro-data, vehicle weight A_GEW. Processed 2026-06.
"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical label sets (must stay identical to fleet_tables.py)
# ---------------------------------------------------------------------------
SEGMENT_LABELS: tuple[str, ...] = (
    "minis", "kleinwagen", "kompaktklasse", "mittelklasse", "obere_mittelklasse",
    "oberklasse", "suv", "gelaendewagen", "sportwagen", "mini_vans",
    "grossraum_vans", "utilities", "wohnmobile", "sonstige",
)

STATUS_LABELS: tuple[str, ...] = (
    "very_low", "low", "medium", "high", "very_high",
)

AGE_BAND_LABELS: tuple[str, ...] = (
    "under_5", "5_to_9", "10_to_14", "15_to_19", "20_to_24", "25_to_29",
    "30_plus",
)

# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------
# seg_kba integer code -> canonical segment label.
# Source: MiD2023_Codepläne_B1_Standard_v1.1.xlsx, sheet "Autos", variable
# seg_kba.  Code 7 = "Sportgeländewagen" = our "suv" (same as MID_SEGMENT_MAP
# in extract_kba_fleet.py).  Code 95 = "nicht zuzuordnen" = sonstige.
# No code exists for wohnmobile in MiD Autos.
SEG_KBA_MAP: dict[int, str] = {
    1: "minis",
    2: "kleinwagen",
    3: "kompaktklasse",
    4: "mittelklasse",
    5: "obere_mittelklasse",
    6: "oberklasse",
    7: "suv",           # MiD: Sportgeländewagen
    8: "gelaendewagen",
    9: "sportwagen",
    10: "mini_vans",
    11: "grossraum_vans",
    12: "utilities",
    95: "sonstige",     # MiD: nicht zuzuordnen
}

# oek_status integer -> canonical status label.
OEK_STATUS_MAP: dict[int, str] = {
    1: "very_low",
    2: "low",
    3: "medium",
    4: "high",
    5: "very_high",
}

# Age-band bin edges (left-closed, right-open), aligned to KBA FZ 27.7.
# vehicle_age = 2023 - A_BAUJ (integer years, reference year = survey year).
_AGE_BINS = [0, 5, 10, 15, 20, 25, 30, float("inf")]
_AGE_LABELS = list(AGE_BAND_LABELS)  # 7 bands

SURVEY_YEAR = 2023

# A_BAUJ range to keep: vehicles built before 1980 require a "40_plus" band
# not in the KBA scheme; vehicles with A_BAUJ == 9999 (unknown) are dropped.
_BAUJ_MIN = 1980
_BAUJ_MAX = SURVEY_YEAR  # age >= 0; built no later than survey year


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build(mid_path: Path) -> pd.DataFrame:
    """Read MiD Autos CSV, derive the age-by-segment-status table.

    Returns a tidy DataFrame with columns:
        segment, status, age_band, share, base_weighted.
    """
    logger.info("[build_mid_age_by_segment_status] reading %s", mid_path)
    df = pd.read_csv(mid_path, usecols=["A_BAUJ", "A_GEW", "seg_kba", "oek_status"])
    n_raw = len(df)

    # --- filter A_BAUJ ---
    mask_valid = df["A_BAUJ"].between(_BAUJ_MIN, _BAUJ_MAX)
    n_dropped = (~mask_valid).sum()
    df = df[mask_valid].copy()
    logger.info(
        "[build_mid_age_by_segment_status] A_BAUJ filter [%d, %d]: "
        "kept %d / %d rows (dropped %d, %.2f%%)",
        _BAUJ_MIN, _BAUJ_MAX, len(df), n_raw,
        n_dropped, 100.0 * n_dropped / n_raw,
    )

    # --- vehicle age & age band ---
    df["vehicle_age"] = SURVEY_YEAR - df["A_BAUJ"]
    df["age_band"] = pd.cut(
        df["vehicle_age"],
        bins=_AGE_BINS,
        labels=_AGE_LABELS,
        right=False,
    )

    # --- segment ---
    df["segment"] = df["seg_kba"].map(SEG_KBA_MAP)
    unmapped_seg = df["segment"].isna().sum()
    if unmapped_seg > 0:
        bad_codes = df.loc[df["segment"].isna(), "seg_kba"].value_counts()
        logger.warning(
            "[build_mid_age_by_segment_status] %d rows with unmapped seg_kba "
            "(dropped): %s",
            unmapped_seg, bad_codes.to_dict(),
        )
        df = df.dropna(subset=["segment"])

    # --- status ---
    df["status"] = df["oek_status"].map(OEK_STATUS_MAP)
    unmapped_sta = df["status"].isna().sum()
    if unmapped_sta > 0:
        bad_codes = df.loc[df["status"].isna(), "oek_status"].value_counts()
        logger.warning(
            "[build_mid_age_by_segment_status] %d rows with unmapped oek_status "
            "(dropped): %s",
            unmapped_sta, bad_codes.to_dict(),
        )
        df = df.dropna(subset=["status"])

    # --- weighted aggregation ---
    # base_weighted = total A_GEW weight per (segment, status)
    base = (
        df.groupby(["segment", "status"], observed=True)["A_GEW"]
        .sum()
        .rename("base_weighted")
        .reset_index()
    )

    # weighted count per (segment, status, age_band)
    counts = (
        df.groupby(["segment", "status", "age_band"], observed=True)["A_GEW"]
        .sum()
        .rename("weighted_count")
        .reset_index()
    )

    merged = counts.merge(base, on=["segment", "status"], how="left")
    merged["share"] = merged["weighted_count"] / merged["base_weighted"]

    # Round to sensible precision
    merged["share"] = merged["share"].round(8)
    merged["base_weighted"] = merged["base_weighted"].round(3)

    tidy = (
        merged[["segment", "status", "age_band", "share", "base_weighted"]]
        .sort_values(["segment", "status", "age_band"])
        .reset_index(drop=True)
    )

    # Sanity: check shares sum to 1 per (segment, status)
    totals = tidy.groupby(["segment", "status"])["share"].sum()
    bad = totals[abs(totals - 1.0) > 1e-4]
    if len(bad) > 0:
        raise RuntimeError(
            f"share does not sum to 1.0 for (segment, status) cells: {bad}"
        )

    logger.info(
        "[build_mid_age_by_segment_status] built %d rows "
        "(%d segment x status x age_band cells)",
        len(tidy), len(tidy),
    )
    return tidy


def _print_gradient(tidy: pd.DataFrame) -> None:
    """Print the P(age_band | status) table pooled over segments."""
    print("\n[build_mid_age_by_segment_status] P(age_band | status) pooled over segments:")
    pivot = (
        tidy.assign(wshare=tidy["share"] * tidy["base_weighted"])
        .groupby(["status", "age_band"])[["wshare", "base_weighted"]].sum()
        .reset_index()
    )
    pivot["p"] = pivot["wshare"] / pivot.groupby("status")["base_weighted"].transform("sum")
    table = pivot.pivot(index="age_band", columns="status", values="p")
    # reorder columns low -> high
    ordered_cols = [s for s in STATUS_LABELS if s in table.columns]
    ordered_rows = [b for b in AGE_BAND_LABELS if b in table.index]
    table = table.loc[ordered_rows, ordered_cols]
    print(table.round(4).to_string())
    print()

    # Print under_5 gradient
    under5 = table.loc["under_5"]
    print("P(under_5 | status) gradient:")
    for s in ordered_cols:
        print(f"  {s:20s}: {under5[s]:.4f}")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

_DEFAULT_MID_PATH = (
    Path(r"C:\Users\bienzeisler\Documents\GitHub\popsimprep")
    / "inputs" / "MiD2023" / "MiD2023_B1_Datensatzpaket" / "CSV"
    / "MiD2023_Autos.csv"
)

_DEFAULT_DATA_PATH = REPO / "eqasim-data" / "data"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mid-path",
        default=str(_DEFAULT_MID_PATH),
        help="Path to MiD2023_Autos.csv (default: %(default)s).",
    )
    parser.add_argument(
        "--data-path",
        default=str(_DEFAULT_DATA_PATH),
        help="Root data path for eqasim-data (default: %(default)s).",
    )
    parser.add_argument(
        "--git-add",
        action="store_true",
        help="Force-add the generated CSV to git (git add -f).",
    )
    args = parser.parse_args()

    mid_path = Path(args.mid_path)
    if not mid_path.exists():
        raise FileNotFoundError(f"MiD Autos CSV not found: {mid_path}")

    tidy = build(mid_path)
    _print_gradient(tidy)

    out_dir = Path(args.data_path) / "braunschweig" / "kba" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "mid2023_age_by_segment_status.csv"

    header = (
        "# MiD 2023 vehicle-age band x segment x economic status.\n"
        "# Generated by scripts/build_mid_age_by_segment_status.py from\n"
        "# MiD2023_Autos.csv (B1 Datensatzpaket, A_GEW-weighted).\n"
        "# share = P(age_band | segment, status); sums to 1.0 per (segment, status).\n"
        "# base_weighted = A_GEW-weighted vehicle count of the (segment, status) cell.\n"
        "# seg_kba mapping: 1=minis 2=kleinwagen 3=kompaktklasse 4=mittelklasse\n"
        "#   5=obere_mittelklasse 6=oberklasse 7=suv 8=gelaendewagen 9=sportwagen\n"
        "#   10=mini_vans 11=grossraum_vans 12=utilities 95=sonstige\n"
        "# oek_status mapping: 1=very_low 2=low 3=medium 4=high 5=very_high\n"
        "# Rows with A_BAUJ outside [1980, 2023] (incl. 9999=unknown) dropped.\n"
    )

    with open(out_csv, "w", encoding="utf-8", newline="") as fh:
        fh.write(header)
        tidy.to_csv(fh, index=False)

    print(f"[build_mid_age_by_segment_status] wrote {out_csv} ({len(tidy)} rows)")

    if args.git_add:
        subprocess.run(["git", "add", "-f", str(out_csv)], cwd=str(REPO), check=True)
        print(f"[build_mid_age_by_segment_status] git add -f {out_csv}")


if __name__ == "__main__":
    main()
