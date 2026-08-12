"""Per-category building potentials + landuse candidate assembly (issue #262).

Task 4: two pure helpers in ``secondary_chainsolvers`` that (a) mask the
existing pot_leisure / pot_other aggregates into five SrV-grounded building
categories, and (b) turn deterministic landuse grid points into
``sec_lu_*`` candidate rows. Both are pure functions (no synpp context).
"""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.synthesis.locations import landuse_candidates
from braunschweig.synthesis.locations import secondary_chainsolvers as sc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mini_candidates():
    """Four candidate rows: three sec_b_* buildings + one legacy 'sec_0' row.

    b1 carries pot_leisure=5.0 (class "large cinemas" maps to leisure_culture),
    b2 carries pot_other=8.0 (class "hospitals" maps to errand_authority_medical),
    b3 carries no potential and an unmapped class ("normal office"); sec_0 is
    not a building candidate at all (must never receive a category value).
    """
    return gpd.GeoDataFrame({
        "location_id": ["sec_b_b1", "sec_b_b2", "sec_b_b3", "sec_0"],
        "commune_id": ["1", "1", "1", "1"],
        "iris_id": ["1", "1", "1", "1"],
        "offers_shop": [False, False, False, False],
        "offers_leisure": [True, False, False, False],
        "offers_other": [False, True, False, True],
        "offers_escort": [True, True, True, True],
        "pot_shop": [0.0, 0.0, 0.0, 0.0],
        "pot_shop_daily": [0.0, 0.0, 0.0, 0.0],
        "pot_shop_non_daily": [0.0, 0.0, 0.0, 0.0],
        "pot_leisure": [5.0, 0.0, 0.0, 0.0],
        "pot_other": [0.0, 8.0, 0.0, 3.0],
        "geometry": [Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)],
    }, crs="EPSG:25832")


def _mini_potentials():
    return pd.DataFrame({
        "building_id": ["b1", "b2", "b3"],
        "bosserhof_class_clean": ["large cinemas", "hospitals", "normal office"],
    })


def _mini_mapping():
    return pd.DataFrame({
        "bosserhof_class": ["large cinemas", "hospitals", "restaurants"],
        "location_category": ["leisure_culture", "errand_authority_medical", "leisure_gastronomy"],
    })


def _mini_landuse_points():
    """Four grid points: one per leisure layer inside the study municipality,
    plus one 'ln_sportanlage' point far outside it (dropped)."""
    return gpd.GeoDataFrame({
        "layer": [
            "ln_freiluftundnaherholung", "ln_sportanlage",
            "ln_kulturundunterhaltung", "ln_sportanlage",
        ],
        "represented_area_m2": [22500.0, 22500.0, 100.0, 22500.0],
        "geometry": [Point(10, 10), Point(20, 20), Point(30, 30), Point(9999, 9999)],
    }, crs="EPSG:25832")


def _mini_municipalities():
    return gpd.GeoDataFrame({
        "commune_id": ["1"],
        "geometry": [box(0, 0, 100, 100)],
    }, crs="EPSG:25832")


# ---------------------------------------------------------------------------
# append_location_category_columns
# ---------------------------------------------------------------------------

def test_building_category_columns_mask_existing_potentials():
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    row = out[out.location_id == "sec_b_b1"].iloc[0]
    assert row["offers_leisure_culture"] and row["pot_leisure_culture"] == 5.0
    assert not row["offers_leisure_gastronomy"] and row["pot_leisure_gastronomy"] == 0.0
    assert not row["offers_leisure_sports"] and row["pot_leisure_sports"] == 0.0


def test_unmapped_class_gets_no_category():
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    row = out[out.location_id == "sec_b_b3"].iloc[0]
    for category in sc.SRV_BUILDING_CATEGORY_BASE_POTENTIAL:
        assert not row["offers_" + category]
        assert row["pot_" + category] == 0.0


def test_errand_categories_mask_pot_other():
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    row = out[out.location_id == "sec_b_b2"].iloc[0]
    assert row["offers_errand_authority_medical"] and row["pot_errand_authority_medical"] == 8.0
    assert not row["offers_errand_service"] and row["pot_errand_service"] == 0.0


def test_non_building_rows_never_get_a_category():
    """The legacy 'sec_0' row is not a sec_b_* building candidate; it must
    stay False/0.0 for all five categories even though it carries a non-zero
    pot_other (which would otherwise, if the row were mistakenly treated as
    a building, get masked into errand_service/errand_authority_medical)."""
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    row = out[out.location_id == "sec_0"].iloc[0]
    for category in sc.SRV_BUILDING_CATEGORY_BASE_POTENTIAL:
        assert not row["offers_" + category]
        assert row["pot_" + category] == 0.0


def test_missing_base_potential_column_raises():
    candidates = _mini_candidates().drop(columns=["pot_leisure"])
    with pytest.raises(ValueError, match="pot_leisure"):
        sc.append_location_category_columns(candidates, _mini_potentials(), _mini_mapping())


def test_missing_potentials_source_column_raises():
    potentials = _mini_potentials().drop(columns=["bosserhof_class_clean"])
    with pytest.raises(ValueError, match="bosserhof_class_clean"):
        sc.append_location_category_columns(_mini_candidates(), potentials, _mini_mapping())


def test_missing_mapping_column_raises():
    mapping = _mini_mapping().drop(columns=["location_category"])
    with pytest.raises(ValueError, match="location_category"):
        sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), mapping)


def test_building_not_in_potentials_gets_no_category_and_warns(capsys):
    """A sec_b_* candidate whose building_id has no row in df_potentials is a
    join gap (candidates and building_potentials should share the same
    building_id space) -- it must fall back to False/0.0, not raise, but the
    fallback rate must be surfaced (CLAUDE.md fallback transparency)."""
    potentials = _mini_potentials()
    potentials = potentials[potentials["building_id"] != "b3"]
    out = sc.append_location_category_columns(_mini_candidates(), potentials, _mini_mapping())
    row = out[out.location_id == "sec_b_b3"].iloc[0]
    assert not row["offers_leisure_culture"] and row["pot_leisure_culture"] == 0.0
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "1/3" in captured.out


# ---------------------------------------------------------------------------
# check_category_supply
# ---------------------------------------------------------------------------

def test_check_category_supply_raises_for_zero_supply_category():
    candidates = _mini_candidates()  # has no pot_leisure_gastronomy column at all
    with pytest.raises(RuntimeError, match="leisure_gastronomy"):
        sc.check_category_supply(candidates, ["leisure_gastronomy"])


def test_check_category_supply_passes_when_positive_potential_exists():
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    # Must not raise: leisure_culture (5.0) and errand_authority_medical (8.0)
    # both have a positive-potential row.
    sc.check_category_supply(out, ["leisure_culture", "errand_authority_medical"])


def test_check_category_supply_names_all_empty_categories():
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    with pytest.raises(RuntimeError, match="leisure_gastronomy.*errand_service|errand_service.*leisure_gastronomy"):
        sc.check_category_supply(out, ["leisure_culture", "leisure_gastronomy", "errand_service"])


# ---------------------------------------------------------------------------
# append_landuse_candidates
# ---------------------------------------------------------------------------

def test_landuse_rows_added_with_area_potential_and_commune():
    candidates = _mini_candidates()
    out = sc.append_landuse_candidates(
        candidates, _mini_landuse_points(), landuse_candidates.LANDUSE_LAYER_TO_CATEGORY,
        _mini_municipalities())
    lu = out[out.location_id.str.startswith("sec_lu_")]
    assert len(lu) == 3  # the point at (9999, 9999) is outside the municipality
    assert (lu["offers_leisure_outdoor"] | lu["offers_leisure_sports"] | lu["offers_leisure_culture"]).all()
    assert (lu["pot_shop"] == 0.0).all()
    assert lu["commune_id"].notna().all()
    assert (lu["commune_id"] == "1").all()
    assert (lu["iris_id"] == "1").all()

    row0 = out[out.location_id == "sec_lu_0"].iloc[0]
    assert row0["offers_leisure_outdoor"] and row0["pot_leisure_outdoor"] == 22500.0
    assert not row0["offers_leisure_sports"] and not row0["offers_leisure_culture"]

    row2 = out[out.location_id == "sec_lu_2"].iloc[0]
    assert row2["offers_leisure_culture"] and row2["pot_leisure_culture"] == 100.0


def test_landuse_preserves_pre_existing_category_values():
    """When append_location_category_columns already ran, its per-building
    category values for pre-existing rows must survive the landuse append
    untouched (only new landuse rows get fresh category values)."""
    candidates = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    out = sc.append_landuse_candidates(
        candidates, _mini_landuse_points(), landuse_candidates.LANDUSE_LAYER_TO_CATEGORY,
        _mini_municipalities())
    row = out[out.location_id == "sec_b_b1"].iloc[0]
    assert row["offers_leisure_culture"] and row["pot_leisure_culture"] == 5.0


def test_point_outside_municipalities_dropped_and_counted(capsys):
    candidates = _mini_candidates()
    out = sc.append_landuse_candidates(
        candidates, _mini_landuse_points(), landuse_candidates.LANDUSE_LAYER_TO_CATEGORY,
        _mini_municipalities())
    assert "sec_lu_3" not in set(out["location_id"])
    captured = capsys.readouterr()
    assert "3/4" in captured.out
    assert "1 dropped" in captured.out


def test_landuse_unknown_layer_raises():
    points = _mini_landuse_points()
    points.loc[0, "layer"] = "ln_unknown_layer"
    with pytest.raises(ValueError, match="ln_unknown_layer"):
        sc.append_landuse_candidates(
            _mini_candidates(), points, landuse_candidates.LANDUSE_LAYER_TO_CATEGORY,
            _mini_municipalities())


def test_landuse_missing_municipality_commune_id_raises():
    municipalities = _mini_municipalities().rename(columns={"commune_id": "zone_id"})
    with pytest.raises(ValueError, match="commune_id"):
        sc.append_landuse_candidates(
            _mini_candidates(), _mini_landuse_points(), landuse_candidates.LANDUSE_LAYER_TO_CATEGORY,
            municipalities)


def test_landuse_missing_points_column_raises():
    points = _mini_landuse_points().drop(columns=["represented_area_m2"])
    with pytest.raises(ValueError, match="represented_area_m2"):
        sc.append_landuse_candidates(
            _mini_candidates(), points, landuse_candidates.LANDUSE_LAYER_TO_CATEGORY,
            _mini_municipalities())


def test_landuse_growth_factor_warn(capsys):
    """A large landuse point count relative to the tiny candidates base must
    trigger the VISIT_CANDIDATE_WARN_FACTOR growth-guard warning."""
    candidates = _mini_candidates()
    many_points = gpd.GeoDataFrame({
        "layer": ["ln_sportanlage"] * 20,
        "represented_area_m2": [100.0] * 20,
        "geometry": [Point(1 + 0.01 * i, 1 + 0.01 * i) for i in range(20)],
    }, crs="EPSG:25832")
    sc.append_landuse_candidates(
        candidates, many_points, landuse_candidates.LANDUSE_LAYER_TO_CATEGORY,
        _mini_municipalities())
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "growth" in captured.out
