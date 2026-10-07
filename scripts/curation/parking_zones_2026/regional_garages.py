"""Garage dataset of the regional evidence package of 2026-10-07 (parking cost zones v2, spec Amendment E1, issue #436).

Not part of the pipeline and never run by synpp. Input: the owner-supplied consolidated evidence package
``Regional_Parkdaten_Belege_2026-10-07.zip`` (the one ``regional_zones.py`` reads, checked against the same pinned SHA-256
before anything is read; layers and tables are read straight from the zip, no extracted copy exists that could drift) and
the city car-park directory of Braunschweig ``bs_plan_parkplaetze.geojson`` (retrieved 2026-09-29, SHA-256 pinned in
``DIRECTORY_SHA256``). Output: ``parking_garages_2026.geojson`` (``braunschweig.parking.garages``) and the QA table
``parking_garages_2026_qa.csv`` (``braunschweig.parking.garage_qa``).

The dataset lists every garage of the package's garage layers (``bs_parkhaeuser``, ``wob_parkhaeuser``,
``region_parkhaeuser`` and the Parkhaus points of the Goslar service that have no other row) once. A garage is PRICED where
the garage columns express its published tariff exactly (rulings R-4b-3 and R-4b-4; ``regional_garage_specs`` names which
rule carries which column and the reason of every garage that is not priced), from the package rules that are marked
``preferred_for_current_use``; no tariff number is typed anywhere: the values are read from the rules by the roles. The
assumptions P3 (day family only), P4 (a rate without a stated rounding is billed per started unit) and P5 (the fee window
is 0 to 24 h where the source states no charging times) are named in the row's ``assumptions`` and notes, and counted.
Monthly and 30-day products are read per spec Amendment D2 (the cheapest publicly purchasable fixed-price product per
garage is ``monthly_eur``; products for a customer group the model cannot identify, without a fixed price or limited to a
small number of places are recorded and not used). Every candidate that is no garage of the dataset (a BgA lot, a zone car
park, a station car park, a garage without coordinates) is a row of the QA table with its reason.

Every layer is read as the package states it (EPSG:25832, valid geometries, nothing repaired); per layer the step prints
the features used, the duplicates it skipped and the features no garage specification covers (which stops the step), and
at the end the rates: priced against not priced and, among the priced, how many rest on each assumption. CRS: EPSG:25832
throughout in memory (WGS84 in the file), money in EUR, windows in decimal hours of the weekday, distances in m.

Usage (from the repository root)::

    python scripts/curation/parking_zones_2026/regional_garages.py \
        --regional-dir eqasim-data/data/braunschweig/parking/raw_sources/municipal_2026-10-07 \
        --directory eqasim-data/data/braunschweig/parking/raw_sources/bs_plan_parkplaetze.geojson \
        --municipalities <main checkout>/eqasim-data/cache_bs_bpsmoke/data.spatial.municipalities__<hash>.p \
        --out eqasim-data/data/braunschweig/parking/parking_garages_2026.geojson \
        --qa-out eqasim-data/data/braunschweig/parking/parking_garages_2026_qa.csv \
        [--tariffs eqasim-data/data/braunschweig/parking/parking_tariffs_2026.csv]
"""
from __future__ import annotations

import argparse
import html
import json
import pickle
import re
import sys
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd

import curation_common as cc
import municipal_zones as mz
import regional_garage_specs as specs
import regional_zones as rz

# The script runs from its own directory (curation_common); the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from braunschweig.parking import garage_qa as pq  # noqa: E402
from braunschweig.parking import garages as pg  # noqa: E402
from braunschweig.parking import zones as pz  # noqa: E402

FACILITIES_MEMBER = f"{rz.PACKAGE_NAME}/daten/facilities.json"
SOURCES_MEMBER = f"{rz.PACKAGE_NAME}/daten/sources.json"
#: The garage layers of the package and the column that holds the name of a feature.
GARAGE_LAYERS = {"bs_parkhaeuser": "name", "wob_parkhaeuser": "name", "region_parkhaeuser": "name",
                 "gos_parking_locations": "Bezeichnung"}
#: The layer of the Wolfsburg car parks (no tariff in the package; one QA row).
WOB_LOTS_LAYER = "wob_parkplaetze"
#: How the position of a feature was found where the layer states no method itself (``region_parkhaeuser`` does).
POSITION_METHODS = {"bs_parkhaeuser": "pulp_feed_point", "wob_parkhaeuser": "geoviewer_point",
                    "gos_parking_locations": "arcgis_service_point_2018"}
#: The city car-park directory of Braunschweig (retrieved 2026-09-29): the file and its SHA-256 (also in the data record
#: parking_tariffs_2026, where it is cited with its first eight digits).
DIRECTORY_FILE = "bs_plan_parkplaetze.geojson"
DIRECTORY_SHA256 = "861bc29046fe1ee95dead0ead198908505529cd26ba133de46335efcfb62fd43"
DIRECTORY_RETRIEVED = "2026-09-29"
DIRECTORY_URL = "https://www.braunschweig.de/geojson/parkplaetze.geojson"
#: Rules of the package that carry the rate and the day cap of a garage.
RATE_RULE_TYPES = ("increment", "published_hourly_rate")
FIRST_PERIOD_RULE_TYPES = ("duration_total", "increment")
CAP_RULE_TYPES = ("cap", "daily_cap", "daily_rate_published", "published_day_tariff")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
#: Day specifications of a time window that cover Monday to Friday (the model's average weekday, D1).
WEEKDAY_SPECIFICATIONS = (None, "Mo-Su", "Mo-Sa", "Mo-Fr", "Mo-Sa_except_public_holidays", "all_days")
#: Printed as a warning when more than this share of the priced garages rest on one of the assumptions P4 or P5.
ASSUMPTION_WARNING_SHARE = 0.75

LICENSE = (
    "Per source; the dataset holds facts (tariffs, capacities, names and positions of garages) from public sources, none of "
    "them under a verified open licence: the garage feed of the City of Braunschweig (PULP; the package records no licence); "
    "the operator pages of the garages (tariff facts; the licence of the pages is not verified); the Stadt Wolfsburg "
    "Geoviewer Themenkarte Parken (Stand 12/2024; open reuse licence not verified; used by owner decision 2026-10-01); the "
    "Stadt Goslar ArcGIS service Bewohnerparken (last edit 2018-11-22) and its service page meingoslar.de/service (open reuse "
    "licence not verified; used by owner decision 2026-10-07); and, for some positions, directory pages (parkinglist.de, "
    "parkito.ch) and OpenStreetMap features read through mapcarta.com (ODbL 1.0, (c) OpenStreetMap contributors; the terms of "
    "the directory pages are not verified)")
ATTRIBUTION = (
    "Tariffs and capacities after the garage operators and the cities of Braunschweig, Wolfsburg, Wolfenbuettel, Gifhorn, "
    "Helmstedt, Peine, Salzgitter and Goslar (source_url per feature); positions after the city feeds, the operators' map "
    "markers and directory pages (geometry_source_url per feature); (c) OpenStreetMap contributors for the positions taken "
    "from OpenStreetMap (Peine Werderstrasse, Salzgitter BRAWO Carree). Package: " + rz.PACKAGE_FILE + " (SHA-256 "
    + rz.PACKAGE_SHA256 + ").")

#: The definition of every column of the dataset (the foreign member ``documentation`` of the file and the data record).
COLUMN_GLOSSARY = {
    "garage_id": "identifier of the garage in this dataset, lower-case ASCII, unique",
    "package_facility_id": "facility_id of the garage in the package's facilities.json (BS_None is shared by two Braunschweig "
                           "features there; this dataset joins by the feature's source id)",
    "name": "name of the garage in the package layer, ASCII transliteration",
    "operator": "operator as the package states it, empty where it states none",
    "municipality": "municipality of the garage (the municipality of its zone tariff rows)",
    "municipality_ags": "8-digit AGS of the municipality",
    "capacity_reported": "spaces as the package's facility record reports them, empty where it reports none or conflicting "
                         "values; the other observations are in the notes; no capacity is a model input",
    "capacity_scope": "what capacity_reported counts, as the package states it",
    "garage_hourly_rate_eur": "EUR per hour of the garage tariff; the price of one billing unit is this rate times "
                              "garage_billing_unit_min / 60 (garage columns of the tariff table, spec Amendment A6)",
    "garage_billing_unit_min": "length of a started billing unit in minutes",
    "garage_first_period_min": "length of the first period in minutes, charged once in full when any part is used; empty "
                               "without a first period (the pair with garage_first_period_eur)",
    "garage_first_period_eur": "price of the first period in EUR",
    "garage_daily_cap_eur": "maximum charge of a stay in EUR; empty = no cap published",
    "garage_fee_start_h": "start of the fee window in decimal hours of the weekday",
    "garage_fee_end_h": "end of the fee window in decimal hours of the weekday (24 = midnight)",
    "monthly_eur": "the cheapest publicly purchasable fixed-price monthly or 30-day product in EUR (spec Amendment D2); "
                   "empty where the package holds none; independent of priced",
    "monthly_source_url": "source of monthly_eur",
    "monthly_product": "name, minimum contract and conditions of the monthly product",
    "priced": "true exactly when the garage columns express the published tariff (the garage core is set)",
    "not_priced_reason": "why a garage is listed and not priced: one of " + ", ".join(sorted(pg.NOT_PRICED_REASONS)),
    "assumptions": "';'-separated ids of the assumptions the priced row rests on: " + "; ".join(
        f"{key} = {text}" for key, text in pg.ASSUMPTIONS.items()),
    "source_url": "primary source of the tariff (the rate rule's page; for an unpriced garage the page or layer that shows the "
                  "reason)",
    "source_date": "retrieval date of source_url (ISO)",
    "tariff_rule_ids": "';'-separated package rule ids the values (or the reason) rest on",
    "geometry_method": "how the position was found, as the package states it",
    "geometry_source_url": "source of the position",
    "package_sha256": "SHA-256 of the evidence package " + rz.PACKAGE_FILE,
    "notes": "the tariff in words, every assumption by name, what the columns do not express (other tiers, ignored rules, "
             "conflicts) and the other capacity observations",
}
QA_INTRO = (
    "Curation QA of the parking garage dataset and of the monthly products (parking cost zones v2, spec Amendments D2 and "
    "E1, issue #436), written by scripts/curation/parking_zones_2026/regional_garages.py from the regional evidence package "
    "of 2026-10-07 (" + rz.PACKAGE_FILE + ", SHA-256 " + rz.PACKAGE_SHA256 + ", gitignored under raw_sources/"
    "municipal_2026-10-07/) and the car-park directory of Braunschweig (" + DIRECTORY_FILE + ", SHA-256 "
    + DIRECTORY_SHA256 + ", retrieved " + DIRECTORY_RETRIEVED + "). One row per garage of the dataset (record_type garage), per "
    "monthly or 30-day product the sources publish (monthly_product: used, or recorded and not used with a reason, ruling "
    "R-D2-a) and per car park or garage that is not in the dataset (candidate: ruling R-4b-4). "
    "braunschweig.parking.garage_qa.validate_garage_qa compares the table with parking_garages_2026.geojson and with "
    "parking_tariffs_2026.csv (commuter_day_eur is the amount of a used zone product over 21 working days, ASSUMPTION P2). "
    "Money in EUR. ASCII. Columns:")
QA_COLUMN_GLOSSARY = {
    "record_id": "unique id of the row, lower-case ASCII",
    "record_type": "garage, monthly_product or candidate",
    "municipality_ags": "8-digit AGS of the record's municipality",
    "subject": "the garage, product or car park",
    "garage_id": "garage_id in parking_garages_2026.geojson (garage rows and the monthly products of a garage); empty otherwise",
    "zone_ids": "';'-separated zone ids of parking_tariffs_2026.csv whose commuter_day_eur a used zone product sets; empty "
                "otherwise",
    "decision": "garage: priced or not_priced; monthly_product: used or not_used; candidate: not_listed",
    "reason_code": "not_priced: a code of garages.NOT_PRICED_REASONS; not_used: garage_qa.MONTHLY_NOT_USED_REASONS; not_listed: "
                   "garage_qa.CANDIDATE_REASONS; empty for priced and used rows",
    "amount_eur": "monthly_product: the amount in EUR per month (or 30 days) as published; empty for the others and for a "
                  "product without a fixed amount",
    "count": "number of records the row stands for (1, except the aggregated Wolfsburg car parks)",
    "evidence": "';'-separated package rule ids, layer references or documents the row rests on",
    "note": "what is published and why the decision follows",
}


# ---------------------------------------------------------------- helpers
def _ascii(text) -> str:
    return rz._ascii(text)


def _hours(text) -> float:
    return rz._hours(text)


def _is_set(value) -> bool:
    return pz._is_set(value) and str(value).strip() != ""


def _money(value: float) -> str:
    return f"{float(value):.2f} EUR"


WEEK_ORDER = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "public_holidays")
DAY_ABBREVIATIONS = dict(zip(WEEK_ORDER, ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su", "PH")))


def _days_text(days: list) -> str:
    """The days in order, a run of three or more consecutive week days as 'Mo-Sa', public holidays as 'PH'."""
    names = [DAY_ABBREVIATIONS[day] for day in WEEK_ORDER if day in days]
    positions = [WEEK_ORDER.index(day) for day in WEEK_ORDER if day in days and day != "public_holidays"]
    parts, start = [], 0
    while start < len(positions):
        end = start
        while end + 1 < len(positions) and positions[end + 1] == positions[end] + 1:
            end += 1
        run = [DAY_ABBREVIATIONS[WEEK_ORDER[position]] for position in positions[start:end + 1]]
        parts.append(f"{run[0]}-{run[-1]}" if len(run) >= 3 else ",".join(run))
        start = end + 1
    return ",".join(parts + (["PH"] if "PH" in names else []))


def describe_window(rule: dict) -> str:
    """The clock window a rule states with its days, as 'Mo-Sa 10:00-18:00' (several intervals joined by ' and '), or ''
    where it states none; a ``time_window`` states its own day specification (``days_raw``) where it has one."""
    times = rule.get("charging_times") or {}
    groups = {}
    for day in WEEK_ORDER:
        for entry in times.get(day) or []:
            groups.setdefault(f"{entry['start']}-{entry['end']}", []).append(day)
    parts = [f"{_days_text(days)} {interval}" for interval, days in groups.items()]
    window = rule.get("time_window") or {}
    if not parts and window.get("from") and window.get("to"):
        parts.append(f"{window['from']}-{window['to']}" + (f" ({window['days_raw']})" if window.get("days_raw") else ""))
    return " and ".join(parts)


def describe_rule(rule: dict) -> str:
    """A rule of the package in words with its id, for the notes: the amount and unit (or cap, free period, total), the
    elapsed range it covers, its cap period and its clock window. Every number is read from the rule."""
    kind = rule["rule_type"]
    amount = rule.get("amount_eur")
    unit = rule.get("billing_unit_minutes")
    started = "started " if rule.get("rounding") == "started_unit" else ""
    if kind in RATE_RULE_TYPES:
        what = f"{_money(amount)} per {started}{int(unit) if unit else '?'} min"
    elif kind == "duration_total":
        what = f"{_money(amount)} in total"
    elif kind in ("free", "free_period", "conditional_free"):
        what = "free"
    elif kind in CAP_RULE_TYPES or kind in ("night_cap", "published_duration_tariff", "published_duration_price",
                                              "published_day_total_from_duration", "daily_cap"):
        value = rule.get("daily_cap_eur") if rule.get("daily_cap_eur") is not None else amount
        what = f"{_money(value)} at most" if kind in CAP_RULE_TYPES or kind == "night_cap" else f"{_money(value)} in total"
    elif kind == "weekly_ticket":
        what = f"weekly ticket {_money(amount)}"
    elif kind == "lost_ticket_fee":
        what = f"lost ticket {_money(amount)}"
    elif amount is not None:
        what = f"{kind} {_money(amount)}"
    else:
        what = kind
    start, end = rule.get("elapsed_from_minutes"), rule.get("elapsed_to_minutes")
    span = ""
    if start is not None or end is not None:
        span = f" for elapsed {int(start or 0)}-{'' if end is None else int(end)} min"
    period = f" ({rule['cap_period']})" if rule.get("cap_period") else ""
    window = describe_window(rule)
    return f"{rule['rule_id']}: {what}{span}{period}{' ' + window if window else ''}"


# ---------------------------------------------------------------- the package
def load_garage_inputs(directory, expected_sha256: Optional[str] = None) -> dict:
    """The verified garage layers, facility records, tariff rules and sources of the owner's package in ``directory``.

    The package must exist as ``regional_zones.PACKAGE_FILE`` with exactly the pinned SHA-256 (or ``expected_sha256``, for a
    synthetic test package), else ``SystemExit``. Returns {"file": {"file", "sha256", "bytes"}, "layers" (layer ->
    GeoDataFrame in EPSG:25832), "facilities" (facility_id -> record), "rules" (rule_id -> rule), "sources" (source_id ->
    record), "wob_lots" (the GeoDataFrame of the Wolfsburg car parks), "ledger" (the per-layer accounting)}."""
    path, sha256 = rz.verify_package_file(directory, expected_sha256)
    layers = {layer: rz.read_zip_layer(path, layer) for layer in GARAGE_LAYERS}
    facilities = {}
    for record in rz.read_zip_json(path, FACILITIES_MEMBER):
        if record["facility_id"] in facilities:
            raise SystemExit(f"facilities.json holds the facility id {record['facility_id']} twice")
        facilities[record["facility_id"]] = record
    rules = {rule["rule_id"]: rule for rule in rz.read_zip_json(path, rz.RULES_MEMBER)["rules"]}
    sources = {source["source_id"]: source for source in rz.read_zip_json(path, SOURCES_MEMBER)}
    inputs = {"file": {"file": path.name, "sha256": sha256, "bytes": path.stat().st_size}, "layers": layers,
              "facilities": facilities, "rules": rules, "sources": sources,
              "wob_lots": rz.read_zip_layer(path, WOB_LOTS_LAYER), "ledger": []}
    print(f"garage inputs verified: {path.name} ({inputs['file']['bytes']} bytes, SHA-256 {sha256}); "
          f"{len(facilities)} facility records, {len(rules)} tariff rules")
    return inputs


def _feature(inputs: dict, layer: str, match: tuple) -> pd.Series:
    column, value = match
    frame = inputs["layers"][layer]
    hits = frame[frame[column].astype(str) == value]
    if len(hits) != 1:
        raise SystemExit(f"layer {layer}: {len(hits)} feature(s) with {column} = {value!r}; a garage specification needs "
                         "exactly one")
    return hits.iloc[0]


def _rule(inputs: dict, rule_id: str) -> dict:
    if rule_id not in inputs["rules"]:
        raise SystemExit(f"tariff_rules.json of the package has no rule {rule_id}")
    return inputs["rules"][rule_id]


def _facility(inputs: dict, facility_id: str) -> dict:
    if facility_id not in inputs["facilities"]:
        raise SystemExit(f"facilities.json of the package has no facility {facility_id}")
    return inputs["facilities"][facility_id]


# ---------------------------------------------------------------- reading the rules by their roles
def rule_window(rule: dict) -> Optional[tuple]:
    """The fee window (start, end in decimal hours) of the weekday Monday to Friday that the rule states, else None.

    Read from ``charging_times`` (Monday to Friday must state the one same interval that does not cross midnight) or from
    ``time_window`` (``days_raw`` one of ``WEEKDAY_SPECIFICATIONS``; an end of 00:00 is the end of the day). A window that
    crosses midnight is a night window and raises: the garage columns hold the day family. Never a guessed window."""
    times = rule.get("charging_times") or {}
    weekday = [times.get(day) for day in WEEKDAYS]
    if any(weekday):
        if not all(weekday) or any(len(entries) != 1 for entries in weekday):
            raise SystemExit(f"rule {rule['rule_id']}: Monday to Friday do not all state exactly one charging interval "
                             f"{weekday}")
        intervals = {(entries[0]["start"], entries[0]["end"], bool(entries[0].get("crosses_midnight")))
                     for entries in weekday}
        if len(intervals) != 1:
            raise SystemExit(f"rule {rule['rule_id']}: Monday to Friday state different charging intervals {weekday}")
        start, end, crosses = next(iter(intervals))
        if crosses:
            raise SystemExit(f"rule {rule['rule_id']}: the charging interval {start}-{end} crosses midnight, a night window "
                             "is no fee window of the day family")
        return _hours(start), _hours(end)
    window = rule.get("time_window") or {}
    if window.get("from") and window.get("to"):
        if window.get("days_raw") not in WEEKDAY_SPECIFICATIONS:
            raise SystemExit(f"rule {rule['rule_id']}: time window days {window.get('days_raw')!r} are none of "
                             f"{list(WEEKDAY_SPECIFICATIONS)}; the weekday window cannot be read")
        start, end = _hours(window["from"]), _hours(window["to"])
        if end == 0.0 and start > 0.0:
            end = 24.0
        if end <= start:
            raise SystemExit(f"rule {rule['rule_id']}: the window {window['from']}-{window['to']} crosses midnight, a night "
                             "window is no fee window of the day family")
        return start, end
    return None


def night_complement(rule: dict) -> tuple:
    """The day window that is the complement of the night window ``rule`` states (clock times that cross midnight):
    (end of the night, start of the night). A rule without such a window raises."""
    window = rule.get("time_window") or {}
    if not (window.get("from") and window.get("to")):
        raise SystemExit(f"rule {rule['rule_id']}: no clock window, so no complement")
    start, end = _hours(window["from"]), _hours(window["to"])
    if not start > end:
        raise SystemExit(f"rule {rule['rule_id']}: the window {window['from']}-{window['to']} does not cross midnight, so "
                         "it is no night window whose complement is the day")
    return end, start


def cap_eur(rule: dict) -> float:
    """The day cap of a cap rule: its ``daily_cap_eur`` or, for a published day tariff, its amount."""
    if rule["rule_type"] not in CAP_RULE_TYPES:
        raise SystemExit(f"rule {rule['rule_id']}: type {rule['rule_type']} is no cap rule {list(CAP_RULE_TYPES)}")
    value = rule.get("daily_cap_eur") if rule.get("daily_cap_eur") is not None else rule.get("amount_eur")
    if value is None or not float(value) > 0:
        raise SystemExit(f"rule {rule['rule_id']}: a cap needs a positive amount, found {value!r}")
    return float(value)


def _named_rule(rules: dict, rule_id: str, garage_id: str) -> dict:
    """The rule a specification names, or ``SystemExit`` naming the garage when the package holds no such rule."""
    if rule_id not in rules:
        raise SystemExit(f"garage {garage_id}: the specification names the rule {rule_id}, which the package does not hold")
    return rules[rule_id]


def encode_tariff(spec: dict, rules: dict) -> dict:
    """The garage columns of a priced specification, read from the rules by their roles, with what they rest on.

    Returns {"values" (the seven tariff columns, None where empty), "assumptions" (ids of ``garages.ASSUMPTIONS``),
    "rule_ids" (the rules the values rest on), "sentence" (the tariff in words), "rounding_stated", "window_stated"}.
    ``SystemExit`` for anything the columns would not express exactly: a rate rule that is no rate or does not start at
    0 min (without a first period that ends where it starts), a first period that does not begin at 0 min, a cap that is
    not a cap, a window that crosses midnight."""
    garage = spec["garage_id"]
    rate = _named_rule(rules, spec["rate"], garage)
    if rate["rule_type"] not in RATE_RULE_TYPES:
        raise SystemExit(f"garage {spec['garage_id']}: rule {rate['rule_id']} of type {rate['rule_type']} is no rate rule "
                         f"{list(RATE_RULE_TYPES)}")
    unit, amount = rate.get("billing_unit_minutes"), rate.get("amount_eur")
    if not unit or int(unit) != float(unit) or int(unit) <= 0 or amount is None or not float(amount) > 0:
        raise SystemExit(f"garage {spec['garage_id']}: rule {rate['rule_id']} needs a positive amount and a whole billing "
                         f"unit, found {amount!r} per {unit!r} min")
    unit = int(unit)
    rate_from = rate.get("elapsed_from_minutes") or 0
    rule_ids = [rate["rule_id"]]
    first_period_min = first_period_eur = None
    if spec.get("first"):
        first = _named_rule(rules, spec["first"], garage)
        if (first["rule_type"] not in FIRST_PERIOD_RULE_TYPES or (first.get("elapsed_from_minutes") or 0) != 0
                or not first.get("elapsed_to_minutes") or first.get("amount_eur") is None):
            raise SystemExit(f"garage {spec['garage_id']}: rule {first['rule_id']} is no first period (a total or an "
                             "increment from 0 min to a stated end)")
        if int(first["elapsed_to_minutes"]) != rate_from:
            raise SystemExit(f"garage {spec['garage_id']}: the first period ends at {first['elapsed_to_minutes']} min but "
                             f"the rate starts at {rate_from} min")
        first_period_min, first_period_eur = int(first["elapsed_to_minutes"]), float(first["amount_eur"])
        rule_ids.insert(0, first["rule_id"])
    elif spec.get("first_equals_rate"):
        first = _named_rule(rules, spec["first_equals_rate"], garage)
        if ((first.get("elapsed_from_minutes") or 0) != 0 or first.get("elapsed_to_minutes") != unit
                or first.get("amount_eur") is None or float(first["amount_eur"]) != float(amount) or rate_from != unit):
            raise SystemExit(f"garage {spec['garage_id']}: rule {first['rule_id']} is not the price of one billing unit of "
                             f"{rate['rule_id']} ({amount} EUR per {unit} min, from {unit} min): the rate alone would be a "
                             "different tariff")
        rule_ids.insert(0, first["rule_id"])
    elif rate_from != 0:
        raise SystemExit(f"garage {spec['garage_id']}: the rate {rate['rule_id']} starts at {rate_from} min and no first "
                         "period or first-unit rule covers the start")
    cap = None
    if spec.get("cap"):
        cap_rule = _named_rule(rules, spec["cap"], garage)
        cap = cap_eur(cap_rule)
        rule_ids.append(cap_rule["rule_id"])
    window_role = spec["window"]
    if window_role == "rate":
        window = rule_window(rate)
        if window is None:
            raise SystemExit(f"garage {spec['garage_id']}: the window is to come from {rate['rule_id']}, which states none; "
                             "use window=None (ASSUMPTION P5) or name the rule that states it")
    elif isinstance(window_role, tuple) and window_role[0] == "night_complement":
        window = night_complement(_named_rule(rules, window_role[1], garage))
        rule_ids.append(window_role[1])
    elif window_role is None:
        window = None
    else:
        raise SystemExit(f"garage {spec['garage_id']}: unknown window role {window_role!r}")
    assumptions = []
    if spec.get("other_tiers"):
        assumptions.append("P3")
    rounding_stated = rate.get("rounding") == "started_unit"
    if not rounding_stated:
        assumptions.append("P4")
    if window is None:
        assumptions.append("P5")
    start, end = window if window is not None else (0.0, 24.0)
    values = {"garage_hourly_rate_eur": round(float(amount) / unit * 60.0, 6), "garage_billing_unit_min": unit,
              "garage_first_period_min": first_period_min, "garage_first_period_eur": first_period_eur,
              "garage_daily_cap_eur": cap, "garage_fee_start_h": start, "garage_fee_end_h": end}
    sentence = ""
    if first_period_min is not None:
        sentence += f"{_money(first_period_eur)} for the first {first_period_min} min, then "
    sentence += f"{_money(amount)} per started {unit} min"
    if cap is not None:
        sentence += f", at most {_money(cap)} per day"
    sentence += f", charged {_clock(start)}-{_clock(end)}"
    return {"values": values, "assumptions": assumptions, "rule_ids": rule_ids, "sentence": sentence,
            "rounding_stated": rounding_stated, "window_stated": window is not None}


def _clock(hours: float) -> str:
    whole = int(hours)
    minutes = int(round((hours - whole) * 60))
    return f"{whole:02d}:{minutes:02d}"


# ---------------------------------------------------------------- one garage
def _capacity(facility: dict) -> tuple:
    """(capacity or None, scope or None, the other observations in words) of a facility record."""
    capacity = facility.get("capacity")
    capacity = int(round(float(capacity))) if capacity is not None else None
    observations = facility.get("capacity_observations") or []
    scope = facility.get("capacity_scope")
    if capacity is not None and scope is None:
        scope = next((observation["scope"] for observation in observations if observation.get("value") is not None
                      and int(round(float(observation["value"]))) == capacity), "package_registry")
    others = [f"{observation['scope']} {int(round(float(observation['value'])))}"
              + (f" ({observation['source_url']})" if observation.get("source_url") else "")
              for observation in observations if observation.get("value") is not None
              and (capacity is None or int(round(float(observation["value"]))) != capacity)]
    return capacity, scope, others


def _monthly(inputs: dict, garage_id: str) -> dict:
    """The used monthly product of a garage and the check that it is the cheapest of its publicly purchasable products."""
    products = [product for product in specs.MONTHLY_PRODUCTS if product.get("garage_id") == garage_id]
    used = [product for product in products if product["decision"] == "used"]
    if not used:
        return {}
    if len(used) != 1:
        raise SystemExit(f"garage {garage_id}: {len(used)} used monthly products; the cheapest is the one")
    rule = _rule(inputs, used[0]["rule"])
    amount = float(rule["monthly_price_eur"])
    for other in products:
        if other["decision"] == "not_used" and other.get("reason") == "not_the_cheapest":
            other_amount = float(_rule(inputs, other["rule"])["monthly_price_eur"])
            if other_amount < amount:
                raise SystemExit(f"garage {garage_id}: the used monthly product {used[0]['rule']} ({amount} EUR) is dearer "
                                 f"than {other['rule']} ({other_amount} EUR), which is marked not the cheapest")
    raw = rule.get("raw_rule") or {}
    contract = rule.get("minimum_contract_months")
    text = f"{raw.get('product_name') or rule['rule_id']}: {_money(amount)} per month"
    text += f"; minimum contract {contract} months" if contract else "; no minimum contract stated"
    if rule.get("conditions"):
        text += f"; {rule['conditions']}"
    return {"monthly_eur": amount, "monthly_source_url": rule["source_url"], "monthly_product": _ascii(text),
            "rule_id": rule["rule_id"]}


def _source_date(rule: Optional[dict], feature: pd.Series, inputs: dict) -> str:
    """Retrieval date of the primary source: the rule's, else the feature's, else the retrieval of its geometry source."""
    if rule is not None and rule.get("retrieved_at"):
        return str(rule["retrieved_at"])[:10]
    for column in ("retrieved_on", "retrieved_at_utc"):
        if column in feature.index and _is_set(feature[column]):
            return str(feature[column])[:10]
    source = inputs["sources"].get(str(feature.get("geometry_source_id")))
    if source and source.get("retrieval_dates"):
        return max(source["retrieval_dates"])
    raise SystemExit(f"garage {feature.get('facility_id')}: no retrieval date of its primary source")


def build_garage(inputs: dict, spec: dict) -> dict:
    """The dataset row (properties and ``geometry``, EPSG:25832) and the QA facts of one garage specification."""
    layer, match = spec["layer"], spec["feature"]
    feature = _feature(inputs, layer, match)
    attributes = _feature(inputs, *spec["attributes"]) if spec.get("attributes") else None
    facility = _facility(inputs, spec["facility"])
    rules = inputs["rules"]
    ags, municipality = {**specs.TOWNS, **specs.OTHER_TOWNS}[spec["town"]]
    members = set(facility.get("tariff_rule_ids") or [])
    priced = "reason" not in spec
    if priced:
        encoded = encode_tariff(spec, rules)
        evidence = encoded["rule_ids"] + list(spec.get("other_tiers") or ()) + list(spec.get("ignored") or {})
    else:
        if spec["reason"] not in pg.NOT_PRICED_REASONS:
            raise SystemExit(f"garage {spec['garage_id']}: unknown reason {spec['reason']!r}")
        encoded = None
        evidence = list(spec["evidence"])
    missing = [rule_id for rule_id in evidence if rule_id not in members]
    if missing:
        raise SystemExit(f"garage {spec['garage_id']}: the rules {missing} are not in the tariff rules of the package "
                         f"facility {spec['facility']}")
    # the primary source of the tariff: the page of the rate rule, for a garage that is not priced the first rule that shows
    # the reason (a garage without any rule has the page of its position as its source)
    primary = rules[spec["rate"]] if priced else (rules[evidence[0]] if evidence else None)
    capacity, scope, other_capacity = _capacity(facility)
    name = _ascii(str(feature[GARAGE_LAYERS[layer]]))
    operator = None
    if "operator" in spec:  # an explicit statement of the specification (None: the package's text is no operator)
        operator = spec["operator"]
    else:
        for source in (feature, attributes):
            if source is not None and "operator" in source.index and _is_set(source["operator"]):
                operator = _ascii(str(source["operator"]).strip())
                break
        if operator is None and _is_set((facility.get("attributes") or {}).get("operator")):
            operator = _ascii(str(facility["attributes"]["operator"]).strip())
    position = attributes if attributes is not None and "geometry_method" in attributes.index else feature
    method = str(position["geometry_method"]) if "geometry_method" in position.index and _is_set(
        position["geometry_method"]) else POSITION_METHODS[layer]
    geometry_url = str(position["geometry_source_url"])
    point = feature.geometry
    # ---- notes
    notes = [f"Package facility {spec['facility']}, feature {layer}:{match[1]}; position: {method}."]
    if priced:
        notes.append(f"Priced from the package rules {', '.join(encoded['rule_ids'])} (preferred for current use): "
                     f"{encoded['sentence']}.")
        if "P3" in encoded["assumptions"]:
            tiers = "; ".join(describe_rule(rules[rule_id]) for rule_id in spec["other_tiers"])
            notes.append(f"Day family only (ASSUMPTION P3); not charged by the model: {tiers}.")
        if "P4" in encoded["assumptions"]:
            notes.append("ASSUMPTION P4: the source states no rounding of the rate, so it is billed per started unit, as at "
                         "every garage of the dataset that states its rounding.")
        if "P5" in encoded["assumptions"]:
            hours = str((facility.get("attributes") or {}).get("opening_hours_text") or "").strip().rstrip(".")
            notes.append("ASSUMPTION P5: the source states no charging times of the tariff, so the fee window is 0-24 h"
                         + (f" (opening hours, which are no charging hours: {hours})" if hours else "") + ".")
        if spec.get("ignored"):
            notes.append("Not encoded: " + "; ".join(f"{describe_rule(rules[rule_id])} ({why})"
                                                       for rule_id, why in spec["ignored"].items()) + ".")
    else:
        shown = "; ".join(describe_rule(rules[rule_id]) for rule_id in evidence)
        notes.append(f"Not priced ({spec['reason']}): {spec['reason_text']}." + (f" Rules: {shown}." if shown else ""))
    if spec.get("comment"):
        notes.append(spec["comment"])
    if capacity is not None:
        notes.append(f"Capacity {capacity} ({scope})" + (f"; other observations: {'; '.join(other_capacity)}"
                                                           if other_capacity else "") + ".")
    elif other_capacity:
        notes.append(f"No capacity taken ({scope or 'unknown'}); observations: {'; '.join(other_capacity)}.")
    # a second position of the package for the same facility (another layer): the distance is stated, never hidden
    for other_layer, frame in inputs["layers"].items():
        if other_layer == layer or "facility_id" not in frame.columns:
            continue
        twins = frame[frame["facility_id"].astype(str) == spec["facility"]]
        for _, twin in twins.iterrows():
            distance = float(point.distance(twin.geometry))
            if distance > 1.0:
                notes.append(f"The package holds a second point of this garage in {other_layer} (coordinates differ by "
                             f"{distance:.0f} m); the point of {layer} is used.")
    row = {"garage_id": spec["garage_id"], "package_facility_id": spec["facility"], "name": name, "operator": operator,
           "municipality": municipality, "municipality_ags": ags, "capacity_reported": capacity,
           "capacity_scope": scope, "monthly_eur": None, "monthly_source_url": None, "monthly_product": None,
           "priced": priced, "not_priced_reason": None if priced else spec["reason"],
           "assumptions": ";".join(encoded["assumptions"]) if priced and encoded["assumptions"] else None,
           "source_url": str(primary["source_url"]) if primary is not None else geometry_url,
           "source_date": _source_date(primary, feature, inputs),
           "tariff_rule_ids": ";".join(encoded["rule_ids"] if priced else evidence) or None,
           "geometry_method": method, "geometry_source_url": geometry_url,
           "package_sha256": inputs["file"]["sha256"], "geometry": point}
    for column in pg.TARIFF_COLUMNS:
        row[column] = encoded["values"][column] if priced else None
    monthly = _monthly(inputs, spec["garage_id"])
    row.update({key: monthly.get(key) for key in ("monthly_eur", "monthly_source_url", "monthly_product")})
    if monthly:
        notes.append(f"Monthly product {monthly['rule_id']}: {monthly['monthly_product']}.")
    row["notes"] = _ascii(" ".join(notes))
    return row


def build_garages(inputs: dict) -> gpd.GeoDataFrame:
    """The dataset: one row per garage specification, in specification order, EPSG:25832. Stops (``SystemExit``) when a
    garage of the package's layers is covered by no specification, when two specifications use one feature or when the
    result violates ``garages.validate_garages``. Prints the accounting per layer and the rates."""
    used = {layer: [] for layer in GARAGE_LAYERS}
    rows = []
    for spec in specs.GARAGE_SPECS:
        rows.append(build_garage(inputs, spec))
        used[spec["layer"]].append(spec["feature"])
        if spec.get("attributes"):
            used[spec["attributes"][0]].append(spec["attributes"][1])
    ids = [spec["garage_id"] for spec in specs.GARAGE_SPECS]
    if len(set(ids)) != len(ids):
        raise SystemExit("two garage specifications share a garage_id")
    # Every garage of the package's layers must be known: specified explicitly or a twin (the same facility id in another
    # layer) of a specified garage. A garage nobody specified stops the step instead of being left out silently.
    specified = {str(spec["facility"]) for spec in specs.GARAGE_SPECS}
    for layer, layer_frame in inputs["layers"].items():
        garage_features = layer_frame[layer_frame["Nutzung"] == "Parkhaus"] if layer == "gos_parking_locations" else layer_frame
        keys = {(column, str(value)) for column, value in used[layer]}
        if len(keys) != len(used[layer]):
            raise SystemExit(f"layer {layer}: two garage specifications use the same feature {sorted(used[layer])}")
        explicit = [index for index, row in garage_features.iterrows()
                    if any(column in layer_frame.columns and str(row[column]) == value for column, value in keys)]
        twins = [index for index in garage_features.index if index not in explicit
                 and str(garage_features.loc[index, "facility_id"]) in specified]
        unknown = [str(garage_features.loc[index, "facility_id"]) for index in garage_features.index
                   if index not in explicit and index not in twins]
        if unknown:
            raise SystemExit(f"layer {layer}: garage feature(s) {unknown} are covered by no garage specification; add them "
                             "to regional_garage_specs.GARAGE_SPECS or give the reason in the candidate tables")
        inputs["ledger"].append({"layer": layer, "features": len(layer_frame), "garages": len(garage_features),
                                 "used": len(explicit), "twins": len(twins)})
        print(f"[garages] {layer}: {len(layer_frame)} features, {len(garage_features)} garages, {len(explicit)} used, {len(twins)} "
              "twin(s) of a garage that another layer gives (same facility id, skipped), 0 repaired (an invalid geometry "
              "stops the step)")
    frame = gpd.GeoDataFrame(pd.DataFrame(rows).drop(columns=["geometry"]).reindex(columns=list(pg.DATASET_COLUMNS)),
                             geometry=[row["geometry"] for row in rows], crs=cc.METRIC_CRS)
    for column in pg.MINUTE_COLUMNS + pg.INTEGER_COLUMNS:
        frame[column] = frame[column].astype("Int64")
    for column in pg.MONEY_COLUMNS + pg.HOUR_COLUMNS:
        frame[column] = frame[column].astype(float)
    frame["priced"] = frame["priced"].astype(bool)
    try:
        pg.validate_garages(frame)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    _print_rates(frame)
    return frame


def _print_rates(frame: gpd.GeoDataFrame) -> None:
    """The rates of the dataset: priced against not priced, and among the priced the primary method (the source states the
    rounding and the charging times) against the assumptions P3 to P5; a high share of an assumption is a warning."""
    summary = pg.coverage(frame)
    priced = summary["priced"]
    print(f"[garages] {summary['listed']} garages: priced {priced} ({100.0 * priced / summary['listed']:.1f} %), not priced "
          f"{summary['not_priced']} (" + ", ".join(f"{reason} {count}" for reason, count in
                                                   summary["not_priced_by_reason"].items()) + ")")
    counts = summary["priced_by_assumption"]
    for assumption, text in pg.ASSUMPTIONS.items():
        count = counts.get(assumption, 0)
        share = count / priced if priced else 0.0
        warning = "; WARNING: most priced garages rest on it" if share > ASSUMPTION_WARNING_SHARE else ""
        print(f"[garages] priced garages resting on ASSUMPTION {assumption}: {count}/{priced} ({100.0 * share:.1f} %){warning} "
              f"- {text}")
    stated = int((frame["priced"] & ~frame["assumptions"].fillna("").str.contains("P4")).sum())
    print(f"[garages] rounding stated by the source {stated}/{priced}, ASSUMPTION P4 {counts.get('P4', 0)}/{priced}; "
          f"charging times stated {priced - counts.get('P5', 0)}/{priced}, ASSUMPTION P5 {counts.get('P5', 0)}/{priced}; "
          f"monthly product on {summary['with_monthly_product']} garages")


def check_positions(frame: gpd.GeoDataFrame, municipalities: gpd.GeoDataFrame) -> None:
    """Every garage lies inside the polygon of its own municipality (EPSG:25832 polygons indexed by AGS), else
    ``SystemExit`` listing the garages and their distance; also stops for two garages less than 1 m apart (a duplicated
    garage). Prints the rate."""
    outside, inside = [], 0
    for _, row in frame.iterrows():
        polygon = municipalities.loc[row["municipality_ags"], "geometry"]
        if polygon.contains(row.geometry):
            inside += 1
        else:
            outside.append(f"{row['garage_id']} ({row['municipality_ags']}, {polygon.distance(row.geometry):.0f} m outside)")
    if outside:
        raise SystemExit("garages outside their municipality: " + "; ".join(outside))
    pairs = []
    for position, (garage, point) in enumerate(zip(frame["garage_id"], frame.geometry)):
        for other, other_point in zip(frame["garage_id"].iloc[position + 1:], frame.geometry.iloc[position + 1:]):
            if point.distance(other_point) < 1.0:
                pairs.append(f"{garage}/{other}")
    if pairs:
        raise SystemExit(f"garages less than 1 m apart (a duplicated garage?): {pairs}")
    print(f"[garages] position check: {inside}/{len(frame)} garages inside their own municipality, no two garages within 1 m")


# ---------------------------------------------------------------- QA rows
def _directory_text(description: str) -> str:
    text = re.sub(r"<[^>]+>", " ", description)
    text = re.sub(r"\s+", " ", html.unescape(text).replace("€", "EUR")).strip()
    marker = "Tarife:" if "Tarife:" in text else "Weitere Informationen"
    return _ascii(text.split(marker, 1)[1].strip() if marker in text else text)[:240]


def load_directory(path) -> list:
    """The entries of the city car-park directory of Braunschweig: [{"name" (ASCII), "id", "text" (the tariff text)}].

    The file must have exactly ``DIRECTORY_SHA256``, else ``SystemExit``; an entry without a decision in
    ``regional_garage_specs.DIRECTORY_DECISIONS`` stops the step (a changed directory is never read silently)."""
    path = Path(path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: the car-park directory of Braunschweig ({DIRECTORY_FILE}, retrieved "
                         f"{DIRECTORY_RETRIEVED})")
    actual = mz.file_sha256(path)
    if actual != DIRECTORY_SHA256:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {DIRECTORY_SHA256}; a changed directory is never read")
    entries = []
    for feature in json.loads(path.read_text(encoding="utf-8"))["features"]:
        properties = feature["properties"]
        entries.append({"name": _ascii(properties["name"]), "id": str(properties["id"]),
                        "text": _directory_text(properties["description"])})
    undecided = sorted(entry["name"] for entry in entries if entry["name"] not in specs.DIRECTORY_DECISIONS)
    gone = sorted(set(specs.DIRECTORY_DECISIONS) - {entry["name"] for entry in entries})
    if undecided or gone:
        raise SystemExit(f"the car-park directory differs from the decisions: entries without a decision {undecided}, "
                         f"decisions without an entry {gone}")
    return entries


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", _ascii(text).lower()).strip("_")


def qa_rows(inputs: dict, frame: gpd.GeoDataFrame, directory: list) -> list:
    """The rows of the QA table (``garage_qa.GARAGE_QA_COLUMNS``): one per garage, per monthly product and per candidate."""
    rules = inputs["rules"]
    rows = []

    def row(**fields):
        record = {column: "" for column in pq.GARAGE_QA_COLUMNS}
        record.update({key: ("" if value is None else str(value)) for key, value in fields.items()})
        rows.append(record)

    specs_by_id = {spec["garage_id"]: spec for spec in specs.GARAGE_SPECS}
    for _, garage in frame.iterrows():
        spec = specs_by_id[garage["garage_id"]]
        priced = bool(garage["priced"])
        if priced:
            note = encode_tariff(spec, rules)["sentence"] + (f" ({garage['assumptions'].replace(';', ', ')})"
                                                              if garage["assumptions"] else "")
        else:
            note = spec["reason_text"]
        row(record_id=f"garage_{garage['garage_id']}", record_type="garage", municipality_ags=garage["municipality_ags"],
            subject=garage["name"], garage_id=garage["garage_id"], decision="priced" if priced else "not_priced",
            reason_code="" if priced else garage["not_priced_reason"], count=1,
            evidence=garage["tariff_rule_ids"] or garage["package_facility_id"], note=_ascii(note))
    garage_ags = dict(zip(frame["garage_id"], frame["municipality_ags"]))
    for product in specs.MONTHLY_PRODUCTS:
        rule = rules[product["rule"]] if product.get("rule") else None
        amount = product.get("amount_eur")
        if rule is not None and amount is None:
            amount = rule.get("monthly_price_eur")
        note = product.get("note")
        if note is None and rule is not None:
            raw = rule.get("raw_rule") or {}
            note = f"{raw.get('product_name') or rule['rule_id']}"
            if rule.get("minimum_contract_months"):
                note += f"; minimum contract {rule['minimum_contract_months']} months"
            if rule.get("conditions"):
                note += f"; {rule['conditions']}"
            elif raw.get("conditions"):
                note += f"; {raw['conditions']}"
            if amount is None:
                note += ("; no fixed monthly amount: " + (f"minimum {raw['minimum_monthly_eur']} EUR, maximum "
                                                          f"{raw['monthly_maximum_eur']} EUR per month with a discount of "
                                                          f"{raw['discount_percent']} % on the day tariff"
                                                          if raw.get("minimum_monthly_eur") else "see the rule"))
        garage_id = product.get("garage_id")
        raw = (rule or {}).get("raw_rule") or {}
        subject = product.get("subject") or (f"{frame.set_index('garage_id').loc[garage_id, 'name']}: "
                                             f"{raw.get('product_name') or rule['rule_id']}")
        evidence = list(product.get("evidence") or ()) or ([rule["rule_id"]] if rule is not None else [])
        row(record_id=product["record_id"], record_type="monthly_product",
            municipality_ags=product.get("municipality_ags") or garage_ags[garage_id], subject=subject,
            garage_id=garage_id, zone_ids=";".join(product.get("zone_ids") or ()), decision=product["decision"],
            reason_code=product.get("reason", ""), amount_eur=None if amount is None else f"{float(amount):.2f}", count=1,
            evidence=";".join(evidence), note=_ascii(note))
    for entry in directory:
        reason, why = specs.DIRECTORY_DECISIONS[entry["name"]]
        row(record_id=f"candidate_bs_directory_{_slug(entry['name'])}", record_type="candidate", municipality_ags="03101000",
            subject=entry["name"], decision="not_listed", reason_code=reason, count=1,
            evidence=f"{DIRECTORY_FILE} id {entry['id']}",
            note=_ascii(f"{why}; directory text: {entry['text']}"))
    for record_id, town, subject, facility_id, reason, why in specs.PACKAGE_CANDIDATES:
        ags = (specs.TOWNS.get(town) or specs.OTHER_TOWNS[town])[0]
        facility = _facility(inputs, facility_id)
        tariff = "; ".join(describe_rule(rules[rule_id]) for rule_id in facility.get("tariff_rule_ids") or [])
        row(record_id=record_id, record_type="candidate", municipality_ags=ags, subject=subject, decision="not_listed",
            reason_code=reason, count=1, evidence=f"facilities.json {facility_id}",
            note=_ascii(why + (f"; package rules: {tariff}" if tariff else "")))
    lots = inputs["wob_lots"]
    tariffs = [rule_ids for rule_ids in lots["tariff_rule_ids"] if rule_ids and str(rule_ids) not in ("[]", "None")]
    if tariffs:
        raise SystemExit(f"layer {WOB_LOTS_LAYER}: {len(tariffs)} car park(s) carry tariff rules; the dataset takes large "
                         "public car parks with a published tariff, so the step needs a decision for them")
    row(record_id="candidate_wob_car_parks", record_type="candidate", municipality_ags="03103000",
        subject=f"{len(lots)} Wolfsburg car parks of the Geoviewer layer {WOB_LOTS_LAYER}", decision="not_listed",
        reason_code="no_published_tariff", count=len(lots), evidence=f"layer {WOB_LOTS_LAYER}",
        note=_ascii("the layer lists car parks (among them the Autostadt, the Volkswagen Arena, the hospital and the "
                    "cinema car parks) without any tariff rule or capacity in the package"))
    return rows


def write_garage_qa(path, rows: list) -> None:
    """The QA table with its header: the intro and one '# <column>: <definition>' line per column; ASCII, LF."""
    missing = [column for column in pq.GARAGE_QA_COLUMNS if column not in QA_COLUMN_GLOSSARY]
    if missing or len(QA_COLUMN_GLOSSARY) != len(pq.GARAGE_QA_COLUMNS):
        raise SystemExit(f"QA_COLUMN_GLOSSARY and GARAGE_QA_COLUMNS differ: missing {missing}")
    intro = _wrap(QA_INTRO)
    header = [f"# {line}" for line in intro] + [f"# {column}: {QA_COLUMN_GLOSSARY[column]}" for column in pq.GARAGE_QA_COLUMNS]
    table = pd.DataFrame(rows, columns=list(pq.GARAGE_QA_COLUMNS))
    text = cc.ascii_transliteration("\n".join(header) + "\n" + table.to_csv(index=False, lineterminator="\n"))
    Path(path).write_text(text, encoding="utf-8", newline="\n")
    print(f"written {path} with {len(table)} rows ({(table['record_type'] == 'garage').sum()} garages, "
          f"{(table['record_type'] == 'monthly_product').sum()} monthly products, "
          f"{(table['record_type'] == 'candidate').sum()} candidates)")


def _wrap(text: str, width: int = 118) -> list:
    import textwrap

    return textwrap.wrap(text, width=width)


def dataset_members() -> dict:
    """The foreign members of the dataset file: licence, attribution and the definition of every column."""
    missing = [column for column in pg.DATASET_COLUMNS if column not in COLUMN_GLOSSARY]
    if missing or len(COLUMN_GLOSSARY) != len(pg.DATASET_COLUMNS):
        raise SystemExit(f"COLUMN_GLOSSARY and DATASET_COLUMNS differ: missing {missing}")
    return {"license": LICENSE, "attribution": ATTRIBUTION,
            "documentation": {"dataset": "parking_garages_2026: one point per garage with its own tariff (parking cost zones "
                                         "v2, spec Amendment E1, issue #436); written by scripts/curation/parking_zones_2026/"
                                         "regional_garages.py; points in WGS84, loaded to EPSG:25832 by "
                                         "braunschweig.parking.garages.load_garages; money in EUR, minutes whole numbers, "
                                         "fee window in decimal hours of the weekday; null = not stated",
                              "columns": {column: COLUMN_GLOSSARY[column] for column in pg.DATASET_COLUMNS}}}


# ---------------------------------------------------------------- command line
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--regional-dir", required=True, help="folder with the package zip (raw_sources/municipal_2026-10-07)")
    parser.add_argument("--directory", required=True, help="the Braunschweig car-park directory bs_plan_parkplaetze.geojson")
    parser.add_argument("--municipalities", required=True,
                        help="pickle of data.spatial.municipalities (EPSG:25832 containment check)")
    parser.add_argument("--out", required=True, help="the dataset parking_garages_2026.geojson to write")
    parser.add_argument("--qa-out", required=True, help="the QA table parking_garages_2026_qa.csv to write")
    parser.add_argument("--tariffs", help="parking_tariffs_2026.csv: cross-check commuter_day_eur with the used zone products")
    args = parser.parse_args(argv)
    inputs = load_garage_inputs(args.regional_dir)
    frame = build_garages(inputs)
    with open(args.municipalities, "rb") as stream:
        municipalities = pickle.load(stream)
    ars = municipalities["commune_id"].astype(str)
    municipalities["ags"] = ars.str[:5] + ars.str[-3:]
    check_positions(frame, municipalities.set_index("ags").to_crs(cc.METRIC_CRS))
    rows = qa_rows(inputs, frame, load_directory(args.directory))
    pg.write_garages(frame, args.out, members=dataset_members())
    write_garage_qa(args.qa_out, rows)
    # the written files are the release: read them back through the loaders of the pipeline side and validate
    loaded = pg.load_garages(args.out)
    pg.validate_garages(loaded)
    tariffs = pz.load_tariffs(args.tariffs) if args.tariffs else None
    try:
        pq.validate_garage_qa(pq.load_garage_qa(args.qa_out), loaded, tariffs)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    coverage = pq.qa_coverage(pq.load_garage_qa(args.qa_out))
    print(f"[garages] monthly products: used {coverage['monthly_used']}, recorded and not used "
          f"{coverage['monthly_not_used']} ({coverage['monthly_not_used_by_reason']}); candidates not listed "
          f"{coverage['candidates']} ({coverage['candidates_by_reason']})")
    print("[garages] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
