"""Output-identity tests for performance refactors in
``braunschweig.synthesis.population.enriched``.

FIX 3.8 -- per-home zone membership via a single spatial join (APPLIED)
    The legacy code ran one ``gpd.sjoin`` per zone to build the
    ``inside_<zone>`` boolean columns. The refactor
    (:func:`_compute_zone_membership`) runs a SINGLE join and reduces it to the
    same columns. These tests pin that the single-join columns equal the
    per-zone-loop columns exactly, including a home on a shared zone boundary
    and a home inside several overlapping zones.

FIX 3.5 -- car/bike availability raking early-break (SKIPPED, NOT APPLIED)
    Investigated and rejected as NOT output-identical. The raking factors do
    not reach a bit-stable fixed point: on real-valued targets they settle into
    a 1-ULP LIMIT CYCLE (factors alternate between ``1.0000000000000002`` and
    ``0.9999999999999996`` and never become exactly 1.0 simultaneously). The
    final array of the legacy fixed-1000 loop therefore depends on the exact
    sweep parity at iteration 1000; ANY early break (even at ``|factor-1| <
    1e-12``) lands on a different ULP state and is NOT bit-identical (observed:
    a 1-ULP difference). The only break that would be bit-identical requires
    factors == 1.0 exactly, which never occurs, so it would never fire and save
    nothing. Per the task's "if you cannot prove identity, leave it" rule, the
    raking loops are left byte-identical to the original. See the agent report
    for the empirical limit-cycle trace.

Both applied refactors must be output-identical: the seeded synthesis is not
re-baselined, so any value/RNG change would invalidate cached results.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import geopandas as gpd  # noqa: E402
from shapely.geometry import Point, box  # noqa: E402

from braunschweig.synthesis.population.enriched import (  # noqa: E402
    _compute_zone_membership,
)


# ---------------------------------------------------------------------------
# FIX 3.8 -- reference (legacy) per-zone sjoin loop
# ---------------------------------------------------------------------------

def _legacy_zone_membership(df_homes, df_zones):
    """Faithful copy of the legacy per-zone sjoin loop (one join per zone)."""
    df_homes = df_homes.copy()
    for zone in df_zones["name"].unique():
        df_query = gpd.sjoin(
            df_homes, df_zones[df_zones["name"] == zone], predicate="within")
        df_homes["inside_{}".format(zone)] = df_homes["household_id"].isin(
            df_query["household_id"])
    return df_homes


def _build_homes_and_zones():
    """Synthetic homes + zones including a boundary-tie and a multi-zone home.

    Zones A and B are two unit squares that SHARE an edge at x == 1. Zone C is a
    larger square that OVERLAPS zone A entirely, so any home inside A is also
    inside C -- a home that genuinely belongs to two zones at once. A home placed
    exactly on the shared A/B boundary point is ``within`` neither closed-square
    interior, so it tests the boundary-tie / no-match case identically for both
    code paths.
    """
    zone_a = box(0, 0, 1, 1)
    zone_b = box(1, 0, 2, 1)             # shares the edge x == 1 with A
    zone_c = box(-0.5, -0.5, 1.5, 1.5)   # overlaps A (and part of B)

    df_zones = gpd.GeoDataFrame(
        {"name": ["A", "B", "C"]},
        geometry=[zone_a, zone_b, zone_c],
        crs="EPSG:25832",
    )

    homes = [
        ("h_in_a", Point(0.5, 0.5)),        # inside A and C (multi-zone home)
        ("h_in_b", Point(1.5, 0.5)),        # inside B and C
        ("h_in_b_only", Point(1.8, 0.5)),   # inside B only (outside C in x)
        ("h_outside", Point(5.0, 5.0)),     # inside no zone
        ("h_boundary", Point(1.0, 0.5)),    # ON the shared A/B edge (within none)
        ("h_corner_c", Point(-0.25, -0.25)),  # inside C only
    ]
    df_homes = gpd.GeoDataFrame(
        {"household_id": [h[0] for h in homes]},
        geometry=[h[1] for h in homes],
        crs="EPSG:25832",
    )
    return df_homes, df_zones


# ---------------------------------------------------------------------------
# FIX 3.8 -- tests
# ---------------------------------------------------------------------------

def test_zone_membership_matches_per_zone_loop():
    """The single-join helper produces the same ``inside_<zone>`` columns as the
    legacy per-zone sjoin loop, including multi-zone and boundary-tie homes."""
    df_homes, df_zones = _build_homes_and_zones()

    legacy = _legacy_zone_membership(df_homes.copy(), df_zones)
    new = _compute_zone_membership(df_homes.copy(), df_zones)

    for col in ["inside_A", "inside_B", "inside_C"]:
        assert col in new.columns
        pd.testing.assert_series_equal(
            legacy[col], new[col], check_names=True,
            obj=f"{col} (single-join vs per-zone loop)",
        )


def test_zone_membership_boundary_home_resolves_identically():
    """The home on the shared A/B edge resolves to the SAME membership under
    both code paths (no spurious extra match from the single join)."""
    df_homes, df_zones = _build_homes_and_zones()
    legacy = _legacy_zone_membership(df_homes.copy(), df_zones)
    new = _compute_zone_membership(df_homes.copy(), df_zones)

    b_idx = df_homes.index[df_homes["household_id"] == "h_boundary"][0]
    for col in ["inside_A", "inside_B", "inside_C"]:
        assert bool(legacy.loc[b_idx, col]) == bool(new.loc[b_idx, col])


def test_zone_membership_multi_zone_home_counts_in_all_matches():
    """A home inside several zones is True for every matching zone (the legacy
    semantics: per-zone independent ``isin`` membership)."""
    df_homes, df_zones = _build_homes_and_zones()
    new = _compute_zone_membership(df_homes.copy(), df_zones)

    a_idx = df_homes.index[df_homes["household_id"] == "h_in_a"][0]
    assert bool(new.loc[a_idx, "inside_A"])
    assert bool(new.loc[a_idx, "inside_C"])   # also inside the overlapping zone
    assert not bool(new.loc[a_idx, "inside_B"])


def test_zone_membership_coverage_or_matches_loop():
    """The OR of the per-zone columns (used by the caller to derive
    ``inside_external``) is identical between the two code paths."""
    df_homes, df_zones = _build_homes_and_zones()
    legacy = _legacy_zone_membership(df_homes.copy(), df_zones)
    new = _compute_zone_membership(df_homes.copy(), df_zones)

    zone_names = df_zones["name"].unique()
    legacy_cov = np.zeros(len(df_homes), dtype=bool)
    new_cov = np.zeros(len(df_homes), dtype=bool)
    for zone in zone_names:
        legacy_cov |= legacy["inside_{}".format(zone)].to_numpy()
        new_cov |= new["inside_{}".format(zone)].to_numpy()
    assert np.array_equal(legacy_cov, new_cov)
