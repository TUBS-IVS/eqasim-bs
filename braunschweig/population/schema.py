"""Harmonised output-schema contract for the three population workflows.

All three ``population.method`` workflows must produce a persons frame (and the
household projection derived from it) that satisfies a shared **superset**
contract:

- **Required columns** -- present in EVERY workflow's output. These are the
  structural keys, the core demographics, the simulation-critical attributes
  that the MATSim / eqasim mode choice reads (household income, car / bicycle
  availability, licence, PT subscription), plus the MiD-gap columns and popsim
  provenance IDs that ``build_persons`` emits (Tasks 1-5). Any workflow that
  does not have a native source for a gap column must supply an open
  proxy/default rather than omit it (user decision 2026-06-08).
- **Optional columns** -- the MiD-rich extras (numeric income in EUR, economic
  status, housing tenure, vehicle counts, fleet attributes). The
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

Schema coupling to validate_person_columns usage
-------------------------------------------------
As of 2026-06-09 ``validate_person_columns`` is called **exclusively** from
``braunschweig.popsim.assembly.build_persons`` (the popsim workflow). The
default ``simple_ipf_open`` pipeline does NOT invoke it. Therefore the
popsim-specific provenance IDs (``source_person_id``, ``source_household_id``)
can safely live here as required columns without breaking the default pipeline.
If a future workflow also adopts this validator it must emit those columns (or
supply explicit defaults).
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

# Gap columns added by the popsim workflow (build_persons, Tasks 1-5) that
# capture MiD-survey attributes needed for SimWrapper dashboards and downstream
# validation.  Every workflow must emit these; open workflows supply
# proxy/default values so the schema stays uniform.
GAP_COLUMNS: tuple[str, ...] = (
    "age_range",              # coarse MiD age band (string label, e.g. "30-39")
    "high_income",            # bool: household above the MiD high-income threshold
    "household_size",         # integer: number of persons in the household
    "is_urban_resident",      # bool: home inside the Braunschweig core city boundary
    "pt_subscription_type",   # categorical MiD P24.1 ticket type (e.g. "deutschlandticket")
    "socioprofessional_class", # eqasim SPC label (employed/student/inactive/retired/…)
)

# Provenance IDs written by build_persons so every synthetic person is
# traceable to the MiD survey respondent whose trips were used as the donor.
# These are popsim-specific; the validator is only called on the popsim path
# (see module docstring), so requiring them here does not affect the default
# simple_ipf_open pipeline.
PROVENANCE_COLUMNS: tuple[str, ...] = (
    "source_person_id",       # MiD P_ID of the donor person
    "source_household_id",    # MiD household key of the donor household
)

# The base required superset every workflow must produce.
REQUIRED_PERSON_COLUMNS: tuple[str, ...] = (
    STRUCTURAL_COLUMNS
    + CORE_DEMOGRAPHIC_COLUMNS
    + SIMULATION_CRITICAL_COLUMNS
    + GAP_COLUMNS
    + PROVENANCE_COLUMNS
)

# Required columns of the household projection.
REQUIRED_HOUSEHOLD_COLUMNS: tuple[str, ...] = (
    "household_id",
    "household_income",
    "car_availability",
    "bicycle_availability",
)

# MiD-rich extras: populated where the data allows, optional in open workflows.
# Note: high_income and pt_subscription_type have been promoted to GAP_COLUMNS
# (required) and are therefore intentionally absent from this optional list.
# employment_status (P9 taxonomy, MiD P_BKAT) is popsim_mid-only -- like
# economic_status / housing_tenure it is NOT produced by the ENTD donor path
# (braunschweig.popsim.sources.entd.EntdSource.map_person_attributes has no P_BKAT
# equivalent), so it belongs here and NOT in GAP_COLUMNS: build_persons' schema
# validation (validate_person_columns) runs unconditionally for both the MiD and
# ENTD popsim sources, and promoting it to required would raise
# PopulationSchemaError on every ENTD/popsim_open run.
OPTIONAL_PERSON_COLUMNS: tuple[str, ...] = (
    "household_income_eur",
    "economic_status",
    "license_type",
    "housing_tenure",
    "number_of_cars",
    "number_of_bicycles",
    "employment_status",
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
