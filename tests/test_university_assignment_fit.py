"""The residents' flow fit and the unbiased controlled rounding (issue #140, Phase 4, design 5a, B5, B6).

Defects these tests catch: a rounding that breaks a margin (a site or a home Kreis gains or loses a student), a
biased rounding (students drift between sites in expectation), a rounding that ignores its generator, a fit on the
heads of the table instead of the persons present (two away rates), a destination that silently loses its share,
and a row without persons that is not reported.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.locations.university_assignment import controlled_round, fit_flow_matrix

#: The integrality tolerance of the module's contract (values this close to an integer count as that integer).
_EPS = 1e-9


def _assert_floor_or_ceil(rounded: np.ndarray, real: np.ndarray, what: str) -> None:
    lower = np.floor(real + _EPS)
    upper = np.ceil(real - _EPS)
    assert ((rounded >= lower) & (rounded <= upper)).all(), f"{what}: {rounded} not within floor/ceil of {real}"


def _random_matrix(rng: np.random.Generator, integral_margins: bool) -> pd.DataFrame:
    n_rows, n_cols = int(rng.integers(1, 10)), int(rng.integers(1, 12))
    if integral_margins:
        # A double-centred noise matrix has row and column sums of exactly zero, so the margins stay those of the
        # integer base while (almost) every cell is fractional; the base of at least 2 keeps every cell >= 0.
        noise = rng.random((n_rows, n_cols))
        centred = noise - noise.mean(axis=1, keepdims=True) - noise.mean(axis=0, keepdims=True) + noise.mean()
        values = rng.integers(2, 6, size=(n_rows, n_cols)) + centred
    else:
        # Scales from in-commuters at 1 % (far below one) to residents at 100 %, with structural zeros and
        # cells that are already whole.
        values = rng.random((n_rows, n_cols)) * float(rng.choice([0.05, 1.0, 7.0, 400.0]))
        values[rng.random(values.shape) < 0.2] = 0.0
        whole = rng.random(values.shape) < 0.1
        values[whole] = rng.integers(0, 4, size=int(whole.sum()))
    return pd.DataFrame(values, index=[f"k{i}" for i in range(n_rows)], columns=[f"d{j}" for j in range(n_cols)])


@pytest.mark.parametrize("integral_margins", [True, False], ids=["integral_margins", "fractional_margins"])
def test_controlled_round_keeps_every_cell_and_margin_within_floor_and_ceiling(integral_margins):
    generator = np.random.default_rng(20261127 + int(integral_margins))
    for case in range(200):
        matrix = _random_matrix(generator, integral_margins)
        rounded = controlled_round(matrix, np.random.default_rng(case))
        real = matrix.to_numpy()
        result = rounded.to_numpy()

        assert rounded.index.equals(matrix.index) and rounded.columns.equals(matrix.columns)
        assert all(pd.api.types.is_integer_dtype(dtype) for dtype in rounded.dtypes)
        _assert_floor_or_ceil(result, real, f"case {case} cells")
        _assert_floor_or_ceil(result.sum(axis=1), real.sum(axis=1), f"case {case} row sums")
        _assert_floor_or_ceil(result.sum(axis=0), real.sum(axis=0), f"case {case} column sums")
        _assert_floor_or_ceil(np.array([result.sum()]), np.array([real.sum()]), f"case {case} total")
        if integral_margins:
            np.testing.assert_array_equal(result.sum(axis=1), np.rint(real.sum(axis=1)).astype(np.int64))
            np.testing.assert_array_equal(result.sum(axis=0), np.rint(real.sum(axis=0)).astype(np.int64))


def test_controlled_round_is_unbiased_in_every_cell():
    # 4,000 draws are the sample the acceptance rule needs (each cell's mean within 4 standard errors); the
    # whole test takes about a second.
    matrix = pd.DataFrame([[0.03, 1.40, 2.70, 0.55],
                           [0.90, 0.25, 1.75, 0.10],
                           [2.20, 0.60, 0.33, 1.08]],
                          index=["03101", "03153", "03158"], columns=["TU", "HBK", "Ostfalia", "Leibniz"])
    n_draws = 4000
    draws = np.stack([controlled_round(matrix, np.random.default_rng(seed)).to_numpy() for seed in range(n_draws)])
    mean = draws.mean(axis=0)
    standard_error = draws.std(axis=0, ddof=1) / np.sqrt(n_draws)

    assert (standard_error > 0).all()
    assert (np.abs(mean - matrix.to_numpy()) <= 4 * standard_error).all(), (mean, standard_error)


def test_controlled_round_is_deterministic_under_the_same_seed():
    matrix = _random_matrix(np.random.default_rng(7), integral_margins=False)

    first = controlled_round(matrix, np.random.default_rng(42))
    second = controlled_round(matrix, np.random.default_rng(42))

    pd.testing.assert_frame_equal(first, second)


# ------------------------------------------------------------------------------------------------ the fit
def _seed(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["home_ars5", "destination_id", "seed"])


#: Two home Kreise of 120 and 100 heads in T; destination heads 80, 60, 80 of 220.
_TWO_KREISE_SEED = _seed([
    ("03101", "TU Braunschweig", 70.0), ("03101", "Ostfalia Wolfenbüttel", 30.0), ("03101", "Ostfalia Salzgitter", 20.0),
    ("03153", "TU Braunschweig", 10.0), ("03153", "Ostfalia Wolfenbüttel", 30.0), ("03153", "Ostfalia Salzgitter", 60.0),
])
_TWO_KREISE_SHARES = pd.Series({"TU Braunschweig": 80 / 220, "Ostfalia Wolfenbüttel": 60 / 220,
                                "Ostfalia Salzgitter": 80 / 220})


def test_fit_gives_every_destination_its_share_of_the_present_persons_under_two_away_rates():
    # 100 of 120 and 60 of 100 heads present: scaling each row of T to its persons would give the TU about 64
    # persons instead of its share of 160 (58.2); the balanced fit must not.
    present = pd.Series({"03101": 100, "03153": 60})
    targets = 160 * _TWO_KREISE_SHARES

    fitted, diagnostics = fit_flow_matrix(_TWO_KREISE_SEED, present, _TWO_KREISE_SHARES,
                                          tolerance=1e-6, max_iterations=1000)

    assert diagnostics["rows_left_out"] == []
    assert diagnostics["max_deviation_persons"] <= 1e-6 and 1 <= diagnostics["iterations"] <= 1000
    np.testing.assert_allclose(fitted.sum(axis=1).loc[["03101", "03153"]], [100.0, 60.0], rtol=0, atol=1e-9)
    np.testing.assert_allclose(fitted.sum(axis=0)[targets.index], targets, rtol=0, atol=1e-6)
    for seed in range(50):
        rounded = controlled_round(fitted, np.random.default_rng(seed))
        assert rounded.sum(axis=1).loc[["03101", "03153"]].tolist() == [100, 60]
        assert (np.abs(rounded.sum(axis=0)[targets.index] - targets) < 1).all()


def test_fit_leaves_out_and_counts_the_rows_without_persons():
    seed = _seed(list(_TWO_KREISE_SEED.itertuples(index=False)) + [
        ("03158", "TU Braunschweig", 25.0), ("03158", "Ostfalia Wolfenbüttel", 40.0),
        ("03154", "TU Braunschweig", 15.0),
    ])
    # 03158 has no person present; 03154 is in T but missing from the persons (no student there at all).
    present = pd.Series({"03101": 100, "03153": 60, "03158": 0})

    fitted, diagnostics = fit_flow_matrix(seed, present, _TWO_KREISE_SHARES, tolerance=1e-6, max_iterations=1000)

    assert diagnostics["rows_left_out"] == ["03154", "03158"]
    assert sorted(fitted.index) == ["03101", "03153"]
    np.testing.assert_allclose(fitted.sum(axis=0)[_TWO_KREISE_SHARES.index], 160 * _TWO_KREISE_SHARES, atol=1e-6)


@pytest.mark.parametrize("present, max_iterations, message", [
    # Only 03153, which has no person, reaches the Salzgitter site: its share would be lost.
    (pd.Series({"03101": 100, "03153": 0}), 1000, "Ostfalia Salzgitter"),
    # 03157 has persons but no seed on any destination with a share.
    (pd.Series({"03101": 100, "03157": 10}), 1000, "03157"),
    # 03101 reaches only the TU; with 90 of 100 persons there the TU's target of 36.4 cannot be met.
    (pd.Series({"03101": 90, "03153": 10}), 50, "did not converge"),
], ids=["unreachable_destination", "row_without_seed", "infeasible_margins"])
def test_fit_refuses_a_table_it_cannot_balance(present, max_iterations, message):
    seed = _TWO_KREISE_SEED
    if message == "Ostfalia Salzgitter":
        seed = seed[~((seed["home_ars5"] == "03101") & (seed["destination_id"] == "Ostfalia Salzgitter"))]
    if message == "did not converge":
        seed = seed[(seed["home_ars5"] == "03153") | (seed["destination_id"] == "TU Braunschweig")]

    with pytest.raises(ValueError, match=message):
        fit_flow_matrix(seed, present, _TWO_KREISE_SHARES, tolerance=1e-6, max_iterations=max_iterations)
