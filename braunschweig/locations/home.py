"""
Density-weighted home-location candidates for Braunschweig (TASK-003).

Wraps ``bavaria.locations.home`` (per-building point candidates with
``weight = area``) and, when ``braunschweig.home_density_weighting`` is
true, multiplies each building's weight by the spatially-joined Zensus
2022 100 m population value of the cell containing the centroid.

Per-Gemeinde normalisation ensures the within-Gemeinde redistribution
of households does not shift the inter-Gemeinde totals enforced by
``synthesis.population.spatial.home.zones`` upstream — the Zensus grid
only redistributes *within* a Gemeinde (e.g. Braunschweig downtown vs.
peripheral districts).

Output schema is identical to ``bavaria.locations.home``::

    home_location_id, weight, commune_id, iris_id, geometry

so it is a drop-in replacement via the synpp alias map (config key
``synthesis.locations.home.locations``).

Algorithm
---------
1. Load building points from ``bavaria.locations.home``.
2. If flag off -> return unchanged (delegates fully).
3. Re-project to EPSG:3035 (Zensus grid CRS).
4. ``sjoin`` (predicate=within) the building points against the Zensus
   100 m polygon grid; cells are unique → 1 row per building.
5. Replace the building weight with ``weight × max(einwohner, 1)``;
   buildings whose centroid falls outside any populated cell keep the
   pure area weight (einwohner=0 fallback).
6. Per-``commune_id`` rescale so ``Σ weight`` matches the original
   ``Σ weight``. This preserves Gemeinde-level home totals while
   shifting mass towards densely populated cells.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

import bavaria.locations.home as delegate

ZENSUS_CRS = "EPSG:3035"


def configure(context):
    delegate.configure(context)
    context.config("braunschweig.home_density_weighting", False)
    if context.config("braunschweig.home_density_weighting"):
        context.stage("braunschweig.data.zensus_grid.population")


def execute(context):
    df = delegate.execute(context)

    if not context.config("braunschweig.home_density_weighting"):
        return df

    df_grid = context.stage("braunschweig.data.zensus_grid.population")
    if df_grid is None or len(df_grid) == 0:
        print("[braunschweig.locations.home] empty Zensus grid; "
              "falling back to area-only weights.")
        return df

    src_crs = df.crs
    if src_crs is None:
        raise RuntimeError("[braunschweig.locations.home] building CRS is None")

    df_proj = df.to_crs(ZENSUS_CRS)
    df_grid = df_grid[["einwohner", "geometry"]].to_crs(ZENSUS_CRS)

    # Spatial join: each building point → at most one Zensus cell.
    joined = gpd.sjoin(
        df_proj[["geometry"]].assign(_idx=np.arange(len(df_proj))),
        df_grid,
        how="left",
        predicate="within",
    )
    # Duplicates can occur on cell boundaries; keep the first match.
    joined = joined.drop_duplicates(subset="_idx", keep="first")
    einwohner = (joined.set_index("_idx")["einwohner"]
                 .reindex(np.arange(len(df_proj)))
                 .fillna(0.0)
                 .astype(float).values)

    n_in_grid = int((einwohner > 0).sum())
    print(
        "[braunschweig.locations.home] "
        f"{n_in_grid}/{len(df)} buildings inside populated Zensus cells "
        f"({n_in_grid / len(df):.1%})"
    )

    # Density factor: ≥ 1 inside grid, 1 outside (centroid in unpopulated
    # cell → keep area-only weight). This shifts mass towards dense areas
    # without zeroing rural buildings.
    density = np.where(einwohner > 0, einwohner, 1.0)
    new_weight = df["weight"].astype(float).values * density

    # Per-Gemeinde rescale: keep Σ weight per commune_id constant so the
    # household-allocation step does not shift inter-Gemeinde shares.
    df_out = df.copy()
    df_out["weight"] = new_weight
    df_out["_orig_weight"] = df["weight"].astype(float).values

    sums = df_out.groupby("commune_id", observed=True).agg(
        new_sum=("weight", "sum"),
        old_sum=("_orig_weight", "sum"),
    )
    scale = (sums["old_sum"] / sums["new_sum"].replace(0.0, np.nan)).fillna(1.0)
    df_out["weight"] = df_out["weight"] * df_out["commune_id"].map(scale).fillna(1.0)
    df_out = df_out.drop(columns=["_orig_weight"])

    return df_out[[
        "home_location_id", "weight", "commune_id", "iris_id", "geometry",
    ]]
