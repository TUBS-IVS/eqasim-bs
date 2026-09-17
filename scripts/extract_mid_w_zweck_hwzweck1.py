"""Derive the MiD 2023 W_ZWECK x hwzweck1 fold evidence table (issue #373, ADR-0111).

MiD's own main-purpose derivation ``hwzweck1`` folds each raw ``W_ZWECK`` (Wegezweck) code onto
one of seven main purposes (Arbeit/dienstlich/Ausbildung/Einkauf/Erledigung/Freizeit/Begleitung).
Notably, ``W_ZWECK 10`` ("anderer Zweck", filed by the model as ``other``) is folded by MiD to
6 Freizeit -- this table is the row-share evidence that pin test
``tests/test_w_zweck_hwzweck1_fold.py`` and ADR-0111 rely on. It is an EVIDENCE TABLE, not a
control target: it does not carry a population-level reference share, only the internal MiD
W_ZWECK -> hwzweck1 row distribution.

Input:  MiD 2023 Wege (LOCAL raw; columns W_ZWECK, hwzweck1, W_GEW, W_RBW, kernwo). The server
        deployment keeps this under ``eqasim-data/data/braunschweig/popsim/mid2023_raw`` (the
        script's ``--raw`` default); a local scientific-use delivery may live elsewhere, so pass
        ``--raw`` explicitly to point at it.
Output: eqasim-data/data/braunschweig/mid/mid2023_w_zweck_by_hwzweck1.csv
        (committed evidence table; regenerate here, never edit).
"""
from __future__ import annotations

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from braunschweig.popsim.mid.csv_format import detect_csv_separator
from braunschweig.popsim.trips import (
    RBW_LEG_FLAG, WEEKDAY_DIARY_KERNWO, weekday_diary_leg_mask,
)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_RAW_DIR = REPO / "eqasim-data" / "data" / "braunschweig" / "popsim" / "mid2023_raw"
DEFAULT_OUTPUT_PATH = (REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
                       / "mid2023_w_zweck_by_hwzweck1.csv")
WEGE_FILE = "MiD2023_Wege.csv"

#: Weekday legs only (Kernwochentage Di/Mi/Do); mirrors the weekday-trip convention used by the
#: other committed MiD Wege aggregates in this repository (e.g. mid2023_escort_w_zweck_split.csv
#: draws from the same weekday sample logic). IMPORTED from the model's ONE weekday-universe
#: definition (``braunschweig.popsim.trips.WEEKDAY_DIARY_KERNWO``, which itself reads the
#: PopulationSim seed's own day filter ``seed.MID_SEED_COLUMNS.day_filter_values``) rather than
#: re-typed as a literal tuple: until issue #373's cleanup wave this module WAS re-typed as a
#: bare ``(1, 2, 3)`` -- not yet DRIFTED from the seed's actual value (both were literally
#: ``(1, 2, 3)``), but a re-typed copy has no mechanism to notice a future change to the
#: weekday-code universe, which is exactly the silent-drift risk importing the single home
#: removes (the committed MiD Wege aggregates and the model's own estimation must describe the
#: SAME leg universe -- ADR-0116). Kept as ``KERNWO_WEEKDAY_CODES`` (not renamed) because
#: ``scripts/extract_mid_w_zwd_groups.py`` imports this exact name from this module.
KERNWO_WEEKDAY_CODES = WEEKDAY_DIARY_KERNWO
#: W_RBW == 1 marks a route-break summary leg (Ruecken/Bogen-Weg fragment introduced by MiD's own
#: route-splitting), not a genuine, independently reported trip purpose; excluded so the fold is
#: computed over real legs only, matching how downstream trip construction filters W_RBW.
#: Imported (``trips.RBW_LEG_FLAG``) for the same single-home reason as the weekday codes above.
RBW_SUMMARY_LEG_CODE = RBW_LEG_FLAG
REQUIRED_COLUMNS = ["W_ZWECK", "hwzweck1", "W_GEW", "W_RBW", "kernwo"]


def _git_commit_hash() -> str:
    """Return the short git commit hash of HEAD, or 'unknown' if it cannot be determined."""
    try:
        result = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO,
                                capture_output=True, text=True, check=True)
        return result.stdout.strip()
    except Exception:
        return "unknown"


def derive_fold_table(wege: pd.DataFrame) -> pd.DataFrame:
    """Compute the W_GEW-weighted W_ZWECK x hwzweck1 row-share table.

    Restricts the legs to the WEEKDAY DIARY universe -- weekday legs (``kernwo in
    KERNWO_WEEKDAY_CODES``) that are not route-break summary legs (``W_RBW ==
    RBW_SUMMARY_LEG_CODE``) -- through ``braunschweig.popsim.trips.weekday_diary_leg_mask``,
    the ONE definition of that universe the model's own estimation stages apply as well
    (ADR-0116), so this evidence table and the estimation it informs cannot describe
    different days. The filter is documented in the module header and in the committed CSV.
    ``share_weighted`` is the W_GEW share of each (w_zweck, hwzweck1) pair within its w_zweck
    total, so the rows for one w_zweck sum to 1.0.
    """
    wege = wege.copy()
    for column in ("W_ZWECK", "hwzweck1", "W_GEW", "W_RBW", "kernwo"):
        wege[column] = pd.to_numeric(wege[column], errors="coerce")
    filtered = wege[weekday_diary_leg_mask(wege)]
    if len(filtered) == 0:
        raise ValueError(
            "[extract_mid_w_zweck_hwzweck1] no legs left after the weekday/non-rbW filter; "
            "check the kernwo and W_RBW column contents.")

    grouped = filtered.groupby(["W_ZWECK", "hwzweck1"], dropna=False).agg(
        n_unweighted=("W_GEW", "size"),
        weight_sum=("W_GEW", "sum"),
    ).reset_index()
    zweck_total_weight = filtered.groupby("W_ZWECK")["W_GEW"].sum().rename("zweck_total_weight")
    grouped = grouped.merge(zweck_total_weight, left_on="W_ZWECK", right_index=True, how="left")
    grouped["share_weighted"] = grouped["weight_sum"] / grouped["zweck_total_weight"]

    table = grouped[["W_ZWECK", "hwzweck1", "n_unweighted", "share_weighted"]].rename(
        columns={"W_ZWECK": "w_zweck"})
    table["w_zweck"] = table["w_zweck"].astype(int)
    table["hwzweck1"] = table["hwzweck1"].astype(int)
    table["share_weighted"] = table["share_weighted"].round(4)
    table = table.sort_values(["w_zweck", "hwzweck1"]).reset_index(drop=True)
    return table


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=DEFAULT_RAW_DIR,
                        help="Directory containing the raw MiD 2023 Wege CSV "
                             f"(default: the server path {DEFAULT_RAW_DIR}).")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT_PATH,
                        help=f"Output path for the committed evidence table (default: {DEFAULT_OUTPUT_PATH}).")
    parser.add_argument("--source-commit", type=str, default=None,
                        help="Git commit hash to record in the header (default: HEAD of this repo).")
    args = parser.parse_args(argv)

    wege_path = args.raw / WEGE_FILE
    if not wege_path.exists():
        raise FileNotFoundError(
            f"[extract_mid_w_zweck_hwzweck1] raw MiD Wege file not found: {wege_path}. "
            "Pass --raw pointing at the directory holding MiD2023_Wege.csv (LOCAL raw delivery, "
            "not committed to this repository).")
    separator = detect_csv_separator(wege_path)
    header = pd.read_csv(wege_path, sep=separator, nrows=0).columns
    missing = [column for column in REQUIRED_COLUMNS if column not in header]
    if missing:
        raise RuntimeError(
            f"[extract_mid_w_zweck_hwzweck1] {wege_path} is missing required column(s) {missing} "
            f"(present: {sorted(header)[:20]} ...); cannot derive the fold table without them.")
    wege = pd.read_csv(wege_path, sep=separator, usecols=REQUIRED_COLUMNS, low_memory=False)

    table = derive_fold_table(wege)
    source_commit = args.source_commit or _git_commit_hash()
    generated_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    header_lines = (
        "# Source: MiD 2023 Wege (local raw MiD2023_Wege.csv), W_GEW-weighted row shares of\n"
        "# W_ZWECK (Wegezweck) x hwzweck1 (MiD's own main-purpose derivation).\n"
        f"# Filter: kernwo in {KERNWO_WEEKDAY_CODES} (weekday legs), W_RBW != {RBW_SUMMARY_LEG_CODE} "
        "(route-break summary legs excluded).\n"
        "# Weight: W_GEW (MiD trip expansion weight).\n"
        f"# Generated: {generated_date}\n"
        f"# Source commit: {source_commit}\n"
        "# EVIDENCE TABLE for ADR-0111 (issue #373); not a control target.\n"
        "# Generated by scripts/extract_mid_w_zweck_hwzweck1.py; regenerate there, never edit.\n"
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as handle:
        handle.write(header_lines)
        table.to_csv(handle, index=False, float_format="%.4f")

    n_codes = table["w_zweck"].nunique()
    print(f"[extract_mid_w_zweck_hwzweck1] -> {args.out} ({n_codes} W_ZWECK codes)")
    print(table.to_string(index=False))
    code_10 = table[table["w_zweck"] == 10]
    if len(code_10):
        print(f"[extract_mid_w_zweck_hwzweck1] W_ZWECK 10 folds to hwzweck1 "
              f"{code_10['hwzweck1'].tolist()} with share_weighted {code_10['share_weighted'].tolist()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
