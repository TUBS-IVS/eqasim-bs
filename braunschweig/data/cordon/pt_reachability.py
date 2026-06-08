"""Rail-only PT entry stations reachable to ZGB directly or with one transfer.

A cross-cordon PT in-commuter boards at a real BAHNHOF (rail/regional/long-distance
station) from which ZGB is reachable on the schedule either directly (a single train
that also serves a ZGB stop) or with exactly one transfer (board train A, change once
to a train B that serves ZGB). Buses and trams are excluded by design -- the entry
point must be a rail station. Region-neutral; operates on the cut transit schedule
structure of ``braunschweig.data.cordon.network.read_transit_stops_routes``.

Population+accessibility weighting and per-agent station draw (B2) are provided by
:func:`weight_entry_stations` and :func:`sample_pt_station_per_agent`, mirroring the
road-gate pattern in ``braunschweig.data.cordon.gate_assignment``.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.synthesis.incommuters import RAIL_LIKE_MODES


def eligible_rail_entry_stations(
    routes,
    stop_kreis,
    zgb_kreise,
    rail_like=RAIL_LIKE_MODES,
) -> pd.DataFrame:
    """Return all external rail stops reachable to ZGB directly or with one transfer.

    A stop is an "entry station" if it lies outside the ZGB region, has a known
    source Kreis, and a rail-only commuter can reach ZGB from it with at most one
    transfer:

    - **direct**: the stop is on a rail route that also serves at least one ZGB stop
      (no transfer needed).
    - **transfer**: the stop is on a rail route that shares at least one stop with
      any direct route (i.e. one transfer at that shared stop reaches a ZGB-bound
      service).

    When a stop qualifies for both, "direct" takes precedence.

    ZGB stops themselves are never returned; stops whose Kreis is ``None`` (not
    mapped) are silently skipped because they cannot be attributed to a source
    Kreis for the in-commuter demand model.

    Args:
        routes: Iterable of ``(mode, [stop_id, ...])`` pairs representing transit
            routes in stop order, as produced by
            ``braunschweig.data.cordon.network.read_transit_stops_routes``.
            ``mode`` is compared lower-cased against ``rail_like``; ``None`` is
            treated as non-rail.
        stop_kreis: Mapping ``stop_id -> 5-digit Kreis ARS`` (or ``None`` if the
            stop could not be attributed to any Kreis).
        zgb_kreise: Iterable of 5-digit Kreis ARS strings identifying the ZGB
            in-scope region.
        rail_like: Set of transport-mode strings (lower-cased) treated as rail.
            Defaults to ``RAIL_LIKE_MODES``; covers heavy rail, regional rail,
            subway, and light rail. Buses and trams are excluded.

    Returns:
        DataFrame with columns ``["source_ars5", "stop_id", "reach"]`` (one row per
        external entry station). ``source_ars5`` is the 5-digit Kreis ARS of the
        stop. ``reach`` is ``"direct"`` or ``"transfer"``. ``"direct"`` beats
        ``"transfer"`` when a stop qualifies for both.
    """
    zgb = {str(k) for k in zgb_kreise}
    rail = {str(m).strip().lower() for m in rail_like}

    # Restrict to rail-mode routes only (bus/tram excluded).
    rail_routes = [
        stops for mode, stops in routes
        if mode is not None and str(mode).strip().lower() in rail
    ]

    def serves_zgb(stops):
        """Return True if any stop on this route maps to a ZGB Kreis."""
        return any(stop_kreis.get(s) in zgb for s in stops)

    # Direct routes: rail routes with at least one ZGB stop.
    direct_routes = [stops for stops in rail_routes if serves_zgb(stops)]

    # Transfer hubs: all stops appearing on any direct route.
    # A rail route that shares >= 1 stop with this set offers a one-transfer
    # connection to ZGB (change at any hub stop to a direct-route service).
    hubs = {s for stops in direct_routes for s in stops}

    # ZGB stops must never appear in the result.
    zgb_stops = {s for s, k in stop_kreis.items() if k in zgb}

    # best[stop_id] = "direct" | "transfer"; "direct" is final once set.
    best: dict[str, str] = {}

    def mark(stop_id: str, reach: str) -> None:
        """Record reach for stop_id, skipping ZGB stops and honouring direct-first."""
        if stop_id in zgb_stops:
            return
        if best.get(stop_id) == "direct":
            return
        if reach == "direct" or stop_id not in best:
            best[stop_id] = reach

    # Pass 1 -- mark stops on direct routes as "direct".
    for stops in direct_routes:
        for s in stops:
            mark(s, "direct")

    # Pass 2 -- mark stops on one-transfer-eligible routes as "transfer"
    # (only applied if not already "direct").
    for stops in rail_routes:
        if any(s in hubs for s in stops):
            for s in stops:
                mark(s, "transfer")

    # Build result: external stops only, with a known non-ZGB Kreis.
    rows = []
    for stop_id, reach in best.items():
        kreis = stop_kreis.get(stop_id)
        if kreis is not None and kreis not in zgb:
            rows.append((kreis, stop_id, reach))

    return pd.DataFrame(rows, columns=["source_ars5", "stop_id", "reach"])


def weight_entry_stations(stations: pd.DataFrame,
                          transfer_penalty: float = 0.5) -> pd.DataFrame:
    """Per (Kreis, station) selection weight = ewz * accessibility, normalised per Kreis.

    The weight reflects how attractive a rail entry station is for cross-cordon PT
    in-commuters from its source Kreis: a station with a larger population catchment
    (``ewz``) and a direct rail connection is preferred over a small or transfer-only
    station.

    - accessibility = 1.0 for ``reach == "direct"`` (no transfer needed).
    - accessibility = ``transfer_penalty`` for ``reach == "transfer"`` (one transfer
      required); must be in (0, 1] so transfer stations are downweighted but never
      excluded.
    - ``ewz`` is floored at 1.0 before multiplication to guard against zero or
      negative values (which would silently suppress a valid station).
    - Weights are normalised within each ``source_ars5`` so they form a proper
      probability distribution for per-agent sampling.  If a Kreis total is zero
      (theoretically impossible after the ewz floor, but guarded), all weights
      remain 0.

    Args:
        stations: DataFrame with at least columns ``[source_ars5, stop_id, reach,
            ewz]``.  Additional columns (e.g. ``x``, ``y``) are preserved unchanged.
            ``ewz`` is the station population catchment (inhabitants in the Gemeinden
            nearest to that station, set upstream).  ``reach`` must be ``"direct"``
            or ``"transfer"``.
        transfer_penalty: accessibility multiplier for one-transfer stations; default
            0.5.  Must be in (0, 1].

    Returns:
        Copy of ``stations`` with an additional ``weight`` column (float).  Weights
        sum to 1.0 within each ``source_ars5`` (provided the total is positive).
    """
    s = stations.copy()
    s["ewz"] = s["ewz"].astype(float).clip(lower=1.0)
    access = np.where(s["reach"].to_numpy() == "direct", 1.0, float(transfer_penalty))
    s["weight"] = s["ewz"].to_numpy() * access
    tot = s.groupby("source_ars5")["weight"].transform("sum")
    s["weight"] = np.where(tot.to_numpy() > 0, s["weight"] / tot.to_numpy(), 0.0)
    return s


def sample_pt_station_per_agent(orig_ars, weighted: pd.DataFrame,
                                rng) -> list:
    """Draw one entry station per PT agent from its Kreis's weighted stations.

    Each injected cross-cordon PT in-commuter belongs to an external source Kreis;
    this samples its rail entry station categorically, weighted by that Kreis's
    per-station weights from :func:`weight_entry_stations`.  Agents from a Kreis
    therefore spread over that Kreis's eligible stations in the same proportions the
    population+accessibility model produces.

    The caller is responsible for logging and handling ``None`` returns -- they
    indicate an agent whose Kreis has no eligible rail entry station in the schedule,
    which is a data gap that must not be silently snapped to a road gate.

    Args:
        orig_ars: iterable of each agent's external source Kreis ARS (5-digit
            string).
        weighted: output of :func:`weight_entry_stations`; must contain columns
            ``[source_ars5, stop_id, weight]``.
        rng: a ``numpy.random.Generator`` (seeded by the caller for reproducibility).

    Returns:
        list of ``stop_id`` values (one per agent).  ``None`` where the agent's
        Kreis has no station with positive weight; the caller logs and handles this
        fallback explicitly.
    """
    dist: dict = {}
    for ars5, sub in weighted.groupby("source_ars5"):
        sub = sub[sub["weight"] > 0]
        if len(sub):
            w = sub["weight"].to_numpy(dtype=float)
            dist[str(ars5)] = (sub["stop_id"].to_numpy(), w / w.sum())
    out = []
    for a in orig_ars:
        choice = dist.get(str(a))
        if choice is None:
            out.append(None)
        else:
            stop_ids, probs = choice
            out.append(stop_ids[int(rng.choice(len(stop_ids), p=probs))])
    return out
