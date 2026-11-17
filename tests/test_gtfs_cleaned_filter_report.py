"""Stage-level test of ``data.gtfs.cleaned``: on-demand filters run in order and are reported.

Catches a wiring defect the unit tests in ``test_gtfs_flex_filter.py`` cannot: the stage
not calling a filter, calling them in the wrong order, or not persisting the counts in
``gtfs_filter_report.json`` (the run would then only have them in the log).
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import shapely.geometry as geo

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import data.gtfs.cleaned as stage  # noqa: E402

FILES = {
    "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n10,VRB,http://x,Europe/Berlin\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon,location_type,parent_station\n"
                 "A,A,52.26,10.52,0,\nB,B,52.27,10.53,0,\nC,C,52.28,10.54,0,\n",
    "routes.txt": "route_id,agency_id,route_short_name,route_type\n1,10,1,3\n2,10,R2,3\n9,10,Flexo,3\n",
    "trips.txt": "route_id,service_id,trip_id\n1,s1,t_sched\n2,s1,t_flex\n9,s1,t_flexo\n",
    "stop_times.txt": "trip_id,arrival_time,departure_time,stop_id,stop_sequence,"
                      "start_pickup_drop_off_window,end_pickup_drop_off_window\n"
                      "t_sched,08:00:00,08:00:00,A,1,,\nt_sched,08:10:00,08:10:00,B,2,,\n"
                      "t_flex,,,A,1,06:00:00,20:00:00\nt_flex,,,C,2,06:00:00,20:00:00\n"
                      "t_flexo,09:00:00,09:00:00,B,1,,\nt_flexo,09:10:00,09:10:00,C,2,,\n",
    "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
                    "s1,1,1,1,1,1,0,0,20260101,20261231\n",
}


class _Context:
    def __init__(self, data_path, out_path):
        self._config = {
            "data_path": str(data_path), "gtfs_path": "gtfs", "cordon_enabled": False,
            "cordon_network_source_buffer_m": 0.0,
            "gtfs_excluded_route_short_name_patterns": ["^Flexo$"],
            "gtfs_excluded_agency_ids": [], "gtfs_excluded_agency_name_patterns": [],
        }
        self._out = out_path

    def config(self, key):
        return self._config[key]

    def stage(self, name):
        assert name == "data.spatial.municipalities"
        return gpd.GeoDataFrame(geometry = [geo.box(10.0, 52.0, 11.0, 53.0)], crs = "EPSG:4326")

    def path(self):
        return str(self._out)


def test_stage_drops_flex_then_flexo_and_writes_report(tmp_path):
    (tmp_path / "gtfs").mkdir()
    with zipfile.ZipFile(tmp_path / "gtfs" / "feed.zip", "w") as archive:
        for name, content in FILES.items():
            archive.writestr(name, content)
    out = tmp_path / "stage"
    out.mkdir()

    stage.execute(_Context(tmp_path, out))

    trips = (out / "output" / "trips.txt").read_text(encoding = "utf-8")
    assert "t_sched" in trips and "t_flex" not in trips and "t_flexo" not in trips

    report = json.loads((out / stage.FILTER_REPORT_FILE_NAME).read_text(encoding = "utf-8"))
    (feed_report,) = report["feeds"]
    assert feed_report["feed"] == "feed.zip"
    assert feed_report["gtfs_flex_filter"]["trips_removed"] == 1
    assert feed_report["gtfs_flex_filter"]["trips_before"] == 3
    assert feed_report["route_filter"]["rule_matches"] == {"route_short_name /^Flexo$/": 1}
    assert feed_report["route_filter"]["trips_removed"] == 1
