"""MATSim population writer with cross-cordon in-commuter injection (terminal).

Overrides matsim.scenario.population: loads the resident synthesis frames, and -- when
cordon_enabled -- concatenates the injected in-commuter frames before the SAME prepare +
write. Two independent in-commuter sources are merged: the SvB cross-cordon commuters
(braunschweig.synthesis.incommuters) and the student in-commuters
(braunschweig.synthesis.student_incommuters, #140 Task 5), both contributing persons,
activities, locations, trips, and vehicles (Task 5 review fix, 2026-07-18: the student
stage now also builds a vehicles frame -- see student_incommuters._inject -- so both
sources are merged into ``raw["vehicles"]`` identically; this frame drives the
per-person ``PersonVehicles`` attribute written by ``add_person`` below, so a missing
merge here would leave in-commuters unroutable even if vehicles.xml is otherwise
correct). Terminal injection downstream of the whole synthesis chain, so there is no
alias cycle and the resident chain stays resident-only. OFF -> both in-commuter frames
empty -> byte-identical output.

Reporting-day view (ADR-0104, issue #244): the plans written here must describe the day
the simulation actually runs, so the pre-assignment trips/activities that the vendored
``load_raw`` reads are replaced by ``synthesis.population.trips.final`` /
``...activities.final``, and the drawn ``commute_day_state`` is merged into the resident
persons frame so ``matsim.scenario.population.add_person`` emits it as the person
attribute ``commuteDayState``. With ``commute_day_state_enabled`` false both ``.final``
aliases are pass-throughs and no state column exists -> byte-identical plans.

General day absence (ADR-0110, issue #370), independent of the commute-day model above: the
drawn ``day_absence_state`` (``braunschweig.synthesis.day_absence.absence_stage``, EXACTLY one
row per enriched person on both its enabled and disabled path) is merged into the resident
persons frame the SAME way, so ``add_person`` emits it as ``dayAbsenceState``. With
``day_absence_enabled`` false no such column is merged -> byte-identical plans.

Zone-based parking costs (issue #436), the alternative to the legacy 8 km ring
(``enable_urban_parking``; both flags on is rejected in ``configure``): with
``parking_zones_enabled`` the frames AFTER the in-commuter merge are handed to
``braunschweig.parking.attach`` -- the zone of every activity location, the resident zone of every
home and the free-parking draw of the work/education activities, and, since parking cost zones v2
(spec Amendment C3), the resident parking district of every activity location and of every home --
so ``add_person`` emits ``parkingZone`` / ``parkingFree`` / ``parkingDistrict`` on activities and
``residentParkingZone`` / ``residentParkingDistrict`` on persons, in-commuters included. The districts
are a second layer, independent of the zones. With the flag false the zones stage is not even
declared and no column is attached -> byte-identical plans.
"""
from __future__ import annotations

import hashlib
import importlib
import inspect
import logging
import math

import numpy as np
import pandas as pd

import matsim.scenario.population as base
from braunschweig.synthesis.commute_day import day_view as _day_view
from braunschweig.synthesis.commute_day.day_view import StageOverrideContext
from braunschweig.synthesis.incommuter_merge import _base as _incommuter_merge_base
from braunschweig.synthesis.incommuter_merge._base import (assert_unique_ids,
                                                            concat_frame)

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day population]"

#: Module whose source this stage's cache token must cover (see :func:`validate`): the VENDORED
#: writer, not this wrapper alone. synpp hashes only THIS module's source, so an edit to the
#: vendored ``matsim.scenario.population`` (``load_raw``, ``prepare_frames``, ``write_population``,
#: ``add_person``/``OPTIONAL_PERSON_FIELDS``) would otherwise leave a stale cached plans.xml.gz in
#: place although the writer that produced it changed (final-review fix wave, Important finding
#: 8; same mechanism as ``braunschweig.synthesis.commute_day.output_day.validate``).
#: ``day_view`` owns :class:`StageOverrideContext`, i.e. WHICH frames the vendored ``load_raw``
#: is handed in place of the pre-assignment ones, and ``incommuter_merge._base`` owns
#: ``assert_unique_ids`` / ``concat_frame``, i.e. how the incommuter rows are appended and which
#: id collisions are rejected. Both therefore decide the CONTENT of the written plans while
#: leaving this file's own source untouched; both were module-level imports outside the token
#: until the #327 gate was re-run (this stage acquired its ``validate()`` only afterwards).
_HELPER_MODULES = (base, _day_view, _incommuter_merge_base)

#: Hashed by dotted NAME: the rest of this stage's import closure, i.e. the modules its helpers
#: import, whose code this stage runs without importing it itself. The gate in
#: tests/test_audit_synpp_helper_hash.py keeps this list complete (ADR-0136).
#: ``braunschweig.parking.attach`` (imported inside :func:`execute` only when parking_zones_enabled)
#: decides which zone, resident zone and free-parking value every plan element carries, and
#: ``braunschweig.parking.zones`` supplies its point-in-polygon test (``assign_zones``), so both shape
#: the CONTENT of the parking attributes written here. Hashed unconditionally, like the module
#: objects above: a token must not depend on a flag.
_DEFERRED_HELPER_MODULE_NAMES = (
    "matsim.writers",
    "braunschweig.parking.attach",
    "braunschweig.parking.zones",
)

#: Reporting-day view of the day (ADR-0104, issue #244). The MATSim plans must carry the day
#: the simulation runs, so the pre-assignment trips/activities the vendored ``load_raw`` reads
#: are replaced by these before the frames are prepared. Pass-throughs when
#: ``commute_day_state_enabled`` is false.
DAY_TRIPS_STAGE = "synthesis.population.trips.final"
DAY_ACTIVITIES_STAGE = "synthesis.population.activities.final"
STATE_STAGE = "braunschweig.synthesis.commute_day.state_stage"

#: The two stage names the VENDORED ``matsim.scenario.population.load_raw`` reads and that the
#: shim answers with the reporting-day frames instead.
BASE_TRIPS_STAGE = "synthesis.population.trips"
BASE_ACTIVITIES_STAGE = "synthesis.population.activities"

KEY_COMMUTE_DAY_STATE_ENABLED = "commute_day_state_enabled"
DEFAULT_COMMUTE_DAY_STATE_ENABLED = True

#: Person column carrying the drawn state; ``matsim.scenario.population.OPTIONAL_PERSON_FIELDS``
#: emits it as the MATSim person attribute ``commuteDayState`` (java.lang.String) for the
#: persons that have one, and writes no attribute at all for the persons that do not.
STATE_COLUMN = "commute_day_state"

#: General day-absence flag (issue #370) and the stage it reads when on -- independent of
#: KEY_COMMUTE_DAY_STATE_ENABLED. Same names as
#: braunschweig.synthesis.commute_day.trips_day_stage.KEY_DAY_ABSENCE_ENABLED / ABSENCE_STAGE.
KEY_DAY_ABSENCE_ENABLED = "day_absence_enabled"
DEFAULT_DAY_ABSENCE_ENABLED = True
ABSENCE_STAGE = "braunschweig.synthesis.day_absence.absence_stage"

#: Person column carrying the drawn absence state; ``matsim.scenario.population.
#: OPTIONAL_PERSON_FIELDS`` emits it as the MATSim person attribute ``dayAbsenceState``
#: (java.lang.String) for the persons that have one.
ABSENCE_STATE_COLUMN = "day_absence_state"

#: Zone-based parking costs (issue #436) and the stage they read when on. Default off: the flag
#: is switched on in the canonical configuration, not here. Mutually exclusive with
#: KEY_URBAN_PARKING, the legacy 8 km ring declared by the vendored ``base.declare_writer_inputs``.
KEY_PARKING_ZONES_ENABLED = "parking_zones_enabled"
DEFAULT_PARKING_ZONES_ENABLED = False
KEY_URBAN_PARKING = "enable_urban_parking"
ZONES_STAGE = "braunschweig.parking.zones_stage"

#: Sensitivity arm of the free-parking draw: a share difference (unitless, valid range [-1, 1])
#: added to the SrV free-parking share of every workplace class before the draw clips the
#: probability to [0, 1]. 0.0 keeps the observed shares. Declared only when the flag is on.
KEY_FREE_SHIFT = "parking_workplace_free_share_shift"
DEFAULT_FREE_SHIFT = 0.0

#: Frame columns ``braunschweig.parking.attach`` adds and ``matsim.scenario.population`` writes:
#: activity attributes ``parkingZone`` / ``parkingFree`` / ``parkingDistrict``, person attributes
#: ``residentParkingZone`` / ``residentParkingDistrict``.
PARKING_ZONE_COLUMN = "parking_zone"
PARKING_FREE_COLUMN = "parking_free"
RESIDENT_PARKING_ZONE_COLUMN = "resident_parking_zone"
PARKING_DISTRICT_COLUMN = "parking_district"
RESIDENT_PARKING_DISTRICT_COLUMN = "resident_parking_district"
#: The purpose of the activities at the household home (they never pay, H1), and the mode of the initial plan's
#: car-driver legs; only the own-district coverage rate reads them.
HOME_PURPOSE = "home"
CAR_MODE = "car"
#: The identifying columns of the frames the attach functions extend, which must come back unchanged:
#: an activity is keyed by (person_id, activity_index), a person by person_id.
ACTIVITY_KEY_COLUMNS = ("person_id", "activity_index")
PERSON_KEY_COLUMNS = ("person_id",)

_PARKING_LOG_TAG = "[parking population]"


def validate(context):
    """synpp validation token: md5 over the vendored writer's source.

    synpp hashes only THIS module's source, so an edit to the vendored
    ``matsim.scenario.population`` this wrapper delegates to (``load_raw``, ``prepare_frames``,
    ``write_population``, ``add_person``/``OPTIONAL_PERSON_FIELDS``) would otherwise leave a stale
    cached ``plans.xml.gz`` in place although the writer that produced it changed. The token folds
    that source in, so a vendored-writer edit devalidates the stage exactly like an edit here
    (same mechanism as ``braunschweig.synthesis.commute_day.output_day.validate``).

    The deferred helpers (:data:`_DEFERRED_HELPER_MODULE_NAMES`) follow, imported by name. One that
    cannot be imported or read -- an absent one included -- raises rather than being skipped:
    skipping it would silently reuse a stale ``plans.xml.gz`` exactly when the helper is broken.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    for module_name in _DEFERRED_HELPER_MODULE_NAMES:
        try:
            deferred_module = importlib.import_module(module_name)
            deferred_source = inspect.getsource(deferred_module)
        except Exception as error:
            raise RuntimeError(
                f"population validate(): cannot hash the deferred helper module "
                f"{module_name!r} ({type(error).__name__}: {error}); it must not be skipped, "
                "because skipping it would silently reuse stale cached output."
            ) from error
        digest.update(deferred_source.encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    # The vendored writer's declarations only: its own configure() additionally rejects
    # KEY_PARKING_ZONES_ENABLED, because the plain writer attaches no parking attribute; this
    # wrapper attaches them (execute) and declares the flag itself below.
    base.declare_writer_inputs(context)
    context.stage(DAY_TRIPS_STAGE)
    context.stage(DAY_ACTIVITIES_STAGE)
    context.config(KEY_COMMUTE_DAY_STATE_ENABLED, DEFAULT_COMMUTE_DAY_STATE_ENABLED)
    # Declared only when the model is on, exactly like the cordon block below: a workflow that
    # runs with the model off (every configs/fixtures/* config, whose reporting-day aliases are
    # pass-throughs of the pre-assignment stages) must not carry the whole donor/state chain in
    # its DAG for a value it never writes.
    if context.config(KEY_COMMUTE_DAY_STATE_ENABLED):
        context.stage(STATE_STAGE)
    # issue #370: the general day-absence draw is independent of the commute-day model above, so
    # it gets its own flag and its own gate -- the same trade-off.
    context.config(KEY_DAY_ABSENCE_ENABLED, DEFAULT_DAY_ABSENCE_ENABLED)
    if context.config(KEY_DAY_ABSENCE_ENABLED):
        context.stage(ABSENCE_STAGE)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")
        context.stage("braunschweig.synthesis.student_incommuters")
    # Zone-based parking costs (issue #436), declared only when on -- the same trade-off as the
    # blocks above: a workflow with the model off carries neither the zones stage nor its keys.
    context.config(KEY_PARKING_ZONES_ENABLED, DEFAULT_PARKING_ZONES_ENABLED)
    if context.config(KEY_PARKING_ZONES_ENABLED):
        # KEY_URBAN_PARKING was declared by base.declare_writer_inputs above. The zone tariffs and the
        # legacy ring fees are alternative parking-cost models for the same stays, so the pair
        # is a configuration error, raised here before any stage runs.
        if context.config(KEY_URBAN_PARKING):
            raise ValueError(
                f"{_PARKING_LOG_TAG} {KEY_PARKING_ZONES_ENABLED} and {KEY_URBAN_PARKING} are both "
                f"true, but the zone tariffs ({KEY_PARKING_ZONES_ENABLED}) and the legacy 8 km "
                f"ring fees ({KEY_URBAN_PARKING}, isParis attributes) must never be combined: set "
                f"{KEY_URBAN_PARKING}: false to run the zone model, or "
                f"{KEY_PARKING_ZONES_ENABLED}: false to reproduce the legacy ring.")
        context.stage(ZONES_STAGE)
        _require_free_share_shift(context.config(KEY_FREE_SHIFT, DEFAULT_FREE_SHIFT))
        context.config("random_seed")


def _require_free_share_shift(value):
    """Return ``parking_workplace_free_share_shift`` as a float, raising unless it lies in [-1, 1].

    The shift is a difference of shares: a value beyond +-1 could only saturate the clipped
    free-parking probability, so it is almost certainly a unit error (percentage points instead
    of a share difference) and is rejected at configure time.
    """
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not -1.0 <= value <= 1.0):
        raise ValueError(
            f"{_PARKING_LOG_TAG} {KEY_FREE_SHIFT} must be a number in [-1, 1] (a difference of "
            f"free-parking shares, {DEFAULT_FREE_SHIFT} keeps the SrV-observed shares); got "
            f"{value!r}.")
    return float(value)


def attach_commute_day_state(persons, states):
    """Left-join ``commute_day_state`` onto the resident persons frame.

    One row per worker on the ``states`` side, so persons without an assigned workplace keep a
    missing value and ``matsim.scenario.population.add_person`` writes NO ``commuteDayState``
    attribute for them (never a substituted one). The coverage rate is logged and a coverage of
    zero raises, because an all-missing column is a broken ``person_id`` join rather than a
    population in which nobody works (CLAUDE.md "Fallback transparency").
    """
    if STATE_COLUMN in persons.columns:
        raise ValueError(
            f"{_LOG_TAG} the persons frame already carries a {STATE_COLUMN!r} column; merging "
            "the state stage on top of it would produce two ambiguous columns.")
    merged = persons.merge(states[["person_id", STATE_COLUMN]], on="person_id", how="left",
                           validate="one_to_one")  # one row per person on BOTH sides
    n_with_state = int(merged[STATE_COLUMN].notna().sum())
    logger.info("%s %d/%d resident persons (%.1f%%) carry a reporting-day state written as the "
                "MATSim attribute 'commuteDayState'; the remainder have no assigned workplace "
                "and get no attribute", _LOG_TAG, n_with_state, len(merged),
                100.0 * n_with_state / max(len(merged), 1))
    if n_with_state == 0:
        raise ValueError(
            f"{_LOG_TAG} not one of the {len(merged)} resident persons was matched to a row of "
            f"the state frame ({len(states)} rows); this is a broken person_id join, not a "
            "population without workers.")
    return merged


def attach_day_absence_state(persons, absence):
    """Left-join ``day_absence_state`` onto the resident persons frame.

    Unlike ``attach_commute_day_state`` (worker-only), ``absence`` carries EXACTLY one row per
    enriched person on both the enabled and disabled path
    (``braunschweig.synthesis.day_absence.absence_stage``), so a healthy join covers every
    resident; any uncovered remainder is a person_id mismatch with the absence stage, not a
    person without a day-absence state. The coverage rate is logged and a coverage of zero
    raises, because an all-missing column is a broken ``person_id`` join rather than a
    population in which nobody is ever absent (CLAUDE.md "Fallback transparency").
    """
    if ABSENCE_STATE_COLUMN in persons.columns:
        raise ValueError(
            f"{_LOG_TAG} the persons frame already carries a {ABSENCE_STATE_COLUMN!r} column; "
            "merging the absence stage on top of it would produce two ambiguous columns.")
    merged = persons.merge(absence[["person_id", ABSENCE_STATE_COLUMN]], on="person_id",
                           how="left", validate="one_to_one")  # one row per person on BOTH sides
    n_with_state = int(merged[ABSENCE_STATE_COLUMN].notna().sum())
    logger.info("%s %d/%d resident persons (%.1f%%) carry a day-absence state written as the "
                "MATSim attribute 'dayAbsenceState'", _LOG_TAG, n_with_state, len(merged),
                100.0 * n_with_state / max(len(merged), 1))
    if n_with_state == 0:
        raise ValueError(
            f"{_LOG_TAG} not one of the {len(merged)} resident persons was matched to a row of "
            f"the absence frame ({len(absence)} rows); this is a broken person_id join, not a "
            "population without absences.")
    return merged


def _require_attach_result(before, after, column, producer, keys):
    """Return ``after`` if ``producer`` kept every row of ``before`` with its ``keys`` and added ``column``.

    Each ``braunschweig.parking.attach`` function ADDS one column to a copy of the frame it is handed:
    same rows, same order. A changed row count is a duplicating or dropping join inside it (e.g. one
    location matched to two zones); a changed key at an unchanged count is a duplicating-and-dropping
    or a mismatching join. Either would silently duplicate, lose or mislabel plan elements in the
    written population, so it raises instead. ``keys`` are the identifying columns of the frame
    (:data:`ACTIVITY_KEY_COLUMNS` or :data:`PERSON_KEY_COLUMNS`); they are compared row by row, which
    implies an unchanged key set, with column-wise comparisons that stay cheap on a full population.
    """
    if len(after) != len(before):
        raise ValueError(
            f"{_PARKING_LOG_TAG} {producer} returned {len(after)} rows for {len(before)} input "
            "rows; the parking attributes must be attached row-preserving, and a changed count "
            "is a duplicating or dropping join.")
    for key in keys:
        if key not in after.columns:
            raise ValueError(
                f"{_PARKING_LOG_TAG} {producer} dropped the key column {key!r}; the parking "
                "attributes must be attached to the rows as handed.")
        expected = before[key].reset_index(drop=True)
        found = after[key].reset_index(drop=True)
        unchanged = expected.eq(found).fillna(False).to_numpy(dtype=bool)
        if not unchanged.all():
            changed = np.flatnonzero(~unchanged)
            first = int(changed[0])
            raise ValueError(
                f"{_PARKING_LOG_TAG} {producer} changed the key column {key!r} in {len(changed)} of "
                f"{len(before)} rows (first at position {first}: {expected.iloc[first]!r} -> "
                f"{found.iloc[first]!r}); the parking attributes must be attached to the rows as "
                "handed and in their order, so a changed key is a duplicating, dropping or "
                "mismatching join.")
    if column not in after.columns:
        raise ValueError(
            f"{_PARKING_LOG_TAG} {producer} did not add the {column!r} column that "
            "matsim.scenario.population writes as a MATSim attribute.")
    return after


def _own_district_stay_counts(activities, persons, trips):
    """(non-home activities, of them in the district of the person's home, of those reached by car in the initial plan).

    An "own-district stay" is a non-home activity whose ``parking_district`` equals the ``resident_parking_district`` of
    its person (both set): the stays assumption R2 can exempt. "Reached by car" reads the initial plan's trip into the
    activity (``trips`` row ``trip_index`` k leads to activity ``activity_index`` k + 1, the pairing of
    ``matsim.scenario.population.add_person``) with mode ``car``: a car-DRIVER leg of the plan the simulation starts from,
    before the mode choice of the run, so the count is an exposure indicator and no forecast of the priced stays.
    """
    missing = [column for column in ("person_id", "trip_index", "mode") if column not in trips.columns]
    if missing:
        raise ValueError(
            f"{_PARKING_LOG_TAG} the trips frame lacks the column(s) {missing}; the own-district coverage rate pairs "
            "every trip with the activity it leads to.")
    non_home = (activities["purpose"] != HOME_PURPOSE).to_numpy()
    person_district = activities["person_id"].map(
        persons.set_index("person_id")[RESIDENT_PARKING_DISTRICT_COLUMN])
    activity_district = activities[PARKING_DISTRICT_COLUMN]
    own = (non_home & activity_district.notna().to_numpy() & person_district.notna().to_numpy()
           & (activity_district == person_district).to_numpy())
    car_legs = trips.loc[trips["mode"] == CAR_MODE, ["person_id", "trip_index"]]
    reached_by_car = pd.MultiIndex.from_arrays([activities["person_id"], activities["activity_index"]]).isin(
        pd.MultiIndex.from_arrays([car_legs["person_id"], car_legs["trip_index"] + 1]))
    return int(non_home.sum()), int(own.sum()), int((own & reached_by_car).sum())


def _log_parking_coverage(activities, persons, resident_person_ids, trips):
    """Log ONCE per population how many plan elements carry each parking attribute.

    An activity without ``parkingZone`` lies outside every zone and parks free, and a person
    without ``residentParkingZone`` is a resident of no zone: both are modelled states, but their
    rates are the evidence that the zone join worked (CLAUDE.md "Fallback transparency").
    In-commuters (persons absent from the resident enriched frame) are counted separately,
    because zoned residents next to not one zoned in-commuter activity is the signature of a
    broken in-commuter locations join -- that case is logged as a warning.
    """
    has_zone = activities[PARKING_ZONE_COLUMN].notna()
    is_incommuter = ~activities["person_id"].isin(resident_person_ids)
    n_activities = len(activities)
    n_zoned = int(has_zone.sum())
    n_incommuter = int(is_incommuter.sum())
    n_incommuter_zoned = int((has_zone & is_incommuter).sum())
    n_free = int(activities[PARKING_FREE_COLUMN].eq(True).sum())
    n_persons = len(persons)
    n_resident = int(persons[RESIDENT_PARKING_ZONE_COLUMN].notna().sum())
    logger.info(
        "%s parkingZone on %d/%d activities (%.1f%%), in-commuter activities with a parkingZone: "
        "%d/%d; parkingFree=true on %d activities; residentParkingZone on %d/%d persons "
        "(%.1f%%). Activities without a parkingZone lie outside every zone and park free "
        "(assumption Z1).", _PARKING_LOG_TAG, n_zoned, n_activities,
        100.0 * n_zoned / max(n_activities, 1), n_incommuter_zoned, n_incommuter, n_free,
        n_resident, n_persons, 100.0 * n_resident / max(n_persons, 1))
    if n_incommuter > 0 and n_incommuter_zoned == 0 and n_zoned > 0:
        logger.warning(
            "%s none of the %d in-commuter activities carries a parkingZone although %d resident "
            "activities do; check the in-commuter locations handed to attach_parking_zones -- an "
            "all-unzoned in-commuter set is the signature of a broken join rather than of "
            "in-commuters who never enter a zone.", _PARKING_LOG_TAG, n_incommuter, n_zoned)

    # The resident parking districts (spec Amendment C3), a second layer: their own coverage line, so that the zone
    # line above stays what it was. The own-district stays are the exposure of assumption R2.
    n_districted = int(activities[PARKING_DISTRICT_COLUMN].notna().sum())
    n_incommuter_districted = int((activities[PARKING_DISTRICT_COLUMN].notna() & is_incommuter).sum())
    n_resident_district = int(persons[RESIDENT_PARKING_DISTRICT_COLUMN].notna().sum())
    n_non_home, n_own, n_own_by_car = _own_district_stay_counts(activities, persons, trips)
    logger.info(
        "%s parkingDistrict on %d/%d activities (%.1f%%), in-commuter activities with a parkingDistrict: %d/%d; "
        "residentParkingDistrict on %d/%d persons (%.1f%%). Own-district stays (assumption R2): %d/%d non-home "
        "activities lie in the district of the person's home (%.1f%%); reached by car in the initial plan: %d. "
        "Activities without a parkingDistrict lie outside every district, and a person without a "
        "residentParkingDistrict gets no district exemption.", _PARKING_LOG_TAG, n_districted, n_activities,
        100.0 * n_districted / max(n_activities, 1), n_incommuter_districted, n_incommuter, n_resident_district,
        n_persons, 100.0 * n_resident_district / max(n_persons, 1), n_own, n_non_home,
        100.0 * n_own / max(n_non_home, 1), n_own_by_car)


def execute(context):
    output_path = "%s/population.xml.gz" % context.path()
    enable_urban_parking = bool(context.config("enable_urban_parking"))
    write_income_eur = bool(context.config("write_income_eur"))
    parking_zones_enabled = bool(context.config(KEY_PARKING_ZONES_ENABLED))
    # The vendored load_raw reads the PRE-ASSIGNMENT trips/activities by name; the shim answers
    # those two names with the reporting-day frames instead, so the finished day reaches the
    # writer without the pre-assignment pickles ever being unpickled and dropped (both are full
    # population-sized frames on a 100 % run). Every other stage name it reads goes to the real
    # context unchanged. This happens BEFORE the in-commuter injection below, so the injected
    # frames are aligned against the same schema either way.
    raw = base.load_raw(StageOverrideContext(context, {
        BASE_TRIPS_STAGE: context.stage(DAY_TRIPS_STAGE),
        BASE_ACTIVITIES_STAGE: context.stage(DAY_ACTIVITIES_STAGE),
    }))
    if bool(context.config(KEY_COMMUTE_DAY_STATE_ENABLED)):
        raw["persons"] = attach_commute_day_state(
            raw["persons"], context.stage(STATE_STAGE)["states"])
    else:
        # No column -> matsim.scenario.population.effective_person_fields is unchanged -> no
        # commuteDayState attribute -> byte-identical plans.
        logger.info("%s %s is false -- no commuteDayState attribute is written.",
                    _LOG_TAG, KEY_COMMUTE_DAY_STATE_ENABLED)

    if bool(context.config(KEY_DAY_ABSENCE_ENABLED)):
        raw["persons"] = attach_day_absence_state(
            raw["persons"], context.stage(ABSENCE_STAGE)["absence"])
    else:
        # No column -> matsim.scenario.population.effective_person_fields is unchanged -> no
        # dayAbsenceState attribute -> byte-identical plans.
        logger.info("%s %s is false -- no dayAbsenceState attribute is written.",
                    _LOG_TAG, KEY_DAY_ABSENCE_ENABLED)

    # The resident persons, captured BEFORE the in-commuter merge below, so the parking coverage
    # log can count the injected in-commuters separately.
    resident_person_ids = raw["persons"]["person_id"].to_numpy() if parking_zones_enabled else None

    if context.config("cordon_enabled"):
        inc = context.stage("braunschweig.synthesis.incommuters")
        student_inc = context.stage("braunschweig.synthesis.student_incommuters")
        # Loud safety net (CLAUDE.md no-silent-corruption): the student stage's
        # household_id block is offset above the resident+SvB range by a fixed
        # assumption (student_incommuters._ID_OFFSET_ABOVE_RESIDENTS), not a
        # hard dependency on the SvB stage's actual count -- verify the two
        # in-commuter household_id blocks never actually overlap.
        assert_unique_ids([inc["persons"], student_inc["persons"]], "household_id",
                         "braunschweig.matsim.scenario.population (SvB vs student "
                         "in-commuter households)")
        raw["persons"] = concat_frame(raw["persons"], inc["persons"], "person_id")
        raw["persons"] = concat_frame(raw["persons"], student_inc["persons"], "person_id")
        # person_id must be globally unique across residents + both in-commuter
        # sources; unlike household_id this holds even for multi-member resident
        # households, so it is checked on the full merged frame directly.
        assert_unique_ids([raw["persons"]], "person_id",
                         "braunschweig.matsim.scenario.population (merged persons)")
        raw["activities"] = concat_frame(raw["activities"], inc["activities"],
                                         ["person_id", "activity_index"])
        raw["activities"] = concat_frame(raw["activities"], student_inc["activities"],
                                         ["person_id", "activity_index"])
        raw["locations"] = concat_frame(raw["locations"], inc["locations"],
                                        ["person_id", "activity_index"])
        raw["locations"] = concat_frame(raw["locations"], student_inc["locations"],
                                        ["person_id", "activity_index"])
        raw["trips"] = concat_frame(raw["trips"], inc["trips"],
                                    ["person_id", "trip_index"])
        raw["trips"] = concat_frame(raw["trips"], student_inc["trips"],
                                    ["person_id", "trip_index"])
        # Both in-commuter sources contribute vehicles (Task 5 review fix): this
        # frame drives the per-person "vehicles" (PersonVehicles) attribute written
        # by add_person below, which MATSim's router uses to resolve a vehicle id
        # per mode -- missing an entry here aborts routing for that agent/mode,
        # exactly like a missing vehicles.xml row (see braunschweig.matsim.scenario
        # .vehicles for the corresponding vehicles.xml merge).
        raw["vehicles"] = concat_frame(raw["vehicles"], inc["vehicles"], "owner_id")
        raw["vehicles"] = concat_frame(raw["vehicles"], student_inc["vehicles"],
                                       "owner_id")

    # Zone-based parking costs (issue #436). Attached AFTER the in-commuter merge so injected
    # in-commuters carry parkingZone / parkingFree exactly like residents. The order is fixed by
    # the data flow: the resident zone is read from the zoned home activities, and the free-parking
    # draw needs the zone of every work/education activity. The module is imported here rather
    # than at module level, so the OFF path never imports it; validate() hashes it by name.
    if parking_zones_enabled:
        import braunschweig.parking.attach as attach

        release = context.stage(ZONES_STAGE)
        zoned_activities = attach.attach_parking_zones(
            raw["activities"], raw["locations"], release["zones"])
        raw["activities"] = _require_attach_result(
            raw["activities"], zoned_activities, PARKING_ZONE_COLUMN, "attach_parking_zones",
            ACTIVITY_KEY_COLUMNS)
        persons_with_zone = attach.attach_resident_zones(
            raw["persons"], raw["activities"], release["tariffs"])
        raw["persons"] = _require_attach_result(
            raw["persons"], persons_with_zone, RESIDENT_PARKING_ZONE_COLUMN,
            "attach_resident_zones", PERSON_KEY_COLUMNS)
        drawn_activities = attach.draw_parking_free(
            raw["activities"], release["tariffs"], release["workplace_shares"],
            int(context.config("random_seed")), shift=float(context.config(KEY_FREE_SHIFT)))
        raw["activities"] = _require_attach_result(
            raw["activities"], drawn_activities, PARKING_FREE_COLUMN, "draw_parking_free",
            ACTIVITY_KEY_COLUMNS)
        # The resident parking districts (spec Amendment C3): a second layer on the same merged frames. Attached
        # after the zone columns only by convention; neither layer reads the other.
        districted_activities = attach.attach_parking_districts(
            raw["activities"], raw["locations"], release["districts"])
        raw["activities"] = _require_attach_result(
            raw["activities"], districted_activities, PARKING_DISTRICT_COLUMN, "attach_parking_districts",
            ACTIVITY_KEY_COLUMNS)
        persons_with_district = attach.attach_resident_districts(raw["persons"], raw["activities"])
        raw["persons"] = _require_attach_result(
            raw["persons"], persons_with_district, RESIDENT_PARKING_DISTRICT_COLUMN,
            "attach_resident_districts", PERSON_KEY_COLUMNS)
        _log_parking_coverage(raw["activities"], raw["persons"], resident_person_ids, raw["trips"])
    else:
        # No column -> matsim.scenario.population.effective_activity_fields and
        # effective_person_fields are unchanged -> no parking attribute -> byte-identical plans.
        logger.info("%s %s is false -- no parkingZone, parkingFree, parkingDistrict, residentParkingZone or "
                    "residentParkingDistrict attribute is written.", _PARKING_LOG_TAG, KEY_PARKING_ZONES_ENABLED)

    df_persons, df_activities, df_trips, df_vehicles = base.prepare_frames(
        raw["persons"], raw["activities"], raw["locations"], raw["trips"], raw["vehicles"])
    return base.write_population(output_path, df_persons, df_activities, df_trips,
                                df_vehicles, enable_urban_parking, context,
                                write_income_eur=write_income_eur)
