"""Pin tests on the COMMITTED student reference tables (issue #140, Phase 0, R4 and R5).

The tables under ``eqasim-data/data/braunschweig/srv`` and ``.../mid`` are committed aggregates,
so these tests read them directly (not data-gated). Two kinds of check:

* invariants that must hold for any correct extraction (row sets, shares in [0, 1], partitions
  summing to 1, NaN where the base is empty, shared label vocabulary of the two surveys);
* three value pins per file, copied unrounded from the files written by the extraction run of
  2026-11-25 (code state ``fbb54d76``; the MiD behaviour table and the decision tables from the
  re-run of 2026-11-26 at code state ``f8c79e4d``, run manifest
  ``student-commuting-phase0-reference-2026-11-25``). A pin failing after a deliberate re-extraction means
  the pin is to be re-copied from the new file together with a new run manifest; it is not a
  reference value, only a guard against an unnoticed change of a committed table.
"""
import ast
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration.srv_distance_targets import WOLFSBURG_KREIS
from braunschweig.calibration.srv_student_reference import (
    DESTINATION_TABLE, HOME_GROUP_BRAUNSCHWEIG, HOME_GROUP_NO_SITE, HOME_GROUP_SITE,
    MEAN_LABEL, MODE_BY_E_HVM_5, PARAMETER_TABLE, POST_MATSIM_TABLE, QUANTITY_DAILY_DISTANCE,
    QUANTITY_DAILY_TRAVEL_TIME, QUANTITY_MODE_ALL_LEGS, QUANTITY_MODE_EDUCATION_LEGS,
    YOUNG_ADULT_STATUS_TABLE, YOUNG_ADULT_STATUSES, destination_vocabulary)
from braunschweig.calibration.student_control_evidence import MIN_RESPONDENTS_PER_CELL
from braunschweig.calibration.student_parameters import EXTRA_BASES, PARAMETERS

ROOT = Path(__file__).resolve().parents[1]
SRV = ROOT / "eqasim-data" / "data" / "braunschweig" / "srv"
MID = ROOT / "eqasim-data" / "data" / "braunschweig" / "mid"

ZGB_KREISE = ["03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"]
REGION_ROW = "03ZGB"
DISTANCE_SHARE_COLUMNS = [
    "share_0_5", "share_5_10", "share_10_25", "share_25_50", "share_50_100", "share_100_plus",
]


def _read(directory, name):
    return pd.read_csv(directory / name, comment="#", dtype={"code": str, "region": str})


def _row(frame, **keys):
    mask = np.ones(len(frame), dtype=bool)
    for column, value in keys.items():
        mask &= (frame[column] == value).to_numpy()
    assert mask.sum() == 1, "expected exactly one row for %s, found %d" % (keys, mask.sum())
    return frame[mask].iloc[0]


def _in_unit_interval(series):
    values = series.dropna()
    return bool(((values >= 0.0) & (values <= 1.0)).all())


# --------------------------------------------------------------------------------- SrV tables


def test_srv_share_by_kreis_rows_and_invariants():
    frame = _read(SRV, "srv2023_student_share_by_kreis.csv")
    assert list(frame["code"]) == ZGB_KREISE + [REGION_ROW]
    wolfsburg = _row(frame, code="03103")
    assert wolfsburg["n_adults"] == 0
    assert np.isnan(wolfsburg["student_share_adults"])
    assert np.isnan(wolfsburg["student_share_20_29"])
    for column in ["student_share_adults", "student_share_20_29", "presence_share"]:
        assert _in_unit_interval(frame[column]), column
    for column in ["se_student_share_adults", "se_student_share_20_29"]:
        assert (frame[column].dropna() > 0).all(), column


def test_srv_share_by_kreis_value_pins_2026_11_25():
    frame = _read(SRV, "srv2023_student_share_by_kreis.csv")
    assert _row(frame, code="03101")["student_share_20_29"] == pytest.approx(0.4578637028, rel=1e-9)
    assert _row(frame, code="03101")["n_students_20_29"] == 199
    assert _row(frame, code="03102")["se_student_share_20_29"] == pytest.approx(0.04857737943, rel=1e-9)


def test_srv_education_status_by_age_partitions():
    frame = _read(SRV, "srv2023_education_status_by_age.csv")
    assert set(frame["age_band"]) == {"18_19", "20_29", "30plus"}
    assert _in_unit_interval(frame["share"])
    sums = frame.groupby("age_band")["share"].sum()
    assert sums.to_numpy() == pytest.approx(np.ones(3), abs=1e-8)


def test_srv_education_status_by_age_value_pins_2026_11_25():
    frame = _read(SRV, "srv2023_education_status_by_age.csv")
    student = _row(frame, age_band="18_19", status="student")
    assert student["n_unweighted"] == 7
    assert student["share"] == pytest.approx(0.0208189887, rel=1e-9)
    assert _row(frame, age_band="18_19", status="pupil")["share"] == pytest.approx(0.8949494283, rel=1e-9)


def test_srv_distance_by_kreis_rows_and_invariants():
    frame = _read(SRV, "srv2023_student_education_distance_by_kreis.csv")
    assert list(frame["code"]) == ZGB_KREISE + [REGION_ROW]
    filled = frame[frame["n_persons"] > 0]
    assert filled[DISTANCE_SHARE_COLUMNS].sum(axis=1).to_numpy() == pytest.approx(
        np.ones(len(filled)), abs=1e-8)
    # the header states a floor of 0.0 for a row of a single person; positive wherever there are two or more
    assert (filled["emd_noise_95"] >= 0).all()
    assert (filled.loc[filled["n_persons"] > 1, "emd_noise_95"] > 0).all()
    empty = _row(frame, code="03103")
    assert empty["n_persons"] == 0
    assert empty[DISTANCE_SHARE_COLUMNS].isna().all()


def test_srv_distance_by_kreis_value_pins_2026_11_25():
    frame = _read(SRV, "srv2023_student_education_distance_by_kreis.csv")
    row = _row(frame, code="03101")
    assert row["n_persons"] == 68
    assert row["share_0_5"] == pytest.approx(0.7105607654, rel=1e-9)
    assert row["emd_noise_95"] == pytest.approx(0.06346545741, rel=1e-9)


def test_srv_comparison_invariants_and_pins_2026_11_25():
    frame = _read(SRV, "srv2023_student_comparison.csv")
    assert _in_unit_interval(frame["share"])
    sums = frame.groupby(["variable", "group"])["share"].sum()
    assert sums.to_numpy() == pytest.approx(np.ones(len(sums)), abs=1e-8)
    row = _row(frame, variable="V_PKW_VERFUEG", group="student", code="-8")
    assert row["n_unweighted"] == 102
    assert row["share"] == pytest.approx(0.274778774, rel=1e-9)
    assert _row(frame, variable="V_PKW_VERFUEG", group="student", code="1")["share"] == pytest.approx(
        0.4335924217, rel=1e-9)


# --------------------------------------------------------------------------------- MiD tables


def test_mid_status_by_age_partitions_and_pins_2026_11_25():
    frame = _read(MID, "mid2023_student_status_by_age.csv")
    assert set(frame["age_band"]) == {"lt20", "20_29", "30plus"}
    assert _in_unit_interval(frame["share"])
    sums = frame.groupby("age_band")["share"].sum()
    assert sums.to_numpy() == pytest.approx(np.ones(3), abs=1e-8)
    student = _row(frame, age_band="lt20", status="student")
    assert student["n_unweighted"] == 204
    assert student["share"] == pytest.approx(0.007523271496, rel=1e-9)
    assert _row(frame, age_band="lt20", status="pupil")["share"] == pytest.approx(0.6755780361, rel=1e-9)


def test_mid_distance_mode_partitions_and_pins_2026_11_25():
    frame = _read(MID, "mid2023_student_education_distance_mode.csv")
    assert _in_unit_interval(frame["share_of_band"])
    assert _in_unit_interval(frame["share_of_all"])
    per_band = frame.groupby("distance_band")["share_of_band"].sum()
    assert per_band.to_numpy() == pytest.approx(np.ones(len(per_band)), abs=1e-8)
    assert frame["share_of_all"].sum() == pytest.approx(1.0, abs=1e-8)
    row = _row(frame, distance_band="0_5", mode="walk")
    assert row["n_unweighted"] == 200
    assert row["share_of_band"] == pytest.approx(0.2961731434, rel=1e-9)
    assert row["share_of_all"] == pytest.approx(0.1543911778, rel=1e-9)


def test_mid_share_20_29_by_land_rows_and_pins_2026_11_25():
    frame = _read(MID, "mid2023_student_share_20_29_by_land.csv")
    assert list(frame["region"]) == ["DE"] + ["%02d" % i for i in range(1, 17)]
    for column in ["share_pupils", "share_students", "share_pupils_and_students"]:
        assert _in_unit_interval(frame[column]), column
    assert (frame["share_pupils"] + frame["share_students"]).to_numpy() == pytest.approx(
        frame["share_pupils_and_students"].to_numpy(), abs=1e-8)
    germany = _row(frame, region="DE")
    assert germany["n_unweighted"] == 34158
    assert germany["share_students"] == pytest.approx(0.1677322173, rel=1e-9)
    assert _row(frame, region="01")["share_pupils_and_students"] == pytest.approx(0.1254952029, rel=1e-9)


def test_mid_age_sex_partitions_and_pins_2026_11_27():
    frame = _read(MID, "mid2023_student_age_sex.csv")
    assert len(frame) == 96
    de = frame[frame["area"] == "DE"]
    joint = de[de["sex"].isin(["female", "male"])]
    assert len(joint) == 8
    assert joint["share_of_adult_students"].sum() == pytest.approx(1.0, abs=1e-8)
    with_respondents = frame[frame["n_effective"].notna()]
    per_base = with_respondents.groupby(["area", "sex"])["share_within_sex"].sum()
    assert per_base.to_numpy() == pytest.approx(np.ones(len(per_base)), abs=1e-8)
    by_sex = de.pivot(index="age_group", columns="sex", values="n_unweighted")
    assert (by_sex["all"] == by_sex["female"] + by_sex["male"]).all()
    assert joint["n_unweighted"].min() >= MIN_RESPONDENTS_PER_CELL
    row = _row(frame, area="DE", sex="female", age_group="20_24")
    assert row["n_unweighted"] == 3869
    assert row["share_within_sex"] == pytest.approx(0.6499259037, rel=1e-9)
    assert _row(frame, area="DE", sex="male", age_group="25_29")[
        "share_of_adult_students"] == pytest.approx(0.1159770254, rel=1e-9)
    assert _row(frame, area="DE", sex="all", age_group="30plus")[
        "n_effective"] == pytest.approx(3624.775464, rel=1e-9)


# ------------------------------------------------------------ behaviour tables (both surveys)


def test_behaviour_tables_share_labels_and_partition_students():
    srv = _read(SRV, "srv2023_student_behaviour.csv")
    mid = _read(MID, "mid2023_student_behaviour_seed.csv")
    keys = ["control", "universe", "label"]
    assert set(map(tuple, srv[keys].to_numpy())) == set(map(tuple, mid[keys].to_numpy()))
    for frame in (srv, mid):
        assert _in_unit_interval(frame["share"])
        assert (frame["n_effective"].dropna() > 0).all()
        students = frame[frame["universe"] == "students"]
        sums = students.groupby("control")["share"].sum()
        assert sums.to_numpy() == pytest.approx(np.ones(len(sums)), abs=1e-8)


def test_behaviour_value_pins_2026_11_25():
    srv = _read(SRV, "srv2023_student_behaviour.csv")
    row = _row(srv, control="status_x_education_leg", universe="students", label="student_edu")
    assert row["n_unweighted"] == 156
    assert row["share"] == pytest.approx(0.3705664224, rel=1e-9)
    assert row["n_effective"] == pytest.approx(198.8957514, rel=1e-9)
    mid = _read(MID, "mid2023_student_behaviour_seed.csv")
    row = _row(mid, control="status_x_education_leg", universe="students", label="student_edu")
    assert row["n_unweighted"] == 1478
    assert row["share"] == pytest.approx(0.3054065027, rel=1e-9)
    assert row["n_effective"] == pytest.approx(1428.127849, rel=1e-9)


# ------------------------------------------------------------------ decision tables (artifacts)

ARTIFACTS = ROOT / "docs" / "runs" / "artifacts" / "student-commuting-phase0-reference-2026-11-25"


def test_decision_tables_rows_verdicts_and_value_pins_2026_11_25():
    rule = pd.read_csv(ARTIFACTS / "student_control_build_rule.csv", comment="#")
    assert rule["control"].nunique() == 5
    assert len(rule) == 14
    verdicts = rule.groupby("control")["verdict"].agg(lambda v: sorted(set(v)))
    assert verdicts.to_dict() == {
        "status_x_education_leg": ["comparison_only"],
        "arrival": ["comparison_only"],
        "trip_class": ["comparison_only"],
        "work_leg": ["comparison_only"],
        "season_ticket": ["comparison_only"],
    }
    assert (rule["rule3_smoke"] == "pending_phase2").all()
    row = _row(rule, control="arrival", label="student_no_education_leg")
    assert row["difference_in_se"] == pytest.approx(-1.913354468, rel=1e-8)
    assert _row(rule, control="trip_class", label="student_trips_0")["srv_share_students"] == pytest.approx(
        0.1784765925, rel=1e-9)

    gate = pd.read_csv(ARTIFACTS / "student_level_gate.csv", comment="#", dtype={"code": str})
    assert list(gate["code"]) == ZGB_KREISE + [REGION_ROW]
    assert gate["residents_implied_by_enrollment_persons"].isna().all()
    assert np.isnan(_row(gate, code="03103")["srv_estimate_students_20_29_persons"])
    assert _row(gate, code="03101")["srv_estimate_students_20_29_persons"] == pytest.approx(
        16776.12607, rel=1e-9)
    assert _row(gate, code=REGION_ROW)["enrollment_local_sites_persons"] == 26054


# ------------------------------------------------------- Phase 1 tables (extraction of 2026-11-26)
# Written by the extraction run of 2026-11-26 at the code state named in each file header. The
# structural tests read the vocabulary and the groups from the modules that own them and the site
# Kreise from the file header; the value pins are copied unrounded from the committed files and are
# guards against an unnoticed change, not reference values.

PHASE1_TABLES = (PARAMETER_TABLE, YOUNG_ADULT_STATUS_TABLE, POST_MATSIM_TABLE, DESTINATION_TABLE)
FORBIDDEN_HEADER_PATTERNS = {
    "drive letter": r"\b[A-Za-z]:[\\/]",
    "home directory": r"/home/",
    "at sign": r"@",
    "IPv4 address": r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b",
}


def _read_with_nan_label(name):
    return pd.read_csv(SRV / name, comment="#", dtype={"code": str, "label": str},
                       keep_default_na=False, na_values=[""])


def _header_lines(name):
    return [line for line in (SRV / name).read_text(encoding="utf-8").splitlines()
            if line.startswith("#")]


def _site_kreise_of_header(name):
    match = re.search(r"Site Kreise \([^)]*\): (\[[^\]]*\]);", " ".join(_header_lines(name)))
    assert match, "no 'Site Kreise' header line in %s" % name
    return frozenset(ast.literal_eval(match.group(1)))


def _assert_statistics(cell, base_size, share_column="share"):
    """One base: n_effective <= its size, se = sqrt(p (1 - p) / n_effective), NaN only if empty."""
    if base_size == 0:
        assert cell[share_column].isna().all()
        assert cell["n_effective"].isna().all() and cell["se_share"].isna().all()
        return
    assert cell[share_column].notna().all()
    assert (cell["n_effective"] <= base_size * (1 + 1e-9)).all()
    share = cell[share_column].to_numpy()
    expected = np.sqrt(share * (1.0 - share) / cell["n_effective"].to_numpy())
    assert cell["se_share"].to_numpy() == pytest.approx(expected, rel=1e-6, abs=1e-12)


def test_student_parameters_cover_the_vocabulary_and_partition():
    frame = _read_with_nan_label(PARAMETER_TABLE)
    assert set(frame["parameter"]) == set(PARAMETERS)
    expected_bases = {(name, spec.base) for name, spec in PARAMETERS.items()}
    expected_bases |= {(name, base) for name, bases in EXTRA_BASES.items() for base in bases}
    assert {tuple(pair) for pair in frame[["parameter", "base"]].drop_duplicates().to_numpy()} == \
        expected_bases
    for (parameter, base), cell in frame.groupby(["parameter", "base"]):
        assert list(cell["label"]) == list(PARAMETERS[parameter].labels), (parameter, base)
        assert _in_unit_interval(cell["share"]), (parameter, base)
        base_size = int(cell["n_unweighted"].sum())
        if base_size > 0:
            assert cell["share"].sum() == pytest.approx(1.0, abs=1e-8), (parameter, base)
        _assert_statistics(cell, base_size)


def test_student_parameters_value_pins_20261126():
    frame = _read_with_nan_label(PARAMETER_TABLE)
    row = _row(frame, parameter="age_group", base="students_present", label="20_24")
    assert row["n_unweighted"] == 240
    assert row["share"] == pytest.approx(0.6211472026, rel=1e-9)
    row = _row(frame, parameter="mobile", base="students_present", label="yes")
    assert row["n_unweighted"] == 365
    assert row["se_share"] == pytest.approx(0.02715112635, rel=1e-9)
    row = _row(frame, parameter="away_all_day", base="other_adults_all", label="yes")
    assert row["share"] == pytest.approx(0.05186169651, rel=1e-9)
    assert row["n_effective"] == pytest.approx(6562.401874, rel=1e-9)


def test_young_adult_status_rows_partition_and_wolfsburg_is_nan():
    frame = _read(SRV, YOUNG_ADULT_STATUS_TABLE)
    kreis_rows = frame[frame["level"] == "kreis"]
    assert list(kreis_rows["code"].drop_duplicates()) == ZGB_KREISE
    for code, cell in frame.groupby("code"):
        assert list(cell["status"]) == list(YOUNG_ADULT_STATUSES), code
        if code == WOLFSBURG_KREIS:
            assert (cell["n_unweighted"] == 0).all()
        else:
            assert cell["share"].sum() == pytest.approx(1.0, abs=1e-8), code
        _assert_statistics(cell, int(cell["n_unweighted"].sum()))
    assert _in_unit_interval(frame["share"])


def test_young_adult_status_value_pins_20261126():
    frame = _read(SRV, YOUNG_ADULT_STATUS_TABLE)
    row = _row(frame, code="03101", status="student")
    assert row["n_unweighted"] == 199
    assert row["share"] == pytest.approx(0.4578637028, rel=1e-9)
    assert _row(frame, code="03102", status="employed")["se_share"] == pytest.approx(
        0.05492499539, rel=1e-9)
    assert _row(frame, code="03ZGB", status="student")["weight_sum"] == pytest.approx(
        23549.04024, rel=1e-9)


def test_post_matsim_reference_rows_and_pins_20261126():
    frame = _read_with_nan_label(POST_MATSIM_TABLE)
    modes = list(MODE_BY_E_HVM_5.values())
    expected = [(quantity, mode) for quantity in (QUANTITY_MODE_ALL_LEGS,
                                                  QUANTITY_MODE_EDUCATION_LEGS)
                for mode in modes]
    expected += [(QUANTITY_DAILY_DISTANCE, MEAN_LABEL), (QUANTITY_DAILY_TRAVEL_TIME, MEAN_LABEL)]
    assert [tuple(pair) for pair in frame[["quantity", "label"]].to_numpy()] == expected
    for quantity in (QUANTITY_MODE_ALL_LEGS, QUANTITY_MODE_EDUCATION_LEGS):
        cell = frame[frame["quantity"] == quantity]
        assert _in_unit_interval(cell["value"])
        assert cell["value"].sum() == pytest.approx(1.0, abs=1e-6), quantity
    assert (frame["n_unweighted"] >= 0).all()
    assert _row(frame, quantity="main_mode_all_legs", label="walk")["value"] == pytest.approx(
        0.2958223002, rel=1e-9)
    assert _row(frame, quantity="daily_distance_km_mean", label="mean")["value"] == pytest.approx(
        23.27352894, rel=1e-9)
    row = _row(frame, quantity="daily_travel_time_min_mean", label="mean")
    assert row["n_unweighted"] == 378
    assert row["value"] == pytest.approx(65.50703693, rel=1e-9)


def test_education_destination_groups_partition_and_noise_floors():
    frame = pd.read_csv(SRV / DESTINATION_TABLE, comment="#")
    sites = _site_kreise_of_header(DESTINATION_TABLE)
    assert set(frame["home_group"]) == {
        HOME_GROUP_BRAUNSCHWEIG, HOME_GROUP_SITE, HOME_GROUP_NO_SITE, "region"}
    for home_group, cell in frame.groupby("home_group"):
        assert list(cell["destination_group"]) == destination_vocabulary(sites, home_group), \
            home_group
        base_size = int(cell["n_persons_group"].iloc[0])
        assert (cell["n_persons_group"] == base_size).all()
        assert (cell["noise_floor_tvd_95"] >= 0).all()
        assert _in_unit_interval(cell["share"])
        if base_size > 0:
            assert cell["share"].sum() == pytest.approx(1.0, abs=1e-8), home_group
        _assert_statistics(cell, base_size)


def test_education_destination_value_pins_20261126():
    frame = pd.read_csv(SRV / DESTINATION_TABLE, comment="#")
    row = _row(frame, home_group="braunschweig", destination_group="own_kreis")
    assert row["n_persons_group"] == 88
    assert row["share"] == pytest.approx(0.7736633592, rel=1e-9)
    row = _row(frame, home_group="region", destination_group="own_kreis")
    assert row["n_unweighted"] == 82
    assert row["noise_floor_tvd_95"] == pytest.approx(0.1311193596, rel=1e-9)
    assert _row(frame, home_group="region", destination_group="landkreis_hildesheim")[
        "weight_sum"] == pytest.approx(428.7944169, rel=1e-9)


@pytest.mark.parametrize("name", PHASE1_TABLES)
def test_phase1_table_headers_are_traceable_and_carry_no_local_paths(name):
    lines = _header_lines(name)
    header = " ".join(lines)
    assert sum(line.startswith("# Exclusions:") for line in lines) == 1
    assert re.search(r"table sha256 [0-9a-f]{64}", header), name
    assert _site_kreise_of_header(name)
    for label, pattern in FORBIDDEN_HEADER_PATTERNS.items():
        assert not re.search(pattern, header), (name, label)
    if name == PARAMETER_TABLE:
        assert "education_leg_share_harmonised_purpose" in header
        assert "education_leg_share_control_definition" in header
