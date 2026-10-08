"""Which written trips file a diary validator reads (eqasim-bs#442, ADR-0141).

The synthesis output writes ``<prefix>trips.csv`` from ``synthesis.population.trips.final``. With
``braunschweig.portal.enabled`` true that table is the day AFTER the portal rewrite: a far destination carries the
purpose ``outside`` and the inner legs of a stay are dropped. A validator that compares the written diary with a
travel survey (purpose distribution, work/education participation, trips per person) must see the donor day instead,
so the output stage additionally writes ``<prefix>trips_pre_portal.csv`` (``config_keys.PRE_PORTAL_TRIPS_FILE_STEM``)
and the readers of this module's family take it when it exists.

A reader that needs the realised plan (for example one that joins the trips with MATSim output by
``(person_id, trip_index)``) does NOT use this module: the MATSim mode-choice trip indices refer to the
post-portal table, which stays in ``<prefix>trips.csv``.
"""
from __future__ import annotations

import logging
from pathlib import Path

from braunschweig.synthesis.portal_trips.config_keys import PRE_PORTAL_TRIPS_FILE_STEM

LOGGER = logging.getLogger(__name__)

#: A pre-portal file older than ``trips.csv`` by more than this many seconds probably stems from an earlier run in
#: the same output directory. Both files are written by one output stage, the pre-portal one last, so a genuine pair
#: differs by seconds at most; the slack absorbs file copies that do not preserve modification times exactly.
STALE_AFTER_SECONDS = 60.0


def resolve_pipeline_trips_path(directory: Path | str, prefix: str) -> Path | None:
    """Path of the trips CSV a diary validator should read, or ``None`` when neither file exists.

    Prefers ``<directory>/<prefix>trips_pre_portal.csv`` (donor purposes, written only while the portal layer is on)
    over ``<directory>/<prefix>trips.csv`` and logs which file was chosen and why. Without the pre-portal file the
    behaviour is that of reading ``<prefix>trips.csv`` alone. A pre-portal file that is older than ``trips.csv`` is
    still used but reported with a warning, because it may be left over from an earlier run of that directory.
    """
    directory = Path(directory)
    pre_portal_path = directory / f"{prefix}{PRE_PORTAL_TRIPS_FILE_STEM}.csv"
    trips_path = directory / f"{prefix}trips.csv"
    if pre_portal_path.exists():
        LOGGER.info(
            "Reading the pre-portal trips %s instead of %s: with the portal layer on the latter carries the purpose "
            "'outside' for far destinations and has the inner legs of a stay removed, which a diary validation "
            "against a survey must not see (eqasim-bs#442).", pre_portal_path.name, trips_path.name)
        if trips_path.exists():
            age_seconds = trips_path.stat().st_mtime - pre_portal_path.stat().st_mtime
            if age_seconds > STALE_AFTER_SECONDS:
                LOGGER.warning(
                    "%s is older than %s by %.0f s; it may stem from an earlier run in %s. Delete it if this run "
                    "was made with braunschweig.portal.enabled false.",
                    pre_portal_path.name, trips_path.name, age_seconds, directory)
        return pre_portal_path
    if trips_path.exists():
        return trips_path
    return None
