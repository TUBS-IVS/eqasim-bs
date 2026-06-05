"""Cross-cordon in-commuter (Einpendler) synthesis helpers.

This module assembles injected in-commuter agents for the cross-cordon external-
demand feature. It composes the already-tested building blocks:

  - demand:        BA-Pendler OD -> per-agent (orig/dest Kreis) counts.
  - gate_assignment: population-gravity Kreis->gate volumes + per-agent gate draw.
  - mode_reference / plans: Mikrozensus fixed mode + resident-schema plan frames.
  - gate_entry:    network-entry time at the gate (work start - in-ZGB travel).

It also derives the **PT entry stops** (rail/bus) per source Kreis from the cut
transit schedule: stops on a route that also serves a ZGB stop, i.e. a one-seat
(no-transfer) ride into the region, where PT in-commuters board.

Region-neutral; the synpp stage wires the data sources. See
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations


def direct_ride_stops(routes, stop_kreis, zgb_kreise):
    """Per source Kreis, the transit stops offering a one-seat ride into ZGB.

    A route gives a one-seat ride into the region if it serves at least one stop in
    a ZGB Kreis. Every NON-ZGB stop on such a route is an entry stop for its source
    Kreis -- a PT in-commuter from that Kreis can board there and reach ZGB without
    transferring. (Rail and bus are treated identically; the route mode is ignored
    here.)

    Args:
        routes: iterable of ``(mode, [stop_id, ...])`` transit routes (stop order).
        stop_kreis: mapping ``stop_id -> 5-digit Kreis ARS`` (None if unmapped).
        zgb_kreise: iterable of in-scope ZGB 5-digit Kreis ARS.

    Returns:
        dict ``{source_ars5: set(stop_id)}`` -- the entry stops per external Kreis.
    """
    zgb = {str(k) for k in zgb_kreise}
    entry: dict[str, set] = {}
    for _mode, stops in routes:
        kreise = [stop_kreis.get(s) for s in stops]
        if not any(k in zgb for k in kreise if k is not None):
            continue
        for stop_id, kreis in zip(stops, kreise):
            if kreis is not None and kreis not in zgb:
                entry.setdefault(kreis, set()).add(stop_id)
    return entry
