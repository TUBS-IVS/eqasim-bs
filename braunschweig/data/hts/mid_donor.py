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
KEY_DEMOGRAPHICS = "cordon_incommuter_donor_demographics"

# These transformations live outside the stage file synpp hashes itself.
_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.popsim.expand",
    "braunschweig.popsim.attributes",
    "braunschweig.popsim.trips",
    "braunschweig.popsim.sources.mid",
    "braunschweig.popsim.mid.donor",
)


def validate(context):
    """Invalidate cached donors when a directly used MiD helper changes."""
    import hashlib
    import importlib
    import inspect

    digest = hashlib.md5()
    for name in _DEFERRED_HELPER_MODULE_NAMES:
        digest.update(inspect.getsource(importlib.import_module(name)).encode("utf-8"))
    return digest.hexdigest()


def _map_donor_demographics(persons, rng):
    """Use the resident MiD coding, without accepting an all-default donor pool."""
    from braunschweig.popsim.expand import map_demographics

    required = {"HP_ALTER", "HP_SEX"}
    missing = required - set(persons.columns)
    if missing:
        raise ValueError(f"[mid_donor] missing raw MiD demographics: {sorted(missing)}")
    age = persons["HP_ALTER"].to_numpy(dtype=float)
    if not (np.isfinite(age) & (age >= 0) & (age == np.floor(age))).all():
        raise ValueError("[mid_donor] HP_ALTER must contain finite non-negative integer ages")
    observed = persons["HP_SEX"].isin([1, 2])
    n, n_primary = len(persons), int(observed.sum())
    n_fallback = n - n_primary
    log = _log.warning if n_fallback else _log.info
    log("[mid_donor] sex: primary %d/%d (%.1f%%), fallback %d/%d (%.1f%%); "
        "non-binary/missing codes use the observed MiD pool, seeded by random_seed",
        n_primary, n, 100.0 * n_primary / max(n, 1),
        n_fallback, n, 100.0 * n_fallback / max(n, 1))
    if n and not n_primary:
        raise ValueError("[mid_donor] HP_SEX has no observed binary-sex pool for imputation")
    # A missing age-band key must use the global observed pool, not be skipped by
    # pandas groupby in the shared mapper. Give just those rows an empty band.
    mapping_input = persons.copy()
    if "alter_gr1" in mapping_input:
        mapping_input["alter_gr1"] = mapping_input["alter_gr1"].astype(object)
        mapping_input.loc[~observed & mapping_input["alter_gr1"].isna(), "alter_gr1"] = "missing"
    mapped = map_demographics(mapping_input, rng=rng)
    if not mapped["sex"].isin(["male", "female"]).all():
        raise ValueError("[mid_donor] HP_SEX mapping left unresolved donor demographics")
    _log.info("[mid_donor] age: primary %d/%d (%.1f%%), fallback 0/%d (0.0%%)",
              n, n, 100.0 if n else 0.0, n)
    # Preserve the raw donor attributes, including the original age-band keys.
    out = persons.copy()
    out[["age", "sex"]] = mapped[["age", "sex"]]
    return out


def build_mid_donor_frames(households, persons, wege, rng, *, preserve_demographics=True):
    """Pure transform: raw MiD (households, persons, wege) -> in-commuter donor
    (households, persons, trips).

    ``persons`` gets a stable per-(H_ID, P_ID) ``person_id`` plus boolean
    ``employed``/``studies`` and MiD ``age``/``sex`` via the shared mappers.
    ``preserve_demographics=False`` retains the legacy optional-column adapter,
    including its downstream scalar defaults on raw input (#397). ``trips``
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
    if preserve_demographics:
        # Map after employment so its imputation retains the previous RNG stream.
        p = _map_donor_demographics(p, rng)
    else:
        for column in ("age", "sex"):
            n_primary = len(p) if column in p else 0
            _log.warning(
                "[mid_donor] legacy %s: primary %d/%d (%.1f%%), fallback %d/%d (%.1f%%); "
                "missing columns trigger worker/student scalar defaults",
                column, n_primary, len(p), 100.0 * n_primary / max(len(p), 1),
                len(p) - n_primary, len(p), 100.0 * (len(p) - n_primary) / max(len(p), 1))

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
    context.config(KEY_DEMOGRAPHICS, True)


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

    # configure() declares KEY_MID with DEFAULT_MID_RAW_PATH as its default, so the
    # single-argument execute-time read returns that default when a cordon-enabled
    # config does not set mid_raw_path explicitly. (ExecuteContext.config() takes the
    # key alone; passing a default here would crash the stage at runtime.)
    mid_dir = context.config(KEY_MID)
    households, persons, wege = MidSource().load_donor(mid_dir)
    rng = np.random.RandomState(int(context.config("random_seed")))
    return build_mid_donor_frames(
        households, persons, wege, rng,
        preserve_demographics=context.config(KEY_DEMOGRAPHICS))
