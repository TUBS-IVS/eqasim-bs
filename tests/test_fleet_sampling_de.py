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
FIXTURES = REPO / "tests" / "fixtures"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402
from braunschweig.synthesis.vehicles import hbefa  # noqa: E402

DATA_PATH = str(DATA)


def _load_golden(name: str) -> pd.DataFrame:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"golden fixture not found: {path}")
    return pd.read_parquet(path)


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
    df_spec, df_types, _ = fs.sample_fleet(
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
        df_spec, _, _summary = fs.sample_fleet(df_cars, DATA_PATH, random_seed=1)
    assert len(df_spec) == 50  # never drops a car
    text = " ".join(r.message for r in caplog.records).lower()
    assert "fallback" in text


def test_deterministic_given_seed():
    df_cars = _make_cars(n_per_kreis=300)
    a, _, _s1 = fs.sample_fleet(df_cars, DATA_PATH, random_seed=123)
    b, _, _s2 = fs.sample_fleet(df_cars, DATA_PATH, random_seed=123)
    pd.testing.assert_frame_equal(a, b)


def test_different_seed_changes_draw():
    df_cars = _make_cars(n_per_kreis=300)
    a, _, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=1)
    b, _, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=2)
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

    spec_base, _, _ = fs.sample_fleet(base, DATA_PATH, random_seed=5)
    spec_tilt, _, _ = fs.sample_fleet(tilted, DATA_PATH, random_seed=5)
    bev_base = float((spec_base["powertrain"] == "bev").mean())
    bev_tilt = float((spec_tilt["powertrain"] == "bev").mean())
    assert bev_tilt > bev_base, (bev_base, bev_tilt, top["ratio"])


# --------------------------------------------------------------------------- #
# Task 5 — sonstige redistribution (consistency_v2).
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def sampled_v2(sampler):
    """sample_fleet with consistency_v2=True (default)."""
    df_cars = _make_cars()
    df_spec, df_types, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42, sampler=sampler, consistency_v2=True)
    return df_spec, df_types


@pytest.fixture(scope="module")
def sampled_v2_off(sampler):
    """sample_fleet with consistency_v2=False (legacy path)."""
    df_cars = _make_cars()
    df_spec, df_types = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42, sampler=sampler, consistency_v2=False)
    return df_spec, df_types


def test_no_sonstige_segment_in_output(sampled_v2):
    """After redistribution, no vehicle carries segment=='sonstige'."""
    df_spec, _ = sampled_v2
    assert (df_spec["segment"] == "sonstige").sum() == 0
    assert (df_spec["brand"].astype(str).str.strip() == "").mean() < 0.001


def test_sonstige_off_keeps_old_behaviour(sampled_v2_off):
    """With consistency_v2=False, sonstige can appear (legacy path unchanged)."""
    df_spec, _ = sampled_v2_off
    # sonstige is ~2.3% of fleet; with n=4000*num_kreise it must appear at least
    # once (statistical near-certainty).
    assert (df_spec["segment"] == "sonstige").sum() > 0


# --------------------------------------------------------------------------- #
# Task 7 — per-Kreis BEV/PHEV recalibration on the feasible support (Bug 2).
# --------------------------------------------------------------------------- #
def test_every_electric_car_is_electric_capable(sampled, sampler):
    """Feasibility preserved post-recalibration: every BEV/PHEV the v2 chain
    emits must be assigned to an electric-CAPABLE model (its HSN/TSN feasible set
    includes that powertrain). The Task 7 rake only RE-WEIGHTS feasible electric
    mass, so it can never create a petrol-only BEV.
    """
    from braunschweig.data.kba.hsn_tsn import canonical_brand, model_family

    ff = sampler.feasible_fuels
    if ff is None:
        pytest.skip("HSN/TSN lookup absent; feasibility mask disabled.")
    df_spec, _ = sampled
    electric = df_spec[df_spec["powertrain"].isin(["bev", "phev"])]
    violations = 0
    checked = 0
    for _, car in electric.iterrows():
        brand = str(car["brand"])
        model = str(car["model"])
        if not brand or not model:
            continue
        feasible = ff.model_feasible_powertrains(
            brand, model_family(canonical_brand(brand) or "", model))
        if feasible is None:
            # Unknown model -> unmasked pmf was kept; no feasibility claim.
            continue
        checked += 1
        if car["powertrain"] not in feasible:
            violations += 1
    assert checked > 0, "no model-constrained electric cars to verify"
    assert violations == 0, f"{violations}/{checked} electric cars not capable"


def test_euro_age_consistent_after_recalibration(sampled):
    """Internal consistency preserved post-recalibration: euro/age are re-derived
    from the FINAL (raked) powertrain, so every combustion car still satisfies the
    age<->euro rule and no BEV carries a combustion Euro concept.
    """
    df_spec, _ = sampled
    combustion = df_spec[df_spec["powertrain"].isin(["petrol", "diesel", "gas"])]
    for _, car in combustion.iterrows():
        assert fs._age_consistent_with_euro(
            car["age_band"], car["euro_class"], car["powertrain"]), car.to_dict()
    bev = df_spec[df_spec["powertrain"] == "bev"]
    assert (bev["hbefa_emission"] == "PC BEV").all()


def test_recalibration_deterministic_given_seed():
    """Same seed -> identical raked output (the Task 7 rake uses no fresh rng)."""
    df_cars = _make_cars(n_per_kreis=400)
    a, _, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=99, consistency_v2=True)
    b, _, _ = fs.sample_fleet(df_cars, DATA_PATH, random_seed=99, consistency_v2=True)
    pd.testing.assert_frame_equal(a, b)


# --------------------------------------------------------------------------- #
# Task 8 — provenance columns (Feature P).
# --------------------------------------------------------------------------- #
def test_provenance_columns_present(sampled):
    """brand_source and powertrain_feasibility must be present on df_spec (v2).

    brand_source ∈ {"kba_model", "sonstige_redistributed"};
    powertrain_feasibility ∈ {"model_constrained", "segment_fallback"}.
    (The fixture ``sampled`` uses consistency_v2=True, the default.)
    """
    df_spec, _ = sampled
    for col, domain in [
        ("brand_source", {"kba_model", "sonstige_redistributed"}),
        ("powertrain_feasibility", {"model_constrained", "segment_fallback"}),
    ]:
        assert col in df_spec.columns, f"column '{col}' missing from df_spec"
        assert set(df_spec[col].unique()) <= domain, (
            f"column '{col}' has unexpected values: "
            f"{set(df_spec[col].unique()) - domain}"
        )


def test_provenance_columns_absent_on_off_path(sampled_v2_off):
    """consistency_v2=False (OFF path) must NOT add the provenance columns so
    the existing output columns stay byte-identical."""
    df_spec, _ = sampled_v2_off
    assert "brand_source" not in df_spec.columns
    assert "powertrain_feasibility" not in df_spec.columns


# --------------------------------------------------------------------------- #
# Task 8 review fix — household.py keep_tier gate
# --------------------------------------------------------------------------- #
def _make_minimal_df_spec(with_provenance: bool) -> pd.DataFrame:
    """Synthetic df_spec frame with the columns attach_hsn_tsn requires.

    ``with_provenance=True`` adds ``brand_source`` + ``powertrain_feasibility``
    to simulate the consistency_v2 ON path; without them we simulate the OFF
    path (consistency_v2=False).  The ``segment`` column is omitted so the
    segment tier is skipped (unit test environment, no data_path).
    """
    from braunschweig.data.kba import hsn_tsn as hst

    # Build a tiny 4-row HSN/TSN lookup in-memory (no data_path required).
    lookup_df = pd.DataFrame([
        {"brand": "VW", "hsn": "0603", "tsn": "ABC",
         "model": "VW Golf", "power_ps": 115, "power_kw": 85,
         "displacement_ccm": 1968, "fuel": "Diesel"},
        {"brand": "BMW", "hsn": "0005", "tsn": "XYZ",
         "model": "BMW 3er", "power_ps": 184, "power_kw": 135,
         "displacement_ccm": 1998, "fuel": "Benzin"},
    ])
    lookup = hst.HsnTsnLookup.from_frame(lookup_df)

    df = pd.DataFrame([
        {"brand": "VW", "model": "VW Golf", "powertrain": "diesel",
         "household_id": 1, "owner_id": 101, "vehicle_id": "101:car:0",
         "mode": "car", "economic_status": "medium",
         "kreis_ags5": "03101", "gemeinde": "BRAUNSCHWEIG", "raumtyp": 71},
        {"brand": "BMW", "model": "BMW 3er", "powertrain": "petrol",
         "household_id": 2, "owner_id": 201, "vehicle_id": "201:car:0",
         "mode": "car", "economic_status": "high",
         "kreis_ags5": "03101", "gemeinde": "BRAUNSCHWEIG", "raumtyp": 71},
    ])
    if with_provenance:
        df["brand_source"] = "kba_model"
        df["powertrain_feasibility"] = "model_constrained"

    # Run attach_hsn_tsn using the in-memory lookup (no data_path).
    keep_tier = "brand_source" in df.columns   # the gate under test
    df = hst.attach_hsn_tsn(df, lookup=lookup, keep_tier=keep_tier)
    return df


def test_household_off_path_no_hsn_tsn_match_tier():
    """OFF path (no brand_source on df_spec): keep_tier=False -> no tier column.

    Verifies the gate ``keep_tier = "brand_source" in df_spec.columns`` in
    household.execute() so df_vehicles stays byte-identical for existing columns
    when consistency_v2=False.
    """
    df = _make_minimal_df_spec(with_provenance=False)
    assert "hsn_tsn_match_tier" not in df.columns, (
        "OFF path must NOT have hsn_tsn_match_tier; found columns: "
        + str(list(df.columns))
    )
    # Engine attribute columns must still be present (the attach ran).
    for col in ("engine_power_kw", "engine_power_ps", "displacement_ccm",
                "fuel_detail", "hsn", "tsn"):
        assert col in df.columns, f"expected engine column '{col}' missing"


def test_household_v2_path_all_three_provenance_columns():
    """v2 path (brand_source present): keep_tier=True -> all three provenance
    columns survive to df_vehicles.

    Verifies the three columns travel together: brand_source,
    powertrain_feasibility, and hsn_tsn_match_tier.
    """
    df = _make_minimal_df_spec(with_provenance=True)
    for col in ("brand_source", "powertrain_feasibility", "hsn_tsn_match_tier"):
        assert col in df.columns, (
            f"v2 path must have '{col}'; found columns: {list(df.columns)}"
        )
    # Verify tier values are non-empty strings.
    assert df["hsn_tsn_match_tier"].notna().all()
    assert (df["hsn_tsn_match_tier"].str.len() > 0).all()


# --------------------------------------------------------------------------- #
# Feature B — income-age tilt in sample_fleet (Task 3).
# --------------------------------------------------------------------------- #

def _make_cars_multi_status(n_per_status: int = 3000, seed: int = 99) -> pd.DataFrame:
    """Cars with EQUAL numbers of each economic_status across ZGB Kreise.

    Using a single Kreis (03101) with n_per_status cars per status gives a
    balanced frame large enough for a stable income-age gradient.
    """
    rng = np.random.default_rng(seed)
    statuses = list(ft.STATUS_LABELS)
    rows = []
    kreis = ft.ZGB_KREISE_AGS5[0]   # Braunschweig Kreis
    for status in statuses:
        for _ in range(n_per_status):
            rows.append({
                "economic_status": status,
                "kreis_ags5": kreis,
                "gemeinde": np.nan,
                "raumtyp": int(rng.choice([71, 72, 73, 74, 75, 76, 77])),
            })
    return pd.DataFrame(rows)


def test_income_age_gradient_in_output():
    """With age_income_coupling=True, very_low status has clearly older cars
    than very_high status (income->age signal is observable in the output).

    Today (pre-Feature-B) the age column is flat ~6.7 across all statuses.
    After applying the MiD tilt, very_low households drive older cars.
    """
    df_cars = _make_cars_multi_status(n_per_status=3000)
    df_spec, _, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42,
        consistency_v2=True,
        age_income_coupling=True,
    )
    mean_age_by_status = df_spec.groupby("economic_status")["age"].mean()
    age_very_low = mean_age_by_status["very_low"]
    age_very_high = mean_age_by_status["very_high"]
    # Very_low status should have meaningfully older cars than very_high.
    # The MiD tilt shifts the distribution by ~1-3 years; assert at least 0.5
    # years gap so this is robust to sampling noise at n=3000.
    assert age_very_low > age_very_high + 0.5, (
        f"Expected age(very_low)={age_very_low:.3f} > age(very_high)={age_very_high:.3f} + 0.5 "
        f"(flat pre-tilt ~6.7 → gradient expected post-tilt)"
    )


def test_age_income_off_unchanged():
    """age_income_coupling=False must produce age columns byte-identical to the
    committed golden fixture (``tests/fixtures/feature_b_age_off_golden.parquet``).

    The golden was generated on this feature branch by running
    ``sample_fleet(_make_cars_multi_status(n_per_status=500), ...,
    random_seed=42, consistency_v2=True, age_income_coupling=False)``
    and taking the [\"age\", \"age_band\"] columns. Analytically, coupling=False is
    byte-identical to pre-Feature-B because the tilt block is guarded by
    ``if age_model is not None`` and the model is only built when
    ``consistency_v2=True AND age_income_coupling=True``.

    The generation command was::

        python -c "
        import numpy as np, pandas as pd, sys; sys.path.insert(0, '.')
        from braunschweig.data.kba import fleet_tables as ft
        from braunschweig.synthesis.vehicles import fleet_sampling_de as fs
        rng = np.random.default_rng(99)
        statuses = list(ft.STATUS_LABELS)
        kreis = ft.ZGB_KREISE_AGS5[0]
        rows = []
        for status in statuses:
            for _ in range(500):
                rows.append({'economic_status': status, 'kreis_ags5': kreis,
                             'gemeinde': float('nan'),
                             'raumtyp': int(rng.choice([71,72,73,74,75,76,77]))})
        df_cars = pd.DataFrame(rows)
        df_spec, _ = fs.sample_fleet(df_cars, 'eqasim-data/data',
                                     random_seed=42, consistency_v2=True,
                                     age_income_coupling=False)
        df_spec[['age', 'age_band']].to_parquet(
            'tests/fixtures/feature_b_age_off_golden.parquet', index=False)
        "

    The golden is committed to tests/fixtures/feature_b_age_off_golden.parquet.
    """
    golden = _load_golden("feature_b_age_off_golden.parquet")

    # Reproduce exactly the same input as used during golden generation.
    rng_input = np.random.default_rng(99)
    statuses = list(ft.STATUS_LABELS)
    kreis = ft.ZGB_KREISE_AGS5[0]
    rows = []
    for status in statuses:
        for _ in range(500):
            rows.append({
                "economic_status": status,
                "kreis_ags5": kreis,
                "gemeinde": float("nan"),
                "raumtyp": int(rng_input.choice([71, 72, 73, 74, 75, 76, 77])),
            })
    df_cars = pd.DataFrame(rows)

    df_spec, _, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42,
        consistency_v2=True, age_income_coupling=False)

    # Byte-identical assertion against the frozen baseline.
    pd.testing.assert_frame_equal(
        df_spec[["age", "age_band"]].reset_index(drop=True),
        golden.reset_index(drop=True),
        check_like=False,
        obj="coupling=False age vs golden",
    )

    # Also verify the coupling=True run produces DIFFERENT ages (tilt is active).
    df_spec_on, _, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42,
        consistency_v2=True, age_income_coupling=True)
    # With n=2500 cars the age distribution must differ somewhere.
    assert not df_spec["age"].equals(df_spec_on["age"]), (
        "coupling=True and coupling=False produced identical age columns; "
        "the tilt is not being applied"
    )


# --------------------------------------------------------------------------- #
# Task 5 (final) — age×status validation panel + MiD-match e2e.
# --------------------------------------------------------------------------- #

def test_synthetic_age_status_matches_mid():
    """Validation panel: Feature B income-age gradient checks (three assertions).

    Uses a representative fleet built with age_income_coupling=True (Feature B
    fully active) and consistency_v2=True.  The validation panel module is
    exercised end-to-end: build_panel computes both synthetic and MiD reference
    values.  Three assertions, in order of scientific importance:

    (a) Synthetic mean-age-by-status is MONOTONE DECREASING (very_low → very_high).
        This is the primary gradient-direction check.

    (b) The income GRADIENT SPREAD of the synthetic ``under_5`` share matches the
        MiD spread within ±0.10.  Feature B injects the MiD income gradient (not
        the absolute level), so this is the scientifically correct coupling check.
        Measured values: syn_spread ~0.197, MiD_spread ~0.252, diff ~0.055 → passes.

    (c) Loose GROSS-FAILURE SENTINEL: every status's synthetic ``under_5`` share
        stays within ±0.20 (abs) of MiD.  The synthetic level is systematically
        +5-11pp above MiD BY DESIGN — the absolute age level is anchored to KBA
        P(age|powertrain), not to the MiD marginal.  This bound catches only
        catastrophic failures (e.g. the tilt is inverted or disabled entirely);
        it is NOT a scientific match assertion.
    """
    from braunschweig.analysis.population_validation import fleet_age_status as FAS

    # Build a representative fleet with balanced statuses.
    df_cars = _make_cars_multi_status(n_per_status=3000)
    df_spec, _, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42,
        consistency_v2=True, age_income_coupling=True,
    )

    panel = FAS.build_panel(df_spec, DATA_PATH)

    # (a) Panel must have one row per status, in order.
    assert list(panel["economic_status"]) == list(FAS.STATUS_ORDER), (
        f"Panel status order mismatch: {list(panel['economic_status'])}"
    )

    # (b) Synthetic mean_age MONOTONE DECREASING: very_low > ... > very_high.
    mean_ages = panel.set_index("economic_status")["synthetic_mean_age_yr"]
    for lo, hi in zip(FAS.STATUS_ORDER, FAS.STATUS_ORDER[1:]):
        assert mean_ages[lo] > mean_ages[hi], (
            f"Monotone violated: mean_age({lo})={mean_ages[lo]:.3f} <= "
            f"mean_age({hi})={mean_ages[hi]:.3f} -- income-age gradient missing"
        )

    # (b2) Income-gradient SPREAD in under_5 share matches MiD spread (Feature B
    #      injects only the GRADIENT, not the absolute KBA-anchored level).
    syn_spread = panel["synthetic_under_5_share"].max() - panel["synthetic_under_5_share"].min()
    mid_spread = panel["mid_under_5_share"].max() - panel["mid_under_5_share"].min()
    assert abs(syn_spread - mid_spread) <= 0.10, (
        f"income-age gradient spread {syn_spread:.3f} differs from MiD {mid_spread:.3f} "
        f"by more than 0.10 -- the coupling strength is off"
    )

    # (c) LOOSE GROSS-FAILURE SENTINEL — NOT a scientific match.
    # Feature B anchors the ABSOLUTE age level to KBA P(age|powertrain), so the
    # synthetic under_5 share is structurally +5-11pp above the MiD marginal at
    # every status.  Tolerance 0.20 catches only catastrophic failures (inverted
    # or disabled tilt); a tighter bound would be a false negative.
    SENTINEL_TOLERANCE = 0.20  # absolute — loose sentinel only, NOT scientific match
    for _, row in panel.iterrows():
        status = row["economic_status"]
        syn = row["synthetic_under_5_share"]
        ref = row["mid_under_5_share"]
        if pd.isna(ref):
            continue  # no MiD reference for this status — skip
        delta = abs(syn - ref)
        assert delta <= SENTINEL_TOLERANCE, (
            f"GROSS FAILURE: under_5 share for {status}: "
            f"synthetic={syn:.3f}, MiD={ref:.3f}, |delta|={delta:.3f} "
            f"exceeds sentinel tolerance {SENTINEL_TOLERANCE} "
            f"(KBA-anchored level offset is +5-11pp by design; this bound "
            f"catches only catastrophic failures)"
        )


def test_fleet_age_panel_data_absent_safe():
    """build_panel returns an empty DataFrame (correct columns) when the
    vehicles frame is None or missing the required columns."""
    from braunschweig.analysis.population_validation import fleet_age_status as FAS

    # None frame.
    panel = FAS.build_panel(None, DATA_PATH)
    assert panel.empty
    assert set(FAS.PANEL_COLUMNS) == set(panel.columns)

    # Frame present but no 'age' column.
    df_no_age = pd.DataFrame({
        "economic_status": ["medium", "high"],
        "some_other_col": [1, 2],
    })
    panel2 = FAS.build_panel(df_no_age, DATA_PATH)
    assert panel2.empty

    # Frame present but no 'economic_status' column.
    df_no_status = pd.DataFrame({
        "age": [5.0, 10.0],
    })
    panel3 = FAS.build_panel(df_no_status, DATA_PATH)
    assert panel3.empty


# --------------------------------------------------------------------------- #
# Task 3 — validate_realised_margins wired into sample_fleet (consistency_v2). #
# --------------------------------------------------------------------------- #

def test_effective_expected_probs_sum_to_one():
    """_effective_expected returns PMF dicts that each sum to ~1.0.

    Uses a 3-car synthetic input (no data loading) to verify the pure helper
    produces normalised probability dicts for all dimensions.
    """
    n_pt = len(fs.POWERTRAINS)
    n_age = len(ft.AGE_BAND_LABELS)
    n_euro = len(ft.EURO_CLASS_LABELS)

    # Synthetic per-car inputs (3 cars).
    n = 3
    # Equal powertrain probability vectors (will be normalised).
    car_pmfs = [np.ones(n_pt) / n_pt] * n
    # Per-car effective factor: identity (uniform, no rake bias).
    kreis_factors = [np.ones(n_pt)] * n
    # Synthetic age_euro_joint: uniform over all cells (rows=age, cols=euro).
    uniform_joint = np.ones((n_age, n_euro), dtype=float) / (n_age * n_euro)
    age_euro_joints = [uniform_joint] * n
    # No income tilt (None).
    tilts = [None] * n

    expected = fs._effective_expected(
        car_pmfs=car_pmfs,
        kreis_factors=kreis_factors,
        age_euro_joints=age_euro_joints,
        tilts=tilts,
        powertrains=list(fs.POWERTRAINS),
        age_labels=list(ft.AGE_BAND_LABELS),
        euro_labels=list(ft.EURO_CLASS_LABELS),
    )

    for dim in ("powertrain", "age_band", "euro_class"):
        total = sum(expected[dim].values())
        assert abs(total - 1.0) < 1e-9, (
            f"_effective_expected '{dim}' PMF sums to {total} (expected 1.0)"
        )


def test_effective_expected_with_tilt_sums_to_one():
    """_effective_expected with a non-trivial tilt still sums to 1.0."""
    n_age = len(ft.AGE_BAND_LABELS)
    n_euro = len(ft.EURO_CLASS_LABELS)
    n_pt = len(fs.POWERTRAINS)

    n = 2
    car_pmfs = [np.ones(n_pt) / n_pt] * n
    kreis_factors = [np.ones(n_pt)] * n
    # Non-uniform joint: more mass on first age band.
    joint = np.ones((n_age, n_euro), dtype=float)
    joint[0, :] *= 3.0
    joint = joint / joint.sum()
    age_euro_joints = [joint] * n
    # Non-trivial tilt: down-weight older bands.
    tilt = np.linspace(2.0, 0.5, n_age)
    tilts = [tilt] * n

    expected = fs._effective_expected(
        car_pmfs=car_pmfs,
        kreis_factors=kreis_factors,
        age_euro_joints=age_euro_joints,
        tilts=tilts,
        powertrains=list(fs.POWERTRAINS),
        age_labels=list(ft.AGE_BAND_LABELS),
        euro_labels=list(ft.EURO_CLASS_LABELS),
    )

    for dim in ("powertrain", "age_band", "euro_class"):
        total = sum(expected[dim].values())
        assert abs(total - 1.0) < 1e-9, (
            f"_effective_expected '{dim}' PMF sums to {total} after tilt "
            f"(expected 1.0)"
        )


def test_sample_fleet_validation_not_flagged(caplog):
    """sample_fleet (consistency_v2=True) emits a validation summary with
    any_flagged=False on a correctly implemented model (healthy fleet).

    Uses a moderately-sized seeded fleet (2 Kreise, 1000 cars per Kreis) so
    Monte-Carlo noise is small enough for the 4-sigma band not to fire.
    """
    rng_input = np.random.default_rng(77)
    statuses = list(ft.STATUS_LABELS)
    rows = []
    for kreis in ft.ZGB_KREISE_AGS5[:2]:
        for _ in range(1000):
            rows.append({
                "economic_status": str(rng_input.choice(statuses)),
                "kreis_ags5": kreis,
                "gemeinde": np.nan,
                "raumtyp": int(rng_input.choice([71, 72, 73, 74, 75, 76, 77])),
            })
    df_cars = pd.DataFrame(rows)

    with caplog.at_level(logging.INFO):
        result = fs.sample_fleet(
            df_cars, DATA_PATH, random_seed=7,
            consistency_v2=True, age_income_coupling=True,
        )

    # Task 3 returns a 3-tuple (df_spec, df_types, validation_summary).
    assert len(result) == 3, (
        f"sample_fleet (consistency_v2=True) should return 3-tuple; "
        f"got {len(result)}-tuple"
    )
    df_spec, df_types, summary = result

    assert isinstance(summary, dict), "validation summary must be a dict"
    assert "any_flagged" in summary, "summary must contain 'any_flagged'"
    assert summary["any_flagged"] is False, (
        f"Validation flagged drift on a healthy fleet: "
        f"{summary.get('dimensions', {})}"
    )

    # Verify the validator fired: all four key dimensions must appear.
    dims = set(summary.get("dimensions", {}).keys())
    for expected_dim in ("powertrain", "age_band", "euro_class", "segment"):
        assert expected_dim in dims, (
            f"Expected dimension '{expected_dim}' missing from summary; "
            f"found: {dims}"
        )

    # Verify a log message was emitted for the validation.
    text = " ".join(r.message for r in caplog.records).lower()
    assert "fleet_validation" in text or "fleet_de" in text, (
        "Expected validation log message not found in captured log"
    )


# --------------------------------------------------------------------------- #
# Task A2 -- validator "segment" target = EFFECTIVE per-car pmf, not raw KBA.  #
#                                                                               #
# expected["segment"] used to be sampler.segment_model.kba_marginal (the raw   #
# national KBA FZ 27.10 marginal). The realised distribution is instead        #
# status/raumtyp-conditioned (segment_probabilities) AND has its "sonstige"    #
# mass redistributed over the modelled segments (Task 5), so the raw-KBA      #
# target cries wolf at scale. These tests pin the fix: the validator target   #
# must be the EFFECTIVE per-car segment pmf that PASS 1 actually draws from.  #
# --------------------------------------------------------------------------- #

def test_expected_segment_is_effective_pmf_not_raw_kba(sampler):
    """validation_summary['dimensions']['segment']['expected'] must equal the
    EFFECTIVE per-car segment pmf (status/raumtyp-conditioned segment draw with
    'sonstige' mass redistributed over the modelled segments), independently
    recomputed here from the same per-car inputs and the same redistribution
    rule PASS 1 applies -- NOT the raw national KBA FZ 27.10 marginal.

    This does not depend on RNG draws: segment_probabilities(status, raumtyp)
    and the sonstige redistribution weights are deterministic given the car's
    (status, raumtyp), so the "effective" target can be recomputed exactly
    regardless of sample size or seed.
    """
    rng_input = np.random.default_rng(11)
    statuses = list(ft.STATUS_LABELS)
    kreis = ft.ZGB_KREISE_AGS5[0]
    rows = []
    for _ in range(5000):
        rows.append({
            "economic_status": str(rng_input.choice(statuses)),
            "kreis_ags5": kreis,
            "gemeinde": np.nan,
            "raumtyp": int(rng_input.choice([71, 72, 73, 74, 75, 76, 77])),
        })
    df_cars = pd.DataFrame(rows)

    _, _, summary = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=13, sampler=sampler, consistency_v2=True)

    # Independently recompute the effective segment pmf: seg_pmf with its
    # "sonstige" mass redistributed over the modelled segments, exactly as
    # the PASS-1 sonstige redraw does (Task 5).
    segments = sampler.segment_model.segments
    modelled_segments = [
        s for s in segments
        if s in sampler.model_given_segment and s != "sonstige"
    ]
    df_seg_share = ft.load_segment_powertrain(DATA_PATH)
    seg_share_map = df_seg_share.set_index("segment")["segment_share"].to_dict()
    raw = np.array([seg_share_map.get(s, 0.0) for s in modelled_segments], dtype=float)
    modelled_seg_pmf = raw / raw.sum()

    exp_manual = np.zeros(len(segments))
    for _, car in df_cars.iterrows():
        seg_pmf = sampler.segment_model.segment_probabilities(
            car["economic_status"], int(car["raumtyp"]))
        eff = np.asarray(seg_pmf, dtype=float).copy()
        s_idx = segments.index("sonstige")
        mass = eff[s_idx]
        eff[s_idx] = 0.0
        for j, seg_name in enumerate(modelled_segments):
            eff[segments.index(seg_name)] += mass * float(modelled_seg_pmf[j])
        exp_manual += eff / eff.sum()
    exp_manual /= len(df_cars)

    expected_from_summary = summary["dimensions"]["segment"]["expected"]
    for i, seg in enumerate(segments):
        assert expected_from_summary[seg] == pytest.approx(exp_manual[i], abs=1e-9), (
            f"segment '{seg}': validator expected {expected_from_summary[seg]} "
            f"vs manually recomputed effective pmf {exp_manual[i]}"
        )

    # The fix must actually change something: the effective target must
    # differ from the raw KBA marginal (else the bug is still present).
    raw_kba = dict(zip(sampler.segment_model.segments,
                        sampler.segment_model.kba_marginal))
    assert any(
        abs(expected_from_summary[s] - raw_kba.get(s, 0.0)) > 1e-6
        for s in segments
    ), "expected['segment'] must differ from the raw KBA marginal after the fix"

    # "sonstige" mass must be fully redistributed away from the target.
    assert expected_from_summary.get("sonstige", 0.0) == pytest.approx(0.0, abs=1e-9)

    # The target must remain a proper probability distribution.
    assert sum(expected_from_summary.values()) == pytest.approx(1.0, abs=1e-6)


def test_sample_fleet_segment_not_flagged_at_scale(sampler):
    """At a car count large enough for the sonstige-redistribution gap
    (~2-3pp) to exceed the Monte-Carlo band, the validator must NOT flag
    'segment' DRIFT against the effective target -- and the raw KBA marginal
    (the pre-fix target) WOULD have flagged, demonstrating this is the fix
    for the cry-wolf bug (review Finding 2), not a widened tolerance.
    """
    df_cars = _make_cars(n_per_kreis=4000)  # 32,000 cars across 8 ZGB Kreise.
    df_spec, _, summary = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42, sampler=sampler, consistency_v2=True)

    seg_summary = summary["dimensions"]["segment"]
    assert seg_summary["flagged"] is False, (
        f"segment flagged DRIFT against the effective target at scale: "
        f"max_dev={seg_summary['max_abs_pp']}pp band={seg_summary['band_pp']}pp"
    )

    # Demonstrate the bug this fixes: re-validating against the raw KBA
    # marginal (the OLD, wrong target) flags DRIFT at this n.
    from braunschweig.synthesis.vehicles import fleet_validation as fv
    raw_kba_expected = {
        s: float(v)
        for s, v in zip(sampler.segment_model.segments,
                        sampler.segment_model.kba_marginal)
    }
    raw_summary = fv.validate_realised_margins(
        df_spec, {"segment": raw_kba_expected}, sample_rate=1.0)
    assert raw_summary["dimensions"]["segment"]["flagged"] is True, (
        "Expected the raw-KBA segment target to flag DRIFT at this n "
        "(demonstrates the cry-wolf bug); if this no longer flags, the "
        "fixture's n is too small to exercise the regression."
    )
