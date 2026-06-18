"""DIAGNOSTIC: where (if anywhere) does LoD2 height actually change home-match typing?

Runs on the real Salzgitter popsim population (Kreis 03102). Two questions:
  (1) how often does volume(height) typing differ from area-only typing, and
  (2) in the cells with NO census building-type signal (where the matcher currently
      forces all footprints to EFH and ignores height), does popsim actually place
      MFH/sonst households, and are there tall footprints that could host them?
This quantifies the ceiling of what height-based typing could fix.

Run (conda eqasim env, with GDAL_DATA/PROJ_LIB set):
  python scripts/lod2_height_diagnostic.py
"""
from __future__ import annotations
import sys
import numpy as np, pandas as pd, geopandas as gpd
REPO = r"C:/Users/bienzeisler/Documents/GitHub/eqasim-bs"
sys.path.insert(0, REPO)
from braunschweig.synthesis.locations import cell_building_signals as cbs
from braunschweig.synthesis.locations.home_cell import building_cell_id
from braunschweig.synthesis.locations import building_typing as BT
from braunschweig.popsim.prepared_cells import clean_col_name
from braunschweig.data.buildings import join_lod2_heights
import pyarrow.parquet as pq

KREIS = "03102"; SEED = 20260617
POP = r"C:/Users/bienzeisler/AppData/Local/Temp/salzgitter_popsim_population.p"
CENSUS = REPO + "/eqasim-data/data/braunschweig/popsim/cells/zensus2022_grid_100m_de_prepared.parquet"
ALKIS = REPO + "/eqasim-data/data/braunschweig/preprocessed/alkis_buildings.parquet"
HEIGHTS = REPO + "/eqasim-data/data/braunschweig/preprocessed/lod2_heights.parquet"
THREE = cbs.THREE_CLASSES


def load_census_kreis():
    names = list(pq.ParquetFile(CENSUS).schema.names)
    c2r = {clean_col_name(n): n for n in names}
    want = (["GITTER_ID_100m", "x_mp_100m", "y_mp_100m"]
            + [clean_col_name(c) for cs in cbs.BUILDING_COUNT_COLS.values() for c in cs]
            + [clean_col_name(c) for cs in cbs.DWELLING_COUNT_COLS.values() for c in cs]
            + [cbs.OCCUPIED_COL, clean_col_name("LeerstehendWhg_Leerstand_100m-Gitter")]
            + [clean_col_name(c) for c in cbs.SIZE_BIN_COLS])
    ars = next(c for c in c2r if "Regional" in c); want.append(ars)
    df = pd.read_parquet(CENSUS, columns=[c2r[c] for c in dict.fromkeys(want) if c in c2r])
    df.columns = [clean_col_name(c) for c in df.columns]
    df = df.rename(columns={"GITTER_ID_100m": "ZENSUS100m"})
    return df[df[ars].astype(str).str[:5] == KREIS].reset_index(drop=True)


def load_footprints(cells):
    g = gpd.read_parquet(ALKIS); g["activity"] = g["activity"].astype(str)
    g = g[g["activity"].isin(["residential", "unknown"]) & (g["area_m2"].astype(float) >= 40)].copy()
    cen = g.geometry.centroid.to_crs("EPSG:3035")
    g["_cell"] = [building_cell_id(north_m=p.y, east_m=p.x) for p in cen]
    g = g[g["_cell"].isin(set(cells["ZENSUS100m"]))].reset_index(drop=True)
    b = gpd.GeoDataFrame({"building_id": np.arange(len(g)), "area_m2": g["area_m2"].astype(float).values,
                          "OI": g["OI"].values, "_cell": g["_cell"].values},
                         geometry=g.geometry.values, crs="EPSG:25832")
    return join_lod2_heights(b, pd.read_parquet(HEIGHTS))


def main():
    cells = load_census_kreis()
    sig = cbs.cell_signals(cells).set_index("ZENSUS100m")
    geb_tot = {cid: sum(max(0.0, float(sig.loc[cid][f"geb_{c}"]))
                        for c in THREE if f"geb_{c}" in sig.loc[cid].index) for cid in sig.index}
    nosig = {cid for cid, t in geb_tot.items() if t <= 0}

    b = load_footprints(cells)
    print(f"footprints {len(b)}  height-cov {b['height_m'].notna().mean()*100:.1f}%  "
          f"median height {b['height_m'].median():.1f}m")

    # --- Q1: volume-vs-area typing differences ---
    n_cells = n_buildings = n_type_diff = mfh_vol_not_area = 0
    for cid, grp in b.groupby("_cell", sort=False):
        n_cells += 1; n_buildings += len(grp)
        geb = {c: float(sig.loc[str(cid)][f"geb_{c}"]) if str(cid) in sig.index
               and f"geb_{c}" in sig.loc[str(cid)].index else 0.0 for c in THREE}
        tv = BT.assign_building_types(grp[["building_id", "area_m2", "height_m"]].copy(), geb, np.random.RandomState(SEED))
        ta = BT.assign_building_types(grp[["building_id", "area_m2"]].copy(), geb, np.random.RandomState(SEED))
        diff = (tv["btype"].to_numpy() != ta["btype"].to_numpy())
        n_type_diff += int(diff.sum())
        mfh_vol_not_area += int(((tv["btype"].to_numpy() == "mfh") & (ta["btype"].to_numpy() != "mfh")).sum())
    print("\n=== Q1: volume vs area typing ===")
    print(f"cells {n_cells}  footprints {n_buildings}")
    print(f"no-census-signal cells (all->EFH, height ignored): {len(nosig)} ({len(nosig)/len(sig)*100:.1f}%)")
    print(f"footprints whose TYPE differs volume-vs-area: {n_type_diff} ({n_type_diff/n_buildings*100:.2f}%)")
    print(f"footprints MFH-under-volume but NOT-under-area: {mfh_vol_not_area} ({mfh_vol_not_area/n_buildings*100:.2f}%)")

    # --- Q2: no-signal cells — HH type mix + footprint heights ---
    pop = pd.read_pickle(POP)
    hh = pop.drop_duplicates("household_id")[["household_id", "ZENSUS100m", "building_type_3class"]].copy()
    hh["in_nosig"] = hh["ZENSUS100m"].astype(str).isin(nosig)
    print("\n=== Q2: no-signal cells ===")
    print(f"HH total {len(hh)}  in no-signal cells {hh['in_nosig'].sum()} ({hh['in_nosig'].mean()*100:.1f}%)")
    print("HH building_type_3class mix in NO-SIGNAL cells:")
    print(hh[hh["in_nosig"]]["building_type_3class"].value_counts(dropna=False).to_string())

    bn = b[b["_cell"].astype(str).isin(nosig)]
    print(f"\nfootprints in no-signal cells: {len(bn)}  height-cov {bn['height_m'].notna().mean()*100:.1f}%")
    print(f"  height p50={bn['height_m'].median():.1f}  p75={bn['height_m'].quantile(.75):.1f}  "
          f"p90={bn['height_m'].quantile(.90):.1f}  max={bn['height_m'].max():.1f}")
    tall = (bn["height_m"] >= 9).sum()
    print(f"  footprints >=9m (>=3 floors, MFH-like): {tall} ({tall/len(bn)*100:.1f}%) "
          f"in {bn[bn['height_m']>=9]['_cell'].nunique()} of {bn['_cell'].nunique()} no-signal cells")


if __name__ == "__main__":
    main()
