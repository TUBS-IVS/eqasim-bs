"""Measure the headroom of issue #409 option 1: a SURROGATE adult for an unlinked passive
escort leg.

Context. Issue #385 / ADR-0119 anchors an escorted child's joint activity at the
accompanying adult's placed secondary location. The link is established by PLAN-SOURCE
IDENTITY, so it survives only where the donor pair survived synthesis: measured in the
production configuration (run manifest
``docs/runs/i385-passive-joint-location-production-smoke-03101-2026-09-15.yml``),
12 of 58 paired passive legs link (20.7 %). Option 1 of issue #409 asks whether an
unlinked child could instead be anchored on a DIFFERENT adult of its own synthetic
household. This script measures how many legs such a surrogate would reach; it does NOT
implement the re-pointing, and it changes no pipeline behaviour.

What a surrogate must be, and why. The anchor must be a SECONDARY activity (shop, leisure
or other). An adult's ``escort`` activity is NOT usable: issue #201 / ADR-0072 anchors it
at the escorted child's EDUCATION location, so anchoring a shopping child there would
place it at a school. This is also why ``build_passive_joint_links`` already requires BOTH
sides of a link to be secondary. The issue text lists "already escorts someone" and "has a
secondary activity" as two candidate columns of one table; only the second can carry an
anchor, so this script treats "already escorts someone" as an additional PLAUSIBILITY
FILTER on top of a secondary-activity candidate, never as a candidate in its own right.

Equally, a child whose OWN activity is not secondary is out of scope: an education
activity is the child's own assigned Kita or school (anchored by the primary-location
machinery) and a home activity is the household's own location. Only the eligible
remainder can ever change a realised LOCATION, and that count -- not the raw link rate --
is the number the build-or-drop decision rests on.

Read-only against a COMPLETED working directory; never runs synpp. Run on the server:

    python scripts/measure_passive_joint_surrogate_adults.py \
        --cache ~/eqasim-bs/eqasim-data/cache_i385_smoke \
        --out ~/i409_headroom

    python scripts/measure_passive_joint_surrogate_adults.py \
        --mid-dir ~/eqasim-bs/eqasim-data/data/braunschweig/popsim/mid2023_raw --out ~/i409_headroom

Self-check. The script first rebuilds the production link table with the very function the
pipeline uses and compares it against ``--expect-paired`` / ``--expect-linked`` (defaults:
the production smoke's 58 / 12). A mismatch aborts, so no headroom number is ever reported
against a working directory that is not the one the baseline was measured on.

RESULT (2026-09-16, working directory ``eqasim-data/cache_i385_smoke`` on host felix -- the
production-configuration smoke of run manifest
``i385-passive-joint-location-production-smoke-03101-2026-09-15``, Kreis 03101 at a 1 %
sampling rate; 2,571 persons, 8,003 trips; runtime a few seconds). Self-check passed:
58 paired passive legs, 12 linked, 20.7 %.

The 46 unlinked legs by the CHILD's own activity purpose::

    education 19 | home 10 | other 13 | shop 4

Only the 17 ``other``/``shop`` legs are eligible -- an education activity is the child's
own assigned facility and a home activity is the household's own location. 16 of the 17
have at least one household surrogate adult SOMEWHERE in the day (65 candidate
activities), but the time fit is poor::

    gap <= 15 min   any household adult with a secondary activity     6 of 46 legs
    gap <= 30 min   any household adult with a secondary activity     9 of 46 legs
    gap <= 15 min   ... restricted to an adult who ALSO escorts       0 of 46 legs
    gap <= 30 min   ... restricted to an adult who ALSO escorts       0 of 46 legs

The reachable legs by the child's own purpose, which is what would actually move::

    gap <= 15 min   other 5 | shop 1      (6 legs)
    gap <= 30 min   other 5 | shop 4      (9 legs)

Reading. (1) The realistic ceiling of option 1 is 6 additional legs at the phase-1 gap
threshold, i.e. 12/58 -> 18/58 (20.7 % -> 31.0 %). All 6 do change a realised location by
construction, but 5 of them are the catch-all ``other`` purpose and exactly 1 is a
shopping trip, so the visible effect on the model output is smaller still.
(2) The corroboration the issue leans on is NOT available where it would be needed: among
household adults carrying a usable secondary anchor, none is also escorting somebody
within 30 minutes of the child's departure, so the "both diaries independently attest
travel at nearly the same minute" argument cannot be invoked for these legs. The
substitution would rest on time proximity alone, at a median gap of 10.6 minutes rather
than the 4 minutes the issue quotes.

Reconciliation with issue #409's own table (``summarise_issue_409_cut``), reproduced
exactly, so the difference is attributable to the CUT and not to either measurement::

    gap <= 15 / 30 min   secondary adult, child purpose IGNORED   13 / 20 of 46 legs
    gap <= 15 / 30 min   escorting adult, ESCORT trip as the pair 10 / 14 of 46 legs

The issue's wider counts include children whose own activity is education or home (which
cannot change location) and, in the second row, pair on an escort trip (which cannot carry
an anchor, being pinned to a school by issue #201 / ADR-0072).

SIBLING-AGE HISTOGRAM (2026-09-16, raw MiD 2023 B1, 10,905 passive legs of minors, gap 15
min): floor 18 pairs 10,343 (94.8 %), 562 unpaired; newly paired at floor 16: 6 (16:3, 17:3)
· 14: 15 (14:5, 15:4, 16:3, 17:3) · 12: 19 · 10: 27 · 6: 56 (dominated by 7- and 9-year-olds,
i.e. co-travelling children). Re-measured by this mode on 2026-09-17 on the server and
reproduced EXACTLY, figure for figure (run manifest
``docs/runs/i409-passive-joint-surrogate-smoke-03101-2026-09-17.yml``, artifact
``mid_sibling_age_histogram.csv``).

LIMITS. n = 46 unlinked legs in ONE Kreis at a 1 % sampling rate, and the 17 eligible legs
sit in only 9 households. The error bars are wide and no reference value exists for any of
these quantities -- this is a descriptive smoke-scale measurement, not a validation.
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
import pickle
import sys

import numpy as np
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from braunschweig.popsim.escort_pairing import (                       # noqa: E402
    DEFAULT_ADULT_MIN_AGE, STATUS_PAIRED,
)
from braunschweig.popsim.trips import PURPOSE_BY_W_ZWECK               # noqa: E402
from braunschweig.synthesis.locations.passive_joint_links import (     # noqa: E402
    SECONDARY_JOINT_PURPOSES, build_passive_joint_links,
)

logger = logging.getLogger(__name__)

#: synpp stage aliased to ``synthesis.population.trips.final`` -- the REPORTING-DAY frame
#: the two-pass chainsolver wiring actually consumes, i.e. the frame the production link
#: rate was measured on (after the commute-day / day-absence splice).
TRIPS_STAGE = "braunschweig.synthesis.commute_day.trips_day_stage"
#: synpp stage holding the synthetic persons' identity and age.
PERSONS_STAGE = "synthesis.population.sampled"

#: Plan-level purpose of an ACTIVE escort activity. Never an anchor (issue #201 pins it to
#: the escorted child's education location); used only to flag an adult who escorts.
ESCORT_PURPOSE = "escort"

#: Production smoke baseline this script self-checks against (run manifest
#: ``i385-passive-joint-location-production-smoke-03101-2026-09-15``).
DEFAULT_EXPECT_PAIRED = 58
DEFAULT_EXPECT_LINKED = 12

#: Gap thresholds reported, in minutes. 15 is the phase-1 pairing threshold
#: (``escort_pairing.DEFAULT_MAX_GAP_MINUTES``), 30 the wider band issue #409 quotes.
DEFAULT_GAP_MINUTES = (15.0, 30.0)

UNLINKED_COLUMNS = [
    "child_person_id", "trip_index", "child_activity_index", "child_purpose",
    "child_departure_time", "household_id", "donor_adult_purpose", "donor_gap_minutes",
]
CANDIDATE_COLUMNS = [
    "child_person_id", "child_activity_index", "child_purpose", "child_departure_time",
    "household_id", "donor_adult_purpose",
    "adult_person_id", "adult_trip_index", "adult_activity_index", "adult_purpose",
    "adult_departure_time", "gap_minutes", "adult_escorts", "purpose_matches_donor",
]

_REQUIRED_PERSON_COLUMNS = ("person_id", "household_id", "HP_ALTER")
_REQUIRED_TRIP_COLUMNS = ("person_id", "trip_index", "following_purpose", "departure_time",
                          "passive_pair_status", "passive_pair_adult_w_zweck",
                          "passive_pair_gap_minutes")
_LOG_TAG = "[i409 surrogate]"


def _require_columns(frame: pd.DataFrame, required, frame_name: str) -> None:
    missing = [column for column in required if column not in frame.columns]
    if missing:
        raise ValueError(
            f"{_LOG_TAG} {frame_name} is missing required column(s) {missing}; the "
            "measurement needs the phase-1 pairing columns "
            "(escort_passive_from_adult must have been ON for the measured run)."
        )


def unlinked_paired_legs(df_trips: pd.DataFrame, df_persons: pd.DataFrame,
                         links: pd.DataFrame) -> pd.DataFrame:
    """The PAIRED passive escort legs that ``build_passive_joint_links`` did NOT link.

    Mirrors that function's own child-side derivation: activity ``i + 1`` is the
    destination of trip ``i``, and the child's plan-level ``following_purpose`` is the
    purpose of that activity. Adds the household the child actually lives in and the
    purpose of the DONOR adult the child rode with in the survey, read from the paired
    leg's ``passive_pair_adult_w_zweck`` through the plain MiD purpose table (this is the
    adult's own leg, so no passive-leg override applies).

    Parameters
    ----------
    df_trips:
        ``synthesis.population.trips.final`` with :data:`_REQUIRED_TRIP_COLUMNS`.
    df_persons:
        ``synthesis.population.sampled`` with :data:`_REQUIRED_PERSON_COLUMNS`.
    links:
        The link table from ``build_passive_joint_links`` (columns ``child_person_id``,
        ``child_activity_index``). Its rows are removed from the result.

    Returns
    -------
    A frame with :data:`UNLINKED_COLUMNS`, one row per unlinked paired passive leg.

    Raises
    ------
    ValueError
        On a missing required column, or when a paired leg's person is absent from the
        persons frame -- the two frames come from one pipeline, so a gap is a wiring
        error, not a legitimate exclusion.
    """
    _require_columns(df_trips, _REQUIRED_TRIP_COLUMNS, "trips frame")
    _require_columns(df_persons, _REQUIRED_PERSON_COLUMNS, "persons frame")

    is_paired = (df_trips["passive_pair_status"] == STATUS_PAIRED).to_numpy()
    passive = df_trips.loc[is_paired, ["person_id", "trip_index", "following_purpose",
                                       "departure_time", "passive_pair_adult_w_zweck",
                                       "passive_pair_gap_minutes"]].copy()
    passive = passive.rename(columns={"person_id": "child_person_id",
                                      "following_purpose": "child_purpose",
                                      "departure_time": "child_departure_time",
                                      "passive_pair_gap_minutes": "donor_gap_minutes"})
    passive["trip_index"] = passive["trip_index"].astype("int64")
    passive["child_activity_index"] = passive["trip_index"] + 1
    passive["donor_adult_purpose"] = (
        passive["passive_pair_adult_w_zweck"].map(
            lambda code: PURPOSE_BY_W_ZWECK.get(int(code)) if pd.notna(code) else None)
    )

    households = df_persons[["person_id", "household_id"]].drop_duplicates("person_id")
    households = households.rename(columns={"person_id": "child_person_id"})
    passive = passive.merge(households, on="child_person_id", how="left")
    missing_child = passive["household_id"].isna()
    if bool(missing_child.any()):
        raise ValueError(
            f"{_LOG_TAG} {int(missing_child.sum())} paired passive leg(s) belong to a "
            f"person_id absent from the persons frame (first: "
            f"{passive.loc[missing_child, 'child_person_id'].iloc[0]!r}); the trips and "
            "persons frames must come from the same population."
        )
    passive["household_id"] = passive["household_id"].astype("int64")

    if len(links) > 0:
        linked_keys = set(zip(links["child_person_id"].astype("int64").tolist(),
                              links["child_activity_index"].astype("int64").tolist()))
        keys = list(zip(passive["child_person_id"].astype("int64").tolist(),
                        passive["child_activity_index"].tolist()))
        passive = passive[[key not in linked_keys for key in keys]]

    passive = passive.sort_values(["child_person_id", "child_activity_index"])
    return passive[UNLINKED_COLUMNS].reset_index(drop=True)


def _leg_activity_pairs(legs: pd.DataFrame, df_persons: pd.DataFrame, df_trips: pd.DataFrame,
                        *, activity_purposes, adult_min_age: int) -> pd.DataFrame:
    """Pair each leg with every activity of ``activity_purposes`` performed by an adult of
    the SAME synthetic household, and attach the absolute departure gap in minutes.

    Shared by the decision-relevant cut and by the reconciliation against issue #409's own
    numbers, so both are computed by one join rule and any difference between them can
    only come from the cut, never from the implementation.
    """
    adults = df_persons[df_persons["HP_ALTER"] >= int(adult_min_age)][
        ["person_id", "household_id"]].drop_duplicates("person_id")
    adults = adults.rename(columns={"person_id": "adult_person_id"})

    activities = df_trips[df_trips["following_purpose"].isin(frozenset(activity_purposes))][
        ["person_id", "trip_index", "following_purpose", "departure_time"]].copy()
    activities = activities.rename(columns={
        "person_id": "adult_person_id", "trip_index": "adult_trip_index",
        "following_purpose": "adult_purpose", "departure_time": "adult_departure_time"})
    activities = activities.merge(adults, on="adult_person_id", how="inner")

    merged = legs.merge(activities, on="household_id", how="inner")
    merged = merged[merged["adult_person_id"] != merged["child_person_id"]].copy()
    merged["adult_trip_index"] = merged["adult_trip_index"].astype("int64")
    merged["adult_activity_index"] = merged["adult_trip_index"] + 1
    merged["gap_minutes"] = (
        (merged["adult_departure_time"] - merged["child_departure_time"]).abs() / 60.0
    )
    return merged


def build_surrogate_candidates(unlinked: pd.DataFrame, df_persons: pd.DataFrame,
                               df_trips: pd.DataFrame, *,
                               secondary_purposes=SECONDARY_JOINT_PURPOSES,
                               adult_min_age: int = DEFAULT_ADULT_MIN_AGE
                               ) -> tuple[pd.DataFrame, dict]:
    """Every admissible (unlinked leg, surrogate adult activity) pair, with its time gap.

    A leg is ELIGIBLE only when the child's own activity purpose is secondary: an
    education activity is the child's own assigned facility and a home activity is the
    household's own location, so neither can change by anchoring on an adult. A candidate
    adult activity is a SECONDARY activity of a household member of at least
    ``adult_min_age`` who is not the child itself. ``adult_escorts`` marks a candidate
    adult who also escorts somebody that day, and ``purpose_matches_donor`` whether the
    candidate's purpose equals the donor adult's -- both are reported as additional
    plausibility filters, neither restricts the candidate set.

    Returns
    -------
    (candidates, stats):
        ``candidates`` with :data:`CANDIDATE_COLUMNS`, one row per admissible pair, sorted
        by child and gap so the nearest surrogate of each leg comes first. ``stats``
        accounts for every unlinked leg exactly once:
        ``n_ineligible_child_purpose + n_eligible_with_candidate
        + n_eligible_without_candidate == n_unlinked``.
    """
    _require_columns(df_persons, _REQUIRED_PERSON_COLUMNS, "persons frame")
    secondary = frozenset(secondary_purposes)

    stats = {
        "n_unlinked": int(len(unlinked)),
        "n_eligible_child_purpose": 0,
        "n_ineligible_child_purpose": 0,
        "n_eligible_with_candidate": 0,
        "n_eligible_without_candidate": 0,
        "n_candidate_rows": 0,
    }

    eligible = unlinked[unlinked["child_purpose"].isin(secondary)].copy()
    stats["n_eligible_child_purpose"] = int(len(eligible))
    stats["n_ineligible_child_purpose"] = stats["n_unlinked"] - stats["n_eligible_child_purpose"]
    if len(eligible) == 0:
        _log_headroom(stats)
        return pd.DataFrame(columns=CANDIDATE_COLUMNS), stats

    escorting = set(
        df_trips.loc[df_trips["following_purpose"] == ESCORT_PURPOSE, "person_id"].tolist()
    )
    merged = _leg_activity_pairs(eligible, df_persons, df_trips,
                                 activity_purposes=secondary, adult_min_age=adult_min_age)
    merged["adult_escorts"] = merged["adult_person_id"].isin(escorting)
    merged["purpose_matches_donor"] = (
        merged["adult_purpose"] == merged["donor_adult_purpose"]
    )

    merged = merged.sort_values(
        ["child_person_id", "child_activity_index", "gap_minutes", "adult_person_id"])
    candidates = merged[CANDIDATE_COLUMNS].reset_index(drop=True)

    stats["n_candidate_rows"] = int(len(candidates))
    with_candidate = set(
        zip(candidates["child_person_id"].tolist(),
            candidates["child_activity_index"].tolist())
    )
    stats["n_eligible_with_candidate"] = len(with_candidate)
    stats["n_eligible_without_candidate"] = (
        stats["n_eligible_child_purpose"] - stats["n_eligible_with_candidate"]
    )
    _log_headroom(stats)
    return candidates, stats


def _log_headroom(stats: dict) -> None:
    """One line per measurement, in the rate style CLAUDE.md requires of this pipeline."""
    total = stats["n_unlinked"]
    if total == 0:
        logger.info("%s no unlinked paired passive legs; nothing to measure.", _LOG_TAG)
        return

    def pct(count: int) -> float:
        return 100.0 * count / total

    logger.info(
        "%s %d unlinked paired passive legs: %d eligible (%.1f%%, the child's own "
        "activity is secondary), %d ineligible (%.1f%%, education or home). Of the "
        "eligible, %d have at least one household surrogate adult (%.1f%% of all "
        "unlinked) across %d candidate activities, %d have none.",
        _LOG_TAG, total, stats["n_eligible_child_purpose"],
        pct(stats["n_eligible_child_purpose"]), stats["n_ineligible_child_purpose"],
        pct(stats["n_ineligible_child_purpose"]), stats["n_eligible_with_candidate"],
        pct(stats["n_eligible_with_candidate"]), stats["n_candidate_rows"],
        stats["n_eligible_without_candidate"],
    )


def summarise_headroom(candidates: pd.DataFrame, unlinked: pd.DataFrame,
                       gap_minutes=DEFAULT_GAP_MINUTES) -> pd.DataFrame:
    """Reachable legs per gap threshold and candidate restriction.

    Three restrictions are reported, from the widest to the most conservative: ANY
    household adult with a secondary activity; only an adult who ALSO escorts somebody
    that day; and only an adult whose purpose additionally EQUALS the donor adult's. The
    counts are legs, not candidate rows -- a leg counts once however many surrogates it
    has. ``n_unlinked`` is carried so every share in the report has its denominator next
    to it.
    """
    restrictions = {
        "any_secondary_adult": pd.Series(True, index=candidates.index),
        "adult_also_escorts": candidates["adult_escorts"].astype(bool)
        if len(candidates) else pd.Series(dtype=bool),
        "and_purpose_matches_donor": (candidates["adult_escorts"].astype(bool)
                                      & candidates["purpose_matches_donor"].astype(bool))
        if len(candidates) else pd.Series(dtype=bool),
    }
    rows = []
    for threshold in gap_minutes:
        within = candidates["gap_minutes"] <= float(threshold) if len(candidates) \
            else pd.Series(dtype=bool)
        for name, mask in restrictions.items():
            subset = candidates[within & mask] if len(candidates) else candidates
            legs = subset.drop_duplicates(["child_person_id", "child_activity_index"])
            rows.append({
                "gap_minutes_max": float(threshold),
                "restriction": name,
                "n_legs_reached": int(len(legs)),
                "n_unlinked": int(len(unlinked)),
                "median_gap_minutes": float(subset["gap_minutes"].median())
                if len(subset) else float("nan"),
            })
    return pd.DataFrame(rows)


def summarise_issue_409_cut(unlinked: pd.DataFrame, df_persons: pd.DataFrame,
                            df_trips: pd.DataFrame, *,
                            gap_minutes=DEFAULT_GAP_MINUTES,
                            adult_min_age: int = DEFAULT_ADULT_MIN_AGE,
                            secondary_purposes=SECONDARY_JOINT_PURPOSES) -> pd.DataFrame:
    """Reproduce the two candidate rows issue #409's own table quotes.

    RECONCILIATION ONLY -- these counts are deliberately WIDER than the decision-relevant
    headroom, in two ways the issue's table does not state:

    * they count every unlinked leg, including the children whose own activity is
      education or home; anchoring those on an adult cannot change a realised location,
      because an education activity is the child's own assigned facility and a home
      activity is the household's own location;
    * the second row pairs on the adult's ESCORT trip, which cannot carry an anchor at
      all: issue #201 / ADR-0072 pins an escort activity to the escorted child's education
      location.

    Printing both cuts side by side keeps the difference attributable to the CUT rather
    than leaving a later reader to suspect a defect in either measurement.
    """
    rows = []
    cuts = {
        "any_secondary_adult__child_purpose_ignored": secondary_purposes,
        "escorting_adult__escort_trip_as_pair": (ESCORT_PURPOSE,),
    }
    for name, purposes in cuts.items():
        pairs = _leg_activity_pairs(unlinked, df_persons, df_trips,
                                    activity_purposes=purposes, adult_min_age=adult_min_age)
        for threshold in gap_minutes:
            within = pairs[pairs["gap_minutes"] <= float(threshold)] if len(pairs) else pairs
            legs = within.drop_duplicates(["child_person_id", "child_activity_index"])
            rows.append({
                "gap_minutes_max": float(threshold),
                "issue_cut": name,
                "n_legs_reached": int(len(legs)),
                "n_unlinked": int(len(unlinked)),
                "median_gap_minutes": float(within["gap_minutes"].median())
                if len(within) else float("nan"),
            })
    return pd.DataFrame(rows)


def purpose_breakdown(frame: pd.DataFrame, purpose_column: str) -> pd.DataFrame:
    """Counts per activity purpose, sorted by purpose so the table is stable across runs."""
    counts = frame[purpose_column].value_counts(dropna=False).rename_axis(
        purpose_column).reset_index(name="n")
    return counts.sort_values(purpose_column).reset_index(drop=True)


def sibling_age_histogram(wege: pd.DataFrame, *, floors=(16, 14, 12, 10, 6),
                          max_gap_minutes: float = 15.0, reference_floor: int = 18
                          ) -> pd.DataFrame:
    """How old is the nearest-in-time household leg that a MINOR's passive leg could pair with?

    Runs the phase-1 pairing (``escort_pairing.pair_passive_legs``) on RAW MiD Wege at the
    reference floor (18, the production value) and at each lower floor, and reports, per
    floor, how many of the legs UNPAIRED at the reference floor newly pair, with the age
    histogram of their partners. This is the committed source of the
    ``escort_passive_joint_surrogate_min_age_years`` default (ADR-0127): a marginal table,
    because with a low floor a sibling can also WIN over an adult that is close in time, which
    is not the question. Restricted to passive legs of minors (HP_ALTER <= 17).

    Raises
    ------
    ValueError
        When a floor in ``floors`` is at or above ``reference_floor`` (named): the histogram
        reports floors BELOW the reference, so such a floor could never describe a leg newly
        paired relative to it.
    """
    bad_floors = [floor for floor in floors if floor >= reference_floor]
    if bad_floors:
        raise ValueError(
            f"{_LOG_TAG} floor(s) {bad_floors} are at or above reference_floor "
            f"{reference_floor}; every floor in `floors` must be strictly below the "
            "reference floor it is measured against."
        )
    # STATUS_PAIRED is already imported at module level; only the two names not imported
    # there are re-imported here.
    from braunschweig.popsim.escort_pairing import PASSIVE_W_ZWECK, pair_passive_legs
    age = wege.drop_duplicates(["H_ID", "P_ID"]).set_index(["H_ID", "P_ID"])["HP_ALTER"]
    minors = ((wege["W_ZWECK"] == PASSIVE_W_ZWECK)
              & (pd.to_numeric(wege["HP_ALTER"], errors="coerce") <= 17)).to_numpy()

    def paired_with_partner_age(floor: int) -> pd.DataFrame:
        out, _diagnostics = pair_passive_legs(wege, max_gap_minutes=max_gap_minutes,
                                              adult_min_age=floor)
        sub = out[minors].copy()
        partner = pd.MultiIndex.from_arrays([sub["H_ID"], sub["passive_pair_adult_p_id"]])
        sub["partner_age"] = pd.to_numeric(age.reindex(partner).to_numpy(), errors="coerce")
        return sub

    base = paired_with_partner_age(reference_floor)
    paired_base = base["passive_pair_status"] == STATUS_PAIRED
    unpaired_index = base.index[~paired_base]
    rows = [{"floor_years": int(reference_floor), "n_passive_minor_legs": int(len(base)),
             "n_paired_at_floor": int(paired_base.sum()), "n_newly_paired_vs_reference": 0,
             "share_of_unpaired_at_reference": 0.0, "partner_age_histogram": ""}]
    for floor in floors:
        sub = paired_with_partner_age(int(floor))
        newly = sub.loc[unpaired_index]
        newly = newly[newly["passive_pair_status"] == STATUS_PAIRED]
        histogram = newly["partner_age"].value_counts().sort_index()
        rows.append({
            "floor_years": int(floor), "n_passive_minor_legs": int(len(sub)),
            "n_paired_at_floor": int((sub["passive_pair_status"] == STATUS_PAIRED).sum()),
            "n_newly_paired_vs_reference": int(len(newly)),
            "share_of_unpaired_at_reference": (len(newly) / len(unpaired_index)
                                               if len(unpaired_index) else float("nan")),
            "partner_age_histogram": ", ".join(f"{int(a)}:{int(n)}" for a, n in histogram.items()),
        })
    return pd.DataFrame(rows)


def load_stage(working_directory: str, stage_name: str):
    """One cached synpp stage from a COMPLETED working directory.

    Fails on an ambiguous cache instead of guessing: several pickles of one stage mean
    several runs share this directory, and picking by modification time has silently
    served a stale frame before. Name the intended run's directory, or clean it.
    """
    hits = sorted(glob.glob(os.path.join(working_directory, f"{stage_name}__*.p")))
    if not hits:
        raise RuntimeError(
            f"{_LOG_TAG} no cached pickle for stage '{stage_name}' in "
            f"{working_directory}; the measurement needs a COMPLETED run of that stage."
        )
    if len(hits) > 1:
        names = ", ".join(os.path.basename(hit) for hit in hits)
        raise RuntimeError(
            f"{_LOG_TAG} {len(hits)} cached pickles for stage '{stage_name}' in "
            f"{working_directory} ({names}); the working directory holds more than one "
            "run of this stage, so the measurement cannot tell which one the baseline "
            "was measured on."
        )
    logger.info("%s load %s <- %s", _LOG_TAG, stage_name, os.path.basename(hits[0]))
    with open(hits[0], "rb") as handle:
        return pickle.load(handle)


def _print_frame(title: str, frame: pd.DataFrame) -> None:
    print(f"\n--- {title} ---")
    print(frame.to_string(index=False) if len(frame) else "(empty)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", help="COMPLETED synpp working directory of the measured run")
    parser.add_argument("--out", help="output directory for the per-leg and per-candidate CSVs")
    parser.add_argument("--mid-dir",
                        help="RAW MiD 2023 Wege directory: run the sibling-age histogram "
                             "(the committed source of the surrogate age-floor default) "
                             "instead of the cache headroom measurement")
    parser.add_argument("--adult-min-age", type=int, default=DEFAULT_ADULT_MIN_AGE,
                        help="minimum HP_ALTER of a surrogate adult, in years "
                             f"(default {DEFAULT_ADULT_MIN_AGE}, the phase-1 threshold)")
    parser.add_argument("--gap-minutes", type=float, nargs="+", default=list(DEFAULT_GAP_MINUTES),
                        help="reported |departure gap| thresholds in minutes "
                             f"(default {' '.join(str(g) for g in DEFAULT_GAP_MINUTES)})")
    parser.add_argument("--expect-paired", type=int, default=DEFAULT_EXPECT_PAIRED,
                        help="self-check: paired passive legs the baseline recorded "
                             f"(default {DEFAULT_EXPECT_PAIRED}; -1 disables)")
    parser.add_argument("--expect-linked", type=int, default=DEFAULT_EXPECT_LINKED,
                        help="self-check: linked legs the baseline recorded "
                             f"(default {DEFAULT_EXPECT_LINKED}; -1 disables)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.mid_dir:
        from braunschweig.popsim.mid.donor import load_mid_wege
        wege = load_mid_wege(args.mid_dir)
        table = sibling_age_histogram(wege, max_gap_minutes=args.gap_minutes[0])
        _print_frame("MiD sibling-age histogram: legs unpaired at floor 18 that pair at a lower floor",
                     table)
        if args.out:
            os.makedirs(args.out, exist_ok=True)
            path = os.path.join(args.out, "mid_sibling_age_histogram.csv")
            table.to_csv(path, index=False)
            print(f"wrote {path}")
        return 0
    if not (args.cache and args.out):
        parser.error("--cache and --out are required unless --mid-dir is given")

    df_persons = load_stage(args.cache, PERSONS_STAGE)
    df_trips = load_stage(args.cache, TRIPS_STAGE)
    print(f"{_LOG_TAG} persons {len(df_persons)} rows, trips {len(df_trips)} rows")

    links, link_stats = build_passive_joint_links(df_persons, df_trips)
    print(f"\n=== self-check against the recorded baseline ===")
    print(f"paired passive legs : {link_stats['n_passive_paired']} "
          f"(expected {args.expect_paired})")
    print(f"linked              : {link_stats['n_linked']} "
          f"(expected {args.expect_linked})")
    print(f"link rate           : {100.0 * link_stats['link_rate']:.1f} %")
    mismatches = []
    if args.expect_paired >= 0 and link_stats["n_passive_paired"] != args.expect_paired:
        mismatches.append(f"paired {link_stats['n_passive_paired']} != {args.expect_paired}")
    if args.expect_linked >= 0 and link_stats["n_linked"] != args.expect_linked:
        mismatches.append(f"linked {link_stats['n_linked']} != {args.expect_linked}")
    if mismatches:
        raise SystemExit(
            f"{_LOG_TAG} SELF-CHECK FAILED ({'; '.join(mismatches)}). This working "
            "directory is not the run the baseline was measured on, so no headroom "
            "number from it would be comparable. Pass --expect-paired/--expect-linked "
            "explicitly if you intend to measure a different run."
        )
    print("self-check OK")

    unlinked = unlinked_paired_legs(df_trips, df_persons, links)
    candidates, stats = build_surrogate_candidates(
        unlinked, df_persons, df_trips, adult_min_age=args.adult_min_age)

    _print_frame("unlinked legs by the CHILD's own activity purpose",
                 purpose_breakdown(unlinked, "child_purpose"))
    summary = summarise_headroom(candidates, unlinked, gap_minutes=args.gap_minutes)
    _print_frame("headroom: unlinked legs a household surrogate adult could reach", summary)
    for threshold in args.gap_minutes:
        within = candidates[candidates["gap_minutes"] <= float(threshold)]
        reached = within.drop_duplicates(["child_person_id", "child_activity_index"])
        _print_frame(f"reachable legs within {threshold:g} min by the CHILD's own activity "
                     "purpose (these are the activities whose LOCATION would change)",
                     purpose_breakdown(reached, "child_purpose"))

    reconciliation = summarise_issue_409_cut(
        unlinked, df_persons, df_trips, gap_minutes=args.gap_minutes,
        adult_min_age=args.adult_min_age)
    _print_frame("RECONCILIATION with issue #409's own table -- WIDER cuts, not the "
                 "decision-relevant headroom (see summarise_issue_409_cut)", reconciliation)

    print("\n--- accounting ---")
    for key, value in stats.items():
        print(f"{key:32s} {value}")

    os.makedirs(args.out, exist_ok=True)
    paths = {
        "unlinked_passive_legs.csv": unlinked,
        "surrogate_adult_candidates.csv": candidates,
        "surrogate_headroom_summary.csv": summary,
        "issue_409_reconciliation.csv": reconciliation,
    }
    print()
    for name, frame in paths.items():
        path = os.path.join(args.out, name)
        frame.to_csv(path, index=False)
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
