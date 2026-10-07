"""Exposure of a plans file to two parking zone releases (``scripts/curation/parking_zones_2026/count_zone_exposure.py``).

The one-off curation check of parking cost zones v2 (issue #436, spec Amendment D, ruling R-4a-7) counts, per zone a
release adds, changes or removes, the activities of the local 1 % scenario inside it. The plans parser and the counting
are pinned on a small synthetic plans file: interaction activities are no activities, a car arrival is an activity
reached by a trip with a ``car`` leg, only the selected plan counts, and the zone of every activity is the one the
production function ``braunschweig.parking.attach.attach_parking_zones`` assigns.
"""
from __future__ import annotations

import gzip
import importlib.util
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
X0, Y0 = 604_000.0, 5_789_000.0

#: person 1: home, car to work in zone A, walk to a shop in zone A (plus a pt interaction that is no activity), home;
#: person 2: home, pt to education in zone B, home; person 3: home only, with two plans of which the second is selected.
PLANS = f"""<?xml version="1.0" encoding="utf-8"?>
<population>
  <person id="p1">
    <plan selected="yes">
      <activity type="home" x="{X0 + 5000}" y="{Y0}" end_time="07:00:00"/>
      <leg mode="car"><route type="links" distance="12000"/></leg>
      <activity type="work" x="{X0 + 10}" y="{Y0 + 10}" end_time="12:00:00"/>
      <leg mode="walk"/>
      <activity type="pt interaction" x="{X0 + 20}" y="{Y0 + 20}"/>
      <leg mode="walk"/>
      <activity type="shop" x="{X0 + 30}" y="{Y0 + 30}" end_time="13:00:00"/>
      <leg mode="car"/>
      <activity type="home" x="{X0 + 5000}" y="{Y0}"/>
    </plan>
  </person>
  <person id="p2">
    <plan selected="yes">
      <activity type="home" x="{X0 + 5000}" y="{Y0}" end_time="07:00:00"/>
      <leg mode="walk"/>
      <activity type="pt interaction" x="{X0 + 5100}" y="{Y0}"/>
      <leg mode="pt"/>
      <activity type="pt interaction" x="{X0 + 210}" y="{Y0 + 5}"/>
      <leg mode="walk"/>
      <activity type="education" x="{X0 + 220}" y="{Y0 + 10}" end_time="15:00:00"/>
      <leg mode="car_passenger"/>
      <activity type="home" x="{X0 + 5000}" y="{Y0}"/>
    </plan>
  </person>
  <person id="p3">
    <plan selected="no">
      <activity type="home" x="{X0 + 5000}" y="{Y0}" end_time="07:00:00"/>
      <leg mode="car"/>
      <activity type="work" x="{X0 + 10}" y="{Y0 + 10}"/>
    </plan>
    <plan selected="yes">
      <activity type="home" x="{X0 + 5000}" y="{Y0}"/>
    </plan>
  </person>
</population>
"""


@pytest.fixture(scope="module")
def exposure():
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("count_zone_exposure_under_test",
                                                      CURATION_DIR / "count_zone_exposure.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


@pytest.fixture
def plans_path(tmp_path):
    path = tmp_path / "plans.xml.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write(PLANS)
    return path


def _zone_file(path: Path, zones: dict) -> Path:
    """A minimal release: zone id -> metric box (x0, y0, x1, y1) relative to the plans origin, written as WGS84 GeoJSON
    with the provenance fields ``load_zone_polygons`` requires."""
    frame = gpd.GeoDataFrame(
        {"zone_id": list(zones), "geometry_source": "centre_approximation", "source_url": "https://example.org/zones",
         "source_date": "2026-10-07", "digitised_on": "2026-10-07", "digitising_note": "synthetic zone of the exposure test"},
        crs="EPSG:25832", geometry=[box(X0 + x0, Y0 + y0, X0 + x1, Y0 + y1) for x0, y0, x1, y1 in zones.values()])
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_crs("EPSG:4326").to_file(path, driver="GeoJSON")
    return path


def test_the_plans_parser_keeps_main_activities_of_the_selected_plan_only(exposure, plans_path):
    activities, persons = exposure.read_main_activities(plans_path)
    assert persons == 3
    assert list(activities["person_id"]) == ["p1"] * 4 + ["p2"] * 3 + ["p3"]
    assert list(activities["purpose"]) == ["home", "work", "shop", "home", "home", "education", "home", "home"]
    # numbered per person in plan order; the pt interaction between work and shop is no activity
    assert list(activities["activity_index"]) == [0, 1, 2, 3, 0, 1, 2, 0]
    # a car arrival: the trip since the previous main activity holds a car leg; walk after the work is not one,
    # the car_passenger leg is not a car leg (the passenger does not park), the unselected plan of p3 does not count
    assert list(activities["car_arrival"]) == [False, True, False, True, False, False, False, False]
    assert activities.loc[1, ["x", "y"]].tolist() == [X0 + 10, Y0 + 10]


def test_the_parser_refuses_a_plans_file_without_a_selected_plan(exposure, tmp_path):
    path = tmp_path / "empty.xml.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write('<population><person id="p"><plan selected="no"><activity type="home" x="1" y="2"/></plan></person>'
                     "</population>")
    with pytest.raises(ValueError, match="no main activity"):
        exposure.read_main_activities(path)


def _releases(tmp_path):
    """Old release: a and c; new release: a shrunk, b new, c unchanged; the zones sit on the work, shop and education
    activities of the plans (work (10, 10), shop (30, 30), education (220, 10))."""
    old = _zone_file(tmp_path / "old" / "zones.geojson", {"a": (0, 0, 100, 100), "c": (200, 0, 300, 100)})
    new = _zone_file(tmp_path / "new" / "zones.geojson", {"a": (0, 0, 15, 100), "b": (15, 0, 100, 100),
                                                          "c": (200, 0, 300, 100)})
    return old, new


def test_zones_are_assigned_by_the_production_function(exposure, plans_path, tmp_path):
    old, _ = _releases(tmp_path)
    activities, _ = exposure.read_main_activities(plans_path)
    zone = exposure.zone_per_activity(activities, old)
    assert list(zone.index) == list(activities.index)
    # work and shop in a, education in c, every home and the activities of p3 outside every zone
    assert list(zone.fillna("none")) == ["none", "a", "a", "none", "none", "c", "none", "none"]


def test_exposure_counts_activities_per_zone_purpose_and_release(exposure, plans_path, tmp_path):
    old, new = _releases(tmp_path)
    activities, _ = exposure.read_main_activities(plans_path)
    zones = {"old": exposure.zone_per_activity(activities, old), "new": exposure.zone_per_activity(activities, new)}
    table = exposure.exposure_table(activities, zones)
    assert list(table.columns) == exposure.EXPOSURE_COLUMNS
    counts = table.set_index(["release", "zone_id", "purpose"])
    # old: work and shop in a, education in c; the work is reached by car, the shop by walking
    assert counts.loc[("old", "a", "work"), "activities"] == 1 and counts.loc[("old", "a", "work"), "car_arrivals"] == 1
    assert counts.loc[("old", "a", "shop"), "activities"] == 1 and counts.loc[("old", "a", "shop"), "car_arrivals"] == 0
    assert counts.loc[("old", "c", "education"), "activities"] == 1
    # the education arrival by pt and walk is no car arrival
    assert counts.loc[("old", "c", "education"), "car_arrivals"] == 0
    # new: a keeps the work activity only, the shop moves to the new zone b
    assert counts.loc[("new", "a", "work"), "activities"] == 1
    assert ("new", "a", "shop") not in counts.index
    assert counts.loc[("new", "b", "shop"), "activities"] == 1
    # the homes far away lie in no zone and appear in no row
    assert set(table["zone_id"]) == {"a", "b", "c"}
    assert table["activities"].sum() == 3 + 3
    assert list(table["release"].unique()) == ["old", "new"]


def test_transitions_list_the_activities_whose_zone_changes(exposure, plans_path, tmp_path):
    old, _ = _releases(tmp_path)
    activities, _ = exposure.read_main_activities(plans_path)
    new = _zone_file(tmp_path / "other" / "zones.geojson", {"a": (0, 0, 15, 100), "b": (15, 0, 100, 100)})
    transitions = exposure.transition_table(activities, exposure.zone_per_activity(activities, old),
                                            exposure.zone_per_activity(activities, new))
    rows = {(row.old_zone_id, row.new_zone_id): (row.activities, row.car_arrivals)
            for row in transitions.itertuples(index=False)}
    # the shop leaves a for b (no car arrival), the education activity leaves c for no zone; the work stays (no row)
    assert rows == {("a", "b"): (1, 0), ("c", "none"): (1, 0)}


def test_no_transition_when_no_zone_changes(exposure, plans_path, tmp_path):
    old, _ = _releases(tmp_path)
    activities, _ = exposure.read_main_activities(plans_path)
    zone = exposure.zone_per_activity(activities, old)
    assert exposure.transition_table(activities, zone, zone).empty


def test_zone_changes_name_added_removed_and_redrawn_zones_only(exposure, tmp_path):
    old, new = _releases(tmp_path)
    changes = exposure.zone_changes(old, new).set_index("zone_id")
    # a shrinks from 10,000 to 1,500 m2, b is new, c is unchanged and no row
    assert list(changes.index) == ["a", "b"]
    assert changes.loc["a", "change"] == "changed" and changes.loc["a", "symmetric_difference_m2"] == pytest.approx(8500, abs=1)
    assert changes.loc["b", "change"] == "added" and changes.loc["b", "new_area_m2"] == pytest.approx(8500, abs=1)
    reverse = exposure.zone_changes(new, old).set_index("zone_id")
    assert reverse.loc["b", "change"] == "removed" and reverse.loc["b", "old_area_m2"] == pytest.approx(8500, abs=1)
    assert exposure.zone_changes(old, old).empty


def test_the_command_line_writes_the_table_and_reports_the_rates(exposure, plans_path, tmp_path, capsys):
    old = _zone_file(tmp_path / "old" / "zones.geojson", {"a": (0, 0, 100, 100)})
    new = _zone_file(tmp_path / "new" / "zones.geojson", {"a": (0, 0, 100, 100), "b": (200, 0, 300, 100)})
    out = tmp_path / "exposure.csv"
    arguments = ["--plans", str(plans_path), "--old-zones", str(old), "--new-zones", str(new), "--out", str(out)]
    assert exposure.main(arguments) == 0
    written = pd.read_csv(out)
    assert list(written.columns) == ["release", "zone_id", "purpose", "activities", "car_arrivals"]
    assert written["activities"].sum() == 2 + 3
    report = capsys.readouterr().out
    assert "3 persons, 8 main activities" in report
    assert "old release: 2 of 8 activities (25.0 %) lie in a zone" in report
    assert "new release: 3 of 8 activities (37.5 %) lie in a zone" in report
    assert "1 added, removed or changed zones" in report and "b (added, 0 -> 10000 m2): 0 / 0 -> 1 / 0" in report
    assert "education 1" in report and "c -> none" not in report and "none -> b: 1 activities" in report
    # a second run does not replace the table silently
    with pytest.raises(SystemExit, match="exists"):
        exposure.main(arguments)
    assert exposure.main(arguments + ["--overwrite"]) == 0


def test_the_command_line_refuses_a_missing_input(exposure, tmp_path):
    with pytest.raises(SystemExit, match="plans file"):
        exposure.main(["--plans", str(tmp_path / "missing.xml.gz"), "--old-zones", str(tmp_path / "a.geojson"),
                       "--new-zones", str(tmp_path / "b.geojson"), "--out", str(tmp_path / "out.csv")])


def test_the_parser_refuses_an_activity_without_coordinates(exposure, tmp_path):
    path = tmp_path / "no_coordinates.xml"
    path.write_text('<population><person id="p"><plan selected="yes"><activity type="work" link="1"/></plan></person>'
                    "</population>", encoding="utf-8")
    with pytest.raises(ValueError, match="no x/y attribute"):
        exposure.read_main_activities(path)
