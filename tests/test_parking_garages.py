"""Loader, writer and validator of the parking garage dataset (parking cost zones v2, spec Amendment E1, issue #436).

The dataset ``parking_garages_2026.geojson`` holds one point per garage with its own tariff (the garage columns of spec
Amendment A6), a monthly product where one is published and the provenance of every value. These tests pin the rules of
``braunschweig.parking.garages`` on small synthetic datasets; the committed dataset is pinned in
``tests/test_parking_regional_garages.py`` and checked by ``scripts/validate_parking_zones.py``.
"""
from __future__ import annotations

import json
import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import LineString, Point

from braunschweig.parking import garage_qa as pq
from braunschweig.parking import garages as pg
from braunschweig.parking import zones as pz

SHA = "e789623752bf508b3e31f37ed2e30fe019e171cb43274f495cfacc12924008e7"
SOURCE = "https://www.contipark.de/de/parken/braunschweig/tiefgarage-eiermarkt/"


def _row(**changes) -> dict:
    """A valid priced garage (the shape of the Braunschweig Eiermarkt row): day family 7 to 18 h, first hour 0.60 EUR,
    1.20 EUR per started hour, day cap 9.60 EUR, no monthly product."""
    row = {
        "garage_id": "bs_eiermarkt", "package_facility_id": "BS_PH004", "name": "Parkhaus Eiermarkt",
        "facility_kind": "garage", "operator": None,
        "municipality": "Braunschweig", "municipality_ags": "03101000", "capacity_reported": 500,
        "capacity_scope": "reported_in_PULP", "garage_hourly_rate_eur": 1.2, "garage_billing_unit_min": 60,
        "garage_first_period_min": 60, "garage_first_period_eur": 0.6, "garage_daily_cap_eur": 9.6,
        "garage_fee_start_h": 7.0, "garage_fee_end_h": 18.0, "garage_first_period_start_h": None,
        "garage_first_period_end_h": None, "tariff_tiers": None, "tariff_duration_bands": None, "monthly_eur": None,
        "monthly_source_url": None, "monthly_product": None, "monthly_imputed_eur": None, "priced": True,
        "not_priced_reason": None, "assumptions": "P3",
        "source_url": SOURCE, "source_date": "2026-10-07", "tariff_rule_ids": "r-35;r-36;r-37",
        "geometry_method": "official_feed_point", "geometry_source_url": "https://www.braunschweig.de/apps/pulp/result/x",
        "package_sha256": SHA, "notes": "Flat night fee not charged (ASSUMPTION P3); night 18-07 h 5.00 EUR in total.",
        "x": 603408.0, "y": 5791173.0,
    }
    row.update(changes)
    return row


TIERS = "08:00-10:00 0.30/30; 10:00-18:00 0.60/30; 18:00-23:00 0.30/30; 23:00-08:00 0.10/30"


def _tiered(**changes) -> dict:
    """A valid priced garage in the tiered form (the shape of the Wolfenbuettel Rosenwall row): four time-of-day tiers
    that cover the whole day, no single-window core, no first period, no cap, ASSUMPTION P6."""
    base = {"garage_id": "wf_rosenwall", "package_facility_id": "WF_ROSENWALL", "name": "Parkhaus Rosenwall",
            "municipality": "Wolfenbuettel", "municipality_ags": "03158037", "garage_hourly_rate_eur": None,
            "garage_billing_unit_min": None, "garage_first_period_min": None, "garage_first_period_eur": None,
            "garage_daily_cap_eur": None, "garage_fee_start_h": None, "garage_fee_end_h": None, "tariff_tiers": TIERS,
            "assumptions": "P6", "tariff_rule_ids": "r-157;r-158;r-159;r-160",
            "notes": "Four tiers per started 30 min (ASSUMPTION P6: the tier in force at the start of a unit).",
            "x": 604180.0, "y": 5786250.0}
    base.update(changes)
    return _row(**base)


BANDS = "0-30 total 0.20; 30-60 total 0.80; 60-300 0.40/30; 300-1440 total 4.00"


def _banded(**changes) -> dict:
    """A valid priced garage in the banded form (the shape of the Peine Werderstrasse row): duration bands, no single-window
    rate or unit, no first period, no tiers, the fee window 0 to 24 h (ASSUMPTION P5), ASSUMPTIONS P4, P5 and P8."""
    base = {"garage_id": "pe_werderstrasse", "package_facility_id": "PE_WERDER", "name": "Parkhaus Werderstrasse",
            "municipality": "Peine", "municipality_ags": "03157006", "garage_hourly_rate_eur": None,
            "garage_billing_unit_min": None, "garage_first_period_min": None, "garage_first_period_eur": None,
            "garage_first_period_start_h": None, "garage_first_period_end_h": None, "garage_daily_cap_eur": None,
            "garage_fee_start_h": 0.0, "garage_fee_end_h": 24.0, "tariff_tiers": None, "tariff_duration_bands": BANDS,
            "assumptions": "P4;P5;P8", "tariff_rule_ids": "r-168;r-169;r-170;r-171",
            "notes": "Four duration bands (ASSUMPTION P8); no rounding stated (ASSUMPTION P4); no charging times "
                     "(ASSUMPTION P5).", "x": 603200.0, "y": 5793100.0}
    base.update(changes)
    return _row(**base)


GRACE_BANDS = "0-15 free; 15-60 total 1.50; 60- 1.50/60"
FREE_BANDS = "0- free"
AUTOSTADT_TIERS = "06:00-18:00 1.00/60; 18:00-06:00 0.50/60"
SUPPLEMENT_SHA = "76d2651433e05a4d0d0a75ba352fd17f99b329555eb3bb4525c34990383978fa"
FOLLOWUP_SHA = "3bbaff93fb8b26d6cdfb187c7e0bf746f099997c1c7fd04a961fcae7d37329d8"
WOLFSBURG_LOTS_SHA = "650508f87c80ee2b84063def301ff67b5ecb8f2653cef2ab4202fb398c818df0"


def _graced(**changes) -> dict:
    """A valid priced garage whose first band is a free period read as a grace period (ASSUMPTION P10, spec E12; the shape of
    the Forschungsflughafen row): ASSUMPTIONS P5, P8 and P10, a cap, two package SHA-256 values."""
    base = {"garage_id": "bs_forschungsflughafen", "package_facility_id": "BS_SOURCE_4781", "name": "Parkhaus Forschungsflughafen",
            "municipality": "Braunschweig", "municipality_ags": "03101000", "garage_hourly_rate_eur": None,
            "garage_billing_unit_min": None, "garage_first_period_min": None, "garage_first_period_eur": None,
            "garage_first_period_start_h": None, "garage_first_period_end_h": None, "garage_daily_cap_eur": 18.0,
            "garage_fee_start_h": 0.0, "garage_fee_end_h": 24.0, "tariff_tiers": None, "tariff_duration_bands": GRACE_BANDS,
            "assumptions": "P5;P8;P10", "tariff_rule_ids": "r-5;r-6", "package_sha256": f"{SHA};{SUPPLEMENT_SHA}",
            "notes": "Grace period (ASSUMPTION P10); bands (ASSUMPTION P8); no charging times (ASSUMPTION P5).",
            "x": 603200.0, "y": 5793100.0}
    base.update(changes)
    return _row(**base)


def _unpriced(**changes) -> dict:
    """A valid unpriced garage: an incomplete tariff, no tariff value, no assumption."""
    base = {"garage_id": "bs_ring_center", "package_facility_id": "BS_None", "name": "Parkhaus Ring-Center",
            "garage_hourly_rate_eur": None, "garage_billing_unit_min": None, "garage_first_period_min": None,
            "garage_first_period_eur": None, "garage_daily_cap_eur": None, "garage_fee_start_h": None,
            "garage_fee_end_h": None, "priced": False, "not_priced_reason": "incomplete_tariff", "assumptions": None,
            "tariff_rule_ids": "r-19;r-20;r-21", "capacity_reported": None, "capacity_scope": None,
            "notes": "1.50 EUR per started hour for hours 1 and 2, then 2.00 EUR.", "x": 604798.2, "y": 5790032.2}
    base.update(changes)
    return _row(**base)


def _free(**changes) -> dict:
    """A valid priced car park that is free for every stay (the shape of the Allerpark row, Task 4b3, spec E14): the one
    open free band '0- free', the formal fee window 0 to 24 h, no rate, no tier, no assumption (the source states the car park
    free)."""
    base = {"garage_id": "wob_lot_1835028", "package_facility_id": "WOB_PARK_1835028", "name": "Parkplatz Allerpark",
            "facility_kind": "surface_lot", "municipality": "Wolfsburg", "municipality_ags": "03103000",
            "capacity_reported": None, "capacity_scope": None, "garage_hourly_rate_eur": None,
            "garage_billing_unit_min": None, "garage_first_period_min": None, "garage_first_period_eur": None,
            "garage_first_period_start_h": None, "garage_first_period_end_h": None, "garage_daily_cap_eur": None,
            "garage_fee_start_h": 0.0, "garage_fee_end_h": 24.0, "tariff_tiers": None, "tariff_duration_bands": FREE_BANDS,
            "assumptions": None, "tariff_rule_ids": "WOB_PARK_1835028_R01",
            "notes": "Free of charge for every stay (the source states it); the fee window 0-24 h is formal.",
            "x": 604_010.0, "y": 5_813_140.0}
    base.update(changes)
    return _row(**base)


def _free_default(**changes) -> dict:
    """A car park free under ASSUMPTION P12 (the municipal free default of a point without any fee evidence)."""
    base = {"garage_id": "wob_lot_262165", "package_facility_id": "WOB_PARK_262165", "name": "Parkplatz",
            "assumptions": "P12", "tariff_rule_ids": "WOB_PARK_262165:fee_status",
            "notes": "Free of charge by ASSUMPTION P12: outside every published municipal tariff area."}
    base.update(changes)
    return _free(**base)


def _graced_tiers(**changes) -> dict:
    """A valid priced garage with time-of-day tiers and a grace period (the shape of the Autostadt P2 row, Task 4b3): day and
    night tier per started 60 min, the free band 0-30 as the whole of its duration bands, ASSUMPTIONS P4, P6 and P10, the four
    single-window columns empty."""
    base = {"garage_id": "wob_lot_1900571", "package_facility_id": "WOB_PARK_1900571", "name": "Parkplatz Autostadt",
            "facility_kind": "surface_lot", "municipality": "Wolfsburg", "municipality_ags": "03103000",
            "garage_hourly_rate_eur": None, "garage_billing_unit_min": None, "garage_first_period_min": None,
            "garage_first_period_eur": None, "garage_daily_cap_eur": None, "garage_fee_start_h": None,
            "garage_fee_end_h": None, "tariff_tiers": AUTOSTADT_TIERS, "tariff_duration_bands": "0-30 free",
            "assumptions": "P4;P6;P10", "tariff_rule_ids": "OP_AUTOSTADT_P2_DAY;OP_AUTOSTADT_P2_NIGHT;OP_AUTOSTADT_P2_GRACE",
            "notes": "Day and night tier (ASSUMPTION P6), no rounding stated (ASSUMPTION P4), 30 min grace (ASSUMPTION P10).",
            "x": 604_100.0, "y": 5_813_300.0}
    base.update(changes)
    return _row(**base)


def _frame(*rows, crs="EPSG:25832") -> gpd.GeoDataFrame:
    rows = [dict(row) for row in rows]
    geometries = [row.pop("geometry") if "geometry" in row else Point(row["x"], row["y"]) for row in rows]
    for row in rows:
        row.pop("x", None), row.pop("y", None)
    frame = pd.DataFrame(rows, columns=list(pg.DATASET_COLUMNS))
    for column in pg.MONEY_COLUMNS + pg.HOUR_COLUMNS:
        frame[column] = frame[column].astype(float)
    for column in pg.MINUTE_COLUMNS + pg.INTEGER_COLUMNS:
        frame[column] = frame[column].astype("Int64")
    frame["priced"] = frame["priced"].astype(bool)
    return gpd.GeoDataFrame(frame, geometry=geometries, crs=crs)


# --------------------------------------------------------------------------- layout


def test_the_dataset_layout_is_the_documented_one():
    assert pg.TARIFF_COLUMNS == pz.GARAGE_COLUMNS  # the garage columns of the tariff table (spec Amendment A6)
    assert len(pg.DATASET_COLUMNS) == len(set(pg.DATASET_COLUMNS))
    assert pg.DATASET_COLUMNS[:7] == ("garage_id", "package_facility_id", "name", "facility_kind", "operator",
                                      "municipality", "municipality_ags")
    assert set(pg.FACILITY_KINDS) == {"garage", "surface_lot"}
    assert set(pg.TARIFF_COLUMNS) <= set(pg.DATASET_COLUMNS)
    # the clock window of the first period (ruling R-4b-12) follows the single-window columns, then the tiered form (ruling
    # R-4b-10b) and the banded form (ruling R-4b-11) are one text column each, before the monthly product
    assert pg.FIRST_PERIOD_WINDOW_COLUMNS == ("garage_first_period_start_h", "garage_first_period_end_h")
    assert pg.TIER_COLUMNS == ("tariff_tiers",) and pg.BAND_COLUMNS == ("tariff_duration_bands",)
    position = pg.DATASET_COLUMNS.index("garage_first_period_start_h")
    assert pg.DATASET_COLUMNS[position - 1] == pg.TARIFF_COLUMNS[-1]
    assert pg.DATASET_COLUMNS[position:position + 4] == ("garage_first_period_start_h", "garage_first_period_end_h",
                                                         "tariff_tiers", "tariff_duration_bands")
    assert pg.DATASET_COLUMNS[position + 4] == "monthly_eur"
    # spec Amendment F2: the imputed monthly product (ASSUMPTION P13) follows the published one and is money
    assert pg.DATASET_COLUMNS[position + 4:position + 8] == ("monthly_eur", "monthly_source_url", "monthly_product",
                                                             "monthly_imputed_eur")
    assert "monthly_imputed_eur" in pg.MONEY_COLUMNS and pg.LEGACY_OPTIONAL_COLUMNS == ("monthly_imputed_eur",)
    assert set(pg.FIRST_PERIOD_WINDOW_COLUMNS) <= set(pg.HOUR_COLUMNS)
    # banded_tariff is gone: every duration schedule of the sources is expressible (ruling R-4b-11)
    assert set(pg.NOT_PRICED_REASONS) == {"no_published_tariff", "free_period", "incomplete_tariff", "conflicting_sources"}
    assert set(pg.ASSUMPTIONS) == {"P3", "P4", "P5", "P6", "P7", "P8", "P10", "P11", "P12", "P13"}
    assert pg.ASSUMPTIONS["P6"].startswith("units are counted from arrival and each started unit costs the rate of the tier "
                                           "in force at the unit's start")
    # P6 as amended (ruling R-4b-12): the first period belongs to its clock window, the tiers run on from its end
    assert "charged once when the arrival lies inside its clock window" in pg.ASSUMPTIONS["P6"]
    assert "from the end of the first period" in pg.ASSUMPTIONS["P6"]
    assert "an arrival outside the window pays the tiers from the arrival" in pg.ASSUMPTIONS["P6"]
    # P8 (ruling R-4b-11): the cumulative reading of the duration bands
    for phrase in ("a total band sets the price of the stay", "an increment band adds", "started unit",
                   "applies as the day cap"):
        assert phrase in pg.ASSUMPTIONS["P8"], phrase
    # P10 (spec E12, owner decision 2026-10-07): a published free period at the start of a stay is a grace period
    for phrase in ("grace period", "a stay not longer than it costs 0", "billed from the arrival",
                   "the free minutes are not deducted"):
        assert phrase in pg.ASSUMPTIONS["P10"], phrase
    # P11 (spec E13, ruling R-4b2-8): the best available secondary evidence where no operator tariff is published
    for phrase in ("best available secondary evidence", "no current operator tariff", "checked for consistency",
                   "lack of a date", "operator's missing confirmation"):
        assert phrase in pg.ASSUMPTIONS["P11"], phrase
    # P10 also covers the grace period of a tiered garage (Task 4b3: the free band is the whole of its duration bands)
    assert "time-of-day tiers" in pg.ASSUMPTIONS["P10"] and "ASSUMPTION P6" in pg.ASSUMPTIONS["P10"]
    # P12 (spec E14, owner direction 2026-10-07): the municipal free default of a car park without any fee evidence
    for phrase in ("outside every published municipal tariff area", "no operator tariff", "free of charge", "ticket machine",
                   "no evidence of a fee is not evidence of none"):
        assert phrase in pg.ASSUMPTIONS["P12"], phrase
    assert 0.5 < pg.UNION_WARNING_SHARE < 1.0  # a named share of the priced garages, above which the loader warns


# --------------------------------------------------------------------------- loader and writer


def test_a_valid_priced_and_a_valid_unpriced_garage_pass():
    pg.validate_garages(_frame(_row(), _unpriced()))


def test_the_facility_kind_is_a_garage_or_a_surface_lot_and_nothing_else_and_is_required():
    pg.validate_garages(_frame(_row(), _row(garage_id="wob_lot_1", facility_kind="surface_lot")))
    for value, message in ((None, "facility_kind: required for every garage but empty"),
                           ("car_park", "facility_kind: 'car_park' is not one of"),
                           ("Garage", "facility_kind: 'Garage' is not one of")):
        with pytest.raises(ValueError, match=message):
            pg.validate_garages(_frame(_row(facility_kind=value)))


def test_the_coverage_counts_the_garages_and_the_surface_lots_separately():
    frame = _frame(_row(garage_id="a"), _row(garage_id="b", facility_kind="surface_lot"),
                   _row(garage_id="c", facility_kind="surface_lot", municipality_ags="03103000"),
                   _unpriced(garage_id="d"), _unpriced(garage_id="e", facility_kind="surface_lot"))
    assert pg.coverage(frame)["by_facility_kind"] == {"garage": {"listed": 2, "priced": 1, "not_priced": 1},
                                                      "surface_lot": {"listed": 3, "priced": 2, "not_priced": 1}}


def test_the_writer_and_the_loader_round_trip_typed_metric_points(tmp_path):
    frame = _frame(_row(monthly_eur=48.0, monthly_source_url="https://example.org/monat", monthly_product="Dauerstellplatz",
                        garage_first_period_start_h=7.0, garage_first_period_end_h=18.0),
                   _unpriced(), _tiered(), _banded())
    path = tmp_path / "garages.geojson"
    pg.write_garages(frame, path, members={"license": "terms of the sources", "attribution": "operators"})
    text = path.read_text(encoding="utf-8")
    assert text.isascii() and "\r" not in text
    document = json.loads(text)
    assert document["license"] == "terms of the sources" and len(document["features"]) == 4
    first = document["features"][0]
    assert list(first["properties"]) == list(pg.DATASET_COLUMNS)  # the column order is the file order
    assert first["properties"]["operator"] is None and first["properties"]["monthly_eur"] == 48.0
    assert first["geometry"]["type"] == "Point" and 10.0 < first["geometry"]["coordinates"][0] < 11.0  # lon, lat
    loaded = pg.load_garages(path)
    assert loaded.crs.to_epsg() == 25832 and list(loaded.columns) == list(pg.DATASET_COLUMNS) + ["geometry"]
    # the file keeps 7 decimals of a degree (about 1 cm): the points come back within 2 cm
    assert loaded.geometry.distance(frame.geometry).max() < 0.02
    assert str(loaded["garage_billing_unit_min"].dtype) == "Int64" and str(loaded["capacity_reported"].dtype) == "Int64"
    assert loaded["garage_hourly_rate_eur"].dtype == float and loaded["priced"].dtype == bool
    assert list(loaded["priced"]) == [True, False, True, True]
    assert loaded.loc[2, "tariff_tiers"] == TIERS and pd.isna(loaded.loc[2, "garage_hourly_rate_eur"])
    assert loaded.loc[0, "tariff_tiers"] is None and loaded.loc[0, "tariff_duration_bands"] is None
    assert loaded.loc[3, "tariff_duration_bands"] == BANDS and loaded.loc[3, "tariff_tiers"] is None
    assert pd.isna(loaded.loc[3, "garage_hourly_rate_eur"]) and loaded.loc[3, "garage_fee_end_h"] == 24.0
    # the clock window of the first period is typed like the fee window: hours as float, empty as NaN
    assert loaded.loc[0, "garage_first_period_start_h"] == 7.0 and loaded.loc[0, "garage_first_period_end_h"] == 18.0
    assert pd.isna(loaded.loc[2, "garage_first_period_start_h"]) and loaded["garage_first_period_start_h"].dtype == float
    assert loaded.loc[0, "operator"] is None and loaded.loc[0, "monthly_product"] == "Dauerstellplatz"
    assert pd.isna(loaded.loc[1, "garage_hourly_rate_eur"]) and pd.isna(loaded.loc[1, "garage_first_period_min"])
    assert loaded.loc[1, "not_priced_reason"] == "incomplete_tariff" and loaded.loc[0, "not_priced_reason"] is None
    pg.validate_garages(loaded)
    # equal content, equal bytes
    again = tmp_path / "again.geojson"
    pg.write_garages(frame, again, members={"license": "terms of the sources", "attribution": "operators"})
    assert again.read_bytes() == path.read_bytes()


def test_the_loader_refuses_a_missing_file_missing_or_unexpected_columns_and_a_null_priced_flag(tmp_path):
    with pytest.raises(FileNotFoundError, match="garage dataset missing"):
        pg.load_garages(tmp_path / "absent.geojson")
    frame = _frame(_row())
    path = tmp_path / "garages.geojson"
    pg.write_garages(frame, path)
    document = json.loads(path.read_text(encoding="utf-8"))
    for broken, message in ((lambda props: props.pop("notes"), r"missing \['notes'\]"),
                            (lambda props: props.update({"extra": 1}), r"unexpected \['extra'\]"),
                            (lambda props: props.update({"priced": None}), "priced must be true or false"),
                            (lambda props: props.update({"garage_billing_unit_min": 60.5}), "non-integer"),
                            (lambda props: props.update({"garage_hourly_rate_eur": "free"}), "Unable to parse")):
        changed = json.loads(json.dumps(document))
        broken(changed["features"][0]["properties"])
        (tmp_path / "broken.geojson").write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError, match=message):
            pg.load_garages(tmp_path / "broken.geojson")


def _assumption_notes(assumptions: str) -> str:
    return " ".join(f"ASSUMPTION {assumption}." for assumption in assumptions.split(";"))


def test_the_loader_logs_the_priced_rate_the_reasons_the_assumption_rates_and_the_union_rates(tmp_path, caplog):
    frame = _frame(_row(), _row(garage_id="bs_magni", assumptions="P5", notes=_assumption_notes("P5")),
                   _row(garage_id="bs_packhof", assumptions="P4;P5", notes=_assumption_notes("P4;P5")),
                   _row(garage_id="bs_wallstrasse", assumptions=None, notes="Stated in full."), _tiered(), _banded(),
                   _unpriced())
    path = tmp_path / "garages.geojson"
    pg.write_garages(frame, path)
    with caplog.at_level(logging.INFO, logger=pg.log.name):
        pg.load_garages(path)
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "loaded 7 garages" in text and "priced 6/7 (85.7 %)" in text and "not priced 1 (incomplete_tariff 1)" in text
    assert "P3 1/6" in text and "P4 2/6" in text and "P5 3/6" in text and "P6 1/6" in text and "P8 1/6" in text
    # the union rates: five of the six priced garages rest on an assumption, three of them on P4 or P5; one is tiered, one
    # banded
    assert "at least one assumption 5/6 (83.3 %)" in text and "P4 or P5 3/6 (50.0 %)" in text
    assert "tiered 1/6; banded 1/6; free 0/6; by facility kind garage 7" in text


def test_the_loader_warns_above_the_union_threshold_and_stays_silent_at_or_below_it(tmp_path, caplog):
    def warnings_of(name, rows):
        path = tmp_path / f"{name}.geojson"
        pg.write_garages(_frame(*rows), path)
        caplog.clear()
        with caplog.at_level(logging.INFO, logger=pg.log.name):
            pg.load_garages(path)
        return [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]

    def garage(number, assumptions):
        notes = _assumption_notes(assumptions) if assumptions else "Stated in full."
        return _row(garage_id=f"g_{number}", assumptions=assumptions, notes=notes, x=603408.0 + 50.0 * number)

    assert pg.UNION_WARNING_SHARE == 0.75
    # all four priced garages rest on an assumption and on P5: both union rates are above the threshold
    crowded = warnings_of("crowded", [garage(number, "P5") for number in range(4)])
    assert len(crowded) == 2 and "100.0 %" in crowded[0] and "rest on at least one assumption" in crowded[0]
    assert "ASSUMPTION P4 or P5" in crowded[1]
    # three of four is exactly the threshold share and does not warn: the check is "above", not "at"
    assert warnings_of("edge", [garage(0, None)] + [garage(number, "P5") for number in range(1, 4)]) == []
    assert warnings_of("quiet", [garage(0, None), garage(1, None), garage(2, "P3"), garage(3, "P3")]) == []
    # P3 alone is an assumption but no replaced detail: only the first union rate is above the threshold
    only_p3 = warnings_of("only_p3", [garage(number, "P3") for number in range(4)])
    assert len(only_p3) == 1 and "rest on at least one assumption" in only_p3[0]


# --------------------------------------------------------------------------- time-of-day tiers


def test_the_tiers_of_a_text_are_parsed_to_minutes_and_formatted_back_to_the_same_text():
    tiers = pg.parse_tariff_tiers(TIERS)
    assert [(tier.start_min, tier.end_min, tier.unit_min, tier.eur) for tier in tiers] == [
        (480, 600, 30, 0.30), (600, 1080, 30, 0.60), (1080, 1380, 30, 0.30), (1380, 480, 30, 0.10)]
    assert [tier.crosses_midnight for tier in tiers] == [False, False, False, True]
    assert tiers[3].intervals() == [(1380, 1440), (0, 480)] and tiers[0].intervals() == [(480, 600)]
    assert pg.format_tariff_tiers(tiers) == TIERS
    # every time of day lies in a tier here; a single tier to midnight and a whole-day tier are valid texts as well
    assert pg.tier_coverage_minutes(tiers) == 1440
    assert pg.parse_tariff_tiers("00:00-24:00 1.00/60")[0].end_min == 1440
    assert pg.tier_coverage_minutes(pg.parse_tariff_tiers("00:00-06:00 0.50/60; 06:00-24:00 1.20/60")) == 1440
    # a time of day outside every tier is free: one tier from 8 to 18 h leaves 14 of 24 hours free
    assert pg.tier_coverage_minutes(pg.parse_tariff_tiers("08:00-18:00 1.00/60")) == 600


@pytest.mark.parametrize("text, message", [
    ("", "the tiers are empty"),
    (None, "the tiers are empty"),
    ("8:00-10:00 0.30/30", "is not 'HH:MM-HH:MM <eur>/<unit_min>'"),
    ("08:00-10:00 0.3/30", "is not 'HH:MM-HH:MM <eur>/<unit_min>'"),
    ("08:00-10:00 0.30", "is not 'HH:MM-HH:MM <eur>/<unit_min>'"),
    ("08:00-10:00 0.30/30;10:00-18:00 0.60/30", "is not 'HH:MM-HH:MM <eur>/<unit_min>'"),
    ("08:00-10:00 0.30/30; ", "is not 'HH:MM-HH:MM <eur>/<unit_min>'"),
    ("08:00-10:00 0.30/30.5", "is not 'HH:MM-HH:MM <eur>/<unit_min>'"),
    ("25:00-26:00 0.30/30", "the start 25:00 is no clock time"),
    ("24:00-02:00 0.30/30", "the start 24:00 is no clock time"),
    ("08:60-10:00 0.30/30", "the start 08:60 is no clock time"),
    ("08:00-24:30 0.30/30", "the end 24:30 is no clock time"),
    ("08:00-10:75 0.30/30", "the end 10:75 is no clock time"),
    ("10:00-10:00 0.30/30", "has no length"),
    ("08:00-10:00 0.00/30", "the price must be positive"),
    ("08:00-10:00 0.30/0", "the unit must be a positive number of minutes"),
    ("08:00-10:00 0.30/30; 10:00-18:00 0.60/60", "different units"),
    ("18:00-23:00 0.30/30; 08:00-10:00 0.30/30", "strictly ascending order of their start"),
    ("08:00-10:00 0.30/30; 08:00-12:00 0.60/30", "strictly ascending order of their start"),
    ("08:00-12:00 0.30/30; 10:00-18:00 0.60/30", "tiers overlap at 10:00"),
    ("08:00-12:00 0.30/30; 23:00-09:00 0.10/30", "tiers overlap at 08:00"),
    ("01:00-12:00 0.30/30; 23:00-02:00 0.10/30", "tiers overlap at 01:00"),
])
def test_a_malformed_overlapping_or_unordered_tier_text_is_rejected_with_the_reason(text, message):
    with pytest.raises(ValueError, match=message):
        pg.parse_tariff_tiers(text)


def test_tiers_that_touch_at_midnight_or_at_a_boundary_do_not_overlap():
    pg.parse_tariff_tiers("00:00-06:00 0.50/60; 06:00-24:00 1.20/60")
    pg.parse_tariff_tiers("07:00-18:00 1.20/60; 18:00-07:00 1.00/60")
    pg.parse_tariff_tiers("06:00-12:00 1.00/60; 12:00-18:00 2.00/60; 22:00-06:00 0.50/60")


# --------------------------------------------------------------------------- duration bands (ruling R-4b-11)

#: The schedules of the garages that the package states as duration bands, in the grammar of ``parse_duration_bands`` (the
#: committed data pins the same texts against the sources independently).
OUTLETS = "0-20 free; 20-120 total 1.00; 120-240 0.50/60; 240-420 1.50/60; 420- 5.00/60"
RING_CENTER = "0-120 1.50/60; 120- 2.00/60"
BRAWO = "0-360 1.50/60; 360- total 15.00"
SCHILLER = "0-120 1.00/60; 120- 1.50/60"
GS_CA_BANDS = "0-180 1.50/60; 180- 0.80/30"


def test_the_bands_of_a_text_are_parsed_to_minutes_kinds_and_amounts_and_formatted_back_to_the_same_text():
    bands = pg.parse_duration_bands(OUTLETS)
    assert [(band.from_min, band.to_min, band.kind, band.eur, band.unit_min) for band in bands] == [
        (0, 20, "free", 0.0, None), (20, 120, "total", 1.0, None), (120, 240, "increment", 0.5, 60),
        (240, 420, "increment", 1.5, 60), (420, None, "increment", 5.0, 60)]
    assert pg.format_duration_bands(bands) == OUTLETS
    for text in (BANDS, RING_CENTER, BRAWO, SCHILLER, GS_CA_BANDS, "0-1440 total 4.00", "0- 1.00/60"):
        assert pg.format_duration_bands(pg.parse_duration_bands(text)) == text
    assert pg.parse_duration_bands(BRAWO)[1] == pg.DurationBand(360, None, "total", 15.0, None)
    assert [band.is_open_ended for band in pg.parse_duration_bands(RING_CENTER)] == [False, True]


@pytest.mark.parametrize("text, message", [
    ("", "the bands are empty"),
    (None, "the bands are empty"),
    ("0-30 total 0.2", "is not '<from_min>-<to_min> free"),
    ("0-30 total 0.20;30-60 total 0.80", "is not '<from_min>-<to_min> free"),
    ("0-30 total 0.20; ", "is not '<from_min>-<to_min> free"),
    ("0-30 free 0.20", "is not '<from_min>-<to_min> free"),
    ("0-30 0.20", "is not '<from_min>-<to_min> free"),
    ("0-30 0.20/30.5", "is not '<from_min>-<to_min> free"),
    ("0-30 0.20/30 each", "is not '<from_min>-<to_min> free"),
    ("0 -30 total 0.20", "is not '<from_min>-<to_min> free"),
    ("0-30total 0.20", "is not '<from_min>-<to_min> free"),
    ("00:00-30 total 0.20", "is not '<from_min>-<to_min> free"),
    ("-30 total 0.20", "is not '<from_min>-<to_min> free"),
    ("10-30 total 0.20", "the first band must start at 0 min"),
    ("0-30 total 0.20; 40-60 total 0.80", "gap or overlap: band '40-60 total 0.80' starts at 40 min but the band before ends "
                                          "at 30 min"),
    ("0-30 total 0.20; 20-60 total 0.80", "gap or overlap: band '20-60 total 0.80' starts at 20 min"),
    ("0-30 total 0.20; 30-30 total 0.80", "has no length"),
    ("0-30 total 0.20; 30-20 total 0.80", "has no length"),
    ("0- total 0.20; 30-60 total 0.80", "only the last band may be open-ended"),
    ("0-30 total 0.00", "the amount must be positive"),
    ("0-30 0.00/60", "the amount must be positive"),
    ("0-30 0.20/0", "the unit must be a positive number of minutes"),
    ("0-30 total 0.20; 30-60 free", "a free band can only be the first band"),
    ("0-30 total 0.80; 30-60 total 0.20", "below the price 0.80 reached at its start"),
    ("0-60 1.00/60; 60-120 total 0.50", "below the price 1.00 reached at its start"),
])
def test_a_malformed_gapped_overlapping_or_falling_band_text_is_rejected_with_the_reason(text, message):
    with pytest.raises(ValueError, match=message):
        pg.parse_duration_bands(text)


def test_a_free_first_band_a_total_that_repeats_the_price_and_a_single_open_band_are_valid_texts():
    pg.parse_duration_bands("0-20 free; 20- 1.00/60")
    pg.parse_duration_bands("0-60 1.00/60; 60-120 total 1.00")  # a total equal to the price reached is no fall
    pg.parse_duration_bands("0-1440 total 4.00")
    assert pg.parse_duration_bands("0- free")[0].kind == "free"


@pytest.mark.parametrize("text, duration_min, expected", [
    # Peine Werderstrasse (0.20 for the first half hour, 0.80 up to 1 h, then 0.40 per started half hour up to 5 h, 4.00
    # in total from 5 to 24 h): the published anchors 0.20 at 30 min, 0.80 at 60 min, 4.00 at 300 min
    (BANDS, 1, 0.20), (BANDS, 30, 0.20), (BANDS, 30.5, 0.80), (BANDS, 31, 0.80), (BANDS, 60, 0.80), (BANDS, 61, 1.20),
    (BANDS, 90, 1.20), (BANDS, 91, 1.60), (BANDS, 120, 1.60), (BANDS, 299, 4.00), (BANDS, 300, 4.00), (BANDS, 301, 4.00),
    (BANDS, 1440, 4.00),
    # Designer Outlets (free for 20 min, 1.00 up to 2 h, 0.50 per started hour up to 4 h, 1.50 per started hour up to 7 h,
    # then 5.00 per started hour): 0 at 20 min, 1.00 at 120 min
    (OUTLETS, 20, 0.0), (OUTLETS, 21, 1.00), (OUTLETS, 120, 1.00), (OUTLETS, 121, 1.50), (OUTLETS, 180, 1.50),
    (OUTLETS, 181, 2.00), (OUTLETS, 240, 2.00), (OUTLETS, 241, 3.50), (OUTLETS, 300, 3.50), (OUTLETS, 301, 5.00),
    (OUTLETS, 420, 6.50), (OUTLETS, 421, 11.50), (OUTLETS, 481, 16.50), (OUTLETS, 1440, 91.50),
    # Braunschweig Ring-Center (1.50 for each of the first two started hours, then 2.00 per started hour)
    (RING_CENTER, 1, 1.50), (RING_CENTER, 60, 1.50), (RING_CENTER, 61, 3.00), (RING_CENTER, 120, 3.00),
    (RING_CENTER, 121, 5.00), (RING_CENTER, 180, 5.00), (RING_CENTER, 181, 7.00),
    # BRAWO Carree (1.50 per started hour for the first 6 h, from the 7th started hour a day total of 15.00)
    (BRAWO, 360, 9.00), (BRAWO, 361, 15.00), (BRAWO, 1440, 15.00), (BRAWO, 3000, 15.00),
    # Wolfsburg Schillerstrasse
    (SCHILLER, 120, 2.00), (SCHILLER, 121, 3.50), (SCHILLER, 181, 5.00),
])
def test_the_evaluation_reproduces_the_published_anchor_values_and_the_band_edges(text, duration_min, expected):
    assert pg.duration_band_price_eur(pg.parse_duration_bands(text), duration_min) == pytest.approx(expected, abs=1e-9)


def test_the_evaluation_is_exact_to_the_cent_without_a_float_drift():
    bands = pg.parse_duration_bands("0- 0.10/1")
    for minutes in (1, 3, 7, 30, 100, 1000):
        assert pg.duration_band_price_eur(bands, minutes) == round(minutes * 0.10, 2)
    assert pg.duration_band_price_eur(pg.parse_duration_bands(BANDS), 120) == 1.60  # an exact decimal, not 1.6000000000000003
    # amounts whose binary form is just below the decimal (0.29 * 100 = 28.999999999999996) still sum to the exact cent
    for amount, minutes, expected in ((0.29, 1, 0.29), (1.15, 3, 3.45), (4.35, 2, 8.70), (0.57, 7, 3.99)):
        bands = pg.parse_duration_bands(f"0- {amount:.2f}/1")
        assert pg.duration_band_price_eur(bands, minutes) == expected, (amount, minutes)
    assert pg.duration_band_price_eur(pg.parse_duration_bands("0-10 total 1.15"), 5) == 1.15
    assert pg.duration_band_price_eur(pg.parse_duration_bands("0-10 total 5.00"), 5, daily_cap_eur=4.35) == 4.35


def test_a_stay_of_no_length_is_free_and_a_negative_duration_is_refused():
    bands = pg.parse_duration_bands(BANDS)
    assert pg.duration_band_price_eur(bands, 0) == 0.0
    with pytest.raises(ValueError, match="duration must not be negative"):
        pg.duration_band_price_eur(bands, -1)


def test_a_stay_beyond_a_closed_schedule_is_refused_instead_of_priced_by_a_guess():
    closed = pg.parse_duration_bands("0-30 total 0.20; 30-1440 total 4.00")
    assert pg.duration_band_price_eur(closed, 1440) == 4.0
    with pytest.raises(ValueError, match="beyond the last band, which ends at 1440 min"):
        pg.duration_band_price_eur(closed, 1441)
    assert pg.duration_band_price_eur(pg.parse_duration_bands(BRAWO), 100_000) == 15.0  # an open last band covers every stay


@pytest.mark.parametrize("duration_min", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_duration_is_refused_instead_of_priced_as_free(duration_min):
    # Task 4b re-review carry-over: NaN passes `< 0` and `> 0` alike and used to return 0.0 (a free stay).
    bands = pg.parse_duration_bands(BANDS)
    with pytest.raises(ValueError, match="finite"):
        pg.duration_band_price_eur(bands, duration_min)


def test_the_day_cap_limits_the_schedule_price_from_the_duration_where_it_bites():
    # Goslar C&A: 1.50 per started hour for 3 h, then 0.80 per started half hour, 25.00 for 24 h (the day cap)
    bands = pg.parse_duration_bands(GS_CA_BANDS)
    assert pg.duration_band_price_eur(bands, 180, daily_cap_eur=25.0) == 4.50
    assert pg.duration_band_price_eur(bands, 181, daily_cap_eur=25.0) == 5.30
    assert pg.duration_band_price_eur(bands, 960, daily_cap_eur=25.0) == 25.0  # 4.50 + 26 * 0.80 = 25.30, capped
    assert pg.duration_band_price_eur(bands, 930, daily_cap_eur=25.0) == pytest.approx(24.50)  # 4.50 + 25 * 0.80
    assert pg.duration_band_price_eur(bands, 1440, daily_cap_eur=25.0) == 25.0
    assert pg.duration_band_price_eur(bands, 960) == pytest.approx(25.30)  # no cap: the schedule alone
    # a cap above the schedule price changes nothing; a cap over a total band
    assert pg.duration_band_price_eur(pg.parse_duration_bands(BRAWO), 400, daily_cap_eur=20.0) == 15.0
    assert pg.duration_band_price_eur(pg.parse_duration_bands(BRAWO), 400, daily_cap_eur=12.0) == 12.0
    with pytest.raises(ValueError, match="the day cap must be positive"):
        pg.duration_band_price_eur(bands, 60, daily_cap_eur=0.0)


@pytest.mark.parametrize("bands_text", [BANDS, OUTLETS, RING_CENTER, BRAWO, SCHILLER, GS_CA_BANDS])
def test_the_schedule_price_never_falls_as_the_stay_gets_longer(bands_text):
    bands = pg.parse_duration_bands(bands_text)
    last = bands[-1].to_min or 1440
    prices = [pg.duration_band_price_eur(bands, minutes) for minutes in range(0, last + 1)]
    assert all(later >= earlier for earlier, later in zip(prices, prices[1:]))


# --------------------------------------------------------------------------- validator


@pytest.mark.parametrize("changes, message", [
    ({"garage_id": "BS_Eiermarkt"}, "lower-case ASCII letters"),
    ({"name": None}, "name: required for every garage but empty"),
    ({"municipality_ags": "3101000"}, "not an 8-digit AGS of the ZGB counties"),
    ({"municipality_ags": "09162000"}, "not an 8-digit AGS of the ZGB counties"),
    ({"municipality": None}, "municipality: required"),
    ({"source_url": "contipark.de"}, "source_url: 'contipark.de' is not an http"),
    ({"geometry_source_url": None}, "geometry_source_url: required"),
    ({"source_date": "7.10.2026"}, "not an ISO date"),
    ({"package_sha256": "E789"}, "not a lower-case hexadecimal SHA-256"),
    ({"notes": None}, "notes: required"),
    ({"geometry_method": None}, "geometry_method: required"),
    # position
    ({"x": 5791173.0, "y": 603408.0}, "outside the ZGB extent"),
    ({"x": 10.5, "y": 52.3}, "outside the ZGB extent"),
    ({"geometry": LineString([(603408.0, 5791173.0), (603420.0, 5791180.0)])}, "LineString is not a point"),
    ({"geometry": Point()}, "empty geometry"),
    # the garage core of spec Amendment A6 is all-or-none
    ({"garage_billing_unit_min": None}, "garage_billing_unit_min: the garage core"),
    ({"garage_fee_end_h": None}, "garage_fee_end_h: the garage core"),
    ({"garage_hourly_rate_eur": None}, "garage_hourly_rate_eur: the garage core"),
    # positive amounts, whole cents, positive units
    ({"garage_hourly_rate_eur": 0.0}, "garage_hourly_rate_eur: must be a positive amount"),
    ({"garage_hourly_rate_eur": -1.2}, "must be a positive amount"),
    ({"garage_hourly_rate_eur": 1.234}, "not a whole number of cents"),
    ({"garage_daily_cap_eur": 0.0}, "garage_daily_cap_eur: must be a positive amount"),
    ({"garage_first_period_eur": 0.0}, "garage_first_period_eur: must be a positive amount"),
    ({"garage_billing_unit_min": 0}, "garage_billing_unit_min: must be a positive number of minutes"),
    ({"garage_first_period_min": 0}, "garage_first_period_min: must be a positive number of minutes"),
    # windows
    ({"garage_fee_start_h": 18.0}, "fee window 18.0 .. 18.0 h"),
    ({"garage_fee_start_h": 20.0}, "fee window 20.0 .. 18.0 h"),
    ({"garage_fee_start_h": -1.0}, "fee window -1.0 .. 18.0 h"),
    ({"garage_fee_end_h": 24.5}, "fee window 7.0 .. 24.5 h"),
    # the clock window of the first period (ruling R-4b-12): both hours or neither, with a first period, like the fee window
    ({"garage_first_period_start_h": 7.0}, "garage_first_period_end_h: garage_first_period_start_h and "
                                           "garage_first_period_end_h are set together"),
    ({"garage_first_period_end_h": 18.0}, "garage_first_period_start_h: garage_first_period_start_h and "
                                          "garage_first_period_end_h are set together"),
    ({"garage_first_period_start_h": 18.0, "garage_first_period_end_h": 7.0}, "first-period window 18.0 .. 7.0 h"),
    ({"garage_first_period_start_h": 7.0, "garage_first_period_end_h": 7.0}, "first-period window 7.0 .. 7.0 h"),
    ({"garage_first_period_start_h": -1.0, "garage_first_period_end_h": 7.0}, "first-period window -1.0 .. 7.0 h"),
    ({"garage_first_period_start_h": 7.0, "garage_first_period_end_h": 24.5}, "first-period window 7.0 .. 24.5 h"),
    ({"garage_first_period_min": None, "garage_first_period_eur": None, "garage_first_period_start_h": 7.0,
      "garage_first_period_end_h": 18.0}, "a first-period window needs a first period"),
    # cap and first period
    ({"garage_daily_cap_eur": 0.5}, "the day cap 0.5 is below the first period 0.6"),
    ({"garage_first_period_eur": None}, "garage_first_period_eur: garage_first_period_min and garage_first_period_eur"),
    ({"garage_first_period_min": None}, "garage_first_period_min: garage_first_period_min and garage_first_period_eur"),
    # priced is exactly "the core is set"
    ({"priced": False}, "priced is False but the garage tariff"),
    ({"not_priced_reason": "incomplete_tariff"}, "a priced garage has no reason"),
    ({"tariff_rule_ids": None}, "tariff_rule_ids: a priced garage names the package rules"),
    # the two tariff forms exclude each other (ruling R-4b-10b)
    ({"tariff_tiers": TIERS}, "a tiered garage leaves the single-window core"),
    ({"tariff_duration_bands": BANDS}, "a banded garage leaves the rate and the billing unit empty"),
    ({"assumptions": "P6", "notes": "ASSUMPTION P6."}, "ASSUMPTION P6 prices a stay from tariff_tiers, but this garage has none"),
    ({"assumptions": "P8", "notes": "ASSUMPTION P8."},
     "ASSUMPTION P8 prices a stay from tariff_duration_bands, but this garage has none"),
    # assumptions are named in the notes
    ({"assumptions": "P9"}, "'P9' is not one of"),
    ({"assumptions": "P3;P3"}, "listed twice"),
    ({"assumptions": "P3;P4"}, "the notes must name ASSUMPTION P4"),
    ({"notes": "no assumption is named here"}, "the notes must name ASSUMPTION P3"),
    # monthly product: no value without its source
    ({"monthly_eur": 48.0}, "monthly_source_url: a monthly product needs its source"),
    ({"monthly_eur": 48.0, "monthly_source_url": "https://example.org/m"}, "monthly_product: a monthly product needs"),
    ({"monthly_eur": 48.0, "monthly_source_url": "example.org", "monthly_product": "x"}, "monthly_source_url: 'example.org'"),
    ({"monthly_eur": 0.0, "monthly_source_url": "https://example.org/m", "monthly_product": "x"},
     "monthly_eur: must be a positive amount"),
    ({"monthly_eur": 48.005, "monthly_source_url": "https://example.org/m", "monthly_product": "x"},
     "not a whole number of cents"),
    ({"monthly_source_url": "https://example.org/m"}, "set without a monthly_eur amount"),
    ({"monthly_product": "Dauerstellplatz"}, "set without a monthly_eur amount"),
    # capacity
    ({"capacity_reported": 0}, "capacity_reported: must be a positive number of spaces"),
    ({"capacity_scope": None}, "capacity_scope: a reported capacity needs its scope"),
])
def test_the_validator_rejects_a_broken_priced_garage(changes, message):
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_row(**changes)))


@pytest.mark.parametrize("changes, message", [
    ({"not_priced_reason": None}, "an unpriced garage states its reason"),
    ({"not_priced_reason": "too_complicated"}, "'too_complicated' is not one of"),
    ({"garage_hourly_rate_eur": 1.5}, "an unpriced garage carries no tariff value"),
    ({"tariff_tiers": TIERS}, "an unpriced garage carries no tariff value"),
    ({"tariff_duration_bands": BANDS}, "an unpriced garage carries no tariff value"),
    ({"garage_first_period_start_h": 7.0, "garage_first_period_end_h": 18.0}, "an unpriced garage carries no tariff value"),
    ({"garage_daily_cap_eur": 15.0}, "a day cap or a first period needs the complete garage core"),
    ({"garage_first_period_min": 60, "garage_first_period_eur": 1.5}, "needs the complete garage core"),
    ({"garage_first_period_min": 60}, "are set together or not at all"),
    ({"priced": True}, "priced is True but the garage tariff"),
    ({"assumptions": "P4"}, "an unpriced garage rests on no assumption"),
    ({"tariff_rule_ids": None}, None),  # the evidence of a reason is a good thing, not a requirement
])
def test_the_validator_rejects_a_broken_unpriced_garage(changes, message):
    if message is None:
        pg.validate_garages(_frame(_unpriced(**changes)))
        return
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_unpriced(**changes)))


def test_a_tiered_garage_passes_with_and_without_a_first_period_and_a_cap_and_a_midnight_crossing_tier():
    pg.validate_garages(_frame(_tiered()))
    pg.validate_garages(_frame(_tiered(garage_first_period_min=60, garage_first_period_eur=0.6, garage_daily_cap_eur=9.6)))
    pg.validate_garages(_frame(_tiered(tariff_tiers="07:00-18:00 1.20/60; 18:00-07:00 1.00/60", assumptions="P6;P7",
                                       notes="ASSUMPTION P6. ASSUMPTION P7.")))
    # free times of day (outside every tier) are valid
    pg.validate_garages(_frame(_tiered(tariff_tiers="08:00-18:00 1.00/60")))


@pytest.mark.parametrize("changes, message", [
    ({"tariff_tiers": "10:00-10:00 0.30/30"}, "tariff_tiers: tier '10:00-10:00 0.30/30' has no length"),
    ({"tariff_tiers": "08:00-12:00 0.30/30; 10:00-18:00 0.60/30"}, "tariff_tiers: tiers overlap at 10:00"),
    ({"tariff_tiers": "08:00-10:00 0.30/30; 10:00-18:00 0.60/60"}, "tariff_tiers: the tiers use different units"),
    ({"tariff_tiers": "8-10 0.30/30"}, "tariff_tiers: tier '8-10 0.30/30' is not"),
    # a tiered garage leaves the whole single-window core empty, a part of it is as wrong as all of it
    ({"garage_hourly_rate_eur": 1.2}, "a tiered garage leaves the single-window core"),
    ({"garage_fee_end_h": 18.0}, "a tiered garage leaves the single-window core"),
    # the first period and the cap of the tiered form follow the rules of the single-window form
    ({"garage_first_period_min": 60}, "are set together or not at all"),
    ({"garage_first_period_min": 60, "garage_first_period_eur": 0.6, "garage_daily_cap_eur": 0.5},
     "the day cap 0.5 is below the first period 0.6"),
    # priced is exactly "one tariff form is set", and P6 belongs to the tiers
    ({"priced": False}, "priced is False but the garage tariff"),
    ({"assumptions": None}, "a tiered garage rests on ASSUMPTION P6"),
    ({"assumptions": "P5", "notes": "ASSUMPTION P5."}, "a tiered garage rests on ASSUMPTION P6"),
    ({"assumptions": "P6", "notes": "no assumption is named here"}, "the notes must name ASSUMPTION P6"),
])
def test_the_validator_rejects_a_broken_tiered_garage(changes, message):
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_tiered(**changes)))


def test_a_first_period_may_carry_its_clock_window_in_the_single_window_and_the_tiered_form():
    # the Eiermarkt shape: the first hour 0.60 EUR applies to an arrival between 7 and 18 h (ruling R-4b-12, ASSUMPTION P6)
    pg.validate_garages(_frame(_row(garage_first_period_start_h=7.0, garage_first_period_end_h=18.0)))
    pg.validate_garages(_frame(_tiered(garage_first_period_min=60, garage_first_period_eur=1.1, garage_daily_cap_eur=9.6,
                                       garage_first_period_start_h=6.0, garage_first_period_end_h=24.0)))
    # a window that ends at midnight is 24, a first period without any window stays valid (the source ties it to none)
    pg.validate_garages(_frame(_row(garage_first_period_start_h=0.0, garage_first_period_end_h=24.0)))
    pg.validate_garages(_frame(_row()))


def test_a_banded_garage_passes_with_and_without_a_day_cap_and_with_a_stated_fee_window():
    pg.validate_garages(_frame(_banded()))
    pg.validate_garages(_frame(_banded(tariff_duration_bands=OUTLETS)))
    pg.validate_garages(_frame(_banded(tariff_duration_bands=GS_CA_BANDS, garage_daily_cap_eur=25.0)))
    # a fee window that a source states replaces ASSUMPTION P5 (the column pair is still complete)
    pg.validate_garages(_frame(_banded(garage_fee_start_h=6.0, garage_fee_end_h=21.5, assumptions="P4;P8",
                                       notes="ASSUMPTION P4. ASSUMPTION P8.")))
    # the other forms still validate next to a banded garage, and the loader round trip is in the test above
    pg.validate_garages(_frame(_banded(), _row(), _tiered(), _unpriced()))


@pytest.mark.parametrize("changes, message", [
    # the text is validated by the parser
    ({"tariff_duration_bands": "0-30 total 0.2"}, "tariff_duration_bands: band '0-30 total 0.2' is not"),
    ({"tariff_duration_bands": "0-30 total 0.20; 40-60 total 0.80"}, "tariff_duration_bands: gap or overlap"),
    ({"tariff_duration_bands": "10-30 total 0.20"},
     "tariff_duration_bands: band .10-30 total 0.20.: the first band must start at 0 min"),
    ({"tariff_duration_bands": "0- total 0.20; 30-60 total 0.80"}, "only the last band may be open-ended"),
    ({"tariff_duration_bands": "0-30 total 0.80; 30-60 total 0.20"}, "below the price 0.80 reached at its start"),
    # one tariff structure per garage: the single rate, the first period and the tiers are all excluded
    ({"garage_hourly_rate_eur": 1.2}, "garage_hourly_rate_eur: a banded garage leaves the rate and the billing unit empty"),
    ({"garage_billing_unit_min": 60}, "garage_billing_unit_min: a banded garage leaves the rate and the billing unit empty"),
    ({"garage_first_period_min": 60, "garage_first_period_eur": 0.6}, "a garage with duration bands has no first period"),
    ({"tariff_tiers": TIERS, "assumptions": "P4;P5;P6;P8", "notes": "ASSUMPTION P4. ASSUMPTION P5. ASSUMPTION P6. ASSUMPTION P8."},
     "tariff_duration_bands: a tiered garage carries duration bands only as a grace period"),
    # the fee window applies unchanged: both hours are set, and satisfy 0 <= start < end <= 24
    ({"garage_fee_start_h": None, "garage_fee_end_h": None}, "garage_fee_start_h: a banded garage needs its fee window"),
    ({"garage_fee_end_h": None}, "garage_fee_end_h: a banded garage needs its fee window"),
    ({"garage_fee_start_h": 12.0, "garage_fee_end_h": 10.0}, "fee window 12.0 .. 10.0 h"),
    ({"garage_fee_end_h": 24.5}, "fee window 0.0 .. 24.5 h"),
    # a cap is a positive whole-cent amount like every cap
    ({"garage_daily_cap_eur": 0.0}, "garage_daily_cap_eur: must be a positive amount"),
    ({"garage_daily_cap_eur": 4.005}, "not a whole number of cents"),
    # the status follows the tariff form and ASSUMPTION P8 belongs to the bands
    ({"priced": False}, "priced is False but the garage tariff"),
    ({"tariff_rule_ids": None}, "tariff_rule_ids: a priced garage names the package rules"),
    ({"assumptions": "P4;P5", "notes": "ASSUMPTION P4. ASSUMPTION P5."}, "a banded garage rests on ASSUMPTION P8"),
    ({"assumptions": "P4;P5;P6;P8", "notes": "ASSUMPTION P4. ASSUMPTION P5. ASSUMPTION P6. ASSUMPTION P8."},
     "ASSUMPTION P6 prices a stay from tariff_tiers, but this garage has none"),
    ({"assumptions": "P4;P5;P8", "notes": "ASSUMPTION P4. ASSUMPTION P5."}, "the notes must name ASSUMPTION P8"),
])
def test_the_validator_rejects_a_broken_banded_garage(changes, message):
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_banded(**changes)))


# --------------------------------------------------------------------------- the grace period (ASSUMPTION P10, spec E12)


def test_a_grace_period_garage_is_a_banded_garage_with_a_free_first_band_and_assumption_p10():
    pg.validate_garages(_frame(_graced()))
    bands = pg.parse_duration_bands(GRACE_BANDS)
    # a stay not longer than the free period costs 0; a longer stay is billed from the arrival (the minutes are not deducted)
    assert [pg.duration_band_price_eur(bands, minutes) for minutes in (0, 1, 15, 16, 60, 61, 120, 121)] == [
        0.0, 0.0, 0.0, 1.5, 1.5, 3.0, 3.0, 4.5]
    assert pg.duration_band_price_eur(bands, 24 * 60, 18.0) == 18.0  # the day cap acts on the schedule price


@pytest.mark.parametrize("changes, message", [
    # P10 reads a free first band: without bands, or without a free first band, there is no grace period
    ({"tariff_duration_bands": "0-15 total 1.00; 15- 1.50/60"},
     "ASSUMPTION P10 reads the free first band of tariff_duration_bands as a grace period, but this garage's first band is "
     "not free"),
    ({"tariff_duration_bands": None, "garage_hourly_rate_eur": 1.5, "garage_billing_unit_min": 60,
      "assumptions": "P8;P10"}, "ASSUMPTION P10 reads the free first band of tariff_duration_bands as a grace period"),
    ({"assumptions": "P5;P8;P10", "notes": "ASSUMPTION P5. ASSUMPTION P8."}, "the notes must name ASSUMPTION P10"),
])
def test_the_validator_rejects_assumption_p10_without_a_free_first_band(changes, message):
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_graced(**changes)))


# ----------------------------------------------------------------- the free schedule and the tiered grace period (spec E14)


def test_a_free_car_park_is_the_one_open_free_band_and_needs_no_assumption_and_a_municipal_default_names_p12():
    # '0- free' is a valid schedule of the existing grammar: no stay, however long, costs anything
    bands = pg.parse_duration_bands(FREE_BANDS)
    assert [(band.from_min, band.to_min, band.kind, band.eur) for band in bands] == [(0, None, "free", 0.0)]
    assert [pg.duration_band_price_eur(bands, minutes) for minutes in (0, 1, 30, 600, 20_000)] == [0.0] * 5
    pg.validate_garages(_frame(_free()))
    pg.validate_garages(_frame(_free_default(), _free(), _row(), _banded(), _graced_tiers(), _unpriced()))
    assert pg.is_free_schedule(bands) and not pg.is_free_schedule(pg.parse_duration_bands(GRACE_BANDS))
    assert not pg.is_free_schedule(pg.parse_duration_bands("0-30 free"))  # a closed free band is a grace period, not a car park


@pytest.mark.parametrize("changes, message", [
    # nothing is read cumulatively and nothing follows a free schedule, so neither P8 nor P10 belongs to it
    ({"assumptions": "P8", "notes": "ASSUMPTION P8."}, "ASSUMPTION P8 prices a stay from a duration schedule with a priced band"),
    ({"assumptions": "P10", "notes": "ASSUMPTION P10."}, "nothing follows the free band"),
    # P12 frees a car park: it belongs to the free schedule only
    ({"tariff_duration_bands": "0-30 free; 30- 1.00/60", "assumptions": "P8;P12", "notes": "ASSUMPTION P8. ASSUMPTION P12."},
     "ASSUMPTION P12 frees a car park"),
    ({"assumptions": "P12"}, "the notes must name ASSUMPTION P12"),
    # the banded rules still hold: the formal fee window and the empty rate
    ({"garage_fee_start_h": None, "garage_fee_end_h": None}, "a banded garage needs its fee window"),
    ({"garage_hourly_rate_eur": 1.0}, "a banded garage leaves the rate and the billing unit empty"),
    ({"tariff_rule_ids": None}, "a priced garage names the package rules"),
])
def test_the_validator_rejects_a_broken_free_car_park(changes, message):
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_free(**changes)))


def test_p12_belongs_to_the_free_schedule_alone():
    with pytest.raises(ValueError, match="ASSUMPTION P12 frees a car park"):
        pg.validate_garages(_frame(_graced_tiers(assumptions="P4;P6;P10;P12",
                                                 notes="ASSUMPTION P4. ASSUMPTION P6. ASSUMPTION P10. ASSUMPTION P12.")))
    with pytest.raises(ValueError, match="ASSUMPTION P12 frees a car park"):
        pg.validate_garages(_frame(_row(assumptions="P12", notes="ASSUMPTION P12.")))


def test_a_tiered_garage_may_carry_one_closed_free_band_as_its_grace_period():
    pg.validate_garages(_frame(_graced_tiers()))
    pg.validate_garages(_frame(_graced_tiers(garage_daily_cap_eur=6.0)))
    assert pg.is_grace_period(pg.parse_duration_bands("0-30 free"))
    assert not pg.is_grace_period(pg.parse_duration_bands("0- free"))  # open ended: a free car park
    assert not pg.is_grace_period(pg.parse_duration_bands("0-30 free; 30-60 total 1.00"))  # a schedule with a price


@pytest.mark.parametrize("changes, message", [
    # only a grace period (one closed free band) may stand next to tiers
    ({"tariff_duration_bands": "0-30 free; 30-60 total 1.00", "assumptions": "P4;P6;P10;P8",
      "notes": "ASSUMPTION P4. ASSUMPTION P6. ASSUMPTION P10. ASSUMPTION P8."},
     "a tiered garage carries duration bands only as a grace period"),
    ({"tariff_duration_bands": "0- free"}, "a tiered garage carries duration bands only as a grace period"),
    ({"tariff_duration_bands": "0-30 total 1.00"}, "a tiered garage carries duration bands only as a grace period"),
    # the grace period is ASSUMPTION P10, the tiers are P6, and no schedule is read cumulatively (no P8)
    ({"assumptions": "P4;P6", "notes": "ASSUMPTION P4. ASSUMPTION P6."},
     "a tiered garage with a grace period rests on ASSUMPTION P10"),
    ({"assumptions": "P4;P6;P8;P10", "notes": "ASSUMPTION P4. ASSUMPTION P6. ASSUMPTION P8. ASSUMPTION P10."},
     "ASSUMPTION P8 prices a stay from a duration schedule with a priced band"),
    ({"assumptions": "P4;P10", "notes": "ASSUMPTION P4. ASSUMPTION P10."}, "a tiered garage rests on ASSUMPTION P6"),
    # the single-window core stays empty and a banded first period is none (the first band is the grace period)
    ({"garage_fee_start_h": 6.0, "garage_fee_end_h": 18.0}, "a tiered garage leaves the single-window core"),
    ({"garage_first_period_min": 60, "garage_first_period_eur": 1.0}, "has no first period"),
])
def test_the_validator_rejects_a_broken_tiered_garage_with_a_grace_period(changes, message):
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_graced_tiers(**changes)))


def test_the_coverage_counts_the_banded_the_free_and_the_tiered_garages_with_a_grace_period_apart():
    frame = _frame(_banded(garage_id="a"), _banded(garage_id="b"), _free(garage_id="c"), _free_default(garage_id="d"),
                   _free_default(garage_id="e"), _graced_tiers(garage_id="f"), _tiered(garage_id="g"),
                   _graced(garage_id="h"), _unpriced(garage_id="i"))
    summary = pg.coverage(frame)
    # banded: a priced schedule without tiers (a, b and the grace-period schedule h); free: the open free band (c, d, e);
    # tiered: every row with tiers, the one with a grace period included (f, g)
    assert (summary["priced_banded"], summary["priced_free"], summary["priced_tiered"]) == (3, 3, 2)
    assert summary["priced_by_assumption"] == {"P4": 3, "P5": 3, "P6": 2, "P8": 3, "P10": 2, "P12": 2}
    assert summary["priced_with_assumption"] == 7 and summary["priced_with_p4_or_p5"] == 4


# --------------------------------------------------------------------------- the packages of a row (spec E12)


def test_a_row_may_cite_the_regional_package_and_the_two_supplement_packages_by_their_sha256_values():
    pg.validate_garages(_frame(_row(package_sha256=f"{SHA};{SUPPLEMENT_SHA}")))
    pg.validate_garages(_frame(_row(package_sha256=f"{SHA};{SUPPLEMENT_SHA};{FOLLOWUP_SHA}")))
    pg.validate_garages(_frame(_row(package_sha256=SHA)))  # a row that the supplement does not touch cites one package
    # Task 4b3 (ruling R-4b3-0): the Wolfsburg car-park package of 2026-10-07 is the fourth package a row may cite
    pg.validate_garages(_frame(_row(package_sha256=f"{SHA};{WOLFSBURG_LOTS_SHA}")))
    pg.validate_garages(_frame(_row(package_sha256=f"{SHA};{SUPPLEMENT_SHA};{FOLLOWUP_SHA};{WOLFSBURG_LOTS_SHA}")))
    assert pg.MAXIMUM_PACKAGE_HASHES == 4


@pytest.mark.parametrize("value, message", [
    (f"{SHA};E789", "not a lower-case hexadecimal SHA-256"),
    (f"{SHA};{SHA}", "lists a package SHA-256 twice"),
    (f"{SHA};{SUPPLEMENT_SHA};{FOLLOWUP_SHA};{WOLFSBURG_LOTS_SHA};{SHA[::-1]}", "at most four package SHA-256 values"),
    (f"{SHA}, {SUPPLEMENT_SHA}", "not a lower-case hexadecimal SHA-256"),
])
def test_a_malformed_duplicated_or_too_long_package_sha256_list_is_rejected(value, message):
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_row(package_sha256=value)))


def test_a_day_cap_or_a_first_period_alone_never_prices_a_garage():
    for changes in ({"garage_daily_cap_eur": 9.6}, {"garage_first_period_min": 60, "garage_first_period_eur": 0.6}):
        with pytest.raises(ValueError, match="needs the complete garage core, tariff_tiers or tariff_duration_bands"):
            pg.validate_garages(_frame(_tiered(tariff_tiers=None, priced=False, assumptions=None,
                                               not_priced_reason="incomplete_tariff", **changes)))


def test_every_priced_core_value_may_be_set_without_the_optional_parts():
    # the day cap (empty = none) and the first-period pair are optional (spec Amendment A6), the core is not
    pg.validate_garages(_frame(_row(garage_daily_cap_eur=None, garage_first_period_min=None,
                                    garage_first_period_eur=None, assumptions="P5",
                                    notes="Fee window 0 to 24 h (ASSUMPTION P5).")))


def test_a_monthly_product_needs_no_priced_tariff_but_its_source():
    pg.validate_garages(_frame(_unpriced(monthly_eur=48.0, monthly_source_url="https://example.org/m",
                                         monthly_product="Dauerstellplatz, minimum term 2 months")))


def test_duplicate_ids_are_rejected_and_every_violation_is_listed():
    broken = _frame(_row(), _row(garage_hourly_rate_eur=-1.0, priced=False, source_date="x"))
    with pytest.raises(ValueError) as error:
        pg.validate_garages(broken)
    message = str(error.value)
    assert "duplicate garage_id(s) ['bs_eiermarkt']" in message
    assert "must be a positive amount" in message and "priced is False" in message and "not an ISO date" in message


def test_the_validator_needs_metric_points_and_the_documented_columns():
    with pytest.raises(ValueError, match="no garages"):
        pg.validate_garages(_frame(_row()).iloc[0:0])
    with pytest.raises(ValueError, match="must be in EPSG:25832"):
        pg.validate_garages(_frame(_row(x=10.52, y=52.26), crs="EPSG:4326"))
    frame = _frame(_row())
    with pytest.raises(ValueError, match=r"missing \['notes'\]"):
        pg.validate_garages(frame.drop(columns=["notes"]))
    frame["extra"] = 1
    with pytest.raises(ValueError, match=r"unexpected \['extra'\]"):
        pg.validate_garages(frame)


def test_the_extent_box_holds_every_zgb_municipality_and_excludes_the_neighbouring_utm_zone():
    min_x, min_y, max_x, max_y = pg.ZGB_EXTENT_25832
    # the eight ZGB counties span x 567,953 to 642,483 m and y 5,722,106 to 5,854,930 m (VG250 as cached)
    assert min_x <= 567_953 and max_x >= 642_484 and min_y <= 5_722_106 and max_y >= 5_854_930
    assert max_x - min_x < 100_000 and max_y - min_y < 140_000
    pg.validate_garages(_frame(_row(x=min_x, y=min_y), _row(garage_id="b", x=max_x, y=max_y)))
    with pytest.raises(ValueError, match="outside the ZGB extent"):
        pg.validate_garages(_frame(_row(x=min_x - 1.0, y=min_y)))


# --------------------------------------------------------------------------- coverage


def test_the_coverage_counts_garages_reasons_towns_assumptions_and_monthly_products():
    frame = _frame(
        _row(garage_id="a", assumptions="P3"),
        _row(garage_id="b", assumptions="P4;P5", monthly_eur=50.0, monthly_source_url="https://x.org/m", monthly_product="x"),
        _row(garage_id="c", municipality_ags="03103000", assumptions="P5"),
        _unpriced(garage_id="d", municipality_ags="03103000"),
        _unpriced(garage_id="e", not_priced_reason="free_period"),
        _unpriced(garage_id="f", municipality_ags="03153017", not_priced_reason="no_published_tariff"),
    )
    summary = pg.coverage(frame)
    assert (summary["listed"], summary["priced"], summary["not_priced"]) == (6, 3, 3)
    assert summary["not_priced_by_reason"] == {"incomplete_tariff": 1, "free_period": 1, "no_published_tariff": 1}
    assert summary["by_municipality"] == {"03101000": {"listed": 3, "priced": 2, "not_priced": 1},
                                          "03103000": {"listed": 2, "priced": 1, "not_priced": 1},
                                          "03153017": {"listed": 1, "priced": 0, "not_priced": 1}}
    assert summary["priced_by_assumption"] == {"P3": 1, "P4": 1, "P5": 2}
    assert summary["with_monthly_product"] == 1
    assert (summary["priced_with_assumption"], summary["priced_with_p4_or_p5"], summary["priced_tiered"]) == (3, 2, 0)
    assert summary["priced_banded"] == 0
    # an unpriced garage's assumption (invalid anyway) never counts as a priced row's
    assert np.isclose(sum(summary["priced_by_assumption"].values()), 4)


def test_the_coverage_counts_the_union_rates_and_the_tiered_garages():
    frame = _frame(
        _row(garage_id="a", assumptions=None, notes="Stated in full."),
        _row(garage_id="b", assumptions="P3"),
        _row(garage_id="c", assumptions="P4;P5", notes="ASSUMPTION P4. ASSUMPTION P5."),
        _row(garage_id="d", assumptions="P5", notes="ASSUMPTION P5."),
        _tiered(garage_id="e"),
        _tiered(garage_id="f", assumptions="P4;P6;P7", notes="ASSUMPTION P4. ASSUMPTION P6. ASSUMPTION P7."),
        _banded(garage_id="h"),
        _banded(garage_id="i", assumptions="P8", notes="ASSUMPTION P8.", garage_fee_start_h=6.0, garage_fee_end_h=21.0),
        _unpriced(garage_id="g"))
    summary = pg.coverage(frame)
    assert summary["priced"] == 8 and summary["priced_tiered"] == 2 and summary["priced_banded"] == 2
    assert summary["priced_by_assumption"] == {"P3": 1, "P4": 3, "P5": 3, "P6": 2, "P7": 1, "P8": 2}
    # at least one assumption: b, c, d, e, f, h, i (a states everything); P4 or P5: c, d, f, h (b rests on P3, e on P6 only, i
    # on P8 only)
    assert summary["priced_with_assumption"] == 7 and summary["priced_with_p4_or_p5"] == 4


# --------------------------------------------------------------------------- the imputed monthly product (ASSUMPTION P13)

PUBLISHED = {"monthly_eur": 48.0, "monthly_source_url": "https://example.org/m", "monthly_product": "Dauerstellplatz"}


def _imputed(**changes) -> dict:
    """A valid priced garage with an imputed monthly product (ASSUMPTION P13 in its assumptions and its notes)."""
    base = {"garage_id": "bs_imputed", "assumptions": "P3;P13", "monthly_imputed_eur": 107.48,
            "notes": "Flat night fee not charged (ASSUMPTION P3). ASSUMPTION P13: the median of the published products."}
    base.update(changes)
    return _row(**base)


def test_an_imputed_monthly_product_with_assumption_p13_is_a_valid_row():
    pg.validate_garages(_frame(_imputed()))
    assert "P13" in pg.ASSUMPTIONS and pg.MINIMUM_PUBLISHED_MONTHLY_PRODUCTS == 2
    for phrase in ("monthly_imputed_eur", "median", "own municipality", "never a surface lot", "at least two"):
        assert phrase in pg.ASSUMPTIONS["P13"], phrase


@pytest.mark.parametrize("changes, message", [
    # a value only with P13 ...
    ({"assumptions": "P3"}, "an imputed monthly product rests on ASSUMPTION P13"),
    # ... and P13 only with a value
    ({"monthly_imputed_eur": None}, "ASSUMPTION P13 states an imputed monthly product, but monthly_imputed_eur is empty"),
    # never next to a published product
    ({"monthly_eur": 48.0, "monthly_source_url": "https://example.org/m", "monthly_product": "x"},
     "set next to a published monthly_eur"),
    # never at a surface lot
    ({"facility_kind": "surface_lot"}, "a surface_lot never gets an imputed monthly product"),
    # money rules apply: positive whole cents
    ({"monthly_imputed_eur": 0.0}, "monthly_imputed_eur: must be a positive amount"),
    ({"monthly_imputed_eur": 107.485}, "monthly_imputed_eur: 107.485 EUR is not a whole number of cents"),
    # the notes name the assumption
    ({"notes": "Flat night fee not charged (ASSUMPTION P3)."}, "the notes must name ASSUMPTION P13"),
], ids=["no_p13", "p13_without_value", "next_to_published", "surface_lot", "zero", "fraction_of_a_cent", "notes_silent"])
def test_the_validator_enforces_the_rules_of_the_imputed_monthly_product(changes, message):
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_imputed(**changes)))


def test_an_unpriced_garage_gets_no_imputed_monthly_product():
    with pytest.raises(ValueError, match="an unpriced garage is no option, so it gets no imputed monthly product"):
        pg.validate_garages(_frame(_unpriced(monthly_imputed_eur=50.0)))


@pytest.mark.parametrize("values, expected", [
    ([100.0], 100.0),
    ([80.0, 100.0, 129.0], 100.0),
    ([80.0, 100.0, 114.95, 129.0], 107.48),   # (100.00 + 114.95) / 2 = 107.475 -> half up -> 107.48 (spec F2, Braunschweig)
    ([50.0, 55.0, 60.0, 98.0], 57.5),         # Wolfsburg of spec F2
    ([48.0, 48.0], 48.0),
    ([0.01, 0.02], 0.02),                     # 1.5 ct -> 2 ct, half up
    ([129.0, 80.0, 114.95, 100.0], 107.48),   # order does not matter
])
def test_the_median_is_rounded_half_up_to_the_cent_in_integer_cents(values, expected):
    assert pg.monthly_median_eur(values) == expected


@pytest.mark.parametrize("values", [[], [0.0, 10.0], [-5.0], [10.005]], ids=["empty", "zero", "negative", "fraction"])
def test_the_median_refuses_what_is_no_published_monthly_product(values):
    with pytest.raises(ValueError):
        pg.monthly_median_eur(values)


def test_the_legacy_layout_without_the_imputed_column_loads_as_nothing_imputed_and_says_so(tmp_path, caplog):
    path = tmp_path / "garages.geojson"
    pg.write_garages(_frame(_row(**PUBLISHED), _row(garage_id="b")), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    for feature in document["features"]:
        del feature["properties"]["monthly_imputed_eur"]
    path.write_text(json.dumps(document), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=pg.log.name):
        loaded = pg.load_garages(path)
    assert loaded["monthly_imputed_eur"].isna().all() and loaded["monthly_imputed_eur"].dtype == float
    assert "lacks the column(s) ['monthly_imputed_eur']" in caplog.text and "no monthly product is imputed" in caplog.text
    pg.validate_garages(loaded)
    # any other missing column is still an error
    for feature in document["features"]:
        del feature["properties"]["notes"]
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match=r"missing \['notes'\]"):
        pg.load_garages(path)


def test_the_written_file_carries_the_imputed_product_and_the_loader_reads_it_back(tmp_path):
    path = tmp_path / "garages.geojson"
    pg.write_garages(_frame(_imputed(), _row(garage_id="b")), path)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["features"][0]["properties"]["monthly_imputed_eur"] == 107.48
    assert document["features"][1]["properties"]["monthly_imputed_eur"] is None
    loaded = pg.load_garages(path)
    assert loaded.loc[0, "monthly_imputed_eur"] == 107.48 and pd.isna(loaded.loc[1, "monthly_imputed_eur"])
    assert pg.coverage(loaded)["with_monthly_imputed"] == 1 and pg.coverage(loaded)["priced_by_assumption"]["P13"] == 1


def _municipal_frame():
    """Two municipalities: Braunschweig with a published product, an imputed one and one garage without any; Wolfsburg with an
    imputed garage, a surface lot (never imputed) and an unpriced garage."""
    return _frame(
        _row(garage_id="bs_published", **PUBLISHED),
        _imputed(garage_id="bs_imputed"),
        _row(garage_id="bs_none", assumptions=None, notes="Stated in full."),
        _imputed(garage_id="wob_imputed", municipality="Wolfsburg", municipality_ags="03103000", monthly_imputed_eur=57.5),
        _free(garage_id="wob_lot"),
        _unpriced(garage_id="wob_unpriced", municipality="Wolfsburg", municipality_ags="03103000"))


def test_the_summary_counts_published_imputed_and_none_per_municipality_with_the_median():
    summary = pg.monthly_summary(_municipal_frame())
    assert summary == {"03101000": {"municipality": "Braunschweig", "published": 1, "imputed": 1, "none": 1,
                                    "median_eur": 107.48, "surface_lots": 0},
                       "03103000": {"municipality": "Wolfsburg", "published": 0, "imputed": 1, "none": 0,
                                    "median_eur": 57.5, "surface_lots": 1}}
    text = pg.monthly_summary_text(summary)
    assert "Braunschweig (03101000) published 1, imputed 1, P13 median 107.48 EUR, none 1" in text
    assert "Wolfsburg (03103000) published 0, imputed 1, P13 median 57.50 EUR, none 0" in text
    assert "total published 1, imputed 2, none 1 (surface lots 1, never imputed)" in text


def test_the_summary_without_imputation_counts_the_published_products_only():
    summary = pg.monthly_summary(_municipal_frame(), imputation=False)
    assert [(entry["published"], entry["imputed"], entry["none"], entry["median_eur"]) for entry in summary.values()] == [
        (1, 0, 2, None), (0, 0, 1, None)]


def test_the_summary_refuses_two_medians_in_one_municipality():
    frame = _frame(_imputed(garage_id="a"), _imputed(garage_id="b", monthly_imputed_eur=108.0))
    with pytest.raises(ValueError, match="one median per municipality"):
        pg.monthly_summary(frame)


# --------------------------------------------------------------------------- QA table of the dataset and the monthly products


def _qa_row(**changes) -> dict:
    row = {"record_id": "garage_bs_eiermarkt", "record_type": "garage", "municipality_ags": "03101000",
           "subject": "Parkhaus Eiermarkt", "garage_id": "bs_eiermarkt", "zone_ids": "", "decision": "priced",
           "reason_code": "", "amount_eur": "", "count": "1", "evidence": "r-35;r-36;r-37",
           "note": "day family 7 to 18 h"}
    row.update(changes)
    return row


def _qa(*rows) -> pd.DataFrame:
    return pd.DataFrame([{column: str(row.get(column, "")) for column in pq.GARAGE_QA_COLUMNS} for row in rows],
                        columns=list(pq.GARAGE_QA_COLUMNS))


def _qa_tables():
    """A dataset of one priced garage with a monthly product and one unpriced garage, the matching QA rows and a tariff
    table with one zone commuter product: 79 EUR / 21 working days = 3.76 EUR (ASSUMPTION P2)."""
    garages = _frame(_row(monthly_eur=48.0, monthly_source_url="https://example.org/m", monthly_product="Dauerstellplatz"),
                     _unpriced())
    rows = [_qa_row(),
            _qa_row(record_id="garage_bs_ring_center", subject="Parkhaus Ring-Center", garage_id="bs_ring_center",
                    decision="not_priced", reason_code="incomplete_tariff"),
            _qa_row(record_id="monthly_bs_eiermarkt", record_type="monthly_product", decision="used", amount_eur="48.0",
                    evidence="r-m1", note="Dauerstellplatz, minimum term 2 months"),
            _qa_row(record_id="monthly_bs_eiermarkt_plus", record_type="monthly_product", decision="not_used",
                    reason_code="not_the_cheapest", amount_eur="60.0", evidence="r-m2", note="Tag/Nacht"),
            _qa_row(record_id="monthly_bs_zone_ib", record_type="monthly_product", garage_id="", zone_ids="bs_zone_ib",
                    decision="used", amount_eur="79.0", evidence="bs_zone_ib_30D", note="30-day ticket"),
            _qa_row(record_id="candidate_hauptbahnhof_nord", record_type="candidate", garage_id="", decision="not_listed",
                    reason_code="station_bahnpark", subject="Parkplatz Hauptbahnhof Nord", evidence="directory", note="x"),
            _qa_row(record_id="candidate_wob_lots", record_type="candidate", garage_id="", municipality_ags="03103000",
                    decision="not_listed", reason_code="no_published_tariff", count="24", subject="24 car parks",
                    evidence="wob_parkplaetze", note="no tariff")]
    tariffs = pd.DataFrame({"zone_id": ["bs_zone_ib", "bs_zone_ia"], "commuter_day_eur": [3.76, np.nan]})
    return garages, _qa(*rows), tariffs


def test_a_consistent_qa_table_passes_with_and_without_the_tariff_table():
    garages, qa, tariffs = _qa_tables()
    pq.validate_garage_qa(qa, garages)
    pq.validate_garage_qa(qa, garages, tariffs)
    assert pq.WORKING_DAYS_PER_MONTH == 21 and round(79 / pq.WORKING_DAYS_PER_MONTH, 2) == 3.76


def _drop(record_id):
    return lambda qa: qa[qa["record_id"] != record_id]


def _set(target, /, **changes):
    """An edit of the QA row whose record_id is ``target``."""
    def edit(qa):
        qa = qa.copy()
        for column, value in changes.items():
            qa.loc[qa["record_id"] == target, column] = value
        return qa
    return edit


@pytest.mark.parametrize("edit, message", [
    (_set("garage_bs_eiermarkt", record_type="garden"), "record_type 'garden' is not one of"),
    (_set("garage_bs_eiermarkt", decision="used"), "decision 'used' is not one of"),
    (_set("garage_bs_ring_center", reason_code=""), "reason_code '' is not one of"),
    (_set("garage_bs_ring_center", reason_code="too_hard"), "reason_code 'too_hard' is not one of"),
    (_set("garage_bs_eiermarkt", reason_code="incomplete_tariff"), "reason_code 'incomplete_tariff' on a row without a reason"),
    (_set("monthly_bs_eiermarkt", reason_code="not_the_cheapest"), "on a row without a reason"),
    (_set("monthly_bs_eiermarkt_plus", reason_code="no_such_reason"), "reason_code 'no_such_reason' is not one of"),
    (_set("candidate_wob_lots", count="0"), "count '0' must be a positive whole number"),
    (_set("candidate_wob_lots", count="two"), "must be a positive whole number"),
    (_set("monthly_bs_eiermarkt_plus", amount_eur="-5"), "amount_eur '-5' must be a number >= 0"),
    (_set("monthly_bs_eiermarkt", amount_eur=""), "a used product states its amount_eur"),
    (_set("garage_bs_eiermarkt", evidence=""), "evidence is empty"),
    (_set("garage_bs_eiermarkt", note=""), "note is empty"),
    (_set("garage_bs_eiermarkt", subject=""), "subject is empty"),
    (_set("garage_bs_eiermarkt", record_id="Garage 1"), "record_id uses other than"),
    # against the dataset
    (_drop("garage_bs_ring_center"), "garage 'bs_ring_center' of the dataset has no garage row"),
    (_set("garage_bs_ring_center", garage_id="bs_unknown"), "garage row for 'bs_unknown', which the dataset does not hold"),
    (_set("garage_bs_eiermarkt", decision="not_priced", reason_code="incomplete_tariff"),
     "the QA decision is 'not_priced' but the dataset says priced True"),
    (_set("garage_bs_ring_center", reason_code="free_period"),
     "the QA reason_code is 'free_period' but the dataset says 'incomplete_tariff'"),
    (_set("garage_bs_ring_center", municipality_ags="03103000"), "the QA municipality '03103000' differs"),
    (_drop("monthly_bs_eiermarkt"), "has a monthly_eur but no used monthly product"),
    (_set("monthly_bs_eiermarkt", amount_eur="50.0"),
     "the used monthly product is '50.0' EUR but the dataset's monthly_eur is 48.0"),
    (_set("monthly_bs_eiermarkt_plus", decision="used", reason_code=""), "has 2 used monthly products"),
    (_set("monthly_bs_eiermarkt", garage_id="bs_ring_center"),
     "the used monthly product is '48.0' EUR but the dataset's monthly_eur is None"),
    (_set("monthly_bs_eiermarkt", garage_id="bs_nowhere"),
     "used monthly product of 'bs_nowhere', which the dataset does not hold"),
])
def test_the_garage_qa_validator_rejects_a_table_that_contradicts_itself_or_the_dataset(edit, message):
    garages, qa, tariffs = _qa_tables()
    with pytest.raises(ValueError, match=message):
        pq.validate_garage_qa(edit(qa), garages, tariffs)


def test_a_duplicate_record_id_and_a_second_garage_row_are_rejected():
    garages, qa, _ = _qa_tables()
    doubled = pd.concat([qa, qa[qa["record_id"] == "garage_bs_eiermarkt"]], ignore_index=True)
    with pytest.raises(ValueError) as error:
        pq.validate_garage_qa(doubled, garages)
    assert "duplicate record_id(s) ['garage_bs_eiermarkt']" in str(error.value)
    assert "garage 'bs_eiermarkt' has several garage rows" in str(error.value)


@pytest.mark.parametrize("edit, message", [
    (_set("monthly_bs_zone_ib", amount_eur="84.0"),
     "zone 'bs_zone_ib': commuter_day_eur is 3.76 but the used product 'monthly_bs_zone_ib' gives 84.0 EUR / 21 working "
     "days = 4.00 EUR"),
    (_set("monthly_bs_zone_ib", zone_ids="bs_zone_ia"), "zone 'bs_zone_ia': commuter_day_eur is nan but"),
    (_set("monthly_bs_zone_ib", zone_ids="bs_zone_ib;bs_zone_xx"), "zone 'bs_zone_xx' is not in the tariff table"),
    (_set("monthly_bs_zone_ib", decision="not_used", reason_code="not_the_cheapest"),
     "zone 'bs_zone_ib' has a commuter_day_eur that no used monthly product of the QA table explains"),
])
def test_the_commuter_day_amounts_of_the_tariff_table_are_the_monthly_amount_over_21_working_days(edit, message):
    garages, qa, tariffs = _qa_tables()
    with pytest.raises(ValueError, match=message):
        pq.validate_garage_qa(edit(qa), garages, tariffs)


def test_the_qa_coverage_counts_products_and_candidates_by_reason():
    garages, qa, _ = _qa_tables()
    assert pq.qa_coverage(qa) == {
        "monthly_used": 2, "monthly_not_used": 1, "monthly_not_used_by_reason": {"not_the_cheapest": 1},
        "candidates": 25, "candidates_by_reason": {"no_published_tariff": 24, "station_bahnpark": 1}}


def _zone_candidate(facility="524303", zone_id="wob_tarifzone_1", reference="2.00", **changes) -> dict:
    """A Wolfsburg car park inside a zone (class a, spec E14): the candidate row names the zone and the published hourly
    reference of the car park's tariff area in EUR per hour."""
    row = _qa_row(record_id=f"candidate_wob_lot_{facility}", record_type="candidate", garage_id="",
                  municipality_ags="03103000", zone_ids=zone_id, decision="not_listed", reason_code="zone_street_product",
                  amount_eur=reference, subject=f"Parkplatz ({facility})", evidence=f"WOB_PARK_{facility}",
                  note="inside the zone; the consistency check is in the note")
    row.update(changes)
    return row


def _wolfsburg_tariffs() -> pd.DataFrame:
    return pd.DataFrame({"zone_id": ["wob_tarifzone_1", "wob_tarifzone_2", "bs_zone_ib"], "hourly_rate_eur": [2.0, 1.2, 1.8],
                         "commuter_day_eur": [np.nan, np.nan, 3.76]})


def test_the_published_hourly_reference_of_a_car_park_inside_a_zone_is_compared_with_the_street_rate_of_the_zone():
    garages, qa, _ = _qa_tables()
    rows = [_zone_candidate(), _zone_candidate("983050", "wob_tarifzone_2", "1.20"),
            _zone_candidate("1245204", "wob_tarifzone_2", "1.00")]  # the third differs: 1.00 against 1.20 EUR per hour
    qa = pd.concat([qa, _qa(*rows)], ignore_index=True)
    tariffs = _wolfsburg_tariffs()
    # a difference is reported, never an error: the zone tariff is not changed by the car park (spec E14)
    pq.validate_garage_qa(qa, garages, tariffs)
    checks = pq.zone_reference_check(qa, tariffs)
    assert [(check["record_id"], check["zone_id"], check["reference_eur"], check["zone_rate_eur"], check["equal"])
            for check in checks] == [("candidate_wob_lot_524303", "wob_tarifzone_1", 2.0, 2.0, True),
                                     ("candidate_wob_lot_983050", "wob_tarifzone_2", 1.2, 1.2, True),
                                     ("candidate_wob_lot_1245204", "wob_tarifzone_2", 1.0, 1.2, False)]
    # the candidates of the directory without a zone and an amount (a ParkGO car park of Braunschweig) are no check
    qa = pd.concat([qa, _qa(_qa_row(record_id="candidate_bs_werder", record_type="candidate", garage_id="", decision="not_listed",
                                    reason_code="zone_street_product", subject="Parkplatz Werder", evidence="directory",
                                    note="ParkGO zone 1"))], ignore_index=True)
    pq.validate_garage_qa(qa, garages, tariffs)
    assert len(pq.zone_reference_check(qa, tariffs)) == 3
    summary = pq.zone_reference_summary(qa, tariffs)
    assert summary == {"checked": 3, "equal": 2, "differing": ["candidate_wob_lot_1245204"]}
    assert pq.zone_reference_summary(_qa(_qa_row()), tariffs) == {"checked": 0, "equal": 0, "differing": []}


@pytest.mark.parametrize("changes, message", [
    ({"zone_ids": "wob_tarifzone_9"}, "zone 'wob_tarifzone_9' is not in the tariff table"),
    ({"zone_ids": "wob_tarifzone_1;wob_tarifzone_2"}, "names one zone"),
    ({"amount_eur": ""}, "states the published hourly reference"),
    ({"zone_ids": ""}, "names the zone"),
])
def test_a_car_park_inside_a_zone_names_its_zone_and_its_reference_and_the_zone_must_exist(changes, message):
    garages, qa, _ = _qa_tables()
    qa = pd.concat([qa, _qa(_zone_candidate(**changes))], ignore_index=True)
    with pytest.raises(ValueError, match=message):
        pq.validate_garage_qa(qa, garages, _wolfsburg_tariffs())


def test_the_qa_vocabulary_excludes_the_station_car_parks_of_both_cities_and_the_lots_of_long_term_renters():
    assert set(pq.CANDIDATE_REASONS) == {"bga_zone", "zone_street_product", "station_bahnpark", "customer_regime",
                                         "no_coordinates", "no_published_tariff", "outside_source_list", "dauerparker_only",
                                         "user_group_only"}
    assert "reserved for a user group" in pq.CANDIDATE_REASONS["user_group_only"]
    station = pq.CANDIDATE_REASONS["station_bahnpark"]
    assert "Braunschweig and Wolfsburg alike" in station and "R-4b-4" in station and "R-4b-9" in station
    assert "long-term renters only" in pq.CANDIDATE_REASONS["dauerparker_only"]
    assert set(pq.MONTHLY_NOT_USED_REASONS) == {
        "not_the_cheapest", "restricted_customer_group", "no_fixed_price", "capacity_limited_permits", "no_coordinates",
        "garage_not_listed", "not_monthly_or_30_day", "outdated_source",
        # spec Amendment F1: the monthly status of the Braunschweig garages and the recorded facilities
        "sold_out", "no_price", "price_on_request", "period_unconfirmed", "excluded_by_package", "not_a_dataset_option",
        "station_bahnpark", "surface_lot"}
    assert pq.RECORDED_GARAGE_REASON == "not_a_dataset_option"
    garages, qa, tariffs = _qa_tables()
    extra = _qa(
        _qa_row(record_id="candidate_wf_parkpalette_karlstrasse", record_type="candidate", garage_id="",
                municipality_ags="03158037", decision="not_listed", reason_code="dauerparker_only",
                subject="Parkpalette Karlstrasse", evidence="stadtbetriebe-wf.de/parkhaeuser.html", note="long-term only"),
        _qa_row(record_id="candidate_wob_hauptbahnhof", record_type="candidate", garage_id="", municipality_ags="03103000",
                decision="not_listed", reason_code="station_bahnpark", subject="Parkdeck Hauptbahnhof",
                evidence="facilities.json WOB_HAUPTBAHNHOF", note="DB BahnPark"),
        _qa_row(record_id="monthly_wob_hauptbahnhof_24h", record_type="monthly_product", garage_id="",
                municipality_ags="03103000", decision="not_used", reason_code="garage_not_listed", amount_eur="100.0",
                subject="Parkdeck Hauptbahnhof Dauerparken", evidence="r-hbf-m1", note="the garage is a station BahnPark"))
    pq.validate_garage_qa(pd.concat([qa, extra], ignore_index=True), garages, tariffs)
    coverage = pq.qa_coverage(pd.concat([qa, extra], ignore_index=True))
    assert coverage["candidates_by_reason"] == {"dauerparker_only": 1, "no_published_tariff": 24, "station_bahnpark": 2}
    assert coverage["monthly_not_used_by_reason"] == {"garage_not_listed": 1, "not_the_cheapest": 1}


def test_the_qa_table_loads_from_a_documented_csv(tmp_path):
    garages, qa, _ = _qa_tables()
    path = tmp_path / "qa.csv"
    path.write_text("# QA of the garage dataset\n# garage_id: the id\n" + qa.to_csv(index=False, lineterminator="\n"),
                    encoding="utf-8")
    loaded = pq.load_garage_qa(path)
    assert list(loaded.columns) == list(pq.GARAGE_QA_COLUMNS) and len(loaded) == len(qa)
    pq.validate_garage_qa(loaded, garages)
    path.write_text("# QA\nrecord_id,record_type\nx,garage\n", encoding="utf-8")
    with pytest.raises(ValueError, match="columns differ from the documented layout"):
        pq.load_garage_qa(path)


# --------------------------------------------------------------------------- ASSUMPTION P13 in the QA table (spec Amendment F2)


def _product_row(record_id, decision="not_used", reason="", amount="", garage_id="", ags="03101000", **changes) -> dict:
    row = _qa_row(record_id=record_id, record_type="monthly_product", garage_id=garage_id, municipality_ags=ags,
                  decision=decision, reason_code=reason, amount_eur=amount, subject=record_id, evidence="package",
                  note="a product")
    row.update(changes)
    return row


def _p13_tables(imputed=107.48):
    """Braunschweig as in spec F2: two published garage products (100.00 and 114.95), two recorded garages that are no option
    (80.00 and 129.00), one imputed garage; products that never count (a station car park, a surface lot, an amount without a
    confirmed period, a zone ticket) and a municipality with one published product (Goslar)."""
    published = {"monthly_source_url": "https://example.org/m", "monthly_product": "Dauerparken"}
    garages = _frame(
        _row(garage_id="bs_steinstrasse", monthly_eur=100.0, **published),
        _row(garage_id="bs_wallstrasse", monthly_eur=114.95, **published),
        _imputed(garage_id="bs_magni", monthly_imputed_eur=imputed),
        _row(garage_id="gs_galeria", municipality="Goslar", municipality_ags="03153017", monthly_eur=39.0, **published),
        _row(garage_id="gs_ca", municipality="Goslar", municipality_ags="03153017"))
    rows = [_qa_row(record_id=f"garage_{garage_id}", garage_id=garage_id, subject=garage_id,
                    municipality_ags="03153017" if garage_id.startswith("gs_") else "03101000")
            for garage_id in garages["garage_id"]]
    rows += [
        _product_row("monthly_bs_steinstrasse_a", "used", amount="100.00", garage_id="bs_steinstrasse"),
        _product_row("monthly_bs_wallstrasse", "used", amount="114.95", garage_id="bs_wallstrasse"),
        _product_row("monthly_bs_eves", reason="not_a_dataset_option", amount="80.00"),
        _product_row("monthly_bs_fichtengrund", reason="not_a_dataset_option", amount="129.00"),
        _product_row("monthly_bs_hbf_p1", reason="station_bahnpark", amount="120.00"),
        _product_row("monthly_bs_apcoa_s1", reason="surface_lot", amount="99.00"),
        _product_row("monthly_bs_wilhelmstrasse_o01", reason="period_unconfirmed", amount="75.00"),
        _product_row("monthly_bs_forschungsflughafen_hidden", reason="excluded_by_package", amount="85.00"),
        _product_row("monthly_bs_magni_sold_out", reason="sold_out", garage_id="bs_magni"),
        _product_row("monthly_bs_zone_ib", "used", amount="79.00", zone_ids="bs_zone_ib"),
        _product_row("monthly_gs_galeria", "used", amount="39.00", garage_id="gs_galeria", ags="03153017")]
    return garages, _qa(*rows)


def test_the_published_current_garage_products_are_the_used_products_of_the_garages_and_the_recorded_garages():
    garages, qa = _p13_tables()
    values = pq.published_monthly_values(qa, garages)
    assert values == {"03101000": [(80.0, "monthly_bs_eves"), (100.0, "monthly_bs_steinstrasse_a"),
                                   (114.95, "monthly_bs_wallstrasse"), (129.0, "monthly_bs_fichtengrund")],
                      "03153017": [(39.0, "monthly_gs_galeria")]}
    # a station car park, a surface lot, an unconfirmed period, a hidden amount, a sold-out product and a zone ticket never count
    assert pq.expected_imputed_monthly(qa, garages) == {"03101000": 107.48}


def test_a_surface_lot_with_a_used_product_is_no_garage_product():
    garages, qa = _p13_tables()
    garages = pd.concat([garages, _frame(_free(garage_id="wob_lot", monthly_eur=None))], ignore_index=True)
    garages = gpd.GeoDataFrame(garages, geometry="geometry", crs="EPSG:25832")
    extra = _qa(_product_row("monthly_wob_lot", "used", amount="40.00", garage_id="wob_lot", ags="03103000"))
    values = pq.published_monthly_values(pd.concat([qa, extra], ignore_index=True), garages)
    assert "03103000" not in values


def test_the_qa_validator_accepts_the_p13_median_and_nothing_imputed_below_two_published_products():
    garages, qa = _p13_tables()
    pq.validate_garage_qa(qa, garages)


@pytest.mark.parametrize("imputed, message", [
    (107.47, r"monthly_imputed_eur is 107.47 but the median of the published garage monthly products of municipality 03101000 "
             r"in the QA table is 107.48 EUR"),
    (None, "is None but the median"),
], ids=["stale_median", "missing_value"])
def test_the_qa_validator_rejects_a_stale_or_missing_imputed_product(imputed, message):
    garages, qa = _p13_tables(imputed=imputed)
    if imputed is None:
        garages["assumptions"] = "P3"
        garages["notes"] = "Flat night fee not charged (ASSUMPTION P3)."
    with pytest.raises(ValueError, match=message):
        pq.validate_garage_qa(qa, garages)


def test_the_qa_validator_rejects_an_imputed_product_in_a_municipality_with_fewer_than_two_published_products():
    garages, qa = _p13_tables()
    garages.loc[garages["garage_id"] == "gs_ca", ["monthly_imputed_eur", "assumptions", "notes"]] = [
        39.0, "P3;P13", "Flat night fee not charged (ASSUMPTION P3). ASSUMPTION P13: copied."]
    with pytest.raises(ValueError, match=r"gs_ca': monthly_imputed_eur is 39.0 but the municipality 03153017 has fewer than 2"):
        pq.validate_garage_qa(qa, garages)


def test_a_published_product_that_is_withdrawn_changes_the_median_the_validator_expects():
    garages, qa = _p13_tables()
    shorter = qa[qa["record_id"] != "monthly_bs_fichtengrund"]
    # the median of 80.00, 100.00 and 114.95 is 100.00: the committed 107.48 is stale
    with pytest.raises(ValueError, match="is 100.00 EUR"):
        pq.validate_garage_qa(shorter, garages)


@pytest.mark.parametrize("changes, message", [
    ({"amount_eur": ""}, "states its amount_eur"),
    ({"garage_id": "bs_steinstrasse"}, "a garage that is no option of the dataset has no garage_id"),
    ({"municipality_ags": ""}, "a recorded garage names its municipality"),
], ids=["no_amount", "garage_id", "no_municipality"])
def test_a_recorded_garage_that_is_no_option_states_an_amount_a_municipality_and_no_garage_id(changes, message):
    garages, qa = _p13_tables()
    qa = qa.copy()
    for column, value in changes.items():
        qa.loc[qa["record_id"] == "monthly_bs_eves", column] = value
    with pytest.raises(ValueError, match=message):
        pq.validate_garage_qa(qa, garages)
