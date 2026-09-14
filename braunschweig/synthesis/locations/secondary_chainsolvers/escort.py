"""Escort household-link trip rewriting and distance-by-type factor map.

Issue #201: ``rewrite_linked_escort_trips`` rewrites ANCHORED escort
activities' plan-level purposes to the fixed ``escort_linked`` purpose so
the problem splitter anchors them at the linked child's education location;
``_build_escort_distance_factor_map`` builds the SrV-derived per-type
distance scaling map for the A3 escort distance-by-type feature (or None
when OFF, the byte-identical path).

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .activity_types import ESCORT_CATEGORY_TO_ACTIVITY


def _anchored_side_masks(out: pd.DataFrame, anchored: pd.MultiIndex, *,
                         candidate_preceding=None, candidate_following=None):
    """POSITIONAL boolean masks of the trip rows on either side of an anchored activity.

    A trip's ``preceding_purpose`` reflects activity ``trip_index`` and its
    ``following_purpose`` activity ``trip_index + 1``, so each side is probed against
    ``anchored`` (a ``(person_id, activity_index)`` MultiIndex) with its own activity
    index (the ``activity_offset`` below). ``candidate_preceding`` /
    ``candidate_following`` are optional positional boolean arrays pre-selecting the rows
    worth probing (issue #201 only rewrites rows whose purpose is already ``escort``): the
    MultiIndex and the activity index are then built over those rows only, which is the
    dominant cost at scale. ``None`` probes every row. The masks are positional, so a
    non-monotonic row index is handled.
    """
    def _side_mask(activity_offset, candidate):
        if candidate is None:
            trip_index = out["trip_index"]
            activity_index = trip_index + activity_offset if activity_offset else trip_index
            return pd.MultiIndex.from_arrays(
                [out["person_id"], activity_index]).isin(anchored)
        mask = np.zeros(len(out), dtype=bool)
        if candidate.any():
            trip_index = out.loc[candidate, "trip_index"]
            activity_index = trip_index + activity_offset if activity_offset else trip_index
            mask[candidate] = pd.MultiIndex.from_arrays([
                out.loc[candidate, "person_id"], activity_index,
            ]).isin(anchored)
        return mask

    return _side_mask(0, candidate_preceding), _side_mask(1, candidate_following)


def rewrite_linked_escort_trips(df_trips: pd.DataFrame,
                                df_anchors: pd.DataFrame) -> pd.DataFrame:
    """Return a COPY of the trips frame where ANCHORED escort activities'
    plan-level ``escort`` purposes become the fixed ``escort_linked`` purpose
    (issue #201 Phase 2; per-activity since the multi-child fix).

    A trip's ``preceding_purpose`` reflects activity ``trip_index`` and its
    ``following_purpose`` activity ``trip_index + 1``; both sides of an
    anchored activity are rewritten so the problem splitter sees a consistent
    fixed boundary. Escort activities WITHOUT an anchor row (overflow beyond
    the household's linkable children) keep the plain ``escort`` purpose and
    stay on the SrV-weighted draw path. Only the chainsolver-local problem
    construction sees this frame; the persisted activities/plans keep the
    plain ``escort`` purpose. The MultiIndex/``isin`` masks are built only over
    the rows whose ``preceding_purpose`` / ``following_purpose`` is already
    ``escort`` (ASSUMPTION: typically 5-8% of all trips -- an order-of-magnitude
    expectation motivating the mask-cost argument, not a committed reference;
    the realised, W_GEW-weighted escort share is logged per run and the escort
    mechanism is documented in ADR-0072), not the full trips frame, since
    building and probing a MultiIndex over every row is the dominant cost at
    scale for a candidate set this small."""
    out = df_trips.copy()
    anchored = pd.MultiIndex.from_frame(df_anchors[["person_id", "activity_index"]])
    mask_preceding, mask_following = _anchored_side_masks(
        out, anchored,
        candidate_preceding=(out["preceding_purpose"] == "escort").to_numpy(),
        candidate_following=(out["following_purpose"] == "escort").to_numpy(),
    )
    # Boolean (positional) .loc assignment exactly as before the helper extraction.
    out.loc[mask_preceding, "preceding_purpose"] = "escort_linked"
    out.loc[mask_following, "following_purpose"] = "escort_linked"
    return out


def rewrite_anchored_activities(df_trips: pd.DataFrame, df_anchors: pd.DataFrame,
                                fixed_purpose: str) -> pd.DataFrame:
    """Return a COPY of the trips frame where BOTH sides of every anchored activity carry
    ``fixed_purpose``, whatever the plan-level purpose was (issue #385, ADR-0119).

    Unlike :func:`rewrite_linked_escort_trips`, which only touches activities whose purpose
    is already ``escort``, the passive joint anchor applies to ``shop`` / ``leisure`` /
    ``other`` activities, so the rewrite is keyed on ``(person_id, activity_index)`` alone.
    A trip's ``preceding_purpose`` is activity ``trip_index``, its ``following_purpose``
    activity ``trip_index + 1``. Only the chainsolver-local problem construction sees this
    frame; the persisted plans keep the real purpose. Positional boolean masks, so a
    non-monotonic AND a duplicate row index are both handled.
    """
    out = df_trips.copy()
    if len(df_anchors) == 0:
        return out
    anchored = pd.MultiIndex.from_frame(df_anchors[["person_id", "activity_index"]])
    # No candidate pre-filter: every activity of the pass frame may be anchored.
    mask_preceding, mask_following = _anchored_side_masks(out, anchored)
    # Boolean (positional) .loc assignment, exactly as in rewrite_linked_escort_trips.
    # Passing the mask itself keeps the selection POSITIONAL; resolving it to labels first
    # (out.index[mask]) would rewrite every row sharing a selected label, which silently
    # corrupts an unrelated person's purposes on a frame with a duplicate index.
    out.loc[mask_preceding, "preceding_purpose"] = fixed_purpose
    out.loc[mask_following, "following_purpose"] = fixed_purpose
    return out


def _build_escort_distance_factor_map(context):
    """{activity_name: factor} for escort distance-by-type (A3), or None when OFF.

    Factors are SrV between-type structure ratios applied to the MiD escort
    level (spec 2026-08-11). Keys are the chainsolver activity names the
    escort location decider draws (ESCORT_CATEGORY_TO_ACTIVITY values), so the
    leg loop can use the drawn name as the distance-layer key directly.
    """
    if not context.config("escort_distance_by_type"):
        return None
    if not context.config("escort_purpose"):
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] escort_distance_by_type requires "
            "escort_purpose to be ON (there is no escort distance layer to scale)."
        )
    activities = list(context.config("escort_distance_factor_activities"))
    factors = [float(f) for f in context.config("escort_distance_factors")]
    if len(activities) != len(factors):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_distance_factor_activities "
            f"and escort_distance_factors must have the same length, got "
            f"{len(activities)} and {len(factors)}."
        )
    if len(set(activities)) != len(activities):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_distance_factor_activities "
            f"contains duplicate escort location categories: {activities}."
        )
    unknown = sorted(set(activities) - set(ESCORT_CATEGORY_TO_ACTIVITY))
    if unknown:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] unknown escort location "
            f"categor{'y' if len(unknown) == 1 else 'ies'} in "
            f"escort_distance_factor_activities: {unknown}."
        )
    if any(f <= 0.0 for f in factors):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_distance_factors must be "
            f"positive, got {factors}."
        )

    # Vocabulary consistency (final-review finding): escort_locations_activities
    # (what the location decider actually draws) and escort_distance_factor_activities
    # (what has a factor entry) are configured independently -- a draw category
    # missing a factor entry falls back silently unless flagged HERE, before any
    # leg is placed. Reading escort_locations_activities is safe: this same stage
    # declares it in configure(), so it is always present once execute() runs.
    drawn_categories = set(context.config("escort_locations_activities"))
    missing_factors = sorted(drawn_categories - set(activities))
    if missing_factors:
        print(
            "[braunschweig.secondary_chainsolvers] WARNING: escort_distance_by_type: "
            f"no distance factor for drawn categor{'y' if len(missing_factors) == 1 else 'ies'} "
            f"{missing_factors} -- their legs will fall back counted to the aggregate "
            "escort layer."
        )
    return {ESCORT_CATEGORY_TO_ACTIVITY[c]: f for c, f in zip(activities, factors)}
