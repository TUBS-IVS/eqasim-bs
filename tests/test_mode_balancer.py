"""Unit tests for braunschweig.data.cordon.mode_balancer.balance_incommuter_modes.

TDD: tests are written before the implementation. Run -> FAIL (module missing),
implement mode_balancer.py, run -> PASS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.mode_balancer import balance_incommuter_modes  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Global PT count is conserved when the flexible pool is large enough
# ---------------------------------------------------------------------------

def test_global_pt_conserved_when_pool_suffices():
    """n_pt_final == n_pt_target when enough reachable car agents exist for promotion.

    10 agents:
      - indices 0-3: Mikrozensus mode = "pt"
        - indices 0-1: can_board_pt = True  (keep as pt)
        - indices 2-3: can_board_pt = False (forced to car, 2 forced demotions)
      - indices 4-9: Mikrozensus mode = "car"
        - indices 4-7: can_board_pt = True  (flexible pool, 4 candidates)
        - indices 8-9: can_board_pt = False (not flexible)

    n_pt_target = 4, n_forced = 2 -> promote 2 from the 4 flexible -> n_pt_final = 4.
    """
    modes_mikro = ["pt", "pt", "pt", "pt",    # 0-3 are pt
                   "car", "car", "car", "car",  # 4-7 are reachable car
                   "car", "car"]                # 8-9 are non-reachable car
    can_board_pt = [True, True, False, False,   # 0-1 reachable pt, 2-3 not
                    True, True, True, True,     # 4-7 reachable car (flexible)
                    False, False]               # 8-9 not reachable
    pt_propensity = [0.9, 0.9, 0.9, 0.9,       # pt agents (irrelevant for promotion)
                     0.6, 0.7, 0.5, 0.8,        # flexible car: varied propensity
                     0.3, 0.3]                  # non-flexible car

    rng = np.random.default_rng(42)
    modes_final, stats = balance_incommuter_modes(
        modes_mikro, can_board_pt, pt_propensity, rng)

    assert stats["n_pt_target"] == 4
    assert stats["n_forced_car"] == 2
    assert stats["n_flexible"] == 4
    assert stats["n_promoted"] == 2
    assert stats["n_pt_final"] == 4
    assert stats["residual"] == 0

    # Exact count in output
    assert int((modes_final == "pt").sum()) == 4

    # Every final pt agent can board (hard constraint)
    can = np.asarray(can_board_pt, dtype=bool)
    for i, m in enumerate(modes_final):
        if m == "pt":
            assert can[i], f"Agent {i} is pt in output but cannot board PT"


# ---------------------------------------------------------------------------
# 2. PT agents without a reachable station are always demoted to car
# ---------------------------------------------------------------------------

def test_forced_pt_without_station_becomes_car():
    """A pt agent with can_board_pt = False must NEVER appear as pt in the output."""
    modes_mikro = ["pt", "pt", "car"]
    can_board_pt = [False, True, True]  # agent 0 is pt but cannot board
    pt_propensity = [0.9, 0.9, 0.5]

    rng = np.random.default_rng(0)
    modes_final, stats = balance_incommuter_modes(
        modes_mikro, can_board_pt, pt_propensity, rng)

    # Agent 0 (pt, not boardable) must be car
    assert modes_final[0] == "car", (
        "Agent 0 has can_board_pt=False but was assigned 'pt' in the output."
    )

    # Invariant: no final pt agent is unreachable
    can = np.asarray(can_board_pt, dtype=bool)
    for i, m in enumerate(modes_final):
        if m == "pt":
            assert can[i], f"Agent {i} is pt but cannot board"


# ---------------------------------------------------------------------------
# 3. Promotion is weighted by propensity (high-propensity candidate preferred)
# ---------------------------------------------------------------------------

def test_promotion_weighted_by_propensity():
    """With 1 forced demotion and 2 flexible car candidates, the high-propensity one is promoted.

    Candidate A (index 2): propensity = 0.99 (almost certain pt)
    Candidate B (index 3): propensity = 0.001 (almost never pt)

    Over 1000 seeds, candidate A should be chosen >>90% of the time.
    """
    modes_mikro = ["pt", "car", "car", "car"]
    can_board_pt = [False, True, True, True]  # agent 0 forced out; agents 1-3 flexible
    pt_propensity = [0.9, 0.99, 0.001, 0.001]  # agent 1 high; agents 2-3 low

    n_trials = 1000
    chosen_high = 0
    for seed in range(n_trials):
        rng = np.random.default_rng(seed)
        modes_final, stats = balance_incommuter_modes(
            modes_mikro, can_board_pt, pt_propensity, rng)
        assert stats["n_promoted"] == 1
        # "high propensity" = agent 1 (index 1)
        if modes_final[1] == "pt":
            chosen_high += 1

    share = chosen_high / n_trials
    assert share > 0.90, (
        f"High-propensity candidate (index 1) was chosen only {share:.1%} of the time. "
        "Promotion should strongly prefer high-propensity candidates."
    )


# ---------------------------------------------------------------------------
# 4. Residual is reported (not an exception) when the pool is too small
# ---------------------------------------------------------------------------

def test_residual_reported_when_pool_starved():
    """More forced demotions than flexible car agents -> residual > 0, no exception.

    3 pt agents forced to car (can_board = False), but only 1 reachable car agent.
    n_pt_target = 3, n_forced = 3, n_flexible = 1 -> n_promoted = 1, n_pt_final = 1,
    residual = 2.
    """
    modes_mikro = ["pt", "pt", "pt", "car", "car"]
    can_board_pt = [False, False, False, True, False]  # 3 forced out; 1 flexible; 1 not
    pt_propensity = [0.9, 0.9, 0.9, 0.6, 0.3]

    rng = np.random.default_rng(7)
    modes_final, stats = balance_incommuter_modes(
        modes_mikro, can_board_pt, pt_propensity, rng)

    assert stats["n_pt_target"] == 3
    assert stats["n_forced_car"] == 3
    assert stats["n_flexible"] == 1
    assert stats["n_promoted"] == 1
    assert stats["n_pt_final"] == 1
    assert stats["residual"] == 2  # shortfall: could not fully restore

    # n_pt_final < n_pt_target (pool was starved)
    assert stats["n_pt_final"] < stats["n_pt_target"]

    # The output must still be internally consistent
    assert int((modes_final == "pt").sum()) == stats["n_pt_final"]


# ---------------------------------------------------------------------------
# 5. Determinism: same seed produces identical output
# ---------------------------------------------------------------------------

def test_deterministic_given_rng():
    """Identical seeds must produce byte-identical modes_final."""
    modes_mikro = ["pt", "pt", "car", "car", "car", "car"]
    can_board_pt = [False, True, True, True, True, False]
    pt_propensity = [0.9, 0.7, 0.8, 0.4, 0.6, 0.1]

    rng_a = np.random.default_rng(99)
    modes_a, stats_a = balance_incommuter_modes(
        modes_mikro, can_board_pt, pt_propensity, rng_a)

    rng_b = np.random.default_rng(99)
    modes_b, stats_b = balance_incommuter_modes(
        modes_mikro, can_board_pt, pt_propensity, rng_b)

    np.testing.assert_array_equal(modes_a, modes_b,
        err_msg="Same seed must produce identical modes_final.")
    assert stats_a == stats_b, "Same seed must produce identical stats."


# ---------------------------------------------------------------------------
# 6. Invariant: every final pt agent can board (holds across a random case)
# ---------------------------------------------------------------------------

def test_every_final_pt_can_board():
    """Invariant: no agent is assigned 'pt' in the output unless can_board_pt is True.

    Uses a larger randomized scenario to stress the invariant.
    """
    rng_setup = np.random.default_rng(1234)
    n = 200

    # Random Mikrozensus assignments (~40% pt)
    mikro_arr = rng_setup.choice(["pt", "car"], size=n, p=[0.4, 0.6])
    # Random reachability (~70% reachable)
    can = rng_setup.random(n) < 0.7
    # Random propensity in (0, 1)
    prop = rng_setup.uniform(0.05, 0.95, size=n)

    rng = np.random.default_rng(42)
    modes_final, stats = balance_incommuter_modes(mikro_arr, can, prop, rng)

    for i, m in enumerate(modes_final):
        if m == "pt":
            assert can[i], (
                f"Invariant violated: agent {i} is 'pt' in output but "
                "can_board_pt is False."
            )

    # Basic integrity: output length unchanged
    assert len(modes_final) == n
    # All values are valid modes
    assert set(modes_final).issubset({"car", "pt"})


# ---------------------------------------------------------------------------
# 7. Iterative re-balancing with timetable-connected agents (#467, D3)
# ---------------------------------------------------------------------------
# After the timetable chain switched some pt in-commuters to car, rebalance_with_timetable promotes car agents back
# to pt until the pt count is within the tolerance of the balancer's target; a promoted agent the timetable does not
# serve goes back to car and leaves the pool.

from braunschweig.data.cordon.mode_balancer import rebalance_with_timetable  # noqa: E402

# 12 agents: 0-3 Mikrozensus pt (target 4); the chain switched 2 and 3 to car; 10 and 11 cannot board pt.
_MIKRO = ["pt"] * 4 + ["car"] * 8
_CAN = [True] * 10 + [False] * 2
_PROP = [0.5, 0.5, 0.5, 0.5, 0.9, 0.1, 0.7, 0.3, 0.6, 0.2, 0.9, 0.9]
_SWITCHED = np.zeros(12, dtype=bool)
_SWITCHED[[2, 3]] = True


def _after_chain():
    modes = np.array(_MIKRO, dtype=object)
    modes[_SWITCHED] = "car"
    return modes.astype(str)


def _rebalance(connect, rng, *, max_iterations=10, tolerance=0.01, modes=None):
    return rebalance_with_timetable(_after_chain() if modes is None else modes, 4, _CAN, _PROP, connect, rng,
                                    excluded=_SWITCHED, tolerance=tolerance, max_iterations=max_iterations)


def test_rebalance_with_always_connected_agents_equals_the_balancer():
    # The chain-switched agents act like the balancer's forced ones: same pool, same weighted draw, same seed.
    expected, _ = balance_incommuter_modes(_MIKRO, np.array(_CAN) & ~_SWITCHED, _PROP, np.random.default_rng(5))
    modes, stats = _rebalance(lambda idx: np.ones(len(idx), dtype=bool), np.random.default_rng(5))
    np.testing.assert_array_equal(modes, expected)
    assert stats == {"n_pt_target": 4, "n_pt_start": 2, "n_pt_final": 4, "n_iterations": 1, "n_promoted": 2,
                     "n_promoted_unconnected": 0, "residual": 0, "met": True}
    assert not (modes[_SWITCHED] == "pt").any()          # the chain's switches are final


def test_an_unconnected_promoted_agent_goes_back_to_car_and_another_is_promoted():
    tried, refused = [], []

    def connect(idx):
        tried.append(list(idx))
        ok = np.ones(len(idx), dtype=bool)
        if not refused:                                  # the very first promoted agent has no connection
            ok[0] = False
            refused.append(int(idx[0]))
        return ok

    modes, stats = _rebalance(connect, np.random.default_rng(5))
    assert stats["n_iterations"] == 2 and stats["met"] and stats["n_pt_final"] == 4
    assert stats["n_promoted"] == 3 and stats["n_promoted_unconnected"] == 1 and stats["residual"] == 0
    assert modes[refused[0]] == "car"
    assert refused[0] not in tried[1]                    # out of the pool after its failed attempt
    assert all(_CAN[i] and not _SWITCHED[i] for batch in tried for i in batch)


def test_rebalance_stops_at_the_iteration_cap_when_the_timetable_serves_nobody(caplog):
    calls = []
    modes, stats = _rebalance(lambda idx: calls.append(len(idx)) or np.zeros(len(idx), dtype=bool),
                              np.random.default_rng(5), max_iterations=3)
    assert len(calls) == 3 and stats["n_iterations"] == 3
    assert stats["met"] is False and stats["residual"] == 2 and stats["n_pt_final"] == 2
    np.testing.assert_array_equal(modes, _after_chain())
    assert any(r.levelname == "WARNING" for r in caplog.records)


def test_rebalance_stops_when_the_pool_is_exhausted():
    modes, stats = _rebalance(lambda idx: np.zeros(len(idx), dtype=bool), np.random.default_rng(5),
                              max_iterations=100)
    assert stats["n_promoted"] == 6                      # the six flexible car agents 4-9, each tried once
    assert stats["met"] is False and stats["residual"] == 2


@pytest.mark.parametrize("modes, tolerance", [
    (np.array(["pt"] * 4 + ["car"] * 8), 0.01),          # no deficit
    (np.array(["pt"] * 3 + ["car"] * 9), 0.3),           # deficit 1 of 4 within a 30 % tolerance
])
def test_rebalance_within_tolerance_draws_nothing(modes, tolerance):
    rng = np.random.default_rng(5)
    state = rng.bit_generator.state
    out, stats = _rebalance(lambda idx: pytest.fail("connect must not be called"), rng, modes=modes,
                            tolerance=tolerance)
    assert rng.bit_generator.state == state
    np.testing.assert_array_equal(out, modes)
    assert stats["n_iterations"] == 0 and stats["met"] is True


def test_rebalance_promotes_zero_propensity_agents_only_after_the_weighted_ones(caplog):
    # Deficit 2, but only agent 4 has a positive pt propensity: a weighted draw without replacement cannot pick 2
    # (numpy raises); agent 4 is promoted and the second agent comes from the zero-weight rest, counted and warned.
    prop = [0.5] * 4 + [0.8] + [0.0] * 7
    modes, stats = rebalance_with_timetable(_after_chain(), 4, _CAN, prop, lambda idx: np.ones(len(idx), bool),
                                            np.random.default_rng(5), excluded=_SWITCHED, tolerance=0.01,
                                            max_iterations=10)
    assert modes[4] == "pt" and stats["n_pt_final"] == 4 and stats["met"]
    assert any("zero" in r.getMessage() and r.levelname == "WARNING" for r in caplog.records)


def test_balancer_with_all_zero_propensities_draws_uniformly_without_a_warning(caplog):
    # All weights 0 is the balancer's defined uniform case, not a fallback: no WARNING (only the mixed case warns).
    modes, stats = balance_incommuter_modes(["pt", "car", "car", "car"], [False, True, True, True], [0.0] * 4,
                                            np.random.default_rng(3))
    assert stats["n_promoted"] == 1 and stats["n_pt_final"] == 1
    assert not any(r.levelname == "WARNING" for r in caplog.records)
