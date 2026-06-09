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
"""
from __future__ import annotations

import numpy as np


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
        if n_promote == n_flexible:
            # Promote the entire flexible pool -- no weighted draw needed.
            chosen = flexible_idx
        else:
            # Weighted draw without replacement.
            w = prop[flexible_idx].copy()
            # Clip negatives (guard against caller passing negative propensities).
            w = np.where(w > 0.0, w, 0.0)
            total_w = w.sum()
            if total_w > 0.0:
                p = w / total_w
            else:
                # All propensities are zero -- fall back to uniform.
                p = np.full(n_flexible, 1.0 / n_flexible)
            chosen = rng.choice(flexible_idx, size=n_promote, replace=False, p=p)

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
