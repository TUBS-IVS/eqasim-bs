"""Passive escort joint location: link the escorted child's joint activity to the
accompanying adult's activity (issue #385, ADR-0119; phase 2 of #372 / ADR-0112).

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
  coverage check; deferred, ADR-0119) are therefore excluded and counted
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

#: At or above this share of links whose adult activity has no placed pass-1 location the
#: pass-1 output is probably incomplete (a truncated or mis-keyed pass-1 result), so
#: :func:`resolve_joint_anchors` escalates its rate line from INFO to WARNING
#: (CLAUDE.md fallback transparency: a pathological fallback rate is a failure signal).
#: The comparison is ``>=``, the convention of every sibling rate instrument of this stage
#: (``reporting._fallback_accounting_summary``,
#: ``reporting._excursion_boundary_clip_summary``, the SrV marginal-fallback line), so a
#: rate landing exactly on the threshold warns instead of staying silent.
DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE = 0.5

#: Config keys and code defaults of the surrogate rescue (issue #409 option 1, ADR-0124, written
#: by the same change). The keys live HERE, in the module that owns the rule, and are imported by
#: the chainsolver stage (never retyped). Code default of the flag is False, mirroring ADR-0112
#: ruling C-R9 / ADR-0119: the rescue extends the ADR-0119 link table, which itself needs the
#: phase-1 pairing columns; the production ON state is realised only by configs/base_bs.yml.
KEY_SURROGATE_ENABLED = "escort_passive_joint_surrogate"
KEY_SURROGATE_MAX_GAP_MINUTES = "escort_passive_joint_surrogate_max_gap_minutes"
KEY_SURROGATE_MIN_AGE_YEARS = "escort_passive_joint_surrogate_min_age_years"
KEY_SURROGATE_REQUIRE_SAME_PURPOSE = "escort_passive_joint_surrogate_require_same_purpose"
#: Unit: minutes. Equal to the phase-1 pairing gap by choice; a separate key because the
#: semantics differ (synthetic-side surrogate, not donor-side pair).
DEFAULT_SURROGATE_MAX_GAP_MINUTES = 15.0
#: Unit: years. ASSUMPTION (a user judgment, bounded by a measurement on the raw MiD 2023
#: delivery): accompaniment by a household member under 18 is rare above 14 and, below about
#: 12, the nearest-in-time "partner" is a co-travelling small child, not an accompanying
#: person. Both the record (ADR-0124) and the reproducible measurement (the --mid-dir mode of
#: scripts/measure_passive_joint_surrogate_adults.py) are ADDED BY THE SAME CHANGE as this
#: constant (issue #409); neither exists in a checkout that predates it.
DEFAULT_SURROGATE_MIN_AGE_YEARS = 14
#: True keeps the ADR-0119 invariant that both sides of a link carry the SAME purpose.
DEFAULT_SURROGATE_REQUIRE_SAME_PURPOSE = True

#: ``link_source`` values on the composed link table (only present when the rescue is on).
LINK_SOURCE_PLAN_SOURCE = "plan_source"
LINK_SOURCE_SURROGATE = "surrogate"
#: Shape of the rescue's own output: the link columns plus provenance and the realised gap.
SURROGATE_LINK_COLUMNS = LINK_COLUMNS + ["link_source", "gap_minutes"]

_RESCUE_STAT_KEYS = ("n_rescue_candidates", "n_rescue_ineligible_child_purpose",
                     "n_rescue_no_candidate_activity", "n_rescue_gap_exceeded",
                     "n_rescue_purpose_mismatch_rows", "n_rescue_cyclic_dropped_rows",
                     "n_surrogate_linked")

_REQUIRED_PERSON_COLUMNS = ("person_id", "household_id", "source_H_ID", "source_P_ID")
_REQUIRED_TRIP_COLUMNS = ("person_id", "trip_index", "following_purpose", "W_ID",
                          "passive_pair_status", "passive_pair_adult_p_id",
                          "passive_pair_adult_w_id")
_REQUIRED_SURROGATE_PERSON_COLUMNS = _REQUIRED_PERSON_COLUMNS + ("HP_ALTER",)
_REQUIRED_SURROGATE_TRIP_COLUMNS = _REQUIRED_TRIP_COLUMNS + ("departure_time",)
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


def _paired_passive_legs(df_trips: pd.DataFrame) -> pd.DataFrame:
    """The PAIRED passive escort legs as child-side rows of the link table.

    Renames the child's columns, derives ``child_activity_index`` (activity ``i + 1`` is
    the destination of trip ``i``) and casts the two pairing ids to int64. The pairing
    columns are float on the trips frame (NaN on unpaired rows); on the paired subset they
    are whole numbers by construction, so a NaN there is a phase-1 wiring defect and raises
    instead of producing a generic pandas cast error.
    """
    is_paired = (df_trips["passive_pair_status"] == STATUS_PAIRED).to_numpy()
    passive = df_trips.loc[is_paired, ["person_id", "trip_index", "following_purpose",
                                       "passive_pair_adult_p_id",
                                       "passive_pair_adult_w_id"]].copy()
    passive = passive.rename(columns={"person_id": "child_person_id",
                                      "following_purpose": "child_purpose"})
    passive["child_activity_index"] = passive["trip_index"].astype("int64") + 1
    for column in ("passive_pair_adult_p_id", "passive_pair_adult_w_id"):
        missing = passive[column].isna()
        if bool(missing.any()):
            first = passive.loc[missing].iloc[0]
            raise ValueError(
                f"{_LOG_TAG} {int(missing.sum())} paired passive leg(s) carry no "
                f"{column} (first: person_id {first['child_person_id']}, trip_index "
                f"{first['trip_index']}); a leg marked '{STATUS_PAIRED}' without the "
                "adult's pairing ids is a defect of the phase-1 pairing "
                "(escort_passive_from_adult), not a legitimate exclusion."
            )
    passive["adult_source_P_ID"] = passive["passive_pair_adult_p_id"].astype("int64")
    passive["adult_W_ID"] = passive["passive_pair_adult_w_id"].astype("int64")
    return passive


def _match_adult_by_plan_source(passive: pd.DataFrame, df_persons: pd.DataFrame,
                                stats: dict) -> pd.DataFrame:
    """Attach the paired adult: the SAME synthetic household member whose plan source is
    the child's donor household and the paired adult's ``P_ID``.

    Counts the legs whose adult is absent from the synthetic household
    (``n_adult_not_in_household``) and the survivors (``n_adult_in_household``). A child
    that is itself absent from the persons frame raises: the two frames come from one
    pipeline, so a gap is a wiring error, not an exclusion.
    """
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
    return merged


def _match_adult_leg(merged: pd.DataFrame, df_trips: pd.DataFrame,
                     stats: dict) -> pd.DataFrame:
    """Attach the adult's paired leg (its ``W_ID``) and the activity it ends at.

    Counts the legs whose adult trip is absent -- a spliced commute day or a dropped leg --
    as ``n_adult_leg_missing`` and the survivors as ``n_adult_leg_present``.
    """
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
    return merged


def _keep_secondary_and_acyclic(merged: pd.DataFrame, secondary_purposes,
                                stats: dict) -> pd.DataFrame:
    """Keep the pairs whose BOTH activities are secondary and whose adult is not itself a
    linked child.

    A person that is BOTH a linked adult and a linked child would make the two solver
    passes cyclic (its own anchor would come from the pass it must precede). Expected 0:
    passive legs are minors' legs, paired adults are >= adult_min_age.
    """
    secondary = (merged["child_purpose"].isin(secondary_purposes)
                 & merged["adult_purpose"].isin(secondary_purposes))
    stats["n_purpose_not_secondary"] = int((~secondary).sum())
    merged = merged[secondary]

    linked_children = set(merged["child_person_id"].tolist())
    is_linked_child = merged["adult_person_id"].isin(linked_children)
    stats["n_adult_is_linked_child"] = int(is_linked_child.sum())
    return merged[~is_linked_child]


def _log_link_rates(stats: dict) -> None:
    """Log the link rate and the per-reason exclusion rates of one link build.

    Paired legs but NO link at all means the pairing columns or the plan-source ids are
    broken rather than that the population happens to say so, so that case escalates to
    WARNING (CLAUDE.md fallback transparency rule 2).
    """
    def _pct(n: int) -> float:
        return 100.0 * n / stats["n_passive_paired"]

    nothing_linked = stats["n_passive_paired"] > 0 and stats["n_linked"] == 0
    logger.log(
        logging.WARNING if nothing_linked else logging.INFO,
        "%s %d/%d paired passive legs linked to the adult's activity (%.1f%%); excluded: "
        "adult not in the synthetic household (plan source) %d (%.1f%%), adult leg missing "
        "%d (%.1f%%), purpose not secondary %d (%.1f%%), adult is a linked child %d, "
        "duplicate child activity %d. Unlinked children keep the independent draw.%s",
        _LOG_TAG, stats["n_linked"], stats["n_passive_paired"], _pct(stats["n_linked"]),
        stats["n_adult_not_in_household"], _pct(stats["n_adult_not_in_household"]),
        stats["n_adult_leg_missing"], _pct(stats["n_adult_leg_missing"]),
        stats["n_purpose_not_secondary"], _pct(stats["n_purpose_not_secondary"]),
        stats["n_adult_is_linked_child"], stats["n_duplicate_dropped"],
        (" No paired passive leg could be linked -- the pairing columns or the "
         "plan-source ids are probably broken.") if nothing_linked else "",
    )


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
        + n_adult_is_linked_child + n_duplicate_dropped == n_passive_paired``. The identity
        holds as long as the plan-source key is unique per synthetic household, which is the
        normal case; two household members sharing one plan source (a defensive case) make a
        child activity match several adults, and each such EXTRA row is dropped by the
        per-child-activity de-duplication, so the sum then exceeds ``n_passive_paired`` by
        exactly that inflation (``n_duplicate_dropped`` whenever no inflated row was excluded
        earlier). ``link_rate = n_linked / n_passive_paired`` (NaN when there is no paired
        leg).

    Raises
    ------
    ValueError
        On a missing required column (named), when a paired passive leg carries no adult
        pairing id, or when a paired passive leg's person is absent from ``df_persons``
        (the two frames come from one pipeline; a gap is a wiring error, not a legitimate
        exclusion).
    """
    _require_columns(df_persons, _REQUIRED_PERSON_COLUMNS, "persons frame")
    _require_columns(df_trips, _REQUIRED_TRIP_COLUMNS, "trips frame")
    stats = {key: 0 for key in _STAT_KEYS}
    stats["link_rate"] = float("nan")

    passive = _paired_passive_legs(df_trips)
    stats["n_passive_paired"] = int(len(passive))
    if len(passive) == 0:
        logger.info("%s no paired passive escort legs on the trips frame; nothing to link.",
                    _LOG_TAG)
        return _empty_links(), stats

    merged = _match_adult_by_plan_source(passive, df_persons, stats)
    merged = _match_adult_leg(merged, df_trips, stats)
    merged = _keep_secondary_and_acyclic(merged, secondary_purposes, stats)

    # One link per child activity, lowest adult_person_id first so the kept row does not
    # depend on the frame order.
    merged = merged.sort_values(["child_person_id", "child_activity_index", "adult_person_id"])
    n_before = len(merged)
    merged = merged.drop_duplicates(["child_person_id", "child_activity_index"], keep="first")
    stats["n_duplicate_dropped"] = int(n_before - len(merged))

    links = merged[LINK_COLUMNS].reset_index(drop=True)
    for column in LINK_COLUMNS[:-1]:
        links[column] = links[column].astype("int64")
    stats["n_linked"] = int(len(links))
    stats["link_rate"] = stats["n_linked"] / stats["n_passive_paired"]
    _log_link_rates(stats)
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
            "%s %d of %d placed pass-1 rows were duplicate (person_id, activity_index) rows "
            "and were dropped (first kept). This suggests the upstream solver placed the "
            "same activity multiple times.", _LOG_TAG, n_dropped, n_before
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
    # A pathological unresolved share is a failure signal, not a data property: it means
    # the pass-1 placements the anchors are read from are incomplete (CLAUDE.md fallback
    # transparency rule 2).
    incomplete = (stats["n_unresolved"] / stats["n_links"]
                  >= DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE)
    logger.log(
        logging.WARNING if incomplete else logging.INFO,
        "%s anchors resolved for %d/%d links (%.1f%%); %d links pointing at an adult "
        "activity without a placed location -> the child keeps the independent draw.%s",
        _LOG_TAG, stats["n_resolved"], stats["n_links"],
        100.0 * stats["n_resolved"] / stats["n_links"], stats["n_unresolved"],
        (f" At or above {100.0 * DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE:.0f}% of the links "
         "are unresolved -- the pass-1 output is probably incomplete.") if incomplete else "",
    )
    return anchors, stats


# --------------------------------------------------------------- surrogate rescue (#409)

def _empty_surrogate_links() -> pd.DataFrame:
    frame = _empty_links()
    frame["link_source"] = pd.Series(dtype=object)
    frame["gap_minutes"] = pd.Series(dtype="float64")
    return frame


def _rescue_set(df_persons: pd.DataFrame, df_trips: pd.DataFrame) -> pd.DataFrame:
    """The paired passive legs whose plan-source adult is ABSENT from the synthetic household.

    Exactly the ``adult_not_in_household`` exclusion of :func:`build_passive_joint_links`,
    re-derived through the same helper (``_match_adult_by_plan_source``) so the two can never
    disagree about which legs those are. Attaches the child's ``household_id`` and the
    departure time of the child's own leg (``child_departure_time``, seconds).
    """
    passive = _paired_passive_legs(df_trips)
    if len(passive) == 0:
        # LOAD-BEARING, do not "simplify" away: with an empty frame the boolean mask built
        # below becomes an EMPTY LIST, which pandas reads as COLUMN selection rather than row
        # masking and silently returns a frame with no columns at all.
        return passive.assign(household_id=pd.Series(dtype="int64"),
                              child_departure_time=pd.Series(dtype="float64"))
    present = _match_adult_by_plan_source(passive, df_persons, {})
    present_keys = set(zip(present["child_person_id"].tolist(),
                           present["child_activity_index"].tolist()))
    keys = zip(passive["child_person_id"].tolist(), passive["child_activity_index"].tolist())
    absent = passive[[key not in present_keys for key in keys]].copy()

    households = df_persons[["person_id", "household_id"]].drop_duplicates("person_id")
    households = households.rename(columns={"person_id": "child_person_id"})
    absent = absent.merge(households, on="child_person_id", how="left")
    departures = df_trips[["person_id", "trip_index", "departure_time"]].rename(
        columns={"person_id": "child_person_id", "departure_time": "child_departure_time"})
    absent = absent.merge(departures, on=["child_person_id", "trip_index"], how="left")
    absent["household_id"] = absent["household_id"].astype("int64")
    return absent


def rescue_with_surrogates(links: pd.DataFrame, df_persons: pd.DataFrame,
                           df_trips: pd.DataFrame, *, max_gap_minutes: float,
                           min_age_years: int, require_same_purpose: bool,
                           secondary_purposes=SECONDARY_JOINT_PURPOSES
                           ) -> tuple[pd.DataFrame, dict]:
    """Surrogate links for the children whose donor adult is absent from the household.

    Issue #409 option 1 (ADR-0124, written by the same change). For every paired passive leg that
    :func:`build_passive_joint_links` excluded as ``adult_not_in_household`` and whose child
    activity is secondary, the nearest-in-time secondary activity of another household member
    of at least ``min_age_years`` becomes the anchor, provided its departure lies within
    ``max_gap_minutes`` of the child's. Deterministic: no random draw.

    Parameters
    ----------
    links:
        The identity link table (``LINK_COLUMNS``) -- its children can never be surrogates.
    df_persons:
        ``synthesis.population.sampled`` with ``person_id, household_id, source_H_ID,
        source_P_ID, HP_ALTER``.
    df_trips:
        The PLAN-LEVEL trips frame the identity link is built on, with ``departure_time``
        (seconds) in addition to the identity link's required columns.
    max_gap_minutes:
        Maximum |departure gap| for a surrogate activity. Unit: minutes; must be > 0.
    min_age_years:
        Minimum ``HP_ALTER`` of a surrogate. Unit: years; must be > 0.
    require_same_purpose:
        When True, the surrogate's activity purpose must EQUAL the child's (the ADR-0119
        invariant); when False any secondary purpose is admissible.

    Returns
    -------
    (surrogate_links, stats):
        ``surrogate_links`` with :data:`SURROGATE_LINK_COLUMNS` (int64 ids and indices,
        ``link_source == "surrogate"``, ``gap_minutes`` float), one row per rescued child
        activity, sorted by child. ``stats`` accounts for every rescue candidate exactly once:
        ``n_rescue_ineligible_child_purpose + n_rescue_no_candidate_activity
        + n_rescue_gap_exceeded + n_surrogate_linked == n_rescue_candidates``; the two ``*_rows``
        counters are candidate ROWS dropped by the purpose rule and the acyclic guard, reported
        alongside. ``rescue_rate = n_surrogate_linked / n_rescue_candidates`` (NaN without
        candidates).

    Raises
    ------
    ValueError
        On a missing required column (named) or a non-positive parameter (named).
    """
    _require_columns(df_persons, _REQUIRED_SURROGATE_PERSON_COLUMNS, "persons frame")
    _require_columns(df_trips, _REQUIRED_SURROGATE_TRIP_COLUMNS, "trips frame")
    if not float(max_gap_minutes) > 0:
        raise ValueError(f"{_LOG_TAG} {KEY_SURROGATE_MAX_GAP_MINUTES} must be > 0 minutes, "
                         f"got {max_gap_minutes!r}.")
    if not int(min_age_years) > 0:
        raise ValueError(f"{_LOG_TAG} {KEY_SURROGATE_MIN_AGE_YEARS} must be > 0 years, "
                         f"got {min_age_years!r}.")
    secondary = frozenset(secondary_purposes)
    stats = {key: 0 for key in _RESCUE_STAT_KEYS}
    stats["rescue_rate"] = float("nan")

    rescue = _rescue_set(df_persons, df_trips)
    stats["n_rescue_candidates"] = int(len(rescue))
    if stats["n_rescue_candidates"] > 0:
        stats["rescue_rate"] = 0.0
    eligible = rescue[rescue["child_purpose"].isin(secondary)]
    stats["n_rescue_ineligible_child_purpose"] = int(len(rescue) - len(eligible))
    # Task 2 continues here: candidate persons, candidate activities, gap, selection.
    _log_rescue_rates(stats, max_gap_minutes, min_age_years, require_same_purpose,
                      material=False)
    return _empty_surrogate_links(), stats


def _log_rescue_rates(stats: dict, max_gap_minutes: float, min_age_years: int,
                      require_same_purpose: bool, *, material: bool) -> None:
    """One rate line per run (CLAUDE.md fallback transparency).

    ``material`` says whether at least one admissible candidate activity existed for some
    eligible leg; a rescue that links NOTHING despite material is a failure signal (a broken
    household join, a time-unit mismatch) and is logged at WARNING.
    """
    n = stats["n_rescue_candidates"]
    if n == 0:
        logger.info("%s surrogate rescue: no adult-not-in-household legs; nothing to rescue.",
                    _LOG_TAG)
        return
    dead = material and stats["n_surrogate_linked"] == 0
    logger.log(
        logging.WARNING if dead else logging.INFO,
        "%s surrogate rescue: %d/%d adult-not-in-household legs re-pointed to a household "
        "surrogate (%.1f%%); child purpose not secondary %d, no admissible candidate activity "
        "%d, nearest candidate beyond %.1f min %d; purpose-mismatch rows dropped %d, cyclic rows "
        "dropped %d (require_same_purpose=%s, min_age_years=%d). Unrescued children keep the "
        "independent draw.%s",
        _LOG_TAG, stats["n_surrogate_linked"], n, 100.0 * stats["n_surrogate_linked"] / n,
        stats["n_rescue_ineligible_child_purpose"], stats["n_rescue_no_candidate_activity"],
        float(max_gap_minutes), stats["n_rescue_gap_exceeded"],
        stats["n_rescue_purpose_mismatch_rows"], stats["n_rescue_cyclic_dropped_rows"],
        bool(require_same_purpose), int(min_age_years),
        " Candidate activities existed but none was linked -- check the household join and "
        "the departure-time unit." if dead else "",
    )
