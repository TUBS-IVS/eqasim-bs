"""Groups of PopulationSim controls that partition one seed population.

A control group collects the controls whose categories are meant to be mutually
exclusive and (if ``exhaustive``) to cover every seed record of the group's universe.
The group table is the single owner of this classification; consumers are the
popsim batch diagnostics (``braunschweig.analysis.popsim_batch_diagnostics``).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd


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


_AGE14 = "(persons.HP_ALTER >= 14)"
_AGE15 = "(persons.HP_ALTER >= 15)"

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
    ControlGroup("education_flag", "persons", r"^education_", None, True),
    ControlGroup("schulabschluss", "persons", r"^schulabschluss_", _AGE15, False),
    ControlGroup("beruflabschluss", "persons", r"^beruflabschluss_", _AGE15, False),
    ControlGroup("employed", "persons", r"^employed_", None, False),
    ControlGroup("household_size", "households", r"^\d_Person", None, True),
    ControlGroup("household_type", "households", r"Typ_priv_HH_Familie",
                 "(households.H_GR >= 2)", True),
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


def unmatched_controls(control_spec: pd.DataFrame, total_hh_control: str,
                       groups=CONTROL_GROUPS) -> list:
    """Targets that belong to no group or to more than one group."""
    spec = _grouped_candidates(control_spec, total_hh_control)
    hits = {t: 0 for t in spec["target"]}
    for group in groups:
        for target in group_targets(group, control_spec, total_hh_control):
            hits[target] += 1
    return [t for t, n in hits.items() if n != 1]
