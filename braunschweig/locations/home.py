"""Density-weighted home-location candidates for Braunschweig.

This module is the merged successor of:
- ``bavaria/locations/home.py`` (Origin: eqasim-bavaria @ b20fbe6) - the base
  per-building point candidates (``weight = area``).
- ``braunschweig/locations/home.py`` (TASK-003) - the Zensus 2022 100 m
  density-weighting overlay.

Phase 2.7 of the eqasim-bs refactor merged both into a single module so the
BS pipeline no longer delegates through ``bavaria.locations.home``.

When ``braunschweig.home_density_weighting`` is true, the per-Gemeinde
within-Gemeinde redistribution multiplies each building's weight by the
spatially-joined Zensus 2022 100 m population value of the cell containing
the centroid; per-Gemeinde rescaling preserves the inter-Gemeinde totals
enforced by ``synthesis.population.spatial.home.zones`` upstream.

Output schema (drop-in replacement via the ``synthesis.locations.home.locations``
alias)::

    home_location_id, weight, commune_id, iris_id, geometry
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd

ZENSUS_CRS = "EPSG:3035"


# --- Inherited from eqasim-bavaria -----------------------------------------

def _execute_base(context):
    """Per-building home-location candidates with ``weight = area``."""
    df = context.stage("bavaria.data.buildings")
    df = df.rename(columns={"building_id": "home_location_id"})
    return df[[
        "home_location_id", "weight", "commune_id", "iris_id", "geometry",
    ]]


# --- Braunschweig-specific -------------------------------------------------

def configure(context):
    context.stage("bavaria.data.buildings")
    context.config("braunschweig.home_density_weighting", False)
    if context.config("braunschweig.home_density_weighting"):
        context.stage("braunschweig.data.zensus_grid.population")


def execute(context):
    df = _execute_base(context)

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

    # Spatial join: each building point -> at most one Zensus cell.
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

    # Density factor: >= 1 inside grid, 1 outside (centroid in unpopulated
    # cell -> keep area-only weight). This shifts mass towards dense areas
    # without zeroing rural buildings.
    density = np.where(einwohner > 0, einwohner, 1.0)
    new_weight = df["weight"].astype(float).values * density

    # Per-Gemeinde rescale: keep sum(weight) per commune_id constant so the
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
