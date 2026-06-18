"""Income-age tilt for the per-vehicle age-band distribution.

Goal
----
For every household car, produce a **multiplicative tilt vector** of length 7
(one entry per ``AGE_BAND_LABELS``) such that

    tilt(age | segment, status) = P_MiD(age | segment, status) / P_MiD(age)

where ``P_MiD(age)`` is the base-weighted marginal over all (segment, status)
cells.  Multiplying the KBA ``P(age | powertrain)`` pmf by this tilt (and
renormalising) incorporates the empirical income×segment → age-of-car signal
without hard-coding the KBA distribution: an all-ones tilt (sum = 7) leaves
the KBA pmf unchanged after renormalisation.

Method
------
The tilt is a pure ratio of proper pmfs (each sums to 1 over 7 age bands):

  1. ``P_MiD(age | segment, status)`` -- the ``share`` column of
     ``mid2023_age_by_segment_status.csv``, already normalised per cell.
  2. ``P_MiD(age)`` -- the base-weighted marginal:

         P_MiD(age) = Σ_{seg,st} base_weighted(seg,st) * P(age|seg,st)
                      ————————————————————————————————————————————————
                          Σ_{seg,st} base_weighted(seg,st)

Fallback ladder (no-silent-fallback rule)
-----------------------------------------
When a (segment, status) cell is absent OR its ``base_weighted`` is below
``MIN_CELL_WEIGHT = 30``:

  1. **(status)-only pool** -- average ``P(age | seg, status)`` over all
     segments at that status, weighted by ``base_weighted``.  If this pool is
     also absent / degenerate → step 2.
  2. **all-ones** -- length-7 vector of 1.0 (KBA-only, no MiD signal).

Every fallback is logged at WARNING level and counted via ``_fallback_count``.
``log_fallback_rate()`` exposes the running primary/fallback rate.

Numeric safety
--------------
* Division-by-zero in the tilt ratio: where the marginal ``P_MiD(age)`` is
  ~0 (< 1e-12), the tilt at that band is set to 1.0.
* After all guards, NaN/inf entries are replaced by 1.0.
* The module never raises on missing / sparse cells; only on schema failures
  propagated from the loader.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np
import pandas as pd

from braunschweig.data.kba import fleet_tables as ft

logger = logging.getLogger(__name__)

#: Minimum base_weighted for a (segment, status) cell to be used directly.
#: Cells below this threshold are treated as missing and fall back via the
#: fallback ladder to the (status)-only pool or all-ones.
MIN_CELL_WEIGHT: float = 30.0

#: Convenience alias.
_AGE_BANDS = list(ft.AGE_BAND_LABELS)
_N_BANDS = len(_AGE_BANDS)
_STATUSES = list(ft.STATUS_LABELS)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #

def _cell_pmf(df: pd.DataFrame, segment: str, status: str) -> tuple[np.ndarray, float] | None:
    """Return ``(pmf, base_weighted)`` for a (segment, status) cell, or None.

    Returns None when the cell is absent from *df* or has a degenerate share
    vector (all zeros).  Does NOT apply the MIN_CELL_WEIGHT guard -- the caller
    decides.
    """
    sub = df[(df["segment"] == segment) & (df["status"] == status)]
    if sub.empty:
        return None
    pmf = (
        sub.set_index("age_band")["share"]
        .reindex(_AGE_BANDS)
        .fillna(0.0)
        .to_numpy(dtype=float)
    )
    total = pmf.sum()
    if total <= 0:
        return None
    base = float(sub["base_weighted"].iloc[0])
    return pmf / total, base


def _status_pool_pmf(df: pd.DataFrame, status: str) -> np.ndarray | None:
    """Base-weighted average of ``P(age | seg, status)`` over all segments.

    Used as the (status)-only fallback when the (segment, status) cell is
    absent / low-base.  Returns None if no valid segment contributes.
    """
    sub = df[df["status"] == status]
    if sub.empty:
        return None
    weighted = np.zeros(_N_BANDS, dtype=float)
    total_base = 0.0
    for seg in sub["segment"].unique():
        res = _cell_pmf(sub, seg, status)
        if res is None:
            continue
        pmf, base = res
        if base < MIN_CELL_WEIGHT:
            continue
        weighted += base * pmf
        total_base += base
    if total_base <= 0:
        return None
    return weighted / total_base


def _marginal_pmf(df: pd.DataFrame) -> np.ndarray:
    """Base-weighted marginal ``P_MiD(age)`` pooled over all (segment, status).

    Falls back to uniform if the table is empty.
    """
    weighted = np.zeros(_N_BANDS, dtype=float)
    total_base = 0.0
    for (seg, status), grp in df.groupby(["segment", "status"]):
        res = _cell_pmf(grp, seg, status)
        if res is None:
            continue
        pmf, base = res
        weighted += base * pmf
        total_base += base
    if total_base <= 0:
        return np.ones(_N_BANDS, dtype=float) / _N_BANDS
    return weighted / total_base


def _safe_ratio(numerator: np.ndarray, denominator: np.ndarray) -> np.ndarray:
    """Elementwise numerator/denominator with guard: where denom ~0 use 1.0."""
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = np.where(denominator > 1e-12, numerator / denominator, 1.0)
    return np.where(np.isfinite(ratio), ratio, 1.0)


# --------------------------------------------------------------------------- #
# AgeIncomeModel
# --------------------------------------------------------------------------- #

@dataclass
class AgeIncomeModel:
    """Income-age tilt model.

    Build with :meth:`from_data_path` (production) or :meth:`_from_dataframe`
    (unit-testing with a synthetic DataFrame).

    The core artefact is a dict mapping ``(segment, status)`` -> length-7 pmf
    (cells that pass the MIN_CELL_WEIGHT filter) and the length-7 overall
    marginal ``P_MiD(age)`` used as the tilt denominator.
    """

    #: Lookup: (segment, status) -> P_MiD(age | segment, status) pmf.
    _cell_pmfs: dict[tuple[str, str], np.ndarray]
    #: Lookup: status -> P_MiD(age | status) pooled pmf (fallback level 1).
    _status_pmfs: dict[str, np.ndarray]
    #: Overall marginal P_MiD(age) (fallback level 2 denominator).
    _marginal: np.ndarray

    # Mutable fallback counters.
    _primary_count: int = field(default=0)
    _fallback_count: int = field(default=0)

    # -- construction --------------------------------------------------------

    @classmethod
    def _from_dataframe(cls, df: pd.DataFrame) -> "AgeIncomeModel":
        """Build from a DataFrame with the same schema as the loader output.

        Exposed for unit tests that inject a synthetic table without touching
        the filesystem.
        """
        # Build per-cell pmfs (only cells above MIN_CELL_WEIGHT).
        cell_pmfs: dict[tuple[str, str], np.ndarray] = {}
        for (seg, status), grp in df.groupby(["segment", "status"]):
            res = _cell_pmf(grp, seg, status)
            if res is None:
                continue
            pmf, base = res
            if base >= MIN_CELL_WEIGHT:
                cell_pmfs[(seg, status)] = pmf

        # Build per-status pooled pmfs (fallback level 1).
        status_pmfs: dict[str, np.ndarray] = {}
        for status in _STATUSES:
            pool = _status_pool_pmf(df, status)
            if pool is not None:
                status_pmfs[status] = pool

        # Build overall marginal (fallback level 2 denominator).
        marginal = _marginal_pmf(df)

        n_total = len(df[["segment", "status"]].drop_duplicates())
        n_kept = len(cell_pmfs)
        logger.info(
            "[age_income] loaded %d (segment,status) cells above MIN_CELL_WEIGHT=%.0f "
            "(of %d total); %d status-level pools; marginal P_MiD(age) computed",
            n_kept, MIN_CELL_WEIGHT, n_total, len(status_pmfs),
        )
        return cls(
            _cell_pmfs=cell_pmfs,
            _status_pmfs=status_pmfs,
            _marginal=marginal,
        )

    @classmethod
    def from_data_path(cls, data_path: str) -> "AgeIncomeModel":
        """Construct from the committed ``mid2023_age_by_segment_status.csv``."""
        df = ft.load_mid_age_by_segment_status(data_path)
        return cls._from_dataframe(df)

    # -- core query ----------------------------------------------------------

    def age_tilt(self, segment: str, status: str) -> np.ndarray:
        """Return the length-7 multiplicative tilt vector.

        tilt[i] = P_MiD(age_band_i | segment, status) / P_MiD(age_band_i)

        An all-ones vector (sum = 7) means no income signal (KBA-only).

        Fallback ladder (logged, counted):
          1. Direct (segment, status) cell if base_weighted >= MIN_CELL_WEIGHT.
          2. (status)-only pool if the cell is missing / low-base.
          3. All-ones if the status pool is also missing.
        """
        numerator = self._cell_pmfs.get((segment, status))

        if numerator is None:
            # Level-2 fallback: (status)-only pool.
            pool = self._status_pmfs.get(status)
            if pool is None:
                # Level-3 fallback: all-ones.
                self._fallback_count += 1
                logger.warning(
                    "[age_income] no (segment,status) cell AND no (status) pool "
                    "for (%r, %r) -> all-ones tilt (KBA-only)", segment, status,
                )
                return np.ones(_N_BANDS, dtype=float)
            self._fallback_count += 1
            logger.warning(
                "[age_income] missing/low-base (segment,status) cell for "
                "(%r, %r) -> (status)-only pool fallback", segment, status,
            )
            return _safe_ratio(pool, self._marginal)

        self._primary_count += 1
        return _safe_ratio(numerator, self._marginal)

    # -- fallback observability ---------------------------------------------

    def log_fallback_rate(
        self,
        queries: Iterable[tuple[str, str]] | None = None,
    ) -> tuple[int, int]:
        """Log the primary-vs-fallback rate (no-silent-fallback rule).

        If *queries* is given (iterable of ``(segment, status)``), each is
        evaluated (resetting the running counters first) so the logged rate
        reflects exactly that batch.  Otherwise the running counters
        accumulated by prior :meth:`age_tilt` calls are logged.

        Returns ``(primary, fallback)``.
        """
        if queries is not None:
            self._primary_count = 0
            self._fallback_count = 0
            for seg, status in queries:
                self.age_tilt(seg, status)

        primary = self._primary_count
        fallback = self._fallback_count
        total = primary + fallback
        rate = (fallback / total) if total > 0 else 0.0
        log = logger.warning if rate > 0.05 else logger.info
        log(
            "[age_income] tilt: primary %d/%d (%.1f%%), fallback %d (%.1f%%)",
            primary, total, 100.0 * primary / total if total else 0.0,
            fallback, 100.0 * rate,
        )
        return primary, fallback
