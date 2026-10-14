"""The garage decay lengths of the configs against their calibration table (spec Amendments E5, E6 and H, issue #436).

``parking_garage_decay_m`` and ``parking_garage_decay_commute_m`` are release values, not free run parameters: they equal the
``decay_length_m`` and the ``decay_commute_length_m`` of the committed calibration table
``parking_garage_decay_calibration_2026.csv`` (written by ``scripts/parking/calibrate_garage_decay.py``).
The table is committed (the calibration of the 25 % reference plans on the option set of the garage dataset named in its
header, spec Amendment I6, issue #436) and the value equals it. The table belongs to that garage dataset exactly: the
SHA-256 in its header must be the pin of the data record ``parking_garages_2026`` (a test below), so a regenerated
dataset with a stale table fails here. The 0 branch below remains for a checkout without the table: the value must
then be 0 (the garage options are off, E6); with the table the value must equal it, and the first of these two tests
that fits the repository state decides. Writing the table without setting the config value, or setting a value without
the table, fails here.
"""
from __future__ import annotations

import importlib.util
import re
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
GARAGE_RECORD = REPO / "docs" / "registry" / "data" / "parking_garages_2026.yml"


def _config(name: str) -> dict:
    return yaml.safe_load(CONFIGS[name].read_text(encoding="utf-8"))["config"]


#: config key -> row of the calibration table (spec Amendment H: one table, both release values)
DECAY_KEYS = {"parking_garage_decay_m": "decay_length_m", "parking_garage_decay_commute_m": "decay_commute_length_m"}


def _table_value(row: str) -> float:
    spec = importlib.util.spec_from_file_location("calibrate_garage_decay_config_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.read_calibration_table(TABLE)[row]


@pytest.mark.parametrize("key, row", DECAY_KEYS.items(), ids=list(DECAY_KEYS))
@pytest.mark.parametrize("name", CONFIGS)
def test_the_decay_lengths_equal_the_calibration_table_or_are_zero_while_the_table_is_pending(name, key, row):
    value = _config(name)[key]
    assert isinstance(value, (int, float)) and not isinstance(value, bool)
    if TABLE.is_file():
        # the calibration exists: the config carries exactly its result (a release value with its calibration record)
        assert value == _table_value(row), f"{name}: {key} must equal {row} of {TABLE.name}"
    else:
        # CALIBRATION PENDING: no table, so no calibrated value; 0 switches the garage options off (spec E6)
        assert value == 0, (f"{name}: {TABLE.name} does not exist, so {key} must be 0 (garage options "
                            "off); a value needs its calibration table")



def _table_input_hash(name: str) -> str:
    """The SHA-256 that the header of the committed table records for an input (the line ``#   <name>: <path> sha256=<hash>``)."""
    found = re.findall(rf"^#\s+{re.escape(name)}: \S+ sha256=([0-9a-f]{{64}})", TABLE.read_text(encoding="utf-8"), flags=re.M)
    assert len(found) == 1, f"{TABLE.name}: expected exactly one header line for the input {name!r}, found {len(found)}"
    return found[0]


def test_the_committed_table_belongs_to_the_garage_dataset_pinned_in_its_data_record():
    # The decay lengths are calibrated on ONE option set: another garage dataset (a regenerated release, a lot that becomes
    # a free option) needs a new calibration. The data record owns the pin of the restricted dataset (storage.notes, once),
    # so the test runs without the dataset itself; a stale table fails here instead of silently pricing another option set.
    if not TABLE.is_file():
        pytest.skip("the calibration table does not exist yet")
    record = yaml.safe_load(GARAGE_RECORD.read_text(encoding="utf-8"))
    pinned = " ".join(record["storage"]["notes"].split())
    assert _table_input_hash("garages") in pinned, (
        f"{TABLE.name} was calibrated on a garage dataset that is not the pin of {GARAGE_RECORD.name}: "
        "rerun scripts/parking/calibrate_garage_decay.py on the current dataset, or restore the pin")


@pytest.mark.parametrize("name", CONFIGS)
def test_the_maximum_garage_distance_is_the_assumption_g2_value_in_every_config(name):
    assert _config(name)["parking_garage_max_distance_m"] == cost.GARAGE_MAX_DISTANCE_M == 1000.0


def test_the_three_configs_name_the_same_garage_dataset_and_decay():
    base = _config("base")
    for name in ("popsim_mid", "popsim_open"):
        for key in ("parking_garages_path", "parking_garage_decay_m", "parking_garage_decay_commute_m",
                    "parking_garage_max_distance_m",
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
