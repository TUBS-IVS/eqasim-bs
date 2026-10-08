"""Monthly products of the Braunschweig garages (parking cost zones v2, spec Amendment F1 and F2, issue #436, Task 4f).

The owner's package ``Braunschweig_Monatstarife_2026-10-08.zip`` and the Contipark evidence directory are gitignored, so the
two further optional inputs of the curation step ``regional_garages.py`` (``bs_monthly_products.py``) are pinned on a synthetic
package written the way the owner supplied it (a zip with ``manifest.sha256``, ``data/tariff_rules.json``,
``data/tariff_observations.json``, ``data/facilities.json``, ``data/sources.json``, a README and the visibility evidence) and a
synthetic evidence directory with its ``SHA256SUMS``. What is pinned: the verification of the package, of every member that is
read and of the evidence directory; that an amount is READ (the cheapest rule of the facility, the 'weitere Monate ... Brutto'
line of the configurator text, the amount that the visibility evidence rejects) and never typed, with the refusal paths of
every reading (a rule that is not the cheapest, a configurator text without or with two amounts, net and VAT that do not add
up, a capture of another garage, a status that the package's records contradict, a README that does not hold the quoted
sentence); the status rows and the recorded products; and ASSUMPTION P13, the median per municipality
(``regional_garages.apply_monthly_imputation``: at least two published products, never another municipality, never a surface
lot, never next to a published product). The committed dataset is pinned independently in ``test_parking_regional_garages.py``
and the real package is read where it exists (the last tests).
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

from braunschweig.parking import garage_qa as pq
from braunschweig.parking import garages as pg

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATION_DIR = REPO_ROOT / "scripts" / "curation" / "parking_zones_2026"
COMMITTED_PARKING_DIR = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "parking"
ROOT = "Braunschweig_Monatstarife_2026-10-08"
EVIDENCE = "contipark_configurator_2026-10-08"
BS = "03101000"

README = (
    "# Braunschweig: Monats- und Dauerparkertarife\n\n"
    "Recherchestand 08.10.2026.\n\n"
    "## Ergebnis\n\n"
    "Magni: Dauerstellplätze laut Betreiber belegt, kein veröffentlichter Preis. Wallstraße: Dauerparken verlinkt, "
    "Preis hinter Anmeldung. Schloss, Eiermarkt: kein belastbarer aktueller Monatspreis ermittelt. Forschungsflughafen: "
    "sichtbare Betreibersektion nur Preis auf Anfrage; 85 EUR stehen in ausgeblendeten Seitenabschnitten.\n\n"
    "Wilhelmstraße: Tarifblatt 75/79 EUR brutto, gültig ab 01.05.2025, in Anbieteranzeige vom 26.08.2026. Monatsbezug "
    "fehlt ausdrücklich.\n")

CONFIGURATOR = (
    "# Evidence capture (visible page text; nothing submitted, no login)\n"
    "# url: https://stellplatz.example/produktkonfiguration?carparkId=1&productId=2\n"
    "# retrieved_at_utc: 2026-10-08T05:50:27.069Z\n"
    "# facility: Parkhaus Wallstrasse, Braunschweig (dataset garage bs_wall), operator Example GmbH\n"
    "# relevant content (verbatim, umlauts kept):\n"
    "Dauerparken Mo.-So. - 24 Stunden PKW\n"
    "Mindestvertragslaufzeit: 1 Monat Kündigungsfrist: 1 Monat zum Monatsende\n"
    "Monatsmiete\n"
    "1. Monat: Stellplätze 1 54,74 € | Netto 54,74 € | MwSt. 10,40 € | Brutto 65,14 €   (pro rata)\n"
    "weitere Monate: Stellplätze 1 96,60 € | Netto 96,60 € | MwSt. 18,35 € | Brutto 114,95 €\n")
NO_RENTAL = (
    "# Evidence capture (page inspection; nothing submitted, no login)\n"
    "# url: https://www.example.org/tiefgarage/\n"
    "# facility: Tiefgarage Schloss, Braunschweig (dataset garage bs_schloss), operator Example\n"
    "# finding: the location page offers no rental (Dauerparken) product and holds no link to the product configurator\n")


def _rule(offer_id, facility, code, amount, *, name=None, status="published_monthly_price", contract="not_confirmed",
          usable=True, period="month", monthly="same", sources=("SRC_TARIFF",), days=("MO", "TU", "WE", "TH", "FR"),
          start="06:30", end="21:00") -> dict:
    return {"offer_id": offer_id, "facility_id": facility, "offer_code": code, "amount_eur": amount,
            "billing_period": period, "product_name": name or f"Tarif {code} – Tagesparker", "evidence_status": status,
            "model_usable_as_monthly_price": usable, "monthly_amount_eur": amount if monthly == "same" else monthly,
            "new_contract_status": contract, "source_ids": list(sources), "vat_included": True, "minimum_term_months": None,
            "access_schedule": {"weekly_windows": [{"days": list(days), "start": start, "end": end, "end_day_offset": 0}]}}


def _offer(offer_id, facility, status, amount=None, name="Dauerstellplatz", period=None, contract="not_confirmed") -> dict:
    return {"offer_id": offer_id, "facility_id": facility, "evidence_status": status, "amount_eur": amount,
            "monthly_amount_eur": None, "billing_period": period, "product_name": name, "new_contract_status": contract,
            "source_ids": ["SRC_TARIFF"]}


def _facility(facility_id, key, kind="garage", review="monthly_price_unknown", contract="not_confirmed", offers=(),
              upstream=None, name=None) -> dict:
    return {"facility_id": facility_id, "facility_key": key, "facility_type": kind, "review_status": review,
            "new_contract_status": contract, "offer_ids": list(offers), "upstream_feature_id": upstream,
            "name": name or f"Parkhaus {key} Übung"}


def _package_content() -> dict:
    """The tables of the synthetic package: a garage with three published products (A is the cheapest), the Wallstrasse whose
    price only the configurator holds, a garage without any price information, a sold-out garage, a garage with a price on
    request, a garage whose amounts have no confirmed period, two garages that are no option (one with a season ticket that
    is dearer) and a station car park."""
    rules = [_rule("S_A", "F_STEIN", "A", 100.0), _rule("S_B", "F_STEIN", "B", 105.01, days=("MO", "TU", "WE", "TH", "FR", "SA")),
             _rule("S_C", "F_STEIN", "C", 130.0, days=("MO",), start="00:00", end="24:00"),
             _rule("E_1", "F_EVES", "x", 80.0, name="Dauerparken 24/7"),
             _rule("E_2", "F_EVES", "y", 170.0, name="Saisonparken Monat"),
             _rule("P1_1", "F_P1", "x", 120.0, name="1 Monat"), _rule("P1_2", "F_P1", "y", 160.0, name="1 Monat fest")]
    offers = [dict(_offer(rule["offer_id"], rule["facility_id"], "published_monthly_price", rule["amount_eur"]), **{
        "monthly_amount_eur": rule["monthly_amount_eur"], "product_name": rule["product_name"]}) for rule in rules]
    offers += [_offer("W_1", "F_WALL", "monthly_price_unknown", name="Dauerparken Mo.-So. – 24 Stunden PKW"),
               _offer("M_1", "F_SOLD", "monthly_price_unknown", contract="sold_out"),
               _offer("M_2", "F_SOLD", "not_a_monthly_product", 1.2, name="Kurzparken", period="started_hour"),
               _offer("R_1", "F_REQ", "monthly_price_unknown"),
               _offer("N_1", "F_NOPRICE", "monthly_price_unknown", name="Monatsstellplatz"),
               _offer("Q_1", "F_PERIOD", "billing_period_unconfirmed", 75.0, name="Dauerstellplatz Tarif A"),
               _offer("Q_2", "F_PERIOD", "billing_period_unconfirmed", 79.0, name="Dauerstellplatz Tarif B"),
               _offer("Q_3", "F_PERIOD", "not_a_monthly_product", 1.2, name="Kurzparken", period="hour")]
    facilities = [
        _facility("F_STEIN", "steinstrasse", review="monthly_price_confirmed", offers=("S_A", "S_B", "S_C")),
        _facility("F_WALL", "braunschweig_wallstrasse", offers=("W_1",)),
        _facility("F_SCHLOSS", "schloss", offers=("N_1",)),
        _facility("F_SOLD", "magni", contract="sold_out", offers=("M_1", "M_2")),
        _facility("F_REQ", "forschungsflughafen", offers=("R_1",), upstream="4781"),
        _facility("F_PERIOD", "wilhelmstrasse", review="billing_period_unconfirmed", offers=("Q_1", "Q_2", "Q_3")),
        _facility("F_EVES", "eves", review="monthly_price_confirmed", offers=("E_1", "E_2")),
        _facility("F_P1", "braunschweig_hbf_nord_p1", kind="surface_parking", review="monthly_price_confirmed",
                  offers=("P1_1", "P1_2"))]
    sources = [{"id": "SRC_TARIFF", "url": "https://op.example/tarife"}]
    visibility = {"rejected_price_eur": 85, "reason": "All occurrences lie in hidden sections."}
    return {"rules": rules, "offers": offers, "facilities": facilities, "sources": sources, "visibility": visibility,
            "readme": README}


def _write_package(directory: Path, content: dict, tamper: dict | None = None) -> tuple:
    """Write the package zip the way the owner supplied it; return (path, SHA-256). ``tamper`` replaces the bytes of members
    AFTER the manifest was computed (a member that no longer matches its entry)."""
    directory.mkdir(parents=True, exist_ok=True)
    members = {
        "data/tariff_rules.json": json.dumps({"rules": content["rules"]}),
        "data/tariff_observations.json": json.dumps({"offers": content["offers"], "historical_observations": []}),
        "data/facilities.json": json.dumps(content["facilities"]),
        "data/sources.json": json.dumps(content["sources"]),
        "evidence/apcoa/visibility_evidence.json": json.dumps(content["visibility"]),
        "README.md": content["readme"]}
    manifest = "".join(f"{hashlib.sha256(text.encode('utf-8')).hexdigest()}  {name}\n" for name, text in members.items())
    path = directory / f"{ROOT}.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for name, text in members.items():
            archive.writestr(f"{ROOT}/{name}", (tamper or {}).get(name, text))
        archive.writestr(f"{ROOT}/manifest.sha256", manifest)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def _write_evidence(directory: Path, configurator: str = CONFIGURATOR, no_rental: str = NO_RENTAL, extra: dict | None = None,
                    sums_override: dict | None = None) -> tuple:
    directory.mkdir(parents=True, exist_ok=True)
    files = {"wallstrasse_configurator.txt": configurator, "eiermarkt_no_rental_product.txt": no_rental}
    sums = {name: hashlib.sha256(text.encode("utf-8")).hexdigest() for name, text in files.items()}
    sums.update(sums_override or {})
    for name, text in {**files, **(extra or {})}.items():
        (directory / name).write_bytes(text.encode("utf-8"))
    sums_text = "\n".join(f"{digest} *{name}" for name, digest in sums.items())
    (directory / "SHA256SUMS").write_bytes(sums_text.encode("utf-8"))
    return directory, hashlib.sha256(sums_text.encode("utf-8")).hexdigest()


SPECS = (
    {"garage_id": "bs_stein", "facility": "F_STEIN", "facility_key": "steinstrasse", "status": "published", "rule": "S_A"},
    {"garage_id": "bs_wall", "facility": "F_WALL", "facility_key": "braunschweig_wallstrasse", "status": "published",
     "offer": "W_1", "quotes": (("Wallstraße", "Preis hinter Anmeldung"),)},
    {"garage_id": "bs_schloss", "facility": "F_SCHLOSS", "facility_key": "schloss", "status": "no_price",
     "quotes": (("Schloss", "kein belastbarer aktueller Monatspreis ermittelt"),), "evidence": "eiermarkt"},
    {"garage_id": "bs_magni", "facility": "F_SOLD", "facility_key": "magni", "status": "sold_out",
     "quotes": (("Magni", "Dauerstellplätze laut Betreiber belegt"),)},
    {"garage_id": "bs_req", "facility": "F_REQ", "facility_key": "forschungsflughafen", "status": "price_on_request",
     "quotes": (("Forschungsflughafen", "Preis auf Anfrage"),), "hidden_amount": True},
    {"garage_id": "bs_period", "facility": "F_PERIOD", "facility_key": "wilhelmstrasse", "status": "period_unconfirmed",
     "quotes": (("Wilhelmstraße", "Tarifblatt 75/79 EUR brutto"), ("Monatsbezug", "Monatsbezug fehlt ausdrücklich"))},
)
RECORDED = (
    {"record_id": "monthly_bs_eves", "facility": "F_EVES", "facility_key": "eves", "facility_type": "garage",
     "reason": "not_a_dataset_option", "why": "a garage that the dataset does not list"},
    {"record_id": "monthly_bs_p1", "facility": "F_P1", "facility_key": "braunschweig_hbf_nord_p1",
     "facility_type": "surface_parking", "reason": "station_bahnpark", "why": "a station car park"},
)


@pytest.fixture(scope="module")
def step():
    """The curation step as a module (it imports its siblings, ``bs_monthly_products`` among them, from its own directory)."""
    sys.path.insert(0, str(CURATION_DIR))
    try:
        spec = importlib.util.spec_from_file_location("regional_garages_for_monthly", CURATION_DIR / "regional_garages.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(CURATION_DIR))
    return module


@pytest.fixture(scope="module")
def bsm(step):
    return step.bsm


@pytest.fixture()
def inputs(bsm, tmp_path):
    path, sha256 = _write_package(tmp_path / "package", _package_content())
    directory, sums_sha256 = _write_evidence(tmp_path / "evidence")
    return bsm.load_monthly_inputs(path, directory, sha256, sums_sha256)


def _load(bsm, tmp_path, content=None, tamper=None, evidence=None):
    path, sha256 = _write_package(tmp_path / "package", content or _package_content(), tamper)
    directory, sums_sha256 = evidence or _write_evidence(tmp_path / "evidence")
    return bsm.load_monthly_inputs(path, directory, sha256, sums_sha256)


# --------------------------------------------------------------------------- verification before anything is read


def test_the_pins_are_the_hashes_the_manifest_records(bsm):
    assert bsm.PACKAGE_SHA256 == "72fec30d4515a3f7468af66222802697b09ae042b283adc335b5ae3607650c81"
    assert bsm.EVIDENCE_SUMS_SHA256 == "2617d497bddfaadced2ff433cd4b675122570a58ca7b6b2b49c27b0514a76ceb"
    assert bsm.PACKAGE_FILE == "Braunschweig_Monatstarife_2026-10-08.zip"
    assert bsm.EVIDENCE_DIRECTORY_NAME == "contipark_configurator_2026-10-08"


def test_a_changed_or_missing_package_is_refused(bsm, tmp_path):
    path, sha256 = _write_package(tmp_path / "package", _package_content())
    directory, sums_sha256 = _write_evidence(tmp_path / "evidence")
    with pytest.raises(SystemExit, match="is not the recorded"):
        bsm.load_monthly_inputs(path, directory, "0" * 64, sums_sha256)
    with pytest.raises(SystemExit, match="missing: pass the owner's package"):
        bsm.load_monthly_inputs(tmp_path / "absent.zip", directory, sha256, sums_sha256)


def test_a_member_that_differs_from_the_manifest_of_the_package_is_never_read(bsm, tmp_path):
    with pytest.raises(SystemExit, match="a changed member is never read"):
        _load(bsm, tmp_path, tamper={"data/tariff_rules.json": json.dumps({"rules": []})})


def test_a_changed_or_extended_evidence_directory_is_refused(bsm, tmp_path):
    path, sha256 = _write_package(tmp_path / "package", _package_content())
    directory, sums_sha256 = _write_evidence(tmp_path / "evidence")
    with pytest.raises(SystemExit, match="is not the recorded"):
        bsm.load_monthly_inputs(path, directory, sha256, "1" * 64)
    with pytest.raises(SystemExit, match="SHA256SUMS missing|missing: pass the evidence directory"):
        bsm.load_monthly_inputs(path, tmp_path / "nowhere", sha256, sums_sha256)
    # a file that the sums do not list
    other, other_sums = _write_evidence(tmp_path / "extended", extra={"notes.txt": "x"})
    with pytest.raises(SystemExit, match="that the sums do not vouch for is never read"):
        bsm.load_monthly_inputs(path, other, sha256, other_sums)
    # a file whose bytes differ from its entry (the sums file itself is consistent and pinned)
    wrong, wrong_sums = _write_evidence(tmp_path / "wrong", sums_override={"wallstrasse_configurator.txt": "2" * 64})
    with pytest.raises(SystemExit, match="a changed evidence file is never read"):
        bsm.load_monthly_inputs(path, wrong, sha256, wrong_sums)


# --------------------------------------------------------------------------- F1: the published products are read


def test_the_published_product_is_the_cheapest_rule_of_its_facility_read_from_the_package(bsm, inputs):
    found = bsm.read_garage_products(inputs, SPECS)["bs_stein"]
    assert found["status"] == "published" and found["facility"] == "F_STEIN"
    monthly = found["monthly"]
    assert monthly["monthly_eur"] == 100.0 and monthly["rule_id"] == "S_A"
    assert monthly["monthly_source_url"] == "https://op.example/tarife"
    assert monthly["monthly_product"].startswith(
        "Tarif A - Tagesparker: 100.00 EUR per month, VAT included; access Mo-Fr 06:30-21:00")
    by_id = {product["record_id"]: product for product in found["products"]}
    assert (by_id["monthly_bs_stein_a"]["decision"], by_id["monthly_bs_stein_a"]["amount_eur"]) == ("used", 100.0)
    # the two dearer products are recorded and not used, with their own amounts read from their rules
    assert (by_id["monthly_bs_stein_b"]["reason"], by_id["monthly_bs_stein_b"]["amount_eur"]) == ("not_the_cheapest", 105.01)
    assert (by_id["monthly_bs_stein_c"]["reason"], by_id["monthly_bs_stein_c"]["amount_eur"]) == ("not_the_cheapest", 130.0)
    assert "not modelled" in by_id["monthly_bs_stein_a"]["note"]
    assert found["note"].startswith(f"Monthly product (spec Amendment F1, package {bsm.PACKAGE_FILE}): published, Tarif A")


def test_a_changed_amount_in_the_package_changes_the_value_it_is_read_not_typed(bsm, tmp_path):
    content = _package_content()
    for table in ("rules", "offers"):
        for record in content[table]:
            if record["offer_id"] == "S_A":
                record["amount_eur"] = record["monthly_amount_eur"] = 96.5
    found = bsm.read_garage_products(_load(bsm, tmp_path, content), SPECS)["bs_stein"]
    assert found["monthly"]["monthly_eur"] == 96.5


def test_a_specification_that_names_a_rule_that_is_not_the_cheapest_stops_the_step(bsm, inputs):
    wrong = ({**SPECS[0], "rule": "S_B"},)
    with pytest.raises(SystemExit, match="is not the one cheapest product"):
        bsm.read_garage_products(inputs, wrong)


@pytest.mark.parametrize("rule, message", [
    ("E_1", "is no publicly purchasable monthly product of F_STEIN"),   # a rule of another facility
    ("S_Z", "is no publicly purchasable monthly product of F_STEIN"),   # a rule the package does not hold
], ids=["another_facility", "unknown_rule"])
def test_a_rule_that_is_no_product_of_the_facility_is_refused(bsm, inputs, rule, message):
    with pytest.raises(SystemExit, match=message):
        bsm.read_garage_products(inputs, ({**SPECS[0], "rule": rule},))


def test_a_rule_that_the_package_does_not_mark_usable_is_no_product(bsm, tmp_path):
    content = _package_content()
    for record in content["rules"]:
        if record["offer_id"] == "S_A":
            record["model_usable_as_monthly_price"] = False
    with pytest.raises(SystemExit, match="is no publicly purchasable monthly product"):
        bsm.read_garage_products(_load(bsm, tmp_path, content), SPECS)


def test_a_facility_key_that_differs_is_refused_so_an_id_is_never_taken_for_another_garage(bsm, inputs):
    with pytest.raises(SystemExit, match="a wrong facility id is never read"):
        bsm.read_garage_products(inputs, ({**SPECS[0], "facility_key": "eves"},))
    with pytest.raises(SystemExit, match="has no facility F_UNKNOWN"):
        bsm.read_garage_products(inputs, ({**SPECS[0], "facility": "F_UNKNOWN"},))


def test_two_specifications_of_one_garage_and_an_unknown_status_are_refused(bsm, inputs):
    with pytest.raises(SystemExit, match="two monthly specifications for bs_stein"):
        bsm.read_garage_products(inputs, (SPECS[0], SPECS[0]))
    with pytest.raises(SystemExit, match="unknown status 'maybe'"):
        bsm.read_garage_products(inputs, ({**SPECS[0], "status": "maybe"},))


# --------------------------------------------------------------------------- F1: the configurator text


def test_the_wallstrasse_product_is_the_gross_amount_of_the_further_months_of_the_configurator_text(bsm, inputs):
    found = bsm.read_garage_products(inputs, SPECS)["bs_wall"]
    monthly = found["monthly"]
    assert monthly["monthly_eur"] == 114.95
    assert monthly["monthly_source_url"] == "https://stellplatz.example/produktkonfiguration?carparkId=1&productId=2"
    assert "114.95 EUR gross per further month (net 96.60 + VAT 18.35), first month pro rata" in monthly["monthly_product"]
    assert "minimum term 1 Monat, notice period 1 Monat zum Monatsende" in monthly["monthly_product"]
    assert monthly["monthly_product"].isascii()
    [product] = found["products"]
    assert (product["decision"], product["amount_eur"], product["reason"]) == ("used", 114.95, "")
    # the package itself records no price for the product: its own sentence is quoted, the first month is not the price
    assert "Wallstrasse: Dauerparken verlinkt, Preis hinter Anmeldung." in product["note"]
    assert "65.14" not in monthly["monthly_product"] and "retrieved 2026-10-08T05:50:27.069Z" in product["note"]
    assert f"{EVIDENCE}/wallstrasse_configurator.txt" in product["evidence"]


def test_a_different_amount_in_the_capture_is_a_different_product_price(bsm, tmp_path):
    changed = CONFIGURATOR.replace("96,60", "83,19").replace("18,35", "15,81").replace("114,95", "99,00")
    found = bsm.read_garage_products(_load(bsm, tmp_path, evidence=_write_evidence(tmp_path / "e2", configurator=changed)),
                                     SPECS)["bs_wall"]
    assert found["monthly"]["monthly_eur"] == 99.0


@pytest.mark.parametrize("edit, message", [
    (lambda text: text.replace("weitere Monate:", "danach:"), "0 line"),
    (lambda text: text + "weitere Monate: Stellplätze 1 96,60 € | Netto 96,60 € | MwSt. 18,35 € | "
                         "Brutto 114,95 €\n", "2 line"),
    (lambda text: text.replace("Brutto 114,95", "Brutto 115,95"), "net 96.60 \\+ VAT 18.35 is not the gross amount 115.95"),
    (lambda text: text.replace("(dataset garage bs_wall)", "(dataset garage bs_other)"), "not of the dataset garage bs_wall"),
    (lambda text: text.replace("# url: https://stellplatz.example/produktkonfiguration?carparkId=1&productId=2\n", ""),
     "no '# url:' header line"),
    (lambda text: text.replace("Dauerparken Mo.-So. - 24 Stunden PKW", "Tagesparken"), "is not the package's offer"),
], ids=["no_amount", "two_amounts", "vat_does_not_add_up", "other_garage", "no_url", "other_product"])
def test_a_configurator_text_that_cannot_be_read_exactly_stops_the_step(bsm, tmp_path, edit, message):
    inputs = _load(bsm, tmp_path, evidence=_write_evidence(tmp_path / "e2", configurator=edit(CONFIGURATOR)))
    with pytest.raises(SystemExit, match=message):
        bsm.read_garage_products(inputs, SPECS)


def test_a_configurator_reading_for_a_product_whose_price_the_package_knows_is_refused(bsm, tmp_path):
    content = _package_content()
    for record in content["offers"]:
        if record["offer_id"] == "W_1":
            record["monthly_amount_eur"] = 120.0
    with pytest.raises(SystemExit, match="the package states a price for W_1"):
        bsm.read_garage_products(_load(bsm, tmp_path, content), SPECS)


# --------------------------------------------------------------------------- F1: the status of a garage without a product


def test_a_garage_without_a_published_product_gets_its_status_with_the_package_wording(bsm, inputs):
    found = bsm.read_garage_products(inputs, SPECS)
    assert {garage: entry["status"] for garage, entry in found.items()} == {
        "bs_stein": "published", "bs_wall": "published", "bs_schloss": "no_price", "bs_magni": "sold_out",
        "bs_req": "price_on_request", "bs_period": "period_unconfirmed"}
    assert all(entry["monthly"] is None for garage, entry in found.items() if entry["status"] != "published")
    magni = found["bs_magni"]
    [product] = magni["products"]
    assert (product["decision"], product["reason"], product["amount_eur"]) == ("not_used", "sold_out", None)
    assert "Magni: Dauerstellplaetze laut Betreiber belegt, kein veroeffentlichter Preis." in product["note"]
    assert "no monthly price is published (Magni: Dauerstellplaetze" in magni["note"]
    # the sentence of the package is cut at its sentence boundary: the next sentence is not part of the quotation
    assert "Wallstrasse" not in product["note"]
    schloss = found["bs_schloss"]["products"][0]
    assert "Schloss, Eiermarkt: kein belastbarer aktueller Monatspreis ermittelt." in schloss["note"]
    assert "the location page offers no rental (Dauerparken) product" in schloss["note"]
    assert f"{EVIDENCE}/eiermarkt_no_rental_product.txt" in schloss["evidence"]


def test_unconfirmed_amounts_and_the_hidden_amount_are_recorded_and_not_used_with_the_package_amounts(bsm, inputs):
    found = bsm.read_garage_products(inputs, SPECS)
    period = {product["record_id"]: product for product in found["bs_period"]["products"]}
    assert sorted(period) == ["monthly_bs_period_q_1", "monthly_bs_period_q_2"]
    assert [(product["decision"], product["reason"], product["amount_eur"]) for product in period.values()] == [
        ("not_used", "period_unconfirmed", 75.0), ("not_used", "period_unconfirmed", 79.0)]
    assert "Monatsbezug fehlt ausdruecklich" in period["monthly_bs_period_q_1"]["note"]
    hidden = {product["record_id"]: product for product in found["bs_req"]["products"]}
    assert hidden["monthly_bs_req_hidden"]["reason"] == "excluded_by_package"
    assert hidden["monthly_bs_req_hidden"]["amount_eur"] == 85.0
    assert hidden["monthly_bs_req_price_on_request"]["amount_eur"] is None


@pytest.mark.parametrize("edit, message", [
    # a rule exists although the specification says there is no price
    (lambda content: (content["rules"].append(_rule("N_R", "F_SCHLOSS", "A", 70.0)),
                      content["offers"].append(_offer("N_R", "F_SCHLOSS", "published_monthly_price", 70.0))),
     "the package holds monthly rules"),
    # the review status of the record does not say what the status says
    (lambda content: content["facilities"][2].update(review_status="monthly_price_confirmed"), "needs 'monthly_price_unknown'"),
    (lambda content: content["facilities"][3].update(new_contract_status="not_confirmed"), "needs new_contract_status 'sold_out'"),
    (lambda content: content["facilities"][2].update(new_contract_status="sold_out"),
     "the package states the facility sold out, the specification says no_price"),
    (lambda content: content["facilities"][5].update(review_status="monthly_price_unknown"), "needs 'billing_period_unconfirmed'"),
    (lambda content: content["offers"].remove(next(o for o in content["offers"] if o["offer_id"] == "R_1")) or
     content["facilities"][4].update(offer_ids=[]), "needs an offer without a known price"),
    # an offer states a monthly amount
    (lambda content: next(o for o in content["offers"] if o["offer_id"] == "N_1").update(monthly_amount_eur=70.0),
     "state a monthly amount, the specification says no_price"),
    # the README does not hold the sentence
    (lambda content: content.update(readme=README.replace("Preis auf Anfrage", "Preis unbekannt")), "0 sentence"),
    (lambda content: content.update(readme=README + "\nMagni: Dauerstellplätze laut Betreiber belegt.\n"), "2 sentence"),
], ids=["rule_exists", "review_not_unknown", "contract_not_sold_out", "sold_out_but_no_price", "period_review", "no_offer",
        "offer_priced", "sentence_missing", "sentence_twice"])
def test_a_status_that_the_package_contradicts_stops_the_step(bsm, tmp_path, edit, message):
    content = _package_content()
    edit(content)
    with pytest.raises(SystemExit, match=message):
        bsm.read_garage_products(_load(bsm, tmp_path, content), SPECS)


def test_the_eiermarkt_capture_must_be_of_the_garage_and_state_that_no_rental_product_exists(bsm, tmp_path):
    other = NO_RENTAL.replace("bs_schloss", "bs_other")
    with pytest.raises(SystemExit, match="not of the dataset garage bs_schloss"):
        bsm.read_garage_products(_load(bsm, tmp_path, evidence=_write_evidence(tmp_path / "e2", no_rental=other)), SPECS)
    silent = NO_RENTAL.replace("no rental", "a rental")
    with pytest.raises(SystemExit, match="does not state that no rental product is offered"):
        bsm.read_garage_products(_load(bsm, tmp_path, evidence=_write_evidence(tmp_path / "e3", no_rental=silent)), SPECS)


# --------------------------------------------------------------------------- recorded products and the QA rows


def test_the_recorded_facilities_have_their_cheapest_current_product_with_the_dearer_ones_named(bsm, inputs):
    rows = {row["record_id"]: row for row in bsm.read_recorded_products(inputs, RECORDED)}
    eves = rows["monthly_bs_eves"]
    assert (eves["decision"], eves["reason"], eves["amount_eur"]) == ("not_used", "not_a_dataset_option", 80.0)
    assert "other products not taken: Saisonparken Monat 170.00 EUR" in eves["note"]
    assert rows["monthly_bs_p1"]["reason"] == "station_bahnpark" and rows["monthly_bs_p1"]["amount_eur"] == 120.0
    assert "other products not taken: 1 Monat fest 160.00 EUR" in rows["monthly_bs_p1"]["note"]


@pytest.mark.parametrize("edit, message", [
    ({"facility_type": "garage"}, "calls F_P1 'surface_parking'"),
    ({"facility_key": "schloss"}, "a wrong facility id is never read"),
], ids=["other_kind", "other_key"])
def test_a_recorded_facility_of_another_kind_or_key_is_refused(bsm, inputs, edit, message):
    with pytest.raises(SystemExit, match=message):
        bsm.read_recorded_products(inputs, ({**RECORDED[1], **edit},))


def test_a_recorded_facility_without_a_product_or_with_two_equal_cheapest_products_is_refused(bsm, tmp_path):
    content = _package_content()
    content["rules"] = [rule for rule in content["rules"] if rule["facility_id"] != "F_EVES"]
    with pytest.raises(SystemExit, match="holds no publicly purchasable monthly product"):
        bsm.read_recorded_products(_load(bsm, tmp_path / "a", content), RECORDED[:1])
    content = _package_content()
    for record in content["rules"]:
        if record["offer_id"] == "E_2":
            record["monthly_amount_eur"] = 80.0
    with pytest.raises(SystemExit, match="the cheapest is ambiguous"):
        bsm.read_recorded_products(_load(bsm, tmp_path / "b", content), RECORDED[:1])


def test_the_qa_rows_carry_every_product_with_the_name_of_the_garage_and_validate_ascii_only(bsm, inputs):
    garages = bsm.read_garage_products(inputs, SPECS)
    recorded = bsm.read_recorded_products(inputs, RECORDED)
    names = {garage: f"Parkhaus {garage}" for garage in garages}
    rows = bsm.qa_product_rows(garages, recorded, names)
    assert len(rows) == sum(len(entry["products"]) for entry in garages.values()) + len(recorded) == 3 + 1 + 1 + 1 + 2 + 2 + 2
    assert {row["record_type"] for row in rows} == {"monthly_product"} and {row["municipality_ags"] for row in rows} == {BS}
    assert all(set(row) <= set(pq.GARAGE_QA_COLUMNS) for row in rows)
    assert len({row["record_id"] for row in rows}) == len(rows)
    by_id = {row["record_id"]: row for row in rows}
    assert by_id["monthly_bs_stein_a"]["subject"] == "Parkhaus bs_stein: Tarif A - Tagesparker"
    assert by_id["monthly_bs_stein_a"]["amount_eur"] == "100.00" and by_id["monthly_bs_stein_a"]["garage_id"] == "bs_stein"
    assert by_id["monthly_bs_magni_sold_out"]["amount_eur"] is None
    assert by_id["monthly_bs_eves"]["garage_id"] is None and by_id["monthly_bs_eves"]["subject"].endswith("Dauerparken 24/7")
    assert all(str(row[column]).isascii() for row in rows for column in row if row[column] is not None)
    table = pd.DataFrame([{column: "" if row.get(column) is None else str(row.get(column)) for column in pq.GARAGE_QA_COLUMNS}
                          for row in rows])
    assert set(table["reason_code"]) - {""} <= set(pq.MONTHLY_NOT_USED_REASONS)


# --------------------------------------------------------------------------- the dataset and ASSUMPTION P13


def _frame(*rows) -> gpd.GeoDataFrame:
    """A small dataset frame (the test_parking_garages helpers, loaded from that file)."""
    spec = importlib.util.spec_from_file_location("garages_tests_for_monthly", Path(__file__).with_name("test_parking_garages.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._frame(*[module._row(**row) if "_free" not in row else module._free(**{
        key: value for key, value in row.items() if key != "_free"}) for row in rows])


def _qa_rows(*products) -> list:
    rows = []
    for record_id, decision, reason, amount, garage_id, ags in products:
        rows.append({"record_id": record_id, "record_type": "monthly_product", "municipality_ags": ags, "subject": record_id,
                     "garage_id": garage_id, "decision": decision, "reason_code": reason, "amount_eur": amount, "count": 1,
                     "evidence": "package", "note": "a product"})
    return rows


PUBLISHED = {"monthly_source_url": "https://example.org/m", "monthly_product": "Dauerparken"}


def _municipal_dataset():
    """Braunschweig: two published garages, three garages without a product (one unpriced); Wolfsburg: one published garage and
    one without (one value: nothing is imputed), a surface lot; Peine: two published garages and nothing to impute."""
    frame = _frame(
        {"garage_id": "bs_a", "monthly_eur": 100.0, **PUBLISHED},
        {"garage_id": "bs_b", "monthly_eur": 114.95, **PUBLISHED},
        {"garage_id": "bs_c", "assumptions": "P4", "notes": "ASSUMPTION P4."},
        {"garage_id": "bs_d", "assumptions": "P5", "notes": "ASSUMPTION P5."},
        {"garage_id": "wob_a", "municipality": "Wolfsburg", "municipality_ags": "03103000", "monthly_eur": 60.0, **PUBLISHED},
        {"garage_id": "wob_b", "municipality": "Wolfsburg", "municipality_ags": "03103000"},
        {"garage_id": "pe_a", "municipality": "Peine", "municipality_ags": "03157006", "monthly_eur": 48.0, **PUBLISHED},
        {"garage_id": "pe_b", "municipality": "Peine", "municipality_ags": "03157006", "monthly_eur": 48.0, **PUBLISHED})
    lot = _frame({"garage_id": "wob_lot", "_free": True})
    data = pd.concat([frame, lot], ignore_index=True)
    return gpd.GeoDataFrame(data, geometry="geometry", crs="EPSG:25832")


def _municipal_rows() -> list:
    return _qa_rows(("monthly_bs_a", "used", "", "100.00", "bs_a", BS), ("monthly_bs_b", "used", "", "114.95", "bs_b", BS),
                    ("monthly_bs_eves", "not_used", "not_a_dataset_option", "80.00", "", BS),
                    ("monthly_bs_fichtengrund", "not_used", "not_a_dataset_option", "129.00", "", BS),
                    ("monthly_bs_station", "not_used", "station_bahnpark", "74.00", "", BS),
                    ("monthly_bs_s1", "not_used", "surface_lot", "99.00", "", BS),
                    ("monthly_wob_a", "used", "", "60.00", "wob_a", "03103000"),
                    ("monthly_pe_a", "used", "", "48.00", "pe_a", "03157006"),
                    ("monthly_pe_b", "used", "", "48.00", "pe_b", "03157006"))


def test_the_imputation_gives_the_garages_without_a_product_the_median_of_their_own_municipality(step):
    frame = step.apply_monthly_imputation(_municipal_dataset(), _municipal_rows())
    by_id = frame.set_index("garage_id")
    # Braunschweig: 80.00, 100.00, 114.95, 129.00 -> 107.475 -> 107.48 (half up)
    for garage in ("bs_c", "bs_d"):
        assert by_id.loc[garage, "monthly_imputed_eur"] == 107.48
        assert by_id.loc[garage, "assumptions"].split(";")[-1] == "P13"
        assert "ASSUMPTION P13" in by_id.loc[garage, "notes"] and "107.48 EUR" in by_id.loc[garage, "notes"]
        assert "monthly_bs_eves" in by_id.loc[garage, "notes"] and "monthly_bs_station" not in by_id.loc[garage, "notes"]
    assert by_id.loc["bs_c", "assumptions"] == "P4;P13" and by_id.loc["bs_d", "assumptions"] == "P5;P13"
    # the original frame is not changed in place; a published product is never replaced or accompanied
    assert by_id.loc[["bs_a", "bs_b"], "monthly_imputed_eur"].isna().all()
    assert by_id.loc["bs_a", "monthly_eur"] == 100.0
    # one published product is too few (Wolfsburg), a municipality whose garages all publish has nothing to impute (Peine), a
    # surface lot never gets a value, and no value crosses a municipality boundary
    assert by_id.loc[["wob_a", "wob_b", "pe_a", "pe_b", "wob_lot"], "monthly_imputed_eur"].isna().all()
    assert "P13" not in "".join(by_id.loc[["wob_b", "wob_lot", "pe_a"], "assumptions"].fillna(""))
    pg.validate_garages(frame)
    pq.validate_garage_qa(_qa_table(frame, _municipal_rows()), frame)


def _qa_table(frame, rows) -> pd.DataFrame:
    garage_rows = [{"record_id": f"garage_{row['garage_id']}", "record_type": "garage", "municipality_ags": row["municipality_ags"],
                    "subject": row["name"], "garage_id": row["garage_id"], "decision": "priced", "reason_code": "", "count": 1,
                    "evidence": "r", "note": "priced"} for _, row in frame.iterrows()]
    return pd.DataFrame([{column: "" if row.get(column) is None else str(row.get(column)) for column in pq.GARAGE_QA_COLUMNS}
                         for row in garage_rows + _municipal_rows()])


def test_the_imputation_is_the_same_value_the_qa_validator_expects_and_is_stable_under_a_second_run(step):
    first = step.apply_monthly_imputation(_municipal_dataset(), _municipal_rows())
    second = step.apply_monthly_imputation(_municipal_dataset(), _municipal_rows())
    pd.testing.assert_frame_equal(pd.DataFrame(first.drop(columns="geometry")), pd.DataFrame(second.drop(columns="geometry")))
    assert pq.expected_imputed_monthly(_qa_table(first, _municipal_rows()), first) == {BS: 107.48, "03157006": 48.0}


def test_a_municipality_with_one_published_product_imputes_nothing_and_says_so(step, capsys):
    step.apply_monthly_imputation(_municipal_dataset(), _municipal_rows())
    out = capsys.readouterr().out
    assert "P13 Braunschweig (03101000): median 107.48 EUR of 4 published current garage monthly products" in out
    assert "P13 Wolfsburg (03103000): 1 published current garage monthly product(s), fewer than 2: nothing imputed" in out
    assert "monthly products: Braunschweig (03101000) published 2, imputed 2, P13 median 107.48 EUR, none 0" in out
    assert "Wolfsburg (03103000) published 1, imputed 0, none 1" in out and "surface lots 1, never imputed" in out


def test_an_unpriced_garage_gets_no_imputed_product_and_is_named(step, capsys):
    frame = _municipal_dataset()
    frame.loc[frame["garage_id"] == "bs_d", ["priced", "not_priced_reason", "assumptions", "garage_hourly_rate_eur",
                                           "garage_billing_unit_min", "garage_first_period_min", "garage_first_period_eur",
                                           "garage_daily_cap_eur", "garage_fee_start_h", "garage_fee_end_h"]] = [
        False, "incomplete_tariff", None, None, None, None, None, None, None, None]
    result = step.apply_monthly_imputation(frame, _municipal_rows())
    assert pd.isna(result.set_index("garage_id").loc["bs_d", "monthly_imputed_eur"])
    assert "1 unpriced garage(s) ['bs_d'] get no imputed product" in capsys.readouterr().out


def test_the_p13_rate_is_printed_after_the_assumption_rates_of_the_dataset(step, capsys):
    step.apply_monthly_imputation(_municipal_dataset(), _municipal_rows())
    assert "priced garages resting on ASSUMPTION P13: 2/9 (22.2 %)" in capsys.readouterr().out


# --------------------------------------------------------------------------- the step as a whole (synthetic regional package)


@pytest.fixture(scope="module")
def regional_tests():
    spec = importlib.util.spec_from_file_location("regional_garages_tests_for_monthly",
                                                  Path(__file__).with_name("test_parking_regional_garages.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BS_A_SPECS = (
    {"garage_id": "bs_a", "facility": "BS_A", "facility_key": "garage_a", "status": "published", "rule": "A_1"},
    {"garage_id": "bs_b", "facility": "BS_B", "facility_key": "garage_b", "status": "no_price",
     "quotes": (("Garage B", "kein belastbarer aktueller Monatspreis ermittelt"),)},
    {"garage_id": "bs_t", "facility": "BS_T", "facility_key": "garage_t", "status": "sold_out",
     "quotes": (("Garage T", "Dauerstellplätze laut Betreiber belegt"),)},
)


def _step_package(tmp_path, regional_tests, facility_a="BS_A"):
    readme = ("# Test\n\nGarage B: kein belastbarer aktueller Monatspreis ermittelt. "
              "Garage T: Dauerstellplätze laut Betreiber belegt.\n")
    content = {"rules": [_rule("A_1", facility_a, "A", 55.0), _rule("A_2", facility_a, "B", 70.0)],
               "offers": [], "sources": [{"id": "SRC_TARIFF", "url": "https://op.example/tarife"}],
               "visibility": {"rejected_price_eur": 85, "reason": "hidden"}, "readme": readme,
               "facilities": [_facility(facility_a, "garage_a", review="monthly_price_confirmed", offers=("A_1", "A_2")),
                              _facility("BS_B", "garage_b"),
                              _facility("BS_T", "garage_t", contract="sold_out")]}
    content["offers"] = [_offer(rule["offer_id"], rule["facility_id"], "published_monthly_price", rule["amount_eur"])
                         | {"monthly_amount_eur": rule["monthly_amount_eur"]} for rule in content["rules"]]
    path, sha256 = _write_package(tmp_path / "bs_package", content)
    directory, sums = _write_evidence(tmp_path / "bs_evidence")
    return path, directory, sha256, sums


@pytest.fixture()
def step_with_specs(step, regional_tests, monkeypatch):
    """The step with the synthetic regional garages of the regional test file and the three monthly specifications above."""
    monkeypatch.setattr(step.specs, "GARAGE_SPECS", regional_tests._specs())
    monkeypatch.setattr(step.specs, "MONTHLY_PRODUCTS", ())
    monkeypatch.setattr(step.specs, "PACKAGE_CANDIDATES", regional_tests.CANDIDATES)
    monkeypatch.setattr(step.specs, "DIRECTORY_DECISIONS", regional_tests.DIRECTORY)
    monkeypatch.setattr(step.specs, "LOT_SPECS", ())
    monkeypatch.setattr(step.specs, "BS_MONTHLY_SPECS", BS_A_SPECS)
    monkeypatch.setattr(step.specs, "BS_RECORDED_SPECS", ())
    return step


def _inputs(step, regional_tests, tmp_path, facility_a="BS_A"):
    directory = tmp_path / "regional"
    sha256 = regional_tests._write_package(directory, regional_tests._layers(), regional_tests._rules(),
                                           regional_tests._facilities())
    path, evidence, package_sha256, sums = _step_package(tmp_path, regional_tests, facility_a)
    return step.load_garage_inputs(directory, expected_sha256=sha256, bs_monthly_path=path, evidence_directory=evidence,
                                   expected_bs_monthly_sha256=package_sha256, expected_evidence_sums_sha256=sums)


def test_the_monthly_status_reaches_the_dataset_rows_the_notes_the_hashes_and_the_qa_table(step_with_specs, regional_tests,
                                                                                         tmp_path, monkeypatch):
    step = step_with_specs
    # the regional package also holds a monthly product of bs_a (a-m1): the dataset takes exactly one, so the two sources conflict
    inputs = _inputs(step, regional_tests, tmp_path)
    monkeypatch.setattr(step.specs, "MONTHLY_PRODUCTS", ())
    frame = step.build_garages(inputs)
    by_id = frame.set_index("garage_id")
    assert by_id.loc["bs_a", "monthly_eur"] == 55.0 and by_id.loc["bs_a", "monthly_source_url"] == "https://op.example/tarife"
    assert pd.isna(by_id.loc["bs_b", "monthly_eur"]) and pd.isna(by_id.loc["bs_t", "monthly_eur"])
    # a Braunschweig row cites the monthly package as its last hash; the other rows do not
    assert by_id.loc["bs_a", "package_sha256"].split(";") == [inputs["file"]["sha256"], inputs["bs_monthly"]["file"]["sha256"]]
    assert by_id.loc["bs_b", "package_sha256"].endswith(inputs["bs_monthly"]["file"]["sha256"])
    assert by_id.loc["wob_x", "package_sha256"] == inputs["file"]["sha256"]
    assert "Monthly product (spec Amendment F1, package Braunschweig_Monatstarife_2026-10-08.zip): published" in (
        by_id.loc["bs_a", "notes"])
    assert "sold_out, no monthly price is published (Garage T: Dauerstellplaetze laut Betreiber belegt." in (
        by_id.loc["bs_t", "notes"])
    rows = step.qa_rows(inputs, frame, [])
    table = pd.DataFrame(rows)
    products = table[table["record_type"] == "monthly_product"].set_index("record_id")
    assert products.loc["monthly_bs_a_a", "decision"] == "used" and products.loc["monthly_bs_a_a", "amount_eur"] == "55.00"
    assert products.loc["monthly_bs_a_b", "reason_code"] == "not_the_cheapest"
    assert products.loc["monthly_bs_b_no_price", "garage_id"] == "bs_b"
    assert products.loc["monthly_bs_t_sold_out", "reason_code"] == "sold_out"
    pq.validate_garage_qa(table, frame)


def test_a_dataset_product_of_the_regional_package_and_one_of_the_monthly_package_conflict(step_with_specs, regional_tests,
                                                                                         tmp_path, monkeypatch):
    step = step_with_specs
    monkeypatch.setattr(step.specs, "MONTHLY_PRODUCTS", regional_tests.MONTHLY)
    inputs = _inputs(step, regional_tests, tmp_path)
    with pytest.raises(SystemExit, match="a monthly product from the regional package and one from the Braunschweig monthly "
                                        "package"):
        step.build_garages(inputs)


def test_a_monthly_status_is_matched_to_the_garage_by_facility_id_never_by_name(step_with_specs, regional_tests, tmp_path,
                                                                                monkeypatch):
    step = step_with_specs
    # the specification names BS_A for the regional garage, the monthly package's record has another id (and no upstream id)
    monkeypatch.setattr(step.specs, "BS_MONTHLY_SPECS", ({**BS_A_SPECS[0], "facility": "BS_OTHER"}, *BS_A_SPECS[1:]))
    inputs = _inputs(step, regional_tests, tmp_path, facility_a="BS_OTHER")
    with pytest.raises(SystemExit, match="the monthly package facility BS_OTHER .* is not the facility BS_A"):
        step.build_garages(inputs)


def test_every_braunschweig_garage_needs_exactly_one_monthly_status(step_with_specs, regional_tests, tmp_path, monkeypatch):
    step = step_with_specs
    inputs = _inputs(step, regional_tests, tmp_path)
    monkeypatch.setattr(step.specs, "BS_MONTHLY_SPECS", BS_A_SPECS[:2])
    with pytest.raises(SystemExit, match="every Braunschweig garage has exactly one monthly status"):
        step.build_garages(inputs)


def test_monthly_specifications_without_the_package_stop_the_step_and_the_two_inputs_belong_together(
        step_with_specs, regional_tests, tmp_path):
    step = step_with_specs
    directory = tmp_path / "regional"
    sha256 = regional_tests._write_package(directory, regional_tests._layers(), regional_tests._rules(),
                                           regional_tests._facilities())
    inputs = step.load_garage_inputs(directory, expected_sha256=sha256)
    with pytest.raises(SystemExit, match="pass it as --bs-monthly-zip"):
        step.build_garages(inputs)
    path, evidence, _, _ = _step_package(tmp_path, regional_tests)
    with pytest.raises(SystemExit, match="belong together: pass both or neither"):
        step.load_garage_inputs(directory, expected_sha256=sha256, bs_monthly_path=path)
    with pytest.raises(SystemExit, match="belong together: pass both or neither"):
        step.load_garage_inputs(directory, expected_sha256=sha256, evidence_directory=evidence)


def test_two_runs_of_the_whole_step_with_the_imputation_write_identical_bytes(step_with_specs, regional_tests, tmp_path):
    step = step_with_specs
    written = []
    shared = _inputs(step, regional_tests, tmp_path)   # one package file: a zip written twice differs in its timestamps
    for run in ("first", "second"):
        (tmp_path / run).mkdir()
        inputs = shared
        frame = step.build_garages(inputs)
        rows = step.qa_rows(inputs, frame, [])
        frame = step.apply_monthly_imputation(frame, rows)
        pg.validate_garages(frame)
        pg.write_garages(frame, tmp_path / run / "garages.geojson", members=step.dataset_members())
        step.write_garage_qa(tmp_path / run / "qa.csv", rows)
        written.append((tmp_path / run / "garages.geojson").read_bytes() + (tmp_path / run / "qa.csv").read_bytes())
    assert written[0] == written[1]


# --------------------------------------------------------------------------- the real package (where it exists)


def _real_inputs(step):
    raw = COMMITTED_PARKING_DIR / "raw_sources" / "municipal_2026-10-08"
    package, evidence = raw / step.bsm.PACKAGE_FILE, raw / step.bsm.EVIDENCE_DIRECTORY_NAME
    if not (package.is_file() and evidence.is_dir()):
        pytest.skip("the owner's Braunschweig monthly package and the Contipark evidence are gitignored and absent here")
    return step.bsm.load_monthly_inputs(package, evidence)


def test_the_real_package_gives_the_published_values_of_spec_f1(step):
    found = step.bsm.read_garage_products(_real_inputs(step), step.specs.BS_MONTHLY_SPECS)
    published = {garage: entry["monthly"]["monthly_eur"] for garage, entry in found.items() if entry["monthly"]}
    assert published == {"bs_steinstrasse": 100.0, "bs_wallstrasse": 114.95}
    assert {garage: entry["status"] for garage, entry in found.items() if not entry["monthly"]} == {
        "bs_eiermarkt": "no_price", "bs_forschungsflughafen": "price_on_request", "bs_lange_strasse_nord": "no_price",
        "bs_lange_strasse_sued": "no_price", "bs_magni": "sold_out", "bs_packhof": "sold_out", "bs_ring_center": "no_price",
        "bs_schloss": "no_price", "bs_schuetzenstrasse": "no_price", "bs_wilhelmstrasse": "period_unconfirmed"}
    steinstrasse = {product["record_id"]: product["amount_eur"] for product in found["bs_steinstrasse"]["products"]}
    assert steinstrasse == {"monthly_bs_steinstrasse_a": 100.0, "monthly_bs_steinstrasse_b": 105.01,
                            "monthly_bs_steinstrasse_c": 130.0, "monthly_bs_steinstrasse_d": 110.0,
                            "monthly_bs_steinstrasse_s": 150.0}
    wilhelm = sorted(product["amount_eur"] for product in found["bs_wilhelmstrasse"]["products"])
    assert wilhelm == [75.0, 79.0, 88.0]
    hidden = [product["amount_eur"] for product in found["bs_forschungsflughafen"]["products"] if product["amount_eur"]]
    assert hidden == [85.0]


def test_the_real_package_gives_the_recorded_products_of_spec_f2(step):
    rows = {row["record_id"]: row for row in step.bsm.read_recorded_products(_real_inputs(step), step.specs.BS_RECORDED_SPECS)}
    assert {key: (row["reason"], row["amount_eur"]) for key, row in rows.items()} == {
        "monthly_bs_eves": ("not_a_dataset_option", 80.0), "monthly_bs_fichtengrund": ("not_a_dataset_option", 129.0),
        "monthly_bs_hbf_nord_p1": ("station_bahnpark", 120.0), "monthly_bs_hbf_sued_p2": ("station_bahnpark", 74.0),
        "monthly_bs_hbf_west_p3": ("station_bahnpark", 74.0), "monthly_bs_apcoa_s1": ("surface_lot", 99.0),
        "monthly_bs_apcoa_s3": ("surface_lot", 129.0)}
