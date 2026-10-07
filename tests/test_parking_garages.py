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

from braunschweig.parking import garages as pg
from braunschweig.parking import zones as pz

SHA = "e789623752bf508b3e31f37ed2e30fe019e171cb43274f495cfacc12924008e7"
SOURCE = "https://www.contipark.de/de/parken/braunschweig/tiefgarage-eiermarkt/"


def _row(**changes) -> dict:
    """A valid priced garage (the shape of the Braunschweig Eiermarkt row): day family 7 to 18 h, first hour 0.60 EUR,
    1.20 EUR per started hour, day cap 9.60 EUR, no monthly product."""
    row = {
        "garage_id": "bs_eiermarkt", "package_facility_id": "BS_PH004", "name": "Parkhaus Eiermarkt", "operator": None,
        "municipality": "Braunschweig", "municipality_ags": "03101000", "capacity_reported": 500,
        "capacity_scope": "reported_in_PULP", "garage_hourly_rate_eur": 1.2, "garage_billing_unit_min": 60,
        "garage_first_period_min": 60, "garage_first_period_eur": 0.6, "garage_daily_cap_eur": 9.6,
        "garage_fee_start_h": 7.0, "garage_fee_end_h": 18.0, "monthly_eur": None, "monthly_source_url": None,
        "monthly_product": None, "priced": True, "not_priced_reason": None, "assumptions": "P3",
        "source_url": SOURCE, "source_date": "2026-10-07", "tariff_rule_ids": "r-35;r-36;r-37",
        "geometry_method": "official_feed_point", "geometry_source_url": "https://www.braunschweig.de/apps/pulp/result/x",
        "package_sha256": SHA, "notes": "Day family only (ASSUMPTION P3); night 18-07 h 1.00 EUR per started hour.",
        "x": 603408.0, "y": 5791173.0,
    }
    row.update(changes)
    return row


def _unpriced(**changes) -> dict:
    """A valid unpriced garage: a banded tariff, no tariff value, no assumption."""
    base = {"garage_id": "bs_ring_center", "package_facility_id": "BS_None", "name": "Parkhaus Ring-Center",
            "garage_hourly_rate_eur": None, "garage_billing_unit_min": None, "garage_first_period_min": None,
            "garage_first_period_eur": None, "garage_daily_cap_eur": None, "garage_fee_start_h": None,
            "garage_fee_end_h": None, "priced": False, "not_priced_reason": "banded_tariff", "assumptions": None,
            "tariff_rule_ids": "r-19;r-20;r-21", "capacity_reported": None, "capacity_scope": None,
            "notes": "1.50 EUR per started hour for hours 1 and 2, then 2.00 EUR.", "x": 604798.2, "y": 5790032.2}
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
    assert pg.DATASET_COLUMNS[:6] == ("garage_id", "package_facility_id", "name", "operator", "municipality",
                                      "municipality_ags")
    assert set(pg.TARIFF_COLUMNS) <= set(pg.DATASET_COLUMNS)
    assert set(pg.NOT_PRICED_REASONS) == {"no_published_tariff", "free_period", "banded_tariff", "incomplete_tariff",
                                          "conflicting_sources"}
    assert set(pg.ASSUMPTIONS) == {"P3", "P4", "P5"}


# --------------------------------------------------------------------------- loader and writer


def test_a_valid_priced_and_a_valid_unpriced_garage_pass():
    pg.validate_garages(_frame(_row(), _unpriced()))


def test_the_writer_and_the_loader_round_trip_typed_metric_points(tmp_path):
    frame = _frame(_row(monthly_eur=48.0, monthly_source_url="https://example.org/monat", monthly_product="Dauerstellplatz"),
                   _unpriced())
    path = tmp_path / "garages.geojson"
    pg.write_garages(frame, path, members={"license": "terms of the sources", "attribution": "operators"})
    text = path.read_text(encoding="utf-8")
    assert text.isascii() and "\r" not in text
    document = json.loads(text)
    assert document["license"] == "terms of the sources" and len(document["features"]) == 2
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
    assert list(loaded["priced"]) == [True, False]
    assert loaded.loc[0, "operator"] is None and loaded.loc[0, "monthly_product"] == "Dauerstellplatz"
    assert pd.isna(loaded.loc[1, "garage_hourly_rate_eur"]) and pd.isna(loaded.loc[1, "garage_first_period_min"])
    assert loaded.loc[1, "not_priced_reason"] == "banded_tariff" and loaded.loc[0, "not_priced_reason"] is None
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


def test_the_loader_logs_the_priced_rate_the_reasons_and_the_assumption_rates(tmp_path, caplog):
    frame = _frame(_row(), _row(garage_id="bs_magni", assumptions="P5"), _row(garage_id="bs_packhof", assumptions="P4;P5"),
                   _unpriced())
    path = tmp_path / "garages.geojson"
    pg.write_garages(frame, path)
    with caplog.at_level(logging.INFO, logger=pg.log.name):
        pg.load_garages(path)
    text = " ".join(record.getMessage() for record in caplog.records)
    assert "loaded 4 garages" in text and "priced 3/4 (75.0 %)" in text and "not priced 1 (banded_tariff 1)" in text
    assert "P3 1/3" in text and "P4 1/3" in text and "P5 2/3" in text


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
    # cap and first period
    ({"garage_daily_cap_eur": 0.5}, "the day cap 0.5 is below the first period 0.6"),
    ({"garage_first_period_eur": None}, "garage_first_period_eur: garage_first_period_min and garage_first_period_eur"),
    ({"garage_first_period_min": None}, "garage_first_period_min: garage_first_period_min and garage_first_period_eur"),
    # priced is exactly "the core is set"
    ({"priced": False}, "priced is False but the garage core is complete"),
    ({"not_priced_reason": "banded_tariff"}, "a priced garage has no reason"),
    ({"tariff_rule_ids": None}, "tariff_rule_ids: a priced garage names the package rules"),
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
    ({"garage_daily_cap_eur": 15.0}, "a day cap or a first period needs the complete garage core"),
    ({"garage_first_period_min": 60, "garage_first_period_eur": 1.5}, "needs the complete garage core"),
    ({"garage_first_period_min": 60}, "are set together or not at all"),
    ({"priced": True}, "priced is True but the garage core is not complete"),
    ({"assumptions": "P4"}, "an unpriced garage rests on no assumption"),
    ({"tariff_rule_ids": None}, None),  # the evidence of a reason is a good thing, not a requirement
])
def test_the_validator_rejects_a_broken_unpriced_garage(changes, message):
    if message is None:
        pg.validate_garages(_frame(_unpriced(**changes)))
        return
    with pytest.raises(ValueError, match=message):
        pg.validate_garages(_frame(_unpriced(**changes)))


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
    assert summary["not_priced_by_reason"] == {"banded_tariff": 1, "free_period": 1, "no_published_tariff": 1}
    assert summary["by_municipality"] == {"03101000": {"listed": 3, "priced": 2, "not_priced": 1},
                                          "03103000": {"listed": 2, "priced": 1, "not_priced": 1},
                                          "03153017": {"listed": 1, "priced": 0, "not_priced": 1}}
    assert summary["priced_by_assumption"] == {"P3": 1, "P4": 1, "P5": 2}
    assert summary["with_monthly_product"] == 1
    # an unpriced garage's assumption (invalid anyway) never counts as a priced row's
    assert np.isclose(sum(summary["priced_by_assumption"].values()), 4)
