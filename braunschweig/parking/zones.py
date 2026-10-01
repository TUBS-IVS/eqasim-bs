"""Committed parking cost zones: polygons, tariff rows and coverage register (issue #249).

The zone-based parking cost model (design spec ``2026-09-28-parking-cost-zones-design.md``, sections 3.1,
3.2 and 5.3) reads three hand-curated, committed inputs:

* **zone polygons** -- WGS84 GeoJSON on disk, EPSG:25832 in memory; one (multi)polygon per ``zone_id``
  with its digitisation provenance (``ZONE_PROVENANCE_COLUMNS``). Zones must not overlap by more than
  ``OVERLAP_TOLERANCE_M2``; a paid island inside a resident zone is its own polygon cut out of it.
* **tariff table** -- one row per zone with the columns ``TARIFF_COLUMNS``. Lines starting with ``#`` are
  the documentation header (cost semantics and assumptions) and are skipped; a ``#`` elsewhere in a line
  (e.g. a URL fragment) is data. Money in EUR as float, minutes as nullable integers (``Int64``), fee
  hours as decimal hours of the weekday, ``resident_exempt`` from the literals ``true``/``false``.
  Empty means "not applicable". Schema 2 (parking cost zones v2, issue #436, levers 2 and 4) appends the optional
  columns ``OPTIONAL_TARIFF_COLUMNS``: the garage product, ``commuter_day_eur`` and ``search_time_min``. A
  table without them (schema 1, ``SCHEMA_1_TARIFF_COLUMNS``) loads with every one of them empty, which prices as v1.
* **coverage register** -- one row per municipality of the eight ZGB counties with a status in
  ``REGISTER_STATUSES``, plus one ``excluded`` row per paid-parking area deliberately left out.

Polygons built by the rule-based construction of parking cost zones v2 (lever 1, issue #436;
``braunschweig.parking.zone_geometry``) carry ``geometry_source`` ``osm_fee_erosion`` and, for that source only,
the walking tolerance ``unavoidable_walk_m`` and the OSM snapshot ``osm_timestamp`` of the Overpass response
(``EROSION_PROVENANCE_COLUMNS``). Their curation QA is the committed table ``parking_zones_2026_qa.csv``
(``ZONE_QA_COLUMNS``, one row per curated municipality), cross-checked against the polygons by
``validate_zone_qa``; it is not a release input of the synpp stage. Polygons of the majority rule over the parking
supply (spec Amendment B; ``braunschweig.parking.supply_share``) carry ``geometry_source`` ``osm_supply_majority`` and,
for that source only, the walk distance, the share threshold, the minimum supply and the OSM snapshot of the extract
(``SUPPLY_MAJORITY_PROVENANCE_COLUMNS``); their QA table is checked by ``supply_share_qa.validate_supply_share_qa``.
Polygons built from a municipality's published paid street sections (spec Amendment C2, the curation step
``scripts/curation/parking_zones_2026/municipal_zones.py``) carry ``geometry_source``
``municipal_street_sections_buffered`` and, for that source only, the buffer around the sections ``section_buffer_m``
(``MUNICIPAL_SECTIONS_PROVENANCE_COLUMNS``). Any polygon may carry ``reconstructed_section_m2``
(``RECONSTRUCTED_SECTION_COLUMN``), the part of its area whose boundary the digitised source reconstructs from an older
map (spec Amendment C1); the municipal QA table is checked by ``municipal_zone_qa.validate_municipal_qa``.

Every validator raises ``ValueError`` listing every violation with the zone id or AGS and the field, so
a broken release fails at load time and never degrades into free parking. The only repair is
``shapely.make_valid`` for invalid rings when the polygons are loaded; it is counted and logged.
CRS: EPSG:25832 throughout, areas in square metres.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import math
import re
from pathlib import Path
from typing import Iterable, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import is_valid_reason, make_valid
from shapely.ops import unary_union

log = logging.getLogger(__name__)

CRS = "EPSG:25832"
#: Digitisation tolerance: two zones may share an edge, but their intersection must not exceed this area.
OVERLAP_TOLERANCE_M2 = 1.0
#: Float noise allowed when checking that a EUR amount is a whole number of cents (the Java side uses cents).
MONEY_CENT_TOLERANCE = 1e-6

ZONE_TYPES = ("street_paid", "resident_zone", "campus")
BRAUNSCHWEIG_COUNTY_KEY = "03101"
#: SrV 2023 Oberbezirke of Braunschweig: 1 Zentrum; 2 and 3 Innenbereich West/Ost; 4 to 6 the rest (spec 3.4).
BRAUNSCHWEIG_WORKPLACE_CLASSES = ("bs_zentrum", "bs_innenbereich", "bs_outer")
ZGB_COUNTY_KEYS = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")
#: One class per Braunschweig Oberbezirk group plus one per other ZGB county key.
WORKPLACE_CLASSES = BRAUNSCHWEIG_WORKPLACE_CLASSES + tuple(key for key in ZGB_COUNTY_KEYS
                                                            if key != BRAUNSCHWEIG_COUNTY_KEY)

#: The v1 layout (spec 5.3); every tariff table has these columns.
SCHEMA_1_TARIFF_COLUMNS = (
    "zone_id", "name", "municipality_ags", "zone_type", "workplace_class", "hourly_rate_eur", "billing_unit_min",
    "free_if_stay_at_most_min", "first_period_min", "first_period_eur", "daily_cap_eur", "max_stay_min",
    "long_stay_product_eur", "member_day_eur", "guest_day_eur", "fee_start_h", "fee_end_h", "resident_exempt",
    "source_url", "source_date", "valid_from", "fee_window_source", "notes",
)
#: Schema 2, the garage product (v2 lever 2, spec amendment A6): rate per hour, billing unit, an optional first
#: period, an optional day cap and the garage's own fee window in decimal hours (usually 0 to 24). The core
#: ``GARAGE_CORE_COLUMNS`` is all-or-none per row; ``garage_daily_cap_eur`` (empty = no day cap) and the pair
#: ``GARAGE_FIRST_PERIOD_COLUMNS`` are optional, but only together with the core.
GARAGE_COLUMNS = ("garage_hourly_rate_eur", "garage_billing_unit_min", "garage_first_period_min",
                  "garage_first_period_eur", "garage_daily_cap_eur", "garage_fee_start_h", "garage_fee_end_h")
GARAGE_CORE_COLUMNS = ("garage_hourly_rate_eur", "garage_billing_unit_min", "garage_fee_start_h", "garage_fee_end_h")
GARAGE_FIRST_PERIOD_COLUMNS = ("garage_first_period_min", "garage_first_period_eur")
#: Schema 2, all optional: the garage product, the commuter product per working day (ASSUMPTION P2; street_paid and
#: campus rows only) and the parking search time in whole minutes >= 0 (lever 4; not a price input).
OPTIONAL_TARIFF_COLUMNS = GARAGE_COLUMNS + ("commuter_day_eur", "search_time_min")
#: The loaded layout: schema 1 followed by the schema-2 columns ("schema 2 = schema 1 plus optional columns").
TARIFF_COLUMNS = SCHEMA_1_TARIFF_COLUMNS + OPTIONAL_TARIFF_COLUMNS
MONEY_COLUMNS = ("hourly_rate_eur", "first_period_eur", "daily_cap_eur", "long_stay_product_eur",
                 "member_day_eur", "guest_day_eur", "garage_hourly_rate_eur", "garage_first_period_eur",
                 "garage_daily_cap_eur", "commuter_day_eur")
#: Whole minutes (``Int64``); all positive except ``search_time_min``, where 0 means "no search time".
MINUTE_COLUMNS = ("billing_unit_min", "free_if_stay_at_most_min", "first_period_min", "max_stay_min",
                  "garage_billing_unit_min", "garage_first_period_min", "search_time_min")
NON_NEGATIVE_MINUTE_COLUMNS = ("search_time_min",)
HOUR_COLUMNS = ("fee_start_h", "fee_end_h", "garage_fee_start_h", "garage_fee_end_h")
BOOLEAN_COLUMNS = ("resident_exempt",)
TEXT_COLUMNS = tuple(column for column in TARIFF_COLUMNS
                     if column not in MONEY_COLUMNS + MINUTE_COLUMNS + HOUR_COLUMNS + BOOLEAN_COLUMNS)
#: Fields every row must carry, whatever its type (``notes`` is optional).
ALWAYS_REQUIRED_COLUMNS = ("zone_id", "name", "municipality_ags", "zone_type", "workplace_class", "fee_start_h",
                           "fee_end_h", "resident_exempt", "source_url", "source_date", "valid_from",
                           "fee_window_source")
#: Spec 3.1. ``resident_zone`` also needs ``billing_unit_min`` so that the metering arithmetic of spec 3.2
#: (units of the billing unit at rate 0) is defined; the unit has no effect on the price at rate 0. Its long-stay
#: product is required by the maximum-stay rule of ``validate_tariffs`` unless the row has a garage (v2).
REQUIRED_FIELDS_BY_TYPE = {
    "street_paid": ("hourly_rate_eur", "billing_unit_min"),
    "resident_zone": ("hourly_rate_eur", "billing_unit_min", "max_stay_min"),
    "campus": ("member_day_eur", "guest_day_eur"),
}
#: Fields the cost function of spec 3.2 would silently ignore (or that contradict the regime) per type.
FORBIDDEN_FIELDS_BY_TYPE = {
    "street_paid": ("member_day_eur", "guest_day_eur"),
    # free_if_stay_at_most_min is not inert on a resident zone: FREE_WITHIN_LIMIT precedes the max-stay check of
    # spec 3.2, so it would change the price. A day cap has no element to cap (the rate is 0 and PAID_LONG_STAY
    # returns before the metered block applies a cap), so it would be silently ignored. The cost reference
    # (braunschweig.parking.cost.ZoneTariff) rejects both as well. The commuter product exists on street_paid and
    # campus rows only (v2 spec amendment A4).
    "resident_zone": ("member_day_eur", "guest_day_eur", "first_period_min", "first_period_eur",
                      "free_if_stay_at_most_min", "daily_cap_eur", "commuter_day_eur"),
    # A campus stay pays a day product, so a garage would be ignored.
    "campus": ("hourly_rate_eur", "billing_unit_min", "free_if_stay_at_most_min", "first_period_min",
               "first_period_eur", "daily_cap_eur", "max_stay_min", "long_stay_product_eur") + GARAGE_COLUMNS,
}
#: Fields that are only meaningful together (spec 3.1: "first_period_min + first_period_eur"; v2: the garage first
#: period). ``max_stay_min`` and ``long_stay_product_eur`` follow the maximum-stay rule of ``validate_tariffs``.
PAIRED_FIELDS = (("first_period_min", "first_period_eur"), GARAGE_FIRST_PERIOD_COLUMNS)

FEE_WINDOW_SOURCES = ("ordinance", "signage", "municipal_page", "assumption")
#: Rule-based core of v2 lever 1 (``braunschweig.parking.zone_geometry``): erode(fill(R), W) minus buffer(F, W).
EROSION_GEOMETRY_SOURCE = "osm_fee_erosion"
#: Majority rule of v2 Amendment B (``braunschweig.parking.supply_share``): paid_share of the supply within W.
SUPPLY_MAJORITY_GEOMETRY_SOURCE = "osm_supply_majority"
#: Municipal street sections buffered (v2 spec Amendment C2): the area within ``section_buffer_m`` of the paid street
#: sections a municipality publishes for one tariff zone, split by the nearer section where the areas of two tariff
#: zones overlap (ties to the higher tariff).
MUNICIPAL_SECTIONS_GEOMETRY_SOURCE = "municipal_street_sections_buffered"
GEOMETRY_SOURCES = ("street_list_buffer", "osm_fee_tags", "centre_approximation", "ordinance_map",
                    EROSION_GEOMETRY_SOURCE, SUPPLY_MAJORITY_GEOMETRY_SOURCE, MUNICIPAL_SECTIONS_GEOMETRY_SOURCE)
#: Marker of the synthetic test set in ``tests/fixtures/parking``; rejected for the committed data.
FIXTURE_MARKER = "fixture"
ZONE_PROVENANCE_COLUMNS = ("geometry_source", "source_url", "source_date", "digitised_on", "digitising_note")
#: Required for ``osm_fee_erosion`` polygons only (empty on every other source): the walking tolerance W in metres
#: and the OSM snapshot of the Overpass response ('YYYY-MM-DDTHH:MM:SSZ', UTC).
EROSION_PROVENANCE_COLUMNS = ("unavoidable_walk_m", "osm_timestamp")
#: Required for ``osm_supply_majority`` polygons only: the walk distance W in metres, the paid-share threshold (a
#: share in (0, 1]), the minimum usable supply of a classified cell in spaces and the OSM snapshot of the extract.
SUPPLY_MAJORITY_PROVENANCE_COLUMNS = ("supply_walk_m", "paid_share_threshold", "minimum_usable_spaces",
                                      "osm_timestamp")
#: Required for ``municipal_street_sections_buffered`` polygons only: the buffer around the street sections in metres
#: (the access walk from the destination to a paid street section, ASSUMPTION C-a of spec Amendment C2).
MUNICIPAL_SECTIONS_PROVENANCE_COLUMNS = ("section_buffer_m",)
#: Provenance columns per rule-based source; a column is required on the sources that list it and empty elsewhere.
RULE_PROVENANCE_COLUMNS = {EROSION_GEOMETRY_SOURCE: EROSION_PROVENANCE_COLUMNS,
                           SUPPLY_MAJORITY_GEOMETRY_SOURCE: SUPPLY_MAJORITY_PROVENANCE_COLUMNS,
                           MUNICIPAL_SECTIONS_GEOMETRY_SOURCE: MUNICIPAL_SECTIONS_PROVENANCE_COLUMNS}
#: Optional on any polygon (empty = no reconstruction recorded): the part of its area in m2 whose boundary the digitised
#: source reconstructs from an older map, the flag of spec Amendment C1 (Braunschweig zone 1a: the southern part cut off
#: by the fee zone map of 2025-11-26, continued after the Amtsblatt annex of 2024-04-30). 0 means "none".
RECONSTRUCTED_SECTION_COLUMN = "reconstructed_section_m2"

REGISTER_COLUMNS = ("ags", "name", "status", "source", "note")
REGISTER_STATUSES = ("zoned", "no_paid_parking_known", "not_audited", "excluded")

#: Curation QA of the rule-based cores (``parking_zones_2026_qa.csv``), one row per curated municipality. Counts,
#: areas (m2) and the tagging completeness come from ``<ags>_qa_<tag>.json`` of the curation aid; ``role`` says whether
#: an accepted core may replace polygons (``zones_from_core``) or is QA only; ``q4_decision`` is the pre-registered
#: acceptance rule Q4 or ``request_failed`` (no response, the municipality stayed unchanged); ``applied`` and
#: ``zone_ids`` (';'-separated) name the polygons that carry the core.
ZONE_QA_COLUMNS = (
    "ags", "name", "role", "raw_response", "osm_timestamp", "walk_m", "maximum_filled_hole_m2", "minimum_island_m2",
    "segments", "regulated_segments", "free_segments", "free_segments_without_fee_tag", "mixed_segments",
    "separate_segments", "lots", "free_street_side_areas", "free_street_side_areas_without_fee_tag",
    "free_offstreet_lots", "free_offstreet_lots_in_core", "free_offstreet_lot_area_in_core_m2", "tagging_completeness",
    "regulated_area_m2", "filled_area_m2", "eroded_filled_area_m2", "core_area_m2", "core_parts", "reference",
    "core_share_inside_reference", "reference_share_covered_by_core", "reference_tagging_completeness",
    "largest_outline_distance_m", "reference_paid_ways", "reference_paid_street_side_areas", "reference_free_side_ways",
    "reference_free_side_length_m",
    "reference_free_street_side_areas", "reference_conflicts", "piece_assignment", "q4_decision", "applied", "zone_ids",
    "note",
)
ZONE_QA_ROLES = ("zones_from_core", "qa_only")
ZONE_QA_DECISIONS = ("accepted", "rejected", "request_failed")
_QA_COUNT_COLUMNS = ("segments", "regulated_segments", "free_segments", "free_segments_without_fee_tag",
                     "mixed_segments", "separate_segments", "lots", "free_street_side_areas",
                     "free_street_side_areas_without_fee_tag", "free_offstreet_lots", "free_offstreet_lots_in_core",
                     "core_parts")
#: Counts that exist only where a reference outline was compared (empty otherwise).
_QA_OPTIONAL_COUNT_COLUMNS = ("reference_paid_ways", "reference_paid_street_side_areas", "reference_free_side_ways",
                              "reference_free_street_side_areas")
_QA_AREA_COLUMNS = ("free_offstreet_lot_area_in_core_m2", "regulated_area_m2", "filled_area_m2", "eroded_filled_area_m2",
                    "core_area_m2")
_QA_PARAMETER_COLUMNS = ("walk_m", "maximum_filled_hole_m2", "minimum_island_m2")
_QA_SHARE_COLUMNS = ("tagging_completeness", "core_share_inside_reference", "reference_share_covered_by_core",
                     "reference_tagging_completeness")
#: Relative tolerance when comparing W of a polygon with W of its QA row.
_WALK_TOLERANCE = 1e-9

_ZONE_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
_AGS_PATTERN = re.compile(r"^\d{8}$")
_OSM_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


# --------------------------------------------------------------------------- reading helpers


def _read_documented_csv(path) -> pd.DataFrame:
    """Read a committed CSV whose documentation header lines start with ``#``; every cell as text.

    Only lines whose FIRST character is ``#`` are skipped: ``pandas.read_csv(comment="#")`` would cut a
    source URL with a fragment (``.../plan/#parken``) and is deliberately not used.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"parking input missing: {path}")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if not line.startswith("#")]
    if not lines:
        raise ValueError(f"{path}: no header row after the '#' documentation lines")
    return pd.read_csv(io.StringIO("\n".join(lines)), dtype=str, keep_default_na=False)


def _check_columns(frame: pd.DataFrame, expected: Iterable[str], where: str) -> None:
    expected = list(expected)
    missing = [column for column in expected if column not in frame.columns]
    unexpected = [column for column in frame.columns if column not in expected]
    if missing or unexpected:
        raise ValueError(f"{where}: columns differ from the documented layout: missing {missing}, "
                         f"unexpected {unexpected}")


def _parse_float(text: pd.Series, column: str, zone_ids: pd.Series, path) -> pd.Series:
    values = []
    for zone_id, value in zip(zone_ids, text):
        value = value.strip()
        if value == "":
            values.append(np.nan)
            continue
        try:
            number = float(value)
        except ValueError:
            raise ValueError(f"{path}: zone {zone_id!r}: {column} = {value!r} is not a number") from None
        if not math.isfinite(number):
            raise ValueError(f"{path}: zone {zone_id!r}: {column} = {value!r} is not a finite number")
        values.append(number)
    return pd.Series(values, index=text.index, dtype=float)


def _parse_minutes(text: pd.Series, column: str, zone_ids: pd.Series, path) -> pd.Series:
    values = []
    for zone_id, value in zip(zone_ids, text):
        value = value.strip()
        if value == "":
            values.append(pd.NA)
        elif re.fullmatch(r"-?\d+", value):
            values.append(int(value))
        else:
            raise ValueError(f"{path}: zone {zone_id!r}: {column} = {value!r} is not a whole number of minutes")
    return pd.Series(values, index=text.index, dtype="Int64")


def _parse_boolean(text: pd.Series, column: str, zone_ids: pd.Series, path) -> pd.Series:
    literals = {"true": True, "false": False}
    values = []
    for zone_id, value in zip(zone_ids, text):
        value = value.strip()
        if value not in literals:
            raise ValueError(f"{path}: zone {zone_id!r}: {column} = {value!r}; use the literal 'true' or 'false'")
        values.append(literals[value])
    return pd.Series(values, index=text.index, dtype=bool)


def _is_set(value) -> bool:
    """False for None, NaN, pd.NA and the empty string; True for every other scalar."""
    if isinstance(value, str):
        return value != ""
    return value is not None and not pd.isna(value)


def _is_true(value) -> bool:
    return isinstance(value, (bool, np.bool_)) and bool(value)


def _is_iso_date(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return True


# --------------------------------------------------------------------------- tariff table


def _check_tariff_columns(frame: pd.DataFrame, where: str) -> list[str]:
    """Raise for a missing schema-1 column or an unknown column; return the absent schema-2 columns, in order."""
    missing = [column for column in SCHEMA_1_TARIFF_COLUMNS if column not in frame.columns]
    unexpected = [column for column in frame.columns if column not in TARIFF_COLUMNS]
    if missing or unexpected:
        raise ValueError(f"{where}: columns differ from the documented layout: missing {missing}, "
                         f"unexpected {unexpected}")
    return [column for column in OPTIONAL_TARIFF_COLUMNS if column not in frame.columns]


def _log_schema_2_products(tariffs: pd.DataFrame, absent: list, path) -> None:
    """One line on the schema-2 products, so that a table without them is visibly priced by the street alone."""
    def rows_with(columns) -> int:
        return int(tariffs[list(columns)].notna().all(axis=1).sum())

    if absent:
        log.info("[parking-zones] %s lacks the optional schema-2 column(s) %s; they are read as empty", path, absent)
    log.info("[parking-zones] schema-2 products: garage on %d/%d rows, commuter product on %d/%d, search time on "
             "%d/%d", rows_with(GARAGE_CORE_COLUMNS), len(tariffs), rows_with(("commuter_day_eur",)), len(tariffs),
             rows_with(("search_time_min",)), len(tariffs))


def load_tariffs(path) -> pd.DataFrame:
    """Load the tariff table: ``#`` lines skipped, columns ``TARIFF_COLUMNS`` in order, typed.

    Types: money and hours ``float`` (NaN = not applicable), minutes ``Int64`` (NA = not applicable),
    ``resident_exempt`` ``bool`` (literal ``true``/``false`` only), text columns ``object`` with ``pd.NA``
    for empty cells. The schema-1 columns are required; an absent schema-2 column (``OPTIONAL_TARIFF_COLUMNS``) is
    read as an all-empty column and named in the log, so the returned frame always has the full layout. The table is
    NOT validated here; call ``validate_tariffs``.
    """
    raw = _read_documented_csv(path)
    absent = _check_tariff_columns(raw, str(path))
    for column in absent:
        raw[column] = ""
    zone_ids = raw["zone_id"].str.strip()
    typed = pd.DataFrame(index=raw.index)
    for column in TARIFF_COLUMNS:
        if column in MONEY_COLUMNS or column in HOUR_COLUMNS:
            typed[column] = _parse_float(raw[column], column, zone_ids, path)
        elif column in MINUTE_COLUMNS:
            typed[column] = _parse_minutes(raw[column], column, zone_ids, path)
        elif column in BOOLEAN_COLUMNS:
            typed[column] = _parse_boolean(raw[column], column, zone_ids, path)
        else:
            stripped = raw[column].str.strip()
            typed[column] = stripped.where(stripped != "", pd.NA).astype(object)
    log.info("[parking-zones] loaded %d tariff rows from %s", len(typed), path)
    _log_schema_2_products(typed, absent, path)
    return typed


def validate_tariffs(tariffs: pd.DataFrame, *, allow_fixture_marker: bool = True) -> None:
    """Check the tariff rows against spec 3.1/5.3 and the schema-2 rules; raise ``ValueError`` listing every violation.

    Schema 2 (v2 lever 2, amendment A6): the garage core is all-or-none, its day cap (empty = no cap) and its
    first-period pair are optional but need the core; a maximum stay needs a long-stay product or a garage (a longer
    stay loses the street product); ``commuter_day_eur`` exists on
    street_paid and campus rows only and is never negative; ``search_time_min`` is a whole number >= 0. A frame
    without the schema-2 columns is checked as if they were empty. ``allow_fixture_marker=False`` additionally
    rejects the synthetic test-set marker ``fixture`` in ``source_url`` and ``fee_window_source`` (the committed data
    must never carry it).
    """
    _check_tariff_columns(tariffs, "tariff table")
    tariffs = tariffs.reindex(columns=list(TARIFF_COLUMNS))
    if tariffs.empty:
        raise ValueError("tariff table: no rows")
    problems = []
    duplicates = sorted(set(tariffs["zone_id"][tariffs["zone_id"].duplicated()].dropna()))
    if duplicates:
        problems.append(f"duplicate zone_id(s) {duplicates}")
    fee_window_sources = FEE_WINDOW_SOURCES + ((FIXTURE_MARKER,) if allow_fixture_marker else ())
    for _, row in tariffs.iterrows():
        zone_id = row["zone_id"]
        prefix = f"zone {zone_id!r}"

        def problem(field: str, message: str) -> None:
            problems.append(f"{prefix}: {field}: {message}")

        for field in ALWAYS_REQUIRED_COLUMNS:
            if not _is_set(row[field]):
                problem(field, "required for every zone but empty")
        if _is_set(zone_id) and not _ZONE_ID_PATTERN.match(str(zone_id)):
            problem("zone_id", "use lower-case ASCII letters, digits and '_' only")
        zone_type = row["zone_type"]
        if _is_set(zone_type) and zone_type not in ZONE_TYPES:
            problem("zone_type", f"{zone_type!r} is not one of {list(ZONE_TYPES)}")
        workplace_class = row["workplace_class"]
        if _is_set(workplace_class) and workplace_class not in WORKPLACE_CLASSES:
            problem("workplace_class", f"{workplace_class!r} is not one of {list(WORKPLACE_CLASSES)}")
        ags = row["municipality_ags"]
        if _is_set(ags):
            if not _AGS_PATTERN.match(str(ags)) or str(ags)[:5] not in ZGB_COUNTY_KEYS:
                problem("municipality_ags", f"{ags!r} is not an 8-digit AGS of the ZGB counties {list(ZGB_COUNTY_KEYS)}")
            elif _is_set(workplace_class) and workplace_class in WORKPLACE_CLASSES:
                county = str(ags)[:5]
                allowed = BRAUNSCHWEIG_WORKPLACE_CLASSES if county == BRAUNSCHWEIG_COUNTY_KEY else (county,)
                if workplace_class not in allowed:
                    problem("workplace_class", f"{workplace_class!r} does not belong to county {county} of "
                                               f"municipality_ags {ags}; expected one of {list(allowed)}")
        for field in ("source_date", "valid_from"):
            if _is_set(row[field]) and not _is_iso_date(row[field]):
                problem(field, f"{row[field]!r} is not an ISO date YYYY-MM-DD")
        fee_window_source = row["fee_window_source"]
        if _is_set(fee_window_source) and fee_window_source not in fee_window_sources:
            problem("fee_window_source", f"{fee_window_source!r} is not one of {list(fee_window_sources)}")
        if not allow_fixture_marker and row["source_url"] == FIXTURE_MARKER:
            problem("source_url", f"the test-set marker {FIXTURE_MARKER!r} is not a source")

        for field in MONEY_COLUMNS:
            if _is_set(row[field]):
                if row[field] < 0:
                    problem(field, f"negative amount {row[field]}")
                elif abs(row[field] * 100 - round(row[field] * 100)) > MONEY_CENT_TOLERANCE:
                    problem(field, f"{row[field]} EUR is not a whole number of cents")
        if _is_set(row["daily_cap_eur"]) and row["daily_cap_eur"] == 0:
            problem("daily_cap_eur", "a day cap of 0 EUR makes the zone free; leave the field empty or drop the zone")
        if _is_set(row["garage_daily_cap_eur"]) and row["garage_daily_cap_eur"] == 0:
            problem("garage_daily_cap_eur", "a day cap of 0 EUR makes the garage free; enter the garage's real cap")
        for field in MINUTE_COLUMNS:
            if not _is_set(row[field]):
                continue
            if field in NON_NEGATIVE_MINUTE_COLUMNS:
                if row[field] < 0:
                    problem(field, f"must be a whole number of minutes >= 0, found {row[field]}")
            elif row[field] <= 0:
                problem(field, f"must be a positive number of minutes, found {row[field]}")
        for window in ("", "garage_"):
            start, end = row[f"{window}fee_start_h"], row[f"{window}fee_end_h"]
            if _is_set(start) and _is_set(end) and not (0.0 <= start < end <= 24.0):
                problem(f"{window}fee_start_h", f"fee window {start} .. {end} h must satisfy "
                                                f"0 <= {window}fee_start_h < {window}fee_end_h <= 24")
        for first, second in PAIRED_FIELDS:
            if _is_set(row[first]) != _is_set(row[second]):
                missing = second if _is_set(row[first]) else first
                problem(missing, f"{first} and {second} are set together or not at all")
        core_set = [field for field in GARAGE_CORE_COLUMNS if _is_set(row[field])]
        if core_set and len(core_set) < len(GARAGE_CORE_COLUMNS):
            for field in GARAGE_CORE_COLUMNS:
                if field not in core_set:
                    problem(field, f"the garage core {list(GARAGE_CORE_COLUMNS)} is set completely or not at all; "
                                   f"this row sets {core_set}")
        has_garage = len(core_set) == len(GARAGE_CORE_COLUMNS)
        if not core_set and _is_set(row["garage_daily_cap_eur"]):
            problem("garage_daily_cap_eur", f"a garage day cap needs the garage core {list(GARAGE_CORE_COLUMNS)}; "
                                            "it is never priced without it")
        if not core_set and any(_is_set(row[field]) for field in GARAGE_FIRST_PERIOD_COLUMNS):
            problem(GARAGE_FIRST_PERIOD_COLUMNS[0], f"a garage first period needs the garage core "
                                                    f"{list(GARAGE_CORE_COLUMNS)}; it is never priced without it")
        # A long-stay product is sold only above a maximum stay, and a longer stay loses the street product (v2): it
        # needs the long-stay product or a garage (the commuter product serves work and education only).
        if _is_set(row["long_stay_product_eur"]) and not _is_set(row["max_stay_min"]):
            problem("max_stay_min", "max_stay_min and long_stay_product_eur are set together or not at all: a "
                                    "long-stay product without a maximum stay is never sold")
        if _is_set(row["max_stay_min"]) and not _is_set(row["long_stay_product_eur"]) and not has_garage:
            problem("long_stay_product_eur", "max_stay_min is set but neither long_stay_product_eur nor the garage "
                                             "columns are: a stay above the maximum stay would have no product")

        if zone_type in ZONE_TYPES:
            for field in REQUIRED_FIELDS_BY_TYPE[zone_type]:
                if not _is_set(row[field]):
                    problem(field, f"required for zone_type {zone_type!r} but empty")
            for field in FORBIDDEN_FIELDS_BY_TYPE[zone_type]:
                if _is_set(row[field]):
                    problem(field, f"not applicable to zone_type {zone_type!r}; leave it empty")
            if zone_type == "resident_zone":
                if not _is_true(row["resident_exempt"]):
                    problem("resident_exempt", "a resident_zone must exempt its residents (true)")
                if _is_set(row["hourly_rate_eur"]) and row["hourly_rate_eur"] != 0:
                    problem("hourly_rate_eur", "a resident_zone charges non-residents by disc, rate must be 0")
    if problems:
        raise ValueError("invalid parking tariff table:\n  " + "\n  ".join(problems))


# --------------------------------------------------------------------------- zone polygons


def _polygonal_part(geometry):
    """The polygonal part of a (repaired) geometry; lines or points left by ``make_valid`` are dropped."""
    if geometry is None or geometry.is_empty:
        return geometry
    if geometry.geom_type in ("Polygon", "MultiPolygon"):
        return geometry
    parts = [part for part in getattr(geometry, "geoms", []) if part.geom_type in ("Polygon", "MultiPolygon")]
    return unary_union(parts) if parts else None


def _osm_timestamp_text(values: pd.Series) -> pd.Series:
    """``osm_timestamp`` as text: GDAL reads an ISO string with 'Z' as a UTC datetime, which is written back as
    'YYYY-MM-DDTHH:MM:SSZ'; a timezone-naive datetime (a date or a local time) keeps its ISO form and then fails
    the snapshot pattern."""
    if isinstance(values.dtype, pd.DatetimeTZDtype):
        utc = values.dt.tz_convert("UTC")
        return pd.Series([None if pd.isna(value) else value.strftime("%Y-%m-%dT%H:%M:%SZ") for value in utc],
                         index=values.index, dtype=object)
    if pd.api.types.is_datetime64_any_dtype(values):
        return pd.Series([None if pd.isna(value) else value.isoformat() for value in values], index=values.index,
                         dtype=object)
    return pd.Series([None if not _is_set(value) else str(value).strip() for value in values], index=values.index,
                     dtype=object)


#: Valid range of every numeric rule provenance column: (description, test).
_RULE_PROVENANCE_RANGES = {
    "unavoidable_walk_m": ("a positive number of metres", lambda value: value > 0),
    "supply_walk_m": ("a positive number of metres", lambda value: value > 0),
    "paid_share_threshold": ("a share in (0, 1]", lambda value: 0 < value <= 1),
    "minimum_usable_spaces": ("a number of spaces >= 0", lambda value: value >= 0),
    "section_buffer_m": ("a positive number of metres", lambda value: value > 0),
}


def _rule_provenance_problems(zones: pd.DataFrame) -> list:
    """Every column of ``RULE_PROVENANCE_COLUMNS`` set, valid and non-empty exactly on the polygons of the sources
    that list it (``osm_timestamp`` belongs to both OSM rule sources; every column without a numeric range is an OSM
    snapshot)."""
    problems = []
    sources = zones["geometry_source"].to_numpy()
    ids = zones["zone_id"].astype(str).to_numpy()
    columns = list(dict.fromkeys(column for listed in RULE_PROVENANCE_COLUMNS.values() for column in listed))
    for column in columns:
        owners = [source for source, listed in RULE_PROVENANCE_COLUMNS.items() if column in listed]
        owned = np.isin(sources, owners)
        values = zones[column] if column in zones.columns else pd.Series([None] * len(zones), index=zones.index)
        empty = np.array([not _is_set(value) or (isinstance(value, str) and not value.strip()) for value in values],
                         dtype=bool)
        for source in owners:
            missing = (sources == source) & empty
            if missing.any():
                problems.append(f"{column} required for geometry_source {source} but empty for zone(s) "
                                f"{sorted(ids[missing])}")
        if (~owned & ~empty).any():
            problems.append(f"{column} only applies to geometry_source {', '.join(owners)}; set for zone(s) "
                            f"{sorted(ids[~owned & ~empty])}")
        if column in _RULE_PROVENANCE_RANGES:
            description, valid = _RULE_PROVENANCE_RANGES[column]
            numbers = pd.to_numeric(values, errors="coerce").to_numpy(dtype=float)
            invalid = owned & ~empty & ~np.array([math.isfinite(number) and valid(number) for number in numbers],
                                                 dtype=bool)
            if invalid.any():
                problems.append(f"{column} must be {description} for zone(s) {sorted(ids[invalid])}")
        else:
            invalid = owned & ~empty & np.array([not (isinstance(value, str) and _OSM_TIMESTAMP_PATTERN.match(value))
                                                 for value in values], dtype=bool)
            if invalid.any():
                problems.append(f"{column} must be the OSM snapshot 'YYYY-MM-DDTHH:MM:SSZ' for zone(s) "
                                f"{sorted(ids[invalid])}")
    return problems


def _reconstructed_section_problems(zones: gpd.GeoDataFrame) -> list:
    """``RECONSTRUCTED_SECTION_COLUMN`` where set: a number of m2 from 0 to the polygon's area (EPSG:25832; the
    digitisation tolerance ``OVERLAP_TOLERANCE_M2`` absorbs the rounding of the stored WGS84 coordinates)."""
    if RECONSTRUCTED_SECTION_COLUMN not in zones.columns:
        return []
    values = pd.to_numeric(zones[RECONSTRUCTED_SECTION_COLUMN], errors="coerce").to_numpy(dtype=float)
    given = np.array([_is_set(value) and not (isinstance(value, str) and not value.strip())
                      for value in zones[RECONSTRUCTED_SECTION_COLUMN]], dtype=bool)
    areas = zones.geometry.area.to_numpy(dtype=float)
    invalid = given & ~np.array([math.isfinite(value) and 0.0 <= value <= area + OVERLAP_TOLERANCE_M2
                                 for value, area in zip(values, areas)], dtype=bool)
    if invalid.any():
        return [f"{RECONSTRUCTED_SECTION_COLUMN} of zone(s) {sorted(zones.loc[invalid, 'zone_id'].astype(str))} must be "
                "a number of m2 from 0 to the polygon's area"]
    return []


def load_zone_polygons(path, *, max_repairs: Optional[int] = None) -> gpd.GeoDataFrame:
    """Load the zone polygons, reproject to EPSG:25832, repair invalid rings, validate; return the frame.

    ``max_repairs`` bounds the number of polygons that may need the repair (``None``: any number, counted and logged):
    a repair changes the polygon the file states, so the committed release must need none
    (``scripts/validate_parking_zones.py`` and the curation assembly pass 0) and more raises ``ValueError`` naming the
    invalid polygons and the reason.

    Required properties per feature: ``zone_id`` and ``ZONE_PROVENANCE_COLUMNS``; ``geometry_source`` must
    be one of ``GEOMETRY_SOURCES`` (or the test-set marker). A rule-based polygon also needs the provenance columns
    of its source (``RULE_PROVENANCE_COLUMNS``: ``osm_fee_erosion`` a positive ``unavoidable_walk_m``,
    ``osm_supply_majority`` a positive ``supply_walk_m``, ``paid_share_threshold`` in (0, 1] and
    ``minimum_usable_spaces`` >= 0; both ``osm_timestamp`` as returned text 'YYYY-MM-DDTHH:MM:SSZ';
    ``municipal_street_sections_buffered`` a positive ``section_buffer_m``), which every other polygon leaves empty. The
    optional ``reconstructed_section_m2`` lies between 0 and the polygon's area. Other properties are kept as they are.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"parking zone polygons missing: {path}")
    zones = gpd.read_file(path)
    if zones.crs is None:
        raise ValueError(f"{path}: the polygon file declares no CRS")
    required = ("zone_id",) + ZONE_PROVENANCE_COLUMNS
    missing_columns = [column for column in required if column not in zones.columns]
    if missing_columns:
        raise ValueError(f"{path}: zone polygons lack the provenance field(s) {missing_columns}")
    problems = []
    for column in required:
        empty = zones[column].isna() | (zones[column].astype(str).str.strip() == "")
        if empty.any():
            problems.append(f"{column} empty for zone(s) {sorted(zones.loc[empty, 'zone_id'].astype(str))}")
    allowed_sources = GEOMETRY_SOURCES + (FIXTURE_MARKER,)
    unknown = ~zones["geometry_source"].isin(allowed_sources)
    if unknown.any():
        problems.append(f"geometry_source not one of {list(GEOMETRY_SOURCES)} for zone(s) "
                        f"{sorted(zones.loc[unknown, 'zone_id'].astype(str))}")
    if "osm_timestamp" in zones.columns:
        zones["osm_timestamp"] = _osm_timestamp_text(zones["osm_timestamp"])
    problems += _rule_provenance_problems(zones)
    if problems:
        raise ValueError(f"{path}: invalid zone provenance: " + "; ".join(problems))
    zones = zones.to_crs(CRS)
    invalid = ~zones.geometry.is_valid
    if max_repairs is not None and int(invalid.sum()) > max_repairs:
        reasons = [f"{zone_id}: {is_valid_reason(geometry)}"
                   for zone_id, geometry in zip(zones.loc[invalid, "zone_id"], zones.geometry[invalid])]
        raise ValueError(f"{path}: {int(invalid.sum())} invalid polygon(s) would need a repair, at most {max_repairs} "
                         f"allowed (write valid polygons: clean thin features before the file is written): "
                         + "; ".join(reasons))
    if invalid.any():
        repaired = [_polygonal_part(make_valid(geometry)) for geometry in zones.geometry[invalid]]
        zones.loc[invalid, "geometry"] = gpd.GeoSeries(repaired, index=zones.index[invalid], crs=CRS)
    log.info("[parking-zones] loaded %d zone polygons from %s; repaired %d invalid geometries (%s)", len(zones),
             path, int(invalid.sum()), sorted(zones.loc[invalid, "zone_id"].astype(str)))
    problems = _reconstructed_section_problems(zones)
    if problems:
        raise ValueError(f"{path}: invalid zone provenance: " + "; ".join(problems))
    validate_zone_polygons(zones)
    ordered = list(required) + [column for column in zones.columns if column not in required and column != "geometry"]
    return gpd.GeoDataFrame(zones[ordered + ["geometry"]], geometry="geometry", crs=CRS)


def validate_zone_polygons(zones: gpd.GeoDataFrame, *, overlap_tolerance_m2: float = OVERLAP_TOLERANCE_M2) -> None:
    """Non-empty, EPSG:25832, unique ids, valid non-empty (multi)polygons, pairwise overlap <= tolerance."""
    if zones is None or len(zones) == 0:
        raise ValueError("zone polygons: no zones")
    if zones.crs is None or zones.crs.to_epsg() != 25832:
        raise ValueError(f"zone polygons must be in {CRS}, found {zones.crs}")
    if "zone_id" not in zones.columns or zones["zone_id"].isna().any():
        raise ValueError("zone polygons: every polygon needs a zone_id")
    duplicates = sorted(set(zones["zone_id"][zones["zone_id"].duplicated()]))
    if duplicates:
        raise ValueError(f"zone polygons: duplicate zone_id(s) {duplicates}")
    problems = []
    for zone_id, geometry in zip(zones["zone_id"], zones.geometry):
        if geometry is None or geometry.is_empty:
            problems.append(f"{zone_id}: empty geometry")
        elif geometry.geom_type not in ("Polygon", "MultiPolygon"):
            problems.append(f"{zone_id}: {geometry.geom_type} is not a polygon")
        elif not geometry.is_valid:
            problems.append(f"{zone_id}: invalid geometry")
        elif not geometry.area > 0:
            problems.append(f"{zone_id}: zero area")
    if problems:
        raise ValueError("zone polygons: " + "; ".join(problems))
    ids = list(zones["zone_id"])
    geometries = list(zones.geometry)
    left, right = zones.sindex.query(zones.geometry, predicate="intersects")
    overlaps = []
    for i, j in zip(left, right):
        if i < j:
            area = geometries[i].intersection(geometries[j]).area
            if area > overlap_tolerance_m2:
                overlaps.append(f"{ids[i]}/{ids[j]} {area:.1f} m2")
    if overlaps:
        raise ValueError(f"zone polygons overlap by more than {overlap_tolerance_m2} m2: " + "; ".join(overlaps))


def cross_validate(zones: gpd.GeoDataFrame, tariffs: pd.DataFrame) -> None:
    """Every polygon has exactly one tariff row and every tariff row a polygon (ids compared as sets)."""
    zone_ids, tariff_ids = set(zones["zone_id"]), set(tariffs["zone_id"])
    without_tariff = sorted(zone_ids - tariff_ids)
    without_polygon = sorted(tariff_ids - zone_ids)
    if without_tariff or without_polygon:
        raise ValueError(f"parking zones and tariffs disagree: polygons without a tariff row {without_tariff}; "
                         f"tariff rows without a polygon {without_polygon}")


def _assign_polygon_ids(points: gpd.GeoDataFrame, polygons: gpd.GeoDataFrame, *, id_column: str, output_name: str,
                        function_name: str, noun: str) -> pd.Series:
    """The ``id_column`` value of the polygon containing each point (``within``), NaN outside; indexed like ``points``.

    The one point-in-polygon join of ``assign_zones`` and ``assign_districts``; ``function_name`` and ``noun``
    ("zone" or "district") only word the error messages. The spatial join runs on a positional copy of the points,
    so a duplicated index of ``points`` is preserved. A point inside two polygons raises, and so does a null or empty
    point geometry: a missing coordinate must not become free parking.
    """
    if points.crs is None or polygons.crs is None or not points.crs.equals(polygons.crs):
        raise ValueError(f"{function_name} needs points and {noun}s in the same CRS, found {points.crs} and "
                         f"{polygons.crs}")
    missing = points.geometry.isna() | points.geometry.is_empty
    if missing.any():
        raise ValueError(f"{function_name}: {int(missing.sum())} point(s) without coordinates (null or empty geometry), "
                         f"first index labels {list(points.index[missing][:5])}; locate them before assigning {noun}s")
    positions = gpd.GeoDataFrame({"_position": np.arange(len(points))}, geometry=list(points.geometry), crs=points.crs)
    joined = gpd.sjoin(positions, polygons[[id_column, "geometry"]], how="left", predicate="within")
    hits = joined.dropna(subset=[id_column])
    multiple = hits["_position"].value_counts()
    multiple = multiple[multiple > 1]
    if len(multiple):
        examples = hits[hits["_position"].isin(multiple.index[:5])].groupby("_position")[id_column].apply(sorted)
        raise ValueError(f"{len(multiple)} point(s) lie in more than one {noun} (overlapping polygons), e.g. "
                         f"{examples.to_dict()}")
    by_position = hits.set_index("_position")[id_column]
    values = by_position.reindex(np.arange(len(points))).to_numpy(dtype=object)
    return pd.Series(values, index=points.index, name=output_name, dtype=object)


def assign_zones(points: gpd.GeoDataFrame, zones: gpd.GeoDataFrame) -> pd.Series:
    """Zone id of every point (``within`` its polygon) or NaN outside every zone; indexed like ``points``.

    A point inside two zones means overlapping polygons and raises. A null or empty point geometry raises too: a
    missing coordinate must not become free parking. Under ``within`` a point exactly on a shared zone edge lies in
    neither zone and gets NaN (a measure-zero case, accepted). Coverage is logged as a rate because "no zone" means
    free parking downstream (assumption Z1), never a silent default.
    """
    assigned = _assign_polygon_ids(points, zones, id_column="zone_id", output_name="parking_zone",
                                   function_name="assign_zones", noun="zone")
    inside = int(assigned.notna().sum())
    share = 100.0 * inside / len(points) if len(points) else 0.0
    log.info("[parking-zones] %d of %d points (%.1f %%) lie in a parking zone; %d outside every zone (free, Z1)",
             inside, len(points), share, len(points) - inside)
    return assigned


# --------------------------------------------------------------------------- resident parking districts


#: How a district polygon came to be (spec Amendment C3): digitised from the municipality's published district map
#: (Braunschweig), or taken as published from the municipality's feature service (Goslar).
DIGITISED_MAP_GEOMETRY_SOURCE = "digitised_published_map"
FEATURE_SERVICE_GEOMETRY_SOURCE = "municipal_feature_service"
DISTRICT_GEOMETRY_SOURCES = (DIGITISED_MAP_GEOMETRY_SOURCE, FEATURE_SERVICE_GEOMETRY_SOURCE)
#: The properties of every district feature, in this order. ``district_id`` is unique across the layer (the curation
#: prefixes it per town, e.g. ``bs_district_a``), ``district_code`` is the municipality's own label (A, B, C, ...),
#: ``municipality_ags`` the 8-digit AGS. ``source_date`` is the ISO date the source was retrieved or supplied and
#: ``digitised_on`` the ISO date the geometry was digitised (Braunschweig) or exported from the service (Goslar).
DISTRICT_PROVENANCE_COLUMNS = ("district_id", "district_code", "municipality_ags", "name", "geometry_source",
                               "source_url", "source_date", "digitised_on", "digitising_note")
_DISTRICT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9]+$")
_ISO_DATE_COLUMNS = ("source_date", "digitised_on")


def _iso_date_text(values: pd.Series) -> pd.Series:
    """Provenance dates as text. GDAL reads a GeoJSON column whose values are all ``YYYY-MM-DD`` strings as a date
    column; a midnight timestamp is written back as ``YYYY-MM-DD``, anything else keeps its ISO form and then fails
    the date check."""
    if pd.api.types.is_datetime64_any_dtype(values):
        return pd.Series([None if pd.isna(value) else
                          (value.strftime("%Y-%m-%d") if value == value.normalize() else value.isoformat())
                          for value in values], index=values.index, dtype=object)
    return pd.Series([None if not _is_set(value) else str(value).strip() for value in values], index=values.index,
                     dtype=object)


def load_resident_districts(path) -> gpd.GeoDataFrame:
    """Load the resident parking district layer (spec Amendment C3), reproject to EPSG:25832, validate; return it.

    The layer is a second, independent layer next to the fee zones: districts may overlap fee zones, never each
    other. Required properties per feature are ``DISTRICT_PROVENANCE_COLUMNS``, non-empty, with a
    ``geometry_source`` from ``DISTRICT_GEOMETRY_SOURCES`` (or the test-set marker) and ISO dates. Unlike the zone
    polygons the districts are NEVER repaired: a polygon the loader had to repair is not the district the file
    states, so any invalid geometry raises ``ValueError`` naming the district and the reason ("valid as stored").
    Further rules: ``validate_resident_districts``. Other properties are kept.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"parking resident districts missing: {path}")
    districts = gpd.read_file(path)
    if districts.crs is None:
        raise ValueError(f"{path}: the district file declares no CRS")
    missing_columns = [column for column in DISTRICT_PROVENANCE_COLUMNS if column not in districts.columns]
    if missing_columns:
        raise ValueError(f"{path}: resident districts lack the provenance field(s) {missing_columns}")
    for column in _ISO_DATE_COLUMNS:
        districts[column] = _iso_date_text(districts[column])
    ids = districts["district_id"].astype(str)
    problems = []
    for column in DISTRICT_PROVENANCE_COLUMNS:
        empty = districts[column].isna() | (districts[column].astype(str).str.strip() == "")
        if empty.any():
            problems.append(f"{column} empty for district(s) {sorted(ids[empty])}")
    allowed_sources = DISTRICT_GEOMETRY_SOURCES + (FIXTURE_MARKER,)
    unknown = ~districts["geometry_source"].isin(allowed_sources)
    if unknown.any():
        problems.append(f"geometry_source not one of {list(DISTRICT_GEOMETRY_SOURCES)} for district(s) "
                        f"{sorted(ids[unknown])}")
    for column in _ISO_DATE_COLUMNS:
        bad = districts[column].notna() & ~districts[column].map(_is_iso_date).astype(bool)
        if bad.any():
            problems.append(f"{column} is not an ISO date YYYY-MM-DD for district(s) {sorted(ids[bad])}")
    if problems:
        raise ValueError(f"{path}: invalid district provenance: " + "; ".join(problems))
    districts = districts.to_crs(CRS)
    invalid = ~districts.geometry.is_valid
    if invalid.any():
        reasons = [f"{district_id}: {is_valid_reason(geometry)}"
                   for district_id, geometry in zip(ids[invalid], districts.geometry[invalid])]
        raise ValueError(f"{path}: {int(invalid.sum())} invalid district polygon(s): " + "; ".join(reasons)
                         + ". The layer must be valid as stored: a repair would change the district the file states.")
    validate_resident_districts(districts)
    per_municipality = districts["municipality_ags"].value_counts().sort_index()
    log.info("[parking-zones] loaded %d resident parking districts from %s (%s)", len(districts), path,
             ", ".join(f"{ags} {count}" for ags, count in per_municipality.items()))
    ordered = list(DISTRICT_PROVENANCE_COLUMNS) + [column for column in districts.columns
                                                   if column not in DISTRICT_PROVENANCE_COLUMNS
                                                   and column != "geometry"]
    return gpd.GeoDataFrame(districts[ordered + ["geometry"]], geometry="geometry", crs=CRS)


def validate_resident_districts(districts: gpd.GeoDataFrame, *,
                                overlap_tolerance_m2: float = OVERLAP_TOLERANCE_M2) -> None:
    """Check the district layer; raise ``ValueError`` listing every violation.

    Non-empty, EPSG:25832, lower-case ASCII ``district_id`` unique across the layer, ``district_code`` unique per
    municipality, 8-digit AGS of the ZGB counties, valid non-empty (multi)polygons, and no two districts overlapping
    by more than ``overlap_tolerance_m2``: a point must lie in at most one district, whatever the municipality, so
    that ``assign_districts`` is a function.
    """
    if districts is None or len(districts) == 0:
        raise ValueError("resident districts: no districts")
    if districts.crs is None or districts.crs.to_epsg() != 25832:
        raise ValueError(f"resident districts must be in {CRS}, found {districts.crs}")
    missing = [column for column in ("district_id", "district_code", "municipality_ags") if column not in districts.columns]
    if missing:
        raise ValueError(f"resident districts: columns {missing} missing")
    problems = []
    ids = districts["district_id"].astype(str)
    duplicated = sorted(set(ids[ids.duplicated()]))
    if duplicated:
        problems.append(f"duplicate district_id(s) {duplicated}")
    keys = list(zip(districts["municipality_ags"].astype(str), districts["district_code"].astype(str)))
    repeated = sorted({key for key in keys if keys.count(key) > 1})
    if repeated:
        problems.append(f"duplicate (municipality_ags, district_code) {repeated}")
    for raw_id, district_id, code, ags, geometry in zip(districts["district_id"], ids, districts["district_code"],
                                                        districts["municipality_ags"], districts.geometry):
        if not isinstance(raw_id, str):
            problems.append(f"{district_id}: district_id {raw_id!r} is not text (the plans carry it as a string)")
        elif not _ZONE_ID_PATTERN.match(district_id):
            problems.append(f"{district_id}: district_id uses other than lower-case ASCII letters, digits and '_'")
        if not isinstance(code, str) or not _DISTRICT_CODE_PATTERN.match(code):
            problems.append(f"{district_id}: district_code {code!r} must be a non-empty label of letters and digits")
        if not _AGS_PATTERN.match(str(ags)):
            problems.append(f"{district_id}: municipality_ags {ags!r} is not an 8-digit AGS")
        elif str(ags)[:5] not in ZGB_COUNTY_KEYS:
            problems.append(f"{district_id}: municipality_ags {ags!r} is not an 8-digit AGS of the ZGB counties "
                            f"{list(ZGB_COUNTY_KEYS)}")
        if geometry is None or geometry.is_empty:
            problems.append(f"{district_id}: empty geometry")
        elif geometry.geom_type not in ("Polygon", "MultiPolygon"):
            problems.append(f"{district_id}: {geometry.geom_type} is not a polygon")
        elif not geometry.is_valid:
            problems.append(f"{district_id}: invalid geometry ({is_valid_reason(geometry)})")
        elif not geometry.area > 0:
            problems.append(f"{district_id}: zero area")
    if problems:
        raise ValueError("resident districts: " + "; ".join(problems))
    geometries = list(districts.geometry)
    left, right = districts.sindex.query(districts.geometry, predicate="intersects")
    overlaps = []
    for i, j in zip(left, right):
        if i < j:
            area = geometries[i].intersection(geometries[j]).area
            if area > overlap_tolerance_m2:
                overlaps.append(f"{ids.iloc[i]}/{ids.iloc[j]} {area:.1f} m2")
    if overlaps:
        raise ValueError(f"resident districts overlap by more than {overlap_tolerance_m2} m2: " + "; ".join(overlaps))


def validate_district_municipalities(districts: gpd.GeoDataFrame, register: pd.DataFrame) -> None:
    """Every district municipality needs a status row of the coverage register; warn where it is not ``zoned``.

    A district in a municipality the register does not know is a release inconsistency and raises
    ``ValueError``. A district in a municipality that is not ``zoned`` is inert -- the district rule acts inside a fee
    zone only (``braunschweig.parking.cost.parking_cost_cents``) -- and is logged as a warning naming the
    municipality and its status, never silently accepted.
    """
    status = register[register["status"] != "excluded"].set_index("ags")["status"]
    municipalities = sorted(set(districts["municipality_ags"].astype(str)))
    unknown = [ags for ags in municipalities if ags not in status.index]
    if unknown:
        raise ValueError(f"resident districts lie in municipality_ags {unknown} that the coverage register has no "
                         "status row for: the districts and the register must belong to one release")
    inert = {ags: status[ags] for ags in municipalities if status[ags] != "zoned"}
    if inert:
        log.warning("[parking-zones] resident districts in municipalities that are not 'zoned' have no effect (the "
                    "district rule acts inside a fee zone only): %s", inert)


def assign_districts(points: gpd.GeoDataFrame, districts: gpd.GeoDataFrame) -> pd.Series:
    """District id of every point (``within`` its polygon) or NaN outside every district; indexed like ``points``.

    The same join as :func:`assign_zones` on the ``district_id`` of a layer that passed
    :func:`validate_resident_districts`, so a point lies in at most one district; a point inside two raises, and so
    does a null or empty point geometry. Coverage is logged as a rate: "no district" only means that the person
    gets no resident exemption there, but the rate is the evidence that the join worked.
    """
    assigned = _assign_polygon_ids(points, districts, id_column="district_id", output_name="parking_district",
                                   function_name="assign_districts", noun="district")
    inside = int(assigned.notna().sum())
    share = 100.0 * inside / len(points) if len(points) else 0.0
    log.info("[parking-zones] %d of %d points (%.1f %%) lie in a resident parking district; %d outside every "
             "district (no district exemption)", inside, len(points), share, len(points) - inside)
    return assigned


# --------------------------------------------------------------------------- coverage register


def load_coverage_register(path) -> pd.DataFrame:
    """Load the coverage register (``#`` lines skipped, text columns ``REGISTER_COLUMNS``, empty = "")."""
    register = _read_documented_csv(path)
    _check_columns(register, REGISTER_COLUMNS, str(path))
    register = register[list(REGISTER_COLUMNS)].apply(lambda column: column.str.strip())
    log.info("[parking-zones] loaded %d coverage register rows from %s", len(register), path)
    return register


def validate_coverage_register(register: pd.DataFrame, tariffs: pd.DataFrame, *,
                               expected_ags: Optional[Iterable[str]] = None) -> None:
    """Check the register against itself, the tariff table and (optionally) the municipality universe.

    Rules: every row has an 8-digit ZGB ``ags``, a ``name`` and a status in ``REGISTER_STATUSES``; every AGS
    has exactly one non-excluded row; ``excluded`` rows name their reason in ``note`` and belong to a
    municipality with a status row; ``no_paid_parking_known`` names its ``source``; ``zoned`` municipalities
    own at least one tariff row and every tariff row's municipality is ``zoned``. With ``expected_ags``
    the municipalities of the non-excluded rows must equal that set.
    """
    _check_columns(register, REGISTER_COLUMNS, "coverage register")
    problems = []
    for _, row in register.iterrows():
        ags, status = row["ags"], row["status"]
        if not _AGS_PATTERN.match(str(ags)) or str(ags)[:5] not in ZGB_COUNTY_KEYS:
            problems.append(f"ags {ags!r}: not an 8-digit AGS of the ZGB counties")
        if not _is_set(row["name"]):
            problems.append(f"ags {ags}: name is empty")
        if status not in REGISTER_STATUSES:
            problems.append(f"ags {ags}: status {status!r} is not one of {list(REGISTER_STATUSES)}")
        if status == "no_paid_parking_known" and not _is_set(row["source"]):
            problems.append(f"ags {ags}: 'no_paid_parking_known' needs a source")
        if status == "excluded" and not _is_set(row["note"]):
            problems.append(f"ags {ags} ({row['name']}): an excluded area needs its reason in 'note'")
    status_rows = register[register["status"] != "excluded"]
    duplicated = sorted(set(status_rows["ags"][status_rows["ags"].duplicated()]))
    if duplicated:
        problems.append(f"duplicate status rows for ags {duplicated}; one non-excluded row per municipality")
    status_ags = set(status_rows["ags"])
    orphans = sorted(set(register.loc[register["status"] == "excluded", "ags"]) - status_ags)
    if orphans:
        problems.append(f"excluded areas in municipalities without a status row: {orphans}")
    zoned = set(status_rows.loc[status_rows["status"] == "zoned", "ags"])
    tariff_ags = set(tariffs["municipality_ags"].dropna())
    zoned_without_zone = sorted(zoned - tariff_ags)
    if zoned_without_zone:
        problems.append(f"'zoned' municipalities without any tariff row: {zoned_without_zone}")
    zones_outside_zoned = sorted(tariff_ags - zoned)
    if zones_outside_zoned:
        problems.append(f"tariff rows in municipalities not marked 'zoned': {zones_outside_zoned}")
    if expected_ags is not None:
        expected = {str(ags) for ags in expected_ags}
        missing, unexpected = sorted(expected - status_ags), sorted(status_ags - expected)
        if missing or unexpected:
            problems.append(f"register differs from the municipality universe: missing {missing}, "
                            f"unexpected {unexpected}")
    if problems:
        raise ValueError("invalid parking coverage register:\n  " + "\n  ".join(problems))


# --------------------------------------------------------------------------- curation QA of the rule-based cores


def load_zone_qa(path) -> pd.DataFrame:
    """Load ``parking_zones_2026_qa.csv`` (``#`` lines skipped, text columns ``ZONE_QA_COLUMNS``, empty = "")."""
    qa = _read_documented_csv(path)
    _check_columns(qa, ZONE_QA_COLUMNS, str(path))
    qa = qa[list(ZONE_QA_COLUMNS)].apply(lambda column: column.str.strip())
    log.info("[parking-zones] loaded %d zone QA rows from %s", len(qa), path)
    return qa


def _qa_number(value) -> Optional[float]:
    """A QA cell as float, None when empty, NaN when not a number."""
    if not _is_set(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _zone_ids(text) -> list:
    return [zone_id.strip() for zone_id in str(text or "").split(";") if zone_id.strip()]


def validate_zone_qa(qa: pd.DataFrame, zones: pd.DataFrame, tariffs: pd.DataFrame) -> None:
    """Check the QA table against itself and against the polygons; raise ``ValueError`` listing every violation.

    Rules: one row per 8-digit ZGB ``ags``; ``role`` in ``ZONE_QA_ROLES``, ``q4_decision`` in
    ``ZONE_QA_DECISIONS``, ``applied`` in {true, false}; a ``request_failed`` row is not applied, carries no numbers
    and names the failure in ``note``; every other row has the snapshot, the raw file, positive parameters,
    non-negative counts and areas and shares in [0, 1] (empty allowed where undefined); an applied row has role
    ``zones_from_core``, decision ``accepted`` and at least one zone id, a row that is not applied none. Every zone id
    of an applied row is an ``osm_fee_erosion`` polygon whose tariff row lies in the row's municipality and whose
    ``unavoidable_walk_m`` and ``osm_timestamp`` equal the row's ``walk_m`` and ``osm_timestamp``; every
    ``osm_fee_erosion`` polygon is listed by exactly one applied row. The acceptance rule itself (Q4) is re-applied
    by ``scripts/validate_parking_zones.py``.
    """
    _check_columns(qa, ZONE_QA_COLUMNS, "zone QA table")
    problems = []
    if qa.empty:
        problems.append("no rows")
    duplicated = sorted(set(qa["ags"][qa["ags"].duplicated()]))
    if duplicated:
        problems.append(f"duplicate rows for ags {duplicated}; one row per municipality")
    polygons = zones.set_index("zone_id")
    municipality = tariffs.set_index("zone_id")["municipality_ags"]
    listed = {}
    for _, row in qa.iterrows():
        ags = row["ags"]
        prefix = f"ags {ags}"
        if not _AGS_PATTERN.match(str(ags)) or str(ags)[:5] not in ZGB_COUNTY_KEYS:
            problems.append(f"{prefix}: not an 8-digit AGS of the ZGB counties")
        if row["role"] not in ZONE_QA_ROLES:
            problems.append(f"{prefix}: role {row['role']!r} is not one of {list(ZONE_QA_ROLES)}")
        decision = row["q4_decision"]
        if decision not in ZONE_QA_DECISIONS:
            problems.append(f"{prefix}: q4_decision {decision!r} is not one of {list(ZONE_QA_DECISIONS)}")
        if row["applied"] not in ("true", "false"):
            problems.append(f"{prefix}: applied {row['applied']!r}; use the literal 'true' or 'false'")
        applied = row["applied"] == "true"
        numbers = _QA_COUNT_COLUMNS + _QA_OPTIONAL_COUNT_COLUMNS + _QA_AREA_COLUMNS + _QA_PARAMETER_COLUMNS + \
            _QA_SHARE_COLUMNS + ("largest_outline_distance_m", "reference_free_side_length_m")
        if decision == "request_failed":
            carried = [column for column in numbers + ("osm_timestamp", "raw_response") if _is_set(row[column])]
            if carried:
                problems.append(f"{prefix}: request_failed rows carry no numbers and no snapshot, found {carried}")
            if not _is_set(row["note"]):
                problems.append(f"{prefix}: a request_failed row names the failure in 'note'")
        elif decision in ZONE_QA_DECISIONS:
            if not _OSM_TIMESTAMP_PATTERN.match(str(row["osm_timestamp"])):
                problems.append(f"{prefix}: osm_timestamp {row['osm_timestamp']!r} is not 'YYYY-MM-DDTHH:MM:SSZ'")
            if not _is_set(row["raw_response"]):
                problems.append(f"{prefix}: raw_response is empty")
            for column in _QA_PARAMETER_COLUMNS:
                value = _qa_number(row[column])
                if value is None or not (math.isfinite(value) and value >= 0) or (column == "walk_m" and value <= 0):
                    problems.append(f"{prefix}: {column} = {row[column]!r} must be a positive number")
            for column in _QA_COUNT_COLUMNS + _QA_OPTIONAL_COUNT_COLUMNS:
                empty_allowed = column in _QA_OPTIONAL_COUNT_COLUMNS and not _is_set(row[column])
                if not empty_allowed and not re.fullmatch(r"\d+", str(row[column])):
                    problems.append(f"{prefix}: {column} = {row[column]!r} must be a whole number >= 0")
            for column in _QA_AREA_COLUMNS + ("largest_outline_distance_m", "reference_free_side_length_m"):
                value = _qa_number(row[column])
                required = column in _QA_AREA_COLUMNS
                if (value is None and required) or (value is not None and not (math.isfinite(value) and value >= 0)):
                    problems.append(f"{prefix}: {column} = {row[column]!r} must be a number >= 0")
            for column in _QA_SHARE_COLUMNS:
                value = _qa_number(row[column])
                if value is not None and not (math.isfinite(value) and 0.0 <= value <= 1.0):
                    problems.append(f"{prefix}: {column} = {row[column]!r} must lie in [0, 1] (or be empty)")
        zone_ids = _zone_ids(row["zone_ids"])
        if applied:
            if row["role"] != "zones_from_core":
                problems.append(f"{prefix}: applied rows need role 'zones_from_core', found {row['role']!r}")
            if decision != "accepted":
                problems.append(f"{prefix}: applied rows need q4_decision 'accepted', found {decision!r}")
            if not zone_ids:
                problems.append(f"{prefix}: an applied row lists its zone_ids")
        elif zone_ids:
            problems.append(f"{prefix}: zone_ids {zone_ids} on a row that is not applied")
        for zone_id in zone_ids if applied else []:
            listed.setdefault(zone_id, []).append(ags)
            if zone_id not in polygons.index:
                problems.append(f"{prefix}: zone {zone_id!r} has no polygon")
                continue
            polygon = polygons.loc[zone_id]
            if polygon["geometry_source"] != EROSION_GEOMETRY_SOURCE:
                problems.append(f"{prefix}: zone {zone_id!r} is not an osm_fee_erosion polygon "
                                f"({polygon['geometry_source']})")
                continue
            if municipality.get(zone_id) != ags:
                problems.append(f"{prefix}: zone {zone_id!r} has municipality_ags {municipality.get(zone_id)!r}")
            walk = _qa_number(row["walk_m"])
            if walk is None or not math.isclose(float(polygon["unavoidable_walk_m"]), walk, rel_tol=_WALK_TOLERANCE):
                problems.append(f"{prefix}: zone {zone_id!r} has unavoidable_walk_m {polygon['unavoidable_walk_m']} "
                                f"but the QA row walk_m {row['walk_m']!r}")
            if polygon["osm_timestamp"] != row["osm_timestamp"]:
                problems.append(f"{prefix}: zone {zone_id!r} has osm_timestamp {polygon['osm_timestamp']!r} but the "
                                f"QA row {row['osm_timestamp']!r}")
    eroded = sorted(zones.loc[zones["geometry_source"] == EROSION_GEOMETRY_SOURCE, "zone_id"].astype(str))
    for zone_id in eroded:
        if zone_id not in listed:
            problems.append(f"zone {zone_id!r} (osm_fee_erosion) is not listed in the zone_ids of an applied QA row")
        elif len(listed[zone_id]) > 1:
            problems.append(f"zone {zone_id!r} is listed by several applied QA rows {listed[zone_id]}")
    if problems:
        raise ValueError("invalid parking zone QA table:\n  " + "\n  ".join(problems))
