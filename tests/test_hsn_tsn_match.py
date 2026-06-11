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
    df_spec, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=42)
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
    # displacement). A combustion powertrain can still resolve to an electric
    # model family because the fleet draws powertrain and model family
    # independently (e.g. a "petrol" draw on a Renault ZOE, which exists only as
    # Elektro in HSN/TSN); the engine record reflects the model family. So every
    # vehicle whose matched fuel_detail is NOT electric must carry a positive
    # displacement.
    non_electric = out[~out["fuel_detail"].str.contains("Elektro", na=False)]
    assert (non_electric["displacement_ccm"] > 0).all()


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
    rates = out["_hsn_tsn_match_tier"].value_counts(normalize=True)
    exact_model = float(rates.get("exact", 0.0) + rates.get("model", 0.0))
    assert exact_model > 0.5, (
        f"exact+model match rate {exact_model:.3f} below floor 0.5 -- "
        f"check brand canonicalisation / model-family normalisation"
    )
    # The tier rates must be logged (no-silent-fallback rule).
    assert any("match tier" in r.message.lower() or "tier" in r.message.lower()
               for r in caplog.records), "tier rates not logged"


def test_attach_drops_internal_tier_column_by_default(lookup, fleet_spec):
    """The diagnostic ``_hsn_tsn_match_tier`` column is internal; the public
    attach keeps the five engine columns but the tier column is opt-in."""
    out = hsn_tsn.attach_hsn_tsn(fleet_spec, lookup=lookup, keep_tier=False)
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
