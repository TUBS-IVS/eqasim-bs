"""Regression tests for upstream eqasim-france fixes ported under issue #199.

Each test corresponds to one upstream PR whose fix is ported into our shared
pipeline code (see docs/UPSTREAM_FIX_SWEEP.md for the full classification):

- eqasim-france #427 (partial): read GTFS identifier columns as ``str`` at load
  time so numeric-looking ids and NaN cells cannot flip column dtypes.
- eqasim-france #512: tolerate ``agency.txt`` without an ``agency_id`` column
  (optional per GTFS spec for single-agency feeds).
- eqasim-france #521: stops without a parent station but ``location_type`` 0
  are promoted to stations so ``cut_feed`` does not silently drop them; the
  duplicate-id replacement in ``merge_two_feeds`` only requires the reference
  slot in the second feed.
- eqasim-france #309: ``cut_feed`` must not crash on feeds without a
  ``location_type`` column.
- eqasim-france #447 (GTFS part): ``merge_two_feeds`` must not coerce id
  columns with ``astype(str)`` — that turned genuine NaN into the string
  ``"nan"``, corrupting ``isna()``-based logic downstream (e.g. the
  parent-station handling in ``cut_feed``).
- eqasim-france #428: ``merge_two_feeds`` must tolerate collision slots whose
  identifier column is absent (e.g. ``attributions`` without
  ``attribution_id``, optional per GTFS spec).
- eqasim-france #414: ``statistical_matching`` sorts by the full identifier so
  the assignment is invariant to the input row order.
- eqasim-france #291 (partial): ``synthesis/output.py`` must write the trips
  parquet output with ``to_parquet`` (it used ``to_csv`` on the ``.parquet``
  path).
"""
from __future__ import annotations

import io
import re
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import shapely.geometry as geo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import data.gtfs.utils as gtfs  # noqa: E402


def _write_zip_feed(path, tables):
    """Write a dict of DataFrames as a minimal GTFS zip archive."""
    with zipfile.ZipFile(path, "w") as archive:
        for slot, df in tables.items():
            buffer = io.StringIO()
            df.to_csv(buffer, index=False, lineterminator="\n")
            archive.writestr("%s.txt" % slot, buffer.getvalue())


def _minimal_tables(agency=None, stops=None):
    """Minimal set of required GTFS tables, overridable per test."""
    if agency is None:
        agency = pd.DataFrame.from_records([
            dict(agency_id="10", agency_name="Operator")
        ])
    if stops is None:
        stops = pd.DataFrame.from_records([
            dict(stop_id="A", stop_name="Alpha", stop_lat=52.26, stop_lon=10.52,
                 location_type=0, parent_station=""),
        ])
    routes = pd.DataFrame.from_records([
        dict(route_id="1", agency_id=agency["agency_id"].iloc[0] if "agency_id" in agency else "10",
             route_short_name="1", route_long_name="Bus", route_type=3),
    ])
    trips = pd.DataFrame.from_records([
        dict(route_id="1", service_id="s1", trip_id="t1"),
    ])
    stop_times = pd.DataFrame.from_records([
        dict(trip_id="t1", stop_id=stops["stop_id"].iloc[0], stop_sequence=1,
             arrival_time="06:00:00", departure_time="06:00:00"),
    ])
    calendar = pd.DataFrame.from_records([
        dict(service_id="s1", monday=1, tuesday=1, wednesday=1, thursday=1,
             friday=1, saturday=0, sunday=0,
             start_date="20250101", end_date="20251231"),
    ])
    return dict(agency=agency, stops=stops, routes=routes, trips=trips,
                stop_times=stop_times, calendar=calendar)


def test_read_feed_reads_identifier_columns_as_string(tmp_path):
    """#427: numeric-looking stop ids and parent stations stay strings."""
    stops = pd.DataFrame.from_records([
        dict(stop_id=1001, stop_name="Alpha", stop_lat=52.26, stop_lon=10.52,
             location_type=0, parent_station=2001),
        dict(stop_id=2001, stop_name="Alpha Station", stop_lat=52.26, stop_lon=10.52,
             location_type=1, parent_station=""),
    ])
    path = tmp_path / "feed.zip"
    _write_zip_feed(path, _minimal_tables(stops=stops))

    feed = gtfs.read_feed(str(path))

    assert feed["stops"]["stop_id"].dtype == object
    assert set(feed["stops"]["stop_id"]) == {"1001", "2001"}
    assert feed["agency"]["agency_id"].dtype == object
    assert feed["routes"]["agency_id"].dtype == object


def test_read_feed_accepts_missing_agency_id_column(tmp_path):
    """#512: agency.txt without agency_id (optional for single-agency feeds)."""
    agency = pd.DataFrame.from_records([dict(agency_name="Solo Operator")])
    path = tmp_path / "feed.zip"
    tables = _minimal_tables(agency=agency)
    tables["routes"] = tables["routes"].drop(columns=["agency_id"])
    _write_zip_feed(path, tables)

    feed = gtfs.read_feed(str(path))

    assert (feed["agency"]["agency_id"] == "generic").all()
    assert (feed["routes"]["agency_id"] == "generic").all()


def test_read_feed_promotes_orphan_stops_to_stations(tmp_path):
    """#521: standalone stops (location_type 0, no parent) become stations."""
    stops = pd.DataFrame.from_records([
        dict(stop_id="A", stop_name="Standalone", stop_lat=52.26, stop_lon=10.52,
             location_type=0, parent_station=""),
        dict(stop_id="B", stop_name="Child", stop_lat=52.27, stop_lon=10.53,
             location_type=0, parent_station="S"),
        dict(stop_id="S", stop_name="Station", stop_lat=52.27, stop_lon=10.53,
             location_type=1, parent_station=""),
    ])
    path = tmp_path / "feed.zip"
    _write_zip_feed(path, _minimal_tables(stops=stops))

    feed = gtfs.read_feed(str(path))
    df_stops = feed["stops"].set_index("stop_id")

    assert df_stops.loc["A", "location_type"] == 1  # promoted
    assert df_stops.loc["B", "location_type"] == 0  # keeps its parent
    assert df_stops.loc["S", "location_type"] == 1


def _square_area(min_x, min_y, max_x, max_y):
    return gpd.GeoDataFrame(
        {"geometry": [geo.box(min_x, min_y, max_x, max_y)]}, crs="EPSG:4326")


def test_cut_feed_without_location_type_column():
    """#309: feeds without location_type must not crash cut_feed."""
    feed = {
        "stops": pd.DataFrame.from_records([
            dict(stop_id="A", stop_lat=52.26, stop_lon=10.52, parent_station=np.nan),
            dict(stop_id="B", stop_lat=53.26, stop_lon=11.52, parent_station=np.nan),
        ]),
        "stop_times": pd.DataFrame.from_records([
            dict(trip_id="t1", stop_id="A", stop_sequence=1),
            dict(trip_id="t1", stop_id="B", stop_sequence=2),
        ]),
        "trips": pd.DataFrame.from_records([
            dict(route_id="1", service_id="s1", trip_id="t1"),
        ]),
        "routes": pd.DataFrame.from_records([
            dict(route_id="1", agency_id="10", route_type=3),
        ]),
        "agency": pd.DataFrame.from_records([
            dict(agency_id="10", agency_name="Operator"),
        ]),
    }

    cut = gtfs.cut_feed(feed, _square_area(0.0, 0.0, 60.0, 60.0))

    # Fallback keeps all stops ("malformatted" path), no KeyError raised.
    assert set(cut["stops"]["stop_id"]) == {"A", "B"}


def test_merge_two_feeds_preserves_nan_parent_station():
    """#447 (GTFS): merging must not turn NaN parent stations into 'nan'.

    The duplicate-id replacement path coerced the reference columns (here
    ``stops.parent_station``) with ``astype(str)``, so genuine NaN became the
    string ``"nan"`` and ``isna()``-based logic downstream (the parent-station
    handling in ``cut_feed``) silently broke. The stop id ``"A"`` collides
    across both feeds with DIFFERENT row contents on purpose, so it survives
    ``drop_duplicates`` and forces the replacement path.
    """
    def make_feed(extra_stop_id, latitude):
        return {
            "stops": pd.DataFrame.from_records([
                dict(stop_id="A", stop_lat=latitude, stop_lon=10.52,
                     location_type=0, parent_station=np.nan),
                dict(stop_id=extra_stop_id, stop_lat=52.27, stop_lon=10.53,
                     location_type=0, parent_station=np.nan),
            ]),
            "agency": pd.DataFrame.from_records([
                dict(agency_id="10", agency_name="Operator"),
            ]),
        }

    merged = gtfs.merge_two_feeds(make_feed("B", 52.26), make_feed("C", 52.99))

    assert merged["stops"]["parent_station"].isna().all(), \
        "astype(str) coercion turned NaN parent_station into the string 'nan'"


def test_merge_two_feeds_tolerates_missing_identifier_column():
    """#428: collision slots without their identifier column must not crash."""
    def make_feed(trip_id):
        return {
            "trips": pd.DataFrame.from_records([
                dict(route_id="1", service_id="s1", trip_id=trip_id),
            ]),
            # attributions without the optional attribution_id column
            "attributions": pd.DataFrame.from_records([
                dict(organization_name="Operator %s" % trip_id),
            ]),
        }

    merged = gtfs.merge_two_feeds(make_feed("t1"), make_feed("t2"))

    assert len(merged["attributions"]) == 2


class _FakeProgress:
    def update(self, count=1):
        pass


def test_statistical_matching_invariant_to_source_row_order():
    """#414: matching results must not depend on the donor input row order."""
    from synthesis.population.matched import statistical_matching

    random = np.random.RandomState(0)
    df_source = pd.DataFrame({
        "donor_id": np.arange(100),
        "weight": random.uniform(0.5, 2.0, 100),
        "sex": random.choice(["m", "f"], 100),
        "age_class": random.choice(["young", "old"], 100),
    })
    df_target = pd.DataFrame({
        "person_id": np.arange(500),
        "sex": random.choice(["m", "f"], 500),
        "age_class": random.choice(["young", "old"], 500),
    })

    columns = ["sex", "age_class"]

    assigned_a, _ = statistical_matching(
        _FakeProgress(), df_source, "donor_id", "weight",
        df_target, "person_id", columns, random_seed=42)

    df_shuffled = df_source.sample(frac=1.0, random_state=7).reset_index(drop=True)
    assigned_b, _ = statistical_matching(
        _FakeProgress(), df_shuffled, "donor_id", "weight",
        df_target, "person_id", columns, random_seed=42)

    assert np.array_equal(assigned_a, assigned_b), \
        "statistical matching depends on donor row order (unstable sort)"


def test_output_stage_writes_parquet_with_to_parquet():
    """#291 (partial): no ``to_csv`` call may target a ``.parquet`` path."""
    source = (REPO / "synthesis" / "output.py").read_text(encoding="utf-8")

    offenders = [
        line.strip() for line in source.splitlines()
        if "to_csv" in line and re.search(r"\.parquet", line)
    ]

    assert offenders == [], \
        "to_csv used for parquet output: %s" % offenders
