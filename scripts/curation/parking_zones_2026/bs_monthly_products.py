"""Monthly products of the Braunschweig garages (parking cost zones v2, spec Amendment F1, issue #436, Task 4f).

Not part of the pipeline and never run by synpp: two further optional inputs of ``regional_garages.py``
(``--bs-monthly-zip`` and ``--contipark-evidence-dir``). The owner-supplied ``Braunschweig_Monatstarife_2026-10-08.zip`` is a
research package on the monthly and long-term products of 14 garages and 5 surface lots of Braunschweig; the directory
``contipark_configurator_2026-10-08`` holds two evidence captures that the controller made of the public Contipark pages on the
owner's instruction (the product configurator of the Parkhaus Wallstrasse and the location page of the Tiefgarage Eiermarkt).
The step reads them the way the other packages are read (``garage_supplement``, ``wolfsburg_lots``):

* ``load_monthly_inputs`` checks the zip against the SHA-256 pinned in ``PACKAGE_SHA256`` before anything is read, every member
  that is read against the zip's own ``manifest.sha256``, the evidence directory against its ``SHA256SUMS`` (itself pinned in
  ``EVIDENCE_SUMS_SHA256``; every file of the directory must be listed and match, and nothing else may be in it). Nothing is
  extracted and no script of the package is ever executed.
* ``read_garage_products`` reads, for every Braunschweig garage of the dataset, its monthly status. No amount is typed: a
  published product is read from the package's rules (the cheapest publicly purchasable current product, rule D2: the
  specification names WHICH rule to read and the step checks that it is the cheapest of its facility) or, for the Parkhaus
  Wallstrasse, from the "weitere Monate ... Brutto" line of the configurator text (the step stops when it cannot read exactly
  one amount whose net and VAT add up to it). A garage without a published product gets its status with the package's own wording
  (``sold_out``, ``no_price``, ``price_on_request``, ``period_unconfirmed``): the specification names the sentence of the
  package README that states it, and the step checks the status against the package's records (no rule for the facility, the
  review status and contract status of its record). Amounts that the package records and that the model does not use (the
  Wilhelmstrasse 75/79/88 EUR whose billing period is unconfirmed, the 85 EUR in hidden sections of the Forschungsflughafen page)
  are rows that are recorded and not used.
* ``read_recorded_products`` reads the products of the facilities that are no garage of the dataset: the garages Eves and
  Fichtengrund (their current published product is the value of a municipality for ASSUMPTION P13), the three station car parks
  of DB BahnPark and the two APCOA surface lots (recorded, never garage products).
* ``qa_product_rows`` writes every product as a row of the QA table (``braunschweig.parking.garage_qa``).

CRS: not applicable (no geometry is read); money in EUR.
"""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Optional

import garage_supplement as sup
import municipal_zones as mz
import regional_zones as rz

PACKAGE_NAME = "Braunschweig_Monatstarife_2026-10-08"
PACKAGE_FILE = f"{PACKAGE_NAME}.zip"
#: SHA-256 of the owner's package (also in MANIFEST.md of the folder and in the data record parking_garages_2026).
PACKAGE_SHA256 = "72fec30d4515a3f7468af66222802697b09ae042b283adc335b5ae3607650c81"
RULES_MEMBER = "data/tariff_rules.json"
OBSERVATIONS_MEMBER = "data/tariff_observations.json"
FACILITIES_MEMBER = "data/facilities.json"
SOURCES_MEMBER = "data/sources.json"
README_MEMBER = "README.md"
VISIBILITY_MEMBER = "evidence/apcoa/visibility_evidence.json"
MEMBERS = (README_MEMBER, RULES_MEMBER, OBSERVATIONS_MEMBER, FACILITIES_MEMBER, SOURCES_MEMBER, VISIBILITY_MEMBER)

#: The evidence directory of the Contipark captures: the file that lists the SHA-256 of every file, its own SHA-256 (the
#: pin) and the two evidence files.
EVIDENCE_DIRECTORY_NAME = "contipark_configurator_2026-10-08"
EVIDENCE_SUMS_FILE = "SHA256SUMS"
EVIDENCE_SUMS_SHA256 = "2617d497bddfaadced2ff433cd4b675122570a58ca7b6b2b49c27b0514a76ceb"
CONFIGURATOR_FILE = "wallstrasse_configurator.txt"
EIERMARKT_FILE = "eiermarkt_no_rental_product.txt"

#: The statuses of a garage without a published product and the checks of the package's records each one needs.
STATUSES = ("published", "sold_out", "no_price", "price_on_request", "period_unconfirmed")
#: The review status of a facility record whose monthly price the package does not know, and of one whose amount has no
#: confirmed billing period.
REVIEW_UNKNOWN = "monthly_price_unknown"
REVIEW_PERIOD_UNCONFIRMED = "billing_period_unconfirmed"
EVIDENCE_PUBLISHED = "published_monthly_price"
EVIDENCE_PERIOD_UNCONFIRMED = "billing_period_unconfirmed"
CONTRACT_SOLD_OUT = "sold_out"
BRAUNSCHWEIG_AGS = "03101000"
#: The rule that the cheapest published product is chosen by: publicly purchasable (model usable), a fixed monthly amount.
RULE_BILLING_PERIOD = "month"

_CENT = Decimal("0.01")
_SENTENCE_BOUNDARY = re.compile(r"(?<=\.)\s+(?=[A-ZÄÖÜ0-9])")
_GERMAN_AMOUNT = r"(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2})"
_WEITERE_MONATE = re.compile(
    r"^weitere Monate:.*?\|\s*Netto\s+" + _GERMAN_AMOUNT + r"\s*€\s*\|\s*MwSt\.\s+" + _GERMAN_AMOUNT
    + r"\s*€\s*\|\s*Brutto\s+" + _GERMAN_AMOUNT + r"\s*€\s*$", re.MULTILINE)
_HEADER = re.compile(r"^#\s*(\w+):\s*(.*)$", re.MULTILINE)


def _ascii(text) -> str:
    return rz._ascii(text)


def _eur_text(amount: float) -> str:
    return f"{float(amount):.2f}"


def _german_decimal(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def _normalised(text: str) -> str:
    """The letters and digits of ``text`` in lower case (the comparison of two product names that differ in dashes)."""
    return re.sub(r"[^0-9a-zäöüß]", "", text.lower())


def _whole_cents(value, where: str) -> float:
    """``value`` as a positive amount in whole cents (a float with at most two decimals), else ``SystemExit``."""
    amount = Decimal(str(value)).quantize(_CENT)
    if amount <= 0 or amount != Decimal(str(value)):
        raise SystemExit(f"{where}: {value!r} is no positive amount in whole cents")
    return float(amount)


# ---------------------------------------------------------------- the inputs
def _verify_evidence_directory(directory, expected_sums_sha256: Optional[str]) -> dict:
    """{file name: text} of the evidence directory, checked against its ``SHA256SUMS`` (itself pinned): the sums file must have
    the pinned SHA-256, every line ``<sha256> *<name>`` must match its file, and the directory must hold exactly the listed
    files plus the sums file."""
    directory = Path(directory)
    sums_path = directory / EVIDENCE_SUMS_FILE
    if not sums_path.is_file():
        raise SystemExit(f"{sums_path} missing: pass the evidence directory {EVIDENCE_DIRECTORY_NAME} (--contipark-evidence-dir) "
                         "unchanged")
    pinned = expected_sums_sha256 or EVIDENCE_SUMS_SHA256
    actual = mz.file_sha256(sums_path)
    if actual != pinned:
        raise SystemExit(f"{sums_path}: SHA-256 {actual} is not the recorded {pinned} (data record parking_garages_2026); a "
                         "changed evidence directory is never read")
    listed = {}
    for line in sums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, separator, name = line.strip().partition(" *")
        if not separator or not re.fullmatch(r"[0-9a-f]{64}", digest) or not name:
            raise SystemExit(f"{sums_path}: the line {line!r} is not '<sha256> *<file name>'")
        listed[name] = digest
    present = {path.name for path in directory.iterdir() if path.is_file()} - {EVIDENCE_SUMS_FILE}
    if present != set(listed):
        raise SystemExit(f"{directory}: the files {sorted(present)} differ from the files {sorted(listed)} that "
                         f"{EVIDENCE_SUMS_FILE} lists; a file that the sums do not vouch for is never read")
    texts = {}
    for name, digest in sorted(listed.items()):
        content = (directory / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise SystemExit(f"{directory / name}: its SHA-256 differs from the entry of {EVIDENCE_SUMS_FILE}; a changed "
                             "evidence file is never read")
        texts[name] = content.decode("utf-8")
    return texts


def load_monthly_inputs(package_path, evidence_directory, expected_package_sha256: Optional[str] = None,
                        expected_evidence_sums_sha256: Optional[str] = None) -> dict:
    """The verified Braunschweig monthly package at ``package_path`` (the zip itself) and its evidence directory.

    The zip must exist with exactly ``PACKAGE_SHA256`` (or ``expected_package_sha256``, for a synthetic test package), else
    ``SystemExit``; every member read must match its entry of ``manifest.sha256``; the evidence directory is verified by
    ``_verify_evidence_directory``. Returns {"path", "file" ({"file", "sha256", "bytes"}), "evidence_sums_sha256",
    "facilities" (facility_id -> record), "rules" (offer_id -> rule of ``tariff_rules.json``), "offers" (offer_id -> offer of
    ``tariff_observations.json``, the rules included), "sources" (source id -> record), "readme" (text), "visibility" (the
    package's visibility evidence of the hidden Forschungsflughafen price), "evidence" (file name -> text)}."""
    path = Path(package_path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: pass the owner's package {PACKAGE_FILE} (--bs-monthly-zip) unchanged")
    sha256 = expected_package_sha256 or PACKAGE_SHA256
    actual = mz.file_sha256(path)
    if actual != sha256:
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {sha256} (data record parking_garages_2026); a changed "
                         "package is never read")
    with zipfile.ZipFile(path) as archive:
        manifest = sup._manifest(archive, PACKAGE_NAME)

        def member(name):
            return sup._read_member(archive, manifest, name, PACKAGE_NAME)

        rules = json.loads(member(RULES_MEMBER).decode("utf-8"))["rules"]
        observations = json.loads(member(OBSERVATIONS_MEMBER).decode("utf-8"))["offers"]
        facilities = json.loads(member(FACILITIES_MEMBER).decode("utf-8"))
        sources = json.loads(member(SOURCES_MEMBER).decode("utf-8"))
        readme = member(README_MEMBER).decode("utf-8")
        visibility = json.loads(member(VISIBILITY_MEMBER).decode("utf-8"))
    by_id = {}
    for label, records, key in (("facilities.json", facilities, "facility_id"), ("sources.json", sources, "id")):
        by_id[label] = {}
        for record in records:
            if record[key] in by_id[label]:
                raise SystemExit(f"{label} holds the id {record[key]} twice")
            by_id[label][record[key]] = record
    rule_ids = {rule["offer_id"]: rule for rule in rules}
    offer_ids = {offer["offer_id"]: offer for offer in observations}
    if len(rule_ids) != len(rules) or len(offer_ids) != len(observations):
        raise SystemExit("tariff_rules.json or tariff_observations.json holds an offer id twice")
    missing = sorted(set(rule_ids) - set(offer_ids))
    if missing:
        raise SystemExit(f"the rules {missing} are no offers of tariff_observations.json; the package is inconsistent")
    inputs = {"path": path, "file": {"file": path.name, "sha256": actual, "bytes": path.stat().st_size},
              "facilities": by_id["facilities.json"], "rules": rule_ids, "offers": offer_ids,
              "sources": by_id["sources.json"], "readme": readme, "visibility": visibility,
              "evidence_sums_sha256": expected_evidence_sums_sha256 or EVIDENCE_SUMS_SHA256,
              "evidence": _verify_evidence_directory(evidence_directory, expected_evidence_sums_sha256)}
    print(f"Braunschweig monthly inputs verified: {path.name} ({inputs['file']['bytes']} bytes, SHA-256 {actual}); "
          f"{len(inputs['facilities'])} facility records, {len(rule_ids)} monthly rules, {len(offer_ids)} observed offers; "
          f"evidence directory {Path(evidence_directory).name} ({len(inputs['evidence'])} files, {EVIDENCE_SUMS_FILE} SHA-256 "
          f"{inputs['evidence_sums_sha256']})")
    return inputs


# ---------------------------------------------------------------- reading
def readme_sentence(readme: str, token: str, phrase: str) -> str:
    """The one sentence of the package README that contains both ``token`` (the facility's name) and ``phrase``; ``SystemExit``
    when there is none or several (the specification names a sentence that states the status)."""
    sentences = [sentence.strip() for line in readme.splitlines() for sentence in _SENTENCE_BOUNDARY.split(line)
                 if sentence.strip()]
    hits = [sentence for sentence in sentences if token in sentence and phrase in sentence]
    if len(hits) != 1:
        raise SystemExit(f"the package README holds {len(hits)} sentence(s) with {token!r} and {phrase!r}; a status needs "
                         "exactly one sentence of the package that states it")
    return hits[0]


def read_configurator(text: str, garage_id: str) -> dict:
    """The monthly product of the configurator capture (``wallstrasse_configurator.txt``): the gross amount of 'weitere Monate'
    (the first month is pro rata, so it is no monthly price), its net and VAT amounts (which must add up to the gross amount), the
    product line, the minimum contract, the page URL and the retrieval time. ``SystemExit`` when the text is not the capture of
    the garage ``garage_id`` (its ``# facility:`` line must name it), holds no or several 'weitere Monate' lines, or the
    amounts do not add up."""
    header = {key: value.strip() for key, value in _HEADER.findall(text)}
    for key in ("url", "retrieved_at_utc", "facility"):
        if key not in header:
            raise SystemExit(f"{CONFIGURATOR_FILE}: no '# {key}:' header line")
    if f"dataset garage {garage_id}" not in header["facility"]:
        raise SystemExit(f"{CONFIGURATOR_FILE}: the capture is of {header['facility']!r}, not of the dataset garage {garage_id}")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
    hits = [match for match in _WEITERE_MONATE.finditer(text)]
    if len(hits) != 1:
        raise SystemExit(f"{CONFIGURATOR_FILE}: {len(hits)} line(s) 'weitere Monate: ... Netto ... MwSt. ... Brutto ...' "
                         "found; the "
                         "step reads exactly one gross amount of the further months and stops when it cannot")
    net, vat, gross = (_german_decimal(group) for group in hits[0].groups())
    if net + vat != gross:
        raise SystemExit(f"{CONFIGURATOR_FILE}: net {net} + VAT {vat} is not the gross amount {gross}")
    term = next((line for line in lines if line.startswith("Mindestvertragslaufzeit:")), None)
    return {"amount_eur": _whole_cents(gross, f"{CONFIGURATOR_FILE} gross amount"), "net_eur": float(net), "vat_eur": float(vat),
            "product": lines[0], "minimum_term": term, "url": header["url"], "retrieved_at": header["retrieved_at_utc"]}


def read_eiermarkt_finding(text: str, garage_id: str) -> str:
    """The finding line of the Eiermarkt capture (no rental product on the location page); ``SystemExit`` when the capture is
    of another garage or does not state that no rental product is offered."""
    header = {key: value.strip() for key, value in _HEADER.findall(text)}
    if f"dataset garage {garage_id}" not in header.get("facility", ""):
        raise SystemExit(f"{EIERMARKT_FILE}: the capture is of {header.get('facility')!r}, not of the dataset garage {garage_id}")
    finding = header.get("finding", "")
    if "no rental" not in finding:
        raise SystemExit(f"{EIERMARKT_FILE}: the finding {finding!r} does not state that no rental product is offered")
    return finding


def _facility(inputs: dict, spec: dict) -> dict:
    record = inputs["facilities"].get(spec["facility"])
    if record is None:
        raise SystemExit(f"facilities.json of the monthly package has no facility {spec['facility']}")
    if record.get("facility_key") != spec["facility_key"]:
        raise SystemExit(f"facility {spec['facility']} has the key {record.get('facility_key')!r}, the specification expects "
                         f"{spec['facility_key']!r}: a wrong facility id is never read")
    return record


def _rules_of(inputs: dict, facility_id: str) -> list:
    return [rule for rule in inputs["rules"].values() if rule["facility_id"] == facility_id]


def _usable_rules(inputs: dict, facility_id: str) -> list:
    """The publicly purchasable fixed monthly products of a facility (spec Amendment D2): a rule with a confirmed monthly
    amount that the package marks usable as a monthly price."""
    return [rule for rule in _rules_of(inputs, facility_id)
            if rule.get("evidence_status") == EVIDENCE_PUBLISHED and rule.get("model_usable_as_monthly_price") is True
            and rule.get("billing_period") == RULE_BILLING_PERIOD and rule.get("monthly_amount_eur") is not None]


def _source_url(inputs: dict, rule: dict) -> str:
    for source_id in rule.get("source_ids") or ():
        source = inputs["sources"].get(source_id)
        if source and str(source.get("url", "")).startswith("http"):
            return source["url"]
    raise SystemExit(f"offer {rule['offer_id']}: none of its sources {rule.get('source_ids')} has a URL")


def _access_text(rule: dict) -> str:
    """The weekly access windows of a product in words ('; access Mo-Fr 06:30-21:00'), empty where the rule states none."""
    names = {"MO": "Mo", "TU": "Tu", "WE": "We", "TH": "Th", "FR": "Fr", "SA": "Sa", "SU": "Su"}
    parts = []
    for window in (rule.get("access_schedule") or {}).get("weekly_windows") or []:
        days = window["days"]
        label = names[days[0]] if len(days) == 1 else f"{names[days[0]]}-{names[days[-1]]}"
        parts.append(f"{label} {window['start']}-{window['end']}")
    return "; access " + ", ".join(parts) if parts else ""


def _product_text(rule: dict, amount: float) -> str:
    text = f"{rule['product_name']}: {_eur_text(amount)} EUR per month"
    text += ", VAT included" if rule.get("vat_included") else ""
    text += _access_text(rule)
    if rule.get("minimum_term_months"):
        text += f"; minimum contract {rule['minimum_term_months']} months"
    return _ascii(text)


def _product(record_id: str, decision: str, reason: str, rule_or_offer: dict, amount, note: str, evidence: list,
             product_name: Optional[str] = None) -> dict:
    return {"record_id": record_id, "decision": decision, "reason": reason, "amount_eur": amount,
            "product_name": _ascii(product_name or rule_or_offer["product_name"]), "note": _ascii(note),
            "evidence": [PACKAGE_FILE, *evidence]}


def _quotes(inputs: dict, spec: dict) -> list:
    return [readme_sentence(inputs["readme"], token, phrase) for token, phrase in spec.get("quotes") or ()]


def _check_status(inputs: dict, spec: dict, record: dict) -> None:
    """The package's records must say what the specification says about a garage without a published product."""
    status = spec["status"]
    rules = _rules_of(inputs, record["facility_id"])
    if rules:
        raise SystemExit(f"{spec['garage_id']}: the package holds monthly rules {[rule['offer_id'] for rule in rules]} for "
                         f"{record['facility_id']}, but the specification says {status}")
    offers = [inputs["offers"][offer_id] for offer_id in record.get("offer_ids") or ()]
    expected_review = REVIEW_PERIOD_UNCONFIRMED if status == "period_unconfirmed" else REVIEW_UNKNOWN
    if record.get("review_status") != expected_review:
        raise SystemExit(f"{spec['garage_id']}: the package's review status is {record.get('review_status')!r}, the status "
                         f"{status} needs {expected_review!r}")
    if status == "sold_out" and record.get("new_contract_status") != CONTRACT_SOLD_OUT:
        raise SystemExit(f"{spec['garage_id']}: the status sold_out needs new_contract_status {CONTRACT_SOLD_OUT!r}, the package "
                         f"states {record.get('new_contract_status')!r}")
    if status != "sold_out" and record.get("new_contract_status") == CONTRACT_SOLD_OUT:
        raise SystemExit(f"{spec['garage_id']}: the package states the facility sold out, the specification says {status}")
    if status == "period_unconfirmed":
        if not any(offer.get("evidence_status") == EVIDENCE_PERIOD_UNCONFIRMED and offer.get("amount_eur") for offer in offers):
            raise SystemExit(f"{spec['garage_id']}: the status period_unconfirmed needs offers with an amount whose billing "
                             "period the package marks unconfirmed")
    if status == "price_on_request" and not any(offer.get("evidence_status") == REVIEW_UNKNOWN for offer in offers):
        raise SystemExit(f"{spec['garage_id']}: the status price_on_request needs an offer without a known price")
    if status in ("no_price", "sold_out", "price_on_request"):
        priced = [offer["offer_id"] for offer in offers if offer.get("monthly_amount_eur") is not None]
        if priced:
            raise SystemExit(f"{spec['garage_id']}: the offers {priced} state a monthly amount, the specification says {status}")


def _published_from_rules(inputs: dict, spec: dict, record: dict) -> dict:
    usable = _usable_rules(inputs, record["facility_id"])
    named = inputs["rules"].get(spec["rule"])
    if named is None or named["facility_id"] != record["facility_id"] or named not in usable:
        raise SystemExit(f"{spec['garage_id']}: the rule {spec['rule']} is no publicly purchasable monthly product of "
                         f"{record['facility_id']} in the package")
    amount = _whole_cents(named["monthly_amount_eur"], f"rule {named['offer_id']}")
    cheaper = [rule["offer_id"] for rule in usable if float(rule["monthly_amount_eur"]) <= amount and rule is not named]
    if cheaper:
        raise SystemExit(f"{spec['garage_id']}: the rule {spec['rule']} ({amount} EUR) is not the one cheapest product: "
                         f"{cheaper} cost the same or less (spec Amendment D2)")
    url = _source_url(inputs, named)
    text = _product_text(named, amount)
    products = [_product(f"monthly_{spec['garage_id']}_{str(named['offer_code']).lower()}", "used", "", named, amount,
                         f"{text}; published monthly product, cheapest of {len(usable)} (spec Amendment D2); the access window "
                         "is not modelled (a work stay outside it still uses the product)", [named["offer_id"]])]
    for rule in sorted(usable, key=lambda item: item["offer_id"]):
        if rule is named:
            continue
        other = _whole_cents(rule["monthly_amount_eur"], f"rule {rule['offer_id']}")
        products.append(_product(f"monthly_{spec['garage_id']}_{str(rule['offer_code']).lower()}", "not_used",
                                 "not_the_cheapest", rule, other,
                                 f"{_product_text(rule, other)}; another product of the same garage is cheaper",
                                 [rule["offer_id"]]))
    return {"monthly": {"monthly_eur": amount, "monthly_source_url": url, "monthly_product": text, "rule_id": named["offer_id"]},
            "products": products, "quotes": []}


def _published_from_configurator(inputs: dict, spec: dict, record: dict) -> dict:
    offer = inputs["offers"].get(spec["offer"])
    if offer is None or offer["facility_id"] != record["facility_id"]:
        raise SystemExit(f"{spec['garage_id']}: the offer {spec['offer']} is no offer of {record['facility_id']}")
    if offer.get("monthly_amount_eur") is not None or offer.get("evidence_status") != REVIEW_UNKNOWN:
        raise SystemExit(f"{spec['garage_id']}: the package states a price for {spec['offer']}; the configurator reading is for "
                         "a product whose price the package does not know")
    facts = read_configurator(inputs["evidence"][CONFIGURATOR_FILE], spec["garage_id"])
    if _normalised(facts["product"]) != _normalised(offer["product_name"]):
        raise SystemExit(f"{spec['garage_id']}: the configurator product {facts['product']!r} is not the package's offer "
                         f"{offer['product_name']!r}")
    amount = facts["amount_eur"]
    term_text = (facts["minimum_term"] or "").replace("Mindestvertragslaufzeit:", "minimum term")
    term_text = term_text.replace(" K\u00fcndigungsfrist:", ", notice period")
    term = f"; {term_text}" if term_text else ""
    text = _ascii(f"{facts['product']}: {_eur_text(amount)} EUR gross per further month (net {_eur_text(facts['net_eur'])} + VAT "
                  f"{_eur_text(facts['vat_eur'])}), first month pro rata{term}")
    quotes = _quotes(inputs, spec)
    note = (f"{text}; the public product configurator of the operator ({CONFIGURATOR_FILE} of {EVIDENCE_DIRECTORY_NAME}, retrieved "
            f"{facts['retrieved_at']}, nothing submitted); the package itself records no price here: {' '.join(quotes)}")
    product = _product(f"monthly_{spec['garage_id']}_dauerparken", "used", "", offer, amount, note,
                       [f"{EVIDENCE_DIRECTORY_NAME}/{CONFIGURATOR_FILE}", offer["offer_id"]], facts["product"])
    return {"monthly": {"monthly_eur": amount, "monthly_source_url": facts["url"], "monthly_product": text,
                        "rule_id": offer["offer_id"]}, "products": [product], "quotes": quotes}


def _unpublished(inputs: dict, spec: dict, record: dict) -> dict:
    """A garage without a published product: the status row (and the recorded amounts that are no product)."""
    _check_status(inputs, spec, record)
    quotes = _quotes(inputs, spec)
    status = spec["status"]
    extra = []
    if spec.get("evidence") == "eiermarkt":
        extra.append(read_eiermarkt_finding(inputs["evidence"][EIERMARKT_FILE], spec["garage_id"]))
        extra.append(f"({EVIDENCE_DIRECTORY_NAME}/{EIERMARKT_FILE})")
    offers = [inputs["offers"][offer_id] for offer_id in record.get("offer_ids") or ()]
    products = []
    if status == "period_unconfirmed":
        for offer in sorted((item for item in offers if item.get("evidence_status") == EVIDENCE_PERIOD_UNCONFIRMED),
                            key=lambda item: item["offer_id"]):
            amount = _whole_cents(offer["amount_eur"], f"offer {offer['offer_id']}")
            suffix = str(offer["offer_id"])[-3:].lower()
            products.append(_product(f"monthly_{spec['garage_id']}_{suffix}", "not_used", status, offer,
                                     amount, f"{offer['product_name']}: {_eur_text(amount)} EUR published without a billing "
                                     f"period; {' '.join(quotes)}", [offer["offer_id"]]))
    else:
        offer = next((item for item in offers if item.get("evidence_status") == REVIEW_UNKNOWN), None)
        name = offer["product_name"] if offer else "monthly product"
        products.append({"record_id": f"monthly_{spec['garage_id']}_{status}", "decision": "not_used", "reason": status,
                         "amount_eur": None, "product_name": _ascii(name),
                         "note": _ascii(f"no published monthly price ({status}): {' '.join(quotes)} {' '.join(extra)}".strip()),
                         "evidence": [PACKAGE_FILE, *([offer["offer_id"]] if offer else [])]
                                     + ([f"{EVIDENCE_DIRECTORY_NAME}/{EIERMARKT_FILE}"] if extra else [])})
    if spec.get("hidden_amount"):
        hidden = inputs["visibility"]
        amount = _whole_cents(hidden["rejected_price_eur"], f"{VISIBILITY_MEMBER} rejected_price_eur")
        products.append({"record_id": f"monthly_{spec['garage_id']}_hidden", "decision": "not_used",
                         "reason": "excluded_by_package", "amount_eur": amount,
                         "product_name": "Dauerstellplatz (amount in hidden page sections)",
                         "note": _ascii(f"{_eur_text(amount)} EUR stands only in page sections that the operator's page hides "
                                        f"(CSS display none); the package excludes it from the current model: "
                                        f"{hidden['reason']}"),
                         "evidence": [PACKAGE_FILE, VISIBILITY_MEMBER]})
    return {"monthly": None, "products": products, "quotes": quotes}


def read_garage_products(inputs: dict, specs_rows) -> dict:
    """The monthly status of every Braunschweig garage of ``specs_rows`` (``regional_garage_specs.BS_MONTHLY_SPECS``): {garage_id:
    {"status", "facility", "monthly" ({"monthly_eur", "monthly_source_url", "monthly_product", "rule_id"} or None), "products"
    (the QA products of the garage), "quotes" (the sentences of the package README), "note" (the sentence of the garage row)}}.
    ``SystemExit`` when a specification contradicts the package (see ``_check_status`` and ``_published_from_rules``)."""
    result = {}
    for spec in specs_rows:
        if spec["status"] not in STATUSES:
            raise SystemExit(f"{spec['garage_id']}: unknown status {spec['status']!r}; one of {list(STATUSES)}")
        record = _facility(inputs, spec)
        if spec["garage_id"] in result:
            raise SystemExit(f"two monthly specifications for {spec['garage_id']}")
        if spec["status"] == "published":
            reader = _published_from_rules if spec.get("rule") else _published_from_configurator
            found = reader(inputs, spec, record)
        else:
            found = _unpublished(inputs, spec, record)
        found.update({"status": spec["status"], "facility": record["facility_id"]})
        sentence = (f"Monthly product (spec Amendment F1, package {PACKAGE_FILE}): "
                    + (f"published, {found['monthly']['monthly_product']}" if found["monthly"] else
                       f"{spec['status']}, no monthly price is published ({' '.join(found['quotes'])})"))
        found["note"] = _ascii(sentence)
        result[spec["garage_id"]] = found
    return result


def read_recorded_products(inputs: dict, recorded_specs) -> list:
    """The products of the facilities that are no garage of the dataset (``regional_garage_specs.BS_RECORDED_SPECS``): a list of
    {"record_id", "facility", "name", "decision" "not_used", "reason", "amount_eur", "product_name", "note", "evidence"}: the
    cheapest publicly purchasable current product of each facility (a sold-out facility included, the note says so), with the
    other products of the facility named in the note. ``SystemExit`` for a facility that has no such product or whose
    package record is of another kind than the specification expects (a garage or a surface parking)."""
    rows = []
    for spec in recorded_specs:
        record = _facility(inputs, spec)
        if record["facility_type"] != spec["facility_type"]:
            raise SystemExit(f"{spec['record_id']}: the package calls {record['facility_id']} {record['facility_type']!r}, the "
                             f"specification {spec['facility_type']!r}")
        usable = sorted(_usable_rules(inputs, record["facility_id"]), key=lambda rule: (float(rule["monthly_amount_eur"]),
                                                                                         rule["offer_id"]))
        if not usable:
            raise SystemExit(f"{spec['record_id']}: the package holds no publicly purchasable monthly product for "
                             f"{record['facility_id']}")
        if len(usable) > 1 and float(usable[0]["monthly_amount_eur"]) == float(usable[1]["monthly_amount_eur"]):
            raise SystemExit(f"{spec['record_id']}: two products cost the same; the cheapest is ambiguous")
        cheapest = usable[0]
        amount = _whole_cents(cheapest["monthly_amount_eur"], f"rule {cheapest['offer_id']}")
        others = "; ".join(f"{rule['product_name']} {_eur_text(rule['monthly_amount_eur'])} EUR" for rule in usable[1:])
        sold_out = CONTRACT_SOLD_OUT in (record.get("new_contract_status"), cheapest.get("new_contract_status"))
        note = (f"{_product_text(cheapest, amount)}; {spec['why']}"
                + ("; the package states the product sold out for new contracts" if sold_out else "")
                + (f"; other products not taken: {others}" if others else ""))
        rows.append({"record_id": spec["record_id"], "facility": record["facility_id"], "name": _ascii(record["name"]),
                     "decision": "not_used", "reason": spec["reason"], "amount_eur": amount,
                     "product_name": _ascii(cheapest["product_name"]), "note": _ascii(note),
                     "evidence": [PACKAGE_FILE, cheapest["offer_id"]]})
    return rows


def qa_product_rows(garage_products: dict, recorded: list, names: dict) -> list:
    """The QA rows (field dicts for ``regional_garages.qa_rows``) of every product that ``read_garage_products`` and
    ``read_recorded_products`` found; ``names`` is {garage_id: name of the garage in the dataset} (the subject of a row)."""
    rows = []
    for garage_id, found in garage_products.items():
        for product in found["products"]:
            rows.append({"record_id": product["record_id"], "record_type": "monthly_product",
                         "municipality_ags": BRAUNSCHWEIG_AGS, "subject": f"{names[garage_id]}: {product['product_name']}",
                         "garage_id": garage_id, "decision": product["decision"], "reason_code": product["reason"],
                         "amount_eur": None if product["amount_eur"] is None else _eur_text(product["amount_eur"]),
                         "count": 1, "evidence": ";".join(product["evidence"]), "note": product["note"]})
    for product in recorded:
        rows.append({"record_id": product["record_id"], "record_type": "monthly_product", "municipality_ags": BRAUNSCHWEIG_AGS,
                     "subject": f"{product['name']}: {product['product_name']}", "garage_id": None,
                     "decision": product["decision"], "reason_code": product["reason"],
                     "amount_eur": _eur_text(product["amount_eur"]), "count": 1, "evidence": ";".join(product["evidence"]),
                     "note": product["note"]})
    return rows
