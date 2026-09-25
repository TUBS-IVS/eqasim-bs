import hashlib
import json

import pytest

from braunschweig.data.vrb import fare_model_export as fme


def test_model_from_committed_inputs_has_the_documented_shape():
    model = fme.build_fare_model(snapshot_date="2026-06-20", assumptions=fme.DEFAULT_ASSUMPTIONS)
    assert model["schema_version"] == 2 and model["money_price_year"] == 2026
    assert len(model["zones"]) == 48 and model["city_zones"] == ["20", "40", "80"]
    assert model["single_adult_cents"] == {"city": 360, "ps1": 390, "ps2": 560, "ps3": 770, "ps4": 1230}
    assert model["single_child_cents"]["ps4"] == 740 and model["short_trip_cents"] == 200
    assert model["day_ticket_cents"] == {"city": 720, "ps1": 780, "ps2": 1120, "ps3": 1540, "ps4": 2460}
    assert model["validity_minutes"] == {"city": 90, "ps1": 90, "ps2": 90, "ps3": 120, "ps4": 150}
    assert len(model["price_class_by_pair"]) == 2302
    assert model["price_class_by_pair"]["40|70"] == "ps2" and model["price_class_by_pair"]["40|40"] == "city"
    assert model["price_class_by_pair"]["70|70"] == "ps1"
    assert model["category_holder"]["deutschlandticket"] == "national_flat"
    assert model["category_holder"]["monthly_or_annual_subscription"] == "vrb_flat"
    assert model["category_holder"]["never_pt"] == "none" and len(model["category_holder"]) == 9
    assert len(model["external"]["rail_distance_bands_adult"]) == 500
    assert model["external"]["rail_distance_bands_adult"][0]["up_to_km"] > 0
    assert model["external"]["distance_factor"] == 1.0 and model["external"]["local_single_cents"] == 370
    assert model["fallback"]["unsupported_ride_cents"] == 370
    # DB Sparpreis entry price 2026 for every long-distance ride (maintainer decision 2026-09-24).
    assert model["long_distance"] == {"single_cents": 2190}
    assert any("21.90" in text and "long-distance" in text for text in model["assumptions"])
    assert any("2022" in text for text in model["assumptions"])


def test_category_holder_covers_exactly_the_population_ticket_categories():
    from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES
    model = fme.build_fare_model(snapshot_date="2026-06-20", assumptions=fme.DEFAULT_ASSUMPTIONS)
    assert set(model["category_holder"]) == set(PT_TICKET_CATEGORIES)


def test_source_paths_are_platform_independent():
    model = fme.build_fare_model(snapshot_date="2026-06-20", assumptions=fme.DEFAULT_ASSUMPTIONS)
    paths = [source["path"] for source in model["sources"] if "path" in source]
    assert paths and all("\\" not in path for path in paths)


def test_assumption_parameters_are_validated():
    bad = dict(fme.DEFAULT_ASSUMPTIONS, external_local_single_cents=-1)
    with pytest.raises(ValueError, match="external_local_single_cents"):
        fme.build_fare_model(snapshot_date="2026-06-20", assumptions=bad)
    with pytest.raises(ValueError, match="long_distance_single_cents"):
        fme.build_fare_model(snapshot_date="2026-06-20",
                             assumptions=dict(fme.DEFAULT_ASSUMPTIONS, long_distance_single_cents=21.9))
    with pytest.raises(ValueError, match="rail_distance_factor"):
        fme.build_fare_model(snapshot_date="2026-06-20",
                             assumptions=dict(fme.DEFAULT_ASSUMPTIONS, rail_distance_factor=0))


def test_unknown_snapshot_fails_instead_of_mixing_years():
    with pytest.raises(ValueError, match="no prices for snapshot 2031-01-01"):
        fme.build_fare_model(snapshot_date="2031-01-01", assumptions=fme.DEFAULT_ASSUMPTIONS)


def test_write_is_deterministic(tmp_path):
    model = fme.build_fare_model(snapshot_date="2026-06-20", assumptions=fme.DEFAULT_ASSUMPTIONS)
    a = fme.write_fare_model(tmp_path / "a.json", model).read_bytes()
    b = fme.write_fare_model(tmp_path / "b.json", model).read_bytes()
    assert a == b and json.loads(a)["schema_version"] == 2


def test_sources_record_the_content_hash_of_every_committed_input():
    # A cached fare model must be recognisable as stale: each source carries the sha256 of the file it was built from.
    model = fme.build_fare_model(snapshot_date="2026-06-20", assumptions=fme.DEFAULT_ASSUMPTIONS)
    by_path = {source["path"]: source["sha256"] for source in model["sources"]}
    for path in fme.committed_input_paths():
        committed = path.read_bytes().replace(b"\r\n", b"\n")
        assert by_path[path.relative_to(fme.REPO).as_posix()] == hashlib.sha256(committed).hexdigest()


def test_content_hash_ignores_the_checkout_line_endings(tmp_path):
    lf, crlf = tmp_path / "lf.csv", tmp_path / "crlf.csv"
    lf.write_bytes(b"a,b\n1,2\n")
    crlf.write_bytes(b"a,b\r\n1,2\r\n")
    assert fme.content_sha256(lf) == fme.content_sha256(crlf) == hashlib.sha256(b"a,b\n1,2\n").hexdigest()

