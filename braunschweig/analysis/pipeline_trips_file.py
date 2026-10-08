"""Which written trips file a diary validator reads (eqasim-bs#442, ADR-0141).

The synthesis output writes ``<prefix>trips.csv`` from ``synthesis.population.trips.final``. With
``braunschweig.portal.enabled`` true that table is the day AFTER the portal rewrite: a far destination carries the
purpose ``outside`` and the inner legs of a stay are dropped. A validator that compares the written diary with a
travel survey (purpose distribution, work/education participation, trips per person) must see the donor day instead,
so the output stage additionally writes ``<prefix>trips_pre_portal.csv`` (``config_keys.PRE_PORTAL_TRIPS_FILE_STEM``)
and the readers of this module's family take it when it exists and is current (not older than ``trips.csv``).

A reader that needs the realised plan (for example one that joins the trips with MATSim output by
``(person_id, trip_index)``) does NOT use this module: the MATSim mode-choice trip indices refer to the
post-portal table, which stays in ``<prefix>trips.csv``.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from braunschweig.synthesis.portal_trips.config_keys import (
    PRE_PORTAL_COMMUTES_FILE_STEM, PRE_PORTAL_TRIPS_FILE_STEM)

LOGGER = logging.getLogger(__name__)

#: A pre-portal file older than ``trips.csv`` by more than this many seconds is treated as left over from an earlier run
#: in the same output directory and ignored. Both files are written by one output stage, the pre-portal one last, so a
#: genuine pair differs by seconds at most (and never in this direction); the slack absorbs file copies that do not
#: preserve modification times exactly.
STALE_AFTER_SECONDS = 60.0


def is_pre_portal_trips_path(path: Path | str) -> bool:
    """True when ``path`` names a ``<prefix>trips_pre_portal.csv`` file (as returned by the resolver)."""
    return Path(path).name.endswith(f"{PRE_PORTAL_TRIPS_FILE_STEM}.csv")


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")


def _is_stale(pre_portal_path: Path, current_path: Path) -> bool:
    """True (with a warning naming both files and mtimes) when the pre-portal file is older than the current one."""
    if not current_path.exists():
        return False
    age_seconds = current_path.stat().st_mtime - pre_portal_path.stat().st_mtime
    if age_seconds <= STALE_AFTER_SECONDS:
        return False
    LOGGER.warning(
        "%s (mtime %s) is older than %s (mtime %s) by %.0f s: it stems from an earlier run in %s and is ignored; "
        "%s is read instead. Delete the stale file to silence this warning.",
        pre_portal_path.name, _iso_mtime(pre_portal_path), current_path.name, _iso_mtime(current_path), age_seconds,
        pre_portal_path.parent, current_path.name)
    return True


def resolve_pre_portal_commutes_path(directory: Path | str, prefix: str) -> Path | None:
    """Path of ``<prefix>commutes_pre_portal.gpkg`` when it exists and is current, else ``None``.

    The file holds the home -> work lines (layer 1) and home -> education lines (layer ``education``) built from the
    assigned primary locations while the portal layer is on; ``<prefix>commutes.gpkg`` lacks the far commuters. The
    same staleness rule as for the trips applies, measured against ``<prefix>commutes.gpkg``: an older file is
    ignored with a warning naming both files and their modification times. The choice is logged.
    """
    directory = Path(directory)
    pre_portal_path = directory / f"{prefix}{PRE_PORTAL_COMMUTES_FILE_STEM}.gpkg"
    if not pre_portal_path.exists():
        return None
    if _is_stale(pre_portal_path, directory / f"{prefix}commutes.gpkg"):
        return None
    LOGGER.info(
        "Reading the pre-portal commutes %s instead of %s: the latter is built from the written activities, where "
        "a far workplace is an 'outside' activity (eqasim-bs#442).", pre_portal_path.name, f"{prefix}commutes.gpkg")
    return pre_portal_path


def resolve_pipeline_trips_path(directory: Path | str, prefix: str) -> Path | None:
    """Path of the trips CSV a diary validator should read, or ``None`` when neither file exists.

    Prefers ``<directory>/<prefix>trips_pre_portal.csv`` (donor purposes, written only while the portal layer is on)
    over ``<directory>/<prefix>trips.csv`` and logs which file was chosen and why. Without the pre-portal file the
    behaviour is that of reading ``<prefix>trips.csv`` alone. A pre-portal file that is older than ``trips.csv`` by
    more than ``STALE_AFTER_SECONDS`` is NOT used: it stems from an earlier run of the directory (for example one
    with the portal layer on, followed by one with it off), so it would describe another population. A warning
    names both files and their modification times and ``trips.csv`` is returned.
    """
    directory = Path(directory)
    pre_portal_path = directory / f"{prefix}{PRE_PORTAL_TRIPS_FILE_STEM}.csv"
    trips_path = directory / f"{prefix}trips.csv"
    if pre_portal_path.exists():
        if _is_stale(pre_portal_path, trips_path):
            return trips_path
        LOGGER.info(
            "Reading the pre-portal trips %s instead of %s: with the portal layer on the latter carries the purpose "
            "'outside' for far destinations and has the inner legs of a stay removed, which a diary validation "
            "against a survey must not see (eqasim-bs#442).", pre_portal_path.name, trips_path.name)
        return pre_portal_path
    if trips_path.exists():
        return trips_path
    return None
