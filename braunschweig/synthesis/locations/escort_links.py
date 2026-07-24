"""Household escort links: anchor escort activities at a child's school (issue #201).

An escorter (a synthetic person with at least one plan-level ``escort``
activity) is LINKED to the education location of the youngest OTHER member of
the same household that (a) has a realised education assignment and (b) is at
most ``max_child_age_years`` old. Linked escorters' escort activities are
anchored at that location by the secondary chainsolver stage (they stop being
free location-choice activities); unlinked escorters keep the SrV-weighted
location-type draw. The link rate is logged -- an escorter escorting a non-
household person (about half of MiD escort legs) is EXPECTED to stay unlinked.

ASSUMPTION (documented in the spec): one link location per escorter (all of a
person's escort activities anchor at the same school -- bring + fetch
consistency); the youngest child is chosen (Kita/Grundschule trips dominate the
observed SrV escort destinations); ties break on the lowest person_id for
determinism.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

DEFAULT_MAX_CHILD_AGE_YEARS = 17

_REQUIRED_PERSON_COLUMNS = ("person_id", "household_id", "HP_ALTER")


def build_escort_links(df_persons: pd.DataFrame,
                       df_education_assignments: pd.DataFrame,
                       df_trips: pd.DataFrame,
                       *, max_child_age_years: int = DEFAULT_MAX_CHILD_AGE_YEARS
                       ) -> tuple[pd.DataFrame, dict]:
    """One (escorter person_id -> child's education location) row per linkable escorter.

    Returns ``(links, stats)`` with ``links`` columns
    ``[person_id, location_id, geometry]`` and ``stats`` keys
    ``n_escorters`` / ``n_linked`` / ``link_rate``.
    """
    missing = [c for c in _REQUIRED_PERSON_COLUMNS if c not in df_persons.columns]
    if missing:
        raise ValueError(
            f"[escort_links] persons frame is missing required column(s) {missing}; "
            "the household link needs household_id and HP_ALTER."
        )

    escorter_ids = pd.Index(
        df_trips.loc[df_trips["following_purpose"] == "escort", "person_id"].unique()
    )
    n_escorters = int(len(escorter_ids))
    if n_escorters == 0:
        logger.info("[escort_links] no escort trips found; link table is empty.")
        return (
            pd.DataFrame(columns=["person_id", "location_id", "geometry"]),
            {"n_escorters": 0, "n_linked": 0, "link_rate": float("nan")},
        )

    persons = df_persons[list(_REQUIRED_PERSON_COLUMNS)].copy()

    children = persons[persons["HP_ALTER"] <= int(max_child_age_years)].merge(
        df_education_assignments[["person_id", "location_id", "geometry"]],
        on="person_id", how="inner",
    )
    # Youngest first, deterministic tie-break on person_id.
    children = children.sort_values(["household_id", "HP_ALTER", "person_id"])

    escorters = persons[persons["person_id"].isin(escorter_ids)]
    merged = escorters.merge(
        children, on="household_id", how="inner", suffixes=("", "_child"),
    )
    merged = merged[merged["person_id"] != merged["person_id_child"]]
    # First row per escorter = youngest linkable child (sort order above is
    # preserved by the merge within each escorter's household block).
    merged = merged.sort_values(["person_id", "HP_ALTER_child", "person_id_child"])
    links = merged.drop_duplicates(subset=["person_id"])[
        ["person_id", "location_id", "geometry"]
    ].reset_index(drop=True)

    n_linked = int(len(links))
    link_rate = n_linked / n_escorters
    logger.info(
        "[escort_links] household link: %d/%d escorters linked (%.1f%%) to a "
        "child's education location (max_child_age_years=%d); unlinked escorters "
        "use the SrV-weighted location-type draw.",
        n_linked, n_escorters, 100.0 * link_rate, int(max_child_age_years),
    )
    return links, {
        "n_escorters": n_escorters,
        "n_linked": n_linked,
        "link_rate": link_rate,
    }
