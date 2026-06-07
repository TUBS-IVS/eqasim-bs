"""Extract the MiD 2023 'monatliches HH-Nettoeinkommen x Wohnen (Miete/Eigentum) x Region' tables.

Source (local-only raw xlsx, exported from the MiD 2023 online table tool,
sheet ``MiD Tabellen``):

  * ``mid2023_income_by_tenure_bundesland.xlsx``
        16 stacked blocks, one per Bundesland (header row
        ``Bundesland: - <name>``). Within a block: columns = "Wohnen zur Miete oder
        in Wohneigentum" (Miete / Eigentum / anderes / keine Angabe), rows =
        ``monatliches HH-Nettoeinkommen in 10 Gruppen`` (10 brackets). Each tenure
        column gives P(income_bracket | tenure, Bundesland) (Spalten %, column %
        summing to 100 over brackets); the ``Basis gewichtet`` row gives the
        (tenure, Bundesland) weighted household base.
  * ``mid2023_income_by_tenure_raumtyp.xlsx``
        Same layout, blocks = the 7 RegioStaR Raumtyp categories.

Both tables encode P(income_bracket | tenure, region). The Bundesland table is
the BASE (Niedersachsen) and the raumtyp table is a within-NDS tilt downstream
(see braunschweig.data.mid.tenure_by_income). Downstream the tenure marginal +
the per-tenure income conditional are Bayes-inverted to P(tenure | income_bracket)
to sample the completeness attribute ``housing_tenure``.

Tenure folding: the MiD "anderes" and "keine Angabe" columns are FOLDED into a
single canonical ``other`` tenure -- their weighted bases are summed and their
per-bracket masses base-weighted-averaged -- so the committed CSV already carries
the 3-class partition {rent, own, other}.

Output (committed tidy CSVs, long form):

    eqasim-data/data/braunschweig/mid/mid2023_income_by_tenure_bundesland.csv
        columns [region, tenure, income_bracket, share_pct, base_weighted]
    eqasim-data/data/braunschweig/mid/mid2023_income_by_tenure_raumtyp.csv
        columns [region, tenure, income_bracket, share_pct, base_weighted]

MiD suppression/zero symbols ("-", ".", "/", "()", empty, "0") are coerced to
0.0 share_pct explicitly and the coercion counts are logged (no silent
fallbacks).

The derived CSVs are force-added to git (``git add -f``) to match the existing
committed MiD-table pattern, even though the ``eqasim-data`` tree is gitignored.

Regenerate with:

    python scripts/extract_mid_income_by_tenure.py --git-add

Provenance: MiD 2023 (BMDV / infas), regional sample online table tool, table
"monatliches HH-Nettoeinkommen in 10 Gruppen" cross-tabulated by "Wohnen zur
Miete oder in Wohneigentum" x Bundesland and x Raumtyp, exported 2026-06.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.mid.tenure_by_income import (  # noqa: E402
    INCOME_BRACKET_CATEGORIES,
    REGION_LABEL_TO_KEY_BUNDESLAND,
    REGION_LABEL_TO_KEY_RAUMTYP,
    TENURE_CATEGORIES,
    TENURE_LABEL_TO_KEY,
)
from braunschweig.data.mid.income_by_size import (  # noqa: E402
    INCOME_BRACKET_LABEL_TO_KEY,
)

SHEET = "MiD Tabellen"

_SUPPRESSION_TOKENS = {"-", ".", "/", "()", "(", ")", "", "nan"}

_BUNDESLAND_MARKER = "bundesland:"
_RAUMTYP_MARKER = "zusammengefasster regionalstatistischer raumtyp"
_COLUMN_HEADER_MARKER = "spalten % (gewichtet)"
_BASE_WEIGHTED_MARKER = "basis gewichtet"


def _normalise_ws(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _coerce_percent(raw) -> tuple[float, str]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0, "blank"
    s = _normalise_ws(raw).replace("%", "").strip()
    if s.lower() in _SUPPRESSION_TOKENS:
        return 0.0, "suppression"
    s = s.replace(",", ".")
    try:
        return float(s), "value"
    except ValueError:
        return 0.0, "suppression"


def _coerce_base(raw) -> float:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0
    s = _normalise_ws(raw)
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _region_for_header(cell: str, marker: str,
                       region_label_to_key: dict[str, str]) -> str | None:
    low = _normalise_ws(cell).lower()
    if marker not in low:
        return None
    raw = _normalise_ws(cell)
    after = raw.split(":", 1)[1].strip()
    if after.startswith("-"):
        after = after[1:].strip()
    return region_label_to_key.get(after)


def _locate_tenure_columns(df_raw: pd.DataFrame, start: int, end: int) -> dict[int, str]:
    """Locate the column index -> tenure key map for one block.

    The tenure labels sit on the row AFTER the 'Spalten % (gewichtet)' header row
    ('Miete', 'Eigentum', 'anderes', 'keine Angabe'). 'anderes' and 'keine Angabe'
    both map to the canonical ``other`` key (folded later).
    """
    for i in range(start, end):
        if _normalise_ws(df_raw.iloc[i, 0]).lower() == _COLUMN_HEADER_MARKER:
            hdr = df_raw.iloc[i + 1]
            found: dict[int, str] = {}
            for c in range(2, df_raw.shape[1]):
                label = _normalise_ws(hdr.iloc[c]).lower()
                if label in TENURE_LABEL_TO_KEY:
                    found[c] = TENURE_LABEL_TO_KEY[label]
            if found:
                return found
    raise RuntimeError(
        f"extract_mid_income_by_tenure: no Wohnen (tenure) column header in block "
        f"rows {start}..{end}."
    )


def _read_base_weighted(df_raw: pd.DataFrame, start: int, end: int,
                        cols: dict[int, str]) -> dict[int, float]:
    for i in range(start, end):
        if _normalise_ws(df_raw.iloc[i, 0]).lower() == _BASE_WEIGHTED_MARKER:
            return {c: _coerce_base(df_raw.iloc[i, c]) for c in cols}
    raise RuntimeError(
        f"extract_mid_income_by_tenure: 'Basis gewichtet' row missing in block "
        f"rows {start}..{end}."
    )


def _block_bounds(df_raw: pd.DataFrame, marker: str,
                  region_label_to_key: dict[str, str]) -> list[tuple[int, int, str]]:
    n_rows = len(df_raw)
    starts: list[tuple[int, str]] = []
    unknown: list[str] = []
    for i in range(n_rows):
        cell = _normalise_ws(df_raw.iloc[i, 0])
        if marker in cell.lower():
            region = _region_for_header(cell, marker, region_label_to_key)
            if region is None:
                unknown.append(cell)
                continue
            starts.append((i, region))
    if unknown:
        raise RuntimeError(
            "extract_mid_income_by_tenure: unmapped region header(s) "
            f"{unknown}. Update the region label map."
        )
    if not starts:
        raise RuntimeError(
            f"extract_mid_income_by_tenure: no region block headers found "
            f"(marker '{marker}')."
        )
    bounds = []
    for bi, (start, region) in enumerate(starts):
        end = starts[bi + 1][0] if bi + 1 < len(starts) else n_rows
        bounds.append((start, end, region))
    return bounds


def parse_sheet(df_raw: pd.DataFrame, marker: str,
                region_label_to_key: dict[str, str]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Parse one income-by-tenure sheet into tidy long form (3-class tenure).

    The MiD 'anderes' and 'keine Angabe' columns are FOLDED into ``other``: their
    weighted bases are summed and their per-bracket shares are base-weighted-
    averaged, so the result is the canonical {rent, own, other} partition.
    Returns ``(tidy_df, coercion_counts)``.
    """
    coercion = {"value": 0, "suppression": 0, "blank": 0}
    records = []

    for start, end, region in _block_bounds(df_raw, marker, region_label_to_key):
        cols = _locate_tenure_columns(df_raw, start, end)
        base_weighted = _read_base_weighted(df_raw, start, end, cols)

        # Read the per-(column, bracket) share, then fold columns sharing a tenure
        # key (i.e. anderes + keine Angabe -> other) by base-weighted average.
        # share_acc[tenure][bracket] = sum_c base_c * share_c ; base_acc[tenure].
        share_acc: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        base_acc: dict[str, float] = defaultdict(float)
        for c, tenure in cols.items():
            base_acc[tenure] += base_weighted[c]
        for i in range(start, end):
            label = _normalise_ws(df_raw.iloc[i, 0])
            bracket = INCOME_BRACKET_LABEL_TO_KEY.get(label)
            if bracket is None:
                continue
            for c, tenure in cols.items():
                share, kind = _coerce_percent(df_raw.iloc[i, c])
                coercion[kind] += 1
                share_acc[tenure][bracket] += base_weighted[c] * share

        for tenure in TENURE_CATEGORIES:
            base_t = base_acc.get(tenure, 0.0)
            for bracket in INCOME_BRACKET_CATEGORIES:
                weighted_sum = share_acc[tenure].get(bracket, 0.0)
                # base-weighted average share over the folded columns.
                share = (weighted_sum / base_t) if base_t > 0 else 0.0
                records.append({
                    "region": region,
                    "tenure": tenure,
                    "income_bracket": bracket,
                    "share_pct": round(share, 6),
                    "base_weighted": round(base_t, 3),
                })

    tidy = (
        pd.DataFrame.from_records(
            records,
            columns=["region", "tenure", "income_bracket", "share_pct", "base_weighted"],
        )
        .sort_values(["region", "tenure", "income_bracket"])
        .reset_index(drop=True)
    )
    return tidy, coercion


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data-path",
        default=str(REPO / "eqasim-data" / "data"),
        help="Root data path (contains braunschweig/mid).",
    )
    parser.add_argument(
        "--git-add",
        action="store_true",
        help="Force-add the generated CSVs to git (git add -f).",
    )
    args = parser.parse_args()

    mid_dir = Path(args.data_path) / "braunschweig" / "mid"

    bund_xlsx = mid_dir / "mid2023_income_by_tenure_bundesland.xlsx"
    raum_xlsx = mid_dir / "mid2023_income_by_tenure_raumtyp.xlsx"
    for p in (bund_xlsx, raum_xlsx):
        if not p.exists():
            raise FileNotFoundError(f"Source xlsx not found: {p}")

    df_bund = pd.read_excel(bund_xlsx, sheet_name=SHEET, header=None)
    tidy_bund, coerce_bund = parse_sheet(
        df_bund, _BUNDESLAND_MARKER, REGION_LABEL_TO_KEY_BUNDESLAND
    )
    print(
        f"[extract_mid_income_by_tenure] {bund_xlsx.name}: {len(tidy_bund)} rows, "
        f"{tidy_bund['region'].nunique()} regions x {tidy_bund['tenure'].nunique()} "
        f"tenure x {tidy_bund['income_bracket'].nunique()} brackets; "
        f"coercion: numeric={coerce_bund['value']}, "
        f"suppression-token={coerce_bund['suppression']}, blank={coerce_bund['blank']}."
    )

    df_raum = pd.read_excel(raum_xlsx, sheet_name=SHEET, header=None)
    tidy_raum, coerce_raum = parse_sheet(
        df_raum, _RAUMTYP_MARKER, REGION_LABEL_TO_KEY_RAUMTYP
    )
    print(
        f"[extract_mid_income_by_tenure] {raum_xlsx.name}: {len(tidy_raum)} rows, "
        f"{tidy_raum['region'].nunique()} regions x {tidy_raum['tenure'].nunique()} "
        f"tenure x {tidy_raum['income_bracket'].nunique()} brackets; "
        f"coercion: numeric={coerce_raum['value']}, "
        f"suppression-token={coerce_raum['suppression']}, blank={coerce_raum['blank']}."
    )

    out_bund = mid_dir / "mid2023_income_by_tenure_bundesland.csv"
    out_raum = mid_dir / "mid2023_income_by_tenure_raumtyp.csv"

    header_common = (
        "# Generated by scripts/extract_mid_income_by_tenure.py from\n"
        "# {src} (sheet 'MiD Tabellen').\n"
        "# share_pct = P(income_bracket | tenure, region) in percent (a tenure\n"
        "# column sums to ~100 over brackets); base_weighted = weighted household\n"
        "# base of the (tenure, region) cell. Tenure = {{rent, own, other}}; the MiD\n"
        "# 'anderes' + 'keine Angabe' columns are folded into 'other'.\n"
    )

    for out_csv, src, tidy in (
        (out_bund, bund_xlsx.name, tidy_bund),
        (out_raum, raum_xlsx.name, tidy_raum),
    ):
        with open(out_csv, "w", encoding="utf-8", newline="") as handle:
            handle.write("# MiD 2023 monatliches HH-Nettoeinkommen x Wohnen (Miete/Eigentum) x Region.\n")
            handle.write(header_common.format(src=src))
            tidy.to_csv(handle, index=False)
        print(f"[extract_mid_income_by_tenure] wrote {out_csv}")

    if args.git_add:
        import subprocess
        for out_csv in (out_bund, out_raum):
            subprocess.run(["git", "add", "-f", str(out_csv)], cwd=str(REPO), check=True)
        print("[extract_mid_income_by_tenure] git add -f 2 CSVs")


if __name__ == "__main__":
    main()
