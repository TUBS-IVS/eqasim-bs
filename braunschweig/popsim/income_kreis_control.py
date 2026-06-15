"""Kreis-Income-Control: real MiD net-income draw + max-entropy per-Kreis calibration.

Replaces the popsim income fudge (class_midpoint x INKAR_scale[Kreis]) with a real
continuous draw from P(bracket | hh_size, economic_status, raumtyp) (reusing the
data/mid income builders), reshaped per Kreis by an exponential tilt q*exp(lambda*e_b)
whose lambda is solved so the Kreis mean equals the construct-corrected INKAR target.

economic_status is left untouched (it is an INPUT: income is drawn conditioned on it);
household_income (label) + high_income are re-derived from the drawn EUR. The enriched/
IPF path is NOT touched. Pure module: no file I/O.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.data.mid.income_by_size import (
    INCOME_BRACKET_CATEGORIES,
    INCOME_BRACKET_BOUNDS_EUR,
    RS7_TO_RAUMTYP_KEY,
    SIZE_CATEGORIES,
    income_bracket_probabilities,
)
from braunschweig.data.mid.income_by_status import (
    income_bracket_probabilities_by_status,
    overall_bracket_pmf,
    combine_size_status_bracket_pmf,
)
from braunschweig.popsim.income import HIGH_INCOME_THRESHOLD_EUR
# The popsim income-class vocabulary (label) + its EUR midpoints, so the re-derived
# household_income label stays in the SAME vocabulary the donor mappers use. No
# circular import: attributes imports only reference_tables / ipf.attributed / popsim.missing.
from braunschweig.popsim.attributes import (
    INCOME_CLASS_BY_GROUP,
    INCOME_GROUP_MIDPOINT_EUR,
)

logger = logging.getLogger(__name__)

# Constants mirrored from the enriched distribution draw (kept local; enriched untouched).
INCOME_MIN_EUR = 100.0
INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION = 0.4
INCOME_OPEN_TOP_MAX_EUR = 18000.0

# Dedicated RNG offset so this draw is reproducible and independent of other streams.
INCOME_KC_RNG_OFFSET = 91237
DEFAULT_DRAW_METHOD = "combined"


def bracket_expected_eur() -> np.ndarray:
    """Per-bracket expected income e_b over INCOME_BRACKET_CATEGORIES (the mean the
    within-bracket draw realizes): closed -> (max(low, INCOME_MIN_EUR)+high)/2,
    open-top -> low*(1 + INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION). Used as the support
    points of the max-ent tilt."""
    e = np.empty(len(INCOME_BRACKET_CATEGORIES), dtype=float)
    for i, b in enumerate(INCOME_BRACKET_CATEGORIES):
        low, high = INCOME_BRACKET_BOUNDS_EUR[b]
        if high is None:
            e[i] = low * (1.0 + INCOME_OPEN_TOP_EXP_MEAN_EUR_FRACTION)
        else:
            e[i] = (max(low, INCOME_MIN_EUR) + high) / 2.0
    return e


def build_class_midpoint_eur() -> dict[str, float]:
    """Label -> midpoint EUR for the popsim income-class vocabulary, built from the
    attribute mappers so the re-derived label stays in the SAME vocabulary popsim uses.
    Key order is not significant; income_class_from_eur sorts by midpoint internally."""
    return {
        INCOME_CLASS_BY_GROUP[code]: float(INCOME_GROUP_MIDPOINT_EUR[code])
        for code in INCOME_CLASS_BY_GROUP
    }


def income_class_from_eur(eur_values, class_midpoint_eur: dict[str, float]) -> np.ndarray:
    """Nearest-midpoint classifier (1-D, monotone). Mirrors enriched._income_class_from_eur
    but kept local so enriched is not imported/touched."""
    labels = list(class_midpoint_eur.keys())
    midpoints = np.array([class_midpoint_eur[k] for k in labels], dtype=float)
    order = np.argsort(midpoints)
    labels_sorted = [labels[i] for i in order]
    mids_sorted = midpoints[order]
    edges = (mids_sorted[:-1] + mids_sorted[1:]) / 2.0
    idx = np.searchsorted(edges, np.asarray(eur_values, dtype=float), side="right")
    return np.asarray(labels_sorted, dtype=object)[idx]


def build_kreis_income_targets(
    inkar_df: pd.DataFrame,
    kreis_stats_df: pd.DataFrame,
    in_scope_ars5,
    *,
    hhsize_correct: bool = True,
) -> dict[str, float]:
    """Per-Kreis relative income factor rf_k, household-count-weighted, normalized to
    mean 1 over the in-scope Kreise.

    rf_k_raw = INKAR scale[k] * (mean_size[k] if hhsize_correct else 1)
    rf_k     = rf_k_raw / weighted_mean_k(rf_k_raw), weight = hh_count[k].

    Region-relative on purpose: imposing only the BETWEEN-Kreis relativity preserves
    the region-wide income level set by the MiD draw. Single Kreis -> rf_k == 1 (no-op).
    """
    scope = [str(a) for a in in_scope_ars5]
    scale = dict(zip(inkar_df["ars5"].astype(str), inkar_df["scale"].astype(float)))
    mean_size = dict(zip(kreis_stats_df["ars5"].astype(str),
                         kreis_stats_df["mean_size"].astype(float)))
    hh_count = dict(zip(kreis_stats_df["ars5"].astype(str),
                        kreis_stats_df["hh_count"].astype(float)))

    raw, weight = {}, {}
    for k in scope:
        s = scale.get(k, 1.0)
        sz = mean_size.get(k, 1.0) if hhsize_correct else 1.0
        raw[k] = s * sz
        weight[k] = hh_count.get(k, 1.0)

    wsum = sum(weight[k] for k in scope) or 1.0
    wmean = sum(raw[k] * weight[k] for k in scope) / wsum
    if wmean <= 0:
        logger.warning("[income_kreis_control] degenerate target mean <= 0; rf=1 for all.")
        return {k: 1.0 for k in scope}
    return {k: raw[k] / wmean for k in scope}
