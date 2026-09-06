"""Dwell time before a synthetic return-home trip (spec 2026-09-05, section 2.3, issue #367).

``fixed`` reproduces today's constant (``HOME_CLOSURE_DWELL_S``); ``from_trips`` draws the
last activity's duration from what MiD donors actually report for the same purpose and
arrival band when their day continues, so a closed-off work day lasts like an observed
work day instead of exactly one hour.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# RNG stream offset for callers that derive this model's rng from a shared seed
# (mirrors the pattern used by TIME_IMPUTATION_SEED_OFFSET / MATCHED_REPLACEMENT_SEED_OFFSET).
CLOSURE_SEED_OFFSET = 74517

# Arrival-time bands (hours since midnight) the empirical pools are stratified by,
# in addition to the following purpose: <14h, 14-17h, 18-19h, 20-21h, 22h+.
ARRIVAL_BANDS_H = (0, 14, 18, 20, 22, 48)

# Above this share of draws falling back to the GLOBAL marginal (i.e. neither
# the cell nor the purpose marginal had any usable observations), the model is
# no longer scientifically defensible: it means the purpose vocabulary of the
# donor trips used to BUILD the model and the purposes seen among the closure
# rows it is asked to DRAW for disagree (e.g. mismatched purpose taxonomies, or
# the model was built from the wrong donor subset). ``repair_trips`` logs a
# WARNING when the observed rate exceeds this threshold.
CLOSURE_GLOBAL_FALLBACK_WARN_RATE = 0.5

# Longest observed activity duration still pooled by from_trips(). Deliberately a
# module CONSTANT and not a config key: it is the bound of what a day-closing
# activity can plausibly last (a longer "duration" means the donor's day spans a
# night, i.e. the observation is not a within-day dwell at all), not a scientific
# parameter a study would vary. min_obs, by contrast, trades cell resolution
# against cell occupancy and IS configurable
# (braunschweig.population.popsim.closure_dwell_min_obs).
MAX_OBSERVED_DWELL_S = 16 * 3600


def _band(arrival_time_s: float) -> int:
    """Return the index of the arrival band (in ``ARRIVAL_BANDS_H``) containing ``arrival_time_s``.

    Raises:
        ValueError: If ``arrival_time_s`` is not finite (NaN/inf). Silently
            pooling a non-finite arrival into the last band would hide a data
            error (e.g. an unclosed NaN-time chain reaching the model).
    """
    hour = float(arrival_time_s) / 3600.0
    if not np.isfinite(hour):
        raise ValueError(
            f"closure_dwell._band: arrival_time_s must be finite, got {arrival_time_s!r}"
        )
    for i in range(len(ARRIVAL_BANDS_H) - 1):
        if ARRIVAL_BANDS_H[i] <= hour < ARRIVAL_BANDS_H[i + 1]:
            return i
    return len(ARRIVAL_BANDS_H) - 2


class ClosureDwellModel:
    """Draws the dwell time at the last activity before a synthetic return-home trip.

    Two construction modes:

    - :meth:`fixed` reproduces the legacy constant-dwell behaviour.
    - :meth:`from_trips` builds an empirical model from a donor trip table: for
      every activity FOLLOWED by another trip of the same person (i.e. NOT the
      day's last, open-ended activity), the observed duration
      (``next departure - this arrival``) is pooled by ``(following_purpose,
      arrival band)``.  A draw for an unclosed chain's last activity then reuses
      one of these observed durations instead of assuming a fixed value.

    Fallback cascade (never silent, see :attr:`report`):

    1. cell ``(purpose, arrival band)`` if it has at least ``min_obs`` observations
       (``min_obs`` applies to the CELL only);
    2. else the purpose marginal (pooled across all arrival bands for that
       purpose) — used as the fallback regardless of its OWN size (no minimum
       is enforced on it: it is already the aggregated fallback level, and
       requiring it to also clear ``min_obs`` would just add a second,
       undocumented threshold);
    3. else the global marginal (pooled across all purposes) when the purpose is
       unknown or has no observations at all.

    A draw counts exactly ONE fallback: if the purpose marginal itself is missing
    or empty, the draw falls straight through to the global marginal and only
    ``n_fallback_global`` increments (not both counters).

    The appended return-home departure is additionally capped at the caller's
    plan-time bound (``_append_return_home`` in ``plan_validation``, since only
    that caller knows the outbound travel time and the bound); each capped draw
    increments this model's ``report["n_capped"]`` so the rate is observable
    alongside the fallback rates.
    """

    def __init__(self, *, fixed_s=None, cells=None, purpose_marginal=None,
                 global_marginal=None, rng=None, min_obs: int = 30):
        if fixed_s is None and rng is None:
            # An empirical model (cells/purpose_marginal/global_marginal) draws
            # via self._rng.randint(...); constructing one without an rng would
            # crash on the first draw() call instead of at construction time,
            # and (per the project's seeded-randomness rule) this constructor
            # must never silently create one itself.
            raise ValueError(
                "ClosureDwellModel: an empirical model (fixed_s=None) requires an "
                "rng for its draws. Use ClosureDwellModel.from_trips(..., rng=...) "
                "to build one, or ClosureDwellModel.fixed(...) for the "
                "constant-dwell mode."
            )
        self._fixed = fixed_s
        self._cells = cells or {}
        self._purpose = purpose_marginal or {}
        self._global = global_marginal if global_marginal is not None else np.array([], dtype=float)
        self._rng = rng
        self._min_obs = min_obs
        self.report = {
            "kind": "fixed" if fixed_s is not None else "empirical",
            "n_cells": len(self._cells),
            "n_obs_by_purpose": {k: int(len(v)) for k, v in self._purpose.items()},
            "n_draws": 0,
            "n_fallback_purpose_marginal": 0,
            "n_fallback_global": 0,
            "n_capped": 0,
        }

    @classmethod
    def fixed(cls, dwell_s: float = 3600.0) -> "ClosureDwellModel":
        """Return a model that always draws the constant ``dwell_s`` (the legacy behaviour)."""
        return cls(fixed_s=float(dwell_s))

    @classmethod
    def from_trips(cls, trips: pd.DataFrame, *, rng, min_obs: int = 30,
                    max_dwell_s: float = MAX_OBSERVED_DWELL_S) -> "ClosureDwellModel":
        """Build an empirical model from a donor trip table (before closure repair).

        Args:
            trips: eqasim-schema frame with at least ``person_id``,
                ``departure_time``, ``arrival_time``, ``following_purpose`` — the
                DONOR diaries (i.e. before any synthetic closure has been appended).
            rng: ``numpy.random.RandomState`` (or compatible) used for every draw.
                Must be seeded by the caller for determinism (never construct an
                unseeded RNG here).
            min_obs: Minimum number of observations a ``(purpose, arrival band)``
                cell must have to be used directly; below this the draw falls back
                to the purpose marginal. Applies to the CELL only — the purpose
                marginal is used as-is at whatever size it has (no minimum is
                enforced on it), since it is already the aggregated fallback level.
            max_dwell_s: Observed durations above this bound are dropped as
                implausible (e.g. a donor's day genuinely spans midnight) before
                pooling. Defaults to the module constant
                :data:`MAX_OBSERVED_DWELL_S`; see there for why it is not a
                config key.

        Returns:
            A ``ClosureDwellModel`` in empirical mode.
        """
        for col in ("person_id", "departure_time", "arrival_time", "following_purpose"):
            if col not in trips.columns:
                raise KeyError(
                    f"ClosureDwellModel.from_trips: donor trips frame is missing "
                    f"required column '{col}' (has: {sorted(trips.columns)})"
                )

        t = trips.sort_values(["person_id", "departure_time"]).copy()
        # An activity's duration is only observable when the SAME person has a
        # following trip; the day's last (open-ended) activity has no next
        # departure and must be excluded (that is exactly what this model closes).
        t["next_departure"] = t.groupby("person_id")["departure_time"].shift(-1)
        t = t[t["next_departure"].notna()]

        duration = (t["next_departure"] - t["arrival_time"]).to_numpy(dtype=float)
        # Non-positive durations are data errors (arrival at/after the next
        # departure); durations beyond max_dwell_s are implausible outliers.
        # Both are dropped rather than silently pooled, since they would
        # otherwise bias the empirical distribution.
        valid = (duration > 0) & (duration <= max_dwell_s)
        t = t.loc[valid]
        duration = duration[valid]

        purposes = t["following_purpose"].astype(str).to_numpy()
        bands = np.array([_band(a) for a in t["arrival_time"].to_numpy()], dtype=int)

        cells: dict = {}
        purpose_marginal: dict = {}
        for purpose in np.unique(purposes):
            purpose_mask = purposes == purpose
            purpose_marginal[str(purpose)] = duration[purpose_mask]
            for band in np.unique(bands[purpose_mask]):
                cell_mask = purpose_mask & (bands == band)
                cells[(str(purpose), int(band))] = duration[cell_mask]

        model = cls(
            cells=cells, purpose_marginal=purpose_marginal, global_marginal=duration,
            rng=rng, min_obs=min_obs,
        )
        logger.info(
            "[closure_dwell] empirical model built: %d observations pooled into "
            "%d (purpose x arrival-band) cells across %d purposes",
            len(duration), len(cells), len(purpose_marginal),
        )
        return model

    def draw(self, purpose: str, arrival_time_s: float) -> float:
        """Draw a dwell duration (seconds) for the given following ``purpose`` and arrival time.

        Empirical mode: uniformly resamples one observed duration from the most
        specific available pool (see the fallback cascade documented on the
        class). Fixed mode: always returns the constant configured at
        construction, regardless of ``purpose``/``arrival_time_s``.
        """
        self.report["n_draws"] += 1
        if self._fixed is not None:
            return self._fixed

        pool = self._cells.get((str(purpose), _band(arrival_time_s)))
        if pool is None or len(pool) < self._min_obs:
            purpose_pool = self._purpose.get(str(purpose))
            if purpose_pool is not None and len(purpose_pool) > 0:
                pool = purpose_pool
                self.report["n_fallback_purpose_marginal"] += 1
            else:
                # Both the cell AND the purpose marginal are unavailable: fall
                # straight through to the global marginal. This counts as ONE
                # fallback (n_fallback_global), NOT also a purpose-marginal
                # fallback, so the two counters never double-count a single draw.
                pool = self._global
                self.report["n_fallback_global"] += 1

        if pool is None or len(pool) == 0:
            raise ValueError(
                "ClosureDwellModel.draw: no observations available at any level "
                "(cell, purpose marginal, or global marginal) — cannot draw a "
                "closure dwell time; the donor trip table used to build this "
                "model must contain at least one non-open-ended activity."
            )
        return float(pool[self._rng.randint(len(pool))])
