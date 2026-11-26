"""Aggregate the outputs of ``AirPollutionAnalysis`` (post-run emissions, #688).

Inputs are model output of the emissions tool, already multiplied by ``1 / sample size``:
``emissions_per_vehicle_type.csv`` (columns ``vehicleType,pollutant,value``, grams, whole
simulated time) and ``emissions_total.csv`` (columns ``Pollutant,kg``, emissions before 24:00
only). Nothing is scaled here. Totals are taken from the per-vehicle-type file because it has no
cut at 24:00; the difference to ``emissions_total.csv`` is reported as the after-24:00 share.

Vehicle-km come from the events (``braunschweig.emissions.vehicle_km``, sample values) and are
scaled by ``1 / sample size`` only for the derived indicator grams per vehicle-km.

Every vehicle type belongs to one group, from the pre-check of
``braunschweig.emissions.table_keys``:

* ``primary``: a fleet type answered by the detailed table;
* ``fallback``: a fleet type answered below it (technology average or average table);
* ``average_table_by_choice``: a type written as average on purpose (``default_car``,
  ``heavy_truck``), or every type under ``directlyTryAverageTable``;
* ``excluded``: a type that emits nothing by assumption (public transport, car passengers);
* ``unknown``: a type in the tool's output that the pre-check did not see.
"""
from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Iterable, Mapping

logger = logging.getLogger("braunschweig.emissions.totals")

GROUPS = ("primary", "fallback", "average_table_by_choice", "excluded", "unknown")
_EMITTING_GROUPS = ("primary", "fallback", "average_table_by_choice")
_GRAMS_PER_KILOGRAM = 1000.0
_METERS_PER_KILOMETER = 1000.0


def _rows(path: Path, columns: tuple[str, ...]) -> Iterable[dict]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        absent = [name for name in columns if name not in (reader.fieldnames or [])]
        if absent:
            raise ValueError(f"{path} lacks the columns {absent}; found {reader.fieldnames}")
        yield from reader


def read_per_vehicle_type(path: str | Path) -> dict[tuple[str, str], float]:
    """``{(vehicle type, pollutant): grams}`` from ``emissions_per_vehicle_type.csv``."""
    grams: dict[tuple[str, str], float] = {}
    for row in _rows(Path(path), ("vehicleType", "pollutant", "value")):
        key = (row["vehicleType"], row["pollutant"])
        if key in grams:
            raise ValueError(f"{path}: vehicle type and pollutant {key} occur twice")
        grams[key] = float(row["value"])
    if not grams:
        raise ValueError(f"{path} holds no emission rows")
    return grams


def read_total(path: str | Path) -> dict[str, float]:
    """``{pollutant: kg before 24:00}`` from ``emissions_total.csv``."""
    kilograms = {row["Pollutant"]: float(row["kg"]) for row in _rows(Path(path), ("Pollutant", "kg"))}
    if not kilograms:
        raise ValueError(f"{path} holds no emission rows")
    return kilograms


def group_of(type_entry: Mapping | None) -> str:
    """The group of one pre-check type entry (``None``: the pre-check did not see the type)."""
    if type_entry is None:
        return "unknown"
    if type_entry["excluded"]:
        return "excluded"
    if not type_entry["expects_detailed"]:
        return "average_table_by_choice"
    return "fallback" if type_entry["fallback"] else "primary"


def summarize(
    grams_by_type_and_pollutant: Mapping[tuple[str, str], float],
    kilograms_before_day_end: Mapping[str, float],
    vehicle_km: Mapping,
    type_entries: Iterable[Mapping],
    sample_size: float,
) -> dict:
    """Totals per pollutant and per group, the after-24:00 share and the fallback share by vehicle-km."""
    if not 0.0 < sample_size <= 1.0:
        raise ValueError(f"sample size {sample_size!r} must lie in (0, 1]")
    entries = {entry["type_id"]: entry for entry in type_entries}
    groups = {
        group: {"vehicle_types_driving": 0, "vehicle_km_sample": 0.0, "kilograms": {}} for group in GROUPS
    }
    meters_after_day_end = 0.0
    driving_types = set()
    for type_id, driven in vehicle_km["by_type"].items():
        group = groups[group_of(entries.get(type_id))]
        group["vehicle_km_sample"] += driven["meters"] / _METERS_PER_KILOMETER
        meters_after_day_end += driven["meters_after_day_end"]
        if driven["meters"] > 0:
            group["vehicle_types_driving"] += 1
            driving_types.add(type_id)

    kilograms_total: dict[str, float] = {}
    emitting_types = set()
    for (type_id, pollutant), grams in sorted(grams_by_type_and_pollutant.items()):
        kilograms = grams / _GRAMS_PER_KILOGRAM
        kilograms_total[pollutant] = kilograms_total.get(pollutant, 0.0) + kilograms
        by_pollutant = groups[group_of(entries.get(type_id))]["kilograms"]
        by_pollutant[pollutant] = by_pollutant.get(pollutant, 0.0) + kilograms
        if grams > 0:
            emitting_types.add(type_id)

    emitting_km_sample = sum(groups[group]["vehicle_km_sample"] for group in _EMITTING_GROUPS)
    total_km_sample = sum(group["vehicle_km_sample"] for group in groups.values())
    emitting_km_scaled = emitting_km_sample / sample_size
    pollutants = {}
    for pollutant, kilograms in sorted(kilograms_total.items()):
        before = kilograms_before_day_end.get(pollutant)
        pollutants[pollutant] = {
            "total_kg": kilograms,
            "before_24h_kg": before,
            "after_24h_share": (1.0 - before / kilograms) if before is not None and kilograms > 0 else None,
            "gram_per_vehicle_km": (
                kilograms * _GRAMS_PER_KILOGRAM / emitting_km_scaled if emitting_km_scaled > 0 else None
            ),
        }
    for group in groups.values():
        group["vehicle_km_scaled"] = group["vehicle_km_sample"] / sample_size
        group["share_of_emitting_vehicle_km"] = (
            group["vehicle_km_sample"] / emitting_km_sample if emitting_km_sample > 0 else None
        )
    fleet_km = groups["primary"]["vehicle_km_sample"] + groups["fallback"]["vehicle_km_sample"]
    summary = {
        "sample_size": sample_size,
        "pollutants": pollutants,
        "groups": groups,
        "emitting_vehicle_km_sample": emitting_km_sample,
        "emitting_vehicle_km_scaled": emitting_km_scaled,
        "fleet_vehicle_km_sample": fleet_km,
        "fallback_vehicle_km_share": groups["fallback"]["vehicle_km_sample"] / fleet_km if fleet_km > 0 else 0.0,
        "average_table_by_choice_vehicle_km_share": groups["average_table_by_choice"]["share_of_emitting_vehicle_km"],
        "vehicle_km_after_24h_share": (
            meters_after_day_end / _METERS_PER_KILOMETER / total_km_sample if total_km_sample > 0 else None
        ),
        "unknown_vehicle_link_entries": vehicle_km["unknown_vehicle_link_entries"],
        "unknown_vehicle_km_sample": vehicle_km["unknown_vehicle_meters"] / _METERS_PER_KILOMETER,
        # Emitting types that drive but have no positive emission: the tool and the events disagree.
        "types_driving_without_emissions": sorted(
            type_id for type_id in driving_types - emitting_types
            if group_of(entries.get(type_id)) in _EMITTING_GROUPS
        ),
        "event_lines": vehicle_km["event_lines"],
    }
    primary_km = groups["primary"]["vehicle_km_sample"]
    logger.info(
        "[emissions.summary] primary %.1f/%.1f fleet vehicle-km (%.1f%%), fallback %.1f (%.1f%%); "
        "average table by choice %.1f%% of the emitting vehicle-km; %.1f%% of all vehicle-km after 24:00",
        primary_km, fleet_km, 100.0 * primary_km / fleet_km if fleet_km > 0 else 0.0,
        groups["fallback"]["vehicle_km_sample"], 100.0 * summary["fallback_vehicle_km_share"],
        100.0 * (summary["average_table_by_choice_vehicle_km_share"] or 0.0),
        100.0 * (summary["vehicle_km_after_24h_share"] or 0.0),
    )
    return summary
