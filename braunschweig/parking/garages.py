"""Committed parking garage dataset: garages as points with their own tariff (parking cost zones v2, issue #436).

Spec Amendment E1 makes garages entities of their own: ``parking_garages_2026.geojson`` holds one point per garage (WGS84
on disk like the zone polygons, EPSG:25832 in memory) with its identity, the garage tariff columns of spec Amendment A6, a
monthly product where one is published, the reported capacity and the provenance of every value. The dataset is built by
the curation step ``scripts/curation/parking_zones_2026/regional_garages.py`` from the regional evidence package of
2026-10-07 and read by the distance-weighted garage options of spec Amendment E (task 4d): the zone release stage
(``braunschweig.parking.zones_stage``) loads and validates it, the tariff model export writes its priced garages (schema 3)
and ``braunschweig.parking.cost.parking_cost_with_garages`` is the reference pricing.

The garage tariff columns are those of the tariff table (``braunschweig.parking.zones.GARAGE_COLUMNS``, money in EUR,
minutes as whole numbers, fee window in decimal hours of the weekday) and mean the same: the core (hourly rate, billing
unit, fee window) is all-or-none, the day cap (empty = none) and the first-period pair (both or neither) need the core. A
garage is ``priced`` exactly when its core is set; a garage whose published tariff the columns cannot express exactly stays
listed and not priced with a ``not_priced_reason`` (``NOT_PRICED_REASONS``): no value is approximated or invented. Every
assumption a row rests on is an id in ``assumptions`` (``ASSUMPTIONS``) that the row's notes name, so that a priced garage
whose tariff is not stated in every detail can be told apart and counted.

A garage is priced in exactly one of three forms (rulings R-4b-10b and R-4b-11) plus the free schedule, documented here
because the validator enforces it (one tariff structure per garage):

* the single-window form, the garage core of the tariff table: one hourly rate in started billing units with one fee
  window (``garage_hourly_rate_eur``, ``garage_billing_unit_min``, ``garage_fee_start_h``, ``garage_fee_end_h``);
* the tiered form, ``tariff_tiers``: the published time-of-day tiers of a garage whose rate changes with the time of day
  (a morning, day, evening and night rate), one text of ``HH:MM-HH:MM <eur>/<unit_min>`` tiers separated by ``"; "`` (see
  :func:`parse_tariff_tiers`), with the four single-window columns EMPTY. A time of day outside every tier is free. A
  tiered garage rests on ASSUMPTION P6 (how a stay is priced from the tiers; implemented by the pricing code, not here);
* the banded form, ``tariff_duration_bands``: a published schedule over the elapsed duration of the stay, one text of
  ``<from_min>-<to_min> <kind>`` bands separated by ``"; "`` (grammar in :func:`parse_duration_bands`; for example
  ``"0-20 free; 20-120 total 1.00; 120-240 0.50/60; 240-420 1.50/60; 420- 5.00/60"``), with the rate and the billing unit
  EMPTY, no first period and no tiers; the fee window (both hours) applies unchanged. A banded garage rests on
  ASSUMPTION P8 (the cumulative reading of the bands). :func:`duration_band_price_eur` is the reference evaluation of a
  schedule and the one the garage option pricing of ``braunschweig.parking.cost`` calls (no logic is copied).

Two uses of the bands text are no schedule in that sense (Task 4b3, spec E14), and the validator tells them apart:

* the FREE SCHEDULE ``"0- free"`` (:func:`is_free_schedule`): one open free band, a car park that is free for every stay (the
  free public car parks of the Wolfsburg city layer; ASSUMPTION P12 where the car park is free only by default). It has the
  formal fee window 0 to 24 h, rests on no assumption of the bands (neither P8 nor P10) and counts as ``priced_free``;
* the GRACE PERIOD of a tiered garage (:func:`is_grace_period`): ``tariff_tiers`` together with one CLOSED free band, e.g.
  ``"0-30 free"``: a stay not longer than the band costs 0 and every longer stay is priced by the tiers from its arrival
  (ASSUMPTION P10 read for tiers: the free minutes are not deducted). The tiered garage has no fee window and no first
  period; it rests on ASSUMPTIONS P6 and P10 and not on P8 (nothing is read cumulatively).

The first period (``garage_first_period_min`` with ``garage_first_period_eur``, both or neither) and the day cap
(``garage_daily_cap_eur``, empty = none) belong to the single-window and the tiered form and need one of them; a banded
garage has no first period (its first band is the first period) but may carry the day cap, which is the published cap or
24-hour price. The first period may carry the clock window it is tied to (``garage_first_period_start_h``,
``garage_first_period_end_h``, both or neither, only together with a first period; ruling R-4b-12): empty where the source
ties the first period to no window.

Every validator raises ``ValueError`` listing every violation with the garage id and the column, so a broken dataset fails
at load time. CRS: EPSG:25832 in memory, distances in metres, clock times in minutes after midnight.
"""
from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import NamedTuple, Optional

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

IDENTITY_COLUMNS = ("garage_id", "package_facility_id", "name", "facility_kind", "operator", "municipality",
                    "municipality_ags")
#: What a row of the dataset is (``facility_kind``, ruling R-4b3-0): a ``garage`` (a multi-storey or underground garage, or a
#: car park the sources call one) or a ``surface_lot`` (an open car park, e.g. the car parks of the Wolfsburg city layer). The
#: pricing treats both alike (no special case); the kind is documentation and a count of the dataset.
FACILITY_KINDS = {
    "garage": "a multi-storey or underground garage, or a car park the sources call a garage",
    "surface_lot": "an open surface car park (the car parks of the Wolfsburg city layer wob_parkplaetze)",
}
CAPACITY_COLUMNS = ("capacity_reported", "capacity_scope")
#: The garage tariff columns of the tariff table (spec Amendment A6), in the order of the table.
TARIFF_COLUMNS = zones.GARAGE_COLUMNS
#: The clock window of the first period (ruling R-4b-12), in decimal hours of the weekday like the fee window: the first
#: period is charged once when the arrival lies inside it; empty where the source ties the first period to no window.
FIRST_PERIOD_WINDOW_COLUMNS = ("garage_first_period_start_h", "garage_first_period_end_h")
#: The tiered form of the tariff (ruling R-4b-10b): the time-of-day tiers as one text, see :func:`parse_tariff_tiers`.
TIER_COLUMNS = ("tariff_tiers",)
#: The banded form of the tariff (ruling R-4b-11): the duration bands as one text, see :func:`parse_duration_bands`.
BAND_COLUMNS = ("tariff_duration_bands",)
MONTHLY_COLUMNS = ("monthly_eur", "monthly_source_url", "monthly_product")
STATUS_COLUMNS = ("priced", "not_priced_reason", "assumptions")
PROVENANCE_COLUMNS = ("source_url", "source_date", "tariff_rule_ids", "geometry_method", "geometry_source_url",
                      "package_sha256", "notes")
#: Every property of a feature, in file order.
DATASET_COLUMNS = (IDENTITY_COLUMNS + CAPACITY_COLUMNS + TARIFF_COLUMNS + FIRST_PERIOD_WINDOW_COLUMNS + TIER_COLUMNS
                   + BAND_COLUMNS + MONTHLY_COLUMNS + STATUS_COLUMNS + PROVENANCE_COLUMNS)
MONEY_COLUMNS = ("garage_hourly_rate_eur", "garage_first_period_eur", "garage_daily_cap_eur", "monthly_eur")
MINUTE_COLUMNS = ("garage_billing_unit_min", "garage_first_period_min")
HOUR_COLUMNS = ("garage_fee_start_h", "garage_fee_end_h") + FIRST_PERIOD_WINDOW_COLUMNS
INTEGER_COLUMNS = ("capacity_reported",)
TEXT_COLUMNS = tuple(column for column in DATASET_COLUMNS
                     if column not in MONEY_COLUMNS + MINUTE_COLUMNS + HOUR_COLUMNS + INTEGER_COLUMNS + ("priced",))
#: Columns a row must carry whatever its status; ``operator``, ``capacity_*`` and the monthly product are optional
#: (empty = the package states none).
REQUIRED_TEXT_COLUMNS = ("garage_id", "package_facility_id", "name", "facility_kind", "municipality", "municipality_ags",
                         "source_url",
                         "source_date", "geometry_method", "geometry_source_url", "package_sha256", "notes")
LIST_SEPARATOR = ";"
#: A row cites the regional evidence package and, where the supplement package (spec E12), the follow-up package (spec E13) or
#: the Wolfsburg car-park package (spec E14) of 2026-10-07 touches the row, that package too: its ``package_sha256`` holds one
#: to four lower-case SHA-256 values separated by ``LIST_SEPARATOR`` (the regional package first, then the supplement, the
#: follow-up and the Wolfsburg car-park package in that order).
MAXIMUM_PACKAGE_HASHES = 4

#: Why a garage is listed and not priced (``not_priced_reason``): the column holds the code, the details of the garage
#: (what is published, which rule ids) are in its notes.
NOT_PRICED_REASONS = {
    "no_published_tariff": "the sources give no tariff of the garage",
    "free_period": "the tariff has a free first period, which the garage columns cannot express (they hold no free "
                   "threshold) and whose treatment on longer stays the source does not state",
    "incomplete_tariff": "the published tariff does not state how a stay is billed beyond its first unit",
    "conflicting_sources": "the sources of the tariff contradict each other and the package does not mark one rule set as "
                           "calculation-ready",
}
#: The assumptions a priced row may rest on (``assumptions``); each is named as ``ASSUMPTION <id>`` in the row's notes.
#: P3 to P5 say how a published detail that the preferred rules leave open or the columns cannot express is read; P6 is
#: the pricing semantics of the tiered form, which the pricing code of the garage options implements (ruling R-4b-10b,
#: amended by ruling R-4b-12 for the clock window of the first period); P7 names the caps the single day-cap column cannot
#: hold; P8 is the pricing semantics of the banded form (ruling R-4b-11); P10 reads a published free period at the start of a
#: stay as a grace period (spec E12, owner decision 2026-10-07; P9, the pricing of a stay beyond a closed schedule, is a
#: rule of the pricing code and no assumption a row rests on, so it lives in the assumptions register of the tariff model
#: export only); P11 prices a garage from the best available secondary evidence where no operator tariff is published
#: (spec E13, ruling R-4b2-8). There is no reason code for a duration
#: schedule that the columns cannot express any more: every schedule of the sources is a band text.
ASSUMPTIONS = {
    "P3": "a night tariff that is no per-unit rate of the preferred rules (a flat night fee, an unresolved night tier) is "
          "stated in the notes and not charged",
    "P4": "a published rate per unit without a stated rounding is billed per started unit, as at every garage of the "
          "dataset that states its rounding",
    "P5": "the fee window is 0 to 24 h where no preferred rule states charging times of the garage tariff: a ticket "
          "garage bills the stay from entry to exit, and opening hours are no charging hours",
    "P6": "units are counted from arrival and each started unit costs the rate of the tier in force at the unit's start; "
          "a first period, where published, is charged once when the arrival lies inside its clock window (the whole day "
          "where the source ties it to none) and the tiers then apply per started unit from the end of the first period; "
          "an arrival outside the window pays the tiers from the arrival",
    "P7": "where a garage publishes several caps (a day cap, a night cap, a maximum for day and night together), the day "
          "cap column holds the day cap, or the 24-hour maximum where there is no day cap, and applies to the whole stay; "
          "the other caps are stated in the notes and not applied",
    "P8": "a duration schedule is read cumulatively over the elapsed duration d of the stay: a total band sets the price of "
          "the stay to its amount (an absolute price, no addition), an increment band adds its amount for every started "
          "unit counted from the band's start to the price reached at its start, a free band costs nothing; where the "
          "source states no rounding the unit is a started unit (as in P4); a published cap or 24-hour price applies as "
          "the day cap, the smaller of the schedule price and the cap",
    "P10": "a published free period at the start of a stay is a grace period: a stay not longer than it costs 0; a longer "
           "stay is billed from the arrival as if there were no free period (the free minutes are not deducted); encoded as "
           "a free first duration band followed by the price of the first billing units (ASSUMPTION P8), or, next to "
           "time-of-day tiers (ASSUMPTION P6), as the one closed free band that precedes the tiers of every longer stay",
    "P11": "where no current operator tariff is published, the garage is priced from the best available secondary evidence "
           "for the same facility (a directory or tourism table, newest and most detailed first), checked for consistency "
           "against the garages of the same town; the source, its lack of a date and the operator's missing confirmation "
           "are named in the notes",
    "P12": "a public car park of the Wolfsburg city layer that lies outside every published municipal tariff area and has "
           "no operator tariff in the sources is free of charge for every stay (municipal free default): the municipal fee "
           "ordinance levies fees where parking is permitted only with a ticket machine and the city's published layer of "
           "those areas holds none at the point; no evidence of a fee is not evidence of none, so every such row is named "
           "and counted; encoded as the free schedule '0- free'",
}
#: Warn when more than this share of the priced garages rest on at least one assumption of ``ASSUMPTIONS``, or on P4 or P5
#: (a stated rounding or stated charging times replaced by an assumption): then the published structure itself covers a
#: minority of the priced garages and a user of the dataset should know. The same share is the per-assumption warning
#: threshold of the curation step. A share is a rate of the priced garages (0 to 1); the check is "above", not "at".
UNION_WARNING_SHARE = 0.75
#: Priced and not-priced counts are reported per assumption with these ids, so a sensitivity arm can leave a group out.
_ID_PATTERN = re.compile(r"^[a-z0-9_]+$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_URL_PATTERN = re.compile(r"^https?://\S+$")


def _split(text) -> list:
    """The ids of a ';'-separated list cell (``assumptions``, ``tariff_rule_ids``); empty for an empty cell."""
    if not zones._is_set(text):
        return []
    return [part.strip() for part in str(text).split(LIST_SEPARATOR) if part.strip()]


# --------------------------------------------------------------------------- time-of-day tiers

MINUTES_PER_DAY = 24 * 60
TIER_SEPARATOR = "; "
_TIER_PATTERN = re.compile(r"^(\d{2}):(\d{2})-(\d{2}):(\d{2}) (\d+\.\d{2})/(\d+)$")


class TariffTier(NamedTuple):
    """One time-of-day tier of a tiered garage (ruling R-4b-10b): a started unit of ``unit_min`` minutes that begins
    between ``start_min`` (inclusive) and ``end_min`` (exclusive), both minutes after midnight, costs ``eur`` EUR. A tier
    whose end is not after its start crosses midnight (23:00-08:00 is 1380 to 480); an end of 1440 is midnight itself."""

    start_min: int
    end_min: int
    unit_min: int
    eur: float

    @property
    def crosses_midnight(self) -> bool:
        return self.end_min <= self.start_min

    def intervals(self) -> list:
        """The minutes of the day the tier covers as ``(start, end)`` pairs inside 0 to 1440 (two for a crossing tier)."""
        if self.crosses_midnight:
            return [(self.start_min, MINUTES_PER_DAY), (0, self.end_min)]
        return [(self.start_min, self.end_min)]


def _clock_text(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def format_tariff_tiers(tiers) -> str:
    """The canonical text of ``tiers`` (``HH:MM-HH:MM <eur>/<unit_min>``, two decimals, joined by ``"; "``); the inverse of
    :func:`parse_tariff_tiers` for a valid list."""
    return TIER_SEPARATOR.join(f"{_clock_text(tier.start_min)}-{_clock_text(tier.end_min)} {tier.eur:.2f}/{tier.unit_min}"
                               for tier in tiers)


def parse_tariff_tiers(text) -> list:
    """The tiers of a ``tariff_tiers`` text, validated; raises ``ValueError`` naming the first problem.

    Format: ``HH:MM-HH:MM <eur>/<unit_min>`` per tier, tiers separated by ``"; "`` (for example ``"08:00-10:00 0.30/30;
    10:00-18:00 0.60/30; 18:00-23:00 0.30/30; 23:00-08:00 0.10/30"``): the clock times are 24-hour times with two digits (an
    end of 24:00 is midnight), ``<eur>`` is the price in EUR of one started unit with two decimals and ``<unit_min>`` the
    length of a unit in whole minutes. Rules: every tier has a positive length, price and unit; the tiers are listed in
    strictly ascending order of their start (one canonical text per tariff), do not overlap (a tier that crosses midnight
    covers the end and the beginning of the day) and share one unit length (the units of a stay are counted from its
    arrival, ASSUMPTION P6). A time of day outside every tier is free.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the tiers are empty")
    tiers = []
    for part in text.split(TIER_SEPARATOR):
        match = _TIER_PATTERN.match(part)
        if match is None:
            raise ValueError(f"tier {part!r} is not 'HH:MM-HH:MM <eur>/<unit_min>' with two decimals of EUR and a whole unit "
                             f"in minutes, tiers separated by {TIER_SEPARATOR!r}")
        start_hour, start_minute, end_hour, end_minute = (int(match.group(index)) for index in range(1, 5))
        if start_minute > 59 or start_hour > 23:
            raise ValueError(f"tier {part!r}: the start {match.group(1)}:{match.group(2)} is no clock time of a day")
        if end_minute > 59 or end_hour > 24 or (end_hour == 24 and end_minute != 0):
            raise ValueError(f"tier {part!r}: the end {match.group(3)}:{match.group(4)} is no clock time (24:00 is midnight)")
        start, end = start_hour * 60 + start_minute, end_hour * 60 + end_minute
        eur, unit = float(match.group(5)), int(match.group(6))
        if start == end:
            raise ValueError(f"tier {part!r} has no length: its start and end are equal (the whole day is 00:00-24:00)")
        if not eur > 0:
            raise ValueError(f"tier {part!r}: the price must be positive (a free time of day lies outside every tier)")
        if unit <= 0:
            raise ValueError(f"tier {part!r}: the unit must be a positive number of minutes")
        tiers.append(TariffTier(start, end, unit, eur))
    if len({tier.unit_min for tier in tiers}) != 1:
        raise ValueError(f"the tiers use different units {sorted({tier.unit_min for tier in tiers})} min: the units of a stay "
                         "are counted from its arrival, so a garage has one unit length (ASSUMPTION P6)")
    starts = [tier.start_min for tier in tiers]
    if any(later <= earlier for earlier, later in zip(starts, starts[1:])):
        raise ValueError(f"the tiers must be listed in strictly ascending order of their start, found {text!r}")
    covered = sorted(interval for tier in tiers for interval in tier.intervals())
    for (_, earlier_end), (later_start, _) in zip(covered, covered[1:]):
        if later_start < earlier_end:
            raise ValueError(f"tiers overlap at {_clock_text(later_start)}: {text!r}")
    return tiers


def tier_coverage_minutes(tiers) -> int:
    """The minutes of the day that lie inside a tier (1440 = every time of day is charged, the rest is free)."""
    return sum(end - start for tier in tiers for start, end in tier.intervals())


# --------------------------------------------------------------------------- duration bands (ruling R-4b-11)

BAND_SEPARATOR = "; "
BAND_KINDS = ("free", "total", "increment")
_BAND_PATTERN = re.compile(r"^(\d+)-(\d*) (?:(free)|total (\d+\.\d{2})|(\d+\.\d{2})/(\d+))$")


class DurationBand(NamedTuple):
    """One band of a banded garage (ruling R-4b-11): it covers the elapsed durations ``from_min < d <= to_min`` (minutes;
    ``to_min`` is None for the open-ended last band) and is of one ``kind``:

    * ``free``: the stay costs 0 while d is in the band (``eur`` 0.0, no unit);
    * ``total``: the stay costs ``eur`` EUR while d is in the band, an absolute price and no addition;
    * ``increment``: the stay costs the price reached at the band's start plus ``eur`` EUR for every started unit of
      ``unit_min`` minutes counted from the band's start (ASSUMPTION P8)."""

    from_min: int
    to_min: Optional[int]
    kind: str
    eur: float
    unit_min: Optional[int]

    @property
    def is_open_ended(self) -> bool:
        return self.to_min is None


def _band_text(band: DurationBand) -> str:
    end = "" if band.to_min is None else str(band.to_min)
    if band.kind == "free":
        what = "free"
    elif band.kind == "total":
        what = f"total {band.eur:.2f}"
    else:
        what = f"{band.eur:.2f}/{band.unit_min}"
    return f"{band.from_min}-{end} {what}"


def format_duration_bands(bands) -> str:
    """The canonical text of ``bands`` (``<from_min>-<to_min> free``, ``... total <eur>`` or ``... <eur>/<unit_min>``, two
    decimals, joined by ``"; "``, an empty end for the open last band); the inverse of :func:`parse_duration_bands` for a
    valid list."""
    return BAND_SEPARATOR.join(_band_text(band) for band in bands)


def _eur_cents(eur: float) -> int:
    return int(round(eur * 100))


def parse_duration_bands(text) -> list:
    """The bands of a ``tariff_duration_bands`` text, validated; raises ``ValueError`` naming the first problem.

    Grammar (ASCII, human readable): bands are separated by ``"; "`` and written ``<from_min>-<to_min> <kind>`` with whole
    minutes, ``<kind>`` one of ``free``, ``total <eur>`` (the stay costs this amount) or ``<eur>/<unit_min>`` (this amount
    per started unit of ``unit_min`` minutes, counted from the band's start, added to the price reached at the band's
    start), ``<eur>`` with two decimals; the end of the open last band is empty (``"420- 5.00/60"``). Example: ``"0-20 free;
    20-120 total 1.00; 120-240 0.50/60; 240-420 1.50/60; 420- 5.00/60"``. Semantics: a band covers ``from < d <= to`` over the
    elapsed duration d in minutes, so a stay of exactly ``to`` minutes still belongs to the band (ASSUMPTION P8, see
    :func:`duration_band_price_eur`). Rules: the first band starts at 0; every band has a positive length; the bands are
    contiguous and ascending with no gap or overlap; only the last band may be open-ended; amounts and units are positive;
    the price never falls as the stay gets longer (a total band is not below the price reached at its start, and a free
    band can only be the first band). One canonical text per schedule: a published cap or 24-hour price is the day cap
    column, not a band.

    The rules "the price never falls" and "free only as the first band" are STRICTER than spec Amendment E11, which
    states only the three band kinds and the cumulative reading (P8): a published schedule whose price falls with the
    duration, or that is free again later, is rejected here although E11 would let it be written. No published
    schedule of the dataset needs it; a source that does needs the rule relaxed (and the pricing code checked) first."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("the bands are empty")
    bands = []
    reached_cents = 0
    parts = text.split(BAND_SEPARATOR)
    for position, part in enumerate(parts):
        match = _BAND_PATTERN.match(part)
        if match is None:
            raise ValueError(f"band {part!r} is not '<from_min>-<to_min> free', '<from_min>-<to_min> total <eur>' or "
                             f"'<from_min>-<to_min> <eur>/<unit_min>' (whole minutes, EUR with two decimals, the end empty "
                             f"for the open last band), bands separated by {BAND_SEPARATOR!r}")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else None
        if match.group(3):
            band = DurationBand(start, end, "free", 0.0, None)
        elif match.group(4):
            band = DurationBand(start, end, "total", float(match.group(4)), None)
        else:
            band = DurationBand(start, end, "increment", float(match.group(5)), int(match.group(6)))
        if band.kind != "free" and not band.eur > 0:
            raise ValueError(f"band {part!r}: the amount must be positive (a free stretch is a 'free' band)")
        if band.kind == "increment" and band.unit_min <= 0:
            raise ValueError(f"band {part!r}: the unit must be a positive number of minutes")
        if band.to_min is not None and band.to_min <= band.from_min:
            raise ValueError(f"band {part!r} has no length: it must end after it starts")
        if band.to_min is None and position != len(parts) - 1:
            raise ValueError(f"band {part!r}: only the last band may be open-ended")
        if position == 0:
            if band.from_min != 0:
                raise ValueError(f"band {part!r}: the first band must start at 0 min")
        elif band.from_min != bands[-1].to_min:
            raise ValueError(f"gap or overlap: band {part!r} starts at {band.from_min} min but the band before ends at "
                             f"{bands[-1].to_min} min")
        if band.kind == "free" and position > 0:
            raise ValueError(f"band {part!r}: a free band can only be the first band (the price never falls as the stay "
                             "gets longer)")
        if band.kind == "total" and _eur_cents(band.eur) < reached_cents:
            raise ValueError(f"band {part!r}: the total {band.eur:.2f} is below the price {reached_cents / 100:.2f} reached "
                             "at its start (the price never falls as the stay gets longer)")
        bands.append(band)
        if band.to_min is not None:
            reached_cents = _band_price_cents(band, band.to_min, reached_cents)
    return bands


def bands_have_price(bands) -> bool:
    """Whether a parsed schedule has a band that costs something (a total or an increment band)."""
    return any(band.kind != "free" for band in bands)


def is_free_schedule(bands) -> bool:
    """Whether a parsed schedule is the free schedule ``"0- free"``: one open-ended free band (a car park that is free for
    every stay, spec E14)."""
    return len(bands) == 1 and bands[0].kind == "free" and bands[0].to_min is None


def is_grace_period(bands) -> bool:
    """Whether a parsed schedule is the grace period of a tiered garage: one CLOSED free band (``"0-30 free"``); a stay not
    longer than the band costs 0 and a longer stay is priced by the tiers (ASSUMPTION P10, Task 4b3)."""
    return len(bands) == 1 and bands[0].kind == "free" and bands[0].to_min is not None


def _band_price_cents(band: DurationBand, duration_min: float, reached_cents: int) -> int:
    """The price in whole cents of a stay of ``duration_min`` minutes inside ``band``, whose price at its start is
    ``reached_cents`` (integer cents: published amounts are whole cents, so the arithmetic is exact)."""
    if band.kind == "free":
        return 0
    if band.kind == "total":
        return _eur_cents(band.eur)
    started_units = math.ceil((duration_min - band.from_min) / band.unit_min)
    return reached_cents + _eur_cents(band.eur) * started_units


def duration_band_price_eur(bands, duration_min: float, daily_cap_eur: Optional[float] = None) -> float:
    """The price in EUR of a stay of ``duration_min`` minutes under a parsed duration schedule (ASSUMPTION P8): the reference
    evaluation that the tests use and that the pricing of the garage options (``braunschweig.parking.cost``) calls.

    The band with ``from_min < d <= to_min`` applies (a stay of exactly ``to_min`` still belongs to the band, one minute more
    to the next): ``free`` costs 0, ``total`` costs its amount (absolute) and ``increment`` costs the price reached at the
    band's start plus its amount for every started unit counted from the band's start, ``ceil((d - from) / unit)`` units.
    The price reached at a band's start is the price at its predecessor's end. ``daily_cap_eur`` (the published cap or
    24-hour price; None = none) limits the result: the smaller of both. A stay of no length costs 0. No rounding beyond the
    published amounts (whole cents, summed exactly). Raises ``ValueError`` for a negative duration, a non-positive cap and a
    stay beyond a closed schedule (a schedule that ends at ``to_min`` says nothing about longer stays, so none is guessed).
    No side effects; the day boundary of a cap and the fee window are the caller's concern. A NaN or infinite duration raises
    ``ValueError`` as well (NaN fails every comparison below and would be priced as a free stay)."""
    if not math.isfinite(duration_min):
        raise ValueError(f"the duration must be a finite number of minutes, found {duration_min}")
    if duration_min < 0:
        raise ValueError(f"the duration must not be negative, found {duration_min} min")
    if daily_cap_eur is not None and not daily_cap_eur > 0:
        raise ValueError(f"the day cap must be positive, found {daily_cap_eur}")
    cents = 0
    if duration_min > 0:
        reached_cents = 0
        for band in bands:
            if band.to_min is None or duration_min <= band.to_min:
                cents = _band_price_cents(band, duration_min, reached_cents)
                break
            reached_cents = _band_price_cents(band, band.to_min, reached_cents)
        else:
            raise ValueError(f"the duration {duration_min} min is beyond the last band, which ends at {bands[-1].to_min} min: "
                             "the schedule states no price for a longer stay")
    if daily_cap_eur is not None:
        cents = min(cents, _eur_cents(daily_cap_eur))
    return cents / 100.0


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
    priced = summary["priced"]
    log.info("[parking-garages] loaded %d garages from %s: priced %d/%d (%.1f %%), not priced %d (%s); priced rows resting "
             "on an assumption: %s; at least one assumption %d/%d (%.1f %%), P4 or P5 %d/%d (%.1f %%); tiered %d/%d; "
             "banded %d/%d; free %d/%d; by facility kind %s; monthly product on %d", summary["listed"], path, priced,
             summary["listed"],
             100.0 * priced / max(summary["listed"], 1), summary["not_priced"],
             ", ".join(f"{reason} {count}" for reason, count in summary["not_priced_by_reason"].items()) or "none",
             ", ".join(f"{name} {count}/{priced}" for name, count in summary["priced_by_assumption"].items()) or "none",
             summary["priced_with_assumption"], priced, 100.0 * summary["priced_with_assumption"] / max(priced, 1),
             summary["priced_with_p4_or_p5"], priced, 100.0 * summary["priced_with_p4_or_p5"] / max(priced, 1),
             summary["priced_tiered"], priced, summary["priced_banded"], priced, summary["priced_free"], priced,
             ", ".join(f"{kind} {counts['listed']}" for kind, counts in summary["by_facility_kind"].items()) or "none",
             summary["with_monthly_product"])
    for label, count in (("at least one assumption", summary["priced_with_assumption"]),
                         ("ASSUMPTION P4 or P5 (a stated rounding or stated charging times replaced by an assumption)",
                          summary["priced_with_p4_or_p5"])):
        if priced and count / priced > UNION_WARNING_SHARE:
            log.warning("[parking-garages] %d of %d priced garages (%.1f %%, above %.0f %%) rest on %s: the published "
                        "structure alone covers a minority of the priced garages", count, priced, 100.0 * count / priced,
                        100.0 * UNION_WARNING_SHARE, label)
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
    ``source_date``, one to three distinct hexadecimal SHA-256 values of the packages (the regional package, and the supplement
    and follow-up packages where they touch the row, specs E12 and E13), ``notes`` set. Tariff (spec Amendment A6, rulings
    R-4b-10b and R-4b-11): a garage is priced in exactly one form, the single-window core (hourly rate, billing unit, fee
    start, fee end; set completely or not at all), ``tariff_tiers`` (a text that :func:`parse_tariff_tiers` accepts, with the
    four core columns empty) or ``tariff_duration_bands`` (a text that :func:`parse_duration_bands` accepts, with the rate and the
    billing unit empty, the fee window set completely, no first period and no tiers), or the grace period of a tiered garage
    (:func:`is_grace_period`: ``tariff_tiers`` with ONE closed free band, no fee window and no first period, ASSUMPTIONS P6
    and P10); ``tariff_duration_bands`` ``"0- free"`` is the free schedule (:func:`is_free_schedule`, a car park free for
    every stay: no P8, no P10, P12 where it is a municipal default); the day cap and the first-period pair
    need one form, and the pair is set together; the clock window of the first period (ruling R-4b-12) is set together,
    only with a first period and satisfies 0 <= start < end <= 24; amounts are positive whole cents, minutes positive, the
    fee window satisfies 0 <= start < end <= 24, and the day cap is not below the first period. Status: ``priced`` is
    exactly "one tariff form is set"; a priced row names its ``tariff_rule_ids`` and no reason, an unpriced row carries no
    tariff value and one ``not_priced_reason`` of ``NOT_PRICED_REASONS`` and no assumption; every id of ``assumptions`` is
    one of ``ASSUMPTIONS`` and is named as ``ASSUMPTION <id>`` in the notes, and a row rests on ASSUMPTION P6 exactly when
    it has tiers and on ASSUMPTION P8 exactly when it has a schedule with a priced band and no tiers; ASSUMPTION P10 (a grace
    period) needs a free first band that something follows (a priced band or the tiers) and a tiered garage with a grace
    period lists it; ASSUMPTION P12 (a municipal free default) needs the free schedule.
    Monthly product: ``monthly_eur`` is a positive whole-cent amount with its ``monthly_source_url`` and ``monthly_product``,
    and neither text without the amount. Capacity: a positive whole number with its ``capacity_scope``.
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
        kind = row["facility_kind"]
        if zones._is_set(kind) and kind not in FACILITY_KINDS:
            problem("facility_kind", f"{kind!r} is not one of {sorted(FACILITY_KINDS)}")
        for column in ("source_url", "geometry_source_url"):
            if zones._is_set(row[column]) and not _URL_PATTERN.match(str(row[column])):
                problem(column, f"{row[column]!r} is not an http(s) URL")
        if zones._is_set(row["source_date"]) and not zones._is_iso_date(row["source_date"]):
            problem("source_date", f"{row['source_date']!r} is not an ISO date YYYY-MM-DD")
        if zones._is_set(row["package_sha256"]):
            hashes = _split(row["package_sha256"])
            for value in hashes:
                if not _SHA256_PATTERN.match(value):
                    problem("package_sha256", f"{value!r} is not a lower-case hexadecimal SHA-256")
            if len(set(hashes)) != len(hashes):
                problem("package_sha256", f"{row['package_sha256']!r} lists a package SHA-256 twice")
            if len(hashes) > MAXIMUM_PACKAGE_HASHES:
                problem("package_sha256", f"{row['package_sha256']!r}: at most four package SHA-256 values (the regional "
                                          "package and the three supplement packages), separated by ';'")
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
        has_tiers = zones._is_set(row["tariff_tiers"])
        has_bands = zones._is_set(row["tariff_duration_bands"])
        if has_tiers:
            try:
                parse_tariff_tiers(row["tariff_tiers"])
            except ValueError as error:
                problem("tariff_tiers", str(error))
        bands = None
        if has_bands:
            try:
                bands = parse_duration_bands(row["tariff_duration_bands"])
            except ValueError as error:
                problem("tariff_duration_bands", str(error))
        if has_tiers and has_bands and bands is not None and not is_grace_period(bands):
            problem("tariff_duration_bands", "a tiered garage carries duration bands only as a grace period (one closed free "
                                             "band, ASSUMPTION P10); otherwise the tiered and the banded form exclude each "
                                             "other (one tariff structure per garage)")
        if has_bands and not has_tiers:
            # the fee window applies unchanged, so both hours are set; the rate and the unit belong to the single window
            single_rate = [column for column in ("garage_hourly_rate_eur", "garage_billing_unit_min")
                           if zones._is_set(row[column])]
            if single_rate:
                problem(single_rate[0], f"a banded garage leaves the rate and the billing unit empty (the bands are the "
                                        f"tariff; the forms exclude each other); this row sets {single_rate}")
            for column in ("garage_fee_start_h", "garage_fee_end_h"):
                if column not in core_set:
                    problem(column, "a banded garage needs its fee window (both hours; 0 and 24 under ASSUMPTION P5 where "
                                    "no preferred rule states charging times)")
        elif has_tiers:
            if core_set:
                problem(core_set[0], f"a tiered garage leaves the single-window core {list(zones.GARAGE_CORE_COLUMNS)} empty "
                                     f"(the two forms exclude each other); this row sets {core_set}")
        elif core_set and len(core_set) < len(zones.GARAGE_CORE_COLUMNS):
            for column in zones.GARAGE_CORE_COLUMNS:
                if column not in core_set:
                    problem(column, f"the garage core {list(zones.GARAGE_CORE_COLUMNS)} is set completely or not at all; "
                                    f"this row sets {core_set}")
        has_core = not has_tiers and not has_bands and len(core_set) == len(zones.GARAGE_CORE_COLUMNS)
        has_tariff = has_core or has_tiers or has_bands
        pair = [zones._is_set(row[column]) for column in zones.GARAGE_FIRST_PERIOD_COLUMNS]
        if pair[0] != pair[1]:
            problem(zones.GARAGE_FIRST_PERIOD_COLUMNS[1 if pair[0] else 0],
                    f"{zones.GARAGE_FIRST_PERIOD_COLUMNS[0]} and {zones.GARAGE_FIRST_PERIOD_COLUMNS[1]} are set together "
                    "or not at all")
        if has_bands and any(pair):
            problem(zones.GARAGE_FIRST_PERIOD_COLUMNS[0], "a garage with duration bands has no first period (its first band "
                                                          "is the first period; the forms exclude each other)")
        # the clock window of the first period (ruling R-4b-12): both hours or neither, only with a first period, and the
        # same 0 <= start < end <= 24 rule as the fee window
        first_window = [zones._is_set(row[column]) for column in FIRST_PERIOD_WINDOW_COLUMNS]
        if first_window[0] != first_window[1]:
            problem(FIRST_PERIOD_WINDOW_COLUMNS[1 if first_window[0] else 0],
                    f"{FIRST_PERIOD_WINDOW_COLUMNS[0]} and {FIRST_PERIOD_WINDOW_COLUMNS[1]} are set together or not at all")
        elif first_window[0]:
            first_start, first_end = row[FIRST_PERIOD_WINDOW_COLUMNS[0]], row[FIRST_PERIOD_WINDOW_COLUMNS[1]]
            if not (0.0 <= first_start < first_end <= 24.0):
                problem(FIRST_PERIOD_WINDOW_COLUMNS[0], f"first-period window {first_start} .. {first_end} h must satisfy "
                                                        "0 <= garage_first_period_start_h < garage_first_period_end_h <= 24")
            if not any(pair):
                problem(FIRST_PERIOD_WINDOW_COLUMNS[0], "a first-period window needs a first period (both "
                                                        "garage_first_period_min and garage_first_period_eur); it is empty "
                                                        "where the source ties the first period to no window")
        if not has_tariff and (zones._is_set(row["garage_daily_cap_eur"]) or any(pair)):
            problem("garage_daily_cap_eur" if zones._is_set(row["garage_daily_cap_eur"]) else
                    zones.GARAGE_FIRST_PERIOD_COLUMNS[0],
                    "a day cap or a first period needs the complete garage core, tariff_tiers or tariff_duration_bands; it "
                    "is never priced without one")
        if (zones._is_set(row["garage_daily_cap_eur"]) and zones._is_set(row["garage_first_period_eur"])
                and row["garage_daily_cap_eur"] < row["garage_first_period_eur"]):
            problem("garage_daily_cap_eur", f"the day cap {row['garage_daily_cap_eur']} is below the first period "
                                            f"{row['garage_first_period_eur']}: the cap would act before the first period ends")
        # --- status
        priced = row["priced"]
        reason = row["not_priced_reason"]
        assumptions = _split(row["assumptions"])
        if bool(priced) != has_tariff:
            problem("priced", f"priced is {bool(priced)} but the garage tariff (the complete core, tariff_tiers or "
                              f"tariff_duration_bands) is {'set' if has_tariff else 'not set'}: a garage is priced exactly "
                              "when one tariff form is set")
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
            tariff_values = [column for column in TARIFF_COLUMNS + FIRST_PERIOD_WINDOW_COLUMNS + TIER_COLUMNS + BAND_COLUMNS
                             if zones._is_set(row[column])]
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
        if priced and has_tiers and "P6" not in assumptions:
            problem("assumptions", "a tiered garage rests on ASSUMPTION P6 (how a stay is priced from the tiers) and lists it")
        if priced and not has_tiers and "P6" in assumptions:
            problem("assumptions", "ASSUMPTION P6 prices a stay from tariff_tiers, but this garage has none")
        # a schedule with a priced band, read cumulatively (P8); the free schedule and the grace period of a tiered garage
        # have none. A text that does not parse is reported above and counts as a schedule here.
        schedule = has_bands and not has_tiers and (bands is None or bands_have_price(bands))
        if priced and schedule and "P8" not in assumptions:
            problem("assumptions", "a banded garage rests on ASSUMPTION P8 (how a stay is priced from the bands) and lists it")
        if priced and not has_bands and "P8" in assumptions:
            problem("assumptions", "ASSUMPTION P8 prices a stay from tariff_duration_bands, but this garage has none")
        elif priced and has_bands and not schedule and "P8" in assumptions:
            problem("assumptions", "ASSUMPTION P8 prices a stay from a duration schedule with a priced band, but the bands of "
                                   "this garage are the free schedule or the grace period of a tiered garage")
        if priced and has_tiers and has_bands and bands is not None and is_grace_period(bands) and "P10" not in assumptions:
            problem("assumptions", "a tiered garage with a grace period rests on ASSUMPTION P10 and lists it")
        if "P10" in assumptions:
            # the grace period is the free first band that something follows: without bands, with a first band that is not
            # free or with a free schedule (nothing follows), there is none
            first_kind = bands[0].kind if bands else None
            if first_kind != "free":
                suffix = " has no duration bands" if not has_bands else "'s first band is not free"
                problem("assumptions", "ASSUMPTION P10 reads the free first band of tariff_duration_bands as a grace period, "
                                       f"but this garage{suffix}")
            elif bands is not None and is_free_schedule(bands):
                problem("assumptions", "ASSUMPTION P10 reads the free first band of tariff_duration_bands as a grace period, "
                                       "but nothing follows the free band of this garage (a car park that is free for every stay)")
        if "P12" in assumptions and not (bands is not None and is_free_schedule(bands) and not has_tiers):
            problem("assumptions", "ASSUMPTION P12 frees a car park: it belongs to the free schedule '0- free' only")
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
    (``by_municipality``: ags -> {listed, priced, not_priced}), per facility kind (``by_facility_kind``: the same counts for
    the garages and the surface lots), the priced garages per assumption
    (``priced_by_assumption``, ids that no priced garage uses are absent), the union rates ``priced_with_assumption``
    (priced garages resting on at least one assumption) and ``priced_with_p4_or_p5`` (on a stated rounding or stated
    charging times replaced by an assumption), the priced garages in the tiered form (``priced_tiered``, the tiered garages
    with a grace period included), in the banded form (``priced_banded``: a schedule without tiers) and with the free
    schedule (``priced_free``) and the garages with a monthly product. Plain numbers and dicts, sorted, so a caller can print
    or compare them."""
    priced = frame["priced"].astype(bool)
    reasons = frame.loc[~priced, "not_priced_reason"].fillna("").astype(str)
    by_reason = {reason: int(count) for reason, count in reasons.value_counts().sort_index().items()}
    towns = {}
    for ags, group in frame.groupby("municipality_ags"):
        flags = group["priced"].astype(bool)
        towns[str(ags)] = {"listed": int(len(group)), "priced": int(flags.sum()), "not_priced": int((~flags).sum())}
    kinds = {}
    for kind, group in frame.groupby("facility_kind"):
        flags = group["priced"].astype(bool)
        kinds[str(kind)] = {"listed": int(len(group)), "priced": int(flags.sum()), "not_priced": int((~flags).sum())}
    counts = {}
    with_assumption = with_p4_or_p5 = banded = free = 0
    for _, row in frame[priced].iterrows():
        if zones._is_set(row["tariff_duration_bands"]) and not zones._is_set(row["tariff_tiers"]):
            try:
                is_free = is_free_schedule(parse_duration_bands(row["tariff_duration_bands"]))
            except ValueError:  # an invalid text is reported by validate_garages; it counts as a schedule here
                is_free = False
            free += is_free
            banded += not is_free
        ids = _split(row["assumptions"])
        for assumption in ids:
            counts[assumption] = counts.get(assumption, 0) + 1
        with_assumption += bool(ids)
        with_p4_or_p5 += bool({"P4", "P5"} & set(ids))
    return {"listed": int(len(frame)), "priced": int(priced.sum()), "not_priced": int((~priced).sum()),
            "not_priced_by_reason": by_reason, "by_municipality": dict(sorted(towns.items())),
            "by_facility_kind": dict(sorted(kinds.items())),
            "priced_by_assumption": dict(sorted(counts.items())), "priced_with_assumption": int(with_assumption),
            "priced_with_p4_or_p5": int(with_p4_or_p5),
            "priced_tiered": int(frame.loc[priced, "tariff_tiers"].map(zones._is_set).sum()),
            "priced_banded": banded, "priced_free": free,
            "with_monthly_product": int(frame["monthly_eur"].notna().sum())}
