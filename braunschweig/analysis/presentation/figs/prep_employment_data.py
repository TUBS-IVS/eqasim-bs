# Data preparation for fig_employment.png
# Computes per-Kreis synthetic employment rates (all ages, and 14+ sanity check)
# from the 100% popsim output via point-in-polygon Kreis join (EPSG:25832),
# and collects the committed Zensus anchor rates + MiD P9 cross-check values.
# Output: employment_panel_data.csv (one row per Kreis) written next to this script.
import json
import os

import geopandas as gpd
import pandas as pd

BASE = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim"
REPO = "c:/Users/bienzeisler/Documents/GitHub/eqasim-bs"
OUT_DIR = os.path.dirname(os.path.abspath(__file__))

# --- 1. Synthetic micro data -> per-Kreis employment rates ------------------
persons = pd.read_csv(
    f"{BASE}/braunschweig_100pct_allfeat_popsim_persons.csv",
    sep=";", usecols=["person_id", "household_id", "age", "employed"],
)
print("persons:", len(persons), "employed dtype:", persons["employed"].dtype)
if persons["employed"].dtype == object:
    persons["employed"] = persons["employed"].astype(str).str.lower().eq("true")

homes = gpd.read_file(f"{BASE}/braunschweig_100pct_allfeat_popsim_homes.gpkg")
print("homes:", len(homes), homes.crs)

kreis = gpd.read_file(f"{BASE}/simwrapper/kreis_socio.geojson")[["ars5", "geometry"]]
kreis = kreis.to_crs("EPSG:25832")

joined = gpd.sjoin(homes, kreis, how="left", predicate="within")
n_unmatched = joined["ars5"].isna().sum()
print(f"PIP join: {len(joined) - n_unmatched}/{len(joined)} homes matched, {n_unmatched} unmatched")
if n_unmatched > 0:
    # nearest-fill for boundary artefacts; report the rate (no silent fallback)
    unmatched = joined[joined["ars5"].isna()][["household_id", "geometry"]].copy()
    filled = gpd.sjoin_nearest(unmatched, kreis, how="left")
    joined.loc[joined["ars5"].isna(), "ars5"] = filled["ars5"].values
    print(f"  -> nearest-fill applied to {len(unmatched)} homes "
          f"({100.0 * len(unmatched) / len(joined):.3f}% fallback rate)")

hh_kreis = joined[["household_id", "ars5"]]
persons = persons.merge(hh_kreis, on="household_id", how="left", validate="many_to_one")
assert persons["ars5"].notna().all(), "persons without Kreis after join"

grp_all = persons.groupby("ars5").agg(
    n_persons=("person_id", "size"), n_employed=("employed", "sum"))
grp_all["syn_rate_all_ages"] = grp_all["n_employed"] / grp_all["n_persons"]

p14 = persons[persons["age"] >= 14]
grp_14 = p14.groupby("ars5").agg(
    n_persons_14=("person_id", "size"), n_employed_14=("employed", "sum"))
grp_14["syn_rate_14plus"] = grp_14["n_employed_14"] / grp_14["n_persons_14"]

# Base "20+": THE single synthetic employment rate shown in the figure (both panels).
# Base 20+ is used because the output flag "employed" for under-20s (Schueler/Ausbildung)
# deviates from the Zensus employment definition; the 14+ base is contaminated by that
# minor-employment-flag artefact. Computed for ALL 8 Kreise via the same PIP Kreis join.
p20 = persons[persons["age"] >= 20]
grp_20 = p20.groupby("ars5").agg(
    n_persons_20=("person_id", "size"), n_employed_20=("employed", "sum"))
grp_20["syn_rate_20plus"] = grp_20["n_employed_20"] / grp_20["n_persons_20"]

syn = grp_all.join(grp_14).join(grp_20)

# --- 2. Committed Zensus anchor (all-ages rate per region) ------------------
ref = pd.read_csv(f"{REPO}/eqasim-data/data/braunschweig/popsim/zensus2022_employment_by_age_ref.csv",
                  dtype={"region": str})
anchor = ref.groupby("region").agg(total=("total", "sum"), emp=("erwerbstaetige", "sum"))
anchor["ref_rate_all_ages"] = anchor["emp"] / anchor["total"]
print("\nAnchor all-ages rates:\n", anchor["ref_rate_all_ages"])

# --- 3. MiD P9 cross-check (base: persons 14+) -------------------------------
p9 = pd.read_csv(f"{REPO}/eqasim-data/data/braunschweig/mid/mid2023_P9.csv",
                 dtype={"ars5": str})
employ_cols = ["vollzeit", "teilzeit", "geringfuegig", "sonstiges", "erwerbstaetig_unspec"]
p9["p9_employed_pct"] = p9[employ_cols].sum(axis=1)
p9 = p9[p9["ars5"] != "03ZGB"]  # drop the Gesamt row

# --- 4. controls_long validation values (employment, kreis) -----------------
cl = pd.read_csv(f"{BASE}/analysis/population_validation/controls_long.csv",
                 dtype={"geo_id": str})
emp = cl[(cl["control"] == "employment") & (cl["geography"] == "kreis")
         & (cl["category"] == "employed")][["geo_id", "synthetic_pct", "target_pct"]]
emp = emp.rename(columns={"geo_id": "ars5"})

# --- 5. Assemble one row per Kreis -------------------------------------------
out = p9[["kreis", "ars5", "n_unweighted", "p9_employed_pct"]].merge(
    emp, on="ars5", how="left").merge(
    syn, left_on="ars5", right_index=True, how="left").merge(
    anchor[["ref_rate_all_ages"]], left_on="ars5", right_index=True, how="left")

de_fallback = anchor.loc["DE_large_gemeinden", "ref_rate_all_ages"]
out["de_fallback_rate"] = de_fallback

out.to_csv(f"{OUT_DIR}/employment_panel_data.csv", index=False)
print("\n", out.to_string())

# Sanity: controls_long synthetic_pct should match our own 14+ rate
out["check_14plus_pp"] = (out["syn_rate_14plus"] * 100 - out["synthetic_pct"]).abs()
print("\nmax |own 14+ rate - controls_long synthetic_pct| (pp):",
      out["check_14plus_pp"].max())
print(json.dumps({"de_fallback_all_ages_rate": float(de_fallback)}, indent=2))
