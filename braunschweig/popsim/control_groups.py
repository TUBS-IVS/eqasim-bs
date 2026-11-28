"""Groups of PopulationSim controls that partition one seed population.

A control group collects the controls whose categories are meant to be mutually
exclusive and (if ``exhaustive``) to cover every seed record of the group's universe.
The group table is the single owner of this classification; consumers are the
popsim batch diagnostics (``braunschweig.analysis.popsim_batch_diagnostics``, after the run)
and the stage's contract check (``braunschweig.popsim.seed_contract``, before the run).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

# Largest accepted |group target sum / household total - 1|.  One owner for the WP1 tool
# (``Thresholds.universe_ratio_tolerance``) and the stage's contract check.
UNIVERSE_RATIO_TOLERANCE = 0.02


def evaluate_expression(expr: str, frame: pd.DataFrame) -> np.ndarray:
    """Evaluate a control_spec prefixed boolean expression against ``frame``.

    The expression uses the seed table name as a namespace prefix
    (``persons.<col>`` / ``households.<col>``).  Both names are bound to
    ``frame`` (only the control's own table is actually referenced) plus
    numpy, and Python ``eval`` is used (expressions are trusted, from
    control_spec).  Returns a boolean numpy array aligned to ``frame``.
    """
    env = {"np": np, "persons": frame, "households": frame, "__builtins__": {}}
    return np.asarray(eval(expr, env)).astype(bool)  # noqa: S307 (trusted source)


@dataclass(frozen=True)
class ControlGroup:
    """One family of controls over one seed table.

    ``target_pattern`` is applied with ``re.search`` to the control target name;
    ``universe`` is an expression in the control language (``None`` = every record
    of the table); ``exhaustive`` states that the categories are meant to cover the
    whole universe.
    """

    name: str
    seed_table: str
    target_pattern: str
    universe: str | None
    exhaustive: bool


# Owner: ``KreisAttributeControl.min_age`` (14 for the four groups below), rendered by
# ``control_spec`` as "(persons.HP_ALTER >= <min_age>)"; a test ties this copy to the registry.
_AGE14 = "(persons.HP_ALTER >= 14)"
# ASSUMPTION (no owner): the non-exhaustive schulabschluss/beruflabschluss groups are judged
# on persons aged 15 and over.  The controls carry no age clause; control_spec notes that the
# MiD bildung1 code 1 mixes "no school-leaving certificate" with all children under 15, so a
# person under 15 is not expected to fall in a certificate category.  Only the group's
# non-exhaustive coverage numbers depend on it; it changes no control and no target.
_AGE15 = "(persons.HP_ALTER >= 15)"
# Owner: ``kreis_attribute_control.STUDENT_AGE_BOUNDS`` (the soft student entries of A3, ADR-0211),
# rendered by ``control_spec`` as the min_age and max_age clauses; the same test ties this copy.
_AGE18_29 = "(persons.HP_ALTER >= 18) & (persons.HP_ALTER <= 29)"

CONTROL_GROUPS: tuple = (
    ControlGroup("age_sex", "persons", r"^[MF]_AGE_", None, True),
    ControlGroup("employed_grid", "persons", r"^EMPLOYED_[MF]_",
                 "(persons.P_TAET.isin([1, 2, 3, 4, 6, 8]))&(persons.HP_ALTER>15)", True),
    ControlGroup("trip_class", "persons", r"^trip_class_", None, True),
    ControlGroup("employment_status", "persons", r"^employment_status_", _AGE14, True),
    ControlGroup("pt_ticket_group", "persons", r"^pt_ticket_group_", _AGE14, True),
    ControlGroup("pt_ticket_group4", "persons", r"^pt_ticket_group4_", _AGE14, True),
    ControlGroup("work_participation", "persons", r"^work_participation_", None, True),
    ControlGroup("leisure_participation", "persons", r"^leisure_participation_", None, True),
    ControlGroup("escort_participation", "persons", r"^escort_participation_", None, True),
    ControlGroup("work_by_employment", "persons", r"^work_by_employment_", _AGE14, True),
    # Two partitions: the all-ages participation flag (yes/no) and the education-by-age family.
    # The family's controls carry their age range in their own expression, so together they cover
    # every person once (universe None): the bands 0-5 and 6-17, and for the adults either the band
    # 18+ or, when the student education entries replace it (A3, ADR-0211), the entries for 18-29
    # (student with / without an education leg, other with / without) and for 30+ (student, other
    # with / without). Both adult variants at once count every adult twice, which the check reports.
    ControlGroup("education_participation", "persons", r"^education_participation_", None, True),
    ControlGroup("education_band", "persons", r"^(education_\d|student_education_)", None, True),
    # The two soft student entries (A3, ADR-0211): each partitions the persons aged 18 to 29.
    ControlGroup("student_work_leg", "persons", r"^student_work_leg_", _AGE18_29, True),
    ControlGroup("student_season_ticket", "persons", r"^student_season_ticket_", _AGE18_29, True),
    ControlGroup("schulabschluss", "persons", r"^schulabschluss_", _AGE15, False),
    ControlGroup("beruflabschluss", "persons", r"^beruflabschluss_", _AGE15, False),
    ControlGroup("employed", "persons", r"^employed_", None, False),
    ControlGroup("household_size", "households", r"^\d_Person", None, True),
    ControlGroup("household_type", "households", r"Typ_priv_HH_Familie",
                 "(households.H_GR >= 2)", True),
    # The 1 km minor-child household controls (#659): households without a minor match
    # neither class, so the group is not exhaustive.
    ControlGroup("household_minor", "households", r"^HH_MINOR_", None, False),
    ControlGroup("tenure", "households", r"_Tenure_", None, True),
    ControlGroup("building_type", "households", r"^building_type_", None, True),
    ControlGroup("own_cars", "households", r"^OWN_CARS_", None, True),
    ControlGroup("own_bikes", "households", r"^OWN_BIKES_", None, True),
    ControlGroup("economic_status", "households", r"^economic_status_", None, True),
    ControlGroup("number_of_cars", "households", r"^number_of_cars_", None, True),
    ControlGroup("number_of_bicycles", "households", r"^number_of_bicycles_", None, True),
    ControlGroup("has_ebike", "households", r"^has_ebike_", None, True),
)


def _total_expression(control_spec: pd.DataFrame, total_hh_control: str) -> str:
    rows = control_spec.loc[control_spec["target"] == total_hh_control, "expression"]
    if rows.empty:
        raise ValueError(f"household-total control {total_hh_control!r} is not in the "
                         "control specification")
    return rows.iloc[0]


def _grouped_candidates(control_spec: pd.DataFrame, total_hh_control: str) -> pd.DataFrame:
    """Controls that may belong to a group: everything but household-total controls."""
    total_expression = _total_expression(control_spec, total_hh_control)
    is_total = ((control_spec["seed_table"] == "households")
                & (control_spec["expression"] == total_expression))
    return control_spec[~is_total]


def group_targets(group: ControlGroup, control_spec: pd.DataFrame,
                  total_hh_control: str) -> list:
    """Targets of the group's seed table whose name matches the group pattern.

    Household-total controls (same expression as the ``total_hh_control`` row) are
    never part of a group.
    """
    spec = _grouped_candidates(control_spec, total_hh_control)
    spec = spec[spec["seed_table"] == group.seed_table]
    pattern = re.compile(group.target_pattern)
    return [t for t in spec["target"] if pattern.search(t)]


def seed_group_hits(group: ControlGroup, control_spec: pd.DataFrame, frame: pd.DataFrame,
                    total_hh_control: str) -> tuple:
    """Per seed record: how many of the group's controls count it, and the universe mask.

    Returns ``(hits, in_universe)`` aligned to ``frame`` (``hits`` int64, ``in_universe``
    bool).  ``frame`` is the group's seed table.  A group without a control gives all-zero
    hits; callers decide whether to skip it.
    """
    expressions = dict(zip(control_spec["target"], control_spec["expression"]))

    def evaluate(expression: str, what: str) -> np.ndarray:
        # A column the seed table lacks is a wiring defect, not a coverage finding: name it.
        try:
            return evaluate_expression(expression, frame)
        except (AttributeError, KeyError) as error:
            raise ValueError(
                f"control group {group.name!r} (seed table {group.seed_table!r}): {what} "
                f"{expression!r} references a column the seed table lacks "
                f"({type(error).__name__}: {error}); seed columns: {sorted(frame.columns)[:30]}"
            ) from error

    hits = np.zeros(len(frame), dtype=np.int64)
    for target in group_targets(group, control_spec, total_hh_control):
        hits += evaluate(expressions[target], f"control {target!r} expression").astype(np.int64)
    if group.universe is None:
        in_universe = np.ones(len(frame), dtype=bool)
    else:
        in_universe = evaluate(group.universe, "universe")
    return hits, in_universe


def unmatched_controls(control_spec: pd.DataFrame, total_hh_control: str,
                       groups=CONTROL_GROUPS) -> list:
    """Targets that belong to no group or to more than one group."""
    spec = _grouped_candidates(control_spec, total_hh_control)
    hits = {t: 0 for t in spec["target"]}
    for group in groups:
        for target in group_targets(group, control_spec, total_hh_control):
            hits[target] += 1
    return [t for t, n in hits.items() if n != 1]
