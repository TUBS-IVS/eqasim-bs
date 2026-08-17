"""Build the MiD 2023 vehicle-powertrain-by-economic-status derived table.

Source: MiD 2023 Autos micro-data CSV (``MiD2023_Autos.csv``, 290930 rows).

Output:
    eqasim-data/data/braunschweig/kba/derived/mid2023_antrieb_by_status.csv
    columns: status, powertrain, share, base_weighted

where ``share = P(powertrain | status)`` (within each status cell the shares
sum to 1.0) and ``base_weighted`` is the A_GEW-weighted vehicle base of that
cell. The ``status == "all"`` row pools every usable row regardless of
economic status and carries the overall MiD powertrain mix -- this is the
denominator used by the EV-income tilt (Task B2) to compute
``P(powertrain | status) / P(powertrain)``.

A_ANTRIEB (fine code) -> powertrain mapping
--------------------------------------------
Derived from MiD2023_Codepläne_B1_Standard_v1.1.xlsx, sheet "Autos", variable
"A_ANTRIEB" (Antriebsart). VERIFIED against the codeplan:

    1  -> petrol  (Benzin)
    2  -> diesel  (Diesel)
    3  -> hybrid  (Hybrid ohne Ladeanschluss)
    4  -> phev    (Plug-in-Hybrid)
    5  -> bev     (rein elektrisch)
    6  -> gas     (Gas)
    7  -> other   (anderes)
    94 -> EXCLUDED (unplausibel)
    99 -> EXCLUDED (keine Angabe)

Do NOT use the collapsed ``antrieb`` variable (only 3 categories) -- this
extractor reads the fine-grained ``A_ANTRIEB`` code.

oek_status (1..5) -> STATUS_LABELS mapping (MiD Codeplan, variable "oek_status"):
    1 -> very_low  (sehr niedrig)
    2 -> low       (niedrig)
    3 -> medium    (mittel)
    4 -> high      (hoch)
    5 -> very_high (sehr hoch)

Rows with A_ANTRIEB in {94, 99} or oek_status outside 1..5 are excluded from
the table and counted/logged (no silent fallback: the drop rate is always
visible in the log).

Regenerate with:
    python scripts/build_mid_antrieb_by_status.py [--mid-path PATH] [--git-add]

Provenance: MiD 2023 (BMDV / infas), B1 (person-level) Datensatzpaket,
Autos micro-data, vehicle weight A_GEW. Processed 2026-07.
"""

from __future__ import annotations

import argparse
import logging
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
# Powertrain labels producible from MiD A_ANTRIEB (a subset of the broader
# fleet_tables.POWERTRAIN_LABELS, which also carries "hydrogen" -- MiD does not
# report a hydrogen category).
ANTRIEB_LABELS: tuple[str, ...] = (
    "petrol", "diesel", "hybrid", "phev", "bev", "gas", "other",
)

STATUS_LABELS: tuple[str, ...] = (
    "very_low", "low", "medium", "high", "very_high",
)

#: Label of the pooled row (all statuses combined) = the tilt's denominator.
ALL_STATUS_LABEL = "all"

# ---------------------------------------------------------------------------
# Mappings
# ---------------------------------------------------------------------------
# A_ANTRIEB integer code -> canonical powertrain label.
# Source: MiD2023_Codepläne_B1_Standard_v1.1.xlsx, sheet "Autos", variable
# A_ANTRIEB. Codes 94 (unplausibel) and 99 (keine Angabe) are intentionally
# absent from this map so they fall out as "unmapped" and are dropped + logged.
ANTRIEB_MAP: dict[int, str] = {
    1: "petrol",
    2: "diesel",
    3: "hybrid",
    4: "phev",
    5: "bev",
    6: "gas",
    7: "other",
}

# Named for documentation / log messages only (ANTRIEB_MAP already excludes them).
_ANTRIEB_EXCLUDED_CODES = (94, 99)

# oek_status integer -> canonical status label.
OEK_STATUS_MAP: dict[int, str] = {
    1: "very_low",
    2: "low",
    3: "medium",
    4: "high",
    5: "very_high",
}

# Plausibility band for the pooled "all" BEV share (2023 fleet). This is a
# log-only sanity check, not a validated reference target: the brief
# (p3-B1-brief.md, Task B1) states the expected 2023 national BEV share lands
# roughly in 2-6 %; a value outside this band is logged as a WARNING so a
# mapping/data bug is caught early, but the build does not abort on it (only a
# 0-row result aborts).
_BEV_SHARE_SANITY_MIN = 0.02
_BEV_SHARE_SANITY_MAX = 0.06


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build(mid_path: Path) -> pd.DataFrame:
    """Read MiD Autos CSV, derive the powertrain-by-economic-status table.

    Returns a tidy DataFrame with columns:
        status, powertrain, share, base_weighted.

    Raises:
        RuntimeError: If the A_ANTRIEB / oek_status filtering leaves 0 usable
            rows (a mapping or input-schema bug), or if the resulting shares do
            not sum to 1.0 per status.
    """
    logger.info("[build_mid_antrieb_by_status] reading %s", mid_path)
    df = pd.read_csv(mid_path, usecols=["A_ANTRIEB", "A_GEW", "oek_status"])
    n_raw = len(df)

    # --- powertrain mapping (94/99 fall out of ANTRIEB_MAP as unmapped) ---
    df["powertrain"] = df["A_ANTRIEB"].map(ANTRIEB_MAP)
    unmapped_powertrain = df["powertrain"].isna().sum()
    if unmapped_powertrain > 0:
        bad_codes = df.loc[df["powertrain"].isna(), "A_ANTRIEB"].value_counts()
        logger.info(
            "[build_mid_antrieb_by_status] %d / %d rows with excluded/unmapped "
            "A_ANTRIEB (dropped; expected exclusions are codes %s = "
            "unplausibel/keine Angabe): %s",
            unmapped_powertrain, n_raw, _ANTRIEB_EXCLUDED_CODES, bad_codes.to_dict(),
        )
        df = df.dropna(subset=["powertrain"])

    # --- status mapping ---
    df["status"] = df["oek_status"].map(OEK_STATUS_MAP)
    unmapped_status = df["status"].isna().sum()
    if unmapped_status > 0:
        bad_codes = df.loc[df["status"].isna(), "oek_status"].value_counts()
        logger.warning(
            "[build_mid_antrieb_by_status] %d rows with invalid/unmapped "
            "oek_status (dropped; not in 1..5): %s",
            unmapped_status, bad_codes.to_dict(),
        )
        df = df.dropna(subset=["status"])

    if len(df) == 0:
        raise RuntimeError(
            "[build_mid_antrieb_by_status] 0 rows remain after A_ANTRIEB / "
            "oek_status filtering -- the mapping produced no usable rows; check "
            "the MiD Autos CSV column codes against ANTRIEB_MAP / OEK_STATUS_MAP."
        )

    logger.info(
        "[build_mid_antrieb_by_status] %d / %d rows usable after filtering (%.2f%%)",
        len(df), n_raw, 100.0 * len(df) / n_raw,
    )

    # --- weighted aggregation per (status, powertrain) ---
    base = (
        df.groupby("status", observed=True)["A_GEW"]
        .sum()
        .rename("base_weighted")
        .reset_index()
    )
    counts = (
        df.groupby(["status", "powertrain"], observed=True)["A_GEW"]
        .sum()
        .rename("weighted_count")
        .reset_index()
    )
    merged = counts.merge(base, on="status", how="left")
    merged["share"] = merged["weighted_count"] / merged["base_weighted"]

    # --- pooled "all" row: overall MiD powertrain mix (the tilt's denominator) ---
    all_base_weighted = df["A_GEW"].sum()
    all_counts = (
        df.groupby("powertrain", observed=True)["A_GEW"]
        .sum()
        .rename("weighted_count")
        .reset_index()
    )
    all_counts["status"] = ALL_STATUS_LABEL
    all_counts["base_weighted"] = all_base_weighted
    all_counts["share"] = all_counts["weighted_count"] / all_counts["base_weighted"]

    tidy = pd.concat(
        [merged[["status", "powertrain", "share", "base_weighted"]],
         all_counts[["status", "powertrain", "share", "base_weighted"]]],
        ignore_index=True,
    )

    # Round to sensible precision.
    tidy["share"] = tidy["share"].round(8)
    tidy["base_weighted"] = tidy["base_weighted"].round(3)

    # --- ensure rectangular: every present status has all 7 powertrains ---
    present_statuses = tidy["status"].drop_duplicates().tolist()
    full_index = pd.MultiIndex.from_product(
        [present_statuses, ANTRIEB_LABELS], names=["status", "powertrain"]
    )
    tidy = tidy.set_index(["status", "powertrain"])
    missing_cells = full_index.difference(tidy.index)
    if len(missing_cells) > 0:
        base_map = tidy["base_weighted"].groupby(level="status").first()
        fill_rows = []
        filled_statuses: set[str] = set()
        for sta, pt in missing_cells:
            fill_rows.append({
                "status": sta,
                "powertrain": pt,
                "share": 0.0,
                "base_weighted": float(base_map.get(sta, 0.0)),
            })
            filled_statuses.add(sta)
        filled_df = pd.DataFrame(fill_rows).set_index(["status", "powertrain"])
        tidy = pd.concat([tidy, filled_df])
        logger.warning(
            "[build_mid_antrieb_by_status] filled %d zero-share (status, powertrain) "
            "cells (no observations) across statuses %s",
            len(missing_cells), sorted(filled_statuses),
        )
    tidy = (
        tidy.reset_index()
        .sort_values(["status", "powertrain"])
        .reset_index(drop=True)
    )

    # --- sanity: shares sum to 1 per status ---
    totals = tidy.groupby("status")["share"].sum()
    bad = totals[abs(totals - 1.0) > 1e-4]
    if len(bad) > 0:
        raise RuntimeError(f"share does not sum to 1.0 for status rows: {bad}")

    # --- sanity: overall BEV share plausibility (log-only, see module docstring) ---
    all_bev_rows = tidy[
        (tidy["status"] == ALL_STATUS_LABEL) & (tidy["powertrain"] == "bev")
    ]
    if len(all_bev_rows) == 1:
        all_bev_share = float(all_bev_rows["share"].iloc[0])
        logger.info(
            "[build_mid_antrieb_by_status] overall ('all') BEV share = %.4f",
            all_bev_share,
        )
        if not (_BEV_SHARE_SANITY_MIN <= all_bev_share <= _BEV_SHARE_SANITY_MAX):
            logger.warning(
                "[build_mid_antrieb_by_status] overall BEV share %.4f is outside "
                "the plausible 2023 band [%.2f, %.2f] -- check the mapping / data.",
                all_bev_share, _BEV_SHARE_SANITY_MIN, _BEV_SHARE_SANITY_MAX,
            )

    logger.info(
        "[build_mid_antrieb_by_status] built %d rows (%d status groups x %d powertrains)",
        len(tidy), tidy["status"].nunique(), len(ANTRIEB_LABELS),
    )
    return tidy


def _print_summary(tidy: pd.DataFrame) -> None:
    """Print the P(powertrain | status) table, ordered low -> high -> all."""
    print("\n[build_mid_antrieb_by_status] P(powertrain | status):")
    pivot = tidy.pivot(index="powertrain", columns="status", values="share")
    ordered_cols = [s for s in (*STATUS_LABELS, ALL_STATUS_LABEL) if s in pivot.columns]
    ordered_rows = [p for p in ANTRIEB_LABELS if p in pivot.index]
    pivot = pivot.loc[ordered_rows, ordered_cols]
    print(pivot.round(4).to_string())
    print()

    bev_row = pivot.loc["bev"] if "bev" in pivot.index else None
    if bev_row is not None:
        print("P(bev | status) gradient:")
        for s in ordered_cols:
            print(f"  {s:20s}: {bev_row[s]:.4f}")


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
    _print_summary(tidy)

    out_dir = Path(args.data_path) / "braunschweig" / "kba" / "derived"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_csv = out_dir / "mid2023_antrieb_by_status.csv"

    header = (
        "# MiD 2023 vehicle powertrain (A_ANTRIEB) x economic status (oek_status).\n"
        "# Generated by scripts/build_mid_antrieb_by_status.py from\n"
        "# MiD2023_Autos.csv (B1 Datensatzpaket, A_GEW-weighted).\n"
        "# share = P(powertrain | status); sums to 1.0 per status, including the\n"
        "#   pooled 'all' row = overall MiD powertrain mix (the EV-income tilt's\n"
        "#   denominator, see Task B2).\n"
        "# base_weighted = A_GEW-weighted vehicle count of the status cell.\n"
        "# A_ANTRIEB mapping: 1=petrol 2=diesel 3=hybrid 4=phev 5=bev 6=gas 7=other\n"
        "#   (94=unplausibel, 99=keine Angabe excluded).\n"
        "# oek_status mapping: 1=very_low 2=low 3=medium 4=high 5=very_high\n"
        "#   (values outside 1..5 excluded).\n"
    )

    with open(out_csv, "w", encoding="utf-8", newline="") as fh:
        fh.write(header)
        tidy.to_csv(fh, index=False)

    print(f"[build_mid_antrieb_by_status] wrote {out_csv} ({len(tidy)} rows)")

    if args.git_add:
        subprocess.run(["git", "add", "-f", str(out_csv)], cwd=str(REPO), check=True)
        print(f"[build_mid_antrieb_by_status] git add -f {out_csv}")


if __name__ == "__main__":
    main()
