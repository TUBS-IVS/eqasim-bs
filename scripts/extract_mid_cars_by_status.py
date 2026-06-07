"""Extract the MiD 2023 'Anzahl Autos im HH x Status/Haushaltstyp / Raumtyp' tables.

Source (local-only raw xlsx, exported from the MiD 2023 online table tool,
sheet ``MiD Tabellen``):

  * ``mid2023_cars_by_status_hhtype.xlsx``
        5 stacked blocks, one per "Anzahl Autos im HH in Gruppen (0 bis 4+)"
        level (kein Auto, 1 Auto, 2 Autos, 3 Autos, 4 Autos und mehr). Within a
        block: rows = Haushaltstyp (Groesse/Alter/Kinder, same categories as the
        status_by_hhtype tables), columns = oekonomischer Status (sehr niedrig ..
        sehr hoch). Each block column gives P(Haushaltstyp | num_cars, status)
        (column %) and the "Basis gewichtet" row gives the (num_cars, status)
        weighted household base.
  * ``mid2023_cars_by_raumtyp.xlsx``
        Same "Anzahl Autos" blocks, columns = RegioStaR Raumtyp (7 categories).
        The body rows are the trivial 100 %; the informative content is the
        "Basis gewichtet" row = the (num_cars, raumtyp) weighted household base,
        from which P(num_cars | raumtyp) is recovered downstream.

3 / 4+ fold
-----------
The MiD "Anzahl Autos" grouping has 5 levels (0/1/2/3/4+) but the MiD H7 Kreis
CONTROL (mid2023_H7_cars_by_kreis.csv) has 4 categories 0/1/2/3 where 3 == "3 or
more". To couple the two, the "3 Autos" and "4 Autos und mehr" blocks are FOLDED
into a single canonical category 3:

  * status x hhtype: per (hhtype, status) the column % is converted back to a
    weighted household count (share_pct/100 * base_weighted), the counts of the
    3-car and 4+-car blocks are summed, and the folded share % is recomputed
    against the folded base (base(3) + base(4+)). This is the exact aggregation
    of two car-count buckets into one (no assumption beyond "3+" = 3 or 4+).
  * raumtyp: the (num_cars, raumtyp) weighted bases of the 3-car and 4+-car
    blocks are simply summed per raumtyp.

Output (committed tidy CSVs, long form):

    eqasim-data/data/braunschweig/mid/mid2023_cars_by_status_hhtype.csv
        columns [num_cars, hhtype, status, share_pct, base_weighted]
    eqasim-data/data/braunschweig/mid/mid2023_cars_by_raumtyp.csv
        columns [num_cars, region, base_weighted]

MiD suppression/zero symbols ("-", ".", "/", "()", empty) are coerced to 0.0
share_pct explicitly and the coercion counts are logged (no silent fallbacks).

The derived CSVs are force-added to git (``git add -f``) to match the existing
committed MiD-table pattern, even though the ``eqasim-data`` tree is gitignored.

Regenerate with:

    python scripts/extract_mid_cars_by_status.py --git-add

Provenance: MiD 2023 (BMDV / infas), regional sample online table tool, table
"Anzahl Autos im HH in Gruppen (0 bis 4+)" cross-tabulated by oekonomischer
Status x Haushaltstyp and by Raumtyp, exported 2026-06.
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

from braunschweig.data.mid.cars_by_status import (  # noqa: E402
    CAR_LEVEL_LABEL_TO_COUNT,
)
from braunschweig.data.mid.status_by_hhtype import (  # noqa: E402
    HHTYPE_LABEL_TO_KEY,
    REGION_LABEL_TO_KEY_RAUMTYP,
    STATUS_LABEL_TO_KEY,
)

SHEET = "MiD Tabellen"

# Cell values that MiD uses for "no / suppressed / rounds to zero" entries.
_SUPPRESSION_TOKENS = {"-", ".", "/", "()", "(", ")", "", "nan"}

# Marker substrings used to locate the block anchors (robust to extra spaces).
_CARS_HEADER_MARKER = "anzahl autos im hh in gruppen"
_BASE_WEIGHTED_MARKER = "basis gewichtet"
_TOTAL_MARKER = "total"

# Pre-fold car count that the H7 control's "3+" category absorbs.
_FOLD_TARGET = 3


def _normalise_ws(text) -> str:
    return re.sub(r"\s+", " ", str(text)).strip()


def _coerce_percent(raw) -> tuple[float, bool]:
    """Return (share_pct, was_coerced). Suppression tokens / blanks -> 0.0."""
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


def _find_cars_for_header(cell: str) -> int | None:
    """Map a block-header cell to a (pre-fold) car count, or None."""
    low = _normalise_ws(cell).lower()
    if _CARS_HEADER_MARKER not in low:
        return None
    # The level follows the last ':' (then strip the leading '-'), e.g.
    # 'Anzahl Autos im HH in Gruppen (0 bis 4+): - 4 Autos und mehr'.
    level = low.split(":")[-1].strip().lstrip("-").strip()
    return CAR_LEVEL_LABEL_TO_COUNT.get(level)


def _locate_region_columns(
    df_raw: pd.DataFrame, region_label_to_key: dict[str, str] | None,
) -> dict[int, str]:
    """Locate the column index -> region/status key map from the header row.

    For the status x hhtype file pass ``region_label_to_key=STATUS_LABEL_TO_KEY``
    (the columns are status levels); for the raumtyp file pass the raumtyp map.
    """
    n_rows = len(df_raw)
    for i in range(n_rows):
        row = df_raw.iloc[i]
        found: dict[int, str] = {}
        for c in range(2, df_raw.shape[1]):
            label = _normalise_ws(row.iloc[c])
            if label in region_label_to_key:
                found[c] = region_label_to_key[label]
        if found:
            return found
    raise RuntimeError(
        "extract_mid_cars_by_status: could not locate any column header. "
        "Check the column-label mapping vs. the sheet."
    )


def _block_bounds(df_raw: pd.DataFrame) -> list[tuple[int, int, int]]:
    """Return ``[(start_row, end_row, car_count)]`` for each 'Anzahl Autos' block."""
    n_rows = len(df_raw)
    starts: list[tuple[int, int]] = []
    for i in range(n_rows):
        cars = _find_cars_for_header(_normalise_ws(df_raw.iloc[i, 0]))
        if cars is not None:
            starts.append((i, cars))
    if not starts:
        raise RuntimeError("extract_mid_cars_by_status: no 'Anzahl Autos' block headers found.")
    bounds = []
    for bi, (start, cars) in enumerate(starts):
        end = starts[bi + 1][0] if bi + 1 < len(starts) else n_rows
        bounds.append((start, end, cars))
    return bounds


def _read_base_weighted(df_raw: pd.DataFrame, start: int, end: int,
                        cols: dict[int, str]) -> dict[int, float]:
    for i in range(start, end):
        if _normalise_ws(df_raw.iloc[i, 0]).lower() == _BASE_WEIGHTED_MARKER:
            return {c: _coerce_base(df_raw.iloc[i, c]) for c in cols}
    raise RuntimeError(
        f"extract_mid_cars_by_status: 'Basis gewichtet' row missing in block "
        f"starting at row {start}."
    )


def parse_status_hhtype_sheet(df_raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Parse the status x hhtype sheet into folded tidy long form.

    Returns ``(tidy_df, coercion_counts)``. ``share_pct`` is P(hhtype | num_cars,
    status) after folding the 3-car and 4+-car blocks into category 3.
    """
    cols = _locate_region_columns(df_raw, STATUS_LABEL_TO_KEY)
    coercion = {"suppression": 0, "blank": 0, "value": 0}

    # Accumulate per (num_cars, status) base and per (num_cars, hhtype, status)
    # count (share_pct/100 * base) so the fold is an exact bucket aggregation.
    base_by: dict[tuple[int, str], float] = {}
    count_by: dict[tuple[int, str, str], float] = {}

    for start, end, cars in _block_bounds(df_raw):
        car_key = _FOLD_TARGET if cars >= _FOLD_TARGET else cars
        base_weighted = _read_base_weighted(df_raw, start, end, cols)
        for c, status in cols.items():
            base_by[(car_key, status)] = base_by.get((car_key, status), 0.0) + base_weighted[c]

        for i in range(start, end):
            label = _normalise_ws(df_raw.iloc[i, 0])
            if label.lower() == _TOTAL_MARKER:
                continue
            hhtype = HHTYPE_LABEL_TO_KEY.get(label)
            if hhtype is None:
                continue
            for c, status in cols.items():
                share, coerced = _coerce_percent(df_raw.iloc[i, c])
                if coerced:
                    raw = df_raw.iloc[i, c]
                    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                        coercion["blank"] += 1
                    else:
                        coercion["suppression"] += 1
                else:
                    coercion["value"] += 1
                count = (share / 100.0) * base_weighted[c]
                key = (car_key, hhtype, status)
                count_by[key] = count_by.get(key, 0.0) + count

    records = []
    for (car_key, hhtype, status), count in count_by.items():
        base = base_by.get((car_key, status), 0.0)
        share_pct = (100.0 * count / base) if base > 0 else 0.0
        records.append({
            "num_cars": car_key,
            "hhtype": hhtype,
            "status": status,
            "share_pct": round(share_pct, 6),
            "base_weighted": round(base, 3),
        })
    tidy = pd.DataFrame.from_records(
        records, columns=["num_cars", "hhtype", "status", "share_pct", "base_weighted"]
    ).sort_values(["num_cars", "status", "hhtype"]).reset_index(drop=True)
    return tidy, coercion


def parse_raumtyp_sheet(df_raw: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, int]]:
    """Parse the raumtyp sheet into folded tidy long form ([num_cars, region, base_weighted]).

    Only the per-(num_cars, raumtyp) weighted base is informative (the body rows
    are the trivial 100 %), so no percentage coercion is needed; we still return a
    coercion dict (all zero) for a uniform logging interface.
    """
    cols = _locate_region_columns(df_raw, REGION_LABEL_TO_KEY_RAUMTYP)
    base_by: dict[tuple[int, str], float] = {}
    for start, end, cars in _block_bounds(df_raw):
        car_key = _FOLD_TARGET if cars >= _FOLD_TARGET else cars
        base_weighted = _read_base_weighted(df_raw, start, end, cols)
        for c, region in cols.items():
            base_by[(car_key, region)] = base_by.get((car_key, region), 0.0) + base_weighted[c]

    records = [
        {"num_cars": car_key, "region": region, "base_weighted": round(base, 3)}
        for (car_key, region), base in base_by.items()
    ]
    tidy = pd.DataFrame.from_records(
        records, columns=["num_cars", "region", "base_weighted"]
    ).sort_values(["num_cars", "region"]).reset_index(drop=True)
    return tidy, {"suppression": 0, "blank": 0, "value": len(records)}


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

    status_xlsx = mid_dir / "mid2023_cars_by_status_hhtype.xlsx"
    raumtyp_xlsx = mid_dir / "mid2023_cars_by_raumtyp.xlsx"
    for p in (status_xlsx, raumtyp_xlsx):
        if not p.exists():
            raise FileNotFoundError(f"Source xlsx not found: {p}")

    df_status = pd.read_excel(status_xlsx, sheet_name=SHEET, header=None)
    tidy_status, coerce_status = parse_status_hhtype_sheet(df_status)
    print(
        f"[extract_mid_cars_by_status] {status_xlsx.name}: {len(tidy_status)} rows, "
        f"{tidy_status['num_cars'].nunique()} car categories "
        f"(folded 3/4+ -> {_FOLD_TARGET}) x {tidy_status['hhtype'].nunique()} hhtypes "
        f"x {tidy_status['status'].nunique()} status; "
        f"coercion: numeric={coerce_status['value']}, "
        f"suppression-token={coerce_status['suppression']}, blank={coerce_status['blank']}."
    )

    df_raumtyp = pd.read_excel(raumtyp_xlsx, sheet_name=SHEET, header=None)
    tidy_raumtyp, _ = parse_raumtyp_sheet(df_raumtyp)
    print(
        f"[extract_mid_cars_by_status] {raumtyp_xlsx.name}: {len(tidy_raumtyp)} rows, "
        f"{tidy_raumtyp['num_cars'].nunique()} car categories "
        f"(folded 3/4+ -> {_FOLD_TARGET}) x {tidy_raumtyp['region'].nunique()} raumtyp."
    )

    out_status = mid_dir / "mid2023_cars_by_status_hhtype.csv"
    out_raumtyp = mid_dir / "mid2023_cars_by_raumtyp.csv"

    header_status = (
        "# MiD 2023 Anzahl Autos im HH x oekonomischer Status x Haushaltstyp.\n"
        "# Generated by scripts/extract_mid_cars_by_status.py from\n"
        f"# {status_xlsx.name} (sheet 'MiD Tabellen').\n"
        "# num_cars folded to 0/1/2/3 (3 == '3 or more' = MiD '3 Autos' + '4 Autos\n"
        "# und mehr'), matching the MiD H7 Kreis control. share_pct = P(hhtype |\n"
        "# num_cars, status) in percent; base_weighted = weighted household base\n"
        "# of the (num_cars, status) cell.\n"
    )
    header_raumtyp = (
        "# MiD 2023 Anzahl Autos im HH x RegioStaR Raumtyp (weighted bases).\n"
        "# Generated by scripts/extract_mid_cars_by_status.py from\n"
        f"# {raumtyp_xlsx.name} (sheet 'MiD Tabellen').\n"
        "# num_cars folded to 0/1/2/3 (3 == '3 or more'). base_weighted = weighted\n"
        "# household base of the (num_cars, raumtyp) cell; P(num_cars | raumtyp) is\n"
        "# the base normalised over num_cars within a raumtyp.\n"
    )

    for out_csv, header, tidy in (
        (out_status, header_status, tidy_status),
        (out_raumtyp, header_raumtyp, tidy_raumtyp),
    ):
        with open(out_csv, "w", encoding="utf-8", newline="") as handle:
            handle.write(header)
            tidy.to_csv(handle, index=False)
        print(f"[extract_mid_cars_by_status] wrote {out_csv}")

    if args.git_add:
        import subprocess
        for out_csv in (out_status, out_raumtyp):
            subprocess.run(["git", "add", "-f", str(out_csv)], cwd=str(REPO), check=True)
        print("[extract_mid_cars_by_status] git add -f 2 CSVs")


if __name__ == "__main__":
    main()
