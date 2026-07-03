# Data preparation for fig_employment.png
# Computes per-Kreis synthetic employment rates (all-ages, 14+, and per decadal
# age band) from the 100% popsim run micro data via point-in-polygon household
# -> Kreis assignment (EPSG:25832). Caches the aggregates as CSVs so the figure
# script can iterate quickly.
import os
import pandas as pd
import geopandas as gpd

RUN = "C:/Users/bienzeisler/Downloads/popsim_100pct_results/output_bs_100pct_allfeat_popsim"
OUT = os.path.dirname(os.path.abspath(__file__))

persons = pd.read_csv(
    RUN + "/braunschweig_100pct_allfeat_popsim_persons.csv",
    sep=";", usecols=["person_id", "household_id", "age", "employed"],
)
print("persons:", len(persons), "employed dtype:", persons["employed"].dtype)
# employed is written as True/False strings or bools; normalise to bool
if persons["employed"].dtype != bool:
    persons["employed"] = (
        persons["employed"].astype(str).str.strip().str.lower().isin(["true", "1", "yes"])
    )

homes = gpd.read_file(RUN + "/braunschweig_100pct_allfeat_popsim_homes.gpkg")
print("homes:", len(homes), homes.crs)
kreise = gpd.read_file(RUN + "/simwrapper/kreis_socio.geojson")[["ars5", "geometry"]]
kreise = kreise.to_crs("EPSG:25832")

joined = gpd.sjoin(homes, kreise, how="left", predicate="within")
n_missing = joined["ars5"].isna().sum()
print(f"households without Kreis: {n_missing} / {len(joined)}")
if n_missing > 0:
    # assign leftover boundary points to nearest Kreis polygon (log the count)
    missing = joined[joined["ars5"].isna()][["household_id", "geometry"]]
    fixed = gpd.sjoin_nearest(missing, kreise, how="left")
    joined.loc[joined["ars5"].isna(), "ars5"] = fixed["ars5"].to_numpy()
    print(f"nearest-assigned {len(missing)} boundary households")

hh_kreis = joined[["household_id", "ars5"]]
p = persons.merge(hh_kreis, on="household_id", how="left", validate="many_to_one")
assert p["ars5"].notna().all(), "persons without Kreis after join"

# per-Kreis rates on two explicit bases
def rate(df):
    return pd.Series({
        "n_persons": len(df),
        "n_employed": int(df["employed"].sum()),
        "rate": df["employed"].mean(),
    })

all_ages = p.groupby("ars5").apply(rate, include_groups=False).reset_index()
all_ages["base"] = "all_ages"
p14 = p[p["age"] >= 14]
r14 = p14.groupby("ars5").apply(rate, include_groups=False).reset_index()
r14["base"] = "age14plus"
pd.concat([all_ages, r14]).to_csv(OUT + "/synthetic_kreis_rates.csv", index=False)
print(pd.concat([all_ages, r14]).to_string())

# per decadal age band (matches the zensus2022_employment_by_age_ref.csv bands)
bands = list(range(0, 90, 10))
labels = [f"{b}-{b+9}" for b in bands[:-1]] + ["80+"]
p2 = p.copy()
p2["age_band"] = pd.cut(p2["age"], bins=bands + [200], right=False, labels=labels)
by_band = (
    p2.groupby(["ars5", "age_band"], observed=True)
      .agg(n_persons=("employed", "size"), n_employed=("employed", "sum"))
      .reset_index()
)
by_band["rate"] = by_band["n_employed"] / by_band["n_persons"]
by_band.to_csv(OUT + "/synthetic_kreis_ageband_rates.csv", index=False)
print(by_band[by_band["ars5"].isin(["03101", "03102", "03103"])].to_string())
