"""Tests for the differentiated German fleet MATSim vehicles writer (Task F6).

Two behaviours are asserted:

1. **Differentiated VehicleType + per-vehicle attributes.** When the household
   fleet (``vehicles_method:"household"``) hands the writer several distinct
   HBEFA :class:`VehicleType` records and per-vehicle spec columns
   (``segment``/``powertrain``/``euro``/``age``/``brand``/``model``), the written
   ``vehicles.xml.gz`` contains one ``<vehicleType>`` per distinct type with its
   four HBEFA engine attributes, and each ``<vehicle>`` references a real type and
   carries its per-vehicle attributes.

2. **OFF byte-identical.** With the legacy single ``default_car`` type and the
   legacy four per-vehicle attributes (``critair``/``technology``/``age``/``euro``)
   only -- i.e. no German spec columns present -- the writer emits exactly the
   legacy XML (the extra German attributes are NOT written), so the OFF path is
   byte-identical to the upstream eqasim writer.

3. **Incommuter cars draw from the NDS fleet.** The cross-cordon in-commuter
   vehicle frame is no longer a single hardcoded ``Gazole/euro6`` row; the cars
   are drawn from the German NDS fleet, so several distinct types/powertrains
   appear across many in-commuters.
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

import matsim.scenario.vehicles as base  # noqa: E402

DATA_PATH = str(DATA)
MATSIM_NS = "{http://www.matsim.org/files/dtd}"


# --------------------------------------------------------------------------- #
# Minimal synpp context: only path() + progress() are used by write_vehicles.
# --------------------------------------------------------------------------- #
class _Progress:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def update(self):
        pass


class _WriterContext:
    def __init__(self, path):
        self._path = str(path)

    def path(self):
        return self._path

    def progress(self, total=None, label=None):
        return _Progress()


def _write_and_parse(tmp_path, df_types, df_vehicles):
    out = Path(tmp_path) / "vehicles.xml.gz"
    base.write_vehicles(str(out), df_types, df_vehicles, _WriterContext(tmp_path))
    with gzip.open(out, "rb") as handle:
        text = handle.read().decode("utf-8")
    root = ET.fromstring(text)
    return text, root


def _engine_attributes(type_element):
    engine = type_element.find(f"{MATSIM_NS}engineInformation")
    if engine is None:
        return {}
    attrs = engine.find(f"{MATSIM_NS}attributes")
    return {a.get("name"): a.text for a in attrs.findall(f"{MATSIM_NS}attribute")}


def _vehicle_attributes(vehicle_element):
    attrs = vehicle_element.find(f"{MATSIM_NS}attributes")
    if attrs is None:
        return {}
    return {a.get("name"): a.text for a in attrs.findall(f"{MATSIM_NS}attribute")}


# --------------------------------------------------------------------------- #
# Fixtures: a small differentiated German fleet and the legacy single fleet.
# --------------------------------------------------------------------------- #
def _german_fleet():
    df_types = pd.DataFrame.from_records([
        {"type_id": "car_petrol_4s_medium_pc_petrol_euro_6", "length": 7.5,
         "width": 1.0, "mode": "car", "hbefa_cat": "PASSENGER_CAR",
         "hbefa_tech": "petrol (4S)", "hbefa_size": "medium",
         "hbefa_emission": "PC petrol Euro-6"},
        {"type_id": "car_electricity_small_pc_bev", "length": 7.5, "width": 1.0,
         "mode": "car", "hbefa_cat": "PASSENGER_CAR", "hbefa_tech": "electricity",
         "hbefa_size": "small", "hbefa_emission": "PC BEV"},
        {"type_id": "car_diesel_large_pc_diesel_euro_5", "length": 7.5,
         "width": 1.0, "mode": "car", "hbefa_cat": "PASSENGER_CAR",
         "hbefa_tech": "diesel", "hbefa_size": "large",
         "hbefa_emission": "PC diesel Euro-5"},
    ])
    df_vehicles = pd.DataFrame.from_records([
        {"vehicle_id": "1:car:0", "type_id": "car_petrol_4s_medium_pc_petrol_euro_6",
         "critair": "", "technology": "petrol", "age": 5, "euro": "euro6",
         "segment": "kompaktklasse", "powertrain": "petrol", "euro_class": "euro6",
         "brand": "VW", "model": "VW GOLF", "mode": "car"},
        {"vehicle_id": "2:car:0", "type_id": "car_electricity_small_pc_bev",
         "critair": "", "technology": "bev", "age": 2, "euro": "other",
         "segment": "kleinwagen", "powertrain": "bev", "euro_class": "other",
         "brand": "TESLA", "model": "TESLA MODEL 3", "mode": "car"},
        {"vehicle_id": "3:car:0", "type_id": "car_diesel_large_pc_diesel_euro_5",
         "critair": "", "technology": "diesel", "age": 12, "euro": "euro5",
         "segment": "suv", "powertrain": "diesel", "euro_class": "euro5",
         "brand": "BMW", "model": "BMW X5", "mode": "car"},
    ])
    return df_types, df_vehicles


def _legacy_fleet():
    df_types = pd.DataFrame.from_records([{
        "type_id": "default_car", "nb_seats": 4, "length": 5.0, "width": 1.0,
        "pce": 1.0, "mode": "car", "hbefa_cat": "PASSENGER_CAR",
        "hbefa_tech": "average", "hbefa_size": "average", "hbefa_emission": "average",
    }])
    df_vehicles = pd.DataFrame.from_records([{
        "owner_id": 1, "mode": "car", "vehicle_id": "1:car", "type_id": "default_car",
        "critair": "Crit'air 1", "technology": "Gazole", "age": 0, "euro": 6,
    }])
    return df_types, df_vehicles


# --------------------------------------------------------------------------- #
# 1. Differentiated VehicleType records + per-vehicle attributes.
# --------------------------------------------------------------------------- #
def test_writer_emits_distinct_vehicle_types(tmp_path):
    df_types, df_vehicles = _german_fleet()
    _, root = _write_and_parse(tmp_path, df_types, df_vehicles)
    types = root.findall(f"{MATSIM_NS}vehicleType")
    type_ids = {t.get("id") for t in types}
    assert type_ids == set(df_types["type_id"])
    assert len(types) == 3


def test_writer_each_type_has_valid_hbefa_engine_attributes(tmp_path):
    df_types, df_vehicles = _german_fleet()
    _, root = _write_and_parse(tmp_path, df_types, df_vehicles)
    by_id = {t.get("id"): t for t in root.findall(f"{MATSIM_NS}vehicleType")}
    eng = _engine_attributes(by_id["car_electricity_small_pc_bev"])
    assert eng["HbefaVehicleCategory"] == "PASSENGER_CAR"
    assert eng["HbefaTechnology"] == "electricity"
    assert eng["HbefaSizeClass"] == "small"
    assert eng["HbefaEmissionsConcept"] == "PC BEV"
    # Every type carries all four HBEFA engine attributes.
    for t in root.findall(f"{MATSIM_NS}vehicleType"):
        attrs = _engine_attributes(t)
        assert {"HbefaVehicleCategory", "HbefaTechnology", "HbefaSizeClass",
                "HbefaEmissionsConcept"} <= set(attrs)


def test_writer_vehicle_references_real_type(tmp_path):
    df_types, df_vehicles = _german_fleet()
    _, root = _write_and_parse(tmp_path, df_types, df_vehicles)
    type_ids = {t.get("id") for t in root.findall(f"{MATSIM_NS}vehicleType")}
    for v in root.findall(f"{MATSIM_NS}vehicle"):
        assert v.get("type") in type_ids


def test_writer_emits_per_vehicle_german_attributes(tmp_path):
    df_types, df_vehicles = _german_fleet()
    _, root = _write_and_parse(tmp_path, df_types, df_vehicles)
    by_id = {v.get("id"): v for v in root.findall(f"{MATSIM_NS}vehicle")}
    attrs = _vehicle_attributes(by_id["2:car:0"])
    # German spec attributes are present per vehicle (additive to legacy four).
    assert attrs["segment"] == "kleinwagen"
    assert attrs["powertrain"] == "bev"
    assert attrs["brand"] == "TESLA"
    assert attrs["model"] == "TESLA MODEL 3"
    # The legacy four are still present.
    assert "technology" in attrs and "age" in attrs and "euro" in attrs


# --------------------------------------------------------------------------- #
# 2. OFF byte-identical: legacy single default_car type + four attributes.
# --------------------------------------------------------------------------- #
def _legacy_reference_xml(tmp_path):
    """Reproduce the upstream eqasim writer output for the legacy fleet.

    Mirrors ``matsim.scenario.vehicles.write_vehicles`` BEFORE the F6 change:
    one ``default_car`` type and the four legacy per-vehicle attributes only.
    """
    import io
    import matsim.writers as writers

    df_types, df_vehicles = _legacy_fleet()
    out = io.BytesIO()
    writer = writers.VehiclesWriter(out)
    writer.start_vehicles()
    for t in df_types.to_dict(orient="records"):
        writer.add_type(
            t["type_id"], length=t["length"], width=t["width"],
            engine_attributes={
                "HbefaVehicleCategory": t["hbefa_cat"],
                "HbefaTechnology": t["hbefa_tech"],
                "HbefaSizeClass": t["hbefa_size"],
                "HbefaEmissionsConcept": t["hbefa_emission"],
            })
    for v in df_vehicles.to_dict(orient="records"):
        writer.add_vehicle(v["vehicle_id"], v["type_id"], attributes={
            "critair": v["critair"], "technology": v["technology"],
            "age": v["age"], "euro": v["euro"],
        })
    writer.end_vehicles()
    return out.getvalue().decode("utf-8")


def test_off_legacy_fleet_is_byte_identical(tmp_path):
    df_types, df_vehicles = _legacy_fleet()
    text, _ = _write_and_parse(tmp_path, df_types, df_vehicles)
    assert text == _legacy_reference_xml(tmp_path)


def test_off_legacy_has_single_default_type(tmp_path):
    df_types, df_vehicles = _legacy_fleet()
    _, root = _write_and_parse(tmp_path, df_types, df_vehicles)
    types = root.findall(f"{MATSIM_NS}vehicleType")
    assert len(types) == 1
    assert types[0].get("id") == "default_car"


# --------------------------------------------------------------------------- #
# 3. Incommuter cars drawn from the NDS fleet (not all Gazole/euro6).
# --------------------------------------------------------------------------- #
def test_incommuter_vehicles_drawn_from_nds_fleet():
    from braunschweig.synthesis import incommuters as ic

    n = 400
    person_ids = np.arange(1000, 1000 + n)
    modes = ["car"] * n
    orig_ars = np.array(["03241"] * n)            # Hannover region (out of ZGB)
    income_eur = np.full(n, 3000.0)
    rng = np.random.default_rng(7)

    df_types, df_vehicles = ic.build_incommuter_fleet(
        person_ids, modes, orig_ars, income_eur, DATA_PATH, rng)

    # Every car-mode in-commuter gets exactly one vehicle.
    assert len(df_vehicles) == n
    # The cars are NOT all identical: several distinct types/powertrains appear.
    assert df_vehicles["type_id"].nunique() > 1
    assert df_vehicles["powertrain"].nunique() > 1
    # The vehicle types are valid HBEFA records and referenced by the vehicles.
    assert set(df_vehicles["type_id"]).issubset(set(df_types["type_id"]))
    for col in ("vehicle_id", "type_id", "critair", "technology", "age", "euro"):
        assert col in df_vehicles.columns


def test_incommuter_non_car_modes_get_no_vehicle():
    from braunschweig.synthesis import incommuters as ic

    person_ids = np.array([1, 2, 3, 4])
    modes = ["car", "pt", "car", "pt"]
    orig_ars = np.array(["03241"] * 4)
    income_eur = np.full(4, 3000.0)
    rng = np.random.default_rng(1)

    _, df_vehicles = ic.build_incommuter_fleet(
        person_ids, modes, orig_ars, income_eur, DATA_PATH, rng)
    assert len(df_vehicles) == 2          # only the two car commuters
    assert set(df_vehicles["owner_id"]) == {1, 3}
