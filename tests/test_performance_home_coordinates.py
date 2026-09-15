"""Performance-equivalence tests for typed-home coordinate transforms."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.synthesis.locations import home_cell


CELL_A = "CRS3035RES100mN2689100E4337000"
CELL_B = "CRS3035RES100mN2689100E4337100"
CELL_C = "CRS3035RES100mN2689100E4337200"
CELL_EMPTY = "CRS3035RES100mN2689500E4337500"


def _signals(cell_ids: list[str]) -> pd.DataFrame:
    return pd.DataFrame([{
        "ZENSUS100m": cell_id,
        "FreiEFH_Geb_Gebaeudetyp_Groesse_100m_Gitter": 1.0,
        "MFH_3bis6Wohnungen_Geb_Gebaeudetyp_Groesse_100m_Gitter": 0.0,
        "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 8.0,
        "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": 0.0,
        "BewohntWhg_Leerstand_100m_Gitter": 8.0,
        "90bis99_Flaeche_der_Wohnung_10m2_Intervalle_100m_Gitter": 8.0,
    } for cell_id in cell_ids])


def _footprint_fixture() -> tuple[pd.DataFrame, gpd.GeoDataFrame, pd.DataFrame]:
    """Return native, clamped, repeated-pair, and random-fallback inputs."""
    native = box(4337020, 2689120, 4337080, 2689180)
    boundary = box(4337060, 2689120, 4337140, 2689180)
    footprints_3035 = gpd.GeoSeries([native, boundary], crs="EPSG:3035")
    footprints = footprints_3035.to_crs("EPSG:25832")
    centroids = footprints.centroid
    buildings = gpd.GeoDataFrame(
        {
            "building_id": [10, 20],
            "area_m2": [120.0, 120.0],
            "height_m": [float("nan"), float("nan")],
            "footprint": list(footprints),
        },
        geometry=centroids,
        crs="EPSG:25832",
    )
    households = pd.DataFrame({
        "household_id": ["a0", "a1", "b0", "b1", "empty"],
        "commune_id": ["031010000000"] * 5,
        "ZENSUS100m": [CELL_A, CELL_A, CELL_B, CELL_B, CELL_EMPTY],
        "building_type_3class": ["ein_zweifamilienhaus"] * 5,
        "household_size": [2] * 5,
    })
    return households, buildings, _signals([CELL_A, CELL_B, CELL_EMPTY])


def _one_building_per_cell_fixture(
    household_cells: list[str],
) -> tuple[pd.DataFrame, gpd.GeoDataFrame, pd.DataFrame]:
    """Return footprint cells with exactly one selected pair per household cell."""
    unique_cells = list(dict.fromkeys(household_cells))
    footprints_3035 = []
    for cell_id in unique_cells:
        _, north_m, east_m = home_cell._parse_inspire_id(cell_id)
        footprints_3035.append(box(east_m + 20, north_m + 20, east_m + 80, north_m + 80))
    footprints = gpd.GeoSeries(footprints_3035, crs="EPSG:3035").to_crs("EPSG:25832")
    buildings = gpd.GeoDataFrame(
        {
            "building_id": list(range(len(unique_cells))),
            "area_m2": [120.0] * len(unique_cells),
            "height_m": [float("nan")] * len(unique_cells),
            "footprint": list(footprints),
        },
        geometry=footprints.centroid,
        crs="EPSG:25832",
    )
    households = pd.DataFrame({
        "household_id": [f"hh_{index}" for index in range(len(household_cells))],
        "commune_id": ["031010000000"] * len(household_cells),
        "ZENSUS100m": household_cells,
        "building_type_3class": ["ein_zweifamilienhaus"] * len(household_cells),
        "household_size": [2] * len(household_cells),
    })
    return households, buildings, _signals(unique_cells)


def _wkb(frame: gpd.GeoDataFrame) -> list[bytes | None]:
    return list(frame.geometry.to_wkb())


def _rng_state(rng) -> tuple[object, bytes, int, int, float]:
    """Return every RandomState component that changes with draws."""
    algorithm, state, position, has_gauss, cached_gaussian = rng.get_state()
    return algorithm, state.tobytes(), position, has_gauss, cached_gaussian


def test_home_coordinate_batching_is_registered_default_on():
    """The stage must declare the rollback switch during configure()."""
    configured = {}

    class Context:
        def stage(self, _name):
            pass

        def config(self, key, default=None):
            configured[key] = default

    home_cell.configure(Context())
    assert configured["braunschweig.performance.home_coordinates"] is True


def test_typed_batch_coordinates_preserves_results_and_eliminates_repeated_transforms(monkeypatch):
    """Batching must retain scalar output while transforming each pair only once."""
    households, buildings, cells = _footprint_fixture()
    original_to_crs = gpd.GeoSeries.to_crs
    original_random_point = home_cell.hm.random_point_in_cell
    calls = {"count": 0}
    random_states = []

    def count_real_transforms(series, *args, **kwargs):
        calls["count"] += 1
        return original_to_crs(series, *args, **kwargs)

    def record_random_state(cell_id, rng):
        point = original_random_point(cell_id, rng)
        random_states.append(_rng_state(rng))
        return point

    monkeypatch.setattr(gpd.GeoSeries, "to_crs", count_real_transforms)
    monkeypatch.setattr(home_cell.hm, "random_point_in_cell", record_random_state)
    scalar, scalar_report = home_cell.assign_homes_typed(
        households, buildings, cells, random_seed=1234, batch_coordinates=False,
    )
    scalar_calls = calls["count"]
    scalar_random_states = list(random_states)
    calls["count"] = 0
    random_states.clear()
    batched, batched_report = home_cell.assign_homes_typed(
        households, buildings, cells, random_seed=1234, batch_coordinates=True,
    )

    assert list(batched.columns) == list(scalar.columns)
    assert batched.dtypes.equals(scalar.dtypes)
    pd.testing.assert_frame_equal(
        batched.drop(columns="geometry"), scalar.drop(columns="geometry"),
    )
    assert _wkb(batched) == _wkb(scalar)
    assert batched_report == scalar_report
    assert random_states == scalar_random_states
    assert calls["count"] < scalar_calls


def test_typed_batch_coordinates_transform_counts_match_distinct_selected_pairs(monkeypatch):
    """Repeated pairs batch once; all-unique pairs retain the scalar call count."""
    original_to_crs = gpd.GeoSeries.to_crs
    calls = {"count": 0}

    def count_real_transforms(series, *args, **kwargs):
        calls["count"] += 1
        return original_to_crs(series, *args, **kwargs)

    def count_assignments(households, buildings, cells, batch_coordinates):
        calls["count"] = 0
        home_cell.assign_homes_typed(
            households, buildings, cells, random_seed=1234,
            batch_coordinates=batch_coordinates,
        )
        return calls["count"]

    monkeypatch.setattr(gpd.GeoSeries, "to_crs", count_real_transforms)
    repeated = _one_building_per_cell_fixture([CELL_A, CELL_A, CELL_A])
    assert count_assignments(*repeated, batch_coordinates=False) == 7
    assert count_assignments(*repeated, batch_coordinates=True) == 3

    unique = _one_building_per_cell_fixture([CELL_A, CELL_B, CELL_C])
    assert count_assignments(*unique, batch_coordinates=False) == 7
    assert count_assignments(*unique, batch_coordinates=True) == 7


def test_typed_batch_coordinates_keeps_centroid_mode_and_empty_fallback_byte_identical():
    """The switch leaves no-footprint and no-building paths exactly unchanged."""
    households, buildings, cells = _footprint_fixture()
    no_footprints = buildings.drop(columns="footprint")
    scalar, scalar_report = home_cell.assign_homes_typed(
        households, no_footprints, cells, random_seed=1234, batch_coordinates=False,
    )
    batched, batched_report = home_cell.assign_homes_typed(
        households, no_footprints, cells, random_seed=1234, batch_coordinates=True,
    )
    assert _wkb(batched) == _wkb(scalar)
    assert batched_report == scalar_report

    empty_buildings = gpd.GeoDataFrame(
        {"building_id": pd.Series(dtype="int64"), "area_m2": pd.Series(dtype="float64")},
        geometry=gpd.GeoSeries([], crs="EPSG:25832"),
        crs="EPSG:25832",
    )
    empty_households = households.iloc[[4]].copy()
    scalar_empty, scalar_empty_report = home_cell.assign_homes_typed(
        empty_households, empty_buildings, cells, random_seed=1234, batch_coordinates=False,
    )
    batched_empty, batched_empty_report = home_cell.assign_homes_typed(
        empty_households, empty_buildings, cells, random_seed=1234, batch_coordinates=True,
    )
    assert _wkb(batched_empty) == _wkb(scalar_empty)
    assert batched_empty_report == scalar_empty_report


def test_typed_batch_coordinates_accepts_empty_households():
    """An empty typed frame must retain its established empty output and report."""
    households, buildings, cells = _footprint_fixture()
    empty_households = households.iloc[0:0].copy()
    scalar, scalar_report = home_cell.assign_homes_typed(
        empty_households, buildings, cells, random_seed=1234, batch_coordinates=False,
    )
    batched, batched_report = home_cell.assign_homes_typed(
        empty_households, buildings, cells, random_seed=1234, batch_coordinates=True,
    )
    assert scalar.empty and batched.empty
    assert _wkb(batched) == _wkb(scalar)
    assert batched_report == scalar_report


def test_typed_batch_coordinates_fails_before_later_random_fallback(monkeypatch):
    """A queued coordinate error must retain scalar exception and RNG ordering."""
    households, buildings, cells = _footprint_fixture()
    original_parse = home_cell._parse_inspire_id
    original_random_point = home_cell.hm.random_point_in_cell
    random_states = []

    def fail_early_selected_cell(cell_id):
        if cell_id == CELL_A:
            raise ValueError("early selected coordinate failure")
        return original_parse(cell_id)

    def record_random_state(cell_id, rng):
        point = original_random_point(cell_id, rng)
        random_states.append(_rng_state(rng))
        return point

    monkeypatch.setattr(home_cell, "_parse_inspire_id", fail_early_selected_cell)
    monkeypatch.setattr(home_cell.hm, "random_point_in_cell", record_random_state)
    with pytest.raises(ValueError, match="early selected coordinate failure") as scalar_error:
        home_cell.assign_homes_typed(
            households, buildings, cells, random_seed=1234, batch_coordinates=False,
        )
    scalar_states = list(random_states)
    random_states.clear()
    with pytest.raises(ValueError, match="early selected coordinate failure") as batched_error:
        home_cell.assign_homes_typed(
            households, buildings, cells, random_seed=1234, batch_coordinates=True,
        )

    assert type(batched_error.value) is type(scalar_error.value)
    assert str(batched_error.value) == str(scalar_error.value)
    assert scalar_states == random_states == []


def test_typed_batch_coordinates_preserves_missing_building_geometry_error():
    """A missing active geometry must fail with the same public API error in both modes."""
    households, _buildings, cells = _footprint_fixture()
    missing_geometry_buildings = gpd.GeoDataFrame(
        {
            "building_id": [10],
            "area_m2": [120.0],
            "footprint": [box(4337020, 2689120, 4337080, 2689180)],
        },
        geometry=[None],
        crs="EPSG:25832",
    )
    selected_household = households.iloc[[0]].copy()
    with pytest.raises(AttributeError) as scalar_error:
        home_cell.assign_homes_typed(
            selected_household, missing_geometry_buildings, cells,
            random_seed=1234, batch_coordinates=False,
        )
    with pytest.raises(AttributeError) as batched_error:
        home_cell.assign_homes_typed(
            selected_household, missing_geometry_buildings, cells,
            random_seed=1234, batch_coordinates=True,
        )
    assert str(batched_error.value) == str(scalar_error.value)


def test_typed_batch_coordinates_preserves_duplicate_ids_and_nondefault_buildings_crs():
    """The lookup must preserve scalar duplicate-ID and CRS behaviour."""
    households, buildings, cells = _footprint_fixture()
    duplicate = buildings.iloc[[0]].copy()
    duplicate["building_id"] = 10
    duplicate.geometry = duplicate.geometry.translate(xoff=5.0)
    duplicate["footprint"] = list(
        gpd.GeoSeries(duplicate["footprint"], crs=buildings.crs).translate(xoff=5.0)
    )
    duplicated_buildings = pd.concat([buildings, duplicate], ignore_index=True)
    duplicated_buildings = gpd.GeoDataFrame(
        duplicated_buildings, geometry="geometry", crs=buildings.crs,
    )
    scalar, scalar_report = home_cell.assign_homes_typed(
        households, duplicated_buildings, cells, random_seed=1234, batch_coordinates=False,
    )
    batched, batched_report = home_cell.assign_homes_typed(
        households, duplicated_buildings, cells, random_seed=1234, batch_coordinates=True,
    )
    assert _wkb(batched) == _wkb(scalar)
    assert batched_report == scalar_report

    projected_footprints = gpd.GeoSeries(
        buildings["footprint"], crs=buildings.crs
    ).to_crs("EPSG:3035")
    projected_buildings = buildings.to_crs("EPSG:3035").copy()
    projected_buildings["footprint"] = list(projected_footprints)
    scalar_projected, scalar_projected_report = home_cell.assign_homes_typed(
        households, projected_buildings, cells, random_seed=1234, batch_coordinates=False,
    )
    batched_projected, batched_projected_report = home_cell.assign_homes_typed(
        households, projected_buildings, cells, random_seed=1234, batch_coordinates=True,
    )
    assert _wkb(batched_projected) == _wkb(scalar_projected)
    assert batched_projected_report == scalar_projected_report


def test_execute_forwards_home_coordinate_batching_switch(monkeypatch):
    """The configured OFF switch reaches the typed assignment function."""
    households, buildings, cells = _footprint_fixture()
    sampled = households.copy()
    observed = {}

    class Context:
        def stage(self, name):
            return {
                "braunschweig.data.buildings": buildings,
                "synthesis.population.sampled": sampled,
            }[name]

        def config(self, key):
            return {
                "random_seed": 1234,
                "braunschweig.home_matching": "typed",
                "braunschweig.performance.home_coordinates": False,
                home_cell.KEY_CELLS_100M: "unused",
            }[key]

    def capture_assignment(*args, **kwargs):
        observed["batch_coordinates"] = kwargs["batch_coordinates"]
        return gpd.GeoDataFrame(
            {
                "household_id": sampled["household_id"],
                "commune_id": sampled["commune_id"],
                "home_location_id": pd.NA,
            },
            geometry=[Point(0, 0)] * len(sampled), crs="EPSG:25832",
        ), None

    monkeypatch.setattr(home_cell, "load_prepared_cells", lambda _path: cells)
    monkeypatch.setattr(home_cell, "assign_homes_typed", capture_assignment)
    home_cell.execute(Context())
    assert observed["batch_coordinates"] is False
