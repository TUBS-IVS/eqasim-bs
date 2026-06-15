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
    open-top -> 7000*(1+exp_mean). Used as the support points of the max-ent tilt."""
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
    attribute mappers so the re-derived label stays in the SAME vocabulary popsim uses."""
    from braunschweig.popsim.attributes import (
        INCOME_CLASS_BY_GROUP,
        INCOME_GROUP_MIDPOINT_EUR,
    )
    return {
        INCOME_CLASS_BY_GROUP[code]: float(INCOME_GROUP_MIDPOINT_EUR[code])
        for code in INCOME_CLASS_BY_GROUP
    }


def income_class_from_eur(eur_values, class_midpoint_eur) -> np.ndarray:
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
