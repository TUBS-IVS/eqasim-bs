"""Validate realised secondary trip distances vs MiD 2023 Tabelle A W12.

Measures the euclidean secondary trip-distance distribution per purpose
(shop / leisure / other) from a cached synpp working_directory and compares
to the committed W12 band-share targets via EMD.

A secondary trip is any leg whose destination activity has purpose
shop / leisure / other. The distance is computed as the straight-line
(euclidean) distance from the PREVIOUS activity location to the secondary
activity location (EPSG:25832 metres -> km). Each leg's euclidean distance
is scaled to the routed axis using the fitted circuity curve for its mode
network (car/pt/walk), matching the MiD W12 reported trip lengths.

Usage::

    python scripts/validate_secondary_distances.py \\
        --cache eqasim-data/cache_bs_25pct_allfeat \\
        --mid-dir eqasim-data/data/braunschweig/mid \\
        [--output secondary_distances.csv]

The script reads only the cached stage pickles and the committed MiD CSV;
it does NOT run synpp or any simulation step.
"""
from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import pickle
import sys
from pathlib import Path
from typing import NamedTuple, Optional

import numpy as np
import pandas as pd

# Add project root to sys.path so braunschweig.* imports work when the script
# is run directly (not as a module) without a prior `pip install -e .`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.calibration import circuity  # noqa: E402
from braunschweig.calibration.metrics import emd_on_bands  # noqa: E402
from braunschweig.calibration.secondary_measurement import (  # noqa: E402
    LOOP_ORIGIN_COLUMN,
    SUBTYPE_DONOR_MEAN_KM_RANGE,
    leave_out_loop_legs,
    mode_to_network,
    per_group_distance_summary,
    placement_share_at_positive_potential,
    w12_band_shares as _w12_band_shares,
)
from braunschweig.calibration.targets import (  # noqa: E402
    W12_BAND_EDGES_KM,
    load_w12_band_shares,
)

logger = logging.getLogger(__name__)

# Default cache path (25 % all-features run, absolute Windows path).
_DEFAULT_CACHE = (
    r"C:\Users\bienzeisler\Documents\GitHub\eqasim-bs"
    r"\eqasim-data\cache_bs_25pct_allfeat"
)
_DEFAULT_MID_DIR = (
    r"C:\Users\bienzeisler\Documents\GitHub\eqasim-bs"
    r"\eqasim-data\data\braunschweig\mid"
)

# Secondary purposes to report.
_SECONDARY_PURPOSES = ("shop", "leisure", "other")


# ---------------------------------------------------------------------------
# Stage-cache loading
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Loop marker (issue #679)
# ---------------------------------------------------------------------------

#: The secondary locations stage under its logical name and under the name synpp caches it by
#: in the production workflow (``configs/base_bs.yml`` aliases the first to the second).
_SECONDARY_LOCATIONS_STAGE = "synthesis.population.spatial.secondary.locations"
_SECONDARY_LOCATIONS_ALIASES = ("braunschweig.synthesis.locations.secondary_chainsolvers",)

#: The stages the legs are built from, as the names their pickles carry, PRODUCTION NAME FIRST.
#: synpp caches a stage under its RESOLVED alias target, so the production workflow has no pickle
#: of the logical name. The production names are the targets of ``configs/base_bs.yml``
#: ``aliases:`` (``synthesis.population.spatial.locations``, ``synthesis.population.activities.
#: final``, ``synthesis.population.trips.final``, ``synthesis.population.spatial.secondary.
#: locations``; measured on the 1 % run in docs/runs/artifacts/i679-stroll-loops-ab-1pct-2026-11-25/
#: i679_validate_stage_name_mapping.txt); the plain names are those of the older workflows, where
#: the logical stage is its own implementation. ``braunschweig.popsim.trips_stage`` stays the last
#: trips name for the popsim_mid workflow without the portal layer; in the production workflow it
#: is the PRE-ASSIGNMENT trip table, which :func:`_resolve_role` never takes next to a cached
#: ``trips_final``.
_LOCATIONS_NAMES = ("braunschweig.synthesis.commute_day.spatial_locations_day",
                    "synthesis.population.spatial.locations")
_ACTIVITIES_NAMES = ("braunschweig.synthesis.commute_day.activities_day_stage",
                     "synthesis.population.activities")
_TRIPS_NAMES = ("braunschweig.synthesis.portal_trips.trips_final",
                "synthesis.population.trips", "braunschweig.popsim.trips_stage")
_SECONDARY_NAMES = (*_SECONDARY_LOCATIONS_ALIASES, _SECONDARY_LOCATIONS_STAGE)


class AmbiguousCacheRunError(RuntimeError):
    """The cache holds several runs and does not say which one the legs belong to (or says so
    inconsistently). A ``RuntimeError`` too, but a callers' stage-not-found handling must let it
    through: it is not "the stage is missing"."""


class RunPickles(NamedTuple):
    """The cache pickles of ONE run: the legs' locations, the activities and trips they were
    built with and, when the cache holds one, the secondary locations result (``None``)."""

    locations: str
    activities: str
    trips: str
    secondary: Optional[str]


def _stage_pickle_paths(working_directory: str, names) -> list:
    """Every ``<name>__<hash>.p`` of ``names`` in ``working_directory``, newest first.

    A stage without any config is cached under its bare name (synpp's ``hash_name``), so
    ``<name>.p`` counts too; its key is the bare name."""
    paths = []
    for name in names:
        paths.extend(glob.glob(os.path.join(working_directory, name + "__*.p")))
        bare = os.path.join(working_directory, name + ".p")
        if os.path.exists(bare):
            paths.append(bare)
    return sorted(paths, key=os.path.getmtime, reverse=True)


def _key_is_of(key: str, name: str) -> bool:
    """Whether the pickle key ``key`` (``<stage>__<hash>`` or the bare stage name) is of stage ``name``."""
    return key == name or key.startswith(name + "__")


def _pickle_key(path: str) -> str:
    """synpp's key of a cache pickle: its file name without ``.p`` (``<stage>__<hash>``)."""
    return os.path.basename(path)[:-len(".p")]


def _dependency_closure(meta: dict, key: str) -> set:
    """Every stage key ``key`` depends on, directly or not, per synpp's ``pipeline.json``."""
    seen, pending = set(), [key]
    while pending:
        for dependency in meta.get(pending.pop(), {}).get("dependencies", {}):
            if dependency not in seen:
                seen.add(dependency)
                pending.append(dependency)
    return seen


def _describe_candidate(key: str, path: str, meta) -> str:
    """``key (pipeline.json updated <UTC time>)`` or ``key (file modified <UTC time>, not in
    pipeline.json)``: what the refusal of an ambiguous anchor shows to choose from."""
    import datetime

    def utc(seconds):
        return datetime.datetime.fromtimestamp(float(seconds), datetime.timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S UTC")

    if meta is not None and key in meta and "updated" in meta[key]:
        return f"{key} (pipeline.json updated {utc(meta[key]['updated'])})"
    return f"{key} (file modified {utc(os.path.getmtime(path))}, not in pipeline.json)"


def read_pickle(path: str):
    """The content of a cache pickle."""
    logger.info("Loading cache pickle '%s'", path)
    with open(path, "rb") as fh:
        return pickle.load(fh)


def _resolve_role(role: str, names, working_directory: str, closure, legs_key: str, tag: str,
                  required: bool):
    """The pickle of one stage role that belongs to the legs' run, or None (not required, none cached).

    ``names`` are the pickle names of the role, production name first; ``closure`` is the
    dependency closure of the legs' pickle in ``pipeline.json`` (None when that file or the legs'
    entry is missing). Rules, each logged under ``tag``:

    - PRIMARY: the highest-priority name that has a pickle in the closure, when exactly one
      pickle of it is linked and cached. A name with cached pickles but no link is skipped only
      when no lower-priority name is linked either; a link on a LOWER-priority name while a
      higher-priority name is cached but not linked raises (production: the pre-assignment
      activities and trips are upstream of the day ones and are linked too, so they must not win
      by being the only link).
    - FALLBACK (WARNING): no link at all and exactly one pickle of the role in the cache.
    - :class:`AmbiguousCacheRunError` naming the pickles otherwise (several candidates without
      a link, a link to a pickle that is not cached, several linked pickles of one name).
    - ``RuntimeError`` (the ordinary stage-not-found) when the role is ``required`` and nothing is
      cached under any of its names.
    """
    by_key = {_pickle_key(path): path for path in _stage_pickle_paths(working_directory, names)}
    if not by_key:
        if required:
            raise RuntimeError(
                f"No cached pickle found for the {role} (names tried: {', '.join(names)}) in "
                f"'{working_directory}'.")
        return None
    listing = ", ".join(sorted(by_key))
    if closure is not None:
        skipped = []
        for name in names:
            cached = sorted(key for key in by_key if _key_is_of(key, name))
            linked = sorted(key for key in closure if _key_is_of(key, name))
            if not linked:
                skipped.extend(cached)
                continue
            if skipped:
                raise AmbiguousCacheRunError(
                    f"{tag} {role}: pipeline.json links the legs' pickle '{legs_key}' to {linked} "
                    f"but the higher-priority {role} pickle(s) {skipped} in the cache are not "
                    "linked to it; refusing to take a lower-priority (pre-assignment) pickle. "
                    "Missing metadata can cause this too: a cache primed without pipeline.json "
                    "entries for the higher-priority pickle looks like an unlinked one.")
            if len(linked) == 1 and linked[0] in by_key:
                logger.info("%s run resolution, %s: PRIMARY, '%s' is the pickle the legs' pickle "
                            "'%s' depends on (pipeline.json); %d %s pickle(s) in the cache.",
                            tag, role, linked[0], legs_key, len(by_key), role)
                return by_key[linked[0]]
            raise AmbiguousCacheRunError(
                f"{tag} {role}: pipeline.json links the legs' pickle '{legs_key}' to {linked}, "
                f"but the cache holds {listing}; refusing to pair the legs with a pickle of "
                "another run. Rebuild the cache or run from a working directory that holds this "
                "run only.")
    if len(by_key) == 1:
        logger.warning(
            "%s run resolution, %s: FALLBACK, '%s' is the only %s pickle and pipeline.json does "
            "not link it to the legs' pickle; taken without a check.",
            tag, role, next(iter(by_key)), role)
        return next(iter(by_key.values()))
    raise AmbiguousCacheRunError(
        f"{tag} {role}: the cache holds {len(by_key)} {role} pickles ({listing}) and cannot say "
        f"which belongs to the legs' pickle '{legs_key}' (pipeline.json missing, or no dependency "
        "link to one of them); refusing to take the newest by modification time, which can be a "
        "pickle of another run (switch on versus off, or the pre-assignment table). Work in a "
        "directory that holds one run.")


def resolve_run_pickles(working_directory: str, tag: str = "[secondary-validate]",
                        locations_key: Optional[str] = None) -> RunPickles:
    """The cache pickles of the run the legs belong to: ONE resolution for locations, activities,
    trips and the secondary locations marker.

    The legs' anchor is the locations pickle, of the production name or of the plain name of the
    older workflows. It must be unique: a cache with several candidates (hashed variants of a
    copied cache, or both names) raises :class:`AmbiguousCacheRunError` listing them with their
    ``updated`` time from ``pipeline.json`` (the file time when the key is unknown to it), unless
    the caller names the run with ``locations_key`` (``<stage>__<hash>``, ``--locations-key`` of
    both scripts; an uncached key raises). The
    activities, the trips and the secondary locations pickle are the ones in the dependency closure
    of that pickle in the working directory's ``pipeline.json`` (synpp records, per pickle key
    ``<stage>__<hash>``, the keys it was built from), found by :func:`_resolve_role`; a cache
    without such a link falls back to a role's only pickle, and several candidates without a link
    raise :class:`AmbiguousCacheRunError`.

    The trips are the ones in the closure, ``trips_final`` in production: the modes the chain
    solver saw, BEFORE the outside-tour mode stage (``trips_simulated``) that writes the modes to
    the population. The activities are the day activities (``activities_day_stage``), the ones the
    day locations were built on, never the pre-assignment table of the plain name.

    Raises ``RuntimeError`` when no locations, activities or trips pickle is cached at all.
    ``secondary`` is None when no secondary locations pickle is cached.
    """
    candidates = _stage_pickle_paths(working_directory, _LOCATIONS_NAMES)
    meta_path = os.path.join(working_directory, "pipeline.json")
    meta = None
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as fh:
            meta = json.load(fh)
    if not candidates:
        raise RuntimeError(
            f"No cached pickle found for the legs' locations (names tried: "
            f"{', '.join(_LOCATIONS_NAMES)}) in '{working_directory}'.")
    by_key = {_pickle_key(path): path for path in candidates}
    if locations_key is not None:
        if locations_key not in by_key:
            raise AmbiguousCacheRunError(
                f"{tag} locations: the named run '{locations_key}' is not cached; the cache holds "
                f"{', '.join(sorted(by_key))}.")
        legs = by_key[locations_key]
    elif len(candidates) == 1:
        legs = candidates[0]
    else:
        raise AmbiguousCacheRunError(
            f"{tag} locations: the cache holds {len(candidates)} locations pickles and the legs' "
            "run is not unique (copied caches hold several hashed variants; the plain name is an "
            "alias in the production workflow and should not be cached next to the production "
            "name). Name the run with the locations_key argument (--locations-key "
            "<stage>__<hash>). Candidates: "
            + "; ".join(_describe_candidate(key, path, meta)
                        for key, path in sorted(by_key.items())))
    legs_key = _pickle_key(legs)
    closure = _dependency_closure(meta, legs_key) if meta is not None and legs_key in meta else None
    return RunPickles(
        locations=legs,
        activities=_resolve_role("activities", _ACTIVITIES_NAMES, working_directory, closure,
                                 legs_key, tag, required=True),
        trips=_resolve_role("trips", _TRIPS_NAMES, working_directory, closure, legs_key, tag,
                            required=True),
        secondary=_resolve_role("secondary locations", _SECONDARY_NAMES, working_directory,
                                closure, legs_key, tag, required=False))


def require_same_activity_keys(df_locations: pd.DataFrame, df_activities: pd.DataFrame,
                               tag: str = "[secondary-validate]") -> None:
    """Raise ``RuntimeError`` unless both frames hold the same ``(person_id, activity_index)`` keys.

    The legs join the locations with the activities by that key. Activities of another day (the
    pre-assignment table next to the day locations) would join with the wrong purposes on the
    keys both hold, and drop or ignore the rest; a row-count log does not show that.

    LIMITS: equal key sets prove a matching shape, not the same day or run (two runs can share
    their keys and differ in purposes); the run is established by the dependency link of
    :func:`resolve_run_pickles`. A role taken as FALLBACK (the only pickle, no link) is accepted
    unchecked apart from this key test. The trips are not checked here: a leg without a mode
    after the mode join is logged by :func:`measure_secondary_distances` ("mode join: primary
    n/n", or a WARNING with the count of legs missing a mode, which are then counted as car)."""
    keys = ["person_id", "activity_index"]
    locations = pd.MultiIndex.from_frame(df_locations[keys])
    activities = pd.MultiIndex.from_frame(df_activities[keys])
    only_locations = locations.difference(activities)
    only_activities = activities.difference(locations)
    if len(only_locations) or len(only_activities):
        raise RuntimeError(
            f"{tag} the locations and the activities must hold the same (person_id, "
            f"activity_index) keys, but {len(only_locations)} keys are only in the locations "
            f"(first {[tuple(map(int, k)) for k in only_locations[:3]]}) and "
            f"{len(only_activities)} only in the activities (first "
            f"{[tuple(map(int, k)) for k in only_activities[:3]]}): they do not belong to the "
            "same run or day.")


def load_loop_origin(working_directory: str, tag: str = "[secondary-validate]",
                     not_filtered_text: str = "the model side was NOT filtered for loop legs "
                                              "(strolls, dog walks)",
                     run: Optional[RunPickles] = None):
    """The ``loop_origin`` marker per (person_id, activity_index), or None when there is none.

    The cached locations frame the legs are built from keeps only person, activity, location and
    geometry, so the loop marker (which the secondary locations stage writes when
    ``secondary_loop_zero_displacement`` is ON) is read from that stage's own cached result, the
    one run the legs belong to (``run.secondary`` of :func:`resolve_run_pickles`, resolved here
    unless the caller passes the ``run`` it already resolved; :class:`AmbiguousCacheRunError`
    when a cache with several runs does not say which). None, with the reason logged, when the
    cache holds no such stage or the result has no marker column (switch off): the legs are then
    left exactly as before.

    Public because ``scripts/calibrate_detour_circuity.py`` reuses it. ``tag`` prefixes the log
    lines; ``not_filtered_text`` completes the NOTE printed when the cache has no secondary
    locations pickle ("NOTE: no secondary locations pickle in the cache -- <text>.").
    """
    # Outside any try: the resolution refusal (RuntimeError) must stop the run, not read as "no
    # pickle in the cache".
    marker_path = (run if run is not None else resolve_run_pickles(working_directory, tag)).secondary
    if marker_path is None:
        logger.info("%s loop_origin: no secondary locations pickle in the cache; loops cannot "
                    "be identified, legs are left unchanged.", tag)
        print(f"NOTE: no secondary locations pickle in the cache -- {not_filtered_text}.")
        return None
    logger.info("Loading stage '%s' from '%s'", _SECONDARY_LOCATIONS_STAGE, marker_path)
    with open(marker_path, "rb") as fh:
        result = pickle.load(fh)
    df_secondary = result[0] if isinstance(result, tuple) else result
    if LOOP_ORIGIN_COLUMN not in df_secondary.columns:
        logger.info("%s loop_origin: the secondary locations result has no '%s' column "
                    "(secondary_loop_zero_displacement OFF); legs are left unchanged.",
                    tag, LOOP_ORIGIN_COLUMN)
        return None
    marker = df_secondary[["person_id", "activity_index", LOOP_ORIGIN_COLUMN]]
    n_loops = int(marker[LOOP_ORIGIN_COLUMN].notna().sum())
    logger.info("%s loop_origin: found, %d/%d secondary location rows are loops.",
                tag, n_loops, len(marker))
    return marker


def attach_loop_marker(df_merged: pd.DataFrame, df_loop_origin: pd.DataFrame,
                       tag: str = "[secondary-validate]") -> pd.DataFrame:
    """``df_merged`` (``person_id``, ``activity_index``, ``purpose``, ...) with the ``loop_origin``
    column of ``df_loop_origin`` (:func:`load_loop_origin`) joined on, row for row.

    Raises ``AssertionError`` when the join changes the row count (a repeated key in the marker)
    and ``RuntimeError`` when a secondary activity has no marker row: the marker pickle is picked
    by modification time independently of the locations frame, and an unmatched secondary
    activity would silently count as "not a loop", so a cache pair that does not belong together
    must stop the run. ``tag`` prefixes the messages (the calibration script reuses this).
    """
    n_before_loop_join = len(df_merged)
    df_merged = df_merged.merge(df_loop_origin, on=["person_id", "activity_index"],
                                how="left", indicator="_loop_marker_match")
    if len(df_merged) != n_before_loop_join:
        raise AssertionError(
            f"{tag} loop_origin join changed row count "
            f"{n_before_loop_join} -> {len(df_merged)}: non-unique "
            "(person_id, activity_index) key in the secondary locations result?"
        )
    is_secondary_row = df_merged["purpose"].isin(_SECONDARY_PURPOSES)
    n_secondary_rows = int(is_secondary_row.sum())
    n_unmatched = int((is_secondary_row & (df_merged["_loop_marker_match"] != "both")).sum())
    if n_unmatched:
        raise RuntimeError(
            f"{tag} loop_origin: {n_unmatched}/{n_secondary_rows} secondary "
            "activities of the locations frame have no row in the secondary locations "
            "pickle; the two cached stages do not belong to the same run. Refusing to "
            "count their legs as non-loops."
        )
    logger.info("%s loop_origin join: matched %d/%d secondary activities "
                "(100%%, no unmatched row).", tag, n_secondary_rows, n_secondary_rows)
    return df_merged.drop(columns="_loop_marker_match")


# ---------------------------------------------------------------------------
# Main measurement logic
# ---------------------------------------------------------------------------

def measure_secondary_distances(working_directory: str, locations_key: Optional[str] = None):
    """Load cache and compute per-purpose euclidean secondary trip distances.

    Returns
    -------
    dict[str, pd.DataFrame]
        Per purpose (shop / leisure / other): DataFrame with columns
        ``euclidean_km`` (float) and ``mode`` (str), plus ``loop_origin`` (None on a
        destination leg, the origin kind on a loop leg) when the cache carries the loop
        marker of the secondary locations stage (issue #679). Empty DataFrame when no
        trips of that purpose are present.

    The three stages and the loop marker are the pickles of ONE run, resolved from the legs'
    locations pickle (:func:`resolve_run_pickles`). The trips are the ones the chain solver saw
    (``trips_final`` in the production workflow), i.e. the modes BEFORE the outside-tour mode
    stage; the activities are the day activities the locations were built on, and the two frames
    must hold the same ``(person_id, activity_index)`` keys (:func:`require_same_activity_keys`).
    """
    run = resolve_run_pickles(working_directory, locations_key=locations_key)
    df_locs = read_pickle(run.locations)
    df_acts = read_pickle(run.activities)
    df_trips = read_pickle(run.trips)

    logger.info(
        "Loaded locations (%d rows), activities (%d rows) and trips (%d rows).",
        len(df_locs), len(df_acts), len(df_trips),
    )
    require_same_activity_keys(df_locs, df_acts)

    # Merge geometry onto activities using (person_id, activity_index) as the
    # join key.  The locations stage stores one geometry per (person_id,
    # activity_index); activities stores one row per activity with its purpose.
    # Both stages have the same (person_id, activity_index) index.
    df_acts = df_acts.reset_index(drop=True)
    df_locs = df_locs.reset_index(drop=True)

    # Extract x/y from geometry (EPSG:25832).
    df_xy = df_locs[["person_id", "activity_index"]].copy()
    df_xy["x_m"] = df_locs["geometry"].x
    df_xy["y_m"] = df_locs["geometry"].y

    # Merge purpose onto coordinates.
    df_merged = df_xy.merge(
        df_acts[["person_id", "activity_index", "purpose"]],
        on=["person_id", "activity_index"],
        how="inner",
    )
    logger.info(
        "Merged locations+activities: %d rows (from %d locations, %d activities).",
        len(df_merged), len(df_locs), len(df_acts),
    )

    # Loop marker of the secondary locations result (issue #679): the leg INTO a marked
    # activity is the loop leg.
    df_loop_origin = load_loop_origin(working_directory, run=run)
    if df_loop_origin is not None:
        df_merged = attach_loop_marker(df_merged, df_loop_origin)

    # Sort by (person_id, activity_index) to ensure leg ordering is correct.
    df_merged = df_merged.sort_values(["person_id", "activity_index"]).reset_index(drop=True)

    # Build per-person shifted DataFrame to get the PREVIOUS activity's
    # location.  A leg from activity i-1 to activity i is valid only when both
    # activities belong to the same person.
    df_prev = df_merged[["person_id", "x_m", "y_m"]].shift(1).rename(
        columns={"person_id": "prev_person_id", "x_m": "prev_x", "y_m": "prev_y"}
    )
    df_legs = pd.concat([df_merged, df_prev], axis=1)

    # Drop rows where the previous activity belongs to a different person
    # (i.e. the first activity of each person has no valid predecessor).
    same_person_mask = df_legs["person_id"] == df_legs["prev_person_id"]
    n_dropped = (~same_person_mask).sum()
    n_total = len(df_legs)
    logger.info(
        "Leg construction: %d/%d legs are within-person (dropped %d first-activity rows).",
        same_person_mask.sum(), n_total, n_dropped,
    )
    df_legs = df_legs[same_person_mask].copy()

    # Keep only secondary purposes.
    secondary_mask = df_legs["purpose"].isin(_SECONDARY_PURPOSES)
    n_secondary = secondary_mask.sum()
    logger.info(
        "[secondary-validate] secondary legs (shop/leisure/other): %d / %d total legs.",
        n_secondary, len(df_legs),
    )
    df_secondary = df_legs[secondary_mask].copy()

    # Compute euclidean distance in km (EPSG:25832 -> metres -> km).
    dx = df_secondary["x_m"].values - df_secondary["prev_x"].values
    dy = df_secondary["y_m"].values - df_secondary["prev_y"].values
    df_secondary = df_secondary.copy()
    df_secondary["euclidean_km"] = np.sqrt(dx * dx + dy * dy) / 1000.0

    # Join mode from the trips stage.  In the synthesis pipeline the leg that
    # brings a person TO activity_index=k corresponds to trip_index=k in
    # synthesis.population.trips (activities.py sets activity_index = trip_index).
    df_mode = df_trips[["person_id", "trip_index", "mode"]].rename(
        columns={"trip_index": "activity_index"}
    )
    n_before_mode_join = len(df_secondary)
    df_secondary = df_secondary.merge(
        df_mode, on=["person_id", "activity_index"], how="left"
    )
    if len(df_secondary) != n_before_mode_join:
        raise AssertionError(
            f"[secondary-validate] secondary mode-join changed row count "
            f"{n_before_mode_join} -> {len(df_secondary)}: "
            "non-unique (person_id, activity_index) key in trips stage?"
        )
    n_mode_missing = df_secondary["mode"].isna().sum()
    if n_mode_missing > 0:
        logger.warning(
            "[secondary-validate] mode join: %d/%d legs missing mode; defaulting to 'car'.",
            n_mode_missing, n_before_mode_join,
        )
        df_secondary["mode"] = df_secondary["mode"].fillna("car")
    else:
        logger.info(
            "[secondary-validate] mode join: primary %d/%d (100%%, no fallback).",
            n_before_mode_join, n_before_mode_join,
        )

    # Split by purpose and return per-leg DataFrames with euclidean_km + mode (+ loop_origin
    # when the cache carries the marker).
    leg_columns = ["euclidean_km", "mode"]
    if df_loop_origin is not None:
        leg_columns.append(LOOP_ORIGIN_COLUMN)
    result: dict[str, pd.DataFrame] = {}
    for purpose in _SECONDARY_PURPOSES:
        mask = df_secondary["purpose"] == purpose
        df_purpose = df_secondary.loc[mask, leg_columns].copy()
        result[purpose] = df_purpose
        n_legs = len(df_purpose)
        mean_euc = float(df_purpose["euclidean_km"].mean()) if n_legs > 0 else float("nan")
        logger.info(
            "[secondary-validate] purpose=%s: %d legs, mean euclidean=%.2f km.",
            purpose, n_legs, mean_euc,
        )

    # Log primary-path rate (CLAUDE.md no-silent-fallback): all legs derived
    # directly from the cached synthesis locations -- there is no fallback path
    # in this measurement step.
    logger.info(
        "[secondary-validate] measurement: primary (direct from cache) %d/%d (100%%); "
        "no fallback path exists.",
        n_secondary, n_secondary,
    )

    return result


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_report(distances_by_purpose: dict, w12_targets: dict) -> list[dict]:
    """Build a per-purpose comparison row list.

    Parameters
    ----------
    distances_by_purpose : dict[str, pd.DataFrame]
        Per purpose: DataFrame with columns ``euclidean_km`` and ``mode``.
    w12_targets : dict
        Output of load_w12_band_shares: includes purpose band shares + mean_km.

    Returns
    -------
    list[dict]
        One dict per purpose with keys:
        purpose, n, mean_euclidean_km, mean_routed_equiv_km,
        w12_mean_km, emd_vs_w12.
    """
    rows = []
    for purpose in _SECONDARY_PURPOSES:
        df_purpose = distances_by_purpose.get(purpose, pd.DataFrame(columns=["euclidean_km", "mode"]))
        n = len(df_purpose)
        if n == 0:
            rows.append({
                "purpose": purpose,
                "n": 0,
                "mean_euclidean_km": float("nan"),
                "mean_routed_equiv_km": float("nan"),
                "w12_mean_km": w12_targets.get(f"{purpose}_mean_km", float("nan")),
                "emd_vs_w12": float("nan"),
            })
            logger.warning(
                "[secondary-validate] purpose=%s: no legs found; EMD reported as NaN.",
                purpose,
            )
            continue

        mean_euc = float(df_purpose["euclidean_km"].mean())

        # Per-leg routed-equivalent: scale each leg by its mode's circuity network
        # using the constant detour factor (mode="constant", the default). The
        # distance-dependent curve is opt-in only (mode="curve") and was measured
        # immaterial for ZGB secondary trips (EMD delta ~0.003).
        # routed is computed ONCE and reused for both the mean and the band shares.
        routed_km = np.empty(n, dtype=float)
        for network, grp_idx in df_purpose.groupby(
            df_purpose["mode"].map(mode_to_network)
        ).groups.items():
            sl = df_purpose.index.get_indexer(list(grp_idx))
            routed_km[sl] = circuity.euclidean_to_routed(
                df_purpose.loc[grp_idx, "euclidean_km"].to_numpy(), network,
                mode="constant",
            )

        mean_routed = float(np.mean(routed_km))

        # Band shares on the W12 9-band grid (after per-leg circuity scaling).
        model_shares = _w12_band_shares(routed_km)
        target_shares = w12_targets[purpose]
        emd = emd_on_bands(model_shares, target_shares)

        rows.append({
            "purpose": purpose,
            "n": n,
            "mean_euclidean_km": mean_euc,
            "mean_routed_equiv_km": mean_routed,
            "w12_mean_km": w12_targets.get(f"{purpose}_mean_km", float("nan")),
            "emd_vs_w12": emd,
        })

    return rows


def print_table(rows: list[dict], survey_includes_loops: bool = False) -> None:
    """Print a formatted per-purpose comparison table.

    ``survey_includes_loops`` adds the footnote that the W12 side still holds the strolls and
    dog walks the model side left out (issue #679); it is set exactly when loops were left out.
    """
    header = (
        f"{'purpose':<10}  {'n':>8}  "
        f"{'mean_euc_km':>12}  {'mean_routed_km':>14}  "
        f"{'w12_mean_km':>12}  {'emd_vs_w12':>12}"
    )
    sep = "-" * len(header)
    print("\n" + sep)
    print("Secondary trip distances: model vs MiD 2023 W12")
    print("  Routed-equiv = per-leg circuity scaling by mode network (car/pt/walk)")
    print("  EMD computed on the 9 W12 distance bands after per-leg circuity scaling")
    print(sep)
    print(header)
    print(sep)
    for row in rows:
        print(
            f"{row['purpose']:<10}  {row['n']:>8d}  "
            f"{row['mean_euclidean_km']:>12.2f}  {row['mean_routed_equiv_km']:>14.2f}  "
            f"{row['w12_mean_km']:>12.1f}  {row['emd_vs_w12']:>12.4f}"
        )
    print(sep)
    if survey_includes_loops:
        print("* W12 (published MiD table over all trips of the purpose) INCLUDES strolls and "
              "dog walks; the model rows above leave loop legs out.")
    print()


#: Name of the CSV column that carries the survey-side loop caveat with the numbers.
SURVEY_INCLUDES_LOOPS_COLUMN = "survey_includes_loops"


def mark_survey_includes_loops(rows: list[dict]) -> list[dict]:
    """Add ``survey_includes_loops = True`` to every CSV row (issue #679).

    Used exactly when loop legs were left out on the model side: the W12 survey side of every
    row still holds them, and the caveat must travel with the numbers, not only with the
    printed text. Without left-out loops the column does not exist.
    """
    return [dict(row, **{SURVEY_INCLUDES_LOOPS_COLUMN: True}) for row in rows]


#: ``purpose`` value of the CSV row that reports the left-out loop legs.
LOOP_ROW_PURPOSE = "loops_left_out"


def loop_csv_row(loop_summary: pd.DataFrame) -> dict:
    """The one CSV row that reports the loop legs left out of the comparison (issue #679).

    ``n`` is the loop count over all secondary purposes and ``loop_share_of_secondary_legs``
    its share of all secondary legs before leaving them out; the per-purpose split is in the log
    and the printed summary. The column exists in the CSV only when loops were identified.
    """
    n_loops = int(loop_summary["n_loops"].sum())
    n_legs = int(loop_summary["n_legs"].sum())
    return {"purpose": LOOP_ROW_PURPOSE, "n": n_loops,
            "loop_share_of_secondary_legs": n_loops / n_legs if n_legs else float("nan")}


def print_loop_summary(loop_summary: pd.DataFrame, w12_path: str) -> None:
    """Print and log the loop legs left out, per purpose, and which survey table the model is
    compared with (issue #679).

    The survey side of this comparison is the published W12 table, which is NOT filtered for
    strolls and dog walks; the loop-free MiD reference is a different table (subtype groups,
    ``scripts/extract_mid_w_zwd_groups.py --without-loops``) that this EMD comparison does not
    read. The summary says so instead of implying that both sides are loop-free.
    """
    print("Loop legs (strolls, dog walks) left out of the model side above:")
    for _, row in loop_summary.iterrows():
        logger.info("[secondary-validate] loops left out: purpose=%s %d/%d legs (%.2f%%).",
                    row["purpose"], row["n_loops"], row["n_legs"], 100.0 * row["loop_share"])
        print(f"  {row['purpose']:<10}  {int(row['n_loops']):>8d} of {int(row['n_legs']):>8d} legs"
              f"  ({100.0 * row['loop_share']:.2f} %)")
    print(f"Survey side: {w12_path}")
    print("  (published MiD W12: all trips of the purpose, NOT filtered for W_ZWD 710/711; no "
          "loop-free W12 exists.")
    print("  The loop-free MiD reference is written by scripts/extract_mid_w_zwd_groups.py "
          "--without-loops; this comparison does not read it.)\n")


# ---------------------------------------------------------------------------
# Subtype validation additions (issue #127, Task 6)
#
# Optional column names a future cache MIGHT expose, carrying the internal
# leisure/other subtype label per leg and the placed candidate's residential
# ("leisure_visit") potential value. As of THIS task the synpp stage output
# does NOT persist either: secondary_chainsolvers._extract_locations maps the
# internal subtype activity (e.g. "leisure_visit") back to the aggregate
# eqasim purpose ("leisure") before the locations/activities stages are
# cached, so no currently-cached stage carries the subtype label past that
# point. The checks below are written against these column names so they
# activate automatically the moment a future stage change exposes them (no
# invented data meanwhile); until then each logs an explicit, honest skip
# rather than silently omitting the check (CLAUDE.md "no silent fallback").
#
# The equivalent LIVE transparency signal for the "leisure_excursion"
# boundary-clip effect is already wired directly into the chainsolvers run
# log (see secondary_chainsolvers._excursion_boundary_clip_summary), because
# that data is available inside the solve step, before the subtype label is
# discarded -- this script-side check covers the complementary per-group
# mean-distance and leisure_visit-placement signals, which are not available
# without persisting the label.
# ---------------------------------------------------------------------------

_SUBTYPE_ACTIVITY_COLUMN = "activity_subtype"
_VISIT_POTENTIAL_COLUMN = "pot_visit"


def build_subtype_report(distances_by_purpose: dict) -> None:
    """Log per-group realised distances and the leisure_visit placement share.

    Both checks require the internal subtype label (and, for the placement
    check, the placed candidate's ``pot_visit`` value) on the realised legs
    frame -- see the module note above for why neither is present on the
    frames returned by ``measure_secondary_distances`` today. This function
    checks for the columns and either reports the real numbers (once a future
    stage exposes them) or logs a clear, traceable "not available in this
    cache" line -- it never fabricates a result.
    """
    any_subtype_column_found = False
    for purpose, df_purpose in distances_by_purpose.items():
        if _SUBTYPE_ACTIVITY_COLUMN not in df_purpose.columns:
            continue
        any_subtype_column_found = True
        summary = per_group_distance_summary(df_purpose, _SUBTYPE_ACTIVITY_COLUMN, "euclidean_km")
        for _, row in summary.iterrows():
            group = row["group"]
            logger.info(
                "[secondary-validate] subtype=%s: %d legs, mean realised=%.2f km.",
                group, row["n"], row["mean_distance"],
            )
            donor_range = SUBTYPE_DONOR_MEAN_KM_RANGE.get(group)
            if donor_range is not None:
                lo, hi = donor_range
                logger.info(
                    "[secondary-validate] in-sample sanity (NOT a validated target "
                    "-- ad-hoc donor measurement recorded in ADR-0116; the frame and "
                    "the route are stated at braunschweig.calibration."
                    "secondary_measurement.SUBTYPE_DONOR_MEAN_KM_RANGE): %s realised "
                    "mean %.1f km vs measured donor mean %.1f-%.1f km.",
                    group, row["mean_distance"], lo, hi,
                )
    if not any_subtype_column_found:
        logger.info(
            "[secondary-validate] per-group subtype distances: NOT available in "
            "this cache (the '%s' column is not yet exposed by the cached stage "
            "output -- see issue #127 Task 6 follow-up). Skipping subtype rows.",
            _SUBTYPE_ACTIVITY_COLUMN,
        )

    df_leisure = distances_by_purpose.get("leisure", pd.DataFrame())
    has_subtype = _SUBTYPE_ACTIVITY_COLUMN in df_leisure.columns
    has_pot_visit = _VISIT_POTENTIAL_COLUMN in df_leisure.columns
    if has_subtype and has_pot_visit:
        mask = df_leisure[_SUBTYPE_ACTIVITY_COLUMN] == "leisure_visit"
        share, n_pos, n_total = placement_share_at_positive_potential(
            df_leisure, _VISIT_POTENTIAL_COLUMN, group_mask=mask,
        )
        share_pct = 100.0 * share if n_total else float("nan")
        logger.info(
            "[secondary-validate] leisure_visit placement share (pot_visit > 0): "
            "%d/%d (%.1f%%; expected ~100%% when leisure_visit_building_potential "
            "is ON).",
            n_pos, n_total, share_pct,
        )
    else:
        logger.info(
            "[secondary-validate] leisure_visit placement share: NOT available in "
            "this cache (neither '%s' nor '%s' is exposed by the cached stage "
            "output -- see issue #127 Task 6 follow-up). Skipping.",
            _SUBTYPE_ACTIVITY_COLUMN, _VISIT_POTENTIAL_COLUMN,
        )


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description=(
            "Measure realised secondary trip distances per purpose and compare "
            "to MiD 2023 Tabelle A W12 via EMD. Reads only cached synpp stage "
            "pickles -- does NOT run synpp."
        )
    )
    parser.add_argument(
        "--cache", default=_DEFAULT_CACHE,
        help=(
            "Directory containing synpp stage pickles "
            f"(default: {_DEFAULT_CACHE!r})."
        ),
    )
    parser.add_argument(
        "--mid-dir", default=_DEFAULT_MID_DIR,
        help=(
            "Directory containing the MiD W12 CSV "
            f"(default: {_DEFAULT_MID_DIR!r})."
        ),
    )
    parser.add_argument(
        "--output", default=None,
        help="Optional path to write the per-purpose comparison CSV.",
    )
    parser.add_argument(
        "--locations-key", default=None,
        help=(
            "Cache key (<stage>__<hash>, the pickle name without .p) of the locations pickle of "
            "the run to validate. Needed only when the cache holds several candidates (hashed "
            "variants of a copied cache); a unique one is taken without it, an ambiguous cache is "
            "refused."
        ),
    )
    args = parser.parse_args()

    # ------------------------------------------------------------------
    # 1. Validate input paths
    # ------------------------------------------------------------------
    if not os.path.isdir(args.cache):
        raise RuntimeError(
            f"Cache directory not found: '{args.cache}'. "
            "Ensure --cache points to a valid synpp working directory."
        )
    if not os.path.isdir(args.mid_dir):
        raise RuntimeError(
            f"MiD directory not found: '{args.mid_dir}'. "
            "Ensure --mid-dir points to the MiD data directory."
        )

    # ------------------------------------------------------------------
    # 2. Load W12 targets
    # ------------------------------------------------------------------
    w12 = load_w12_band_shares(args.mid_dir)
    w12_path = os.path.join(args.mid_dir, "mid2023_W12_triplength_by_purpose.csv")
    logger.info(
        "[secondary-validate] W12 targets loaded: shop %.1f km, leisure %.1f km, other %.1f km.",
        w12["shop_mean_km"], w12["leisure_mean_km"], w12["other_mean_km"],
    )

    # ------------------------------------------------------------------
    # 3. Measure realised secondary distances from cache
    # ------------------------------------------------------------------
    distances = measure_secondary_distances(args.cache, locations_key=args.locations_key)

    # ------------------------------------------------------------------
    # 4. Build and print comparison table
    # ------------------------------------------------------------------
    # Loops (issue #679): with secondary_loop_zero_displacement ON a stroll or dog walk is placed
    # at the place before it, so its leg has no length. They are left out here and reported
    # on their own; without the marker column nothing changes.
    distances, loop_summary = leave_out_loop_legs(distances)
    rows = build_report(distances, w12)
    print_table(rows, survey_includes_loops=loop_summary is not None)
    if loop_summary is not None:
        print_loop_summary(loop_summary, w12_path)

    # ------------------------------------------------------------------
    # 4b. Subtype validation additions (issue #127, Task 6): per-group
    # realised distances (in-sample sanity vs the spec's donor means) and the
    # leisure_visit residential-placement share. See build_subtype_report's
    # docstring for why these currently log an honest "not available" rather
    # than real numbers on this cache.
    # ------------------------------------------------------------------
    build_subtype_report(distances)

    # ------------------------------------------------------------------
    # 5. Write optional CSV output
    # ------------------------------------------------------------------
    if args.output:
        df_out = pd.DataFrame(rows if loop_summary is None else mark_survey_includes_loops(
            rows + [loop_csv_row(loop_summary)]))
        df_out.to_csv(args.output, index=False)
        logger.info("[secondary-validate] wrote comparison CSV to '%s'.", args.output)

    # Log a note that the ON path (with purpose-resolved distributions enabled)
    # requires a synpp re-run with the feature flags set to true -- that step is
    # deferred and must be run on the server where the all-features popsim cache lives.
    logger.info(
        "[secondary-validate] This is the OFF-path baseline "
        "(secondary_purpose_distributions: false). "
        "The ON comparison requires a synpp re-run with the feature flags enabled "
        "-- that step is DEFERRED (server run)."
    )


if __name__ == "__main__":
    main()
