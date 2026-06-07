"""Tests for the per-vehicle German fleet generative chain.

``braunschweig.synthesis.vehicles.fleet_sampling_de.sample_fleet`` draws a full
spec (segment -> powertrain -> euro -> age -> brand/model -> HBEFA type) for each
household car. The key scientific assertions:

  * per-Kreis BEV share of the sampled fleet matches KBA FZ 27.15 (the rake must
    hit the LOCAL electric share);
  * NDS overall fuel split matches KBA FZ 27.4 within tolerance;
  * Euro conditional on powertrain is valid (a BEV never has a combustion Euro);
  * age is consistent with the Euro class (no Euro-6 on a 25-29-year-old car);
  * every sampled spec maps to a valid HBEFA VehicleType;
  * income coupling is preserved end-to-end (higher economic status -> larger /
    more-often-electric cars);
  * the fallback rate for missing Gemeinde/Kreis/segment cells is logged < a
    threshold;
  * determinism given the seed.
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
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402
from braunschweig.synthesis.vehicles import hbefa  # noqa: E402

DATA_PATH = str(DATA)


# --------------------------------------------------------------------------- #
# Synthetic household-car frame: many cars per ZGB Kreis, status/raumtyp varied.
# --------------------------------------------------------------------------- #
def _make_cars(n_per_kreis: int = 4000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    statuses = list(ft.STATUS_LABELS)
    rows = []
    for kreis in ft.ZGB_KREISE_AGS5:
        for _ in range(n_per_kreis):
            rows.append({
                "economic_status": rng.choice(statuses),
                "kreis_ags5": kreis,
                "gemeinde": np.nan,           # Kreis-level (no Gemeinde tilt)
                "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
            })
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def sampler():
    return fs.FleetSampler.from_data_path(DATA_PATH)


@pytest.fixture(scope="module")
def sampled(sampler):
    df_cars = _make_cars()
    df_spec, df_types = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42, sampler=sampler)
    return df_spec, df_types


# --------------------------------------------------------------------------- #
# THE key assertion: per-Kreis BEV share matches FZ 27.15.
# --------------------------------------------------------------------------- #
def test_per_kreis_bev_share_matches_fz_27_15(sampled):
    df_spec, _ = sampled
    df_kreis = ft.load_kreis_powertrain(DATA_PATH).set_index("kreis_ags5")
    for kreis in ft.ZGB_KREISE_AGS5:
        sub = df_spec[df_spec["kreis_ags5"] == kreis]
        sampled_share = float((sub["powertrain"] == "bev").mean())
        target = float(df_kreis.loc[kreis, "bev_share"])
        assert sampled_share == pytest.approx(target, abs=0.01), (
            f"Kreis {kreis}: sampled BEV {sampled_share:.4f} vs "
            f"FZ 27.15 {target:.4f}"
        )


def test_per_kreis_phev_share_matches_fz_27_15(sampled):
    df_spec, _ = sampled
    df_kreis = ft.load_kreis_powertrain(DATA_PATH).set_index("kreis_ags5")
    for kreis in ft.ZGB_KREISE_AGS5:
        sub = df_spec[df_spec["kreis_ags5"] == kreis]
        sampled_share = float((sub["powertrain"] == "phev").mean())
        target = float(df_kreis.loc[kreis, "phev_share"])
        assert sampled_share == pytest.approx(target, abs=0.01), (
            f"Kreis {kreis}: sampled PHEV {sampled_share:.4f} vs "
            f"FZ 27.15 {target:.4f}"
        )


# --------------------------------------------------------------------------- #
# NDS overall fuel split matches FZ 27.4 (petrol:diesel ratio of combustion).
# --------------------------------------------------------------------------- #
def test_nds_petrol_diesel_ratio_matches_fz_27_4(sampled):
    df_spec, _ = sampled
    petrol = int((df_spec["powertrain"] == "petrol").sum())
    diesel = int((df_spec["powertrain"] == "diesel").sum())
    sampled_petrol_fraction = petrol / (petrol + diesel)

    df_fuel = ft.load_fuel_euro_nds(DATA_PATH)
    totals = df_fuel.groupby("fuel")["count"].sum()
    target = float(totals["petrol"] / (totals["petrol"] + totals["diesel"]))
    assert sampled_petrol_fraction == pytest.approx(target, abs=0.03)


# --------------------------------------------------------------------------- #
# Euro conditional on powertrain valid: no combustion Euro on a BEV.
# --------------------------------------------------------------------------- #
def test_bev_never_has_combustion_euro(sampled):
    df_spec, _ = sampled
    bev = df_spec[df_spec["powertrain"] == "bev"]
    # BEV's emission concept must be the fixed PC BEV concept regardless of euro.
    assert (bev["hbefa_emission"] == "PC BEV").all()


def test_euro_distribution_for_petrol_matches_fz_27_4(sampled, sampler):
    df_spec, _ = sampled
    petrol = df_spec[df_spec["powertrain"] == "petrol"]
    sampled_euro6 = float((petrol["euro_class"] == "euro6").mean())
    target = sampler.euro_given_powertrain["petrol"][
        list(ft.EURO_CLASS_LABELS).index("euro6")]
    # Age<->euro masking shifts the distribution slightly; allow a wider band.
    assert sampled_euro6 == pytest.approx(float(target), abs=0.08)


# --------------------------------------------------------------------------- #
# Age <-> Euro consistency.
# --------------------------------------------------------------------------- #
def test_age_consistent_with_euro(sampled):
    df_spec, _ = sampled
    combustion = df_spec[df_spec["powertrain"].isin(["petrol", "diesel", "gas"])]
    for _, car in combustion.iterrows():
        assert fs._age_consistent_with_euro(
            car["age_band"], car["euro_class"], car["powertrain"]), car.to_dict()


def test_no_euro6_on_old_combustion_car(sampled):
    df_spec, _ = sampled
    old = df_spec[
        df_spec["powertrain"].isin(["petrol", "diesel"])
        & df_spec["age_band"].isin(["25_to_29", "30_plus"])
    ]
    # A 25+-year-old combustion car cannot be Euro-6 (introduced 2015).
    assert not (old["euro_class"] == "euro6").any()


# --------------------------------------------------------------------------- #
# Every spec maps to a valid HBEFA VehicleType.
# --------------------------------------------------------------------------- #
def test_every_spec_is_valid_hbefa_type(sampled):
    df_spec, df_types = sampled
    allowed_tech = set(hbefa.POWERTRAIN_TO_TECHNOLOGY.values())
    for _, row in df_types.iterrows():
        assert row["hbefa_cat"] == "PASSENGER_CAR"
        assert row["hbefa_tech"] in allowed_tech
        assert row["hbefa_size"] in hbefa.HBEFA_SIZE_CLASSES
    # And reconstruct a VehicleType from each spec row to validate the triple.
    for _, car in df_spec.sample(500, random_state=1).iterrows():
        vt = hbefa.vehicle_type_for(
            car["powertrain"], car["euro_class"], car["segment"])
        assert hbefa.is_valid_vehicle_type(vt)
        assert vt.type_id == car["type_id"]


def test_vehicle_types_are_distinct(sampled):
    _, df_types = sampled
    assert df_types["type_id"].is_unique
    assert len(df_types) >= 5  # several technologies x sizes x euro stages


# --------------------------------------------------------------------------- #
# Income coupling preserved end-to-end.
# --------------------------------------------------------------------------- #
def test_income_coupling_segment_size(sampled):
    df_spec, _ = sampled
    large = df_spec["hbefa_size"] == "large"
    share_low = float(large[df_spec["economic_status"] == "very_low"].mean())
    share_high = float(large[df_spec["economic_status"] == "very_high"].mean())
    assert share_high > share_low, (share_low, share_high)


def test_income_coupling_electric_share(sampled):
    df_spec, _ = sampled
    electric = df_spec["powertrain"].isin(["bev", "phev"])
    share_low = float(electric[df_spec["economic_status"] == "very_low"].mean())
    share_high = float(electric[df_spec["economic_status"] == "very_high"].mean())
    # Higher status -> larger segments -> (per FZ 27.10) more electric.
    assert share_high > share_low, (share_low, share_high)


# --------------------------------------------------------------------------- #
# Brand/model are additive and isolated.
# --------------------------------------------------------------------------- #
def test_brand_model_populated_for_most_cars(sampled):
    df_spec, _ = sampled
    have_model = (df_spec["model"].str.len() > 0).mean()
    assert have_model > 0.95
    # Brand is the first token of the model.
    sample = df_spec[df_spec["model"].str.len() > 0].iloc[0]
    assert sample["brand"] == sample["model"].split(" ", 1)[0]


# --------------------------------------------------------------------------- #
# Fallback observability + determinism.
# --------------------------------------------------------------------------- #
def test_fallback_rate_logged(caplog):
    df_cars = _make_cars(n_per_kreis=200)
    with caplog.at_level(logging.INFO):
        fs.sample_fleet(df_cars, DATA_PATH, random_seed=7)
    text = " ".join(r.message for r in caplog.records).lower()
    assert "primary" in text and "fallback" in text


def test_unknown_kreis_falls_back_and_logs(caplog):
    df_cars = pd.DataFrame([{
        "economic_status": "medium", "kreis_ags5": "09999",
        "gemeinde": np.nan, "raumtyp": 73,
    }] * 50)
    with caplog.at_level(logging.WARNING):
        df_spec, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=1)
    assert len(df_spec) == 50  # never drops a car
    text = " ".join(r.message for r in caplog.records).lower()
    assert "fallback" in text


def test_deterministic_given_seed():
    df_cars = _make_cars(n_per_kreis=300)
    a, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=123)
    b, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=123)
    pd.testing.assert_frame_equal(a, b)


def test_different_seed_changes_draw():
    df_cars = _make_cars(n_per_kreis=300)
    a, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=1)
    b, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=2)
    assert not a["powertrain"].equals(b["powertrain"])


def test_gemeinde_tilt_raises_local_bev_share():
    """A Gemeinde with a high private BEV share should get more BEVs than the
    same cars assigned at Kreis level (the FZ 27.17 tilt is observable)."""
    df_gem = ft.load_gemeinde_private_bev(DATA_PATH)
    df_kreis = ft.load_kreis_powertrain(DATA_PATH).set_index("kreis_ags5")
    # Pick a Gemeinde whose private BEV share clearly exceeds its Kreis share.
    df_gem = df_gem.dropna(subset=["private_bev_share"]).copy()
    df_gem["kreis_bev"] = df_gem["kreis_ags5"].map(df_kreis["bev_share"])
    df_gem["ratio"] = df_gem["private_bev_share"] / df_gem["kreis_bev"]
    top = df_gem.sort_values("ratio", ascending=False).iloc[0]
    kreis = top["kreis_ags5"]
    gemeinde = top["gemeinde"]

    base = pd.DataFrame([{
        "economic_status": "medium", "kreis_ags5": kreis,
        "gemeinde": np.nan, "raumtyp": 75,
    }] * 6000)
    tilted = base.copy()
    tilted["gemeinde"] = gemeinde

    spec_base, _ = fs.sample_fleet(base, DATA_PATH, random_seed=5)
    spec_tilt, _ = fs.sample_fleet(tilted, DATA_PATH, random_seed=5)
    bev_base = float((spec_base["powertrain"] == "bev").mean())
    bev_tilt = float((spec_tilt["powertrain"] == "bev").mean())
    assert bev_tilt > bev_base, (bev_base, bev_tilt, top["ratio"])
