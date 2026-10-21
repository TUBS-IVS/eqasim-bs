"""Namespace of the in-repo synpp 1.6.2 fork (spec 2026-10-16).

Phase 1 only wraps ``braunschweig.synpp_deterministic`` (the deterministic implicit-config
propagation of ADR-0105) behind ``install()`` and adds the read-only registry builder,
metadata reader and staleness assessment used by the explainer. Later phases add the
vendored ``run()`` / ``get_stage_hash()`` (semantic stage hash, content-addressed validity).
"""
from __future__ import annotations

from braunschweig import synpp_deterministic

UnsupportedSynppVersion = synpp_deterministic.UnsupportedSynppVersion


def installed_synpp_version() -> str:
    return synpp_deterministic.installed_synpp_version()


def install(*, force_version_check: bool = True) -> None:
    """Install every project patch of synpp before the stage graph is built."""
    synpp_deterministic.install(force_version_check=force_version_check)
