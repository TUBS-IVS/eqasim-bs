"""End-to-end calibration analysis for the Braunschweig synthetic population.

Compares synthesis output against:
  * Zensus 2022 per-Kreis population totals
  * BA Pendleratlas 2025 Kreis-pair SvB commute flows
  * MiD 2023 P13 Gesamt mean commute distance (20.7 km)

Run after `synthesis.output` has completed.
"""
from __future__ import annotations

import pickle
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

CACHE = Path("eqasim-data/cache_bs")
OUTPUT = Path("eqasim-data/output_bs")
SCOPE = ["03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"]


def _latest(pat: str) -> Path:
    hits = sorted(CACHE.glob(pat), key=lambda p: p.stat().st_mtime, reverse=True)
    if not hits:
        raise FileNotFoundError(pat)
    return hits[0]


def _load(pat: str):
    with open(_latest(pat), "rb") as fh:
        obj = pickle.load(fh)
    if isinstance(obj, tuple):
        obj = obj[0]
    return obj


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def analyse_population() -> None:
    section("Per-Kreis population (synthetic vs Zensus 2022)")
    homes = _load("synthesis.population.spatial.home.locations__*.p")
    homes["kreis"] = homes["commune_id"].astype(str).str[:5]
    synth = homes.groupby("kreis").size() * 100

    pop = _load("braunschweig.data.census.population__*.p")
    pop["kreis"] = pop["commune_id"].astype(str).str[:5]
    cens = pop.groupby("kreis")["weight"].sum()

    df = pd.DataFrame({"synth": synth, "zensus": cens})
    df["ratio"] = (df["synth"] / df["zensus"]).round(3)
    print(df)
    tot_r = synth.sum() / cens.sum()
    print(f"TOTAL synth={synth.sum():,} zensus={cens.sum():,.0f} ratio={tot_r:.3f}")


def analyse_commute_flows() -> None:
    section("Pendler flows: synthetic vs BA Pendleratlas 2025")
    # Read pipeline output directly — persons with work activity + locations.
    act = pd.read_csv(OUTPUT / "braunschweig_1pct_activities.csv", sep=";")
    # home Kreis via households CSV → commune via home locations
    homes_geo = gpd.read_file(OUTPUT / "braunschweig_1pct_homes.gpkg")
    # Need home commune → use cache which still has it
    home_cache = _load("synthesis.population.spatial.home.locations__*.p")
    home_cache = home_cache[["household_id", "commune_id"]].rename(
        columns={"commune_id": "home_commune"})
    persons = pd.read_csv(OUTPUT / "braunschweig_1pct_persons.csv", sep=";")
    persons = persons.merge(home_cache, on="household_id", how="left")
    # Work commune from activities+locations cache (join via activity_index)
    act_cache = _load("synthesis.population.activities__*.p")
    loc_cache = _load("synthesis.population.spatial.locations__*.p")
    loc = pd.DataFrame(loc_cache)[["person_id", "activity_index", "commune_id"]]
    act_df = pd.DataFrame(act_cache)[["person_id", "activity_index", "purpose"]]
    merged = act_df.merge(loc, on=["person_id", "activity_index"], how="inner")
    work_loc = (merged[merged["purpose"] == "work"]
                [["person_id", "commune_id"]]
                .rename(columns={"commune_id": "work_commune"})
                .drop_duplicates("person_id"))
    work_loc["work_commune"] = work_loc["work_commune"].astype(str)

    df = persons[["person_id", "home_commune"]].merge(
        work_loc, on="person_id", how="inner")
    df["orig_k"] = df["home_commune"].astype(str).str[:5]
    df["dest_k"] = df["work_commune"].astype(str).str[:5]
    df.loc[df["work_commune"].str.startswith("EXT"), "dest_k"] = df["work_commune"]
    synth_flow = df.groupby(["orig_k", "dest_k"]).size().rename("synth").reset_index()
    synth_flow["synth"] *= 100  # 1% sample

    # BA observed flows
    pend = _load("braunschweig.data.census.pendler__*.p")
    obs = pend.rename(columns={"orig_ars": "orig_k", "dest_ars": "dest_k",
                               "flow": "obs"})

    # Compare per origin Kreis: total SvB, external share
    def per_origin(frame, flow_col):
        out = frame[frame["orig_k"].isin(SCOPE)].copy()
        out["is_ext"] = ~out["dest_k"].isin(SCOPE + [""])
        tot = out.groupby("orig_k")[flow_col].sum()
        ext = out[out["is_ext"]].groupby("orig_k")[flow_col].sum()
        return pd.DataFrame({"total": tot, "ext": ext.reindex(tot.index).fillna(0)})

    syn_k = per_origin(synth_flow, "synth")
    obs_k = per_origin(obs, "obs")
    cmp = syn_k.join(obs_k, lsuffix="_synth", rsuffix="_obs")
    cmp["share_synth"] = (cmp["ext_synth"] / cmp["total_synth"]).round(3)
    cmp["share_obs"] = (cmp["ext_obs"] / cmp["total_obs"]).round(3)
    cmp["tot_ratio"] = (cmp["total_synth"] / cmp["total_obs"]).round(3)
    print(cmp)


def analyse_commute_distance() -> None:
    section("Mean commute distance: synthetic vs MiD P13 ZGB Gesamt")
    g = gpd.read_file(OUTPUT / "braunschweig_1pct_commutes.gpkg")
    d_km = g.geometry.length / 1000.0
    print(f"  n={len(d_km)}  mean={d_km.mean():.2f}  median={d_km.median():.2f}")
    print(f"  p25={d_km.quantile(0.25):.2f}  p75={d_km.quantile(0.75):.2f}")
    print(f"  MiD P13 target: 20.70 km")
    print(f"  gap: {(d_km.mean() - 20.7) / 20.7 * 100:+.1f}%")


if __name__ == "__main__":
    analyse_population()
    analyse_commute_flows()
    analyse_commute_distance()
