"""Map the vehicle types of a MATSim run archive to HBEFA keys (post-run emissions, #688).

The archive's ``output_vehicles`` / ``output_transitVehicles`` files carry vehicle types that the
emissions contrib cannot use as they are: three passenger-car technology names differ from the
contrib's ``HbefaTechnology`` ids, ``heavy_truck`` and the transit types have no engine
information, and ``default_car_passenger`` would be priced as a car. This module decides, per
type, what the emissions view must contain. It reads nothing from the archive and writes
nothing; the view builder applies the result.

Two committed tables hold the decisions (names only, no HBEFA data):

* ``vehicle_type_rules.csv`` -- exact type ids with an action (``keep`` / ``set`` / ``exclude``)
  and the owner assumption behind it;
* ``technology_names.csv`` -- archive technology name -> contrib id. Status ``identity`` and
  ``renamed`` follow the ids of the pinned ``emissions-2026.0-2026w12`` jar;
  ``ASSUMPTION_unverified`` marks a translation not yet checked against real HBEFA tables (the
  later pre-check against the tables must confirm it).

Fleet types (ids starting with ``car_``) keep their size class and emissions concept; only the
technology name is translated. Anything else not in the rules table raises: a silent default
would hide a missing vehicle class.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Mapping

#: HBEFA vehicle category the contrib skips (no emissions computed).
NON_HBEFA_VEHICLE = "NON_HBEFA_VEHICLE"

#: Prefix of the fleet types written by the vehicle stage (``car_<technology>_<size>_<concept>``).
FLEET_TYPE_PREFIX = "car_"

_RULES_FILE = Path(__file__).with_name("vehicle_type_rules.csv")
_TECHNOLOGY_FILE = Path(__file__).with_name("technology_names.csv")

_ATTRIBUTE_NAMES = (
    "HbefaVehicleCategory",
    "HbefaTechnology",
    "HbefaSizeClass",
    "HbefaEmissionsConcept",
)


@dataclass(frozen=True)
class HbefaKeys:
    """The four HBEFA engine attributes of a vehicle type."""

    category: str
    technology: str
    size_class: str
    emissions_concept: str


@dataclass(frozen=True)
class TypeResolution:
    """What the emissions view does with one vehicle type.

    ``excluded`` types are written with category ``NON_HBEFA_VEHICLE`` and emit nothing.
    ``note`` names the assumption or translation applied (empty for a plain pass-through).
    """

    type_id: str
    keys: HbefaKeys
    excluded: bool
    note: str


@lru_cache(maxsize=1)
def _load_rules() -> dict[str, dict[str, str]]:
    with _RULES_FILE.open(newline="", encoding="utf-8") as handle:
        return {row["type_id"]: row for row in csv.DictReader(handle)}


@lru_cache(maxsize=1)
def _load_technologies() -> dict[str, dict[str, str]]:
    with _TECHNOLOGY_FILE.open(newline="", encoding="utf-8") as handle:
        return {row["archive_technology"]: row for row in csv.DictReader(handle)}


def resolve_vehicle_type(type_id: str, attributes: Mapping[str, str]) -> TypeResolution:
    """Resolve one vehicle type; ``attributes`` are its ``engineInformation`` attributes.

    Raises ``ValueError`` for a type id that is neither in the rules table nor a fleet type, for
    a type with missing engine attributes where they are needed, and for a technology name the
    table does not know.
    """
    rule = _load_rules().get(type_id)
    if rule is not None:
        action = rule["action"]
        if action == "exclude":
            keys = HbefaKeys(NON_HBEFA_VEHICLE, "average", "average", "average")
            return TypeResolution(type_id, keys, True, rule["assumption"])
        if action == "set":
            keys = HbefaKeys(
                rule["hbefa_category"],
                rule["hbefa_technology"],
                rule["hbefa_size_class"],
                rule["hbefa_emissions_concept"],
            )
            return TypeResolution(type_id, keys, False, rule["assumption"])
        if action == "keep":
            return TypeResolution(type_id, _keys_from(type_id, attributes), False, rule["assumption"])
        raise ValueError(f"{_RULES_FILE.name}: unknown action {action!r} for type {type_id!r}")

    if not type_id.startswith(FLEET_TYPE_PREFIX):
        raise ValueError(
            f"vehicle type {type_id!r} is neither a fleet type ('{FLEET_TYPE_PREFIX}*') nor in "
            f"{_RULES_FILE.name}; add an explicit rule (with its assumption) before running emissions"
        )
    keys = _keys_from(type_id, attributes)
    translation = _load_technologies().get(keys.technology)
    if translation is None:
        raise ValueError(
            f"vehicle type {type_id!r}: unknown HbefaTechnology {keys.technology!r}; add it to "
            f"{_TECHNOLOGY_FILE.name} once its contrib id is verified"
        )
    note = "" if translation["status"] == "identity" else f"technology {translation['status']}"
    translated = HbefaKeys(
        keys.category, translation["contrib_technology"], keys.size_class, keys.emissions_concept
    )
    return TypeResolution(type_id, translated, False, note)


def _keys_from(type_id: str, attributes: Mapping[str, str]) -> HbefaKeys:
    missing = [name for name in _ATTRIBUTE_NAMES if name not in attributes]
    if missing:
        raise ValueError(f"vehicle type {type_id!r} lacks engine attributes {missing}")
    return HbefaKeys(*(attributes[name] for name in _ATTRIBUTE_NAMES))
