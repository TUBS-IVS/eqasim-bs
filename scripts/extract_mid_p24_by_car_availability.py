"""Extract the MiD 2023 'P24.1 Fahrkartenart x Pkw-Verfuegbarkeit' cross-tab.

Source (local-only raw xlsx, exported from the MiD 2023 online table tool,
sheet 0 / "MiD Tabellen"):

  * ``mid2023_P24_1_by_car_availability.xlsx``

The sheet is a stacked layout: ONE block per Pkw-Verfuegbarkeit level
(jederzeit / gelegentlich / gar nicht / keine Angabe). Within each block the
data is presented as column-percentages of the age distribution per ticket
type, BUT the block also carries a ``Basis gewichtet`` row giving the
**weighted person count per Fahrkartenart** (and the group total in the first
data column). Those weighted counts are exactly what we need: dividing each
ticket count by the group total yields

    P(ticket | car_availability)

which is the carless<->PT-pass coupling the A6 hook consumes. Using the
weighted-base row (not the column-% age table) is the correct and direct source
for the conditional ticket distribution.

Mapping of the MiD car-availability levels to the canonical eqasim
car-availability vocabulary (``CAR_AVAILABILITY_CATEGORIES`` =
``{none, some, all}``):

    jederzeit     -> all     (car available at any time)
    gelegentlich  -> some    (car occasionally available)
    gar nicht     -> none    (no car available)
    keine Angabe  -> DROPPED (no informative car-availability state; including
                             it would fold an "unknown" answer into a concrete
                             coupling cell and bias the conditional. It is the
                             smallest block by far -- weighted base ~1.7 vs.
                             39-255 in the informative blocks -- so dropping it
                             discards only the ambiguous tail.)

Output (committed tidy CSV, one row per car-availability category):

    eqasim-data/data/braunschweig/mid/mid2023_P24_1_by_car_availability.csv

with columns ``[car_availability] + PT_TICKET_CATEGORIES``:

    car_availability   one of {none, some, all} (canonical eqasim key)
    <9 ticket columns> P(ticket | car_availability) probabilities, each ROW
                       summing to 1 (the 9 PT_TICKET_CATEGORIES in column order)

The weighted ticket counts are German-formatted thousands separators
(e.g. ``98.054`` = 98054); the ``keine_angabe`` ticket column for some blocks is
a plain integer (e.g. ``317``). All values are parsed by stripping the German
thousands separator. MiD suppression/zero symbols ("-", ".", "/", "()", empty,
"0") are coerced to 0.0 explicitly and the coercion counts are logged (no silent
fallbacks). A row whose ticket counts sum to zero would be a hard error (a block
must have a positive weighted base).

The derived CSV is force-added to git (``git add -f``) to match the existing
committed MiD-table pattern, even though the ``eqasim-data`` tree is gitignored.

Regenerate with:

    python scripts/extract_mid_p24_by_car_availability.py --git-add

Provenance: MiD 2023 (BMDV / infas), regional sample online table tool,
Tabelle A P24.1 ("ueblicherweise genutzte Fahrkartenart") cross-tabulated by
Pkw-Verfuegbarkeit, exported 2026-06.
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

from braunschweig.data.mid.reference_tables import (  # noqa: E402
    PT_TICKET_CATEGORIES,
)

SHEET_INDEX = 0

# Cell values that MiD uses for "no / suppressed / rounds to zero" entries.
# All are coerced to 0.0 explicitly (counted + logged).
_SUPPRESSION_TOKENS = {"-", ".", "/", "()", "(", ")", "", "nan", "0"}

# Marker substrings (whitespace-collapsed, lower-cased) used to anchor blocks.
_CAR_AVAILABILITY_HEADER_MARKER = "pkw-verf"  # 'Pkw-Verfuegbarkeit ...'
_BASE_WEIGHTED_MARKER = "basis gewichtet"
_TICKET_LABEL_ANCHOR = "einzelfahr"  # first ticket label cell within a block

# MiD Pkw-Verfuegbarkeit level (parsed from the block header, after the last
# '-') -> canonical eqasim car-availability key. "keine angabe" is intentionally
# absent: it is dropped (documented in the module docstring).
_CAR_AVAILABILITY_LEVEL_TO_KEY: dict[str, str] = {
    "jederzeit": "all",
    "gelegentlich": "some",
    "gar nicht": "none",
}
_CAR_AVAILABILITY_DROP_LEVELS = frozenset({"keine angabe"})

# Explicit Fahrkartenart label -> canonical PT_TICKET_CATEGORIES key. The MiD
# export wraps long labels across lines, inserting spaces inside words
# ("Einzelfahrsc hein"), and uses non-ASCII umlauts (e.g. "oeffentlichen"). To
# make the match robust to both effects, labels and the keys below are first run
# through :func:`_label_key` (whitespace-collapsed, lower-cased, non-ASCII
# stripped). This stays an EXPLICIT one-to-one lookup (no fuzzy matching, no
# silent column-order assumption) while tolerating the export's umlaut encoding.
_FAHRKARTENART_LABEL_TO_KEY_RAW: dict[str, str] = {
    "Einzelfahrsc hein, Tageskarte, Kurzstrecke": "einzelfahrschein",
    "Mehrfachkar te, Streifenkart e oder digitaler Tarif nach Entfernung":
        "mehrfachkarte",
    "Deutschlan dticket": "deutschlandticket",
    "andere Wochen- oder Monatskarte ohne Abonnemen t": "wochen_monat_ohne_abo",
    "andere Monatskarte im Abonnemen t oder Jahreskarte": "monat_abo_jahreskarte",
    "regionales/ lokales Jobticket, Firmenabo, Semestertic ket oder vergleichbar e Angebote":
        "jobticket_semesterticket",
    "anderes": "anderes",
    # "fahre nie mit oeffentlichen Verkehrsmitteln in meiner Region"; the umlaut
    # in "oeffentlichen" is stripped by _label_key, so the literal here is the
    # ASCII-normalised form.
    "fahre nie mit ffentlichen Verkehrsmit teln in meiner Region": "fahre_nie",
    "keine Angabe": "keine_angabe",
}


def _normalise_ws(text) -> str:
    """Collapse runs of whitespace and strip."""
    return re.sub(r"\s+", " ", str(text)).strip()


def _label_key(text) -> str:
    """Normalise a header label for matching: whitespace-collapsed, lower-cased,
    non-ASCII characters dropped.

    The MiD online-tool export encodes umlauts inconsistently (some exports carry
    a literal 'oe'/0xf6 'oe', others a replacement character); stripping non-ASCII
    makes the explicit label lookup robust to that without resorting to fuzzy
    matching.
    """
    collapsed = _normalise_ws(text).lower()
    return "".join(ch for ch in collapsed if ord(ch) < 128).strip()


# Matching dict keyed by the normalised label (see _label_key).
_FAHRKARTENART_LABEL_TO_KEY: dict[str, str] = {
    _label_key(label): key
    for label, key in _FAHRKARTENART_LABEL_TO_KEY_RAW.items()
}


def _coerce_count(raw) -> tuple[float, bool]:
    """Parse a German-formatted weighted count like '98.054' -> 98054.0.

    Returns ``(value, was_coerced)``. Suppression tokens, blanks and an explicit
    '0' map to 0.0 and are flagged as coerced so the caller can count them.
    """
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return 0.0, True
    s = _normalise_ws(raw)
    if s.lower() in _SUPPRESSION_TOKENS:
        return 0.0, True
    # German thousands separator '.' and decimal ',': strip the thousands dot,
    # convert a decimal comma to a dot. Weighted MiD bases are integers in
    # thousands notation (e.g. '98.054' = 98054), so removing all dots is safe.
    s = s.replace(".", "").replace(",", ".")
    try:
        return float(s), False
    except ValueError:
        return 0.0, True


def _ticket_columns(df_raw: pd.DataFrame, header_row: int) -> dict[int, str]:
    """Map sheet column index -> canonical PT ticket key from a label header row.

    The ticket labels live one row ABOVE the 'Spalten %' marker row, in the
    columns from index 2 onward (column 0 = row label, column 1 = 'Total').
    """
    cols: dict[int, str] = {}
    for c in range(2, df_raw.shape[1]):
        key = _FAHRKARTENART_LABEL_TO_KEY.get(_label_key(df_raw.iloc[header_row, c]))
        if key is not None:
            cols[c] = key
    return cols


def parse_car_availability_sheet(
    df_raw: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Parse the raw cross-tab sheet into the tidy one-row-per-car-availability
    form ``[car_availability] + PT_TICKET_CATEGORIES`` (row-normalised probs).

    Returns ``(tidy_df, coercion_counts)`` where ``coercion_counts`` reports how
    many ticket-count cells hit each coercion path (suppression token / blank),
    for transparent logging (no silent fallbacks).
    """
    n_rows = len(df_raw)

    # Locate block-header rows (one per Pkw-Verfuegbarkeit level).
    block_starts: list[tuple[int, str]] = []
    for i in range(n_rows):
        cell = _normalise_ws(df_raw.iloc[i, 0]).lower()
        if _CAR_AVAILABILITY_HEADER_MARKER in cell:
            level = cell.split("-")[-1].strip()
            block_starts.append((i, level))
    if not block_starts:
        raise RuntimeError(
            "extract_mid_p24_by_car_availability: no Pkw-Verfuegbarkeit block "
            "headers found (marker '%s')." % _CAR_AVAILABILITY_HEADER_MARKER
        )

    records: list[dict] = []
    coercion = {"suppression": 0, "blank": 0, "value": 0}
    dropped_levels: list[str] = []

    for bi, (start, level) in enumerate(block_starts):
        end = block_starts[bi + 1][0] if bi + 1 < len(block_starts) else n_rows

        if level in _CAR_AVAILABILITY_DROP_LEVELS:
            dropped_levels.append(level)
            continue
        car_availability = _CAR_AVAILABILITY_LEVEL_TO_KEY.get(level)
        if car_availability is None:
            raise RuntimeError(
                f"extract_mid_p24_by_car_availability: unmapped Pkw-"
                f"Verfuegbarkeit level {level!r}; extend "
                f"_CAR_AVAILABILITY_LEVEL_TO_KEY or "
                f"_CAR_AVAILABILITY_DROP_LEVELS."
            )

        # Find the ticket-label header row (the row carrying the first ticket
        # label) within this block.
        ticket_cols: dict[int, str] = {}
        for i in range(start, end):
            first = _normalise_ws(df_raw.iloc[i, 2]).lower()
            if first.startswith(_TICKET_LABEL_ANCHOR):
                ticket_cols = _ticket_columns(df_raw, i)
                break
        if len(ticket_cols) != len(PT_TICKET_CATEGORIES):
            raise RuntimeError(
                f"extract_mid_p24_by_car_availability: block '{level}' resolved "
                f"{len(ticket_cols)} of {len(PT_TICKET_CATEGORIES)} ticket "
                f"columns; check _FAHRKARTENART_LABEL_TO_KEY vs. the sheet."
            )

        # Find the 'Basis gewichtet' row (weighted person count per ticket).
        base_row = None
        for i in range(start, end):
            if _normalise_ws(df_raw.iloc[i, 0]).lower() == _BASE_WEIGHTED_MARKER:
                base_row = i
                break
        if base_row is None:
            raise RuntimeError(
                f"extract_mid_p24_by_car_availability: 'Basis gewichtet' row "
                f"missing in block '{level}'."
            )

        counts: dict[str, float] = {}
        for c, ticket_key in ticket_cols.items():
            value, coerced = _coerce_count(df_raw.iloc[base_row, c])
            if coerced:
                raw = df_raw.iloc[base_row, c]
                if raw is None or (isinstance(raw, float) and pd.isna(raw)):
                    coercion["blank"] += 1
                else:
                    coercion["suppression"] += 1
            else:
                coercion["value"] += 1
            counts[ticket_key] = value

        total = sum(counts.values())
        if total <= 0.0:
            raise RuntimeError(
                f"extract_mid_p24_by_car_availability: block '{level}' has a "
                f"non-positive weighted ticket total ({total}); cannot form "
                f"P(ticket | car_availability)."
            )

        record = {"car_availability": car_availability}
        for ticket_key in PT_TICKET_CATEGORIES:
            record[ticket_key] = counts[ticket_key] / total
        records.append(record)

    if dropped_levels:
        print(
            "[extract_mid_p24_by_car_availability] dropped car-availability "
            f"level(s) {dropped_levels} (folded into none/some/all is not "
            "informative; see module docstring)."
        )

    tidy = pd.DataFrame.from_records(
        records, columns=["car_availability"] + list(PT_TICKET_CATEGORIES)
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
        help="Force-add the generated CSV to git (git add -f).",
    )
    args = parser.parse_args()

    mid_dir = Path(args.data_path) / "braunschweig" / "mid"
    xlsx_path = mid_dir / "mid2023_P24_1_by_car_availability.xlsx"
    out_csv = mid_dir / "mid2023_P24_1_by_car_availability.csv"

    if not xlsx_path.exists():
        raise FileNotFoundError(f"Source xlsx not found: {xlsx_path}")

    df_raw = pd.read_excel(xlsx_path, sheet_name=SHEET_INDEX, header=None)
    tidy, coercion = parse_car_availability_sheet(df_raw)

    total_cells = sum(coercion.values())
    print(
        "[extract_mid_p24_by_car_availability] "
        f"{len(tidy)} car-availability rows x {len(PT_TICKET_CATEGORIES)} "
        f"ticket columns; coercion: numeric={coercion['value']}, "
        f"suppression-token={coercion['suppression']}, blank={coercion['blank']} "
        f"(of {total_cells} ticket-count cells)"
    )
    # Sanity: every row is a probability vector summing to 1.
    row_sums = tidy[list(PT_TICKET_CATEGORIES)].sum(axis=1)
    if not ((row_sums - 1.0).abs() < 1e-9).all():
        raise RuntimeError(
            "extract_mid_p24_by_car_availability: a row does not sum to 1 after "
            f"normalisation: {row_sums.tolist()}"
        )
    for _, row in tidy.iterrows():
        print(
            f"[extract_mid_p24_by_car_availability]   {row['car_availability']:>4}: "
            "deutschlandticket="
            f"{row['deutschlandticket']:.1%}, fahre_nie={row['fahre_nie']:.1%}"
        )

    header = (
        "# MiD 2023 P24.1 (Fahrkartenart) x Pkw-Verfuegbarkeit cross-tab.\n"
        "# Generated by scripts/extract_mid_p24_by_car_availability.py from\n"
        f"# {xlsx_path.name} (sheet index 0).\n"
        "# Each row is P(ticket | car_availability) over PT_TICKET_CATEGORIES;\n"
        "# the 9 ticket columns sum to 1 per row. car_availability in\n"
        "# {none, some, all} (MiD jederzeit->all, gelegentlich->some,\n"
        "# gar nicht->none; 'keine Angabe' dropped).\n"
    )
    with open(out_csv, "w", encoding="utf-8", newline="") as handle:
        handle.write(header)
        tidy.to_csv(handle, index=False)
    print(f"[extract_mid_p24_by_car_availability] wrote {out_csv}")

    if args.git_add:
        import subprocess
        subprocess.run(["git", "add", "-f", str(out_csv)], cwd=str(REPO), check=True)
        print(f"[extract_mid_p24_by_car_availability] git add -f {out_csv}")


if __name__ == "__main__":
    main()
