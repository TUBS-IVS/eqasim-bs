"""Tests of the pure inputs of the student flows (issue #140, Phase 4, Task 3).

They run on the committed tables (the origin-destination table T, the level table, the SrV tables
of the day factor) and on small synthetic frames for the failure paths. The constants shared with
Phase 3 are pinned in ``tests/test_student_flows_phase3_pins.py``; the import boundary of the
module is asserted here because it is the reason those pins exist.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration.srv_distance_targets import WOLFSBURG_KREIS, ZGB_KREISE
from braunschweig.calibration.srv_student_reference import AREA_WOLFSBURG
from braunschweig.data.education import student_flows_inputs as SFI

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data" / "braunschweig"
OD_PATH = DATA / "schools" / "student_od_table.csv"
LEVEL_PATH = DATA / "popsim" / "student_level_table.csv"
SRV_DIR = DATA / "srv"
ART_ACADEMY = "Hochsch. f. Bildende Künste Braunschweig | 03101000"


@pytest.fixture(scope="module")
def od() -> pd.DataFrame:
    return SFI.load_od_table(OD_PATH)


# --------------------------------------------------------------------------- the import boundary
def test_module_does_not_load_the_stage_package_or_the_phase3_modules():
    # The run ref holds a code state without the Phase 3 bundle (global constraints, "Import
    # boundary"): the module must load neither the PopulationSim stage package nor the two Phase 3
    # modules that import it. braunschweig.popsim.attributes and .missing ARE loaded, transitively
    # by srv_student_reference -> student_parameters; both files are identical on the run ref and at
    # the run commit, so that import works there (see the report of Task 3).
    code = ("import sys; sys.path.insert(0, %r); "
            "import braunschweig.data.education.student_flows_inputs; "
            "forbidden = ('braunschweig.popsim.stage', 'braunschweig.popsim.student_targets', "
            "'braunschweig.popsim.student_level'); "
            "bad = sorted(m for m in sys.modules if m.startswith(forbidden)); "
            "print(bad); sys.exit(1 if bad else 0)" % str(REPO))
    done = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True)
    assert done.returncode == 0, done.stdout + done.stderr


# --------------------------------------------------------------------------- the table T
def test_region_seed_sums_the_cell_kinds_per_pair(od):
    seed = SFI.region_seed(od)
    assert list(seed.columns) == ["home_ars5", "destination_id", "seed"]
    assert not seed.duplicated(["home_ars5", "destination_id"]).any()
    region = od[od["home_in_region"]]
    assert seed["seed"].sum() == pytest.approx(region["students_persons"].sum())
    # Braunschweig -> Wolfenbuettel has two rows (a commuter and a mover cell) in T.
    pair = region[(region["home_ars5"] == "03101") & (region["destination_id"].str.endswith("| 03158037"))]
    assert len(pair) == 2
    summed = seed[(seed["home_ars5"] == "03101") & (seed["destination_id"] == pair["destination_id"].iloc[0])]
    assert len(summed) == 1
    assert summed["seed"].iloc[0] == pytest.approx(pair["students_persons"].sum())


def test_destination_shares_sum_to_one_and_report_the_empty_destinations(od):
    shares, dropped = SFI.destination_shares(od)
    assert shares.sum() == pytest.approx(1.0)
    assert (shares > 0).all()
    region_sum = od[od["home_in_region"]].groupby("destination_id")["students_persons"].sum()
    expected_dropped = sorted(set(od["destination_id"]) - set(region_sum[region_sum > 0].index))
    assert sorted(dropped) == expected_dropped
    assert set(shares.index) == set(od["destination_id"]) - set(dropped)
    assert shares.loc[region_sum[region_sum > 0].index].to_numpy() == pytest.approx(
        (region_sum[region_sum > 0] / region_sum.sum()).to_numpy())


def test_outside_seed_exists_for_local_sites_only_and_the_art_academy_has_rows(od):
    outside = SFI.outside_seed(od)
    assert list(outside.columns) == ["home_ars5", "destination_id", "seed"]
    assert len(outside) > 0
    local_sites = set(od.loc[od["scope"] == "local", "destination_id"])
    assert set(outside["destination_id"]) <= local_sites
    assert outside["seed"].sum() == pytest.approx(od.loc[~od["home_in_region"], "students_persons"].sum())
    # Task 1b: the art academy takes the origin pattern of TU Braunschweig, so it has outside rows.
    assert outside.loc[outside["destination_id"] == ART_ACADEMY, "seed"].sum() > 0


def test_od_weights_are_read_from_the_header_of_the_committed_table():
    weights = SFI.od_weights(OD_PATH)
    assert set(weights) == {"short", "middle", "long", "max_commute_km"}
    assert 1 >= weights["short"] >= weights["middle"] >= weights["long"] >= 0
    assert weights["max_commute_km"] > 0


def test_od_weights_parse_exact_values_and_raise_when_a_line_is_absent(tmp_path):
    header = ["# Weight rule: weight_rule=commune_curve, curve_anchors_km=[17.5, 37.5, 75.0], "
              "max_commute_km=80 (ASSUMPTION A10).\n",
              "# Weights: short=0.9 middle=0.5 long=0.2 mover_share=0.4 weights_source=owner (text).\n"]
    path = tmp_path / "t.csv"
    path.write_text("".join(header) + "a,b\n1,2\n", encoding="utf-8")
    assert SFI.od_weights(path) == {"short": 0.9, "middle": 0.5, "long": 0.2, "max_commute_km": 80.0}
    for missing in (0, 1):
        path.write_text("".join(h for i, h in enumerate(header) if i != missing) + "a,b\n1,2\n", encoding="utf-8")
        with pytest.raises(ValueError, match="(?i)weight"):
            SFI.od_weights(path)


# --------------------------------------------------------------------------- the level table
def test_level_sums_the_young_age_groups_and_keeps_every_cell(tmp_path):
    level = SFI.load_level(LEVEL_PATH, ZGB_KREISE)
    assert list(level.columns) == ["code", "age_range", "students_persons"]
    assert set(level["age_range"]) == {"18_29", "30plus"}
    assert len(level) == len(ZGB_KREISE) * 2
    raw = pd.read_csv(LEVEL_PATH, comment="#", encoding="utf-8", dtype={"code": str})
    raw = raw[raw["code"].isin(ZGB_KREISE)]
    assert level["students_persons"].sum() == pytest.approx(raw["students_persons"].sum())
    young = raw[raw["age_group"].isin(["18_19", "20_24", "25_29"])]
    got = level[level["age_range"] == "18_29"].set_index("code")["students_persons"]
    assert got.loc[ZGB_KREISE[0]] == pytest.approx(young[young["code"] == ZGB_KREISE[0]]["students_persons"].sum())


def test_level_raises_for_a_missing_or_duplicated_cell(tmp_path):
    rows = [(kreis, sex, age, 10.0) for kreis in ZGB_KREISE for sex in ("female", "male")
            for age in ("18_19", "20_24", "25_29", "30plus")]
    frame = pd.DataFrame(rows, columns=["code", "sex", "age_group", "students_persons"])
    path = tmp_path / "level.csv"

    def write(table):
        path.write_text("# header\n" + table.to_csv(index=False), encoding="utf-8")

    write(frame)
    assert SFI.load_level(path, ZGB_KREISE)["students_persons"].sum() == pytest.approx(10.0 * len(rows))
    write(frame.drop(index=3))
    with pytest.raises(ValueError, match="missing"):
        SFI.load_level(path, ZGB_KREISE)
    write(pd.concat([frame, frame.iloc[[0]]]))
    with pytest.raises(ValueError, match="more than once"):
        SFI.load_level(path, ZGB_KREISE)


# --------------------------------------------------------------------------- home areas
def test_area_by_kreis_maps_every_kreis_and_wolfsburg_to_its_own_area():
    areas = SFI.default_home_area_kreise()
    by_kreis = SFI.area_by_kreis(areas, ZGB_KREISE)
    assert set(by_kreis) == set(ZGB_KREISE)
    assert by_kreis[WOLFSBURG_KREIS] == AREA_WOLFSBURG
    assert {by_kreis[k] for k in ZGB_KREISE if k != WOLFSBURG_KREIS} == set(areas)


@pytest.mark.parametrize("mutate, message", [
    (lambda a: {**a, "surrounding": a["surrounding"] + [WOLFSBURG_KREIS]}, "Wolfsburg"),
    (lambda a: {**a, "surrounding": a["surrounding"][1:]}, "no home area"),
    (lambda a: {**a, "surrounding": a["surrounding"] + a["braunschweig"]}, "more than one area"),
    (lambda a: {**a, "surrounding": a["surrounding"] + ["03999"]}, "outside the region"),
    (lambda a: {}, "non-empty"),
])
def test_area_by_kreis_raises_naming_the_key_on_every_violation(mutate, message):
    with pytest.raises(ValueError, match=message) as error:
        SFI.area_by_kreis(mutate(SFI.default_home_area_kreise()), ZGB_KREISE)
    assert SFI.KEY_HOME_AREA_KREISE in str(error.value)


# --------------------------------------------------------------------------- the day factor
def test_away_share_by_area_reads_the_surveyed_areas_and_gives_wolfsburg_the_region_value():
    away = SFI.away_share_by_area(SRV_DIR)
    assert set(away) == {*SFI.default_home_area_kreise(), AREA_WOLFSBURG}
    assert all(0 < value < 1 for value in away.values())
    region = pd.read_csv(SRV_DIR / "srv2023_student_parameters.csv", comment="#", encoding="utf-8")
    row = region[(region["parameter"] == "away_all_day") & (region["base"] == "students_all")
                 & (region["label"] == "yes")]
    assert len(row) == 1
    assert away[AREA_WOLFSBURG] == pytest.approx(float(row["share"].iloc[0]))


def test_education_leg_share_is_a_share():
    assert 0 < SFI.education_leg_share(SRV_DIR) < 1


def test_day_factor_follows_the_area_and_the_absence_mode():
    e = SFI.education_leg_share(SRV_DIR)
    away = SFI.away_share_by_area(SRV_DIR)
    by_kreis = SFI.area_by_kreis(SFI.default_home_area_kreise(), ZGB_KREISE)
    cells = SFI.day_factor_by_kreis(e, away, by_kreis, "cells")
    assert cells.to_dict() == pytest.approx({kreis: e * (1.0 - away[area]) for kreis, area in by_kreis.items()})
    off = SFI.day_factor_by_kreis(e, away, by_kreis, "off")
    assert (off == e).all()
    with pytest.raises(ValueError, match="absence_mode"):
        SFI.day_factor_by_kreis(e, away, by_kreis, "on")
    with pytest.raises(ValueError, match="away"):
        SFI.day_factor_by_kreis(e, {"braunschweig": 0.1}, by_kreis, "cells")


def test_effective_day_factor_is_the_level_weighted_mean():
    e = SFI.education_leg_share(SRV_DIR)
    by_kreis = SFI.area_by_kreis(SFI.default_home_area_kreise(), ZGB_KREISE)
    f = SFI.day_factor_by_kreis(e, SFI.away_share_by_area(SRV_DIR), by_kreis, "cells")
    level = SFI.load_level(LEVEL_PATH, ZGB_KREISE).groupby("code")["students_persons"].sum()
    effective = SFI.effective_day_factor(level, f)
    assert f.min() <= effective <= f.max()
    assert effective == pytest.approx(float((level * f.loc[level.index]).sum() / level.sum()))
    with pytest.raises(ValueError, match="03999"):
        SFI.effective_day_factor(pd.Series({"03999": 1.0}), f)


# --------------------------------------------------------------------------- the home Kreis
def _zones(departement, commune):
    return pd.DataFrame({"household_id": np.arange(len(departement)), "departement_id": departement,
                         "commune_id": commune, "iris_id": [c + "0000" for c in commune]})


def test_home_kreis_by_household_reads_the_five_digit_kreis():
    zones = _zones(["03101", "03158", "03103"], ["031010000000", "031580370000", "031030000000"])
    zones["departement_id"] = zones["departement_id"].astype("category")
    kreis = SFI.home_kreis_by_household(zones)
    assert kreis.index.name == "household_id"
    assert kreis.tolist() == ["03101", "03158", "03103"]
    assert kreis.dtype == object


@pytest.mark.parametrize("departement, commune, message", [
    (["031010000000"], ["031010000000"], "5-digit"),
    ([None], ["031010000000"], "5-digit"),
    (["03999"], ["039990000000"], "region"),
    (["03101"], ["031580370000"], "commune_id"),
])
def test_home_kreis_by_household_raises_when_the_column_does_not_hold_kreis_codes(departement, commune, message):
    with pytest.raises(ValueError, match=message):
        SFI.home_kreis_by_household(_zones(departement, commune))
