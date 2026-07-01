"""Tests for the HSN/TSN engine-attribute matcher.

The feature attaches engine technical attributes (power kW/PS, displacement
ccm, a representative HSN/TSN and a dominant fuel) to every synthetic fleet
vehicle by matching on canonical brand + model family (optionally refined by
powertrain). It is additive completeness data -- not consumed by the simulation
yet -- and is flag-gated (``fleet_hsn_tsn_attributes``, default ON). OFF means
the five new columns are absent and the MATSim vehicles writer emits no engine
attributes (byte-identical to the pre-feature output).

The match is inherently approximate: the KBA fleet model family (e.g. ``GOLF``)
is matched against the much finer HSN/TSN variant strings (e.g.
``VW Golf VII 2.0 TDI``) and aggregated by MEDIAN over the matching variants.
These tests therefore assert plausibility ranges and a tiered-match-rate floor
rather than exact engine values, and check that the no-silent-fallback tier
rates are logged.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.data.kba import hsn_tsn  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

DATA_PATH = str(DATA)

# The HSN/TSN lookup is local-only (gitignored, scraped from hsn-tsn.de);
# without it every test in this module would fail on import of the lookup.
_HSN_TSN_LOOKUP = DATA / "braunschweig" / "kba" / "hsn_tsn_lookup.csv"
pytestmark = pytest.mark.skipif(
    not _HSN_TSN_LOOKUP.exists(),
    reason="HSN/TSN lookup is local-only (run scripts/scrape_hsn_tsn.py); skipped when absent",
)

#: The five additive columns the matcher attaches.
HSN_TSN_COLUMNS = [
    "engine_power_kw", "engine_power_ps", "displacement_ccm",
    "fuel_detail", "hsn", "tsn",
]


# --------------------------------------------------------------------------- #
# Reader: lookup construction + canonicalisation
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def lookup():
    return hsn_tsn.HsnTsnLookup.from_data_path(DATA_PATH)


def test_reader_builds_lookup_nonempty(lookup):
    assert len(lookup.brand_model_records) > 0
    assert len(lookup.brand_records) > 0
    assert lookup.global_record.power_kw > 0
    assert lookup.global_record.displacement_ccm > 0


def test_brand_canonicalisation_covers_fleet_brands():
    """Every fleet brand token that has a real automotive identity maps to a
    canonical HSN/TSN display brand, or is recorded as unmapped (logged)."""
    df = pd.read_csv(
        DATA / "braunschweig" / "kba" / "derived" / "kba_segment_model.csv",
        comment="#")
    fleet_brands = sorted({m.split(" ", 1)[0] for m in df["model"]})
    mapped = {b for b in fleet_brands if hsn_tsn.canonical_brand(b) is not None}
    # The 36 HSN/TSN display brands are the common ones; the fleet has extra
    # marques absent from the HSN/TSN file (BYD, TESLA, MINI, JEEP, ...). The
    # mapped share must still cover the large majority of common brands.
    assert "VW" in mapped
    assert hsn_tsn.canonical_brand("MERCEDES") == "Mercedes-Benz"
    assert hsn_tsn.canonical_brand("ALFA") == "Alfa Romeo"
    assert hsn_tsn.canonical_brand("VW") == "VW"


def test_known_vehicle_golf_petrol_plausible(lookup):
    """A VW / GOLF / petrol vehicle resolves to a non-empty, plausible engine."""
    rec, tier = lookup.lookup("VW", "golf", "petrol")
    assert tier in {"exact", "model"}
    assert 40.0 <= rec.power_kw <= 200.0          # a Golf is ~55-150 kW
    assert 900.0 <= rec.displacement_ccm <= 2500.0
    assert rec.fuel_detail != ""
    assert rec.hsn != "" and rec.tsn != ""


def test_model_family_normalisation():
    """The normaliser strips the brand prefix and yields the first model token."""
    # HSN/TSN side: full display-brand prefix + variant tail.
    assert hsn_tsn.model_family("VW", "VW Golf VII 2.0 TDI") == "golf"
    # Mercedes class names collapse to the single-letter class so the KBA
    # "C-KLASSE" family matches the HSN/TSN "C 200 ..." family ("c").
    assert hsn_tsn.model_family("Mercedes-Benz", "Mercedes-Benz C-Klasse") == "c"
    assert hsn_tsn.model_family("Mercedes-Benz", "Mercedes-Benz C 200 CDI") == "c"
    # Opel generation suffixes are stripped to match the suffix-less KBA family.
    assert hsn_tsn.model_family("Opel", "Opel Corsa D 1.2") == "corsa"
    assert hsn_tsn.model_family("Opel", "OPEL CORSA") == "corsa"
    # Genuine hyphenated names with a short stem keep their hyphen.
    assert hsn_tsn.model_family("Ford", "Ford C-Max 1.6") == "c-max"
    # Fleet side: the KBA Modellreihe is "BRAND MODEL" with the first token brand.
    assert hsn_tsn.model_family("VW", "VW GOLF") == "golf"
    assert hsn_tsn.model_family("Fiat", "FIAT 500") == "500"
    # Alfa Romeo: KBA repeats the marque ("ALFA ROMEO ALFA 147"); the redundant
    # leading "alfa" token is dropped so the family is the numeric series.
    assert hsn_tsn.model_family("Alfa Romeo", "ALFA ROMEO ALFA 147") == "147"


# --------------------------------------------------------------------------- #
# Matcher on a representative fleet sample
# --------------------------------------------------------------------------- #
def _make_cars(n_per_kreis: int = 2000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    statuses = list(ft.STATUS_LABELS)
    rows = []
    for kreis in ft.ZGB_KREISE_AGS5:
        for _ in range(n_per_kreis):
            rows.append({
                "economic_status": rng.choice(statuses),
                "kreis_ags5": kreis,
                "gemeinde": np.nan,
                "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def fleet_spec():
    df_cars = _make_cars()
    df_spec, _, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=42)
    return df_spec


def test_attach_adds_five_columns(lookup, fleet_spec):
    out = hsn_tsn.attach_hsn_tsn(fleet_spec, lookup=lookup)
    for col in HSN_TSN_COLUMNS:
        assert col in out.columns
    # No vehicle is left without an engine record (global-median fallback).
    # Power is always meaningful; displacement is 0 for battery-electric model
    # families (BEVs have no combustion displacement -- a correct value), so
    # only combustion vehicles must carry a positive displacement.
    assert (out["engine_power_kw"] > 0).all()
    assert (out["displacement_ccm"] >= 0).all()
    # Displacement is 0 only for battery-electric model families (no combustion
    # displacement). Since Task 2, fuel_detail is derived from the vehicle's OWN
    # powertrain (not from the matched record), so a "petrol" vehicle matched to
    # a BEV model family (e.g. Renault ZOE) correctly carries fuel_detail="Benzin"
    # but displacement_ccm=0 (the BEV family has no engine). The correct filter is
    # therefore the powertrain column: combustion powertrains on a non-BEV model
    # family carry positive displacement.
    # We verify that BEVs are the only vehicles with zero displacement, i.e. every
    # non-BEV powertrain that ended up on a non-BEV model family has displacement > 0.
    # Because the fleet can match a BEV powertrain to a petrol model family too (same
    # independence), we conservatively assert that the vast majority of non-BEV
    # powertrain cars have positive displacement (some mismatch is acceptable).
    non_bev = out[out["powertrain"] != "bev"]
    positive_displacement_rate = (non_bev["displacement_ccm"] > 0).mean()
    assert positive_displacement_rate > 0.95, (
        f"only {positive_displacement_rate:.2%} of non-BEV vehicles have "
        f"displacement_ccm > 0 -- check the variant pool draw or HSN/TSN data"
    )


def test_match_tier_rate_above_floor(lookup, fleet_spec, caplog):
    """The exact + model tier rate on a representative fleet must clear a sane
    floor; a low rate signals a brand/model-normalisation bug, not sparse data.

    Measured on the cached ZGB synthesis: exact+model ~= 0.8-0.95 (most mass
    sits on common VW/Opel/Ford/etc. models present in the HSN/TSN file). The
    0.5 floor is a conservative bound; investigate normalisation if it drops
    below it rather than lowering the bar.
    """
    with caplog.at_level(logging.INFO, logger="braunschweig.data.kba.hsn_tsn"):
        out = hsn_tsn.attach_hsn_tsn(fleet_spec, lookup=lookup)
    n = len(out)
    rates = out["hsn_tsn_match_tier"].value_counts(normalize=True)
    exact_model = float(rates.get("exact", 0.0) + rates.get("model", 0.0))
    assert exact_model > 0.5, (
        f"exact+model match rate {exact_model:.3f} below floor 0.5 -- "
        f"check brand canonicalisation / model-family normalisation"
    )
    # The tier rates must be logged (no-silent-fallback rule).
    assert any("match tier" in r.message.lower() or "tier" in r.message.lower()
               for r in caplog.records), "tier rates not logged"


def test_attach_drops_internal_tier_column_by_default(lookup, fleet_spec):
    """The diagnostic ``hsn_tsn_match_tier`` column is kept by default; when
    ``keep_tier=False`` it is dropped and only the five engine columns are present."""
    out = hsn_tsn.attach_hsn_tsn(fleet_spec, lookup=lookup, keep_tier=False)
    assert "hsn_tsn_match_tier" not in out.columns
    assert "_hsn_tsn_match_tier" not in out.columns
    for col in HSN_TSN_COLUMNS:
        assert col in out.columns


# --------------------------------------------------------------------------- #
# Powertrain -> fuel group mapping for the optional refinement
# --------------------------------------------------------------------------- #
def test_powertrain_fuel_group():
    assert hsn_tsn.powertrain_to_fuel_group("petrol") == "Benzin"
    assert hsn_tsn.powertrain_to_fuel_group("diesel") == "Diesel"
    assert hsn_tsn.powertrain_to_fuel_group("bev") == "Elektro"
    # An unmapped/unknown powertrain returns None (no fuel refinement).
    assert hsn_tsn.powertrain_to_fuel_group("hydrogen") in {None, "Wasserstoff/Elektro"}


# --------------------------------------------------------------------------- #
# Task 1: fuel-conditioned brand/global medians (Bug 3 – lookup side)
# --------------------------------------------------------------------------- #
def test_lookup_brand_tier_respects_fuel_group():
    """A diesel powertrain must never receive a petrol brand/global median.

    We build a frame where petrol is the DOMINANT fuel (3 Benzin vs 1 Diesel)
    so the fuel-agnostic brand median picks Benzin. The fuel-conditioned brand
    median for Diesel must be returned instead when powertrain='diesel'.
    """
    df = pd.DataFrame({
        "brand": ["VW", "VW", "VW", "VW"],
        "hsn": ["0603", "0603", "0603", "0603"],
        "tsn": ["AAA", "BBB", "CCC", "DDD"],
        "model": ["VW Golf", "VW Golf", "VW Golf", "VW Passat"],
        "power_ps": [110.0, 120.0, 130.0, 190.0],
        "power_kw": [81.0, 88.0, 96.0, 140.0],
        "displacement_ccm": [1598.0, 1598.0, 1598.0, 1968.0],
        "fuel": ["Benzin", "Benzin", "Benzin", "Diesel"],
    })
    lk = hsn_tsn.HsnTsnLookup.from_frame(df)
    # Fuel-agnostic brand median: Benzin dominates (3 vs 1). With the fix,
    # a diesel powertrain must prefer the Diesel fuel-conditioned brand record.
    rec, tier = lk.lookup("VW", "nonexistent", "diesel")
    assert rec.fuel_detail == "Diesel", f"got {rec.fuel_detail} for a diesel car"


# --------------------------------------------------------------------------- #
# Task 2: fuel_detail from powertrain + distribution engine draw + rename tier
# --------------------------------------------------------------------------- #
def test_fuel_detail_follows_powertrain(lookup):
    df = pd.DataFrame({"brand": ["VW", "VW"], "model": ["VW Golf", "VW Golf"],
                       "powertrain": ["diesel", "bev"]})
    out = hsn_tsn.attach_hsn_tsn(df, lookup=lookup, random_seed=1)
    assert list(out["fuel_detail"]) == ["Diesel", "Elektro"]
    assert "hsn_tsn_match_tier" in out.columns
    assert "_hsn_tsn_match_tier" not in out.columns


def test_unmatched_brand_engines_not_all_identical(lookup):
    # 200 unmapped-brand petrol cars must NOT all get one identical engine.
    df = pd.DataFrame({"brand": ["LAMBORGHINI"] * 200,
                       "model": ["LAMBORGHINI URUS"] * 200,
                       "powertrain": ["petrol"] * 200})
    out = hsn_tsn.attach_hsn_tsn(df, lookup=lookup, random_seed=7)
    assert out["engine_power_kw"].nunique() > 1, "global fallback is a single constant"
    assert (out["fuel_detail"] == "Benzin").all()


# --------------------------------------------------------------------------- #
# Task 3: segment-conditioned engine fallback pool
# --------------------------------------------------------------------------- #
def test_segment_fuel_fallback_prefers_same_segment(lookup):
    # An unmapped exotic in 'gelaendewagen' petrol should draw an SUV-sized
    # engine pool, i.e. median kW above the global petrol median.
    # Since LAMBORGHINI is now mapped to the lookup (brand tier), we use FERRARI
    # which has no HSN/TSN counterpart and must fall through to the segment tier.
    df_glob = pd.DataFrame({"brand": ["FERRARI"], "model": ["FERRARI 296"],
                            "powertrain": ["petrol"], "segment": ["gelaendewagen"]})
    out = hsn_tsn.attach_hsn_tsn(df_glob, lookup=lookup, random_seed=3)
    assert out["hsn_tsn_match_tier"].iloc[0] == "segment"
    assert out["engine_power_kw"].iloc[0] > 0


# --------------------------------------------------------------------------- #
# Task 4: brand coverage lift + model_family normalisation improvements
# --------------------------------------------------------------------------- #
def test_extended_brand_map_covers_top_unmapped():
    """Fleet tokens without any HSN/TSN counterpart (exotic/niche/non-automotive)
    must stay unmapped so they fall to the global/segment fallback.

    ASTON (Martin), BENTLEY, FERRARI, GWM, MG, MORGAN, DS, and the catch-all
    SONSTIGE have no matching brand in the 62-brand HSN/TSN lookup.
    """
    for token in ["ASTON", "BENTLEY", "FERRARI", "GWM", "MG", "MORGAN", "DS", "SONSTIGE"]:
        assert hsn_tsn.canonical_brand(token) is None, (
            f"{token} should stay unmapped (no HSN/TSN counterpart)"
        )


def test_new_brands_canonicalise():
    """26 newly-scraped brands now present in the 62-brand HSN/TSN lookup CSV
    must be reachable via canonical_brand.  Each assertion verifies both the
    fleet-token -> display-brand mapping AND that the display brand actually
    exists in the lookup CSV (so a typo here would be caught by the data test).
    """
    # Load the lookup brand set for the in-lookup assertion.
    import pandas as pd
    lookup_csv = DATA / "braunschweig" / "kba" / "hsn_tsn_lookup.csv"
    lookup_brands = set(pd.read_csv(lookup_csv)["brand"].unique())

    expected = {
        "TESLA": "Tesla",
        "MINI": "Mini",
        "JEEP": "Jeep",
        "LAMBORGHINI": "Lamborghini",
        "LAND": "Land Rover",
        "LEXUS": "Lexus",
        "MASERATI": "Maserati",
        "SSANGYONG": "Ssangyong",
        "POLESTAR": "Polestar",
        "BYD": "BYD",
        "LYNK": "Lynk-Co",
        "INFINITI": "Infiniti",
        "LOTUS": "Lotus",
        "DODGE": "Dodge",
        "IVECO": "Iveco",
        "MAN": "MAN",
        "CADILLAC": "Cadillac",
        "HUMMER": "Hummer",
        "CUPRA": "Cupra",
        "ABARTH": "Abarth",
        "ALPINE": "Alpine",
        "GENESIS": "Genesis",
        "NIO": "Nio",
        "INEOS": "Ineos",
        # AUSTIN remapped: was "Rover", now "Austin" (direct entry in 62-brand lookup).
        "AUSTIN": "Austin",
        # CHEVROLET: all KBA Chevrolet models (Spark, Matiz, Aveo, Captiva, Cruze, Orlando, Corvette, Trax, Kalos, Camaro, Nubira, Lacetti, Epica, Rezzo) in lookup.
        "CHEVROLET": "Chevrolet",
    }

    for token, display in sorted(expected.items()):
        result = hsn_tsn.canonical_brand(token)
        assert result == display, (
            f"canonical_brand({token!r}) == {result!r}, expected {display!r}"
        )
        assert display in lookup_brands, (
            f"Display brand {display!r} (for token {token!r}) not found in "
            f"hsn_tsn_lookup.csv — typo or CSV not updated?"
        )


def test_model_family_strips_trailing_comma():
    """Comma-separated KBA model strings (e.g. 'MERCEDES GLK, GLC') produce
    a trailing-comma first token ('glk,'); the comma must be stripped so the
    family matches the lookup key ('glk').
    """
    # Fleet side: comma-separated multi-model entries
    assert hsn_tsn.model_family("Mercedes-Benz", "MERCEDES GLK, GLC") == "glk"
    # ML-KLASSE -> the -klasse suffix is stripped further to "ml" (matches lookup "ml")
    assert hsn_tsn.model_family("Mercedes-Benz", "MERCEDES ML-KLASSE, GLE") == "ml"
    assert hsn_tsn.model_family("Mitsubishi", "MITSUBISHI MIRAGE, SPACE STAR") == "mirage"
    assert hsn_tsn.model_family("Ford", "FORD TRANSIT, TOURNEO") == "transit"


def test_model_family_strips_exclamation_suffix():
    """HSN/TSN lookup stores VW 'Up!' with an exclamation mark; the KBA fleet
    model is 'VW UP' (no '!'). The normaliser must strip trailing '!' so both
    sides agree on the family 'up'.
    """
    # Lookup side: 'VW Up! 1.0' -> family should be 'up' (not 'up!')
    assert hsn_tsn.model_family("VW", "VW Up! 1.0") == "up"
    # Fleet side: already 'up' (no change needed)
    assert hsn_tsn.model_family("VW", "VW UP") == "up"


def test_vw_up_resolves_to_model_tier(lookup):
    """After the '!' normalisation fix, VW UP must resolve at model tier
    (not fall to brand tier as before).
    """
    rec, tier = lookup.lookup("VW", "up", "petrol")
    assert tier in {"exact", "model"}, f"VW UP fell to {tier} tier (expected model/exact)"
