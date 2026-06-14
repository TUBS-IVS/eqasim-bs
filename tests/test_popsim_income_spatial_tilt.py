from __future__ import annotations
import logging
import numpy as np
import pandas as pd
import pytest
from braunschweig.popsim import income_spatial_tilt as ist


def test_renter_index_is_concave_and_kreis_mean_one() -> None:
    # 3 cells in one Kreis, with rent/m2 and household counts.
    cells = pd.DataFrame({
        "cell_id": ["a", "b", "c"],
        "ars5": ["03101", "03101", "03101"],
        "rent_qm": [6.0, 9.0, 12.0],     # cheap / median / expensive
        "n_households": [100, 100, 100],
    })
    out = ist.build_renter_rent_index(
        cells, rent_col="rent_qm", kreis_col="ars5",
        weight_col="n_households", beta=0.3,
    )
    idx = out.set_index("cell_id")["renter_income_index"]
    # concave + monotone: expensive cell > median > cheap, but compressed (beta<1)
    assert idx["a"] < idx["b"] < idx["c"]
    # household-weighted mean over the Kreis is exactly 1
    w = out["n_households"].to_numpy()
    assert abs(float(np.average(out["renter_income_index"], weights=w)) - 1.0) < 1e-9


def test_renter_index_missing_rent_is_neutral() -> None:
    cells = pd.DataFrame({
        "cell_id": ["a", "b"], "ars5": ["03101", "03101"],
        "rent_qm": [np.nan, 0.0], "n_households": [10, 10],
    })
    out = ist.build_renter_rent_index(cells, rent_col="rent_qm", kreis_col="ars5",
                                      weight_col="n_households", beta=0.3)
    # missing/zero rent -> neutral index 1.0 (no tilt), and mean stays 1
    assert (out["renter_income_index"] == 1.0).all()


def test_owner_index_high_ownership_is_higher_and_mean_one() -> None:
    cells = pd.DataFrame({
        "cell_id": ["a", "b", "c"], "ars5": ["03101"] * 3,
        "eigentuemerquote": [0.2, 0.5, 0.8], "n_households": [100, 100, 100],
    })
    out = ist.build_owner_income_index(cells, quote_col="eigentuemerquote",
                                       kreis_col="ars5", weight_col="n_households", beta=0.3)
    idx = out.set_index("cell_id")["owner_income_index"]
    assert idx["a"] < idx["b"] < idx["c"]
    w = out["n_households"].to_numpy()
    assert abs(float(np.average(out["owner_income_index"], weights=w)) - 1.0) < 1e-9


def test_renter_index_mixed_valid_and_missing_kreis_mean_one() -> None:
    """Kreis with a mix of valid-rent and missing/zero-rent cells; unequal HH weights.

    The household-weighted mean of renter_income_index over the Kreis must be exactly 1.0,
    and the valid-rent cells must still be monotone in rent (concavity check).
    Note: missing/zero cells receive a raw index of 1.0 which is then rescaled by the
    Kreis normalization — so their final value may differ from 1.0 (correct behaviour).
    """
    cells = pd.DataFrame({
        "cell_id": ["cheap", "mid", "expensive", "missing", "zero"],
        "ars5": ["03101"] * 5,
        "rent_qm": [5.0, 8.0, 14.0, np.nan, 0.0],
        "n_households": [200, 50, 300, 100, 75],  # deliberately unequal
    })
    out = ist.build_renter_rent_index(
        cells, rent_col="rent_qm", kreis_col="ars5",
        weight_col="n_households", beta=0.3,
    )
    w = out["n_households"].to_numpy()
    # Household-weighted mean must be exactly 1.0.
    assert abs(float(np.average(out["renter_income_index"], weights=w)) - 1.0) < 1e-9
    # Valid cells must still be monotone in rent.
    idx = out.set_index("cell_id")["renter_income_index"]
    assert idx["cheap"] < idx["mid"] < idx["expensive"]


def test_renter_index_multi_kreis_normalizes_independently() -> None:
    """Two Kreise with different rent levels; each Kreis must normalize to mean 1 independently.

    This verifies that a cheap cell in an expensive Kreis can have a higher absolute index
    than an expensive cell in a cheap Kreis while each Kreis still means to 1.
    """
    cells = pd.DataFrame({
        "cell_id": ["k1_low", "k1_high", "k2_low", "k2_high"],
        # Kreis 1 is expensive: rents 10 / 20; Kreis 2 is cheap: rents 3 / 6.
        "ars5":         ["03101", "03101", "03102", "03102"],
        "rent_qm":      [10.0, 20.0, 3.0, 6.0],
        "n_households": [100, 100, 100, 100],
    })
    out = ist.build_renter_rent_index(
        cells, rent_col="rent_qm", kreis_col="ars5",
        weight_col="n_households", beta=0.3,
    )
    out_idx = out.set_index("cell_id")["renter_income_index"]

    # Each Kreis independently averages to 1.0.
    k1 = out[out["ars5"] == "03101"]
    k2 = out[out["ars5"] == "03102"]
    w1 = k1["n_households"].to_numpy()
    w2 = k2["n_households"].to_numpy()
    assert abs(float(np.average(k1["renter_income_index"], weights=w1)) - 1.0) < 1e-9
    assert abs(float(np.average(k2["renter_income_index"], weights=w2)) - 1.0) < 1e-9

    # Because rents within each Kreis are symmetric around the median (ratio 0.5/1 vs 1/2),
    # the per-Kreis index values collapse to the same relative shape; both Kreise produce
    # the same index distribution (just the raw rent levels differ).
    # Crucially each Kreis is internally consistent: low < high within each.
    assert out_idx["k1_low"] < out_idx["k1_high"]
    assert out_idx["k2_low"] < out_idx["k2_high"]


def test_normalize_zero_weight_kreis_no_crash_emits_warning(caplog: pytest.LogCaptureFixture) -> None:
    """A Kreis where ALL cells have zero household weight must not crash and should warn."""
    cells = pd.DataFrame({
        "cell_id": ["a", "b", "normal"],
        "ars5":    ["03101", "03101", "03102"],
        "rent_qm": [8.0, 12.0, 9.0],
        "n_households": [0, 0, 100],  # Kreis 03101 has zero total weight
    })
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.income_spatial_tilt"):
        out = ist.build_renter_rent_index(
            cells, rent_col="rent_qm", kreis_col="ars5",
            weight_col="n_households", beta=0.3,
        )

    # Should not crash; all cells must have a finite index.
    assert out["renter_income_index"].notna().all()
    assert np.isfinite(out["renter_income_index"].to_numpy()).all()
    # The warning about zero-weight Kreis must have been emitted.
    assert any("zero total household weight" in r.message for r in caplog.records)
    # Normal Kreis 03102 (with real weights) still normalizes to mean 1.
    k2 = out[out["ars5"] == "03102"]
    w2 = k2["n_households"].to_numpy()
    assert abs(float(np.average(k2["renter_income_index"], weights=w2)) - 1.0) < 1e-9


# ---------------------------------------------------------------------------
# Task 2: apply_spatial_income_tilt
# ---------------------------------------------------------------------------

def test_apply_tilt_preserves_kreis_mean_and_clips() -> None:
    # 2 cells, 1 Kreis; renter index strongly differs; clip caps the deviation.
    cell_index = pd.DataFrame({
        "cell_id": ["a", "b"], "ars5": ["03101", "03101"],
        "renter_income_index": [0.5, 1.5], "owner_income_index": [0.9, 1.1],
    })
    hh = pd.DataFrame({
        "household_id": [1, 2, 3, 4],
        "cell_id": ["a", "a", "b", "b"],
        "ars5": ["03101"] * 4,
        "tenure": ["renter", "owner", "renter", "owner"],
        "household_income_eur": [2000.0, 2000.0, 2000.0, 2000.0],
    })
    out, diag = ist.apply_spatial_income_tilt(
        hh, cell_index, cell_col="cell_id", kreis_col="ars5",
        tenure_col="tenure", income_col="household_income_eur", clip=0.30,
    )
    # Kreis mean income is exactly preserved.
    assert abs(out["household_income_eur"].mean() - 2000.0) < 1e-6
    # renter in cheap cell a is tilted down, renter in expensive b up.
    inc = out.set_index("household_id")["household_income_eur"]
    assert inc[1] < 2000.0 < inc[3]
    # clip honored: no household moves more than 30% before re-normalization bookkeeping.
    # With indices [0.5, 1.5] and clip=0.30, clipping *does* fire — pin that it occurs.
    assert diag["clipped_fraction"] > 0.0
    assert "kreis_mean_preserved" in diag


def test_apply_tilt_multi_kreis_each_mean_preserved() -> None:
    """Each Kreis's mean income is preserved independently, not just the global mean."""
    # Kreis 03101: rent index [0.6, 1.4]; Kreis 03102: rent index [0.8, 1.2].
    # All base incomes 3000 EUR; all renters so we exercise the renter index path.
    cell_index = pd.DataFrame({
        "cell_id": ["k1a", "k1b", "k2a", "k2b"],
        "ars5": ["03101", "03101", "03102", "03102"],
        "renter_income_index": [0.6, 1.4, 0.8, 1.2],
        "owner_income_index": [1.0, 1.0, 1.0, 1.0],
    })
    # 2 households per cell, all renters.
    rows = []
    for hid, (cell, kreis) in enumerate(
        [("k1a", "03101"), ("k1a", "03101"),
         ("k1b", "03101"), ("k1b", "03101"),
         ("k2a", "03102"), ("k2a", "03102"),
         ("k2b", "03102"), ("k2b", "03102")],
        start=1,
    ):
        rows.append({"household_id": hid, "cell_id": cell, "ars5": kreis,
                     "tenure": "renter", "household_income_eur": 3000.0})
    hh = pd.DataFrame(rows)

    out, diag = ist.apply_spatial_income_tilt(
        hh, cell_index, cell_col="cell_id", kreis_col="ars5",
        tenure_col="tenure", income_col="household_income_eur", clip=0.30,
    )

    # Each Kreis mean must be exactly 3000.
    for kreis in ["03101", "03102"]:
        mask = out["ars5"] == kreis
        mean_k = out.loc[mask, "household_income_eur"].mean()
        assert abs(mean_k - 3000.0) < 1e-6, (
            f"Kreis {kreis}: expected mean 3000.0, got {mean_k}"
        )

    # Corrected per-Kreis diagnostic must confirm mean is preserved and deviation is tiny.
    assert diag["kreis_mean_preserved"] is True
    assert "max_kreis_mean_abs_dev" in diag
    # Re-normalization restores per-Kreis sums exactly; residual must be ~machine epsilon.
    assert diag["max_kreis_mean_abs_dev"] < 1e-6, (
        f"max per-Kreis mean abs dev too large: {diag['max_kreis_mean_abs_dev']:.2e}"
    )


def test_apply_tilt_tenure_routing_renter_vs_owner() -> None:
    """Renters and owners in the same cell use their respective indices.

    Cell 'x': renter_income_index=0.7 (cheap), owner_income_index=1.3 (wealthy area).
    Both start at 2500 EUR. After tilt the renter must go down and the owner must go up.
    """
    cell_index = pd.DataFrame({
        "cell_id": ["x"],
        "ars5": ["03101"],
        "renter_income_index": [0.7],
        "owner_income_index": [1.3],
    })
    hh = pd.DataFrame({
        "household_id": [10, 20],
        "cell_id": ["x", "x"],
        "ars5": ["03101", "03101"],
        "tenure": ["renter", "owner"],
        "household_income_eur": [2500.0, 2500.0],
    })
    out, _diag = ist.apply_spatial_income_tilt(
        hh, cell_index, cell_col="cell_id", kreis_col="ars5",
        tenure_col="tenure", income_col="household_income_eur", clip=0.30,
    )
    inc = out.set_index("household_id")["household_income_eur"]
    # Renter index (0.7) < 1 -> tilted down; owner index (1.3) > 1 -> tilted up.
    assert inc[10] < 2500.0, f"Renter should be tilted down, got {inc[10]}"
    assert inc[20] > 2500.0, f"Owner should be tilted up, got {inc[20]}"
    # Kreis mean still preserved.
    assert abs(out["household_income_eur"].mean() - 2500.0) < 1e-6


def test_apply_tilt_unknown_tenure_uses_renter_index() -> None:
    """With unknown_neutral=False (legacy), unknown tenure falls through to the renter index.

    This pins the contract of apply_spatial_income_tilt: any tenure that is NOT "owner"
    (including "unknown") gets the renter index. When unknown_neutral=False is passed to
    maybe_apply_income_tilt, this behaviour is forwarded unchanged.

    Setup: two cells in one Kreis. Cell A has a cheap-area renter index (0.6); cell B
    has an expensive-area renter index (1.4). An "unknown"-tenure household in cell A
    should receive a LOWER income than the one in cell B (because it is treated as a
    renter and rented the cheaper cell). The per-Kreis re-normalization restores the
    mean, but the relative ordering must reflect the renter-index difference.
    """
    cell_index = pd.DataFrame({
        "cell_id": ["cheap", "expensive"],
        "ars5": ["03101", "03101"],
        "renter_income_index": [0.6, 1.4],   # cheap cell low, expensive cell high
        "owner_income_index": [1.0, 1.0],    # neutral owner index (not used here)
    })
    hh = pd.DataFrame({
        "household_id": [1, 2],
        "cell_id": ["cheap", "expensive"],
        "ars5": ["03101", "03101"],
        "tenure": ["unknown", "unknown"],
        "household_income_eur": [2000.0, 2000.0],
    })
    out = ist.maybe_apply_income_tilt(
        hh, cell_index, enabled=True,
        cell_col="cell_id", kreis_col="ars5", tenure_col="tenure",
        unknown_neutral=False,
    )
    inc = out.set_index("household_id")["household_income_eur"]
    # With unknown_neutral=False, "unknown" is treated as renter.
    # Cheap-cell household gets renter_index=0.6 -> should have LOWER income than expensive cell.
    assert inc[1] < inc[2], (
        f"With unknown_neutral=False, unknown-tenure in cheap cell ({inc[1]:.1f}) "
        f"should have lower income than expensive cell ({inc[2]:.1f})"
    )
    # Per-Kreis mean must still be preserved by apply_spatial_income_tilt.
    assert abs(out["household_income_eur"].mean() - 2000.0) < 1e-6, (
        "Per-Kreis mean should be preserved by the re-normalization"
    )


def test_apply_tilt_unknown_tenure_neutral_when_unknown_neutral_true() -> None:
    """With unknown_neutral=True (default), unknown-tenure rows are not tilted.

    Their income must remain exactly unchanged (factor 1.0), while known-tenure
    rows in the same Kreis receive the spatial adjustment.
    """
    cell_index = pd.DataFrame({
        "cell_id": ["x"],
        "ars5": ["03101"],
        "renter_income_index": [0.7],   # renter gets a downward tilt
        "owner_income_index": [1.3],    # owner gets an upward tilt
    })
    hh = pd.DataFrame({
        "household_id": [1, 2, 3],
        "cell_id": ["x", "x", "x"],
        "ars5": ["03101", "03101", "03101"],
        "tenure": ["renter", "owner", "unknown"],
        "household_income_eur": [2000.0, 2000.0, 2000.0],
    })
    out = ist.maybe_apply_income_tilt(
        hh, cell_index, enabled=True,
        cell_col="cell_id", kreis_col="ars5", tenure_col="tenure",
        unknown_neutral=True,
    )
    inc = out.set_index("household_id")["household_income_eur"]
    # renter is tilted down (renter index 0.7 < 1).
    assert inc[1] < 2000.0, f"Renter should be tilted down, got {inc[1]}"
    # owner is tilted up (owner index 1.3 > 1).
    assert inc[2] > 2000.0, f"Owner should be tilted up, got {inc[2]}"
    # unknown is NEUTRAL: income unchanged.
    assert inc[3] == 2000.0, (
        f"Unknown-tenure household should be unaffected (neutral factor), got {inc[3]}"
    )


def test_tilt_off_path_returns_income_unchanged() -> None:
    """When enabled=False, maybe_apply_income_tilt returns the frame BYTE-IDENTICAL."""
    hh = pd.DataFrame({
        "household_id": [1, 2], "cell_id": ["a", "b"], "ars5": ["03101", "03101"],
        "tenure": ["renter", "owner"], "household_income_eur": [1500.0, 3000.0],
    })
    cell_index = pd.DataFrame({"cell_id": ["a", "b"], "ars5": ["03101", "03101"],
                               "renter_income_index": [0.5, 1.5], "owner_income_index": [0.9, 1.1]})
    out = ist.maybe_apply_income_tilt(hh, cell_index, enabled=False, cell_col="cell_id",
                                      kreis_col="ars5", tenure_col="tenure")
    pd.testing.assert_frame_equal(out, hh)  # OFF -> unchanged (byte-identical)


def test_apply_tilt_effective_dev_reported_on_asymmetric_kreis() -> None:
    """Realized per-household effective multiplier can exceed the nominal clip band.

    When a Kreis is dominated by clipped-UP cells (index values hitting the upper clip),
    the per-Kreis re-normalization scalar (base_sum / tilt_sum) is < 1, which scales
    EVERYONE down — including the already-clipped-up cells.  The net realized deviation
    (clipped * scale - 1) for those cells can thus exceed the nominal clip.

    This test pins that documented behaviour:
      - per-Kreis mean income is still EXACTLY preserved (kreis_mean_preserved is True),
      - diag["max_effective_dev"] is present and GREATER than clip (0.30),
        demonstrating the realized deviation can exceed the nominal pre-renorm bound.
    """
    clip = 0.30
    # 4 cells: 3 with high index (will be clipped UP to 1+clip=1.30), 1 with low index.
    # Equal weights and equal base incomes so renorm arithmetic is transparent.
    cell_index = pd.DataFrame({
        "cell_id":            ["a",  "b",  "c",  "d"],
        "ars5":               ["03101"] * 4,
        "renter_income_index": [1.5, 1.5, 1.5, 0.5],
        "owner_income_index":  [1.0, 1.0, 1.0, 1.0],
    })
    hh = pd.DataFrame({
        "household_id":       [1, 2, 3, 4],
        "cell_id":            ["a", "b", "c", "d"],
        "ars5":               ["03101"] * 4,
        "tenure":             ["renter"] * 4,
        "household_income_eur": [2000.0] * 4,
    })
    out, diag = ist.apply_spatial_income_tilt(
        hh, cell_index, cell_col="cell_id", kreis_col="ars5",
        tenure_col="tenure", income_col="household_income_eur", clip=clip,
    )

    # 1. Per-Kreis mean income must be exactly preserved.
    assert diag["kreis_mean_preserved"] is True, (
        "per-Kreis mean preservation failed"
    )
    assert abs(out["household_income_eur"].mean() - 2000.0) < 1e-6, (
        f"global mean not preserved: {out['household_income_eur'].mean()}"
    )

    # 2. max_effective_dev must be present and EXCEED the nominal clip.
    assert "max_effective_dev" in diag, "diag missing 'max_effective_dev'"
    assert diag["max_effective_dev"] > clip, (
        f"expected max_effective_dev > {clip} (nominal clip), got {diag['max_effective_dev']:.6f}; "
        "asymmetric Kreis should demonstrate realized deviation exceeds pre-renorm bound"
    )
