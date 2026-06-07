"""Tests for the HBEFA vehicle-type mapping.

``braunschweig.synthesis.vehicles.hbefa`` maps a ``(powertrain, euro_class,
segment)`` spec to a canonical HBEFA :class:`VehicleType`. Covered:

  * technology mapping is correct for every powertrain;
  * combustion emission concept encodes the Euro stage; electrified powertrains
    carry a fixed, Euro-independent concept (no combustion Euro stage on a BEV);
  * the size class comes from the (configurable) segment -> size map, with a
    logged fallback for a missing segment;
  * every produced VehicleType is valid (technology x emission x size in the
    allowed sets);
  * the type_id is canonical (equal specs collapse to one type; the BEV Euro
    class does not change the type).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import hbefa  # noqa: E402


def test_technology_for_every_powertrain():
    expected = {
        "petrol": "petrol (4S)",
        "diesel": "diesel",
        "gas": "bifuel CNG/petrol",
        "bev": "electricity",
        "phev": "plug-in hybrid petrol/electricity",
        "hybrid": "petrol (hybrid)",
        "hydrogen": "Fuel Cell",
        "other": "petrol (4S)",
    }
    for pt in ft.POWERTRAIN_LABELS:
        assert hbefa.technology_for_powertrain(pt) == expected[pt]


def test_combustion_emission_concept_encodes_euro():
    assert hbefa.emission_concept_for("petrol", "euro6") == "PC petrol Euro-6"
    assert hbefa.emission_concept_for("diesel", "euro4") == "PC diesel Euro-4"
    assert hbefa.emission_concept_for("gas", "euro5") == "PC CNG Euro-5"


def test_electrified_emission_concept_is_euro_independent():
    # A BEV has no combustion Euro stage; its concept is fixed regardless of euro.
    bev6 = hbefa.emission_concept_for("bev", "euro6")
    bev_other = hbefa.emission_concept_for("bev", "other")
    assert bev6 == bev_other == "PC BEV"
    assert hbefa.emission_concept_for("phev", "euro6") == "PC PHEV petrol"
    assert hbefa.emission_concept_for("hybrid", "euro6") == "PC P-Hybrid"
    assert hbefa.emission_concept_for("hydrogen", "euro6") == "PC Fuel Cell"


def test_size_class_from_default_map():
    assert hbefa.size_class_for_segment("minis") == "small"
    assert hbefa.size_class_for_segment("kompaktklasse") == "medium"
    assert hbefa.size_class_for_segment("oberklasse") == "large"
    assert hbefa.size_class_for_segment("suv") == "large"


def test_size_class_override_via_config_map():
    custom = {"minis": "large"}
    assert hbefa.size_class_for_segment("minis", size_map=custom) == "large"
    # Unspecified segments keep the default.
    assert hbefa.size_class_for_segment("suv", size_map=custom) == "large"


def test_size_class_missing_segment_falls_back_and_logs(caplog):
    counter: dict[str, int] = {}
    with caplog.at_level(logging.WARNING):
        size = hbefa.size_class_for_segment(
            "not_a_segment", fallback_counter=counter)
    assert size == hbefa.DEFAULT_SIZE_FALLBACK
    assert counter["not_a_segment"] == 1
    assert any("fallback" in r.message.lower() for r in caplog.records)


def test_every_spec_maps_to_valid_vehicle_type():
    for pt in ft.POWERTRAIN_LABELS:
        for euro in ft.EURO_CLASS_LABELS:
            for seg in ft.SEGMENT_LABELS:
                vt = hbefa.vehicle_type_for(pt, euro, seg)
                assert hbefa.is_valid_vehicle_type(vt), vt
                assert vt.hbefa_category == "PASSENGER_CAR"
                assert vt.hbefa_size in hbefa.HBEFA_SIZE_CLASSES


def test_bev_type_id_is_euro_independent():
    a = hbefa.vehicle_type_for("bev", "euro6", "minis")
    b = hbefa.vehicle_type_for("bev", "other", "minis")
    assert a.type_id == b.type_id


def test_petrol_type_id_depends_on_euro():
    a = hbefa.vehicle_type_for("petrol", "euro6", "minis")
    b = hbefa.vehicle_type_for("petrol", "euro4", "minis")
    assert a.type_id != b.type_id


def test_type_id_is_ascii_and_collision_free():
    seen: dict[str, hbefa.VehicleType] = {}
    for pt in ft.POWERTRAIN_LABELS:
        for euro in ft.EURO_CLASS_LABELS:
            for seg in ("minis", "kompaktklasse", "oberklasse"):
                vt = hbefa.vehicle_type_for(pt, euro, seg)
                assert vt.type_id.isascii()
                assert " " not in vt.type_id
                if vt.type_id in seen:
                    # Same id must mean identical HBEFA attributes.
                    assert seen[vt.type_id] == vt
                seen[vt.type_id] = vt


def test_as_record_has_writer_fields():
    vt = hbefa.vehicle_type_for("diesel", "euro5", "mittelklasse")
    rec = vt.as_record()
    for key in ("type_id", "length", "width", "hbefa_cat", "hbefa_tech",
                "hbefa_size", "hbefa_emission"):
        assert key in rec
