"""End-to-end fleet consistency tests (Task 9).

Two test groups:
1. OFF-path byte-identical regression: sample_fleet(..., consistency_v2=False)
   must match a committed golden fixture generated on this feature branch.
   Guards against unintended drift of the legacy path.

2. v2 consistency validation: sample_fleet + attach_hsn_tsn in v2 mode on
   real data must satisfy the Plan-1 acceptance invariants:
   - 0 powertrain/fuel_detail contradictions
   - top-fingerprint share < 5% (old global-median bug put 5.3% on ONE pair)
   - brand-empty rate == 0
   - per-Kreis BEV share within abs 0.01 of FZ 27.15
"""

from __future__ import annotations

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
from braunschweig.data.kba import hsn_tsn  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

DATA_PATH = str(DATA)
GOLDEN_PATH = FIXTURES / "fleet_v1_golden.parquet"


# --------------------------------------------------------------------------- #
# Shared synthetic car frame
# --------------------------------------------------------------------------- #
def _make_cars(n_per_kreis: int = 4000, seed: int = 0) -> pd.DataFrame:
    """Synthetic household-car frame matching the test fixture in test_fleet_sampling_de."""
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


def _load_golden(name: str) -> pd.DataFrame:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"golden fixture not found: {path}")
    return pd.read_parquet(path)


# --------------------------------------------------------------------------- #
# Module-scoped fixtures (build the real sampler + v2 output once per session)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def sampler():
    return fs.FleetSampler.from_data_path(DATA_PATH)


@pytest.fixture(scope="module")
def v2_output_with_hsn(sampler):
    """Full v2 pipeline: sample_fleet (v2) + attach_hsn_tsn on real data."""
    df_cars = _make_cars()
    spec, _, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42, sampler=sampler, consistency_v2=True)
    try:
        out = hsn_tsn.attach_hsn_tsn(spec, data_path=DATA_PATH, random_seed=42)
    except FileNotFoundError:
        pytest.skip(
            "HSN/TSN lookup is local-only (run scripts/scrape_hsn_tsn.py); skipped when absent"
        )
    return out


# --------------------------------------------------------------------------- #
# 1. OFF-path byte-identical regression
# --------------------------------------------------------------------------- #
def test_off_path_byte_identical(sampler):
    """consistency_v2=False output must match the committed golden fixture exactly.

    The golden was generated on this feature branch by running
    ``sample_fleet(..., consistency_v2=False)`` — it is NOT a snapshot from the
    pre-feature main head.  The OFF==pre-feature-main equivalence rests on the
    legacy code path being a verbatim copy of the original loop, which was
    verified during task reviews.  The generation command was:

        python -c \"
        import numpy as np, pandas as pd, sys; sys.path.insert(0,'.')
        from braunschweig.data.kba import fleet_tables as ft
        from braunschweig.synthesis.vehicles import fleet_sampling_de as fs
        rng = np.random.default_rng(0); statuses = list(ft.STATUS_LABELS)
        rows = [{'economic_status': rng.choice(statuses), 'kreis_ags5': k,
                 'gemeinde': float('nan'), 'raumtyp': int(rng.choice(range(71,78)))}
                for k in ft.ZGB_KREISE_AGS5 for _ in range(4000)]
        df, _ = fs.sample_fleet(pd.DataFrame(rows), 'eqasim-data/data',
                                random_seed=42, consistency_v2=False)
        df.to_parquet('tests/fixtures/fleet_v1_golden.parquet', index=False)
        \"

    The golden is committed to tests/fixtures/fleet_v1_golden.parquet.
    """
    df_cars = _make_cars()
    off, _ = fs.sample_fleet(
        df_cars, DATA_PATH, random_seed=42, sampler=sampler, consistency_v2=False)
    golden = _load_golden("fleet_v1_golden.parquet")
    pd.testing.assert_frame_equal(
        off.reset_index(drop=True),
        golden.reset_index(drop=True),
        check_like=False,
        obj="OFF-path vs golden",
    )


# --------------------------------------------------------------------------- #
# 2. v2 consistency invariants
# --------------------------------------------------------------------------- #
def test_v2_zero_fuel_contradictions(v2_output_with_hsn):
    """0 powertrain/fuel_detail contradictions in the v2 output.

    Validates Plan-1 spec C2: the per-vehicle chain + attach_hsn_tsn together
    must produce no (diesel, 'Benzin'), (petrol, 'Diesel') or (bev, not-'Elektro')
    combinations.
    """
    out = v2_output_with_hsn
    assert "fuel_detail" in out.columns, "attach_hsn_tsn must produce fuel_detail"
    fd = out["fuel_detail"].str.lower()
    pt = out["powertrain"].str.lower()
    diesel_with_benzin = (pt == "diesel") & fd.str.contains("benzin", na=False)
    petrol_with_diesel = (pt == "petrol") & fd.str.contains("diesel", na=False)
    bev_not_elektro = (pt == "bev") & ~fd.str.contains("elektro", na=False)
    total_bad = (
        diesel_with_benzin.sum()
        + petrol_with_diesel.sum()
        + bev_not_elektro.sum()
    )
    assert total_bad == 0, (
        f"fuel contradictions: diesel/Benzin={diesel_with_benzin.sum()}, "
        f"petrol/Diesel={petrol_with_diesel.sum()}, "
        f"bev/not-Elektro={bev_not_elektro.sum()}"
    )


def test_v2_no_degenerate_single_fingerprint(v2_output_with_hsn):
    """Top engine fingerprint < 5% of fleet (old global-median bug: 5.3%).

    The (engine_power_kw, displacement_ccm) pair must not be dominated by a
    single global-median fallback that inflates one pair to >=5% of all cars.
    The old bug pinned a single median fingerprint at 5.3%; after the fix the
    top pair is a legitimate VW engine variant at ~3.8% (exact HSN/TSN match,
    not a fallback). The threshold is set to 5% to catch any regression back to
    the global-median bug while allowing realistic VW market-share concentration.
    """
    out = v2_output_with_hsn
    assert "engine_power_kw" in out.columns, "attach_hsn_tsn must produce engine_power_kw"
    fp = out.groupby(["engine_power_kw", "displacement_ccm"]).size()
    top_share = float(fp.max()) / len(out)
    assert top_share < 0.05, (
        f"top fingerprint share {top_share:.4f} >= 0.05 (old global-median bug: 0.053); "
        f"likely a global-median fallback is dominating"
    )

    # Sub-assertion: the top fingerprint must NOT be dominated by the global-median
    # fallback tier.  A legitimate popular engine matches at exact/model tier; a
    # fallback collapse concentrates global-tier assignments onto a single pair.
    top_pair = fp.idxmax()
    top_mask = (
        (out["engine_power_kw"] == top_pair[0])
        & (out["displacement_ccm"] == top_pair[1])
    )
    global_frac = float((out.loc[top_mask, "hsn_tsn_match_tier"] == "global").mean())
    assert global_frac < 0.10, (
        f"top engine fingerprint {top_pair} is {global_frac:.1%} global-median "
        f"fallback -- looks like a fallback collapse, not a real popular engine"
    )


def test_v2_brand_empty_rate_zero(v2_output_with_hsn):
    """consistency_v2=True must produce brand-empty rate == 0.

    All cars are redistributed out of sonstige (Task 5) so no car carries an
    empty brand. This is an exact assertion: any brand-empty car is a regression.
    """
    out = v2_output_with_hsn
    brand_empty = (out["brand"].astype(str).str.strip() == "").sum()
    assert brand_empty == 0, (
        f"{brand_empty}/{len(out)} cars have an empty brand in v2 path"
    )


def test_v2_per_kreis_bev_share_within_abs_001(v2_output_with_hsn):
    """Per-Kreis BEV share in v2 output matches FZ 27.15 within abs 0.01.

    Reuses the same assertion as test_fleet_sampling_de::test_per_kreis_bev_share
    but on the FULL v2 pipeline (sample_fleet + attach_hsn_tsn) so the e2e
    output is validated, not just the sampler output.
    """
    out = v2_output_with_hsn
    df_kreis = ft.load_kreis_powertrain(DATA_PATH).set_index("kreis_ags5")
    for kreis in ft.ZGB_KREISE_AGS5:
        sub = out[out["kreis_ags5"] == kreis]
        sampled_share = float((sub["powertrain"] == "bev").mean())
        target = float(df_kreis.loc[kreis, "bev_share"])
        assert sampled_share == pytest.approx(target, abs=0.01), (
            f"Kreis {kreis}: sampled BEV {sampled_share:.4f} vs "
            f"FZ 27.15 {target:.4f} (delta {abs(sampled_share - target):.4f})"
        )


def test_v2_provenance_columns_present(v2_output_with_hsn):
    """All three provenance columns must be present in the full v2 pipeline output.

    brand_source, powertrain_feasibility (from sample_fleet) and
    hsn_tsn_match_tier (from attach_hsn_tsn with keep_tier=True) must travel
    together to the final df_vehicles.
    """
    out = v2_output_with_hsn
    for col in ("brand_source", "powertrain_feasibility", "hsn_tsn_match_tier"):
        assert col in out.columns, (
            f"provenance column '{col}' missing from v2 output; "
            f"found: {[c for c in out.columns if 'source' in c or 'tier' in c or 'feasib' in c]}"
        )
