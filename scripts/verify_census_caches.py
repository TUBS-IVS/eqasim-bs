"""Read cached BS census stage outputs and print diagnostics.

Run with:
    python scripts/verify_census_caches.py
"""
from __future__ import annotations

import glob
import os
import pickle
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "eqasim-data" / "cache_bs"
ZGB = {"03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"}


def _latest(prefix: str) -> str | None:
    hits = glob.glob(str(CACHE / f"{prefix}__*.p"))
    if not hits:
        # unhashed caches (some BS modules)
        direct = CACHE / f"{prefix}.p"
        if direct.exists():
            return str(direct)
        return None
    return max(hits, key=os.path.getmtime)


def _load(prefix: str):
    p = _latest(prefix)
    if p is None:
        print(f"[MISS]  {prefix}")
        return None
    with open(p, "rb") as fh:
        obj = pickle.load(fh)
    print(f"[OK]    {prefix}  <- {os.path.basename(p)}")
    return obj


def check_population():
    df = _load("braunschweig.data.census.population")
    if df is None:
        return
    assert {"commune_id", "sex", "age_class", "weight"} <= set(df.columns)
    kreise = set(df["commune_id"].astype(str).str[:5].unique())
    missing = ZGB - kreise
    extra = kreise - ZGB
    print(f"        rows={len(df):,} communes={df['commune_id'].nunique()} "
          f"kreise={len(kreise)} missing_ZGB={missing} extra={extra}")
    print(f"        total={df['weight'].sum():,.0f}")
    dup = df.duplicated(["commune_id", "sex", "age_class"]).sum()
    print(f"        duplicates={dup}")
    per_kreis = df.assign(k=df["commune_id"].astype(str).str[:5]).groupby("k")["weight"].sum()
    print(f"        per-Kreis totals:\n{per_kreis.to_string()}")


def check_employment():
    df = _load("braunschweig.data.census.employment")
    if df is None:
        return
    assert {"departement_id", "age_class", "sex", "weight"} <= set(df.columns)
    kreise = set(df["departement_id"].astype(str).unique())
    print(f"        rows={len(df):,} kreise={kreise} total={df['weight'].sum():,}")
    dup = df.duplicated(["departement_id", "age_class", "sex"]).sum()
    print(f"        duplicates={dup}  missing_ZGB={ZGB - kreise}  extra={kreise - ZGB}")


def check_employees():
    df = _load("braunschweig.data.census.employees")
    if df is None:
        return
    assert {"commune_id", "weight"} <= set(df.columns)
    kreise = set(df["commune_id"].astype(str).str[:5].unique())
    print(f"        communes={df['commune_id'].nunique()} kreise={len(kreise)}")
    print(f"        total SvB AO={df['weight'].sum():,.0f}")
    dup = df["commune_id"].duplicated().sum()
    print(f"        duplicate commune_ids={dup}  missing_ZGB={ZGB - kreise}  extra={kreise - ZGB}")


def check_licenses():
    obj = _load("braunschweig.data.census.licenses")
    if obj is None:
        return
    df_country, df_land, df_kreis = obj
    print(f"        country rows={len(df_country)} (age x sex), "
          f"total_rel={df_country['relative_weight'].sum():.3f}")
    print(f"        land rows={len(df_land)} "
          f"total_rel={df_land['relative_weight'].sum():.3f}")
    print(f"        kreis rows={len(df_kreis)} "
          f"total={df_kreis['weight'].sum():,.0f}")
    kreise = set(df_kreis["departement_id"].astype(str).unique())
    print(f"        missing_ZGB={ZGB - kreise}  extra={kreise - ZGB}")


def check_pendler():
    df = _load("braunschweig.data.census.pendler")
    if df is None:
        return
    assert {"orig_ars", "dest_ars", "flow"} <= set(df.columns)
    dup = df.duplicated(["orig_ars", "dest_ars"]).sum()
    internal = df[df["orig_ars"].isin(ZGB) & df["dest_ars"].isin(ZGB)]["flow"].sum()
    out_flow = df[df["orig_ars"].isin(ZGB) & ~df["dest_ars"].isin(ZGB)]["flow"].sum()
    in_flow = df[~df["orig_ars"].isin(ZGB) & df["dest_ars"].isin(ZGB)]["flow"].sum()
    print(f"        rows={len(df):,} unique_pairs={len(df) - dup} duplicates={dup}")
    print(f"        inbound={in_flow:,}  outbound={out_flow:,}  intra-ZGB={internal:,}")
    # Self-loops must be gone
    sl = (df["orig_ars"] == df["dest_ars"]).sum()
    print(f"        self_loops={sl}")


def check_household_size():
    df = _load("braunschweig.data.census.household_size")
    if df is None:
        return
    print(f"        columns={list(df.columns)} rows={len(df)}")
    print(df.head().to_string())


def check_household_income():
    df = _load("braunschweig.data.census.household_income")
    if df is None:
        return
    print(f"        columns={list(df.columns)} rows={len(df)}")
    print(df.head().to_string())


if __name__ == "__main__":
    print("=== population ==="); check_population()
    print("=== employment ==="); check_employment()
    print("=== employees ===");  check_employees()
    print("=== licenses ===");   check_licenses()
    print("=== pendler ===");    check_pendler()
    print("=== household_size ==="); check_household_size()
    print("=== household_income ==="); check_household_income()
