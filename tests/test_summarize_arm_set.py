"""The pooled mode share, zone-group and free-share tables of an arm set (``scripts/parking/summarize_arm_set.py``, issue #436).

Everything is synthetic: modestats tables, outcome reports of CSV v4, a pipeline log and a resolved config written per test,
every expected number derived in a comment. The committed tariff table is read only for the zone types of the real zone ids
the synthetic reports use. A drift guard pins the one production log line the script parses to its source file.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "parking" / "summarize_arm_set.py"
COMMITTED_TARIFFS = REPO / "eqasim-data" / "data" / "braunschweig" / "parking" / "parking_tariffs_2026.csv"

V4_HEADER = ("outcome,zone_id,purpose,count,share,garage_probability_sum,stays_with_garages_in_range,"
             "garage_probability_sum_paid,garage_probability_sum_free,street_infeasible_count,no_feasible_option_count,"
             "expected_cost_cents_sum,ev_exempt_count")

#: (outcome, zone id, purpose, count, garage paid, garage free, street infeasible, no feasible option, expected cost sum, EV exempt).
#: bs_zone_ia and bs_zone_ib are street_paid zones, tu_zentralcampus a campus zone, wob_tarifzone_1 a street_paid zone
#: of the group "wob", se_am_markt a street_paid zone of its own group, pe_innenstadt a street_paid zone of "other".
ROWS_V3 = [
    ("NO_ZONE", "", "work", 50, 0.0, 0.0, 0, 0, 0.0, 0),
    ("EMPLOYER_FREE", "bs_zone_ia", "work", 30, 0.0, 0.0, 0, 0, 0.0, 0),
    ("PAID_EXPECTED", "bs_zone_ia", "work", 20, 8.0, 2.0, 4, 0, 4000.0, 0),
    ("PAID_METERED", "bs_zone_ia", "shop", 10, 0.0, 0.0, 0, 0, 2000.0, 0),
    ("HOME", "bs_zone_ia", "home", 20, 0.0, 0.0, 0, 0, 0.0, 0),
    ("EV_EXEMPT", "bs_zone_ia", "leisure", 5, 0.0, 0.0, 0, 0, 0.0, 5),
    ("NO_FEASIBLE_OPTION", "bs_zone_ia", "leisure", 5, 0.0, 0.0, 0, 5, 1500.0, 0),
    ("PAID_EXPECTED", "bs_zone_ib", "education", 10, 3.0, 0.0, 0, 0, 1200.0, 0),
    ("EMPLOYER_FREE", "wob_tarifzone_1", "work", 8, 0.0, 0.0, 0, 0, 0.0, 0),
    ("PAID_GARAGE", "wob_tarifzone_1", "shop", 2, 1.0, 0.0, 0, 0, 500.0, 0),
    ("EMPLOYER_FREE", "se_am_markt", "work", 6, 0.0, 0.0, 0, 0, 0.0, 0),
    ("PAID_METERED", "pe_innenstadt", "other", 4, 0.0, 0.0, 0, 0, 800.0, 0),
    ("EMPLOYER_FREE", "tu_zentralcampus", "work", 7, 0.0, 0.0, 0, 0, 0.0, 0),
]
ROWS_V3_SECOND_ITERATION = [
    ("NO_ZONE", "", "work", 40, 0.0, 0.0, 0, 0, 0.0, 0),
    ("EMPLOYER_FREE", "bs_zone_ia", "work", 10, 0.0, 0.0, 0, 0, 0.0, 0),
    ("PAID_EXPECTED", "bs_zone_ia", "work", 10, 4.0, 1.0, 2, 0, 2000.0, 0),
]

DRAW_LINE = ("15:52:14 | INFO    | parking.attach   | [parking] free-parking draw (RandomState(random_seed 1234 + 7371), "
             "shift {shift}): 510/12046 persons (4.2 %) have a work/education activity in a street_paid or resident_zone zone, "
             "{free} of them ({percent} %) park free; 226/12046 persons (1.9 %) have one in a campus zone, 47 of them (20.8 %) "
             "park free; parkingFree on 620 of 929 eligible activities (paid zones 549 of 630, campus 71 of 299)")


@pytest.fixture(scope="module")
def summary():
    spec = importlib.util.spec_from_file_location("summarize_arm_set_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_report(path: Path, rows) -> Path:
    total = sum(row[3] for row in rows)
    lines = [V4_HEADER]
    for outcome, zone, purpose, count, paid, free, infeasible, no_option, cost_sum, ev in rows:
        lines.append(f"{outcome},{zone},{purpose},{count},{count / total:.6f},{paid + free:.6f},{count if paid + free else 0},"
                     f"{paid:.6f},{free:.6f},{infeasible},{no_option},{cost_sum:.6f},{ev}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_modestats(path: Path, shares_by_iteration: dict) -> Path:
    modes = ["car", "pt", "walk"]
    lines = ["iteration;" + ";".join(modes)]
    for iteration, shares in shares_by_iteration.items():
        lines.append(f"{iteration};" + ";".join(repr(share) for share in shares))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def make_arm(root: Path, label: str, *, shares, with_outcomes=True, shift=0.0, free_persons=445, percent="87.3",
             log_lines=None) -> Path:
    arm = root / label
    write_modestats(arm / "matsim_output" / "modestats.csv", shares)
    if with_outcomes:
        write_report(arm / "matsim_output" / "ITERS" / "it.1" / "1.parking_outcomes.csv", ROWS_V3)
        write_report(arm / "matsim_output" / "ITERS" / "it.2" / "2.parking_outcomes.csv", ROWS_V3_SECOND_ITERATION)
        lines = log_lines if log_lines is not None else ["unrelated line", DRAW_LINE.format(shift=f"{shift:+.3f}", free=free_persons,
                                                                                            percent=percent)]
        (arm / f"parking_v3_{label}.log").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (arm / f"parking_v3_{label}_resolved.yml").write_text(f"config:\n  random_seed: 1234\n  {summary_key()}: {shift}\n",
                                                              encoding="utf-8")
    return arm


def summary_key() -> str:
    return "parking_workplace_free_share_shift"


# ------------------------------------------------------------------------------------------------------------- mode shares


def test_pooled_mode_shares_are_mean_sample_sd_and_differences_in_percentage_points(summary):
    frames = {
        "off": pd.DataFrame({"iteration": [0, 1, 2], "car": [0.9, 0.5, 0.6], "pt": [0.05, 0.3, 0.2], "walk": [0.05, 0.2, 0.2]}),
        "v3": pd.DataFrame({"iteration": [0, 1, 2], "car": [0.9, 0.4, 0.5], "pt": [0.05, 0.3, 0.3], "walk": [0.05, 0.3, 0.2]}),
    }
    table = summary.pooled_mode_shares(frames, [1, 2], baseline_arm="v3", off_arm="off")
    row = table[(table["arm"] == "off") & (table["mode"] == "car")].iloc[0]
    # off car over iterations 1 and 2: mean (0.5 + 0.6) / 2 = 0.55, sample SD sqrt(((0.05)^2 * 2) / 1) = 0.0707107
    assert row["mean_share"] == pytest.approx(0.55)
    assert row["sd_share_within_run"] == pytest.approx(np.sqrt(0.005))
    # v3 car mean 0.45: off minus v3 = +10 pp, off minus off = 0
    assert row["diff_to_baseline_pp"] == pytest.approx(10.0)
    assert row["diff_to_off_pp"] == pytest.approx(0.0)
    v3_pt = table[(table["arm"] == "v3") & (table["mode"] == "pt")].iloc[0]
    assert v3_pt["mean_share"] == pytest.approx(0.3) and v3_pt["sd_share_within_run"] == pytest.approx(0.0)
    assert v3_pt["diff_to_baseline_pp"] == pytest.approx(0.0) and v3_pt["diff_to_off_pp"] == pytest.approx(5.0)
    assert set(table["n_iterations"]) == {2} and set(table["iterations"]) == {"1-2"}
    # the iteration 0 value (0.9) is outside the selection and changes nothing
    assert table[table["mode"] == "car"]["mean_share"].max() < 0.6


def test_a_missing_baseline_arm_leaves_its_difference_empty(summary):
    frames = {"v3": pd.DataFrame({"iteration": [1, 2], "car": [0.5, 0.5], "pt": [0.5, 0.5]})}
    table = summary.pooled_mode_shares(frames, [1, 2], baseline_arm="v3", off_arm="off")
    assert table["diff_to_off_pp"].isna().all() and (table["diff_to_baseline_pp"] == 0.0).all()


def test_pooling_fails_loudly_for_a_missing_iteration_a_single_iteration_and_different_modes(summary):
    frame = pd.DataFrame({"iteration": [1, 2], "car": [0.5, 0.5], "pt": [0.5, 0.5]})
    with pytest.raises(summary.InputError, match="no row for iteration"):
        summary.pooled_mode_shares({"a": frame}, [1, 2, 3], baseline_arm="a", off_arm="a")
    with pytest.raises(ValueError, match="at least two iterations"):
        summary.pooled_mode_shares({"a": frame}, [1], baseline_arm="a", off_arm="a")
    other = pd.DataFrame({"iteration": [1, 2], "car": [0.5, 0.5], "bike": [0.5, 0.5]})
    with pytest.raises(summary.InputError, match="differ from those of the first arm"):
        summary.pooled_mode_shares({"a": frame, "b": other}, [1, 2], baseline_arm="a", off_arm="a")


def test_the_modestats_reader_rejects_shares_that_do_not_sum_to_one_and_a_wrong_layout(summary, tmp_path):
    good = write_modestats(tmp_path / "good.csv", {0: (0.5, 0.3, 0.2), 1: (0.4, 0.4, 0.2)})
    assert summary.read_modestats(good)["iteration"].tolist() == [0, 1]
    bad = write_modestats(tmp_path / "bad.csv", {0: (0.5, 0.3, 0.3)})
    with pytest.raises(summary.InputError, match="do not sum to 1"):
        summary.read_modestats(bad)
    comma = tmp_path / "comma.csv"
    comma.write_text("iteration,car\n0,1.0\n", encoding="utf-8")
    with pytest.raises(summary.InputError, match="column 'iteration'"):
        summary.read_modestats(comma)
    with pytest.raises(summary.InputError, match="missing"):
        summary.read_modestats(tmp_path / "absent.csv")


# --------------------------------------------------------------------------------------------------------------- zone groups


def test_zone_groups_follow_the_patterns_in_order_and_everything_else_is_other(summary):
    assert [summary.zone_group(zone) for zone in ("bs_zone_ia", "bs_zone_ib", "bs_zone_ia_sued", "bs_zone_ii", "wob_tarifzone_2",
                                                  "wob_area_727", "bh_kurpark", "se_am_markt", "pe_innenstadt",
                                                  "tu_zentralcampus")] == [
        "bs_zone_ia", "bs_zone_ib", "bs_zone_ia_sued", "bs_zone_ii", "wob", "wob", "bh", "se_am_markt", "other", "other"]
    assert summary.group_order() == ["bs_zone_ia", "bs_zone_ib", "bs_zone_ia_sued", "bs_zone_ii", "wob", "bh", "se_am_markt",
                                     "other"]


def pooled_frame(summary, tmp_path, *row_sets):
    paths = [write_report(tmp_path / f"{index}.csv", rows) for index, rows in enumerate(row_sets, start=1)]
    return summary.compare.pool_outcome_reports([summary.compare.read_parking_outcomes(path) for path in paths])[0]


def test_the_zone_group_table_sums_the_pooled_calls_per_group_and_purpose_class(summary, tmp_path):
    frame = pooled_frame(summary, tmp_path, ROWS_V3, ROWS_V3_SECOND_ITERATION)
    table = summary.zone_group_table("v3", frame, [1, 2])
    ia = table[table["zone_group"] == "bs_zone_ia"].set_index("purpose_class")
    # all purposes in Ia: iteration 1 has 30 + 20 + 10 + 20 + 5 + 5 = 90 calls, iteration 2 has 10 + 10 = 20; NO_ZONE has no zone
    assert ia.loc["all", "priced_calls"] == 110
    # paid: PAID_EXPECTED 20 + 10, PAID_METERED 10 and NO_FEASIBLE_OPTION 5 (a PAID_OUTCOME of the comparison script) = 45
    assert ia.loc["all", "paid_calls"] == 45 and ia.loc["all", "paid_share"] == pytest.approx(45 / 110)
    # garage options: paid 8 + 4 = 12 and free 2 + 1 = 3 expected options over the 110 calls
    assert ia.loc["all", "garage_share_paid_options"] == pytest.approx(12 / 110)
    assert ia.loc["all", "garage_share_free_options"] == pytest.approx(3 / 110)
    # unscaled cost cents: 4000 + 2000 + 1500 + 2000 = 9500 over 110 calls
    assert ia.loc["all", "mean_expected_cost_cents"] == pytest.approx(9500 / 110)
    assert (ia.loc["all", "ev_exempt_count"], ia.loc["all", "street_infeasible_count"],
            ia.loc["all", "no_feasible_option_count"], ia.loc["all", "employer_free_count"]) == (5, 6, 5, 40)
    # work and education in Ia: 30 + 20 + 10 + 10 = 70 calls; the other purposes (shop, leisure) 10 + 5 + 5 = 20; home 20 is in "all" only
    assert ia.loc["work_education", "priced_calls"] == 70 and ia.loc["other_purposes", "priced_calls"] == 20
    assert ia.loc["work_education", "street_infeasible_count"] == 6
    assert ia.loc["other_purposes", "ev_exempt_count"] == 5 and ia.loc["other_purposes", "no_feasible_option_count"] == 5
    assert ia.loc["all", "zone_ids"] == "bs_zone_ia"


def test_groups_without_a_call_have_zero_calls_and_empty_ratios_and_other_collects_the_rest(summary, tmp_path):
    frame = pooled_frame(summary, tmp_path, ROWS_V3)
    table = summary.zone_group_table("v3", frame, [1])
    sued = table[(table["zone_group"] == "bs_zone_ia_sued") & (table["purpose_class"] == "all")].iloc[0]
    assert sued["priced_calls"] == 0 and np.isnan(sued["paid_share"]) and np.isnan(sued["mean_expected_cost_cents"])
    other = table[(table["zone_group"] == "other") & (table["purpose_class"] == "all")].iloc[0]
    assert other["zone_ids"] == "pe_innenstadt, tu_zentralcampus" and other["priced_calls"] == 11
    # no call outside a zone is part of a group: the groups hold every zone call exactly once
    assert table[table["purpose_class"] == "all"]["priced_calls"].sum() == frame[frame["outcome"] != "NO_ZONE"]["count"].sum()


def test_the_delta_table_is_the_arm_minus_the_baseline_on_the_same_rows(summary, tmp_path):
    baseline_frame = pooled_frame(summary, tmp_path / "a", ROWS_V3)
    changed = [row if row[:3] != ("PAID_METERED", "bs_zone_ia", "shop") else ("PAID_METERED", "bs_zone_ia", "shop", 10, 0.0, 0.0,
                                                                              0, 0, 3000.0, 0) for row in ROWS_V3]
    arm_frame = pooled_frame(summary, tmp_path / "b", changed)
    table = pd.concat([summary.zone_group_table("v3", baseline_frame, [1]), summary.zone_group_table("x", arm_frame, [1])],
                      ignore_index=True)
    deltas = summary.zone_group_deltas(table, "v3")
    row = deltas[(deltas["zone_group"] == "bs_zone_ia") & (deltas["purpose_class"] == "all")].iloc[0]
    # one call costs 1000 cents more, spread over the 90 calls of Ia in iteration 1
    assert row["arm"] == "x" and row["baseline_arm"] == "v3" and row["priced_calls"] == row["baseline_priced_calls"] == 90
    assert row["mean_expected_cost_cents_diff"] == pytest.approx(1000 / 90)
    assert row["paid_share_diff_pp"] == pytest.approx(0.0) and row["ev_exempt_count_diff"] == 0
    assert "v3" not in set(deltas["arm"])
    assert summary.zone_group_deltas(table[table["arm"] == "x"], "v3").empty


# ---------------------------------------------------------------------------------------------------------------- free share


def test_the_free_draw_line_is_parsed_and_zero_or_two_lines_are_errors(summary):
    line = DRAW_LINE.format(shift="-0.100", free=390, percent="76.5")
    draw = summary.parse_free_draw(["noise", line], "log")
    assert (draw["shift"], draw["seed"], draw["offset"], draw["paid_persons"], draw["free_persons"], draw["persons"]) == (
        "-0.100", "1234", "7371", "510", "390", "12046")
    assert (draw["campus_persons"], draw["campus_free_persons"]) == ("226", "47")
    with pytest.raises(summary.InputError, match="no '\\[parking\\] free-parking draw"):
        summary.parse_free_draw(["noise"], "log")
    with pytest.raises(summary.InputError, match="2 free-parking draw summary lines"):
        summary.parse_free_draw([line, line], "log")


def test_the_free_draw_format_the_script_parses_still_exists_in_the_production_source():
    """Drift guard: a reworded production line must fail here, not on the server."""
    text = (REPO / "braunschweig" / "parking" / "attach.py").read_text(encoding="utf-8")
    for needle in ("free-parking draw (RandomState(random_seed %d + %d), shift %+.3f): %d/%d persons (%.1f %%) have a ",
                   "work/education activity in a street_paid or resident_zone zone, %d of them (%.1f %%) park free; %d/%d ",
                   "persons (%.1f %%) have one in a campus zone, %d of them (%.1f %%) park free"):
        assert needle in text, f"braunschweig/parking/attach.py no longer contains {needle!r}: update summarize_arm_set.py"


def test_the_free_share_row_pools_the_employer_free_calls_of_the_free_draw_zones_and_of_all_zones(summary, tmp_path):
    frame = pooled_frame(summary, tmp_path, ROWS_V3, ROWS_V3_SECOND_ITERATION)
    zone_types = {"bs_zone_ia": "street_paid", "bs_zone_ib": "street_paid", "wob_tarifzone_1": "street_paid",
                  "se_am_markt": "street_paid", "pe_innenstadt": "street_paid", "tu_zentralcampus": "campus"}
    draw = summary.parse_free_draw([DRAW_LINE.format(shift="+0.000", free=445, percent="87.3")], "log")
    row = summary.free_share_row("v3", frame, zone_types, draw, 0.0, [1, 2])
    # work and education calls in street zones: Ia 30 + 20 + 10 + 10, Ib 10, wob 8, se_am_markt 6 = 94; EMPLOYER_FREE 30 + 10 + 8 + 6 = 54
    assert row["work_education_calls_in_draw_zones"] == 94 and row["employer_free_calls_in_draw_zones"] == 54
    assert row["call_employer_free_share_draw_zones"] == pytest.approx(54 / 94)
    # the campus zone adds 7 EMPLOYER_FREE work calls: 101 calls, 61 employer free
    assert row["work_education_calls_in_all_zones"] == 101 and row["employer_free_calls_in_all_zones"] == 61
    assert row["call_employer_free_share_all_zones"] == pytest.approx(61 / 101)
    assert (row["configured_shift"], row["logged_shift"], row["persons_in_paid_zones"], row["persons_drawn_free"]) == (
        0.0, 0.0, 510, 445)
    assert row["person_free_share"] == pytest.approx(445 / 510) and row["campus_persons_drawn_free"] == 47
    with pytest.raises(summary.InputError, match="without a tariff row"):
        summary.free_share_row("v3", frame, {"bs_zone_ia": "street_paid"}, draw, 0.0, [1, 2])


def test_the_configured_shift_is_read_from_the_config_section_and_must_be_a_number(summary, tmp_path):
    path = tmp_path / "resolved.yml"
    path.write_text("config:\n  parking_workplace_free_share_shift: -0.2\n", encoding="utf-8")
    assert summary.read_configured_shift(path) == -0.2
    path.write_text("config:\n  random_seed: 1\n", encoding="utf-8")
    with pytest.raises(summary.InputError, match="no 'config: parking_workplace_free_share_shift'"):
        summary.read_configured_shift(path)
    path.write_text("config:\n  parking_workplace_free_share_shift: 'x'\n", encoding="utf-8")
    with pytest.raises(summary.InputError, match="must be a number"):
        summary.read_configured_shift(path)


# --------------------------------------------------------------------------------------------------------------- the arm set


@pytest.fixture()
def arm_set(tmp_path):
    shares = {0: (0.6, 0.2, 0.2), 1: (0.5, 0.3, 0.2), 2: (0.4, 0.3, 0.3)}
    arms = [
        make_arm(tmp_path, "off", shares=shares, with_outcomes=False),
        make_arm(tmp_path, "v3", shares=shares),
        make_arm(tmp_path, "v3_free_shift_-0.1", shares={0: (0.6, 0.2, 0.2), 1: (0.45, 0.3, 0.25), 2: (0.4, 0.3, 0.3)},
                 shift=-0.1, free_persons=390, percent="76.5"),
    ]
    return arms


def arm_inputs(summary, arms):
    return [summary.ArmInputs(path.name, path, path.name != "off") for path in arms]


@pytest.mark.skipif(not COMMITTED_TARIFFS.is_file(), reason="the committed tariff table is not in this checkout")
def test_the_arm_set_end_to_end_writes_four_tables_and_a_provenance_with_hashes(summary, arm_set, tmp_path):
    result = summary.summarize(arm_inputs(summary, arm_set), iterations=[1, 2], tariffs_path=COMMITTED_TARIFFS)
    assert set(result["modestats_pooled"]["arm"]) == {"off", "v3", "v3_free_shift_-0.1"}
    assert set(result["zone_groups"]["arm"]) == {"v3", "v3_free_shift_-0.1"}
    assert result["free_share"]["configured_shift"].tolist() == [0.0, -0.1]
    assert result["free_share"]["logged_shift"].tolist() == [0.0, -0.1]
    assert result["free_share"]["persons_drawn_free"].tolist() == [445, 390]
    paths = summary.write_tables(result, tmp_path / "out", "prefix")
    assert sorted(path.name for path in paths) == ["prefix_free_share.csv", "prefix_modestats_pooled.csv",
                                                   "prefix_provenance.json", "prefix_zone_group_deltas.csv",
                                                   "prefix_zone_groups.csv"]
    provenance = json.loads((tmp_path / "out" / "prefix_provenance.json").read_text(encoding="utf-8"))
    assert provenance["iterations"] == [1, 2] and len(provenance["arms"]["v3"]["outcome_reports"]) == 2
    assert len(provenance["arms"]["v3"]["modestats"]["sha256"]) == 64 and "outcome_reports" not in provenance["arms"]["off"]
    # an existing output is never replaced silently
    with pytest.raises(FileExistsError, match="--overwrite"):
        summary.write_tables(result, tmp_path / "out", "prefix")
    assert len(summary.write_tables(result, tmp_path / "out", "prefix", overwrite=True)) == 5
    # the fixed decimals make the file reproducible: a second run gives the identical text
    first = (tmp_path / "out" / "prefix_zone_groups.csv").read_bytes()
    summary.write_tables(result, tmp_path / "out", "prefix", overwrite=True)
    assert (tmp_path / "out" / "prefix_zone_groups.csv").read_bytes() == first


@pytest.mark.skipif(not COMMITTED_TARIFFS.is_file(), reason="the committed tariff table is not in this checkout")
def test_a_zone_arm_without_a_draw_line_fails_naming_the_log(summary, tmp_path):
    arm = make_arm(tmp_path, "v3", shares={0: (0.5, 0.3, 0.2), 1: (0.5, 0.3, 0.2), 2: (0.5, 0.3, 0.2)}, log_lines=["nothing here"])
    with pytest.raises(summary.InputError, match="no '\\[parking\\] free-parking draw"):
        summary.summarize([summary.ArmInputs("v3", arm, True)], iterations=[1, 2], tariffs_path=COMMITTED_TARIFFS)


@pytest.mark.skipif(not COMMITTED_TARIFFS.is_file(), reason="the committed tariff table is not in this checkout")
def test_duplicate_labels_missing_reports_and_two_logs_are_errors(summary, tmp_path):
    shares = {0: (0.5, 0.3, 0.2), 1: (0.5, 0.3, 0.2), 2: (0.5, 0.3, 0.2)}
    arm = make_arm(tmp_path, "v3", shares=shares)
    with pytest.raises(ValueError, match="duplicate arm label"):
        summary.summarize([summary.ArmInputs("v3", arm, True)] * 2, iterations=[1, 2], tariffs_path=COMMITTED_TARIFFS)
    with pytest.raises(FileNotFoundError, match="outcome report missing"):
        summary.summarize([summary.ArmInputs("v3", arm, True)], iterations=[1, 2, 3], tariffs_path=COMMITTED_TARIFFS)
    (arm / "second.log").write_text("x\n", encoding="utf-8")
    with pytest.raises(summary.InputError, match="exactly one pipeline log"):
        summary.summarize([summary.ArmInputs("v3", arm, True)], iterations=[1, 2], tariffs_path=COMMITTED_TARIFFS)
    with pytest.raises(summary.InputError, match="not found"):
        summary.summarize([summary.ArmInputs("v3", tmp_path / "absent", True)], iterations=[1, 2],
                          tariffs_path=COMMITTED_TARIFFS)
