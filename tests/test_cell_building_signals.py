import pandas as pd
from braunschweig.synthesis.locations import cell_building_signals as cbs


def _one_cell():
    row = {"ZENSUS100m": "CELL1"}
    for cols in list(cbs.BUILDING_COUNT_COLS.values()) + list(cbs.DWELLING_COUNT_COLS.values()):
        for c in cols:
            row[c] = 0.0
    for c in cbs.SIZE_BIN_COLS:
        row[c] = 0.0
    row[cbs.OCCUPIED_COL] = 0.0
    # 3 EFH buildings, 1 MFH building; 3 EFH dwellings, 8 MFH dwellings; 10 occupied.
    row["FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter"] = 3.0
    row["MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter"] = 1.0
    row["FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"] = 3.0
    row["MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"] = 8.0
    row[cbs.OCCUPIED_COL] = 10.0
    row["90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter"] = 10.0
    return pd.DataFrame([row])


def test_cell_signals_collapses_to_three_classes():
    out = cbs.cell_signals(_one_cell()).set_index("ZENSUS100m").loc["CELL1"]
    assert out["geb_efh_zfh"] == 3 and out["geb_mfh"] == 1 and out["geb_sonst"] == 0
    assert out["whg_efh_zfh"] == 3 and out["whg_mfh"] == 8
    assert out["occupied"] == 10
    assert out["size_hist"] == [(95.0, 10.0)]
