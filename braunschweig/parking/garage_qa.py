"""Curation QA of the parking garage dataset and of the monthly products (parking cost zones v2, issue #436).

The committed table ``parking_garages_2026_qa.csv`` is written by the curation step
``scripts/curation/parking_zones_2026/regional_garages.py`` next to the dataset ``parking_garages_2026.geojson``
(``braunschweig.parking.garages``); it is no input of any synpp stage. One row per record the curation considered:

* ``garage``: one per garage of the dataset, with its decision (priced or not priced) and, for a garage that is not priced,
  the reason code of the dataset;
* ``monthly_product``: one per monthly or 30-day product the sources publish (spec Amendment D2, ruling R-D2-a), used (it is
  the ``monthly_eur`` of a garage or the ``commuter_day_eur`` of tariff rows) or recorded and not used, with the reason;
* ``candidate``: one per car park of the city directories or per garage of the package that is NOT in the dataset, with the
  reason (ruling R-4b-4: BgA lots are zones, customer-only regimes stay out; ruling R-4b-9: the station car parks of DB
  BahnPark stay out in every city; a car park open to long-term renters only is no garage option).

``validate_garage_qa`` compares the table with the dataset (every garage once, the same status and reason; every
``monthly_eur`` the amount of exactly one used product; a car park inside a zone, spec E14, names its zone and the published
hourly reference of its tariff area, and ``zone_reference_check`` compares that reference with the street rate of the zone)
and, when the tariff table is given, with ``commuter_day_eur``
(the amount of a used zone product divided by ``WORKING_DAYS_PER_MONTH``, ASSUMPTION P2, to whole cents), so a regenerated
dataset or tariff table cannot keep a stale table. Money in EUR.
"""
from __future__ import annotations

import logging
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
}
#: Why a car park or garage is not in the dataset (``reason_code`` of a ``candidate`` row).
CANDIDATE_REASONS = {
    "bga_zone": "a BgA car park is a zone of its own (ruling R-E1: no double role)",
    "zone_street_product": "a car park under the ParkGO of its zone: the zone's street product is its tariff",
    "station_bahnpark": "a car park or garage at a railway station that is run by a private operator or as DB BahnPark "
                        "(Contipark), in Braunschweig and Wolfsburg alike (rulings R-4b-4 and R-4b-9)",
    "customer_regime": "a hospital, shopping-centre or airport short-stay regime (ruling R-4b-4)",
    "no_coordinates": "the package gives the garage no coordinates, and no geometry is invented",
    "no_published_tariff": "the package lists the car parks without any published tariff (spec Amendment E1 lists large "
                           "public car parks with a published tariff)",
    "outside_source_list": "not in the towns and sources that spec Amendment E1 names for the dataset",
    "dauerparker_only": "currently open to long-term renters only, a closed group: no garage option (the free or contract "
                        "parking of such a group is covered by the free-parking share of the model)",
    "user_group_only": "a car park or a part of one that is reserved for a user group (disabled parking), so no option of the "
                       "general public",
}
_RECORD_ID = re.compile(r"^[a-z0-9_]+$")


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
