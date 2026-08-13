"""Canonical path constants for the dashboard package.

``REPO_ROOT`` and ``DASHBOARD_DIR`` were previously recomputed privately (via
``Path(__file__).resolve()``) in every sibling that needed them
(``build_dashboard.py``, ``mid_reference.py``, ``spatial_metrics.py``) -- same
value, duplicated derivation. This leaf module centralises that single fact so
future drift (e.g. a sibling moving to a different directory depth and
silently getting a wrong ``parents[N]`` index) is caught in one place instead
of three. ``RUNS_DIR`` (the per-run output directory) is a pure derivation of
``DASHBOARD_DIR`` with no sibling-specific logic attached, so it moves here
too rather than staying a ``build_dashboard.py``-only constant; any future
sibling that needs it (e.g. a run-records module) can import it from here
without recomputing.

Domain-specific derived paths (``MID_DIR`` in ``mid_reference.py``,
``VG250_ZIP``/``VG250_CACHE`` in ``spatial_metrics.py``) are not moved here:
they are owned by the sibling whose data they describe, and are built from the
anchors below.

This module must import nothing from this package -- it is a leaf that every
sibling (and the facade) may depend on.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DASHBOARD_DIR = Path(__file__).resolve().parent
RUNS_DIR = DASHBOARD_DIR / "runs"
