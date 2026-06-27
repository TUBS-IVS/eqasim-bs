"""End-to-end integration test for the German fleet stage (Task F7).

Exercises the flag-gated household vehicles stage on a tiny synthetic scenario:

  * the synpp stage ``braunschweig.synthesis.vehicles.cars.household`` ``configure``
    declares its dependencies and ``execute`` runs to completion via a stub context;
  * the produced ``(df_vehicle_types, df_vehicles)`` are valid -- distinct HBEFA
    types, every vehicle references a real type, the per-vehicle KBA spec columns
    are present, and the vehicle count per household equals ``number_of_cars``;
  * the frames write to a real MATSim ``vehicles.xml.gz`` with valid HBEFA engine
    attributes per type (the full stage -> sample_fleet -> writer path);
  * ``fleet_model_enabled:false`` reproduces the legacy one-car-per-person fleet
    (OFF-equivalence), as does ``vehicles_method:"default"`` (the upstream stage).
"""

from __future__ import annotations

import gzip
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

import matsim.scenario.vehicles as writer  # noqa: E402
from braunschweig.synthesis.vehicles.cars import household as hh  # noqa: E402

DATA_PATH = str(DATA)
MATSIM_NS = "{http://www.matsim.org/files/dtd}"


# --------------------------------------------------------------------------- #
# synpp context stub (config + stage), plus the writer's path()/progress().
# --------------------------------------------------------------------------- #
class _Progress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self):
        pass


class _StubContext:
    def __init__(self, config, stages, path=None):
        self._config = config
        self._stages = stages
        self._path = str(path) if path is not None else None
        self.requested_configs = []
        self.requested_stages = []

    def config(self, key, default=...):
        self.requested_configs.append(key)
        if key in self._config:
            return self._config[key]
        if default is not ...:
            return default
        raise KeyError(f"missing config key: {key}")

    def stage(self, name, **kwargs):
        self.requested_stages.append(name)
        if name not in self._stages:
            raise KeyError(f"missing stage: {name}")
        return self._stages[name]

    def path(self):
        return self._path

    def progress(self, total=None, label=None):
        return _Progress()


# --------------------------------------------------------------------------- #
# Tiny synthetic scenario: a handful of households in two ZGB Kreise.
# --------------------------------------------------------------------------- #
def _persons():
    return pd.DataFrame.from_records([
        {"household_id": 1, "person_id": 101, "age": 45, "has_license": True,
         "number_of_cars": 2, "economic_status": "high"},
        {"household_id": 1, "person_id": 102, "age": 43, "has_license": True,
         "number_of_cars": 2, "economic_status": "high"},
        {"household_id": 2, "person_id": 201, "age": 30, "has_license": True,
         "number_of_cars": 0, "economic_status": "low"},
        {"household_id": 3, "person_id": 301, "age": 50, "has_license": True,
         "number_of_cars": 1, "economic_status": "very_high"},
        {"household_id": 4, "person_id": 401, "age": 38, "has_license": True,
         "number_of_cars": 1, "economic_status": "medium"},
    ])


def _homes():
    return pd.DataFrame.from_records([
        {"household_id": 1, "commune_id": "031010000000"},
        {"household_id": 2, "commune_id": "031020000000"},
        {"household_id": 3, "commune_id": "031530000000"},
        {"household_id": 4, "commune_id": "031010000000"},
    ])


def _regiostar():
    return pd.DataFrame.from_records([
        {"commune_id": "03101000", "name": "Braunschweig, Stadt", "regiostar7": 72},
        {"commune_id": "03102000", "name": "Salzgitter", "regiostar7": 73},
        {"commune_id": "03153000", "name": "Goslar", "regiostar7": 76},
    ])


def _stub(config_overrides=None, path=None):
    # Mimics the synpp context AFTER configure(): every config key the stage
    # registers (with its default) is resolvable by key alone in execute(), per
    # tests/test_execute_context_config_contract.py.
    config = {
        "data_path": DATA_PATH,
        "random_seed": 42,
        "hbefa_segment_size_map": None,
        "fleet_model_enabled": True,
        "fleet_model_brands": True,
        "fleet_hsn_tsn_attributes": True,
        # fleet_consistency_v2 (PR #12) and fleet_age_income_coupling (PR #13) are read
        # without a default in execute() (configure() registers their defaults); the stub
        # context does not carry configure-time defaults, so the test must provide them.
        "fleet_consistency_v2": True,
        "fleet_age_income_coupling": True,
        "fleet_electric_calibration": "kreis_mix_gemeinde_bev_tilt",
        "kba_fleet_paths": None,
    }
    config.update(config_overrides or {})
    stages = {
        "synthesis.population.enriched": _persons(),
        "synthesis.population.spatial.home.zones": _homes(),
        "braunschweig.data.bbsr.regiostar": _regiostar(),
    }
    return _StubContext(config, stages, path=path)


# --------------------------------------------------------------------------- #
# 1. The stage configure + execute resolve end-to-end (ON / default).
# --------------------------------------------------------------------------- #
def test_stage_configure_declares_dependencies():
    ctx = _stub()
    hh.configure(ctx)
    assert "synthesis.population.enriched" in ctx.requested_stages
    assert "synthesis.population.spatial.home.zones" in ctx.requested_stages
    assert "braunschweig.data.bbsr.regiostar" in ctx.requested_stages
    assert "data_path" in ctx.requested_configs
    assert "random_seed" in ctx.requested_configs


def test_stage_execute_produces_valid_fleet():
    ctx = _stub()
    df_vehicle_types, df_vehicles = hh.execute(ctx)

    # 4 typed household cars (2 + 0 + 1 + 1) PLUS 1 routing default_car for the carless
    # non-owner member (hh2 person 201) added by _add_default_cars_for_non_owners = 5.
    assert len(df_vehicles) == 5
    # Distinct HBEFA types, every vehicle references a real one.
    assert df_vehicle_types["type_id"].is_unique
    assert set(df_vehicles["type_id"]).issubset(set(df_vehicle_types["type_id"]))
    # Per-vehicle KBA spec columns are present.
    for col in ("segment", "powertrain", "euro_class", "brand", "model",
                "technology", "age", "euro", "critair"):
        assert col in df_vehicles.columns
    # HBEFA type records carry the four engine attributes.
    for col in ("hbefa_cat", "hbefa_tech", "hbefa_size", "hbefa_emission"):
        assert col in df_vehicle_types.columns


# --------------------------------------------------------------------------- #
# 2. The produced frames write a valid MATSim vehicles file (stage -> writer).
# --------------------------------------------------------------------------- #
def test_stage_output_writes_valid_matsim_vehicles(tmp_path):
    ctx = _stub()
    df_vehicle_types, df_vehicles = hh.execute(ctx)

    # Add the dummy passenger type the real vehicles stage concatenates so the
    # written type table is complete (mirrors synthesis.vehicles.vehicles).
    write_ctx = _StubContext({}, {}, path=tmp_path)
    writer.write_vehicles(str(Path(tmp_path) / "vehicles.xml.gz"),
                          df_vehicle_types, df_vehicles, write_ctx)

    out = Path(tmp_path) / "vehicles.xml.gz"
    with gzip.open(out, "rb") as handle:
        root = ET.fromstring(handle.read().decode("utf-8"))

    types = root.findall(f"{MATSIM_NS}vehicleType")
    type_ids = {t.get("id") for t in types}
    assert len(types) == len(type_ids) == df_vehicle_types["type_id"].nunique()
    # Every type has valid HBEFA engine attributes.
    for t in types:
        engine = t.find(f"{MATSIM_NS}engineInformation")
        attrs = {a.get("name"): a.text
                 for a in engine.find(f"{MATSIM_NS}attributes").findall(f"{MATSIM_NS}attribute")}
        assert attrs["HbefaVehicleCategory"] == "PASSENGER_CAR"
        assert attrs["HbefaTechnology"]
        # "average" is the eqasim-base default_car type (routing placeholder for carless
        # non-owners); the typed German fleet uses small/medium/large.
        assert attrs["HbefaSizeClass"] in ("small", "medium", "large", "average")
        assert attrs["HbefaEmissionsConcept"]
    # Every written vehicle references a defined type.
    vehicles = root.findall(f"{MATSIM_NS}vehicle")
    assert len(vehicles) == len(df_vehicles)
    for v in vehicles:
        assert v.get("type") in type_ids


# --------------------------------------------------------------------------- #
# 3. OFF-equivalence: fleet_model_enabled:false -> legacy one-car-per-person.
# --------------------------------------------------------------------------- #
def test_fleet_model_disabled_reproduces_legacy(tmp_path):
    ctx = _stub(config_overrides={"fleet_model_enabled": False})
    df_vehicle_types, df_vehicles = hh.execute(ctx)

    # Legacy: one car per person, single default_car type, four legacy attributes.
    assert len(df_vehicles) == len(_persons())
    assert list(df_vehicle_types["type_id"]) == ["default_car"]
    assert (df_vehicles["type_id"] == "default_car").all()
    assert set(df_vehicles["vehicle_id"]) == {
        f"{pid}:car" for pid in _persons()["person_id"]}
    # No German spec columns are emitted on the OFF path.
    for col in ("segment", "powertrain", "brand", "model"):
        assert col not in df_vehicles.columns


def test_fleet_model_disabled_writes_single_type(tmp_path):
    ctx = _stub(config_overrides={"fleet_model_enabled": False})
    df_vehicle_types, df_vehicles = hh.execute(ctx)
    write_ctx = _StubContext({}, {}, path=tmp_path)
    writer.write_vehicles(str(Path(tmp_path) / "vehicles.xml.gz"),
                          df_vehicle_types, df_vehicles, write_ctx)
    with gzip.open(Path(tmp_path) / "vehicles.xml.gz", "rb") as handle:
        root = ET.fromstring(handle.read().decode("utf-8"))
    types = root.findall(f"{MATSIM_NS}vehicleType")
    assert [t.get("id") for t in types] == ["default_car"]


# --------------------------------------------------------------------------- #
# 4. fleet_model_brands:false drops the brand/model attributes (additive).
# --------------------------------------------------------------------------- #
def test_fleet_model_brands_disabled_drops_brand_model():
    ctx = _stub(config_overrides={"fleet_model_brands": False})
    _, df_vehicles = hh.execute(ctx)
    # Brand/model columns are still present (schema-stable) but empty.
    assert (df_vehicles["brand"] == "").all()
    assert (df_vehicles["model"] == "").all()
    # The emissions-relevant chain is unaffected.
    assert df_vehicles["powertrain"].notna().all()
    assert df_vehicles["type_id"].notna().all()


# --------------------------------------------------------------------------- #
# 5. HSN/TSN engine attributes: ON adds the six engine columns, OFF omits them.
# --------------------------------------------------------------------------- #
HSN_TSN_COLUMNS = [
    "engine_power_kw", "engine_power_ps", "displacement_ccm",
    "fuel_detail", "hsn", "tsn",
]


def test_hsn_tsn_attributes_on_adds_engine_columns():
    ctx = _stub()  # fleet_hsn_tsn_attributes defaults True
    _, df_vehicles = hh.execute(ctx)
    for col in HSN_TSN_COLUMNS:
        assert col in df_vehicles.columns
    # Power is always populated (global-median fallback at worst).
    assert (df_vehicles["engine_power_kw"] > 0).all()


def test_hsn_tsn_attributes_off_omits_engine_columns():
    ctx = _stub(config_overrides={"fleet_hsn_tsn_attributes": False})
    _, df_vehicles = hh.execute(ctx)
    for col in HSN_TSN_COLUMNS:
        assert col not in df_vehicles.columns
    # The rest of the German spec is unaffected.
    assert "powertrain" in df_vehicles.columns
    assert "brand" in df_vehicles.columns


def test_hsn_tsn_attributes_off_writes_no_engine_attributes(tmp_path):
    """OFF -> the vehicles XML carries none of the engine attributes on any
    vehicle (the legacy + non-engine German attributes only)."""
    ctx = _stub(config_overrides={"fleet_hsn_tsn_attributes": False})
    df_vehicle_types, df_vehicles = hh.execute(ctx)
    write_ctx = _StubContext({}, {}, path=tmp_path)
    writer.write_vehicles(str(Path(tmp_path) / "vehicles.xml.gz"),
                          df_vehicle_types, df_vehicles, write_ctx)
    with gzip.open(Path(tmp_path) / "vehicles.xml.gz", "rb") as handle:
        root = ET.fromstring(handle.read().decode("utf-8"))
    for v in root.findall(f"{MATSIM_NS}vehicle"):
        attrs_node = v.find(f"{MATSIM_NS}attributes")
        names = ({a.get("name") for a in attrs_node.findall(f"{MATSIM_NS}attribute")}
                 if attrs_node is not None else set())
        assert not (set(HSN_TSN_COLUMNS) & names), (
            f"engine attributes leaked into the OFF XML: {set(HSN_TSN_COLUMNS) & names}")


def test_hsn_tsn_attributes_on_writes_engine_attributes(tmp_path):
    """ON -> at least one vehicle carries the engine attributes in the XML."""
    ctx = _stub()
    df_vehicle_types, df_vehicles = hh.execute(ctx)
    write_ctx = _StubContext({}, {}, path=tmp_path)
    writer.write_vehicles(str(Path(tmp_path) / "vehicles.xml.gz"),
                          df_vehicle_types, df_vehicles, write_ctx)
    with gzip.open(Path(tmp_path) / "vehicles.xml.gz", "rb") as handle:
        root = ET.fromstring(handle.read().decode("utf-8"))
    seen = set()
    for v in root.findall(f"{MATSIM_NS}vehicle"):
        attrs_node = v.find(f"{MATSIM_NS}attributes")
        if attrs_node is None:
            continue
        seen |= {a.get("name") for a in attrs_node.findall(f"{MATSIM_NS}attribute")}
    assert "engine_power_kw" in seen
    assert "displacement_ccm" in seen
    assert "hsn" in seen and "tsn" in seen
