"""Tests for the assembled secondary-candidate stage + facilities coverage.

Guards the 2026-07-11 kreis5 failure mode: the chainsolvers placed activities
on the REPLACE candidate set (sec_b_* gpkg buildings, external Gemeinde
centroids, sec_res_* visit rows) while facilities.xml only contained the
legacy frame -- every realised sec_b_* id crashed MATSim RunPreparation
(LinkAssignment: "Facility ... does not exist"). Both consumers now share
``braunschweig.synthesis.locations.secondary_candidates``; the facilities
writer additionally fail-fasts on any dangling realised id.

Synthetic frames only; no real data.
"""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon, box

from braunschweig.matsim.scenario.facilities import (
    secondary_facility_frame,
    validate_secondary_coverage,
)
from braunschweig.synthesis.locations import secondary_candidates


class StubContext:
    """Minimal synpp-like context: dict-backed config() and stage()."""

    def __init__(self, config, stages):
        self._config = dict(config)
        self._stages = dict(stages)

    def config(self, key, default=None, volatile=False):
        # Mirrors synpp ConfigurationContext.config(option, default, volatile)
        # (see #259): "volatile" only marks an option as cache-neutral, it has
        # no effect on the returned value, so this double accepts and ignores it.
        if key in self._config:
            return self._config[key]
        self._config[key] = default
        return default

    def stage(self, name):
        if name not in self._stages:
            raise KeyError(f"stage {name!r} not stubbed")
        return self._stages[name]


CRS = "EPSG:25832"


def _legacy_frame():
    return gpd.GeoDataFrame(
        {
            "location_id": ["sec_0", "sec_1"],
            "commune_id": ["03101000", "03101000"],
            "iris_id": ["03101000", "03101000"],
            "offers_shop": [True, False],
            "offers_leisure": [False, True],
            "offers_other": [True, True],
        },
        geometry=[Point(0, 0), Point(10, 10)], crs=CRS,
    )


def _buildings_potentials_frame():
    # One building with retail potential, one with leisure potential.
    square = Polygon([(0, 0), (0, 5), (5, 5), (5, 0)])
    return gpd.GeoDataFrame(
        {
            "building_id": [11, 22],
            "potential_retail_daily": [4.0, 0.0],
            "potential_retail_non_daily": [1.0, 0.0],
            "potential_leisure": [0.0, 3.0],
            "potential_generic": [2.0, 2.0],
            "commune_id": ["03101000", "03101000"],
        },
        geometry=[square, square], crs=CRS,
    )


def _external_frame():
    return gpd.GeoDataFrame(
        {"ewz": [1000.0], "commune_id": ["03459999"]},
        geometry=[Point(99999, 99999)], crs=CRS,
    )


def _residential_frame():
    return gpd.GeoDataFrame(
        {
            "building_id": [77],
            "weight": [2.5],
            "commune_id": ["03101000"],
        },
        geometry=[Point(5, 5)], crs=CRS,
    )


def _stage_context(sec_enabled=True, external=True, visit=False):
    config = {
        "secondary_building_potentials": sec_enabled,
        "secondary_external_candidates": external,
        "cordon_enabled": True,  # suppress the cordon warning in tests
        "secondary_other_smart_potential": False,
        "secondary_leisure_subtype_split": visit,
        "leisure_visit_building_potential": visit,
    }
    stages = {
        "synthesis.locations.secondary": _legacy_frame(),
        "braunschweig.data.building_potentials": _buildings_potentials_frame(),
        "braunschweig.data.external_secondary_points": _external_frame(),
        "braunschweig.data.buildings": _residential_frame(),
    }
    return StubContext(config, stages)


def _srv_building_potentials_frame():
    """Five buildings, mapped via the real committed Bosserhof->location-
    category classes to all five SrV building categories (issue #262):

    - 101/102/103 carry a positive pot_leisure and mask into the three
      leisure_* categories (unchanged leisure masking).
    - 104/105 carry NO retail/leisure potential (so build_secondary_candidates'
      keep-filter excludes them from the input candidates entirely) but carry
      a positive potential_generic and volume_m3 above the floor, so
      append_location_category_columns derives a positive errand_* potential
      for them directly from df_potentials and appends them as NEW sec_b_*
      candidate rows (the #262 plan amendment, commit 75c6021).
    """
    boxes = [
        box(0, 100, 5, 105), box(10, 100, 15, 105), box(20, 100, 25, 105),
        box(30, 100, 35, 105), box(40, 100, 45, 105),
    ]
    return gpd.GeoDataFrame(
        {
            "building_id": ["101", "102", "103", "104", "105"],
            "potential_retail_daily": [0.0, 0.0, 0.0, 0.0, 0.0],
            "potential_retail_non_daily": [0.0, 0.0, 0.0, 0.0, 0.0],
            "potential_leisure": [5.0, 6.0, 7.0, 0.0, 0.0],
            "potential_generic": [0.0, 0.0, 0.0, 80.0, 60.0],
            "volume_m3": [1000.0, 1000.0, 1000.0, 100.0, 100.0],
            "commune_id": ["03101000"] * 5,
            "bosserhof_class_clean": [
                "large cinemas", "restaurants gastronomy", "fitness wellness",
                "hospitals", "business oriented services",
            ],
        },
        geometry=boxes, crs=CRS,
    )


def _srv_location_category_mapping():
    return pd.DataFrame({
        "bosserhof_class": [
            "large cinemas", "restaurants gastronomy", "fitness wellness",
            "hospitals", "business oriented services",
        ],
        "location_category": [
            "leisure_culture", "leisure_gastronomy", "leisure_sports",
            "errand_authority_medical", "errand_service",
        ],
    })


def _srv_landuse_frame():
    """One ATKIS outdoor-leisure polygon, large enough that grid_seed_polygons
    (10 m spacing in the SrV test config) catches interior grid nodes."""
    return gpd.GeoDataFrame(
        {"layer": ["ln_freiluftundnaherholung"]},
        geometry=[box(0, 0, 30, 30)], crs=CRS,
    )


def _srv_municipalities_frame():
    return gpd.GeoDataFrame(
        {"commune_id": ["03101000"]},
        geometry=[box(-1000, -1000, 1000, 1000)], crs=CRS,
    )


def _srv_stage_context():
    """All prerequisites for secondary_srv_location_types=True."""
    context = _stage_context(sec_enabled=True, external=False, visit=True)
    context._config["secondary_srv_location_types"] = True
    context._config["secondary_other_subtype_split"] = True
    context._config["secondary_landuse_grid_spacing_meters"] = 10.0
    context._config["secondary_other_min_volume_m3"] = 50.0
    context._config["secondary_other_cap_percentile"] = 0.99
    context._stages["braunschweig.data.building_potentials"] = _srv_building_potentials_frame()
    context._stages["braunschweig.data.landuse"] = _srv_landuse_frame()
    context._stages["braunschweig.data.bosserhof_location_category"] = _srv_location_category_mapping()
    context._stages["data.spatial.municipalities"] = _srv_municipalities_frame()
    return context


# --------------------------------------------------------------------------- #
# secondary_candidates stage assembly
# --------------------------------------------------------------------------- #
def test_stage_off_returns_legacy_frame_unchanged():
    context = _stage_context(sec_enabled=False)
    out = secondary_candidates.execute(context)
    assert list(out["location_id"]) == ["sec_0", "sec_1"]
    assert "pot_shop" not in out.columns  # untouched legacy schema


def test_stage_assembles_all_id_families():
    context = _stage_context(sec_enabled=True, external=True, visit=True)
    out = secondary_candidates.execute(context)
    ids = set(out["location_id"].astype(str))

    # gpkg building candidates (REPLACE) for shop/leisure
    assert "sec_b_11" in ids and "sec_b_22" in ids
    # legacy rows survive as 'other'-only candidates
    assert "sec_0" in ids and "sec_1" in ids
    # external Gemeinde centroid keeps its bare commune id
    assert "03459999" in ids
    # residential visit candidate (issue #127)
    assert "sec_res_77" in ids

    # visit rows are visit-only (never shop/leisure/other candidates)
    visit_row = out[out["location_id"] == "sec_res_77"].iloc[0]
    assert bool(visit_row["offers_visit"]) is True
    assert not visit_row[["offers_shop", "offers_leisure", "offers_other"]].any()


def test_stage_visit_requires_subtype_split():
    context = _stage_context(sec_enabled=True, visit=True)
    context._config["secondary_leisure_subtype_split"] = False
    with pytest.raises(ValueError, match="secondary_leisure_subtype_split"):
        secondary_candidates.execute(context)


def test_configure_declares_escort_purpose_even_when_short_circuited():
    """Regression test (issue #201): ``escort_purpose`` must end up declared in
    ``configure()``'s required config even when ``secondary_building_potentials``
    is OFF (skips the ``sec_enabled`` block, where the flag used to also be
    declared) AND ``leisure_visit_building_potential`` is ON (short-circuits the
    ``or`` condition's right operand, where the flag used to otherwise get
    declared). Before the fix, neither site ran in this combination, so
    ``escort_purpose`` was never added to ``StubContext._config`` -- and in the
    real synpp pipeline, ``execute()``'s one-arg ``context.config("escort_purpose")``
    read would then raise ``PipelineError: Config option escort_purpose is not
    requested`` instead of reaching the intended ``ValueError`` guard.
    """
    context = StubContext(
        config={
            "secondary_building_potentials": False,
            "leisure_visit_building_potential": True,
        },
        stages={
            "synthesis.locations.secondary": _legacy_frame(),
            # Only stage reachable in this combination: sec_enabled is False
            # (skips the whole `if sec_enabled:` block), so the OR-condition's
            # left operand alone triggers this dependency.
            "braunschweig.data.buildings": _residential_frame(),
        },
    )
    secondary_candidates.configure(context)
    assert "escort_purpose" in context._config
    assert context._config["escort_purpose"] is False


# --------------------------------------------------------------------------- #
# secondary_srv_location_types (issue #262): SrV-grounded location-category
# candidate assembly (append_location_category_columns + landuse grid seeding).
# --------------------------------------------------------------------------- #
def test_srv_location_types_off_no_category_columns():
    """Byte-identical OFF path: with every OTHER feature ON (building
    potentials, visit) but secondary_srv_location_types left at its default
    False, the frame must carry NONE of the new SrV category columns -- the
    full set of five building-category offers_/pot_ pairs plus the
    landuse-only leisure_outdoor pair."""
    context = _stage_context(sec_enabled=True, external=True, visit=True)
    out = secondary_candidates.execute(context)
    for category in [
        "leisure_culture", "leisure_gastronomy", "leisure_sports",
        "errand_authority_medical", "errand_service", "leisure_outdoor",
    ]:
        assert "offers_" + category not in out.columns
        assert "pot_" + category not in out.columns


def test_srv_location_types_requires_leisure_visit_building_potential():
    """Isolate ONLY leisure_visit_building_potential=False: sec_enabled,
    secondary_leisure_subtype_split, and secondary_other_subtype_split are all
    explicitly ON, so this fails only because of the term under test -- a
    regression that dropped just the leisure_visit term from the four-way
    guard would be caught here (visit=False would set BOTH subtype-split
    flags False too, proving nothing about leisure_visit specifically)."""
    context = _stage_context(sec_enabled=True, external=False, visit=True)
    context._config["leisure_visit_building_potential"] = False
    context._config["secondary_srv_location_types"] = True
    context._config["secondary_other_subtype_split"] = True
    assert context._config["secondary_leisure_subtype_split"] is True
    with pytest.raises(ValueError, match="leisure_visit_building_potential"):
        secondary_candidates.execute(context)


def test_srv_location_types_assembles_category_and_landuse_candidates():
    context = _srv_stage_context()
    out = secondary_candidates.execute(context)
    ids = set(out["location_id"].astype(str))

    assert any(location_id.startswith("sec_lu_") for location_id in ids)
    # Errand-class buildings (104/105) carry no retail/leisure potential, so
    # build_secondary_candidates' keep-filter excludes them from the input
    # candidates; append_location_category_columns must append them as NEW
    # sec_b_* rows once their derived errand potential is positive.
    assert "sec_b_104" in ids and "sec_b_105" in ids

    for category in [
        "leisure_culture", "leisure_gastronomy", "leisure_sports",
        "errand_authority_medical", "errand_service",
    ]:
        assert "offers_" + category in out.columns
        assert "pot_" + category in out.columns
    assert "offers_leisure_outdoor" in out.columns
    assert "pot_leisure_outdoor" in out.columns

    # Positive supply for all six categories: execute() calls
    # check_category_supply with BUILDING_CATEGORIES + ("leisure_outdoor",),
    # so simply reaching this point without a RuntimeError is itself the
    # structural assertion; these checks additionally pin the concrete rows.
    assert (out["pot_leisure_culture"] > 0.0).any()
    assert (out["pot_leisure_gastronomy"] > 0.0).any()
    assert (out["pot_leisure_sports"] > 0.0).any()
    assert (out["pot_leisure_outdoor"] > 0.0).any()
    assert (out["pot_errand_authority_medical"] > 0.0).any()
    assert (out["pot_errand_service"] > 0.0).any()


def test_srv_location_types_opens_every_category_on_external_centroids():
    """Long-distance reach parity (post-Task-8 review finding): the external
    Gemeinde centroids are category-AGNOSTIC distance escapes. With the flag ON
    they must offer every SrV location category at their aggregate ewz potential,
    or a leisure_culture / errand_* leg has no out-of-area candidate and its long
    desired distance clips to the region edge -- a regression versus the OFF
    path, where the same leg was a plain leisure/other leg."""
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        EXTERNAL_CATEGORY_ESCAPE_CATEGORIES,
    )
    context = _srv_stage_context()
    context._config["secondary_external_candidates"] = True
    out = secondary_candidates.execute(context)

    external = out[out["location_id"].astype(str) == "03459999"]
    assert len(external) == 1  # the _external_frame() centroid, ewz = 1000.0
    row = external.iloc[0]
    for category in EXTERNAL_CATEGORY_ESCAPE_CATEGORIES:
        assert bool(row["offers_" + category]), category
        assert row["pot_" + category] == 1000.0, category
    # In-area families are untouched by the escape step.
    building = out[out["location_id"].astype(str) == "sec_b_104"].iloc[0]
    assert not bool(building["offers_leisure_outdoor"])
    # leisure_visit keeps its residential-only pool.
    assert not bool(row["offers_visit"])


def test_configure_declares_srv_keys_even_when_flag_is_off():
    """synpp execute-config contract: configure() must declare
    secondary_srv_location_types and secondary_landuse_grid_spacing_meters
    UNCONDITIONALLY, so execute()'s one-arg config() reads never crash with
    synpp's PipelineError when the flag defaults to False."""
    context = StubContext(
        config={"secondary_building_potentials": False},
        stages={"synthesis.locations.secondary": _legacy_frame()},
    )
    secondary_candidates.configure(context)
    assert "secondary_srv_location_types" in context._config
    assert context._config["secondary_srv_location_types"] is False
    assert "secondary_landuse_grid_spacing_meters" in context._config
    assert context._config["secondary_landuse_grid_spacing_meters"] == 150.0


# --------------------------------------------------------------------------- #
# facilities: candidate frame mapping + coverage validation
# --------------------------------------------------------------------------- #
def test_facility_frame_folds_visit_into_leisure():
    context = _stage_context(sec_enabled=True, external=True, visit=True)
    candidates = secondary_candidates.execute(context)
    fac = secondary_facility_frame(candidates)

    assert list(fac.columns) == [
        "location_id", "geometry", "offers_leisure", "offers_shop", "offers_other",
        "offers_escort",
    ]
    visit_fac = fac[fac["location_id"] == "sec_res_77"].iloc[0]
    # A visit facility must offer "leisure": the population writes the BASE
    # purpose for subtype legs, so this is what MATSim sees.
    assert bool(visit_fac["offers_leisure"]) is True


def test_coverage_validation_raises_on_dangling_id():
    df_secondary = pd.DataFrame({"location_id": ["sec_0", "sec_b_11"]})
    df_realised = pd.DataFrame({"location_id": ["sec_0", "sec_b_11", "sec_b_999"]})
    with pytest.raises(RuntimeError, match="sec_b_999"):
        validate_secondary_coverage(df_realised, df_secondary)


def test_coverage_validation_passes_when_all_ids_written():
    context = _stage_context(sec_enabled=True, external=True, visit=True)
    candidates = secondary_candidates.execute(context)
    fac = secondary_facility_frame(candidates)
    # Realised ids drawn from every family the chainsolvers can produce.
    df_realised = pd.DataFrame({
        "location_id": ["sec_0", "sec_b_11", "sec_b_22", "03459999", "sec_res_77"]
    })
    validate_secondary_coverage(df_realised, fac)  # must not raise
