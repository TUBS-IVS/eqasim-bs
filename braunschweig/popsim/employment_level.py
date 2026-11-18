"""One employment LEVEL for every employment control of a PopulationSim batch (#658, Task A5).

A Kreis part of a batch carries three employment levels: ``employed_KREIS`` (Zensus), the 100 m
employed grid (``EMPLOYED_*_agg``, the same Zensus total) and the employed classes of the KREIS
families ``work_by_employment`` / ``employment_status`` (a MiD-blended margin, about 5 % below the
Zensus). Owner decision D-emp (2026-11-18): the Zensus count is the employment LEVEL of every
employment control; the surveys keep only the conditional SHARES.

ASSUMPTIONS
-----------
* The "bridge". The survey families count a person as employed by ``P_BKAT`` (the employed classes
  of ``attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES``), the Zensus and the grid by ``P_TAET``
  (``attributes.EMPLOYED_TAET``). The share of persons employed by ``P_TAET`` that are also in the
  survey's employed classes is taken as in the weighted MiD seed. A family's employed target is
  therefore ``bridge x`` the Zensus employed count, not the Zensus count itself.
* The bridge is computed over the age universe of the employed grid (its lowest age band starts at
  16, :func:`employed_grid_min_age`), because the Zensus count it multiplies covers only that
  universe. Both KREIS families have a 14+ universe (``WORK_BY_EMPLOYMENT_MIN_AGE_YEARS`` and the
  ``min_age`` of the ``employment_status`` registry entry, both 14). The WHOLE employed group of a
  family, including its employed persons aged 14-15, is scaled to ``bridge x`` the Zensus count of
  the 16+ universe: those younger employed persons are INSIDE that level, neither on top of it nor
  left as apportioned. The employed persons aged 16+ of a family therefore come out lower than
  ``bridge x`` Zensus by about their below-grid share (the weighted seed share of the family's
  employed persons that are younger than the grid's lower age). That share, and the bridge over the
  family's own universe for comparison, are logged with the bridge (:func:`employed_bridge_report`).

Everything here is opt-in: :func:`reconcile_kreis_employment_levels` only runs when the caller
passes a bridge (``assemble_batch_folder(employed_bridge=...)``); without it the tables are
unchanged.
"""

from __future__ import annotations

import logging
import math
from typing import Mapping, NamedTuple, Sequence

import numpy as np
import pandas as pd

from braunschweig.popsim import attributes, control_spec, folders
from braunschweig.popsim import kreis_attribute_control as kac
from braunschweig.popsim.fallback_rates import log_fallback_rate
from braunschweig.popsim.zensus_employment_age import AGE_GROUPS

logger = logging.getLogger(__name__)

#: Seed columns the bridge reads (MiD seed, ``seed.MID_SEED_COLUMNS``).
_HOUSEHOLD_ID = "H_ID"
_HOUSEHOLD_WEIGHT = "H_GEW"
_AGE = "HP_ALTER"
_TAET = "P_TAET"

#: Employed / not-employed labels of the ``work_by_employment`` seed column: employment status in
#: ``attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES`` x work leg or not
#: (``mid.participation.derive_work_by_employment_seed``).
WORK_BY_EMPLOYMENT_EMPLOYED_LABELS = ("employed_work", "employed_nowork")
WORK_BY_EMPLOYMENT_OTHER_LABELS = ("nonemployed_work", "nonemployed_nowork")
#: The ``employment_status`` classes outside ``attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES``.
EMPLOYMENT_STATUS_OTHER_LABELS = ("sonstiges", "erwerbstaetig_unspec", "nicht_erwerbstaetig")

#: Name of the scratch column that carries the batch sum of the employed grid per Kreis part.
_GRID_SUM_COLUMN = "employed_grid_sum_" + folders.GEO_KREIS

#: Reasons a Kreis part's family is left unchanged (see :class:`Reconciled`).
EMPTY_TOTAL = "empty_total"
EMPLOYED_GROUP_ZERO = "employed_group_zero"
OTHER_GROUP_ZERO = "other_group_zero"


def employed_grid_min_age() -> int:
    """Lower age bound of the employed grid's universe: the start of its lowest age band."""
    return min(lo for _name, lo, _hi in AGE_GROUPS)


def build_employment_families(
    employed_by_family: Mapping[str, Sequence[str]] | None = None,
    other_by_family: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, tuple[tuple[str, ...], tuple[str, ...]]]:
    """``{KREIS family: (employed labels, other labels)}``, checked against the registry both ways.

    The employed and the other labels of a family must be disjoint and together EQUAL the
    categories of its ``kreis_attribute_control.REGISTRY`` entry: a category added to (or removed
    from) the registry fails here instead of landing silently in the "other" group.
    """
    employed_by_family = dict(employed_by_family or {
        "work_by_employment": WORK_BY_EMPLOYMENT_EMPLOYED_LABELS,
        "employment_status": attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES})
    other_by_family = dict(other_by_family or {
        "work_by_employment": WORK_BY_EMPLOYMENT_OTHER_LABELS,
        "employment_status": EMPLOYMENT_STATUS_OTHER_LABELS})
    registry = {c.name: c for c in kac.REGISTRY}
    out = {}
    for family, employed in employed_by_family.items():
        other = tuple(other_by_family[family])
        labels = tuple(label for label, _ in registry[family].categories)
        if set(employed) & set(other) or set(employed) | set(other) != set(labels):
            raise ValueError(
                f"{family}: employed labels {list(employed)} and other labels {list(other)} must be "
                f"disjoint and together equal the registry categories {list(labels)}; classify the new "
                "or removed category as employed or other here")
        out[family] = (tuple(employed), other)
    return out


#: ``{KREIS family: (employed labels, other labels)}`` of the families reconciled to the Zensus level.
EMPLOYMENT_FAMILIES = build_employment_families()


class BridgeReport(NamedTuple):
    """The bridge and the figures logged with it (see :func:`employed_bridge_report`)."""

    bridge: float                  #: over the employed grid's age universe (the one that is applied)
    bridge_family_universe: float  #: over the families' 14+ universe, for comparison only
    below_grid_share: float        #: weighted share of the family's employed aged below the grid
    source: str                    #: seed column that defines the survey employed class


def _bridge_inputs(seed_persons: pd.DataFrame, seed_households: pd.DataFrame):
    """(employed-class flag, P_TAET flag, age, weight, source column) of the seed persons."""
    if "work_by_employment" in seed_persons.columns:
        employed_class = seed_persons["work_by_employment"].isin(WORK_BY_EMPLOYMENT_EMPLOYED_LABELS)
        source = "work_by_employment"
    elif "employment_status" in seed_persons.columns:
        employed_class = seed_persons["employment_status"].isin(attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES)
        source = "employment_status"
    else:
        raise ValueError("employed_definition_bridge: the seed persons carry neither work_by_employment "
                         "nor employment_status; one of the two KREIS families must be active")
    for table, columns in ((seed_persons, (_HOUSEHOLD_ID, _AGE, _TAET)),
                           (seed_households, (_HOUSEHOLD_ID, _HOUSEHOLD_WEIGHT))):
        absent = [c for c in columns if c not in table.columns]
        if absent:
            raise ValueError(f"employed_definition_bridge: seed column(s) {absent} missing")
    weight = seed_persons[_HOUSEHOLD_ID].map(seed_households.set_index(_HOUSEHOLD_ID)[_HOUSEHOLD_WEIGHT])
    if weight.isna().any():
        raise ValueError(f"employed_definition_bridge: {int(weight.isna().sum())} person(s) without a "
                         f"household weight ({_HOUSEHOLD_WEIGHT} of seed_households)")
    age = pd.to_numeric(seed_persons[_AGE], errors="coerce")
    by_taet = pd.to_numeric(seed_persons[_TAET], errors="coerce").isin(list(attributes.EMPLOYED_TAET))
    return employed_class, by_taet, age, weight, source


def _bridge_at(employed_class, by_taet, age, weight, min_age: int) -> float:
    in_age = age >= min_age
    denominator = float(weight[in_age & by_taet].sum())
    if not denominator > 0:
        raise ValueError(f"employed_definition_bridge: no household-weighted person aged {min_age}+ with "
                         f"{_TAET} in {sorted(attributes.EMPLOYED_TAET)}; the bridge is undefined")
    return float(weight[in_age & employed_class].sum()) / denominator


def employed_definition_bridge(
    seed_persons: pd.DataFrame, seed_households: pd.DataFrame, *, min_age: int,
) -> float:
    """Household-weighted share of the ``P_TAET``-employed persons that are in a survey employed class.

    Numerator: persons aged ``min_age`` or more in an employed class of the survey families
    (the ``work_by_employment`` column when the seed carries it, else ``employment_status``;
    both are defined by ``attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES``). Denominator: the
    same age group with ``P_TAET`` in ``attributes.EMPLOYED_TAET``. Weights are the household
    weights of ``seed_households`` (``H_GEW``, joined on ``H_ID``).

    Raises:
        ValueError: a needed column is missing, a person has no household weight, or the
            denominator is 0 (the bridge would be undefined).
    """
    employed_class, by_taet, age, weight, _source = _bridge_inputs(seed_persons, seed_households)
    return _bridge_at(employed_class, by_taet, age, weight, min_age)


def employed_bridge_report(
    seed_persons: pd.DataFrame, seed_households: pd.DataFrame, *, grid_min_age: int, family_min_age: int,
) -> BridgeReport:
    """The bridge over the employed grid's age universe, with its comparison figures, logged once.

    ``bridge`` (the value to apply) is :func:`employed_definition_bridge` at ``grid_min_age``;
    ``bridge_family_universe`` is the same at ``family_min_age``; ``below_grid_share`` is the
    weighted share of the persons in a survey employed class aged ``family_min_age`` or more that
    are younger than ``grid_min_age`` (they are inside the scaled employed group, so the persons
    aged ``grid_min_age``+ come out lower than ``bridge x`` Zensus by about this share; module
    ASSUMPTIONS). One INFO line names the seed column used.
    """
    employed_class, by_taet, age, weight, source = _bridge_inputs(seed_persons, seed_households)
    bridge = _bridge_at(employed_class, by_taet, age, weight, grid_min_age)
    comparison = _bridge_at(employed_class, by_taet, age, weight, family_min_age)
    family_employed = float(weight[employed_class & (age >= family_min_age)].sum())
    below = float(weight[employed_class & (age >= family_min_age) & (age < grid_min_age)].sum())
    share = below / family_employed if family_employed > 0 else float("nan")
    logger.info(
        "[popsim.employment_level] bridge %.4f over persons aged %d+ (the employed grid's universe) = "
        "persons in an employed class of %s / persons with %s in EMPLOYED_TAET, household-weighted MiD "
        "seed; %.4f over the families' own %d+ universe; %.2f%% of the weighted employed persons aged "
        "%d+ are younger than %d: they sit inside the scaled employed group, so the employed persons "
        "aged %d+ end up lower than bridge x Zensus by about that share", bridge, grid_min_age, source,
        _TAET, comparison, family_min_age, 100.0 * share, family_min_age, grid_min_age, grid_min_age)
    return BridgeReport(bridge, comparison, share, source)


class Reconciled(NamedTuple):
    """Result of :func:`reconcile_employed_level`."""

    values: dict[str, float]          #: the class values (the input values when ``unchanged_reason``)
    capped: bool                      #: the target was above the total and was capped at it
    unchanged_reason: str | None      #: ``None`` if reconciled, else why the values were left as given


def reconcile_employed_level(
    values: Mapping[str, float], employed: Sequence[str], other: Sequence[str], employed_target: float,
) -> Reconciled:
    """Scale the ``employed`` classes to ``employed_target`` and the ``other`` ones to the rest.

    The total ``sum(values)`` is kept; proportions inside each group are kept. A target above the
    total is capped at the total (no negative class) and ``capped`` says so.

    The values are returned unchanged, with a reason, instead of raising when the data cannot be
    scaled: :data:`EMPTY_TOTAL` (the total is 0, nothing to apportion; not a capped part),
    :data:`EMPLOYED_GROUP_ZERO` (the employed classes sum to 0 but must carry mass) or
    :data:`OTHER_GROUP_ZERO` (the other classes sum to 0 but must carry the rest).

    Raises:
        ValueError: the target is negative or not finite, a class is negative, or ``employed`` and
            ``other`` do not partition ``values`` (a caller error, not a data condition).
    """
    employed, other = list(employed), list(other)
    if set(employed) & set(other) or set(employed) | set(other) != set(values):
        raise ValueError(f"employed {employed} and other {other} must partition the classes {list(values)}")
    if not math.isfinite(employed_target) or employed_target < 0:
        raise ValueError(f"employed_target must be finite and >= 0, got {employed_target}")
    if any(v < 0 for v in values.values()):
        raise ValueError(f"negative class value in {dict(values)}")
    unchanged = {k: float(v) for k, v in values.items()}
    total = float(sum(unchanged.values()))
    if total <= 0:
        return Reconciled(unchanged, False, EMPTY_TOTAL)
    capped = employed_target > total
    target = min(float(employed_target), total)
    out: dict[str, float] = {}
    for group, mass, reason in ((employed, target, EMPLOYED_GROUP_ZERO), (other, total - target, OTHER_GROUP_ZERO)):
        group_sum = float(sum(unchanged[k] for k in group))
        if mass > 0 and group_sum <= 0:
            return Reconciled(unchanged, False, reason)
        for k in group:
            out[k] = unchanged[k] * mass / group_sum if mass > 0 else 0.0
    return Reconciled({k: out[k] for k in values}, capped, None)


def reconcile_kreis_employment_levels(
    kreis_totals: pd.DataFrame,
    fine_totals: Mapping[str, pd.DataFrame],
    geo_crosswalk: pd.DataFrame,
    *,
    bridge: float,
    stats: dict | None = None,
) -> pd.DataFrame:
    """Give the employed classes of every active employment family the Zensus level of their Kreis part.

    Per Kreis row: ``employed_target = bridge x`` the batch sum of the ten employed-grid columns
    over the cells of the Kreis part (the crosswalk summing of
    :func:`folders.set_kreis_controls_from_fine_sums`, reused); each family of
    :data:`EMPLOYMENT_FAMILIES` that is an active control (all its columns in ``kreis_totals``)
    is then reconciled with :func:`reconcile_employed_level`, so its employed classes sum to the
    target and its row total is unchanged.

    Universe: the grid and the Zensus count cover persons aged 16+ (:func:`employed_grid_min_age`),
    the families' universe is 14+ (``work_by_employment``: ``WORK_BY_EMPLOYMENT_MIN_AGE_YEARS``;
    ``employment_status``: ``min_age`` 14 of its registry entry). The target applies to the family's
    employed classes as a whole (module ASSUMPTIONS).

    Observability: a family that is not active is logged and skipped. A target above a part's row
    total is capped (never a negative class) and counted. A part with a zero total is skipped and
    counted as empty. A part whose employed or other group sums to 0 although it must carry mass
    stays unchanged; that is a fallback, logged as a WARNING with Kreis id, family and reason and
    as a rate over the reconciled parts. When ``stats`` is a dict it receives the counts over all
    families for the stage report file: ``parts_reconciled``, ``parts_capped``, ``capped_persons``,
    ``parts_empty``, ``parts_unchanged`` (Kreis part x family pairs). One INFO line per batch reports the bridge, per family the
    mean relative change of the employed level, the capped parts and their persons, and the empty
    and unchanged parts. Returns a copy.

    Raises:
        ValueError: the employed grid is not an active control of the batch, ``bridge`` is not a
            positive finite number, or a family column is only partly present.
    """
    if not (math.isfinite(bridge) and bridge > 0):
        raise ValueError(f"employed_bridge must be a positive finite number, got {bridge}")
    grid_columns = control_spec.kreis_grid_pairs()[f"employed_{folders.GEO_KREIS}"]
    table = fine_totals.get(folders.GEO_100M)
    absent = [c for c in grid_columns if table is None or c not in table.columns]
    if absent:
        raise ValueError(f"employed_bridge is set but the employed grid is not active in this batch: "
                         f"{len(absent)} column(s) missing ({absent[:3]} ...); the Zensus employment level "
                         "is the sum of the ten EMPLOYED_*_agg targets")
    scratch = kreis_totals[[folders.GEO_KREIS]].assign(**{_GRID_SUM_COLUMN: 0.0})
    grid_sum = folders.set_kreis_controls_from_fine_sums(
        scratch, fine_totals, geo_crosswalk, {_GRID_SUM_COLUMN: grid_columns})[_GRID_SUM_COLUMN]
    target = bridge * grid_sum.to_numpy(dtype=float)
    kreise = kreis_totals[folders.GEO_KREIS].astype(str).tolist()
    out = kreis_totals.copy()
    summary, skipped = [], []
    n_reconciled = n_unchanged = 0
    n_done = n_capped = n_empty_total = 0
    capped_persons_total = 0.0
    for family, (employed, other) in EMPLOYMENT_FAMILIES.items():
        columns = {label: f"{family}_{label}_{folders.GEO_KREIS}" for label in (*employed, *other)}
        present = [c for c in columns.values() if c in out.columns]
        if not present:
            skipped.append(family)
            continue
        if len(present) != len(columns):
            raise ValueError(f"KREIS family {family!r} is only partly present: missing "
                             f"{sorted(set(columns.values()) - set(present))}")
        old = out[list(columns.values())].to_numpy(dtype=float)
        new = old.copy()
        capped = np.zeros(len(out), dtype=bool)
        done = np.zeros(len(out), dtype=bool)
        n_empty = 0
        unchanged_parts = []
        for i in range(len(out)):
            result = reconcile_employed_level(dict(zip(columns, old[i])), employed, other, target[i])
            if result.unchanged_reason == EMPTY_TOTAL:
                n_empty += 1
            elif result.unchanged_reason is not None:
                unchanged_parts.append((kreise[i], family, result.unchanged_reason))
            else:
                new[i] = [result.values[label] for label in columns]
                capped[i], done[i] = result.capped, True
        for j, column in enumerate(columns.values()):
            out[column] = new[:, j]
        if unchanged_parts:
            logger.warning("[popsim.employment_level] %d Kreis part(s) of %s left at their apportioned "
                           "values (Kreis, family, reason): %s", len(unchanged_parts), family, unchanged_parts)
        n_reconciled += int(done.sum()) + len(unchanged_parts)
        n_unchanged += len(unchanged_parts)
        n_employed = len(employed)
        before, after = old[:, :n_employed].sum(axis=1), new[:, :n_employed].sum(axis=1)
        positive = done & (before > 0)
        change = (float(np.mean(np.abs(after - before)[positive] / before[positive]))
                  if positive.any() else float("nan"))
        persons = float(old.sum(axis=1)[capped].sum())
        n_done += int(done.sum())
        n_capped += int(capped.sum())
        n_empty_total += n_empty
        capped_persons_total += persons
        summary.append(f"{family}: mean relative change of the employed level {100.0 * change:.2f}% over "
                       f"{int(done.sum())} part(s), capped {int(capped.sum())} part(s) ({persons:.0f} "
                       f"persons), empty {n_empty}, unchanged {len(unchanged_parts)}")
    logger.info("[popsim.employment_level] bridge %.4f, Zensus employment level on %d family(ies): %s%s",
                bridge, len(summary), "; ".join(summary) if summary else "none active",
                f"; skipped (not an active control): {skipped}" if skipped else "")
    if summary:
        log_fallback_rate(logger, "popsim.employment_level.unchanged_part", n_unchanged, n_reconciled,
                          what="rate over Kreis-part x family pairs; a pair whose employed or other group is "
                               "empty keeps its apportioned values")
    if stats is not None:
        stats.update(parts_reconciled=n_done, parts_capped=n_capped, capped_persons=capped_persons_total,
                     parts_empty=n_empty_total, parts_unchanged=n_unchanged)
    return out
