"""Curation QA of the parking garage dataset and of the monthly products (parking cost zones v2, issue #436).

The committed table ``parking_garages_2026_qa.csv`` is written by the curation step
``scripts/curation/parking_zones_2026/regional_garages.py`` next to the dataset ``parking_garages_2026.geojson``
(``braunschweig.parking.garages``); it is no input of any synpp stage. One row per record the curation considered:

* ``garage``: one per garage of the dataset, with its decision (priced or not priced) and, for a garage that is not priced,
  the reason code of the dataset;
* ``monthly_product``: one per monthly or 30-day product the sources publish (spec Amendment D2, ruling R-D2-a), used (it is
  the ``monthly_eur`` of a garage or the ``commuter_day_eur`` of tariff rows) or recorded and not used, with the reason;
* ``candidate``: one per car park of the city directories or per garage of the package that is NOT in the dataset, with the
  reason (ruling R-4b-4: BgA lots are zones, customer-only regimes stay out; spec Amendment G: the Braunschweig Hbf car
  parks of DB BahnPark are zones and the Wolfsburg Parkdeck Hauptbahnhof a garage (rulings R-4b-4 and R-4b-9 revoked); a car
  park open to long-term renters only is no garage option).

``validate_garage_qa`` compares the table with the dataset (every garage once, the same status and reason; every
``monthly_eur`` the amount of exactly one used product; a car park inside a zone, spec E14, names its zone and the published
hourly reference of its tariff area, and ``zone_reference_check`` compares that reference with the street rate of the zone)
and, when the tariff table is given, with ``commuter_day_eur``
(the amount of a used zone product divided by ``WORKING_DAYS_PER_MONTH``, ASSUMPTION P2, to whole cents), so a regenerated
dataset or tariff table cannot keep a stale table. Money in EUR.
"""
from __future__ import annotations

import logging
import math
import re

import pandas as pd

from braunschweig.parking import garages as pg
from braunschweig.parking import zones as pz

log = logging.getLogger(__name__)

#: The layout of ``parking_garages_2026_qa.csv`` (the curation step's header defines every column).
GARAGE_QA_COLUMNS = ("record_id", "record_type", "municipality_ags", "subject", "garage_id", "zone_ids", "decision",
                     "reason_code", "amount_eur", "count", "evidence", "note")
RECORD_TYPES = ("garage", "monthly_product", "candidate")
GARAGE_DECISIONS = ("priced", "not_priced")
MONTHLY_DECISIONS = ("used", "not_used")
CANDIDATE_DECISIONS = ("not_listed",)
#: ASSUMPTION P2 (spec lever 2, owner decision 2): a regular commuter holds the monthly product for 21 working days.
WORKING_DAYS_PER_MONTH = 21
#: Why a monthly or 30-day product is recorded and not used (``reason_code`` of a ``monthly_product`` row that is
#: ``not_used``).
MONTHLY_NOT_USED_REASONS = {
    "not_the_cheapest": "another product of the same garage is cheaper; the monthly product is the cheapest publicly "
                        "purchasable one (spec Amendment D2)",
    "restricted_customer_group": "sold to a customer group the model cannot identify (public-transport customers)",
    "no_fixed_price": "the source gives no fixed monthly amount (a discount card with a variable amount)",
    "capacity_limited_permits": "a permit offer limited to a fixed small number of places (ruling R-D2-a)",
    "no_coordinates": "the garage has no coordinates in the package and lies in no zone, so no dataset row can carry it",
    "garage_not_listed": "the garage of the product is no garage of the dataset (a candidate row gives the reason), so no "
                         "dataset row can carry the product",
    "not_monthly_or_30_day": "a product of another duration (a 7-day ticket); spec Amendment D2 takes monthly and 30-day "
                             "products",
    "outdated_source": "only an older document states the amount and no current source confirms it",
    # spec Amendment F1: the monthly status of the garages of the Braunschweig package of 2026-10-08 (the package's own words
    # are in the note of the row)
    "sold_out": "the operator states the product sold out for new contracts and publishes no price",
    "no_price": "the sources give no current public monthly price of the garage; no value is taken from another source",
    "price_on_request": "the operator's visible page offers a monthly product on request only, without a price",
    "period_unconfirmed": "an amount is published but no source states that it is a monthly amount (the billing period is "
                          "unconfirmed), so it is no monthly product",
    "excluded_by_package": "an amount that the package itself excluded from the current model (it stands only in page sections "
                           "that the operator's page hides)",
    "not_a_dataset_option": "the current published monthly product of a garage that is no option of the dataset (the package "
                            "lists it without a tariff the dataset encodes): recorded, never a garage option, and counted among "
                            "the published garage products of its municipality for ASSUMPTION P13",
    "surface_lot": "a product of an open car park that is no garage: no garage of the dataset and no garage product for "
                   "ASSUMPTION P13 (surface lots never impute)",
}
#: The reason of a ``not_used`` product row that is the published current monthly product of a recorded garage that is no
#: option of the dataset: with the used products of the dataset's garages these are the values of ASSUMPTION P13.
RECORDED_GARAGE_REASON = "not_a_dataset_option"
#: Why a car park or garage is not in the dataset (``reason_code`` of a ``candidate`` row).
CANDIDATE_REASONS = {
    "bga_zone": "a BgA car park is a zone of its own (ruling R-E1: no double role; spec Amendment I1 revises the rule for large "
                "surface lots: such a lot is a zone AND an option, so a BgA car park is a candidate of this reason only where no "
                "lot of the research package covers it)",
    "zone_street_product": "a car park under the ParkGO of its zone: the zone's street product is its tariff (ruling R-E1; no "
                           "row of the current dataset has this reason: spec Amendment I1 makes such a car park an option when it "
                           "passes the size policy, so the reason stays in the vocabulary for tables written before)",
    "station_zone": "a station car park of DB BahnPark (Contipark) that is a single paid-site zone of its own (spec Amendment "
                    "G1; the owner decision of 2026-10-08 revokes rulings R-4b-4 and R-4b-9 for the Braunschweig Hbf car parks)",
    "customer_regime": "a hospital, shopping-centre or airport short-stay regime (ruling R-4b-4)",
    "no_coordinates": "the package gives the garage no coordinates, and no geometry is invented",
    "no_published_tariff": "the package lists the car parks without any published tariff (spec Amendment E1 lists large "
                           "public car parks with a published tariff)",
    "outside_source_list": "not in the towns and sources that spec Amendment E1 names for the dataset",
    "dauerparker_only": "currently open to long-term renters only, a closed group: no garage option (the free or contract "
                        "parking of such a group is covered by the free-parking share of the model)",
    "user_group_only": "a car park or a part of one that is reserved for a user group (disabled parking), so no option of the "
                       "general public",
    # spec Amendment I2 to I5 (Task 4j): the records of the research package of the large surface lots that are no option
    "lot_below_threshold": "a public surface lot whose capacity for the size threshold is below it (spec Amendment I2, ASSUMPTION "
                           "L50: 50 spaces; 30 and 100 are sensitivities)",
    "lot_capacity_unknown": "a surface lot whose capacity the package does not state or estimate (unknown, never 0): the size "
                            "policy cannot select it",
    "lot_access_not_public": "access that is not evidenced as general public use: customers, visitors, events, monthly-only, "
                             "permit-only, a closed group or unknown in the package (spec Amendment I2)",
    "lot_not_open": "closed, planned, under construction or excluded from the current inventory",
    "lot_not_surface_lot": "a record that is no single surface lot (a roadside parking stretch, a site group or an unresolved "
                           "street or lot)",
    "lot_alias": "an alias or a subpoint of another record of the same site: one site is one row (spec Amendment I2)",
    "lot_unconfirmed_candidate": "a candidate that the package keeps unconfirmed (secondary evidence only): no current primary "
                                 "confirmation of its public use",
    "lot_mixed_tariff_unseparated": "paid and free subareas without separate verified subarea geometry: no blanket tariff, and "
                                    "the cheapest subarea product is never taken for the whole site (spec Amendment I3)",
    "lot_tariff_not_expressible": "a documented tariff component that the schedule grammar of the dataset cannot express: the "
                                  "row is stopped with the named error and never approximated (spec Amendment I3; a grammar or "
                                  "Java extension is a separate task)",
    "lot_tariff_unknown": "a public lot of at least the threshold whose tariff is not stated and for which ASSUMPTION P14 has "
                          "fewer than two priced lots in its zone and in its municipality",
    "lot_no_position": "a site without a position in the package; none is invented",
    "lot_free_evidence_missing": "a Wolfsburg lot that is free only on a per-site source (spec Amendment I5a, ASSUMPTION P12 "
                                 "withdrawn) whose evidence page is not archived: never free without a source",
}
_RECORD_ID = re.compile(r"^[a-z0-9_]+$")
#: A lot that carries the street product of its zone (spec Amendment I1) names the table row it carries in its ``evidence`` as
#: ``parking_tariffs_2026.csv:<zone_id>``; ``zone_product_lot_check`` compares the dataset row with that table row.
ZONE_PRODUCT_EVIDENCE = re.compile(r"parking_tariffs_2026\.csv:([a-z0-9_]+)")
#: The columns of the zone's table row that a lot inside the zone is compared with (spec Amendment I1, rulings R-4j-6 and R-4j-7):
#: the street product (rate per hour, billing unit, the free threshold that a lot carries as its grace band, daily cap, fee window,
#: and the maximum stay with its long-stay product, assumption M1) and the commuter product per working day. A lot that carries the
#: street product of its zone equals the row in all of them; ``lot_street_equivalent`` reads the same quantities from a dataset row
#: (from its columns or, for the banded form, from its bands) and the differences are named in the terms of these columns.
ZONE_PRODUCT_COLUMNS = ("hourly_rate_eur", "billing_unit_min", "free_if_stay_at_most_min", "daily_cap_eur", "fee_start_h",
                        "fee_end_h", "max_stay_min", "long_stay_product_eur", "commuter_day_eur")


def load_garage_qa(path) -> pd.DataFrame:
    """Load the garage QA table (``#`` lines skipped, text columns ``GARAGE_QA_COLUMNS``, empty = "")."""
    qa = pz._read_documented_csv(path)
    pz._check_columns(qa, GARAGE_QA_COLUMNS, str(path))
    qa = qa[list(GARAGE_QA_COLUMNS)].apply(lambda column: column.str.strip())
    log.info("[parking-garages] loaded %d QA rows from %s", len(qa), path)
    return qa


def _amount(text: str):
    """The EUR amount of a cell, None when empty, NaN when not a number."""
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return float("nan")


def validate_garage_qa(qa: pd.DataFrame, garages: pd.DataFrame, tariffs: pd.DataFrame | None = None) -> None:
    """Check the QA table against itself, the dataset and (when given) the tariff table; raise ``ValueError`` listing
    every violation.

    Table: the columns ``GARAGE_QA_COLUMNS``, unique ``record_id``, ``record_type`` in ``RECORD_TYPES``, the decision of
    its type, a reason code of its vocabulary exactly where one is due (a priced garage and a used product have none), a
    positive whole ``count`` and a non-negative ``amount_eur`` where one is stated (a used product always states one).
    Dataset: one ``garage`` row per garage with its municipality, ``priced`` flag and reason code; a garage's
    ``monthly_eur`` is the ``amount_eur`` of exactly one used product of that garage and a used product of a garage needs
    the ``monthly_eur``. Tariff table: the ``zone_ids`` of a used zone product are the rows whose ``commuter_day_eur`` is
    the amount divided by ``WORKING_DAYS_PER_MONTH`` to whole cents, and every ``commuter_day_eur`` belongs to such a row.
    """
    pz._check_columns(qa, GARAGE_QA_COLUMNS, "garage QA table")
    problems = []
    if qa.empty:
        problems.append("no rows")
    duplicated = sorted(set(qa["record_id"][qa["record_id"].duplicated()]))
    if duplicated:
        problems.append(f"duplicate record_id(s) {duplicated}")
    vocabularies = {"garage": (GARAGE_DECISIONS, pg.NOT_PRICED_REASONS, "not_priced"),
                    "monthly_product": (MONTHLY_DECISIONS, MONTHLY_NOT_USED_REASONS, "not_used"),
                    "candidate": (CANDIDATE_DECISIONS, CANDIDATE_REASONS, "not_listed")}
    for _, row in qa.iterrows():
        prefix = f"row {row['record_id']!r}"
        if not _RECORD_ID.match(row["record_id"]):
            problems.append(f"{prefix}: record_id uses other than lower-case ASCII letters, digits and '_'")
        if row["record_type"] not in RECORD_TYPES:
            problems.append(f"{prefix}: record_type {row['record_type']!r} is not one of {list(RECORD_TYPES)}")
            continue
        decisions, reasons, reason_decision = vocabularies[row["record_type"]]
        if row["decision"] not in decisions:
            problems.append(f"{prefix}: decision {row['decision']!r} is not one of {list(decisions)} for a "
                            f"{row['record_type']} row")
        needs_reason = row["decision"] == reason_decision
        if needs_reason and row["reason_code"] not in reasons:
            problems.append(f"{prefix}: reason_code {row['reason_code']!r} is not one of {sorted(reasons)}")
        if not needs_reason and row["reason_code"]:
            problems.append(f"{prefix}: reason_code {row['reason_code']!r} on a row without a reason ({row['decision']})")
        if not re.fullmatch(r"[1-9]\d*", row["count"]):
            problems.append(f"{prefix}: count {row['count']!r} must be a positive whole number")
        amount = _amount(row["amount_eur"])
        if amount is not None and not amount >= 0:
            problems.append(f"{prefix}: amount_eur {row['amount_eur']!r} must be a number >= 0")
        if row["record_type"] == "monthly_product" and row["decision"] == "used" and (amount is None or not amount > 0):
            problems.append(f"{prefix}: a used product states its amount_eur")
        if row["record_type"] == "monthly_product" and row["reason_code"] == RECORDED_GARAGE_REASON:
            if amount is None or not amount > 0:
                problems.append(f"{prefix}: the product of a recorded garage that is no option states its amount_eur (a value of "
                                "ASSUMPTION P13)")
            if row["garage_id"]:
                problems.append(f"{prefix}: a garage that is no option of the dataset has no garage_id")
            if not row["municipality_ags"]:
                problems.append(f"{prefix}: a recorded garage names its municipality (the values of ASSUMPTION P13 are per "
                                "municipality)")
        for column in ("subject", "evidence", "note"):
            if not row[column]:
                problems.append(f"{prefix}: {column} is empty")
        if _is_zone_car_park(row):
            if row["zone_ids"] == "":
                problems.append(f"{prefix}: a car park inside a zone names the zone (zone_ids)")
            elif pg.LIST_SEPARATOR in row["zone_ids"]:
                problems.append(f"{prefix}: a car park inside a zone names one zone, found {row['zone_ids']!r}")
            if row["amount_eur"] == "":
                problems.append(f"{prefix}: a car park inside a zone states the published hourly reference (amount_eur)")
            if tariffs is not None and row["zone_ids"] and pg.LIST_SEPARATOR not in row["zone_ids"]:
                if row["zone_ids"] not in set(tariffs["zone_id"]):
                    problems.append(f"{prefix}: zone {row['zone_ids']!r} is not in the tariff table")
    # --- the dataset
    dataset = garages.set_index("garage_id")
    garage_rows = qa[qa["record_type"] == "garage"]
    by_garage = garage_rows.set_index("garage_id") if not garage_rows.empty else pd.DataFrame(columns=qa.columns)
    for garage_id in sorted(set(garage_rows["garage_id"][garage_rows["garage_id"].duplicated()])):
        problems.append(f"garage {garage_id!r} has several garage rows")
    for garage_id in sorted(set(dataset.index) - set(by_garage.index)):
        problems.append(f"garage {garage_id!r} of the dataset has no garage row")
    for garage_id in sorted(set(by_garage.index) - set(dataset.index)):
        problems.append(f"garage row for {garage_id!r}, which the dataset does not hold")
    for garage_id in sorted(set(dataset.index) & set(by_garage.index)):
        if garage_id in garage_rows["garage_id"][garage_rows["garage_id"].duplicated()].values:
            continue
        record, row = by_garage.loc[garage_id], dataset.loc[garage_id]
        priced = bool(row["priced"])
        if (record["decision"] == "priced") != priced:
            problems.append(f"garage {garage_id!r}: the QA decision is {record['decision']!r} but the dataset says "
                            f"priced {priced}")
        expected_reason = "" if priced else str(row["not_priced_reason"])
        if record["reason_code"] != expected_reason:
            problems.append(f"garage {garage_id!r}: the QA reason_code is {record['reason_code']!r} but the dataset "
                            f"says {expected_reason!r}")
        if record["municipality_ags"] != str(row["municipality_ags"]):
            problems.append(f"garage {garage_id!r}: the QA municipality {record['municipality_ags']!r} differs from the "
                            f"dataset's {row['municipality_ags']!r}")
    used = qa[(qa["record_type"] == "monthly_product") & (qa["decision"] == "used")]
    used_by_garage = used[used["garage_id"] != ""]
    for garage_id, group in used_by_garage.groupby("garage_id"):
        if len(group) > 1:
            problems.append(f"garage {garage_id!r} has {len(group)} used monthly products; the cheapest is the one")
        if garage_id not in dataset.index:
            problems.append(f"used monthly product of {garage_id!r}, which the dataset does not hold")
            continue
        monthly = dataset.loc[garage_id, "monthly_eur"]
        amount = _amount(group.iloc[0]["amount_eur"])
        if pd.isna(monthly) or amount is None or abs(float(monthly) - amount) > 1e-9:
            problems.append(f"garage {garage_id!r}: the used monthly product is {group.iloc[0]['amount_eur']!r} EUR but "
                            f"the dataset's monthly_eur is {None if pd.isna(monthly) else float(monthly)}")
    for garage_id in sorted(dataset.index[dataset["monthly_eur"].notna()]):
        if garage_id not in set(used_by_garage["garage_id"]):
            problems.append(f"garage {garage_id!r} has a monthly_eur but no used monthly product in the QA table")
    # --- ASSUMPTION P13: the imputed monthly products are the medians of the published products of the QA table
    expected = expected_imputed_monthly(qa, garages)
    for garage_id in sorted(dataset.index):
        row = dataset.loc[garage_id]
        if row["facility_kind"] != "garage" or not bool(row["priced"]) or not pd.isna(row["monthly_eur"]):
            continue
        wanted = expected.get(str(row["municipality_ags"]))
        actual = row["monthly_imputed_eur"]
        if wanted is None:
            if not pd.isna(actual):
                problems.append(f"garage {garage_id!r}: monthly_imputed_eur is {float(actual)} but the municipality "
                                f"{row['municipality_ags']} has fewer than {pg.MINIMUM_PUBLISHED_MONTHLY_PRODUCTS} published "
                                "garage monthly products in the QA table (ASSUMPTION P13)")
        elif pd.isna(actual) or abs(float(actual) - wanted) > 1e-9:
            problems.append(f"garage {garage_id!r}: monthly_imputed_eur is {None if pd.isna(actual) else float(actual)} but the "
                            f"median of the published garage monthly products of municipality {row['municipality_ags']} in the "
                            f"QA table is {wanted:.2f} EUR (ASSUMPTION P13)")
    # --- a lot that carries the street product of its zone must equal the zone's row of the tariff table (spec Amendment I1)
    if tariffs is not None:
        for check in zone_product_lot_check(qa, garages, tariffs):
            if check["zone_id"] not in set(tariffs["zone_id"]):
                problems.append(f"garage {check['garage_id']!r}: it carries the street product of the zone {check['zone_id']!r}, "
                                "which is not in the tariff table")
            elif check["differences"]:
                problems.append(f"garage {check['garage_id']!r}: it carries the street product of the zone {check['zone_id']!r} "
                                "but differs from the table row: " + "; ".join(check["differences"]))
        # every zone that a lot's QA row names (the zone polygons that contain it, ruling R-4j-7) is a zone of the table; a
        # difference of a lot that carries no zone product is reported, never an error
        for check in zone_consistency_check(qa, garages, tariffs):
            if not check["zone_in_table"]:
                problems.append(f"garage {check['garage_id']!r}: the QA row names the zone {check['zone_id']!r}, which is not in "
                                "the tariff table")
    # --- the tariff table: commuter_day_eur = monthly amount / working days (ASSUMPTION P2)
    if tariffs is not None:
        commuter = tariffs.set_index("zone_id")["commuter_day_eur"]
        covered = set()
        for _, row in used[used["zone_ids"] != ""].iterrows():
            amount = _amount(row["amount_eur"])
            for zone_id in [zone.strip() for zone in row["zone_ids"].split(pg.LIST_SEPARATOR) if zone.strip()]:
                covered.add(zone_id)
                if zone_id not in commuter.index:
                    problems.append(f"row {row['record_id']!r}: zone {zone_id!r} is not in the tariff table")
                    continue
                expected = round((amount or 0.0) / WORKING_DAYS_PER_MONTH, 2)
                if pd.isna(commuter[zone_id]) or abs(float(commuter[zone_id]) - expected) > 1e-9:
                    problems.append(f"zone {zone_id!r}: commuter_day_eur is {commuter[zone_id]} but the used product "
                                    f"{row['record_id']!r} gives {amount} EUR / {WORKING_DAYS_PER_MONTH} working days = "
                                    f"{expected:.2f} EUR (ASSUMPTION P2)")
        for zone_id in sorted(commuter.index[commuter.notna()]):
            if zone_id not in covered:
                problems.append(f"zone {zone_id!r} has a commuter_day_eur that no used monthly product of the QA table "
                                "explains")
    if problems:
        raise ValueError("invalid parking garage QA table:\n  " + "\n  ".join(problems))


def _cell(value):
    """The value of a dataset or table cell as a float; None for an empty cell (None, NaN, NA or blank text)."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, str):
        return float(value) if value.strip() else None
    number = float(value)
    return None if math.isnan(number) else number


def _same(left, right) -> bool:
    """Two quantities are the same when both are empty or both are numbers that agree to a ten-thousandth."""
    if left is None or right is None:
        return left is None and right is None
    return abs(round(left, 4) - round(right, 4)) < 1e-9


def _text(value) -> str:
    return "none" if value is None else f"{value:g}"


def lot_street_equivalent(row) -> dict:
    """The quantities of the street product of a zone that a dataset row of a lot carries, in the terms of the zone's table
    columns (``ZONE_PRODUCT_COLUMNS``): the hourly rate and the billing unit (of the columns, or, for the banded form, of the first
    increment band), the grace period (the closed free band that opens the bands, else 0 min), the daily cap, the fee window, the
    maximum stay with its long-stay product (assumption M1: only the form that the street product itself has, the rate from 0 min
    to the maximum stay and one open total band after it; any other bands are products of the lot of their own) and the commuter
    product per working day (the monthly product over ``WORKING_DAYS_PER_MONTH`` working days, rounded half up to the cent as
    ``braunschweig.parking.cost.garage_monthly_day_cents`` does, ASSUMPTION P2). An empty quantity is None. Pure."""
    equivalent = {column: None for column in ZONE_PRODUCT_COLUMNS}
    equivalent["free_if_stay_at_most_min"] = 0
    equivalent["hourly_rate_eur"] = _cell(row["garage_hourly_rate_eur"])
    equivalent["billing_unit_min"] = _cell(row["garage_billing_unit_min"])
    equivalent["daily_cap_eur"] = _cell(row["garage_daily_cap_eur"])
    equivalent["fee_start_h"] = _cell(row["garage_fee_start_h"])
    equivalent["fee_end_h"] = _cell(row["garage_fee_end_h"])
    # ruling R-B-2: a lot that carries the street product of a zone with a maximum stay and no long-stay product holds the zone's
    # maximum in its own column (the rate form has no bands to carry it)
    if "max_stay_min" in row and _cell(row["max_stay_min"]) is not None:
        equivalent["max_stay_min"] = int(_cell(row["max_stay_min"]))
    bands_text = row["tariff_duration_bands"]
    if isinstance(bands_text, str) and bands_text.strip():
        bands = pg.parse_duration_bands(bands_text)
        if not pg.is_free_schedule(bands):
            if bands[0].kind == "free" and bands[0].to_min is not None:
                equivalent["free_if_stay_at_most_min"] = bands[0].to_min
            increments = [band for band in bands if band.kind == "increment"]
            if increments:
                equivalent["hourly_rate_eur"] = round(increments[0].eur * 60.0 / increments[0].unit_min, 4)
                equivalent["billing_unit_min"] = increments[0].unit_min
            if (len(bands) == 2 and bands[0].kind == "increment" and bands[0].from_min == 0 and bands[0].to_min is not None
                    and bands[1].kind == "total" and bands[1].to_min is None):
                equivalent["max_stay_min"], equivalent["long_stay_product_eur"] = bands[0].to_min, bands[1].eur
    monthly = _cell(row["monthly_eur"])
    if monthly is not None:
        from braunschweig.parking.cost import garage_monthly_day_cents

        equivalent["commuter_day_eur"] = garage_monthly_day_cents(int(round(monthly * 100))) / 100.0
    return equivalent


def zone_street_equivalent(zone_row) -> dict:
    """The quantities of ``ZONE_PRODUCT_COLUMNS`` of a row of the tariff table; an empty cell is None, and a zone without a free
    threshold has a grace period of 0 min (the same reading as ``lot_street_equivalent``). Pure."""
    equivalent = {column: _cell(zone_row[column]) if column in zone_row else None for column in ZONE_PRODUCT_COLUMNS}
    if equivalent["free_if_stay_at_most_min"] is None:
        equivalent["free_if_stay_at_most_min"] = 0
    return equivalent


def street_differences(lot: dict, zone: dict) -> list:
    """The differences of a lot's street equivalent from its zone's, one text per column of ``ZONE_PRODUCT_COLUMNS`` that differs:
    ``'<column> <the lot's value> against <the zone's value>'`` (``none`` for an empty quantity). Empty means equal. Pure."""
    return [f"{column} {_text(lot[column])} against {_text(zone[column])}" for column in ZONE_PRODUCT_COLUMNS
            if not _same(lot[column], zone[column])]


def zone_product_lot_check(qa: pd.DataFrame, garages: pd.DataFrame, tariffs: pd.DataFrame) -> list:
    """The consistency check of the lots that carry the street product of their zone (spec Amendment I1, owner decision A, ruling
    R-4j-6): per ``garage`` row of the QA table whose ``evidence`` names a table row (``ZONE_PRODUCT_EVIDENCE``), the street
    equivalent of the dataset row against that row of the tariff table in all ``ZONE_PRODUCT_COLUMNS`` (rate, unit, grace, cap,
    fee window, maximum stay with its long-stay product, commuter product; an empty quantity equals an empty one). A list of
    {"garage_id", "zone_id", "differences" (``street_differences``; empty = equal)}, in the order of the QA table; a garage that the
    dataset does not hold is reported by ``validate_garage_qa`` and skipped here. The zone tariff is never changed by a lot: a
    difference means the dataset or the table is stale."""
    table = tariffs.set_index("zone_id")
    dataset = garages.set_index("garage_id")
    checks = []
    for _, row in qa[qa["record_type"] == "garage"].iterrows():
        match = ZONE_PRODUCT_EVIDENCE.search(row["evidence"])
        if match is None or row["garage_id"] not in dataset.index:
            continue
        zone_id = match.group(1)
        differences = []
        if zone_id in table.index:
            differences = street_differences(lot_street_equivalent(dataset.loc[row["garage_id"]]),
                                             zone_street_equivalent(table.loc[zone_id]))
        checks.append({"garage_id": row["garage_id"], "zone_id": zone_id, "differences": differences})
    return checks


def zone_product_lot_summary(qa: pd.DataFrame, garages: pd.DataFrame, tariffs: pd.DataFrame) -> dict:
    """{"checked", "equal", "differing" (the garage ids that differ from their zone's table row)}."""
    checks = zone_product_lot_check(qa, garages, tariffs)
    return {"checked": len(checks), "equal": sum(not check["differences"] for check in checks),
            "differing": [check["garage_id"] for check in checks if check["differences"]]}


def zone_consistency_check(qa: pd.DataFrame, garages: pd.DataFrame, tariffs: pd.DataFrame) -> list:
    """The report-only pass over EVERY surface lot that lies inside a zone polygon (ruling R-4j-7): per ``garage`` row of the QA
    table that names zones (``zone_ids``, the zone polygons that contain the lot's point, written by the curation) and per zone, the
    street equivalent of the dataset row against the zone's table row. A list of {"garage_id", "zone_id", "differences"
    (``street_differences``), "zone_product" (the lot carries that zone's product: it must be equal), "zone_in_table"}. A garage
    is no lot: its tariff is its own product by construction and it is not compared. The values of a lot are never changed by
    the pass; the package component stands as documented and a difference is named."""
    table = tariffs.set_index("zone_id")
    dataset = garages.set_index("garage_id")
    checks = []
    for _, row in qa[qa["record_type"] == "garage"].iterrows():
        zones = [zone.strip() for zone in row["zone_ids"].split(pg.LIST_SEPARATOR) if zone.strip()]
        if not zones or row["garage_id"] not in dataset.index or dataset.loc[row["garage_id"], "facility_kind"] != "surface_lot":
            continue
        lot = lot_street_equivalent(dataset.loc[row["garage_id"]])
        named = ZONE_PRODUCT_EVIDENCE.search(row["evidence"])
        for zone_id in zones:
            in_table = zone_id in table.index
            checks.append({"garage_id": row["garage_id"], "zone_id": zone_id,
                           "differences": street_differences(lot, zone_street_equivalent(table.loc[zone_id])) if in_table else [],
                           "zone_product": bool(named and named.group(1) == zone_id), "zone_in_table": in_table})
    return checks


def zone_consistency_summary(qa: pd.DataFrame, garages: pd.DataFrame, tariffs: pd.DataFrame) -> dict:
    """{"checked", "equal", "differing" (the garage ids that differ from the zone they lie in)} of ``zone_consistency_check``."""
    checks = zone_consistency_check(qa, garages, tariffs)
    return {"checked": len(checks), "equal": sum(not check["differences"] for check in checks),
            "differing": [check["garage_id"] for check in checks if check["differences"]]}


def published_monthly_values(qa: pd.DataFrame, garages: pd.DataFrame) -> dict:
    """The published current garage monthly products per municipality that ASSUMPTION P13 takes the median of (spec Amendment
    F2): {municipality_ags: list of (EUR amount, record_id) pairs sorted by amount}, from the USED monthly products of the
    garages of the dataset (rows of
    ``facility_kind`` garage; a surface lot never counts) and the recorded garages that are no option of the dataset (a
    ``not_used`` product with the reason ``RECORDED_GARAGE_REASON``). Products of zones (commuter tickets), of station car parks
    and of surface lots are no garage products and never count; neither does a product that is not published as a monthly
    amount. The municipality of a garage row is the dataset's."""
    kinds = dict(zip(garages["garage_id"], garages["facility_kind"]))
    ags_of = dict(zip(garages["garage_id"], garages["municipality_ags"].astype(str)))
    values = {}
    products = qa[qa["record_type"] == "monthly_product"]
    for _, row in products.iterrows():
        amount = _amount(row["amount_eur"])
        if amount is None or not amount > 0:
            continue
        if row["decision"] == "used" and row["garage_id"] and kinds.get(row["garage_id"]) == "garage":
            values.setdefault(ags_of[row["garage_id"]], []).append((amount, row["record_id"]))
        elif row["decision"] == "not_used" and row["reason_code"] == RECORDED_GARAGE_REASON and not row["garage_id"]:
            values.setdefault(row["municipality_ags"], []).append((amount, row["record_id"]))
    return {ags: sorted(pairs) for ags, pairs in sorted(values.items())}


def expected_imputed_monthly(qa: pd.DataFrame, garages: pd.DataFrame) -> dict:
    """{municipality_ags: median in EUR} of ASSUMPTION P13 for every municipality with at least
    ``garages.MINIMUM_PUBLISHED_MONTHLY_PRODUCTS`` published garage monthly products in the QA table (``published_monthly_values``,
    ``garages.monthly_median_eur``); a municipality with fewer imputes nothing and is absent. The single implementation the
    curation step writes the imputed products with and ``validate_garage_qa`` checks them against."""
    return {ags: pg.monthly_median_eur([amount for amount, _ in pairs])
            for ags, pairs in published_monthly_values(qa, garages).items()
            if len(pairs) >= pg.MINIMUM_PUBLISHED_MONTHLY_PRODUCTS}


def _is_zone_car_park(row) -> bool:
    """A candidate row of reason ``zone_street_product`` that names a zone or states a published hourly reference: a car park
    that lies inside a zone and whose fee is the street product of that zone (a Wolfsburg car park of spec E14). The older
    candidates of the Braunschweig directory (a ParkGO car park of a zone) name neither."""
    return (row["record_type"] == "candidate" and row["reason_code"] == "zone_street_product"
            and (row["zone_ids"] != "" or row["amount_eur"] != ""))


def zone_reference_check(qa: pd.DataFrame, tariffs: pd.DataFrame) -> list:
    """The consistency check of the car parks inside a zone (spec E14, class a): per candidate row ``zone_street_product`` that
    names its zone (``zone_ids``) and the published hourly reference of its tariff area (``amount_eur``, EUR per hour), the
    reference against the street rate of the zone (``hourly_rate_eur`` of the tariff table): a list of {"record_id", "zone_id",
    "reference_eur", "zone_rate_eur", "equal" (to the cent)}. A difference is a finding, never an error; the zone tariff is not
    changed by a car park. Rows without a zone and an amount (the directory candidates) are no check."""
    table = tariffs.set_index("zone_id")
    checks = []
    for _, row in qa[qa.apply(_is_zone_car_park, axis=1)].iterrows():
        if row["zone_ids"] == "" or row["amount_eur"] == "" or pg.LIST_SEPARATOR in row["zone_ids"]:
            continue  # reported by validate_garage_qa
        if row["zone_ids"] not in table.index:
            continue  # reported by validate_garage_qa
        reference, rate = float(row["amount_eur"]), float(table.loc[row["zone_ids"], "hourly_rate_eur"])
        checks.append({"record_id": row["record_id"], "zone_id": row["zone_ids"], "reference_eur": reference,
                       "zone_rate_eur": rate, "equal": abs(reference - rate) < 0.005})
    return checks


def zone_reference_summary(qa: pd.DataFrame, tariffs: pd.DataFrame) -> dict:
    """{"checked", "equal", "differing" (the record ids whose reference differs from the street rate of their zone)}."""
    checks = zone_reference_check(qa, tariffs)
    return {"checked": len(checks), "equal": sum(check["equal"] for check in checks),
            "differing": [check["record_id"] for check in checks if not check["equal"]]}


def qa_coverage(qa: pd.DataFrame) -> dict:
    """The counts of the QA table: ``monthly_used`` (products), ``monthly_not_used`` and ``monthly_not_used_by_reason``,
    and the not listed candidates (``candidates``, sum of ``count``) with ``candidates_by_reason``. Plain numbers and
    dicts, sorted."""
    monthly = qa[qa["record_type"] == "monthly_product"]
    not_used = monthly[monthly["decision"] == "not_used"]
    candidates = qa[qa["record_type"] == "candidate"]
    counts = candidates["count"].astype(int)
    by_candidate = {reason: int(counts[candidates["reason_code"] == reason].sum())
                    for reason in sorted(set(candidates["reason_code"]))}
    return {"monthly_used": int((monthly["decision"] == "used").sum()), "monthly_not_used": int(len(not_used)),
            "monthly_not_used_by_reason": {reason: int(count)
                                           for reason, count in not_used["reason_code"].value_counts().sort_index().items()},
            "candidates": int(counts.sum()), "candidates_by_reason": by_candidate}
