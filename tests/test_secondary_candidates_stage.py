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
from shapely.geometry import Point, Polygon

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

    def config(self, key, default=None):
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
