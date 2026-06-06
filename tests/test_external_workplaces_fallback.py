"""Fallback-transparency tests for ``braunschweig.data.external_workplaces``.

The external-workplace stage anchors each external Kreis at the
population-weighted Gemeinde centroid (``emp_weighted``) when usable VG250-EW
Gemeinde EWZ is available, and otherwise falls back to the cruder dissolved
Kreis-polygon centroid (``kreis_centroid``).  This fallback was previously
logged only as two raw counts, with no explicit share and no ``WARNING:``
threshold (CLAUDE.md "Fallback transparency").

These tests exercise the pure accounting / logging helper
``summarise_placement_fallback`` with small synthetic ``placement`` label
series (no real GeoPackage IO) and assert that:

  * all-weighted -> 0 Kreis-centroid fallback, share 0.0, no WARNING,
  * any missing-weight Kreis is counted as a Kreis-centroid fallback,
  * the explicit share is rendered and gets a ``WARNING:`` prefix above the
    configured threshold.

The helper is pure (counting / string formatting only): it does not alter any
geometry, value, or RNG draw, so it is output-preserving.
"""
from __future__ import annotations

import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from braunschweig.data import external_workplaces  # noqa: E402


def test_all_weighted_zero_centroid_fallback():
    # Every external Kreis had usable EWZ -> no Kreis-centroid fallback.
    placement = ["emp_weighted", "emp_weighted", "emp_weighted"]
    line = external_workplaces.summarise_placement_fallback(
        placement, total_employees=61_160, min_flow=50
    )
    assert not line.startswith("WARNING: ")
    assert "3 emp-weighted" in line
    assert "0 Kreis-centroid fallback (0.0%)" in line


def test_missing_weight_is_counted_as_centroid_fallback():
    # One Kreis lacked usable Gemeinde EWZ -> counted as Kreis-centroid fallback.
    placement = ["emp_weighted", "kreis_centroid", "emp_weighted", "emp_weighted"]
    line = external_workplaces.summarise_placement_fallback(
        placement, total_employees=12_345, min_flow=50
    )
    # 1 / 4 = 25 % fallback -> above the 10 % threshold -> WARNING.
    assert line.startswith("WARNING: ")
    assert "3 emp-weighted" in line
    assert "1 Kreis-centroid fallback (25.0%)" in line


def test_fallback_share_below_threshold_no_warning():
    # 1 / 20 = 5 % fallback -> below the 10 % threshold -> no WARNING.
    placement = ["kreis_centroid"] + ["emp_weighted"] * 19
    line = external_workplaces.summarise_placement_fallback(
        placement, total_employees=1_000, min_flow=50
    )
    assert not line.startswith("WARNING: ")
    assert "19 emp-weighted" in line
    assert "1 Kreis-centroid fallback (5.0%)" in line


def test_fallback_share_above_threshold_emits_warning():
    # 2 / 4 = 50 % fallback -> well above threshold -> WARNING.
    placement = ["kreis_centroid", "emp_weighted", "kreis_centroid", "emp_weighted"]
    line = external_workplaces.summarise_placement_fallback(
        placement, total_employees=999, min_flow=50
    )
    assert line.startswith("WARNING: ")
    assert "2 Kreis-centroid fallback (50.0%)" in line


def test_threshold_constant_is_a_fraction():
    # Documented threshold must be a sane fraction in (0, 1).
    share = external_workplaces.KREIS_CENTROID_FALLBACK_WARN_SHARE
    assert 0.0 < share < 1.0
