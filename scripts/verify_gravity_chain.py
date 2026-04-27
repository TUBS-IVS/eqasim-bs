"""Verify the gravity/external/pendler chain end-to-end from cached outputs."""
from __future__ import annotations

import glob
import os
import pickle
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "eqasim-data" / "cache_bs"
ZGB = {"03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"}


def _latest(prefix: str):
    hits = glob.glob(str(CACHE / f"{prefix}__*.p"))
    return max(hits, key=os.path.getmtime) if hits else None


def _load(prefix: str):
    p = _latest(prefix)
    if p is None:
        raise SystemExit(f"missing cache: {prefix}")
    with open(p, "rb") as fh:
        return pickle.load(fh)


# 1) Pendler base totals
df_pendler = _load("braunschweig.data.census.pendler")
out_by_dest = (df_pendler[df_pendler["orig_ars"].isin(ZGB)
                          & ~df_pendler["dest_ars"].isin(ZGB)]
               .groupby("dest_ars")["flow"].sum().sort_values(ascending=False))
print("=== Top-10 outbound external destinations (BA Pendler) ===")
print(out_by_dest.head(10).to_string())
print(f"Total outbound={out_by_dest.sum():,}")

# 2) External workplaces
df_ext = _load("braunschweig.data.external_workplaces")
print("\n=== External workplaces ===")
print(f"N={len(df_ext)}  total employees={df_ext['employees'].sum():,}")
print(f"retained share of outbound = "
      f"{df_ext['employees'].sum() / out_by_dest.sum():.1%}")
missing = set(out_by_dest.index) - set(df_ext["ars5"])
below_50 = out_by_dest[out_by_dest < 50]
print(f"Kreise below threshold (dropped): {len(below_50)} destinations, "
      f"{below_50.sum():,} SvB")

# 3) Gravity output
df_work, df_edu = _load("braunschweig.gravity.model")
print("\n=== Gravity (work) ===")
print(f"rows={len(df_work):,}  "
      f"n_origins={df_work['origin_id'].nunique()}  "
      f"n_destinations={df_work['destination_id'].nunique()}")

row_sums = df_work.groupby("origin_id")["weight"].sum()
print(f"row-sum min={row_sums.min():.6f}  max={row_sums.max():.6f}  "
      f"mean={row_sums.mean():.6f}")

# External destination share
ext_mask = df_work["destination_id"].astype(str).str.startswith("EXT")
print(f"external-dest rows: {ext_mask.sum():,}  "
      f"origins covering ext: {df_work[ext_mask]['origin_id'].nunique()}")

# 4) Per-origin share going external vs internal (expected ~28% at Kreis level)
df_work["orig_kreis"] = df_work["origin_id"].astype(str).str[:5]
df_work["is_ext"] = ext_mask
by_kreis = df_work.groupby("orig_kreis").apply(
    lambda g: (g.loc[g["is_ext"], "weight"].sum() / g["weight"].sum())
)
print("\n=== Outbound-share (external) per origin Kreis ===")
print(by_kreis.sort_index().to_string())

# 5) Education
print(f"\n=== Gravity (education) ===")
print(f"rows={len(df_edu):,} row-sum range="
      f"[{df_edu.groupby('origin_id')['weight'].sum().min():.6f}, "
      f"{df_edu.groupby('origin_id')['weight'].sum().max():.6f}]")

# 6) Work locations pool
df_locw = _load("braunschweig.locations.work")
print(f"\n=== Work locations pool ===")
print(f"rows={len(df_locw):,}  commune uniques={df_locw['commune_id'].nunique()}  "
      f"EXT rows={df_locw['commune_id'].astype(str).str.startswith('EXT').sum()}  "
      f"location_id unique={df_locw['location_id'].is_unique}")
