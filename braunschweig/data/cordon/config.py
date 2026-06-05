"""Typed config for the cross-cordon external-demand module.

Generic, region-neutral keys with safe defaults; the master flag ``cordon_enabled``
defaults to False so the whole feature is OFF (pipeline byte-identical) unless a
config opts in. See
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Default car-gate road classes: long-distance commuters enter on motorways and
# Bundesstrassen (OSM motorway/trunk/primary, plus secondary as a fallback tier).
_DEFAULT_GATE_ROAD_CLASSES = ("motorway", "trunk", "primary", "secondary")


@dataclass(frozen=True)
class CordonConfig:
    """Resolved cross-cordon settings.

    Attributes:
        enabled: master flag; False keeps the pipeline byte-identical.
        network_buffer_fraction: cordon buffer as a fraction of the region extent.
        gate_road_classes: road classes eligible to host car gates.
        gate_min_capacity: optional minimum link capacity for a gate.
        gate_top_n: optional cap on the number of car gates (by capacity).
    """

    enabled: bool = False
    network_buffer_fraction: float = 0.10
    gate_road_classes: tuple = _DEFAULT_GATE_ROAD_CLASSES
    gate_min_capacity: float | None = None
    gate_top_n: int | None = None

    @classmethod
    def from_mapping(cls, cfg) -> "CordonConfig":
        """Build from a plain mapping (e.g. synpp config) using ``cordon_*`` keys."""
        buffer_fraction = float(cfg.get("cordon_network_buffer_fraction", 0.10))
        if buffer_fraction < 0:
            raise ValueError("cordon_network_buffer_fraction must be >= 0")
        road_classes = cfg.get("cordon_gate_road_classes", _DEFAULT_GATE_ROAD_CLASSES)
        top_n = cfg.get("cordon_gate_top_n", None)
        min_capacity = cfg.get("cordon_gate_min_capacity", None)
        return cls(
            enabled=bool(cfg.get("cordon_enabled", False)),
            network_buffer_fraction=buffer_fraction,
            gate_road_classes=tuple(road_classes),
            gate_min_capacity=None if min_capacity is None else float(min_capacity),
            gate_top_n=None if top_n is None else int(top_n),
        )
