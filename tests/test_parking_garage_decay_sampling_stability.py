"""The sampling stability of the calibrated decay lengths (``scripts/parking/garage_decay_sampling_stability.py``, issue #436).

Pinned on a small synthetic universe (the helpers of the calibration tests): the split-half and bootstrap weights are seeded and
disjoint / complete, the same seed gives the same lambda, the person weighting with every weight 1 is the calibration module's own
mean, a known lambda is recovered, and the standard deviation formulas (SD(A - B) / sqrt(2), the 25 % and 100 % extrapolations) are
checked against numbers worked out by hand and against a simulation with a known sigma. The committed measurement table is tied to
its inputs (the header pins) and to the calibration table, and its summary rows are recomputed from its raw run rows.
"""
from __future__ import annotations

import importlib.util
import math
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from tests import test_parking_garage_calibration as calibration_tests
from tests import test_parking_garage_decay_config as config_tests

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "parking" / "garage_decay_sampling_stability.py"
TABLE = REPO / "eqasim-data" / "data" / "braunschweig" / "parking" / "parking_garage_decay_sampling_stability_2026.csv"
CALIBRATION_RECORD = REPO / "docs" / "registry" / "data" / "parking_garage_decay_calibration_2026.yml"


@pytest.fixture(scope="module")
def ss():
    spec = importlib.util.spec_from_file_location("garage_decay_sampling_stability_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- a synthetic universe


def _universe(ss, persons=24):
    """One stay per person at the distances 150 ... 1,050 m from the garage fx_g01_core (the street is the other option)."""
    stays = [calibration_tests._stay([("fx_g01_core", 150.0 + 40.0 * (index % 23))]) for index in range(persons)]
    sample = ss.UniverseSample("visitors", tuple(stays), np.arange(persons), "all_options", False, 0.25)
    return ss.StayShares(sample, {"fx_g01_core": True}, 0.0)


def test_the_script_names_the_measures_of_the_calibration_module(ss):
    assert ss.cgd.MEASURE_ALL_OPTIONS == "all_options" and ss.UNIVERSES == ("visitors", "commuters")


# --------------------------------------------------------------------------- the weights


def test_the_split_halves_are_disjoint_complete_equal_and_seeded(ss):
    a, b = ss.split_weights(101, 7)
    assert set(np.unique(a)) == {0.0, 1.0} and (a + b == 1.0).all() and a.sum() == 50 and b.sum() == 51
    again_a, again_b = ss.split_weights(101, 7)
    assert (a == again_a).all() and (b == again_b).all()
    other_a, _ = ss.split_weights(101, 8)
    assert not (a == other_a).all()


def test_the_bootstrap_weights_count_the_draws_with_replacement_and_are_seeded(ss):
    w = ss.bootstrap_weights(200, ss.BOOTSTRAP_SEED_OFFSET + 3)
    assert w.sum() == 200 and (w == np.floor(w)).all() and w.max() >= 2 and (w == 0).any()   # some drawn twice, some never
    assert (w == ss.bootstrap_weights(200, ss.BOOTSTRAP_SEED_OFFSET + 3)).all()
    assert not (w == ss.bootstrap_weights(200, ss.BOOTSTRAP_SEED_OFFSET + 4)).all()
    assert ss.BOOTSTRAP_SEED_OFFSET >= ss.DEFAULT_SPLIT_SEEDS     # the seeds of the two measurements never overlap


# --------------------------------------------------------------------------- the weighted mean and the calibration


def test_the_person_weighted_mean_with_unit_weights_is_the_calibration_modules_mean_option_share(ss):
    shares = _universe(ss)
    difference = ss.check_unit_weights_agree(shares, 400.0, 24)
    assert difference <= ss.UNIT_WEIGHT_AGREEMENT
    # a zero weight leaves the person out: the mean of the first twelve stays only
    weights = np.array([1.0] * 12 + [0.0] * 12)
    first_twelve = ss.cgd.mean_option_share(list(shares.sample.stays[:12]), 400.0, 0.0, "all_options", {"fx_g01_core": True})
    assert shares.weighted_mean(weights, 400.0) == pytest.approx(first_twelve, abs=1e-12)
    # a weight of 3 counts the stay three times
    doubled = np.array([3.0] + [1.0] * 23)
    manual = (3 * shares.arrays(400.0)[0][0] + shares.arrays(400.0)[0][1:].sum()) / 26.0
    assert shares.weighted_mean(doubled, 400.0) == pytest.approx(manual, abs=1e-12)
    with pytest.raises(ValueError, match="no stay"):
        shares.weighted_mean(np.zeros(24), 400.0)


def test_the_weighted_calibration_recovers_a_known_lambda_and_the_shares_are_memoised(ss):
    # one destination 300 m from the garage and beta 0: P = w / (1 + w), w = exp(-300 / lambda); P = 0.25 needs lambda = 300 / ln 3
    stay = calibration_tests._stay([("fx_g01_core", 300.0)])
    sample = ss.UniverseSample("visitors", (stay,), np.array([0]), "all_options", False, 0.25)
    shares = ss.StayShares(sample, {"fx_g01_core": True}, 0.0)
    value = ss.calibrate_weighted(shares, np.ones(1), 1e-5)
    assert value == pytest.approx(300.0 / math.log(3.0), abs=0.02)
    pricings = shares.pricings
    assert ss.calibrate_weighted(shares, np.ones(1), 1e-5) == value and shares.pricings == pricings   # memoised per decay length


def test_a_target_that_a_sample_cannot_reach_is_none_never_an_end_of_the_range(ss):
    shares = _universe(ss)
    shares.sample = shares.sample._replace(target=0.999)
    assert ss.calibrate_weighted(shares, np.ones(24), 1e-4) is None


def test_the_same_seed_gives_the_same_lambda_and_the_runs_are_reproducible(ss):
    shares = _universe(ss)
    first = ss.run_split_half(shares, 24, 4, 1e-4)
    second = ss.run_split_half(_universe(ss), 24, 4, 1e-4)
    assert first == second and len(first) == 8 and {row["half"] for row in first} == {"A", "B"}
    assert all(row["decay_m"] is not None and row["decay_m"] > 0 for row in first)
    boots = ss.run_bootstrap(shares, 24, 5, 1e-4)
    assert boots == ss.run_bootstrap(_universe(ss), 24, 5, 1e-4) and [row["resample"] for row in boots] == [0, 1, 2, 3, 4]
    assert len({row["decay_m"] for row in boots}) > 1       # the resamples differ, so the lambdas differ


# --------------------------------------------------------------------------- the standard deviations


def test_the_half_sample_sd_is_sd_of_the_difference_over_sqrt_two(ss):
    # A = (10, 12), B = (9, 15): A - B = (1, -3), SD (ddof 1) = sqrt(((1 + 1)^2 + (-3 + 1)^2) / 1) = sqrt(8); / sqrt(2) = 2
    assert ss.half_sample_sd([10.0, 12.0], [9.0, 15.0]) == pytest.approx(2.0, abs=1e-12)
    assert ss.sd_of_full_from_half(2.0) == pytest.approx(math.sqrt(2.0), abs=1e-12)
    assert ss.sd_of_larger_sample(10.0, 4.0) == pytest.approx(5.0, abs=1e-12)


def test_the_half_sample_sd_recovers_a_known_sigma_from_independent_halves(ss):
    rng = np.random.default_rng(0)
    a, b = rng.normal(500.0, 5.0, 4000), rng.normal(500.0, 5.0, 4000)
    assert ss.half_sample_sd(a, b) == pytest.approx(5.0, rel=0.05)    # not 5 * sqrt(2), the SD of the difference
    assert float(np.std(a - b, ddof=1)) == pytest.approx(5.0 * math.sqrt(2.0), rel=0.05)


def test_the_summaries_follow_the_formulas_leave_unreachable_runs_out_and_refuse_too_few(ss):
    rows = [{"seed": 0, "half": "A", "decay_m": 10.0}, {"seed": 0, "half": "B", "decay_m": 9.0},
            {"seed": 1, "half": "A", "decay_m": 12.0}, {"seed": 1, "half": "B", "decay_m": 15.0},
            {"seed": 2, "half": "A", "decay_m": None}, {"seed": 2, "half": "B", "decay_m": 11.0}]
    summary = ss.summarise_split_half(rows)
    assert summary["half_sd_m"] == pytest.approx(2.0, abs=1e-12)
    assert summary["sd_25_pct_m"] == pytest.approx(math.sqrt(2.0), abs=1e-12)
    assert summary["sd_100_pct_m"] == pytest.approx(math.sqrt(2.0) / 2.0, abs=1e-12)
    assert (summary["seeds_used"], summary["seeds"], summary["unreachable_runs"]) == (2, 3, 1)
    assert (summary["mean_m"], summary["min_m"], summary["max_m"]) == (11.5, 9.0, 15.0)
    with pytest.raises(ValueError, match="fewer than two"):
        ss.summarise_split_half(rows[:2])
    boots = ss.summarise_bootstrap([{"resample": i, "decay_m": v} for i, v in enumerate([1.0, 3.0, None, 5.0])])
    assert boots["sd_25_pct_m"] == pytest.approx(2.0, abs=1e-12) and boots["sd_100_pct_m"] == pytest.approx(1.0, abs=1e-12)
    assert (boots["resamples_used"], boots["unreachable_runs"], boots["mean_m"]) == (3, 1, 3.0)
    with pytest.raises(ValueError, match="fewer than two"):
        ss.summarise_bootstrap([{"resample": 0, "decay_m": 1.0}])


def test_a_mutant_that_forgets_the_square_root_of_two_is_caught(ss, monkeypatch):
    monkeypatch.setattr(ss, "half_sample_sd", lambda a, b: float(np.std(np.asarray(a) - np.asarray(b), ddof=1)))
    rows = [{"seed": 0, "half": "A", "decay_m": 10.0}, {"seed": 0, "half": "B", "decay_m": 9.0},
            {"seed": 1, "half": "A", "decay_m": 12.0}, {"seed": 1, "half": "B", "decay_m": 15.0}]
    assert ss.summarise_split_half(rows)["half_sd_m"] != pytest.approx(2.0, abs=1e-6)


def test_an_existing_table_is_not_replaced_without_overwrite(ss, tmp_path):
    out = tmp_path / "table.csv"
    out.write_text("x", encoding="utf-8")
    with pytest.raises(SystemExit, match="--overwrite"):
        ss.run(plans=tmp_path / "plans.xml", data=tmp_path, config_path=tmp_path / "c.yml", out_path=out, overwrite=False,
               seeds=2, resamples=2, tolerance=1e-4)


# --------------------------------------------------------------------------- the committed table


def _table_rows() -> dict:
    assert TABLE.is_file(), f"{TABLE.name} must be committed next to the calibration table"
    frame = pd.read_csv(TABLE, comment="#", dtype={"quantity": str, "value": str})
    assert list(frame.columns) == ["quantity", "value", "unit", "note"] and not frame["quantity"].duplicated().any()
    return {row.quantity: (float(row.value) if isinstance(row.value, str) and row.value else None) for row in frame.itertuples()}


def _header_hash(name: str) -> str:
    found = re.findall(rf"^#\s+{re.escape(name)}: \S+ sha256=([0-9a-f]{{64}})", TABLE.read_text(encoding="utf-8"), flags=re.M)
    assert len(found) == 1, f"{TABLE.name}: expected one header line for {name!r}, found {len(found)}"
    return found[0]


def test_the_committed_table_names_the_inputs_that_their_records_and_files_pin():
    assert _header_hash("garages") == config_tests._record_pin(config_tests.GARAGE_RECORD)
    assert _header_hash("zones") == config_tests._record_pin(config_tests.ZONES_RECORD)
    assert _header_hash("plans") == config_tests._manifest_population_pin(config_tests.PLANS_RUN_MANIFEST)
    for name, path in config_tests.SRV_TABLES.items():
        assert _header_hash(name) == config_tests._lf_sha256(path), name
    assert _header_hash("tariffs") == config_tests._lf_sha256(
        REPO / "eqasim-data" / "data" / "braunschweig" / "parking" / "parking_tariffs_2026.csv")
    # the measurement belongs to THIS calibration table: another calibration needs another measurement
    assert _header_hash("calibration_table") == config_tests._lf_sha256(config_tests.TABLE)


def test_the_committed_table_reproduces_the_calibration_table_and_its_summary_rows_follow_from_its_runs(ss):
    rows = _table_rows()
    committed = ss.cgd.read_calibration_table(config_tests.TABLE)
    assert rows["production_decay_length_m"] == committed["decay_length_m"]
    assert rows["production_decay_commute_length_m"] == committed["decay_commute_length_m"]
    seeds = int(re.search(r"SPLIT HALF: (\d+) fixed seeds", TABLE.read_text(encoding="utf-8")).group(1))
    for symbol in ("lambda", "lambda_c"):
        split_rows = [{"seed": seed, "half": half, "decay_m": rows[f"run_split_seed{seed:02d}_{half}_{symbol}"]}
                      for seed in range(seeds) for half in ("A", "B")]
        summary = ss.summarise_split_half(split_rows)
        assert rows[f"split_half_sd_of_one_half_{symbol}"] == pytest.approx(summary["half_sd_m"], abs=0.006)
        assert rows[f"split_half_sd_25_pct_{symbol}"] == pytest.approx(summary["sd_25_pct_m"], abs=0.006)
        assert rows[f"split_half_sd_100_pct_{symbol}"] == pytest.approx(summary["sd_100_pct_m"], abs=0.006)
        boot_rows = [{"resample": int(re.search(r"run_bootstrap_(\d+)_", name).group(1)), "decay_m": value}
                     for name, value in rows.items() if name.startswith("run_bootstrap_") and name.endswith(f"_{symbol}")]
        boot = ss.summarise_bootstrap(boot_rows)
        assert rows[f"bootstrap_sd_25_pct_{symbol}"] == pytest.approx(boot["sd_25_pct_m"], abs=0.006)
        assert rows[f"bootstrap_sd_100_pct_{symbol}"] == pytest.approx(boot["sd_100_pct_m"], abs=0.006)
        assert rows[f"unreachable_runs_{symbol}"] == 0
        # the SD at 100 % is half of the SD at 25 % (1 / sqrt(4))
        assert rows[f"split_half_sd_100_pct_{symbol}"] == pytest.approx(rows[f"split_half_sd_25_pct_{symbol}"] / 2.0, abs=0.01)


def test_the_calibration_record_states_the_measured_sampling_sds():
    rows = _table_rows()
    record = yaml.safe_load(CALIBRATION_RECORD.read_text(encoding="utf-8"))
    text = " ".join((record["notes"] + " " + record["limitations"]).split())
    assert "SAMPLING SENSITIVITY" in text and "never a release value" in text
    for symbol in ("lambda", "lambda_c"):
        for quantity in (f"split_half_sd_25_pct_{symbol}", f"split_half_sd_100_pct_{symbol}", f"bootstrap_sd_25_pct_{symbol}"):
            assert f"{rows[quantity]:.2f}" in text, quantity


def test_the_sampling_table_pins_exactly_the_committed_calibration_table():
    # The measurement belongs to ONE calibration table: its header pin must be the LF hash of the committed file, not of an
    # earlier or an edited one (the calibration table and this table were once committed with disagreeing pins).
    assert _header_hash("calibration_table") == config_tests._lf_sha256(config_tests.TABLE)


def test_the_sampling_table_was_produced_on_a_clean_tree_at_a_full_commit():
    line = config_tests._code_state_line(TABLE)
    assert "uncommitted" not in line, f"{TABLE.name} was produced on a dirty working tree: rerun on a clean committed tree"
    assert re.search(r"; code state [0-9a-f]{40}\.$", line), f"{TABLE.name}: the code-state line names no full commit: {line!r}"


def test_the_calibration_record_pins_the_committed_sampling_table():
    record = yaml.safe_load(CALIBRATION_RECORD.read_text(encoding="utf-8"))
    text = " ".join(record["limitations"].split())
    pins = re.findall(r"SHA-256 with LF line endings ([0-9a-f]{64})", text)
    assert len(pins) == 1, f"{CALIBRATION_RECORD.name}: limitations must pin the sampling table exactly once, found {len(pins)}"
    assert pins[0] == config_tests._lf_sha256(TABLE)
