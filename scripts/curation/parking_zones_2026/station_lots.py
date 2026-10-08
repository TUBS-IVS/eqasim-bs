"""Station car parks of Braunschweig Hbf as single paid-site zones (parking cost zones v2, spec Amendment G1, issue #436, Task 4g).

Not part of the pipeline and never run by synpp. The owner decided on 2026-10-08 that the station car parks of DB BahnPark are
public (BahnCard and Pcard only give a discount), so the Braunschweig Hbf car parks P1 Nord, P2 Sued and P3 West become single
paid-site zones (spec Amendment D3, as built for the Goslar 1 EUR/h car parks and the BgA lots): the area within 50 m of the lot
outline (ASSUMPTION C-a), cut out of the zones that take precedence. Inputs, all gitignored under
``eqasim-data/data/braunschweig/parking/raw_sources/municipal_2026-10-08/`` and verified before anything is read:

* the owner's package ``Braunschweig_Monatstarife_2026-10-08.zip`` (SHA-256 pinned in ``bs_monthly_products.PACKAGE_SHA256``):
  every member that is read must match the zip's own ``manifest.sha256``; nothing is extracted and no script of the package is
  executed. Read: ``evidence/contipark/p1.txt``, ``p2.txt``, ``p3.txt`` (the text of the Contipark location pages, the PRIMARY
  source of the tariff), ``evidence/contipark/db_bahnpark_8000049.txt`` (the text of the DB BahnPark sheet of 2026-05-29, the
  CORROBORATION), ``data/tariff_rules.json``, ``data/facilities.json`` and ``data/sources.json`` (the cheapest publicly purchasable
  monthly product of each facility and the page URLs);
* the Overpass response ``osm_braunschweig_hbf_station_lots_geometry_2026-10-08.json`` (SHA-256 pinned in ``OSM_SHA256``): the
  outlines of the OSM ways 25411279 (P1 Nord), 7874165 (P2 Sued), 236421384 (P3 West) and 26163572 ('Post', the lot of the BgA
  zone ``bs_bga_willy_brandt_platz``, which borders P1; it is a cross-check reference and no zone of this step).

No amount is typed. The specification (``STATIONS``) names the LABEL of the line to read in the fee table of each page ('1 Stunde',
'30 Minuten', '1 Tag', '1 Monat fuer Stellplatzmieter'); ``read_fee_table`` reads the amount of that line and stops when the
line is missing or ambiguous. The same three amounts must stand in the DB BahnPark sheet (``read_sheet_amounts``) and the
monthly amount must be the cheapest publicly purchasable monthly product of the facility in the package's rules (rule D2); any
difference stops the step. ``commuter_day_eur`` is that monthly amount over ``garage_qa.WORKING_DAYS_PER_MONTH`` working days to
whole cents (ASSUMPTION P2).

Named assumptions of the rows: C-a (the 50 m area), G-a (the amount per started unit and the published '1 Tag' amount as the
daily maximum per stay; the pages state neither a rounding nor the day boundary) and G-b (the workplace class by the location,
as for the neighbouring BgA row), R2-a (resident permits are not valid: no source states that they are). Named and NOT
modelled: the Kiss&Ride tariff and the evening tariff of P1, the BahnCard and Pcard discounts, the weekly and machine monthly
products of P2 and P3, the reserved-space products and the stated maximum parking durations. Free street parking west of the
station stays unzoned (ASSUMPTION Z1).

``append_zones`` adds the three zones to a FINISHED zone release (the committed ``parking_zones_2026.geojson``) and refuses to
change any other zone: it is the last step of the zone chain, run after ``assemble_parking_zones.py``. ``qa_rows`` writes the QA
rows into the municipal QA table, ``tariff_rows`` the tariff rows, and ``check_tariff_rows`` compares the committed tariff table
with the evidence. CRS: EPSG:25832 throughout, areas in m2, distances in m, money in EUR.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import zipfile
from decimal import Decimal
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon
from shapely.ops import unary_union

import assemble_parking_zones as ap
import bs_monthly_products as bsm
import curation_common as cc
import garage_supplement as sup
import municipal_zones as mz
import regional_zones as rz

# The script runs from its own directory; the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from braunschweig.parking import garage_qa as pq  # noqa: E402
from braunschweig.parking import zones as pz  # noqa: E402

STATION_DATE = "2026-10-08"
#: The file name of the zone release; the GeoJSON ``name`` member follows the name of the file that is written.
ZONES_FILE_NAME = "parking_zones_2026.geojson"
PACKAGE_NAME, PACKAGE_FILE, PACKAGE_SHA256 = bsm.PACKAGE_NAME, bsm.PACKAGE_FILE, bsm.PACKAGE_SHA256
#: The Overpass response with the outlines of the station lots (also in MANIFEST.md of the folder).
OSM_FILE = "osm_braunschweig_hbf_station_lots_geometry_2026-10-08.json"
OSM_SHA256 = "fe4332938fcc7e99595aff167071a040fcea01b5be5df38e259647e64a7ecc26"
OSM_PROVENANCE = ap.STATION_OSM_PROVENANCE
BS_AGS = mz.BS_AGS
#: The OSM way of the lot of the BgA zone Willy-Brandt-Platz, the neighbour of P1 (the QA cross-check reference).
POST_WAY = 26163572
BGA_NEIGHBOUR_ZONE = "bs_bga_willy_brandt_platz"
#: ASSUMPTION G-b: the workplace class (the SrV Unterbezirk of the location) of the station rows is the one of the BgA row
#: of the Hauptbahnhof forecourt, ``bs_outer``.
WORKPLACE_CLASS = "bs_outer"
#: The fee window of the rows, decimal hours of the weekday: the pages state the car parks open around the clock and a fee
#: table without a time limit (the evening tariff of P1 is not modelled). Ruling R-4g-2: a window read from the opening hours of
#: an operator page is no ordinance or signage statement (the situation of ASSUMPTION P5 at the garages), so by the rule of
#: ASSUMPTION F1 the rows say ``assumption`` and name ASSUMPTION F1.
FEE_WINDOW_H = (0.0, 24.0)
FEE_WINDOW_SOURCE = "assumption"
#: The unit, in minutes, of the labels of the fee tables that name a billing unit (''1 Tag'' is no unit).
LABEL_UNIT_MIN = {"30 Minuten": 30, "1 Stunde": 60}
GERMAN_MONTH_LABEL = "1 Monat f\u00fcr Stellplatzmieter"
DAY_LABEL = "1 Tag"
SHEET_MONTH_LABEL = "1 Monat Dauerparken*"

#: The station lots, in release order. ``rate_label``: the line of the fee table that is the rate (its unit is the billing
#: unit); ``check_label``: a second unit line that must be the same hourly rate (P2, P3: the hour is twice the half hour);
#: ``sheet_heading``: the name part of the heading of the lot in the DB BahnPark sheet; ``special``: sentences that the page
#: must contain because the note names them as not modelled.
STATIONS = (
    {"zone_id": "bs_hbf_p1_nord", "label": "P1 Nord", "way": 25411279, "osm_name": "Parkplatz Hbf Nord", "facility": "BS_DB_P1",
     "facility_key": "braunschweig_hbf_nord_p1", "source_id": "ct_p1", "page": "evidence/contipark/p1.txt",
     "rate_label": "1 Stunde", "check_label": None, "sheet_heading": "Nord",
     "special": ("Kiss&Ride: 0,80", "Abendtarif: 18:00 bis 02:00 Uhr: 5,00 maximal")},
    {"zone_id": "bs_hbf_p2_sued", "label": "P2 Sued", "way": 7874165, "osm_name": "Parkplatz Hbf S\u00fcd", "facility": "BS_DB_P2",
     "facility_key": "braunschweig_hbf_sued_p2", "source_id": "ct_p2", "page": "evidence/contipark/p2.txt",
     "rate_label": "30 Minuten", "check_label": "1 Stunde", "sheet_heading": "", "special": ()},
    {"zone_id": "bs_hbf_p3_west", "label": "P3 West", "way": 236421384, "osm_name": "Parkplatz Hbf West",
     "facility": "BS_DB_P3", "facility_key": "braunschweig_hbf_west_p3", "source_id": "ct_p3", "page": "evidence/contipark/p3.txt",
     "rate_label": "30 Minuten", "check_label": "1 Stunde", "sheet_heading": "West", "special": ()},
)
STATION_ZONE_IDS = tuple(station["zone_id"] for station in STATIONS)
if STATION_ZONE_IDS != ap.STATION_ZONE_IDS:
    raise SystemExit(f"the specification lists the zones {STATION_ZONE_IDS}, assemble_parking_zones.STATION_ZONE_IDS "
                     f"{ap.STATION_ZONE_IDS}: the precedence of the release must name the same zones")
SHEET_MEMBER = "evidence/contipark/db_bahnpark_8000049.txt"
MEMBERS = tuple(station["page"] for station in STATIONS) + (SHEET_MEMBER, bsm.RULES_MEMBER, bsm.FACILITIES_MEMBER,
                                                           bsm.SOURCES_MEMBER)
_AMOUNT = re.compile(r"^(\d{1,3}(?:\.\d{3})*,\d{2})\u00a0\u20ac$")
_SHEET_HEADING = re.compile(r"^\s*Braunschweig Hbf Parkplatz(?P<name>[^\n]*?)\s{2,}Tarif\s+\(Angaben in EUR")
_SHEET_FOOTER = "Ein Service der DB BahnPark GmbH"


def _ascii(text) -> str:
    return rz._ascii(text)


def _german_decimal(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def _eur(value) -> str:
    return f"{float(value):.2f}"


def commuter_day_eur(monthly_eur: float) -> float:
    """The commuter product of a monthly amount: over ``garage_qa.WORKING_DAYS_PER_MONTH`` working days, whole cents (the
    rounding the QA validator applies, ASSUMPTION P2)."""
    return round(float(monthly_eur) / pq.WORKING_DAYS_PER_MONTH, 2)


# ---------------------------------------------------------------- inputs
def load_station_inputs(package_path, osm_path, expected_package_sha256: Optional[str] = None,
                        expected_osm_sha256: Optional[str] = None) -> dict:
    """The verified station inputs: the package zip (``PACKAGE_SHA256`` or ``expected_package_sha256``, for a synthetic test
    package; every member read against ``manifest.sha256``) and the Overpass response (``OSM_SHA256`` or
    ``expected_osm_sha256``). Returns {"file", "osm_file" ({"file", "sha256", "bytes", "osm_base"}), "texts" (member -> text),
    "rules" (offer id -> rule), "facilities", "sources", "ways" (OSM way id -> {"geometry" (polygon, EPSG:25832), "tags"})}.
    ``SystemExit`` for a changed or missing file, an OSM way that is no closed valid ring, or a way whose tags are not those
    the specification expects (the name, amenity=parking, fee=yes)."""
    path = Path(package_path)
    if not path.is_file():
        raise SystemExit(f"{path} missing: pass the owner's package {PACKAGE_FILE} unchanged")
    actual = mz.file_sha256(path)
    if actual != (expected_package_sha256 or PACKAGE_SHA256):
        raise SystemExit(f"{path}: SHA-256 {actual} is not the recorded {expected_package_sha256 or PACKAGE_SHA256} (data record "
                         "parking_zones_2026); a changed package is never read")
    with zipfile.ZipFile(path) as archive:
        manifest = sup._manifest(archive, PACKAGE_NAME)
        texts = {member: sup._read_member(archive, manifest, member, PACKAGE_NAME).decode("utf-8") for member in MEMBERS}
    rules = {rule["offer_id"]: rule for rule in json.loads(texts[bsm.RULES_MEMBER])["rules"]}
    facilities = {record["facility_id"]: record for record in json.loads(texts[bsm.FACILITIES_MEMBER])}
    sources = {record["id"]: record for record in json.loads(texts[bsm.SOURCES_MEMBER])}
    osm = Path(osm_path)
    if not osm.is_file():
        raise SystemExit(f"{osm} missing: pass the Overpass response {OSM_FILE} unchanged")
    osm_sha256 = mz.file_sha256(osm)
    if osm_sha256 != (expected_osm_sha256 or OSM_SHA256):
        raise SystemExit(f"{osm}: SHA-256 {osm_sha256} is not the recorded {expected_osm_sha256 or OSM_SHA256} (data record "
                         "parking_zones_2026); a changed Overpass response is never read")
    document = json.loads(osm.read_text(encoding="utf-8"))
    ways = _read_ways(document)
    inputs = {"path": path, "file": {"file": path.name, "sha256": actual, "bytes": path.stat().st_size}, "texts": texts,
              "rules": rules, "facilities": facilities, "sources": sources, "ways": ways,
              "osm_file": {"file": osm.name, "sha256": osm_sha256, "bytes": osm.stat().st_size,
                           "osm_base": document.get("osm3s", {}).get("timestamp_osm_base", "")}}
    if not inputs["osm_file"]["osm_base"]:
        raise SystemExit(f"{osm.name}: no osm3s timestamp_osm_base, the OSM snapshot of the outlines is unknown")
    print(f"station inputs verified: {path.name} (SHA-256 {actual}), {osm.name} (SHA-256 {osm_sha256}, OSM base "
          f"{inputs['osm_file']['osm_base']}); {len(ways)} OSM ways, {len(rules)} monthly rules")
    return inputs


def _read_ways(document: dict) -> dict:
    ways = {}
    for element in document.get("elements", []):
        if element.get("type") != "way":
            raise SystemExit(f"the Overpass response holds a {element.get('type')!r} element; only ways are expected")
        ring = [(point["lon"], point["lat"]) for point in element.get("geometry") or []]
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise SystemExit(f"OSM way {element['id']}: the geometry is no closed ring; no outline is invented")
        geometry = cc.to_metric([Polygon(ring)])[0]
        if not geometry.is_valid or geometry.is_empty:
            raise SystemExit(f"OSM way {element['id']}: the outline is an invalid or empty polygon; nothing is repaired")
        if element["id"] in ways:
            raise SystemExit(f"OSM way {element['id']} appears twice")
        ways[element["id"]] = {"geometry": geometry, "tags": element.get("tags") or {}}
    return ways


def _way(inputs: dict, way_id: int) -> dict:
    if way_id not in inputs["ways"]:
        raise SystemExit(f"the Overpass response has no OSM way {way_id}")
    return inputs["ways"][way_id]


# ---------------------------------------------------------------- reading the evidence
def read_fee_table(text: str, where: str) -> tuple:
    """(``{label: amount in EUR}``, special text) of the 'Regulaeres Parkentgelt' table of a Contipark page text: the lines
    between that heading (header lines 'Zeiteinheit' and 'Preis') and 'Alle Angaben ohne Gewaehr', each a label followed by its
    amount ('2,50 EUR'); the line after 'Sondertarife' is the special-tariff text. ``SystemExit`` for a missing table, a
    label that appears twice, an amount without a label or a label without an amount: nothing is guessed."""
    lines = [line.strip() for line in text.splitlines()]
    try:
        start = lines.index("Regul\u00e4res Parkentgelt")
        end = next(position for position in range(start, len(lines)) if lines[position].startswith("Alle Angaben ohne Gew"))
    except (ValueError, StopIteration):
        raise SystemExit(f"{where}: no fee table between 'Regul\u00e4res Parkentgelt' and "
                         "'Alle Angaben ohne Gew\u00e4hr'") from None
    block = lines[start + 1:end]
    if block[:2] != ["Zeiteinheit", "Preis"]:
        raise SystemExit(f"{where}: the fee table does not start with the header lines 'Zeiteinheit' and 'Preis'")
    amounts, special, position = {}, "", 2
    while position < len(block):
        label = block[position]
        if label == "Sondertarife":
            special = " ".join(block[position + 1:])
            break
        if position + 1 >= len(block) or not _AMOUNT.match(block[position + 1]):
            raise SystemExit(f"{where}: the line {label!r} of the fee table has no amount line after it")
        if label in amounts:
            raise SystemExit(f"{where}: the label {label!r} appears twice in the fee table; the line to read is ambiguous")
        amounts[label] = float(_german_decimal(_AMOUNT.match(block[position + 1]).group(1)))
        position += 2
    if not amounts:
        raise SystemExit(f"{where}: the fee table holds no line")
    return amounts, special


def _label_amount(amounts: dict, label: str, where: str) -> float:
    if label not in amounts:
        raise SystemExit(f"{where}: the fee table has no line {label!r} (it has {sorted(amounts)})")
    return amounts[label]


def read_sheet_amounts(sheet: str, station: dict) -> dict:
    """The corroborating amounts of one lot in the text of the DB BahnPark sheet: ``{label: amount}`` for the rate line, the
    optional check line, the day line and the monthly line ('1 Monat Dauerparken*', the plain space, not the fixed space). The
    lot is the block from its heading ('Braunschweig Hbf Parkplatz <name> ... Tarif (Angaben in EUR') to the next heading or the
    page footer; the amounts stand at the end of a line of the right-hand table column. ``SystemExit`` unless the heading is
    found once and every line exactly once."""
    lines = sheet.splitlines()
    headings = [(position, _SHEET_HEADING.match(line).group("name").strip()) for position, line in enumerate(lines)
                if _SHEET_HEADING.match(line)]
    own = [position for position, name in headings if name == station["sheet_heading"]]
    if len(own) != 1:
        raise SystemExit(f"DB BahnPark sheet: {len(own)} heading(s) of the lot {station['label']} (name part "
                         f"{station['sheet_heading']!r}); exactly one is needed")
    begin = own[0]
    ends = [position for position, _ in headings if position > begin]
    ends += [position for position, line in enumerate(lines) if position > begin and _SHEET_FOOTER in line]
    block = lines[begin:min(ends) if ends else len(lines)]
    wanted = [station["rate_label"], DAY_LABEL, SHEET_MONTH_LABEL] + ([station["check_label"]] if station["check_label"] else [])
    found = {}
    for label in wanted:
        pattern = re.compile(r"(?<![\w(])" + re.escape(label) + r"\s{2,}(\d{1,3}(?:\.\d{3})*,\d{2})\s*$")
        hits = [pattern.search(line).group(1) for line in block if pattern.search(line)]
        if len(hits) != 1:
            raise SystemExit(f"DB BahnPark sheet, lot {station['label']}: {len(hits)} line(s) {label!r} with an amount; exactly "
                             "one is needed")
        found[label] = float(_german_decimal(hits[0]))
    return found


def _monthly_rule(inputs: dict, station: dict, page_amount: float) -> dict:
    """The cheapest publicly purchasable current monthly product of the facility in the package's rules (rule D2); it must be
    the amount of the page's monthly line, else ``SystemExit``."""
    facility = bsm._facility(inputs, station)
    usable = sorted(bsm._usable_rules(inputs, facility["facility_id"]),
                    key=lambda rule: (float(rule["monthly_amount_eur"]), rule["offer_id"]))
    if not usable:
        raise SystemExit(f"{station['label']}: the package holds no publicly purchasable monthly product of "
                         f"{facility['facility_id']}")
    if len(usable) > 1 and float(usable[0]["monthly_amount_eur"]) == float(usable[1]["monthly_amount_eur"]):
        raise SystemExit(f"{station['label']}: two products cost the same; the cheapest is ambiguous")
    cheapest = usable[0]
    if float(cheapest["monthly_amount_eur"]) != page_amount:
        raise SystemExit(f"{station['label']}: the cheapest monthly product of the package costs {cheapest['monthly_amount_eur']} "
                         f"EUR but the page line {GERMAN_MONTH_LABEL!r} states {page_amount} EUR; the sources contradict")
    if cheapest["product_name"] != GERMAN_MONTH_LABEL:
        raise SystemExit(f"{station['label']}: the cheapest monthly product is {cheapest['product_name']!r}, the specification "
                         f"reads the line {GERMAN_MONTH_LABEL!r}")
    return cheapest


def station_evidence(inputs: dict) -> dict:
    """zone id -> the values the evidence gives for the tariff row (and what it leaves out), keys ``hourly_rate_eur``,
    ``billing_unit_min``, ``daily_cap_eur``, ``monthly_eur``, ``commuter_day_eur``, ``window``, ``source_url``, ``rule_id``,
    ``other_products`` (the products not used, in words), ``special`` (the page's special tariffs, not modelled), ``sheet`` (the
    corroborating amounts). The page is the primary source; the DB BahnPark sheet and the package's monthly rules must agree
    with it to the cent or the step stops."""
    sheet = inputs["texts"][SHEET_MEMBER]
    evidence = {}
    for station in STATIONS:
        where = f"{station['page']} ({station['label']})"
        amounts, special = read_fee_table(inputs["texts"][station["page"]], where)
        unit = LABEL_UNIT_MIN[station["rate_label"]]
        rate_amount = _label_amount(amounts, station["rate_label"], where)
        if station["check_label"]:
            check_amount = _label_amount(amounts, station["check_label"], where)
            per_hour = rate_amount * 60 / unit
            if abs(check_amount - per_hour) > 1e-9:
                raise SystemExit(f"{where}: the line {station['check_label']!r} states {check_amount} EUR but the rate line "
                                 f"{station['rate_label']!r} gives {per_hour} EUR per hour; the unit is not read")
        day = _label_amount(amounts, DAY_LABEL, where)
        monthly = _label_amount(amounts, GERMAN_MONTH_LABEL, where)
        for phrase in station["special"]:
            if phrase not in special:
                raise SystemExit(f"{where}: the special-tariff text does not hold {phrase!r}, which the note names as not modelled")
        rule = _monthly_rule(inputs, station, monthly)
        corroboration = read_sheet_amounts(sheet, station)
        expected = {station["rate_label"]: rate_amount, DAY_LABEL: day, SHEET_MONTH_LABEL: monthly}
        if station["check_label"]:
            expected[station["check_label"]] = _label_amount(amounts, station["check_label"], where)
        for label, amount in expected.items():
            if corroboration[label] != amount:
                raise SystemExit(f"{where}: the DB BahnPark sheet states {corroboration[label]} EUR for {label!r} but the "
                                 f"Contipark page states {amount} EUR; the sources contradict")
        not_used = {label: amount for label, amount in amounts.items()
                    if label not in (station["rate_label"], station["check_label"], DAY_LABEL, GERMAN_MONTH_LABEL)}
        source = inputs["sources"].get(station["source_id"])
        if source is None or not str(source.get("url", "")).startswith("http"):
            raise SystemExit(f"{station['source_id']}: no source record with a URL in sources.json")
        evidence[station["zone_id"]] = {
            "hourly_rate_eur": round(rate_amount * 60 / unit, 6), "billing_unit_min": unit, "daily_cap_eur": day,
            "monthly_eur": monthly, "commuter_day_eur": commuter_day_eur(monthly), "window": FEE_WINDOW_H,
            "source_url": source["url"], "rule_id": rule["offer_id"], "special": special, "sheet": corroboration,
            "rate_label": station["rate_label"], "rate_amount_eur": rate_amount,
            "other_products": "; ".join(f"{label} {_eur(amount)} EUR" for label, amount in not_used.items())}
    print("station tariffs read from the Contipark pages, corroborated by the DB BahnPark sheet and the package's monthly "
          "rules: " + "; ".join(
              f"{zone_id} {values['rate_amount_eur']:.2f} EUR per {values['billing_unit_min']} min, day "
              f"{values['daily_cap_eur']:.2f}, month {values['monthly_eur']:.2f} -> {values['commuter_day_eur']:.2f} per day"
              for zone_id, values in evidence.items()))
    return evidence


# ---------------------------------------------------------------- tariff rows
def _note(station: dict, values: dict, inputs: dict) -> str:
    unit = values["billing_unit_min"]
    other = f" Not used: {values['other_products']}." if values["other_products"] else ""
    special = (f" Named and NOT modelled (page text: '{_ascii(values['special'])}'): the Kiss&Ride tariff, the evening tariff and "
               "the BahnCard and Pcard discounts." if values["special"] else
               " Named and NOT modelled: the BahnCard and Pcard discounts of DB BahnPark.")
    return _ascii(
        f"Single paid site of spec Amendment G1 (owner decision 2026-10-08: the station car parks are public, rulings R-4b-4 and "
        f"R-4b-9 are revoked for them): the DB BahnPark car park {station['label']} (facility {station['facility']}), operated by "
        f"Contipark, open around the clock. Contipark page {values['source_url']} (retrieved {STATION_DATE}), the text in package "
        f"{PACKAGE_FILE} (SHA-256 {inputs['file']['sha256']}), member {station['page']}: {values['rate_amount_eur']:.2f} EUR per "
        f"'{values['rate_label']}' (hourly_rate_eur {values['hourly_rate_eur']:.2f}, billing_unit_min {unit}), '{DAY_LABEL}' "
        f"{values['daily_cap_eur']:.2f} EUR (daily_cap_eur), the monthly product '{GERMAN_MONTH_LABEL}' "
        f"{values['monthly_eur']:.2f} "
        f"EUR (the cheapest publicly purchasable monthly product, package rule {values['rule_id']}), commuter_day_eur "
        f"{values['commuter_day_eur']:.2f} = {values['monthly_eur']:.2f} / {pq.WORKING_DAYS_PER_MONTH} working days to the cent "
        "(ASSUMPTION P2). The same amounts stand in the DB BahnPark sheet of 2026-05-29 (member "
        f"{SHEET_MEMBER}, checked). ASSUMPTION G-a: the amount is billed per started {unit} min and the '{DAY_LABEL}' amount is "
        "the daily maximum per stay (the pages state neither a rounding nor the day boundary). Fee window 0-24 h: the page states "
        "the car park open 00:00-24:00 and a fee table without a time limit (a reading of the opening hours, no explicit fee "
        "hours): ASSUMPTION F1, fee_window_source assumption (ruling R-4g-2; the operator's page is no ordinance or signage)."
        + special + other + " The stated maximum parking durations are not modelled. resident_permits_valid false: a car park of "
        "DB BahnPark; no source states that resident permits are valid there (ASSUMPTION R2-a). ASSUMPTION C-a: the zone is the "
        f"area within {rz.SITE_BUFFER_M:.0f} m of the lot outline. workplace_class {WORKPLACE_CLASS}: the Hauptbahnhof lies in the "
        "SrV Unterbezirk Hauptbahnhof (Oberbezirk 4 Stadtteilring), as the BgA row at the forecourt; no Unterbezirk polygon is "
        "available, so this is a reading of the location (ASSUMPTION G-b). valid_from = retrieval date (the start of this tariff "
        "is not published).")


def tariff_rows(evidence: dict, inputs: dict) -> list:
    """The tariff rows (dicts in the column order of the committed table) of the station zones, every number from
    ``station_evidence``."""
    rows = []
    for station in STATIONS:
        values = evidence[station["zone_id"]]
        rows.append({
            "zone_id": station["zone_id"], "name": f"Braunschweig station car park Hauptbahnhof {station['label']} (DB BahnPark)",
            "municipality_ags": BS_AGS, "zone_type": "street_paid", "workplace_class": WORKPLACE_CLASS,
            "hourly_rate_eur": f"{values['hourly_rate_eur']:.2f}", "billing_unit_min": str(values["billing_unit_min"]),
            "free_if_stay_at_most_min": "", "first_period_min": "", "first_period_eur": "",
            "daily_cap_eur": f"{values['daily_cap_eur']:.2f}", "max_stay_min": "", "long_stay_product_eur": "",
            "member_day_eur": "", "guest_day_eur": "", "fee_start_h": f"{values['window'][0]:.1f}",
            "fee_end_h": f"{values['window'][1]:.1f}", "resident_exempt": "false", "source_url": values["source_url"],
            "source_date": STATION_DATE, "valid_from": STATION_DATE, "fee_window_source": FEE_WINDOW_SOURCE,
            "notes": _note(station, values, inputs), "commuter_day_eur": f"{values['commuter_day_eur']:.2f}",
            "resident_permits_valid": "false"})
    return rows


#: The header lines of the tariff table that the station rows change: (the fragment of the chain output, its new wording). The
#: bites lists name the station zones where an assumption bites on them.
STATION_BITES = "bs_hbf_p1_nord, bs_hbf_p2_sued, bs_hbf_p3_west"
HEADER_EDITS = (
    ("are not used (spec Amendment D2, ruling R-D2-a; bites: bs_zone_ib and the six TU zones).",
     "are not used (spec Amendment D2, ruling R-D2-a; bites: bs_zone_ib, the six TU zones and the three station zones "
     + STATION_BITES + ", whose product is the monthly product 'Stellplatzmieter' of DB BahnPark, spec Amendment G1)."),
    ("bs_bga_willy_brandt_platz, gs_parkplatz_klubgartenstrasse_zob; the campus rows take the default).",
     "bs_bga_willy_brandt_platz, gs_parkplatz_klubgartenstrasse_zob, " + STATION_BITES + "; the campus rows take the default)."),
    ("the five separately operated BgA car parks and the Goslar car park Klubgartenstrasse/ZOB are marked false",
     "the five separately operated BgA car parks, the Goslar car park Klubgartenstrasse/ZOB and the three DB BahnPark station car "
     "parks are marked false"),
    ("bh_berliner_platz, se_am_markt, br_hexenritt, br_wurmberg).",
     "bh_berliner_platz, se_am_markt, br_hexenritt, br_wurmberg, " + STATION_BITES + ")."),
)
#: The new assumption lines (inserted after the line that starts with ``HEADER_ANCHOR``).
HEADER_ANCHOR = "# ASSUMPTION D3-b:"
HEADER_LINES = (
    "# ASSUMPTION G-a: The three Braunschweig Hbf station car parks (DB BahnPark, Contipark) bill the published amount per started "
    "unit (60 min at P1, 30 min at P2 and P3) and the published '1 Tag' amount is the daily maximum per stay; the pages state "
    "neither a rounding nor the day boundary, and the fee window is the 0-24 h opening of the car park (spec Amendment G1; bites: "
    + STATION_BITES + ").",
    "# ASSUMPTION G-b: The three station rows take the workplace class bs_outer of the BgA row at the Hauptbahnhof forecourt: the "
    "station lies in the SrV Unterbezirk Hauptbahnhof; no Unterbezirk polygon is available, so the class is a reading of the "
    "location and only sets the free-parking share of work and education stays (spec Amendment G1; bites: "
    + STATION_BITES + ").",
)


def station_header(text: str, newline: str = "\n") -> str:
    """The text of the tariff table with the header lines of the station rows (``HEADER_EDITS``, ``HEADER_LINES``): idempotent (a
    change that is made already is skipped) and ``SystemExit`` when an anchor line of the chain output is missing, so a table
    whose header was edited by hand or by another step is never patched blindly."""
    for old, new in HEADER_EDITS:
        if new in text:
            continue
        if text.count(old) != 1:
            raise SystemExit(f"the header of the tariff table holds {text.count(old)} times the fragment {old!r}; the step needs "
                             "exactly one (the chain output before the station rows)")
        text = text.replace(old, new)
    if HEADER_LINES[0] not in text:
        if text.count(HEADER_ANCHOR) != 1:
            raise SystemExit(f"the header of the tariff table holds {text.count(HEADER_ANCHOR)} lines {HEADER_ANCHOR!r}; the step "
                             "inserts the lines G-a and G-b after exactly one")
        start = text.index(HEADER_ANCHOR)
        end = text.index(newline, start) + len(newline)
        text = text[:end] + "".join(line + newline for line in HEADER_LINES) + text[end:]
    return text


def append_tariff_rows(path, rows: list) -> None:
    """Append ``rows`` to the committed tariff table with its own line ending and write the header lines of the station rows
    (``station_header``); ``SystemExit`` when a zone id exists already or the row's columns differ from the table's header line."""
    raw = Path(path).read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = station_header(raw.decode("utf-8"), newline)
    header = next(line for line in text.splitlines() if line.startswith("zone_id,"))
    columns = header.split(",")
    existing = {line.split(",", 1)[0] for line in text.splitlines() if line and not line.startswith("#")}
    for row in rows:
        if row["zone_id"] in existing:
            raise SystemExit(f"{path}: the tariff row {row['zone_id']} exists already; the step appends and never rewrites")
        if list(row) != columns:
            raise SystemExit(f"the tariff row {row['zone_id']} has the columns {list(row)}, the table has {columns}")
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator=newline)
    for row in rows:
        writer.writerow([row[column] for column in columns])
    if not text.endswith("\n"):
        text += newline
    Path(path).write_bytes((text + buffer.getvalue()).encode("utf-8"))


def check_tariff_rows(evidence: dict, tariffs: pd.DataFrame) -> None:
    """The station rows of ``tariffs`` carry the evidence: rate, billing unit, daily maximum, fee window, commuter product,
    class, permit flag false, no maximum stay or long-stay product, the named assumptions and what is not modelled in the note.
    ``SystemExit`` listing every deviation."""
    rows = tariffs.set_index("zone_id")
    problems = [f"tariff row missing for {zone_id}" for zone_id in evidence if zone_id not in rows.index]
    for zone_id, values in evidence.items():
        if zone_id not in rows.index:
            continue
        row = rows.loc[zone_id]
        if row["zone_type"] != "street_paid" or row["municipality_ags"] != BS_AGS:
            problems.append(f"{zone_id}: a street_paid row of {BS_AGS} is expected")
        for column, expected in (("hourly_rate_eur", values["hourly_rate_eur"]), ("daily_cap_eur", values["daily_cap_eur"]),
                                 ("commuter_day_eur", values["commuter_day_eur"]), ("fee_start_h", values["window"][0]),
                                 ("fee_end_h", values["window"][1])):
            if pd.isna(row[column]) or abs(float(row[column]) - float(expected)) > 1e-9:
                problems.append(f"{zone_id}: {column} {row[column]} but the evidence gives {expected}")
        if pd.isna(row["billing_unit_min"]) or int(row["billing_unit_min"]) != values["billing_unit_min"]:
            problems.append(f"{zone_id}: billing_unit_min {row['billing_unit_min']} but the evidence gives "
                            f"{values['billing_unit_min']}")
        for column in ("free_if_stay_at_most_min", "first_period_min", "first_period_eur", "max_stay_min", "long_stay_product_eur",
                       "member_day_eur", "guest_day_eur"):
            if pd.notna(row[column]):
                problems.append(f"{zone_id}: {column} must stay empty (no source states it for a station car park)")
        if pd.isna(row["resident_permits_valid"]) or bool(row["resident_permits_valid"]):
            problems.append(f"{zone_id}: resident_permits_valid must be false (ASSUMPTION R2-a)")
        if row["workplace_class"] != WORKPLACE_CLASS:
            problems.append(f"{zone_id}: workplace_class {row['workplace_class']} but ASSUMPTION G-b gives {WORKPLACE_CLASS}")
        if row["fee_window_source"] != FEE_WINDOW_SOURCE:
            problems.append(f"{zone_id}: fee_window_source must be '{FEE_WINDOW_SOURCE}' (the window is read from the opening "
                            "hours of an operator page, ASSUMPTION F1, ruling R-4g-2)")
        notes = str(row["notes"])
        for phrase in ("ASSUMPTION C-a", "ASSUMPTION F1", "ASSUMPTION G-a", "ASSUMPTION G-b", "ASSUMPTION R2-a", "ASSUMPTION P2",
                       values["rule_id"], "NOT modelled", "BahnCard"):
            if phrase not in notes:
                problems.append(f"{zone_id}: the note must hold {phrase!r}")
        if values["special"] and ("Kiss&Ride" not in notes or "evening tariff" not in notes):
            problems.append(f"{zone_id}: the note must name the Kiss&Ride and the evening tariff as not modelled")
    if problems:
        raise SystemExit("station tariff rows contradict the evidence:\n  " + "\n  ".join(problems))
    print(f"station tariff rows agree with the evidence ({len(evidence)} rows: rate, unit, daily maximum, window, commuter "
          "product, class, permit flag, assumptions named)")


# ---------------------------------------------------------------- zones
def station_sites(inputs: dict, evidence: dict) -> dict:
    """zone id -> {"geometry" (the OSM outline, EPSG:25832), "way", "ags", "source_url", "note"} of the three lots, in release
    order. The tags of the way must be those the specification expects (name, amenity=parking, fee=yes, parking=surface); the
    outline is matched by name and position and not surveyed. The geometry is always a polygon, never a fallback point."""
    sites = {}
    for station in STATIONS:
        way = _way(inputs, station["way"])
        tags = way["tags"]
        expected = {"name": station["osm_name"], "amenity": "parking", "fee": "yes", "parking": "surface"}
        wrong = {key: tags.get(key) for key, value in expected.items() if tags.get(key) != value}
        if wrong:
            raise SystemExit(f"OSM way {station['way']}: the tags {wrong} differ from the expected {expected}; the way is not "
                             f"the lot {station['label']}")
        outline = way["geometry"]
        sites[station["zone_id"]] = {
            "geometry": outline, "kind": "polygon", "way": station["way"], "ags": BS_AGS, "layer": OSM_FILE,
            "key": f"way {station['way']}", "source_url": evidence[station["zone_id"]]["source_url"],
            "note": _ascii(
                f"Single paid site (spec Amendment G1, owner decision 2026-10-08): the DB BahnPark car park {station['label']} "
                f"(Braunschweig Hauptbahnhof), operated by Contipark, a surface car park open around the clock. The outline is "
                f"OSM way {station['way']} ('{tags['name']}', amenity=parking, fee=yes, parking=surface; Overpass response "
                f"{OSM_FILE}, SHA-256 {inputs['osm_file']['sha256']}, OSM base {inputs['osm_file']['osm_base']}), matched by "
                f"name and position and not surveyed, no official fee boundary; outline {outline.area:.0f} m2. Tariff: the "
                "Contipark page of the lot (tariff row). The zone is the area within "
                f"{rz.SITE_BUFFER_M:.0f} m of the outline (ASSUMPTION C-a: the access walk from the destination to the paid "
                "site), cut out of the zones that take precedence (the BgA car parks, whose lot Willy-Brandt-Platz is the "
                "separate 'Post' lot, OSM way 26163572, bordering P1), simplified 0.5 m and cut with a 0.05 m clearance. Free "
                "street parking west of the station stays unzoned (ASSUMPTION Z1). " + OSM_PROVENANCE + ".")}
    return sites


def build_station_zones(inputs: dict, release: gpd.GeoDataFrame) -> dict:
    """The zone records of the three lots appended to the finished ``release`` (EPSG:25832, the committed zones), and the
    context for ``qa_rows``: {"zones" (the release records plus the three new ones), "context"}. The lots are split by the nearer
    source where their 50 m areas overlap (``regional_zones.split_single_sites``), simplified and cut like every rule-based zone
    (``assemble_parking_zones.cut_rule_zone``) against the committed polygons of the zones that precede them in
    ``assemble_parking_zones.PRECEDENCE_REGIONAL`` (the TU campuses, the BgA car parks and the other single sites), then cut once
    more by the exact polygons of those winners. A committed zone that the lots overlap (above ``OVERLAP_TOLERANCE_M2``) stops the
    step: this step never changes another zone. Prints the geometry rate (all polygons) and the cuts."""
    if release.crs is None or release.crs.to_epsg() != 25832:
        raise SystemExit(f"the release must be in EPSG:25832, found {release.crs}")
    clash = [zone_id for zone_id in STATION_ZONE_IDS if zone_id in set(release["zone_id"])]
    if clash:
        raise SystemExit(f"the release has the zones {clash} already; the step appends and never replaces")
    evidence = station_evidence(inputs)
    sites = station_sites(inputs, evidence)
    ranking = sorted(sites, key=lambda zone_id: (-evidence[zone_id]["hourly_rate_eur"], zone_id))
    split = rz.split_single_sites({zone_id: site["geometry"] for zone_id, site in sites.items()}, ranking)
    committed = dict(zip(release["zone_id"].astype(str), release.geometry))
    order = ap.precedence_order([{"zone_id": zone_id} for zone_id in list(committed) + list(STATION_ZONE_IDS)],
                                ap.PRECEDENCE_REGIONAL)
    first = min(order.index(zone_id) for zone_id in STATION_ZONE_IDS)
    winners = [zone_id for zone_id in order[:first] if zone_id in committed]
    losers = [zone_id for zone_id in order[first:] if zone_id in committed]
    taken = unary_union([committed[zone_id] for zone_id in winners])
    final, cuts, before = {}, [], {}
    for zone_id in [zone_id for zone_id in order if zone_id in STATION_ZONE_IDS]:
        geometry = split["zones"][zone_id]
        before[zone_id] = geometry
        geometry, _ = ap.cut_rule_zone(geometry, taken, ap.MINIMUM_PART_M2)
        for winner in winners:
            overlap = float(geometry.intersection(committed[winner]).area)
            if overlap > ap.EXACT_CUT_EPSILON_M2:
                geometry = cc.largest_parts(geometry.difference(committed[winner]).buffer(0), ap.MINIMUM_PART_M2)
        for winner in winners:
            cut = float(before[zone_id].intersection(committed[winner]).area)
            if cut > OVERLAP_TOLERANCE_M2:
                cuts.append((zone_id, winner, cut))
        if geometry.is_empty:
            raise SystemExit(f"{zone_id}: the zone is empty after the precedence cuts")
        final[zone_id] = geometry
        taken = unary_union([taken, geometry])
    stations_union = unary_union(list(final.values()))
    touched = [(zone_id, float(committed[zone_id].intersection(stations_union).area)) for zone_id in losers
               if committed[zone_id].intersection(stations_union).area > OVERLAP_TOLERANCE_M2]
    if touched:
        raise SystemExit("the station zones overlap committed zone(s) that would have to be cut: "
                         + ", ".join(f"{zone_id} {area:.1f} m2" for zone_id, area in touched)
                         + "; this step never changes another zone, so the full zone chain must be re-run with the lots")
    records = [ap.zone_record(zone_id, BS_AGS, final[zone_id], pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE,
                              sites[zone_id]["source_url"], sites[zone_id]["note"], source_date=STATION_DATE,
                              digitised_on=STATION_DATE, site_buffer_m=rz.SITE_BUFFER_M) for zone_id in final]
    print(f"station zones: geometry polygon {len(sites)}/{len(sites)} (100.0 %, the OSM outlines of the lots; no fallback point); "
          f"cuts (loser <- winner, m2): " + ("; ".join(f"{loser} <- {winner} {area:.1f}" for loser, winner, area in cuts) or "none")
          + "; committed zones changed: 0")
    context = {"evidence": evidence, "sites": sites, "split": split, "cuts": cuts, "before": before, "final": final,
               "inputs": inputs, "committed": committed, "winners": winners}
    return {"records": records, "context": context}


#: Overlap, m2, above which a station zone and a committed zone count as overlapping (the tolerance of the zone loader).
OVERLAP_TOLERANCE_M2 = 0.5


def read_release(zones_path) -> gpd.GeoDataFrame:
    """The finished zone release as written (EPSG:25832): the two date columns stay the text of the file (the reader would turn
    them into timestamps and the written file would differ from the committed one in every feature)."""
    release = gpd.read_file(zones_path)
    for column in ("source_date", "digitised_on"):
        if pd.api.types.is_datetime64_any_dtype(release[column]):
            release[column] = release[column].dt.strftime("%Y-%m-%d")
    return release.to_crs(cc.METRIC_CRS)


def append_zones(inputs: dict, zones_path, out_path) -> dict:
    """Append the three station zones to the finished zone release at ``zones_path`` and write ``out_path`` (the same layout and
    licence members as ``assemble_parking_zones.write_zone_file``, the licence wording extended by the station sources). Every other
    zone is carried over unchanged (the file is read back and compared). Returns the build result of ``build_station_zones``."""
    release = read_release(zones_path)
    built = build_station_zones(inputs, release)
    new_frame = ap.zone_frame(built["records"]).reindex(columns=list(release.columns))
    combined = gpd.GeoDataFrame(pd.concat([release, new_frame], ignore_index=True), geometry="geometry", crs=cc.METRIC_CRS)
    ap.write_zone_file(combined, out_path, municipal=True, regional=True, station=True)
    built["context"]["release"] = pz.load_zone_polygons(out_path, max_repairs=0)
    return built


# ---------------------------------------------------------------- QA table
def qa_rows(context: dict, release: gpd.GeoDataFrame) -> list:
    """The rows of the municipal QA table for the station zones (``release``: the written zone file in EPSG:25832): per lot the
    release polygon against the 50 m area of its outline (after the split) and against the outline itself, every precedence cut
    (row ``<loser>_cut_by_<winner>``), and the cross-check of the BgA polygon of Willy-Brandt-Platz against the OSM 'Post' way."""
    polygons = dict(zip(release["zone_id"].astype(str), release.geometry))
    rows = []
    for zone_id, site in context["sites"].items():
        rows.append(mz._row(f"{zone_id}_release_vs_50m_area", BS_AGS, f"release polygon {zone_id}",
                            f"the area within {rz.SITE_BUFFER_M:.0f} m of its source polygon (OSM way {site['way']}, "
                            f"{OSM_FILE})", polygons[zone_id], mz._features(context["split"]["zones"][zone_id]),
                            release_zone_id=zone_id, note=(
                                f"G1/D3: reference_share_in_subject is the share of the {rz.SITE_BUFFER_M:.0f} m area the zone "
                                "keeps after the nearest-source split, the 0.5 m simplification, the 0.05 m cut clearance and "
                                "the cuts of zones that take precedence")))
        rows.append(mz._row(f"{zone_id}_release_vs_osm_outline", BS_AGS, f"release polygon {zone_id}",
                            f"the OSM outline of the lot ({site['key']}, {OSM_FILE})", polygons[zone_id],
                            mz._features(site["geometry"]), release_zone_id=zone_id, note=(
                                "G1: the lot lies in its zone, so reference_share_in_subject is 1 up to the 0.5 m "
                                "simplification, and subject_share_in_reference is the part of the zone that is the lot itself")))
    for loser, winner, area in context["cuts"]:
        rows.append(mz._row(f"{loser}_cut_by_{winner}", BS_AGS, f"{loser} before the precedence cuts",
                            f"release polygon {winner}", context["before"][loser], mz._features(polygons[winner]), note=(
                                f"R-4a-1: {winner} takes precedence over {loser} where they overlap; the overlap "
                                f"({area:.1f} m2 measured against the final polygon) is cut from {loser}")))
    post = _way(context["inputs"], POST_WAY)
    if BGA_NEIGHBOUR_ZONE in polygons:
        rows.append(mz._row(f"{BGA_NEIGHBOUR_ZONE}_vs_osm_post_lot", BS_AGS, f"release polygon {BGA_NEIGHBOUR_ZONE}",
                            f"the OSM outline of the lot 'Post' (way {POST_WAY}, {OSM_FILE})", polygons[BGA_NEIGHBOUR_ZONE],
                            mz._features(post["geometry"]), release_zone_id=BGA_NEIGHBOUR_ZONE, note=(
                                "G1 cross-check: the digitised BgA lot (annex 6 of the Amtsblatt 2022 Nr. 16, uncertainty about "
                                "5 m) and the OSM lot 'Post' are the same lot, which borders the station car park P1 and is "
                                "NOT part of it; P1 is cut against the BgA polygon, which is the release's lot")))
    return rows


QA_INTRO_SUFFIX = (
    "Spec Amendment G (the station car parks of Braunschweig Hbf, scripts/curation/parking_zones_2026/station_lots.py, run "
    "after assemble_parking_zones.py; the package Braunschweig_Monatstarife_2026-10-08.zip and the Overpass response "
    + OSM_FILE + " under raw_sources/municipal_2026-10-08/, gitignored, SHA-256 in the data record parking_zones_2026): "
    "G1: every station zone is compared with the area within 50 m (ASSUMPTION C-a) of its OSM outline and with the outline "
    "itself; the BgA polygon of Willy-Brandt-Platz is compared with the OSM lot 'Post' that borders P1; every precedence cut "
    "that involves a station zone is a row <loser>_cut_by_<winner>. Source of the outlines: " + OSM_PROVENANCE + ".")


def qa_intro(intro: str) -> str:
    """``intro`` (the municipal intro as extended by the regional step) with ``QA_INTRO_SUFFIX`` inserted before the units
    sentence, as ``regional_zones.qa_intro`` does."""
    head, separator, tail = intro.partition(rz._QA_UNITS_SENTENCE)
    if not separator:
        raise SystemExit("the QA intro no longer holds the units sentence the station paragraph precedes")
    return head + " " + QA_INTRO_SUFFIX + separator + tail


def append_qa_rows(qa_path, rows: list) -> None:
    """Append ``rows`` to the committed municipal QA table at ``qa_path`` (header with the station paragraph) and write it
    back; ``SystemExit`` when a row id exists already."""
    from braunschweig.parking import municipal_zone_qa as mq

    table = mq.load_municipal_qa(qa_path)
    clash = sorted(set(table["row_id"]) & {row["row_id"] for row in rows})
    if clash:
        raise SystemExit(f"{qa_path}: the QA rows {clash} exist already; the step appends and never rewrites")
    existing = table.to_dict("records")
    intro = qa_intro(rz.qa_intro(mz.QA_INTRO))
    mz.write_qa_table(qa_path, existing + rows, intro)


# ---------------------------------------------------------------- command line
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--station-dir", required=True, help="raw_sources/municipal_2026-10-08 with the package zip and the "
                                                             "Overpass response of the station lots")
    parser.add_argument("--zones", required=True, help="the finished zone release parking_zones_2026.geojson (read)")
    parser.add_argument("--zones-out", required=True, help="the zone release with the station zones to write")
    parser.add_argument("--tariffs", required=True, help="parking_tariffs_2026.csv: the three station rows are appended "
                                                         "(and checked against the evidence)")
    parser.add_argument("--municipal-qa", required=True, help="parking_zones_2026_municipal_qa.csv: the station rows are appended")
    args = parser.parse_args(argv)
    if Path(args.zones_out).name != ZONES_FILE_NAME:
        raise SystemExit(f"--zones-out must be named {ZONES_FILE_NAME}: the 'name' member of the GeoJSON follows the file name, so "
                         f"another name writes another file than the release (got {Path(args.zones_out).name})")
    directory = Path(args.station_dir)
    inputs = load_station_inputs(directory / PACKAGE_FILE, directory / OSM_FILE)
    built = append_zones(inputs, args.zones, args.zones_out)
    append_tariff_rows(args.tariffs, tariff_rows(built["context"]["evidence"], inputs))
    check_tariff_rows(built["context"]["evidence"], pz.load_tariffs(args.tariffs))
    append_qa_rows(args.municipal_qa, qa_rows(built["context"], built["context"]["release"]))
    print("station zones done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
