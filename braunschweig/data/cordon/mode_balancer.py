"""Conserve the aggregate in-commuter mode split under the rail-reachability constraint.

In-commuters draw a mode (car/pt) from the MiD/Mikrozensus commute-mode-by-distance
reference (the data target). A HARD constraint then forces every PT in-commuter whose
source Kreis has no reachable rail Bahnhof to car (it cannot board PT). That lowers the
realized aggregate PT share below the target. This balancer compensates: it promotes an
equal number of reachable car in-commuters to PT (chosen by PT propensity), so the GLOBAL
PT count equals the Mikrozensus-assigned target while per-Kreis shares vary by rail
quality (PT concentrates where rail is good). Mirrors the project's consistent_car_availability
pattern (hard constraint, then rake the residual back to the marginal).

Target is self-contained: the PT count from the Mikrozensus assignment BEFORE the
constraint (no invented value). Region-neutral; pure + deterministic given rng.

:func:`rebalance_with_timetable` (#467) repeats the compensation AFTER the timetable check of the pt days
(``braunschweig.synthesis.incommuter_pt_connections``) has switched pt in-commuters without a connection to car:
it promotes car agents until the pt count is back within a relative tolerance of the same target, and keeps only
the promoted agents the timetable serves (owner decision, 2026-10-22). When the timetable cannot serve enough
agents, the timetable, not the reference, binds: the shortfall is reported and warned about, never forced.
"""
from __future__ import annotations

import logging
from collections.abc import Callable

import numpy as np

logger = logging.getLogger(__name__)

_LOG_TAG = "[braunschweig.incommuters] pt re-balancing"

#: WARN when more than this share of the promoted agents had no timetable connection (module convention: 5 %).
UNCONNECTED_WARN_SHARE = 0.05


def _draw_by_propensity(pool_idx, n_draw, prop, rng):
    """Draw ``n_draw`` of ``pool_idx`` without replacement, weighted by ``prop`` (negatives clipped to 0).

    The whole pool is returned without an rng draw when ``n_draw`` covers it. Agents whose pt propensity is 0 (a
    distance band without pt): all weights 0 -> a uniform draw (the defined behaviour, logged at INFO); fewer
    positive-weight agents than ``n_draw`` -> all of them plus a uniform draw of the rest among the zero-weight
    ones (a weighted draw without replacement cannot pick more agents than have a positive weight; counted and
    WARNED, the only fallback here). Every other input is drawn exactly as before (one ``rng.choice``)."""
    if n_draw == len(pool_idx):
        return pool_idx
    w = prop[pool_idx].copy()
    w = np.where(w > 0.0, w, 0.0)
    total_w = w.sum()
    n_positive = int((w > 0.0).sum())
    if total_w > 0.0 and n_positive >= n_draw:
        return rng.choice(pool_idx, size=n_draw, replace=False, p=w / total_w)
    zero_idx = pool_idx[w == 0.0]
    n_zero = n_draw - n_positive
    if total_w == 0.0:
        # All weights 0: the uniform draw is the defined behaviour (the balancer's since its introduction), not a
        # fallback from a weighted draw; logged at INFO for traceability.
        logger.info("%s: all %d pool agents have pt propensity 0: %d promotions drawn uniformly", _LOG_TAG,
                    len(pool_idx), n_draw)
    else:
        logger.warning("%s: only %d of %d pool agents have a positive pt propensity for %d promotions: %d (%.1f%%) "
                       "are drawn uniformly among the zero-propensity agents", _LOG_TAG, n_positive, len(pool_idx),
                       n_draw, n_zero, 100.0 * n_zero / n_draw)
    # The explicit uniform p keeps the all-zero case on the exact rng call of the former balancer code.
    return np.concatenate([pool_idx[w > 0.0], rng.choice(zero_idx, size=n_zero, replace=False,
                                                          p=np.full(len(zero_idx), 1.0 / len(zero_idx)))])


def balance_incommuter_modes(
    modes_mikro,
    can_board_pt,
    pt_propensity,
    rng,
):
    """Conserve the global PT count under the no-PT-without-rail-station constraint.

    Args:
        modes_mikro: array-like of per-agent mode ("car"/"pt") from the Mikrozensus draw
            (defines the target: n_pt_target = count of "pt").
        can_board_pt: array-like bool, per agent -- does its source Kreis have a reachable
            rail entry station?
        pt_propensity: array-like float, per agent -- its Mikrozensus P(pt | distance band)
            (used to weight which reachable car agents get promoted to pt). >= 0.
        rng: numpy.random.Generator (seeded).

    Returns:
        (modes_final, stats) where modes_final is a numpy str array ("car"/"pt") with:
          - every "pt" agent has can_board_pt True (hard constraint),
          - n_pt_final == n_pt_target when the flexible pool suffices, else all flexible
            promoted + a logged residual shortfall (never silently off-target).
        stats: dict(n_pt_target, n_forced_car, n_flexible, n_promoted, n_pt_final, residual).
    """
    modes = np.array([str(m) for m in modes_mikro], dtype=object)
    can = np.asarray(can_board_pt, dtype=bool)
    prop = np.asarray(pt_propensity, dtype=float)

    n_pt_target = int((modes == "pt").sum())

    # Step 1: Hard constraint -- PT agents without a reachable station become car.
    forced = (modes == "pt") & (~can)
    modes[forced] = "car"
    n_forced = int(forced.sum())

    # Step 2: Compensate by promoting n_forced reachable car agents to PT,
    # weighted by their PT propensity.  This restores the global PT count.
    flexible_idx = np.flatnonzero((modes == "car") & can)
    n_flexible = int(len(flexible_idx))
    n_promote = min(n_forced, n_flexible)

    if n_promote > 0:
        # Weighted draw without replacement (the whole pool when it is not larger than needed).
        chosen = _draw_by_propensity(flexible_idx, n_promote, prop, rng)
        modes[chosen] = "pt"

    n_pt_final = int((modes == "pt").sum())
    # Positive residual means rail access is too scarce to fully restore the target.
    residual = n_pt_target - n_pt_final

    stats = dict(
        n_pt_target=n_pt_target,
        n_forced_car=n_forced,
        n_flexible=n_flexible,
        n_promoted=int(n_promote),
        n_pt_final=n_pt_final,
        residual=int(residual),
    )
    return modes.astype(str), stats


def rebalance_with_timetable(
    modes,
    n_pt_target: int,
    can_board_pt,
    pt_propensity,
    connect: Callable[[np.ndarray], np.ndarray],
    rng,
    *,
    excluded=None,
    tolerance: float,
    max_iterations: int,
):
    """Pull the pt count back towards ``n_pt_target`` with agents the timetable serves (#467).

    Runs after :func:`balance_incommuter_modes` (iteration 0) and after the timetable check switched pt agents
    without a connection to car. Global over all agents passed in (not per Bundesland).

    Args:
        modes: per-agent mode ("car"/"pt") after the timetable check.
        n_pt_target: the balancer's ``n_pt_target`` (the pt count of the Mikrozensus draw).
        can_board_pt: per-agent bool, as for :func:`balance_incommuter_modes`.
        pt_propensity: per-agent weight of the promotion draw, as for :func:`balance_incommuter_modes`.
        connect: called with the promoted agent indices of one round (int array); returns a bool mask of the
            same length, True where the agent has a timetable connection (the caller records its station and
            times). An unconnected agent stays car (the caller leaves its car placement untouched).
        rng: numpy.random.Generator; consumed only when a promotion draw happens.
        excluded: per-agent bool (optional): agents never promoted (the agents the timetable check switched to
            car: their pt day was already found unserved).
        tolerance: relative tolerance in [0, 1); the loop runs while ``n_pt < n_pt_target * (1 - tolerance)``.
        max_iterations: cap on the promotion rounds (>= 0).

    Each round promotes the full deficit ``n_pt_target - n_pt`` (or the remaining pool, if smaller) from the car
    agents with ``can_board_pt`` that are not excluded and were not tried before, weighted by propensity with the
    balancer's draw. With a deficit within the tolerance nothing is drawn (rng untouched, ``connect`` never
    called). The loop also ends when the pool is empty.

    Returns:
        (modes_final, stats): numpy str array, and dict(n_pt_target, n_pt_start, n_pt_final, n_iterations,
        n_promoted, n_promoted_unconnected, residual, met). ``n_iterations`` counts the promotion rounds after
        the check (the balancer's iteration 0 is not included); ``residual = n_pt_target - n_pt_final``.
        A WARNING is logged when ``met`` is False: the timetable binds.
    """
    if not 0.0 <= float(tolerance) < 1.0:
        raise ValueError(f"{_LOG_TAG}: tolerance must be in [0, 1), got {tolerance}")
    if int(max_iterations) < 0:
        raise ValueError(f"{_LOG_TAG}: max_iterations must be >= 0, got {max_iterations}")
    modes = np.array([str(m) for m in modes], dtype=object)
    can = np.asarray(can_board_pt, dtype=bool)
    prop = np.asarray(pt_propensity, dtype=float)
    blocked = np.zeros(len(modes), dtype=bool) if excluded is None else np.asarray(excluded, dtype=bool).copy()
    n_pt_target = int(n_pt_target)
    threshold = n_pt_target * (1.0 - float(tolerance))

    n_pt_start = int((modes == "pt").sum())
    n_pt = n_pt_start
    n_iterations = n_promoted = n_unconnected = 0
    while n_pt < threshold and n_iterations < int(max_iterations):
        pool = np.flatnonzero((modes == "car") & can & ~blocked)
        if len(pool) == 0:
            break
        n_iterations += 1
        chosen = np.asarray(_draw_by_propensity(pool, min(n_pt_target - n_pt, len(pool)), prop, rng), dtype=int)
        connected = np.asarray(connect(chosen), dtype=bool)
        if connected.shape != chosen.shape:
            raise ValueError(f"{_LOG_TAG}: connect returned shape {connected.shape} for {chosen.shape} agents")
        blocked[chosen] = True                       # tried once: never promoted again
        modes[chosen[connected]] = "pt"
        n_promoted += len(chosen)
        n_unconnected += int((~connected).sum())
        n_pt = int((modes == "pt").sum())

    met = bool(n_pt >= threshold)
    stats = dict(n_pt_target=n_pt_target, n_pt_start=n_pt_start, n_pt_final=n_pt, n_iterations=n_iterations,
                 n_promoted=n_promoted, n_promoted_unconnected=n_unconnected, residual=n_pt_target - n_pt,
                 met=met)
    log_rebalance(stats, n_agents=len(modes), tolerance=float(tolerance))
    return modes.astype(str), stats


def log_rebalance(stats: dict, *, n_agents: int, tolerance: float) -> None:
    """One line with the rates of :func:`rebalance_with_timetable`; WARNING when the target is not met (the
    timetable binds) or when more than ``UNCONNECTED_WARN_SHARE`` of the promoted agents had no connection."""
    n_promoted = stats["n_promoted"]
    unconnected_share = stats["n_promoted_unconnected"] / n_promoted if n_promoted else 0.0
    message = (f"{_LOG_TAG}: target {stats['n_pt_target']}/{n_agents} pt "
               f"({100.0 * stats['n_pt_target'] / max(n_agents, 1):.1f}%), after the timetable check "
               f"{stats['n_pt_start']}, final {stats['n_pt_final']} "
               f"({100.0 * stats['n_pt_final'] / max(n_agents, 1):.1f}%), residual {stats['residual']} "
               f"({100.0 * stats['residual'] / max(stats['n_pt_target'], 1):.1f}% of the target, tolerance "
               f"{100.0 * tolerance:.1f}%); {stats['n_iterations']} iteration(s), promoted {n_promoted}, of them "
               f"without a connection {stats['n_promoted_unconnected']} ({100.0 * unconnected_share:.1f}%)")
    if not stats["met"]:
        logger.warning("%s -- target NOT met: the timetable serves too few of the promotable car in-commuters "
                       "(the timetable, not the reference, binds)", message)
    elif unconnected_share > UNCONNECTED_WARN_SHARE:
        logger.warning("%s -- more than %.0f%% of the promoted agents had no timetable connection", message,
                       100.0 * UNCONNECTED_WARN_SHARE)
    else:
        logger.info(message)
