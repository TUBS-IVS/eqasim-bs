"""Extract per-Bundesland mode and distance margins from GENESIS tables 12251-0105/-0106.

Source CSVs (semicolon-separated, UTF-8):
  mikrozensus2024_12251-0105_mode_by_bundesland.csv  -- mode-of-transport x Bundesland
  mikrozensus2024_12251-0106_distance_by_bundesland.csv -- commute distance x Bundesland

Layout (confirmed by inspection):
  - 0105: 6 metadata rows, then data rows with columns
    Jahr;Bundesland;Geschlecht;Stellung im Beruf;<val>;<flag> ... (11 value/flag pairs)
  - 0106: 8 metadata rows, then data rows with the same key columns followed by
    8 value/flag pairs (Unter 5 km ... Gleiches Grundstueck, Staendig wechselnd, Insgesamt)

We select rows where Geschlecht == "Insgesamt" AND "Stellung im Beruf" == "Insgesamt" to get
the aggregate over all sexes and employment categories.  Each statistic column arrives as a
value/flag PAIR: even offset = numeric value (or "/" for suppressed), odd offset = quality
flag (e.g. "e", "()", "").  Suppressed "/" is treated as 0.

Mode mapping (0105 -> eqasim modes, per design spec D-2):
  Bus + "U-Bahn, Strassenbahn" + "Eisenbahn, S-Bahn" -> pt
  "Pkw (Selbstfahrer)" + "Pkw (Mitfahrer)"            -> car   (Mitfahrer folded; no car_passenger)
  Fahrrad + "Elektrofahrrad / Pedelec"                -> bicycle
  "Kein Verkehrsmittel (zu Fuss)"                     -> walk
  Motorrad..., Sonstige                               -> dropped

Distance mapping (0106 -> 5 coarse bands, per design spec D-2):
  "Unter 5 km" -> "<5", "5 bis unter 10 km" -> "5-10", "10 bis unter 25 km" -> "10-25",
  "25 bis unter 50 km" -> "25-50", "50 km und mehr" -> "50+".
  "Gleiches Grundstueck" and "Staendig wechselnde Arbeitsstaette" are dropped.

Writes (with provenance header):
  mid_mode_margin_by_bundesland.csv     [bundesland, mode, share]
  mid_distance_margin_by_bundesland.csv [bundesland, coarse_band, share]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
MZ_DIR = REPO / "eqasim-data" / "data" / "braunschweig" / "mikrozensus"

# Input GENESIS CSVs.
_FILE_0105 = MZ_DIR / "mikrozensus2024_12251-0105_mode_by_bundesland.csv"
_FILE_0106 = MZ_DIR / "mikrozensus2024_12251-0106_distance_by_bundesland.csv"

# Number of metadata header rows before the first data row.
# 0105: rows 1-6 are metadata (title, description, units, two col-header rows).
# 0106: rows 1-8 are metadata (title spans 3 lines, two description rows, two col-header rows).
_SKIP_0105 = 6
_SKIP_0106 = 8

# Columns (value names only, in order, matching the even-offset positions after the 4 key fields).
_COLS_0105 = [
    "Bus",
    "U-Bahn, Strassenbahn",   # original: U-Bahn, Straßenbahn (normalised below)
    "Eisenbahn, S-Bahn",
    "Pkw (Selbstfahrer)",
    "Pkw (Mitfahrer)",
    "Motorrad, Motorroller, Moped, Mofa",
    "Fahrrad",
    "Elektrofahrrad / Pedelec",
    "Sonstige Verkehrsmittel",
    "Kein Verkehrsmittel (zu Fuss)",  # original: zu Fuß (normalised below)
    "Insgesamt",
]

_COLS_0106 = [
    "Unter 5 km",
    "5 bis unter 10 km",
    "10 bis unter 25 km",
    "25 bis unter 50 km",
    "50 km und mehr",
    "Gleiches Grundstueck",         # original: Gleiches Grundstück
    "Staendig wechselnde Arbeitsstaette",  # original: Ständig wechselnde Arbeitsstätte
    "Insgesamt",
]

# Mode groups: each maps to an eqasim mode label.
_MODE_GROUPS: dict[str, list[str]] = {
    "pt": ["Bus", "U-Bahn, Strassenbahn", "Eisenbahn, S-Bahn"],
    "car": ["Pkw (Selbstfahrer)", "Pkw (Mitfahrer)"],
    "bicycle": ["Fahrrad", "Elektrofahrrad / Pedelec"],
    "walk": ["Kein Verkehrsmittel (zu Fuss)"],
    # Motorrad..., Sonstige are intentionally dropped (see module docstring).
}

# Distance band mapping: source column name -> coarse band label.
_DISTANCE_MAP: dict[str, str] = {
    "Unter 5 km": "<5",
    "5 bis unter 10 km": "5-10",
    "10 bis unter 25 km": "10-25",
    "25 bis unter 50 km": "25-50",
    "50 km und mehr": "50+",
    # Gleiches Grundstueck and Staendig wechselnde Arbeitsstaette are dropped.
}


def _parse_value(token: str) -> float:
    """Return numeric value from a GENESIS cell token.

    "/" indicates suppressed data (cell withheld for confidentiality) and is
    mapped to 0.0.  Empty string also maps to 0.0.  All other tokens are
    parsed as float.
    """
    s = token.strip()
    if s in ("/", ""):
        return 0.0
    return float(s)


def _extract_values(tokens: list[str], cols: list[str]) -> dict[str, float]:
    """Map value column names to numeric values from a data row.

    The first 4 tokens are Jahr, Bundesland, Geschlecht, Stellung im Beruf.
    From index 4 onward, tokens alternate value / flag (every other token).
    The number of value columns must equal len(cols).

    Args:
        tokens: All semicolon-split tokens for one data row.
        cols: Ordered list of value-column names (flags omitted).

    Returns:
        Dict mapping each column name to its numeric value (0.0 for suppressed).
    """
    result: dict[str, float] = {}
    for i, col in enumerate(cols):
        token_index = 4 + i * 2  # 4 key fields, then value;flag pairs
        result[col] = _parse_value(tokens[token_index])
    return result


def parse_genesis_mode_margins(
    rows: list[list[str]],
    cols: list[str],
) -> pd.DataFrame:
    """Parse pre-filtered GENESIS 12251-0105 rows into eqasim mode shares.

    Each entry in `rows` is the full semicolon-split token list for one data row
    where Geschlecht == "Insgesamt" and Stellung im Beruf == "Insgesamt".  The
    function handles the value/flag PAIR structure internally.

    Mode groups (see module docstring):
      pt       = Bus + U-Bahn/Strassenbahn + Eisenbahn/S-Bahn
      car      = Pkw Selbstfahrer + Pkw Mitfahrer
      bicycle  = Fahrrad + Elektrofahrrad/Pedelec
      walk     = Kein Verkehrsmittel (zu Fuss)
    Motorrad and Sonstige are dropped; shares are renormalised to sum to 1
    over the four retained modes.

    Args:
        rows: List of token lists, one per Insgesamt/Insgesamt Bundesland row.
        cols: Ordered value-column names matching the even-offset tokens after
              the 4 key fields.  Column names may use ASCII stand-ins for
              German special characters (normalisation is applied internally).

    Returns:
        DataFrame with columns [bundesland, mode, share], one row per
        (bundesland, mode) combination.
    """
    records: list[dict] = []
    for tokens in rows:
        bundesland = tokens[1].strip()
        values = _extract_values(tokens, cols)
        mode_values: dict[str, float] = {}
        for mode, src_cols in _MODE_GROUPS.items():
            mode_values[mode] = sum(values.get(c, 0.0) for c in src_cols)
        total = sum(mode_values.values())
        if total == 0.0:
            # Skip rows with no usable data (all suppressed).
            continue
        for mode, raw in mode_values.items():
            records.append({
                "bundesland": bundesland,
                "mode": mode,
                "share": raw / total,
            })
    return pd.DataFrame(records, columns=["bundesland", "mode", "share"])


def parse_genesis_distance_margins(
    rows: list[list[str]],
    cols: list[str],
) -> pd.DataFrame:
    """Parse pre-filtered GENESIS 12251-0106 rows into coarse distance-band shares.

    Each entry in `rows` is the full semicolon-split token list for one data row
    where Geschlecht == "Insgesamt" and Stellung im Beruf == "Insgesamt".

    Distance band mapping (see module docstring):
      "<5" "5-10" "10-25" "25-50" "50+"
    "Gleiches Grundstueck" and "Staendig wechselnde Arbeitsstaette" are dropped.
    Shares are renormalised over the five retained bands.

    Args:
        rows: List of token lists, one per Insgesamt/Insgesamt Bundesland row.
        cols: Ordered value-column names matching the even-offset tokens.

    Returns:
        DataFrame with columns [bundesland, coarse_band, share], one row per
        (bundesland, coarse_band) combination.
    """
    records: list[dict] = []
    for tokens in rows:
        bundesland = tokens[1].strip()
        values = _extract_values(tokens, cols)
        band_values: dict[str, float] = {}
        for src_col, band_label in _DISTANCE_MAP.items():
            band_values[band_label] = values.get(src_col, 0.0)
        total = sum(band_values.values())
        if total == 0.0:
            continue
        for band_label, raw in band_values.items():
            records.append({
                "bundesland": bundesland,
                "coarse_band": band_label,
                "share": raw / total,
            })
    return pd.DataFrame(records, columns=["bundesland", "coarse_band", "share"])


def _normalise_column_name(name: str) -> str:
    """Replace German special characters in a column name with ASCII equivalents.

    This allows the pure parser functions (which use ASCII column name constants)
    to be matched against column names read from the actual UTF-8 GENESIS CSVs.
    """
    return (
        name
        .replace("ä", "a")   # a-umlaut
        .replace("ö", "o")   # o-umlaut
        .replace("ü", "u")   # u-umlaut
        .replace("Ä", "A")   # A-umlaut
        .replace("Ö", "O")   # O-umlaut
        .replace("Ü", "U")   # U-umlaut
        .replace("ß", "ss")  # sharp-s
    )


def _read_genesis_csv(
    path: Path,
    skip_rows: int,
) -> tuple[list[list[str]], list[str]]:
    """Read a GENESIS semicolon-CSV and return Insgesamt/Insgesamt data rows plus column names.

    The column names are extracted from the last metadata row (the one containing the
    per-statistic labels such as "Bus", "Unter 5 km" etc.).  German special characters
    in column names are normalised to ASCII so they match the constants defined above.

    Data rows where Geschlecht (index 2) and "Stellung im Beruf" (index 3) are both
    "Insgesamt" are selected.  Only rows where the Bundesland field (index 1) is
    non-empty and not a footnote marker are kept.

    Args:
        path: Path to the semicolon-separated GENESIS CSV file.
        skip_rows: Number of metadata rows before the first data row.

    Returns:
        Tuple of (filtered_data_rows, value_column_names).
        filtered_data_rows: list of token lists for the selected rows.
        value_column_names: list of unique value-column names (ASCII-normalised),
                            one per value/flag pair.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # The last metadata row (skip_rows - 1, 0-indexed) contains the per-column labels.
    # Columns 0-3 are key fields (Jahr, Bundesland, Geschlecht, Stellung im Beruf).
    # From index 4 onward each label appears twice (value header, flag header).
    # We take every other entry starting at index 4 to get the unique value-column names.
    col_header_line = lines[skip_rows - 1]
    all_col_tokens = [_normalise_column_name(t) for t in col_header_line.split(";")]
    # First 4 are key-field placeholders (empty strings in the GENESIS file).
    value_col_names = all_col_tokens[4::2]

    data_rows: list[list[str]] = []
    for line in lines[skip_rows:]:
        tokens = line.split(";")
        if len(tokens) < 4:
            continue
        bundesland = tokens[1].strip()
        geschlecht = tokens[2].strip()
        stellung = tokens[3].strip()
        # Skip footnotes, empty lines and the copyright tail rows.
        if not bundesland or bundesland.startswith("__") or bundesland.startswith("(c)"):
            continue
        if geschlecht == "Insgesamt" and stellung == "Insgesamt":
            data_rows.append(tokens)

    return data_rows, value_col_names


def _write_with_provenance(
    df: pd.DataFrame,
    path: Path,
    genesis_table: str,
    year: int,
) -> None:
    """Write a DataFrame to CSV with a provenance comment header.

    The file begins with a '#'-prefixed line identifying the GENESIS source table and
    year so that downstream users can trace the data origin without external notes.

    Args:
        df: DataFrame to write.
        path: Output file path.
        genesis_table: GENESIS table identifier (e.g. "12251-0105").
        year: Survey reference year.
    """
    provenance = (
        f"# Source: GENESIS table {genesis_table}, Mikrozensus {year}, "
        f"Erwerbstaetige aus Hauptwohnsitzhaushalten; "
        f"Geschlecht=Insgesamt, Stellung im Beruf=Insgesamt; "
        f"generated by scripts/extract_mikrozensus_bundesland_margins.py\n"
    )
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(provenance)
        df.to_csv(fh, index=False)
    print(f"Wrote {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract per-Bundesland Mikrozensus mode + distance margins."
    )
    parser.add_argument(
        "--mz-dir",
        type=Path,
        default=MZ_DIR,
        help="Directory containing the GENESIS input CSVs and where output CSVs are written.",
    )
    args = parser.parse_args()
    mz_dir: Path = args.mz_dir

    # ------------------------------------------------------------------ mode
    rows_0105, cols_0105 = _read_genesis_csv(
        mz_dir / "mikrozensus2024_12251-0105_mode_by_bundesland.csv",
        skip_rows=_SKIP_0105,
    )
    print(f"0105: {len(rows_0105)} Insgesamt/Insgesamt rows, {len(cols_0105)} value columns")
    print(f"      columns: {cols_0105}")

    mode_df = parse_genesis_mode_margins(rows_0105, cols_0105)
    _write_with_provenance(
        mode_df,
        mz_dir / "mid_mode_margin_by_bundesland.csv",
        genesis_table="12251-0105",
        year=2024,
    )

    # --------------------------------------------------------------- distance
    rows_0106, cols_0106 = _read_genesis_csv(
        mz_dir / "mikrozensus2024_12251-0106_distance_by_bundesland.csv",
        skip_rows=_SKIP_0106,
    )
    print(f"0106: {len(rows_0106)} Insgesamt/Insgesamt rows, {len(cols_0106)} value columns")
    print(f"      columns: {cols_0106}")

    distance_df = parse_genesis_distance_margins(rows_0106, cols_0106)
    _write_with_provenance(
        distance_df,
        mz_dir / "mid_distance_margin_by_bundesland.csv",
        genesis_table="12251-0106",
        year=2024,
    )

    # ---------------------------------------------------------------- sanity
    print("\n--- Niedersachsen mode ---")
    print(mode_df[mode_df["bundesland"] == "Niedersachsen"].to_string(index=False))
    print("\n--- Sachsen-Anhalt mode ---")
    print(mode_df[mode_df["bundesland"] == "Sachsen-Anhalt"].to_string(index=False))
    print("\n--- Thueringen mode ---")
    # Thuringia is spelled "Thueringen" in ASCII but "Thüringen" in the CSV.
    thuringia_name = "Thüringen"
    print(mode_df[mode_df["bundesland"] == thuringia_name].to_string(index=False))

    print("\n--- Niedersachsen distance ---")
    print(distance_df[distance_df["bundesland"] == "Niedersachsen"].to_string(index=False))
    print("\n--- Sachsen-Anhalt distance ---")
    print(distance_df[distance_df["bundesland"] == "Sachsen-Anhalt"].to_string(index=False))
    print(f"\n--- {thuringia_name} distance ---")
    print(distance_df[distance_df["bundesland"] == thuringia_name].to_string(index=False))


if __name__ == "__main__":
    main()
