"""Committed parking garage dataset: garages as points with their own tariff (parking cost zones v2, issue #436).

Spec Amendment E1 makes garages entities of their own: ``parking_garages_2026.geojson`` holds one point per garage (WGS84
on disk like the zone polygons, EPSG:25832 in memory) with its identity, the garage tariff columns of spec Amendment A6, a
monthly product where one is published, the reported capacity and the provenance of every value. The dataset is built by
the curation step ``scripts/curation/parking_zones_2026/regional_garages.py`` from the regional evidence package of
2026-10-07 and read by the distance-weighted garage options of spec Amendment E (task 4d); no stage reads it yet.

The garage tariff columns are those of the tariff table (``braunschweig.parking.zones.GARAGE_COLUMNS``, money in EUR,
minutes as whole numbers, fee window in decimal hours of the weekday) and mean the same: the core (hourly rate, billing
unit, fee window) is all-or-none, the day cap (empty = none) and the first-period pair (both or neither) need the core. A
garage is ``priced`` exactly when its core is set; a garage whose published tariff the columns cannot express exactly stays
listed and not priced with a ``not_priced_reason`` (``NOT_PRICED_REASONS``): no value is approximated or invented. Every
assumption a row rests on is an id in ``assumptions`` (``ASSUMPTIONS``) that the row's notes name, so that a priced garage
whose tariff is not stated in every detail can be told apart and counted.

Every validator raises ``ValueError`` listing every violation with the garage id and the column, so a broken dataset fails
at load time. CRS: EPSG:25832 in memory, distances in metres.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

from braunschweig.parking import zones

log = logging.getLogger(__name__)

CRS = zones.CRS
#: Plausibility box of the dataset in EPSG:25832 (min x, min y, max x, max y, m): the bounding box of the 123 spatial
#: units of the eight ZGB counties (VG250 as cached by the pipeline: x 567,953 to 642,483 m, y 5,722,106 to 5,854,930 m),
#: rounded outward to whole kilometres. It catches a wrong CRS or swapped axes; it is no membership test, the curation
#: checks every garage against the polygon of its own municipality.
ZGB_EXTENT_25832 = (567_000.0, 5_722_000.0, 643_000.0, 5_855_000.0)

IDENTITY_COLUMNS = ("garage_id", "package_facility_id", "name", "operator", "municipality", "municipality_ags")
CAPACITY_COLUMNS = ("capacity_reported", "capacity_scope")
#: The garage tariff columns of the tariff table (spec Amendment A6), in the order of the table.
TARIFF_COLUMNS = zones.GARAGE_COLUMNS
MONTHLY_COLUMNS = ("monthly_eur", "monthly_source_url", "monthly_product")
STATUS_COLUMNS = ("priced", "not_priced_reason", "assumptions")
PROVENANCE_COLUMNS = ("source_url", "source_date", "tariff_rule_ids", "geometry_method", "geometry_source_url",
                      "package_sha256", "notes")
#: Every property of a feature, in file order.
DATASET_COLUMNS = (IDENTITY_COLUMNS + CAPACITY_COLUMNS + TARIFF_COLUMNS + MONTHLY_COLUMNS + STATUS_COLUMNS
                   + PROVENANCE_COLUMNS)
MONEY_COLUMNS = ("garage_hourly_rate_eur", "garage_first_period_eur", "garage_daily_cap_eur", "monthly_eur")
MINUTE_COLUMNS = ("garage_billing_unit_min", "garage_first_period_min")
HOUR_COLUMNS = ("garage_fee_start_h", "garage_fee_end_h")
INTEGER_COLUMNS = ("capacity_reported",)
TEXT_COLUMNS = tuple(column for column in DATASET_COLUMNS
                     if column not in MONEY_COLUMNS + MINUTE_COLUMNS + HOUR_COLUMNS + INTEGER_COLUMNS + ("priced",))
#: Columns a row must carry whatever its status; ``operator``, ``capacity_*`` and the monthly product are optional
#: (empty = the package states none).
REQUIRED_TEXT_COLUMNS = ("garage_id", "package_facility_id", "name", "municipality", "municipality_ags", "source_url",
                         "source_date", "geometry_method", "geometry_source_url", "package_sha256", "notes")
LIST_SEPARATOR = ";"

#: Why a garage is listed and not priced (``not_priced_reason``): the column holds the code, the details of the garage
#: (what is published, which rule ids) are in its notes.
NOT_PRICED_REASONS = {
    "no_published_tariff": "the sources give no tariff of the garage",
    "free_period": "the tariff has a free first period, which the garage columns cannot express (they hold no free "
                   "threshold) and whose treatment on longer stays the source does not state",
    "banded_tariff": "the rate changes with the duration (progressive or banded), which a first period plus one rate "
                     "cannot express exactly",
    "incomplete_tariff": "the published tariff does not state how a stay is billed beyond its first unit",
    "conflicting_sources": "the sources of the tariff contradict each other and the package does not mark one rule set as "
                           "calculation-ready",
}
#: The assumptions a priced row may rest on (``assumptions``); each is named as ``ASSUMPTION <id>`` in the row's notes.
ASSUMPTIONS = {
    "P3": "day family only where day and night tariffs differ: the garage columns hold the main daytime tariff and its fee "
          "window, the other tariffs (night, shoulder hours) are stated in the notes and not charged",
    "P4": "a published rate per unit without a stated rounding is billed per started unit, as at every garage of the "
          "dataset that states its rounding",
    "P5": "the fee window is 0 to 24 h where the source states no charging times of the garage tariff: a ticket garage "
          "bills the stay from entry to exit, and opening hours are no charging hours",
}
#: Priced and not-priced counts are reported per assumption with these ids, so a sensitivity arm can leave a group out.
_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_URL_PATTERN = re.compile(r"^https?://\S+$")


def _split(text) -> list:
    """The ids of a ';'-separated list cell (``assumptions``, ``tariff_rule_ids``); empty for an empty cell."""
    if not zones._is_set(text):
        return []
    return [part.strip() for part in str(text).split(LIST_SEPARATOR) if part.strip()]


# --------------------------------------------------------------------------- loader


def _integer_series(values: pd.Series, column: str, path) -> pd.Series:
    numbers = pd.to_numeric(values, errors="raise").astype(float)
    fractional = numbers.notna() & (numbers != numbers.round())
    if fractional.any():
        raise ValueError(f"{path}: {column} holds non-integer value(s) {sorted(set(numbers[fractional]))}")
    return pd.Series(numbers.round().astype("Int64"), index=values.index)


def _text_series(values: pd.Series) -> pd.Series:
    """Text cells as ``str``, an empty or null cell as ``None`` (the file holds JSON null for a value the package lacks)."""
    return pd.Series([str(value).strip() if zones._is_set(value) and str(value).strip() else None for value in values],
                     index=values.index, dtype=object)


def load_garages(path) -> gpd.GeoDataFrame:
    """Load the garage dataset: WGS84 GeoJSON on disk, EPSG:25832 points in memory, every column typed.

    Types: money and hours ``float`` (NaN = not applicable), minutes and the capacity ``Int64`` (NA = not applicable),
    ``priced`` ``bool`` (a null raises), text ``object`` with ``None`` for an empty cell. The columns must be exactly
    ``DATASET_COLUMNS``. The dataset is NOT validated here; call :func:`validate_garages`. Logs the garages, how many are
    priced and how many rest on each assumption, as rates, so that a dataset in which most garages are not priced, or in
    which most prices rest on an assumption, is visible at every load.
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"parking garage dataset missing: {path}")
    frame = gpd.read_file(path)
    if frame.crs is None:
        raise ValueError(f"{path}: the garage file declares no CRS")
    missing = [column for column in DATASET_COLUMNS if column not in frame.columns]
    unexpected = [column for column in frame.columns if column not in DATASET_COLUMNS and column != "geometry"]
    if missing or unexpected:
        raise ValueError(f"{path}: columns differ from the documented layout: missing {missing}, unexpected {unexpected}")
    typed = pd.DataFrame(index=frame.index)
    for column in DATASET_COLUMNS:
        values = frame[column]
        if column in MONEY_COLUMNS + HOUR_COLUMNS:
            typed[column] = pd.to_numeric(values, errors="raise").astype(float)
        elif column in MINUTE_COLUMNS + INTEGER_COLUMNS:
            typed[column] = _integer_series(values, column, path)
        elif column == "source_date":
            # GDAL reads a column whose values are all YYYY-MM-DD strings as a date column; back to ISO text
            typed[column] = _text_series(zones._iso_date_text(values))
        elif column == "priced":
            if values.isna().any() or not all(isinstance(value, (bool, np.bool_)) for value in values):
                raise ValueError(f"{path}: priced must be true or false in every feature, found {sorted(set(map(repr, values)))}")
            typed[column] = values.astype(bool)
        else:
            typed[column] = _text_series(values)
    loaded = gpd.GeoDataFrame(typed, geometry=list(frame.geometry), crs=frame.crs).to_crs(CRS)
    summary = coverage(loaded)
    log.info("[parking-garages] loaded %d garages from %s: priced %d/%d (%.1f %%), not priced %d (%s); priced rows resting "
             "on an assumption: %s; monthly product on %d", summary["listed"], path, summary["priced"], summary["listed"],
             100.0 * summary["priced"] / max(summary["listed"], 1), summary["not_priced"],
             ", ".join(f"{reason} {count}" for reason, count in summary["not_priced_by_reason"].items()) or "none",
             ", ".join(f"{name} {count}/{summary['priced']}" for name, count in summary["priced_by_assumption"].items())
             or "none", summary["with_monthly_product"])
    return loaded


#: Decimals of a longitude or latitude in the file (7 decimals are about 1 cm, as in the zone polygons).
COORDINATE_DECIMALS = 7


def _json_value(value):
    """A cell as a JSON value: NaN, NA and None as null, numpy and pandas scalars as plain Python numbers and booleans."""
    if value is None or value is pd.NA or (isinstance(value, float) and math.isnan(value)):
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if math.isnan(value) else float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    return value


def write_garages(frame: gpd.GeoDataFrame, path, members: dict | None = None) -> None:
    """Write the dataset as WGS84 GeoJSON: one feature per line, the properties in ``DATASET_COLUMNS`` order, null for an
    empty value, coordinates rounded to ``COORDINATE_DECIMALS``, ASCII only (``ensure_ascii``), LF line ends and equal
    bytes for equal content. ``members`` are foreign top-level members (RFC 7946 allows them; GeoJSON readers ignore
    them): the curation puts the licence, the attribution and the column definitions there. The frame is NOT validated;
    call :func:`validate_garages` first."""
    missing = [column for column in DATASET_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"cannot write the garage dataset: columns {missing} missing")
    if frame.crs is None:
        raise ValueError("cannot write the garage dataset: the frame has no CRS")
    wgs84 = frame.to_crs("EPSG:4326")
    features = []
    for (_, row), point in zip(wgs84.iterrows(), wgs84.geometry):
        properties = {column: _json_value(row[column]) for column in DATASET_COLUMNS}
        coordinates = [round(float(point.x), COORDINATE_DECIMALS), round(float(point.y), COORDINATE_DECIMALS)]
        features.append(json.dumps({"type": "Feature", "properties": properties,
                                    "geometry": {"type": "Point", "coordinates": coordinates}}, allow_nan=False))
    head = ['{', '"type": "FeatureCollection",']
    for key, value in (members or {}).items():
        head.append(f"{json.dumps(key)}: {json.dumps(value, allow_nan=False)},")
    text = "\n".join(head) + '\n"features": [\n' + ",\n".join(features) + "\n]\n}\n"
    Path(path).write_text(text, encoding="utf-8", newline="\n")
    log.info("[parking-garages] wrote %d garages to %s", len(features), path)


# --------------------------------------------------------------------------- validator


def _bad_cents(value: float) -> bool:
    return abs(value * 100 - round(value * 100)) > zones.MONEY_CENT_TOLERANCE


def validate_garages(frame: gpd.GeoDataFrame) -> None:
    """Check the dataset; raise ``ValueError`` listing every violation.

    Dataset: not empty, EPSG:25832, exactly the columns ``DATASET_COLUMNS``, unique lower-case ASCII ``garage_id``.
    Position: a non-empty point with finite coordinates inside ``ZGB_EXTENT_25832``. Identity and provenance: the required
    text columns set, an 8-digit AGS of the ZGB counties, ``source_url`` and ``geometry_source_url`` http(s) URLs, an ISO
    ``source_date``, a hexadecimal SHA-256 of the package, ``notes`` set. Tariff (spec Amendment A6): the garage core
    (hourly rate, billing unit, fee start, fee end) is set completely or not at all; the day cap and the first-period
    pair need the core, and the pair is set together; amounts are positive whole cents, minutes positive, the fee window
    satisfies 0 <= start < end <= 24, and the day cap is not below the first period. Status: ``priced`` is exactly "the
    core is set"; a priced row names its ``tariff_rule_ids`` and no reason, an unpriced row carries no tariff value and
    one ``not_priced_reason`` of ``NOT_PRICED_REASONS`` and no assumption; every id of ``assumptions`` is one of
    ``ASSUMPTIONS`` and is named as ``ASSUMPTION <id>`` in the notes. Monthly product: ``monthly_eur`` is a positive
    whole-cent amount with its ``monthly_source_url`` and ``monthly_product``, and neither text without the amount.
    Capacity: a positive whole number with its ``capacity_scope``.
    """
    if frame is None or len(frame) == 0:
        raise ValueError("garage dataset: no garages")
    if frame.crs is None or frame.crs.to_epsg() != 25832:
        raise ValueError(f"garage dataset must be in {CRS}, found {frame.crs}")
    missing = [column for column in DATASET_COLUMNS if column not in frame.columns]
    unexpected = [column for column in frame.columns if column not in DATASET_COLUMNS and column != "geometry"]
    if missing or unexpected:
        raise ValueError(f"garage dataset: columns differ from the documented layout: missing {missing}, "
                         f"unexpected {unexpected}")
    problems = []
    ids = frame["garage_id"].astype(str)
    duplicated = sorted(set(ids[ids.duplicated()]))
    if duplicated:
        problems.append(f"duplicate garage_id(s) {duplicated}")
    min_x, min_y, max_x, max_y = ZGB_EXTENT_25832
    for _, row in frame.iterrows():
        garage = row["garage_id"]
        prefix = f"garage {garage!r}"

        def problem(column: str, message: str) -> None:
            problems.append(f"{prefix}: {column}: {message}")

        for column in REQUIRED_TEXT_COLUMNS:
            if not zones._is_set(row[column]):
                problem(column, "required for every garage but empty")
        if zones._is_set(garage) and not _ID_PATTERN.match(str(garage)):
            problem("garage_id", "use lower-case ASCII letters, digits and '_' only")
        ags = row["municipality_ags"]
        if zones._is_set(ags) and (not zones._AGS_PATTERN.match(str(ags)) or str(ags)[:5] not in zones.ZGB_COUNTY_KEYS):
            problem("municipality_ags", f"{ags!r} is not an 8-digit AGS of the ZGB counties {list(zones.ZGB_COUNTY_KEYS)}")
        geometry = row.geometry
        if geometry is None or geometry.is_empty:
            problem("geometry", "empty geometry")
        elif geometry.geom_type != "Point":
            problem("geometry", f"{geometry.geom_type} is not a point")
        elif not (math.isfinite(geometry.x) and math.isfinite(geometry.y)):
            problem("geometry", "non-finite coordinates")
        elif not (min_x <= geometry.x <= max_x and min_y <= geometry.y <= max_y):
            problem("geometry", f"({geometry.x:.1f}, {geometry.y:.1f}) lies outside the ZGB extent {ZGB_EXTENT_25832} in "
                                f"{CRS}: a wrong CRS or swapped axes")
        for column in ("source_url", "geometry_source_url"):
            if zones._is_set(row[column]) and not _URL_PATTERN.match(str(row[column])):
                problem(column, f"{row[column]!r} is not an http(s) URL")
        if zones._is_set(row["source_date"]) and not zones._is_iso_date(row["source_date"]):
            problem("source_date", f"{row['source_date']!r} is not an ISO date YYYY-MM-DD")
        if zones._is_set(row["package_sha256"]) and not _SHA256_PATTERN.match(str(row["package_sha256"])):
            problem("package_sha256", f"{row['package_sha256']!r} is not a lower-case hexadecimal SHA-256")
        capacity = row["capacity_reported"]
        if zones._is_set(capacity):
            if capacity <= 0:
                problem("capacity_reported", f"must be a positive number of spaces, found {capacity}")
            if not zones._is_set(row["capacity_scope"]):
                problem("capacity_scope", "a reported capacity needs its scope (what the number counts)")
        # --- tariff (spec Amendment A6)
        for column in MONEY_COLUMNS:
            value = row[column]
            if zones._is_set(value):
                if value <= 0:
                    problem(column, f"must be a positive amount, found {value} (a free garage has no tariff row)")
                elif _bad_cents(value):
                    problem(column, f"{value} EUR is not a whole number of cents")
        for column in MINUTE_COLUMNS:
            if zones._is_set(row[column]) and row[column] <= 0:
                problem(column, f"must be a positive number of minutes, found {row[column]}")
        start, end = row["garage_fee_start_h"], row["garage_fee_end_h"]
        if zones._is_set(start) and zones._is_set(end) and not (0.0 <= start < end <= 24.0):
            problem("garage_fee_start_h", f"fee window {start} .. {end} h must satisfy "
                                          "0 <= garage_fee_start_h < garage_fee_end_h <= 24")
        core_set = [column for column in zones.GARAGE_CORE_COLUMNS if zones._is_set(row[column])]
        if core_set and len(core_set) < len(zones.GARAGE_CORE_COLUMNS):
            for column in zones.GARAGE_CORE_COLUMNS:
                if column not in core_set:
                    problem(column, f"the garage core {list(zones.GARAGE_CORE_COLUMNS)} is set completely or not at all; "
                                    f"this row sets {core_set}")
        has_core = len(core_set) == len(zones.GARAGE_CORE_COLUMNS)
        pair = [zones._is_set(row[column]) for column in zones.GARAGE_FIRST_PERIOD_COLUMNS]
        if pair[0] != pair[1]:
            problem(zones.GARAGE_FIRST_PERIOD_COLUMNS[1 if pair[0] else 0],
                    f"{zones.GARAGE_FIRST_PERIOD_COLUMNS[0]} and {zones.GARAGE_FIRST_PERIOD_COLUMNS[1]} are set together "
                    "or not at all")
        if not has_core and (zones._is_set(row["garage_daily_cap_eur"]) or any(pair)):
            problem("garage_daily_cap_eur" if zones._is_set(row["garage_daily_cap_eur"]) else
                    zones.GARAGE_FIRST_PERIOD_COLUMNS[0],
                    "a day cap or a first period needs the complete garage core; it is never priced without it")
        if (zones._is_set(row["garage_daily_cap_eur"]) and zones._is_set(row["garage_first_period_eur"])
                and row["garage_daily_cap_eur"] < row["garage_first_period_eur"]):
            problem("garage_daily_cap_eur", f"the day cap {row['garage_daily_cap_eur']} is below the first period "
                                            f"{row['garage_first_period_eur']}: the cap would act before the first period ends")
        # --- status
        priced = row["priced"]
        reason = row["not_priced_reason"]
        assumptions = _split(row["assumptions"])
        if bool(priced) != has_core:
            problem("priced", f"priced is {bool(priced)} but the garage core is "
                              f"{'complete' if has_core else 'not complete'}: a garage is priced exactly when its core is set")
        if priced:
            if zones._is_set(reason):
                problem("not_priced_reason", "a priced garage has no reason; leave it empty")
            if not _split(row["tariff_rule_ids"]):
                problem("tariff_rule_ids", "a priced garage names the package rules its values rest on")
        else:
            if not zones._is_set(reason):
                problem("not_priced_reason", f"an unpriced garage states its reason, one of {sorted(NOT_PRICED_REASONS)}")
            elif reason not in NOT_PRICED_REASONS:
                problem("not_priced_reason", f"{reason!r} is not one of {sorted(NOT_PRICED_REASONS)}")
            tariff_values = [column for column in TARIFF_COLUMNS if zones._is_set(row[column])]
            if tariff_values:
                problem(tariff_values[0], f"an unpriced garage carries no tariff value, found {tariff_values}")
            if assumptions:
                problem("assumptions", f"an unpriced garage rests on no assumption, found {assumptions}")
        notes = row["notes"] if zones._is_set(row["notes"]) else ""
        for assumption in assumptions:
            if assumption not in ASSUMPTIONS:
                problem("assumptions", f"{assumption!r} is not one of {sorted(ASSUMPTIONS)}")
            elif f"ASSUMPTION {assumption}" not in notes:
                problem("notes", f"the notes must name ASSUMPTION {assumption}, which the row rests on")
        if len(set(assumptions)) != len(assumptions):
            problem("assumptions", f"an assumption is listed twice: {assumptions}")
        # --- monthly product: no value without its source
        monthly = row["monthly_eur"]
        if zones._is_set(monthly):
            for column in ("monthly_source_url", "monthly_product"):
                if not zones._is_set(row[column]):
                    problem(column, "a monthly product needs its source and its description")
            if zones._is_set(row["monthly_source_url"]) and not _URL_PATTERN.match(str(row["monthly_source_url"])):
                problem("monthly_source_url", f"{row['monthly_source_url']!r} is not an http(s) URL")
        else:
            for column in ("monthly_source_url", "monthly_product"):
                if zones._is_set(row[column]):
                    problem(column, "set without a monthly_eur amount; leave it empty")
    if problems:
        raise ValueError("invalid parking garage dataset:\n  " + "\n  ".join(problems))


# --------------------------------------------------------------------------- coverage


def coverage(frame: gpd.GeoDataFrame) -> dict:
    """The counts of the dataset: garages ``listed``, ``priced``, ``not_priced`` and ``not_priced_by_reason``, per town
    (``by_municipality``: ags -> {listed, priced, not_priced}), the priced garages per assumption
    (``priced_by_assumption``, ids that no priced garage uses are absent) and the garages with a monthly product. Plain
    numbers and dicts, sorted, so a caller can print or compare them."""
    priced = frame["priced"].astype(bool)
    reasons = frame.loc[~priced, "not_priced_reason"].fillna("").astype(str)
    by_reason = {reason: int(count) for reason, count in reasons.value_counts().sort_index().items()}
    towns = {}
    for ags, group in frame.groupby("municipality_ags"):
        flags = group["priced"].astype(bool)
        towns[str(ags)] = {"listed": int(len(group)), "priced": int(flags.sum()), "not_priced": int((~flags).sum())}
    counts = {}
    for _, row in frame[priced].iterrows():
        for assumption in _split(row["assumptions"]):
            counts[assumption] = counts.get(assumption, 0) + 1
    return {"listed": int(len(frame)), "priced": int(priced.sum()), "not_priced": int((~priced).sum()),
            "not_priced_by_reason": by_reason, "by_municipality": dict(sorted(towns.items())),
            "priced_by_assumption": dict(sorted(counts.items())),
            "with_monthly_product": int(frame["monthly_eur"].notna().sum())}
