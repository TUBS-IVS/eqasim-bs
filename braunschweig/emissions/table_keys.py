"""Which lookups the HBEFA tables can answer, checked before the Java run (post-run emissions, #688).

``AirPollutionAnalysis`` has no lookup counter and stops with a ``RuntimeException`` at the first
key it cannot find, which on a full archive is hours into the events. This module reads only the
KEY columns of the four tables and replays the lookup order of the emissions contrib, so the
stages can say per vehicle type which table level answers it, and can stop before the Java run
when a lookup would fail.

Ported from the contrib (matsim-libs ``main``: ``HbefaTables``, ``EmissionUtils``,
``WarmEmissionAnalysisModule.getEf``, ``ColdEmissionAnalysisModule.getEmissionsFactor``; the enum
values and string constants are identical in the pinned ``2026w12`` run jar, checked with
``javap`` on 2026-11-26):

* a table row is keyed by vehicle category, component, vehicle attributes (technology, size
  class, emission concept; ``average`` three times in the average tables) and, warm, road type and
  traffic situation (both from ``TrafficSit``) or, cold, parking time and distance (both from
  ``AmbientCondPattern``). ``Year``, ``Gradient``, ``Case`` and every other column are NOT part of
  the key: of two rows with one key the later one silently wins in the tool. Such a table is
  refused here (:func:`check_table_keys`);
* lookup order per component: the detailed table with the type's own attributes; the detailed
  table with the HBEFA 3 rewrite (size class ``not specified``, concept ``PC-P-Euro-1`` ->
  ``PC P Euro-1`` and alike, any other concept ``average``); the detailed table with
  ``<technology>; average; average``; the average table. ``detailedVsAverageLookupBehavior``
  decides how far the order runs and which tables are loaded at all.

Limits, stated so the report is not over-read:

* every emitting vehicle type is checked against every road type on links an emitting mode may
  use; whether a type ever drives there is not known before the run, so a reported failure can be
  one the run would not have met;
* for ``AverageSpeed`` only the free-flow factor is checked: which other traffic situation a link
  gets depends on the simulated speed;
* text is decoded as UTF-8 with replacement, as the tool reads it. A table in another encoding
  keeps its ASCII keys; non-ASCII key values (``RT ≤7,5t``) then match nothing, and the count of
  such lines is reported.

No emission factor is read, stored or reported here.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Mapping

from braunschweig.emissions.hbefa_tables import TABLE_KINDS
from braunschweig.emissions.vehicle_types import NON_HBEFA_VEHICLE, TypeResolution

logger = logging.getLogger("braunschweig.emissions.table_keys")

#: Values of ``detailedVsAverageLookupBehavior`` (enum of the pinned jar).
LOOKUP_BEHAVIORS = (
    "onlyTryDetailedElseAbort",
    "tryDetailedThenTechnologyAverageElseAbort",
    "tryDetailedThenTechnologyAverageThenAverageTable",
    "directlyTryAverageTable",
)
#: Values of ``emissionsComputationMethod`` (enum of the pinned jar).
COMPUTATION_METHODS = ("StopAndGoFraction", "AverageSpeed")

#: Lookup levels, best first. ``abort``: no table answers, the tool would stop.
LEVELS = ("detailed", "detailed_hbefa3_rewrite", "technology_average", "average_table", "abort")
#: Levels that count as the primary method for a type with its own technology, size and concept.
PRIMARY_LEVELS = frozenset({"detailed", "detailed_hbefa3_rewrite"})

_AVERAGE = "average"
_AVERAGE_ATTRIBUTES = (_AVERAGE, _AVERAGE, _AVERAGE)

# EmissionUtils.mapString2HbefaVehicleCategory: substring match in this order, else an enum name.
_CATEGORY_MARKERS = (
    ("pass. car", "PASSENGER_CAR"),
    ("HGV", "HEAVY_GOODS_VEHICLE"),
    ("LCV", "LIGHT_COMMERCIAL_VEHICLE"),
    ("motorcycle", "MOTORCYCLE"),
    ("coach", "COACH"),
    ("urban bus", "URBAN_BUS"),
)
_CATEGORY_NAMES = frozenset({name for _, name in _CATEGORY_MARKERS} | {NON_HBEFA_VEHICLE})

# HbefaTables.mapString2HbefaTrafficSituation: suffix match in this order.
_SITUATION_SUFFIXES = (
    ("Freeflow", "FREEFLOW"),
    ("Heavy", "HEAVY"),
    ("Satur.", "SATURATED"),
    ("St+Go", "STOPANDGO"),
    ("St+Go2", "STOPANDGO_HEAVY"),
)

# EmissionUtils.getPollutant: alternative spellings, else the name of a Pollutant enum value.
_POLLUTANT_SPELLINGS = {
    "CO2(total)": "CO2_TOTAL",
    "CO2(rep)": "CO2_rep",
    "PM2.5 (non-exhaust)": "PM2_5_non_exhaust",
    "PM2.5": "PM2_5",
    "PM (non-exhaust)": "PM_non_exhaust",
    "BC (exhaust)": "BC_exhaust",
    "BC (non-exhaust)": "BC_non_exhaust",
}
_POLLUTANTS = frozenset({
    "CO", "CO2_TOTAL", "FC", "HC", "NMHC", "NOx", "NO2", "PM", "SO2", "FC_MJ", "CO2_rep", "CO2e",
    "PM2_5", "PM2_5_non_exhaust", "PM_non_exhaust", "BC_exhaust", "BC_non_exhaust", "Benzene", "PN",
    "Pb", "CH4", "N2O", "NH3",
})

# EmissionUtils.tryRewriteHbefa3toHbefa4: the only concepts the rewrite keeps.
_HBEFA3_CONCEPTS = {
    f"PC-{fuel}-Euro-{level}": f"PC {fuel} Euro-{level}" for fuel in "PD" for level in range(1, 7)
}
_NOT_SPECIFIED = "not specified"

#: Cold lookups the tool makes per vehicle: parking time 1..12 h or 13 (longer), distance 1 or 2 km.
COLD_PATTERNS = tuple((hours, km) for hours in range(1, 14) for km in (1, 2))

#: Columns the tool ignores; several values in one of them are the usual cause of duplicate keys.
_IGNORED_DISCRIMINATORS = ("Case", "Year", "TrafficScenario", "Gradient")

_UTF8_BOM = b"\xef\xbb\xbf"
_REPLACEMENT = "�"
_DELIMITER = ";"


def read_table_keys(paths: Mapping[str, str | Path]) -> dict[str, dict]:
    """Index the keys of the four tables (``paths``: table kind -> file).

    Per kind: ``lookups`` maps ``(category, technology, size class, concept, a, b)`` to a bit mask
    of the components that key has (``a, b`` = road type and traffic situation for the warm
    tables, parking time in hours and distance in km for the cold ones; bit ``i`` stands for
    ``components[i]``), ``components`` (contrib names, sorted), ``data_rows``, ``duplicate_rows``
    (rows repeating a full key), ``undecodable_lines`` and ``ignored_values`` (the distinct values
    of the columns the tool ignores). Raises ``ValueError`` for a row the tool could not read (an
    unknown vehicle category, component, traffic situation or ambient pattern, or a short row).
    """
    return {kind: _read_table(kind, Path(paths[kind])) for kind in TABLE_KINDS}


def _read_table(kind: str, path: Path) -> dict:
    warm = kind.startswith("warm")
    detailed = kind.endswith("detailed")
    component_bits: dict[str, int] = {}
    lookups: dict[tuple, int] = {}
    ignored_values: dict[str, set[str]] = {}
    data_rows = duplicate_rows = undecodable_lines = 0
    with path.open("rb") as handle:
        header_bytes = handle.readline()
        if header_bytes.startswith(_UTF8_BOM):
            header_bytes = header_bytes[len(_UTF8_BOM):]
        header = [name.strip() for name in header_bytes.decode("utf-8", errors="replace").split(_DELIMITER)]
        column = {name: index for index, name in enumerate(header)}
        needed = ["VehCat", "Component", "TrafficSit" if warm else "AmbientCondPattern"]
        if detailed:
            needed += ["Technology", "SizeClasse", "EmConcept"]
        absent = [name for name in needed if name not in column]
        if absent:
            raise ValueError(f"HBEFA table '{kind}' ({path}) lacks the key columns {absent}")
        widest = max(column[name] for name in needed)
        discriminators = [name for name in _IGNORED_DISCRIMINATORS if name in column]
        category_column, component_column = column["VehCat"], column["Component"]
        rest_column = column["TrafficSit" if warm else "AmbientCondPattern"]
        parse_rest = _road_type_and_situation if warm else _parking_and_distance
        categories: dict[str, str] = {}
        components_seen: dict[str, str] = {}
        rests: dict[str, tuple] = {}
        for line_number, raw in enumerate(handle, start=2):
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line.strip():
                continue
            undecodable_lines += _REPLACEMENT in line
            fields = line.split(_DELIMITER)
            if len(fields) <= widest:
                raise ValueError(f"HBEFA table '{kind}' ({path}), line {line_number}: only {len(fields)} fields")
            try:
                # Parsed once per distinct value: a real table repeats a few hundred values over
                # millions of rows.
                category = _cached(categories, fields[category_column], _category)
                component = _cached(components_seen, fields[component_column], _component)
                rest = _cached(rests, fields[rest_column], parse_rest)
            except ValueError as error:
                raise ValueError(f"HBEFA table '{kind}' ({path}), line {line_number}: {error}") from None
            if detailed:
                attributes = (
                    fields[column["Technology"]], fields[column["SizeClasse"]], fields[column["EmConcept"]],
                )
            else:
                attributes = _AVERAGE_ATTRIBUTES
            bit = component_bits.setdefault(component, 1 << len(component_bits))
            key = (category, *attributes, *rest)
            mask = lookups.get(key, 0)
            duplicate_rows += bool(mask & bit)
            lookups[key] = mask | bit
            for name in discriminators:
                values = ignored_values.setdefault(name, set())
                if len(values) < 20:
                    values.add(fields[column[name]] if column[name] < len(fields) else "")
            data_rows += 1
    # Re-number the bits in sorted component order so equal tables give equal indexes.
    components = sorted(component_bits)
    renumber = {component_bits[name]: 1 << index for index, name in enumerate(components)}
    lookups = {key: _remap(mask, renumber) for key, mask in lookups.items()}
    return {
        "lookups": lookups,
        "components": components,
        "data_rows": data_rows,
        "duplicate_rows": duplicate_rows,
        "undecodable_lines": undecodable_lines,
        "ignored_values": {name: sorted(values) for name, values in ignored_values.items()},
    }


def _remap(mask: int, renumber: Mapping[int, int]) -> int:
    return sum(new for old, new in renumber.items() if mask & old)


def _cached(cache: dict, value: str, parse):
    parsed = cache.get(value)
    if parsed is None:
        parsed = cache[value] = parse(value)
    return parsed


def _category(value: str) -> str:
    for marker, name in _CATEGORY_MARKERS:
        if marker in value:
            return name
    if value in _CATEGORY_NAMES:
        return value
    raise ValueError(f"vehicle category {value!r} is unknown to the emissions contrib")


def _component(value: str) -> str:
    name = _POLLUTANT_SPELLINGS.get(value, value)
    if name not in _POLLUTANTS:
        raise ValueError(
            f"component {value!r} is unknown to the emissions contrib of the pinned jar; "
            "remove the component from the export"
        )
    return name


def _road_type_and_situation(value: str) -> tuple[str, str]:
    situation = None
    for suffix, name in _SITUATION_SUFFIXES:
        if value.endswith(suffix):
            situation = name
            break
    if situation is None or "/" not in value:
        raise ValueError(f"traffic situation {value!r} is unknown to the emissions contrib")
    return value[: value.rindex("/")], situation


def _parking_and_distance(value: str) -> tuple[int, int]:
    try:
        parts = value.split(",")
        parking = 13 if parts[1] == ">12h" else int(parts[1].split("-")[1].split("h")[0])
        distance = int(parts[2].split("-")[1].split("k")[0])
    except (IndexError, ValueError):
        raise ValueError(f"ambient pattern {value!r} is unreadable for the emissions contrib") from None
    return parking, distance


def summarize_table_keys(index: Mapping[str, Mapping]) -> dict[str, dict]:
    """Counts of the index per table kind, for the stage report (JSON-safe, no factor)."""
    summary = {}
    for kind in TABLE_KINDS:
        table = index[kind]
        categories: dict[str, dict] = {}
        for category, technology, size_class, concept, first, _second in table["lookups"]:
            entry = categories.setdefault(category, {"vehicle_keys": set(), "road_types": set()})
            entry["vehicle_keys"].add((technology, size_class, concept))
            if kind.startswith("warm"):
                entry["road_types"].add(first)
        full_mask = (1 << len(table["components"])) - 1
        summary[kind] = {
            "data_rows": table["data_rows"],
            "lookup_keys": len(table["lookups"]),
            "lookup_keys_without_all_components": sum(mask != full_mask for mask in table["lookups"].values()),
            "duplicate_rows": table["duplicate_rows"],
            "undecodable_lines": table["undecodable_lines"],
            "components": list(table["components"]),
            "ignored_values": dict(table["ignored_values"]),
            "categories": {
                category: {
                    "vehicle_keys": len(entry["vehicle_keys"]),
                    "road_types": sorted(entry["road_types"]),
                }
                for category, entry in sorted(categories.items())
            },
        }
    return summary


def check_table_keys(index: Mapping[str, Mapping]) -> None:
    """Raise when a table repeats a key (the tool would silently keep the later row); warn on bad bytes."""
    for kind in TABLE_KINDS:
        table = index[kind]
        if table["undecodable_lines"]:
            logger.warning(
                "[emissions.table_keys] %s: %d of %d rows are not valid UTF-8; the tool reads them with "
                "replacement characters, so non-ASCII key values there match no vehicle type",
                kind, table["undecodable_lines"], table["data_rows"],
            )
        if table["duplicate_rows"]:
            several = {name: values for name, values in table["ignored_values"].items() if len(values) > 1}
            raise ValueError(
                f"HBEFA table '{kind}': {table['duplicate_rows']} of {table['data_rows']} rows repeat the key "
                "of an earlier row (vehicle category, component, vehicle attributes, traffic situation or "
                "ambient pattern). The emissions tool ignores every other column and silently keeps the "
                f"later row. Columns it ignores that hold several values here: {several or 'none found'}. "
                "Export one case, year and gradient per table."
            )


def lookup_level(index: Mapping[str, Mapping], family: str, keys, rest: tuple, lookup_behavior: str) -> str:
    """Worst level (over the components) at which the tool finds ``keys`` + ``rest``.

    ``family`` is ``"warm"`` or ``"cold"``; ``rest`` is ``(road type, traffic situation)`` or
    ``(parking hours, distance km)``. The components are those of the tables the lookup behaviour
    loads; a component missing at one level is looked up at the next one, as the tool does.
    """
    if lookup_behavior not in LOOKUP_BEHAVIORS:
        raise ValueError(f"lookup behaviour {lookup_behavior!r} is not one of {LOOKUP_BEHAVIORS}")
    detailed = index[f"{family}_detailed"]
    average = index[f"{family}_average"]
    uses_detailed = lookup_behavior != "directlyTryAverageTable"
    uses_average = lookup_behavior in ("tryDetailedThenTechnologyAverageThenAverageTable", "directlyTryAverageTable")
    steps: list[tuple[str, Mapping, tuple]] = []
    if uses_detailed:
        steps.append(("detailed", detailed, (keys.technology, keys.size_class, keys.emissions_concept)))
        rewritten = _HBEFA3_CONCEPTS.get(keys.emissions_concept, _AVERAGE)
        steps.append(("detailed_hbefa3_rewrite", detailed, (keys.technology, _NOT_SPECIFIED, rewritten)))
        if lookup_behavior != "onlyTryDetailedElseAbort":
            steps.append(("technology_average", detailed, (keys.technology, _AVERAGE, _AVERAGE)))
    if uses_average:
        steps.append(("average_table", average, _AVERAGE_ATTRIBUTES))
    wanted = set(detailed["components"] if uses_detailed else ()) | set(average["components"] if uses_average else ())
    for level, table, attributes in steps:
        mask = table["lookups"].get((keys.category, *attributes, *rest), 0)
        wanted -= {name for bit, name in enumerate(table["components"]) if mask >> bit & 1}
        if not wanted:
            return level
    return "abort"


def precheck_lookups(
    resolutions: Iterable[TypeResolution],
    length_meters_by_road_type: Mapping[str, float],
    index: Mapping[str, Mapping],
    lookup_behavior: str,
    computation_method: str,
) -> dict:
    """Replay the tool's lookups for every vehicle type; return the report (nothing is raised).

    ``length_meters_by_road_type``: the road types on links an emitting mode may use, with their
    link length. Per emitting type the report gives the share of that length by warm lookup
    level, the road types on which the lookup fails, and the worst cold level. A type that
    carries its own technology *expects* the detailed table (primary); it counts as fallback when
    any of its lookups is answered below it. A type written as ``average`` and every type under
    ``directlyTryAverageTable`` expects the average table by choice and is not a fallback.
    """
    if computation_method not in COMPUTATION_METHODS:
        raise ValueError(f"computation method {computation_method!r} is not one of {COMPUTATION_METHODS}")
    if not length_meters_by_road_type:
        raise ValueError("no road type given: the pre-check needs the road types of the drivable links")
    situations = ("FREEFLOW", "STOPANDGO") if computation_method == "StopAndGoFraction" else ("FREEFLOW",)
    total_length = float(sum(length_meters_by_road_type.values()))
    rank = {level: position for position, level in enumerate(LEVELS)}
    types = []
    for resolution in resolutions:
        keys = resolution.keys
        entry = {
            "type_id": resolution.type_id,
            "category": keys.category,
            "technology": keys.technology,
            "size_class": keys.size_class,
            "emissions_concept": keys.emissions_concept,
            "excluded": resolution.excluded,
            "note": resolution.note,
        }
        if resolution.excluded:
            types.append(entry)
            continue
        expects_detailed = keys.technology != _AVERAGE and lookup_behavior != "directlyTryAverageTable"
        length_by_level = dict.fromkeys(LEVELS, 0.0)
        abort_road_types = []
        for road_type, length in sorted(length_meters_by_road_type.items()):
            level = max(
                (lookup_level(index, "warm", keys, (road_type, situation), lookup_behavior) for situation in situations),
                key=rank.__getitem__,
            )
            length_by_level[level] += length
            if level == "abort":
                abort_road_types.append(road_type)
        cold_levels = [lookup_level(index, "cold", keys, pattern, lookup_behavior) for pattern in COLD_PATTERNS]
        cold_level = max(cold_levels, key=rank.__getitem__)
        warm_levels = {level for level, length in length_by_level.items() if length > 0}
        entry.update({
            "expects_detailed": expects_detailed,
            "warm_length_share_by_level": {
                level: (length / total_length if total_length else 0.0) for level, length in length_by_level.items()
            },
            "warm_abort_road_types": abort_road_types,
            "cold_level": cold_level,
            "cold_patterns_aborting": sum(level == "abort" for level in cold_levels),
            "aborts": "abort" in warm_levels or cold_level == "abort",
            "fallback": expects_detailed and not (warm_levels | {cold_level}) <= PRIMARY_LEVELS,
        })
        types.append(entry)
    emitting = [entry for entry in types if not entry["excluded"]]
    expecting = [entry for entry in emitting if entry["expects_detailed"]]
    fallback = [entry["type_id"] for entry in expecting if entry["fallback"]]
    report = {
        "lookup_behavior": lookup_behavior,
        "computation_method": computation_method,
        "traffic_situations_checked": list(situations),
        "road_types_checked": len(length_meters_by_road_type),
        "vehicle_types": len(types),
        "excluded_types": len(types) - len(emitting),
        "emitting_types": len(emitting),
        "types_expecting_detailed": len(expecting),
        "primary_types": len(expecting) - len(fallback),
        "fallback_types": fallback,
        "fallback_type_share": len(fallback) / len(expecting) if expecting else 0.0,
        "aborting_types": [entry["type_id"] for entry in emitting if entry["aborts"]],
        "types": types,
    }
    logger.info(
        "[emissions.table_keys] primary %d/%d types with their own technology (%.1f%%), fallback %d (%.1f%%); "
        "%d types on the average table by choice, %d excluded, %d would abort (%s, %s)",
        report["primary_types"], len(expecting), 100.0 * (1.0 - report["fallback_type_share"]) if expecting else 0.0,
        len(fallback), 100.0 * report["fallback_type_share"], len(emitting) - len(expecting),
        report["excluded_types"], len(report["aborting_types"]), lookup_behavior, computation_method,
    )
    return report


def check_precheck(report: Mapping, max_fallback_type_share: float) -> None:
    """Raise when the tool would abort, or when more types than allowed miss the detailed table."""
    if not 0.0 <= max_fallback_type_share <= 1.0:
        raise ValueError("max_fallback_type_share must lie in [0, 1]")
    by_id = {entry["type_id"]: entry for entry in report["types"]}
    if report["aborting_types"]:
        examples = []
        for type_id in report["aborting_types"][:5]:
            entry = by_id[type_id]
            examples.append(
                f"{type_id} ({entry['category']}; {entry['technology']}; {entry['size_class']}; "
                f"{entry['emissions_concept']}): warm road types without a factor "
                f"{entry['warm_abort_road_types'][:5]}, cold level {entry['cold_level']} "
                f"({entry['cold_patterns_aborting']} of {len(COLD_PATTERNS)} cold lookups fail)"
            )
        raise ValueError(
            f"{len(report['aborting_types'])} of {report['emitting_types']} emitting vehicle types have a lookup "
            f"no HBEFA table answers under '{report['lookup_behavior']}'; the emissions tool would stop there. "
            + " | ".join(examples)
        )
    if report["fallback_type_share"] > max_fallback_type_share:
        raise ValueError(
            f"{len(report['fallback_types'])} of {report['types_expecting_detailed']} vehicle types with their own "
            f"technology ({100.0 * report['fallback_type_share']:.1f}%, allowed "
            f"{100.0 * max_fallback_type_share:.1f}%) are not answered by the detailed table: "
            f"{report['fallback_types'][:10]}. Their fleet attributes do not match the table's keys "
            "(technology, size class, emission concept); fix the mapping or raise the bound deliberately."
        )
