"""Derive the MiD 2023 escort W_ZWECK active/passive split (issue #256).

MiD codes escort two-sidedly: W_ZWECK 6 = active Bringen/Holen, W_ZWECK 13 =
the escorted person's own (passive) leg -- 100% minors, folded into Begleitung
by MiD's own derivations, hence contained in the published W1 8% and the W12
length profile. With escort_passive_education ON the model's escort purpose is
ACTIVE-ONLY, so validation needs the split to derive apples-to-apples
references:

  begleitung_active_ref  = W1_begleitung * share_weighted[code_6]
  education_adjusted_ref = W1_ausbildung + W1_begleitung * share_weighted[code_13]

and the code_6 rows provide the active-only length profile (bands in the W12
column convention) for the escort distance comparison.

Input:  eqasim-data/data/braunschweig/popsim/mid2023_raw/MiD2023_Wege.csv
        (LOCAL-only raw; comma-separated; W_ZWECK, W_GEW, wegkm_imp)
Output: eqasim-data/data/braunschweig/mid/mid2023_escort_w_zweck_split.csv
        (committed pinned reference; regenerate here, never edit)
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from scripts.derive_escort_location_weights import weighted_median

REPO = Path(__file__).resolve().parents[1]
DEFAULT_WEGE_PATH = (REPO / "eqasim-data" / "data" / "braunschweig" / "popsim"
                     / "mid2023_raw" / "MiD2023_Wege.csv")
DEFAULT_OUTPUT_PATH = (REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
                       / "mid2023_escort_w_zweck_split.csv")

ESCORT_CODES = (6, 13)
# Band edges/columns mirror the committed mid2023_W12_triplength_by_purpose.csv.
BAND_EDGES = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, float("inf")]
BAND_COLUMNS = ["d_unter_0_5km", "d_0_5_1km", "d_1_2km", "d_2_5km", "d_5_10km",
                "d_10_20km", "d_20_50km", "d_50_100km", "d_100km_plus"]


def _band_shares_pct(length_km: pd.Series, weights: pd.Series) -> list[float]:
    bins = pd.cut(np.asarray(length_km, dtype=float), BAND_EDGES, right=False)
    share = (pd.DataFrame({"b": bins, "w": np.asarray(weights, dtype=float)})
             .groupby("b", observed=False)["w"].sum())
    share = share / share.sum()
    return [float(100.0 * s) for s in share.to_numpy()]


def derive_split(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Weighted active/passive split + per-code length profile.

    share_weighted is computed over ALL escort legs (W_GEW); the length
    statistics (mean/median/bands) use only legs with a usable wegkm_imp
    (numeric, >= 0, < 1000 -- mirroring the #257 coherence-gate filter), with
    the weighted coverage reported in stats.
    """
    escort = df[df["W_ZWECK"].isin(ESCORT_CODES)].copy()
    if len(escort) == 0:
        raise ValueError("[derive_escort_w_zweck_split] no escort legs "
                         f"(W_ZWECK in {list(ESCORT_CODES)}) found.")
    escort["W_GEW"] = pd.to_numeric(escort["W_GEW"], errors="coerce").fillna(0.0)
    escort["wegkm_imp"] = pd.to_numeric(escort["wegkm_imp"], errors="coerce")
    total_weight = float(escort["W_GEW"].sum())
    length_ok = escort["wegkm_imp"].notna() & (escort["wegkm_imp"] >= 0) \
        & (escort["wegkm_imp"] < 1000.0)
    coverage = float(escort.loc[length_ok, "W_GEW"].sum() / total_weight) if total_weight else 0.0

    rows = []
    for label, sub in (("code_6", escort[escort["W_ZWECK"] == 6]),
                       ("code_13", escort[escort["W_ZWECK"] == 13]),
                       ("both", escort)):
        sub_ok = sub[length_ok.reindex(sub.index, fill_value=False)]
        row = {
            "w_zweck": label,
            "n_legs_unweighted": int(len(sub)),
            "share_weighted": round(float(sub["W_GEW"].sum() / total_weight), 4)
                if total_weight else float("nan"),
            "mean_km": round(float(np.average(sub_ok["wegkm_imp"],
                                              weights=sub_ok["W_GEW"])), 4)
                if len(sub_ok) else float("nan"),
            "median_km": round(weighted_median(sub_ok["wegkm_imp"],
                                               sub_ok["W_GEW"]), 4)
                if len(sub_ok) else float("nan"),
        }
        bands = _band_shares_pct(sub_ok["wegkm_imp"], sub_ok["W_GEW"]) \
            if len(sub_ok) else [float("nan")] * len(BAND_COLUMNS)
        row.update({c: round(v, 2) for c, v in zip(BAND_COLUMNS, bands)})
        rows.append(row)

    table = pd.DataFrame(rows, columns=["w_zweck", "n_legs_unweighted",
                                        "share_weighted", "mean_km", "median_km",
                                        *BAND_COLUMNS])
    stats = {"length_coverage_weighted": coverage,
             "n_escort_legs": int(len(escort))}
    return table, stats


def _sniff_separator(path) -> str:
    with open(path, "r", encoding="latin-1") as handle:
        first = handle.readline()
    return ";" if first.count(";") > first.count(",") else ","


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wege", type=Path, default=DEFAULT_WEGE_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args(argv)

    df = pd.read_csv(args.wege, sep=_sniff_separator(args.wege),
                     usecols=["W_ZWECK", "W_GEW", "wegkm_imp"], low_memory=False)
    df["W_ZWECK"] = pd.to_numeric(df["W_ZWECK"], errors="coerce")
    table, stats = derive_split(df)

    header = (
        "# Source: MiD 2023 Wege (local raw MiD2023_Wege.csv), W_ZWECK in {6, 13},\n"
        "# W_GEW-weighted. Code 6 = active Bringen/Holen; code 13 = the escorted\n"
        "# person's own (passive) leg (issue #256; 100% minors, verified 2026-08-11).\n"
        f"# share_weighted over ALL escort legs (n={stats['n_escort_legs']}); length stats\n"
        f"# (wegkm_imp >= 0, < 1000 km) cover {stats['length_coverage_weighted']:.4f} of the escort weight.\n"
        "# Band columns follow mid2023_W12_triplength_by_purpose.csv (row-%).\n"
        "# Generated by scripts/derive_escort_w_zweck_split.py; regenerate there, never edit.\n"
    )
    with open(args.output, "w", encoding="utf-8", newline="") as handle:
        handle.write(header)
        table.to_csv(handle, index=False)
    print(f"[derive_escort_w_zweck_split] -> {args.output}")
    print(table.to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
