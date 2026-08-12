"""Per-category building potentials + landuse candidate assembly (issue #262).

Task 4: two pure helpers in ``secondary_chainsolvers`` that (a) derive the
five SrV-grounded building categories' offer/potential columns, and (b) turn
deterministic landuse grid points into ``sec_lu_*`` candidate rows. Both are
pure functions (no synpp context).

PLAN AMENDMENT (post-Task-4 review): the leisure_* categories genuinely MASK
the existing pot_leisure aggregate (unchanged). The errand_* categories
CANNOT mask pot_other -- every sec_b_* row carries pot_other=0.0 by
construction and errand-class buildings (hospitals, services, ...) are
excluded from build_secondary_candidates' candidate set entirely (its
keep-filter is retail>0 | leisure>0). append_location_category_columns now
derives the errand potential directly from df_potentials (the
derive_other_potential cap-and-floor formula, applied per category) and
appends a new sec_b_<building_id> row for every errand-class building absent
from the input candidates.
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
    """Three candidate rows: one sec_b_* building (leisure) + one sec_b_*
    building (unmapped class) + one legacy 'sec_0' row.

    Errand-class buildings (hospitals, ...) are deliberately ABSENT here --
    they never make it into build_secondary_candidates' gpkg output (its
    keep-filter is retail>0 | leisure>0), which is exactly the structural gap
    append_location_category_columns's errand path has to compensate for by
    appending new rows (see _mini_potentials below).
    """
    return gpd.GeoDataFrame({
        "location_id": ["sec_b_b1", "sec_b_b3", "sec_0"],
        "commune_id": ["1", "1", "1"],
        "iris_id": ["1", "1", "1"],
        "offers_shop": [False, False, False],
        "offers_leisure": [True, False, False],
        "offers_other": [False, False, True],
        "offers_escort": [True, True, True],
        "pot_shop": [0.0, 0.0, 0.0],
        "pot_shop_daily": [0.0, 0.0, 0.0],
        "pot_shop_non_daily": [0.0, 0.0, 0.0],
        "pot_leisure": [5.0, 0.0, 0.0],
        "pot_other": [0.0, 0.0, 3.0],
        "geometry": [Point(0, 0), Point(2, 2), Point(3, 3)],
    }, crs="EPSG:25832")


def _mini_potentials():
    """building_id b1 (cinema, maps to leisure_culture, already a candidate),
    b3 (office, unmapped class, already a candidate); b2/b4/b5 (hospitals,
    maps to errand_authority_medical) are NOT in _mini_candidates() -- they
    only ever surface as NEW sec_b_* rows appended by
    append_location_category_columns.

    potential_generic for the three hospitals is [500, 100, 400] so the
    default cap_percentile=0.99 caps b2's 500 down to ~498 (demonstrating the
    cap), while b5's volume_m3=10 is below the default
    min_volume_m3=50 floor (demonstrating the volume floor) regardless of its
    generic potential.
    """
    return gpd.GeoDataFrame({
        "building_id": ["b1", "b3", "b2", "b4", "b5"],
        "bosserhof_class_clean": [
            "large cinemas", "normal office", "hospitals", "hospitals", "hospitals",
        ],
        "potential_generic": [999.0, 50.0, 500.0, 100.0, 400.0],
        "volume_m3": [999.0, 999.0, 1000.0, 1000.0, 10.0],
        "commune_id": ["1", "1", "1", "1", "1"],
        "geometry": [Point(0, 0), Point(2, 2), Point(50, 50), Point(60, 60), Point(70, 70)],
    }, crs="EPSG:25832")


def _mini_mapping():
    """No class maps to leisure_gastronomy or errand_service in
    _mini_potentials() -- both stay genuinely zero-supply categories, used by
    the check_category_supply "all empty" test below."""
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
# append_location_category_columns -- leisure categories (unchanged: masks
# the existing pot_leisure aggregate on pre-existing sec_b_* rows).
# ---------------------------------------------------------------------------

def test_building_category_columns_mask_existing_potentials():
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    row = out[out.location_id == "sec_b_b1"].iloc[0]
    assert row["offers_leisure_culture"] and row["pot_leisure_culture"] == 5.0
    assert not row["offers_leisure_gastronomy"] and row["pot_leisure_gastronomy"] == 0.0
    assert not row["offers_leisure_sports"] and row["pot_leisure_sports"] == 0.0
    # A leisure building must not spuriously pick up an errand category.
    assert not row["offers_errand_authority_medical"] and row["pot_errand_authority_medical"] == 0.0


def test_unmapped_class_gets_no_category():
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    row = out[out.location_id == "sec_b_b3"].iloc[0]
    for category in sc.SRV_BUILDING_CATEGORY_BASE_POTENTIAL:
        assert not row["offers_" + category]
        assert row["pot_" + category] == 0.0


def test_non_building_rows_never_get_a_category():
    """The legacy 'sec_0' row is not a sec_b_* building candidate; it must
    stay False/0.0 for all five categories even though it carries a non-zero
    pot_other."""
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
    join gap -- it must fall back to False/0.0 for the leisure categories,
    not raise, but the fallback rate must be surfaced (CLAUDE.md fallback
    transparency). _mini_candidates() has 2 sec_b_* rows (b1, b3); dropping
    b3 from the potentials source leaves 1/2 unmatched."""
    potentials = _mini_potentials()
    potentials = potentials[potentials["building_id"] != "b3"]
    out = sc.append_location_category_columns(_mini_candidates(), potentials, _mini_mapping())
    row = out[out.location_id == "sec_b_b3"].iloc[0]
    assert not row["offers_leisure_culture"] and row["pot_leisure_culture"] == 0.0
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "1/2" in captured.out


# ---------------------------------------------------------------------------
# append_location_category_columns -- errand categories (plan amendment:
# derived from df_potentials via the derive_other_potential cap-and-floor
# formula, appending new sec_b_* rows for buildings absent from candidates).
# ---------------------------------------------------------------------------

def test_errand_categories_use_derived_potential_formula():
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())

    # b2/b4/b5 are hospital-class buildings absent from the input candidates;
    # each must be appended as a brand-new sec_b_* row.
    assert {"sec_b_b2", "sec_b_b4", "sec_b_b5"} <= set(out["location_id"])

    # cap = nanquantile([100.0, 400.0, 500.0], 0.99) == 498.0
    row_b2 = out[out.location_id == "sec_b_b2"].iloc[0]  # generic=500 > cap -> capped
    assert row_b2["offers_errand_authority_medical"]
    assert row_b2["pot_errand_authority_medical"] == pytest.approx(498.0)

    row_b4 = out[out.location_id == "sec_b_b4"].iloc[0]  # generic=100 < cap -> unchanged
    assert row_b4["offers_errand_authority_medical"]
    assert row_b4["pot_errand_authority_medical"] == pytest.approx(100.0)

    row_b5 = out[out.location_id == "sec_b_b5"].iloc[0]  # volume_m3=10 < min_volume_m3=50 -> floored
    assert not row_b5["offers_errand_authority_medical"]
    assert row_b5["pot_errand_authority_medical"] == 0.0

    # A new errand-only row must not offer anything else -- not the other
    # errand category, not either base purpose, not any leisure category.
    for row in (row_b2, row_b4, row_b5):
        assert not row["offers_shop"] and row["pot_shop"] == 0.0
        assert not row["offers_leisure"] and row["pot_leisure"] == 0.0
        assert not row["offers_other"] and row["pot_other"] == 0.0
        assert not row["offers_escort"]
        assert not row["offers_errand_service"] and row["pot_errand_service"] == 0.0
        assert not row["offers_leisure_culture"] and not row["offers_leisure_gastronomy"]
        assert not row["offers_leisure_sports"]
        assert row["commune_id"] == "1" and row["iris_id"] == "1"


def test_errand_category_updates_existing_row_in_place():
    """A building that is BOTH an errand-class building AND already a
    candidate (e.g. it also has retail/leisure potential) must get its
    errand columns updated on the existing row, not duplicated."""
    candidates = _mini_candidates()
    extra_row = gpd.GeoDataFrame({
        "location_id": ["sec_b_b2"], "commune_id": ["1"], "iris_id": ["1"],
        "offers_shop": [True], "offers_leisure": [False], "offers_other": [False],
        "offers_escort": [True],
        "pot_shop": [12.0], "pot_shop_daily": [12.0], "pot_shop_non_daily": [0.0],
        "pot_leisure": [0.0], "pot_other": [0.0], "geometry": [Point(50, 50)],
    }, crs="EPSG:25832")
    candidates = gpd.GeoDataFrame(
        pd.concat([candidates, extra_row], ignore_index=True), crs=candidates.crs)
    n_before = len(candidates)

    out = sc.append_location_category_columns(candidates, _mini_potentials(), _mini_mapping())

    assert len(out[out.location_id == "sec_b_b2"]) == 1  # updated in place, not duplicated
    row_b2 = out[out.location_id == "sec_b_b2"].iloc[0]
    assert row_b2["offers_errand_authority_medical"]
    assert row_b2["pot_errand_authority_medical"] == pytest.approx(498.0)
    assert row_b2["offers_shop"] and row_b2["pot_shop"] == 12.0  # pre-existing columns untouched
    # b4 and b5 are still absent from candidates -> still appended fresh.
    assert len(out) == n_before + 2


def test_errand_category_new_row_uses_polygon_centroid_geometry():
    potentials = gpd.GeoDataFrame({
        "building_id": ["h1"],
        "bosserhof_class_clean": ["hospitals"],
        "potential_generic": [200.0],
        "volume_m3": [1000.0],
        "commune_id": ["1"],
        "geometry": [box(0, 0, 10, 10)],
    }, crs="EPSG:25832")
    mapping = pd.DataFrame({
        "bosserhof_class": ["hospitals"],
        "location_category": ["errand_authority_medical"],
    })
    out = sc.append_location_category_columns(_mini_candidates(), potentials, mapping)
    row = out[out.location_id == "sec_b_h1"].iloc[0]
    expected_centroid = box(0, 0, 10, 10).centroid
    assert row["geometry"].equals_exact(expected_centroid, tolerance=1e-9)


def test_errand_category_cap_percentile_and_min_volume_are_configurable():
    potentials = gpd.GeoDataFrame({
        "building_id": ["h1", "h2", "h3"],
        "bosserhof_class_clean": ["hospitals", "hospitals", "hospitals"],
        "potential_generic": [100.0, 300.0, 500.0],
        "volume_m3": [1000.0, 1000.0, 40.0],
        "commune_id": ["1", "1", "1"],
        "geometry": [Point(1, 1), Point(2, 2), Point(3, 3)],
    }, crs="EPSG:25832")
    mapping = pd.DataFrame({
        "bosserhof_class": ["hospitals"],
        "location_category": ["errand_authority_medical"],
    })
    out = sc.append_location_category_columns(
        _mini_candidates(), potentials, mapping, min_volume_m3=45.0, cap_percentile=0.5)

    row_h1 = out[out.location_id == "sec_b_h1"].iloc[0]
    row_h2 = out[out.location_id == "sec_b_h2"].iloc[0]
    row_h3 = out[out.location_id == "sec_b_h3"].iloc[0]
    # median of [100, 300, 500] = 300 (the cap at cap_percentile=0.5).
    assert row_h1["pot_errand_authority_medical"] == pytest.approx(100.0)
    assert row_h2["pot_errand_authority_medical"] == pytest.approx(300.0)
    # h3's volume_m3=40 < min_volume_m3=45 -> floored regardless of its generic/cap.
    assert row_h3["pot_errand_authority_medical"] == 0.0


def test_errand_category_zero_members_warns_and_yields_no_supply(capsys):
    """errand_service has no class-member buildings anywhere in
    _mini_potentials() -- the per-category cap must fall back to the
    all-building quantile (mirroring derive_other_potential's own fallback)
    and the fact must be logged, not silently swallowed."""
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "errand_service" in captured.out
    assert not out["offers_errand_service"].any()
    assert (out["pot_errand_service"] == 0.0).all()


# ---------------------------------------------------------------------------
# check_category_supply
# ---------------------------------------------------------------------------

def test_check_category_supply_raises_for_zero_supply_category():
    candidates = _mini_candidates()  # has no pot_leisure_gastronomy column at all
    with pytest.raises(RuntimeError, match="leisure_gastronomy"):
        sc.check_category_supply(candidates, ["leisure_gastronomy"])


def test_check_category_supply_passes_when_positive_potential_exists():
    out = sc.append_location_category_columns(_mini_candidates(), _mini_potentials(), _mini_mapping())
    # Must not raise: leisure_culture (b1, 5.0) and errand_authority_medical
    # (b2/b4, appended with positive potential) both have a positive row.
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
