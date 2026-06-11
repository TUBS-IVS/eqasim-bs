"""Shared constants and helpers for attribute-matched chain assignment.

Two popsim code paths assign a donor trip chain by ATTRIBUTE matching through
the reusable hierarchical-relaxation matcher
(``synthesis.population.matched.match_donors``):

- popsim_open: trip-less synthetic persons matched to ENTD diary donors
  (``braunschweig.popsim.sources.entd.EntdSource.build_trips``);
- popsim_mid: unfixable persons (cascade stage B) matched to valid-chain
  donors (``braunschweig.popsim.trips._match_unfixable``).

Both must bin age and cap the minimum donor-cell size identically, so the
constants live here exactly once (no copied literals).
"""

from __future__ import annotations

import numpy as np

# Age-class bin edges replicated from the legacy matching stage
# (synthesis/population/matched.py, execute(): the local AGE_BOUNDARIES used
# with np.digitize(..., right=True)). Replicated here because the legacy value
# is a function-local variable, not an importable constant.
CHAIN_MATCHING_AGE_BOUNDARIES = [14, 29, 44, 59, 74, 1000]

# Default minimum donor-cell size, identical to the legacy stage default
# (synthesis/population/matched.py, "matching_minimum_observations").
CHAIN_MATCHING_MINIMUM_OBSERVATIONS = 20


def derive_age_class(ages):
    """Bin exact ages into the legacy matching age classes (0..5).

    Same binning as the legacy matching stage: ``np.digitize`` with
    ``right=True`` on :data:`CHAIN_MATCHING_AGE_BOUNDARIES`.
    """
    return np.digitize(ages, CHAIN_MATCHING_AGE_BOUNDARIES, right=True)


def effective_minimum_observations(pool_size: int) -> int:
    """Minimum donor-cell size capped by the pool size.

    Small pools (mini smokes / tests) may be below the legacy default of 20
    donors per cell; cap the minimum at half the pool size (floor 1) so the
    matching is still possible while large real pools keep the legacy 20.
    """
    return min(CHAIN_MATCHING_MINIMUM_OBSERVATIONS, max(1, pool_size // 2))
