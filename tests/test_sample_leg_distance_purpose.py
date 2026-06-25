"""Tests for _sample_leg_distance purpose-layer auto-detection.

Covers two paths:
  - legacy per-mode structure {mode: ...} -> mode_distributions = distributions
  - purpose-layered structure {purpose: {mode: ...}} -> mode_distributions = distributions[purpose]

The vocabulary disjointness (purposes vs modes) is the key invariant: a
top-level key equal to `mode` means the legacy structure; otherwise the purpose
layer is expected.
"""

import numpy as np
import pytest

from braunschweig.synthesis.locations.secondary_chainsolvers import _sample_leg_distance


def _mode_dist(value_km):
    """One travel-time bin, deterministic single value at value_km * 1000 m."""
    return {
        "bounds": np.array([np.inf]),
        "distributions": [
            {
                "cdf": np.array([1.0]),
                "values": np.array([value_km * 1000.0]),
                "weights": np.array([1.0]),
            }
        ],
    }


def test_legacy_mode_keyed_path():
    """Legacy {mode: dist} structure: mode key present at top level -> legacy path."""
    dist = {"car": _mode_dist(5.0)}
    d = _sample_leg_distance(dist, "car", 600, "shop", 1.0, np.random.RandomState(0))
    assert abs(d - 5000.0) < 1e-6, f"Expected 5000.0 m, got {d}"


def test_purpose_layer_selects_by_purpose():
    """Purpose-layered {purpose: {mode: dist}}: correct purpose selected per call."""
    dist = {
        "shop": {"car": _mode_dist(3.0)},
        "leisure": {"car": _mode_dist(12.0)},
    }
    ds = _sample_leg_distance(dist, "car", 600, "shop", 1.0, np.random.RandomState(0))
    dl = _sample_leg_distance(dist, "car", 600, "leisure", 1.0, np.random.RandomState(0))
    assert abs(ds - 3000.0) < 1e-6, f"shop: expected 3000.0 m, got {ds}"
    assert abs(dl - 12000.0) < 1e-6, f"leisure: expected 12000.0 m, got {dl}"
