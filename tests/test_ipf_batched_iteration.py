"""Equivalence tests for the batched IPF iteration in ``braunschweig.ipf.model``.

``run_ipf_iterations(batched=True)`` replaces the per-selector ``np.sum`` +
scatter-multiply loop with one ``np.bincount`` + one vectorised multiply per
mutually-disjoint margin block. Within a disjoint block the sequential update
order cannot influence the result (updating cell A's rows never changes cell
B's sum), so the batched update is mathematically identical; only the SUM
accumulation order differs (bincount sequential vs. np.sum pairwise), so the
weights agree to floating-point round-off but are not bit-identical. These
tests pin that contract:

1. batched == sequential to tight relative tolerance on a multi-block problem
   (incl. empty selectors, zero-target cells, and an epsilon re-seed cell);
2. a block with OVERLAPPING selectors is detected at runtime and falls back to
   the sequential path (then results are bit-identical by construction);
3. block bookkeeping that does not cover the selector list raises (fail-fast).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.ipf import model


def _silent(*_args, **_kwargs):
    pass


def _disjoint_problem(seed=0):
    """A small 3-block IPF problem with disjoint selectors per block.

    Block 1 partitions rows by ``g1`` (4 cells), block 2 by ``g2`` (3 cells),
    block 3 is a single zero-target constraint on a row subset. One g1 cell
    gets target 0 (zeroing path) and one g2 cell starts at weight 0 with a
    positive target (epsilon re-seed path).
    """
    rng = np.random.RandomState(seed)
    n = 240
    df = pd.DataFrame({
        "g1": rng.randint(0, 4, size=n),
        "g2": rng.randint(0, 3, size=n),
    })
    weights = rng.uniform(0.5, 2.0, size=n)

    g1_indices = model._build_group_indices(df, ["g1"])
    g2_indices = model._build_group_indices(df, ["g2"])
    zero_rows = np.flatnonzero((df["g1"] == 0).to_numpy() & (df["g2"] == 1).to_numpy())

    # Derive MUTUALLY CONSISTENT margin targets from one reference weighting
    # (inconsistent margins make the IPF oscillate and never converge, for the
    # sequential and the batched path alike). The reference is zero on the
    # g1 == 3 cell (zero-target path) and on the explicit zero constraint rows.
    w_ref = rng.uniform(0.5, 2.0, size=n)
    w_ref[g1_indices[(3,)][0]] = 0.0
    w_ref[zero_rows] = 0.0

    selectors = []
    targets = []
    blocks = []

    start = len(selectors)
    for value in range(4):
        sel = g1_indices.get((value,), model._EMPTY_SELECTOR)
        selectors.append(sel)
        targets.append(float(w_ref[sel[0]].sum()))
    blocks.append((start, len(selectors)))

    start = len(selectors)
    for value in range(3):
        sel = g2_indices.get((value,), model._EMPTY_SELECTOR)
        selectors.append(sel)
        targets.append(float(w_ref[sel[0]].sum()))
    # Force the epsilon re-seed path: every row of g2 cell 2 starts at weight 0
    # while its target stays positive.
    reseed_rows = g2_indices[(2,)][0]
    weights[reseed_rows] = 0.0
    blocks.append((start, len(selectors)))

    # Single zero-target constraint (its own trivially-sequential block).
    start = len(selectors)
    selectors.append((zero_rows.astype(np.int64),))
    targets.append(0.0)
    blocks.append((start, len(selectors)))

    return selectors, targets, weights, blocks


def test_batched_matches_sequential_to_fp_roundoff():
    selectors, targets, weights, blocks = _disjoint_problem()

    w_seq, it_seq, conv_seq, _ = model.run_ipf_iterations(
        selectors, targets, weights.copy(),
        max_iterations=40, tolerance=-1.0,  # tolerance -1 -> never converges:
        batched=False, log=_silent,         # both paths run all 40 iterations
    )
    w_bat, it_bat, conv_bat, _ = model.run_ipf_iterations(
        selectors, targets, weights.copy(),
        max_iterations=40, tolerance=-1.0,
        selector_blocks=blocks, batched=True, log=_silent,
    )

    assert it_seq == it_bat == 40
    assert conv_seq is False and conv_bat is False
    np.testing.assert_allclose(w_bat, w_seq, rtol=1e-9, atol=1e-12)
    # The zero-target g1 cell and the explicit zero constraint must be exactly 0
    # in both paths (factor 0 is exact in floating point).
    zero_rows = selectors[3][0]
    assert np.all(w_seq[zero_rows] == 0.0) and np.all(w_bat[zero_rows] == 0.0)


def test_batched_converges_like_sequential_on_feasible_problem():
    selectors, targets, weights, blocks = _disjoint_problem(seed=3)

    w_seq, _, conv_seq, _ = model.run_ipf_iterations(
        selectors, targets, weights.copy(),
        max_iterations=1500, tolerance=1e-2, batched=False, log=_silent,
    )
    w_bat, _, conv_bat, _ = model.run_ipf_iterations(
        selectors, targets, weights.copy(),
        max_iterations=1500, tolerance=1e-2,
        selector_blocks=blocks, batched=True, log=_silent,
    )

    assert conv_seq and conv_bat
    np.testing.assert_allclose(w_bat, w_seq, rtol=1e-6, atol=1e-12)


def test_overlapping_block_falls_back_to_sequential_and_is_bit_identical():
    """Two overlapping selectors in one declared block must NOT be batched."""
    rng = np.random.RandomState(1)
    n = 50
    weights = rng.uniform(0.5, 2.0, size=n)
    # Selector 0 and 1 overlap on rows 10..19 -> not a partition.
    s0 = (np.arange(0, 20, dtype=np.int64),)
    s1 = (np.arange(10, 30, dtype=np.int64),)
    selectors = [s0, s1]
    targets = [25.0, 30.0]
    blocks = [(0, 2)]

    prepared = model._build_iteration_blocks(selectors, targets, blocks, n)
    assert prepared[0][0] == "sequential"

    w_seq, _, _, _ = model.run_ipf_iterations(
        selectors, targets, weights.copy(),
        max_iterations=10, tolerance=-1.0, batched=False, log=_silent,
    )
    w_bat, _, _, _ = model.run_ipf_iterations(
        selectors, targets, weights.copy(),
        max_iterations=10, tolerance=-1.0,
        selector_blocks=blocks, batched=True, log=_silent,
    )
    # Fallback means the overlapping block runs the identical sequential code.
    assert np.array_equal(w_seq, w_bat)


def test_block_coverage_mismatch_raises():
    selectors, targets, weights, blocks = _disjoint_problem()
    with pytest.raises(ValueError, match="selector_blocks cover"):
        model.run_ipf_iterations(
            selectors, targets, weights.copy(),
            max_iterations=5, tolerance=1e-2,
            selector_blocks=blocks[:-1], batched=True, log=_silent,
        )


def test_batched_requires_blocks():
    selectors, targets, weights, _ = _disjoint_problem()
    with pytest.raises(ValueError, match="requires selector_blocks"):
        model.run_ipf_iterations(
            selectors, targets, weights.copy(),
            max_iterations=5, tolerance=1e-2, batched=True, log=_silent,
        )
