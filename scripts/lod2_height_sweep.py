"""SWEEP: tune the no-census-signal MFH floor threshold (building_typing.MFH_MIN_FLOORS).

Loads the real Salzgitter popsim population + ALKIS + LoD2 heights ONCE, then for each
candidate threshold re-runs the typed matcher + derived-ground-truth and reports
type-fidelity and over-capacity. Pick the threshold that maximises fidelity / minimises
over-capacity. Exploration only (not a unit test).

Run (conda eqasim env, GDAL_DATA/PROJ_LIB set):  python scripts/lod2_height_sweep.py
"""
from __future__ import annotations
import sys
import numpy as np, pandas as pd, geopandas as gpd
REPO = r"C:/Users/bienzeisler/Documents/GitHub/eqasim-bs"
sys.path.insert(0, REPO)
from braunschweig.synthesis.locations import cell_building_signals as cbs
from braunschweig.synthesis.locations import home_cell
from braunschweig.synthesis.locations.home_cell import building_cell_id, assign_homes_typed
from braunschweig.synthesis.locations import building_typing as BT
from braunschweig.popsim.prepared_cells import clean_col_name
from braunschweig.analysis import home_match_validation as V
from braunschweig.data.buildings import join_lod2_heights
import pyarrow.parquet as pq

KREIS = "03102"; SEED = 20260617
POP = r"C:/Users/bienzeisler/AppData/Local/Temp/salzgitter_popsim_population.p"
CENSUS = REPO + "/eqasim-data/data/braunschweig/popsim/cells/zensus2022_grid_100m_de_prepared.parquet"
ALKIS = REPO + "/eqasim-data/data/braunschweig/preprocessed/alkis_buildings.parquet"
HEIGHTS = REPO + "/eqasim-data/data/braunschweig/preprocessed/lod2_heights.parquet"
COMMUNE = "03102000"
THRESHOLDS = [2, 3, 4, 5, 6, 7, 8, 9, 10]


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


def main():
    pop = pd.read_pickle(POP)
    hh = (pop.drop_duplicates("household_id")[["household_id", "ZENSUS100m",
                                               "building_type_3class", "household_size"]].reset_index(drop=True))
    hh["commune_id"] = COMMUNE
    cells = load_census_kreis()
    g = gpd.read_parquet(ALKIS); g["activity"] = g["activity"].astype(str)
    g = g[g["activity"].isin(["residential", "unknown"]) & (g["area_m2"].astype(float) >= 40)].copy()
    cen = g.geometry.centroid.to_crs("EPSG:3035")
    g["_cell"] = [building_cell_id(north_m=p.y, east_m=p.x) for p in cen]
    g = g[g["_cell"].isin(set(cells["ZENSUS100m"]))].reset_index(drop=True)
    buildings = gpd.GeoDataFrame({
        "building_id": np.arange(len(g)), "area_m2": g["area_m2"].astype(float).values,
        "weight": g["area_m2"].astype(float).values, "OI": g["OI"].values,
        "commune_id": COMMUNE, "footprint": g.geometry.values,
    }, geometry=g.geometry.centroid.values, crs="EPSG:25832")
    buildings = join_lod2_heights(buildings, pd.read_parquet(HEIGHTS))
    print(f"{len(hh):,} HH, {len(buildings):,} footprints, height-cov {buildings['height_m'].notna().mean()*100:.1f}%\n")

    print(f"{'min_floors':>10} {'type_match':>11} {'over_cap_%':>11} {'over_cap_HH':>12} {'size_assort':>12}")
    orig = BT.MFH_MIN_FLOORS
    for thr in THRESHOLDS:
        BT.MFH_MIN_FLOORS = thr
        typed_pts, rep = assign_homes_typed(hh, buildings, cells, random_seed=SEED)
        typed = typed_pts.merge(hh[["household_id", "building_type_3class", "household_size"]],
                                on="household_id", how="left")
        bbt = V.derive_buildings_btype(buildings, cells, SEED)
        rpt = V.home_match_report(typed, bbt, cells, n_overcapacity=rep.n_overcapacity,
                                  n_zero_building_cells=rep.n_zero_building_cells)
        print(f"{thr:>10} {rpt['type_match_share']*100:>10.2f}% {rpt['overflow_rate']*100:>10.2f}% "
              f"{rep.n_overcapacity:>12} {rpt['size_assortativity']:>12.3f}")
    BT.MFH_MIN_FLOORS = orig


if __name__ == "__main__":
    main()
