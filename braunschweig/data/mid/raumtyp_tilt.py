"""The within-Niedersachsen raumtyp tilt shared by the MiD distribution couplings.

The MiD couplings (car ownership, household income by size and by status, tenure by
income) take a Niedersachsen base distribution and tilt it toward one RegioStaR region:
the base is multiplied by ``P(category | region) / P(category | national raumtyp pool)``
and renormalised. :func:`tilt_toward_region` is the one implementation of that step;
each coupling module still derives its own base, region and national pmfs.
"""
from __future__ import annotations

import numpy as np


def tilt_toward_region(
    base: np.ndarray, region_pmf: np.ndarray, national_pmf: np.ndarray
) -> np.ndarray:
    """Tilt ``base`` by the region-to-national ratio and renormalise.

    The factor per category is ``region_pmf / national_pmf``, and 1 where the national
    share is zero (at most 1e-12), so a category absent nationally keeps its base share.
    Returns an untilted copy of ``base`` when the tilted mass is zero. All three arrays
    are ordered by the same categories; the result is a new array and sums to 1 whenever
    the tilted mass is positive.
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        tilt = np.where(national_pmf > 1e-12, region_pmf / national_pmf, 1.0)
    tilted = base * tilt
    total = tilted.sum()
    return (tilted / total) if total > 0 else base.copy()
