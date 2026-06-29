"""Realised activity distances from the actual assigned locations in a cache.

Joins the activity purpose table to the assigned-location geometries on
(person_id, activity_index) and computes the distance that matches each
reference's definition:
  - work / education: residence (the is_first home activity) -> activity, straight line
  - secondary (shop/leisure/other): leg from the immediately preceding activity

All distances are straight-line metres in the locations CRS (EPSG:25832),
converted to km and multiplied by ``detour_factor`` so they are comparable to
the routed MiD references. No re-simulation: this measures what the pipeline
actually assigned.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

SECONDARY_PURPOSES = ("shop", "leisure", "other")


def realised_distances(df_activities, df_locations, *, activity,
                       detour_factor=1.3, rs7_lookup=None):
    """Return realised distances for one activity. See module docstring for semantics."""
    if activity not in ("work", "education", "secondary"):
        raise ValueError(f"unknown activity '{activity}'")

    merged = df_activities.merge(
        df_locations[["person_id", "activity_index", "geometry", "commune_id"]],
        on=["person_id", "activity_index"], how="left",
    )
    n_missing = int(merged["geometry"].isna().sum())
    if n_missing:
        logger.warning(
            "[distance-fit] %d/%d activities lacked an assigned location geometry "
            "(dropped; CLAUDE.md no-silent-fallback).", n_missing, len(merged),
        )
        merged = merged[merged["geometry"].notna()].copy()

    merged = merged.sort_values(["person_id", "activity_index"])
    merged["x_m"] = merged["geometry"].apply(lambda g: g.x).values
    merged["y_m"] = merged["geometry"].apply(lambda g: g.y).values

    home = merged[(merged.purpose == "home") & (merged.is_first)]
    home = home.drop_duplicates("person_id").set_index("person_id")
    home_xy = home[["x_m", "y_m"]]
    home_commune = home["commune_id"]

    if activity in ("work", "education"):
        target = merged[merged.purpose == activity].drop_duplicates("person_id")
        target = target[target.person_id.isin(home_xy.index)].copy()
        hx = home_xy.loc[target.person_id, "x_m"].values
        hy = home_xy.loc[target.person_id, "y_m"].values
        d_m = np.hypot(target["x_m"].values - hx, target["y_m"].values - hy)
        out = pd.DataFrame({
            "person_id": target.person_id.values,
            "distance_km": d_m / 1000.0 * detour_factor,
            "home_commune_id": home_commune.loc[target.person_id].values,
            "purpose": activity,
        })
    else:  # secondary: leg from the preceding activity
        merged["prev_x"] = merged.groupby("person_id")["x_m"].shift(1)
        merged["prev_y"] = merged.groupby("person_id")["y_m"].shift(1)
        sec = merged[merged.purpose.isin(SECONDARY_PURPOSES) & merged.prev_x.notna()].copy()
        sec = sec[sec.person_id.isin(home_commune.index)]
        d_m = np.hypot(sec["x_m"].values - sec["prev_x"].values,
                       sec["y_m"].values - sec["prev_y"].values)
        out = pd.DataFrame({
            "person_id": sec.person_id.values,
            "distance_km": d_m / 1000.0 * detour_factor,
            "home_commune_id": home_commune.loc[sec.person_id].values,
            "purpose": sec.purpose.values,
        })

    lookup = rs7_lookup or {}
    out["home_rs7"] = [int(lookup.get(c, -1)) for c in out["home_commune_id"]]
    return out[["person_id", "distance_km", "home_commune_id", "home_rs7", "purpose"]]
