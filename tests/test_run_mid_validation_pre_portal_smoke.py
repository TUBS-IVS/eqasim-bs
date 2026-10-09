"""End-to-end smoke of ``run_mid_validation.run`` on a tiny synthetic output directory (eqasim-bs#442).

The reference data and the Kreis polygons are replaced by minimal stubs (they live in gitignored data); everything
else -- the readers, the pre-portal preference, the tables, report.json -- is the real code path. The test exists
because a helper was once deleted while ``run()`` still called it, which no unit test of the new functions noticed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point, box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.analysis import run_mid_validation as RMV  # noqa: E402

CRS = "EPSG:25832"
PREFIX = "smoke_"


def _write_output_dir(directory: Path, with_pre_portal: bool) -> None:
    """Persons 1 (commutes 90 km, far worker), 2 (shops), 3 (pupil)."""
    pd.DataFrame({
        "person_id": [1, 2, 3], "household_id": [10, 11, 12], "age": [40, 30, 12],
        "employed": [True, True, False], "sex": ["male", "female", "male"],
        "has_driving_license": [True, True, False], "has_pt_subscription": [False, False, False],
        "is_urban_resident": [True, True, True],
    }).to_csv(directory / f"{PREFIX}persons.csv", sep=";", index=False)
    pd.DataFrame({
        "household_id": [10, 11, 12], "number_of_cars": [1, 1, 0], "number_of_bicycles": [1, 1, 1],
        "household_size": [1, 1, 1],
    }).to_csv(directory / f"{PREFIX}households.csv", sep=";", index=False)
    homes = gpd.GeoDataFrame({"household_id": [10, 11, 12]},
                             geometry=[Point(1000, 1000), Point(2000, 1000), Point(3000, 1000)], crs=CRS)
    homes.to_file(directory / f"{PREFIX}homes.gpkg", driver="GPKG")

    def trips(post_portal: bool) -> pd.DataFrame:
        work_purpose = "outside" if post_portal else "work"
        rows = [
            (1, 0, "home", work_purpose), (1, 1, work_purpose, "home"),
            (2, 0, "home", "shop"), (2, 1, "shop", "home"),
            (3, 0, "home", "education"), (3, 1, "education", "home"),
        ]
        return pd.DataFrame({
            "person_id": [r[0] for r in rows], "trip_index": [r[1] for r in rows],
            "preceding_activity_index": [r[1] for r in rows], "following_activity_index": [r[1] + 1 for r in rows],
            "departure_time": [7 * 3600 + 3600 * r[1] * 9 for r in rows],
            "arrival_time": [8 * 3600 + 3600 * r[1] * 9 for r in rows],
            "preceding_purpose": [r[2] for r in rows], "following_purpose": [r[3] for r in rows],
            "is_first": [r[1] == 0 for r in rows], "is_last": [r[1] == 1 for r in rows],
        })

    trips(post_portal=with_pre_portal).to_csv(directory / f"{PREFIX}trips.csv", sep=";", index=False)
    # Written activities: the post-portal day when the pre-portal files exist (far workplace = outside), else the donor day.
    purposes = {1: ("home", "outside" if with_pre_portal else "work", "home"), 2: ("home", "shop", "home"),
                3: ("home", "education", "home")}
    homes_xy = {1: (1000, 1000), 2: (2000, 1000), 3: (3000, 1000)}
    rows, geometry = [], []
    for person, sequence in purposes.items():
        for index, purpose in enumerate(sequence):
            rows.append({"person_id": person, "activity_index": index, "purpose": purpose})
            geometry.append(Point(homes_xy[person]) if purpose == "home" else Point(1500 + 100 * person, 2500))
    activities = pd.DataFrame(rows)
    activities["household_id"] = activities["person_id"].map({1: 10, 2: 11, 3: 12})
    gpd.GeoDataFrame(activities, geometry=geometry, crs=CRS).to_file(directory / f"{PREFIX}activities.gpkg",
                                                                   driver="GPKG")
    commutes = gpd.GeoDataFrame({"person_id": [3]}, geometry=[LineString([(3000, 1000), (3000, 3500)])], crs=CRS)
    commutes.to_file(directory / f"{PREFIX}commutes.gpkg", driver="GPKG")
    if with_pre_portal:
        trips(post_portal=False).to_csv(directory / f"{PREFIX}trips_pre_portal.csv", sep=";", index=False)
        work = gpd.GeoDataFrame({"person_id": [1]}, geometry=[LineString([(1000, 1000), (91000, 1000)])], crs=CRS)
        education = gpd.GeoDataFrame({"person_id": [3]}, geometry=[LineString([(3000, 1000), (3000, 3500)])],
                                     crs=CRS)
        path = directory / f"{PREFIX}commutes_pre_portal.gpkg"
        work.to_file(path, driver="GPKG")
        education.to_file(path, layer="education", driver="GPKG")
        now = (directory / f"{PREFIX}commutes.gpkg").stat().st_mtime + 5.0
        for name in (f"{PREFIX}trips_pre_portal.csv", f"{PREFIX}commutes_pre_portal.gpkg"):
            os.utime(directory / name, (now, now))


def _stub_reference_data(monkeypatch) -> None:
    kreise = gpd.GeoDataFrame({"ars5": ["03101"]}, geometry=[box(0, 0, 10000, 10000)], crs=CRS)
    monkeypatch.setattr(RMV.spatial, "load_kreise", lambda crs: kreise)

    def assign_geographies(homes, kreise=None):
        frame = homes.copy()
        frame["ars5"] = "03101"
        frame["kreis_name"] = "SK Braunschweig"
        frame["commune_id"] = "03101000"
        frame["regiostar7"] = 71
        frame["rs7_label"] = "Metropole"
        return frame

    monkeypatch.setattr(RMV.spatial, "assign_geographies", assign_geographies)
    p13 = {"ars5": ["03101"], "mittel": [10.0]}
    for _, _, name in RMV.BANDS:
        p13[name] = [12.5]
    monkeypatch.setattr(RMV, "_load_mid", lambda: {
        "P13": pd.DataFrame(p13),
        "P17_1": pd.DataFrame({"ars5": ["03101"], "ja": [80.0]}),
        "P9": pd.DataFrame({"ars5": ["03101"], "vollzeit": [50.0], "teilzeit": [10.0], "geringfuegig": [3.0],
                            "sonstiges": [1.0], "erwerbstaetig_unspec": [0.0]}),
        "P12_1": None,
    })
    monkeypatch.setattr(RMV, "_load_t43", lambda: None)


def _run(directory: Path) -> dict:
    out = directory / "analysis"
    out.mkdir()
    args = RMV._Args(output_dir=directory, prefix=PREFIX, analysis_out=out, label="smoke", sim_cache=None,
                     noise_bands=None)
    report = RMV.run(args)
    assert json.loads((out / "report.json").read_text(encoding="utf-8"))["trips_source"] == report["trips_source"]
    return report


def test_run_completes_on_a_run_without_pre_portal_files_and_records_the_sources(tmp_path, monkeypatch):
    _write_output_dir(tmp_path, with_pre_portal=False)
    _stub_reference_data(monkeypatch)
    report = _run(tmp_path)
    assert report["trips_source"] == "trips_csv"
    assert report["activity_purpose_source"] == "activities_gpkg"
    assert report["commute_table_scope"] == {"source": "activities_gpkg", "n_commute_rows": 1}
    assert report["n_trips"] == 6


def test_run_completes_with_the_pre_portal_files_and_records_the_sources(tmp_path, monkeypatch):
    _write_output_dir(tmp_path, with_pre_portal=True)
    _stub_reference_data(monkeypatch)
    report = _run(tmp_path)
    assert report["trips_source"] == "pre_portal_trips"
    assert report["activity_purpose_source"] == "pre_portal_trips"
    scope = report["commute_table_scope"]
    assert scope["source"] == "pre_portal_commutes"
    assert scope["n_commute_rows"] == 1
    assert scope["n_work_persons_pre_portal_trips"] == 1
    assert scope["n_work_persons_without_commute_row"] == 0
    # The far worker's 90 km commute (x detour factor) is in the table, the written activities have no work activity.
    assert report["commute_mean_km_synth"]["03101"] > 90.0
