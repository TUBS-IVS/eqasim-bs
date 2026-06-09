"""In-ring in-commuter origin-home helper (real population-weighted origin point).

For each synthetic in-commuter agent, decides whether its origin Kreis is
"in-ring" (at least one Gemeinde representative point falls within the
source-network ring: ``zgb_polygon.buffer(source_buffer_m)``) and, if so,
draws a real population-weighted origin home point from those in-ring Gemeinden.

Design rationale
----------------
eqasim's spatial pipeline clips persons to the scenario region by their HOME
coordinate.  In-commuters whose origin Kreis lies within the source-network ring
can therefore be treated as ordinary synthetic residents: they receive a real home
point outside ZGB but inside the wider source network, and eqasim's cutter clips
their legs natively at the cordon boundary.  In-commuters whose origin Kreis is
far (no in-ring Gemeinde) are handled by the CALLER via the gate / station
fallback -- this helper never silently places them.

The population-weighted split reuses :func:`.external_points.gemeinde_external_points`
(DRY): Gemeinde flow = Kreis inbound flow split by EWZ (largest-remainder, so
per-Gemeinde flows sum exactly to the Kreis flow).  The in-ring mask is computed
ONCE over the full Gemeinde-point table, not per agent.

ASSUMPTION (CLAUDE.md no-invented-reference): Gemeinde population (EWZ) is the
proxy for where in-commuter residences concentrate within an external Kreis.
BA Pendleratlas publishes no sub-Kreis OD; this is a documented assumption.

Far agents
----------
Agents with ``is_in_ring=False`` (``home_x/home_y = nan``) are the CALLER's
responsibility: the caller must apply a gate-based or station-based fallback.
This helper logs the primary vs fallback split so a high far-rate (indicating
that ``source_buffer_m`` is too small or inbound data is wrong) is immediately
visible (CLAUDE.md no-silent-fallbacks rule).

Pure function: no I/O, no global state, deterministic given ``rng``.
"""
from __future__ import annotations

import logging
from typing import Sequence

import geopandas as gpd
import numpy as np
import pandas as pd

from braunschweig.data.cordon.external_points import gemeinde_external_points

_log = logging.getLogger(__name__)


def incommuter_origin_homes(
    orig_ars: Sequence[str],
    inbound_by_kreis: pd.DataFrame,
    gemeinden: gpd.GeoDataFrame,
    zgb_polygon,
    source_buffer_m: float,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Assign population-weighted in-ring home points to in-commuter agents.

    For each agent whose origin Kreis has at least one Gemeinde representative
    point inside ``zgb_polygon.buffer(source_buffer_m)``, one such point is
    drawn categorically, weighted by per-Gemeinde inbound flow (itself split
    from the Kreis flow in proportion to EWZ).  Agents from far Kreise (no
    in-ring Gemeinde point) receive ``nan`` coordinates and ``is_in_ring=False``;
    the caller is responsible for applying a gate / station fallback to those.

    Args:
        orig_ars: Array-like of each agent's 5-digit origin Kreis ARS.
        inbound_by_kreis: DataFrame ``[ars5, flow]`` -- inbound SvB count per
            external origin Kreis.  Kreise absent here or with ``flow < 1``
            produce no Gemeinde points and their agents are marked far.
        gemeinden: GeoDataFrame ``[ars5, gem_ags, ewz, geometry]`` -- external
            Gemeinden (polygon, EPSG:25832).  ``ars5`` is the 5-digit Kreis ARS;
            ``ewz`` is population (>0).
        zgb_polygon: Shapely (Multi)Polygon of the dissolved ZGB region
            (EPSG:25832); buffered once by ``source_buffer_m`` to test in-ring
            membership.
        source_buffer_m: Ring buffer in metres (the ``cordon_network_source_buffer_m``
            config value).  A Gemeinde representative point is in-ring iff it lies
            within ``zgb_polygon.buffer(source_buffer_m)``.
        rng: Seeded numpy Generator (``numpy.random.default_rng``).  The same
            generator is consumed in input order so results are deterministic only
            if the caller provides a freshly seeded generator each time.

    Returns:
        Tuple ``(home_x, home_y, is_in_ring)`` -- three numpy arrays of length
        ``len(orig_ars)``:

        - ``home_x``, ``home_y``: float arrays (EPSG:25832 projected metres);
          ``nan`` for far agents.
        - ``is_in_ring``: bool array; ``False`` for far agents.

    Notes:
        Far agents (``is_in_ring=False``) are **not** placed by this helper; the
        caller must handle them (gate / station fallback).  The primary-vs-fallback
        split is logged at INFO level so a high far-rate is immediately visible.
    """
    orig_ars = list(orig_ars)
    n = len(orig_ars)

    home_x = np.full(n, np.nan, dtype=float)
    home_y = np.full(n, np.nan, dtype=float)
    is_in_ring = np.zeros(n, dtype=bool)

    if n == 0:
        return home_x, home_y, is_in_ring

    # ---- Step 1: build per-Gemeinde origin points (min_flow=1 so every Kreis
    # with any inbound count is represented; agent-count threshold is upstream). ----
    gem_pts = gemeinde_external_points(inbound_by_kreis, gemeinden, min_flow=1)

    if len(gem_pts) == 0:
        _log.warning(
            "[incommuter_origins] no Gemeinde points produced (inbound_by_kreis empty "
            "or no matching Gemeinden); all %d agents treated as far", n
        )
        return home_x, home_y, is_in_ring

    # ---- Step 2: in-ring mask per Gemeinde point (computed ONCE, not per agent). ----
    ring = zgb_polygon.buffer(source_buffer_m)
    in_ring_mask = gem_pts.geometry.within(ring).to_numpy(dtype=bool)

    # ---- Step 3: group in-ring Gemeinde points by Kreis. ----
    # Build a dict: ars5 -> (x_array, y_array, weight_array) for in-ring points only.
    kreis_inring: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    gem_pts_inring = gem_pts[in_ring_mask].copy()
    if len(gem_pts_inring) > 0:
        gx = gem_pts_inring.geometry.x.to_numpy(dtype=float)
        gy = gem_pts_inring.geometry.y.to_numpy(dtype=float)
        gf = gem_pts_inring["flow"].to_numpy(dtype=float)
        ga = gem_pts_inring["ars5"].to_numpy()
        # Build groups (order-preserving, single pass avoids repeated filtering).
        from collections import defaultdict
        idx_by_kreis: dict[str, list[int]] = defaultdict(list)
        for i, ars in enumerate(ga):
            idx_by_kreis[str(ars)].append(i)
        for ars, idxs in idx_by_kreis.items():
            ii = np.array(idxs, dtype=int)
            w = gf[ii]
            w_sum = w.sum()
            kreis_inring[ars] = (gx[ii], gy[ii], w / w_sum if w_sum > 0 else np.ones(len(ii)) / len(ii))

    # ---- Step 4: per-agent draw. ----
    n_in_ring = 0
    n_far = 0
    for i, ars in enumerate(orig_ars):
        dist = kreis_inring.get(str(ars))
        if dist is None:
            n_far += 1
            continue
        xs, ys, probs = dist
        j = int(rng.choice(len(xs), p=probs))
        home_x[i] = xs[j]
        home_y[i] = ys[j]
        is_in_ring[i] = True
        n_in_ring += 1

    # ---- Step 5: log primary vs fallback rate (CLAUDE.md no-silent-fallbacks). ----
    if n > 0:
        pct_in = 100.0 * n_in_ring / n
        pct_far = 100.0 * n_far / n
        if n_far == 0:
            _log.info(
                "[incommuter_origins] in-ring primary %d/%d (%.1f%%), far (caller fallback) 0",
                n_in_ring, n, pct_in,
            )
        else:
            level = logging.INFO if pct_in >= 50.0 else logging.WARNING
            _log.log(
                level,
                "[incommuter_origins] in-ring primary %d/%d (%.1f%%), far (caller fallback) %d (%.1f%%)"
                "%s",
                n_in_ring, n, pct_in,
                n_far, pct_far,
                "" if pct_far < 50.0 else " -- WARNING: majority far; check source_buffer_m or inbound data",
            )

    return home_x, home_y, is_in_ring
