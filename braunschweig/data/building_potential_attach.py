"""Attach a per-building potential onto candidate locations by spatial join.

Primary path: a candidate whose representative point falls inside a building
footprint takes that building's potential. Fallback path: candidates with no
containing building keep the supplied ``fallback`` value (e.g. the legacy
``area*floors`` weight, or 0.0 for the chainsolvers default). The primary vs
fallback split is logged as an explicit rate (CLAUDE.md fallback transparency).
"""
from __future__ import annotations

import numpy as np
import geopandas as gpd


def log_attach_rate(label: str, primary: int, fallback: int) -> None:
    total = primary + fallback
    share = (fallback / total) if total else 0.0
    msg = ("[building-potential-attach:%s] primary %d/%d (%.1f%%), "
           "fallback %d (%.1f%%)"
           % (label, primary, total, 100.0 * (primary / total if total else 0.0),
              fallback, 100.0 * share))
    # A high fallback share means the candidate set and the building footprints
    # do not overlap (CRS, coverage, or geometry-type mismatch), not just a few
    # edge cases -- surface it loudly.
    print(("WARNING: " + msg) if share > 0.20 else msg)


# Share of facility units that fall back to the area x floors weights above which
# the unit-basis log line is a WARNING. Module constant (this module reads no
# config); same 20 % level as log_attach_rate above.
MAX_AREA_WEIGHT_UNIT_SHARE = 0.20


def pick_unit_weights(sub, potential_column: str, area_weight_column: str):
    """Weights for ONE facility unit with a single, consistent unit of measure.

    The capacity of a facility unit is split across its POIs proportionally to
    the returned weights. ``potential_column`` (places, from a matched
    building) and ``area_weight_column`` (m2 x floors) are different units, so
    they must never be mixed inside a unit: if EVERY POI of the unit carries a
    potential > 0 the potentials are used, otherwise the area weights of ALL
    POIs are used. Unmatched POIs hold NaN in ``potential_column``.

    Returns ``(weights, used_potential)``.
    """
    pot = sub[potential_column].to_numpy(float)
    if len(pot) and bool((pot > 0).all()):
        return pot, True
    return sub[area_weight_column].to_numpy(float), False


def log_unit_basis(label: str, n_potential_units: int, n_area_units: int) -> None:
    """Log the share of facility units on potentials vs on area weights."""
    total = n_potential_units + n_area_units
    share = (n_area_units / total) if total else 0.0
    msg = ("[building-potential-units:%s] units on potentials %d/%d (%.1f%%), "
           "units on area x floors weights %d (%.1f%%)"
           % (label, n_potential_units, total,
              100.0 * (n_potential_units / total if total else 0.0),
              n_area_units, 100.0 * share))
    print(("WARNING: " + msg) if share > MAX_AREA_WEIGHT_UNIT_SHARE else msg)


def attach_potential(candidates: gpd.GeoDataFrame, buildings: gpd.GeoDataFrame,
                     potential_column: str, fallback, label: str):
    """Return ``(values, primary_count, fallback_count)`` aligned to
    ``candidates`` row order.

    Parameters
    ----------
    candidates:
        Points-or-polygons GeoDataFrame whose locations determine which building
        footprint (if any) each candidate falls inside.
    buildings:
        Building-footprint GeoDataFrame containing the ``potential_column``.
        Must be in EPSG:25832 (or will be reprojected to match ``candidates``).
    potential_column:
        Column name in ``buildings`` holding the per-building potential value.
    fallback:
        Array or Series aligned to ``candidates`` row order. Used for candidates
        whose representative point does not fall inside any building footprint.
    label:
        Short identifier used in the log message (e.g. "work", "education").

    Notes
    -----
    Multi-footprint behaviour: when a candidate's representative point falls
    inside more than one building footprint (e.g. touching ALKIS boundaries),
    the first match in ``buildings`` row order is used (via ``drop_duplicates``).

    Returns
    -------
    values : np.ndarray
        Float array aligned to ``candidates`` row order. Primary candidates
        receive the building's ``potential_column`` value; fallback candidates
        keep the supplied fallback value.
    primary_count : int
        Number of candidates matched to a building footprint.
    fallback_count : int
        Number of candidates that fell back to the supplied fallback value.
    """
    fallback = np.asarray(fallback, dtype=float)
    if len(fallback) != len(candidates):
        raise ValueError("fallback length %d != candidates %d"
                         % (len(fallback), len(candidates)))
    if buildings.crs != candidates.crs:
        buildings = buildings.to_crs(candidates.crs)

    pts = candidates[[candidates.geometry.name]].copy()
    pts["_row"] = np.arange(len(candidates))
    # representative_point() lies inside the geometry for both points and polygons
    pts["geometry"] = candidates.geometry.representative_point()
    pts = pts.set_geometry("geometry", crs=candidates.crs)

    joined = gpd.sjoin(
        pts[["_row", "geometry"]],
        buildings[[potential_column, "geometry"]],
        how="left", predicate="within",
    ).drop_duplicates("_row").sort_values("_row")

    values = fallback.copy()
    matched = joined[potential_column].notna().to_numpy()
    rows = joined["_row"].to_numpy()
    values[rows[matched]] = joined.loc[joined[potential_column].notna(),
                                       potential_column].to_numpy()
    primary = int(matched.sum())
    fallback_count = int(len(candidates) - primary)
    log_attach_rate(label, primary, fallback_count)
    return values, primary, fallback_count
