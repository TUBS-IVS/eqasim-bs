"""Expand popsim donor households into eqasim persons.

The PopulationSim output (``braunschweig.popsim.merge``) is one row per synthetic
household, referencing a MiD donor household ``H_ID`` and its 100 m cell. To
become an eqasim population each synthetic household must be (a) given a stable
unique identity -- the same donor recurs within a cell, so the identity is
``(cell, H_ID, occurrence)`` -- and (b) expanded to its donor persons, which carry
the MiD attributes mapped to the eqasim schema.

This module does the demographic core (identity, person expansion, age/sex). The
richer attribute mapping (income, car/bike availability, licence, PT) and the
home-location + activity-chain assignment build on top of it (see
``braunschweig.popsim.handoff`` and the harmonisation plan in docs/population).
"""

from __future__ import annotations

import pandas as pd

from braunschweig.popsim.fallback_rates import log_fallback_rate
from braunschweig.popsim.missing import impute_binary_sex

# MiD sex codes (HP_SEX): 1 = male, 2 = female; others (3 "diverse", 9 "no answer").
_SEX_LABELS = {1: "male", 2: "female"}
_SEX_RAW_LABELS = {1: "male", 2: "female", 3: "diverse", 9: "not_specified"}


def assign_synthetic_household_ids(
    merged_households: pd.DataFrame,
    *,
    cell_col: str = "ZENSUS100m",
    donor_col: str = "H_ID",
) -> pd.DataFrame:
    """Add a stable, unique ``household_id`` (and ``hh_occurrence``) per row.

    The same donor ``H_ID`` may be placed several times in one cell; the
    occurrence counter disambiguates them, and the household id is
    ``<cell>_<H_ID>_<occurrence>`` (traceable, matching the popsimprep notebook).

    Parameters
    ----------
    merged_households:
        Merged PopulationSim output (one row per synthetic household).

    Returns
    -------
    pandas.DataFrame
        A copy with ``hh_occurrence`` and a unique string ``household_id``.
    """
    df = merged_households.copy()
    df["hh_occurrence"] = df.groupby([cell_col, donor_col]).cumcount()
    df["household_id"] = (
        df[cell_col].astype(str)
        + "_" + df[donor_col].astype(str)
        + "_" + df["hh_occurrence"].astype(str)
    )
    return df


def expand_to_persons(
    households_with_ids: pd.DataFrame,
    mid_persons: pd.DataFrame,
    *,
    donor_col: str = "H_ID",
    person_donor_col: str = "H_ID",
) -> pd.DataFrame:
    """Expand each synthetic household to one row per MiD donor person.

    Joins the MiD persons of the donor household onto every synthetic household
    (so a donor placed N times yields N copies of its persons) and assigns a
    unique ``person_id`` (``<household_id>_<P_ID>`` when ``P_ID`` is present, else
    a sequential index).

    Parameters
    ----------
    households_with_ids:
        Output of :func:`assign_synthetic_household_ids`.
    mid_persons:
        MiD persons keyed by the donor household column.

    Returns
    -------
    pandas.DataFrame
        One row per (synthetic household, donor person), with ``household_id``,
        ``person_id`` and the joined MiD person columns.
    """
    persons = households_with_ids.merge(
        mid_persons, left_on=donor_col, right_on=person_donor_col,
        how="left", suffixes=("", "_person"),
    )
    # The donor persons file is complete by construction; a synthetic household
    # whose donor finds no persons would silently become a NaN ghost row, so a
    # miss means dtype drift or corruption and must fail loudly.
    matched_key = "P_ID" if "P_ID" in persons.columns else person_donor_col
    unmatched = persons[persons[matched_key].isna()]
    if len(unmatched):
        bad = list(unmatched[donor_col].unique()[:10])
        raise ValueError(
            f"[popsim.expand] {unmatched[donor_col].nunique()} synthetic households "
            f"found no donor persons (e.g. {donor_col} {bad}). The donor persons file "
            f"must cover every donor household; a miss means dtype drift or corruption."
        )
    if "P_ID" in persons.columns:
        persons["person_id"] = (
            persons["household_id"].astype(str) + "_" + persons["P_ID"].astype(str)
        )
    else:
        persons["person_id"] = persons.index.astype(str)
    return persons.reset_index(drop=True)


def map_demographics(persons, *, age_col="HP_ALTER", sex_col="HP_SEX", rng=None):
    """Map MiD demographics. ``sex`` is binary male/female (MATSim requires binary;
    HP_SEX codes 3 'diverse' / 9 'keine Angabe' are imputed from the valid pool
    within the same alter_gr1 band, seeded). The original category is retained in
    ``sex_raw`` (male/female/diverse/not_specified) for analysis but is NOT written
    to the MATSim population.

    Imputation tiers, each counted and logged: observed binary sex (primary), then the
    same-band pool, then the global pool, then the constant "male" (last resort, logged
    with its own rate). Persons whose ``alter_gr1`` is NaN have no band pool and draw
    from the global pool after all band draws, so every imputed person lands in exactly
    one tier (checked; a broken count raises).
    """
    import logging
    import numpy as np
    rng = rng if rng is not None else np.random.RandomState(0)
    out = persons.copy()
    out["age"] = out[age_col]
    # A donor person already resolved once before PopulationSim (attributes.resolve_binary_sex)
    # keeps its original code in HP_SEX_raw; sex_col then holds the resolved binary code.
    raw_col = f"{sex_col}_raw"
    sex_raw_source = out[raw_col] if raw_col in out.columns else out[sex_col]
    out["sex_raw"] = sex_raw_source.map(_SEX_RAW_LABELS).fillna("not_specified")
    binary = out[sex_col].map(_SEX_LABELS)
    missing_mask = binary.isna()
    n_missing = int(missing_mask.sum())
    band = out["alter_gr1"] if "alter_gr1" in out.columns else None
    binary, n_band, n_global, n_constant = impute_binary_sex(
        binary, band, rng=rng, constant="male")
    out["sex"] = binary
    logger = logging.getLogger(__name__)
    n_total = len(out)
    log_fallback_rate(
        logger, "popsim.expand.sex", n_missing, n_total,
        what="persons without an observed binary sex (codes 3 diverse / 9 not specified), imputed",
    )
    if n_missing:
        n_unassigned = int(binary.isna().sum())
        # Every imputed person lands in exactly one tier; anything else is a broken invariant.
        if n_unassigned or n_band + n_global + n_constant != n_missing:
            raise RuntimeError(
                f"[popsim.expand.sex] imputation tiers do not add up: {n_missing} to impute, band "
                f"{n_band} + global {n_global} + constant {n_constant}, {n_unassigned} left without a sex")
        logger.info(
            "[popsim.expand.sex] imputation tiers for %d persons: band pool %d, global pool %d "
            "(incl. persons without alter_gr1), constant 'male' %d.",
            n_missing, n_band, n_global, n_constant,
        )
        log_fallback_rate(
            logger, "popsim.expand.sex_constant", n_constant, n_missing,
            what="imputed persons that got the constant 'male' because no pool existed",
        )
    logger.info(
        "[popsim.expand] sex: imputed %d non-binary/missing codes (3 diverse / 9 not specified) "
        "to binary male/female (seeded); sex_raw retains the original category.", n_missing)
    return out
