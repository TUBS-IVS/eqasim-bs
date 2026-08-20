"""Wohnmobile holder-age tilt on the vehicle segment pmf (issue #315, ADR-0093).

The segment draw ``P(segment | economic_status, raumtyp)`` carries no holder-age
dimension, so a motorhome can land with a 25-year-old household -- contradicting
the KBA's own holder-age figures (Stichtag 2025-04-01). This module tilts the
``wohnmobile`` component of the per-car segment pmf by the assigned owner's age
class ``a``:

    p_wm'(a) = base_wm * c * r(a),        r(a) = P_ref(a | wohnmobile) / P_pop(a)

with ``P_pop`` the holder-age distribution of the CAR frame being sampled and
``c = E_cars[base_wm] / E_cars[base_wm * r(a)]`` a single global calibration
scalar. Plain Bayes preserves the wohnmobile marginal only when ``base_wm`` does
not vary with age; because it varies with (status, raumtyp) and age correlates
with both, the residual is exactly ``Cov_cars(base_wm, r)`` -- ``c`` removes it,
making the expected national wohnmobile share exact by construction, while the
renormalised holder-age COMPOSITION is mathematically invariant to ``c`` (a
scalar cancels), so no age signal is spent (ADR-0093).

No clipping band is applied to ``r``: the reference is a full national register
count, not a sparse survey cross-tab, so the extreme low ratio of the youngest
class is real signal (contrast :class:`EvIncomeTiltModel`'s ``[0.2, 5.0]``).
Numerical guards only; every guard hit is counted because it breaks the
exactness claim for that car (logged next to ``c``).

Fallback observability (project no-silent-fallback rule): missing / non-finite /
below-adult owner ages fall back to the untilted pmf and are counted; a batch
whose ``owner_age`` column never arrived is a loud 100% fallback warning.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from braunschweig.data.kba import fleet_tables as ft

logger = logging.getLogger(__name__)

#: The KBA segment label this tilt acts on.
WOHNMOBILE_SEGMENT = "wohnmobile"

#: Minimum owner age (years) for the tilt. KBA holders are licence-age adults;
#: matches ADULT_AGE in braunschweig.synthesis.vehicles.cars.household. A rare
#: sub-adult fallback owner falls back to the untilted pmf (counted).
MINIMUM_OWNER_AGE_YEARS = 18

#: Warn when the calibration scalar leaves this band (spec 3.2): a c far from 1
#: means the holder-age and status/raumtyp axes are more strongly coupled in the
#: population than a 1.99% segment should carry unexamined.
CALIBRATION_WARN_BAND = (0.8, 1.25)


@dataclass
class WohnmobileHolderAgeTilt:
    """Per-frame fitted holder-age tilt for the wohnmobile segment mass.

    Build via :meth:`from_data_path` (loads the committed reference), then call
    :meth:`fit_population` once per sampled car frame (computes ``P_pop``,
    ``r(a)`` and the calibration scalar ``c``), then :meth:`tilt` per car.
    Calling :meth:`tilt` before :meth:`fit_population` raises -- an uncalibrated
    tilt would silently reintroduce the covariance drift ``c`` exists to remove.
    One instance serves one frame at a time (counters reset on fit), matching
    the other per-batch fleet models.
    """

    #: age_class -> P_ref(a | wohnmobile), renormalised over the 8 classes.
    ref_share: dict[str, float]
    #: Ordered (label, min_years, max_years) bounds as published; open bounds
    #: are -inf / +inf.
    class_bounds: list[tuple[str, float, float]]
    # -- fitted per-frame state (None until fit_population) -------------------
    _ratio: Optional[dict[str, float]] = None
    _calibration: Optional[float] = None
    _p_pop: Optional[dict[str, float]] = None
    _expected_wm_share: Optional[float] = None
    # -- fallback counters (no-silent-fallback rule) --------------------------
    _primary: int = field(default=0)
    _fallback: int = field(default=0)
    _guard: int = field(default=0)

    # -- construction ---------------------------------------------------------
    @classmethod
    def from_data_path(cls, data_path: str) -> "WohnmobileHolderAgeTilt":
        return cls._from_dataframe(ft.load_wohnmobile_holder_age(data_path))

    @classmethod
    def _from_dataframe(cls, df: pd.DataFrame) -> "WohnmobileHolderAgeTilt":
        """Build from a loader-shaped DataFrame (unit-test hook, no filesystem)."""
        att = df[df["age_class"] != ft.WOHNMOBILE_AGE_NOT_ATTRIBUTED]
        ref = {str(row["age_class"]): float(row["share_of_attributed"])
               for _, row in att.iterrows()}
        bounds = []
        for _, row in att.iterrows():
            lo = (float(row["age_min_years"])
                  if pd.notna(row["age_min_years"]) else float("-inf"))
            hi = (float(row["age_max_years"])
                  if pd.notna(row["age_max_years"]) else float("inf"))
            bounds.append((str(row["age_class"]), lo, hi))
        return cls(ref_share=ref, class_bounds=bounds)

    # -- age classification ---------------------------------------------------
    def age_class_for(self, owner_age) -> Optional[str]:
        """Map an owner age (years) to a reference class; None -> fallback.

        None / NaN / non-numeric / below :data:`MINIMUM_OWNER_AGE_YEARS` return
        ``None`` (the caller counts the fallback).
        """
        if owner_age is None:
            return None
        try:
            age = float(owner_age)
        except (TypeError, ValueError):
            return None
        if not np.isfinite(age) or age < MINIMUM_OWNER_AGE_YEARS:
            return None
        for label, lo, hi in self.class_bounds:
            if lo <= age <= hi:
                return label
        return None

    # -- per-frame fit ---------------------------------------------------------
    def fit_population(self, df_cars: pd.DataFrame, segment_model) -> None:
        """Fit ``P_pop``, ``r(a)`` and the calibration scalar ``c`` on a frame.

        ``df_cars`` must carry ``owner_age``, ``economic_status`` and ``raumtyp``
        (the caller guards the owner_age column). ``segment_model`` provides
        ``segments`` and ``segment_probabilities(status, raumtyp)``; it is
        queried once per distinct (status, raumtyp) cell, NOT per car (cheap;
        note this adds a handful of primary hits to the segment model's own
        raumtyp-tilt counters). Resets the fallback counters.
        """
        if "owner_age" not in df_cars.columns:
            raise ValueError(
                "fit_population: df_cars lacks the 'owner_age' column -- the "
                "caller (sample_fleet) must guard this case before fitting."
            )
        self._primary = 0
        self._fallback = 0
        self._guard = 0
        classes = df_cars["owner_age"].map(self.age_class_for)
        valid = classes.notna()
        n_total = len(df_cars)
        n_valid = int(valid.sum())
        if n_valid == 0:
            self._ratio = {}
            self._calibration = 1.0
            self._p_pop = {}
            self._expected_wm_share = None
            logger.warning(
                "[wohnmobile_age] fit: 0/%d cars carry a usable owner_age -> "
                "tilt neutral for this frame (every tilt() call will fall back).",
                n_total,
            )
            return
        counts = classes[valid].value_counts()
        p_pop = {str(k): float(v) / n_valid for k, v in counts.items()}
        ratio: dict[str, float] = {}
        for label, _lo, _hi in self.class_bounds:
            p = p_pop.get(label, 0.0)
            ratio[label] = (self.ref_share[label] / p) if p > 0 else float("nan")

        # Calibration scalar c = E[base_wm] / E[base_wm * r] over the VALID cars
        # (invalid-age cars keep the untilted pmf, so they are exact already),
        # via a groupby over the (status, raumtyp, age class) cells (spec 3.2).
        wm_index = segment_model.segments.index(WOHNMOBILE_SEGMENT)
        work = pd.DataFrame({
            "economic_status": df_cars["economic_status"].astype(str),
            # Normalise raumtyp exactly like the PASS-1 loop: int or None.
            "raumtyp": df_cars["raumtyp"].map(
                lambda v: int(v) if pd.notna(v) else None),
            "age_class": classes,
        })
        base_cache: dict[tuple, float] = {}

        def _base_wm(status: str, raumtyp) -> float:
            key = (status, raumtyp)
            if key not in base_cache:
                pmf = segment_model.segment_probabilities(status, raumtyp)
                base_cache[key] = float(pmf[wm_index])
            return base_cache[key]

        num_valid = 0.0   # E-sum of base_wm over valid cars
        den_valid = 0.0   # E-sum of base_wm * r over valid cars
        inv_mass = 0.0    # untilted base_wm mass of fallback cars
        cell_sizes = work.groupby(
            ["economic_status", "raumtyp", "age_class"], dropna=False).size()
        for (status, raumtyp, age_class), n_cell in cell_sizes.items():
            # groupby(dropna=False) can hand the None raumtyp back as NaN;
            # re-normalise so the segment model sees None (NDS base, PRIMARY)
            # and never a NaN that would miscount as an unknown-code fallback.
            raumtyp = int(raumtyp) if pd.notna(raumtyp) else None
            base_wm = _base_wm(status, raumtyp)
            r = ratio.get(age_class, float("nan")) if pd.notna(age_class) else float("nan")
            if np.isfinite(r):
                num_valid += n_cell * base_wm
                den_valid += n_cell * base_wm * r
            else:
                inv_mass += n_cell * base_wm
        if den_valid <= 0.0 or not np.isfinite(den_valid) or num_valid <= 0.0:
            logger.warning(
                "[wohnmobile_age] fit: degenerate calibration (num=%.4g, "
                "den=%.4g) -> c=1.0 (plain Bayes; the preserved-marginal claim "
                "is NOT exact for this frame).", num_valid, den_valid,
            )
            calibration = 1.0
        else:
            calibration = num_valid / den_valid
        self._ratio = ratio
        self._calibration = float(calibration)
        self._p_pop = p_pop
        # Untilted expected wohnmobile share over ALL cars: the aggregate the
        # calibrated tilt preserves (validated by validate_wohnmobile_holder_age).
        self._expected_wm_share = (num_valid + inv_mass) / n_total
        lo, hi = CALIBRATION_WARN_BAND
        in_band = lo <= calibration <= hi
        log = logger.info if in_band else logger.warning
        log(
            "[wohnmobile_age] fit: %d/%d cars with usable owner_age (%.1f%%), "
            "c=%.4f%s, untilted E[wm]=%.4f%%; P_pop=%s; raw r=%s",
            n_valid, n_total, 100.0 * n_valid / n_total, calibration,
            "" if in_band else f" (OUTSIDE the expected band {CALIBRATION_WARN_BAND})",
            100.0 * self._expected_wm_share,
            {k: round(v, 4) for k, v in sorted(p_pop.items())},
            {k: (round(v, 3) if np.isfinite(v) else None)
             for k, v in ratio.items()},
        )

    # -- fitted accessors ------------------------------------------------------
    @property
    def calibration(self) -> Optional[float]:
        """The fitted global calibration scalar ``c`` (None before fit)."""
        return self._calibration

    @property
    def expected_wm_share(self) -> Optional[float]:
        """Untilted expected wohnmobile share of the fitted frame (None before fit)."""
        return self._expected_wm_share

    # -- per-car application ---------------------------------------------------
    def tilt(self, seg_pmf: np.ndarray, owner_age, wm_index: int) -> np.ndarray:
        """Return the holder-age-tilted segment pmf for one car.

        ``p_wm' = base_wm * c * r(a)``; the non-wohnmobile mass is rescaled by
        ``(1 - p_wm') / (1 - base_wm)`` so the other segments keep their relative
        proportions. Fallback (untilted pmf, counted): unclassifiable owner age,
        non-finite ``r``/``c``. Guard (untilted pmf, counted SEPARATELY because
        it breaks the exactness claim for this car): ``base_wm <= 0``,
        ``base_wm >= 1``, ``p_wm' >= 1`` or non-finite.
        """
        if self._ratio is None:
            raise RuntimeError(
                "WohnmobileHolderAgeTilt.tilt() called before fit_population() "
                "-- an uncalibrated tilt silently drops the preserved-marginal "
                "property (ADR-0093)."
            )
        age_class = self.age_class_for(owner_age)
        if age_class is None:
            self._fallback += 1
            return seg_pmf
        r = self._ratio.get(age_class, float("nan"))
        c = self._calibration
        if not np.isfinite(r) or c is None or not np.isfinite(c):
            self._fallback += 1
            return seg_pmf
        base_wm = float(seg_pmf[wm_index])
        p_new = base_wm * c * r
        if base_wm <= 0.0 or base_wm >= 1.0 or p_new >= 1.0 or not np.isfinite(p_new):
            self._guard += 1
            return seg_pmf
        out = np.asarray(seg_pmf, dtype=float) * ((1.0 - p_new) / (1.0 - base_wm))
        out[wm_index] = p_new
        self._primary += 1
        return out

    # -- fallback observability -------------------------------------------------
    def mark_batch_fallback(self, n: int) -> None:
        """Count a whole batch as fallback (the owner_age column never arrived)."""
        self._fallback += int(n)

    def log_fallback_rate(self, population_label: str = "") -> tuple[int, int]:
        """Log primary vs fallback (+ guard) for the batch; returns the counts."""
        tag = (f"[wohnmobile_age][{population_label}]" if population_label
               else "[wohnmobile_age]")
        primary = self._primary
        non_primary = self._fallback + self._guard
        total = primary + non_primary
        rate = (non_primary / total) if total else 0.0
        log = logger.warning if rate > 0.05 else logger.info
        log(
            "%s holder-age tilt: primary %d/%d (%.1f%%), fallback %d (%.1f%%), "
            "guard hits %d (exactness caveat when > 0), c=%s",
            tag, primary, total, 100.0 * primary / total if total else 0.0,
            non_primary, 100.0 * rate, self._guard,
            f"{self._calibration:.4f}" if self._calibration is not None else "unfitted",
        )
        return primary, non_primary
