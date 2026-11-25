"""Shared label vocabulary of the university-student behaviour cells (issue #140, Phase 0).

The SrV 2023 aggregates (:mod:`braunschweig.calibration.srv_student_reference`) and the MiD
seed side (:mod:`braunschweig.calibration.mid_student_reference`) must classify persons into
EXACTLY the same cells, otherwise every candidate control would "differ" between the two surveys
for a reason that has nothing to do with behaviour. This module is therefore the shared
vocabulary: label strings, bin edges, the two universe names, the column order of the behaviour
table, the two statistics every share of those tables carries, the one builder of a control's
rows (:func:`behaviour_rows`) and the exclusion limit all three student modules apply
(:data:`MAX_EXCLUSION_RATE`). It holds no survey code and does no I/O.

Statistics: the effective sample size is Kish's ``n_effective = (sum w)^2 / sum w^2`` over
exactly the rows of the share's universe, and the standard error of a weighted share is
``sqrt(share * (1 - share) / n_effective)``. Both are NaN for an empty base, never 0.0, so a
consumer cannot mistake "not measurable" for "measured with certainty".
"""
from __future__ import annotations

import math

import numpy as np

NOT_STUDENT = "not_student"

# Student labels per candidate control, in display order. In the adults_18plus universe every
# control additionally carries NOT_STUDENT; in the students universe the student labels alone,
# renormalised to the students.
CONTROLS: dict[str, tuple[str, ...]] = {
    "status_x_education_leg": ("student_edu", "student_noedu"),
    "trip_class": ("student_trips_0", "student_trips_1_2", "student_trips_3_4",
                   "student_trips_5plus"),
    "arrival": ("student_arrive_before_9", "student_arrive_9_11", "student_arrive_12_later",
                "student_no_education_leg"),
    "work_leg": ("student_work", "student_nowork"),
    "season_ticket": ("student_flatrate", "student_no_flatrate"),
}

# Number of legs on the reporting day, RIGHT-inclusive bins: (-1, 0] = 0, (0, 2] = 1-2,
# (2, 4] = 3-4, (4, 10000] = 5+. 10000 is an upper sentinel, not a real bound.
TRIP_CLASS_EDGES = (-1, 0, 2, 4, 10_000)

# Arrival hour of the first education leg, RIGHT-inclusive bins: hour <= 8 = before 9,
# 9-11, >= 12 (up to 30, the latest hour a survey day running past midnight can carry).
ARRIVAL_HOUR_EDGES = (-1, 8, 11, 30)

# Home-to-education distance bands, LEFT-inclusive (a 10.0 km leg is in 10_25). The 10 / 25 /
# 50 / 100 km edges are the commuter-weight bands of the design of this work (issue #140), to be
# recorded in an ADR in the next phase.
STUDENT_DISTANCE_EDGES_KM = (0.0, 5.0, 10.0, 25.0, 50.0, 100.0, float("inf"))
STUDENT_DISTANCE_LABELS = ("0_5", "5_10", "10_25", "25_50", "50_100", "100_plus")

MIN_ADULT_AGE = 18
UNIVERSE_ADULTS = "adults_18plus"
UNIVERSE_STUDENTS = "students"

BEHAVIOUR_COLUMNS = ["control", "universe", "label", "n_unweighted", "weight_sum", "share",
                     "n_effective", "se_share"]

# ASSUMPTION: an exclusion step that removes more than half of its base is a broken code mapping
# (a renamed column, a recoded variable), not a measurement, so the build raises instead of
# shipping a table built on the remainder; the decision tables apply the same limit to cells
# whose rule 2 cannot be evaluated. Half is a judgement, not a sourced threshold.
MAX_EXCLUSION_RATE = 0.5


def effective_sample_size(weights) -> float:
    """Kish effective sample size ``(sum w)^2 / sum w^2``; NaN for an empty or zero-weight base."""
    weight = np.asarray(weights, dtype=float)
    total = weight.sum()
    if weight.size == 0 or not total > 0:
        return float("nan")
    return float(total ** 2 / np.square(weight).sum())


def share_standard_error(share: float, n_effective: float) -> float:
    """``sqrt(share * (1 - share) / n_effective)``; NaN when the share or its base is undefined."""
    if share is None or n_effective is None:
        return float("nan")
    if math.isnan(share) or math.isnan(n_effective) or not n_effective > 0:
        return float("nan")
    return math.sqrt(share * (1.0 - share) / n_effective)


def behaviour_rows(control: str, universe_name: str, label_set, weight: np.ndarray,
                   labels: np.ndarray) -> list:
    """Rows of :data:`BEHAVIOUR_COLUMNS` for one control in one universe.

    ``weight`` and ``labels`` are aligned arrays over exactly the base of the control in that
    universe; every label of ``label_set`` gets a row (an absent label a zero count and a 0.0
    share over a non-empty base). The weighted share, the Kish size of the whole base and the
    standard error follow the module docstring; the share is NaN for an empty base.
    """
    total = weight.sum()
    n_effective = effective_sample_size(weight)
    rows = []
    for label in label_set:
        mask = labels == label
        weight_sum = float(weight[mask].sum())
        share = weight_sum / total if total > 0 else float("nan")
        rows.append({"control": control, "universe": universe_name, "label": label,
                     "n_unweighted": int(mask.sum()), "weight_sum": weight_sum, "share": share,
                     "n_effective": n_effective,
                     "se_share": share_standard_error(share, n_effective)})
    return rows
