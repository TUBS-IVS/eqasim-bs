"""One employment LEVEL for every employment control of a PopulationSim batch (#658, Task A5).

A Kreis part of a batch carries three employment levels: ``employed_KREIS`` (Zensus), the 100 m
employed grid (``EMPLOYED_*_agg``, the same Zensus total) and the employed classes of the KREIS
families ``work_by_employment`` / ``employment_status`` (a MiD-blended margin, about 5 % below the
Zensus). Owner decision D-emp (2026-11-18): the Zensus count is the employment LEVEL of every
employment control; the surveys keep only the conditional SHARES.

ASSUMPTION (the "bridge"): the survey families count a person as employed by ``P_BKAT`` (the
employed classes of ``attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES``), the Zensus and the grid by
``P_TAET`` (``attributes.EMPLOYED_TAET``). The share of persons employed by ``P_TAET`` that are
also in the survey's employed classes is taken as in the weighted MiD seed. A family's employed
target is therefore ``bridge x`` the Zensus employed count, not the Zensus count itself.

Everything here is opt-in: :func:`reconcile_kreis_employment_levels` only runs when the caller
passes a bridge (``assemble_batch_folder(employed_bridge=...)``); without it the tables are
unchanged.
"""

from __future__ import annotations

import logging
import math
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from braunschweig.popsim import attributes, control_spec, folders
from braunschweig.popsim import kreis_attribute_control as kac

logger = logging.getLogger(__name__)

#: Seed columns the bridge reads (MiD seed, ``seed.MID_SEED_COLUMNS``).
_HOUSEHOLD_ID = "H_ID"
_HOUSEHOLD_WEIGHT = "H_GEW"
_AGE = "HP_ALTER"
_TAET = "P_TAET"

#: Employed labels of the ``work_by_employment`` seed column: employment status in
#: ``attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES`` x work leg or not
#: (``mid.participation.derive_work_by_employment_seed``).
WORK_BY_EMPLOYMENT_EMPLOYED_LABELS = ("employed_work", "employed_nowork")

#: Name of the scratch column that carries the batch sum of the employed grid per Kreis part.
_GRID_SUM_COLUMN = "employed_grid_sum_" + folders.GEO_KREIS


def _family(name: str, employed_labels: Sequence[str]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    labels = tuple(label for label, _ in {c.name: c for c in kac.REGISTRY}[name].categories)
    if not set(employed_labels) <= set(labels):
        raise ValueError(f"{name}: employed labels {sorted(employed_labels)} are not all categories "
                         f"{labels} of its registry entry")
    return tuple(employed_labels), tuple(label for label in labels if label not in employed_labels)


#: ``{KREIS family: (employed labels, other labels)}`` of the families reconciled to the Zensus level.
EMPLOYMENT_FAMILIES = {
    "work_by_employment": _family("work_by_employment", WORK_BY_EMPLOYMENT_EMPLOYED_LABELS),
    "employment_status": _family("employment_status", attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES),
}


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
    in_age = pd.to_numeric(seed_persons[_AGE], errors="coerce") >= min_age
    by_taet = pd.to_numeric(seed_persons[_TAET], errors="coerce").isin(list(attributes.EMPLOYED_TAET))
    denominator = float(weight[in_age & by_taet].sum())
    if not denominator > 0:
        raise ValueError(f"employed_definition_bridge: no household-weighted person aged {min_age}+ with "
                         f"{_TAET} in {sorted(attributes.EMPLOYED_TAET)}; the bridge is undefined")
    bridge = float(weight[in_age & employed_class].sum()) / denominator
    logger.info("[popsim.employment_level] bridge %.4f = persons aged %d+ in an employed class of %s / "
                "persons with %s in EMPLOYED_TAET (household-weighted MiD seed)",
                bridge, min_age, source, _TAET)
    return bridge


def reconcile_employed_level(
    values: Mapping[str, float], employed: Sequence[str], other: Sequence[str], employed_target: float,
) -> tuple[dict[str, float], bool]:
    """Scale the ``employed`` classes to ``employed_target`` and the ``other`` ones to the rest.

    The total ``sum(values)`` is kept; proportions inside each group are kept. A target above the
    total is capped at the total (no negative class) and the returned flag says so.

    Raises:
        ValueError: the target is negative or not finite, a class is negative, ``employed`` and
            ``other`` do not cover ``values``, or a group that must carry mass sums to 0.
    """
    employed, other = list(employed), list(other)
    if set(employed) & set(other) or set(employed) | set(other) != set(values):
        raise ValueError(f"employed {employed} and other {other} must partition the classes {list(values)}")
    if not math.isfinite(employed_target) or employed_target < 0:
        raise ValueError(f"employed_target must be finite and >= 0, got {employed_target}")
    if any(v < 0 for v in values.values()):
        raise ValueError(f"negative class value in {dict(values)}")
    total = float(sum(values.values()))
    capped = employed_target > total
    target = min(float(employed_target), total)
    out: dict[str, float] = {}
    for group, mass, name in ((employed, target, "employed"), (other, total - target, "other")):
        group_sum = float(sum(values[k] for k in group))
        if mass > 0 and group_sum <= 0:
            raise ValueError(f"the {name} group {group} must carry {mass:g} but its classes sum to 0 "
                             f"(values {dict(values)})")
        for k in group:
            out[k] = values[k] * mass / group_sum if mass > 0 else 0.0
    return {k: out[k] for k in values}, capped


def reconcile_kreis_employment_levels(
    kreis_totals: pd.DataFrame,
    fine_totals: Mapping[str, pd.DataFrame],
    geo_crosswalk: pd.DataFrame,
    *,
    bridge: float,
) -> pd.DataFrame:
    """Give the employed classes of every active employment family the Zensus level of their Kreis part.

    Per Kreis row: ``employed_target = bridge x`` the batch sum of the ten employed-grid columns
    over the cells of the Kreis part (the crosswalk summing of
    :func:`folders.set_kreis_controls_from_fine_sums`, reused); each family of
    :data:`EMPLOYMENT_FAMILIES` that is an active control (all its columns in ``kreis_totals``)
    is then reconciled with :func:`reconcile_employed_level`, so its employed classes sum to the
    target and its row total is unchanged. A family that is not active is logged and skipped. A
    target above a family's row total is capped (never a negative class) and counted. One INFO
    line reports the bridge, the mean relative change of the employed level and the capped parts.
    Returns a copy.

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
    out = kreis_totals.copy()
    summary, skipped = [], []
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
        new = np.empty_like(old)
        capped = np.zeros(len(out), dtype=bool)
        for i in range(len(out)):
            values = dict(zip(columns, old[i]))
            result, capped[i] = reconcile_employed_level(values, employed, other, target[i])
            new[i] = [result[label] for label in columns]
        for j, column in enumerate(columns.values()):
            out[column] = new[:, j]
        n_employed = len(employed)
        before, after = old[:, :n_employed].sum(axis=1), new[:, :n_employed].sum(axis=1)
        positive = before > 0
        change = float(np.mean(np.abs(after - before)[positive] / before[positive])) if positive.any() else float("nan")
        persons = float(old.sum(axis=1)[capped].sum())
        summary.append(f"{family}: mean relative change of the employed level {100.0 * change:.2f}% over "
                       f"{int(positive.sum())} Kreis part(s), capped {int(capped.sum())} part(s) "
                       f"({persons:.0f} persons)")
    logger.info("[popsim.employment_level] bridge %.4f, Zensus employment level on %d family(ies): %s%s",
                bridge, len(summary), "; ".join(summary) if summary else "none active",
                f"; skipped (not an active control): {skipped}" if skipped else "")
    return out
