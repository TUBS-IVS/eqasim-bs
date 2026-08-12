"""Unit tests for the pure transformation logic in the work / secondary
location-candidate stages.

Scope
-----
``braunschweig.locations.work`` and ``braunschweig.locations.secondary`` are
thin synpp stages: their ``configure`` only declares stage dependencies and
their ``execute`` performs a deterministic, in-memory transformation of the
frames returned by ``context.stage(...)``. Neither module factors that
transformation into separately importable helpers, so the pure logic is
exercised here by driving ``execute`` with a *stub context* that returns small
synthetic GeoDataFrames. No real OSM / ALKIS / census data, no geo files and no
synpp runtime are involved -- only the byte-for-byte deterministic mapping the
stage applies to its inputs.

The invariants asserted are read directly from the stage source:

work._execute_base
    - ``employees = area * floors`` for every kept (location_type == "work") row;
    - rows with location_type != "work" are dropped;
    - every Gemeinde present in ``data.spatial.municipalities`` but absent from
      the workplace points gets exactly one synthetic centroid row with
      ``fake == True``, ``employees == 1`` and ``iris_id == commune_id + "0000"``;
    - real rows keep ``fake == False``;
    - ``location_id`` values are unique, contiguous "work_<i>".

work.execute
    - external synthetic workplaces are appended with ``fake == False`` and
      their integer ``employees`` preserved;
    - the concatenated frame re-issues a contiguous, unique "work_<i>" id space;
    - external geometries are reprojected to the ZGB CRS.

secondary.execute
    - ``offers_other`` is True for every row (so every candidate survives the
      filter); ``offers_leisure`` / ``offers_shop`` reflect the location_type;
    - ``location_id`` values are unique, contiguous "sec_<i>";
    - the output schema is exactly the documented column set, in order.

The env has a broken LAPACK, so only plain numpy / pandas / geopandas / shapely
are used (no GLM / SVD / lstsq).
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import geometry as geo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.locations import secondary as secondary_stage  # noqa: E402
from braunschweig.locations import work as work_stage  # noqa: E402


# ---------------------------------------------------------------------------
# Stub synpp context: returns pre-built synthetic frames by stage name.
# ---------------------------------------------------------------------------

class _StubContext:
    """Minimal synpp context returning canned frames keyed by stage name."""

    def __init__(self, frames: dict):
        self._frames = frames
        self.requested: list[str] = []

    def stage(self, name: str, config=None):
        self.requested.append(name)
        if name not in self._frames:
            raise KeyError(f"unexpected stage requested: {name!r}")
        return self._frames[name]

    def config(self, option, default=None):
        return default


_CRS = "EPSG:25832"


# ---------------------------------------------------------------------------
# work._execute_base : headcount, drop non-work, synthetic centroid fill.
# ---------------------------------------------------------------------------

def _locations_frame() -> gpd.GeoDataFrame:
    """OSM/ALKIS-style location points.

    Two genuine workplace points (commune 03101) plus one non-work (shop) point
    that must be dropped by the location_type filter.
    """
    return gpd.GeoDataFrame(
        {
            "location_type": ["work", "work", "shop"],
            "area": [100.0, 50.0, 999.0],
            "floors": [3.0, 2.0, 1.0],
            "commune_id": ["031010000000", "031010000000", "031530000000"],
            "iris_id": pd.Categorical(["a", "a", "b"]),
        },
        geometry=[geo.Point(0.0, 0.0), geo.Point(1.0, 1.0), geo.Point(2.0, 2.0)],
        crs=_CRS,
    )


def _municipalities_frame() -> gpd.GeoDataFrame:
    """Two Gemeinden: 03101 already has workplace points, 03153 does not and so
    must receive a synthetic centroid workplace."""
    return gpd.GeoDataFrame(
        {"commune_id": ["031010000000", "031530000000"]},
        geometry=[
            geo.Polygon([(0, 0), (0, 10), (10, 10), (10, 0)]),
            geo.Polygon([(20, 20), (20, 30), (30, 30), (30, 20)]),
        ],
        crs=_CRS,
    )


def _base_context() -> _StubContext:
    return _StubContext(
        {
            "braunschweig.data.locations": _locations_frame(),
            "data.spatial.municipalities": _municipalities_frame(),
        }
    )


def test_execute_base_computes_employees_as_area_times_floors():
    df = work_stage._execute_base(_base_context())
    real = df[~df["fake"]].sort_values("employees")
    # area*floors = {100*3=300, 50*2=100}; non-work shop row is dropped.
    assert sorted(real["employees"].tolist()) == [100.0, 300.0]


def test_execute_base_drops_non_work_location_types():
    df = work_stage._execute_base(_base_context())
    # The 999.0-area shop row would give employees 999 if it had leaked through.
    assert 999.0 not in df["employees"].tolist()
    # Only one real-row Gemeinde (03101) carries genuine workplaces.
    real = df[~df["fake"]]
    assert set(real["commune_id"]) == {"031010000000"}


def test_execute_base_fills_missing_gemeinde_with_one_fake_centroid():
    df = work_stage._execute_base(_base_context())
    fake = df[df["fake"]]
    # Exactly the Gemeinde absent from the workplace points (03153) is filled.
    assert fake["commune_id"].tolist() == ["031530000000"]
    assert fake["employees"].tolist() == [1]
    # iris_id of a synthetic centroid is commune_id + "0000".
    assert fake["iris_id"].iloc[0] == "0315300000000000"
    # The centroid of the 03153 square [20,30]^2 is (25, 25).
    centroid = fake["geometry"].iloc[0]
    assert np.isclose(centroid.x, 25.0) and np.isclose(centroid.y, 25.0)


def test_execute_base_real_rows_are_not_fake():
    df = work_stage._execute_base(_base_context())
    real = df[df["commune_id"] == "031010000000"]
    assert (~real["fake"]).all()


def test_execute_base_location_ids_are_unique_and_contiguous():
    df = work_stage._execute_base(_base_context())
    ids = df["location_id"].tolist()
    assert len(set(ids)) == len(ids)  # unique
    expected = [f"work_{i}" for i in range(len(df))]
    assert ids == expected


def test_execute_base_single_candidate_per_missing_gemeinde():
    """Edge case: a municipalities table that has ONLY missing Gemeinden yields
    exactly one synthetic workplace per Gemeinde and no real rows at all."""
    ctx = _StubContext(
        {
            "braunschweig.data.locations": _locations_frame().iloc[0:0],
            "data.spatial.municipalities": _municipalities_frame(),
        }
    )
    df = work_stage._execute_base(ctx)
    assert df["fake"].all()
    assert len(df) == 2  # one centroid per Gemeinde
    assert df["employees"].tolist() == [1, 1]


# ---------------------------------------------------------------------------
# work.execute : external-workplace append + contiguous id re-issue + CRS.
# ---------------------------------------------------------------------------

def _external_frame(crs=_CRS) -> gpd.GeoDataFrame:
    """One synthetic external (outbound-Kreis) workplace."""
    return gpd.GeoDataFrame(
        {
            "employees": [250],
            "commune_id": ["099990000000"],
            "iris_id": ["099990000000ext"],
        },
        geometry=[geo.Point(500.0, 500.0)],
        crs=crs,
    )


def _full_context(external) -> _StubContext:
    return _StubContext(
        {
            "braunschweig.data.locations": _locations_frame(),
            "data.spatial.municipalities": _municipalities_frame(),
            "braunschweig.data.external_workplaces": external,
        }
    )


def test_execute_appends_external_workplace_as_non_fake():
    out = work_stage.execute(_full_context(_external_frame()))
    ext = out[out["commune_id"] == "099990000000"]
    assert len(ext) == 1
    assert bool(ext["fake"].iloc[0]) is False
    assert int(ext["employees"].iloc[0]) == 250


def test_execute_reissues_contiguous_unique_location_ids():
    out = work_stage.execute(_full_context(_external_frame()))
    ids = out["location_id"].tolist()
    assert len(set(ids)) == len(ids)
    assert ids == [f"work_{i}" for i in range(len(out))]


def test_execute_total_row_count_is_base_plus_external():
    base = work_stage._execute_base(_base_context())
    out = work_stage.execute(_full_context(_external_frame()))
    assert len(out) == len(base) + 1


def test_execute_reprojects_external_geometry_to_zgb_crs():
    """Edge case: external frame in a different CRS must be reprojected so the
    combined frame is single-CRS (the stage calls to_crs when CRS differ)."""
    ext_wgs84 = _external_frame(crs="EPSG:4326")
    # Place the point in a plausible WGS84 location (near Braunschweig).
    ext_wgs84["geometry"] = [geo.Point(10.5, 52.3)]
    out = work_stage.execute(_full_context(ext_wgs84))
    assert out.crs.to_epsg() == 25832
    ext = out[out["commune_id"] == "099990000000"].geometry.iloc[0]
    # Reprojected easting/northing land in the metric ZGB range, not lon/lat.
    assert ext.x > 100_000.0 and ext.y > 1_000_000.0


def test_execute_empty_external_pool_keeps_base_only():
    """Edge case: an empty external pool leaves the base workplaces untouched
    and still mints a contiguous id space."""
    empty = _external_frame().iloc[0:0]
    base = work_stage._execute_base(_base_context())
    out = work_stage.execute(_full_context(empty))
    assert len(out) == len(base)
    assert out["location_id"].tolist() == [f"work_{i}" for i in range(len(out))]


# ---------------------------------------------------------------------------
# secondary.execute : activity-offer flags + filter + id minting + schema.
# ---------------------------------------------------------------------------

def _secondary_locations() -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame(
        {
            "location_type": ["leisure", "shop", "other", "work"],
            "commune_id": ["1", "1", "2", "2"],
            "iris_id": pd.Categorical(["a", "a", "b", "b"]),
        },
        geometry=[geo.Point(i, i) for i in range(4)],
        crs=_CRS,
    )


def _secondary_context(frame=None) -> _StubContext:
    return _StubContext(
        {"braunschweig.data.locations": _secondary_locations() if frame is None else frame}
    )


def test_secondary_offers_other_true_for_all_rows():
    out = secondary_stage.execute(_secondary_context())
    # offers_other is unconditionally True -> every candidate survives the OR
    # filter, regardless of location_type.
    assert out["offers_other"].all()
    assert len(out) == 4


def test_secondary_offer_flags_reflect_location_type():
    out = secondary_stage.execute(_secondary_context())
    # Re-attach the source type by row order (execute preserves it).
    types = _secondary_locations()["location_type"].tolist()
    leisure = out["offers_leisure"].tolist()
    shop = out["offers_shop"].tolist()
    assert leisure == [t == "leisure" for t in types]
    assert shop == [t == "shop" for t in types]


def test_secondary_location_ids_are_unique_and_contiguous():
    out = secondary_stage.execute(_secondary_context())
    ids = out["location_id"].tolist()
    assert len(set(ids)) == len(ids)
    assert ids == [f"sec_{i}" for i in range(len(out))]


def test_secondary_output_schema_is_exact_and_ordered():
    out = secondary_stage.execute(_secondary_context())
    assert list(out.columns) == [
        "location_id", "commune_id", "iris_id", "geometry",
        "offers_leisure", "offers_shop", "offers_other", "offers_escort",
    ]


def test_secondary_single_candidate_pool():
    """Edge case: a single-row pool yields exactly one 'sec_0' candidate."""
    single = _secondary_locations().iloc[[0]]
    out = secondary_stage.execute(_secondary_context(single))
    assert len(out) == 1
    assert out["location_id"].iloc[0] == "sec_0"


def test_secondary_empty_candidate_pool():
    """Edge case: an empty pool yields an empty (but correctly-shaped) frame,
    no divide-by-zero or index error in the id minting."""
    empty = _secondary_locations().iloc[0:0]
    out = secondary_stage.execute(_secondary_context(empty))
    assert len(out) == 0
    assert list(out.columns) == [
        "location_id", "commune_id", "iris_id", "geometry",
        "offers_leisure", "offers_shop", "offers_other", "offers_escort",
    ]


# ---------------------------------------------------------------------------
# configure() : declares exactly the stage dependencies it consumes.
# ---------------------------------------------------------------------------

def test_work_configure_declares_expected_stages_on():
    """ON path (default): declares building_potentials, not braunschweig.data.locations."""
    requested: list[str] = []

    class _Recorder:
        def stage(self, name, config=None):
            requested.append(name)

        def config(self, option, default=None):
            return default  # work_building_potentials default=True -> ON path

    work_stage.configure(_Recorder())
    assert set(requested) == {
        "data.spatial.municipalities",
        "braunschweig.data.external_workplaces",
        "braunschweig.data.building_potentials",
    }
    assert "braunschweig.data.locations" not in requested


def test_work_configure_declares_expected_stages_off():
    """OFF path: declares braunschweig.data.locations, not building_potentials."""
    requested: list[str] = []

    class _Recorder:
        def stage(self, name, config=None):
            requested.append(name)

        def config(self, option, default=None):
            # Return False when asked for work_building_potentials -> OFF path
            if option == "work_building_potentials":
                return False
            return default

    work_stage.configure(_Recorder())
    assert set(requested) == {
        "data.spatial.municipalities",
        "braunschweig.data.external_workplaces",
        "braunschweig.data.locations",
    }
    assert "braunschweig.data.building_potentials" not in requested


def test_secondary_configure_declares_locations_stage():
    requested: list[str] = []

    class _Recorder:
        def stage(self, name, config=None):
            requested.append(name)

        def config(self, option, default=None):
            return default

    secondary_stage.configure(_Recorder())
    assert requested == ["braunschweig.data.locations"]
