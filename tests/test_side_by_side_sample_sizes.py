"""The side-by-side table of the small and large sample arm pairs (``scripts/parking/side_by_side_sample_sizes.py``, issue #436).

Synthetic tables in the formats of ``compare_parking_targets.py`` and ``summarize_arm_set.py``; every expected cell is a
number written into those tables and read back, so a test fails when a value is read from the wrong row, arm or column.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "parking" / "side_by_side_sample_sizes.py"
COMPARE_COLUMNS = ["arm", "metric", "unit", "universe", "universe_size", "model", "reference", "delta_pp", "reference_source",
                   "universe_note"]
ZONE_COLUMNS = ["arm", "zone_group", "purpose_class", "iterations", "zone_ids", "priced_calls", "paid_calls", "paid_share",
                "garage_share_paid_options", "garage_share_free_options", "mean_expected_cost_cents", "ev_exempt_count",
                "street_infeasible_count", "no_feasible_option_count", "employer_free_count"]


@pytest.fixture(scope="module")
def side():
    spec = importlib.util.spec_from_file_location("side_by_side_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def compare_row(arm, metric, size, model):
    return [arm, metric, "share", "u", size, model, "", "", "src", "note"]


def zone_row(arm, group, calls, paid, garage_paid, garage_free, cost, ev, nfo, purpose="all"):
    return [arm, group, purpose, "51-60", group, calls, round(paid * calls), paid, garage_paid, garage_free, cost, ev, 0, nfo, 0]


def write_set(folder: Path, token: str, v3: str, off: str, scale: int, metrics):
    """One sample: ``scale`` shifts the model numbers so a mix-up of the two samples shows. As in the real table every arm has a
    row of every metric and the off arm has no value for the metrics that need the parking outcomes."""
    off_values = {"car_trip_share_ending_in_ia_ib": 0.50 + 0.001 * scale, "car_trip_share_all_trips": 0.53 + 0.001 * scale}
    v3_values = {"car_trip_share_ending_in_ia_ib": 0.40 + 0.001 * scale, "car_trip_share_all_trips": 0.52 + 0.001 * scale,
                 "paid_share_ia_ib_all_purposes": 0.44 + 0.001 * scale}
    compare = []
    for metric, _ in metrics:
        compare.append(compare_row(v3, metric, 30 * scale, v3_values.get(metric, 0.10 + 0.001 * scale)))
        compare.append(compare_row(off, metric, 100 * scale if metric in off_values else "", off_values.get(metric, "")))
    pd.DataFrame(compare, columns=COMPARE_COLUMNS).to_csv(folder / f"parking_targets_comparison_{token}.csv", index=False)
    modes = []
    for arm, car in ((off, 0.53), (v3, 0.52)):
        modes.append({"arm": arm, "mode": "car", "iterations": "51-60", "n_iterations": 10, "mean_share": car + 0.001 * scale,
                      "sd_share_within_run": 0.0015, "diff_to_baseline_pp": 0.0, "diff_to_off_pp": 0.0})
    pd.DataFrame(modes).to_csv(folder / f"arm_set_summary_{token}_modestats_pooled.csv", index=False)
    zones = [zone_row(v3, "bs_zone_ia", 200 * scale, 0.5, 0.4, 0.0, 150.0 + scale, 0, 0),
             zone_row(v3, "bs_zone_ib", 60 * scale, 0.2, 0.1, 0.0, 80.0 + scale, 0, 0),
             zone_row(v3, "wob", 80 * scale, 0.4, 0.2, 0.05, 110.0 + scale, 0, 0),
             zone_row(v3, "other", 300 * scale, 0.3, 0.05, 0.04, 40.0 + scale, 2 * scale, 1),
             zone_row(v3, "other", 100 * scale, 0.3, 0.05, 0.04, 40.0 + scale, 9, 9, purpose="work_education")]
    pd.DataFrame(zones, columns=ZONE_COLUMNS).to_csv(folder / f"arm_set_summary_{token}_zone_groups.csv", index=False)
    free = [{"arm": v3, "person_free_share": 0.85 + 0.001 * scale, "persons_in_paid_zones": 500 * scale,
             "call_employer_free_share_draw_zones": 0.83 + 0.001 * scale, "work_education_calls_in_draw_zones": 250 * scale}]
    pd.DataFrame(free).to_csv(folder / f"arm_set_summary_{token}_free_share.csv", index=False)


@pytest.fixture()
def folder(tmp_path, side):
    write_set(tmp_path, "1pct_x", "v3", "off", 1, side.COMPARISON_METRICS)
    write_set(tmp_path, "25pct_x", "v3_25pct", "off_25pct", 25, side.COMPARISON_METRICS)
    return tmp_path


def build(side, folder):
    return side.build_side_by_side(folder, "1pct_x", "25pct_x", arms_small=("v3", "off"), arms_large=("v3_25pct", "off_25pct"))


def cell(table, metric, column):
    row = table[table["metric"] == metric]
    assert len(row) == 1, metric
    return row.iloc[0][column]


def test_every_value_is_read_from_its_own_sample_arm_and_row(side, folder):
    table = build(side, folder)
    metric = "car_trip_share_ending_in_ia_ib"
    assert cell(table, metric, "off_small") == pytest.approx(0.501) and cell(table, metric, "v3_small") == pytest.approx(0.401)
    assert cell(table, metric, "off_large") == pytest.approx(0.525) and cell(table, metric, "v3_large") == pytest.approx(0.425)
    assert cell(table, metric, "n_v3_small") == 30 and cell(table, metric, "n_v3_large") == 750
    # a share's change is in percentage points: (0.425 - 0.401) * 100
    assert cell(table, metric, "v3_large_minus_small") == pytest.approx(2.4)
    assert cell(table, "car_share_modestats_mean", "v3_large") == pytest.approx(0.545)


def test_an_arm_without_the_metric_has_an_empty_cell_not_a_zero(side, folder):
    table = build(side, folder)
    assert pd.isna(cell(table, "paid_share_ia_ib_all_purposes", "off_small"))
    assert pd.isna(cell(table, "paid_share_ia_ib_all_purposes", "off_large"))
    assert cell(table, "paid_share_ia_ib_all_purposes", "v3_small") == pytest.approx(0.441)


def test_zone_group_rows_use_the_all_purposes_class_and_totals_sum_the_groups(side, folder):
    table = build(side, folder)
    assert cell(table, "zone_bs_zone_ia_paid_share", "v3_large") == pytest.approx(0.5)
    assert cell(table, "zone_bs_zone_ia_mean_expected_cost_cents", "v3_small") == pytest.approx(151.0)
    assert cell(table, "zone_bs_zone_ia_mean_expected_cost_cents", "v3_large_minus_small") == pytest.approx(24.0)  # cents
    assert cell(table, "zone_other_garage_share_free_options", "v3_large") == pytest.approx(0.04)
    # counts: the all-purposes rows of all groups only (the work_education row of the group 'other' must not be added)
    assert cell(table, "ev_exempt_count_all_zone_groups", "v3_small") == 2
    assert cell(table, "ev_exempt_count_all_zone_groups", "v3_large") == 50
    assert cell(table, "no_feasible_option_count_all_zone_groups", "v3_large") == 1
    assert pd.isna(cell(table, "ev_exempt_count_all_zone_groups", "v3_large_minus_small"))   # counts scale with the sample


def test_free_share_rows_and_the_units(side, folder):
    table = build(side, folder)
    assert cell(table, "person_free_share_draw", "v3_large") == pytest.approx(0.875)
    assert cell(table, "call_employer_free_share_draw_zones", "v3_small") == pytest.approx(0.831)
    assert set(table["unit"]) <= {"share", "cents", "count"}


def test_a_missing_metric_or_arm_is_an_error_naming_it(side, folder):
    with pytest.raises(side.InputError, match="no_such_arm"):
        side.build_side_by_side(folder, "1pct_x", "25pct_x", arms_small=("v3", "off"), arms_large=("no_such_arm", "off_25pct"))
    broken = pd.read_csv(folder / "parking_targets_comparison_25pct_x.csv", keep_default_na=False)
    broken[broken["metric"] != "car_trip_share_all_trips"].to_csv(folder / "parking_targets_comparison_25pct_x.csv", index=False)
    with pytest.raises(side.InputError, match="car_trip_share_all_trips"):
        build(side, folder)


def test_the_main_writes_a_deterministic_table_and_a_provenance(side, folder):
    argv = ["--folder", str(folder), "--set-small", "1pct_x", "--set-large", "25pct_x", "--arms-small", "v3,off",
            "--arms-large", "v3_25pct,off_25pct", "--out-prefix", "side"]
    assert side.main(argv) == 0
    out = folder / "side_side_by_side.csv"
    first = out.read_bytes()
    provenance = json.loads((folder / "side_side_by_side_provenance.json").read_text(encoding="utf-8"))
    assert len(provenance["inputs"]) == 8 and all(len(item["sha256"]) == 64 for item in provenance["inputs"].values())
    assert "code_state" in provenance
    with pytest.raises(SystemExit, match="--overwrite"):
        side.main(argv)
    assert side.main(argv + ["--overwrite"]) == 0
    assert out.read_bytes() == first
