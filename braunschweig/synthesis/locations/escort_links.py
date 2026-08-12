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


def assign_escort_anchors(df_trips: pd.DataFrame,
                          df_links: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Per-activity escort anchors under the CONSECUTIVE-RUN rule (issue #201
    multi-child fix; replaces the one-link-per-escorter assumption).

    Escort activities of each LINKED escorter are enumerated from BOTH trip
    sides (destination of trips with ``following_purpose == "escort"`` ->
    ``trip_index + 1``; origin of trips with ``preceding_purpose == "escort"``
    -> ``trip_index``; deduplicated). Covering only destinations is not
    sufficient: donor chains contain rare inconsistencies (measured 0.027% of
    links) where ``trip[i].following_purpose != trip[i+1].preceding_purpose``;
    an escort activity on the origin side of such a break would otherwise stay
    unanchored and end up without geometry (5% run regression of 2026-08-10).

    Maximal blocks of CONSECUTIVE activity indices form a run (a multi-drop
    chain: home -> school -> school -> work). Within a run, position p anchors
    at the escorter's child of ``child_rank`` p (youngest first); positions
    beyond the household's linkable children are NOT anchored and fall back to
    the SrV-weighted location draw (counted + logged -- never cycled back to
    child 0, which would recreate the zero-distance artifact for one-child
    households). Separate runs (bring ... fetch) restart at rank 0, so both
    halves of a bring/fetch pair anchor at the same children in the same order
    (ASSUMPTION: multi-drop chains visit children youngest-first; the surveys
    do not observe the within-chain child order).

    Returns ``(df_anchors, stats)``: anchors columns
    ``[person_id, activity_index, location_id, geometry]`` sorted by
    (person_id, activity_index); stats keys ``n_escort_activities`` /
    ``n_anchored`` / ``n_overflow_to_draw`` / ``n_runs`` (linked persons only).
    """
    anchor_columns = ["person_id", "activity_index", "location_id", "geometry"]
    empty_stats = {
        "n_escort_activities": 0, "n_anchored": 0,
        "n_overflow_to_draw": 0, "n_runs": 0,
    }
    if len(df_links) == 0:
        return pd.DataFrame(columns=anchor_columns), dict(empty_stats)

    trips = df_trips[df_trips["person_id"].isin(set(df_links["person_id"]))]

    destinations = trips.loc[
        trips["following_purpose"] == "escort", ["person_id", "trip_index"]
    ].copy()
    destinations["activity_index"] = destinations["trip_index"] + 1
    origins = trips.loc[
        trips["preceding_purpose"] == "escort", ["person_id", "trip_index"]
    ].copy()
    origins["activity_index"] = origins["trip_index"]

    activities = pd.concat(
        [destinations[["person_id", "activity_index"]],
         origins[["person_id", "activity_index"]]],
        ignore_index=True,
    ).drop_duplicates().sort_values(["person_id", "activity_index"]).reset_index(drop=True)

    if len(activities) == 0:
        return pd.DataFrame(columns=anchor_columns), dict(empty_stats)

    # Consecutive activity indices of one person = one multi-drop run.
    same_person = activities["person_id"].eq(activities["person_id"].shift())
    consecutive = activities["activity_index"].eq(activities["activity_index"].shift() + 1)
    activities["run_id"] = (~(same_person & consecutive)).cumsum()
    activities["run_position"] = activities.groupby("run_id").cumcount()

    merged = activities.merge(
        df_links.rename(columns={"child_rank": "run_position"}),
        on=["person_id", "run_position"], how="left", indicator=True,
    )
    anchored_mask = merged["_merge"] == "both"
    df_anchors = (
        merged.loc[anchored_mask, anchor_columns]
        .sort_values(["person_id", "activity_index"])
        .reset_index(drop=True)
    )

    stats = {
        "n_escort_activities": int(len(activities)),
        "n_anchored": int(anchored_mask.sum()),
        "n_overflow_to_draw": int(len(activities) - anchored_mask.sum()),
        "n_runs": int(activities["run_id"].nunique()),
    }
    logger.info(
        "[escort_links] anchor assignment: %d/%d escort activities anchored "
        "(%.1f%%) across %d runs; %d beyond the household's linkable children "
        "-> SrV-weighted draw (%.1f%%).",
        stats["n_anchored"], stats["n_escort_activities"],
        100.0 * stats["n_anchored"] / stats["n_escort_activities"],
        stats["n_runs"], stats["n_overflow_to_draw"],
        100.0 * stats["n_overflow_to_draw"] / stats["n_escort_activities"],
    )
    return df_anchors, stats
