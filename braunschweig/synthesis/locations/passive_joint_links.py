"""Passive escort joint location: link the escorted child's joint activity to the
accompanying adult's activity (issue #385, ADR-0118; phase 2 of #372 / ADR-0112).

Phase 1 gave a PAIRED passive escort leg (MiD ``W_ZWECK`` 13, the escorted child's own
leg) the purpose of the same-household adult leg it travels with, and wrote the pairing
on the trips frame (``passive_pair_status``, ``passive_pair_adult_p_id``,
``passive_pair_adult_w_id`` -- ``braunschweig.popsim.escort_pairing``). The child's
LOCATION was still drawn independently. This module builds the link table the secondary
chainsolver needs to anchor the child's joint activity at the adult's PLACED location:

* the paired adult is the synthetic person in the SAME synthetic household whose plan
  source ``(source_H_ID, source_P_ID)`` is the child's donor household and the paired
  adult's ``P_ID`` -- so a member-completion filler (mirror plan source), a diary-plan
  remap of either side, or an adult removed by the day-absence model breaks the pair
  (counted as ``adult_not_in_household``);
* the adult's activity is the destination of the adult trip with the paired ``W_ID``
  (a spliced commute day or a dropped leg breaks it: ``adult_leg_missing``);
* both activities must carry a SECONDARY purpose (:data:`SECONDARY_JOINT_PURPOSES`).
  The adult's trip home (child already at the household home), the adult's own
  Bringen/Holen leg (child at its own school, handled by the #201 household link) and
  the adult's work/education leg (would need primary facility ids in the facilities
  coverage check; deferred, ADR-0118) are therefore excluded and counted
  (``purpose_not_secondary``).

This is the inverse of :mod:`braunschweig.synthesis.locations.escort_links` (#201),
which anchors the ADULT's escort activity at the CHILD's school. Every exclusion is
counted and logged as a rate (CLAUDE.md fallback transparency); the link rate measured
on a run is the answer to "how often does the donor pair survive synthesis".
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim.escort_pairing import STATUS_PAIRED

logger = logging.getLogger(__name__)

#: eqasim purposes the chainsolver places (the fixed purposes home/work/education and the
#: #201 boundary purpose escort_linked are NOT joint-anchorable).
SECONDARY_JOINT_PURPOSES = frozenset({"shop", "leisure", "other"})
#: Chainsolver-local fixed boundary purpose of an anchored child activity (mirror of the
#: #201 ``escort_linked``); never persisted -- the plans keep the real purpose.
PASSIVE_LINKED_PURPOSE = "passive_linked"

LINK_COLUMNS = ["child_person_id", "child_activity_index",
                "adult_person_id", "adult_activity_index", "adult_purpose"]
#: Shape of the anchored child rows appended to the stage output and of the #201
#: ``linked_location_rows`` (same columns, so the caller treats both alike).
ANCHOR_COLUMNS = ["person_id", "activity_index", "location_id", "geometry"]

_REQUIRED_PERSON_COLUMNS = ("person_id", "household_id", "source_H_ID", "source_P_ID")
_REQUIRED_TRIP_COLUMNS = ("person_id", "trip_index", "following_purpose", "W_ID",
                          "passive_pair_status", "passive_pair_adult_p_id",
                          "passive_pair_adult_w_id")
_STAT_KEYS = ("n_passive_paired", "n_adult_in_household", "n_adult_not_in_household",
              "n_adult_leg_present", "n_adult_leg_missing", "n_purpose_not_secondary",
              "n_adult_is_linked_child", "n_duplicate_dropped", "n_linked")
_LOG_TAG = "[passive_joint_links]"


def _require_columns(frame: pd.DataFrame, required, frame_name: str) -> None:
    missing = [c for c in required if c not in frame.columns]
    if missing:
        raise ValueError(
            f"{_LOG_TAG} {frame_name} is missing required column(s) {missing}; the passive "
            "joint link needs the plan-source ids on the persons frame and the phase-1 "
            "pairing columns (escort_passive_from_adult) on the trips frame."
        )


def _empty_links() -> pd.DataFrame:
    return pd.DataFrame({c: pd.Series(dtype="int64") for c in LINK_COLUMNS[:-1]}
                        | {"adult_purpose": pd.Series(dtype=object)})


def build_passive_joint_links(df_persons: pd.DataFrame, df_trips: pd.DataFrame, *,
                              secondary_purposes=SECONDARY_JOINT_PURPOSES
                              ) -> tuple[pd.DataFrame, dict]:
    """Link table (child joint activity -> adult activity) and exclusion accounting.

    Parameters
    ----------
    df_persons:
        ``synthesis.population.sampled`` with ``person_id, household_id, source_H_ID,
        source_P_ID`` (identity is taken from HERE, not from the trips frame, because
        commute-day spliced donor rows carry only the trip contract columns).
    df_trips:
        The chainsolver's trips frame (``synthesis.population.trips.final``) with
        ``person_id, trip_index, following_purpose, W_ID`` and the three phase-1 pairing
        columns. Activity ``i + 1`` is the destination of trip ``i``.
    secondary_purposes:
        Purposes that may be anchored (default :data:`SECONDARY_JOINT_PURPOSES`).

    Returns
    -------
    (links, stats):
        ``links`` with :data:`LINK_COLUMNS` (int64 ids and indices; ``adult_purpose`` the
        adult activity's plan-level purpose), sorted by child person and activity, one row
        per child activity; several children may share one adult activity. ``stats`` counts
        every paired passive leg exactly once:
        ``n_linked + n_adult_not_in_household + n_adult_leg_missing + n_purpose_not_secondary
        + n_adult_is_linked_child + n_duplicate_dropped == n_passive_paired``;
        ``link_rate = n_linked / n_passive_paired`` (NaN when there is no paired leg).

    Raises
    ------
    ValueError
        On a missing required column (named), or when a paired passive leg's person is
        absent from ``df_persons`` (the two frames come from one pipeline; a gap is a
        wiring error, not a legitimate exclusion).
    """
    _require_columns(df_persons, _REQUIRED_PERSON_COLUMNS, "persons frame")
    _require_columns(df_trips, _REQUIRED_TRIP_COLUMNS, "trips frame")
    stats = {key: 0 for key in _STAT_KEYS}
    stats["link_rate"] = float("nan")

    is_paired = (df_trips["passive_pair_status"] == STATUS_PAIRED).to_numpy()
    passive = df_trips.loc[is_paired, ["person_id", "trip_index", "following_purpose",
                                       "passive_pair_adult_p_id",
                                       "passive_pair_adult_w_id"]].copy()
    stats["n_passive_paired"] = int(len(passive))
    if len(passive) == 0:
        logger.info("%s no paired passive escort legs on the trips frame; nothing to link.",
                    _LOG_TAG)
        return _empty_links(), stats

    passive = passive.rename(columns={"person_id": "child_person_id",
                                      "following_purpose": "child_purpose"})
    passive["child_activity_index"] = passive["trip_index"].astype("int64") + 1
    # The pairing columns are float on the frame (NaN on unpaired rows); on the paired
    # subset they are whole numbers by construction.
    passive["adult_source_P_ID"] = passive["passive_pair_adult_p_id"].astype("int64")
    passive["adult_W_ID"] = passive["passive_pair_adult_w_id"].astype("int64")

    persons = df_persons[list(_REQUIRED_PERSON_COLUMNS)].drop_duplicates("person_id")
    child_identity = persons.rename(columns={"person_id": "child_person_id"})[
        ["child_person_id", "household_id", "source_H_ID"]]
    passive = passive.merge(child_identity, on="child_person_id", how="left")
    missing_child = passive["household_id"].isna()
    if bool(missing_child.any()):
        raise ValueError(
            f"{_LOG_TAG} {int(missing_child.sum())} paired passive leg(s) belong to a "
            "person_id absent from the persons frame (first: "
            f"{passive.loc[missing_child, 'child_person_id'].iloc[0]!r}); the trips and "
            "persons frames must come from the same population."
        )

    adults = persons.rename(columns={"person_id": "adult_person_id",
                                     "source_P_ID": "adult_source_P_ID"})[
        ["adult_person_id", "household_id", "source_H_ID", "adult_source_P_ID"]]
    merged = passive.merge(adults, on=["household_id", "source_H_ID", "adult_source_P_ID"],
                           how="left")
    has_adult = merged["adult_person_id"].notna()
    stats["n_adult_not_in_household"] = int((~has_adult).sum())
    merged = merged[has_adult].copy()
    merged["adult_person_id"] = merged["adult_person_id"].astype("int64")
    stats["n_adult_in_household"] = int(len(merged))

    adult_trips = df_trips.loc[df_trips["W_ID"].notna(),
                               ["person_id", "trip_index", "W_ID", "following_purpose"]]
    adult_trips = adult_trips.rename(columns={
        "person_id": "adult_person_id", "trip_index": "adult_trip_index",
        "W_ID": "adult_W_ID", "following_purpose": "adult_purpose"}).copy()
    adult_trips["adult_W_ID"] = adult_trips["adult_W_ID"].astype("int64")
    merged = merged.merge(adult_trips, on=["adult_person_id", "adult_W_ID"], how="left")
    has_leg = merged["adult_trip_index"].notna()
    stats["n_adult_leg_missing"] = int((~has_leg).sum())
    merged = merged[has_leg].copy()
    merged["adult_activity_index"] = merged["adult_trip_index"].astype("int64") + 1
    stats["n_adult_leg_present"] = int(len(merged))

    secondary = (merged["child_purpose"].isin(secondary_purposes)
                 & merged["adult_purpose"].isin(secondary_purposes))
    stats["n_purpose_not_secondary"] = int((~secondary).sum())
    merged = merged[secondary]

    # A person that is BOTH a linked adult and a linked child would make the two solver
    # passes cyclic (its own anchor would come from the pass it must precede). Expected 0:
    # passive legs are minors' legs, paired adults are >= adult_min_age.
    linked_children = set(merged["child_person_id"].tolist())
    is_linked_child = merged["adult_person_id"].isin(linked_children)
    stats["n_adult_is_linked_child"] = int(is_linked_child.sum())
    merged = merged[~is_linked_child]

    merged = merged.sort_values(["child_person_id", "child_activity_index", "adult_person_id"])
    n_before = len(merged)
    merged = merged.drop_duplicates(["child_person_id", "child_activity_index"], keep="first")
    stats["n_duplicate_dropped"] = int(n_before - len(merged))

    links = merged[LINK_COLUMNS].reset_index(drop=True)
    for column in LINK_COLUMNS[:-1]:
        links[column] = links[column].astype("int64")
    stats["n_linked"] = int(len(links))
    stats["link_rate"] = stats["n_linked"] / stats["n_passive_paired"]

    def _pct(n: int) -> float:
        return 100.0 * n / stats["n_passive_paired"]

    logger.info(
        "%s %d/%d paired passive legs linked to the adult's activity (%.1f%%); excluded: "
        "adult not in the synthetic household (plan source) %d (%.1f%%), adult leg missing "
        "%d (%.1f%%), purpose not secondary %d (%.1f%%), adult is a linked child %d, "
        "duplicate child activity %d. Unlinked children keep the independent draw.",
        _LOG_TAG, stats["n_linked"], stats["n_passive_paired"], _pct(stats["n_linked"]),
        stats["n_adult_not_in_household"], _pct(stats["n_adult_not_in_household"]),
        stats["n_adult_leg_missing"], _pct(stats["n_adult_leg_missing"]),
        stats["n_purpose_not_secondary"], _pct(stats["n_purpose_not_secondary"]),
        stats["n_adult_is_linked_child"], stats["n_duplicate_dropped"],
    )
    return links, stats


def _empty_anchors() -> pd.DataFrame:
    return pd.DataFrame({"person_id": pd.Series(dtype="int64"),
                         "activity_index": pd.Series(dtype="int64"),
                         "location_id": pd.Series(dtype=object),
                         "geometry": pd.Series(dtype=object)})


def resolve_joint_anchors(links: pd.DataFrame, df_locations: pd.DataFrame
                          ) -> tuple[pd.DataFrame, dict]:
    """Anchor rows for the linked CHILD activities, taken from the adults' PLACED locations.

    ``df_locations`` is the pass-1 chainsolver output (``person_id, activity_index,
    location_id, geometry``; solver rows AND fallback rows -- both are placements). A link
    whose adult activity has no location row stays unresolved: the child then keeps the
    independent draw in pass 2 (counted, logged). Returns ``(anchor_rows, stats)`` with
    :data:`ANCHOR_COLUMNS` sorted by ``(person_id, activity_index)`` -- the same shape as
    the #201 ``linked_location_rows``, so the stage appends and dict-ifies both alike --
    and ``stats = {n_links, n_resolved, n_unresolved}``. Pure; no randomness.
    """
    stats = {"n_links": int(len(links)), "n_resolved": 0, "n_unresolved": 0}
    if len(links) == 0:
        return _empty_anchors(), stats
    placed = df_locations[ANCHOR_COLUMNS].copy()
    n_before = len(placed)
    placed = placed.drop_duplicates(["person_id", "activity_index"], keep="first")
    n_dropped = n_before - len(placed)
    if n_dropped > 0:
        logger.warning(
            "%s chainsolver pass-1 placed %d duplicate (person_id, activity_index) rows; "
            "the first occurrence is kept, %d dropped. This suggests the upstream solver "
            "placed the same activity multiple times.", _LOG_TAG, n_before, n_dropped
        )
    placed = placed.rename(columns={"person_id": "adult_person_id",
                                    "activity_index": "adult_activity_index"})
    merged = links.merge(placed, on=["adult_person_id", "adult_activity_index"], how="left")
    resolved = merged["geometry"].notna()
    stats["n_resolved"] = int(resolved.sum())
    stats["n_unresolved"] = int((~resolved).sum())
    anchors = merged.loc[resolved, ["child_person_id", "child_activity_index",
                                    "location_id", "geometry"]]
    anchors = anchors.rename(columns={"child_person_id": "person_id",
                                      "child_activity_index": "activity_index"})
    anchors = anchors.sort_values(["person_id", "activity_index"]).reset_index(drop=True)
    anchors["person_id"] = anchors["person_id"].astype("int64")
    anchors["activity_index"] = anchors["activity_index"].astype("int64")
    logger.info(
        "%s anchors resolved for %d/%d links (%.1f%%); %d adult activities without a "
        "placed location -> the child keeps the independent draw.",
        _LOG_TAG, stats["n_resolved"], stats["n_links"],
        100.0 * stats["n_resolved"] / stats["n_links"], stats["n_unresolved"],
    )
    return anchors, stats
