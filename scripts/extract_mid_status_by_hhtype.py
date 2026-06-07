"""Extract the MiD 2023 'oekonomischer Status x Haushaltstyp x Region' tables.

Source (local-only raw xlsx, exported from the MiD 2023 online table tool,
sheet ``MiD Tabellen``):

  * ``mid2023_status_by_hhtype_raumtyp.xlsx``
        columns  = RegioStaR "zusammengefasster regionalstatistischer Raumtyp"
                   (7 categories: Stadtregion Metropole / Regiopole / Mittelstadt
                   / kleinstaedtisch, laendliche Region zentrale Stadt /
                   Mittelstadt / kleinstaedtisch).
  * ``mid2023_status_by_hhtype_bundesland.xlsx``
        columns  = the 16 German Laender (incl. Niedersachsen).

Both files have the SAME stacked-block layout: one block per oekonomischer
Status level (sehr niedrig, niedrig, mittel, hoch, sehr hoch). Within a block:

    "Basis: Personen (Deutschland)"
    "  oekonomischer Status des Haushalts: - <level>"      <- block header
    ...
    "Basis ungewichtet"   <region counts>
    "Basis gewichtet"     <region weighted counts>          <- status x region base
    "Haushaltstyp - Differenzierung ... (11 Kategorien)"
    <Haushaltstyp rows>   <column-% per region>             <- P(hhtype|status,region)
    "Total"               100 % ...

So each block column gives ``P(Haushaltstyp | status, region)`` (column
percentages) and the "Basis gewichtet" row gives the status x region weighted
counts, from which the status marginal per region ``P(status | region)`` is
recovered downstream (Bayes).

Output (committed tidy CSVs, long form):

    eqasim-data/data/braunschweig/mid/mid2023_status_by_hhtype_raumtyp.csv
    eqasim-data/data/braunschweig/mid/mid2023_status_by_hhtype_bundesland.csv

with columns ``[region, hhtype, status, share_pct, base_weighted]``:

    region        canonical region label (whitespace-normalised; Total dropped)
    hhtype        canonical English Haushaltstyp key (see HHTYPE_LABEL_TO_KEY)
    status        canonical English status key (very_low .. very_high)
    share_pct     P(hhtype | status, region) as a percentage in [0, 100]
    base_weighted weighted base (persons) of the (status, region) cell -- repeated
                  across all hhtype rows of that cell

MiD suppression/zero symbols ("-", ".", "/", "()", empty) are coerced to 0.0
share_pct explicitly and the coercion counts are logged (no silent fallbacks).

The derived CSVs are force-added to git (``git add -f``) to match the existing
committed MiD-table pattern, even though the ``eqasim-data`` tree is gitignored.

Regenerate with:

    python scripts/extract_mid_status_by_hhtype.py

Provenance: MiD 2023 (BMDV / infas), regional sample online table tool, table
"oekonomischer Status des Haushalts x Haushaltstyp" cross-tabulated by Raumtyp
and by Bundesland, exported 2026-06.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.mid.status_by_hhtype import (  # noqa: E402
    HHTYPE_LABEL_TO_KEY,
    REGION_LABEL_TO_KEY_BUNDESLAND,
    REGION_LABEL_TO_KEY_RAUMTYP,
    STATUS_LABEL_TO_KEY,
)

SHEET = "MiD Tabellen"

# Cell values that MiD uses for "no / suppressed / rounds to zero" entries.
# All are coerced to a 0.0 percentage share explicitly (counted + logged).
_SUPPRESSION_TOKENS = {"-", ".", "/", "()", "(", ")", "", "nan"}

# Marker substrings used to locate the block anchors (robust to extra spaces).
_STATUS_HEADER_MARKER = "konomischer status des haushalts"  # 'oekonomischer ...'
_BASE_WEIGHTED_MARKER = "basis gewichtet"
_TOTAL_MARKER = "total"


def _normalise_ws(text: str) -> str:
    """Collapse runs of whitespace; strip. Repairs MiD line-wrap artefacts
    such as 'Niedersach sen' -> 'Niedersachsen' is NOT done here (we keep the
    space-collapsed label and map via the LABEL_TO_KEY dicts, which carry the
    wrapped spellings)."""
    return re.sub(r"\s+", " ", str(text)).strip()


def _coerce_percent(raw) -> tuple[float, bool]:
    """Return (share_pct, was_coerced).

    Parses values like '13 %', '0 %', '13', or a suppression token. Suppression
    tokens and blanks map to 0.0 and are flagged as coerced so the caller can
    count them.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0, True
    s = _normalise_ws(raw).replace("%", "").strip()
    if s.lower() in _SUPPRESSION_TOKENS:
        return 0.0, True
    s = s.replace(",", ".")
    try:
        return float(s), False
    except ValueError:
        return 0.0, True


def _coerce_base(raw) -> float:
    """Parse a German-formatted weighted base like '3.254' -> 3254.0."""
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0
    s = _normalise_ws(raw)
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return 0.0


def _find_status_for_header(cell: str) -> str | None:
    """Map a block-header cell to a canonical status key, or None."""
    low = _normalise_ws(cell).lower()
    if _STATUS_HEADER_MARKER not in low:
        return None
    # The level follows the last '-' in e.g. '... Haushalts: - sehr niedrig'.
    level = low.split("-")[-1].strip()
    return STATUS_LABEL_TO_KEY.get(level)


def parse_status_hhtype_sheet(
    df_raw: pd.DataFrame,
    region_label_to_key: dict[str, str],
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Parse one raw 'MiD Tabellen' sheet into the tidy long form.

    Returns ``(tidy_df, coercion_counts)`` where ``coercion_counts`` reports how
    many cells hit each coercion path (suppression token, blank, unparseable),
    for transparent logging (no silent fallbacks).
    """
    n_rows = len(df_raw)

    # 1) Region columns: read the region-name header row (the first row whose
    #    second column is empty/'Total' filler and whose later columns carry the
    #    region labels). We locate it as the row right after the 'Spalten %'
    #    header. Simpler + robust: scan for the row that contains the FIRST
    #    region label of either taxonomy.
    region_row_idx = None
    region_cols: dict[int, str] = {}
    for i in range(n_rows):
        row = df_raw.iloc[i]
        found = {}
        for c in range(2, df_raw.shape[1]):
            label = _normalise_ws(row.iloc[c])
            if label in region_label_to_key:
                found[c] = region_label_to_key[label]
        if found:
            region_row_idx = i
            region_cols = found
            break
    if not region_cols:
        raise RuntimeError(
            "extract_mid_status_by_hhtype: could not locate any region header "
            "column. Check REGION_LABEL_TO_KEY mapping vs. the sheet."
        )

    records = []
    coercion = {"suppression": 0, "blank": 0, "value": 0}

    # 2) Walk the stacked blocks. A block begins at a status header cell and
    #    ends at the next status header (or end of sheet).
    block_starts = []
    for i in range(n_rows):
        status = _find_status_for_header(_normalise_ws(df_raw.iloc[i, 0]))
        if status is not None:
            block_starts.append((i, status))

    if not block_starts:
        raise RuntimeError(
            "extract_mid_status_by_hhtype: no status block headers found."
        )

    for bi, (start, status) in enumerate(block_starts):
        end = block_starts[bi + 1][0] if bi + 1 < len(block_starts) else n_rows

        # Find 'Basis gewichtet' (status x region weighted base) within block.
        base_weighted: dict[int, float] = {}
        for i in range(start, end):
            label = _normalise_ws(df_raw.iloc[i, 0]).lower()
            if label == _BASE_WEIGHTED_MARKER:
                for c, region_key in region_cols.items():
                    base_weighted[c] = _coerce_base(df_raw.iloc[i, c])
                break
        if not base_weighted:
            raise RuntimeError(
                f"extract_mid_status_by_hhtype: 'Basis gewichtet' row missing "
                f"in status block '{status}'."
            )

        # Haushaltstyp rows: every row whose label maps to a canonical hhtype.
        for i in range(start, end):
            label = _normalise_ws(df_raw.iloc[i, 0])
            if label.lower() == _TOTAL_MARKER:
                continue
            hhtype = HHTYPE_LABEL_TO_KEY.get(label)
            if hhtype is None:
                continue
            for c, region_key in region_cols.items():
                share, coerced = _coerce_percent(df_raw.iloc[i, c])
                if coerced:
                    raw = df_raw.iloc[i, c]
                    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                        coercion["blank"] += 1
                    else:
                        coercion["suppression"] += 1
                else:
                    coercion["value"] += 1
                records.append({
                    "region": region_key,
                    "hhtype": hhtype,
                    "status": status,
                    "share_pct": share,
                    "base_weighted": base_weighted[c],
                })

    tidy = pd.DataFrame.from_records(
        records, columns=["region", "hhtype", "status", "share_pct", "base_weighted"]
    )
    return tidy, coercion


def _extract_one(xlsx_path: Path, region_label_to_key: dict[str, str]) -> pd.DataFrame:
    df_raw = pd.read_excel(xlsx_path, sheet_name=SHEET, header=None)
    tidy, coercion = parse_status_hhtype_sheet(df_raw, region_label_to_key)
    total = sum(coercion.values())
    print(
        f"[extract_mid_status_by_hhtype] {xlsx_path.name}: "
        f"{len(tidy)} rows, "
        f"{tidy['region'].nunique()} regions x {tidy['hhtype'].nunique()} hhtypes "
        f"x {tidy['status'].nunique()} status; "
        f"coercion: numeric={coercion['value']}, "
        f"suppression-token={coercion['suppression']}, blank={coercion['blank']} "
        f"(of {total} cells)"
    )
    return tidy


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

    jobs = [
        (
            mid_dir / "mid2023_status_by_hhtype_raumtyp.xlsx",
            REGION_LABEL_TO_KEY_RAUMTYP,
            mid_dir / "mid2023_status_by_hhtype_raumtyp.csv",
        ),
        (
            mid_dir / "mid2023_status_by_hhtype_bundesland.xlsx",
            REGION_LABEL_TO_KEY_BUNDESLAND,
            mid_dir / "mid2023_status_by_hhtype_bundesland.csv",
        ),
    ]

    written = []
    for xlsx_path, region_map, out_csv in jobs:
        if not xlsx_path.exists():
            raise FileNotFoundError(f"Source xlsx not found: {xlsx_path}")
        tidy = _extract_one(xlsx_path, region_map)
        header = (
            "# MiD 2023 oekonomischer Status x Haushaltstyp x Region.\n"
            "# Generated by scripts/extract_mid_status_by_hhtype.py from\n"
            f"# {xlsx_path.name} (sheet 'MiD Tabellen').\n"
            "# share_pct = P(hhtype | status, region) in percent; base_weighted =\n"
            "# weighted base (persons) of the (status, region) cell.\n"
        )
        with open(out_csv, "w", encoding="utf-8", newline="") as handle:
            handle.write(header)
            tidy.to_csv(handle, index=False)
        print(f"[extract_mid_status_by_hhtype] wrote {out_csv}")
        written.append(out_csv)

    if args.git_add:
        import subprocess
        for out_csv in written:
            subprocess.run(["git", "add", "-f", str(out_csv)], cwd=str(REPO), check=True)
        print(f"[extract_mid_status_by_hhtype] git add -f {len(written)} CSVs")


if __name__ == "__main__":
    main()
