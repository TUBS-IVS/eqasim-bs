"""synpp stage: the eqasim synthesis output on the REPORTING-DAY view (ADR-0104, #244).

Aliased to ``synthesis.output``. Two things distinguish it from the vendored
``synthesis.output``:

1. **The finished day.** ``persons.csv``, ``activities.csv``, ``trips.csv`` and the spatial
   exports must describe the day the simulation actually runs, so the trips and activities
   frames come from ``synthesis.population.trips.final`` / ``...activities.final`` instead of
   the pre-assignment views.
2. **The ``commute_day_state`` person attribute.** The drawn reporting-day state
   (``at_workplace`` / ``home`` / ``absent``) is exported through the EXISTING optional-column
   mechanism of ``synthesis.output`` (``PERSON_OPTIONAL_OUTPUT_COLUMNS`` /
   ``select_person_output_columns``): the state column is merged into the enriched persons
   frame BEFORE the vendored writer selects its columns, so the attribute is written by that
   one writer rather than by a second pass over the finished CSV.
3. **The ``day_absence_state`` person attribute** (issue #370, ADR-0110). Independent of the
   commute-day model above: the general day-absence draw from
   :mod:`braunschweig.synthesis.day_absence.absence_stage` -- ``present`` /
   ``absent_household`` / ``absent_individual`` for EVERY enriched person -- is merged in the
   same way, through the same optional-column mechanism.

4. **The pre-portal trips file** (eqasim-bs#442, ADR-0141). With ``braunschweig.portal.enabled`` true the
   ``trips.csv`` above is the day AFTER the portal rewrite (far destinations carry the purpose ``outside``, the
   inner legs of a stay are dropped). The stage then additionally writes ``<prefix>trips_pre_portal.csv`` (and
   ``.parquet`` when that output format is on) from ``braunschweig.synthesis.commute_day.trips_day_stage`` with
   exactly the vendored trips column set, so file-based validators that compare the diary with a survey can read
   the donor purposes. No mode is merged into it: the MATSim mode-choice trip indices refer to the post-portal
   table. With the flag false nothing is written and every other output is untouched.
5. **The pre-portal commutes file** (eqasim-bs#442, ADR-0141). ``commutes.gpkg`` is built by the vendored writer
   from the written activities, where the workplace of a far commuter is an ``outside`` activity, so those persons
   are missing from it. With the flag on the stage also writes ``<prefix>commutes_pre_portal.gpkg`` (and
   ``.geoparquet`` when that format is on): the same home -> work lines (``person_id``, ``geometry``), selected with
   the vendored rule (``synthesis.output.build_commute_frame``) for the persons whose pre-portal trips contain a work
   activity, with the work location taken from ``synthesis.population.spatial.primary.locations``. The home ->
   education lines are the second layer ``education`` of the same GeoPackage.

The eqasim writer itself is NOT re-implemented: ``configure`` and ``execute`` are the vendored
ones, run through the proxies of :mod:`braunschweig.synthesis.commute_day.day_view`.

With ``commute_day_state_enabled`` false and ``day_absence_enabled`` false the ``.final`` aliases
are pass-throughs of the pre-assignment views AND the enriched frame is handed on untouched, so
neither optional column exists, ``select_person_output_columns`` returns the legacy list, and
every output file is byte-identical to the vendored stage's.
"""
from __future__ import annotations

import hashlib
import inspect
import logging
from pathlib import Path

import geopandas as gpd

import synthesis.output as base

from braunschweig.synthesis.commute_day import day_view as _day_view
from braunschweig.synthesis.commute_day.day_view import (
    ConfigureDayViewContext,
    StageOverrideContext,
)
from braunschweig.synthesis.portal_trips import config_keys as _portal_config_keys

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day output]"

ENRICHED_STAGE = "synthesis.population.enriched"
TRIPS_STAGE = "synthesis.population.trips"
ACTIVITIES_STAGE = "synthesis.population.activities"
DAY_TRIPS_STAGE = "synthesis.population.trips.final"
DAY_ACTIVITIES_STAGE = "synthesis.population.activities.final"
STATE_STAGE = "braunschweig.synthesis.commute_day.state_stage"

KEY_ENABLED = "commute_day_state_enabled"
DEFAULT_ENABLED = True

#: Column the state stage carries and this stage exports; it must be one of
#: ``synthesis.output.PERSON_OPTIONAL_OUTPUT_COLUMNS`` for the vendored writer to pick it up.
STATE_COLUMN = "commute_day_state"

#: General day-absence flag (issue #370) and the stage it reads when on -- independent of
#: KEY_ENABLED (the commute-day state model above). Same names as
#: braunschweig.synthesis.commute_day.trips_day_stage.KEY_DAY_ABSENCE_ENABLED / ABSENCE_STAGE.
KEY_DAY_ABSENCE_ENABLED = "day_absence_enabled"
DEFAULT_DAY_ABSENCE_ENABLED = True
ABSENCE_STAGE = "braunschweig.synthesis.day_absence.absence_stage"

#: Column the absence stage carries and this stage exports; it must be one of
#: ``synthesis.output.PERSON_OPTIONAL_OUTPUT_COLUMNS`` for the vendored writer to pick it up.
ABSENCE_STATE_COLUMN = "day_absence_state"

#: Modules whose sources this stage's cache token must cover (see :func:`validate`): the shim
#: decides WHICH frames the vendored writer sees, and the VENDORED writer itself is what
#: produces the output -- synpp hashes only this thin module, so an edit to synthesis/output.py
#: (this very task added a column to its PERSON_OPTIONAL_OUTPUT_COLUMNS) would otherwise leave
#: a stale CSV set in the cache.
#: ``_portal_config_keys`` carries the flag default, the pre-portal stage name and the file stem this stage declares
#: and writes with (eqasim-bs#442).
_HELPER_MODULES = (_day_view, base, _portal_config_keys)

#: Stages the pre-portal commutes read (declared only while the portal layer is on): the assigned work/education
#: locations (a pair of frames) and the household home locations. Both are upstream of ``trips.final``.
PRIMARY_LOCATIONS_STAGE = "synthesis.population.spatial.primary.locations"
HOME_LOCATIONS_STAGE = "synthesis.population.spatial.home.locations"

#: Required columns of the pre-portal trips frame: the inputs of ``synthesis.output.prepare_trip_output_frame``.
_PRE_PORTAL_REQUIRED_COLUMNS = (
    "person_id", "trip_index", "departure_time", "arrival_time", "preceding_purpose", "following_purpose",
    "is_first_trip", "is_last_trip",
)


def validate(context):
    """synpp validation token: md5 over the pure helper modules' sources.

    synpp hashes only THIS module's source, so an edit to a helper module it imports would
    otherwise leave the cached stage output in place although the rules that produced it
    changed. The token folds those sources in, so a helper edit devalidates the stage exactly
    like an edit here (same mechanism as
    ``braunschweig.synthesis.locations.secondary_chainsolvers.validate``).
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    base.configure(ConfigureDayViewContext(context))
    context.config(KEY_ENABLED, DEFAULT_ENABLED)
    # Declared only when the model is on -- the same gate every other consumer of the state
    # stage uses (braunschweig.matsim.scenario.population,
    # braunschweig.analysis.synthesis.work_participation_by_kreis,
    # braunschweig.analysis.cordon_validation), so a workflow running with the model off never
    # carries the donor/state chain in its DAG for a column it never writes.
    if context.config(KEY_ENABLED):
        context.stage(STATE_STAGE)
    # issue #370: the general day-absence draw is independent of the commute-day model above, so
    # it gets its own flag and its own gate.
    context.config(KEY_DAY_ABSENCE_ENABLED, DEFAULT_DAY_ABSENCE_ENABLED)
    if context.config(KEY_DAY_ABSENCE_ENABLED):
        context.stage(ABSENCE_STAGE)
    # eqasim-bs#442: the donor-purpose trips are written next to the post-portal ones only while the portal layer is
    # on; with the flag off the stage is not even in this stage's DAG.
    context.config(_portal_config_keys.KEY_ENABLED, _portal_config_keys.DEFAULT_ENABLED)
    if context.config(_portal_config_keys.KEY_ENABLED):
        context.stage(_portal_config_keys.PRE_PORTAL_TRIPS_STAGE)
        context.stage(PRIMARY_LOCATIONS_STAGE)
        context.stage(HOME_LOCATIONS_STAGE)


def _persons_with_activity(trips, purpose):
    """Ids of the persons whose trips reach or leave an activity of ``purpose`` (the activities-file definition)."""
    mask = (trips["preceding_purpose"] == purpose) | (trips["following_purpose"] == purpose)
    return set(trips.loc[mask, "person_id"])


def build_pre_portal_commutes(pre_portal_trips, persons, df_home, df_work, df_education):
    """Home -> work and home -> education lines of the pre-portal day, as ``(work_lines, education_lines)``.

    The vendored ``<prefix>commutes.gpkg`` pairs each person's first ``home`` and first ``work`` activity of the
    WRITTEN activities. While the portal layer is on, the work activity of a far commuter is an ``outside`` activity
    there, so this rebuilds the same pairing from what the portal layer does not change: a person qualifies when the
    pre-portal trips contain a ``work`` (``education``) activity and a ``home`` activity (a person without any trip
    has the single ``home`` activity), the home is the household location of ``df_home`` and the destination the
    assigned location of ``df_work`` / ``df_education``. Only persons of ``persons`` (the persons file) are kept,
    like the output stage's join. The selection and the line construction are those of
    ``synthesis.output.build_commute_frame``, not a copy.

    A qualifying person without an assigned primary location (or without a household location) is dropped and
    counted in a warning: the upstream location stage asserts that every work/education activity has one, so any
    count above zero means a broken join. All frames must share one CRS.
    """
    crs_by_name = {"home": df_home.crs, "work": df_work.crs, "education": df_education.crs}
    if None in crs_by_name.values() or len(set(crs_by_name.values())) != 1:
        raise ValueError(f"{_LOG_TAG} the pre-portal commutes need the home, work and education locations in one "
                         f"CRS, got {crs_by_name}")
    person_ids = set(persons["person_id"])
    trips = pre_portal_trips[pre_portal_trips["person_id"].isin(person_ids)]
    with_home = _persons_with_activity(trips, "home") | (person_ids - set(trips["person_id"]))
    household_of = persons.drop_duplicates("person_id").set_index("person_id")["household_id"]
    home_geometry = df_home.drop_duplicates("household_id").set_index("household_id")["geometry"]

    lines = {}
    for purpose, df_destination in (("work", df_work), ("education", df_education)):
        wanted = _persons_with_activity(trips, purpose) & with_home
        destination_geometry = df_destination.drop_duplicates("person_id").set_index("person_id")["geometry"]
        located = sorted(person for person in wanted
                         if person in destination_geometry.index and household_of[person] in home_geometry.index)
        if len(located) < len(wanted):
            logger.warning("%s %d of %d persons with a %s activity in the pre-portal trips have no primary location "
                           "or no household location and are left out of the pre-portal commutes; the location "
                           "stage asserts that every such activity is located, so this is a broken join.",
                           _LOG_TAG, len(wanted) - len(located), len(wanted), purpose)
        activities = gpd.GeoDataFrame(
            {"person_id": located + located,
             "purpose": ["home"] * len(located) + [purpose] * len(located),
             "geometry": [home_geometry[household_of[person]] for person in located]
                         + [destination_geometry[person] for person in located]},
            geometry="geometry", crs=df_home.crs)
        lines[purpose] = base.build_commute_frame(activities, purpose)
        logger.info("%s pre-portal %s commutes: %d lines for %d persons with a %s activity in the pre-portal trips",
                    _LOG_TAG, purpose, len(lines[purpose]), len(wanted), purpose)
    return lines["work"], lines["education"]


def write_pre_portal_commutes(work_lines, education_lines, output_path, output_prefix, output_formats):
    """Write the pre-portal commute lines and return the paths written.

    ``<output_path>/<output_prefix>commutes_pre_portal.gpkg`` (work lines as the first layer, education lines as
    the layer ``education``) when ``"gpkg"`` is in ``output_formats``; ``.geoparquet`` (and the education file
    ``education_commutes_pre_portal.geoparquet``) when ``"geoparquet"`` is, mirroring the vendored writer's handling
    of the spatial formats. Logs every file with its row counts and warns when the formats select no file.
    """
    work_stem = f"{output_prefix}{_portal_config_keys.PRE_PORTAL_COMMUTES_FILE_STEM}"
    education_stem = f"{output_prefix}{_portal_config_keys.PRE_PORTAL_EDUCATION_FILE_STEM}"
    written = []
    if "gpkg" in output_formats:
        path = Path(output_path) / f"{work_stem}.gpkg"
        work_lines.to_file(path, driver="GPKG")
        education_lines.to_file(path, layer=_portal_config_keys.PRE_PORTAL_EDUCATION_LAYER, driver="GPKG")
        # clean_gpkg rounds the layer extents and needs one for every layer; an empty layer has none.
        if len(work_lines) and len(education_lines):
            base.clean_gpkg(str(path))
        written.append(path)
    if "geoparquet" in output_formats:
        work_path = Path(output_path) / f"{work_stem}.geoparquet"
        education_path = Path(output_path) / f"{education_stem}.geoparquet"
        work_lines.to_parquet(work_path)
        education_lines.to_parquet(education_path)
        written.extend([work_path, education_path])
    if not written:
        logger.warning("%s %s is true but output_formats %s contains neither 'gpkg' nor 'geoparquet': no pre-portal "
                       "commutes file was written.", _LOG_TAG, _portal_config_keys.KEY_ENABLED, list(output_formats))
    for path in written:
        logger.info("%s wrote %s (work %d lines, education %d lines): the home -> work/education lines of the "
                    "donor day before the portal rewrite; <prefix>commutes.gpkg misses the far commuters.",
                    _LOG_TAG, path, len(work_lines), len(education_lines))
    return written


def write_pre_portal_trips(pre_portal_trips, output_path, output_prefix, output_formats):
    """Write the pre-portal trips table and return the paths written.

    ``pre_portal_trips`` is the frame of ``trips_day_stage`` (the eqasim trips schema). It is reduced to the
    vendored trips column set by ``synthesis.output.prepare_trip_output_frame`` and written as
    ``<output_path>/<output_prefix>trips_pre_portal.csv`` when ``"csv"`` is in ``output_formats`` and as ``.parquet``
    when ``"parquet"`` is, mirroring the vendored writer's handling of the formats. Raises ``ValueError`` naming the
    missing columns when the frame lacks an input of the derivation. Logs every file written with its row count and
    warns when the output formats select no file at all.
    """
    missing = [column for column in _PRE_PORTAL_REQUIRED_COLUMNS if column not in pre_portal_trips.columns]
    if missing:
        raise ValueError(
            f"{_LOG_TAG} the pre-portal trips frame is missing the column(s) {missing} that the trips output "
            f"derives its columns from (present: {sorted(pre_portal_trips.columns)[:20]}). Check that "
            f"{_portal_config_keys.PRE_PORTAL_TRIPS_STAGE} still returns the eqasim trips schema.")
    frame = base.prepare_trip_output_frame(pre_portal_trips)
    stem = f"{output_prefix}{_portal_config_keys.PRE_PORTAL_TRIPS_FILE_STEM}"
    written = []
    if "csv" in output_formats:
        path = Path(output_path) / f"{stem}.csv"
        frame.to_csv(path, sep=";", index=None, lineterminator="\n")
        written.append(path)
    if "parquet" in output_formats:
        path = Path(output_path) / f"{stem}.parquet"
        frame.to_parquet(path)
        written.append(path)
    if not written:
        logger.warning("%s %s is true but output_formats %s contains neither 'csv' nor 'parquet': no pre-portal "
                       "trips file was written.", _LOG_TAG, _portal_config_keys.KEY_ENABLED, list(output_formats))
    for path in written:
        logger.info("%s wrote %s (%d rows): the donor-purpose trips before the portal rewrite, for validators "
                    "that compare the diary with a survey; <prefix>trips.csv holds the post-portal day.",
                    _LOG_TAG, path, len(frame))
    return written


def attach_commute_day_state(persons, states):
    """Left-join ``commute_day_state`` onto the enriched persons frame.

    ``states`` is the state stage's ``states`` frame -- EXACTLY one row per worker -- so every
    person WITHOUT an assigned workplace (non-workers, children) keeps a missing value, which
    ``pandas.DataFrame.to_csv`` writes as an empty field. That absence is meaningful and is not
    filled with a substitute: a person who never works has no reporting-day commute state.

    The coverage rate is logged, and a coverage of zero raises: the only way a finished
    population contains no worker with a state is a broken ``person_id`` join between the state
    stage and the enriched population, which would otherwise ship an all-empty column that
    reads like a measured "no worker works today" (CLAUDE.md "Fallback transparency").
    """
    for frame, columns, what in ((persons, ("person_id",), "the enriched persons frame"),
                                 (states, ("person_id", STATE_COLUMN), "the states frame")):
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise ValueError(f"{_LOG_TAG} {what} is missing the required column(s) {missing} "
                             f"(present: {sorted(frame.columns)[:20]})")
    if STATE_COLUMN in persons.columns:
        raise ValueError(
            f"{_LOG_TAG} the enriched persons frame already carries a {STATE_COLUMN!r} column; "
            "merging the state stage on top of it would produce two ambiguous columns. Check "
            "whether an upstream stage started to emit that name.")

    merged = persons.merge(states[["person_id", STATE_COLUMN]], on="person_id", how="left",
                           validate="one_to_one")
    n_with_state = int(merged[STATE_COLUMN].notna().sum())
    counts = merged[STATE_COLUMN].value_counts().to_dict()
    logger.info("%s %d/%d persons (%.1f%%) carry a reporting-day state: %s; the remainder have "
                "no assigned workplace and keep an empty field", _LOG_TAG, n_with_state,
                len(merged), 100.0 * n_with_state / max(len(merged), 1),
                {str(key): int(value) for key, value in sorted(counts.items())})
    if n_with_state == 0:
        raise ValueError(
            f"{_LOG_TAG} not one of the {len(merged)} persons in the enriched population was "
            f"matched to a row of the state frame ({len(states)} rows); this is a broken "
            "person_id join, not a population without workers. Check the id types on both "
            "sides before exporting an all-empty column.")
    return merged


def attach_day_absence_state(persons, absence):
    """Left-join ``day_absence_state`` onto the enriched persons frame.

    Unlike ``attach_commute_day_state`` (one row per WORKER on the ``states`` side), ``absence``
    carries EXACTLY one row per enriched person on both the enabled and the disabled path
    (:mod:`braunschweig.synthesis.day_absence.absence_stage`), so a healthy join covers every
    person -- there is no legitimate "no assigned workplace" remainder here.

    The coverage rate is logged, and a coverage of zero raises: the only way a finished
    population contains no person with a state is a broken ``person_id`` join between the
    absence stage and the enriched population, which would otherwise ship an all-empty column
    that reads like a measured "nobody is ever absent" (CLAUDE.md "Fallback transparency").
    """
    for frame, columns, what in ((persons, ("person_id",), "the enriched persons frame"),
                                 (absence, ("person_id", ABSENCE_STATE_COLUMN),
                                  "the absence frame")):
        missing = [column for column in columns if column not in frame.columns]
        if missing:
            raise ValueError(f"{_LOG_TAG} {what} is missing the required column(s) {missing} "
                             f"(present: {sorted(frame.columns)[:20]})")
    if ABSENCE_STATE_COLUMN in persons.columns:
        raise ValueError(
            f"{_LOG_TAG} the enriched persons frame already carries a "
            f"{ABSENCE_STATE_COLUMN!r} column; merging the absence stage on top of it would "
            "produce two ambiguous columns. Check whether an upstream stage started to emit "
            "that name.")

    merged = persons.merge(absence[["person_id", ABSENCE_STATE_COLUMN]], on="person_id",
                           how="left", validate="one_to_one")
    n_with_state = int(merged[ABSENCE_STATE_COLUMN].notna().sum())
    counts = merged[ABSENCE_STATE_COLUMN].value_counts().to_dict()
    logger.info("%s %d/%d persons (%.1f%%) carry a day-absence state: %s", _LOG_TAG,
                n_with_state, len(merged), 100.0 * n_with_state / max(len(merged), 1),
                {str(key): int(value) for key, value in sorted(counts.items())})
    if n_with_state == 0:
        raise ValueError(
            f"{_LOG_TAG} not one of the {len(merged)} persons in the enriched population was "
            f"matched to a row of the absence frame ({len(absence)} rows); this is a broken "
            "person_id join, not a population without absences. Check the id types on both "
            "sides before exporting an all-empty column.")
    return merged


def execute(context):
    day_trips = context.stage(DAY_TRIPS_STAGE)
    day_activities = context.stage(DAY_ACTIVITIES_STAGE)
    persons = context.stage(ENRICHED_STAGE)

    if bool(context.config(KEY_ENABLED)):
        states = context.stage(STATE_STAGE)["states"]
        persons = attach_commute_day_state(persons, states)
    else:
        # Untouched frame -> no commute_day_state column -> select_person_output_columns
        # returns the legacy list -> byte-identical persons.csv.
        logger.info("%s %s is false -- the persons output keeps the legacy column set.",
                    _LOG_TAG, KEY_ENABLED)

    if bool(context.config(KEY_DAY_ABSENCE_ENABLED)):
        absence = context.stage(ABSENCE_STAGE)["absence"]
        persons = attach_day_absence_state(persons, absence)
    else:
        logger.info("%s %s is false -- the persons output has no day_absence_state column.",
                    _LOG_TAG, KEY_DAY_ABSENCE_ENABLED)

    result = base.execute(StageOverrideContext(context, {
        TRIPS_STAGE: day_trips,
        ACTIVITIES_STAGE: day_activities,
        ENRICHED_STAGE: persons,
    }))

    # After the vendored writer, whose validate() has already required the output directory to exist.
    if bool(context.config(_portal_config_keys.KEY_ENABLED)):
        write_pre_portal_trips(context.stage(_portal_config_keys.PRE_PORTAL_TRIPS_STAGE),
                               context.config("output_path"), context.config("output_prefix"),
                               context.config("output_formats"))
        df_work, df_education = context.stage(PRIMARY_LOCATIONS_STAGE)
        work_lines, education_lines = build_pre_portal_commutes(
            context.stage(_portal_config_keys.PRE_PORTAL_TRIPS_STAGE), persons,
            context.stage(HOME_LOCATIONS_STAGE), df_work, df_education)
        write_pre_portal_commutes(work_lines, education_lines, context.config("output_path"),
                                  context.config("output_prefix"), context.config("output_formats"))
    return result
