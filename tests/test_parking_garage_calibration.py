"""The calibration of the garage decay length (``scripts/parking/calibrate_garage_decay.py``, spec Amendment E5).

Built and tested on a synthetic universe only: the reference plans of the earlier exposure checks were lost on 2026-10-07
and the real calibration runs on the server plans of Task 5 of issue #436. Covered: the target is READ from the committed
SrV table (0.708 / (0.708 + 0.2537) = 0.7362), the mean garage probability, the bisection (a known solution, an unreachable
target, the maximum distance), the destination universe of E5, the independent checks, the written table with its
provenance and its reader, and the refusal to replace a table silently. Every expected number is derived in its comment.
"""
from __future__ import annotations

import importlib.util
import math
import re
import shutil
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.parking import garages as pg

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "parking"
COMMITTED = REPO / "eqasim-data" / "data" / "braunschweig"
SCRIPT = REPO / "scripts" / "parking" / "calibrate_garage_decay.py"


@pytest.fixture(scope="module")
def cal():
    spec = importlib.util.spec_from_file_location("calibrate_garage_decay_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _probability(distance_m: float, decay_m: float) -> float:
    weight = math.exp(-distance_m / decay_m)
    return weight / (1.0 + weight)


# ------------------------------------------------------------------------------------------------ the targets


def test_the_target_is_read_from_the_committed_city_center_table_and_is_0_736(cal):
    target = cal.read_city_center_target(COMMITTED / "srv" / "srv2023_city_center_parking.csv")
    # rows garage_large_lot 0.708 and street 0.2537: 0.708 / 0.9617 = 0.73620
    assert (target.numerator, target.other) == (0.708, 0.2537)
    assert target.value == pytest.approx(0.708 / (0.708 + 0.2537), abs=1e-12)
    assert target.value == pytest.approx(0.7362, abs=5e-5)


def test_the_commuter_and_paid_references_are_read_from_the_committed_tables(cal):
    # the paid reference is no longer used by the calibration; the comparison script reads it through this reader
    commuters = cal.read_commuter_reference(COMMITTED / "srv" / "srv2023_commute_parking_by_workplace_class.csv")
    # class bs_zentrum: street 0.2225, garage or large lot 0.1923: 0.1923 / 0.4148 = 0.46359
    assert (commuters.numerator, commuters.other) == (0.1923, 0.2225)
    assert commuters.value == pytest.approx(0.464, abs=5e-4)
    assert cal.read_paid_share_reference(COMMITTED / "srv" / "srv2023_city_center_parking.csv") == 0.8333


@pytest.mark.parametrize("rows, message", [
    ("employer_lot,0.02,40\nstreet,0.25,574\n", "exactly one row 'garage_large_lot'"),
    ("garage_large_lot,0.7,1\ngarage_large_lot,0.1,1\nstreet,0.25,574\n", "exactly one row 'garage_large_lot'"),
    ("garage_large_lot,0.0,1\nstreet,0.25,574\n", "share in"),
    ("garage_large_lot,0.7,1\nstreet,1.5,574\n", "share in"),
])
def test_a_city_center_table_without_a_usable_target_row_is_refused(cal, tmp_path, rows, message):
    path = tmp_path / "srv.csv"
    path.write_text("# header\nparking_type,share,n_unweighted\n" + rows, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        cal.read_city_center_target(path)


# --------------------------------------------------------------------------------- probability and bisection


def test_the_mean_garage_probability_is_the_choice_share_of_the_garage_options_g1(cal):
    # one destination, garages at 300 m and 700 m, lambda 400: w = 0.4723665527 and 0.1737739435;
    # P = 0.6461404962 / 1.6461404962 = 0.39251844
    distances = np.array([[300.0, 700.0]])
    assert cal.mean_garage_probability(distances, 400.0) == pytest.approx(0.3925184379, abs=1e-9)
    # two destinations average: (0.3925184379 + 0.3208213008) / 2 (the second sees only the 300 m garage)
    two = np.array([[300.0, 700.0], [300.0, 5000.0]])
    assert cal.mean_garage_probability(two, 400.0, max_distance_m=1000.0) == pytest.approx(
        (0.3925184379 + 0.3208213008) / 2, abs=1e-9)
    # a garage beyond the maximum distance has weight 0, however near the threshold; exactly at it counts
    assert cal.mean_garage_probability(np.array([[1001.0]]), 400.0) == 0.0
    assert cal.mean_garage_probability(np.array([[1000.0]]), 400.0) == pytest.approx(_probability(1000.0, 400.0))
    assert cal.mean_garage_probability(np.zeros((3, 0)), 400.0) == 0.0


def test_an_empty_universe_and_a_bad_decay_are_refused(cal):
    with pytest.raises(ValueError, match="universe is empty"):
        cal.mean_garage_probability(np.zeros((0, 2)), 400.0)
    for decay in (0.0, -1.0, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="decay_m"):
            cal.mean_garage_probability(np.array([[300.0]]), decay)


def test_the_bisection_finds_the_known_lambda_of_one_destination(cal):
    # one destination 300 m from one garage: P = w / (1 + w) = 0.25 needs w = 1/3, i.e. lambda = 300 / ln 3 = 273.06 m.
    # The slope there is 0.00075 per metre, so the tolerance 0.0005 allows a lambda within about 0.7 m.
    result = cal.calibrate_decay(np.array([[300.0]]), 0.25)
    assert result.decay_m == pytest.approx(300.0 / math.log(3.0), abs=1.0)
    assert abs(result.achieved - 0.25) <= 0.0005
    assert abs(_probability(300.0, result.decay_m) - result.achieved) < 1e-12   # the mean at the rounded lambda
    assert result.decay_m == round(result.decay_m, 2)
    assert result.low_end_mean < 0.25 < result.high_end_mean and result.iterations > 1
    # a tighter tolerance gives a closer lambda (down to the rounding of lambda to 0.01 m: 0.005 m x 0.00075 = 4e-6)
    tight = cal.calibrate_decay(np.array([[300.0]]), 0.25, tolerance=1e-5)
    assert tight.decay_m == pytest.approx(300.0 / math.log(3.0), abs=0.02)
    with pytest.raises(ValueError, match="rounded to"):
        cal.calibrate_decay(np.array([[300.0]]), 0.25, tolerance=1e-8)


def test_the_bisection_is_monotone_a_higher_target_needs_a_longer_decay(cal):
    distances = np.array([[200.0, 650.0], [450.0, 900.0]])
    lambdas = [cal.calibrate_decay(distances, target).decay_m for target in (0.1, 0.2, 0.3)]
    assert lambdas == sorted(lambdas) and len(set(lambdas)) == 3


@pytest.mark.parametrize("distance_m, target", [(300.0, 0.9), (1.0, 0.1)])
def test_an_unreachable_target_fails_instead_of_returning_the_end_of_the_range(cal, distance_m, target):
    # a garage at 300 m gives 0.485 at lambda 5000 m (below 0.9); a garage at 1 m already gives 0.475 at lambda 10 m
    # (above 0.1): neither target lies between the two ends
    with pytest.raises(ValueError, match="unreachable") as error:
        cal.calibrate_decay(np.array([[distance_m]]), target)
    assert "0.4" in str(error.value)   # the message names the mean at the two ends of the range


def test_a_universe_without_a_garage_in_range_cannot_reach_a_positive_target(cal):
    with pytest.raises(ValueError, match="unreachable"):
        cal.calibrate_decay(np.array([[2500.0], [3000.0]]), 0.3)


def test_the_search_range_and_target_are_validated(cal):
    with pytest.raises(ValueError, match="target"):
        cal.calibrate_decay(np.array([[300.0]]), 1.0)
    with pytest.raises(ValueError, match="lambda_min_m"):
        cal.calibrate_decay(np.array([[300.0]]), 0.25, lambda_min_m=100.0, lambda_max_m=50.0)


# ------------------------------------------------------------------------------------ universe, checks, end to end


def test_the_destination_universe_leaves_out_home_work_education_and_everything_outside_the_zones(cal):
    activities = pd.DataFrame({"purpose": ["shop", "leisure", "other", "home", "work", "education", "shop", "work"],
                               "x": 0.0, "y": 0.0})
    zone_id = pd.Series(["bs_zone_ia", "bs_zone_ib", "bs_zone_ia", "bs_zone_ia", "bs_zone_ia", "bs_zone_ib", np.nan,
                         "other_zone"])
    universe, commuters = cal.universe_masks(activities, zone_id)
    assert universe.tolist() == [True, True, True, False, False, False, False, False]
    assert commuters.tolist() == [False, False, False, False, True, True, False, False]


def test_the_zone_level_paid_share_check_is_gone_the_time_aware_one_is_the_comparison_scripts(cal):
    # R-5-4 (issue #436, Task 5): the zone-level street-paid share was blind to the fee window, the free threshold and the
    # maximum stay and read 1.0 in the zones Ia and Ib, so it carried no information. The paid share of a run is the
    # time-aware one of scripts/parking/compare_parking_targets.py, computed from the outcomes the Java side priced.
    assert not hasattr(cal, "street_paid_share")
    text = SCRIPT.read_text(encoding="utf-8")
    assert "street_paid_share" not in text and "check_street_paid_share_zone_level" not in text
    assert "compare_parking_targets.py" in text


@pytest.fixture
def inputs(tmp_path):
    """The synthetic universe of tests/fixtures/parking/calibration_plans_fixture.xml: zones, garages, SrV tables."""
    plans = FIXTURES / "calibration_plans_fixture.xml"
    zones = gpd.GeoDataFrame(
        {"zone_id": ["bs_zone_ia", "bs_zone_ib"], "geometry_source": "centre_approximation",
         "source_url": "https://example.org/zones", "source_date": "2026-10-07", "digitised_on": "2026-10-07",
         "digitising_note": "synthetic zone of the calibration test"},
        crs="EPSG:25832", geometry=[box(603000, 5790000, 603200, 5790200), box(603400, 5790000, 603600, 5790200)])
    zones_path = tmp_path / "zones.geojson"
    zones.to_crs("EPSG:4326").to_file(zones_path, driver="GeoJSON")
    # two fixture garages moved next to the zones: 300 m and 500 m from the two activity locations; one out of range
    frame = pg.load_garages(FIXTURES / "parking_garages_fixture.geojson").iloc[:2].copy()
    frame["geometry"] = [Point(603100.0, 5790400.0), Point(603100.0, 5792000.0)]
    garages_path = tmp_path / "garages.geojson"
    pg.write_garages(gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:25832"), garages_path)
    city_center = tmp_path / "srv2023_city_center_parking.csv"
    # target 0.2718 / (0.2718 + 0.7282) = 0.2718; the mean at lambda 400 m is 0.27176 (derived in the test below)
    city_center.write_text("# synthetic\nparking_type,share,n_unweighted\nemployer_lot,0.01,1\nstreet,0.7282,1\n"
                           "garage_large_lot,0.2718,1\nother,0.01,1\npaid_share_overall,0.8,1\n", encoding="utf-8")
    commute = COMMITTED / "srv" / "srv2023_commute_parking_by_workplace_class.csv"
    return {"plans": plans, "zones_path": zones_path, "garages_path": garages_path,
            "city_center_path": city_center, "commute_path": commute}


def test_the_calibration_recovers_its_lambda_on_the_synthetic_universe_and_writes_the_table(cal, inputs, tmp_path, caplog):
    # Universe: p01 shop and p02 leisure at 300 m from the garage, p03 other and p04 shop at 500 m (four activities).
    # At lambda 400 m: P(300) = 0.4723665527 / 1.4723665527 = 0.3208213, P(500) = 0.2865047969 / 1.2865047969 = 0.2227
    # mean = (0.3208213 + 0.2227) / 2 = 0.27176; the target 0.2718 is that mean, so lambda is 400 m within the tolerance.
    out = tmp_path / "parking_garage_decay_calibration_2026.csv"
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=out, generated_on="2026-10-07", **inputs)
    assert result["universe_activities"] == 4 and result["with_garage_in_range"] == 4
    expected_mean_at_400 = (2 * _probability(300.0, 400.0) + 2 * _probability(500.0, 400.0)) / 4
    assert expected_mean_at_400 == pytest.approx(0.27176, abs=1e-5)
    assert result["target"] == pytest.approx(0.2718, abs=1e-12)
    assert abs(result["decay_length_m"] - 400.0) <= 2.0
    assert abs(result["achieved"] - 0.2718) <= 0.0005
    # the independent checks on the same plans: p05 work at 300 m and p06 education at 500 m
    at_lambda = result["decay_length_m"]
    assert result["commuter_mean"] == pytest.approx((_probability(300.0, at_lambda) + _probability(500.0, at_lambda)) / 2,
                                                    abs=1e-5)
    assert result["commuter_reference"] == pytest.approx(0.464, abs=5e-4)
    assert "street_paid_share" not in result and "paid_reference" not in result   # R-5-4: dropped, see the comparison script
    # the table: ASCII, provenance header, the rows, readable by the reader that the config test uses
    text = out.read_bytes().decode("ascii")
    assert text == result["text"] and "\r" not in text
    assert "paid_share" not in text and "tariffs" not in text and "compare_parking_targets.py" in text
    for needle in ("calibrate_garage_decay.py on 2026-10-07", "sha256=", "ASSUMPTIONS G1 to G3", "Universe caveat",
                   "bs_zone_ia, bs_zone_ib", "4 activities, 4 of them with at least one priced garage",
                   "garage_large_lot / (garage_large_lot + street) = 0.2718 / (0.2718 + 0.7282)", "no validation"):
        assert needle in text, needle
    assert len(re.findall(r"sha256=[0-9a-f]{64}", text)) == 5   # plans, zones, garages and the two SrV tables
    values = cal.read_calibration_table(out)
    assert values["decay_length_m"] == result["decay_length_m"] and values["universe_activities"] == 4
    assert values["target_garage_probability"] == pytest.approx(0.2718, abs=1e-6)
    assert any("with a priced garage within 1000 m: 4/4" in message for message in caplog.messages)


def test_an_existing_table_is_not_replaced_without_overwrite(cal, inputs, tmp_path):
    out = tmp_path / "table.csv"
    out.write_text("# an earlier calibration\n", encoding="utf-8")
    with pytest.raises(FileExistsError, match="--overwrite"):
        cal.run(out_path=out, **inputs)
    assert out.read_text(encoding="utf-8") == "# an earlier calibration\n"
    cal.run(out_path=out, overwrite=True, **inputs)
    assert cal.read_calibration_table(out)["decay_length_m"] > 0


def test_a_target_the_synthetic_garages_cannot_reach_fails_and_writes_nothing(cal, inputs, tmp_path):
    inputs["city_center_path"].write_text("parking_type,share,n_unweighted\nstreet,0.01,1\ngarage_large_lot,0.99,1\n"
                                          "paid_share_overall,0.8,1\n", encoding="utf-8")
    out = tmp_path / "table.csv"
    with pytest.raises(ValueError, match="unreachable"):
        cal.run(out_path=out, **inputs)
    assert not out.exists()


def test_a_plans_file_without_a_destination_of_the_universe_fails(cal, inputs, tmp_path):
    # the only activity is a home inside zone Ia: it lies in a zone, but it is no destination of the universe
    plans = tmp_path / "plans.xml"
    plans.write_text('<population><person id="a"><plan selected="yes"><activity type="home" x="603100" y="5790100"/>'
                     "</plan></person></population>", encoding="utf-8")
    with pytest.raises(ValueError, match="no destination of the universe"):
        cal.run(**{**inputs, "plans": plans}, out_path=None)


def test_the_calibration_table_reader_refuses_a_malformed_table(cal, tmp_path):
    path = tmp_path / "table.csv"
    with pytest.raises(FileNotFoundError):
        cal.read_calibration_table(path)
    path.write_text("# x\nquantity,value\ndecay_length_m,300\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns"):
        cal.read_calibration_table(path)
    path.write_text("quantity,value,unit,note\ntolerance,0.0005,share,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decay_length_m"):
        cal.read_calibration_table(path)
    path.write_text("quantity,value,unit,note\ndecay_length_m,300,m,x\ndecay_length_m,301,m,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        cal.read_calibration_table(path)


def test_the_script_does_not_hard_code_the_target(cal):
    # the SrV shares are read from the table: neither 0.708 nor 0.2537 nor 0.736 appears as a number in the script
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    for number in ("0.708", "0.2537", "0.736", "0.7362", "0.464"):
        assert number not in code


# ------------------------------------------------------------------------------------------ the facility-kind filter (C1)


def _with_kinds(inputs, tmp_path, kinds):
    """The synthetic garages of ``inputs`` with the given ``facility_kind`` per garage (near garage first)."""
    frame = pg.load_garages(inputs["garages_path"]).copy()
    frame["facility_kind"] = kinds
    path = tmp_path / "garages_kinds.geojson"
    pg.write_garages(gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:25832"), path)
    return {**inputs, "garages_path": path}


def test_the_facility_kind_filter_keeps_the_selected_kinds_and_the_table_records_it(cal, inputs, tmp_path, caplog):
    # near garage (300 m and 500 m from the universe) is a surface lot, the far one (out of range) a garage: selecting
    # surface lots only leaves the near garage, so lambda stays 400 m; the table records the filter and the garages used
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    everything = cal.run(out_path=None, **mixed)
    out = tmp_path / "surface_only.csv"
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=out, facility_kinds=("surface_lot",), **mixed)
    assert result["decay_length_m"] == everything["decay_length_m"] and abs(result["decay_length_m"] - 400.0) <= 2.0
    text = out.read_text(encoding="ascii")
    assert "facility kinds: surface_lot" in text and "1 priced of 2 listed" in text
    values = cal.read_calibration_table(out)
    assert values["garage_facility_filter_active"] == 1 and values["priced_garages_used"] == 1
    all_out = tmp_path / "all_kinds.csv"
    cal.run(out_path=all_out, **mixed)
    all_values = cal.read_calibration_table(all_out)
    assert all_values["garage_facility_filter_active"] == 0 and all_values["priced_garages_used"] == 2
    assert "facility kinds: all" in all_out.read_text(encoding="ascii")
    assert any("facility kinds surface_lot" in message for message in caplog.messages)


def test_a_facility_kind_filter_that_leaves_no_reachable_garage_or_names_an_unknown_kind_fails(cal, inputs, tmp_path):
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    # only the far garage (out of range of every destination) remains: the target is unreachable, nothing is returned
    with pytest.raises(ValueError, match="unreachable"):
        cal.run(out_path=None, facility_kinds=("garage",), **mixed)
    with pytest.raises(ValueError, match="unknown facility kind 'parkade'"):
        cal.run(out_path=None, facility_kinds=("parkade",), **mixed)
    # a known kind that no priced garage has
    only_garages = _with_kinds(inputs, tmp_path, ["garage", "garage"])
    with pytest.raises(ValueError, match="no priced garage of the facility kind"):
        cal.run(out_path=None, facility_kinds=("surface_lot",), **only_garages)


def test_the_command_line_parses_the_facility_kinds(cal):
    assert cal.parse_facility_kinds("garage") == ("garage",)
    assert cal.parse_facility_kinds("garage,surface_lot") == ("garage", "surface_lot")
    assert cal.parse_facility_kinds("all") is None
