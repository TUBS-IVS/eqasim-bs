"""The garage decay lengths of the configs against their calibration table (spec Amendments E5, E6 and H, issue #436).

``parking_garage_decay_m`` and ``parking_garage_decay_commute_m`` are release values, not free run parameters: they equal the
``decay_length_m`` and the ``decay_commute_length_m`` of the committed calibration table
``parking_garage_decay_calibration_2026.csv`` (written by ``scripts/parking/calibrate_garage_decay.py``).
The table is committed (the calibration of the 25 % reference plans on the option set of the garage dataset named in its
header, spec Amendment I6, issue #436) and the value equals it. The table belongs to that garage dataset exactly: the
SHA-256 in its header must be the single pin of the data record ``parking_garages_2026`` (a test below), and the pins of the
zone file and of the two committed SrV tables must be those of their record and files, so a regenerated input with a stale
table fails here. The 0 branch below remains for a checkout without the table: the value must
then be 0 (the garage options are off, E6); with the table the value must equal it, and the first of these two tests
that fits the repository state decides. Writing the table without setting the config value, or setting a value without
the table, fails here.
"""
from __future__ import annotations

import hashlib
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
#: The run manifest of the 25 % plans that the table was calibrated on (it owns the SHA-256 of the population file).
PLANS_RUN_MANIFEST = REPO / "docs" / "runs" / "parking-v2-plans-25pct-2026-10-12.yml"
#: The lot-threshold sensitivities of the table (spec Amendment I6); the garage data record owns their SHA-256.
SENSITIVITY_THRESHOLDS = (30, 100)
ZONES_RECORD = REPO / "docs" / "registry" / "data" / "parking_zones_2026.yml"
#: The two committed SrV tables the table header pins (their hashes are those of the files in the repository).
SRV_TABLES = {
    "srv2023_city_center_parking": REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_city_center_parking.csv",
    "srv2023_commute_parking_by_workplace_class": (REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
                                                  / "srv2023_commute_parking_by_workplace_class.csv")}


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


def _record_pin(record_path: Path) -> str:
    """The ONE SHA-256 that a data record of a restricted file pins in ``storage.notes``; the test fails when the notes hold none
    or several (a stale table would otherwise pass a membership test against notes that also mention an older hash)."""
    record = yaml.safe_load(Path(record_path).read_text(encoding="utf-8"))
    pins = re.findall(r"\b[0-9a-f]{64}\b", " ".join(record["storage"]["notes"].split()))
    assert len(pins) == 1, f"{Path(record_path).name}: storage.notes must pin exactly one SHA-256, found {len(pins)}"
    return pins[0]


def _lf_sha256(path: Path) -> str:
    """SHA-256 of a committed text file with LF line endings (a Windows checkout may rewrite them; the header pins the LF form)."""
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_the_record_pin_reader_demands_exactly_one_pin(tmp_path):
    old, new = "a" * 64, "b" * 64

    def record(notes: str) -> Path:
        path = tmp_path / "record.yml"
        path.write_text("storage:\n  notes: >-\n    " + notes + "\n", encoding="utf-8")
        return path

    assert _record_pin(record(f"pinned {new} for this file")) == new
    with pytest.raises(AssertionError, match="exactly one SHA-256, found 2"):
        _record_pin(record(f"pinned {new}; the first release had {old}"))   # a stale hash must not be hidden by an old mention
    with pytest.raises(AssertionError, match="exactly one SHA-256, found 0"):
        _record_pin(record("no hash here"))


def test_the_committed_table_belongs_to_the_garage_dataset_pinned_in_its_data_record():
    # The decay lengths are calibrated on ONE option set: another garage dataset (a regenerated release, a lot that becomes
    # a free option) needs a new calibration. The data record owns the pin of the restricted dataset (storage.notes, exactly
    # one), so the test runs without the dataset itself; a stale table fails here instead of silently pricing another set.
    if not TABLE.is_file():
        pytest.skip("the calibration table does not exist yet")
    assert _table_input_hash("garages") == _record_pin(GARAGE_RECORD), (
        f"{TABLE.name} was calibrated on a garage dataset that is not the pin of {GARAGE_RECORD.name}: "
        "rerun scripts/parking/calibrate_garage_decay.py on the current dataset, or restore the pin")


def _manifest_population_pin(manifest_path: Path) -> str:
    """The ONE SHA-256 that the notes of a run manifest state for the population file (``population.xml.gz, <bytes> bytes,
    SHA-256 <hash>``); the notes also hold the hashes of other outputs, so the pin is read by its anchor, exactly once."""
    record = yaml.safe_load(Path(manifest_path).read_text(encoding="utf-8"))
    pins = re.findall(r"population\.xml\.gz, [\d,]+ bytes, SHA-256 ([0-9a-f]{64})", " ".join(record["notes"].split()))
    assert len(pins) == 1, f"{Path(manifest_path).name}: notes must pin the population file exactly once, found {len(pins)}"
    return pins[0]


def _table_sensitivity_hash(threshold: int) -> str:
    """The SHA-256 that the header of the committed table records for the sensitivity garage dataset of a lot threshold
    (``... parking_garages_2026_lot_threshold_<n>.geojson sha256=<hash>``)."""
    pattern = rf"parking_garages_2026_lot_threshold_{threshold}\.geojson sha256=([0-9a-f]{{64}})"
    found = re.findall(pattern, TABLE.read_text(encoding="utf-8"))
    assert len(found) == 1, f"{TABLE.name}: expected exactly one sensitivity hash for threshold {threshold}, found {len(found)}"
    return found[0]


def _record_sensitivity_pin(record_path: Path, threshold: int) -> str:
    """The ONE SHA-256 that a garage data record states for ``parking_garages_2026_lot_threshold_<n>.geojson`` anywhere in
    its text (the file name followed by its hash); a second, older statement fails instead of hiding a stale table."""
    record = yaml.safe_load(Path(record_path).read_text(encoding="utf-8"))
    text = " ".join(yaml.safe_dump(record, width=10 ** 6).split())
    found = re.findall(rf"parking_garages_2026_lot_threshold_{threshold}\.geojson ([0-9a-f]{{64}})", text)
    assert len(found) == 1, f"{Path(record_path).name}: expected exactly one pin for threshold {threshold}, found {len(found)}"
    return found[0]


def test_the_manifest_and_sensitivity_pin_readers_demand_exactly_one_pin(tmp_path):
    first, second = "a" * 64, "b" * 64
    manifest = tmp_path / "manifest.yml"
    manifest.write_text(f"notes: 'out.xml.gz, 1,234 bytes, SHA-256 {second}; x_population.xml.gz, 9,876 bytes, SHA-256 {first}'\n",
                        encoding="utf-8")
    assert _manifest_population_pin(manifest) == first   # read by its anchor, not by position or by the other hashes
    manifest.write_text(f"notes: 'population.xml.gz, 1 bytes, SHA-256 {first}; population.xml.gz, 2 bytes, SHA-256 {second}'\n",
                        encoding="utf-8")
    with pytest.raises(AssertionError, match="exactly once, found 2"):
        _manifest_population_pin(manifest)
    manifest.write_text("notes: 'no pin'\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="exactly once, found 0"):
        _manifest_population_pin(manifest)

    record = tmp_path / "record.yml"
    record.write_text(f"notes: >-\n  files: parking_garages_2026_lot_threshold_30.geojson\n  {first}, qa {second}\n", encoding="utf-8")
    assert _record_sensitivity_pin(record, 30) == first
    with pytest.raises(AssertionError, match="exactly one pin for threshold 100, found 0"):
        _record_sensitivity_pin(record, 100)
    record.write_text(f"notes: >-\n  parking_garages_2026_lot_threshold_30.geojson {first} and then "
                      f"parking_garages_2026_lot_threshold_30.geojson {second}\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="exactly one pin for threshold 30, found 2"):
        _record_sensitivity_pin(record, 30)


def test_the_committed_table_names_the_plans_pinned_in_the_run_manifest():
    # the plans are a local restricted output: the run manifest of the 25 % plans run owns the SHA-256 of the population file
    # that the table was calibrated on (exact equality, like the garage dataset pin)
    if not TABLE.is_file():
        pytest.skip("the calibration table does not exist yet")
    assert _table_input_hash("plans") == _manifest_population_pin(PLANS_RUN_MANIFEST), (
        f"{TABLE.name} was calibrated on other plans than the population file of {PLANS_RUN_MANIFEST.name}: "
        "rerun the calibration on the plans of the manifest, or record the new run in its own manifest")


@pytest.mark.parametrize("threshold", SENSITIVITY_THRESHOLDS)
def test_the_sensitivity_datasets_of_the_table_are_those_pinned_in_the_garage_data_record(threshold):
    # the two sensitivity datasets are local files that are not the release: their hashes are stated once in the garage record
    if not TABLE.is_file():
        pytest.skip("the calibration table does not exist yet")
    assert _table_sensitivity_hash(threshold) == _record_sensitivity_pin(GARAGE_RECORD, threshold), (
        f"{TABLE.name} used another lot-threshold-{threshold} garage dataset than the one pinned in {GARAGE_RECORD.name}")


def test_the_committed_table_names_the_zone_file_pinned_in_its_data_record():
    # the zone polygons are a local restricted file too: the pin of their data record is the reference
    if not TABLE.is_file():
        pytest.skip("the calibration table does not exist yet")
    assert _table_input_hash("zones") == _record_pin(ZONES_RECORD), (
        f"{TABLE.name} was calibrated on a zone file that is not the pin of {ZONES_RECORD.name}: rerun the calibration")


@pytest.mark.parametrize("name", SRV_TABLES)
def test_the_committed_table_names_the_committed_srv_tables_it_was_calibrated_against(name):
    # the two SrV tables are committed: the header must pin the LF SHA-256 of the file that the repository holds now
    if not TABLE.is_file():
        pytest.skip("the calibration table does not exist yet")
    assert _table_input_hash(name) == _lf_sha256(SRV_TABLES[name]), (
        f"{TABLE.name} was calibrated on another {name}.csv than the committed one: rerun the calibration")


@pytest.mark.parametrize("name", CONFIGS)
def test_the_maximum_garage_distance_is_the_assumption_g2_value_in_every_config(name):
    assert _config(name)["parking_garage_max_distance_m"] == cost.GARAGE_MAX_DISTANCE_M == 1000.0


def test_the_three_configs_name_the_same_garage_dataset_and_decay():
    base = _config("base")
    for name in ("popsim_mid", "popsim_open"):
        for key in ("parking_garages_path", "parking_garage_decay_m", "parking_garage_decay_commute_m",
                    "parking_garage_max_distance_m",
                    "parking_garage_monthly_imputation", "parking_option_price_beta_u_per_eur"):
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


@pytest.mark.parametrize("name", CONFIGS)
def test_the_option_price_coefficient_is_the_mode_choice_magnitude_in_every_config_and_matches_the_prepare_default(name):
    # spec v3 A2 (ASSUMPTION G1-d, ADR-0142 decision 2): the key carries the MAGNITUDE of BraunschweigModeParameters.betaCost_u_MU
    # (-0.310998 in eqasim-java-bs), one money coefficient for the choice between parking options, no income or distance
    # term. It is a release value that the Java startup check ParkingOptionPriceBetaCheck compares with the mode choice
    # (1e-9), so a config that drifts from it is a defect, not a sensitivity: the sensitivity arm is 0 (the weights of v2).
    from braunschweig.matsim.simulation import prepare

    value = _config(name)["parking_option_price_beta_u_per_eur"]
    assert value == prepare.PARKING_DEFAULTS["parking_option_price_beta_u_per_eur"] == 0.310998
    assert isinstance(value, float)


@pytest.mark.parametrize("name", CONFIGS)
def test_the_cost_factor_is_the_model_value_in_every_config_and_matches_the_prepare_default(name):
    # spec v3 A3 (ASSUMPTION CF): 1.0 is the model and reproduces v2 bit for bit; every config carries the key explicitly
    # (the value is declared, never absent), so a sensitivity arm that edits one config cannot leave the others behind.
    from braunschweig.matsim.simulation import prepare

    value = _config(name)["parking_cost_utility_factor"]
    assert value == prepare.PARKING_DEFAULTS["parking_cost_utility_factor"] == prepare.PARKING_COST_FACTOR_DEFAULT == 1.0
    assert isinstance(value, float)
