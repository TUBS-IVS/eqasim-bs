"""Integration test: per-Gemeinde external workplaces (Task 2) through downstream.

Task 2 changed the synthetic external workplace identity from one point per
Kreis (``commune_id == "EXT" + ars5``) to one point per Gemeinde
(``commune_id == "EXT" + gem_ags``, 8-digit AGS).  This test confirms that the
per-Gemeinde frame:

1. flows through the EXACT row-append logic of ``braunschweig.locations.work``
   (the ``df_rows`` construction + sequential ``location_id`` re-issue at
   ``braunschweig/locations/work.py:74-93``) without breaking the schema:
   N external rows appended, contiguous unique ``location_id``s, and the summed
   ``employees`` equal to the Kreis BA outbound total;
2. preserves the per-Kreis commuter total: ``commune_id.str[3:8]`` recovers the
   Kreis ARS5 (documented convention) and grouping ``employees`` by that key
   reproduces the BA Kreis outbound exactly;
3. is correctly joined by the gravity outbound-injection helper
   ``braunschweig.gravity.model._append_outbound_flows`` to the per-person
   destination sampler.

Point 3 is where a REAL integration bug surfaces (see
``test_gravity_external_destination_id_matches_work_pool_commune_id``): the
gravity injection still emits ``destination_id == "EXT" + dest_ars`` (5-digit
Kreis), while the work pool now carries the 8-digit per-Gemeinde
``commune_id == "EXT" + gem_ags``.  The exact-string join in
``synthesis/population/spatial/primary/candidates.py:47``
(``df_locations["commune_id"] == destination_id``) therefore matches ZERO
external workplaces.  Documented here as an xfail so the regression is visible
without masking the broken primary path (CLAUDE.md: no silent fallbacks).
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from braunschweig.data.external_workplaces import build_external_workplaces
from braunschweig.gravity.model import _append_outbound_flows


def _build_per_gemeinde_external_frame():
    """Per-Gemeinde external workplace frame for ONE Kreis with TWO Gemeinden.

    Returns the (df_external, kreis_total) pair.  Built via the REAL
    ``build_external_workplaces`` so the column schema and the
    ``commune_id == "EXT" + gem_ags`` / ``iris_id`` conventions match
    production exactly (no re-implementation).
    """
    kreis_total = 1000
    # Two Gemeinden of Kreis 03241, population-weighted 75/25 -> 750/250 SvB.
    gemeinden = gpd.GeoDataFrame(
        [
            {"ars5": "03241", "gem_ags": "03241001", "ewz": 75000.0,
             "geometry": Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])},
            {"ars5": "03241", "gem_ags": "03241002", "ewz": 25000.0,
             "geometry": Polygon([(10, 0), (11, 0), (11, 1), (10, 1)])},
        ],
        crs="EPSG:25832",
    )
    outbound = pd.DataFrame({"ars5": ["03241"], "employees": [kreis_total]})
    df_external = build_external_workplaces(outbound, gemeinden, min_flow=50)
    return df_external, kreis_total


def test_external_rows_append_via_real_locations_work_logic():
    """Exercise the EXACT df_rows/location_id construction of locations.work.

    Replicates only ``braunschweig/locations/work.py:74-93`` (the synthetic-row
    builder + sequential ``location_id`` re-issue) against a small ZGB base
    pool, and asserts the row-append invariants: N external rows appended,
    contiguous unique ``location_id``s, summed ``employees`` == Kreis total.
    """
    df_external, kreis_total = _build_per_gemeinde_external_frame()

    # Minimal ZGB base pool mirroring the schema of ``_execute_base`` output
    # (employees, fake, commune_id, iris_id, geometry, location_id).
    df_zgb = gpd.GeoDataFrame(
        {
            "employees": [10, 20, 30],
            "fake": [False, False, True],
            "commune_id": ["03101000", "03101000", "03102000"],
            "iris_id": ["031010000000", "031010000000", "031020000000"],
            "geometry": [Point(0, 0), Point(1, 1), Point(2, 2)],
            "location_id": ["work_0", "work_1", "work_2"],
        },
        crs="EPSG:25832",
    )

    if df_external.crs != df_zgb.crs:
        df_external = df_external.to_crs(df_zgb.crs)

    # --- BEGIN replica of braunschweig/locations/work.py:74-93 -------------
    df_rows = gpd.GeoDataFrame(
        {
            "employees": df_external["employees"].astype(int),
            "fake": False,
            "commune_id": df_external["commune_id"].astype(str),
            "iris_id": df_external["iris_id"].astype(str),
            "geometry": df_external["geometry"],
        },
        crs=df_zgb.crs,
    )
    df_combined = gpd.GeoDataFrame(
        pd.concat(
            [
                df_zgb.drop(columns=["location_id"], errors="ignore"),
                df_rows,
            ],
            ignore_index=True,
        ),
        crs=df_zgb.crs,
    )
    df_combined["location_id"] = "work_" + pd.Series(
        range(len(df_combined)), index=df_combined.index
    ).astype(str)
    # --- END replica -------------------------------------------------------

    # One external row per Gemeinde (two Gemeinden in the synthetic Kreis).
    assert len(df_rows) == 2, "expected one external workplace per Gemeinde"
    assert len(df_combined) == len(df_zgb) + len(df_rows)

    # location_id space is contiguous and unique after re-issue.
    assert df_combined["location_id"].is_unique
    assert list(df_combined["location_id"]) == [
        f"work_{i}" for i in range(len(df_combined))
    ]

    # External employees sum exactly to the Kreis BA outbound total.
    ext = df_combined[df_combined["commune_id"].str.startswith("EXT")]
    assert int(ext["employees"].sum()) == kreis_total
    assert (ext["employees"] > 0).all()


def test_kreis_recoverable_from_per_gemeinde_commune_id():
    """commune_id.str[3:8] recovers the Kreis and reproduces the Kreis total.

    Verifies the documented convention (CLAUDE.md / external_workplaces.py
    docstring): with per-Gemeinde ids ``EXT<gem_ags8>`` the Kreis ARS5 is the
    8 -> [3:8] slice, NOT [:5] (which yields "EXT" + 2 digits).  Grouping the
    employee counts by the recovered Kreis must equal the BA outbound total.
    """
    df_external, kreis_total = _build_per_gemeinde_external_frame()

    # [:5] is WRONG for these ids -- it returns "EXT" + first 2 AGS digits.
    assert (df_external["commune_id"].str[:5] == "EXT03").all()

    # [3:8] is the documented Kreis recovery.
    recovered_kreis = df_external["commune_id"].str[3:8]
    assert (recovered_kreis == "03241").all()
    assert (recovered_kreis == df_external["ars5"]).all()

    by_kreis = df_external.groupby(df_external["commune_id"].str[3:8])["employees"].sum()
    assert int(by_kreis.loc["03241"]) == kreis_total


def test_gravity_injection_keys_destinations_on_kreis_ars5():
    """Confirm the gravity injection still keys EXT destinations on the Kreis.

    The real ``_append_outbound_flows`` derives the external ``destination_id``
    from ``dest_ars`` (the BA Kreis), independently of the per-Gemeinde
    ``df_external`` rows (which it consumes ONLY as the set of in-scope Kreise).
    This pins the behaviour that produces the downstream key mismatch.
    """
    # One ZGB origin Gemeinde with an outbound flow to external Kreis 03241.
    df_population = pd.DataFrame({
        "commune_id": ["03101000"],
        "weight": [1000.0],
    })
    df_pendler = pd.DataFrame({
        "orig_ars": ["03101"],
        "dest_ars": ["03241"],
        "flow": [500.0],
    })
    df_external, _ = _build_per_gemeinde_external_frame()
    # A trivial intra-origin OD row so each origin's weights can normalise.
    df_od = pd.DataFrame({
        "origin_id": ["03101000"],
        "destination_id": ["03101000"],
        "weight": [1.0],
    })

    out = _append_outbound_flows(
        df_od, df_population, df_pendler, df_external, scope=["03101"],
    )

    ext = out[out["destination_id"].str.startswith("EXT")]
    assert len(ext) > 0, "no external destination injected"
    # The injection emits the 5-digit Kreis id, regardless of how many
    # per-Gemeinde points the work pool now has for that Kreis.
    assert set(ext["destination_id"]) == {"EXT03241"}


@pytest.mark.xfail(
    reason="INTEGRATION BUG (Task 2): gravity emits destination_id='EXT'+ars5 "
           "(5-digit Kreis) at braunschweig/gravity/model.py:747, but the work "
           "pool now carries commune_id='EXT'+gem_ags (8-digit Gemeinde). The "
           "exact-string join in candidates.py:47 matches ZERO external "
           "workplaces. Gravity must be updated to split outbound flow across "
           "the per-Gemeinde EXT points (or join on Kreis), not emit one "
           "EXT<ars5> destination.",
    strict=True,
)
def test_gravity_external_destination_id_matches_work_pool_commune_id():
    """The per-person sampler joins gravity destination_id == pool commune_id.

    ``synthesis/population/spatial/primary/candidates.py:47`` does
    ``df_locations[df_locations["commune_id"] == destination_id]``.  For the
    external pool to be reachable, the set of EXT destination_ids emitted by
    gravity MUST be a subset of the EXT commune_ids in the work pool.  Under
    Task 2 they diverge (5-digit Kreis vs 8-digit Gemeinde), so this fails.
    """
    df_external, _ = _build_per_gemeinde_external_frame()
    pool_commune_ids = set(df_external["commune_id"])  # {"EXT03241001", "EXT03241002"}

    df_population = pd.DataFrame({"commune_id": ["03101000"], "weight": [1000.0]})
    df_pendler = pd.DataFrame({
        "orig_ars": ["03101"], "dest_ars": ["03241"], "flow": [500.0],
    })
    df_od = pd.DataFrame({
        "origin_id": ["03101000"], "destination_id": ["03101000"], "weight": [1.0],
    })
    out = _append_outbound_flows(
        df_od, df_population, df_pendler, df_external, scope=["03101"],
    )
    gravity_ext_dests = set(
        out.loc[out["destination_id"].str.startswith("EXT"), "destination_id"]
    )

    # Every external destination the sampler will look up MUST exist in the
    # work pool, otherwise the multinomial draw lands on an empty candidate set.
    unmatched = gravity_ext_dests - pool_commune_ids
    assert not unmatched, (
        "gravity external destinations absent from work pool commune_ids: "
        f"{sorted(unmatched)} (pool has {sorted(pool_commune_ids)})"
    )
