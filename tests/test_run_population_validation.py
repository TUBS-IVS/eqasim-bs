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


# ---------------------------------------------------------------------------
# participation fit wiring (issue #334)
# ---------------------------------------------------------------------------
# The SrV participation controls (#224 work/leisure/education, #227 escort) are
# input-only popsim seed columns: they never reach the assembled population, so
# controls.build_registry structurally cannot validate them. participation_fit
# measures them from the realised TRIPS instead -- but it was called by nothing,
# so the controls shipped with no routine fit evidence. These tests pin the
# wiring, which is the whole defect.


def _participation_trips():
    """Person 1 makes an escort round-trip, person 3 a work round-trip;
    persons 2 and 4 are immobile."""
    return pd.DataFrame({
        "person_id": [1, 1, 3, 3],
        "preceding_purpose": ["home", "escort", "home", "work"],
        "following_purpose": ["escort", "home", "work", "home"],
    })


def test_participation_fit_report_joins_kreis_and_scores_every_purpose():
    persons = pd.DataFrame({"person_id": [1, 2, 3, 4],
                            "household_id": [10, 10, 20, 20]})
    geo = pd.DataFrame({"household_id": [10, 20], "ars5": ["03101", "03101"]})

    fit = R._participation_fit_report(persons, geo, _participation_trips(), R.DATA_PATH)

    assert set(fit.columns) == {"ars5", "purpose", "realised_rate", "target_rate", "abs_error"}
    # Every registered purpose plus the derived mobility pseudo-purpose.
    assert set(fit["purpose"]) == {"work", "leisure", "education", "escort", "mobility"}
    by_purpose = fit.set_index("purpose")
    # 1 of 4 persons has an escort trip; the ars5 came from the geo join, not
    # from the persons frame (which carries no kreis column at all).
    assert by_purpose.loc["escort", "realised_rate"] == pytest.approx(0.25)
    assert by_purpose.loc["work", "realised_rate"] == pytest.approx(0.25)
    assert by_purpose.loc["leisure", "realised_rate"] == pytest.approx(0.0)
    assert by_purpose.loc["mobility", "realised_rate"] == pytest.approx(0.5)
    # The escort target is the committed SrV 03101 row (0.0751), not invented here.
    assert by_purpose.loc["escort", "target_rate"] == pytest.approx(0.0751, abs=1e-9)


def test_participation_fit_report_drops_persons_without_a_kreis():
    """No silent fallback: a household with no geo row must not be counted into
    a Kreis it does not belong to."""
    persons = pd.DataFrame({"person_id": [1, 2], "household_id": [10, 99]})
    geo = pd.DataFrame({"household_id": [10], "ars5": ["03101"]})

    fit = R._participation_fit_report(persons, geo, _participation_trips(), R.DATA_PATH)

    # Only person 1 remains, and it has the escort trip -> rate 1.0, not 0.5.
    assert fit.set_index("purpose").loc["escort", "realised_rate"] == pytest.approx(1.0)


def test_run_writes_participation_fit_when_trips_are_present(tmp_path, monkeypatch):
    """The regression test for issue #334 itself: run() must INVOKE the analysis.

    Before the fix participation_fit.py was imported by nothing, so no run ever
    produced this artefact.
    """
    base = _e2e_frames()
    frames = PopulationFrames(base.persons, base.households, base.homes, None,
                              "run_output", "x", "e2e_", _participation_trips())

    monkeypatch.setattr(R.spatial, "load_kreise", lambda crs: _e2e_kreise())
    monkeypatch.setattr(R.spatial, "load_gemeinden", lambda crs: _e2e_gemeinden())
    monkeypatch.setattr(R.spatial, "assign_geographies", _e2e_assign_geographies)
    monkeypatch.setattr(R.C, "build_registry", _e2e_registry)
    monkeypatch.setattr(R.PS, "load_population",
                        lambda run_output_dir=None, sim_cache=None, prefix=None: frames)

    out = tmp_path / "analysis_out"
    ns = R._parse_args(["--run-output-dir", str(tmp_path), "--label", "pf",
                        "--analysis-out", str(out), "--no-geo"])
    R.run(ns)

    written = out / "participation_fit.csv"
    assert written.exists(), "run() did not produce participation_fit.csv"
    fit = pd.read_csv(written)
    assert set(fit["purpose"]) == {"work", "leisure", "education", "escort", "mobility"}


# ---------------------------------------------------------------------------
# participation-UNIVERSE fit wiring (Plan B, issue #368, Task 8)
# ---------------------------------------------------------------------------
# work_by_employment / education_0_5 / education_6_17 / education_18plus replace
# the all-persons work_participation / education_participation controls with
# controls defined over a SPECIFIC universe (age band, employment status). These
# tests pin _participation_universe_fit_report's wiring and, critically, that its
# OWN try block in run() cannot take down the (issue #334) participation_fit
# evidence -- see the module's own comment at that call site.


def _participation_universe_persons():
    """One employed, one non-employed 30-year-old in the same household/Kreis --
    both inside work_by_employment's own universe (age >= 14)."""
    return pd.DataFrame({
        "person_id": [1, 2],
        "household_id": [10, 10],
        "employment_status": ["vollzeit", "nicht_erwerbstaetig"],
        "age": [30, 30],
    })


def _participation_universe_trips():
    """Only person 1 (employed) makes a work round-trip; person 2 is immobile."""
    return pd.DataFrame({
        "person_id": [1, 1],
        "preceding_purpose": ["home", "work"],
        "following_purpose": ["work", "home"],
    })


def test_participation_universe_fit_report_joins_kreis_and_scores_work_by_employment():
    persons = _participation_universe_persons()
    geo = pd.DataFrame({"household_id": [10], "ars5": ["03101"]})

    fit = R._participation_universe_fit_report(
        persons, geo, _participation_universe_trips(), R.DATA_PATH)

    assert set(fit.columns) == {
        "ars5", "control", "category", "realised_share", "target_share", "abs_error"}
    by_cat = fit[fit["control"] == "work_by_employment"].set_index("category")
    # 1/2 employed+work, 1/2 non-employed+no-work; ars5 comes from the geo join,
    # not from the persons frame (which carries no kreis column at all).
    assert by_cat.loc["employed_work", "realised_share"] == pytest.approx(0.5)
    assert by_cat.loc["nonemployed_nowork", "realised_share"] == pytest.approx(0.5)
    # The target is the committed 03101 row of target2026_work_by_employment_by_kreis.csv,
    # not invented here.
    assert by_cat.loc["employed_work", "target_share"] == pytest.approx(0.3604, abs=1e-9)


def test_participation_universe_fit_report_drops_persons_without_a_kreis():
    """No silent fallback: a household with no geo row must not be counted into a
    Kreis it does not belong to (mirrors the purpose-only participation_fit test)."""
    persons = pd.concat([
        _participation_universe_persons(),
        pd.DataFrame({"person_id": [3], "household_id": [99],
                      "employment_status": ["vollzeit"], "age": [30]}),
    ], ignore_index=True)
    geo = pd.DataFrame({"household_id": [10], "ars5": ["03101"]})  # household 99 has no geo row

    fit = R._participation_universe_fit_report(
        persons, geo, _participation_universe_trips(), R.DATA_PATH)

    # Only the 2 persons with a Kreis remain (n=2 -> employed_work 0.5); person
    # 3 (employed, no trip) joining the universe would dilute it to 1/3 and add
    # an employed_nowork row instead of leaving it at 0.0 -- the fit's
    # ars5/target_share/abs_error columns carry no n_persons, so the share
    # itself is what proves the exclusion.
    by_cat = fit[fit["control"] == "work_by_employment"].set_index("category")
    assert by_cat.loc["employed_work", "realised_share"] == pytest.approx(0.5)
    assert by_cat.loc["employed_nowork", "realised_share"] == pytest.approx(0.0)


def test_run_participation_universe_fit_failure_does_not_affect_participation_fit(
        tmp_path, monkeypatch):
    """Own-try-block regression (Plan B, issue #368): the e2e fixture's persons frame
    carries no 'employment_status' / 'age', so _participation_universe_fit_report
    must fail -- but ONLY that block's evidence may be missing. participation_fit
    (a separate try block, issue #334) must still be produced from the SAME trips,
    proving one block's failure cannot take down the other."""
    base = _e2e_frames()
    frames = PopulationFrames(base.persons, base.households, base.homes, None,
                              "run_output", "x", "e2e_", _participation_trips())

    monkeypatch.setattr(R.spatial, "load_kreise", lambda crs: _e2e_kreise())
    monkeypatch.setattr(R.spatial, "load_gemeinden", lambda crs: _e2e_gemeinden())
    monkeypatch.setattr(R.spatial, "assign_geographies", _e2e_assign_geographies)
    monkeypatch.setattr(R.C, "build_registry", _e2e_registry)
    monkeypatch.setattr(R.PS, "load_population",
                        lambda run_output_dir=None, sim_cache=None, prefix=None: frames)

    out = tmp_path / "analysis_out"
    ns = R._parse_args(["--run-output-dir", str(tmp_path), "--label", "puf",
                        "--analysis-out", str(out), "--no-geo"])
    report = R.run(ns)

    assert (out / "participation_fit.csv").exists()
    assert report["participation_fit"] is not None
    assert not (out / "participation_universe_fit.csv").exists()
    assert report["participation_universe_fit"] is None
