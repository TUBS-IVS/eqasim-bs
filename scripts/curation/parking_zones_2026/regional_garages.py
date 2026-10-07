"""Garage dataset of the regional evidence package of 2026-10-07 (parking cost zones v2, spec Amendment E1, issue #436).

Not part of the pipeline and never run by synpp. Input: the owner-supplied consolidated evidence package
``Regional_Parkdaten_Belege_2026-10-07.zip`` (the one ``regional_zones.py`` reads, checked against the same pinned SHA-256
before anything is read; layers and tables are read straight from the zip, no extracted copy exists that could drift) and
the city car-park directory of Braunschweig ``bs_plan_parkplaetze.geojson`` (retrieved 2026-09-29, SHA-256 pinned in
``DIRECTORY_SHA256``). Output: ``parking_garages_2026.geojson`` (``braunschweig.parking.garages``) and the QA table
``parking_garages_2026_qa.csv`` (``braunschweig.parking.garage_qa``).

The dataset lists every garage of the package's garage layers (``bs_parkhaeuser``, ``wob_parkhaeuser``,
``region_parkhaeuser`` and the Parkhaus points of the Goslar service that have no other row) once, except the garages that
are no garage option (the station car parks of DB BahnPark, ruling R-4b-9), which are rows of the QA table with their
reason. A garage is PRICED where the garage columns, its time-of-day tiers or its duration bands express its published
tariff exactly (rulings R-4b-3, R-4b-4, R-4b-10b and R-4b-11; ``regional_garage_specs`` names which rule carries which part
and the reason of every garage that is not priced). Only rules that the package marks ``preferred_for_current_use`` set a
value (a rate, a tier, a band, a first period, a cap, a monthly product): a specification that names another rule for such a
role stops the step, with no waiver (ruling R-4b-8). No tariff number is typed anywhere: the values are read from the rules
by the roles, with one exception: a window that a source states only in a page text which the package keeps as text
(``window=("stated", ...)``) is typed with its quotation. A first period carries the clock window its rule states (ruling
R-4b-12). The assumptions P3 (a night tariff that is no per-unit rate is not charged), P4 (a rate without a stated rounding
is billed per started unit), P5 (the fee window is 0 to 24 h where no preferred rule states charging times), P6 (how a stay
is priced from tiers, amended by ruling R-4b-12 for the clock window of a first period), P7 (caps that the day-cap column
cannot hold are not applied) and P8 (how a stay is priced from duration bands, ruling R-4b-11) are named in the row's
``assumptions`` and notes and counted. Two readings that name no assumption are counted and named in the notes as well: a day
cap without a stated day boundary is read as a maximum per stay, and a time window without stated days as Monday to
Friday.
Monthly and 30-day products are read per spec Amendment D2 (the cheapest publicly purchasable fixed-price product per
garage is ``monthly_eur``; products for a customer group the model cannot identify, without a fixed price or limited to a
small number of places are recorded and not used). Every candidate that is no garage of the dataset (a BgA lot, a zone car
park, a station car park, a garage without coordinates) is a row of the QA table with its reason.

Every layer is read as the package states it (EPSG:25832, valid geometries, nothing repaired); per layer the step prints
the features used, the duplicates it skipped and the features no garage specification covers (which stops the step), and
at the end the rates: priced against not priced and, among the priced, how many rest on each assumption, on at least one
assumption and on P4 or P5 (a warning above ``garages.UNION_WARNING_SHARE``). CRS: EPSG:25832
throughout in memory (WGS84 in the file), money in EUR, windows in decimal hours of the weekday, distances in m.

Two further owner-supplied packages are optional inputs (``garage_supplement.py``; the garage specifications state which rows rest
on them and the step stops where one is needed and missing): the supplement ``Parkhaus_Ergaenzungen_2026-10-07.zip``
(``--supplement-zip``, spec E12: four main points for the garages without coordinates, six tariff checks) and the follow-up
``Parkhaus_Nachrecherche_2026-10-07.zip`` (``--followup-zip``, spec E13: directory observations for the garages without a
published tariff). Each is checked against its pinned SHA-256 and every member read against its own ``manifest.sha256``; their
partial rules (the packages mark every rule full_cost_calculation_ready=false) are released only by the owner decisions of
``regional_garage_specs`` and only while the package's field decision has the status the decision relied on. A row that a
package touches cites its SHA-256 as well in ``package_sha256`` (regional, supplement, follow-up, ';'-separated). A free period at
the start of a stay is read as a grace period (ASSUMPTION P10, spec E12); a garage without a published operator tariff is priced
from its best secondary evidence (ASSUMPTION P11, spec E13); both name their basis in the notes.

Usage (from the repository root)::

    python scripts/curation/parking_zones_2026/regional_garages.py \
        --regional-dir eqasim-data/data/braunschweig/parking/raw_sources/municipal_2026-10-07 \
        --supplement-zip <the regional-dir above>/Parkhaus_Ergaenzungen_2026-10-07.zip \
        --followup-zip <the regional-dir above>/Parkhaus_Nachrecherche_2026-10-07.zip \
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
from urllib.parse import urlparse

import geopandas as gpd
import pandas as pd

import curation_common as cc
import garage_supplement as sup
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
RATE_RULE_TYPES = ("increment", "published_hourly_rate", "published_tariff")
#: The rules that state a free period at the start of a stay (the free band of a grace period, ASSUMPTION P10).
FREE_RULE_TYPES = ("free", "free_period")
FIRST_PERIOD_RULE_TYPES = ("duration_total", "increment")
CAP_RULE_TYPES = ("cap", "daily_cap", "daily_rate_published", "published_day_tariff")
#: A total for the whole day that the package states as a duration price (``elapsed_to_minutes`` 1440): a published 24-hour
#: price is the day cap of a banded garage (ruling R-4b-11).
DAY_TOTAL_RULE_TYPES = ("duration_total", "published_duration_tariff", "published_duration_price")
MINUTES_PER_DAY = pg.MINUTES_PER_DAY
#: The rule types of a duration band and the band kind each one is (ruling R-4b-11): a free stretch, a total for the stay while
#: the duration is in the band, an increment per started unit counted from the band's start.
BAND_RULE_KINDS = {"free": "free", "duration_total": "total", "published_day_total_from_duration": "total",
                   "increment": "increment"}
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday")
#: Day specifications of a time window that cover Monday to Friday (the model's average weekday, D1).
WEEKDAY_SPECIFICATIONS = (None, "Mo-Su", "Mo-Sa", "Mo-Fr", "Mo-Sa_except_public_holidays", "all_days")
#: Printed as a warning when more than this share (0 to 1) of the priced garages rest on one assumption of
#: ``garages.ASSUMPTIONS``; the union rates have their own named share, ``garages.UNION_WARNING_SHARE``.
ASSUMPTION_WARNING_SHARE = 0.75
#: A cap period of the package that contains this word states no day boundary (``cap_boundary_unspecified``).
UNSPECIFIED_CAP_PERIOD_MARKER = "unspecified"
#: Cap periods that state a calendar day: the dataset holds one maximum per stay, so such a cap is a reading of its own
#: (``cap_reading``): a stay inside one calendar day pays what is published, a stay across midnight is capped once.
CALENDAR_DAY_CAP_PERIODS = ("calendar_day", "calendar_day_00_24")
#: The caution of the package's README on rounding, quoted in the notes of the rows that rest on ASSUMPTION P4 (ASCII; a
#: stated hourly rate does not prove rounding up per hour).
PACKAGE_ROUNDING_CAUTION = "Ein angegebener Stundensatz beweist noch keine Aufrundung je Stunde"

LICENSE = (
    "Per source; the dataset holds facts (tariffs, capacities, names and positions of garages) from public sources, none of "
    "them under a verified open licence: the garage feed of the City of Braunschweig (PULP; the package records no licence); "
    "the operator pages of the garages (tariff facts; the licence of the pages is not verified); the Stadt Wolfsburg "
    "Geoviewer Themenkarte Parken (Stand 12/2024; open reuse licence not verified; used by owner decision 2026-10-01); the "
    "Stadt Goslar ArcGIS service Bewohnerparken (last edit 2018-11-22) and its service page meingoslar.de/service (open reuse "
    "licence not verified; used by owner decision 2026-10-07); and, for some positions, directory pages (parkinglist.de, "
    "parkito.ch) and OpenStreetMap features read through mapcarta.com (ODbL 1.0, (c) OpenStreetMap contributors; the terms of "
    "the directory pages are not verified); the supplement and follow-up packages of 2026-10-07 (their own evidence copies of "
    "operator, city and directory pages; the packages state that no general licence for the re-publication of the original "
    "files was found), whose four main points of garages are OpenStreetMap features (ODbL 1.0, (c) OpenStreetMap contributors)")
ATTRIBUTION = (
    "Tariffs and capacities after the garage operators and the cities of Braunschweig, Wolfsburg, Wolfenbuettel, Gifhorn, "
    "Helmstedt, Peine, Salzgitter and Goslar (source_url per feature); positions after the city feeds, the operators' map "
    "markers and directory pages (geometry_source_url per feature); (c) OpenStreetMap contributors for the positions taken "
    "from OpenStreetMap (Peine Werderstrasse, Salzgitter BRAWO Carree, and the four main points of the supplement package). "
    "Packages: " + rz.PACKAGE_FILE + " (SHA-256 " + rz.PACKAGE_SHA256 + "), supplement package " + sup.SUPPLEMENT_FILE
    + " (SHA-256 " + sup.SUPPLEMENT_SHA256 + ") and follow-up package " + sup.FOLLOWUP_FILE + " (SHA-256 "
    + sup.FOLLOWUP_SHA256 + ").")

#: The definition of every column of the dataset (the foreign member ``documentation`` of the file and the data record).
COLUMN_GLOSSARY = {
    "garage_id": "identifier of the garage in this dataset, lower-case ASCII, unique",
    "package_facility_id": "facility_id of the garage in the package's facilities.json (BS_None is shared by two Braunschweig "
                           "features there; this dataset joins by the feature's source id)",
    "name": "name of the garage in the package layer, ASCII transliteration",
    "operator": "operator as the package states it (feature or facility attribute) or, where the page named in source_url is "
                "the operator's own page, that operator (the notes say which); empty where no source names one",
    "municipality": "municipality of the garage (the municipality of its zone tariff rows)",
    "municipality_ags": "8-digit AGS of the municipality",
    "capacity_reported": "spaces as the package's facility record reports them, empty where it reports none or conflicting "
                         "values; the other observations are in the notes; no capacity is a model input",
    "capacity_scope": "what capacity_reported counts, as the package states it",
    "garage_hourly_rate_eur": "EUR per hour of the garage tariff; the price of one billing unit is this rate times "
                              "garage_billing_unit_min / 60 (garage columns of the tariff table, spec Amendment A6); empty "
                              "for a garage priced by tariff_tiers or tariff_duration_bands",
    "garage_billing_unit_min": "length of a started billing unit in minutes; empty for a garage priced by tariff_tiers or "
                               "tariff_duration_bands",
    "garage_first_period_min": "length of the first period in minutes, charged once in full when any part is used; empty "
                               "without a first period (the pair with garage_first_period_eur)",
    "garage_first_period_eur": "price of the first period in EUR",
    "garage_first_period_start_h": "start of the clock window the source ties the first period to, in decimal hours of the "
                                   "weekday (ruling R-4b-12); the first period is charged once when the arrival lies inside "
                                   "the window and the tiers then run from its end, an arrival outside it pays the tiers "
                                   "from the arrival (ASSUMPTION P6); empty where the source ties the first period to no "
                                   "window; set only together with a first period and the end hour",
    "garage_first_period_end_h": "end of the clock window of the first period in decimal hours (24 = midnight); see "
                                 "garage_first_period_start_h",
    "garage_daily_cap_eur": "maximum charge of a stay in EUR; empty = no cap published; of several published caps only the "
                            "day cap (or the 24-hour maximum where there is none) is held here (ASSUMPTION P7)",
    "garage_fee_start_h": "start of the fee window in decimal hours of the weekday; empty for a garage priced by tariff_tiers "
                          "(a garage priced by tariff_duration_bands has its fee window set)",
    "garage_fee_end_h": "end of the fee window in decimal hours of the weekday (24 = midnight); empty for a garage priced by "
                        "tariff_tiers (a garage priced by tariff_duration_bands has its fee window set)",
    "tariff_tiers": "the time-of-day tiers of a garage whose rate changes with the time of day (ruling R-4b-10b): "
                    "'HH:MM-HH:MM <eur>/<unit_min>' per tier, joined by '; ', each tier the EUR of one started unit of "
                    "unit_min minutes that begins in it (a tier may cross midnight); a time of day outside every tier is "
                    "free; the four single-window columns are empty and the row rests on ASSUMPTION P6; empty for a garage "
                    "priced by one fee window or by duration bands",
    "tariff_duration_bands": "the duration bands of a garage whose tariff is a published schedule over the elapsed duration d of "
                             "the stay in minutes (ruling R-4b-11): '<from_min>-<to_min> <kind>' per band, joined by '; ', each "
                             "band covering from < d <= to (the first starts at 0, the bands are contiguous, only the last may "
                             "be open-ended with an empty end), kind = 'free' (costs 0), 'total <eur>' (the stay costs this "
                             "amount, an absolute price) or '<eur>/<unit_min>' (this amount per started unit counted from the "
                             "band's start, added to the price reached at its start), e.g. '0-20 free; 20-120 total 1.00; "
                             "120-240 0.50/60; 240-420 1.50/60; 420- 5.00/60'; the rate and the billing unit are empty, there is "
                             "no first period and no tier, the fee window is set, a published cap or 24-hour price is "
                             "garage_daily_cap_eur, and the row rests on ASSUMPTION P8; empty for a garage priced by one fee "
                             "window or by tiers",
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
    "package_sha256": "SHA-256 of the evidence package " + rz.PACKAGE_FILE + "; a row that the supplement package "
                      + sup.SUPPLEMENT_FILE + " (spec E12) or the follow-up package " + sup.FOLLOWUP_FILE + " (spec E13) "
                      "touches (a position, a tariff value or a finding of it) lists that package's SHA-256 as well, in the order "
                      "regional, supplement, follow-up, joined by ';'",
    "notes": "the tariff in words, every assumption and reading by name, what the columns do not express (other tiers, "
             "caps not applied, ignored rules, conflicts), the rules that are not preferred and not used, and the other "
             "capacity observations",
}
QA_INTRO = (
    "Curation QA of the parking garage dataset and of the monthly products (parking cost zones v2, spec Amendments D2 and "
    "E1, issue #436), written by scripts/curation/parking_zones_2026/regional_garages.py from the regional evidence package "
    "of 2026-10-07 (" + rz.PACKAGE_FILE + ", SHA-256 " + rz.PACKAGE_SHA256 + ", gitignored under raw_sources/"
    "municipal_2026-10-07/), the supplement package " + sup.SUPPLEMENT_FILE + " (SHA-256 " + sup.SUPPLEMENT_SHA256 + "), the "
    "follow-up package " + sup.FOLLOWUP_FILE + " (SHA-256 " + sup.FOLLOWUP_SHA256 + ") and the car-park directory of "
    "Braunschweig (" + DIRECTORY_FILE + ", SHA-256 "
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


def opening_hours_text(text: str) -> str:
    """The opening hours as the package states them, with a space where a day name runs into a clock time (the package writes
    'Mo-Fr06:00-22:00' or 'Sundays12:00-18:30'): 'Mo-Fr 06:00-22:00'. Nothing else changes (idempotent)."""
    return re.sub(r"(?<=[A-Za-z])(?=\d{1,2}:\d{2})", " ", text)


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
    elapsed range it covers, its cap period and its clock window, and the mark ``[not preferred for current use]`` where the
    package's flag is not true (derived from the flag, never written by hand). Every number is read from the rule."""
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
    flag = " [not preferred for current use]" if rule.get("preferred_for_current_use") is not True else ""
    return f"{rule['rule_id']}: {what}{span}{period}{' ' + window if window else ''}{flag}"


# ---------------------------------------------------------------- the package
def load_garage_inputs(directory, expected_sha256: Optional[str] = None, supplement_path=None,
                        expected_supplement_sha256: Optional[str] = None, followup_path=None,
                        expected_followup_sha256: Optional[str] = None) -> dict:
    """The verified garage layers, facility records, tariff rules and sources of the owner's package in ``directory``, and, where
    given, the supplement package (``supplement_path``, spec E12) and the follow-up package (``followup_path``, spec E13) merged
    into them (``garage_supplement``: their rules, released by owner decisions, join the facilities they belong to).

    The regional package must exist as ``regional_zones.PACKAGE_FILE`` with exactly the pinned SHA-256 (or ``expected_sha256``,
    for a synthetic test package), else ``SystemExit``; so must the other two zips. Returns {"file": {"file", "sha256",
    "bytes"}, "layers" (layer -> GeoDataFrame in EPSG:25832), "facilities" (facility_id -> record), "rules" (rule_id ->
    rule), "sources" (source_id -> record), "wob_lots" (the GeoDataFrame of the Wolfsburg car parks), "ledger" (the per-layer
    accounting)}."""
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
              "wob_lots": rz.read_zip_layer(path, WOB_LOTS_LAYER), "ledger": [],
              "rounding_census": rounding_census(facilities, rules)}
    census = inputs["rounding_census"]
    print(f"garage inputs verified: {path.name} ({inputs['file']['bytes']} bytes, SHA-256 {sha256}); "
          f"{len(facilities)} facility records, {len(rules)} tariff rules; rounding census (the basis of ASSUMPTION P4): of "
          f"{census['rate_rules']} preferred garage rate rules {census['stated']} state their rounding, "
          f"{census['started_unit']} of them as started unit")
    if supplement_path is not None:
        sup.attach(inputs, sup.load_supplement(supplement_path, expected_supplement_sha256), specs.SUPPLEMENT_RELEASED,
                   specs.BROCHURE_TARIFFS)
    if followup_path is not None:
        if supplement_path is None:
            raise SystemExit("the follow-up package (--followup-zip) refines the supplement package: pass --supplement-zip too")
        sup.attach_followup(inputs, sup.load_followup(followup_path, expected_followup_sha256), specs.FOLLOWUP_RELEASED)
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
def rule_window(rule: dict, *, allow_midnight_crossing: bool = False) -> Optional[tuple]:
    """The clock window (start, end in decimal hours) of the weekday Monday to Friday that the rule states, else None.

    Read from ``charging_times`` (Monday to Friday must state the one same interval) or from ``time_window`` (``days_raw``
    one of ``WEEKDAY_SPECIFICATIONS``; an end of 00:00 is the end of the day). A window that crosses midnight is a night
    window: it raises for the single-window form (``allow_midnight_crossing=False``: the garage columns hold one window of
    one day) and is returned as it stands, with its end before its start, for a tier of the tiered form. Never a guessed
    window. Whether the rule states its days is :func:`window_days_stated`."""
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
        if crosses and not allow_midnight_crossing:
            raise SystemExit(f"rule {rule['rule_id']}: the charging interval {start}-{end} crosses midnight, a night window "
                             "is no fee window of the single-window form (use tiers)")
        return _hours(start), _hours(end)
    window = rule.get("time_window") or {}
    if window.get("from") and window.get("to"):
        if window.get("days_raw") not in WEEKDAY_SPECIFICATIONS:
            raise SystemExit(f"rule {rule['rule_id']}: time window days {window.get('days_raw')!r} are none of "
                             f"{list(WEEKDAY_SPECIFICATIONS)}; the weekday window cannot be read")
        start, end = _hours(window["from"]), _hours(window["to"])
        if end == 0.0 and start > 0.0:
            end = 24.0
        if end <= start and not allow_midnight_crossing:
            raise SystemExit(f"rule {rule['rule_id']}: the window {window['from']}-{window['to']} crosses midnight, a night "
                             "window is no fee window of the single-window form (use tiers)")
        return start, end
    return None


def window_days_stated(rule: dict) -> bool:
    """Whether the clock window of ``rule`` states the days it applies to: ``charging_times`` state them by their structure,
    a ``time_window`` by ``days_raw``. A window without stated days is READ as Monday to Friday (the weekday of the model,
    spec D1): a reading that is counted and named in the notes; the package itself never interprets missing weekdays as
    Monday to Sunday."""
    times = rule.get("charging_times") or {}
    if any(times.get(day) for day in WEEKDAYS):
        return True
    return (rule.get("time_window") or {}).get("days_raw") is not None


def cap_boundary_unspecified(rule: dict) -> bool:
    """Whether the cap period of a cap rule states no day boundary (no period, or a period the package marks unspecified): a
    cap read as a maximum per stay, a reading that is counted and named in the notes."""
    period = rule.get("cap_period")
    return period is None or UNSPECIFIED_CAP_PERIOD_MARKER in str(period)


def cap_reading(rule: dict) -> Optional[tuple]:
    """The reading a cap rule needs, or None: ``("cap", rule id, period)`` where the cap states no day boundary and
    ``("cap_calendar", rule id, period)`` where it is stated per calendar day. Both are held as a maximum per stay (the dataset
    column is one maximum per stay) and both are counted and named in the notes."""
    period = rule.get("cap_period")
    if cap_boundary_unspecified(rule):
        return ("cap", rule["rule_id"], period)
    if str(period) in CALENDAR_DAY_CAP_PERIODS:
        return ("cap_calendar", rule["rule_id"], period)
    return None


def is_day_total(rule: dict) -> bool:
    """Whether ``rule`` is a total for the whole day that the package states as a duration price: one of
    ``DAY_TOTAL_RULE_TYPES`` that starts at 0 min (or states no start) and ends at 1440 min (a published 24-hour price)."""
    return (rule["rule_type"] in DAY_TOTAL_RULE_TYPES and (rule.get("elapsed_from_minutes") or 0) == 0
            and rule.get("elapsed_to_minutes") == MINUTES_PER_DAY)


def cap_eur(rule: dict) -> float:
    """The day cap of a cap rule: its ``daily_cap_eur`` or, for a published day tariff or a 24-hour price (a duration total
    from 0 to 1440 min, ruling R-4b-11), its amount."""
    if rule["rule_type"] not in CAP_RULE_TYPES and not is_day_total(rule):
        raise SystemExit(f"rule {rule['rule_id']}: type {rule['rule_type']} is no cap rule {list(CAP_RULE_TYPES)} and no 24-hour "
                         "price (a duration total from 0 to 1440 min)")
    value = rule.get("daily_cap_eur") if rule.get("daily_cap_eur") is not None else rule.get("amount_eur")
    if value is None or not float(value) > 0:
        raise SystemExit(f"rule {rule['rule_id']}: a cap needs a positive amount, found {value!r}")
    return float(value)


def _named_rule(rules: dict, rule_id: str, garage_id: str) -> dict:
    """The rule a specification names, or ``SystemExit`` naming the garage when the package holds no such rule."""
    if rule_id not in rules:
        raise SystemExit(f"garage {garage_id}: the specification names the rule {rule_id}, which the package does not hold")
    return rules[rule_id]


def _value_rule(rules: dict, rule_id: str, garage_id: str, role: str) -> dict:
    """The rule a specification names for a role that SETS A VALUE (a rate, a tier, a first period, a cap or a monthly
    product), which must be marked ``preferred_for_current_use`` in the package: a rule that is not preferred never sets a
    value, whatever its role and with no waiver (ruling R-4b-8). The flag means preferred evidence, not a rule that is legally
    or arithmetically complete (the package's own caution); the roles of a specification take care of the rest."""
    rule = _named_rule(rules, rule_id, garage_id)
    if rule.get("preferred_for_current_use") is not True:
        raise SystemExit(f"garage {garage_id}: the rule {rule_id} is named as the {role}, but the package does not mark it "
                         f"preferred_for_current_use (flag {rule.get('preferred_for_current_use')!r}, status "
                         f"{rule.get('status')!r}): a rule that is not preferred never sets a value (ruling R-4b-8)")
    return rule


def _rate_rule_values(rate: dict, garage_id: str) -> tuple:
    """(billing unit in minutes, EUR per unit) of a rate or tier rule; ``SystemExit`` for anything else."""
    if rate["rule_type"] not in RATE_RULE_TYPES:
        raise SystemExit(f"garage {garage_id}: rule {rate['rule_id']} of type {rate['rule_type']} is no rate rule "
                         f"{list(RATE_RULE_TYPES)}")
    unit, amount = rate.get("billing_unit_minutes"), rate.get("amount_eur")
    if not unit or int(unit) != float(unit) or int(unit) <= 0 or amount is None or not float(amount) > 0:
        raise SystemExit(f"garage {garage_id}: rule {rate['rule_id']} needs a positive amount and a whole billing unit, found "
                         f"{amount!r} per {unit!r} min")
    return int(unit), float(amount)


def _band_values(rule: dict, garage: str) -> tuple:
    """(kind, EUR, unit in minutes or None, start minute, end minute or None) of a band rule; ``SystemExit`` for a rule
    that is no band, an amount that is no positive whole number of cents (a free band has none), an increment without a
    positive whole unit or a stated rounding that is no started unit (ASSUMPTION P8 reads a stated pro-rata or a rounding
    down as another tariff than the one the bands express)."""
    kind = BAND_RULE_KINDS.get(rule["rule_type"])
    if kind is None:
        raise SystemExit(f"garage {garage}: rule {rule['rule_id']} of type {rule['rule_type']} is no band rule "
                         f"{sorted(BAND_RULE_KINDS)}")
    start = int(rule.get("elapsed_from_minutes") or 0)
    end = rule.get("elapsed_to_minutes")
    end = None if end is None else int(end)
    amount = rule.get("amount_eur")
    if kind == "free":
        if amount not in (None, 0, 0.0):
            raise SystemExit(f"garage {garage}: free rule {rule['rule_id']} states the amount {amount}; a free band has no "
                             "price")
        return kind, 0.0, None, start, end
    if kind == "increment":
        unit, amount = _rate_rule_values(rule, garage)
        rounding = rule.get("rounding")
        if rounding not in (None, "unspecified", "started_unit"):
            raise SystemExit(f"garage {garage}: rule {rule['rule_id']} states the rounding {rounding!r}, which is not a "
                             "started unit; the band text expresses a started unit only (ASSUMPTION P8)")
    else:
        unit = None
        if amount is None or not float(amount) > 0:
            raise SystemExit(f"garage {garage}: rule {rule['rule_id']} needs a positive amount, found {amount!r}")
    if abs(float(amount) * 100 - round(float(amount) * 100)) > 1e-6:
        raise SystemExit(f"garage {garage}: the amount {amount} EUR of the band rule {rule['rule_id']} is not a whole number "
                         "of cents")
    return kind, round(float(amount), 2), unit, start, end


def _first_period_window(first: dict, garage: str) -> Optional[tuple]:
    """The clock window (start, end in decimal hours of the weekday) that the rule of a first period states, else None (the
    source ties the first period to no window); a window that crosses midnight is refused (ruling R-4b-12 needs a documented
    wrap rule for it, and no source has one)."""
    try:
        return rule_window(first)
    except SystemExit as error:
        raise SystemExit(f"garage {garage}: the first-period window cannot be read: {error}") from None


def encode_tariff(spec: dict, rules: dict) -> dict:
    """:func:`_encode_forms` plus ASSUMPTION P11 where the specification states the ``p11_basis`` of a value that rests on the
    best available secondary evidence (spec E13)."""
    encoded = _encode_forms(spec, rules)
    if spec.get("p11_basis"):
        encoded["assumptions"] = encoded["assumptions"] + ["P11"]
    return encoded


def _encode_forms(spec: dict, rules: dict) -> dict:
    """The tariff columns of a priced specification, read from the rules by their roles, with what they rest on.

    A specification names exactly one of ``rate`` (one rate in started units with one fee window: the single-window form),
    ``tiers`` (the rules of the time-of-day tiers: the tiered form, ruling R-4b-10b) and ``bands`` (the rules of the duration
    bands: the banded form, ruling R-4b-11), and optionally a ``first`` period (the single-window form also
    ``first_equals_rate``; a banded garage has none: its first band is the first period) and a ``cap``. Every rule that sets
    a value must be marked ``preferred_for_current_use`` (ruling R-4b-8). The single-window and the banded form take their
    window from the rate or first band rule (``window="rate"``), from a window that a source states in a page text the
    package keeps as text (``window=("stated", start, end, quotation)``) or, with ``window=None``, have none (ASSUMPTION P5);
    the tiered form takes the window of each tier rule, and a tier may cross midnight. A band rule is a ``free``,
    ``duration_total``, ``published_day_total_from_duration`` (a total) or ``increment`` rule with its own elapsed range; the
    cap of a banded garage may also be a published 24-hour price (a duration total from 0 to 1440 min). The clock window of a
    first period is the window its rule states (ruling R-4b-12).

    Returns {"values" (the nine tariff columns, ``garage_first_period_start_h``, ``garage_first_period_end_h``,
    ``tariff_tiers`` and ``tariff_duration_bands``, None where empty), "assumptions" (ids of ``garages.ASSUMPTIONS``),
    "rule_ids" (the rules the values rest on), "sentence" (the tariff in words), "rounding_stated" (every rate, tier and
    increment band states a started unit), "window_stated", "tiers" (the ``garages.TariffTier`` list, empty for the other
    forms), "window_quotation" (the quotation of a stated window or None), "readings" (the readings the notes name: ``("cap",
    rule id, cap period)`` for a cap or day total whose day boundary is not stated and ``("days", rule id)`` for a window
    without stated days)}. ``SystemExit`` for anything the columns would not express exactly: a rule that is not preferred, a
    rate rule that is no rate or starts at a minute that no first period covers, a first period that does not begin at 0 min,
    a cap that is not a cap, a window that crosses midnight in the single-window form or for a first period, tiers that
    overlap or differ in their unit, bands with a gap or an overlap."""
    garage = spec["garage_id"]
    forms = [name for name in ("rate", "tiers", "bands", "grace", "table") if spec.get(name)]
    if len(forms) != 1:
        raise SystemExit(f"garage {garage}: a priced specification names exactly one of 'rate' (the single-window form), "
                         "'tiers' (the tiered form), 'bands' (the banded form), 'grace' (a free period read as a grace period, "
                         "ASSUMPTION P10) and 'table' (a directory price table as bands, ASSUMPTION P11)")
    if forms[0] == "grace":
        return _encode_grace(spec, rules)
    if forms[0] == "table":
        return _encode_table(spec, rules)
    tiered, banded = forms[0] == "tiers", forms[0] == "bands"
    if spec.get("rest_tier") and not tiered:
        raise SystemExit(f"garage {garage}: rest_tier belongs to the tiered form")
    if tiered and spec.get("window") is not None:
        raise SystemExit(f"garage {garage}: a tiered specification has no window; the tiers carry the clock times")
    if tiered and spec.get("first_equals_rate"):
        raise SystemExit(f"garage {garage}: first_equals_rate belongs to the single-window form")
    if banded and (spec.get("first") or spec.get("first_equals_rate")):
        raise SystemExit(f"garage {garage}: a banded specification has no first period; its first band is the first period")
    role = {"rate": "rate", "tiers": "tier", "bands": "band"}[forms[0]]
    rate_rules = [_value_rule(rules, rule_id, garage, role)
                  for rule_id in (spec["tiers"] if tiered else spec["bands"] if banded else (spec["rate"],))]
    if banded:
        return _encode_bands(spec, rules, rate_rules)
    # the rate of a day tier that states no charging times: it applies at every time of day the other tiers do not cover
    rest_rule = _value_rule(rules, spec["rest_tier"], garage, "tier") if spec.get("rest_tier") else None
    if rest_rule is not None and _clock_window_kind(rest_rule):
        raise SystemExit(f"garage {garage}: the rest tier rule {rest_rule['rule_id']} states {_clock_window_kind(rest_rule)} "
                         f"({describe_window(rest_rule)}); a rest tier is a day rate without clock times that takes the rest of "
                         "the day, so a rate with a clock window belongs in 'tiers'")
    explicit_tiers = list(rate_rules)
    if rest_rule is not None:
        rate_rules = rate_rules + [rest_rule]
    values_of_rules = [_rate_rule_values(rate, garage) for rate in rate_rules]
    units = {unit for unit, _ in values_of_rules}
    if len(units) != 1:
        raise SystemExit(f"garage {garage}: the tier rules use different billing units {sorted(units)} min; the tiers of a "
                         "garage share one unit (the units of a stay are counted from its arrival, ASSUMPTION P6)")
    unit = units.pop()
    amount = values_of_rules[0][1]
    rate_starts = [int(rate.get("elapsed_from_minutes") or 0) for rate in rate_rules]
    rule_ids = [rate["rule_id"] for rate in rate_rules]
    first_period_min = first_period_eur = None
    first_window = None
    readings = []
    # a rate whose unit the source does not state and that is read as the unit of the first price is a reading of its own
    readings.extend(("unit", rate["rule_id"]) for rate in rate_rules if rate.get("unit_reading"))
    if spec.get("first"):
        first = _value_rule(rules, spec["first"], garage, "first period")
        if (first["rule_type"] not in FIRST_PERIOD_RULE_TYPES or (first.get("elapsed_from_minutes") or 0) != 0
                or not first.get("elapsed_to_minutes") or first.get("amount_eur") is None):
            raise SystemExit(f"garage {garage}: rule {first['rule_id']} is no first period (a total or an "
                             "increment from 0 min to a stated end)")
        first_end = int(first["elapsed_to_minutes"])
        if not tiered and rate_starts != [first_end]:
            raise SystemExit(f"garage {garage}: the first period ends at {first_end} min but the rate starts at "
                             f"{rate_starts[0]} min")
        if tiered and (any(start not in (0, first_end) for start in rate_starts) or first_end not in rate_starts):
            raise SystemExit(f"garage {garage}: the first period ends at {first_end} min but the tier rules start at "
                             f"{sorted(set(rate_starts))} min; one tier must follow the first period directly and none may "
                             "start elsewhere")
        first_period_min, first_period_eur = first_end, float(first["amount_eur"])
        first_window = _first_period_window(first, garage)
        if first_window is not None and not window_days_stated(first):
            readings.append(("days", first["rule_id"]))
        rule_ids.insert(0, first["rule_id"])
    elif spec.get("first_equals_rate"):
        first = _value_rule(rules, spec["first_equals_rate"], garage, "first unit")
        if ((first.get("elapsed_from_minutes") or 0) != 0 or first.get("elapsed_to_minutes") != unit
                or first.get("amount_eur") is None or float(first["amount_eur"]) != float(amount) or rate_starts != [unit]):
            raise SystemExit(f"garage {garage}: rule {first['rule_id']} is not the price of one billing unit of "
                             f"{rate_rules[0]['rule_id']} ({amount} EUR per {unit} min, from {unit} min): the rate alone "
                             "would be a different tariff")
        rule_ids.insert(0, first["rule_id"])
    elif any(start != 0 for start in rate_starts):
        raise SystemExit(f"garage {garage}: a rate rule starts at {max(rate_starts)} min and no first period or first-unit "
                         "rule covers the start")
    cap = None
    if spec.get("cap"):
        cap_rule = _value_rule(rules, spec["cap"], garage, "cap")
        cap = cap_eur(cap_rule)
        rule_ids.append(cap_rule["rule_id"])
        reading = cap_reading(cap_rule)
        if reading is not None:
            readings.append(reading)
    tiers, tiers_text, window, quotation = [], None, None, None
    if tiered:
        for rate, (_, eur) in zip(explicit_tiers, values_of_rules):
            clock = rule_window(rate, allow_midnight_crossing=True)
            if clock is None:
                raise SystemExit(f"garage {garage}: the tier rule {rate['rule_id']} states no clock window")
            if abs(eur * 100 - round(eur * 100)) > 1e-6:
                raise SystemExit(f"garage {garage}: the price {eur} EUR of the tier rule {rate['rule_id']} is not a whole "
                                 "number of cents")
            if not window_days_stated(rate):
                readings.append(("days", rate["rule_id"]))
            tiers.append(pg.TariffTier(round(clock[0] * 60), round(clock[1] * 60), unit, round(eur, 2)))
        if rest_rule is not None:
            tiers.extend(_rest_tiers(garage, tiers, rest_rule, values_of_rules[-1][1], unit, readings))
        tiers.sort(key=lambda tier: tier.start_min)
        tiers_text = pg.format_tariff_tiers(tiers)
        try:
            pg.parse_tariff_tiers(tiers_text)
        except ValueError as error:
            raise SystemExit(f"garage {garage}: the tier rules {[rate['rule_id'] for rate in rate_rules]} make no valid "
                             f"tiers: {error}") from None
    else:
        window, quotation = _single_window(spec, rate_rules[0], garage, readings)
    assumptions = []
    if spec.get("other_tiers"):
        assumptions.append("P3")
    rounding_stated = all(rate.get("rounding") == "started_unit" for rate in rate_rules)
    if not rounding_stated:
        assumptions.append("P4")
    if not tiered and window is None:
        assumptions.append("P5")
    if tiered:
        assumptions.append("P6")
    if spec.get("other_caps"):
        assumptions.append("P7")
    start, end = window if window is not None else (0.0, 24.0)
    values = {"garage_hourly_rate_eur": None if tiered else round(float(amount) / unit * 60.0, 6),
              "garage_billing_unit_min": None if tiered else unit,
              "garage_first_period_min": first_period_min, "garage_first_period_eur": first_period_eur,
              "garage_first_period_start_h": None if first_window is None else first_window[0],
              "garage_first_period_end_h": None if first_window is None else first_window[1],
              "garage_daily_cap_eur": cap, "garage_fee_start_h": None if tiered else start,
              "garage_fee_end_h": None if tiered else end, "tariff_tiers": tiers_text, "tariff_duration_bands": None}
    sentence = ""
    if first_period_min is not None:
        arrival = "" if first_window is None else f" (arrival {_clock(first_window[0])}-{_clock(first_window[1])})"
        sentence += f"{_money(first_period_eur)} for the first {first_period_min} min{arrival}, then "
    if tiered:
        sentence += (f"tiers per started {unit} min, the tier in force at the unit's start: "
                     + ", ".join(f"{_clock(tier.start_min / 60)}-{_clock(tier.end_min / 60)} {_money(tier.eur)}"
                                 for tier in tiers))
    else:
        sentence += f"{_money(amount)} per started {unit} min"
    if cap is not None:
        sentence += f", at most {_money(cap)} per day"
    if tiered:
        if pg.tier_coverage_minutes(tiers) < pg.MINUTES_PER_DAY:
            sentence += "; free outside the tiers"
    else:
        sentence += f", charged {_clock(start)}-{_clock(end)}"
    return {"values": values, "assumptions": assumptions, "rule_ids": rule_ids, "sentence": sentence,
            "rounding_stated": rounding_stated, "window_stated": tiered or window is not None, "tiers": tiers,
            "window_quotation": quotation, "readings": readings}


def _clock_window_kind(rule: dict) -> Optional[str]:
    """'charging times' or 'a time window' where the rule states a clock window, else None."""
    if any((rule.get("charging_times") or {}).get(day) for day in WEEK_ORDER):
        return "charging times"
    window = rule.get("time_window") or {}
    return "a time window" if window.get("from") or window.get("to") else None


def _rest_tiers(garage: str, tiers: list, rest_rule: dict, eur: float, unit: int, readings: list) -> list:
    """The tiers of the rate ``rest_rule`` that states no charging times: the times of day that no other tier covers. The
    pieces at both ends of the day are joined into one tier that crosses midnight. ``SystemExit`` where the other tiers cover the
    whole day or the price is no whole number of cents; adds the reading ``("rest", rule id, the times)``."""
    if abs(eur * 100 - round(eur * 100)) > 1e-6:
        raise SystemExit(f"garage {garage}: the price {eur} EUR of the rest tier {rest_rule['rule_id']} is not a whole number "
                         "of cents")
    gaps, cursor = [], 0
    for start, end in sorted(interval for tier in tiers for interval in tier.intervals()):
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < pg.MINUTES_PER_DAY:
        gaps.append((cursor, pg.MINUTES_PER_DAY))
    if not gaps:
        raise SystemExit(f"garage {garage}: the tier rules cover the whole day, so no time of day is left for the rest tier "
                         f"{rest_rule['rule_id']}")
    if len(gaps) > 1 and gaps[0][0] == 0 and gaps[-1][1] == pg.MINUTES_PER_DAY:
        gaps = gaps[1:-1] + [(gaps[-1][0], gaps[0][1])]
    readings.append(("rest", rest_rule["rule_id"], " and ".join(f"{_clock(start / 60)}-{_clock(end / 60)}" for start, end in gaps)))
    return [pg.TariffTier(start, end, unit, round(eur, 2)) for start, end in gaps]


def _single_window(spec: dict, window_rule: dict, garage: str, readings: list) -> tuple:
    """(window or None, quotation or None) of the single-window and the banded form: from ``window_rule`` (``window="rate"``),
    from a window that a source states in a page text (``window=("stated", ...)``) or none (``window=None``, ASSUMPTION P5);
    a window without stated days adds the reading ``("days", rule id)``."""
    window_role = spec["window"]
    if window_role == "rate":
        window = rule_window(window_rule)
        if window is None:
            raise SystemExit(f"garage {garage}: the window is to come from {window_rule['rule_id']}, which states none; use "
                             "window=None (ASSUMPTION P5) or name the page text that states it")
        if not window_days_stated(window_rule):
            readings.append(("days", window_rule["rule_id"]))
        return window, None
    if isinstance(window_role, tuple) and window_role[0] == "stated":
        _, start_text, end_text, quotation = window_role
        window = (_hours(start_text), _hours(end_text))
        if not quotation or not 0.0 <= window[0] < window[1] <= 24.0:
            raise SystemExit(f"garage {garage}: a stated window needs 0 <= start < end <= 24 and the quotation of the "
                             f"source that states it, found {window_role!r}")
        return window, quotation
    if window_role is None:
        return None, None
    raise SystemExit(f"garage {garage}: unknown window role {window_role!r}")


def _encode_bands(spec: dict, rules: dict, band_rules: list) -> dict:
    """:func:`encode_tariff` for the banded form (ruling R-4b-11): the bands from the band rules by their elapsed ranges, the
    day cap, the fee window, ASSUMPTIONS P4 (an increment band without a stated rounding), P5 and P8."""
    garage = spec["garage_id"]
    entries = []
    for rule in band_rules:
        kind, eur, unit, start, end = _band_values(rule, garage)
        entries.append((start, pg.DurationBand(start, end, kind, eur, unit), rule))
    entries.sort(key=lambda entry: entry[0])
    bands = [entry[1] for entry in entries]
    ordered_rules = [entry[2] for entry in entries]
    readings = []
    # a published day total from a duration whose day boundary the source does not state is read as a maximum per stay
    for band, rule in zip(bands, ordered_rules):
        if band.kind == "total" and UNSPECIFIED_CAP_PERIOD_MARKER in str(rule.get("cap_period")):
            readings.append(("cap", rule["rule_id"], rule.get("cap_period")))
    increments = [rule for band, rule in zip(bands, ordered_rules) if band.kind == "increment"]
    return _finish_bands(spec, rules, bands, ordered_rules, ordered_rules[0], increments, readings, [])


def _grace_bands(free: dict, rate: dict, garage: str) -> list:
    """The bands of a free period read as a grace period (ASSUMPTION P10, spec E12): a free band up to the end of the free
    period, the total of the first billing unit up to the end of that unit, and the rate per started unit counted from there.
    A stay not longer than the free period costs 0, a longer stay is billed from the arrival (the free minutes are not
    deducted). ``SystemExit`` for a free rule that is none, that does not start at 0 or that is not shorter than the billing
    unit (a longer free period needs more bands), for a rate that is no increment rule, a rounding that is no started unit, an
    amount that is no whole number of cents and a rate that states another free period than the free rule."""
    if free["rule_type"] not in FREE_RULE_TYPES:
        raise SystemExit(f"garage {garage}: rule {free['rule_id']} of type {free['rule_type']} is no free period "
                         f"{list(FREE_RULE_TYPES)}")
    start = int(free.get("elapsed_from_minutes") or 0)
    if start != 0:
        raise SystemExit(f"garage {garage}: the free rule {free['rule_id']} starts at {start} min, not at 0")
    if not free.get("elapsed_to_minutes") or int(free["elapsed_to_minutes"]) <= 0:
        raise SystemExit(f"garage {garage}: the free rule {free['rule_id']} states no end of the free period")
    free_end = int(free["elapsed_to_minutes"])
    kind, eur, unit, _, _ = _band_values(rate, garage)
    if kind != "increment":
        raise SystemExit(f"garage {garage}: rule {rate['rule_id']} of type {rate['rule_type']} is no rate (an increment rule)")
    if free_end >= unit:
        raise SystemExit(f"garage {garage}: the free period of {free_end} min is not shorter than the billing unit of {unit} min "
                         "of the rate; the grace form needs a free period inside the first unit")
    stated = rate.get("free_period_minutes")
    if stated is not None and int(stated) != free_end:
        raise SystemExit(f"garage {garage}: the rule {rate['rule_id']} states a free period of {stated} min but the free rule "
                         f"{free['rule_id']} ends at {free_end} min")
    return [pg.DurationBand(0, free_end, "free", 0.0, None), pg.DurationBand(free_end, unit, "total", eur, None),
            pg.DurationBand(unit, None, "increment", eur, unit)]


def _encode_grace(spec: dict, rules: dict) -> dict:
    """:func:`encode_tariff` for a free period read as a grace period (ASSUMPTION P10): ``grace`` names the free rule and the
    rate rule; the result is the banded form with ASSUMPTIONS P8 and P10 (and P4, P5 as for any band)."""
    garage = spec["garage_id"]
    if spec.get("first") or spec.get("first_equals_rate"):
        raise SystemExit(f"garage {garage}: a grace specification has no first period; its free band and the total of the "
                         "first unit are the first period")
    if len(spec["grace"]) != 2:
        raise SystemExit(f"garage {garage}: 'grace' names exactly two rule ids, the free period and the rate")
    free = _value_rule(rules, spec["grace"][0], garage, "free period")
    rate = _value_rule(rules, spec["grace"][1], garage, "rate")
    return _finish_bands(spec, rules, _grace_bands(free, rate, garage), [free, rate], rate, [rate], [], ["P10"])


def _encode_table(spec: dict, rules: dict) -> dict:
    """:func:`encode_tariff` for a price table that a directory states (spec E13, ASSUMPTION P11 by ``p11_basis``): ``table`` is
    (the table rule, the bands as the owner decided them). The bands must reproduce EVERY price point of the table (capped at the
    24-hour price, which is the cap rule) and the last band must state the further hour the table reports; a stated rounding
    does not exist, so ASSUMPTION P4 applies. The result is the banded form with ASSUMPTION P8."""
    garage = spec["garage_id"]
    if spec.get("first") or spec.get("first_equals_rate"):
        raise SystemExit(f"garage {garage}: a table specification has no first period; its first band is the first period")
    name, bands_text = spec["table"]
    rule = _value_rule(rules, name, garage, "price table")
    if rule["rule_type"] != "price_table":
        raise SystemExit(f"garage {garage}: rule {name} of type {rule['rule_type']} is no price table")
    if not spec.get("cap"):
        raise SystemExit(f"garage {garage}: a price table needs its cap, the price of the 24-hour point")
    try:
        bands = pg.parse_duration_bands(bands_text)
    except ValueError as error:
        raise SystemExit(f"garage {garage}: the bands {bands_text!r} of the table {name} are no valid bands: {error}") from None
    cap = cap_eur(_value_rule(rules, spec["cap"], garage, "cap"))
    for point in sorted(rule["price_points"], key=lambda point: point["minutes"]):
        price = pg.duration_band_price_eur(bands, point["minutes"], cap)
        if abs(price - float(point["eur"])) > 1e-9:
            raise SystemExit(f"garage {garage}: the bands {bands_text!r} do not reproduce the price point of {point['minutes']} "
                             f"min of the table {name} ({price:.2f} EUR, the table states {float(point['eur']):.2f} EUR)")
    further = rule.get("reported_further_hour_eur")
    last = bands[-1]
    if further is not None and not (last.kind == "increment" and last.unit_min == 60 and abs(last.eur - float(further)) < 1e-9):
        raise SystemExit(f"garage {garage}: the last band of {bands_text!r} does not state the reported further hour of "
                         f"{float(further):.2f} EUR per 60 min of the table {name}")
    return _finish_bands(spec, rules, bands, [rule], rule, [rule], [], [])


def _finish_bands(spec: dict, rules: dict, bands: list, ordered_rules: list, window_rule: dict, rounding_rules: list,
                  readings: list, extra_assumptions: list) -> dict:
    """The tail of every banded form: the text of the bands, the day cap, the fee window, the assumptions P3, P4 (an increment
    band without a stated rounding), P5, P7 and P8, then ``extra_assumptions`` (P10), the values and the sentence."""
    garage = spec["garage_id"]
    bands_text = pg.format_duration_bands(bands)
    try:
        pg.parse_duration_bands(bands_text)
    except ValueError as error:
        raise SystemExit(f"garage {garage}: the band rules {[rule['rule_id'] for rule in ordered_rules]} make no valid bands: "
                         f"{error}") from None
    rule_ids = [rule["rule_id"] for rule in ordered_rules]
    cap = None
    if spec.get("cap"):
        cap_rule = _value_rule(rules, spec["cap"], garage, "cap")
        cap = cap_eur(cap_rule)
        rule_ids.append(cap_rule["rule_id"])
        reading = cap_reading(cap_rule)
        if reading is not None:
            readings.append(reading)
    window, quotation = _single_window(spec, window_rule, garage, readings)
    rounding_stated = all(rule.get("rounding") == "started_unit" for rule in rounding_rules)
    assumptions = []
    if spec.get("other_tiers"):
        assumptions.append("P3")
    if not rounding_stated:
        assumptions.append("P4")
    if window is None:
        assumptions.append("P5")
    if spec.get("other_caps"):
        assumptions.append("P7")
    assumptions.append("P8")
    assumptions.extend(extra_assumptions)
    start, end = window if window is not None else (0.0, 24.0)
    values = {"garage_hourly_rate_eur": None, "garage_billing_unit_min": None, "garage_first_period_min": None,
              "garage_first_period_eur": None, "garage_first_period_start_h": None, "garage_first_period_end_h": None,
              "garage_daily_cap_eur": cap, "garage_fee_start_h": start, "garage_fee_end_h": end, "tariff_tiers": None,
              "tariff_duration_bands": bands_text}
    sentence = f"bands over the stay duration in minutes: {bands_text}"
    if cap is not None:
        sentence += f", at most {_money(cap)} per day"
    sentence += f", charged {_clock(start)}-{_clock(end)}"
    return {"values": values, "assumptions": assumptions, "rule_ids": rule_ids, "sentence": sentence,
            "rounding_stated": rounding_stated, "window_stated": window is not None, "tiers": [],
            "window_quotation": quotation, "readings": readings}


def _clock(hours: float) -> str:
    whole = int(hours)
    minutes = int(round((hours - whole) * 60))
    return f"{whole:02d}:{minutes:02d}"


# ---------------------------------------------------------------- one garage
def _capacity(facility: dict) -> tuple:
    """(capacity or None, scope or None, the other observations in words) of a facility record. A reported capacity
    always has its scope: the record's own, or the scope of the observation that carries the same value; where neither
    exists the scope is never guessed (``SystemExit``)."""
    capacity = facility.get("capacity")
    capacity = int(round(float(capacity))) if capacity is not None else None
    observations = facility.get("capacity_observations") or []
    scope = facility.get("capacity_scope")
    if capacity is not None and scope is None:
        scope = next((observation["scope"] for observation in observations if observation.get("value") is not None
                      and int(round(float(observation["value"]))) == capacity), None)
        if scope is None:
            raise SystemExit(f"facility {facility.get('facility_id')}: the capacity {capacity} has no capacity_scope and no "
                             "capacity observation with that value; the scope of a reported capacity is never guessed")
    others = [f"{observation['scope']} {int(round(float(observation['value'])))}"
              + (f" ({observation['source_url']})" if observation.get("source_url") else "")
              for observation in observations if observation.get("value") is not None
              and (capacity is None or int(round(float(observation["value"]))) != capacity)]
    return capacity, scope, others


def _monthly(inputs: dict, garage_id: str) -> dict:
    """The used monthly product of a garage and the check that it is the cheapest of its publicly purchasable products; its
    rule must be marked preferred (ruling R-4b-8)."""
    products = [product for product in specs.MONTHLY_PRODUCTS if product.get("garage_id") == garage_id]
    used = [product for product in products if product["decision"] == "used"]
    if not used:
        return {}
    if len(used) != 1:
        raise SystemExit(f"garage {garage_id}: {len(used)} used monthly products; the cheapest is the one")
    rule = _value_rule(inputs["rules"], used[0]["rule"], garage_id, "monthly product")
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


def _source_date(rule: Optional[dict], feature: Optional[pd.Series], inputs: dict) -> str:
    """Retrieval date of the primary source: the rule's, else the feature's, else the retrieval of its geometry source."""
    if rule is not None and rule.get("retrieved_at"):
        return str(rule["retrieved_at"])[:10]
    if feature is None:
        raise SystemExit("a garage without a layer feature needs a rule with a retrieval date")
    for column in ("retrieved_on", "retrieved_at_utc"):
        if column in feature.index and _is_set(feature[column]):
            return str(feature[column])[:10]
    source = inputs["sources"].get(str(feature.get("geometry_source_id")))
    if source and source.get("retrieval_dates"):
        return max(source["retrieval_dates"])
    raise SystemExit(f"garage {feature.get('facility_id')}: no retrieval date of its primary source")


def rounding_census(facilities: dict, rules: dict) -> dict:
    """The census behind ASSUMPTION P4: of the preferred rate rules of every facility that has a feature in a garage layer,
    how many state their rounding (a value other than none or 'unspecified') and how many of those say started unit.
    {"rate_rules", "stated", "started_unit"}."""
    garage_facilities = {facility_id for facility_id, facility in facilities.items()
                         if any(str(reference).split(":")[0] in GARAGE_LAYERS for reference in facility.get("geometry_refs") or [])}
    census = {"rate_rules": 0, "stated": 0, "started_unit": 0}
    for rule in rules.values():
        if (rule.get("facility_id") in garage_facilities and rule.get("preferred_for_current_use") is True
                and rule["rule_type"] in RATE_RULE_TYPES and rule.get("billing_unit_minutes")):
            census["rate_rules"] += 1
            rounding = rule.get("rounding")
            if rounding not in (None, "unspecified"):
                census["stated"] += 1
                census["started_unit"] += rounding == "started_unit"
    return census


def _operator_named_by_page(operator: str, url: str, garage_id: str) -> str:
    """``operator`` when the page at ``url`` is the operator's own page (its name is in the host of the URL), else
    ``SystemExit``: an operator is never taken from a page that does not name it."""
    host = re.sub(r"[^a-z0-9]", "", urlparse(url).netloc.lower())
    name = re.sub(r"[^a-z0-9]", "", operator.lower())
    if not name or name not in host:
        raise SystemExit(f"garage {garage_id}: the operator {operator!r} is not the owner of the page {url}, so that page does "
                         "not name it; the operator is taken from a page of the operator only")
    return operator


def _primary_rule_id(spec: dict) -> str:
    """The rule whose page is the primary source of a priced garage: the rate, the first tier, the first band rule, the rate of
    a grace period or the table."""
    if spec.get("rate"):
        return spec["rate"]
    if spec.get("grace"):
        return spec["grace"][1]
    if spec.get("table"):
        return spec["table"][0]
    return (spec.get("tiers") or spec["bands"])[0]


def _check_identity(inputs: dict, spec: dict, key: str, identities: str, label: str) -> None:
    """The garage specification must name the regional facility that the package's identity rules give for its supplement (or
    follow-up) facility (``garage_supplement.identity_map``), or the facility's own new id where the regional package holds
    none: a garage is matched by its facility_id or a verified legacy_id, never by name."""
    package = inputs[label]
    facility_id = spec[key]
    if facility_id not in package["facilities"]:
        raise SystemExit(f"garage {spec['garage_id']}: the {label} holds no facility {facility_id}")
    expected = inputs[identities][facility_id] or facility_id
    if spec["facility"] != expected:
        raise SystemExit(f"garage {spec['garage_id']}: the specification names the facility {spec['facility']}, but the identity "
                         f"of the {label} facility {facility_id} in the regional package is {expected} (match by facility_id or "
                         "a verified legacy_id)")


def _package_hashes(inputs: dict, spec: dict) -> str:
    """The SHA-256 values a row cites, ';'-separated: the regional package, and the supplement and follow-up packages where
    they touch the row (in that order)."""
    hashes = [inputs["file"]["sha256"]]
    if spec.get("supplement") or spec.get("supplement_point"):
        hashes.append(inputs["supplement"]["file"]["sha256"])
    if spec.get("followup"):
        hashes.append(inputs["followup"]["file"]["sha256"])
    return ";".join(hashes)


def _released_phrase(rules: dict, rule_ids: list) -> str:
    """The sentence part that says on what authority the rules set their values: the regional rules by their flags (derived,
    never written by hand), the supplement and follow-up rules by the owner decision and field decisions that released them,
    the brochure rules by their quotations."""
    regional = [rule_id for rule_id in rule_ids if rules[rule_id].get("origin") is None]
    others = [rule_id for rule_id in rule_ids if rules[rule_id].get("origin") is not None]
    flags = {rules[rule_id].get("preferred_for_current_use") for rule_id in regional}
    preferred = ("all marked preferred_for_current_use in the package" if flags == {True}
                 else f"flags preferred_for_current_use {sorted(map(str, flags))}")
    if not others:
        return preferred
    parts = [f"regional rules {', '.join(regional)}: {preferred}"] if regional else []
    for origin, label in (("supplement", "supplement rules (the supplement marks every rule full_cost_calculation_ready=false; "
                                         "each is used as the owner decision that released it states)"),
                          ("followup", "follow-up rules (the follow-up package marks every rule full_cost_calculation_ready=false "
                                       "and no observation as the current operator tariff; each is used as the owner decision "
                                       "that released it states)"),
                          ("brochure", "brochure rules (quotations of the city brochure text, checked against it)")):
        used = [rule_id for rule_id in others if rules[rule_id]["origin"] == origin]
        if used:
            parts.append(f"{label}: " + ", ".join(f"{rule_id} ({rules[rule_id]['released_by']})" for rule_id in used))
    return "; ".join(parts)


def build_garage(inputs: dict, spec: dict) -> dict:
    """The dataset row (properties and ``geometry``, EPSG:25832), the QA facts (``_facts``) of one garage specification."""
    supplement_id, followup_id = spec.get("supplement"), spec.get("followup")
    if supplement_id is not None or spec.get("supplement_point"):
        _check_identity(inputs, spec, "supplement", "identity", "supplement")
    if followup_id is not None:
        _check_identity(inputs, spec, "followup", "followup_identity", "followup")
    if spec.get("supplement_point"):
        point_source = sup.entrance(inputs["supplement"], spec["supplement_point"])
        if point_source["facility_id"] != supplement_id:
            raise SystemExit(f"garage {spec['garage_id']}: the main point {spec['supplement_point']} belongs to the supplement "
                             f"facility {point_source['facility_id']}, not to {supplement_id}")
        layer = match = feature = attributes = None
    else:
        point_source = None
        layer, match = spec["layer"], spec["feature"]
        feature = _feature(inputs, layer, match)
        attributes = _feature(inputs, *spec["attributes"]) if spec.get("attributes") else None
    facility = _facility(inputs, spec["facility"])
    rules = inputs["rules"]
    ags, municipality = {**specs.TOWNS, **specs.OTHER_TOWNS}[spec["town"]]
    members = list(facility.get("tariff_rule_ids") or [])
    priced = "reason" not in spec
    if priced:
        encoded = encode_tariff(spec, rules)
        mentioned = (encoded["rule_ids"] + list(spec.get("other_tiers") or ()) + list(spec.get("other_caps") or ())
                     + list(spec.get("ignored") or {}))
    else:
        if spec["reason"] not in pg.NOT_PRICED_REASONS:
            raise SystemExit(f"garage {spec['garage_id']}: unknown reason {spec['reason']!r}")
        encoded = None
        mentioned = list(spec["evidence"])
    missing = [rule_id for rule_id in mentioned if rule_id not in set(members)]
    if missing:
        raise SystemExit(f"garage {spec['garage_id']}: the rules {missing} are not in the tariff rules of the package "
                         f"facility {spec['facility']}")
    # the primary source of the tariff: the page of the (first) rate or tier rule, for a garage that is not priced the first
    # rule that shows the reason (a garage without any rule has the page of its position as its source)
    if priced:
        primary = rules[_primary_rule_id(spec)]
    else:
        primary = rules[mentioned[0]] if mentioned else None
    capacity, scope, other_capacity = _capacity(facility)
    capacity_notes = []
    if point_source is not None:
        # the position and the name of a garage without coordinates in the regional package come from the supplement
        name = _ascii(inputs["supplement"]["facilities"][supplement_id]["name"])
        method, geometry_url = point_source["method"], point_source["url"]
        if point_source["capacity"] is not None:
            if capacity is not None and capacity != point_source["capacity"]:
                raise SystemExit(f"garage {spec['garage_id']}: the capacity {point_source['capacity']} of the supplement main "
                                 f"point conflicts with the capacity {capacity} of the regional facility {spec['facility']}")
            capacity, scope = point_source["capacity"], point_source["capacity_scope"]
            if not scope:
                raise SystemExit(f"garage {spec['garage_id']}: the supplement states the capacity {capacity} without a scope; "
                                 "the scope of a reported capacity is never guessed")
    else:
        name = _ascii(str(feature[GARAGE_LAYERS[layer]]))
        position = attributes if attributes is not None and "geometry_method" in attributes.index else feature
        method = str(position["geometry_method"]) if "geometry_method" in position.index and _is_set(
            position["geometry_method"]) else POSITION_METHODS[layer]
        geometry_url = str(position["geometry_source_url"])
        if supplement_id is not None:
            stated = inputs["supplement"]["facilities"][supplement_id].get("capacity")
            if stated is not None and stated != capacity:
                capacity_notes.append(f"The supplement package reports the capacity {stated} for this garage without a scope; "
                                      "it is not taken (the scope of a reported capacity is never guessed).")
    if spec.get("capacity_from"):
        observed = rules[spec["capacity_from"]].get("capacity_reported")
        if observed is None or (capacity is not None and capacity != int(observed)):
            raise SystemExit(f"garage {spec['garage_id']}: the observation {spec['capacity_from']} states the capacity "
                             f"{observed} but the garage has {capacity}")
        capacity, scope = int(observed), "secondary_directory_total"
    if spec.get("archived_point"):
        # the archived municipal point of 2018 (follow-up package) is the point of the regional layer: the same coordinate
        archived = inputs["followup"]["point"]
        distance = float(archived["geometry"].distance(feature.geometry))
        if distance > sup.COORDINATE_TOLERANCE_M:
            raise SystemExit(f"garage {spec['garage_id']}: the archived point of the follow-up package differs from the point of "
                             f"the layer by {distance:.3f} m (more than {sup.COORDINATE_TOLERANCE_M} m)")
        method = "archived_municipal_point_2018"
        geometry_url = str(inputs["followup"]["sources"][archived["properties"]["geometry_source_id"]]["url"])
    source_url = str(primary["source_url"]) if primary is not None else geometry_url
    operator, operator_note = None, None
    if "operator" in spec and not isinstance(spec["operator"], tuple):  # an explicit statement (None: the text is no operator)
        operator = spec["operator"]
        if operator is not None:
            _operator_named_by_page(operator, source_url, spec["garage_id"])
            operator_note = f"Operator {operator}, named by its own page {source_url} (the primary source)."
    elif isinstance(spec.get("operator"), tuple):
        _, wanted, member, quotation, source_id = spec["operator"]
        named = sup.operator_from_page(inputs["supplement"], supplement_id, wanted, member, quotation, source_id)
        operator = named["operator"]
        operator_note = (f"Operator {operator}, named by its own page {named['url']} (quotation '{named['quotation']}', in the "
                         "copy that the supplement package keeps).")
    else:
        for source in (feature, attributes):
            if source is not None and "operator" in source.index and _is_set(source["operator"]):
                operator = _ascii(str(source["operator"]).strip())
                break
        if operator is None and _is_set((facility.get("attributes") or {}).get("operator")):
            operator = _ascii(str(facility["attributes"]["operator"]).strip())
    point = point_source["geometry"] if point_source is not None else feature.geometry
    # ---- notes
    if point_source is not None:
        notes = [f"Package facility {spec['facility']}, main point {spec['supplement_point']} of the supplement package; "
                 f"position: {method} ({point_source['description']})."]
    else:
        notes = [f"Package facility {spec['facility']}, feature {layer}:{match[1]}; position: {method}."]
    if spec.get("archived_point"):
        notes.append("The position is the archived municipal point of 2018, the same coordinate as the regional layer (checked); "
                     "the budget of 2026 documents the garage but updates neither the age nor the accuracy of the point.")
    if supplement_id is not None:
        notes.append(f"Supplement package {sup.SUPPLEMENT_FILE} (SHA-256 {inputs['supplement']['file']['sha256']}), facility "
                     f"{supplement_id}, patch scope "
                     f"{inputs['supplement']['facilities'][supplement_id].get('patch_scope', 'not stated')}.")
    if followup_id is not None:
        notes.append(f"Follow-up package {sup.FOLLOWUP_FILE} (SHA-256 {inputs['followup']['file']['sha256']}), facility "
                     f"{followup_id}.")
        action = inputs["followup"]["actions"].get(followup_id)
        if priced and action and action.get("include_in_costed_facility_list") is False:
            notes.append(f"The follow-up package recommends {action['action']} (include_in_costed_facility_list false); this "
                         "recommendation is overridden by the owner (ruling R-4b2-8, spec E13).")
    facts = {"tiered": False, "banded": False, "cap": False, "readings": [], "supplement": supplement_id is not None,
             "followup": followup_id is not None, "point": point_source is not None, "priced": priced,
             "followup_values": False}
    if priced:
        notes.append(f"Priced from the package rules {', '.join(encoded['rule_ids'])} "
                     f"({_released_phrase(rules, encoded['rule_ids'])}): {encoded['sentence']}.")
        facts["followup_values"] = any(rules[rule_id].get("origin") == "followup" for rule_id in encoded["rule_ids"])
        if encoded["window_quotation"]:
            notes.append(f"Charging times stated by the source: {_ascii(encoded['window_quotation'])}.")
        if "P3" in encoded["assumptions"]:
            tiers = "; ".join(describe_rule(rules[rule_id]) for rule_id in spec["other_tiers"])
            notes.append(f"ASSUMPTION P3: a night tariff that is no per-unit rate of the preferred rules is not charged by "
                         f"the model: {tiers}.")
        if "P4" in encoded["assumptions"]:
            census = inputs["rounding_census"]
            notes.append("ASSUMPTION P4: the rounding of the rate is not stated, so it is billed per started unit. The package "
                         f"cautions ('{PACKAGE_ROUNDING_CAUTION}'); the census of its preferred garage rate rules supports "
                         f"the assumption: {census['stated']} of {census['rate_rules']} state their rounding, and "
                         f"{census['started_unit']} of those {census['stated']} say started unit.")
        if "P5" in encoded["assumptions"]:
            hours = opening_hours_text(str((facility.get("attributes") or {}).get("opening_hours_text") or "")).strip().rstrip(".")
            notes.append("ASSUMPTION P5: no preferred rule states charging times of the tariff, so the fee window is 0-24 h"
                         + (f" (opening hours, which are no charging hours: {hours})" if hours else "") + ".")
        if "P6" in encoded["assumptions"]:
            notes.append(f"ASSUMPTION P6: {pg.ASSUMPTIONS['P6']}; a time of day outside every tier is free.")
            facts["tiered"] = True
        if "P8" in encoded["assumptions"]:
            notes.append(f"ASSUMPTION P8: {pg.ASSUMPTIONS['P8']}.")
            facts["banded"] = True
        for assumption, key in (("P10", "p10_basis"), ("P11", "p11_basis")):
            if assumption in encoded["assumptions"]:
                if not spec.get(key):
                    raise SystemExit(f"garage {spec['garage_id']}: a row that rests on ASSUMPTION {assumption} states the basis "
                                     f"of its reading in {key!r}")
                notes.append(f"ASSUMPTION {assumption}: {pg.ASSUMPTIONS[assumption]}. Basis of the reading: {spec[key]}.")
        if "P10" in encoded["assumptions"]:
            if not spec.get("p10_decisions"):
                raise SystemExit(f"garage {spec['garage_id']}: a row that rests on ASSUMPTION P10 states the field decisions of "
                                 "its ruling in 'p10_decisions' (the ruling and {decision id: the statuses it relied on})")
            ruling, needed = spec["p10_decisions"]
            sup.require_decisions(inputs["supplement"]["decisions"], needed,
                                  f"garage {spec['garage_id']}: the grace-period reading (ASSUMPTION P10) rests on the ruling "
                                  f"{ruling}")
        if "P7" in encoded["assumptions"]:
            caps = "; ".join(describe_rule(rules[rule_id]) for rule_id in spec["other_caps"])
            notes.append(f"ASSUMPTION P7: the day cap column holds one cap and applies to the whole stay; not applied: {caps}.")
        for reading in encoded["readings"]:
            if reading[0] == "cap":
                notes.append(f"Reading: the day boundary of the cap {reading[1]} is not stated (cap period {reading[2]!r}), so "
                             "it is read as a maximum per stay.")
            elif reading[0] == "cap_calendar":
                notes.append(f"Reading: the cap {reading[1]} is stated per calendar day (cap period {reading[2]!r}); the dataset "
                             "column holds one maximum per stay, so the cap is read as a maximum per stay: a stay inside one "
                             "calendar day pays what is published, a stay across midnight is capped once, not once per calendar "
                             "day.")
            elif reading[0] == "unit":
                notes.append(f"Reading: {rules[reading[1]]['unit_reading']} ({reading[1]}).")
            elif reading[0] == "rest":
                notes.append(f"Reading: the day rate {reading[1]} states no charging times; it applies at every time of day that "
                             f"the other tiers do not cover ({reading[2]}).")
        undated = [reading[1] for reading in encoded["readings"] if reading[0] == "days"]
        if undated:
            notes.append(f"Reading: the window of {', '.join(undated)} states no days, so it is read as Monday to Friday.")
        # a garage counts as capped when it has a day cap or a published day total whose boundary is read as a stay maximum
        facts["cap"] = (encoded["values"]["garage_daily_cap_eur"] is not None
                        or any(reading[0] == "cap" for reading in encoded["readings"]))
        facts["readings"] = encoded["readings"]
        if spec.get("ignored"):
            notes.append("Not encoded: " + "; ".join(f"{describe_rule(rules[rule_id])} ({why})"
                                                       for rule_id, why in spec["ignored"].items()) + ".")
    else:
        shown = "; ".join(describe_rule(rules[rule_id]) for rule_id in mentioned)
        notes.append(f"Not priced ({spec['reason']}): {spec['reason_text']}." + (f" Rules: {shown}." if shown else ""))
    unused = [rule_id for rule_id in members if rules[rule_id].get("preferred_for_current_use") is not True
              and rule_id not in mentioned]
    if unused:
        notes.append(f"Not preferred for current use and not used: {', '.join(unused)}.")
    if spec.get("comment"):
        notes.append(spec["comment"])
    if operator_note:
        notes.append(operator_note)
    if priced:
        elsewhere = {rule_id: str(rules[rule_id]["source_url"]) for rule_id in encoded["rule_ids"]
                     if str(rules[rule_id]["source_url"]) != source_url}
        if elsewhere:
            notes.append("Rules from another page than source_url: " + "; ".join(
                f"{rule_id} ({url})" for rule_id, url in elsewhere.items()) + ".")
    notes.extend(capacity_notes)
    if capacity is not None:
        notes.append(f"Capacity {capacity} ({scope})" + (f"; other observations: {'; '.join(other_capacity)}"
                                                           if other_capacity else "") + ".")
    elif other_capacity:
        notes.append(f"No capacity taken ({scope or 'unknown'}); observations: {'; '.join(other_capacity)}.")
    # a second position of the package for the same facility (another layer): the distance is stated, never hidden
    for other_layer, frame in inputs["layers"].items():
        if layer is None or other_layer == layer or "facility_id" not in frame.columns:
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
           "source_url": source_url, "source_date": _source_date(primary, feature, inputs),
           "tariff_rule_ids": ";".join(encoded["rule_ids"] if priced else mentioned) or None,
           "geometry_method": method, "geometry_source_url": geometry_url,
           "package_sha256": _package_hashes(inputs, spec), "geometry": point}
    for column in pg.TARIFF_COLUMNS + pg.FIRST_PERIOD_WINDOW_COLUMNS + pg.TIER_COLUMNS + pg.BAND_COLUMNS:
        row[column] = encoded["values"][column] if priced else None
    monthly = _monthly(inputs, spec["garage_id"])
    row.update({key: monthly.get(key) for key in ("monthly_eur", "monthly_source_url", "monthly_product")})
    if monthly:
        notes.append(f"Monthly product {monthly['rule_id']}: {monthly['monthly_product']}.")
    row["notes"] = _ascii(" ".join(notes))
    row["_facts"] = facts
    return row


def build_garages(inputs: dict) -> gpd.GeoDataFrame:
    """The dataset: one row per garage specification, in specification order, EPSG:25832. Stops (``SystemExit``) when a
    garage of the package's layers is covered by no specification and no candidate decision, when a facility is both a
    garage and a candidate, when a candidate that states 'no coordinates' has a feature in a garage layer, when two
    specifications use one feature or when the result violates ``garages.validate_garages``. Prints the accounting per layer
    and the rates."""
    if any(spec.get("supplement") or spec.get("supplement_point") for spec in specs.GARAGE_SPECS) and "supplement" not in inputs:
        raise SystemExit("the garage specifications rest on the supplement package: pass it as --supplement-zip "
                         f"({sup.SUPPLEMENT_FILE})")
    if any(spec.get("followup") for spec in specs.GARAGE_SPECS) and "followup" not in inputs:
        raise SystemExit("the garage specifications rest on the follow-up package: pass it as --followup-zip "
                         f"({sup.FOLLOWUP_FILE})")
    used = {layer: [] for layer in GARAGE_LAYERS}
    rows = []
    for spec in specs.GARAGE_SPECS:
        rows.append(build_garage(inputs, spec))
        if spec.get("layer"):
            used[spec["layer"]].append(spec["feature"])
        if spec.get("attributes"):
            used[spec["attributes"][0]].append(spec["attributes"][1])
    facts = [row.pop("_facts") for row in rows]
    ids = [spec["garage_id"] for spec in specs.GARAGE_SPECS]
    if len(set(ids)) != len(ids):
        raise SystemExit("two garage specifications share a garage_id")
    # Every garage of the package's layers must be known: specified explicitly, a twin (the same facility id in another
    # layer) of a specified garage, or decided as a candidate that is no garage of the dataset (a station BahnPark, a lot of
    # long-term renters: a QA row with its reason). A garage nobody decided stops the step instead of being left out
    # silently.
    specified = {str(spec["facility"]) for spec in specs.GARAGE_SPECS}
    decided = {candidate["facility"] for candidate in specs.PACKAGE_CANDIDATES if candidate.get("facility")}
    if specified & decided:
        raise SystemExit(f"facility(ies) {sorted(specified & decided)} are both a garage of the dataset and a candidate that "
                         "is no garage")
    layer_facilities = {str(value) for frame in inputs["layers"].values() if "facility_id" in frame.columns
                        for value in frame["facility_id"]}
    for candidate in specs.PACKAGE_CANDIDATES:
        if candidate["reason"] == "no_coordinates" and candidate.get("facility") in layer_facilities:
            raise SystemExit(f"candidate {candidate['record_id']}: the reason is no_coordinates but the facility "
                             f"{candidate['facility']} has a feature in a garage layer")
    for layer, layer_frame in inputs["layers"].items():
        garage_features = layer_frame[layer_frame["Nutzung"] == "Parkhaus"] if layer == "gos_parking_locations" else layer_frame
        keys = {(column, str(value)) for column, value in used[layer]}
        if len(keys) != len(used[layer]):
            raise SystemExit(f"layer {layer}: two garage specifications use the same feature {sorted(used[layer])}")
        explicit = [index for index, row in garage_features.iterrows()
                    if any(column in layer_frame.columns and str(row[column]) == value for column, value in keys)]
        twins = [index for index in garage_features.index if index not in explicit
                 and str(garage_features.loc[index, "facility_id"]) in specified]
        candidates = [index for index in garage_features.index if index not in explicit and index not in twins
                      and str(garage_features.loc[index, "facility_id"]) in decided]
        unknown = [str(garage_features.loc[index, "facility_id"]) for index in garage_features.index
                   if index not in explicit and index not in twins and index not in candidates]
        if unknown:
            raise SystemExit(f"layer {layer}: garage feature(s) {unknown} are covered by no garage specification and no "
                             "candidate decision; add them to regional_garage_specs.GARAGE_SPECS or PACKAGE_CANDIDATES")
        inputs["ledger"].append({"layer": layer, "features": len(layer_frame), "garages": len(garage_features),
                                 "used": len(explicit), "twins": len(twins), "candidates": len(candidates)})
        print(f"[garages] {layer}: {len(layer_frame)} features, {len(garage_features)} garages, {len(explicit)} used, {len(twins)} "
              f"twin(s) of a garage that another layer gives (same facility id, skipped), {len(candidates)} decided as "
              "candidate(s) that are no garage of the dataset (QA rows), 0 repaired (an invalid geometry stops the step)")
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
    _print_rates(frame, facts)
    return frame


def _print_rates(frame: gpd.GeoDataFrame, facts: list) -> None:
    """The rates of the dataset: priced against not priced, and among the priced the primary method (the source states the
    rounding and the charging times) against the assumptions P3 to P7, the union rates (at least one assumption; P4 or P5)
    and the two readings (a cap without a stated day boundary, a window without stated days); a share above its warning
    threshold is a warning."""
    summary = pg.coverage(frame)
    priced = summary["priced"]
    print(f"[garages] {summary['listed']} garages: priced {priced} ({100.0 * priced / summary['listed']:.1f} %), not priced "
          f"{summary['not_priced']} (" + ", ".join(f"{reason} {count}" for reason, count in
                                                   summary["not_priced_by_reason"].items()) + ")")
    counts = summary["priced_by_assumption"]
    for assumption, text in pg.ASSUMPTIONS.items():
        count = counts.get(assumption, 0)
        share = count / priced if priced else 0.0
        warning = f"; WARNING: more than {100.0 * ASSUMPTION_WARNING_SHARE:.0f} % of the priced garages rest on it" \
            if share > ASSUMPTION_WARNING_SHARE else ""
        print(f"[garages] priced garages resting on ASSUMPTION {assumption}: {count}/{priced} ({100.0 * share:.1f} %){warning} "
              f"- {text}")
    for label, count in (("at least one assumption", summary["priced_with_assumption"]),
                         ("ASSUMPTION P4 or P5", summary["priced_with_p4_or_p5"])):
        share = count / priced if priced else 0.0
        warning = f"; WARNING: more than {100.0 * pg.UNION_WARNING_SHARE:.0f} % of the priced garages" \
            if share > pg.UNION_WARNING_SHARE else ""
        print(f"[garages] priced garages resting on {label}: {count}/{priced} ({100.0 * share:.1f} %){warning}")
    print(f"[garages] rounding stated by the source {priced - counts.get('P4', 0)}/{priced}, ASSUMPTION P4 "
          f"{counts.get('P4', 0)}/{priced}; charging times stated {priced - counts.get('P5', 0)}/{priced}, ASSUMPTION P5 "
          f"{counts.get('P5', 0)}/{priced}; tiered {summary['priced_tiered']}/{priced}; banded "
          f"{summary['priced_banded']}/{priced}; monthly product on {summary['with_monthly_product']} garages")
    capped = [fact for fact in facts if fact["cap"]]
    cap_readings = sum(any(reading[0] == "cap" for reading in fact["readings"]) for fact in capped)
    window_rows = [fact for fact in facts if any(reading[0] == "days" for reading in fact["readings"])]
    print(f"[garages] reading: a day cap whose day boundary is not stated is read as a maximum per stay: {cap_readings}/"
          f"{len(capped)} garages with a cap or a day total; a window without stated days is read as Monday to Friday: "
          f"{len(window_rows)} garage(s)")
    counts = {kind: sum(any(reading[0] == kind for reading in fact["readings"]) for fact in facts)
              for kind in ("cap_calendar", "unit", "rest")}
    if any(counts.values()):
        print(f"[garages] reading: a day cap stated per calendar day is held as a maximum per stay: {counts['cap_calendar']} "
              "garage(s); a rate whose unit the source does not state is read as the unit of the first price: "
              f"{counts['unit']} garage(s); a day rate without charging times takes the rest of the day beside a night tier: "
              f"{counts['rest']} garage(s)")
    touched = [fact for fact in facts if fact["supplement"]]
    if touched:
        print(f"[garages] supplement package: {len(touched)} garages touched ({sum(fact['point'] for fact in touched)} at its "
              f"main points), {sum(fact['priced'] for fact in touched)} priced from its rules or points")
    followed = [fact for fact in facts if fact["followup"]]
    if followed:
        print(f"[garages] follow-up package: {len(followed)} garages touched, "
              f"{sum(fact['priced'] and fact['followup_values'] for fact in followed)} priced from its observations")


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
    text = re.sub(r"\s+", " ", html.unescape(text).replace("\u20ac", "EUR")).strip()
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
            if spec.get("qa_comment"):
                note += f"; {spec['qa_comment']}"
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
        if product.get("why"):
            note = f"{note}; {product['why']}"
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
    for candidate in specs.PACKAGE_CANDIDATES:
        ags = (specs.TOWNS.get(candidate["town"]) or specs.OTHER_TOWNS[candidate["town"]])[0]
        facility_id, tariff, evidence = candidate.get("facility"), "", candidate.get("evidence") or ""
        if facility_id:
            facility = _facility(inputs, facility_id)
            tariff = "; ".join(describe_rule(rules[rule_id]) for rule_id in facility.get("tariff_rule_ids") or [])
            evidence = evidence or f"facilities.json {facility_id}"
        elif not evidence:
            raise SystemExit(f"candidate {candidate['record_id']}: a candidate without a facility record needs its evidence")
        row(record_id=candidate["record_id"], record_type="candidate", municipality_ags=ags, subject=candidate["subject"],
            decision="not_listed", reason_code=candidate["reason"], count=1, evidence=_ascii(evidence),
            note=_ascii(candidate["note"] + (f"; package rules: {tariff}" if tariff else "")))
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
    parser.add_argument("--supplement-zip", help="the owner's supplement package Parkhaus_Ergaenzungen_2026-10-07.zip (spec E12; "
                                                 "required where the garage specifications rest on it)")
    parser.add_argument("--followup-zip", help="the owner's follow-up package Parkhaus_Nachrecherche_2026-10-07.zip (spec E13; "
                                               "needs --supplement-zip; required where the specifications rest on it)")
    args = parser.parse_args(argv)
    inputs = load_garage_inputs(args.regional_dir, supplement_path=args.supplement_zip, followup_path=args.followup_zip)
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
