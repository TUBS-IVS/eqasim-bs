import json

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon

from braunschweig.analysis.population_validation import run_population_validation as R
from braunschweig.analysis.population_validation import controls as C
from braunschweig.analysis.population_validation.population_source import PopulationFrames


def test_parser_requires_exactly_one_source():
    with pytest.raises(SystemExit):
        R._parse_args(["--prefix", "p_"])  # neither source
    with pytest.raises(SystemExit):
        R._parse_args(["--run-output-dir", "a", "--sim-cache", "b"])  # both


def test_parser_escort_passive_education_flag_defaults_off():
    # Issue #256: the flag threading the trip-coherence W1/W12 escort
    # references onto the active-only pinned split must default to False
    # (byte-identical behaviour) and be settable via --escort-passive-education.
    off = R._parse_args(["--run-output-dir", "a"])
    assert off.escort_passive_education is False
    on = R._parse_args(["--run-output-dir", "a", "--escort-passive-education"])
    assert on.escort_passive_education is True


def test_deviation_wide_frame_pivots_delta_pp():
    long = pd.DataFrame({
        "control": ["cars_per_hh"], "category": ["0"], "geography": ["kreis"],
        "geo_id": ["03101"], "delta_pp": [2.5],
    })
    wide = R._deviation_wide(long, geography="kreis", id_name="ars5")
    assert "cars_per_hh__0_delta_pp" in wide.columns
    assert wide.loc[0, "ars5"] == "03101"


def test_interpretation_sections_split_good_and_bad():
    quality = pd.DataFrame({
        "control": ["good_one", "bad_one"], "family": ["mid_person", "mid_household"],
        "grade": ["very good", "needs improvement"], "mean_abs_delta_pp": [0.2, 7.0],
        "srmse": [0.01, 0.3], "cause_hint": ["", "structural offset"],
    })
    md = R._interpretation_markdown(quality)
    assert "good_one" in md and "bad_one" in md
    assert "structural offset" in md


# ---------------------------------------------------------------------------
# _attach_home_geometry_to_vehicles
# ---------------------------------------------------------------------------

def _home_geom():
    return gpd.GeoDataFrame(
        {"household_id": [10], "ars5": ["03101"], "commune_id": ["03101000"]},
        geometry=[Point(605000, 5790000)], crs="EPSG:25832")


def test_attach_vehicle_geometry_via_owner_id():
    vehicles = pd.DataFrame({"vehicle_id": ["v1"], "owner_id": [1], "brand": ["VW"]})
    persons = pd.DataFrame({"person_id": [1], "household_id": [10]})
    out = R._attach_home_geometry_to_vehicles(vehicles, persons, _home_geom())
    assert out is not None
    assert out["ars5"].iloc[0] == "03101"
    assert out["geometry"].notna().all()


def test_attach_vehicle_geometry_via_household_id():
    vehicles = pd.DataFrame({"vehicle_id": ["v1"], "household_id": [10], "brand": ["VW"]})
    persons = pd.DataFrame({"person_id": [1], "household_id": [10]})
    out = R._attach_home_geometry_to_vehicles(vehicles, persons, _home_geom())
    assert out is not None
    assert out["geometry"].notna().all()


def test_attach_vehicle_geometry_no_key_returns_none():
    vehicles = pd.DataFrame({"vehicle_id": ["v1"], "brand": ["VW"]})
    persons = pd.DataFrame({"person_id": [1], "household_id": [10]})
    assert R._attach_home_geometry_to_vehicles(vehicles, persons, _home_geom()) is None


# ---------------------------------------------------------------------------
# End-to-end run() smoke test (monkeypatched: no heavy real spatial / IO)
# ---------------------------------------------------------------------------

def _e2e_frames():
    """A tiny hand-built PopulationFrames covering one kreis/gemeinde point.

    The persons frame carries a categorical control column ``sex`` so a single
    targeted control can exercise the full evaluate -> assess -> chart path.
    """
    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "household_id": [10, 10, 20, 20],
        "sex": ["male", "female", "male", "female"],
    })
    households = pd.DataFrame({
        "household_id": [10, 20],
        "household_size": [2, 2],
        "number_of_cars": [1, 0],
    })
    homes = gpd.GeoDataFrame(
        {"household_id": [10, 20]},
        geometry=[Point(605000, 5790000), Point(605100, 5790100)],
        crs="EPSG:25832")
    return PopulationFrames(persons, households, homes, None, "run_output", "x", "e2e_")


def _e2e_kreise():
    return gpd.GeoDataFrame(
        {"ars5": ["03101"], "kreis_name": ["SK Braunschweig"]},
        geometry=[Polygon([(600000, 5785000), (610000, 5785000),
                           (610000, 5795000), (600000, 5795000)])],
        crs="EPSG:25832")


def _e2e_gemeinden():
    return gpd.GeoDataFrame(
        {"commune_id": ["03101000"]},
        geometry=[Polygon([(600000, 5785000), (610000, 5785000),
                           (610000, 5795000), (600000, 5795000)])],
        crs="EPSG:25832")


def _e2e_assign_geographies(homes, kreise=None):
    """Stand-in for spatial.assign_geographies: attach the test kreis/gemeinde
    columns (+ NA RegioStaR) without reading VG250."""
    out = homes.copy()
    out["ars5"] = "03101"
    out["commune_id"] = "03101000"
    out["regiostar7"] = pd.NA
    out["rs7_label"] = pd.NA
    return out


def _e2e_sex_target(data_path):
    return pd.DataFrame({
        "geo_id": ["03101", "03101"],
        "category": ["male", "female"],
        "target_share": [0.5, 0.5],
    })


def _e2e_registry(data_path):
    return [C.categorical_person_control(
        "sex", "census", "kreis", "sex", ("male", "female"), _e2e_sex_target)]


def test_run_end_to_end_smoke(tmp_path, monkeypatch):
    """Exercise the full run() orchestration with all heavy real-data sources
    monkeypatched, and assert every output artefact is written and the report
    JSON is consistent with the fixture."""
    frames = _e2e_frames()

    monkeypatch.setattr(R.spatial, "load_kreise", lambda crs: _e2e_kreise())
    monkeypatch.setattr(R.spatial, "load_gemeinden", lambda crs: _e2e_gemeinden())
    monkeypatch.setattr(R.spatial, "assign_geographies", _e2e_assign_geographies)
    monkeypatch.setattr(R.C, "build_registry", _e2e_registry)
    monkeypatch.setattr(R.PS, "load_population",
                        lambda run_output_dir=None, sim_cache=None, prefix=None: frames)

    out = tmp_path / "analysis_out"
    ns = R._parse_args(["--run-output-dir", str(tmp_path), "--label", "e2e",
                        "--analysis-out", str(out)])
    report = R.run(ns)

    expected = [
        "controls_long.csv", "controls_summary.csv", "quality_summary.csv",
        "validation_chart_stdev.png", "validation_chart_rmse.png",
        "quality_by_control.png", "report.json", "summary.md",
        "population_explorer.gpkg",
    ]
    for name in expected:
        assert (out / name).exists(), f"missing output artefact: {name}"

    parsed = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert parsed["n_persons"] == len(frames.persons)
    assert report["n_persons"] == len(frames.persons)
