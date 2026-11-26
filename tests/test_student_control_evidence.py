"""Unit tests for the build-rule and level-gate tables of the student controls (issue #140, Phase 0).

Synthetic frames only: the committed SrV / MiD aggregates, the census table and the site table are
never read by a test. Every expected value is computed by hand in the test that asserts it; the
effective sample sizes of the two universes are set far apart so that a standard error taken over
the wrong base flips the verdict.
"""
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import student_control_evidence as E
from braunschweig.calibration.student_behaviour_labels import (
    BEHAVIOUR_COLUMNS, CONTROLS, NOT_STUDENT, UNIVERSE_ADULTS, UNIVERSE_STUDENTS)

REPO = Path(__file__).resolve().parents[1]

ZGB = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")

# Effective size of the ADULTS universe in every synthetic SrV frame: far above any students' size
# used below, so a standard error over the adults instead of the students differs by a factor > 5.
N_EFFECTIVE_ADULTS = 5000.0


def _survey(control, n, share, n_effective_students, *, n_not_student=1000):
    """A behaviour table over every control of CONTROLS; ``control`` carries the given cells.

    The other controls get 50 respondents per label and uniform shares (identical in both surveys
    when built through :func:`_frames`). In the adults universe a student label keeps its count,
    and its share is scaled by an arbitrary 0.2 (the adults' shares do not enter the build rule).
    """
    rows = []
    for name, labels in CONTROLS.items():
        if name == control:
            cell_n, cell_share, n_eff = list(n), list(share), n_effective_students
        else:
            cell_n = [50] * len(labels)
            cell_share = [1.0 / len(labels)] * len(labels)
            n_eff = 100.0
        for label, count, value in zip(labels, cell_n, cell_share):
            rows.append({"control": name, "universe": UNIVERSE_STUDENTS, "label": label,
                         "n_unweighted": count, "weight_sum": float(count), "share": value,
                         "n_effective": n_eff,
                         "se_share": math.sqrt(value * (1 - value) / n_eff)})
            rows.append({"control": name, "universe": UNIVERSE_ADULTS, "label": label,
                         "n_unweighted": count, "weight_sum": float(count), "share": 0.2 * value,
                         "n_effective": N_EFFECTIVE_ADULTS, "se_share": 0.001})
        rows.append({"control": name, "universe": UNIVERSE_ADULTS, "label": NOT_STUDENT,
                     "n_unweighted": n_not_student, "weight_sum": float(n_not_student),
                     "share": 0.8, "n_effective": N_EFFECTIVE_ADULTS, "se_share": 0.001})
    return pd.DataFrame(rows, columns=BEHAVIOUR_COLUMNS)


def _frames(control, srv_n, srv_share, mid_share, srv_n_eff, mid_n=None):
    mid_n = mid_n if mid_n is not None else [500] * len(srv_n)
    srv = _survey(control, srv_n, srv_share, srv_n_eff)
    mid = _survey(control, mid_n, mid_share, 2000.0, n_not_student=20000)
    return srv, mid


def _control(out, control):
    return out[out["control"] == control].set_index("label")


def test_verdict_needs_cell_size_and_a_significant_difference():
    srv, mid = _frames(control="work_leg", srv_n=[40, 35], srv_share=[0.30, 0.70],
                       mid_share=[0.10, 0.90], srv_n_eff=100, mid_n=[400, 600])
    out = E.evaluate_build_rule(srv, mid)

    assert list(out.columns) == [
        "control", "label", "srv_n_unweighted_adults", "mid_n_unweighted_adults",
        "srv_share_students", "mid_share_students", "srv_n_effective_students",
        "srv_se_share_students", "difference_in_se", "rule1_cell_size_ok", "rule2_differs",
        "rule3_smoke", "verdict"]
    # one row per control x student label, never the not_student remainder
    assert len(out) == sum(len(labels) for labels in CONTROLS.values())
    assert NOT_STUDENT not in set(out["label"])
    work = _control(out, "work_leg")
    se = (0.3 * 0.7 / 100) ** 0.5
    assert work.loc["student_work", "difference_in_se"] == pytest.approx(0.2 / se)
    assert work.loc["student_nowork", "difference_in_se"] == pytest.approx(-0.2 / se)
    assert work.loc["student_work", "srv_se_share_students"] == pytest.approx(se)
    assert work["srv_n_unweighted_adults"].tolist() == [40, 35]
    assert work["mid_n_unweighted_adults"].tolist() == [400, 600]
    assert work["mid_share_students"].tolist() == pytest.approx([0.10, 0.90])
    assert work["rule1_cell_size_ok"].tolist() == [True, True]
    assert work["rule2_differs"].tolist() == [True, True]
    assert set(work["rule3_smoke"]) == {"pending_phase2"}
    assert set(work["verdict"]) == {"build_candidate"}
    # the other controls carry identical shares in both surveys: nothing to steer
    assert set(out.loc[out["control"] != "work_leg", "verdict"]) == {"comparison_only"}


@pytest.mark.parametrize("srv_n, srv_share, mid_share, rule1, rule2", [
    # one label below 30 respondents: comparison only even though both labels differ strongly
    ([29, 100], [0.30, 0.70], [0.05, 0.95], [False, True], [True, True]),
    # enough respondents, but |difference| = 0.05 = 1 standard error (sqrt(0.25 / 100)) per label
    ([60, 60], [0.50, 0.50], [0.45, 0.55], [True, True], [False, False]),
])
def test_a_failed_rule_makes_the_control_comparison_only(srv_n, srv_share, mid_share, rule1,
                                                         rule2):
    srv, mid = _frames(control="season_ticket", srv_n=srv_n, srv_share=srv_share,
                       mid_share=mid_share, srv_n_eff=100)
    ticket = _control(E.evaluate_build_rule(srv, mid), "season_ticket")
    assert ticket["rule1_cell_size_ok"].tolist() == rule1
    assert ticket["rule2_differs"].tolist() == rule2
    assert set(ticket["verdict"]) == {"comparison_only"}


def test_standard_error_uses_effective_size_of_the_share_universe():
    # Students' Kish size 50; adults' 5000; unweighted students 250. Difference 0.1:
    #   students: se = sqrt(0.21 / 50)   = 0.0648 -> 1.54 se -> does NOT differ (correct base)
    #   adults:   se = sqrt(0.21 / 5000) = 0.0065 -> 15.4 se -> would differ
    #   unweighted n 250: se = 0.0290            -> 3.45 se -> would differ
    srv, mid = _frames(control="work_leg", srv_n=[100, 150], srv_share=[0.30, 0.70],
                       mid_share=[0.20, 0.80], srv_n_eff=50)
    work = _control(E.evaluate_build_rule(srv, mid), "work_leg")
    assert work["srv_n_effective_students"].tolist() == [50.0, 50.0]
    assert work.loc["student_work", "srv_se_share_students"] == pytest.approx(
        math.sqrt(0.21 / 50))
    assert work.loc["student_work", "difference_in_se"] == pytest.approx(0.1 / math.sqrt(0.21 / 50))
    assert work["rule2_differs"].tolist() == [False, False]
    assert set(work["verdict"]) == {"comparison_only"}


def test_cells_without_a_mid_share_are_counted_not_silently_equal():
    # empty MiD base of the work_leg control: both of its shares are NaN
    srv, mid = _frames(control="work_leg", srv_n=[40, 35], srv_share=[0.30, 0.70],
                       mid_share=[np.nan, np.nan], srv_n_eff=100)
    out = E.evaluate_build_rule(srv, mid)
    work = _control(out, "work_leg")
    assert work["difference_in_se"].isna().all()
    assert work["rule2_differs"].tolist() == [False, False]
    assert set(work["verdict"]) == {"comparison_only"}
    assert E.build_rule_exclusions(out) == {
        "n_cells": sum(len(labels) for labels in CONTROLS.values()),
        "n_cells_rule2_not_evaluable": 2, "n_cells_mid_share_nan": 2,
        "n_cells_srv_se_undefined": 0}


def test_build_rule_refuses_when_most_cells_are_not_evaluable():
    srv, mid = _frames(control="work_leg", srv_n=[40, 35], srv_share=[0.30, 0.70],
                       mid_share=[0.10, 0.90], srv_n_eff=100)
    mid["share"] = np.nan
    with pytest.raises(ValueError, match="cannot be evaluated"):
        E.evaluate_build_rule(srv, mid)


@pytest.mark.parametrize("srv_share, mid_share, verdict, time_labels", [
    # arrival differs only in the cell it shares with the status control: -0.1 / sqrt(0.24 / 100)
    # = -2.04 se there, 0.75 / 0.75 / 1.0 se in the three arrival-time cells
    ([0.20, 0.20, 0.20, 0.40], [0.17, 0.17, 0.16, 0.50], "build_candidate", []),
    # arrival differs in two arrival-time cells: 0.2 / 0.05 = 4.0 and -0.1 / 0.04 = -2.5 se;
    # the shared cell stays at -0.05 / 0.03 = -1.67 se
    ([0.50, 0.20, 0.20, 0.10], [0.30, 0.30, 0.25, 0.15], "build_candidate",
     ["student_arrive_before_9", "student_arrive_9_11"]),
    # nothing passes (the state of the committed table): 0.5 se in the time cells, the shared
    # cell -0.06 / sqrt(0.24 / 100) = -1.22 se
    ([0.20, 0.20, 0.20, 0.40], [0.18, 0.18, 0.18, 0.46], "comparison_only", []),
])
def test_arrival_status_overlap_is_read_from_the_rule_table(srv_share, mid_share, verdict,
                                                            time_labels):
    """The arrival cell student_no_education_leg and the status cell student_noedu hold the
    students without an education leg; the overlap summary must say through which cells arrival
    passes rule 2 and carry both cells' unweighted counts, so a reader sees the two bases."""
    srv, mid = _frames(control="arrival", srv_n=[50, 50, 50, 60], srv_share=srv_share,
                       mid_share=mid_share, srv_n_eff=100, mid_n=[500, 500, 500, 640])
    rule = E.evaluate_build_rule(srv, mid)
    overlap = E.arrival_status_overlap(rule)
    assert overlap["arrival_verdict"] == verdict
    # the status cells carry the 50 respondents _survey gives every other control in both surveys
    assert overlap["n_unweighted_srv"] == {"status": 50, "arrival": 60}
    assert overlap["n_unweighted_mid"] == {"status": 50, "arrival": 640}
    assert overlap["status_verdict"] == "comparison_only"     # identical status cells
    assert overlap["arrival_time_labels_passing_rule2"] == time_labels
    no_edu = _control(rule, "arrival").loc["student_no_education_leg", "difference_in_se"]
    assert overlap["arrival_no_education_leg_difference_in_se"] == pytest.approx(no_edu)
    assert overlap["status_noedu_difference_in_se"] == pytest.approx(0.0)


@pytest.mark.parametrize("survey", ["srv", "mid"])
def test_mismatched_labels_between_surveys_raise(survey):
    srv, mid = _frames(control="work_leg", srv_n=[40, 35], srv_share=[0.30, 0.70],
                       mid_share=[0.10, 0.90], srv_n_eff=100)
    frames = {"srv": srv, "mid": mid}
    frame = frames[survey]
    frame.loc[frame["label"] == "student_work", "label"] = "student_work_renamed"
    with pytest.raises(ValueError, match="student_work_renamed"):
        E.evaluate_build_rule(frames["srv"], frames["mid"])


# --------------------------------------------------------------------------- level gate
def _share_row(code, level, n_adults, n_students, share_adults, n_20_29, n_students_20_29,
               share_20_29, se_20_29, students_weighted):
    return {"code": code, "level": level, "n_adults": n_adults, "n_students": n_students,
            "student_share_adults": share_adults, "n_effective_adults": float(n_adults),
            "se_student_share_adults": 0.01, "n_20_29": n_20_29,
            "n_students_20_29": n_students_20_29, "student_share_20_29": share_20_29,
            "n_effective_20_29": float(n_20_29), "se_student_share_20_29": se_20_29,
            "students_weighted_persons": students_weighted, "presence_share": np.nan,
            "n_effective_presence": np.nan, "se_presence_share": np.nan}


def _gate_inputs():
    """Hand-built inputs; the expected values of the gate tests follow from these numbers.

    Census: 03101 has 500 students of 4000 persons aged 20-29; every other covered Kreis 100 of
    2000; Wolfsburg (03103, not surveyed by SrV) 300 of 3000; Land 03 8000 of 100000 (8 %);
    Germany DG 100000 of 1000000 (10 %). MiD student share 20-29: DE 0.12, Land 03 0.10. So the
    Germany ratio is 0.12 / 0.10 = 1.2 and the Land ratio 0.10 / 0.08 = 1.25.
    """
    shares = []
    for code in ZGB:
        if code == "03101":
            shares.append(_share_row(code, "kreis", 300, 60, 0.2, 80, 20, 0.25, 0.05, 12000.0))
        elif code == "03103":
            shares.append(_share_row(code, "kreis", 0, 0, np.nan, 0, 0, np.nan, np.nan, 0.0))
        else:
            shares.append(_share_row(code, "kreis", 100, 5, 0.05, 30, 3, 0.1, 0.06, 900.0))
    shares.append(_share_row("03ZGB", "total", 900, 90, 0.1, 260, 38, 0.15, 0.02, 20000.0))
    srv = pd.DataFrame(shares)

    census_rows = []
    for code in ZGB:
        students, persons = {"03101": (500, 4000), "03103": (300, 3000)}.get(code, (100, 2000))
        census_rows.append((code, "Kreis " + code, "kreis", "20-29", students, persons))
    census_rows.append(("03", "Niedersachsen", "land", "20-29", 8000, 100000))
    census_rows.append(("DG", "Deutschland", "germany", "20-29", 100000, 1000000))
    census = pd.DataFrame(census_rows, columns=["region", "region_name", "region_level",
                                                "age_group", "students_nonemployed_persons",
                                                "all_persons"])

    mid = pd.DataFrame({"region": ["DE", "03", "06"], "n_unweighted": [5000, 400, 300],
                        "share_pupils": [0.05, 0.05, 0.05],
                        "share_students": [0.12, 0.10, 0.30],
                        "share_pupils_and_students": [0.17, 0.15, 0.35]})

    sites = pd.DataFrame(
        [("Uni A", "A", "03101000", "03101", "local", 1000, "WS 2099/00"),
         ("Art school", "A", "03101000", "03101", "local", 200, "WS 2099/00"),
         ("HS B", "W", "03103000", "03103", "local", 400, "WS 2099/00"),
         ("HS B", "F", "03158037", "03158", "local", 300, "WS 2099/00"),
         ("Uni H", "H", "03241001", "03241", "surrounding", 5000, "WS 2099/00")],
        columns=["institution", "site_commune", "site_ags8", "site_kreis_ars5", "scope",
                 "students_persons", "semester"])
    return srv, census, mid, sites


def test_level_gate_estimates_by_hand():
    srv, census, mid, sites = _gate_inputs()
    gate = E.build_level_gate(srv, census, mid, sites)

    assert gate["code"].tolist() == list(ZGB) + ["03ZGB"]
    row = gate.set_index("code").loc["03101"]
    assert row["mid_to_census_ratio_germany"] == pytest.approx(0.12 / 0.10)
    assert row["census_estimate_students_20_29_persons"] == pytest.approx(500 * 1.2)
    assert row["mid_to_census_ratio_land_03"] == pytest.approx(0.10 / 0.08)
    assert row["census_estimate_land_ratio_students_20_29_persons"] == pytest.approx(500 * 1.25)
    assert row["srv_estimate_students_20_29_persons"] == pytest.approx(0.25 * 4000)
    assert row["srv_n_students_20_29"] == 20
    assert row["srv_se_student_share_20_29"] == pytest.approx(0.05)
    assert row["census_population_20_29_persons"] == 4000
    assert row["census_students_nonemployed_20_29_persons"] == 500
    assert row["srv_students_weighted_persons"] == pytest.approx(12000.0)
    assert row["srv_student_share_adults"] == pytest.approx(0.2)
    assert row["enrollment_local_sites_persons"] == 1000 + 200
    assert np.isnan(row["residents_implied_by_enrollment_persons"])
    # a Kreis without a local site has an enrollment of 0, not NaN
    assert gate.set_index("code").loc["03102", "enrollment_local_sites_persons"] == 0

    # pooled covered area = the seven surveyed Kreise (Wolfsburg excluded on every column)
    pooled = gate.set_index("code").loc["03ZGB"]
    assert pooled["census_population_20_29_persons"] == 4000 + 6 * 2000
    assert pooled["census_students_nonemployed_20_29_persons"] == 500 + 6 * 100
    assert pooled["srv_estimate_students_20_29_persons"] == pytest.approx(0.15 * 16000)
    assert pooled["census_estimate_students_20_29_persons"] == pytest.approx(1100 * 1.2)
    assert pooled["srv_se_student_share_20_29"] == pytest.approx(0.02)
    assert pooled["enrollment_local_sites_persons"] == 1200 + 300


def test_level_gate_keeps_wolfsburg_with_nan_srv_columns():
    srv, census, mid, sites = _gate_inputs()
    wolfsburg = E.build_level_gate(srv, census, mid, sites).set_index("code").loc["03103"]
    for column in ("srv_student_share_20_29", "srv_se_student_share_20_29",
                   "srv_estimate_students_20_29_persons", "srv_student_share_adults"):
        assert np.isnan(wolfsburg[column]), column
    assert wolfsburg["srv_n_students_20_29"] == 0
    # the census estimate and the enrollment exist without SrV
    assert wolfsburg["census_estimate_students_20_29_persons"] == pytest.approx(300 * 1.2)
    assert wolfsburg["enrollment_local_sites_persons"] == 400


def test_build_level_gate_fills_the_reserved_column_and_keeps_nan_by_default():
    srv, census, mid, sites = _gate_inputs()
    default = E.build_level_gate(srv, census, mid, sites)
    assert default["residents_implied_by_enrollment_persons"].isna().all()
    # None is the Phase 0 call: byte-identical to leaving the argument out
    pd.testing.assert_frame_equal(
        default, E.build_level_gate(srv, census, mid, sites, residents_implied=None))

    # distinct hand-picked values: 03101 = 1000, Wolfsburg 03103 = 400, the six others 100 each
    implied = {code: {"03101": 1000.0, "03103": 400.0}.get(code, 100.0) for code in ZGB}
    gate = E.build_level_gate(srv, census, mid, sites, residents_implied=implied).set_index("code")
    assert gate.loc["03101", "residents_implied_by_enrollment_persons"] == 1000.0
    assert gate.loc["03103", "residents_implied_by_enrollment_persons"] == 400.0
    # the pooled row sums the seven surveyed Kreise only: Wolfsburg's 400 is not in it
    assert gate.loc["03ZGB", "residents_implied_by_enrollment_persons"] == 1000.0 + 6 * 100.0
    # every other column is untouched by the new argument
    other = [c for c in E.LEVEL_GATE_COLUMNS if c != "residents_implied_by_enrollment_persons"]
    pd.testing.assert_frame_equal(gate.reset_index()[other], default[other])


@pytest.mark.parametrize("mutate, match", [
    (lambda d: {k: v for k, v in d.items() if k != "03154"}, "03154"),
    (lambda d: {**d, "03999": 5.0}, "03999"),
    (lambda d: {**d, "03101": -1.0}, "03101"),
    (lambda d: {**d, "03102": float("nan")}, "03102")])
def test_build_level_gate_refuses_bad_residents_implied(mutate, match):
    srv, census, mid, sites = _gate_inputs()
    implied = mutate({code: 100.0 for code in ZGB})
    with pytest.raises(ValueError, match=match):
        E.build_level_gate(srv, census, mid, sites, residents_implied=implied)


@pytest.mark.parametrize("table, region", [("census", "03154"), ("census", "DG"),
                                           ("mid", "DE"), ("mid", "03")])
def test_level_gate_refuses_a_missing_region(table, region):
    inputs = dict(zip(("srv", "census", "mid", "sites"), _gate_inputs()))
    frame = inputs[table]
    inputs[table] = frame[frame["region"] != region]
    with pytest.raises(ValueError, match=region):
        E.build_level_gate(inputs["srv"], inputs["census"], inputs["mid"], inputs["sites"])


# --------------------------------------------------------------------------- CLI
def _load_script(name):
    path = REPO / "scripts" / ("%s.py" % name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_census_codes_match_the_census_extraction():
    census_script = _load_script("extract_zensus2022_students_by_age")
    assert E.CENSUS_GERMANY_CODE == census_script.GERMANY_CODE
    assert E.CENSUS_AGE_GROUP == census_script.AGE_GROUP


def _write_with_header(frame, path):
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("# synthetic provenance line\n")
        frame.to_csv(handle, index=False, lineterminator="\n")


def test_cli_writes_gate_and_rule_tables(tmp_path):
    srv_dir, mid_dir = tmp_path / "srv", tmp_path / "mid"
    srv_dir.mkdir()
    mid_dir.mkdir()
    srv_behaviour, mid_behaviour = _frames(control="work_leg", srv_n=[40, 35],
                                           srv_share=[0.30, 0.70], mid_share=[0.10, 0.90],
                                           srv_n_eff=100)
    shares, census, mid_share, sites = _gate_inputs()
    _write_with_header(srv_behaviour, srv_dir / "srv2023_student_behaviour.csv")
    _write_with_header(shares, srv_dir / "srv2023_student_share_by_kreis.csv")
    _write_with_header(pd.DataFrame({"variable": ["V_PKW_VERFUEG", "V_FUEHR_PKW", "V_FK_VERFUEG"],
                                     "group": "student", "code": 1, "n_unweighted": 10,
                                     "share": 0.5}),
                       srv_dir / "srv2023_student_comparison.csv")
    _write_with_header(mid_behaviour, mid_dir / "mid2023_student_behaviour_seed.csv")
    _write_with_header(mid_share, mid_dir / "mid2023_student_share_20_29_by_land.csv")
    _write_with_header(census, tmp_path / "census.csv")
    _write_with_header(sites, tmp_path / "sites.csv")
    out = tmp_path / "out"

    assert _load_script("build_student_control_evidence").main([
        "--srv-dir", str(srv_dir), "--mid-dir", str(mid_dir), "--census",
        str(tmp_path / "census.csv"), "--sites", str(tmp_path / "sites.csv"),
        "--out-dir", str(out), "--source-commit", "abc1234"]) == 0

    for name in ("student_control_build_rule.csv", "student_level_gate.csv",
                 "student_comparison_only.csv"):
        text = (out / name).read_text(encoding="utf-8")
        assert text.startswith("#") and "\r\n" not in text, name
        header = [line for line in text.splitlines() if line.startswith("#")]
        assert any("abc1234" in line for line in header), name
        if name != "student_comparison_only.csv":
            assert sum(line.startswith("# Exclusions:") for line in header) == 1, name
            assert any("GEWICHT_P_ZENSUS" in line and "P_GEW" in line for line in header), name
    rule_text = (out / "student_control_build_rule.csv").read_text(encoding="utf-8")
    assert "ASSUMPTION" in rule_text and UNIVERSE_STUDENTS in rule_text
    assert "n_cells_rule2_not_evaluable=0" in rule_text
    # one generated line on the overlap of the arrival and the status control
    assert sum(line.startswith("# Overlap") for line in rule_text.splitlines()) == 1
    gate_text = (out / "student_level_gate.csv").read_text(encoding="utf-8")
    assert "sha256" in gate_text and "Phase 1" in gate_text
    # the enrollment semester of the local sites is printed; enrollment is no bound
    assert "WS 2099/00" in gate_text
    assert "bound" not in gate_text

    rule = pd.read_csv(out / "student_control_build_rule.csv", comment="#")
    assert set(rule.loc[rule["control"] == "work_leg", "verdict"]) == {"build_candidate"}
    gate = pd.read_csv(out / "student_level_gate.csv", comment="#", dtype={"code": str})
    # zero-padded codes survive the round trip (the inputs are read as text, not numbers)
    assert gate["code"].tolist() == list(ZGB) + ["03ZGB"]
    assert gate.set_index("code").loc["03101", "census_estimate_students_20_29_persons"] == \
        pytest.approx(600)
    comparison = pd.read_csv(out / "student_comparison_only.csv", comment="#")
    assert set(comparison["variable"]) == set(E.COMPARISON_ONLY)
    assert set(comparison["source_table"]) == {"srv2023_student_comparison.csv"}
