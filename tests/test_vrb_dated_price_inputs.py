"""Pins the 2026-06-20 VRB price facts consumed by braunschweig.data.vrb.fare_model_export."""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CLASSES = ("city", "ps1", "ps2", "ps3", "ps4")


def prices_2026():
    data = json.loads((REPO / "docs/data/vrb-dated-price-inputs.json").read_text(encoding="utf-8"))
    table = {}
    for snapshot in data["snapshots"]:
        for row in snapshot["prices"]:
            if row["snapshot_date"] == "2026-06-20":
                table[(row["product_id"], row["price_field"].replace("prices_cent.", ""))] = row["amount_minor"]
    return table


def test_single_short_and_day_prices_2026_are_complete_and_positive():
    table = prices_2026()
    assert [table[("single_adult", c)] for c in CLASSES] == [360, 390, 560, 770, 1230]
    assert [table[("single_child_age_6_to_14", c)] for c in CLASSES] == [210, 230, 330, 460, 740]
    assert [table[("day_ticket", f"party_size_1.{c}")] for c in CLASSES] == [720, 780, 1120, 1540, 2460]
    assert {table[("short_trip", c)] for c in CLASSES} == {200}


def test_rail_single_fare_tables_have_the_documented_extent():
    data = json.loads((REPO / "docs/data/external-regional-rail-single-fares-2026.json").read_text(encoding="utf-8"))
    tariffs = {t["tariff_id"]: t for t in data["tariffs"]}
    assert len(tariffs["niedersachsentarif"]["tiers"]) == 500
    assert len(tariffs["deutschlandtarif"]["tiers"]) == 2000
    first = tariffs["niedersachsentarif"]["tiers"][0]
    assert set(first) >= {"up_to_km", "adult_single_cents", "child_single_cents"}
    assert data["price_unit"] == "cent" and data["distance_basis"] == "published_tariff_distance_km"
