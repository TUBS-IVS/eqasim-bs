# Per-age-band synthetic employment rates for the 3 kreisfreie Staedte,
# compared with the committed Zensus reference bands (same decadal bands).
import os

import geopandas as gpd
import pandas as pd

BASE = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim"
REPO = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

persons = pd.read_csv(
    f"{BASE}/braunschweig_100pct_allfeat_popsim_persons.csv",
    sep=";", usecols=["person_id", "household_id", "age", "employed"],
)
homes = gpd.read_file(f"{BASE}/braunschweig_100pct_allfeat_popsim_homes.gpkg")
kreis = gpd.read_file(f"{BASE}/simwrapper/kreis_socio.geojson")[["ars5", "geometry"]]
kreis = kreis.to_crs("EPSG:25832")
joined = gpd.sjoin(homes, kreis, how="left", predicate="within")
if joined["ars5"].isna().any():
    unmatched = joined[joined["ars5"].isna()][["household_id", "geometry"]].copy()
    filled = gpd.sjoin_nearest(unmatched, kreis, how="left")
    joined.loc[joined["ars5"].isna(), "ars5"] = filled["ars5"].values
persons = persons.merge(joined[["household_id", "ars5"]], on="household_id", how="left")

bins = [0, 10, 20, 30, 40, 50, 60, 70, 80, 200]
labels = ["0-9", "10-19", "20-29", "30-39", "40-49", "50-59", "60-69", "70-79", "80+"]
persons["age_band"] = pd.cut(persons["age"], bins=bins, labels=labels, right=False)

syn_band = (persons.groupby(["ars5", "age_band"], observed=True)
            .agg(n=("person_id", "size"), emp=("employed", "sum")).reset_index())
syn_band["syn_rate"] = syn_band["emp"] / syn_band["n"]

ref = pd.read_csv(f"{REPO}/eqasim-data/data/braunschweig/popsim/zensus2022_employment_by_age_ref.csv",
                  dtype={"region": str})
m = syn_band.merge(ref, left_on=["ars5", "age_band"], right_on=["region", "age_band"], how="left")
m = m[m["ars5"].isin(["03101", "03102", "03103"])]
m["delta_pp"] = (m["syn_rate"] - m["rate"]) * 100
print(m[["ars5", "age_band", "n", "emp", "syn_rate", "rate", "delta_pp"]].to_string())

print("\nEmployed under 16:", int(persons[(persons['age'] < 16)]['employed'].sum()))
print("Employed under 18:", int(persons[(persons['age'] < 18)]['employed'].sum()))

m.to_csv(f"{OUT_DIR}/employment_ageband_data.csv", index=False)
