"""The within-Niedersachsen raumtyp tilt shared by the MiD distribution couplings.

The MiD couplings (car ownership, household income by size and by status, tenure by
income) take a Niedersachsen base distribution and tilt it toward one RegioStaR region:
the base is multiplied by ``P(category | region) / P(category | national raumtyp pool)``
and renormalised. :func:`tilt_toward_region` is the one implementation of that step;
each coupling module still derives its own base, region and national pmfs.

:class:`FallbackTally` (CLAUDE.md "Fallback transparency") lives here as well: the five
coupling modules (income_by_size, income_by_status, cars_by_status, status_by_hhtype,
tenure_by_income) count their degenerate-input branches with it and the caller logs
ONE rate line per build. A unit is one decision of the table build; each unit is
expected to fire at most one branch, primary = units - fired branches (clamped at 0).
Counting never changes a returned value. It sits in this already-hashed module so no
new helper module has to be added to every stage's synpp hash list.
"""
from __future__ import annotations

import logging

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


# Fallback share above which the report is a WARNING instead of INFO.
FALLBACK_WARN_FRACTION = 0.05


class FallbackTally:
    """Counts units and fired fallback branches of one table; logs one rate line."""

    def __init__(self, table: str, logger: logging.Logger):
        self.table = table
        self._logger = logger
        self.units = 0
        self.fallbacks: dict[str, int] = {}

    def unit(self, n: int = 1) -> None:
        """Register ``n`` decisions taken by the table build."""
        self.units += n

    def check(self, branch: str, fired: bool) -> bool:
        """Count ``branch`` when ``fired`` and return ``fired`` unchanged."""
        if fired:
            self.fallbacks[branch] = self.fallbacks.get(branch, 0) + 1
        return fired

    def report(self) -> None:
        """Log ``"<table>: primary X/N (..%), fallback <branch> Y (..%)"`` and reset."""
        n = self.units
        fired = dict(self.fallbacks)
        self.units = 0
        self.fallbacks = {}
        if n <= 0:
            return
        n_fb = sum(fired.values())
        primary = max(n - n_fb, 0)
        parts = ", ".join(
            f"fallback {b} {c} ({100.0 * c / n:.1f}%)" for b, c in sorted(fired.items())
        ) or "fallback 0 (0.0%)"
        msg = f"{self.table}: primary {primary}/{n} ({100.0 * primary / n:.1f}%), {parts}"
        if n_fb / n > FALLBACK_WARN_FRACTION:
            self._logger.warning(msg)
        else:
            self._logger.info(msg)
