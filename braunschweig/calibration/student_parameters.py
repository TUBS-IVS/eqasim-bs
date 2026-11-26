"""Parameter vocabulary, noise floor and person-day labels of the student measurement (#140, Phase 1).

One module that both sides of the measurement use, so that SrV and model students are put into
EXACTLY the same cells: the SrV-side tables and the model-side measurement pass their persons
through :func:`person_day_labels` and their label columns through :func:`parameter_rows`. It does
no I/O and holds no survey code.

Input of :func:`person_day_labels` is the harmonised plan-structure schema, the one comparison
schema of ``plan_structure_vs_srv`` (:data:`~braunschweig.calibration.srv_plan_structure.HARMONISED_PERSON_COLUMNS`,
:data:`~braunschweig.calibration.srv_plan_structure.HARMONISED_TRIP_COLUMNS`), with times in
minutes after midnight.

Day parameters (definitions)
----------------------------
* ``X_leg`` (education, work, leisure, escort): ``yes`` if the person has at least one trip with
  harmonised ``purpose`` X; ``shop_or_errand_leg``: a trip with purpose ``shop`` or ``other``.
  This is the harmonised-purpose definition of the plan-structure comparison. It is deliberately
  not the control definition of the participation targets (``srv_participation_universe``); the
  table builder (``srv_student_reference.build_parameter_table``, Task 2) owns the header that
  states the SrV share of adult students with an education leg under both definitions.
* ``mobile``: ``n_trips > 0`` (the person's ``n_trips`` column).
* ``trip_class``: ``n_trips`` in the right-inclusive classes of
  :data:`~braunschweig.calibration.student_behaviour_labels.TRIP_CLASS_EDGES`.
* ``first_departure_hour``: hour (``dep_min // 60``) of the departure of the trip with the smallest
  ``seq``; bands are inclusive of their named end hours (``7_8`` = hours 7 and 8).
* ``last_arrival_home_hour``: hour of ``arr_min`` of the last trip with purpose ``home``; a mobile
  person without a home trip is excluded and counted.
* ``returns_home``: number of trips with purpose ``home`` (``0``, ``1``, ``2``, ``3plus``).
* ``time_at_education``: for each education trip the next trip's ``dep_min`` minus its
  ``arr_min``, summed over the education trips, in minutes (``under_2h`` < 120, ``2_4h`` in
  [120, 240), ``4_6h`` in [240, 360), ``6h_more`` >= 360). A day that ends at education (no next
  trip), a missing time or a negative duration excludes the person from this parameter and is
  counted.
* ``arrival_band``: hour of ``arr_min`` of the first education trip in the right-inclusive classes
  of :data:`~braunschweig.calibration.student_behaviour_labels.ARRIVAL_HOUR_EDGES`.
* ``away_all_day`` is not a person-day label: it is measured on the file before the universe
  filter by the callers.

A missing time excludes the person from THAT parameter only (the label is NaN); every such
exclusion is counted per parameter in the diagnostics and logged as
``primary n/N (rate), excluded m (rate)``. NaN labels are never part of a share's base.
"""
from __future__ import annotations

import logging
from typing import NamedTuple

import numpy as np
import pandas as pd

from braunschweig.calibration.srv_plan_structure import (
    HARMONISED_PERSON_COLUMNS, HARMONISED_TRIP_COLUMNS, PURPOSES)
from braunschweig.calibration.student_behaviour_labels import (
    ARRIVAL_HOUR_EDGES, TRIP_CLASS_EDGES, behaviour_rows)
from braunschweig.popsim.attributes import PT_TICKET_GROUPS4

LOG = logging.getLogger(__name__)
_LOG_TAG = "[student_parameters]"

GROUP_WHO = "who"
GROUP_TOOLS = "mobility_tools"
GROUP_DAY = "day"

BASE_PRESENT = "students_present"
BASE_MOBILE = "students_mobile"
BASE_EDUCATION = "students_with_education_leg"
BASE_STUDENTS_ALL = "students_all"
BASE_OTHER_ADULTS_ALL = "other_adults_all"

YES_NO = ("yes", "no")


class ParameterSpec(NamedTuple):
    """One parameter: its group, its (first) base, its labels in display order and ordering."""

    group: str
    base: str
    labels: tuple
    ordered: bool


def _spec(group, base, labels, ordered=False):
    return ParameterSpec(group, base, tuple(labels), ordered)


PARAMETERS: dict[str, ParameterSpec] = {
    # who
    "age_group": _spec(GROUP_WHO, BASE_PRESENT, ("18_19", "20_24", "25_29", "30plus"), True),
    "sex": _spec(GROUP_WHO, BASE_PRESENT, ("female", "male")),
    "household_type": _spec(GROUP_WHO, BASE_PRESENT,
                            ("alone", "several_adults", "with_children")),
    "lives_with_person_18_years_older": _spec(GROUP_WHO, BASE_PRESENT, YES_NO),
    # mobility tools
    "licence": _spec(GROUP_TOOLS, BASE_PRESENT, YES_NO),
    "cars_in_household": _spec(GROUP_TOOLS, BASE_PRESENT, ("0", "1", "2plus"), True),
    # car_access: a driving licence AND at least one car in the household
    "car_access": _spec(GROUP_TOOLS, BASE_PRESENT, YES_NO),
    "bicycle_in_household": _spec(GROUP_TOOLS, BASE_PRESENT, YES_NO),
    "season_ticket": _spec(GROUP_TOOLS, BASE_PRESENT, ("flatrate", "no_flatrate")),
    "ticket_group4": _spec(GROUP_TOOLS, BASE_PRESENT, PT_TICKET_GROUPS4),
    # day
    "education_leg": _spec(GROUP_DAY, BASE_PRESENT, YES_NO),
    "work_leg": _spec(GROUP_DAY, BASE_PRESENT, YES_NO),
    "leisure_leg": _spec(GROUP_DAY, BASE_PRESENT, YES_NO),
    "shop_or_errand_leg": _spec(GROUP_DAY, BASE_PRESENT, YES_NO),
    "escort_leg": _spec(GROUP_DAY, BASE_PRESENT, YES_NO),
    "mobile": _spec(GROUP_DAY, BASE_PRESENT, YES_NO),
    "trip_class": _spec(GROUP_DAY, BASE_PRESENT, ("0", "1_2", "3_4", "5plus"), True),
    "first_departure_hour": _spec(GROUP_DAY, BASE_MOBILE,
                                  ("before_7", "7_8", "9_11", "12_15", "16_later"), True),
    "last_arrival_home_hour": _spec(GROUP_DAY, BASE_MOBILE,
                                    ("before_16", "16_18", "19_21", "22_later"), True),
    "returns_home": _spec(GROUP_DAY, BASE_MOBILE, ("0", "1", "2", "3plus"), True),
    "time_at_education": _spec(GROUP_DAY, BASE_EDUCATION,
                               ("under_2h", "2_4h", "4_6h", "6h_more"), True),
    "arrival_band": _spec(GROUP_DAY, BASE_EDUCATION, ("before_9", "9_11", "12_later"), True),
    # away_all_day is measured for both students and the other adults
    "away_all_day": _spec(GROUP_DAY, BASE_STUDENTS_ALL, YES_NO),
}

# Bases a parameter may be computed on besides ``ParameterSpec.base``.
EXTRA_BASES: dict[str, tuple] = {"away_all_day": (BASE_OTHER_ADULTS_ALL,)}

PARAMETER_COLUMNS = ["group", "parameter", "base", "label", "n_unweighted", "weight_sum",
                     "share", "n_effective", "se_share"]

# Hour bands of the first departure and the last arrival at home: RIGHT-inclusive hour edges,
# i.e. before_7 = hours <= 6, 7_8 = hours 7 and 8. 30 is the latest hour a survey day running
# past midnight can carry (the same bound as ARRIVAL_HOUR_EDGES); a later time is excluded.
FIRST_DEPARTURE_HOUR_EDGES = (-1, 6, 8, 11, 15, 30)
LAST_ARRIVAL_HOME_HOUR_EDGES = (-1, 15, 18, 21, 30)
# Time at education in minutes, LEFT-inclusive: [0, 120), [120, 240), [240, 360), [360, inf).
TIME_AT_EDUCATION_EDGES_MIN = (0.0, 120.0, 240.0, 360.0, float("inf"))

_SHOP_OR_ERRAND_PURPOSES = ("shop", "other")
_DAY_LABEL_PARAMETERS = tuple(name for name, spec in PARAMETERS.items()
                              if spec.group == GROUP_DAY and name != "away_all_day")
_EXCLUDABLE_PARAMETERS = ("first_departure_hour", "last_arrival_home_hour",
                          "time_at_education", "arrival_band")


def _valid_bases(parameter: str) -> tuple:
    return (PARAMETERS[parameter].base,) + EXTRA_BASES.get(parameter, ())


def parameter_rows(parameter: str, base: str, labels: pd.Series,
                   weights: pd.Series) -> pd.DataFrame:
    """One :data:`PARAMETER_COLUMNS` row per label of the parameter's vocabulary.

    ``labels`` and ``weights`` are aligned by position over the persons of the base; a NaN label
    (excluded person) is not in the base. Every label of the vocabulary gets a row, in vocabulary
    order; an empty cell has ``n_unweighted`` 0 and share 0.0, an empty base a NaN share. A label
    outside the vocabulary, an unknown parameter or base, or an invalid weight raises
    ``ValueError`` naming it. Labels must be strings or integer-valued (an integer is compared
    through its string, ``1`` is ``"1"``); a float such as ``1.0`` becomes ``"1.0"`` and raises as
    outside the vocabulary.
    """
    if parameter not in PARAMETERS:
        raise ValueError("unknown student parameter %r; known: %s" % (parameter, list(PARAMETERS)))
    if base not in _valid_bases(parameter):
        raise ValueError("parameter %r is not defined on base %r; valid bases: %s"
                         % (parameter, base, list(_valid_bases(parameter))))
    spec = PARAMETERS[parameter]
    label_values = pd.Series(labels).reset_index(drop=True)
    weight_values = pd.Series(weights).reset_index(drop=True)
    if len(label_values) != len(weight_values):
        raise ValueError("parameter %r: %d labels but %d weights" % (
            parameter, len(label_values), len(weight_values)))
    in_base = label_values.notna().to_numpy()
    base_labels = label_values[in_base].astype(str).to_numpy()
    base_weights = weight_values[in_base].to_numpy(dtype=float)
    unknown = sorted(set(base_labels) - set(spec.labels))
    if unknown:
        raise ValueError("parameter %r: label(s) %s outside the vocabulary %s"
                         % (parameter, unknown, list(spec.labels)))
    if not np.isfinite(base_weights).all() or (base_weights < 0).any():
        raise ValueError("parameter %r: weights of the base must be finite and >= 0" % parameter)
    rows = behaviour_rows(parameter, base, spec.labels, base_weights, base_labels)
    frame = pd.DataFrame(rows).rename(columns={"control": "parameter", "universe": "base"})
    frame["group"] = spec.group
    return frame[PARAMETER_COLUMNS].reset_index(drop=True)


def _total_variation(first: np.ndarray, second: np.ndarray) -> float:
    return 0.5 * float(np.abs(first - second).sum())


def _category_shares(codes: np.ndarray, weights: np.ndarray, n_categories: int) -> np.ndarray:
    sums = np.bincount(codes, weights=weights, minlength=n_categories)
    return sums / sums.sum()


def bootstrap_share_noise_floor(labels, weights, categories, n_bootstrap: int, seed: int,
                                quantile: float = 0.95) -> float:
    """The ``quantile`` of the total-variation distance of bootstrap shares to the full shares.

    Persons are resampled with replacement (n = sample size) with their weights carried along;
    the distance is ``0.5 * sum |p_boot - p_full|`` over ``categories``, i.e. the distance a model
    would reach against this reference by sampling noise alone (mirrors
    ``srv_distance_targets.bootstrap_emd_noise_floor``). NaN labels are dropped first. Returns 0.0
    for fewer than two persons. A draw whose persons all carry zero weight has no defined shares:
    it is skipped, counted and logged as a warning with its rate (raises when every draw is skipped).
    Raises ``ValueError`` for ``n_bootstrap < 1``, a label outside
    ``categories`` or a weight that is negative, not finite, or sums to zero.
    """
    if n_bootstrap < 1:
        raise ValueError("n_bootstrap must be >= 1")
    label_values = pd.Series(labels).reset_index(drop=True)
    weight_values = pd.Series(weights).reset_index(drop=True)
    if len(label_values) != len(weight_values):
        raise ValueError("%d labels but %d weights" % (len(label_values), len(weight_values)))
    keep = label_values.notna().to_numpy()
    label_values = label_values[keep].astype(str)
    weight_array = weight_values[keep].to_numpy(dtype=float)
    categories = [str(category) for category in categories]
    unknown = sorted(set(label_values) - set(categories))
    if unknown:
        raise ValueError("label(s) %s outside the categories %s" % (unknown, categories))
    if len(label_values) < 2:
        return 0.0
    if not np.isfinite(weight_array).all() or (weight_array < 0).any() or not weight_array.sum() > 0:
        raise ValueError("weights must be finite, >= 0 and sum to a positive total")
    codes = label_values.map({category: i for i, category in enumerate(categories)}).to_numpy()
    n_categories = len(categories)
    base_shares = _category_shares(codes, weight_array, n_categories)
    rng = np.random.default_rng(seed)
    distances = []
    n_zero_weight_draws = 0
    for _ in range(n_bootstrap):
        index = rng.integers(0, codes.size, codes.size)
        if not weight_array[index].sum() > 0:
            # all drawn persons carry zero weight: no share is defined for this draw
            n_zero_weight_draws += 1
            continue
        distances.append(_total_variation(
            _category_shares(codes[index], weight_array[index], n_categories), base_shares))
    if n_zero_weight_draws:
        LOG.warning("%s noise floor: %d/%d bootstrap draws (%.1f%%) had a zero total weight and "
                    "were skipped", _LOG_TAG, n_zero_weight_draws, n_bootstrap,
                    100.0 * n_zero_weight_draws / n_bootstrap)
    if not distances:
        raise ValueError("every bootstrap draw had a zero total weight")
    return float(np.quantile(distances, quantile))


def _band(values: pd.Series, edges, labels, right: bool) -> pd.Series:
    """Band ``values`` into ``labels``; NaN or out-of-range values stay NaN (excluded)."""
    banded = pd.cut(values.astype(float), bins=list(edges), labels=list(labels), right=right)
    return banded.astype(object).where(banded.notna(), np.nan)


def _row_value(rows: pd.DataFrame, column: str, index: pd.Index) -> pd.Series:
    """``column`` of one selected row per pid, aligned to ``index``; NaN where there is no row."""
    return pd.Series(rows[column].to_numpy(dtype=float), index=rows["pid"].to_numpy()).reindex(index)


def _yes_no(flag: pd.Series) -> pd.Series:
    return pd.Series(np.where(flag.to_numpy(dtype=bool), "yes", "no"), index=flag.index,
                     dtype=object)


def _log_exclusion(parameter: str, n_base: int, n_excluded: int) -> None:
    n_primary = n_base - n_excluded
    rate = (lambda count: 100.0 * count / n_base if n_base else float("nan"))
    message = "%s %s: primary %d/%d (%.1f%%), excluded %d (%.1f%%)" % (
        _LOG_TAG, parameter, n_primary, n_base, rate(n_primary), n_excluded, rate(n_excluded))
    (LOG.warning if n_excluded else LOG.info)(message)


def person_day_labels(persons: pd.DataFrame, trips: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Day labels of every person (one row per ``pid``) and the exclusion diagnostics.

    ``persons`` and ``trips`` are in the harmonised plan-structure schema, times in minutes. The
    result has the column ``pid`` plus one column per day parameter except ``away_all_day``; a
    label is NaN where the person is outside the parameter's base or excluded from it. The
    diagnostics hold ``n_persons``, ``n_mobile``, ``n_with_education_leg`` and
    ``n_excluded_<parameter>`` for the four parameters that can lose persons to a missing time.
    Definitions: module docstring. Raises ``ValueError`` for missing columns, a duplicated
    ``pid`` or ``(pid, seq)``, a missing ``n_trips`` or trips of a ``pid`` not in ``persons``.
    """
    for name, frame, columns in (("persons", persons, HARMONISED_PERSON_COLUMNS),
                                 ("trips", trips, HARMONISED_TRIP_COLUMNS)):
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise ValueError("%s frame lacks harmonised column(s) %s" % (name, missing))
    if persons["pid"].duplicated().any():
        raise ValueError("duplicated pid in persons: %s"
                         % sorted(persons.loc[persons["pid"].duplicated(), "pid"].astype(str)
                                  .unique())[:5])
    if persons["n_trips"].isna().any():
        raise ValueError("persons carry %d missing n_trips" % int(persons["n_trips"].isna().sum()))
    declared = persons["n_trips"].to_numpy(dtype=float)
    if (declared < 0).any() or (declared != np.floor(declared)).any():
        raise ValueError("persons carry %d n_trips that are negative or not whole numbers"
                         % int(((declared < 0) | (declared != np.floor(declared))).sum()))
    orphans = sorted(set(trips["pid"]) - set(persons["pid"]))
    if orphans:
        raise ValueError("trips of %d pid(s) missing from persons, e.g. %s"
                         % (len(orphans), [str(pid) for pid in orphans[:5]]))
    if trips.duplicated(["pid", "seq"]).any():
        raise ValueError("duplicated (pid, seq) in trips")

    observed = trips.groupby("pid").size().reindex(persons["pid"].to_numpy()).fillna(0).to_numpy()
    if (observed != declared).any():
        raise ValueError("%d person(s) have an n_trips that differs from their number of trip rows"
                         " (the harmonised frames must agree)" % int((observed != declared).sum()))

    ordered = trips.sort_values(["pid", "seq"], kind="stable").reset_index(drop=True)
    by_pid = ordered.groupby("pid", sort=False)
    index = pd.Index(persons["pid"].to_numpy(), name="pid")
    n_trips = pd.Series(persons["n_trips"].to_numpy(dtype=float), index=index)
    out = pd.DataFrame(index=index)

    for parameter, purposes in (("education_leg", ("education",)), ("work_leg", ("work",)),
                                ("leisure_leg", ("leisure",)), ("escort_leg", ("escort",)),
                                ("shop_or_errand_leg", _SHOP_OR_ERRAND_PURPOSES)):
        unknown = [purpose for purpose in purposes if purpose not in PURPOSES]
        if unknown:
            raise ValueError("purpose(s) %s are not harmonised purposes %s" % (unknown, PURPOSES))
        has = ordered.loc[ordered["purpose"].isin(purposes), "pid"].unique()
        out[parameter] = _yes_no(pd.Series(index.isin(has), index=index))
    mobile = n_trips > 0
    out["mobile"] = _yes_no(mobile)
    out["trip_class"] = _band(n_trips, TRIP_CLASS_EDGES, PARAMETERS["trip_class"].labels, True)

    # first departure of every person with a trip, then the band; mobile persons only
    # head(1)/tail(1), never first()/last(): those skip a NaN and would return the NEXT trip's time
    # for a person whose defining trip has no readable time, instead of excluding the person.
    first_departure_min = _row_value(by_pid.head(1), "dep_min", index)
    first_hour = np.floor(first_departure_min / 60.0)
    out["first_departure_hour"] = _band(first_hour.where(mobile), FIRST_DEPARTURE_HOUR_EDGES,
                                        PARAMETERS["first_departure_hour"].labels, True)

    home = ordered[ordered["purpose"] == "home"]
    last_home_arrival_min = _row_value(home.groupby("pid", sort=False).tail(1), "arr_min", index)
    out["last_arrival_home_hour"] = _band(
        np.floor(last_home_arrival_min / 60.0).where(mobile), LAST_ARRIVAL_HOME_HOUR_EDGES,
        PARAMETERS["last_arrival_home_hour"].labels, True)

    n_home = home.groupby("pid", sort=False).size().reindex(index).fillna(0).astype(int)
    returns = pd.Series(np.where(n_home >= 3, "3plus", n_home.astype(str)), index=index,
                        dtype=object)
    out["returns_home"] = returns.where(mobile)

    education = ordered.assign(
        next_dep_min=by_pid["dep_min"].shift(-1))[ordered["purpose"] == "education"].copy()
    education["duration_min"] = education["next_dep_min"] - education["arr_min"]
    # a person is excluded when ANY education trip has no readable, non-negative duration
    education["bad"] = education["duration_min"].isna() | (education["duration_min"] < 0)
    has_education = out["education_leg"] == "yes"
    education_by_pid = education.groupby("pid", sort=False)
    time_total = education_by_pid["duration_min"].sum().reindex(index)
    time_total = time_total.where(~education_by_pid["bad"].any().reindex(index).fillna(False)
                                  .astype(bool))
    out["time_at_education"] = _band(time_total.where(has_education),
                                     TIME_AT_EDUCATION_EDGES_MIN,
                                     PARAMETERS["time_at_education"].labels, False)

    first_education_arrival_min = _row_value(education_by_pid.head(1), "arr_min", index)
    out["arrival_band"] = _band(np.floor(first_education_arrival_min / 60.0).where(has_education),
                                ARRIVAL_HOUR_EDGES, PARAMETERS["arrival_band"].labels, True)

    labels = out[list(_DAY_LABEL_PARAMETERS)].reset_index()
    diagnostics = {"n_persons": int(len(index)), "n_mobile": int(mobile.sum()),
                   "n_with_education_leg": int(has_education.sum())}
    base_masks = {"first_departure_hour": mobile, "last_arrival_home_hour": mobile,
                  "time_at_education": has_education, "arrival_band": has_education}
    for parameter in _EXCLUDABLE_PARAMETERS:
        base_mask = base_masks[parameter]
        n_excluded = int(out.loc[base_mask.to_numpy(), parameter].isna().sum())
        diagnostics["n_excluded_%s" % parameter] = n_excluded
        _log_exclusion(parameter, int(base_mask.sum()), n_excluded)
    return labels, diagnostics
