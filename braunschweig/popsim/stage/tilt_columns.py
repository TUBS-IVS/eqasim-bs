"""Income-tilt cell-column selection (issue #136 single-parquet-read helpers).

- :func:`tilt_extra_load_columns` -- extend the parquet load column list with
  the income-tilt cell columns (rent, Eigentuemerquote, HH weight).
- :func:`extract_tilt_cells` -- build the income-tilt working frame from the
  already-loaded, already-ZGB-filtered cells frame.

Together these let the income spatial tilt (``income_spatial_tilt``) fetch its
cell columns in the SAME ``load_control_cells`` parquet read used for the
regular control cells, instead of a second national parquet scan.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

import pandas as pd

# Tilt-specific cell columns (cleaned parquet names; see prepared_cells.clean_col_name):
#   raw: "durchschnMieteQM_Durchschn_Nettokaltmiete_100m-Gitter"
#     -> clean: "durchschnMieteQM_Durchschn_Nettokaltmiete_100m_Gitter"
#   raw: "Eigentuemerquote_Eigentuemerquote_100m-Gitter"
#     -> clean: "Eigentuemerquote_Eigentuemerquote_100m_Gitter"
# Suppression-ADJUSTED household totals are the correct tilt weight: the raw cell
# totals suppress small cells (NaN), making them 0-weight and biasing the Kreis-mean
# normalization toward large dense cells only. The _adj column fills suppressed cells
# with the cleancensus imputed estimates so every cell carries a proper weight.
_TILT_RENT_COL = "durchschnMieteQM_Durchschn_Nettokaltmiete_100m_Gitter"
_TILT_QUOTE_COL = "Eigentuemerquote_Eigentuemerquote_100m_Gitter"
_TILT_HH_COL = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
_TILT_ARS_COL = "RegionalSchlussel_ARS"


def tilt_extra_load_columns(enabled: bool, load_cols: list[str]) -> list[str]:
    """Extend the parquet load column list with the income-tilt cell columns.

    Issue #136: the tilt columns are fetched in the SINGLE ``load_control_cells``
    read instead of a second national parquet scan. When ``enabled`` is False the
    input list is returned as an unchanged copy (OFF path byte-identical);
    ``load_control_cells`` silently skips columns absent from the parquet, exactly
    like the old raw-name mapping did.
    """
    out = list(load_cols)
    if not enabled:
        return out
    for column in (_TILT_RENT_COL, _TILT_QUOTE_COL, _TILT_HH_COL):
        if column not in out:
            out.append(column)
    return out


def extract_tilt_cells(cells: pd.DataFrame) -> pd.DataFrame:
    """Build the income-tilt working frame from the already-loaded cells frame.

    Selects the cell id + the tilt columns (rent, Eigentuemerquote, HH weight,
    ARS) that are present; absent optional columns stay absent, matching the old
    raw-parquet mapping (the downstream code then warns and uses a neutral
    index / uniform weight). The cells frame is already ZGB-filtered, so no
    row filtering is needed (this replaces a full national-row parquet re-read).
    """
    if "ZENSUS100m" not in cells.columns:
        raise ValueError(
            "[popsim.stage] cells frame carries no 'ZENSUS100m' column; cannot "
            "build the income-tilt cell frame from it."
        )
    columns = ["ZENSUS100m"] + [
        c for c in (_TILT_RENT_COL, _TILT_QUOTE_COL, _TILT_HH_COL, _TILT_ARS_COL)
        if c in cells.columns
    ]
    return cells[columns].copy()
