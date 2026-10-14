"""The calibration of the garage decay lengths (``scripts/parking/calibrate_garage_decay.py``, spec Amendments E5 and H2).

Built and tested on a synthetic universe only: the reference plans of the earlier exposure checks were lost on 2026-10-07
and the real calibration runs on the server plans of Task 5 of issue #436. Covered: the target is READ from the committed
SrV table (0.708 / (0.708 + 0.2537) = 0.7362), the mean garage probability, the bisection (a known solution, an unreachable
target, the maximum distance), the destination universe of E5, the commuter universe of H2 (work and education without
parkingFree) with the commuter target READ from the bs_zentrum row, the Wolfsburg check of H3b, the written table with its
provenance and its reader, and the refusal to replace a table silently. Every expected number is derived in its comment.
"""
from __future__ import annotations

import importlib.util
import math
import re
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
    commute = COMMITTED / "srv" / "srv2023_commute_parking_by_workplace_class.csv"
    commuters = cal.read_commuter_reference(commute)
    # class bs_zentrum (the target of H2, ruling R-4h-1: the PAID-only shares, like-for-like with a universe of payers): paid
    # street 0.0456, paid garage or large lot 0.0965: 0.0965 / 0.1421 = 0.67910 (the free-inclusive 0.1923 / 0.4148 = 0.4636
    # is not used: free parkers are in its denominator)
    assert cal.COMMUTER_CLASS == "bs_zentrum"
    assert (commuters.numerator_name, commuters.other_name) == ("share_garage_large_lot_paid", "share_street_paid")
    assert (commuters.numerator, commuters.other) == (0.0965, 0.0456)
    assert commuters.value == pytest.approx(0.0965 / (0.0965 + 0.0456), abs=1e-12)
    assert cal.read_commuter_reference(commute, cal.COMMUTER_CLASS) == commuters
    # row 03103 (Wolfsburg, the check of H3b): paid street 0.0080, paid garage or large lot 0.0225: 0.0225 / 0.0305 = 0.73770
    assert cal.WOLFSBURG_CLASS == "03103"
    wolfsburg = cal.read_commuter_reference(commute, cal.WOLFSBURG_CLASS)
    assert (wolfsburg.numerator, wolfsburg.other) == (0.0225, 0.0080)
    assert wolfsburg.value == pytest.approx(0.0225 / (0.0225 + 0.0080), abs=1e-12)
    with pytest.raises(ValueError, match="expected exactly one class row 'no_such_class'"):
        cal.read_commuter_reference(commute, "no_such_class")


def test_a_commute_table_without_the_payment_per_place_columns_is_refused_with_the_remedy(cal, tmp_path):
    # a table written before the revision of H2 (ruling R-4h-1) has no paid-only shares: it must not fall back to the
    # free-inclusive share
    path = tmp_path / "old_commute.csv"
    path.write_text("# old\nworkplace_class,level,n_unweighted,n_eff,share_employer_lot,share_street,share_garage_large_lot,"
                    "share_other,share_paid_total,share_free_total\nbs_zentrum,class,221,104.4,0.571,0.2225,0.1923,0.0141,"
                    "0.2062,0.7938\n", encoding="utf-8")
    with pytest.raises(ValueError, match="share_garage_large_lot_paid.*extract_srv_commute_parking.py"):
        cal.read_commuter_reference(path)
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
                               "x": 0.0, "y": 0.0, "parking_free": False})
    zone_id = pd.Series(["bs_zone_ia", "bs_zone_ib", "bs_zone_ia", "bs_zone_ia", "bs_zone_ia", "bs_zone_ib", np.nan,
                         "other_zone"])
    masks = cal.universe_masks(activities, zone_id)
    assert masks.universe.tolist() == [True, True, True, False, False, False, False, False]
    assert masks.commuters.tolist() == [False, False, False, False, True, True, False, False]
    assert not masks.wolfsburg_commuters.any()


def test_the_commuter_universe_of_h2_leaves_out_parking_free_activities_and_the_wolfsburg_check_has_its_own_zones(cal):
    # work and education inside Ia and Ib WITHOUT parkingFree (H2): the employer-lot and free-parking users leave the
    # denominator because the SrV share is among those who park on the street or in a garage; parkingFree does not change
    # the E5 universe (it has no work and education); the Wolfsburg check (H3b) is work and education without parkingFree
    # inside wob_tarifzone_1..3
    activities = pd.DataFrame({
        "purpose": ["work", "work", "education", "education", "shop", "work", "education", "work", "work", "home"],
        "parking_free": [False, True, False, True, True, False, False, True, False, False], "x": 0.0, "y": 0.0})
    zone_id = pd.Series(["bs_zone_ia", "bs_zone_ia", "bs_zone_ib", "bs_zone_ib", "bs_zone_ia", "wob_tarifzone_1",
                         "wob_tarifzone_3", "wob_tarifzone_2", "wob_tarifzone_4", "wob_tarifzone_1"])
    masks = cal.universe_masks(activities, zone_id)
    assert cal.WOLFSBURG_ZONE_IDS == ("wob_tarifzone_1", "wob_tarifzone_2", "wob_tarifzone_3")
    assert masks.commuters.tolist() == [True, False, True, False, False, False, False, False, False, False]
    assert masks.universe.tolist() == [False, False, False, False, True, False, False, False, False, False]
    assert masks.wolfsburg_commuters.tolist() == [False, False, False, False, False, True, True, False, False, False]
    assert masks.commuters_with_parking_free == 2   # the two parkingFree work/education activities inside Ia and Ib


def test_a_frame_without_the_parking_free_column_is_refused_instead_of_assuming_nobody_is_free(cal):
    activities = pd.DataFrame({"purpose": ["work"], "x": 0.0, "y": 0.0})
    with pytest.raises(ValueError, match="parking_free"):
        cal.universe_masks(activities, pd.Series(["bs_zone_ia"]))


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
        {"zone_id": ["bs_zone_ia", "bs_zone_ib", "wob_tarifzone_1"], "geometry_source": "centre_approximation",
         "source_url": "https://example.org/zones", "source_date": "2026-10-07", "digitised_on": "2026-10-07",
         "digitising_note": "synthetic zone of the calibration test"},
        crs="EPSG:25832", geometry=[box(603000, 5790000, 603200, 5790200), box(603400, 5790000, 603600, 5790200),
                                    box(603700, 5790000, 603900, 5790200)])
    zones_path = tmp_path / "zones.geojson"
    zones.to_crs("EPSG:4326").to_file(zones_path, driver="GeoJSON")
    # two fixture garages moved next to the zones: 300 m and 500 m from the two activity locations; one out of range
    frame = pg.load_garages(FIXTURES / "parking_garages_fixture.geojson", allow_legacy_columns=True).iloc[:2].copy()
    frame["geometry"] = [Point(603100.0, 5790400.0), Point(603100.0, 5792000.0)]
    garages_path = tmp_path / "garages.geojson"
    pg.write_garages(gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:25832"), garages_path)
    city_center = tmp_path / "srv2023_city_center_parking.csv"
    # target 0.2718 / (0.2718 + 0.7282) = 0.2718; the mean at lambda 400 m is 0.27176 (derived in the test below)
    city_center.write_text("# synthetic\nparking_type,share,n_unweighted\nemployer_lot,0.01,1\nstreet,0.7282,1\n"
                           "garage_large_lot,0.2718,1\nother,0.01,1\npaid_share_overall,0.8,1\n", encoding="utf-8")
    # synthetic commute table: bs_zentrum paid garage 0.04 / (0.04 + paid street 0.06) = 0.4 (the target of lambda_c), 03103 paid
    # 0.06 / (0.06 + 0.04) = 0.6 (the check); the free-inclusive columns are a decoy (0.5 / 0.6 = 0.833 must not be read)
    commute = tmp_path / "srv2023_commute_parking_by_workplace_class.csv"
    commute.write_text(
        "# synthetic\nworkplace_class,level,n_unweighted,n_eff,share_employer_lot,share_street,share_garage_large_lot,share_other,"
        "share_paid_total,share_free_total,share_garage_large_lot_paid,share_garage_large_lot_free,share_street_paid,"
        "share_street_free\n"
        "bs_zentrum,class,221,104.4,0.1,0.1,0.5,0.0,0.1,0.9,0.04,0.46,0.06,0.04\n"
        "03103,class,691,162.7,0.1,0.1,0.5,0.0,0.1,0.9,0.06,0.44,0.04,0.06\n", encoding="utf-8")
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
    # H2: the commuter decay on the same plans: p05 work at 300 m and p06 education at 500 m without parkingFree (p14 carries it
    # and is excluded), target 0.04 / (0.04 + 0.06) = 0.4 READ from the paid columns of the bs_zentrum row; the mean at
    # lambda_c equals it
    at_lambda_c = result["decay_commute_length_m"]
    assert result["commuter_universe_activities"] == 2 and result["commuter_activities_excluded_parking_free"] == 1
    assert result["commuter_target"] == pytest.approx(0.4, abs=1e-12)
    mean_at_lambda_c = (_probability(300.0, at_lambda_c) + _probability(500.0, at_lambda_c)) / 2
    assert mean_at_lambda_c == pytest.approx(result["commuter_target"], abs=0.0005)
    # the garages are stored as WGS84 with 7 decimals (about 1 cm), which moves the distances by about 1e-6 in the mean
    assert result["commuter_achieved"] == pytest.approx(mean_at_lambda_c, abs=1e-5)
    assert at_lambda_c == round(at_lambda_c, 2) and at_lambda_c != result["decay_length_m"]
    # the Wolfsburg check (H3b) with lambda_c: p11 work at 761.58 m and p12 education at 743.30 m from the near garage
    # (p13 carries parkingFree and is excluded); a number and no validation, reference 0.06 / (0.06 + 0.04) = 0.6
    assert result["wolfsburg_activities"] == 2 and result["wolfsburg_with_garage_in_range"] == 2
    assert result["wolfsburg_mean"] == pytest.approx(
        (_probability(math.sqrt(700.0 ** 2 + 300.0 ** 2), at_lambda_c)
         + _probability(math.sqrt(700.0 ** 2 + 250.0 ** 2), at_lambda_c)) / 2, abs=1e-6)
    assert result["wolfsburg_reference"] == pytest.approx(0.6, abs=1e-12)
    assert "commuter_mean" not in result   # the bs_zentrum share is a target now, not a check
    assert "street_paid_share" not in result and "paid_reference" not in result   # R-5-4: dropped, see the comparison script
    # the table: ASCII, provenance header, the rows, readable by the reader that the config test uses
    text = out.read_bytes().decode("ascii")
    assert text == result["text"] and "\r" not in text
    assert "paid_share" not in text and "tariffs" not in text and "compare_parking_targets.py" in text
    for needle in ("calibrate_garage_decay.py on 2026-10-07", "sha256=", "ASSUMPTIONS G1 to G3 and G1-c", "Universe caveat",
                   "bs_zone_ia, bs_zone_ib", "4 activities, 4 of them with at least one priced garage",
                   "garage_large_lot / (garage_large_lot + street) = 0.2718 / (0.2718 + 0.7282)", "no validation",
                   "Commuter universe: the 2 work and education activities", "without parkingFree",
                   "1 more carry parkingFree and are left out",
                   "share_garage_large_lot_paid / (share_garage_large_lot_paid + share_street_paid) = 0.04 / (0.04 + 0.06)",
                   "the payers only", "like-for-like",
                   "class bs_zentrum", "Independent check", "class 03103", "wob_tarifzone_1, wob_tarifzone_2, wob_tarifzone_3",
                   "2 work and education activities", "calibration target"):
        assert needle in text, needle
    assert len(re.findall(r"sha256=[0-9a-f]{64}", text)) == 5   # plans, zones, garages and the two SrV tables
    # the two release values are named in the header with the config keys and the test that requires their equality
    assert "parking_garage_decay_m" in text and "parking_garage_decay_commute_m" in text
    assert "decay_commute_length_m" in text
    values = cal.read_calibration_table(out)
    assert values["decay_length_m"] == result["decay_length_m"] and values["universe_activities"] == 4
    assert values["decay_commute_length_m"] == result["decay_commute_length_m"]
    assert values["commuter_universe_activities"] == 2 and values["commuter_activities_excluded_parking_free"] == 1
    assert values["target_garage_probability"] == pytest.approx(0.2718, abs=1e-6)
    assert values["target_commuter_garage_probability"] == pytest.approx(0.4, abs=1e-5)
    assert values["check_wolfsburg_commuter_activities"] == 2
    assert values["check_wolfsburg_commuter_reference_share"] == pytest.approx(0.6, abs=1e-5)
    assert not [name for name in values if name.startswith("check_commuter")]   # the bs_zentrum share is a target now
    assert any("with a priced garage within 1000 m: 4/4" in message for message in caplog.messages)
    assert any("commuter universe 2" in message and "excluded as parkingFree 1" in message for message in caplog.messages)
    assert any("Wolfsburg check" in message and "no validation" in message for message in caplog.messages)


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


def test_a_commuter_universe_without_activities_or_a_wolfsburg_check_without_activities_fails_and_writes_nothing(
        cal, inputs, tmp_path):
    # only free commuters in Ia/Ib: the H2 universe is empty (every commuter leaves the denominator)
    plans = tmp_path / "free_commuters.xml"
    plans.write_text(
        '<population><person id="a"><plan selected="yes"><activity type="home" x="590000" y="5780000"/><leg mode="car"/>'
        '<activity type="shop" x="603100" y="5790100"/><leg mode="car"/>'
        '<activity type="work" x="603100" y="5790100"><attributes><attribute name="parkingFree" '
        'class="java.lang.Boolean">true</attribute></attributes></activity></plan></person></population>', encoding="utf-8")
    out = tmp_path / "table.csv"
    with pytest.raises(ValueError, match="commuter universe is empty"):
        cal.run(**{**inputs, "plans": plans}, out_path=out)
    assert not out.exists()
    # a commuter in Ia but nobody in the Wolfsburg zones: the check has no universe, which is refused, not skipped
    plans.write_text(
        '<population><person id="a"><plan selected="yes"><activity type="home" x="590000" y="5780000"/><leg mode="car"/>'
        '<activity type="shop" x="603100" y="5790100"/><leg mode="car"/>'
        '<activity type="work" x="603100" y="5790100"/></plan></person></population>', encoding="utf-8")
    with pytest.raises(ValueError, match="Wolfsburg check has no universe"):
        cal.run(**{**inputs, "plans": plans}, out_path=out)
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
    # a table written before spec Amendment H has no commuter decay: refused with the name of the missing row
    path.write_text("quantity,value,unit,note\ndecay_length_m,300,m,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decay_commute_length_m"):
        cal.read_calibration_table(path)
    path.write_text("quantity,value,unit,note\ndecay_length_m,300,m,x\ndecay_commute_length_m,0,m,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="decay_commute_length_m"):
        cal.read_calibration_table(path)
    path.write_text("quantity,value,unit,note\ndecay_length_m,300,m,x\ndecay_length_m,301,m,x\n", encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        cal.read_calibration_table(path)


def test_the_script_does_not_hard_code_the_target(cal):
    # the SrV shares are read from the table: neither 0.708 nor 0.2537 nor 0.736 appears as a number in the script
    text = SCRIPT.read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    for number in ("0.708", "0.2537", "0.736", "0.7362", "0.464", "0.4636", "0.1923", "0.2225", "0.1075", "0.0608", "0.639",
                   "0.0965", "0.0456", "0.0225", "0.0080", "0.679", "0.738",
                   "0.6387"):
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


# ----------------------------------------------------------------------- release-path guard of a filtered run (G1)


def test_run_refuses_a_filtered_calibration_under_the_release_table_name(cal, inputs, tmp_path):
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    release_name = Path(cal.TABLE_RELATIVE).name
    with pytest.raises(ValueError, match="sensitivity number"):
        cal.run(out_path=tmp_path / release_name, facility_kinds=("surface_lot",), **mixed)
    assert not (tmp_path / release_name).exists()
    # an unfiltered run may write the release name, a filtered one another name
    cal.run(out_path=tmp_path / release_name, **mixed)
    cal.run(out_path=tmp_path / "garage_only_sensitivity.csv", facility_kinds=("surface_lot",), **mixed)


def test_the_command_line_refuses_a_filtered_run_without_out_or_onto_the_release_path(cal, tmp_path):
    data = tmp_path / "data"
    release = data / cal.TABLE_RELATIVE
    release.parent.mkdir(parents=True)
    base = ["--plans", str(tmp_path / "plans.xml"), "--data-path", str(data), "--facility-kinds", "surface_lot"]
    with pytest.raises(SystemExit) as no_out:   # --out omitted: the default is the release table
        cal.main(base)
    assert no_out.value.code == 2
    with pytest.raises(SystemExit) as onto_release:
        cal.main(base + ["--out", str(release)])
    assert onto_release.value.code == 2
    with pytest.raises(SystemExit):   # the same path spelled differently
        cal.main(base + ["--out", str(release.parent / ".." / release.parent.name / release.name)])
    assert not release.exists()


# ----------------------------------------------- the maximum garage distance comes from the config, not the code (M6)


def _config_with_distance(tmp_path, text):
    path = tmp_path / "config.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_configured_maximum_distance_is_read_from_the_base_config(cal):
    assert cal.read_configured_max_distance_m() == 1000.0   # configs/base_bs.yml parking_garage_max_distance_m


def test_the_configured_maximum_distance_is_the_value_of_the_config_file_not_the_code_constant(cal, tmp_path):
    config = _config_with_distance(tmp_path, "config:\n  parking_garage_max_distance_m: 750.0\n")
    assert cal.read_configured_max_distance_m(config) == 750.0


@pytest.mark.parametrize("text", [
    "config:\n  parking_zones_enabled: true\n",                        # key missing
    "config:\n  parking_garage_max_distance_m: 0\n",                   # not positive
    "config:\n  parking_garage_max_distance_m: yes\n",                 # a boolean is no distance
    "config:\n  parking_garage_max_distance_m: far\n",                 # not a number
    "stages: []\n",                                                    # no config section
], ids=["missing", "zero", "boolean", "text", "no-config-section"])
def test_an_unusable_configured_maximum_distance_fails_naming_the_key(cal, tmp_path, text):
    with pytest.raises(ValueError, match="parking_garage_max_distance_m"):
        cal.read_configured_max_distance_m(_config_with_distance(tmp_path, text))


def test_run_takes_the_maximum_distance_of_the_config_by_default_and_records_it(cal, inputs, tmp_path):
    # 750 m is another value than the code constant (1000 m); the garage 300 m and 500 m away stays in range
    config = _config_with_distance(tmp_path, "config:\n  parking_garage_max_distance_m: 750.0\n")
    result = cal.run(out_path=None, config_path=config, generated_on="2026-10-07", **inputs)
    assert "garage_max_distance_m,750,m,ASSUMPTION G2" in result["text"]
    assert "within 750 m" in result["text"]
    explicit = cal.run(out_path=None, config_path=config, max_distance_m=900.0, generated_on="2026-10-07", **inputs)
    assert "garage_max_distance_m,900,m,ASSUMPTION G2" in explicit["text"]   # an explicit value still wins


def test_the_command_line_default_of_the_maximum_distance_is_the_config_key(cal, monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cal, "run", lambda **options: captured.update(options))
    cal.main(["--plans", str(tmp_path / "plans.xml"), "--data-path", str(tmp_path / "data")])
    assert captured["max_distance_m"] is None   # run() resolves it from the config key; no code constant is the default
    parser_help = SCRIPT.read_text(encoding="utf-8")
    assert "parking_garage_max_distance_m" in parser_help
    assert 'default=cost.GARAGE_MAX_DISTANCE_M' not in parser_help.split("def main", 1)[1]


# ------------------------------------------- the target contains surface lots, the Braunschweig option set has none (F3)


def test_the_table_header_names_the_option_set_and_that_the_target_contains_surface_lots(cal, inputs, tmp_path):
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    text = cal.run(out_path=None, **mixed)["text"]
    header = [line for line in text.splitlines() if line.startswith("#")]
    option_line = next(line for line in header if line.startswith("# Option set:"))
    assert "1 garage, 1 surface_lot" in option_line
    assert "large surface lots" in option_line and "lambda up" in option_line
    # an option set without a surface lot is called out as such (the Braunschweig dataset has no surface lot)
    garages_only = cal.run(out_path=None, **_with_kinds(inputs, tmp_path, ["garage", "garage"]))["text"]
    assert "2 garage, 0 surface_lot" in garages_only and "holds no surface lot" in garages_only


def test_the_facility_kind_option_says_it_matters_only_where_surface_lots_lie_within_the_maximum_distance(cal):
    text = SCRIPT.read_text(encoding="utf-8")
    assert "matters only where surface lots lie within" in text.split("def main", 1)[1]
    documentation = text.split("def main", 1)[0]
    # before Task 4j the Braunschweig option set held no surface lot, so the garages-only run was vacuous there; since
    # Task 4j (spec Amendment I) it holds the large lots, which the text must not deny any more
    assert "vacuous" in documentation and "since Task 4j" in documentation
    assert "holds 12 garages and no surface lot" not in documentation
    assert "'garage' is vacuous there" not in text.split("def main", 1)[1]


# ------------------------------------------------------------------ the free option and the paid-only measure (I6)


def _fixture_garages():
    """The 19 rows of the garage fixture of the golden parking cases (one of them, fx_g17_free, is the free schedule)."""
    return pg.load_garages(FIXTURES / "parking_garages_fixture.geojson", allow_legacy_columns=True)


@pytest.mark.parametrize("tiers, bands, expected", [
    (None, "0- free", True),                                     # the free schedule: one open free band, no tiers
    (None, None, False),                                         # the single-window form
    (None, "0-20 free; 20-120 total 1.00; 120- 5.00/60", False),    # a free first band that something follows
    (None, "0-30 free", False),                                  # a closed free band is a grace period, not a car park
    ("06:00-18:00 1.00/60", "0-30 free", False),                 # tiers with a grace period
    ("06:00-18:00 1.00/60", "0- free", False),                   # tiers are never free (and the validator refuses this)
    (None, "", False), (float("nan"), None, False),
    (float("nan"), "0- free", True), ("", "0- free", True),      # an empty tiers cell is no tiers
    # the emptiness rule is the one of the garage module (zones._is_set, as garages.coverage counts priced_free): a blank
    # string is a value there, so a blank tiers cell is tiers (the loader turns a blank cell into None before this point)
    ("   ", "0- free", False),
])
def test_a_free_option_is_the_free_schedule_and_nothing_else(cal, tiers, bands, expected):
    assert cal.is_free_option(tiers, bands) is expected


def test_the_free_marker_reads_the_schedule_of_the_rows_and_never_the_name_or_the_rate(cal):
    frame = _fixture_garages()
    paid = cal.paid_option_flags(frame)
    assert paid.dtype == bool and len(paid) == len(frame) == 38
    # of the 38 fixture rows only fx_g17_free and fx_g22_free (golden P02, spec v3) are the free schedule; the grace periods, the
    # free first band of fx_g08_bands and the tiers with a grace period are PAID options
    assert frame.loc[~paid, "garage_id"].tolist() == ["fx_g17_free", "fx_g22_free"]
    # a name that says free does not free a paid option; a name that says paid does not charge a free one
    renamed = frame.copy()
    renamed.loc[renamed["garage_id"] == "fx_g01_core", "name"] = "Kostenlos free gratis"
    renamed.loc[renamed["garage_id"] == "fx_g17_free", "name"] = "Teures Parkhaus 99 EUR"
    assert cal.paid_option_flags(renamed).tolist() == paid.tolist()
    # a rate of zero does not free an option either: only the schedule does (the validator refuses a zero rate anyway)
    zero_rate = frame.copy()
    zero_rate.loc[zero_rate["garage_id"] == "fx_g01_core", "garage_hourly_rate_eur"] = 0.0
    assert cal.paid_option_flags(zero_rate).tolist() == paid.tolist()
    # the id of a row does not decide either (a marker by id text would follow the ids): swap the ids of the free row and a paid
    # row, the flags stay with the schedules and the free row now carries the paid row's id
    ids = frame["garage_id"].tolist()
    free_position, paid_position = ids.index("fx_g17_free"), ids.index("fx_g01_core")
    ids[free_position], ids[paid_position] = ids[paid_position], ids[free_position]
    rotated = frame.copy()
    rotated["garage_id"] = ids
    assert cal.paid_option_flags(rotated).tolist() == paid.tolist()
    assert rotated.loc[~cal.paid_option_flags(rotated), "garage_id"].tolist() == ["fx_g01_core", "fx_g22_free"]


def test_the_free_marker_is_the_one_the_garage_module_counts_as_priced_free(cal):
    # garages.coverage counts priced_free with is_free_schedule(parse_duration_bands(...)): the script marks the same rows
    frame = _fixture_garages()
    assert int((~cal.paid_option_flags(frame)).sum()) == pg.coverage(frame)["priced_free"] == 2


def test_the_paid_only_share_is_the_choice_share_of_the_paid_options_among_paid_options_and_the_street(cal):
    # One destination; two paid options at 300 m and 700 m and one free option at 200 m; lambda 400 m:
    # w = exp(-0.75) = 0.4723665527 (paid), exp(-1.75) = 0.1737739435 (paid), exp(-0.5) = 0.6065306597 (free);
    # W (all options) = 1.2526711559, W_paid = 0.6461404962.
    distances = np.array([[300.0, 700.0, 200.0]])
    paid = np.array([True, True, False])
    weights = np.exp(-distances[0] / 400.0)
    # the model's choice probabilities: street 1 / (1 + W), option i w_i / (1 + W)
    p_street, p_option = 1.0 / (1.0 + weights.sum()), weights / (1.0 + weights.sum())
    # the SrV quantity: paid garage / (paid garage + paid street); the free option is a free parker and leaves both
    paid_share_from_probabilities = p_option[paid].sum() / (p_option[paid].sum() + p_street)
    assert paid_share_from_probabilities == pytest.approx(0.6461404962 / 1.6461404962, abs=1e-9)
    # the form of the script: the same share, W_paid / (1 + W_paid), reached by dropping the free option (1 + W cancels)
    paid_measure = cal.measure_distances(distances, paid, cal.MEASURE_PAID_OPTIONS)
    assert paid_measure.shape == (1, 2)
    assert cal.mean_garage_probability(paid_measure, 400.0) == pytest.approx(paid_share_from_probabilities, abs=1e-12)
    assert cal.mean_garage_probability(paid_measure, 400.0) == pytest.approx(0.39251844, abs=1e-8)
    # the payment-inclusive measure counts every option: W / (1 + W) = 1.2526711559 / 2.2526711559 = 0.55610
    every = cal.measure_distances(distances, paid, cal.MEASURE_ALL_OPTIONS)
    assert every.shape == (1, 3)
    assert cal.mean_garage_probability(every, 400.0) == pytest.approx(1.2526711559 / 2.2526711559, abs=1e-9)
    assert cal.mean_garage_probability(every, 400.0) > cal.mean_garage_probability(paid_measure, 400.0)
    with pytest.raises(ValueError, match="measure"):
        cal.measure_distances(distances, paid, "free_only")


def test_the_paid_only_measure_without_a_paid_option_has_no_probability_instead_of_a_silent_one(cal):
    # only a free option exists: the paid-only share is 0 / (0 + street) = 0 and no target above 0 is reachable
    distances = np.array([[200.0], [300.0]])
    paid = np.array([False])
    measure = cal.measure_distances(distances, paid, cal.MEASURE_PAID_OPTIONS)
    assert measure.shape == (2, 0) and cal.mean_garage_probability(measure, 400.0) == 0.0
    with pytest.raises(ValueError, match="unreachable"):
        cal.calibrate_decay(measure, 0.3)


def test_the_option_reach_counts_the_free_options_within_the_maximum_distance_of_a_universe(cal):
    # columns: paid, paid, free; D_max 1000 m
    distances = np.array([[300.0, 1500.0, 900.0],      # a paid and the free option in range
                          [1200.0, 1100.0, 1001.0],    # nothing in range (1001 m is beyond D_max)
                          [1500.0, 400.0, 1500.0],     # one paid option in range
                          [1500.0, 1500.0, 500.0],     # only the free option in range
                          [1000.0, 1500.0, 1500.0]])   # exactly at D_max counts
    paid = np.array([True, True, False])
    reach = cal.option_reach(distances, paid, 1000.0)
    assert reach.activities == 5
    assert reach.with_option_in_range == 4            # rows 0, 2, 3, 4
    assert reach.with_paid_option_in_range == 3       # rows 0, 2, 4
    assert reach.free_options_in_range == 1           # the one free option (column 2) reaches rows 0 and 3
    assert reach.with_free_option_in_range == 2
    # no free option at all and an empty option set are zeros, not errors
    none_free = cal.option_reach(distances[:, :2], paid[:2], 1000.0)
    assert (none_free.free_options_in_range, none_free.with_free_option_in_range) == (0, 0)
    assert none_free.with_paid_option_in_range == 3
    empty = cal.option_reach(np.zeros((3, 0)), np.zeros(0, dtype=bool), 1000.0)
    assert (empty.activities, empty.with_option_in_range, empty.free_options_in_range) == (3, 0, 0)
    assert reach.free_option_ids == () and empty.free_option_ids == ()   # no ids given: none named


def test_the_option_reach_names_the_free_options_in_range_sorted_by_id(cal):
    # two paid options (columns 0 and 2) and two free ones (columns 1 and 3), each free option within reach of one activity
    distances = np.array([[1500.0, 400.0, 300.0, 1500.0], [1500.0, 1500.0, 1500.0, 700.0]])
    paid = np.array([True, False, True, False])
    reach = cal.option_reach(distances, paid, 1000.0, option_ids=["p1", "f_b", "p2", "f_a"])
    assert reach.free_options_in_range == 2 and reach.free_option_ids == ("f_a", "f_b")
    out_of_range = cal.option_reach(distances + 2000.0, paid, 1000.0, option_ids=["p1", "f_b", "p2", "f_a"])
    assert out_of_range.free_options_in_range == 0 and out_of_range.free_option_ids == ()
    with pytest.raises(ValueError, match="option ids"):
        cal.option_reach(distances, paid, 1000.0, option_ids=["only_one"])


def _option_set(inputs, tmp_path, rows, points, kinds=None, name="options.geojson"):
    """The synthetic garages of ``inputs`` replaced by the given rows of the garage fixture, moved to ``points``."""
    frame = _fixture_garages().iloc[rows].copy()
    frame["geometry"] = [Point(*point) for point in points]
    if kinds is not None:
        frame["facility_kind"] = kinds
    path = tmp_path / name
    pg.write_garages(gpd.GeoDataFrame(frame, geometry="geometry", crs="EPSG:25832"), path)
    return {**inputs, "garages_path": path}


#: The near paid garage (300 m from the Ia activities, 500 m from the Ib ones), the far paid garage (out of range of every
#: activity) and a FREE lot 200 m south of the Ia activities (fixture rows 0, 1 and 17).
NEAR_GARAGE, FAR_GARAGE, FREE_LOT = (603100.0, 5790400.0), (603100.0, 5792000.0), (603100.0, 5789900.0)


def _share(distances_m, decay_m):
    """W / (1 + W) with W the sum of exp(-d / decay) over the distances of one activity (all options counted)."""
    weights = sum(math.exp(-distance / decay_m) for distance in distances_m)
    return weights / (1.0 + weights)


def test_a_free_option_changes_lambda_but_not_lambda_c_nor_the_wolfsburg_check(cal, inputs, tmp_path, caplog):
    plain = cal.run(out_path=None, **inputs)
    with_free = _option_set(inputs, tmp_path, [0, 1, 17], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT], ["garage", "garage",
                                                                                              "surface_lot"])
    with caplog.at_level("INFO", logger="calibrate_garage_decay"):
        result = cal.run(out_path=None, **with_free)
    # E5 (payment-inclusive): the free lot is one more option, so the same target needs a SHORTER decay. Independent check at
    # the result: Ia activities 300 m (near) and 200 m (free lot) away, Ib activities 500 m and sqrt(400^2 + 200^2) = 447.21 m;
    # the mean over the four activities (two each) is the target 0.2718 within the tolerance
    assert result["decay_length_m"] < plain["decay_length_m"] - 20.0
    decay = result["decay_length_m"]
    mean = 0.5 * _share([300.0, 200.0], decay) + 0.5 * _share([500.0, math.hypot(400.0, 200.0)], decay)
    assert mean == pytest.approx(0.2718, abs=0.0006)
    # H2 and H3b (paid-only): the free option leaves numerator and denominator, so nothing changes
    assert result["decay_commute_length_m"] == plain["decay_commute_length_m"]
    assert result["commuter_achieved"] == plain["commuter_achieved"]
    assert result["wolfsburg_mean"] == plain["wolfsburg_mean"]
    # the free lot lies within D_max of every universe and is counted per universe (fallback transparency)
    assert (result["free_options_used"], result["priced_options_used"]) == (1, 3)
    assert result["universe_free_option_ids"] == result["commuter_free_option_ids"] == ("fx_g17_free",)
    assert result["wolfsburg_free_option_ids"] == ("fx_g17_free",)
    assert (result["universe_free_options_in_range"], result["universe_activities_with_free_option"]) == (1, 4)
    assert (result["commuter_free_options_in_range"], result["commuter_activities_with_free_option"]) == (1, 2)
    assert (result["wolfsburg_free_options_in_range"], result["wolfsburg_activities_with_free_option"]) == (1, 2)
    assert (result["commuter_with_paid_option_in_range"], result["wolfsburg_with_paid_option_in_range"]) == (2, 2)
    assert (plain["free_options_used"], plain["universe_free_options_in_range"]) == (0, 0)
    messages = " | ".join(caplog.messages)
    assert "free options within 1000 m: 1 distinct" in messages and "reach 4/4" in messages
    assert "paid options within 1000 m" in messages


def test_the_paid_only_measure_is_not_vacuous_a_paid_option_at_the_same_place_does_change_lambda_c(cal, inputs, tmp_path):
    plain = cal.run(out_path=None, **inputs)
    # fixture row 8 is a PAID banded schedule (it starts with a free band, which is no free car park)
    control = _option_set(inputs, tmp_path, [0, 1, 8], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT])
    result = cal.run(out_path=None, **control)
    assert result["free_options_used"] == 0 and result["universe_free_options_in_range"] == 0
    assert result["decay_commute_length_m"] < plain["decay_commute_length_m"] - 20.0
    assert result["decay_length_m"] < plain["decay_length_m"] - 20.0
    # independent check of the paid-only measure at the result: both options of the commuters count (300 m and 200 m for the
    # Ia commuter, 500 m and 447.21 m for the Ib commuter), the mean equals the target 0.4 (read from the paid columns)
    decay = result["decay_commute_length_m"]
    mean = 0.5 * _share([300.0, 200.0], decay) + 0.5 * _share([500.0, math.hypot(400.0, 200.0)], decay)
    assert mean == pytest.approx(0.4, abs=0.0006)


def test_a_commuter_universe_without_any_paid_option_fails_naming_the_measure(cal, inputs, tmp_path):
    only_free = _option_set(inputs, tmp_path, [17], [FREE_LOT], ["surface_lot"])
    with pytest.raises(ValueError, match="lambda_c.*PAID-only.*unreachable"):
        cal.run(out_path=None, **only_free)


def test_a_commuter_without_a_paid_option_in_range_counts_zero_and_is_reported_separately(cal, inputs, tmp_path):
    # a paid garage 950 m north of the Ia activities (1,030.8 m from the Ib ones, out of range) and the free lot in reach of
    # both: the Ib commuter has a free option in range but no PAID one, which the paid-only measure counts as probability 0.
    # One commuter in reach of one option reaches a mean of at most 0.25, so the commuter target of this test is lowered to
    # 0.01 / (0.01 + 0.09) = 0.1 (the share of the Ia commuter alone: 0.5 * w / (1 + w) = 0.1 for w = 0.25).
    inputs["commute_path"].write_text(
        "# synthetic\nworkplace_class,level,n_unweighted,n_eff,share_garage_large_lot_paid,share_street_paid\n"
        "bs_zentrum,class,221,104.4,0.01,0.09\n03103,class,691,162.7,0.06,0.04\n", encoding="utf-8")
    options = _option_set(inputs, tmp_path, [0, 17], [(603100.0, 5791050.0), FREE_LOT], ["garage", "surface_lot"])
    result = cal.run(out_path=None, **options)
    assert result["commuter_target"] == pytest.approx(0.1, abs=1e-12)
    assert result["commuter_universe_activities"] == 2
    assert result["commuter_with_paid_option_in_range"] == 1       # only the Ia commuter has a paid option in range
    assert result["commuter_with_garage_in_range"] == 2            # but both have an option (the free lot)
    assert result["commuter_activities_with_free_option"] == 2
    # the paid-only mean is 0.5 * w / (1 + w) with w = exp(-950 / lambda_c): independent check at the result
    weight = math.exp(-950.0 / result["decay_commute_length_m"])
    assert 0.5 * weight / (1.0 + weight) == pytest.approx(0.1, abs=0.0006)
    # w = 0.25 at 685.3 m; the tolerance 0.0005 of the mean allows about 3 m at the slope of 0.00016 per metre
    assert result["decay_commute_length_m"] == pytest.approx(950.0 / math.log(4.0), abs=4.0)
    # the Wolfsburg activities (1,180 m and 1,140 m from the paid garage, 728 m and 743 m from the free lot) have no paid
    # option in range: the paid-only check reads 0, a number and no error (the check is no calibration)
    assert result["wolfsburg_with_paid_option_in_range"] == 0 and result["wolfsburg_mean"] == 0.0
    assert result["wolfsburg_activities_with_free_option"] == 2


# ----------------------------------------------------------------- the table says which measure each target uses (I6)


def test_the_table_header_and_rows_say_which_measure_each_target_uses_and_count_the_free_options(cal, inputs, tmp_path):
    with_free = _option_set(inputs, tmp_path, [0, 1, 17], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT], ["garage", "garage",
                                                                                              "surface_lot"])
    out = tmp_path / "parking_garage_decay_calibration_2026.csv"
    result = cal.run(out_path=out, generated_on="2026-10-14", **with_free)
    text = out.read_bytes().decode("ascii")
    header = [line for line in text.splitlines() if line.startswith("#")]
    measures = next(line for line in header if line.startswith("# Measures"))
    for needle in ("spec Amendment I6", "lambda: payment-inclusive", "counts EVERY off-street option", "sum(w) / (1 + sum(w))",
                   "lambda_c and the Wolfsburg check: PAID-only", "sum(w_paid) / (1 + sum(w_paid))",
                   "free option", "free schedule", "cost 0 at every stay duration", "free parker",
                   "leaves numerator and denominator", "(1 + W) cancels", "never a name or a rate"):
        assert needle in measures, needle
    assert "I6" in header[1]
    # every target line names its measure
    target_lambda = next(line for line in header if line.startswith("# Target of lambda:"))
    target_commuter = next(line for line in header if line.startswith("# Target of lambda_c:"))
    assert "payment-inclusive measure (every option counts)" in target_lambda
    assert "PAID-only measure (the free option leaves numerator and denominator)" in target_commuter
    wolfsburg = next(line for line in header if "Wolfsburg: mean" in line)
    assert "PAID-only measure" in wolfsburg
    # the free options within D_max are stated per universe
    universe_line = next(line for line in header if line.startswith("# Universe of lambda:"))
    commuter_line = next(line for line in header if line.startswith("# Commuter universe:"))
    assert "Free options within 1000 m of this universe: 1 distinct (fx_g17_free), reaching 4 of its 4 activities" in universe_line
    assert "Free options within 1000 m of this universe: 1 distinct (fx_g17_free), reaching 2 of its 2 activities" in commuter_line
    # the composition of every universe by purpose is stated (a purpose that does not belong there would be visible)
    assert "By purpose: leisure 1, other 1, shop 2." in universe_line
    assert "By purpose: education 1, work 1." in commuter_line
    assert "2 of 2 activities with at least one PAID option within 1000 m" in commuter_line
    assert "reaching 2 of its 2 activities" in wolfsburg and "1 distinct (fx_g17_free)" in wolfsburg
    assert "By purpose: education 1, work 1." in wolfsburg
    option_line = next(line for line in header if line.startswith("# Option set:"))
    assert "2 garage, 1 surface_lot" in option_line and "1 of the 3 options is free" in option_line
    values = cal.read_calibration_table(out)
    assert values["priced_free_options_used"] == 1 and values["priced_garages_used"] == 3
    assert values["universe_free_options_within_d_max"] == 1 and values["universe_activities_with_free_option_in_range"] == 4
    assert values["commuter_universe_free_options_within_d_max"] == 1
    assert values["commuter_universe_activities_with_free_option_in_range"] == 2
    assert values["commuter_universe_activities_with_paid_option_in_range"] == 2
    assert values["check_wolfsburg_free_options_within_d_max"] == 1
    assert values["check_wolfsburg_activities_with_free_option_in_range"] == 2
    assert values["check_wolfsburg_commuter_activities_with_paid_option_in_range"] == 2
    # the rows of the earlier tables keep their name and meaning (all options)
    assert values["universe_activities_with_garage_in_range"] == 4 == result["with_garage_in_range"]
    assert values["commuter_universe_activities_with_garage_in_range"] == 2
    notes = {line.split(",")[0]: line for line in text.splitlines() if not line.startswith("#")}
    assert "every option" in notes["target_garage_probability"]
    assert "paid-only" in notes["target_commuter_garage_probability"]
    assert "paid-only" in notes["achieved_mean_commuter_garage_probability"]
    assert "paid-only" in notes["check_wolfsburg_commuter_mean_garage_probability"]


def test_the_table_states_that_the_decays_are_bisection_results_within_the_tolerance_and_no_exact_roots(cal, inputs):
    # the search stops at the first midpoint whose mean is within the tolerance of the target, so a decay is not the exact root
    # and option-set differences of the order of the tolerance (divided by the local slope of the mean) are not resolved,
    # the sensitivities included: ONE sentence in the header, stating the rule and quoting no number of this scenario
    text = cal.run(out_path=None, generated_on="2026-10-14", **inputs)["text"]
    header = [line for line in text.splitlines() if line.startswith("#")]
    search = next(line for line in header if line.startswith("# Search for lambda:"))
    for needle in ("first bisection midpoint whose mean lies within the tolerance of the target", "not the exact root",
                   "of the order of the tolerance divided by the local slope of the mean", "not resolved",
                   "the sensitivities below"):
        assert needle in search, needle
    assert text.count("not the exact root") == 1 and text.count("first bisection midpoint") == 1
    assert "0.7 m" not in text and "0.4 m" not in text   # the rule, not the local slopes of one scenario


def test_a_universe_that_the_options_barely_reach_is_warned_about_and_a_covered_one_is_not(cal, inputs, tmp_path, caplog):
    # primary-method coverage (fallback transparency): a universe in which fewer than MINIMUM_REACH_SHARE_WARNING of the
    # activities have an option of the measure within D_max warns, with its name and the numbers
    assert cal.MINIMUM_REACH_SHARE_WARNING == 0.5
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        cal.run(out_path=None, **inputs)   # the near garage reaches every activity of every universe
    assert not [message for message in caplog.messages if "within reach" in message]
    # a garage 400 m west of the Ia activities: 800 m from the Ib ones, 1,100 m from the Wolfsburg activities (out of range),
    # so the Wolfsburg universe (2 activities) has no paid option in reach, while the calibrated universes are covered
    west = _option_set(inputs, tmp_path, [0, 1], [(602700.0, 5790100.0), FAR_GARAGE], name="west.geojson")
    caplog.clear()
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        result = cal.run(out_path=None, **west)
    warned = [message for message in caplog.messages if "within reach" in message]
    assert len(warned) == 1 and "Wolfsburg" in warned[0] and "0/2" in warned[0] and "50 %" in warned[0]
    assert result["wolfsburg_with_paid_option_in_range"] == 0


# ------------------------------------------------------- freight agents are no residents (the person-travel convention)


FREIGHT_PERSON = (
    '<person id="freight_internal_1"><plan selected="yes">'
    '<activity type="freight_start" x="603100" y="5790100" end_time="08:00:00"/><leg mode="truck"/>'
    '<activity type="freight_end" x="603500" y="5790100"/></plan></person>')


def test_freight_agents_are_left_out_of_the_universes_and_counted_in_the_header(cal, inputs, tmp_path, caplog):
    # braunschweig.analysis.freight_filter: freight agents (ids freight_*) are trip creators, not residents, and the SrV
    # shares are about residents. A freight stop inside Ia or Ib would be a destination "other than home, work and education"
    # of the E5 universe (two extra activities here: 4 -> 6) without the exclusion.
    plans = tmp_path / "plans_with_freight.xml"
    fixture = (FIXTURES / "calibration_plans_fixture.xml").read_text(encoding="utf-8")
    plans.write_text(fixture.replace("</population>", FREIGHT_PERSON + "</population>"), encoding="utf-8")
    plain = cal.run(out_path=None, generated_on="2026-10-14", **inputs)
    with caplog.at_level("INFO"):
        result = cal.run(out_path=None, generated_on="2026-10-14", **{**inputs, "plans": plans})
    assert result["universe_activities"] == plain["universe_activities"] == 4
    assert result["freight_agents_excluded"] == 1 and plain["freight_agents_excluded"] == 0
    assert result["persons"] == plain["persons"] == 14      # the 14 persons of the fixture, the freight agent not counted
    assert result["decay_length_m"] == plain["decay_length_m"]
    assert "(14 persons; 1 freight agents with ids freight_* excluded" in result["text"]
    assert "(14 persons; 0 freight agents with ids freight_* excluded" in plain["text"]
    # the shared filter logs what it counts: the two main activities (rows) of the one freight agent
    assert any("excluded 2 rows of 1 freight agents" in message for message in caplog.messages)
    assert any("1 freight agents" in message and "14 persons" in message for message in caplog.messages)


def test_the_reach_warning_threshold_is_a_log_only_option_with_a_documented_default(cal, inputs, tmp_path, caplog):
    # a universe with fewer than the threshold of its activities in reach of an option of its measure is warned about: 0 never
    # warns, the default 0.5 warns below half, a higher threshold also warns for a half-covered universe. Only the log changes.
    west = _option_set(inputs, tmp_path, [0, 1], [(602700.0, 5790100.0), FAR_GARAGE], name="west_option.geojson")
    messages = {}
    for threshold in (0.0, 0.5, 1.0):
        caplog.clear()
        with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
            result = cal.run(out_path=None, minimum_reach_share_warning=threshold, **west)
        messages[threshold] = [m for m in caplog.messages if "within reach" in m]
        assert result["wolfsburg_with_paid_option_in_range"] == 0   # the numbers do not depend on the threshold
    assert messages[0.0] == []
    assert len(messages[0.5]) == 1 and "Wolfsburg" in messages[0.5][0] and "50 %" in messages[0.5][0]
    assert len(messages[1.0]) == 1 and "100 %" in messages[1.0][0]   # the covered universes reach 100 %, not below it
    # the default is the documented constant
    caplog.clear()
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        cal.run(out_path=None, **west)
    assert len([m for m in caplog.messages if "within reach" in m]) == 1 and cal.MINIMUM_REACH_SHARE_WARNING == 0.5
    for bad in (-0.1, 1.5, float("nan"), True, "half"):
        with pytest.raises(ValueError, match="minimum_reach_share_warning"):
            cal.run(out_path=None, minimum_reach_share_warning=bad, **inputs)


def test_the_reach_warning_threshold_applies_to_the_sensitivities_too_with_their_label(cal, inputs, tmp_path, caplog):
    # the release dataset covers every universe; the sensitivity option set barely reaches the Wolfsburg universe: its warning
    # carries the label and follows the threshold that the run was given
    west = _option_set(inputs, tmp_path, [0, 1], [(602700.0, 5790100.0), FAR_GARAGE],
                       name="west_sensitivity.geojson")["garages_path"]
    caplog.clear()
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        cal.run(out_path=None, sensitivity_garages={"west_sensitivity": west}, **inputs)
    warned = [m for m in caplog.messages if "within reach" in m]
    assert len(warned) == 1 and warned[0].startswith("[garage-decay] sensitivity west_sensitivity: Wolfsburg check universe")
    caplog.clear()
    with caplog.at_level("WARNING", logger="calibrate_garage_decay"):
        cal.run(out_path=None, sensitivity_garages={"west_sensitivity": west}, minimum_reach_share_warning=0.0, **inputs)
    assert [m for m in caplog.messages if "within reach" in m] == []


def test_the_command_line_takes_the_reach_warning_threshold_and_refuses_a_value_outside_zero_to_one(cal, monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cal, "run", lambda **options: captured.update(options))
    base = ["--plans", str(tmp_path / "plans.xml"), "--data-path", str(tmp_path / "data")]
    cal.main(base)
    assert captured["minimum_reach_share_warning"] == cal.MINIMUM_REACH_SHARE_WARNING == 0.5
    cal.main(base + ["--minimum-reach-share-warning", "0.2"])
    assert captured["minimum_reach_share_warning"] == 0.2
    for bad in ("1.5", "-0.1", "nan", "half"):
        with pytest.raises(SystemExit) as refused:
            cal.main(base + ["--minimum-reach-share-warning", bad])
        assert refused.value.code == 2
    text = SCRIPT.read_text(encoding="utf-8")
    help_text = text.split("def main", 1)[1]
    assert "--minimum-reach-share-warning" in help_text and "not a scientific parameter" in help_text   # said in the help


# I2: the exact condition of the equality of the per-activity mean and the pooled ratio
def test_the_documentation_states_the_exact_condition_for_the_equality_of_the_mean_and_the_pooled_ratio(cal):
    documentation = " ".join(SCRIPT.read_text(encoding="utf-8").split("def main", 1)[0].split())
    assert "when no free option is in range of any activity of the universe" in documentation
    assert "or where every activity sees the same options" not in documentation
    # the equality it states, checked numerically: without a free option in range the per-activity mean of W / (1 + W)
    # equals sum(p_paid) / sum(p_paid + p_street); with one free option in reach of part of the universe the two differ
    weights = np.array([0.5, 1.5, 3.0])                      # W of three activities, all options paid: W_paid = W
    p_paid, p_street = weights / (1.0 + weights), 1.0 / (1.0 + weights)
    pooled = p_paid.sum() / (p_paid + p_street).sum()
    assert pooled == pytest.approx(np.mean(weights / (1.0 + weights)), abs=1e-12)
    paid_weights = np.array([0.5, 1.5, 1.0])                 # the third activity has a free option of weight 2 in reach
    total = np.array([0.5, 1.5, 3.0])
    pooled_with_free = (paid_weights / (1.0 + total)).sum() / ((paid_weights + 1.0) / (1.0 + total)).sum()
    assert abs(pooled_with_free - np.mean(paid_weights / (1.0 + paid_weights))) > 1e-3


# ---------------------------------------------------------------- the sensitivities of the option set (I6, no release value)


def _dataset_files(inputs, tmp_path):
    """The release dataset of ``inputs`` and two other option sets: with a FREE lot (3 options) and with a PAID lot."""
    free_lot = _option_set(inputs, tmp_path, [0, 1, 17], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT],
                           ["garage", "garage", "surface_lot"], name="with_free_lot.geojson")["garages_path"]
    paid_lot = _option_set(inputs, tmp_path, [0, 1, 8], [NEAR_GARAGE, FAR_GARAGE, FREE_LOT],
                           ["garage", "garage", "surface_lot"], name="with_paid_lot.geojson")["garages_path"]
    return free_lot, paid_lot


def test_the_sensitivities_run_the_same_calibration_on_another_option_set_and_go_into_the_header_only(cal, inputs, tmp_path):
    free_lot, paid_lot = _dataset_files(inputs, tmp_path)
    out = tmp_path / "parking_garage_decay_calibration_2026.csv"
    result = cal.run(out_path=out, generated_on="2026-10-14",
                     sensitivity_garages={"lot_threshold_30": free_lot, "lot_threshold_100": paid_lot}, **inputs)
    release = cal.run(out_path=None, **inputs)
    # the release values are those of the release dataset, whatever the sensitivities say
    assert result["decay_length_m"] == release["decay_length_m"]
    assert result["decay_commute_length_m"] == release["decay_commute_length_m"]
    # each sensitivity equals the direct calibration on its dataset (same plans, targets, D_max and tolerance)
    by_label = {entry["label"]: entry for entry in result["sensitivities"]}
    assert list(by_label) == ["lot_threshold_30", "lot_threshold_100"]
    for label, path in (("lot_threshold_30", free_lot), ("lot_threshold_100", paid_lot)):
        direct = cal.run(out_path=None, **{**inputs, "garages_path": path})
        entry = by_label[label]
        assert entry["decay_length_m"] == direct["decay_length_m"]
        assert entry["decay_commute_length_m"] == direct["decay_commute_length_m"]
        assert entry["achieved"] == direct["achieved"] and entry["commuter_achieved"] == direct["commuter_achieved"]
        assert entry["options"] == 3 and entry["sha256"] == cal.file_sha256(path)
    assert by_label["lot_threshold_30"]["free_options"] == 1 and by_label["lot_threshold_100"]["free_options"] == 0
    assert by_label["lot_threshold_30"]["decay_length_m"] != release["decay_length_m"]
    # the table: header lines labelled sensitivity (never a release value) with the dataset hash; the rows are the release's
    text = out.read_bytes().decode("ascii")
    lines = [line for line in text.splitlines() if line.startswith("# Sensitivity")]
    assert len(lines) == 2
    for line, label, path in zip(lines, ("lot_threshold_30", "lot_threshold_100"), (free_lot, paid_lot)):
        entry = by_label[label]
        assert f"# Sensitivity of the option set (spec Amendment I6; NOT a release value) {label}:" in line
        assert f"sha256={cal.file_sha256(path)}" in line
        assert f"lambda = {entry['decay_length_m']:.2f} m" in line and f"lambda_c = {entry['decay_commute_length_m']:.2f} m" in line
        assert "3 options (2 garage + 1 surface_lot" in line
    assert "1 free" in lines[0] and "0 free" in lines[1]
    assert len(re.findall(r"sha256=[0-9a-f]{64}", text)) == 7   # the five inputs and the two sensitivity datasets
    values = cal.read_calibration_table(out)
    assert values["decay_length_m"] == release["decay_length_m"]
    assert not [name for name in values if "sensitivity" in name or "threshold" in name]
    assert "Sensitivity" not in "\n".join(line for line in text.splitlines() if not line.startswith("#"))


def test_a_run_without_sensitivities_writes_no_sensitivity_line_and_keeps_its_bytes(cal, inputs, tmp_path):
    first = cal.run(out_path=None, generated_on="2026-10-14", **inputs)
    again = cal.run(out_path=None, generated_on="2026-10-14", sensitivity_garages={}, **inputs)
    assert first["text"] == again["text"] and "# Sensitivity" not in first["text"] and first["sensitivities"] == []


def test_a_sensitivity_that_is_the_release_dataset_or_is_badly_named_or_missing_is_refused(cal, inputs, tmp_path):
    release = inputs["garages_path"]
    copy = tmp_path / "copy_of_release.geojson"
    copy.write_bytes(release.read_bytes())
    free_lot, _ = _dataset_files(inputs, tmp_path)
    with pytest.raises(ValueError, match="same SHA-256 as the release dataset"):
        cal.run(out_path=None, sensitivity_garages={"same": copy}, **inputs)
    with pytest.raises(ValueError, match="label"):
        cal.run(out_path=None, sensitivity_garages={"two words": free_lot}, **inputs)
    with pytest.raises(ValueError, match="label"):
        cal.run(out_path=None, sensitivity_garages={"": free_lot}, **inputs)
    with pytest.raises(FileNotFoundError, match="sensitivity dataset lot_threshold_30"):
        cal.run(out_path=None, sensitivity_garages={"lot_threshold_30": tmp_path / "nope.geojson"}, **inputs)
    out = tmp_path / "table.csv"
    with pytest.raises(ValueError):
        cal.run(out_path=out, sensitivity_garages={"same": copy}, **inputs)
    assert not out.exists()   # nothing is written when a sensitivity fails


def test_a_bad_sensitivity_is_refused_before_the_plans_are_read(cal, inputs, tmp_path):
    # the plans (the slow step) are read only after every sensitivity passed its checks: with a plans file that does not exist
    # a bad sensitivity raises ITS error, and the control with a good sensitivity goes on to the plans and raises THEIR error
    options = {**inputs, "plans": tmp_path / "missing_plans.xml"}
    copy = tmp_path / "copy_of_release.geojson"
    copy.write_bytes(inputs["garages_path"].read_bytes())
    free_lot, _ = _dataset_files(inputs, tmp_path)
    with pytest.raises(ValueError, match="same SHA-256 as the release dataset"):
        cal.run(out_path=None, sensitivity_garages={"same": copy}, **options)
    with pytest.raises(ValueError, match="label"):
        cal.run(out_path=None, sensitivity_garages={"two words": free_lot}, **options)
    with pytest.raises(FileNotFoundError, match="sensitivity dataset lot_threshold_30"):
        cal.run(out_path=None, sensitivity_garages={"lot_threshold_30": tmp_path / "nope.geojson"}, **options)
    with pytest.raises(FileNotFoundError, match="missing_plans.xml"):   # the control: the sensitivity is fine, the plans are not
        cal.run(out_path=None, sensitivity_garages={"lot_threshold_30": free_lot}, **options)


def test_a_sensitivity_whose_target_is_unreachable_fails_naming_its_label(cal, inputs, tmp_path):
    # a sensitivity option set with the far garage only: nothing within D_max of any activity
    far = _option_set(inputs, tmp_path, [1], [FAR_GARAGE], name="far_only.geojson")["garages_path"]
    with pytest.raises(ValueError, match="sensitivity far_only.*unreachable"):
        cal.run(out_path=None, sensitivity_garages={"far_only": far}, **inputs)


def test_the_command_line_takes_labelled_sensitivity_datasets(cal, monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(cal, "run", lambda **options: captured.update(options))
    base = ["--plans", str(tmp_path / "plans.xml"), "--data-path", str(tmp_path / "data")]
    cal.main(base)
    assert captured["sensitivity_garages"] is None   # no sensitivity unless asked for
    cal.main(base + ["--sensitivity-garages", f"lot_threshold_30={tmp_path / 'a.geojson'}",
                     "--sensitivity-garages", f"lot_threshold_100={tmp_path / 'b.geojson'}"])
    assert captured["sensitivity_garages"] == {"lot_threshold_30": tmp_path / "a.geojson",
                                               "lot_threshold_100": tmp_path / "b.geojson"}
    for bad in (["--sensitivity-garages", "no_equals_sign"], ["--sensitivity-garages", "=path.geojson"],
                ["--sensitivity-garages", "label="],
                ["--sensitivity-garages", f"a={tmp_path / 'a'}", "--sensitivity-garages", f"a={tmp_path / 'b'}"]):
        with pytest.raises(SystemExit) as refused:
            cal.main(base + bad)
        assert refused.value.code == 2


def test_the_release_path_guard_of_a_filtered_run_stays_with_sensitivities(cal, inputs, tmp_path):
    free_lot, _ = _dataset_files(inputs, tmp_path)
    mixed = _with_kinds(inputs, tmp_path, ["surface_lot", "garage"])
    release_name = Path(cal.TABLE_RELATIVE).name
    with pytest.raises(ValueError, match="sensitivity number"):
        cal.run(out_path=tmp_path / release_name, facility_kinds=("surface_lot",), sensitivity_garages={"x": free_lot}, **mixed)
    assert not (tmp_path / release_name).exists()
