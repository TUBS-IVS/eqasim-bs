"""Weighted, distance-matched and Kreis-tilted donor draw of the worker in-commuters.

Pure mechanics, no file I/O: given a frame of MiD donors it draws, per agent, one donor row from the
donors with a commute of about the agent's length, weighted by the MiD person weight and tilted so
that the mean income per person of the origin Kreis follows an INKAR scale. The definitions (commute
window, tilt, counted fallbacks) are owned by
``docs/superpowers/specs/2026-11-11-incommuter-donor-household-design.md`` (section "Definitions")
and ADR-0191; this module implements them and restates none of the configured values.

Fallbacks are counted per agent and logged as primary / fallback rates by :meth:`KreisDonorSampler.draw`
(CLAUDE.md "Fallback transparency"); :meth:`KreisDonorSampler.report` returns the same counts.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd

LOGGER = logging.getLogger(__name__)

# ASSUMPTION: search bound of the tilt in weighted standard deviations of the income per person. A target
# further away than a tilt of this size is a target no sensible donor weighting can meet; the draw then
# uses the closest reachable tilt and counts the cell as "target not reached".
MAX_ABS_TILT = 30.0

# ASSUMPTION: agents whose commute distances differ by less than about 5 % share one window (and one
# tilted cell): bin = rint(ln(km) / step), window target = exp(bin * step). A coarser step saves cells,
# a finer one follows the distance more closely; 5 % is far below the spread of the donor commutes.
DISTANCE_BIN_STEP = 0.05

# ASSUMPTION: warn threshold for the share of agents whose origin Kreis has no INKAR scale (they are
# drawn without tilt). Above 5 % the scale table is probably keyed differently from the agents' Kreis.
KREIS_SCALE_FALLBACK_WARN_SHARE = 0.05

# ASSUMPTION: warn threshold for the Kish effective size of the smallest used draw cell. Below 100 donors
# a cell repeats the same few households many times.
EFFECTIVE_SIZE_WARN = 100.0

_REQUIRED_COLUMNS = ("donor_weight", "household_income_eur", "household_size")


@dataclass(frozen=True)
class TiltedWeights:
    """Result of :func:`tilt_to_mean`."""

    probabilities: np.ndarray   # sums to 1
    tilt: float                 # t, in weighted standard deviations of the values
    reached: bool               # the weighted mean equals the target
    effective_size: float       # 1 / sum(p^2)


def _validated_weights(weights, name: str = "weights") -> np.ndarray:
    array = np.asarray(weights, dtype=float)
    if array.ndim != 1 or len(array) == 0 or not np.isfinite(array).all() or (array <= 0).any():
        raise ValueError(f"{name} must be a non-empty 1-d array of finite values > 0")
    return array


def tilt_to_mean(weights, values, target_mean: float, *, max_abs_tilt: float = MAX_ABS_TILT) -> TiltedWeights:
    """Exponentially tilt ``weights`` so that the weighted mean of ``values`` equals ``target_mean``.

    The result is ``p_i proportional to w_i * exp(t * z_i)`` with ``z`` the weight-standardised ``values``:
    the distribution closest to the weights (minimum relative entropy) that meets the mean. ``t`` is found
    by bisection on ``[-max_abs_tilt, max_abs_tilt]`` (the tilted mean rises with ``t``). A target outside
    the range of ``values``, or values without spread, cannot be met: the closest reachable tilt (the
    probabilities at the search bound) is returned with ``reached=False``; a caller that must not draw from
    such a distribution (see :class:`KreisDonorSampler`) falls back to the untilted weights itself.

    Args:
        weights: positive donor weights.
        values: one value per donor (same unit as ``target_mean``).
        target_mean: the mean the tilted weights must produce.
        max_abs_tilt: search bound of the tilt, in weighted standard deviations of ``values``.

    Returns:
        The tilted probabilities, the tilt, whether the target was reached and the Kish effective size.
    """
    raw = _validated_weights(weights)
    values = np.asarray(values, dtype=float)
    if values.shape != raw.shape or not np.isfinite(values).all():
        raise ValueError("values must be finite and have one entry per weight")
    w = raw / raw.sum()
    mean = float((w * values).sum())
    sd = float(np.sqrt((w * (values - mean) ** 2).sum()))
    # No spread: exact zero is too strict, equal values with non-representable weights leave sd near 1e-13,
    # and z would then be rounding noise that collapses the draw onto one donor.
    if sd <= 1e-9 * max(1.0, abs(mean)):  # nothing to tilt
        return TiltedWeights(w, 0.0, bool(np.isclose(mean, target_mean, rtol=1e-6, atol=0.0)),
                             float(1.0 / (w ** 2).sum()))
    z = (values - mean) / sd
    log_w = np.log(w)

    def probabilities(t: float) -> np.ndarray:
        score = log_w + t * z
        score = score - score.max()  # max subtracted before exp: no overflow at large |t|
        p = np.exp(score)
        return p / p.sum()

    lo, hi = -float(max_abs_tilt), float(max_abs_tilt)
    for _ in range(200):
        t = (lo + hi) / 2.0
        if float((probabilities(t) * values).sum()) < target_mean:
            lo = t
        else:
            hi = t
    tilt = (lo + hi) / 2.0
    p = probabilities(tilt)
    reached = bool(np.isclose(float((p * values).sum()), target_mean, rtol=1e-6, atol=0.0))
    return TiltedWeights(p, float(tilt), reached, float(1.0 / (p ** 2).sum()))


def _window(distance_km, weights, target_km: float, min_effective_size: float) -> tuple[np.ndarray, bool]:
    """Window positions and whether the minimum effective size was reached (else: the whole pool)."""
    distance_km = np.asarray(distance_km, dtype=float)
    w = _validated_weights(weights)
    w = w / w.max()  # the Kish size is scale-free; this keeps huge weights from overflowing the squares
    gap = np.abs(np.log(distance_km) - np.log(float(target_km)))
    order = np.argsort(gap, kind="stable")
    cumulative = np.cumsum(w[order])
    kish = cumulative ** 2 / np.cumsum(w[order] ** 2)  # effective size after each added donor
    # relative tolerance: a size of 2.9999999999999996 (rounding) counts as 3
    enough = np.flatnonzero(kish >= min_effective_size * (1.0 - 1e-12))
    if len(enough) == 0:
        return np.arange(len(distance_km)), False
    reach = gap[order[enough[0]]]
    # donors as near as the last one join, so ties do not depend on the row order
    return np.flatnonzero(gap <= reach * (1.0 + 1e-9) + 1e-12), True


def nearest_distance_window(distance_km, weights, target_km: float, min_effective_size: float) -> np.ndarray:
    """Donors with the commute nearest to ``target_km`` (in log distance) up to a Kish effective size.

    Donors are added in order of ``|ln(distance) - ln(target)|`` until the Kish effective size
    ``sum(w)^2 / sum(w^2)`` of the window reaches ``min_effective_size``; donors as near as the last one
    join. If the minimum is out of reach, the window is the whole pool.

    Args:
        distance_km: donor commute distances (km), > 0.
        weights: positive donor weights.
        target_km: the agent's (binned) commute distance (km), > 0.
        min_effective_size: minimum Kish effective size of the window.

    Returns:
        Sorted row positions of the window.
    """
    return _window(distance_km, weights, target_km, min_effective_size)[0]


def empty_report() -> dict:
    """The report of a draw without agents: same keys as :meth:`KreisDonorSampler.report`, counts 0."""
    return {"n_agents": 0, "tilted": False, "distance_matched": False, "n_without_kreis_scale": 0,
            "n_target_not_reached": 0, "n_whole_pool_window": 0, "min_effective_size": None,
            "max_window_ratio": None}


@dataclass(frozen=True)
class _Cell:
    positions: np.ndarray          # row positions of the window in the donor frame
    probabilities: np.ndarray      # one per position, sums to 1
    window_reached: bool           # False: the minimum effective size was out of reach (whole pool)
    has_scale: bool                # the Kreis has a (finite) INKAR scale
    tilt: TiltedWeights | None     # None: untilted
    window_ratio: float | None     # longest / shortest donor commute of the window


class KreisDonorSampler:
    """Draw donor rows per agent from a commute window, tilted to the income scale of the origin Kreis.

    Args:
        donors: one row per donor with ``donor_weight`` (> 0), ``household_income_eur`` (EUR per month, the
            donor household), ``household_size`` (>= 1) and, with ``min_effective_pool``,
            ``commute_distance_km`` (> 0).
        scale_by_kreis: origin Kreis -> factor on the weighted mean income per person of the window;
            ``None`` draws without tilt. A Kreis missing from the table (or with a non-finite factor) is
            drawn without tilt and counted. The keys of the table and the agent Kreis values are compared
            as strings (5-digit ARS): both are converted with ``str()``, so an int-keyed table matches too.
            A cell whose target mean is outside the income range of its window (not reached) is drawn
            from the untilted window weights, counted in ``n_target_not_reached`` and warned.
        min_effective_pool: minimum Kish effective size of the commute window; ``None`` draws from every
            donor without distance matching.
    """

    def __init__(self, donors: pd.DataFrame, scale_by_kreis: Mapping[str, float] | None, *,
                 min_effective_pool: float | None):
        missing = [c for c in _REQUIRED_COLUMNS if c not in donors.columns]
        if min_effective_pool is not None and "commute_distance_km" not in donors.columns:
            missing.append("commute_distance_km")
        if missing:
            raise ValueError(f"donor frame lacks required column(s): {', '.join(missing)}")
        if len(donors) == 0:
            raise ValueError("donor frame is empty")
        self._weight = _validated_weights(donors["donor_weight"].to_numpy(dtype=float), "donor_weight")
        income = donors["household_income_eur"].to_numpy(dtype=float)
        size = donors["household_size"].to_numpy(dtype=float)
        if not np.isfinite(income).all():
            raise ValueError("household_income_eur must be finite for every donor")
        if not np.isfinite(size).all() or (size < 1).any():
            raise ValueError("household_size must be finite and >= 1 for every donor")
        self._income_per_person = income / size
        self._commute_km = None
        if min_effective_pool is not None:
            if min_effective_pool <= 0:
                raise ValueError(f"min_effective_pool must be > 0, got {min_effective_pool}")
            commute = donors["commute_distance_km"].to_numpy(dtype=float)
            if not np.isfinite(commute).all() or (commute <= 0).any():
                raise ValueError("commute_distance_km must be finite and > 0 for every donor")
            self._commute_km = commute
        self._min_effective_pool = min_effective_pool
        if scale_by_kreis is not None:
            for kreis, scale in scale_by_kreis.items():
                if np.isfinite(scale) and scale <= 0:
                    raise ValueError(f"scale of Kreis {kreis!r} must be > 0, got {scale}")
        self._scale = None if scale_by_kreis is None else {str(k): v for k, v in scale_by_kreis.items()}
        self._window_cache: dict[int, tuple[np.ndarray, bool]] = {}
        self._cell_cache: dict[tuple[int, str], _Cell] = {}

    # -- cells ----------------------------------------------------------------------------------

    def _bins(self, commute_km) -> np.ndarray:
        """Distance bin per agent; all 0 without distance matching. Raises on an unusable commute."""
        commute_km = np.asarray(commute_km, dtype=float)
        if self._commute_km is None:
            return np.zeros(len(commute_km), dtype=int)
        if not np.isfinite(commute_km).all() or (commute_km <= 0).any():
            raise ValueError("agent commute distance must be finite and > 0 km for every agent")
        return np.rint(np.log(commute_km) / DISTANCE_BIN_STEP).astype(int)

    def _window_of(self, bin_id: int) -> tuple[np.ndarray, bool]:
        if bin_id not in self._window_cache:
            if self._commute_km is None:
                self._window_cache[bin_id] = (np.arange(len(self._weight)), True)
            else:
                target_km = float(np.exp(bin_id * DISTANCE_BIN_STEP))
                self._window_cache[bin_id] = _window(
                    self._commute_km, self._weight, target_km, self._min_effective_pool)
        return self._window_cache[bin_id]

    def _cell(self, bin_id: int, kreis: str) -> _Cell:
        kreis = str(kreis)
        key = (bin_id, kreis)
        if key not in self._cell_cache:
            positions, reached = self._window_of(bin_id)
            w = self._weight[positions]
            y = self._income_per_person[positions]
            scale = None if self._scale is None else self._scale.get(kreis)
            has_scale = scale is not None and bool(np.isfinite(scale))
            tilt = None
            if has_scale:
                base = float((w * y).sum() / w.sum())
                tilt = tilt_to_mean(w, y, base * float(scale))
                # A target out of reach is counted and warned, but drawn from the untilted window weights:
                # the bound tilt would collapse the draw onto the extreme donors.
                probabilities = tilt.probabilities if tilt.reached else w / w.sum()
            else:
                probabilities = w / w.sum()
            ratio = None
            if self._commute_km is not None:
                window_km = self._commute_km[positions]
                ratio = float(window_km.max() / window_km.min())
            self._cell_cache[key] = _Cell(positions, probabilities, reached, has_scale, tilt, ratio)
        return self._cell_cache[key]

    def cell_probabilities(self, kreis: str, commute_km: float) -> pd.Series:
        """Draw probabilities of the donors of one cell, indexed by row position in the donor frame."""
        cell = self._cell(int(self._bins([commute_km])[0]), kreis)
        return pd.Series(cell.probabilities, index=cell.positions)

    def _cells_of(self, kreis, commute_km) -> dict[tuple[int, str], np.ndarray]:
        """Agent positions per ``(bin, Kreis)`` cell, in sorted cell order."""
        kreis = pd.Series(np.asarray(kreis, dtype=object))
        if len(kreis) != len(np.asarray(commute_km)):
            raise ValueError("kreis and commute_km must have one entry per agent")
        if kreis.isna().any():
            raise ValueError("every agent needs an origin Kreis")
        keys = pd.DataFrame({"bin": self._bins(commute_km), "kreis": kreis.astype(str).to_numpy()})
        return {cell: np.asarray(index) for cell, index in sorted(keys.groupby(["bin", "kreis"]).indices.items())}

    # -- draw and report ----------------------------------------------------------------------------

    def draw(self, kreis, commute_km, rng: np.random.Generator) -> np.ndarray:
        """Draw one donor row position per agent (row positions in the donor frame, agent order).

        Args:
            kreis: origin Kreis per agent.
            commute_km: agent commute distance per agent (km), > 0 with distance matching.
            rng: seeded numpy Generator; consumed cell by cell in sorted cell order.
        """
        cells = self._cells_of(kreis, commute_km)
        out = np.empty(len(np.asarray(commute_km)), dtype=int)
        for (bin_id, name), agents in cells.items():
            cell = self._cell(bin_id, name)
            out[agents] = rng.choice(cell.positions, size=len(agents), p=cell.probabilities)
        self._log(self._report_of(cells))
        return out

    def report(self, kreis, commute_km) -> dict:
        """Counts of the draw for these agents (all ``n_`` counters count agents, not cells)."""
        return self._report_of(self._cells_of(kreis, commute_km))

    def _report_of(self, cells: dict[tuple[int, str], np.ndarray]) -> dict:
        report = empty_report()
        report["tilted"] = self._scale is not None
        report["distance_matched"] = self._commute_km is not None
        sizes, ratios = [], []
        for (bin_id, name), agents in cells.items():
            cell = self._cell(bin_id, name)
            n = len(agents)
            report["n_agents"] += n
            if self._scale is not None and not cell.has_scale:
                report["n_without_kreis_scale"] += n
            if cell.tilt is not None and not cell.tilt.reached:
                report["n_target_not_reached"] += n
            if self._commute_km is not None and not cell.window_reached:
                report["n_whole_pool_window"] += n
            sizes.append(float(1.0 / (cell.probabilities ** 2).sum()))
            if cell.window_ratio is not None:
                ratios.append(cell.window_ratio)
        if sizes:
            report["min_effective_size"] = min(sizes)
        if ratios:
            report["max_window_ratio"] = max(ratios)
        return report

    @staticmethod
    def _log(report: dict) -> None:
        n = report["n_agents"]
        if n == 0:
            LOGGER.info("[braunschweig.cordon.donor_draw] no agents drawn")
            return
        warn = False
        parts = [f"{n} agents"]
        if report["tilted"]:
            missing = report["n_without_kreis_scale"]
            lost = report["n_target_not_reached"]
            parts.append(f"tilted primary {n - missing - lost}/{n} ({100.0 * (n - missing - lost) / n:.1f}%), "
                         f"no Kreis scale {missing} ({100.0 * missing / n:.1f}%), "
                         f"target not reached {lost} ({100.0 * lost / n:.1f}%)")
            warn = warn or lost > 0 or missing / n > KREIS_SCALE_FALLBACK_WARN_SHARE
        if report["distance_matched"]:
            whole = report["n_whole_pool_window"]
            parts.append(f"commute window primary {n - whole}/{n} ({100.0 * (n - whole) / n:.1f}%), "
                         f"whole pool {whole} ({100.0 * whole / n:.1f}%), "
                         f"widest window ratio {report['max_window_ratio']:.2f}")
            warn = warn or whole > 0
        parts.append(f"smallest effective size {report['min_effective_size']:.1f}")
        warn = warn or report["min_effective_size"] < EFFECTIVE_SIZE_WARN
        (LOGGER.warning if warn else LOGGER.info)("[braunschweig.cordon.donor_draw] " + "; ".join(parts))
