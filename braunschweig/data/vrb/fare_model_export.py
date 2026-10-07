"""Assemble the compact VRB fare model consumed by the Java VrbZoneFareCostModel.

Every number comes from a committed file (prices, matrix, rail bands) or from an explicitly
named ASSUMPTION parameter; nothing is hard-coded in Java. The JSON contract is pinned on
both sides (tests/test_vrb_fare_model_export.py and the Java VrbFareModelTest). Money is in
euro cents, distances in kilometres, validity in minutes.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from typing import Mapping

REPO = Path(__file__).resolve().parents[3]
PRICE_INPUTS = REPO / "docs/data/vrb-dated-price-inputs.json"
MATRIX_CSV = REPO / "docs/data/vrb-price-stage-matrix-2022.csv"
RAIL_TABLE = REPO / "docs/data/external-regional-rail-single-fares-2026.json"

PRICE_CLASSES = ("city", "ps1", "ps2", "ps3", "ps4")
STAGE_TO_CLASS = {"ST": "city", "1": "ps1", "2": "ps2", "3": "ps3", "4": "ps4"}
CITY_ZONES = ("20", "40", "80")
#: VRB Tarifbestimmungen 01.01.2026 section 3.2: validity of single tickets per price class.
VALIDITY_MINUTES = {"city": 90, "ps1": 90, "ps2": 90, "ps3": 120, "ps4": 150}
#: Section 3.2: short trip = up to the 3rd stop after boarding, no transfer, not on trains.
SHORT_TRIP_MAXIMUM_STOP_INTERVALS = 3
#: ADR-0133 D5. Keys are the population's pt_subscription_type values.
CATEGORY_HOLDER = {
    "deutschlandticket": "national_flat", "job_or_semester_ticket": "national_flat",
    "monthly_or_annual_subscription": "vrb_flat", "weekly_monthly_no_subscription": "vrb_flat",
    "single_ticket": "none", "multi_ride_ticket": "none", "other_ticket": "none", "no_answer": "none",
    "never_pt": "none",
}
#: ASSUMPTIONS (ADR-0133 D7/D8); all configurable through the pipeline config.
DEFAULT_ASSUMPTIONS = {
    # GVH one-zone single 2026 (hannover.de, 2025-12), applied to every external local operator.
    "external_local_single_cents": 370,
    # Ridden stop-to-stop distance -> published tariff distance; uncalibrated.
    "rail_distance_factor": 1.0,
    # Price of a counted fallback outcome; the fare model JSON is its only home (the Java side reads it there).
    "unsupported_fallback_cents": 370,
    # DB Sparpreis entry price 2026 (21.90 EUR, unchanged at the December 2025 timetable change) for every
    # journey with a long-distance ride (DB Fernverkehr, Flix), whatever the ticket: no Germany-wide or VRB
    # pass is valid there (maintainer decision 2026-09-24, ADR-0133 D6). An advertised minimum, not a mean.
    "long_distance_single_cents": 2190,
    # VRB Tarifbestimmungen 2026 section 2.3: child fares for ages 6 to 14 inclusive.
    "child_minimum_age": 6,
    "child_maximum_age": 14,
}


def committed_input_paths() -> tuple[Path, Path, Path]:
    """The committed tables the fare model is built from, resolved at call time.

    ``braunschweig.matsim.simulation.prepare.validate`` hashes their bytes, so a corrected table
    invalidates a cached prepared scenario; ``build_fare_model`` records the same hashes in ``sources``.
    """
    return PRICE_INPUTS, MATRIX_CSV, RAIL_TABLE


def content_sha256(path) -> str:
    """sha256 of a committed text file as git stores it (LF line endings).

    A Windows checkout with core.autocrlf holds CRLF; hashing the LF-normalised bytes keeps the
    recorded provenance identical on every platform and equal to the committed content.
    """
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _file_source(source_id: str, path: Path) -> dict:
    try:
        shown = path.relative_to(REPO).as_posix()
    except ValueError:
        shown = path.as_posix()
    return {"source_id": source_id, "path": shown, "sha256": content_sha256(path)}


def load_prices(snapshot_date: str, path=None) -> dict[tuple[str, str], int]:
    """(product_id, price_field) -> cents for one tariff snapshot; null source cells are skipped."""
    path = PRICE_INPUTS if path is None else path
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    table = {}
    for snapshot in data["snapshots"]:
        for row in snapshot["prices"]:
            if row["snapshot_date"] == snapshot_date and row["amount_minor"] is not None:
                table[(row["product_id"], row["price_field"].replace("prices_cent.", ""))] = int(row["amount_minor"])
    if not table:
        raise ValueError(f"no prices for snapshot {snapshot_date} in {path}")
    return table


def load_price_stage_matrix(path=None) -> tuple[list[str], dict[str, str]]:
    """Sorted zone ids and 'origin|destination' -> price class for every defined matrix cell."""
    path = MATRIX_CSV if path is None else path
    zones, classes = set(), {}
    with Path(path).open(encoding="utf-8", newline="") as stream:
        rows = [line for line in stream if not line.startswith("#")]
    for row in csv.DictReader(rows):
        zones.update((row["origin_zone"], row["destination_zone"]))
        if row["status"] == "defined":
            classes[f"{row['origin_zone']}|{row['destination_zone']}"] = STAGE_TO_CLASS[row["price_stage"]]
    return sorted(zones, key=int), classes


def load_rail_bands(path=None, tariff_id="niedersachsentarif") -> tuple[list[dict], list[dict], dict]:
    """Adult and child single-fare bands of one tariff plus its source identity (committed file and document)."""
    path = Path(RAIL_TABLE if path is None else path)
    data = json.loads(path.read_text(encoding="utf-8"))
    tariff = next(t for t in data["tariffs"] if t["tariff_id"] == tariff_id)
    adult = [{"up_to_km": t["up_to_km"], "price_cents": int(t["adult_single_cents"])} for t in tariff["tiers"]]
    child = [{"up_to_km": t["up_to_km"], "price_cents": int(t["child_single_cents"])} for t in tariff["tiers"]]
    source = _file_source(f"{tariff_id}_single_table", path)
    source.update({"url": tariff["source"]["url"], "source_document_sha256": tariff["source"].get("sha256"),
                   "valid_from": tariff["valid_from"]})
    return adult, child, source


def _check_assumptions(assumptions: Mapping) -> dict:
    values = dict(DEFAULT_ASSUMPTIONS, **assumptions)
    for key in ("external_local_single_cents", "unsupported_fallback_cents", "long_distance_single_cents"):
        if type(values[key]) is not int or values[key] < 0:
            raise ValueError(f"{key} must be a nonnegative integer number of cents, got {values[key]!r}")
    if not (float(values["rail_distance_factor"]) > 0):
        raise ValueError("rail_distance_factor must be positive")
    if not (0 <= values["child_minimum_age"] <= values["child_maximum_age"] <= 130):
        raise ValueError("child age bounds must satisfy 0 <= minimum <= maximum <= 130")
    return values


def build_fare_model(*, snapshot_date: str, assumptions: Mapping) -> dict:
    values = _check_assumptions(assumptions)
    prices = load_prices(snapshot_date)
    zones, price_class_by_pair = load_price_stage_matrix()
    adult_bands, child_bands, rail_source = load_rail_bands()
    short = {prices[("short_trip", c)] for c in PRICE_CLASSES}
    if len(short) != 1:
        raise ValueError(f"short-trip price differs by class in the source: {sorted(short)}")
    return {
        "schema_version": 2, "tariff_snapshot_date": snapshot_date, "money_price_year": int(snapshot_date[:4]),
        "currency": "EUR", "zones": zones, "city_zones": list(CITY_ZONES), "price_classes": list(PRICE_CLASSES),
        "single_adult_cents": {c: prices[("single_adult", c)] for c in PRICE_CLASSES},
        "single_child_cents": {c: prices[("single_child_age_6_to_14", c)] for c in PRICE_CLASSES},
        "short_trip_cents": short.pop(), "short_trip_maximum_stop_intervals": SHORT_TRIP_MAXIMUM_STOP_INTERVALS,
        "day_ticket_cents": {c: prices[("day_ticket", f"party_size_1.{c}")] for c in PRICE_CLASSES},
        "validity_minutes": dict(VALIDITY_MINUTES), "price_class_by_pair": price_class_by_pair,
        "category_holder": dict(CATEGORY_HOLDER),
        "child_minimum_age": values["child_minimum_age"], "child_maximum_age": values["child_maximum_age"],
        "external": {"rail_distance_bands_adult": adult_bands, "rail_distance_bands_child": child_bands,
                     "distance_factor": float(values["rail_distance_factor"]),
                     "local_single_cents": values["external_local_single_cents"]},
        "long_distance": {"single_cents": values["long_distance_single_cents"]},
        "fallback": {"unsupported_ride_cents": values["unsupported_fallback_cents"]},
        # Content hashes make a cached model recognisable as stale when a committed table is corrected.
        "sources": [_file_source("vrb_prices_2026", PRICE_INPUTS), _file_source("vrb_matrix_2022", MATRIX_CSV),
                    rail_source],
        "assumptions": [
            "ASSUMPTION: the VRB price-stage matrix printed 01.01.2022 is applied to the 2026 scenario (ADR-0133 D3).",
            "ASSUMPTION: ticket validity windows are not modelled; every PT trip is priced on its own (D4).",
            "ASSUMPTION: job/semester tickets count as Germany-wide flat; monthly and weekly passes as VRB-network flat (D5).",
            f"ASSUMPTION: external local single {values['external_local_single_cents']} ct for every non-VRB bus/tram ride (D7).",
            f"ASSUMPTION: rail tariff distance = ridden stop distance x {values['rail_distance_factor']} (D7).",
            f"ASSUMPTION: every journey with a long-distance ride costs {values['long_distance_single_cents'] / 100:.2f} "
            "EUR for every traveller of 6 or older, the DB Sparpreis entry price 2026 (D6).",
        ],
    }


def write_fare_model(path, model: Mapping) -> Path:
    path = Path(path)
    path.write_text(json.dumps(model, indent=2, sort_keys=True, ensure_ascii=True) + "\n", encoding="utf-8")
    return path
