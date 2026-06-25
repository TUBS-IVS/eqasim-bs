import numpy as np
from braunschweig.gravity.friction import (
    BAND_EDGES_KM, band_index, build_friction_matrix,
)


def test_band_index_assigns_expected_bands():
    d = np.array([0.0, 4.9, 5.0, 12.0, 250.0])
    np.testing.assert_array_equal(band_index(d), np.array([0, 0, 1, 2, 6]))


def test_off_path_is_byte_identical_to_exp_friction():
    n = 5
    rng = np.random.default_rng(0)
    distances = rng.uniform(0, 80, size=(n, n))
    slope_vec = np.full(n, -0.2)
    constant, diagonal = -2.4, 1.0
    expected = (np.exp(slope_vec[:, None] * distances + constant)
                + np.eye(n) * diagonal)
    got = build_friction_matrix(distances, slope_vec, constant, diagonal,
                                factors=None)
    np.testing.assert_array_equal(got, expected)


def test_global_band_factors_replace_exp_and_keep_diagonal():
    distances = np.array([[0.0, 7.0], [60.0, 3.0]])
    slope_vec = np.array([-0.2, -0.2])
    factors = {0: 2.0, 1: 0.5, 5: 0.1}
    got = build_friction_matrix(distances, slope_vec, constant=-2.4,
                                diagonal=1.0, factors=factors)
    assert got[0, 0] == 2.0 + 1.0   # d=0 -> band 0 + diagonal
    assert got[0, 1] == 0.5         # d=7 -> band 1
    assert got[1, 0] == 0.1         # d=60 -> band 5
    assert got[1, 1] == 2.0 + 1.0   # d=3 -> band 0 + diagonal
