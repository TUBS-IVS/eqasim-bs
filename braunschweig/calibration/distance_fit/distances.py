"""Realised activity distances from the actual assigned locations in a cache.

Joins the activity purpose table to the assigned-location geometries on
(person_id, activity_index) and computes the distance that matches each
reference's definition:
  - work / education: residence (the is_first home activity) -> activity, straight line
  - secondary (shop/leisure/other): leg from the immediately preceding activity

All distances are straight-line metres in the locations CRS (EPSG:25832),
converted to km and multiplied by ``detour_factor`` so they are comparable to
the MiD distance references. No re-simulation: this measures what the pipeline
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

    # CRS soft guard: warn if locations are not in the expected metric CRS.
    crs = getattr(df_locations, "crs", None)
    if crs is not None and crs.to_epsg() != 25832:
        logger.warning(
            "[distance-fit] locations CRS is %s, expected EPSG:25832; distances assume metres.",
            crs,
        )

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

    # Build dict maps to avoid .loc positional reliance.
    commune_map = home_commune.to_dict()

    if activity in ("work", "education"):
        target_all = merged[merged.purpose == activity]
        n_with_dups = int((target_all.groupby("person_id").size() > 1).sum())
        if n_with_dups:
            logger.warning(
                "[distance-fit] %d persons have >1 %s activity; keeping first.",
                n_with_dups, activity,
            )
        target = target_all.drop_duplicates("person_id")

        n_before_home_filter = len(target)
        target = target[target.person_id.isin(home_xy.index)].copy()
        n_no_home = n_before_home_filter - len(target)
        if n_no_home > 0:
            logger.warning(
                "[distance-fit] %d %s rows dropped: person has no is_first home.",
                n_no_home, activity,
            )

        hx = target["person_id"].map(home_xy["x_m"].to_dict()).values
        hy = target["person_id"].map(home_xy["y_m"].to_dict()).values
        d_m = np.hypot(target["x_m"].values - hx, target["y_m"].values - hy)
        out = pd.DataFrame({
            "person_id": target.person_id.values,
            "distance_km": d_m / 1000.0 * detour_factor,
            "home_commune_id": target["person_id"].map(commune_map).values,
            "purpose": activity,
        })
    else:  # secondary: leg from the preceding activity
        merged["prev_x"] = merged.groupby("person_id")["x_m"].shift(1)
        merged["prev_y"] = merged.groupby("person_id")["y_m"].shift(1)
        sec = merged[merged.purpose.isin(SECONDARY_PURPOSES) & merged.prev_x.notna()].copy()
        n_before_commune_filter = len(sec)
        sec = sec[sec.person_id.isin(home_commune.index)]
        n_no_commune = n_before_commune_filter - len(sec)
        if n_no_commune > 0:
            logger.warning(
                "[distance-fit] %d secondary rows dropped: person has no is_first home.",
                n_no_commune,
            )
        d_m = np.hypot(sec["x_m"].values - sec["prev_x"].values,
                       sec["y_m"].values - sec["prev_y"].values)
        out = pd.DataFrame({
            "person_id": sec.person_id.values,
            "distance_km": d_m / 1000.0 * detour_factor,
            "home_commune_id": sec["person_id"].map(commune_map).values,
            "purpose": sec.purpose.values,
        })

    lookup = rs7_lookup or {}
    out["home_rs7"] = out["home_commune_id"].map(
        lambda c: int(lookup[c]) if c in lookup and not pd.isna(lookup[c]) else -1
    )
    return out[["person_id", "distance_km", "home_commune_id", "home_rs7", "purpose"]]
