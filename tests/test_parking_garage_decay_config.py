"""The garage decay length of the configs against its calibration table (spec Amendment E5 and E6, issue #436).

``parking_garage_decay_m`` is a release value, not a free run parameter: it equals the ``decay_length_m`` of the committed
calibration table ``parking_garage_decay_calibration_2026.csv`` (written by ``scripts/parking/calibrate_garage_decay.py``).
The table is committed (the calibration of the 1 % reference plans of 2026-10-08, issue #436) and the value equals
it. The 0 branch below remains for a checkout without the table: the value must
then be 0 (the garage options are off, E6); with the table the value must equal it, and the first of these two tests
that fits the repository state decides. Writing the table without setting the config value, or setting a value without
the table, fails here.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from braunschweig.parking import cost

REPO = Path(__file__).resolve().parents[1]
TABLE = REPO / "eqasim-data" / "data" / "braunschweig" / "parking" / "parking_garage_decay_calibration_2026.csv"
CONFIGS = {"base": REPO / "configs" / "base_bs.yml",
           "popsim_mid": REPO / "configs" / "fixtures" / "config_popsim_mid_braunschweig.yml",
           "popsim_open": REPO / "configs" / "fixtures" / "config_popsim_open_braunschweig.yml"}
SCRIPT = REPO / "scripts" / "parking" / "calibrate_garage_decay.py"


def _config(name: str) -> dict:
    return yaml.safe_load(CONFIGS[name].read_text(encoding="utf-8"))["config"]


def _table_decay_m() -> float:
    spec = importlib.util.spec_from_file_location("calibrate_garage_decay_config_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.read_calibration_table(TABLE)["decay_length_m"]


@pytest.mark.parametrize("name", CONFIGS)
def test_the_decay_length_equals_the_calibration_table_or_is_zero_while_the_table_is_pending(name):
    value = _config(name)["parking_garage_decay_m"]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    if TABLE.is_file():
        # the calibration exists: the config carries exactly its result (a release value with its calibration record)
        assert value == _table_decay_m(), f"{name}: parking_garage_decay_m must equal decay_length_m of {TABLE.name}"
    else:
        # CALIBRATION PENDING: no table, so no calibrated value; 0 switches the garage options off (spec E6)
        assert value == 0, (f"{name}: {TABLE.name} does not exist, so parking_garage_decay_m must be 0 (garage options "
                            "off); a value needs its calibration table")


@pytest.mark.parametrize("name", CONFIGS)
def test_the_maximum_garage_distance_is_the_assumption_g2_value_in_every_config(name):
    assert _config(name)["parking_garage_max_distance_m"] == cost.GARAGE_MAX_DISTANCE_M == 1000.0


def test_the_three_configs_name_the_same_garage_dataset_and_decay():
    base = _config("base")
    for name in ("popsim_mid", "popsim_open"):
        for key in ("parking_garages_path", "parking_garage_decay_m", "parking_garage_max_distance_m",
                    "parking_garage_monthly_imputation"):
            assert _config(name)[key] == base[key], (name, key)
    assert base["parking_garages_path"] == "braunschweig/parking/parking_garages_2026.geojson"


def test_the_committed_release_table_is_not_a_facility_filtered_sensitivity_run():
    # G1: a filtered run (--facility-kinds) is a sensitivity number; the release table is the calibration on every kind
    if not TABLE.is_file():
        pytest.skip("the calibration table does not exist yet")
    spec = importlib.util.spec_from_file_location("calibrate_garage_decay_filter_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    values = module.read_calibration_table(TABLE)
    assert values.get("garage_facility_filter_active", 0) == 0, f"{TABLE.name} was calibrated on a facility-kind filter"


@pytest.mark.parametrize("name", CONFIGS)
def test_the_monthly_imputation_switch_is_on_in_every_config_and_matches_the_prepare_default(name):
    # spec Amendment F3 (ASSUMPTION P13): a boolean, default true; false is the sensitivity arm "published only"
    from braunschweig.matsim.simulation import prepare

    value = _config(name)["parking_garage_monthly_imputation"]
    assert value is True and prepare.PARKING_DEFAULTS["parking_garage_monthly_imputation"] is True
