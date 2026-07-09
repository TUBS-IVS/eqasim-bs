"""Generic multinomial W_ZWD subtype model (issue #127).

Generalises braunschweig.popsim.shop_subtype's binary daily/non-daily shopping split
into an arbitrary number of named subtype groups for any MiD purpose (W_ZWECK), e.g.
splitting leisure trips into "local" vs "visit" via the Wegezweck-Detail (W_ZWD) code.
Estimation reuses the same W_GEW-weighted, (mode, travel-time-band)-conditioned approach
as shop_subtype; see that module and reference-mid-detail-purposes for the underlying
MiD coding. `tt_band` / `TT_BANDS` are imported from shop_subtype rather than copied so
both models share exactly one definition of the travel-time bands.
"""
from __future__ import annotations

import logging

from dataclasses import dataclass

import numpy as np
import pandas as pd

from braunschweig.popsim.shop_subtype import TT_BANDS, tt_band  # noqa: F401 (re-exported for callers)

logger = logging.getLogger(__name__)

# Columns required on the MiD Wege table for probability estimation. code_coverage_guard
# only needs W_ZWECK and the group column; estimation additionally needs mode/travel_time/W_GEW.
_GUARD_REQUIRED_COLUMNS = ("W_ZWECK",)
_ESTIMATION_REQUIRED_COLUMNS = ("W_ZWECK", "mode", "travel_time", "W_GEW")


@dataclass(frozen=True)
class SubtypeSpec:
    """Defines a multinomial W_ZWD subtype split for one MiD purpose (W_ZWECK).

    Parameters
    ----------
    purpose_label : str
        Human-readable purpose name used in log messages and error text (e.g. "leisure").
    zweck_values : frozenset[int]
        MiD W_ZWECK code(s) this spec applies to.
    groups : dict[str, frozenset[int]]
        Group name -> set of W_ZWD codes that label a leg as that group. Every code must
        appear in at most one group (validated in __post_init__). The union of all group
        codes plus `sentinels` should exhaust every W_ZWD code observed on legs with
        W_ZWECK in `zweck_values`; that is enforced separately by `code_coverage_guard`,
        which needs the actual data and therefore cannot run at spec-construction time.
    sentinels : frozenset[int]
        W_ZWD codes that carry no usable label (e.g. PAPI interview, child-reported,
        no-info codes) and are excluded from estimation. Legs with these codes are
        unlabelled, just like any other code that maps to neither a group nor a sentinel
        -- the difference is that `code_coverage_guard` treats unmapped codes as an error
        while sentinel codes are a deliberate, documented exclusion.
    group_col : str
        Column in the MiD Wege table holding the detail code (default "W_ZWD").
    """

    purpose_label: str
    zweck_values: frozenset
    groups: dict
    sentinels: frozenset
    group_col: str = "W_ZWD"

    def __post_init__(self) -> None:
        if not self.groups:
            raise ValueError(f"SubtypeSpec '{self.purpose_label}' must define at least one group.")

        all_codes: list = []
        for codes in self.groups.values():
            all_codes.extend(codes)
        if len(all_codes) != len(set(all_codes)):
            raise ValueError(
                f"SubtypeSpec '{self.purpose_label}' assigns one or more {self.group_col} codes to "
                f"more than one group; groups must partition the code space, not overlap."
            )

        overlap = set(all_codes) & set(self.sentinels)
        if overlap:
            raise ValueError(
                f"SubtypeSpec '{self.purpose_label}' has {self.group_col} code(s) {sorted(overlap)} in "
                f"both a group and the sentinel set; a code cannot be both labelled and excluded."
            )

    @property
    def group_codes(self) -> frozenset:
        """Union of all W_ZWD codes across all groups, i.e. every code with a valid label."""
        codes: set = set()
        for group_code_set in self.groups.values():
            codes |= set(group_code_set)
        return frozenset(codes)


def _require_columns(mid_wege: pd.DataFrame, purpose_label: str, required_columns) -> None:
    missing = [column for column in required_columns if column not in mid_wege.columns]
    if missing:
        raise ValueError(
            f"[purpose_subtype:{purpose_label}] MiD Wege table is missing required column(s) "
            f"{missing}."
        )


def estimate_group_probabilities(mid_wege: pd.DataFrame, spec: SubtypeSpec, *, min_obs: int = 30):
    """P(group | mode, tt_band) and the overall marginal P(group), W_GEW-weighted.

    Mirrors shop_subtype.estimate_daily_probability, generalised from a single daily/
    non-daily flag to an arbitrary set of named groups (spec.groups).

    Parameters
    ----------
    mid_wege : DataFrame
        MiD Wege table. Required columns: W_ZWECK, mode, travel_time, W_GEW, and
        spec.group_col (default "W_ZWD").
    spec : SubtypeSpec
        Defines the purpose, the groups, and the sentinel codes to exclude.
    min_obs : int
        Minimum row count for a (mode, tt_band) cell to receive its own estimate.
        Cells below this threshold are omitted from the result; callers fall back to
        `marginal` for those cells.

    Returns
    -------
    tuple[dict, dict]
        `cell_probs[(mode, band)] = {group_name: probability}` for cells with >= min_obs
        labelled legs; `marginal = {group_name: probability}` over all labelled legs
        (the fallback for thin or absent cells).
    """
    _require_columns(mid_wege, spec.purpose_label, (*_ESTIMATION_REQUIRED_COLUMNS, spec.group_col))

    group_col = spec.group_col
    purpose_legs = mid_wege[mid_wege["W_ZWECK"].isin(spec.zweck_values)]
    labelled = purpose_legs[purpose_legs[group_col].isin(spec.group_codes)].copy()

    total_purpose_legs = len(purpose_legs)
    labelled_share = (len(labelled) / total_purpose_legs) if total_purpose_legs else 0.0
    logger.info(
        "[purpose_subtype:%s] labelled %d/%d legs (%.1f%%) with a known %s group code",
        spec.purpose_label, len(labelled), total_purpose_legs, 100.0 * labelled_share, group_col,
    )

    if labelled.empty:
        raise ValueError(
            f"[purpose_subtype:{spec.purpose_label}] no legs with a known {group_col} group code "
            f"found among {total_purpose_legs} legs with W_ZWECK in {sorted(spec.zweck_values)}; "
            f"cannot estimate group probabilities from zero labelled observations."
        )

    code_to_group = {code: name for name, codes in spec.groups.items() for code in codes}
    labelled["_group"] = labelled[group_col].map(code_to_group)
    labelled["_band"] = labelled["travel_time"].map(tt_band)

    group_names = sorted(spec.groups)
    weights = labelled["W_GEW"].astype(float)
    weight_total = float(weights.sum())

    marginal = {
        name: float(weights[labelled["_group"] == name].sum() / weight_total) for name in group_names
    }

    cell_probs: dict = {}
    thin_cell_count = 0
    total_cell_count = 0
    for (mode, band), cell in labelled.groupby(["mode", "_band"]):
        total_cell_count += 1
        if len(cell) >= min_obs:
            cell_weights = cell["W_GEW"].astype(float)
            cell_weight_total = float(cell_weights.sum())
            cell_probs[(mode, int(band))] = {
                name: float(cell_weights[cell["_group"] == name].sum() / cell_weight_total)
                for name in group_names
            }
        else:
            thin_cell_count += 1

    logger.info(
        "[purpose_subtype:%s] %d/%d (mode, tt_band) cells are below min_obs=%d and fall back to "
        "the marginal",
        spec.purpose_label, thin_cell_count, total_cell_count, min_obs,
    )
    if total_cell_count and thin_cell_count == total_cell_count:
        logger.warning(
            "[purpose_subtype:%s] ALL (mode, tt_band) cells are below min_obs=%d; every leg will use "
            "the marginal fallback, i.e. no cell-level signal was estimated. Check min_obs and sample "
            "size before trusting this result.",
            spec.purpose_label, min_obs,
        )

    return cell_probs, marginal


def impute_groups(modes, tt_values, cell_probs: dict, marginal: dict, rng) -> np.ndarray:
    """Draw one group per leg from P(group | mode, tt_band), falling back to the marginal.

    Determinism: exactly one uniform draw is consumed per leg, in leg order, via
    `rng.random_sample(n)`. For each leg, the applicable probability vector -- the
    (mode, tt_band) cell's distribution if present in `cell_probs`, otherwise `marginal`
    -- is walked in SORTED group-name order while accumulating a running (cumulative)
    sum; the leg is assigned the first group whose cumulative probability exceeds the
    leg's own uniform draw (standard inverse-CDF sampling). Given the same `rng` state
    and the same `modes`/`tt_values`/`cell_probs`/`marginal` inputs, this always returns
    the same output, regardless of how legs happen to be grouped internally by
    (mode, band) during the vectorised computation.

    Parameters
    ----------
    modes : array-like of str
        Mode label per synthetic leg.
    tt_values : array-like of float
        Travel time in seconds per synthetic leg.
    cell_probs : dict
        Output of estimate_group_probabilities: {(mode, band): {group: probability}}.
    marginal : dict
        {group: probability} fallback used when the (mode, band) cell is absent from
        cell_probs.
    rng : numpy.random.RandomState
        Seeded random state for reproducibility.

    Returns
    -------
    np.ndarray of str (dtype object)
        Group name assigned to each leg, same length and order as `modes`.
    """
    modes = np.asarray(modes)
    tt_values = np.asarray(tt_values, dtype=float)
    leg_count = modes.shape[0]
    if tt_values.shape[0] != leg_count:
        raise ValueError(
            f"modes and tt_values must have the same length, got {leg_count} and "
            f"{tt_values.shape[0]}."
        )

    group_names = sorted(marginal)
    bands = np.array([tt_band(value) for value in tt_values], dtype=int)

    # Exactly one uniform draw per leg, in leg order -- see docstring on determinism.
    draws = rng.random_sample(leg_count)

    out = np.empty(leg_count, dtype=object)
    fallback_mask = np.zeros(leg_count, dtype=bool)

    for mode_value, band_value in sorted(set(zip(modes.tolist(), bands.tolist()))):
        cell_mask = (modes == mode_value) & (bands == band_value)
        probs = cell_probs.get((mode_value, band_value))
        if probs is None:
            probs = marginal
            fallback_mask |= cell_mask
        cumulative = np.cumsum([probs.get(name, 0.0) for name in group_names])
        choice = np.searchsorted(cumulative, draws[cell_mask], side="right")
        choice = np.clip(choice, 0, len(group_names) - 1)
        out[cell_mask] = np.asarray(group_names, dtype=object)[choice]

    fallback_count = int(fallback_mask.sum())
    if fallback_count:
        logger.info(
            "[purpose_subtype] marginal fallback used for %d/%d legs (%.1f%%) whose (mode, tt_band) "
            "cell had no direct estimate",
            fallback_count, leg_count, 100.0 * fallback_count / leg_count,
        )

    return out


def code_coverage_guard(mid_wege: pd.DataFrame, spec: SubtypeSpec) -> None:
    """Raise if any observed W_ZWD code is neither grouped nor a sentinel.

    This is the safeguard against a silent NaN bucket: every W_ZWD code observed on a
    leg with W_ZWECK in `spec.zweck_values` must be explicitly accounted for, either as
    a group code (`spec.groups`) or as a deliberately excluded sentinel
    (`spec.sentinels`). An unmapped code would otherwise be imputed as if it were simply
    "unlabelled", hiding the fact that the spec does not actually cover the real MiD
    coding.
    """
    _require_columns(mid_wege, spec.purpose_label, (*_GUARD_REQUIRED_COLUMNS, spec.group_col))

    purpose_legs = mid_wege[mid_wege["W_ZWECK"].isin(spec.zweck_values)]
    known_codes = spec.group_codes | frozenset(spec.sentinels)
    observed_codes = set(purpose_legs[spec.group_col].unique())
    unmapped_codes = sorted(observed_codes - known_codes)

    if unmapped_codes:
        raise ValueError(
            f"[purpose_subtype:{spec.purpose_label}] {spec.group_col} code(s) {unmapped_codes} appear "
            f"on legs with W_ZWECK in {sorted(spec.zweck_values)} but are mapped to neither a group "
            f"nor a sentinel; add them explicitly to avoid a silent NaN bucket."
        )
