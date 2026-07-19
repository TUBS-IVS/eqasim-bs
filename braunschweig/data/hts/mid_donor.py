"""MiD 2023 in-commuter HTS donor stage.

Returns (households, persons, trips) shaped like eqasim's data.hts.selected, but
sourced from the German MiD survey so the cordon in-commuter stages (SvB +
student) draw their trip TIMING from German behaviour instead of the French
ENTD. Distance/mode/count/origin of in-commuters come from other (German)
sources and are unaffected. Reuses the tested popsim MiD machinery
(braunschweig.popsim.attributes.map_employed/map_studies,
braunschweig.popsim.trips.build_trip_table); no reimplementation. See
docs/superpowers/specs/2026-07-18-incommuter-mid-donor-design.md.
"""
from __future__ import annotations

import logging

import numpy as np

from braunschweig.popsim.stage import KEY_MID

_log = logging.getLogger(__name__)

# Canonical committed MiD 2023 raw survey directory, identical to the default
# every popsim config uses for KEY_MID. Cordon in-commuter donor draws from
# this stage whenever cordon_enabled is set (braunschweig/population/pipeline
# in-commuter stages), independent of population.method / hts choice, so a
# config that enables cordon but never sets mid_raw_path explicitly (e.g. an
# ENTD-resident config) must still resolve to a usable path instead of
# hard-failing at synpp graph-build.
DEFAULT_MID_RAW_PATH = "eqasim-data/data/braunschweig/popsim/mid2023_raw"


def build_mid_donor_frames(households, persons, wege, rng):
    """Pure transform: raw MiD (households, persons, wege) -> in-commuter donor
    (households, persons, trips).

    ``persons`` gets a stable per-(H_ID, P_ID) ``person_id`` plus boolean
    ``employed``/``studies`` (via the shared MiD attribute mappers); ``trips``
    is the eqasim trip table (home-first, purposes + float departure/arrival
    seconds) built by the shared MiD trip-table builder. ``households`` is
    passed through unchanged (only used here for the donor-pool size log).

    Raises
    ------
    ValueError
        If (H_ID, P_ID) does not uniquely identify a person (person_id
        collision); this would silently corrupt the donor join downstream.
    """
    from braunschweig.popsim import attributes, trips as trips_mod

    p = persons.copy()
    p["person_id"] = p["H_ID"].astype(str) + "_" + p["P_ID"].astype(str)
    if not p["person_id"].is_unique:
        raise ValueError("[mid_donor] non-unique (H_ID, P_ID) -> person_id collision")
    p = attributes.map_employed(p, rng=rng)
    p = attributes.map_studies(p)

    trips = trips_mod.build_trip_table(
        p[["person_id", "H_ID", "P_ID"]], wege,
        household_col="H_ID", person_col="P_ID", trip_col="W_ID")

    keep = ["person_id", "employed", "studies"]
    for optional_col in ("age", "sex"):
        if optional_col in p.columns:
            keep.append(optional_col)
    persons_out = p[keep].reset_index(drop=True)

    # No-silent-fallback observability (CLAUDE.md): the donor pool that later
    # cordon in-commuter stages draw commute timing from is exactly the set of
    # persons with a work/education trip leg here; a collapsed pool would
    # silently starve the in-commuter timing draw.
    n_work = trips[trips["following_purpose"] == "work"]["person_id"].nunique()
    n_edu = trips[trips["following_purpose"] == "education"]["person_id"].nunique()
    _log.info(
        "[mid_donor] %d households, %d persons, %d trips; donor pool: %d with a "
        "work leg, %d with an education leg",
        len(households), len(persons_out), len(trips), n_work, n_edu)
    return households, persons_out, trips


def configure(context):
    # Any cordon-enabled config without an explicit mid_raw_path falls back to
    # the canonical committed MiD path, so enabling cordon never hard-fails at
    # graph-build; a config that needs a different MiD dir sets the key
    # explicitly (as the popsim configs do).
    context.config(KEY_MID, DEFAULT_MID_RAW_PATH)
    context.config("random_seed")


def execute(context):
    """synpp stage entry point: load the raw MiD donor tables and build the
    in-commuter HTS donor frames.

    Consumes ``mid_raw_path`` (KEY_MID) verbatim, matching the sibling popsim
    stages (braunschweig.popsim.stage, braunschweig.popsim.completed_donor);
    falls back to ``DEFAULT_MID_RAW_PATH`` when the config does not set the
    key explicitly (see ``configure``/``DEFAULT_MID_RAW_PATH``). The
    configured path is repo-root-relative (e.g.
    "eqasim-data/data/braunschweig/popsim/mid2023_raw").
    """
    from braunschweig.popsim.sources.mid import MidSource

    # See configure(): falls back to DEFAULT_MID_RAW_PATH when a cordon-enabled
    # config does not set mid_raw_path explicitly.
    mid_dir = context.config(KEY_MID, DEFAULT_MID_RAW_PATH)
    households, persons, wege = MidSource().load_donor(mid_dir)
    rng = np.random.RandomState(int(context.config("random_seed")))
    return build_mid_donor_frames(households, persons, wege, rng)
