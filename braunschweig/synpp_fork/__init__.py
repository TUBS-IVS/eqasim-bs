"""In-repo vendored fork of the synpp 1.6.2 pipeline runtime (spec 2026-10-16).

Phase 1 installs only the deterministic implicit-config propagation of ADR-0105. Later
phases add the semantic stage hash and the content-addressed validity rule of ``run()``.
"""
from __future__ import annotations

from braunschweig import synpp_deterministic

UnsupportedSynppVersion = synpp_deterministic.UnsupportedSynppVersion


def installed_synpp_version() -> str:
    return synpp_deterministic.installed_synpp_version()


def install(*, force_version_check: bool = True) -> None:
    """Install every project patch of synpp before the stage graph is built."""
    synpp_deterministic.install(force_version_check=force_version_check)
