import numpy as np
from braunschweig.calibration import detour_fit as df


def test_route_on_unit_grid_recovers_manhattan():
    # 3x3 metre grid; nodes at (i*1000, j*1000); edges along grid lines (length 1000 m).
    node_xy, ids, edges = [], [], []
    idx = {}
    for i in range(3):
        for j in range(3):
            idx[(i, j)] = len(node_xy)
            node_xy.append((i * 1000.0, j * 1000.0))
            ids.append(f"{i}_{j}")
    node_xy = np.array(node_xy)
    for i in range(3):
        for j in range(3):
            if i + 1 < 3:
                edges.append((idx[(i, j)], idx[(i + 1, j)], 1000.0))
            if j + 1 < 3:
                edges.append((idx[(i, j)], idx[(i, j + 1)], 1000.0))
    csr, xy = df.build_graph_from_edges(node_xy, edges)
    # origin (0,0) -> dest (2,2): grid distance 4000 m, euclidean 2828 m
    routed, fail = df.route_lengths_km(
        csr, xy, np.array([[0.0, 0.0]]), np.array([[2000.0, 2000.0]]))
    assert not fail[0]
    np.testing.assert_allclose(routed[0], 4.0, rtol=1e-6)  # km


def test_fit_recovers_known_params():
    rng = np.random.RandomState(0)
    d = rng.uniform(0.2, 60.0, size=5000)
    c = 1.15 + 0.6 * np.exp(-d / 2.0)
    routed = d * c * (1.0 + rng.normal(0, 0.01, size=d.size))
    out = df.fit_circuity_curve(d, routed)
    assert abs(out["c_inf"] - 1.15) < 0.05
    assert abs(out["a"] - 0.6) < 0.1
    assert abs(out["tau"] - 2.0) < 0.5
    assert out["r2"] > 0.9


def test_tracker_does_not_converge_before_min_samples():
    t = df.ConvergenceTracker(min_samples=8000, tol=0.01, patience=2)
    p = {"c_inf": 1.15, "a": 0.6, "tau": 2.0}
    assert t.update(2000, p) is False
    assert t.update(4000, p) is False     # stable but below floor -> not converged


def test_tracker_converges_after_floor_when_stable():
    t = df.ConvergenceTracker(min_samples=4000, tol=0.01, patience=2)
    p = {"c_inf": 1.15, "a": 0.60, "tau": 2.00}
    t.update(4000, p)
    assert t.update(6000, {"c_inf": 1.151, "a": 0.601, "tau": 2.00}) is False  # 1st stable
    assert t.update(8000, {"c_inf": 1.151, "a": 0.601, "tau": 2.00}) is True   # 2nd stable -> converged


def test_tracker_resets_patience_on_move():
    t = df.ConvergenceTracker(min_samples=2000, tol=0.01, patience=2)
    t.update(2000, {"c_inf": 1.15, "a": 0.6, "tau": 2.0})
    t.update(4000, {"c_inf": 1.15, "a": 0.6, "tau": 2.0})       # 1st stable
    t.update(6000, {"c_inf": 1.30, "a": 0.6, "tau": 2.0})       # moved -> reset
    assert t.update(8000, {"c_inf": 1.30, "a": 0.6, "tau": 2.0}) is False  # only 1st stable again


def test_stratified_sample_balances_short_and_long():
    rng = np.random.RandomState(0)
    # 9000 short trips (~0.5 km) and 100 long (~30 km); naive sampling buries long.
    o = np.zeros((9100, 2))
    d = np.zeros((9100, 2))
    d[:9000, 0] = 500.0
    d[9000:, 0] = 30000.0
    idx = df.stratified_sample(o, d, n_target=2000, rng=rng)
    dists = np.linalg.norm(d[idx] - o[idx], axis=1) / 1000.0
    assert (dists > 20).sum() >= 50   # long stratum represented, not swamped
    assert (dists < 1).sum() >= 50


def test_convergence_loop_stops_after_floor_on_stable_stream():
    """End-to-end fit loop: fit on a synthetic stream, check convergence fires after
    min_samples floor and stops before max_samples when the stream is stable."""
    rng = np.random.RandomState(1)
    tracker = df.ConvergenceTracker(min_samples=4000, tol=0.02, patience=2)
    n = 0
    converged = False
    while n < 20000 and not converged:
        n += 2000
        d = rng.uniform(0.2, 60.0, size=n)
        routed = d * (1.15 + 0.6 * np.exp(-d / 2.0))
        fit = df.fit_circuity_curve(d, routed)
        converged = tracker.update(n, fit)
    assert converged
    assert n >= 4000


def test_accumulate_accepted_indices_alignment():
    """Prove that cum_pool_indices returned by accumulate_accepted_indices correctly
    maps back to the original pool arrays.

    Given a pool of OD pairs with known euclidean distances, two batch draws are
    simulated.  The accepted-index accumulator is called after each batch.  At the
    end, ``pool_euclidean_km[cum_pool_indices]`` must equal ``cum_euclidean_km``
    element-wise (the alignment invariant).
    """
    rng = np.random.RandomState(7)
    # Pool: 20 OD pairs with known euclidean distances (metres)
    n_pool = 20
    origins = np.zeros((n_pool, 2))
    # Give each pair a distinct euclidean distance so we can verify alignment.
    dests = np.zeros((n_pool, 2))
    dests[:, 0] = np.arange(n_pool, dtype=float) * 100.0 + 50.0  # 50m, 150m, 250m, ...
    pool_eucl_km = np.linalg.norm(dests - origins, axis=1) / 1000.0

    cum_pool_indices: list = []
    cum_euclidean: list = []

    # Simulate two batches, each drawing 8 indices; reject the first two in each batch.
    for batch_start in (0, 8):
        batch_idx = np.arange(batch_start, batch_start + 8, dtype=int) % n_pool
        eucl_batch = pool_eucl_km[batch_idx]
        # Reject first two pairs (keep = False for idx 0,1 in batch)
        keep = np.ones(len(batch_idx), dtype=bool)
        keep[:2] = False

        # Accumulate via helper
        df.accumulate_accepted_indices(cum_pool_indices, batch_idx, keep)
        cum_euclidean.extend(eucl_batch[keep].tolist())

    cum_idx = np.array(cum_pool_indices, dtype=int)

    # Core invariant: pool distances indexed by cum_pool_indices == cum_euclidean
    np.testing.assert_array_equal(
        pool_eucl_km[cum_idx],
        np.array(cum_euclidean),
        err_msg=(
            "pool_eucl_km[cum_pool_indices] must equal cum_euclidean element-wise; "
            "alignment broken."
        ),
    )
    # Lengths must match
    assert len(cum_idx) == len(cum_euclidean), (
        f"cum_pool_indices length ({len(cum_idx)}) != cum_euclidean length "
        f"({len(cum_euclidean)})"
    )
    # Each batch had 8 pairs, first 2 rejected -> 6 accepted per batch, 12 total
    assert len(cum_idx) == 12
