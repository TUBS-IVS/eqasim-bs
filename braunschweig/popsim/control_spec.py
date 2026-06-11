"""Declarative, typed specification of PopulationSim controls.

PopulationSim drives its synthesis from a hand-edited ``controls.csv`` with the
columns ``target, geography, seed_table, importance, control_field, expression``.
A control's ``expression`` is a pandas-style boolean string evaluated over one
seed table, e.g. ``"(persons.HP_ALTER < 10) & (persons.HP_SEX == 1)"`` selects
males aged 0-9 and ``"(households.H_GEW > 0)"`` selects every household for the
household total.

This module replaces the hand-edited CSV with a typed, reproducible
representation: a frozen :class:`ControlDef` record, a :func:`default_zensus_controls`
factory that builds the standard Zensus control set (household / population
totals, nine ten-year age bands x sex, and male/female totals) per geography, a
:func:`render_controls_csv` renderer to the exact PopulationSim column layout,
and a fail-fast :func:`validate_controls` check that refuses controls with a
blank expression (the notebook rule: only controls that carry an expression are
usable -- they must never be silently dropped).

Assumptions
-----------
- The age column holds integer years and the sex column holds two coded values
  (``male_value`` / ``female_value``); both are configurable so the same factory
  works for the persons seed table whatever the Zensus coding is.
- Age bands are ten-year bands with an open lower edge on the first band
  (``< 10``) and an open upper edge on the last band (``>= 80``); the eight
  interior bands are half-open ranges ``>= lo & < hi``.
- The module is pure: it performs no I/O and reads no real data.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Sequence

import pandas as pd

# The exact PopulationSim controls.csv column order. Renderers must preserve it.
CONTROLS_CSV_COLUMNS: Sequence[str] = (
    "target",
    "geography",
    "seed_table",
    "importance",
    "control_field",
    "expression",
)

# Valid seed tables a control may reference.
SEED_TABLE_HOUSEHOLDS = "households"
SEED_TABLE_PERSONS = "persons"

# Nine ten-year age bands as (label, lower_bound, upper_bound). ``None`` marks an
# open edge: the first band has no lower bound, the last band no upper bound.
AGE_BANDS: Sequence[tuple] = (
    ("0_9", None, 10),
    ("10_19", 10, 20),
    ("20_29", 20, 30),
    ("30_39", 30, 40),
    ("40_49", 40, 50),
    ("50_59", 50, 60),
    ("60_69", 60, 70),
    ("70_79", 70, 80),
    ("80_plus", 80, None),
)


@dataclass(frozen=True)
class ControlDef:
    """A single PopulationSim control marginal.

    Attributes
    ----------
    name:
        Human-readable control identifier; used for both the ``target`` and
        ``control_field`` CSV columns (PopulationSim keys the seed-table
        aggregate column by ``control_field`` and the geography target by
        ``target``; here they are the same stable name).
    geography:
        The control geography level (e.g. ``"ZENSUS100m"`` / ``"ZENSUS1km"``).
    seed_table:
        The seed table the expression is evaluated over: ``"households"`` or
        ``"persons"``.
    importance:
        PopulationSim importance weight (higher = harder constraint).
    control_field:
        The aggregated control column name on the seed table.
    expression:
        Pandas-style boolean expression selecting the rows this control counts.
    """

    name: str
    geography: str
    seed_table: str
    importance: int
    control_field: str
    expression: str


def _age_expression(age_col: str, lower: int | None, upper: int | None) -> str:
    """Build the age-range clause(s) for one band.

    Open lower edge -> ``(persons.<age> < upper)``; open upper edge ->
    ``(persons.<age> >= lower)``; interior band -> both clauses joined by ``&``.
    """
    clauses: List[str] = []
    if lower is not None:
        clauses.append(f"(persons.{age_col} >= {lower})")
    if upper is not None:
        clauses.append(f"(persons.{age_col} < {upper})")
    if not clauses:
        raise ValueError("An age band must have at least one bound (got none).")
    return " & ".join(clauses)


def default_zensus_controls(
    geographies: Sequence[str] = ("ZENSUS100m", "ZENSUS1km"),
    *,
    age_col: str = "HP_ALTER",
    sex_col: str = "HP_SEX",
    male_value: int = 1,
    female_value: int = 2,
    hh_weight_col: str = "H_GEW",
    person_weight_col: str = "P_GEW",
    importance: int = 1000,
) -> List[ControlDef]:
    """Build the standard Zensus control set for each requested geography.

    For every geography this produces 22 controls:

    - 1 household total (``households`` table, ``<hh_weight_col> > 0``),
    - 1 population total (``persons`` table, ``<person_weight_col> > 0``),
    - 18 age x sex controls (9 ten-year age bands x {male, female}),
    - 1 male total and 1 female total (``persons`` table, sex only).

    Parameters
    ----------
    geographies:
        Geography level names to emit controls for (one block each).
    age_col, sex_col:
        Seed-table column names for age (integer years) and sex (coded value).
    male_value, female_value:
        The sex codes used in the persons seed table.
    hh_weight_col, person_weight_col:
        Weight columns whose ``> 0`` selects every household / person for the
        respective total.
    importance:
        PopulationSim importance assigned to every control in the set.

    Returns
    -------
    list[ControlDef]
        ``22 * len(geographies)`` controls (44 for the two default geographies).
    """
    sex_codes = {"male": male_value, "female": female_value}
    controls: List[ControlDef] = []

    for geography in geographies:
        # Household total: one row per household, selected by a positive weight.
        controls.append(
            ControlDef(
                name="total_households",
                geography=geography,
                seed_table=SEED_TABLE_HOUSEHOLDS,
                importance=importance,
                control_field="total_households",
                expression=f"(households.{hh_weight_col} > 0)",
            )
        )
        # Population total: one row per person, selected by a positive weight.
        controls.append(
            ControlDef(
                name="total_population",
                geography=geography,
                seed_table=SEED_TABLE_PERSONS,
                importance=importance,
                control_field="total_population",
                expression=f"(persons.{person_weight_col} > 0)",
            )
        )

        # 18 age x sex controls.
        for band_label, lower, upper in AGE_BANDS:
            age_clause = _age_expression(age_col, lower, upper)
            for sex_label, sex_value in sex_codes.items():
                name = f"age_{band_label}_{sex_label}"
                expression = f"{age_clause} & (persons.{sex_col} == {sex_value})"
                controls.append(
                    ControlDef(
                        name=name,
                        geography=geography,
                        seed_table=SEED_TABLE_PERSONS,
                        importance=importance,
                        control_field=name,
                        expression=expression,
                    )
                )

        # Male and female totals (sex only).
        for sex_label, sex_value in sex_codes.items():
            name = f"total_{sex_label}"
            controls.append(
                ControlDef(
                    name=name,
                    geography=geography,
                    seed_table=SEED_TABLE_PERSONS,
                    importance=importance,
                    control_field=name,
                    expression=f"(persons.{sex_col} == {sex_value})",
                )
            )

    return controls


def render_controls_csv(controls: Iterable[ControlDef]) -> pd.DataFrame:
    """Render controls to the PopulationSim ``controls.csv`` table.

    The control ``name`` is written to BOTH the ``target`` and ``control_field``
    columns (PopulationSim keys the geography target by ``target`` and the
    seed-table aggregate column by ``control_field``; here they share one stable
    name). Columns are emitted in the exact PopulationSim order.

    Parameters
    ----------
    controls:
        Controls to render (any iterable).

    Returns
    -------
    pandas.DataFrame
        One row per control with columns
        ``[target, geography, seed_table, importance, control_field, expression]``.
    """
    rows = [
        {
            "target": control.name,
            "geography": control.geography,
            "seed_table": control.seed_table,
            "importance": control.importance,
            "control_field": control.name,
            "expression": control.expression,
        }
        for control in controls
    ]
    return pd.DataFrame(rows, columns=list(CONTROLS_CSV_COLUMNS))


def validate_controls(controls: Iterable[ControlDef]) -> List[ControlDef]:
    """Validate that every control carries a non-blank expression.

    Only controls with an expression are usable by PopulationSim. A blank or
    whitespace-only expression is a configuration error and must fail loudly
    rather than be silently dropped (no silent fallback).

    Parameters
    ----------
    controls:
        Controls to validate.

    Returns
    -------
    list[ControlDef]
        The validated controls (returned for chaining convenience).

    Raises
    ------
    ValueError
        If any control has an empty or whitespace-only expression. The message
        names the offending control(s) so the source can be fixed.
    """
    materialized = list(controls)
    offenders = [
        control.name
        for control in materialized
        if control.expression is None or not control.expression.strip()
    ]
    if offenders:
        raise ValueError(
            "PopulationSim controls must carry a non-blank expression; the "
            f"following control(s) have a blank expression: {sorted(offenders)}."
        )
    return materialized
