"""Joint (cross-attribute) plausibility checks for the synthetic population (issue #133).

Both validation registries (population-validation controls, MiD validation)
check univariate margins only. A population can therefore hit every marginal
distribution and still contain individually impossible records -- exactly the
bug class previously found only by hand (#96 field-width missing-code
collision, the couple/studies/SPC constants, consistent_car_availability).

The table below lists HARD LOGICAL INVARIANTS only (never rare-but-possible
combinations such as a 45-year-old student): each violation indicates a
regression in an attribute mapper or derivation, not survey noise. Violations
are counted and logged per check (rate over the evaluated rows); following the
measure-before-harden convention of the minor-employment guard (PR #102) the
default is WARN-only -- callers opt into raising via ``raise_above_rate``
once the expected clean-run baseline is confirmed.

The under-15 employed RATE keeps its own dedicated guard
(``controls.check_minor_employment``, configurable bound, CSV output); the
``employed_child`` check here is the strict per-record impossibility (age < 14
per the MiD interviewing basis) and complements, not replaces, that guard.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Optional

import pandas as pd

from braunschweig.popsim.attributes import PT_TICKET_NEVER

logger = logging.getLogger(__name__)

# Minimum age for a German Pkw driving licence (BF17 accompanied driving).
# MiD P17.1 is asked from the interviewing basis upward; a has_license=True
# below this age is a mapping/imputation regression, not a real respondent.
LICENSE_MINIMUM_AGE_YEARS = 16

# Below this age employment is impossible in the MiD P_TAET basis (issue #96
# was exactly an inflation of this group via a missing-code collision).
EMPLOYED_MINIMUM_AGE_YEARS = 14


@dataclass(frozen=True)
class JointCheck:
    """One hard cross-attribute invariant.

    ``columns`` are the frame columns the predicate needs; the check is
    SKIPPED (and reported as skipped) when any is absent, so the function
    works on every producer path regardless of which attributes it carries.
    ``predicate`` returns a boolean Series that is True WHERE THE RECORD
    VIOLATES the invariant.
    """
    name: str
    columns: tuple
    predicate: Callable[[pd.DataFrame], pd.Series]
    description: str


JOINT_CHECKS: tuple = (
    JointCheck(
        name="license_underage",
        columns=("has_license", "age"),
        predicate=lambda df: df["has_license"].astype(bool)
        & (df["age"] < LICENSE_MINIMUM_AGE_YEARS),
        description=(
            f"has_license=True below age {LICENSE_MINIMUM_AGE_YEARS} "
            "(below the German Pkw licence minimum incl. BF17)"
        ),
    ),
    JointCheck(
        name="employed_child",
        columns=("employed", "age"),
        predicate=lambda df: df["employed"].astype(bool)
        & (df["age"] < EMPLOYED_MINIMUM_AGE_YEARS),
        description=(
            f"employed=True below age {EMPLOYED_MINIMUM_AGE_YEARS} "
            "(impossible in the MiD P_TAET basis; #96 regression class)"
        ),
    ),
    JointCheck(
        name="car_availability_mismatch",
        columns=("number_of_cars", "car_availability"),
        predicate=lambda df: (
            (df["number_of_cars"].astype(int) == 0)
            != (df["car_availability"].astype(str) == "none")
        ),
        description=(
            "car_availability inconsistent with number_of_cars "
            "(none <-> 0 cars must hold by derivation)"
        ),
    ),
    JointCheck(
        name="couple_single",
        columns=("couple", "household_size"),
        predicate=lambda df: df["couple"].astype(bool)
        & (pd.to_numeric(df["household_size"], errors="coerce") == 1),
        description="couple=True in a single-person household",
    ),
    JointCheck(
        name="pt_never_contradiction",
        columns=("has_pt_subscription", "pt_subscription_type"),
        predicate=lambda df: df["has_pt_subscription"].astype(bool)
        & (df["pt_subscription_type"].astype(str) == PT_TICKET_NEVER),
        description=(
            f"has_pt_subscription=True with pt_subscription_type='{PT_TICKET_NEVER}' "
            "(both derive from P_FKARTE; contradiction = mapper regression)"
        ),
    ),
)


def check_joint_plausibility(
    persons: pd.DataFrame,
    *,
    raise_above_rate: Optional[float] = None,
) -> dict:
    """Run every applicable joint invariant and log counts/rates.

    Parameters
    ----------
    persons:
        Synthetic persons frame (any producer path; checks whose columns are
        absent are skipped and listed under ``skipped``).
    raise_above_rate:
        When set, raise :class:`ValueError` if ANY single check's violation
        rate (violations / evaluated rows) exceeds this bound. Default None =
        WARN-only (measure-before-harden: confirm the clean-run baseline
        before hardening, as with the minor-employment guard).

    Returns
    -------
    dict
        ``{"checks": {name: {n_violations, rate, description}},
        "skipped": [names], "n_violations_total": int, "n_rows": int}`` --
        also attached by the popsim stage to ``persons.attrs`` so it survives
        the synpp cache for downstream validation summaries.
    """
    n_rows = len(persons)
    results: dict = {}
    skipped: list = []
    total = 0

    for check in JOINT_CHECKS:
        missing = [c for c in check.columns if c not in persons.columns]
        if missing:
            skipped.append(check.name)
            logger.debug(
                "[popsim.plausibility] %s skipped (missing columns: %s).",
                check.name, missing,
            )
            continue
        violations = check.predicate(persons)
        n_violations = int(violations.sum())
        rate = n_violations / n_rows if n_rows else 0.0
        results[check.name] = {
            "n_violations": n_violations,
            "rate": rate,
            "description": check.description,
        }
        total += n_violations
        log = logger.warning if n_violations > 0 else logger.info
        log(
            "[popsim.plausibility] %s: %d/%d records (%.4f%%) violate -- %s",
            check.name, n_violations, n_rows, 100.0 * rate, check.description,
        )

    report = {
        "checks": results,
        "skipped": skipped,
        "n_violations_total": total,
        "n_rows": n_rows,
    }

    if raise_above_rate is not None:
        offenders = {
            name: r for name, r in results.items() if r["rate"] > raise_above_rate
        }
        if offenders:
            raise ValueError(
                "[popsim.plausibility] joint plausibility violated above the "
                f"configured bound {raise_above_rate}: "
                + ", ".join(
                    f"{name} rate={r['rate']:.4f} ({r['n_violations']} records)"
                    for name, r in offenders.items()
                )
                + ". A joint invariant violation indicates an attribute-mapper "
                "or derivation regression -- inspect the offending records."
            )

    return report
