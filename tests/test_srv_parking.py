"""Tests for the SrV 2023 commute-parking and city-centre parking tables (issue #249).

The builders in ``braunschweig.calibration.srv_parking`` are exercised on small synthetic
frames whose expected values are computable by hand.

Codebook audit (2026-09-29; ``SrV2023_Datenkodierung_SciUse.xlsx`` sheet ``Tabelle1`` and
``SrV2023_Teilraumkodierung_Braunschweig und RGB.xlsx``; the add-on file headers were read,
no microdata row was inspected or copied):

* Person keys ``HHNR`` and ``PNR`` in both add-on modules, ``SrV2023_Personen.csv`` and
  ``SrV2023_Wege.csv``; the trip key adds ``WNR``. The add-on modules are the delivery's
  ``..._SciUse_v4_P2_BRAU.csv`` (codebook level ``P2_BRAU``) and ``..._SciUse_v4_P2_RGB.csv``
  (level ``P2_RGB``), copied locally as ``SrV2023_Personen_Zusatz_Braunschweig.csv`` and
  ``SrV2023_Personen_Zusatz_RGB.csv``.
* Work/education parking: ``V_BRAU_PARKENAPL`` and ``V_RGB_PARKENAPL`` carry identical codes:
  1 Parkplatz des Arbeitgebers bzw. der Schule/Hochschule, 2 oeffentlicher Strassenraum,
  3 Tiefgarage/Parkhaus/Grossparkplatz, 4 "Ich fahre nicht mit dem Auto zur
  Arbeit/Ausbildung", 5 anderer Ort; -8 Nicht erhoben, -10 Unplausibel. The free-text
  columns ``V_*_PARKENAPL_TXT`` are never read.
* Payment: one follow-up per reported place, ``V_BRAU_PARKENAPL<k>_ENTGELT`` and
  ``V_RGB_PARKENAPL<k>_ENTGELT`` for k in 1, 2, 3, 5 (none for 4): 1 Unentgeltlich, 2 Gegen
  ein Entgelt; -8, -10. There is no single ``_ENTGELT`` column, so the planned filter holds
  as written: the payment is read from the follow-up that matches the reported place.
* City centre (Braunschweig module only): ``V_BRAU_PARKENCITY`` uses a DIFFERENT code order
  from PARKENAPL -- 1 oeffentlicher Strassenraum, 2 Tiefgarage/Parkhaus/Grossparkplatz,
  3 Parkplatz des Arbeitgebers, 4 "Ich fahre nicht mit dem Auto in die Innenstadt",
  5 anderer Ort; -8, -10 -- with follow-ups ``V_BRAU_PARKENCITY<k>_ENTGELT`` (k in 1, 2, 3, 5)
  and the payment codes above. The plan said "the same type map"; that map would swap street,
  garage and employer lot, so the module carries a separate ``PARKENCITY_CODES`` constant (the
  constant changes, not the logic).
* Trips: ``V_ZWECK`` (1 Eigener Arbeitsplatz, 6 Berufs-, Fach-, Hochschule), ``V_ZIEL_AGS``
  (TEXT in the codebook, stored as an integer with 7 or 8 digits in the delivery; -8 Nicht
  erhoben) and ``V_ZIEL_OBERBEZIRK`` (-7 Berechnung nicht moeglich, -6 Nicht definiert).
  Oberbezirk numbers are defined per survey area; for Braunschweig: 1 Zentrum,
  2 Innenbereich West, 3 Innenbereich Ost, 4 Stadtteilring, 5 Aussenbereich Sued-Ost,
  6 Aussenbereich Nord-West.
* Weight: ``GEWICHT_P_ZENSUS`` (Personen file; -6 Nicht definiert is its missing code).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.calibration import srv_parking as sp

REPO = Path(__file__).resolve().parents[1]


def _addon(rows):
    return pd.DataFrame(rows)


def _load_extractor():
    """Import scripts/extract_srv_commute_parking.py by path (scripts/ is not a package)."""
    path = REPO / "scripts" / "extract_srv_commute_parking.py"
    spec = importlib.util.spec_from_file_location("extract_srv_commute_parking", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unify_addon_modules_neutralises_prefixes_and_rejects_duplicates():
    bs = _addon([{"HHNR": 1, "PNR": 1, "V_BRAU_PARKENAPL": 1, "V_BRAU_PARKENAPL1_ENTGELT": 1}])
    rgb = _addon([{"HHNR": 2, "PNR": 1, "V_RGB_PARKENAPL": 2, "V_RGB_PARKENAPL2_ENTGELT": 2}])
    unified = sp.unify_addon_modules(bs, rgb)
    assert list(unified["parking_type"]) == ["employer_lot", "street"]
    assert list(unified["payment"]) == ["free", "paid"]
    assert list(unified["module"]) == ["BRAU", "RGB"]

    # The same person answering in BOTH modules would enter the universe twice.
    rgb_same_person = _addon([{"HHNR": 1, "PNR": 1, "V_RGB_PARKENAPL": 2,
                               "V_RGB_PARKENAPL2_ENTGELT": 2}])
    with pytest.raises(ValueError, match="duplicate"):
        sp.unify_addon_modules(bs, rgb_same_person)
    with pytest.raises(ValueError, match="duplicate"):
        sp.unify_addon_modules(pd.concat([bs, bs], ignore_index=True), rgb)


def test_unify_maps_missing_codes_to_none_and_rejects_unmapped_codes():
    bs = _addon([
        {"HHNR": 1, "PNR": 1, "V_BRAU_PARKENAPL": -8, "V_BRAU_PARKENAPL2_ENTGELT": -8},
        # A payment answer next to an implausible place must not resurrect the respondent.
        {"HHNR": 2, "PNR": 1, "V_BRAU_PARKENAPL": -10, "V_BRAU_PARKENAPL2_ENTGELT": 1},
        {"HHNR": 3, "PNR": 1, "V_BRAU_PARKENAPL": 4, "V_BRAU_PARKENAPL2_ENTGELT": -8},
        {"HHNR": 4, "PNR": 1, "V_BRAU_PARKENAPL": 2, "V_BRAU_PARKENAPL2_ENTGELT": -10},
    ])
    rgb = _addon([{"HHNR": 5, "PNR": 1, "V_RGB_PARKENAPL": 2, "V_RGB_PARKENAPL2_ENTGELT": 1}])
    unified = sp.unify_addon_modules(bs, rgb)
    assert unified["parking_type"].isna().tolist() == [True, True, False, False, False]
    assert unified["parking_type"].tolist()[2:] == ["no_car_commute", "street", "street"]
    assert unified["payment"].isna().tolist() == [True, True, True, True, False]
    assert unified["payment"].iloc[4] == "free"

    with pytest.raises(ValueError, match="V_RGB_PARKENAPL: unmapped code"):
        sp.unify_addon_modules(bs, _addon([{"HHNR": 5, "PNR": 1, "V_RGB_PARKENAPL": 9}]))
    # A reported place whose payment follow-up column is absent cannot be resolved.
    with pytest.raises(ValueError, match="V_RGB_PARKENAPL3_ENTGELT"):
        sp.unify_addon_modules(bs, _addon([{"HHNR": 5, "PNR": 1, "V_RGB_PARKENAPL": 3}]))


def test_no_car_commute_is_excluded_from_the_driver_universe_but_counted():
    addon = pd.DataFrame({"HHNR": [1, 2], "PNR": [1, 1], "parking_type": ["no_car_commute", "street"],
                          "payment": [None, "paid"]})
    persons = pd.DataFrame({"HHNR": [1, 2], "PNR": [1, 1], "GEWICHT_P_ZENSUS": [10.0, 20.0]})
    universe, report = sp.commute_parking_universe(addon, persons)
    assert len(universe) == 1 and report["excluded_no_car_commute"] == 1
    assert universe["weight"].tolist() == [20.0]


def test_negative_weights_and_missing_codes_are_dropped_and_counted():
    addon = pd.DataFrame({"HHNR": [1, 2, 3], "PNR": [1, 1, 1], "parking_type": ["street", "street", None],
                          "payment": ["paid", "paid", None]})
    persons = pd.DataFrame({"HHNR": [1, 2, 3], "PNR": [1, 1, 1], "GEWICHT_P_ZENSUS": [10.0, -9.0, 5.0]})
    universe, report = sp.commute_parking_universe(addon, persons)
    assert len(universe) == 1 and report["dropped_invalid_weight"] == 1 and report["dropped_missing_type"] == 1
    assert report["n_input"] == 3 and report["n_universe"] == 1


def test_commute_universe_rejects_respondents_missing_from_the_person_file():
    addon = pd.DataFrame({"HHNR": [1, 2], "PNR": [1, 1], "parking_type": ["street", "street"],
                          "payment": ["paid", "free"]})
    persons = pd.DataFrame({"HHNR": [1], "PNR": [1], "GEWICHT_P_ZENSUS": [10.0]})
    with pytest.raises(ValueError, match="person file"):
        sp.commute_parking_universe(addon, persons)


def test_classify_workplace():
    assert sp.classify_workplace("03101000", 1) == "bs_zentrum"
    assert sp.classify_workplace("03101000", 3) == "bs_innenbereich"
    assert sp.classify_workplace("03101000", 5) == "bs_outer"
    assert sp.classify_workplace("03103000", None) == "03103"
    assert sp.classify_workplace("01001000", None) == "outside_zgb"
    assert sp.classify_workplace(None, None) == "unknown_destination"


def test_classify_workplace_reads_the_delivery_encoding_and_never_guesses_a_district():
    # The delivery stores V_ZIEL_AGS as an integer without the leading zero.
    assert sp.classify_workplace(3101000, 2) == "bs_innenbereich"
    assert sp.classify_workplace(3158012, -7) == "03158"
    # -8 "Nicht erhoben" is a missing code, never the key "-0000008".
    assert sp.classify_workplace(-8, None) == "unknown_destination"
    # A Braunschweig destination whose district could not be computed (-7) is kept apart
    # instead of being folded into one of the Braunschweig classes.
    assert sp.classify_workplace(3101000, -7) == "bs_unknown_oberbezirk"
    with pytest.raises(ValueError, match="Oberbezirk"):
        sp.classify_workplace(3101000, 7)


def test_first_commute_destination_takes_the_first_work_or_education_trip():
    trips = pd.DataFrame({"HHNR": [1, 1, 1], "PNR": [1, 1, 1], "WNR": [1, 2, 3], "V_ZWECK": [5, 1, 6],
                          "V_ZIEL_AGS": ["03101000", "03101000", "03102000"], "V_ZIEL_OBERBEZIRK": [4, 1, None]})
    destinations = sp.first_commute_destination(trips)
    assert destinations.loc[(1, 1), "workplace_class"] == "bs_zentrum"


def test_first_commute_destination_orders_by_trip_number_not_by_file_order():
    trips = pd.DataFrame({"HHNR": [7, 7, 8], "PNR": [2, 2, 1], "WNR": [3, 1, 1], "V_ZWECK": [1, 6, 8],
                          "V_ZIEL_AGS": [3101000, 3102000, 3101000], "V_ZIEL_OBERBEZIRK": [1, -7, 1]})
    destinations = sp.first_commute_destination(trips)
    assert destinations.loc[(7, 2), "workplace_class"] == "03102"
    # A shopping trip (V_ZWECK 8) is no commute: the person has no destination row at all.
    assert (8, 1) not in destinations.index


def test_attach_workplace_class_labels_car_commuters_without_a_commute_trip():
    universe = pd.DataFrame({"HHNR": [1, 2], "PNR": [1, 1], "weight": [1.0, 1.0],
                             "parking_type": ["street", "street"], "payment": ["paid", "free"]})
    destinations = pd.DataFrame({"workplace_class": ["bs_zentrum"]},
                                index=pd.MultiIndex.from_tuples([(1, 1)], names=["HHNR", "PNR"]))
    attached = sp.attach_workplace_class(universe, destinations)
    assert attached["workplace_class"].tolist() == ["bs_zentrum", "no_commute_trip"]
    assert attached["weight"].tolist() == [1.0, 1.0]


def test_table_shares_sum_to_one_and_small_cells_raise():
    universe = pd.DataFrame({"HHNR": range(120), "PNR": 1, "weight": 1.0,
                             "parking_type": ["employer_lot"] * 90 + ["street"] * 20 + ["garage_large_lot"] * 10,
                             "payment": ["free"] * 90 + ["paid"] * 10 + ["free"] * 10 + ["paid"] * 10,
                             "workplace_class": "bs_zentrum"})
    table = sp.build_commute_parking_table(universe, min_cell_n=100)
    row = table.set_index("workplace_class").loc["bs_zentrum"]
    assert abs(row[["share_employer_lot", "share_street", "share_garage_large_lot", "share_other"]].sum() - 1) < 1e-9
    assert abs(row["share_paid_total"] + row["share_free_total"] - 1) < 1e-9
    assert row["share_free_total"] == pytest.approx(100 / 120)
    with pytest.raises(ValueError, match="bs_zentrum"):
        sp.build_commute_parking_table(universe.head(50), min_cell_n=100)


def test_total_row_pools_exactly_the_zgb_class_rows():
    zentrum = pd.DataFrame({"HHNR": range(100), "PNR": 1, "weight": 1.0, "parking_type": "street",
                            "payment": "paid", "workplace_class": "bs_zentrum"})
    salzgitter = pd.DataFrame({"HHNR": range(100, 200), "PNR": 1, "weight": 3.0,
                               "parking_type": "employer_lot", "payment": "free",
                               "workplace_class": "03102"})
    not_classified = pd.DataFrame({"HHNR": range(200, 210), "PNR": 1, "weight": 1.0,
                                   "parking_type": "other", "payment": "paid",
                                   "workplace_class": ["outside_zgb"] * 5 + ["no_commute_trip"] * 5})
    universe = pd.concat([zentrum, salzgitter, not_classified], ignore_index=True)
    table = sp.build_commute_parking_table(universe, min_cell_n=100)
    assert list(table.columns) == [
        "workplace_class", "level", "n_unweighted", "n_eff", "share_employer_lot", "share_street",
        "share_garage_large_lot", "share_other", "share_paid_total", "share_free_total"]
    assert table["workplace_class"].tolist() == ["bs_zentrum", "03102", "total"]
    assert table["level"].tolist() == ["class", "class", "total"]
    total = table.set_index("workplace_class").loc["total"]
    assert total["n_unweighted"] == 200
    assert total["share_free_total"] == pytest.approx(300 / 400)
    # Kish effective sample size: (100 * 1 + 100 * 3) ** 2 / (100 * 1 + 100 * 9) = 160.
    assert total["n_eff"] == pytest.approx(160.0)
    with pytest.raises(ValueError, match="bs_center"):
        sp.build_commute_parking_table(zentrum.assign(workplace_class="bs_center"), min_cell_n=100)


def test_city_center_table_reports_paid_share_overall():
    bs = pd.DataFrame({"HHNR": [1, 2, 3], "PNR": [1, 1, 1], "V_BRAU_PARKENCITY": [2, 3, 3],
                       "V_BRAU_PARKENCITY2_ENTGELT": [2, None, None], "V_BRAU_PARKENCITY3_ENTGELT": [None, 2, 1]})
    persons = pd.DataFrame({"HHNR": [1, 2, 3], "PNR": [1, 1, 1], "GEWICHT_P_ZENSUS": [1.0, 1.0, 1.0]})
    table = sp.build_city_center_table(bs, persons)
    assert table.set_index("parking_type").loc["paid_share_overall", "share"] == pytest.approx(2 / 3)


def test_city_center_codes_follow_the_parkencity_codebook_order():
    bs = pd.DataFrame({"HHNR": [1, 2, 3, 4], "PNR": [1, 1, 1, 1], "V_BRAU_PARKENCITY": [1, 2, 3, 4],
                       "V_BRAU_PARKENCITY1_ENTGELT": [2, -8, -8, -8],
                       "V_BRAU_PARKENCITY2_ENTGELT": [-8, 2, -8, -8],
                       "V_BRAU_PARKENCITY3_ENTGELT": [-8, -8, 1, -8]})
    persons = pd.DataFrame({"HHNR": [1, 2, 3, 4], "PNR": [1, 1, 1, 1],
                            "GEWICHT_P_ZENSUS": [1.0, 2.0, 1.0, 5.0]})
    table = sp.build_city_center_table(bs, persons).set_index("parking_type")
    assert list(table.columns) == ["share", "n_unweighted"]
    assert table.loc["street", "share"] == pytest.approx(1 / 4)
    assert table.loc["garage_large_lot", "share"] == pytest.approx(2 / 4)
    assert table.loc["employer_lot", "share"] == pytest.approx(1 / 4)
    assert table.loc["other", "share"] == 0
    assert table.loc["paid_share_overall", "share"] == pytest.approx(3 / 4)
    assert table.loc["paid_share_overall", "n_unweighted"] == 3

    _, report = sp.city_center_universe(bs, persons)
    assert report["excluded_no_car_to_city_center"] == 1 and report["n_universe"] == 3


def _write_raw(path: Path, rows: list) -> None:
    """Write a synthetic raw file in the delivery format (semicolon, decimal comma, cp1252)."""
    pd.DataFrame(rows).to_csv(path, sep=";", decimal=",", encoding="cp1252", index=False)


def test_extractor_writes_both_tables_with_the_provenance_header(tmp_path):
    raw = tmp_path / "srv2023_raw"
    raw.mkdir()
    followups_bs = {f"V_BRAU_PARKENAPL{k}_ENTGELT": -8 for k in sp.PAYMENT_FOLLOWUP_CODES}
    city_followups = {f"V_BRAU_PARKENCITY{k}_ENTGELT": -8 for k in sp.PAYMENT_FOLLOWUP_CODES}
    followups_rgb = {f"V_RGB_PARKENAPL{k}_ENTGELT": -8 for k in sp.PAYMENT_FOLLOWUP_CODES}
    _write_raw(raw / "SrV2023_Personen_Zusatz_Braunschweig.csv", [
        {"HHNR": 1, "PNR": 1, "V_BRAU_PARKENAPL": 1, **followups_bs, "V_BRAU_PARKENAPL1_ENTGELT": 1,
         "V_BRAU_PARKENCITY": 2, **city_followups, "V_BRAU_PARKENCITY2_ENTGELT": 2},
        {"HHNR": 2, "PNR": 1, "V_BRAU_PARKENAPL": 2, **followups_bs, "V_BRAU_PARKENAPL2_ENTGELT": 2,
         "V_BRAU_PARKENCITY": 4, **city_followups},
    ])
    _write_raw(raw / "SrV2023_Personen_Zusatz_RGB.csv", [
        {"HHNR": 3, "PNR": 1, "V_RGB_PARKENAPL": 3, **followups_rgb, "V_RGB_PARKENAPL3_ENTGELT": 1},
        {"HHNR": 4, "PNR": 1, "V_RGB_PARKENAPL": -8, **followups_rgb},
    ])
    _write_raw(raw / "SrV2023_Personen.csv", [
        {"HHNR": person, "PNR": 1, "GEWICHT_P_ZENSUS": weight}
        for person, weight in ((1, 1.5), (2, 2.0), (3, 1.0), (4, 1.0))])
    _write_raw(raw / "SrV2023_Wege.csv", [
        {"HHNR": 1, "PNR": 1, "WNR": 1, "V_ZWECK": 1, "V_ZIEL_AGS": 3101000, "V_ZIEL_OBERBEZIRK": 1},
        {"HHNR": 2, "PNR": 1, "WNR": 1, "V_ZWECK": 1, "V_ZIEL_AGS": 3101000, "V_ZIEL_OBERBEZIRK": 5},
        {"HHNR": 3, "PNR": 1, "WNR": 1, "V_ZWECK": 6, "V_ZIEL_AGS": 3103000, "V_ZIEL_OBERBEZIRK": -7},
    ])
    out = tmp_path / "out"

    assert _load_extractor().main(["--raw", str(raw), "--out-dir", str(out), "--min-cell-n", "1",
                                   "--source-commit", "abc1234"]) == 0

    commute_text = (out / sp.COMMUTE_TABLE_FILE).read_text(encoding="utf-8")
    header = [line for line in commute_text.splitlines() if line.startswith("#")]
    assert sum("sha256=" in line for line in header) == 4
    assert any("code state eqasim-bs abc1234" in line for line in header)
    assert any("min_cell_n=1" in line for line in header)
    assert any("GEWICHT_P_ZENSUS" in line for line in header)
    assert ("# Shares are observed among current car commuters (SrV 2023 BS+RGB SciUse_v4); assumption "
            "A1 of the parking-cost-zones design transfers them to all workers of a class.") in header
    commute = pd.read_csv(out / sp.COMMUTE_TABLE_FILE, comment="#", dtype={"workplace_class": str})
    assert list(commute.columns) == list(sp.COMMUTE_TABLE_COLUMNS)
    assert commute["workplace_class"].tolist() == ["bs_zentrum", "bs_outer", "03103", "total"]
    assert commute.set_index("workplace_class").loc["total", "share_free_total"] == pytest.approx(
        2.5 / 4.5, abs=1e-4)

    city_text = (out / sp.CITY_CENTER_TABLE_FILE).read_text(encoding="utf-8")
    assert sum("sha256=" in line for line in city_text.splitlines() if line.startswith("#")) == 2
    city = pd.read_csv(out / sp.CITY_CENTER_TABLE_FILE, comment="#").set_index("parking_type")
    assert list(city.columns) == ["share", "n_unweighted"]
    assert city.loc["garage_large_lot", "share"] == 1.0
    assert city.loc["paid_share_overall", "share"] == 1.0


# --- The committed tables ------------------------------------------------------------------------

SRV_DIR = REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
PLACE_SHARE_COLUMNS = ["share_employer_lot", "share_street", "share_garage_large_lot", "share_other"]
# Four shares rounded to 4 decimals each can miss 1 by up to 2e-4; two shares by up to 1e-4.
ROUNDING_TOLERANCE = 2.5e-4


def _committed_table(name: str) -> pd.DataFrame:
    path = SRV_DIR / name
    # Force-added past the eqasim-data/ ignore rule, so an absent file means a broken checkout,
    # never "nothing to test": assert instead of skipping (the convention of
    # tests/test_srv_kreis_table_conventions.py), or these pins would pass vacuously.
    assert path.exists(), (
        f"Committed SrV table missing: {path}. It is tracked in git (force-added); regenerate it "
        "with scripts/extract_srv_commute_parking.py only together with a reviewed diff.")
    return pd.read_csv(path, comment="#", dtype={"workplace_class": str})


def _committed_header(name: str) -> list:
    text = (SRV_DIR / name).read_text(encoding="utf-8")
    return [line for line in text.splitlines() if line.startswith("#")]


def test_committed_commute_table_has_one_row_per_workplace_class_and_a_pooled_total():
    table = _committed_table(sp.COMMUTE_TABLE_FILE)
    assert list(table.columns) == list(sp.COMMUTE_TABLE_COLUMNS)
    classes = table[table["level"] == sp.LEVEL_CLASS]
    total = table[table["level"] == sp.LEVEL_TOTAL]
    # Every class the zone stage can reference must be present (a missing class would raise there).
    assert classes["workplace_class"].tolist() == list(sp.WORKPLACE_CLASSES)
    assert total["workplace_class"].tolist() == [sp.TOTAL_ROW_CLASS]
    assert len(table) == len(sp.WORKPLACE_CLASSES) + 1
    assert int(classes["n_unweighted"].sum()) == int(total["n_unweighted"].iloc[0])
    assert (classes["n_unweighted"] >= sp.MIN_CELL_N).all()
    assert (table["n_eff"] <= table["n_unweighted"]).all()

    share_columns = [column for column in table.columns if column.startswith("share_")]
    assert ((table[share_columns] >= 0) & (table[share_columns] <= 1)).all().all()
    assert table["share_free_total"].between(0, 1).all()
    assert (table[PLACE_SHARE_COLUMNS].sum(axis=1) - 1).abs().max() < ROUNDING_TOLERANCE
    assert (table["share_paid_total"] + table["share_free_total"] - 1).abs().max() < ROUNDING_TOLERANCE


def test_committed_commute_table_shows_the_centre_as_the_least_free_braunschweig_class():
    """A data fact of the extraction of 2026-09-29 (issue #249), pinned because it is what the zone
    model's free-parking draw rests on: of the three Braunschweig classes, car commuters to the
    Zentrum (Oberbezirk 1) park free least often. A regeneration that flips it needs a review."""
    table = _committed_table(sp.COMMUTE_TABLE_FILE).set_index("workplace_class")
    braunschweig = table.loc[[sp.BS_ZENTRUM, sp.BS_INNENBEREICH, sp.BS_OUTER], "share_free_total"]
    assert braunschweig.idxmin() == sp.BS_ZENTRUM


def test_committed_city_center_table_has_the_place_rows_and_the_paid_share():
    table = _committed_table(sp.CITY_CENTER_TABLE_FILE)
    assert list(table.columns) == list(sp.CITY_CENTER_TABLE_COLUMNS)
    assert table["parking_type"].tolist() == [*sp.DRIVER_PARKING_TYPES, sp.PAID_SHARE_OVERALL]
    assert table["share"].between(0, 1).all()
    places = table[table["parking_type"] != sp.PAID_SHARE_OVERALL]
    assert abs(places["share"].sum() - 1) < ROUNDING_TOLERANCE
    paid = table.set_index("parking_type").loc[sp.PAID_SHARE_OVERALL]
    assert 0 < paid["n_unweighted"] <= places["n_unweighted"].sum()


def test_committed_tables_carry_the_provenance_header():
    commute_header = _committed_header(sp.COMMUTE_TABLE_FILE)
    assert sum("sha256=" in line for line in commute_header) == 4
    assert any(f"min_cell_n={sp.MIN_CELL_N}" in line for line in commute_header)
    assert any("assumption A1 of the parking-cost-zones design" in line for line in commute_header)
    city_header = _committed_header(sp.CITY_CENTER_TABLE_FILE)
    assert sum("sha256=" in line for line in city_header) == 2
    for header in (commute_header, city_header):
        assert any("scripts/extract_srv_commute_parking.py" in line for line in header)
        assert any("GEWICHT_P_ZENSUS" in line for line in header)
        assert not any("code state eqasim-bs unknown" in line for line in header)
