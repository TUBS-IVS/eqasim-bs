"""Station car parks of Braunschweig Hbf as single paid-site zones (parking cost zones v2, spec Amendment G1, issue #436, Task 4g).

The owner's package ``Braunschweig_Monatstarife_2026-10-08.zip`` and the Overpass response of the station lots are gitignored,
so the curation step ``station_lots.py`` is pinned on a synthetic package written the way the owner supplied it (a zip with
``manifest.sha256`` and the Contipark page texts, the DB BahnPark sheet, the monthly rules, facilities and sources) and a
synthetic Overpass response. What is pinned: the verification of both inputs; that every amount is READ from the line the
specification names (never typed: another amount in the page is another value), with the refusal path of every reading (a missing,
duplicated or contradicting line, a sheet that disagrees, a monthly product that is not the cheapest, a not-modelled tariff that
the page does not state, an outline that is no closed ring or a way that is not the lot); the three zones, their cut against the
BgA zone, and the refusal to change any other zone; the tariff rows and their check; the QA rows; and the append to a finished
release (every other zone byte for byte). The committed release is pinned independently in the last tests: the zones exist and do
not overlap the BgA zone, the tariff values and the commuter cents, the work stays priced by hand, and the Java contract and the
golden file are unchanged.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from braunschweig.parking import cost
from braunschweig.parking import zones as pz
from braunschweig.parking.tariff_export import tariff_row_to_zone
from tests.restricted_parking_data import committed_parking_path

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
COMMITTED_PARKING_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking"
ROOT = "Braunschweig_Monatstarife_2026-10-08"
EURO = "\u00a0\u20ac"
HOUR_S = 3600


@pytest.fixture(scope="module")
def sl():
    """The curation step as a module (it imports its siblings from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        return importlib.import_module("station_lots")
    finally:
        sys.path.remove(str(CURATION_DIR))


# ---------------------------------------------------------------------------------------- the synthetic inputs
def _amount(value: float) -> str:
    return f"{value:.2f}".replace(".", ",") + EURO


def _page(*, rate_label, rate, day, monthly, check=None, special="", machine_month=None, fixed=None) -> str:
    """The text of a Contipark location page: header lines, the fee table 'Regulaeres Parkentgelt' and trailing lines."""
    lines = ["Parkplatz Hauptbahnhof in Braunschweig | Contipark", "Startseite", "Regul\u00e4res Parkentgelt",
             "Zeiteinheit", "Preis", rate_label, _amount(rate)]
    if check is not None:
        lines += ["1 Stunde", _amount(check)]
    lines += ["1 Tag", _amount(day)]
    if machine_month is not None:
        lines += ["1 Monat am Parkautomaten", _amount(machine_month)]
    lines += ["1 Monat f\u00fcr Stellplatzmieter", _amount(monthly)]
    if fixed is not None:
        lines += ["1 Monat f\u00fcr Stellplatzmieter (fester Stellplatz)", _amount(fixed)]
    if special:
        lines += ["Sondertarife", special]
    lines += ["Alle Angaben ohne Gew\u00e4hr und inklusive Mehrwertsteuer.", "Durchgehend ge\u00f6ffnet", "00:00 \u2013 24:00"]
    return "\n".join(lines) + "\n"


P1_SPECIAL = ("15 Minuten Kiss&Ride: 0,80. 2. und weitere Stunden 3,00. Abendtarif: 18:00 bis 02:00 Uhr: 5,00 maximal (am ersten "
              "Parktag). Abschlie\u00dfbare Fahrradbox: 15,00 pro Monat")
AMOUNTS = {"p1": dict(rate_label="1 Stunde", rate=3.00, day=18.00, monthly=130.00, fixed=170.00, special=P1_SPECIAL),
           "p2": dict(rate_label="30 Minuten", rate=0.90, check=1.80, day=9.50, monthly=66.00, machine_month=70.00, fixed=120.00),
           "p3": dict(rate_label="30 Minuten", rate=0.90, check=1.80, day=9.50, monthly=66.00, machine_month=70.00, fixed=120.00)}


def _sheet(p1=(3.00, 18.00, 130.00), p2=(0.90, 1.80, 9.50, 66.00), p3=(0.90, 1.80, 9.50, 66.00)) -> str:
    def row(label, amount):
        return " " * 100 + f"{label:<40}" + f"{amount:>8.2f}".replace(".", ",")

    footer = ("Ein Service der DB BahnPark GmbH | S\u00e4mtliche Angaben ohne Gew\u00e4hr | Letzte \u00c4nderung am 29.05.26 | "
              "Seite 2")
    lines = ["Braunschweig Hbf", "", footer, "Braunschweig Hbf",
             " " * 17 + "Braunschweig Hbf Parkplatz Nord" + " " * 40 + "Tarif" + " " * 14 + "(Angaben in EUR, inkl. MwSt.)",
             row("1 Stunde", p1[0]), "Willy-Brandt-Platz" + row("1 Tag", p1[1])[18:], row("1 Monat Dauerparken*", p1[2]),
             row("1 Monat Dauerparken (fester Stellplatz)*", 160.0), footer, "Braunschweig Hbf",
             " " * 17 + "Braunschweig Hbf Parkplatz" + " " * 45 + "Tarif" + " " * 14 + "(Angaben in EUR, inkl. MwSt.)",
             "Eine Parkm\u00f6glichkeit von DB BahnPark" + row("30 Minuten", p2[0])[37:], row("1 Stunde", p2[1]),
             "Ackerstra\u00dfe" + row("1 Tag", p2[2])[12:], row("1 Monat am Automaten*", 70.0),
             "Stellpl\u00e4tze PKW 380" + row("1 Monat Dauerparken*", p2[3])[19:],
             " " * 17 + "Braunschweig Hbf Parkplatz West" + " " * 40 + "Tarif" + " " * 14 + "(Angaben in EUR, inkl. MwSt.)",
             row("30 Minuten", p3[0]), row("1 Stunde", p3[1]), row("1 Tag", p3[2]), row("1 Monat Dauerparken*", p3[3]), footer]
    return "\n".join(lines) + "\n"


def _rule(offer_id, facility, name, amount, usable=True):
    return {"offer_id": offer_id, "facility_id": facility, "billing_period": "month", "product_name": name,
            "evidence_status": "published_monthly_price", "model_usable_as_monthly_price": usable, "monthly_amount_eur": amount,
            "amount_eur": amount, "new_contract_status": "not_confirmed", "source_ids": ["ct_p"], "vat_included": True,
            "minimum_term_months": None}


def _facility(facility_id, key):
    return {"facility_id": facility_id, "facility_key": key, "facility_type": "surface_parking",
            "review_status": "monthly_price_confirmed", "new_contract_status": "not_confirmed", "offer_ids": [],
            "name": f"Parkplatz {key}"}


def _content(**changes) -> dict:
    month = "1 Monat f\u00fcr Stellplatzmieter"
    rules = [_rule("P1_O1", "BS_DB_P1", month, 130.0), _rule("P1_O2", "BS_DB_P1", month + " (fester Stellplatz)", 170.0),
             _rule("P2_O1", "BS_DB_P2", "1 Monat am Parkautomaten", 70.0), _rule("P2_O2", "BS_DB_P2", month, 66.0),
             _rule("P2_O3", "BS_DB_P2", month + " (fester Stellplatz)", 120.0),
             _rule("P3_O1", "BS_DB_P3", "1 Monat am Parkautomaten", 70.0), _rule("P3_O2", "BS_DB_P3", month, 66.0),
             _rule("P3_O3", "BS_DB_P3", month + " (fester Stellplatz)", 120.0)]
    content = {
        "evidence/contipark/p1.txt": _page(**AMOUNTS["p1"]), "evidence/contipark/p2.txt": _page(**AMOUNTS["p2"]),
        "evidence/contipark/p3.txt": _page(**AMOUNTS["p3"]), "evidence/contipark/db_bahnpark_8000049.txt": _sheet(),
        "data/tariff_rules.json": json.dumps({"rules": rules}),
        "data/facilities.json": json.dumps([_facility("BS_DB_P1", "braunschweig_hbf_nord_p1"),
                                            _facility("BS_DB_P2", "braunschweig_hbf_sued_p2"),
                                            _facility("BS_DB_P3", "braunschweig_hbf_west_p3")]),
        "data/sources.json": json.dumps([{"id": "ct_p1", "url": "https://www.example.org/p1/"},
                                         {"id": "ct_p2", "url": "https://www.example.org/p2/"},
                                         {"id": "ct_p3", "url": "https://www.example.org/p3/"}])}
    content.update(changes)
    return content


def _write_package(directory: Path, content: dict, tamper: dict | None = None) -> tuple:
    directory.mkdir(parents=True, exist_ok=True)
    manifest = "".join(f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}  {name}\n" for name, text in content.items())
    path = directory / f"{ROOT}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in content.items():
            archive.writestr(f"{ROOT}/{name}", (tamper or {}).get(name, text))
        archive.writestr(f"{ROOT}/manifest.sha256", manifest)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _ring(lon0, lat0, lon1, lat1):
    return [{"lat": lat0, "lon": lon0}, {"lat": lat0, "lon": lon1}, {"lat": lat1, "lon": lon1}, {"lat": lat1, "lon": lon0},
            {"lat": lat0, "lon": lon0}]


def _osm(**changes) -> dict:
    tags = {"amenity": "parking", "fee": "yes", "parking": "surface"}
    ways = [
        {"type": "way", "id": 25411279, "tags": {**tags, "name": "Parkplatz Hbf Nord"},
         "geometry": _ring(10.5400, 52.2535, 10.5410, 52.2540)},
        {"type": "way", "id": 7874165, "tags": {**tags, "name": "Parkplatz Hbf S\u00fcd"},
         "geometry": _ring(10.5400, 52.2510, 10.5410, 52.2515)},
        {"type": "way", "id": 236421384, "tags": {**tags, "name": "Parkplatz Hbf West"},
         "geometry": _ring(10.5376, 52.2517, 10.5384, 52.2520)},
        {"type": "way", "id": 26163572, "tags": {**tags, "name": "Post"}, "geometry": _ring(10.5405, 52.2541, 10.5409, 52.2543)}]
    document = {"version": 0.6, "osm3s": {"timestamp_osm_base": "2026-10-08T07:29:54Z"}, "elements": ways}
    document.update(changes)
    return document


def _write_osm(directory: Path, document: dict | None = None) -> tuple:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "osm_stations.json"
    path.write_text(json.dumps(document or _osm(), indent=1), encoding="utf-8")
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _load(sl, tmp_path, content=None, tamper=None, document=None):
    package, package_sha256 = _write_package(tmp_path / "package", content or _content(), tamper)
    osm, osm_sha256 = _write_osm(tmp_path / "osm", document)
    return sl.load_station_inputs(package, osm, package_sha256, osm_sha256)


@pytest.fixture()
def inputs(sl, tmp_path):
    return _load(sl, tmp_path)


# ---------------------------------------------------------------------------------------- verification
def test_the_pins_are_the_hashes_the_owner_package_and_the_outline_file_have(sl):
    # written from MANIFEST.md of the folder raw_sources/municipal_2026-10-08 and the data record parking_zones_2026
    assert sl.PACKAGE_SHA256 == "72fec30d4515a3f7468af66222802697b09ae042b283adc335b5ae3607650c81"
    assert sl.OSM_SHA256 == "fe4332938fcc7e99595aff167071a040fcea01b5be5df38e259647e64a7ecc26"
    assert sl.STATION_ZONE_IDS == ("bs_hbf_p1_nord", "bs_hbf_p2_sued", "bs_hbf_p3_west")
    assert [station["way"] for station in sl.STATIONS] == [25411279, 7874165, 236421384] and sl.POST_WAY == 26163572


def test_a_changed_or_missing_package_or_outline_file_is_refused(sl, tmp_path):
    package, package_sha256 = _write_package(tmp_path / "package", _content())
    osm, osm_sha256 = _write_osm(tmp_path / "osm")
    with pytest.raises(SystemExit, match="is not the recorded"):
        sl.load_station_inputs(package, osm, "0" * 64, osm_sha256)
    with pytest.raises(SystemExit, match="is not the recorded"):
        sl.load_station_inputs(package, osm, package_sha256, "0" * 64)
    with pytest.raises(SystemExit, match="missing"):
        sl.load_station_inputs(tmp_path / "nowhere.zip", osm, package_sha256, osm_sha256)
    with pytest.raises(SystemExit, match="missing"):
        sl.load_station_inputs(package, tmp_path / "nowhere.json", package_sha256, osm_sha256)


def test_a_member_that_differs_from_the_manifest_of_the_package_is_never_read(sl, tmp_path):
    changed = _page(**{**AMOUNTS["p1"], "rate": 9.99})
    with pytest.raises(SystemExit, match="a changed member is never read"):
        _load(sl, tmp_path, tamper={"evidence/contipark/p1.txt": changed})


def test_an_outline_that_is_no_closed_ring_or_an_osm_snapshot_without_a_timestamp_is_refused(sl, tmp_path):
    broken = _osm()
    broken["elements"][0]["geometry"] = broken["elements"][0]["geometry"][:-1]
    with pytest.raises(SystemExit, match="no closed ring"):
        _load(sl, tmp_path, document=broken)
    with pytest.raises(SystemExit, match="timestamp_osm_base"):
        _load(sl, tmp_path / "second", document=_osm(osm3s={}))


# ---------------------------------------------------------------------------------------- the amounts are READ
def test_the_amounts_are_read_from_the_named_lines_of_the_pages_and_agree_with_the_sheet_and_the_rules(sl, inputs):
    evidence = sl.station_evidence(inputs)
    assert list(evidence) == list(sl.STATION_ZONE_IDS)
    p1, p2 = evidence["bs_hbf_p1_nord"], evidence["bs_hbf_p2_sued"]
    assert (p1["hourly_rate_eur"], p1["billing_unit_min"], p1["daily_cap_eur"], p1["monthly_eur"]) == (3.0, 60, 18.0, 130.0)
    # the half-hour amount of P2 is 0.90 EUR, so the hourly rate is 1.80 EUR with the unit of 30 min
    assert (p2["hourly_rate_eur"], p2["billing_unit_min"], p2["daily_cap_eur"], p2["monthly_eur"]) == (1.8, 30, 9.5, 66.0)
    # the cheapest publicly purchasable product, not the dearer machine monthly ticket or the reserved space
    assert p2["rule_id"] == "P2_O2" and p1["rule_id"] == "P1_O1"
    assert p1["source_url"] == "https://www.example.org/p1/" and p2["window"] == (0.0, 24.0)
    assert p2["other_products"] == ("1 Monat am Parkautomaten 70.00 EUR; 1 Monat f\u00fcr Stellplatzmieter (fester Stellplatz) "
                                    "120.00 EUR")
    assert "Kiss&Ride" in p1["special"] and p2["special"] == ""
    assert p1["sheet"] == {"1 Stunde": 3.0, "1 Tag": 18.0, "1 Monat Dauerparken*": 130.0}


def test_a_commuter_product_is_the_monthly_amount_over_21_working_days_to_the_cent(sl, inputs):
    # calculated by hand: 130 / 21 = 6.190 -> 6.19 and 66 / 21 = 3.142 -> 3.14; the real values 120 / 21 = 5.714 -> 5.71 and
    # 74 / 21 = 3.524 -> 3.52 are pinned on the committed tariff table below
    evidence = sl.station_evidence(inputs)
    assert (evidence["bs_hbf_p1_nord"]["commuter_day_eur"], evidence["bs_hbf_p2_sued"]["commuter_day_eur"]) == (6.19, 3.14)
    assert (sl.commuter_day_eur(120.0), sl.commuter_day_eur(74.0)) == (5.71, 3.52)
    assert [cost.garage_monthly_day_cents(cents) for cents in (12000, 7400)] == [571, 352]


def test_another_amount_in_the_page_is_another_value_nothing_is_typed(sl, tmp_path):
    amounts = {**AMOUNTS["p2"], "rate": 1.10, "check": 2.20, "day": 11.00, "monthly": 69.00}
    content = _content(**{"evidence/contipark/p2.txt": _page(**amounts)})
    rules = json.loads(content["data/tariff_rules.json"])
    for rule in rules["rules"]:
        if rule["offer_id"] == "P2_O2":
            rule["monthly_amount_eur"] = rule["amount_eur"] = 69.0
    content["data/tariff_rules.json"] = json.dumps(rules)
    content["evidence/contipark/db_bahnpark_8000049.txt"] = _sheet(p2=(1.10, 2.20, 11.00, 69.00))
    values = sl.station_evidence(_load(sl, tmp_path, content=content))["bs_hbf_p2_sued"]
    assert (values["hourly_rate_eur"], values["daily_cap_eur"], values["monthly_eur"], values["commuter_day_eur"]) == (
        2.2, 11.0, 69.0, 3.29)


@pytest.mark.parametrize("edit, message", [
    (lambda text: text.replace("1 Tag\n", "1 Woche\n"), "has no line '1 Tag'"),
    (lambda text: text.replace("Preis\n", "Preis\n1 Tag\n" + _amount(5.0) + "\n"), "appears twice"),
    (lambda text: text.replace("Regul\u00e4res Parkentgelt", "Parkentgelt"), "no fee table"),
    (lambda text: text.replace("Zeiteinheit\nPreis\n", "Preis\n"), "does not start with the header lines"),
    (lambda text: text.replace(_amount(66.0), "66 Euro"), "has no amount line"),
    (lambda text: text.replace(_amount(1.80), _amount(1.70)), "the unit is not read"),
], ids=["no_day_line", "duplicate_label", "no_table", "no_header", "amount_not_readable", "hour_contradicts_half_hour"])
def test_a_page_that_cannot_be_read_exactly_stops_the_step(sl, tmp_path, edit, message):
    content = _content(**{"evidence/contipark/p2.txt": edit(_page(**AMOUNTS["p2"]))})
    with pytest.raises(SystemExit, match=message):
        sl.station_evidence(_load(sl, tmp_path, content=content))


def test_a_not_modelled_tariff_that_the_page_does_not_state_is_refused(sl, tmp_path):
    special = P1_SPECIAL.replace("Abendtarif: 18:00 bis 02:00 Uhr: 5,00 maximal", "Nachttarif")
    content = _content(**{"evidence/contipark/p1.txt": _page(**{**AMOUNTS["p1"], "special": special})})
    with pytest.raises(SystemExit, match="Abendtarif"):
        sl.station_evidence(_load(sl, tmp_path, content=content))


@pytest.mark.parametrize("sheet, message", [
    (_sheet(p1=(3.10, 18.00, 130.00)), "the DB BahnPark sheet states 3.1 EUR"),
    (_sheet(p2=(0.90, 1.80, 9.50, 67.00)), "the DB BahnPark sheet states 67.0 EUR"),
    (_sheet(p3=(0.90, 1.80, 10.00, 66.00)), "the DB BahnPark sheet states 10.0 EUR"),
    (_sheet().replace("Parkplatz West", "Parkplatz Ost"), "0 heading"),
    (_sheet().replace("1 Monat Dauerparken*", "1 Monat Dauerparken"), "0 line"),
], ids=["rate", "monthly", "day", "no_heading", "no_monthly_line"])
def test_a_sheet_that_disagrees_with_the_page_or_cannot_be_read_stops_the_step(sl, tmp_path, sheet, message):
    content = _content(**{"evidence/contipark/db_bahnpark_8000049.txt": sheet})
    with pytest.raises(SystemExit, match=message):
        sl.station_evidence(_load(sl, tmp_path, content=content))


def _rules_with(content, edit):
    rules = json.loads(content["data/tariff_rules.json"])
    edit(rules["rules"])
    return json.dumps(rules)


@pytest.mark.parametrize("edit, message", [
    (lambda rules: rules[3].update(monthly_amount_eur=60.0), "the sources contradict"),
    (lambda rules: rules[4].update(monthly_amount_eur=50.0, product_name="1 Monat f\u00fcr Stellplatzmieter (fester Stellplatz)"),
     "the cheapest monthly product of the package costs 50.0"),
    (lambda rules: rules[3].update(model_usable_as_monthly_price=False), "the cheapest monthly product of the package costs 70.0"),
    (lambda rules: [rules.pop(i) for i in (4, 3, 2)], "holds no publicly purchasable monthly product"),
], ids=["page_differs", "cheaper_product_exists", "page_product_not_usable", "no_product"])
def test_the_monthly_product_must_be_the_cheapest_publicly_purchasable_one_and_equal_to_the_page(sl, tmp_path, edit, message):
    content = _content()
    content["data/tariff_rules.json"] = _rules_with(content, edit)
    with pytest.raises(SystemExit, match=message):
        sl.station_evidence(_load(sl, tmp_path, content=content))


def test_a_facility_key_that_differs_is_refused_so_an_id_is_never_taken_for_another_lot(sl, tmp_path):
    facilities = json.loads(_content()["data/facilities.json"])
    facilities[0]["facility_key"] = "braunschweig_hbf_west_p3"
    with pytest.raises(SystemExit, match="a wrong facility id is never read"):
        sl.station_evidence(_load(sl, tmp_path, content=_content(**{"data/facilities.json": json.dumps(facilities)})))


# ---------------------------------------------------------------------------------------- the zones
def _release(inputs, extra=()) -> gpd.GeoDataFrame:
    """A finished release in EPSG:25832: the BgA lot Willy-Brandt-Platz (the OSM 'Post' outline) and optional extra zones."""
    post = inputs["ways"][26163572]["geometry"]
    records = [{"zone_id": "bs_bga_willy_brandt_platz", "geometry_source": "ordinance_map", "source_url": "https://example.org/bga",
                "source_date": "2026-10-07", "digitised_on": "2026-10-07", "digitising_note": "the BgA lot",
                "section_buffer_m": None, "site_buffer_m": None, "reconstructed_section_m2": None, "geometry": post}]
    records += [{"zone_id": zone_id, "geometry_source": "osm_fee_tags", "source_url": "https://example.org/x",
                 "source_date": "2026-09-29", "digitised_on": "2026-09-29", "digitising_note": "a street zone",
                 "section_buffer_m": None, "site_buffer_m": None, "reconstructed_section_m2": None, "geometry": geometry}
                for zone_id, geometry in extra]
    return gpd.GeoDataFrame(records, geometry="geometry", crs="EPSG:25832")


def test_the_three_zones_are_the_50_m_areas_of_the_outlines_and_p1_is_cut_against_the_bga_zone(sl, inputs):
    release = _release(inputs)
    built = sl.build_station_zones(inputs, release)
    zones = {record["zone_id"]: record for record in built["records"]}
    assert list(zones) == list(sl.STATION_ZONE_IDS)
    assert {record["geometry_source"] for record in zones.values()} == {"single_site_buffered"}
    assert {record["site_buffer_m"] for record in zones.values()} == {50.0}
    assert {record["_ags"] for record in zones.values()} == {"03101000"}
    bga = release.geometry.iloc[0]
    for zone_id, record in zones.items():
        outline = inputs["ways"][next(s["way"] for s in sl.STATIONS if s["zone_id"] == zone_id)]["geometry"]
        assert record["geometry"].contains(outline.buffer(-0.5)), zone_id        # the lot lies in its zone
        assert record["geometry"].intersection(bga).area <= pz.OVERLAP_TOLERANCE_M2, zone_id
    # P2 and P3 are far from every other zone: exactly the 50 m area of their outline (up to the 0.5 m simplification)
    for zone_id in ("bs_hbf_p2_sued", "bs_hbf_p3_west"):
        way = next(s["way"] for s in sl.STATIONS if s["zone_id"] == zone_id)
        area = inputs["ways"][way]["geometry"].buffer(50.0).area
        assert zones[zone_id]["geometry"].area == pytest.approx(area, rel=0.01)
    # P1 loses the part of its 50 m area that the BgA zone covers
    cut = {(loser, winner): square for loser, winner, square in built["context"]["cuts"]}
    expected = inputs["ways"][25411279]["geometry"].buffer(50.0).intersection(bga).area
    assert expected > 100.0 and cut[("bs_hbf_p1_nord", "bs_bga_willy_brandt_platz")] == pytest.approx(expected, rel=0.01)
    assert list(cut) == [("bs_hbf_p1_nord", "bs_bga_willy_brandt_platz")]


def test_the_zones_never_overlap_one_another_where_two_50_m_areas_meet(sl, tmp_path):
    # two lots 60 m apart: their 50 m areas overlap, the nearer outline decides (spec D3)
    document = _osm()
    document["elements"][2]["geometry"] = _ring(10.5400, 52.2528, 10.5410, 52.2533)   # P3 south of P1, a few tens of metres
    built = sl.build_station_zones(_load(sl, tmp_path, document=document), _release(_load(sl, tmp_path / "again")))
    first, third = (record["geometry"] for record in built["records"] if record["zone_id"] in ("bs_hbf_p1_nord", "bs_hbf_p3_west"))
    assert first.intersection(third).area <= pz.OVERLAP_TOLERANCE_M2
    assert sum(1 for area in built["context"]["split"]["contested"].values() if not area.is_empty) == 2


def test_the_step_never_changes_another_zone_and_refuses_one_the_lots_would_have_to_cut(sl, inputs):
    p1_area = inputs["ways"][25411279]["geometry"].buffer(30.0)
    release = _release(inputs, extra=[("bs_zone_x", p1_area)])
    with pytest.raises(SystemExit, match="overlap committed zone"):
        sl.build_station_zones(inputs, release)
    clash = _release(inputs, extra=[("bs_hbf_p2_sued", box(0, 0, 1, 1))])
    with pytest.raises(SystemExit, match="the step appends and never replaces"):
        sl.build_station_zones(inputs, clash)


def test_a_way_that_is_not_the_lot_is_refused(sl, tmp_path):
    document = _osm()
    document["elements"][1]["tags"]["name"] = "Parkplatz Hbf Ost"
    with pytest.raises(SystemExit, match="is not the lot P2 Sued"):
        sl.build_station_zones(_load(sl, tmp_path, document=document), _release(_load(sl, tmp_path / "again")))
    document = _osm()
    document["elements"][0]["tags"]["fee"] = "no"
    with pytest.raises(SystemExit, match="is not the lot P1 Nord"):
        sl.build_station_zones(_load(sl, tmp_path / "third", document=document), _release(_load(sl, tmp_path / "again2")))


# ---------------------------------------------------------------------------------------- the tariff rows
def test_the_tariff_rows_carry_the_evidence_and_the_named_assumptions(sl, inputs):
    evidence = sl.station_evidence(inputs)
    rows = {row["zone_id"]: row for row in sl.tariff_rows(evidence, inputs)}
    p1, p2 = rows["bs_hbf_p1_nord"], rows["bs_hbf_p2_sued"]
    figures = ("hourly_rate_eur", "billing_unit_min", "daily_cap_eur", "commuter_day_eur")
    assert tuple(p1[column] for column in figures) == ("3.00", "60", "18.00", "6.19")
    assert tuple(p2[column] for column in figures) == ("1.80", "30", "9.50", "3.14")
    assert p1["resident_permits_valid"] == "false" and p1["workplace_class"] == "bs_outer" and p1["zone_type"] == "street_paid"
    # ruling R-4g-2: the window is read from the opening hours of an operator page, so the source is the assumption F1
    assert (p1["fee_start_h"], p1["fee_end_h"], p1["fee_window_source"]) == ("0.0", "24.0", "assumption")
    assert p1["max_stay_min"] == "" and p1["long_stay_product_eur"] == "" and p1["source_date"] == "2026-10-08"
    for assumption in ("ASSUMPTION C-a", "ASSUMPTION F1", "ASSUMPTION G-a", "ASSUMPTION G-b", "ASSUMPTION R2-a", "ASSUMPTION P2"):
        assert assumption in p1["notes"], assumption
    assert "Kiss&Ride" in p1["notes"] and "evening tariff" in p1["notes"] and "NOT modelled" in p2["notes"]
    assert "Kiss&Ride" not in p2["notes"] and p1["notes"].isascii()
    assert list(p1) == ["zone_id", "name", "municipality_ags", "zone_type", "workplace_class", "hourly_rate_eur",
                        "billing_unit_min",
                        "free_if_stay_at_most_min", "first_period_min", "first_period_eur", "daily_cap_eur", "max_stay_min",
                        "long_stay_product_eur", "member_day_eur", "guest_day_eur", "fee_start_h", "fee_end_h", "resident_exempt",
                        "source_url", "source_date", "valid_from", "fee_window_source", "notes", "commuter_day_eur",
                        "resident_permits_valid"]


def _table(sl, inputs, tmp_path, edit=None) -> pd.DataFrame:
    evidence = sl.station_evidence(inputs)
    path = tmp_path / "tariffs.csv"
    header = ",".join(sl.tariff_rows(evidence, inputs)[0])
    path.write_bytes((HEADER_BEFORE.replace("\n", "\r\n") + header + "\r\n").encode("utf-8"))
    sl.append_tariff_rows(path, sl.tariff_rows(evidence, inputs))
    frame = pd.read_csv(path, dtype=str, keep_default_na=False, comment="#")
    for column in ("hourly_rate_eur", "billing_unit_min", "daily_cap_eur", "fee_start_h", "fee_end_h", "commuter_day_eur"):
        frame[column] = pd.to_numeric(frame[column])
    for column in ("free_if_stay_at_most_min", "first_period_min", "first_period_eur", "max_stay_min", "long_stay_product_eur",
                   "member_day_eur", "guest_day_eur"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["resident_permits_valid"] = frame["resident_permits_valid"] == "true"
    if edit:
        edit(frame)
    return frame


def test_a_consistent_tariff_table_passes_the_check_and_reports_it(sl, inputs, tmp_path, capsys):
    sl.check_tariff_rows(sl.station_evidence(inputs), _table(sl, inputs, tmp_path))
    assert "station tariff rows agree with the evidence (3 rows" in capsys.readouterr().out


@pytest.mark.parametrize("edit, message", [
    (lambda frame: frame.__setitem__("hourly_rate_eur", 9.0), "hourly_rate_eur"),
    (lambda frame: frame.__setitem__("billing_unit_min", 15), "billing_unit_min"),
    (lambda frame: frame.__setitem__("daily_cap_eur", 1.0), "daily_cap_eur"),
    (lambda frame: frame.__setitem__("commuter_day_eur", 1.0), "commuter_day_eur"),
    (lambda frame: frame.__setitem__("fee_end_h", 18.0), "fee_end_h"),
    (lambda frame: frame.__setitem__("resident_permits_valid", True), "must be false"),
    (lambda frame: frame.__setitem__("workplace_class", "bs_zentrum"), "workplace_class"),
    (lambda frame: frame.__setitem__("max_stay_min", 60.0), "must stay empty"),
    (lambda frame: frame.__setitem__("fee_window_source", "municipal_page"), "fee_window_source must be 'assumption'"),
    (lambda frame: frame.__setitem__("notes", frame["notes"].str.replace("ASSUMPTION F1", "ASSUMPTION F9")),
     "the note must hold 'ASSUMPTION F1'"),
    (lambda frame: frame.__setitem__("notes", "no assumption named"), "the note must hold"),
    (lambda frame: frame.drop(index=[2], inplace=True), "tariff row missing"),
], ids=["rate", "unit", "cap", "commuter", "window", "permit", "class", "max_stay", "window_source_municipal_page",
        "no_f1_in_notes", "notes", "missing_row"])
def test_a_tariff_table_that_contradicts_the_evidence_is_refused(sl, inputs, tmp_path, edit, message):
    with pytest.raises(SystemExit, match=message):
        sl.check_tariff_rows(sl.station_evidence(inputs), _table(sl, inputs, tmp_path, edit))


def test_the_rows_are_appended_with_the_line_ending_of_the_table_and_never_twice(sl, inputs, tmp_path):
    evidence = sl.station_evidence(inputs)
    path = tmp_path / "tariffs.csv"
    header = ",".join(sl.tariff_rows(evidence, inputs)[0])
    old_row = "old_zone," + ",".join([""] * 24)
    path.write_bytes((HEADER_BEFORE.replace("\n", "\r\n") + header + "\r\n" + old_row + "\r\n").encode("utf-8"))
    sl.append_tariff_rows(path, sl.tariff_rows(evidence, inputs))
    raw = path.read_bytes()
    # the five header lines of the chain, the lines G-a and G-b, the column line, the old row and the three rows of the step
    assert raw.count(b"\r\n") == raw.count(b"\n") == 5 + 2 + 1 + 1 + 3 and raw.isascii()
    with pytest.raises(SystemExit, match="exists already"):
        sl.append_tariff_rows(path, sl.tariff_rows(evidence, inputs))
    other = tmp_path / "other.csv"
    other.write_bytes((HEADER_BEFORE.replace("\n", "\r\n") + header + "\r\n").encode("utf-8"))
    shifted = sl.tariff_rows(evidence, inputs)
    shifted[0] = {"extra": "x", **shifted[0]}
    with pytest.raises(SystemExit, match="has the columns"):
        sl.append_tariff_rows(other, shifted)


# ---------------------------------------------------------------------------------------- QA rows and the append
def test_the_qa_rows_compare_each_zone_with_its_area_and_outline_and_record_the_cut_and_the_post_lot(sl, inputs):
    release = _release(inputs)
    built = sl.build_station_zones(inputs, release)
    written = release.copy()
    written = pd.concat([written, gpd.GeoDataFrame(
        [{"zone_id": record["zone_id"], "geometry": record["geometry"]} for record in built["records"]], crs="EPSG:25832")],
        ignore_index=True)
    rows = sl.qa_rows(built["context"], written)
    ids = [row["row_id"] for row in rows]
    assert ids == ["bs_hbf_p1_nord_release_vs_50m_area", "bs_hbf_p1_nord_release_vs_osm_outline",
                   "bs_hbf_p2_sued_release_vs_50m_area", "bs_hbf_p2_sued_release_vs_osm_outline",
                   "bs_hbf_p3_west_release_vs_50m_area", "bs_hbf_p3_west_release_vs_osm_outline",
                   "bs_hbf_p1_nord_cut_by_bs_bga_willy_brandt_platz", "bs_bga_willy_brandt_platz_vs_osm_post_lot"]
    by_id = {row["row_id"]: row for row in rows}
    assert all(row["municipality_ags"] == "03101000" for row in rows) and all(str(row["note"]).isascii() for row in rows)
    assert float(by_id["bs_hbf_p2_sued_release_vs_osm_outline"]["reference_share_in_subject"]) == pytest.approx(1.0, abs=1e-6)
    assert by_id["bs_hbf_p1_nord_cut_by_bs_bga_willy_brandt_platz"]["release_zone_id"] == ""
    assert by_id["bs_hbf_p1_nord_release_vs_50m_area"]["release_zone_id"] == "bs_hbf_p1_nord"
    assert float(by_id["bs_bga_willy_brandt_platz_vs_osm_post_lot"]["subject_share_in_reference"]) == pytest.approx(1.0, abs=1e-6)


def test_the_zones_are_appended_to_a_finished_release_and_every_other_feature_stays_byte_for_byte(sl, inputs, tmp_path):
    source = tmp_path / "release" / "parking_zones_2026.geojson"
    source.parent.mkdir()
    # the far zone is a square in the UTM zone but outside any real city: the bounds are irrelevant to the step
    release = _release(inputs, extra=[("bs_zone_far", box(600000, 5800000, 600100, 5800100))])
    assembly = importlib.import_module("assemble_parking_zones")
    assembly.write_zone_file(release, source, municipal=True, regional=True)
    target = tmp_path / "out" / "parking_zones_2026.geojson"
    target.parent.mkdir()
    built = sl.append_zones(inputs, source, target)
    before = source.read_text(encoding="utf-8").splitlines()
    after = target.read_text(encoding="utf-8").splitlines()
    features_before = [line for line in before if line.startswith('{ "type": "Feature"')]
    features_after = [line for line in after if line.startswith('{ "type": "Feature"')]
    assert len(features_before) == 2 and len(features_after) == 5
    # the old features are the same text except that the last one gets its comma; the new ones follow
    assert [line.rstrip(",") for line in features_after[:2]] == [line.rstrip(",") for line in features_before]
    loaded = pz.load_zone_polygons(target, max_repairs=0)
    assert list(loaded["zone_id"]) == ["bs_bga_willy_brandt_platz", "bs_zone_far", *sl.STATION_ZONE_IDS]
    assert set(loaded.loc[loaded["zone_id"].isin(sl.STATION_ZONE_IDS), "geometry_source"]) == {"single_site_buffered"}
    assert "OpenStreetMap contributors" in after[2] and "Braunschweig Hauptbahnhof car parks" in after[2]
    assert built["context"]["release"].shape[0] == 5
    # a second append to the result is refused instead of duplicating the zones
    with pytest.raises(SystemExit, match="never replaces"):
        sl.append_zones(inputs, target, tmp_path / "out" / "again.geojson")


def test_the_qa_rows_are_appended_with_the_station_paragraph_in_the_header(sl, tmp_path):
    from braunschweig.parking import municipal_zone_qa as mq

    path = tmp_path / "municipal_qa.csv"
    mz, rz = importlib.import_module("municipal_zones"), importlib.import_module("regional_zones")
    existing = [{column: "" for column in mq.MUNICIPAL_QA_COLUMNS} | {"row_id": "old_row", "municipality_ags": "03101000",
                                                                      "note": "an old row"}]
    mz.write_qa_table(path, existing, rz.qa_intro(mz.QA_INTRO))
    new = [{column: "" for column in mq.MUNICIPAL_QA_COLUMNS} | {"row_id": "bs_hbf_p1_nord_release_vs_osm_outline",
                                                                 "municipality_ags": "03101000", "note": "a new row"}]
    sl.append_qa_rows(path, new)
    text = path.read_text(encoding="utf-8")
    assert "Spec Amendment G (the station car parks of Braunschweig Hbf" in text and text.isascii()
    table = mq.load_municipal_qa(path)
    assert list(table["row_id"]) == ["old_row", "bs_hbf_p1_nord_release_vs_osm_outline"]
    with pytest.raises(SystemExit, match="exist already"):
        sl.append_qa_rows(path, new)
    with pytest.raises(SystemExit, match="units sentence"):
        sl.qa_intro("an intro without the sentence")


# ---------------------------------------------------------------------------------------- the committed release
def _committed_tariffs() -> pd.DataFrame:
    return pz.load_tariffs(COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").set_index("zone_id")


def _committed_zones() -> gpd.GeoDataFrame:
    return pz.load_zone_polygons(committed_parking_path("parking_zones_2026.geojson"), max_repairs=0).set_index("zone_id")


def test_the_committed_release_holds_the_three_station_zones_cut_against_the_bga_zone():
    zones = _committed_zones()
    assert len(zones) == 40
    station = zones.loc[["bs_hbf_p1_nord", "bs_hbf_p2_sued", "bs_hbf_p3_west"]]
    assert set(station["geometry_source"]) == {"single_site_buffered"} and set(station["site_buffer_m"]) == {50.0}
    # the 50 m areas of the OSM outlines (6,338, 9,539 and 2,345 m2 outline: 30,695, 44,052 and 21,692 m2 buffer), simplified and
    # cut: P2 and P3 keep almost all of it, P1 loses the BgA lot (958 m2 overlap with the buffer)
    assert station.loc["bs_hbf_p1_nord", "geometry"].area == pytest.approx(29619.0, abs=2.0)
    assert station.loc["bs_hbf_p2_sued", "geometry"].area == pytest.approx(43931.0, abs=2.0)
    assert station.loc["bs_hbf_p3_west", "geometry"].area == pytest.approx(21623.0, abs=2.0)
    bga = zones.loc["bs_bga_willy_brandt_platz", "geometry"]
    for zone_id in station.index:
        for other in zones.index:
            if other != zone_id:
                overlap = station.loc[zone_id, "geometry"].intersection(zones.loc[other, "geometry"]).area
                assert overlap <= pz.OVERLAP_TOLERANCE_M2, (zone_id, other)
    assert station.loc["bs_hbf_p1_nord", "geometry"].distance(bga) < 1.0     # P1 borders the BgA lot, it does not overlap it
    qa = pd.read_csv(COMMITTED_PARKING_DIR / "parking_zones_2026_municipal_qa.csv", comment="#", dtype=str, keep_default_na=False)
    cut = qa[qa["row_id"] == "bs_hbf_p1_nord_cut_by_bs_bga_willy_brandt_platz"].iloc[0]
    assert float(cut["overlap_area_m2"]) == pytest.approx(958.3, abs=0.5)
    assert set(qa["row_id"]) >= {f"{zone_id}_release_vs_{kind}" for zone_id in station.index
                                 for kind in ("50m_area", "osm_outline")}


def test_the_committed_station_rows_carry_the_published_tariffs_and_the_commuter_cents():
    tariffs = _committed_tariffs()
    # written from the Contipark pages of 2026-10-08 (the evidence p1.txt, p2.txt, p3.txt of the package): P1 2.50 EUR per
    # started hour, '1 Tag' 17.00 EUR, '1 Monat fuer Stellplatzmieter' 120.00 EUR; P2 and P3 1.10 EUR per started 30 min
    # (2.20 EUR per hour), '1 Tag' 11.00 EUR, 74.00 EUR; open around the clock; resident permits not valid
    expected = {"bs_hbf_p1_nord": (2.5, 60, 17.0, 5.71, 571, 120.0), "bs_hbf_p2_sued": (2.2, 30, 11.0, 3.52, 352, 74.0),
                "bs_hbf_p3_west": (2.2, 30, 11.0, 3.52, 352, 74.0)}
    for zone_id, (rate, unit, day, commuter, cents, monthly) in expected.items():
        row = tariffs.loc[zone_id]
        assert (row["hourly_rate_eur"], row["billing_unit_min"], row["daily_cap_eur"], row["commuter_day_eur"]) == (
            rate, unit, day, commuter), zone_id
        assert (row["zone_type"], row["municipality_ags"], row["workplace_class"]) == ("street_paid", "03101000", "bs_outer")
        assert (row["fee_start_h"], row["fee_end_h"], row["fee_window_source"]) == (0.0, 24.0, "assumption")
        assert row["resident_permits_valid"] == False and row["resident_exempt"] == False, zone_id  # noqa: E712
        assert pd.isna(row["max_stay_min"]) and pd.isna(row["first_period_eur"]) and pd.isna(row["long_stay_product_eur"]), zone_id
        assert row["commuter_day_eur"] == round(monthly / 21, 2) and row["notes"].isascii(), zone_id
        assert tariff_row_to_zone({**row.to_dict(), "zone_id": zone_id}).commuter_day_cents == cents
        for assumption in ("C-a", "F1", "G-a", "G-b", "R2-a", "P2"):
            assert f"ASSUMPTION {assumption}" in row["notes"], (zone_id, assumption)
    assert "Kiss&Ride" in tariffs.loc["bs_hbf_p1_nord", "notes"] and "evening tariff" in tariffs.loc["bs_hbf_p1_nord", "notes"]


def _station_tariff(zone_id: str) -> cost.ZoneTariff:
    row = _committed_tariffs().loc[zone_id].to_dict()
    return tariff_row_to_zone({**row, "zone_id": zone_id})


def _stay(zone_id, hours, purpose):
    return cost.parking_cost_cents(_station_tariff(zone_id), 28800, 28800 + int(hours * HOUR_S), purpose=purpose,
                                   parking_free=False, resident_of_zone=False)


def test_a_work_stay_in_a_station_zone_pays_the_cheaper_of_the_street_product_and_the_commuter_product_by_hand():
    # P2 and P3: 1.10 EUR per started 30 min. 8 h = 16 units x 110 = 1760 ct -> day maximum 1100 ct; the commuter product is
    # 7400 / 21 = 352 ct. Work and education pay min(1100, 352) = 352 ct; a shopper pays the metered 1100 ct; a 1 h stay is 2 x 110
    # = 220 ct, cheaper than the commuter share, so the metered 220 ct
    for zone_id in ("bs_hbf_p2_sued", "bs_hbf_p3_west"):
        assert _stay(zone_id, 8, "work")[0] == 352 and _stay(zone_id, 8, "education")[0] == 352
        assert _stay(zone_id, 8, "shop")[0] == 1100 and _stay(zone_id, 1, "work")[0] == 220
        assert _stay(zone_id, 8, "home")[0] == 0
    # P1: 2.50 EUR per started hour. 8 h = 2000 ct -> day maximum 1700 ct; 12000 / 21 = 571 ct. Work pays 571 ct, a shopper 1700
    # ct; 1 h is 250 ct metered
    assert _stay("bs_hbf_p1_nord", 8, "work")[0] == 571 and _stay("bs_hbf_p1_nord", 8, "shop")[0] == 1700
    assert _stay("bs_hbf_p1_nord", 1, "work")[0] == 250


def test_the_station_zones_change_neither_the_java_contract_nor_the_golden_file():
    # the zones and the tariff rows are data: the tariff model keeps its schema 3, the Java reader and the golden fixtures keep
    # their committed bytes (the LF hashes are pinned in test_parking_cost.py, ADR-0140 cross-language contract)
    from braunschweig.parking import tariff_export

    pinned = importlib.import_module("tests.test_parking_cost")
    assert tariff_export.SCHEMA_VERSION == 3
    for name, digest in pinned.PINNED_FIXTURE_SHA256.items():
        assert tariff_export.content_sha256(pinned.GOLDEN_JSON.parent / name) == digest, name


# ---------------------------------------------------------------------------------------- the header of the tariff table
HEADER_BEFORE = (
    "# ASSUMPTION P2: commuter_day_eur is a product. They are not used (spec Amendment D2, ruling R-D2-a; bites: "
    "bs_zone_ib and the six TU zones).\n"
    "# ASSUMPTION R2-a: Permits; the five separately operated BgA car parks and the Goslar car park "
    "Klubgartenstrasse/ZOB are marked false (bites: bs_bga_willy_brandt_platz, gs_parkplatz_klubgartenstrasse_zob; "
    "the campus rows take the default).\n"
    "# ASSUMPTION C-a: A destination within 50 m (bites: wob_tarifzone_1, bh_berliner_platz, se_am_markt, br_hexenritt, "
    "br_wurmberg).\n"
    "# ASSUMPTION D3-b: The ParkGO ceiling.\n"
    "# resident_zone rows carry hourly_rate_eur 0.00.\n")


def test_the_step_writes_the_header_lines_of_the_station_rows_idempotently(sl):
    once = sl.station_header(HEADER_BEFORE)
    assert once != HEADER_BEFORE and sl.station_header(once) == once
    lines = once.splitlines()
    # G-a and G-b come right after the line of D3-b, the bites lists name the three zones, the P2 line names the product
    index = next(i for i, line in enumerate(lines) if line.startswith("# ASSUMPTION D3-b:"))
    assert lines[index + 1].startswith("# ASSUMPTION G-a:") and lines[index + 2].startswith("# ASSUMPTION G-b:")
    assert lines[index + 3].startswith("# resident_zone rows")
    assert once.count("bs_hbf_p1_nord, bs_hbf_p2_sued, bs_hbf_p3_west") == 5
    assert "the six TU zones and the three station zones" in once
    assert "and the three DB BahnPark station car parks are marked false" in once
    # the same header with the line ending of the table
    assert sl.station_header(HEADER_BEFORE.replace("\n", "\r\n"), "\r\n").replace("\r\n", "\n") == once


@pytest.mark.parametrize("anchor", [
    "bites: bs_zone_ib and the six TU zones", "gs_parkplatz_klubgartenstrasse_zob; the campus rows",
    "the five separately operated BgA car parks and the Goslar", "br_wurmberg).", "# ASSUMPTION D3-b:"])
def test_a_header_without_an_anchor_line_of_the_chain_output_is_refused(sl, anchor):
    with pytest.raises(SystemExit, match="the step needs exactly one|inserts the lines G-a and G-b"):
        sl.station_header(HEADER_BEFORE.replace(anchor, "x"))


def test_the_chain_output_before_the_step_plus_the_step_is_the_committed_tariff_table_byte_for_byte(sl, tmp_path):
    import subprocess

    base = "9e5cc143"      # the commit before Task 4g: the table as the zone chain wrote it
    path = "eqasim-data/data/braunschweig/parking/parking_tariffs_2026.csv"
    blob = subprocess.run(["git", "show", f"{base}:{path}"], cwd=REPO_ROOT, capture_output=True)
    directory = COMMITTED_PARKING_DIR / "raw_sources" / "municipal_2026-10-08"
    if blob.returncode != 0 or not (directory / sl.PACKAGE_FILE).is_file() or not (directory / sl.OSM_FILE).is_file():
        pytest.skip("the base commit or the gitignored station inputs are not available")
    table = tmp_path / "parking_tariffs_2026.csv"
    table.write_bytes(blob.stdout.replace(b"\r\n", b"\n"))
    inputs = sl.load_station_inputs(directory / sl.PACKAGE_FILE, directory / sl.OSM_FILE)
    sl.append_tariff_rows(table, sl.tariff_rows(sl.station_evidence(inputs), inputs))
    committed = (COMMITTED_PARKING_DIR / "parking_tariffs_2026.csv").read_bytes().replace(b"\r\n", b"\n")
    assert table.read_bytes() == committed


def test_the_zone_file_written_by_the_step_must_carry_the_name_of_the_release(sl, tmp_path):
    with pytest.raises(SystemExit, match="must be named parking_zones_2026.geojson"):
        sl.main(["--station-dir", str(tmp_path), "--zones", str(tmp_path / "in.geojson"), "--zones-out",
                 str(tmp_path / "out.geojson"), "--tariffs", str(tmp_path / "t.csv"), "--municipal-qa", str(tmp_path / "q.csv")])
