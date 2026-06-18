"""Tests for the model-feasible-fuels powertrain mask (Bug 2).

``braunschweig.data.kba.feasible_fuels.FeasibleFuels`` derives, per
``(canonical brand, model family)``, the SET of powertrains a model can
plausibly have -- strictly from the fuels that actually appear for that brand +
family in the HSN/TSN lookup (62 brands). The fleet sampler uses this set to
mask the per-segment powertrain pmf so an exotic petrol-only marque
(Lamborghini) is never assigned a diesel, and a pure-electric marque (Tesla)
only ever gets a BEV.

Resolved design decision (see task-6 brief): feasibility is derived ONLY from
the HSN/TSN lookup. When the (canonical brand, family) is unknown / has no fuel
rows the method returns ``None`` (unknown -> caller keeps the unmasked pmf).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import feasible_fuels as ffmod  # noqa: E402
from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

DATA_PATH = str(DATA)

# The HSN/TSN lookup is local-only (gitignored, scraped from hsn-tsn.de).
_HSN_TSN_LOOKUP = DATA / "braunschweig" / "kba" / "hsn_tsn_lookup.csv"
pytestmark = pytest.mark.skipif(
    not _HSN_TSN_LOOKUP.exists(),
    reason="HSN/TSN lookup is local-only (run scripts/scrape_hsn_tsn.py); skipped when absent",
)


@pytest.fixture(scope="module")
def ff():
    return ffmod.FeasibleFuels.from_data_path(DATA_PATH)


# --------------------------------------------------------------------------- #
# (a) a petrol-only brand excludes diesel.
# --------------------------------------------------------------------------- #
def test_petrol_only_brand_excludes_diesel(ff):
    # Lamborghini has only "Benzin" rows in the 62-brand HSN/TSN lookup
    # (its families normalise to e.g. "gallardo").
    s = ff.model_feasible_powertrains("LAMBORGHINI", "gallardo")
    assert s is not None
    assert "diesel" not in s
    assert "petrol" in s


# --------------------------------------------------------------------------- #
# (b) an unknown brand/family returns None.
# --------------------------------------------------------------------------- #
def test_unknown_brand_returns_none(ff):
    assert ff.model_feasible_powertrains("FABRIKAMARKE", "phantasiemodell") is None


def test_known_brand_unknown_family_returns_brand_fallback(ff):
    # A known canonical brand but a family that has no HSN/TSN rows -> brand-level
    # fallback (not None). VW has petrol AND diesel in its HSN/TSN rows, so the
    # returned set must include both (no false constraint).
    result = ff.model_feasible_powertrains("VW", "zzznichtexistent")
    assert result is not None, "known brand with unknown family should return brand-level fallback"
    assert "petrol" in result
    assert "diesel" in result


# --------------------------------------------------------------------------- #
# Brand-level fallback: new tests
# --------------------------------------------------------------------------- #
def test_brand_level_constrains_single_fuel_brand_lamborghini(ff):
    """Lamborghini family 'urus' is absent from Lambo HSN/TSN rows (only Gallardo etc.).
    The brand-level fallback should return petrol-only for the brand -> excludes diesel."""
    result = ff.model_feasible_powertrains("LAMBORGHINI", "urus")
    assert result is not None, (
        "LAMBORGHINI brand is in HSN/TSN lookup; brand-level fallback must fire, not None"
    )
    assert "diesel" not in result, f"LAMBORGHINI/urus must exclude diesel, got {result}"
    assert "petrol" in result, f"LAMBORGHINI/urus must include petrol, got {result}"


def test_brand_level_constrains_single_fuel_brand_tesla(ff):
    """Tesla family miss (e.g. 'roadster2' not in lookup) -> brand-level fallback.
    Tesla is BEV-only in HSN/TSN -> must exclude petrol and diesel."""
    # Pick a family name that is almost certainly absent from the Tesla lookup rows
    result = ff.model_feasible_powertrains("TESLA", "roadster2")
    assert result is not None, (
        "TESLA brand is in HSN/TSN; brand-level fallback must fire, not None"
    )
    assert "petrol" not in result, f"Tesla brand fallback must exclude petrol, got {result}"
    assert "diesel" not in result, f"Tesla brand fallback must exclude diesel, got {result}"
    assert "bev" in result, f"Tesla brand fallback must include bev, got {result}"


def test_brand_level_permissive_for_multifuel(ff):
    """VW with unknown family -> brand-level fallback includes petrol AND diesel."""
    result = ff.model_feasible_powertrains("VW", "zzznichtexistent")
    assert result is not None
    assert "petrol" in result
    assert "diesel" in result


def test_unknown_brand_still_none(ff):
    """A brand genuinely absent from the 62-brand HSN/TSN lookup returns None.
    Ferrari/MG are not in the scraped set (Lamborghini IS, so we can't use it)."""
    assert ff.model_feasible_powertrains("FERRARI", "roma") is None


# --------------------------------------------------------------------------- #
# (c) Tesla -> {bev} only.
# --------------------------------------------------------------------------- #
def test_tesla_is_bev_only(ff):
    # All Tesla Model S/3/X/Y variants normalise to the family token "model".
    s = ff.model_feasible_powertrains("TESLA", "model")
    assert s is not None
    assert s == {"bev"}


def test_porsche_911_has_petrol_not_only_electric(ff):
    # Porsche 911 is a combustion sports car; petrol must be feasible.
    s = ff.model_feasible_powertrains("PORSCHE", "911")
    assert s is not None
    assert "petrol" in s


# --------------------------------------------------------------------------- #
# Integration: a model-constrained car never gets an infeasible powertrain.
# --------------------------------------------------------------------------- #
def test_sample_fleet_model_constrained_never_infeasible():
    """In the v2 path, when a drawn (brand, family) is feasibility-known, the
    assigned powertrain must lie in the feasible set."""
    sampler = fs.FleetSampler.from_data_path(DATA_PATH)
    ff_obj = ffmod.FeasibleFuels.from_data_path(DATA_PATH)
    rng_statuses = list(ft.STATUS_LABELS)
    rng = np.random.default_rng(0)
    rows = []
    for kreis in ft.ZGB_KREISE_AGS5:
        for _ in range(1500):
            rows.append({
                "economic_status": rng.choice(rng_statuses),
                "kreis_ags5": kreis,
                "gemeinde": np.nan,
                "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
            })
    df_cars = pd.DataFrame(rows)
    df_spec, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=11, sampler=sampler, consistency_v2=True)

    violations = 0
    checked = 0
    from braunschweig.data.kba.hsn_tsn import canonical_brand, model_family
    for _, car in df_spec.iterrows():
        brand = car["brand"]
        model = car["model"]
        if not model:
            continue
        cb = canonical_brand(brand)
        fam = model_family(cb or "", model)
        feasible = ff_obj.model_feasible_powertrains(brand, fam)
        if feasible is None:
            continue
        checked += 1
        if car["powertrain"] not in feasible:
            violations += 1
    assert checked > 0, "no feasibility-known cars in the sample (test is vacuous)"
    assert violations == 0, f"{violations}/{checked} model-constrained cars got an infeasible powertrain"
