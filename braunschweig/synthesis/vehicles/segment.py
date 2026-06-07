"""Income-coupled vehicle segment IPF (KBA marginal x MiD economic status).

Goal
----
For every household car, produce ``P(segment | economic_status, raumtyp)`` such
that

  * the **segment marginal is KBA-exact** -- averaged over the population's
    economic-status distribution, the segment shares reproduce the national KBA
    FZ 27.10 segment marginal, and
  * the **income coupling is MiD-empirical** -- higher economic status puts more
    mass on the upper segments, exactly as observed in MiD 2023, with no free
    correlation parameter.

Method
------
The two pieces of evidence are:

  1. ``P(segment)`` -- the KBA FZ 27.10 segment marginal (``segment_share`` of
     ``kba_segment_powertrain.csv``).
  2. ``P(status | segment, region)`` -- the MiD column-% (``share_pct`` of
     ``mid2023_segment_by_status_*``), with ``base_weighted`` giving the
     per-segment weighted base.

We combine them on a ``segment x status`` contingency table ``X`` by **2D raking**
(the project's :func:`braunschweig.ipf.joint_age_size.rake_2d`), exactly as the
P24.1 / joint-age-size IPFs do:

  * the **seed** is the Bayes joint
    ``X0[seg, st] = P(status | segment) * P(segment)`` -- this already carries
    BOTH the MiD status<->segment association and (as its segment marginal) the
    KBA marginal;
  * the **row target** is the KBA segment marginal ``P(segment)`` (kept exact);
  * the **column target** is the NDS economic-status distribution
    ``P(status)`` recovered from the MiD per-(segment, status) weighted bases.

Raking to consistent marginals does not destroy the seed's association, so the
result is a joint that is KBA-marginal-exact, MiD-status-marginal-consistent, and
preserves the empirical income<->segment coupling. ``P(segment | status)`` is
then the status column of the raked joint, renormalised over segments.

Niedersachsen is the **base region** (the ZGB scope is entirely within NDS). The
RegioStaR-7 raumtyp table is applied as a **within-NDS tilt** -- identical in
spirit to :func:`braunschweig.data.mid.status_by_hhtype.region_status_probabilities`:

  tilt(seg | status) = P_raumtyp,region(seg | status) / P_raumtyp,national(seg | status)

and ``P(segment | status, raumtyp) = P_NDS(segment | status) * tilt``,
renormalised. Where a (status, raumtyp) cell is missing (unknown RS7 code, or a
raumtyp with no data for that status), the NDS-only pmf is used and the
primary-vs-fallback rate is logged (project no-silent-fallback rule).

The numeric reference values live in the committed derived CSVs (loaded via
:mod:`braunschweig.data.kba.fleet_tables`); hard-coding them in Python is
prohibited (see CLAUDE.md).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from braunschweig.data.kba import fleet_tables as ft
from braunschweig.ipf.joint_age_size import rake_2d

logger = logging.getLogger(__name__)

#: Price-tier ranking of the passenger-car segments, from inexpensive to
#: expensive, used by the monotonicity diagnostic :meth:`SegmentModel.segment_rank_vector`
#: and as the documented ordinal meaning of "upper segment". This is an ordering
#: only (not a price). Only the segments whose price tier is well defined are
#: ranked; the body-style / residual segments that do NOT carry a clear price
#: signal -- ``utilities`` (vans/transporters), ``sonstige`` (unclassifiable
#: residual) and ``wohnmobile`` (absent from the MiD table, hence uninformative
#: about income) -- are deliberately EXCLUDED (rank ``NaN``) so the income-tier
#: monotonicity diagnostic is not confounded by them. The income coupling is a
#: statement about the price tier of the car, which only the ranked segments
#: express.
SEGMENT_RANK_ORDER: tuple[str, ...] = (
    "minis",
    "kleinwagen",
    "kompaktklasse",
    "mini_vans",
    "mittelklasse",
    "grossraum_vans",
    "suv",
    "obere_mittelklasse",
    "gelaendewagen",
    "sportwagen",
    "oberklasse",
)


def _status_given_segment(df: pd.DataFrame, region: str,
                          segments: Sequence[str]) -> np.ndarray:
    """Return ``P(status | segment, region)`` as a ``(n_segment, n_status)`` matrix.

    ``share_pct`` is the MiD column percentage (per (segment, region) it sums to
    ~100 over status). We renormalise each segment row to a proper pmf over
    status. Segments absent from the MiD table (e.g. ``wohnmobile``) or with an
    all-zero row get a uniform status distribution so the seed stays positive and
    the rake can place the KBA marginal there.
    """
    statuses = list(ft.STATUS_LABELS)
    sub = df[df["region"] == region]
    out = np.zeros((len(segments), len(statuses)), dtype=float)
    for i, seg in enumerate(segments):
        row = (
            sub[sub["segment"] == seg]
            .set_index("status")["share_pct"]
            .reindex(statuses)
            .fillna(0.0)
            .to_numpy(dtype=float)
        )
        total = row.sum()
        if total > 0:
            out[i] = row / total
        else:
            out[i] = np.ones(len(statuses)) / len(statuses)
    return out


def _status_distribution(df: pd.DataFrame, region: str,
                         segments: Sequence[str]) -> np.ndarray:
    """Recover ``P(status | region)`` from the MiD weighted bases.

    ``base_weighted`` is the per-(segment, region) weighted base (repeated across
    the status rows). The status marginal is approximated by the base-weighted
    average of the per-segment column-% across segments -- i.e. the population
    status distribution implied by the MiD table for ``region``.
    """
    statuses = list(ft.STATUS_LABELS)
    sub = df[df["region"] == region]
    weighted = np.zeros(len(statuses), dtype=float)
    for seg in segments:
        seg_rows = sub[sub["segment"] == seg]
        if seg_rows.empty:
            continue
        base = float(seg_rows["base_weighted"].iloc[0])
        if not np.isfinite(base) or base <= 0:
            continue
        share = (
            seg_rows.set_index("status")["share_pct"]
            .reindex(statuses).fillna(0.0).to_numpy(dtype=float)
        )
        share_sum = share.sum()
        if share_sum <= 0:
            continue
        # base * P(status | segment) summed over segments == base-weighted count
        # per status; summed over segments it is proportional to P(status).
        weighted += base * (share / share_sum)
    total = weighted.sum()
    if total <= 0:
        return np.ones(len(statuses)) / len(statuses)
    return weighted / total


@dataclass
class SegmentModel:
    """Income-coupled ``P(segment | status, raumtyp)`` model.

    Build with :meth:`from_data_path`. The core artefact is the raked NDS joint
    ``joint_nds[segment, status]`` (KBA-marginal-exact, MiD-status-coupled); the
    raumtyp tilt is applied lazily per query in :meth:`segment_probabilities`.
    """

    segments: list[str]
    kba_marginal: np.ndarray                 # P(segment), KBA FZ 27.10
    nds_status: np.ndarray                   # P(status), MiD NDS weighted base
    joint_nds: np.ndarray                    # raked P(segment, status), NDS
    raumtyp_status_given_segment: dict[str, np.ndarray]  # region -> P(status|seg)
    national_raumtyp_status_given_segment: np.ndarray    # pooled P(status|seg)
    rake_residual: float
    rake_converged: bool
    # Mutable fallback counters (one model instance per run).
    _primary_count: int = field(default=0)
    _fallback_count: int = field(default=0)

    # -- construction --------------------------------------------------------
    @classmethod
    def from_data_path(cls, data_path: str,
                       max_iterations: int = 1000,
                       tolerance: float = 1e-12) -> "SegmentModel":
        """Construct the model from the committed KBA / MiD derived CSVs."""
        df_kba = ft.load_segment_powertrain(data_path)
        df_bl = ft.load_mid_segment_by_status_bundesland(data_path)
        df_rt = ft.load_mid_segment_by_status_raumtyp(data_path)

        # Segment order = KBA FZ 27.10 order (carries the marginal).
        df_kba = df_kba.reset_index(drop=True)
        segments = list(df_kba["segment"])
        kba_marginal = df_kba["segment_share"].to_numpy(dtype=float)
        kba_marginal = kba_marginal / kba_marginal.sum()

        # MiD NDS evidence.
        p_status_given_segment = _status_given_segment(
            df_bl, ft.BUNDESLAND_NIEDERSACHSEN, segments)
        nds_status = _status_distribution(
            df_bl, ft.BUNDESLAND_NIEDERSACHSEN, segments)

        # Seed = Bayes joint P(status | segment) * P(segment): carries both the
        # MiD association and (as its segment marginal) the KBA marginal.
        seed = p_status_given_segment * kba_marginal[:, None]

        # Rake to row target = KBA segment marginal, col target = NDS status
        # distribution. Both targets sum to 1, so the rake is feasible.
        joint = rake_2d(
            seed,
            row_targets=kba_marginal,
            col_targets=nds_status,
            max_iterations=max_iterations,
            tolerance=tolerance,
        )
        total = joint.sum()
        if total > 0:
            joint = joint / total
        # Convergence diagnostic: how well the raked segment marginal matches KBA.
        residual = float(np.max(np.abs(joint.sum(axis=1) - kba_marginal)))
        converged = residual < 1e-3

        # Raumtyp tilt evidence: P(status | segment) per raumtyp region, plus the
        # base-weighted national pooled P(status | segment) used as the tilt
        # denominator.
        regions = list(ft.RS7_TO_RAUMTYP_REGION.values())
        raumtyp_psg = {
            region: _status_given_segment(df_rt, region, segments)
            for region in regions
        }
        national_psg = _pooled_status_given_segment(df_rt, segments)

        return cls(
            segments=segments,
            kba_marginal=kba_marginal,
            nds_status=nds_status,
            joint_nds=joint,
            raumtyp_status_given_segment=raumtyp_psg,
            national_raumtyp_status_given_segment=national_psg,
            rake_residual=residual,
            rake_converged=converged,
        )

    # -- public diagnostics --------------------------------------------------
    def kba_segment_marginal(self) -> np.ndarray:
        """The KBA FZ 27.10 segment marginal (pmf over :attr:`segments`)."""
        return self.kba_marginal.copy()

    def nds_status_distribution(self) -> np.ndarray:
        """The MiD NDS economic-status distribution (pmf over status)."""
        return self.nds_status.copy()

    def segment_rank_vector(self) -> np.ndarray:
        """Price-tier rank (0..) per segment in :attr:`segments`, small->large.

        Segments without a defined price tier (``utilities`` / ``sonstige`` /
        ``wohnmobile``; see :data:`SEGMENT_RANK_ORDER`) get ``NaN`` so they are
        excluded from the income-tier monotonicity diagnostic. Used only by the
        diagnostic, not by the sampling path.
        """
        order = {seg: i for i, seg in enumerate(SEGMENT_RANK_ORDER)}
        return np.array([order.get(seg, np.nan) for seg in self.segments],
                        dtype=float)

    def mean_segment_rank(self, pmf: np.ndarray) -> float:
        """Expected price-tier rank of ``pmf`` over the ranked segments only.

        The pmf mass on the non-priced segments (rank ``NaN``) is removed and the
        remaining mass renormalised, so the result is the mean price tier
        conditional on the car belonging to a price-tier segment.
        """
        ranks = self.segment_rank_vector()
        valid = np.isfinite(ranks)
        mass = pmf[valid]
        total = mass.sum()
        if total <= 0:
            return float("nan")
        return float(np.dot(mass / total, ranks[valid]))

    # -- core query ----------------------------------------------------------
    def segment_probabilities(self, economic_status: str,
                              raumtyp: Optional[int]) -> np.ndarray:
        """Return ``P(segment | economic_status, raumtyp)`` as a pmf.

        Parameters
        ----------
        economic_status : one of :data:`braunschweig.data.kba.fleet_tables.STATUS_LABELS`.
        raumtyp : a RegioStaR-7 code (71..77), or ``None`` for the NDS-only base.
            An unknown code (no raumtyp region, or a raumtyp lacking data for the
            requested status) falls back to the NDS-only pmf; the fallback is
            counted and logged (see :meth:`log_fallback_rate`).

        The vector is ordered like :attr:`segments` and sums to 1.
        """
        if economic_status not in ft.STATUS_LABELS:
            raise ValueError(
                f"unknown economic_status '{economic_status}' "
                f"(expected one of {ft.STATUS_LABELS})"
            )
        status_index = ft.STATUS_LABELS.index(economic_status)

        # NDS base: P(segment | status) = status column of the raked joint.
        column = self.joint_nds[:, status_index]
        col_sum = column.sum()
        if col_sum <= 0:
            base = self.kba_marginal.copy()
        else:
            base = column / col_sum

        if raumtyp is None:
            self._primary_count += 1
            return base

        region = ft.RS7_TO_RAUMTYP_REGION.get(raumtyp)
        if region is None:
            self._fallback_count += 1
            logger.warning(
                "[segment_ipf] unknown RegioStaR-7 raumtyp code %r -> fallback "
                "to NDS-only segment pmf (status=%s)", raumtyp, economic_status,
            )
            return base

        region_psg = self.raumtyp_status_given_segment.get(region)
        national_psg = self.national_raumtyp_status_given_segment
        if region_psg is None:
            self._fallback_count += 1
            logger.warning(
                "[segment_ipf] raumtyp region '%s' has no data -> fallback to "
                "NDS-only segment pmf (status=%s)", region, economic_status,
            )
            return base

        # Within-NDS tilt: P_region(status|seg) / P_national(status|seg) at the
        # requested status, applied multiplicatively to the NDS base.
        reg_col = region_psg[:, status_index]
        nat_col = national_psg[:, status_index]
        with np.errstate(divide="ignore", invalid="ignore"):
            tilt = np.where(nat_col > 1e-12, reg_col / nat_col, 1.0)
        tilt = np.where(np.isfinite(tilt), tilt, 1.0)
        tilted = base * tilt
        total = tilted.sum()
        if total <= 0:
            self._fallback_count += 1
            logger.warning(
                "[segment_ipf] degenerate raumtyp tilt (region='%s', status=%s) "
                "-> fallback to NDS-only segment pmf", region, economic_status,
            )
            return base
        self._primary_count += 1
        return tilted / total

    # -- fallback observability ---------------------------------------------
    def log_fallback_rate(
        self, queries: Iterable[tuple[str, Optional[int]]] | None = None,
    ) -> tuple[int, int]:
        """Log the primary-vs-fallback rate (project no-silent-fallback rule).

        If ``queries`` is given (an iterable of ``(status, raumtyp)``), each is
        evaluated (resetting the running counters first) so the logged rate
        reflects exactly that batch. Otherwise the running counters accumulated by
        prior :meth:`segment_probabilities` calls are logged. Returns
        ``(primary, fallback)``.
        """
        if queries is not None:
            self._primary_count = 0
            self._fallback_count = 0
            for status, raumtyp in queries:
                self.segment_probabilities(status, raumtyp)
        primary = self._primary_count
        fallback = self._fallback_count
        total = primary + fallback
        rate = (fallback / total) if total > 0 else 0.0
        log = logger.warning if rate > 0.05 else logger.info
        log(
            "[segment_ipf] raumtyp tilt: primary %d/%d (%.1f%%), fallback %d "
            "(%.1f%%)", primary, total, 100.0 * primary / total if total else 0.0,
            fallback, 100.0 * rate,
        )
        return primary, fallback


def _pooled_status_given_segment(df: pd.DataFrame,
                                 segments: Sequence[str]) -> np.ndarray:
    """Base-weighted national pooled ``P(status | segment)`` over all regions.

    Used as the denominator of the raumtyp within-NDS tilt, analogous to
    :func:`braunschweig.data.mid.status_by_hhtype._pooled_bayes`. For each
    segment the per-region column-% is averaged weighted by the region's
    ``base_weighted``, then renormalised over status.
    """
    statuses = list(ft.STATUS_LABELS)
    out = np.zeros((len(segments), len(statuses)), dtype=float)
    for i, seg in enumerate(segments):
        sub = df[df["segment"] == seg]
        weighted = np.zeros(len(statuses), dtype=float)
        for region in sub["region"].unique():
            reg_rows = sub[sub["region"] == region]
            base = float(reg_rows["base_weighted"].iloc[0])
            if not np.isfinite(base) or base <= 0:
                continue
            share = (
                reg_rows.set_index("status")["share_pct"]
                .reindex(statuses).fillna(0.0).to_numpy(dtype=float)
            )
            s = share.sum()
            if s <= 0:
                continue
            weighted += base * (share / s)
        total = weighted.sum()
        out[i] = (weighted / total) if total > 0 else np.ones(len(statuses)) / len(statuses)
    return out
