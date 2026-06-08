"""Harmonised output-schema contract for the three population workflows.

All three ``population.method`` workflows must produce a persons frame (and the
household projection derived from it) that satisfies a shared **superset**
contract:

- **Required columns** -- present in EVERY workflow's output. These are the
  structural keys, the core demographics, and the simulation-critical attributes
  that the MATSim / eqasim mode choice reads (household income, car / bicycle
  availability, licence, PT subscription). An open workflow that lacks a native
  source for one of these must supply an open proxy/default rather than omit it
  (user decision 2026-06-08: "if we have hh-income mode choice we need it across
  all").
- **Optional columns** -- the MiD-rich extras (numeric income in EUR, ticket
  type, economic status, housing tenure, vehicle counts, fleet attributes). The
  ``popsim_mid`` / IPF workflows populate as many of these as the data allows;
  the open workflows may omit them. Downstream writers already treat these as
  additive (written only when the column is present and non-empty).

This module defines the contract as data + a validator. It deliberately does NOT
re-implement the writers' field lists; it expresses the *minimum* every workflow
must guarantee so the workflows stay interchangeable behind ``population.method``.

Feature coupling
----------------
Some optional columns become *required* when a MATSim feature that consumes them
is enabled (e.g. ``household_income_eur`` under income-elastic mode choice). Pass
the relevant feature names to :func:`required_person_columns` to promote them;
the base required set is the eqasim mode-choice input set that the pipeline
always writes today.
"""

from __future__ import annotations

from typing import Iterable, Sequence

# Structural keys that tie persons to households and to themselves.
STRUCTURAL_COLUMNS: tuple[str, ...] = ("person_id", "household_id")

# Core demographics, present in every workflow.
CORE_DEMOGRAPHIC_COLUMNS: tuple[str, ...] = ("age", "sex")

# Simulation-critical attributes read by the eqasim / MATSim mode choice. The
# current pipeline always writes these (eqasim base PERSON_FIELDS), so every
# workflow must guarantee them for the simulation to run identically.
SIMULATION_CRITICAL_COLUMNS: tuple[str, ...] = (
    "employed",
    "household_income",       # categorical income class (mode-choice input)
    "car_availability",       # {none, some, all}
    "bicycle_availability",   # {none, all}
    "has_license",
    "has_pt_subscription",
)

# The base required superset every workflow must produce.
REQUIRED_PERSON_COLUMNS: tuple[str, ...] = (
    STRUCTURAL_COLUMNS + CORE_DEMOGRAPHIC_COLUMNS + SIMULATION_CRITICAL_COLUMNS
)

# Required columns of the household projection.
REQUIRED_HOUSEHOLD_COLUMNS: tuple[str, ...] = (
    "household_id",
    "household_income",
    "car_availability",
    "bicycle_availability",
)

# MiD-rich extras: populated where the data allows, optional in open workflows.
OPTIONAL_PERSON_COLUMNS: tuple[str, ...] = (
    "household_income_eur",
    "high_income",
    "economic_status",
    "pt_subscription_type",
    "license_type",
    "housing_tenure",
    "number_of_cars",
    "number_of_bicycles",
)

# Optional columns that MUST be promoted to required when a consuming MATSim
# feature is enabled. Keyed by a feature name passed to required_person_columns().
FEATURE_REQUIRED_COLUMNS: dict[str, tuple[str, ...]] = {
    # Income-elastic mode choice (model-realism A1) reads numeric EUR income.
    "income_elastic_mode_choice": ("household_income_eur",),
}


class PopulationSchemaError(ValueError):
    """Raised when a workflow's output frame violates the shared schema contract."""


def required_person_columns(enabled_features: Iterable[str] = ()) -> tuple[str, ...]:
    """Return the required person columns, promoting feature-coupled extras.

    Parameters
    ----------
    enabled_features:
        Names of enabled MATSim features whose inputs must exist across all
        workflows (keys of :data:`FEATURE_REQUIRED_COLUMNS`).
    """
    required = list(REQUIRED_PERSON_COLUMNS)
    for feature in enabled_features:
        for column in FEATURE_REQUIRED_COLUMNS.get(feature, ()):  # unknown -> no-op
            if column not in required:
                required.append(column)
    return tuple(required)


def _missing(columns: Sequence[str], present: Iterable[str]) -> list[str]:
    present_set = set(present)
    return [c for c in columns if c not in present_set]


def validate_person_columns(
    columns: Iterable[str],
    *,
    enabled_features: Iterable[str] = (),
) -> None:
    """Validate that a persons frame exposes the required columns; fail-fast.

    Parameters
    ----------
    columns:
        The column names of the produced persons frame.
    enabled_features:
        Enabled MATSim features (see :func:`required_person_columns`).

    Raises
    ------
    PopulationSchemaError
        If any required column is missing.
    """
    required = required_person_columns(enabled_features)
    missing = _missing(required, columns)
    if missing:
        raise PopulationSchemaError(
            "Population persons frame is missing required columns "
            f"{missing}. Every population.method must produce the shared schema "
            "(see docs/population/DATA_LAYOUT.md and braunschweig.population.schema)."
        )


def validate_household_columns(columns: Iterable[str]) -> None:
    """Validate that a household frame exposes the required columns; fail-fast."""
    missing = _missing(REQUIRED_HOUSEHOLD_COLUMNS, columns)
    if missing:
        raise PopulationSchemaError(
            f"Population household frame is missing required columns {missing}."
        )
