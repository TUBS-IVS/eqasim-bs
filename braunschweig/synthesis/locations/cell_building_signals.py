"""Collapse the prepared 100 m cell columns to the 3-class building/dwelling
signals + dwelling-size histogram the home matcher needs (census-internal)."""
from __future__ import annotations
import pandas as pd
from pandas.api.types import is_extension_array_dtype, is_numeric_dtype

THREE_CLASSES = ("efh_zfh", "mfh", "sonst")
_S = "_100m_Gitter"
BUILDING_COUNT_COLS = {
    "efh_zfh": (f"FreiEFH_Geb_Gebaeudetyp_Groesse{_S}", f"EFH_DHH_Geb_Gebaeudetyp_Groesse{_S}",
                f"EFH_Reihenhaus_Geb_Gebaeudetyp_Groesse{_S}", f"Freist_ZFH_Geb_Gebaeudetyp_Groesse{_S}",
                f"ZFH_DHH_Geb_Gebaeudetyp_Groesse{_S}", f"ZFH_Reihenhaus_Geb_Gebaeudetyp_Groesse{_S}"),
    "mfh": (f"MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse{_S}", f"MFH_7bis12Wohnungen_Geb_Gebaeudetyp_Groesse{_S}",
            f"MFH_13undmehrWohnungen_Geb_Gebaeudetyp_Groesse{_S}"),
    "sonst": (f"AndererGebaeudetyp_Geb_Gebaeudetyp_Groesse{_S}",),
}
DWELLING_COUNT_COLS = {
    "efh_zfh": (f"FreiEFH_Wohnung_Gebaeudetyp_Groesse{_S}", f"EFH_DHH_Wohnung_Gebaeudetyp_Groesse{_S}",
                f"EFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse{_S}", f"Freist_ZFH_Wohnung_Gebaeudetyp_Groesse{_S}",
                f"ZFH_DHH_Wohnung_Gebaeudetyp_Groesse{_S}", f"ZFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse{_S}"),
    "mfh": (f"MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse{_S}", f"MFH_7bis12Wohnungen_Wohnung_Gebaeudetyp_Groesse{_S}",
            f"MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse{_S}"),
    "sonst": (f"AndererGebaeudetyp_Wohnung_Gebaeudetyp_Groesse{_S}",),
}
OCCUPIED_COL = f"BewohntWhg_Leerstand{_S}"
_SIZE_DEF = [("unter30", 25.0), ("30bis39", 35.0), ("40bis49", 45.0), ("50bis59", 55.0),
             ("60bis69", 65.0), ("70bis79", 75.0), ("80bis89", 85.0), ("90bis99", 95.0),
             ("100bis109", 105.0), ("110bis119", 115.0), ("120bis129", 125.0), ("130bis139", 135.0),
             ("140bis149", 145.0), ("150bis159", 155.0), ("160bis169", 165.0), ("170bis179", 175.0),
             ("180undmehr", 190.0)]
SIZE_BIN_COLS = tuple(f"{p}_Flaeche_der_Wohnung_10m2_Intervalle{_S}" for p, _ in _SIZE_DEF)
SIZE_BIN_MIDPOINTS = tuple(m for _, m in _SIZE_DEF)
HOME_SIGNAL_COLUMNS = tuple(dict.fromkeys(
    column
    for columns in (*BUILDING_COUNT_COLS.values(), *DWELLING_COUNT_COLS.values())
    for column in columns
)) + (OCCUPIED_COL,) + SIZE_BIN_COLS


def validate_home_signal_size_bins(cells: pd.DataFrame) -> None:
    """Retain ``cell_signals`` errors before an optimized row projection.

    Only ``cell_signals``' size-bin loop visits every row before household
    matching. Native NumPy numeric columns cannot contain a malformed value and
    therefore take an O(number-of-columns) dtype fast path. For object and
    nullable extension columns, replay the original ``float(value or 0)`` in the
    same row-major, size-bin order without wrapping its exception.
    """
    non_native_size_columns = [
        column for column in SIZE_BIN_COLS
        if column in cells.columns
        and (not is_numeric_dtype(cells[column]) or is_extension_array_dtype(cells[column]))
    ]
    if not non_native_size_columns:
        return
    for _, row in cells[non_native_size_columns].iterrows():
        for column in non_native_size_columns:
            float(row.get(column, 0) or 0)


def select_home_signal_cells(cells: pd.DataFrame, cell_ids) -> pd.DataFrame:
    """Return source-ordered household cells with only home-matching signals.

    ``cell_signals`` is row-local and typed home matching only looks up cells that
    occur in the household frame. Keeping the source order preserves existing
    duplicate prepared-cell behaviour while avoiding unused size histograms.
    """
    validate_home_signal_size_bins(cells)
    selected_columns = ["ZENSUS100m"] + [
        column for column in HOME_SIGNAL_COLUMNS if column in cells.columns
    ]
    requested_ids = pd.Index(pd.Series(cell_ids).dropna().drop_duplicates())
    return cells.loc[cells["ZENSUS100m"].isin(requested_ids), selected_columns]


def _sum(df, cols):
    present = [c for c in cols if c in df.columns]
    return df[present].fillna(0).sum(axis=1) if present else pd.Series(0.0, index=df.index)


def cell_signals(cells: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame({"ZENSUS100m": cells["ZENSUS100m"].to_numpy()})
    for cls in THREE_CLASSES:
        out[f"geb_{cls}"] = _sum(cells, BUILDING_COUNT_COLS[cls]).to_numpy()
        out[f"whg_{cls}"] = _sum(cells, DWELLING_COUNT_COLS[cls]).to_numpy()
    out["occupied"] = (cells[OCCUPIED_COL].fillna(0).to_numpy()
                       if OCCUPIED_COL in cells.columns else 0.0)
    mids = SIZE_BIN_MIDPOINTS
    hist = []
    present_bins = [(c, mids[i]) for i, c in enumerate(SIZE_BIN_COLS) if c in cells.columns]
    for _, r in cells.iterrows():
        hist.append([(m, float(r[c])) for c, m in present_bins if float(r.get(c, 0) or 0) > 0])
    out["size_hist"] = hist
    return out
