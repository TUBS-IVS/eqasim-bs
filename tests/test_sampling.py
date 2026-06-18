# tests/test_sampling.py
import logging
import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import sampling


def test_single_candidate_is_returned():
    assert sampling.weighted_choice([42], [0.001], rng=np.random.RandomState(0)) == 42


def test_lopsided_weights_favour_the_heavy_candidate():
    rng = np.random.RandomState(0)
    counts = {1: 0, 2: 0}
    for _ in range(2000):
        counts[sampling.weighted_choice([1, 2], [1.0, 99.0], rng=rng)] += 1
    # ~99% should be candidate 2; allow generous slack
    assert counts[2] > counts[1] * 10


def test_deterministic_given_seed():
    a = [sampling.weighted_choice([1, 2, 3], [1.0, 2.0, 3.0], rng=np.random.RandomState(7))
         for _ in range(50)]
    b = [sampling.weighted_choice([1, 2, 3], [1.0, 2.0, 3.0], rng=np.random.RandomState(7))
         for _ in range(50)]
    assert a == b


def test_item_order_does_not_change_result():
    # canonical sort by item => same draw regardless of input order
    x = sampling.weighted_choice([3, 1, 2], [3.0, 1.0, 2.0], rng=np.random.RandomState(1))
    y = sampling.weighted_choice([1, 2, 3], [1.0, 2.0, 3.0], rng=np.random.RandomState(1))
    assert x == y


def test_all_invalid_weights_fall_back_to_uniform_with_warning(caplog):
    with caplog.at_level(logging.WARNING):
        out = sampling.weighted_choice([1, 2], [float("nan"), 0.0], rng=np.random.RandomState(0))
    assert out in (1, 2)
    assert any("uniform" in r.getMessage().lower() for r in caplog.records)


def test_tuple_items_supported():
    # used by match_person, which passes (H_ID, P_ID) tuples
    out = sampling.weighted_choice([(5, 1), (5, 2)], [0.0, 3.0], rng=np.random.RandomState(0))
    assert out == (5, 2)  # only the positive-weight candidate is eligible
