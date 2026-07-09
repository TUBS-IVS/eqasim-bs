"""Shared secondary-trip distance measurement helpers.

Single source of truth for the per-leg mode -> circuity-network dispatch and the
W12 9-band share computation used by both the validation script
(``scripts/validate_secondary_distances.py``) and the scorer calibration script
(``scripts/calibrate_secondary_scorer.py``). Keeping these helpers here prevents
the two scripts from diverging silently.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.calibration.targets import W12_BAND_EDGES_KM

# ---------------------------------------------------------------------------
# Per-leg mode -> circuity network dispatch
# ---------------------------------------------------------------------------

# Map a leg mode to the circuity network used for euclidean -> routed scaling.
# Unknown modes default to 'car' (the most common motorised network).
MODE_TO_NETWORK: dict[str, str] = {
    "car":           "car",
    "car_passenger": "car",
    "pt":            "pt",
    "walk":          "walk",
    "bike":          "walk",
}


def mode_to_network(mode: str) -> str:
    """Return the circuity network ('car'|'pt'|'walk') for a leg mode.

    Unknown modes default to 'car' (the most common motorised network).
    """
    return MODE_TO_NETWORK.get(str(mode), "car")


# ---------------------------------------------------------------------------
# W12 band-share helper
# ---------------------------------------------------------------------------

def w12_band_shares(distances_km) -> np.ndarray:
    """Normalised share per W12 band for an array of distances in km.

    W12_BAND_EDGES_KM = (0, 0.5, 1, 2, 5, 10, 20, 50, 100, inf) -> 9 bands.
    Returns a length-9 float array summing to 1.0 (or all-zero on empty input).
    """
    edges = np.asarray(W12_BAND_EDGES_KM[1:-1], dtype=float)  # inner edges only
    bands = np.digitize(np.asarray(distances_km, dtype=float), edges)
    n_bands = len(W12_BAND_EDGES_KM) - 1  # 9
    counts = np.bincount(bands, minlength=n_bands).astype(float)
    total = counts.sum()
    return counts / total if total > 0 else counts


# ---------------------------------------------------------------------------
# Subtype validation helpers (issue #127, Task 6)
#
# Generic, pure, synthetic-frame-testable helpers for the leisure/other W_ZWD
# subtype split validation: per-group realised distance summaries, the
# "leisure_visit" residential-placement share check, and the boundary-clip
# share computation also reused (via a per-run print) by
# braunschweig.synthesis.locations.secondary_chainsolvers for the
# "leisure_excursion" transparency log. None of these read a cache or a
# stage -- callers are responsible for assembling the input frame/arrays.
# ---------------------------------------------------------------------------

# In-sample sanity reference means for the leisure/other W_ZWD subtype split.
# NOT a validated external target (CLAUDE.md "No invented reference values"):
# these are the measured, W_GEW-weighted MiD 2023 donor means cited in the
# design spec's Taxonomy tables --
# docs/superpowers/specs/2026-07-09-wzwd-leisure-errand-split-design.md
# (issue #127). Comparing a model's REALISED (in-sample) mean against the SAME
# donor data used to build the model's subtype split is a sanity check only;
# it must never be reported as a pass/fail validation gate.
SUBTYPE_DONOR_MEAN_KM_RANGE: dict = {
    "leisure_local": (4.0, 7.0),
    "leisure_visit": (19.1, 19.1),
    "leisure_activity": (10.0, 18.0),
    "leisure_excursion": (45.0, 100.0),
    "other_errand_short": (5.0, 9.0),
    "other_errand_long": (11.0, 16.0),
    "other_escort": (4.5, 8.5),
}


def per_group_distance_summary(df: pd.DataFrame, group_column: str, distance_column: str,
                               weight_column: str | None = None) -> pd.DataFrame:
    """Per-group leg count and mean realised distance.

    ``df`` carries one row per leg with a subtype/group label
    (``group_column``, e.g. the internal chainsolver activity name such as
    ``"leisure_visit"``) and a realised distance (``distance_column``, any
    consistent unit -- the validation script convention is km). When
    ``weight_column`` is given, the mean is the weighted mean (e.g. a survey
    weight such as MiD ``W_GEW``); otherwise the plain arithmetic mean over
    legs is used. Callers must check which applies to the frame at hand: the
    realised legs recovered from a synpp/chainsolvers cache carry no survey
    weight, so the unweighted mean is what applies there.

    Parameters
    ----------
    df : pd.DataFrame
        Legs frame with at least ``group_column`` and ``distance_column``.
    group_column : str
        Column carrying the subtype/group label.
    distance_column : str
        Column carrying the realised distance.
    weight_column : str, optional
        Column carrying a per-leg weight. A group with a non-positive total
        weight raises (a weighted mean is undefined there; no silent NaN).

    Returns
    -------
    pd.DataFrame
        Columns: ``group``, ``n``, ``mean_distance``. One row per distinct
        value of ``group_column``, in first-seen order. Empty (same columns,
        zero rows) when ``df`` is empty.
    """
    if df.empty:
        return pd.DataFrame(columns=["group", "n", "mean_distance"])

    rows = []
    for group, sub in df.groupby(group_column, sort=False):
        n = len(sub)
        if weight_column is not None:
            weights = sub[weight_column].to_numpy(dtype=float)
            total_weight = float(weights.sum())
            if total_weight <= 0.0:
                raise ValueError(
                    f"[per_group_distance_summary] group {group!r} has a "
                    f"non-positive total weight ({total_weight}); a weighted "
                    "mean is undefined for this group."
                )
            mean_distance = float(
                np.average(sub[distance_column].to_numpy(dtype=float), weights=weights)
            )
        else:
            mean_distance = float(sub[distance_column].mean())
        rows.append({"group": group, "n": n, "mean_distance": mean_distance})
    return pd.DataFrame(rows)


def placement_share_at_positive_potential(df: pd.DataFrame, potential_column: str,
                                          group_mask=None):
    """Share of legs placed at a candidate with a positive potential value.

    Given a legs frame carrying the PLACED candidate's potential value for
    some activity (e.g. ``pot_visit`` for ``"leisure_visit"`` legs), returns
    the fraction of rows where that value is > 0. Pass ``group_mask`` (a
    boolean Series/array aligned with ``df``) to restrict the check to a
    subset of rows (e.g. only ``"leisure_visit"`` legs within a mixed-purpose
    legs frame).

    Returns
    -------
    tuple[float, int, int]
        ``(share, n_positive, n_total)``. ``share`` is NaN when there are no
        matching rows (no invented value for an empty/zero-leg run).
    """
    subset = df.loc[group_mask] if group_mask is not None else df
    n_total = len(subset)
    if n_total == 0:
        return float("nan"), 0, 0
    positive_mask = subset[potential_column].to_numpy(dtype=float) > 0.0
    n_positive = int(positive_mask.sum())
    return n_positive / n_total, n_positive, n_total


def boundary_clip_share(desired_distances_m, ceiling_m):
    """Share of legs whose desired distance exceeds a candidate-radius ceiling.

    Used for the ``"leisure_excursion"`` boundary-clip transparency check
    (issue #127, Task 6): a leg whose sampled desired distance exceeds the
    farthest candidate actually reachable from its anchor cannot be placed at
    that distance and necessarily clips to the edge of the candidate
    universe. ``ceiling_m`` may be a scalar (one ceiling shared by every leg)
    or an array aligned with ``desired_distances_m`` (a per-leg ceiling).

    Returns
    -------
    tuple[float, int, int]
        ``(share, n_clipped, n_total)``. ``share`` is NaN when
        ``desired_distances_m`` is empty (no invented value for a zero-leg
        run).
    """
    desired = np.asarray(desired_distances_m, dtype=float)
    n_total = int(desired.size)
    if n_total == 0:
        return float("nan"), 0, 0
    ceiling = np.asarray(ceiling_m, dtype=float)
    clipped_mask = desired > ceiling
    n_clipped = int(np.count_nonzero(clipped_mask))
    return n_clipped / n_total, n_clipped, n_total
