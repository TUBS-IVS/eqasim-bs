"""Network-entry time at the cordon gate for injected in-commuters.

An in-commuter appears at its assigned cordon gate (outside-anchored home) and
travels to its workplace inside the ZGB network. The only external quantity we
derive is *when* it enters the network at the gate: the work-arrival time minus
the in-ZGB travel time (routed straight-line distance / speed x detour). Clamped
to >= 0 so an early shift never yields a negative departure.

Part of the cross-cordon external-demand module; see
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations


def gate_entry_time_s(work_arrival_s: float, gate_to_work_km: float,
                      speed_kmh: float, detour_factor: float = 1.3) -> float:
    """Seconds-of-day at which the in-commuter enters the network at the gate.

    Args:
        work_arrival_s: target work-arrival time (seconds of day).
        gate_to_work_km: straight-line gate->work distance (km).
        speed_kmh: assumed door-to-door speed for the in-ZGB leg (km/h).
        detour_factor: routed/straight-line ratio (default 1.3).

    Returns:
        max(0, work_arrival_s - travel_time_s).

    Raises:
        ValueError: if ``speed_kmh`` <= 0.
    """
    if speed_kmh <= 0:
        raise ValueError("gate_entry_time_s: speed_kmh must be > 0")
    travel_s = (gate_to_work_km * detour_factor) / speed_kmh * 3600.0
    return max(0.0, work_arrival_s - travel_s)
