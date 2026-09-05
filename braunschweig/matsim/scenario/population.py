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
"""
from __future__ import annotations

import logging

import matsim.scenario.population as base
from braunschweig.synthesis.incommuter_merge._base import (assert_unique_ids,
                                                            concat_frame)

logger = logging.getLogger(__name__)

_LOG_TAG = "[commute day population]"

#: Reporting-day view of the day (ADR-0104, issue #244). The MATSim plans must carry the day
#: the simulation runs, so the pre-assignment trips/activities the vendored ``load_raw`` reads
#: are replaced by these before the frames are prepared. Pass-throughs when
#: ``commute_day_state_enabled`` is false.
DAY_TRIPS_STAGE = "synthesis.population.trips.final"
DAY_ACTIVITIES_STAGE = "synthesis.population.activities.final"
STATE_STAGE = "braunschweig.synthesis.commute_day.state_stage"

KEY_COMMUTE_DAY_STATE_ENABLED = "commute_day_state_enabled"
DEFAULT_COMMUTE_DAY_STATE_ENABLED = True

#: Person column carrying the drawn state; ``matsim.scenario.population.OPTIONAL_PERSON_FIELDS``
#: emits it as the MATSim person attribute ``commuteDayState`` (java.lang.String) for the
#: persons that have one, and writes no attribute at all for the persons that do not.
STATE_COLUMN = "commute_day_state"


def configure(context):
    base.configure(context)
    context.stage(DAY_TRIPS_STAGE)
    context.stage(DAY_ACTIVITIES_STAGE)
    context.config(KEY_COMMUTE_DAY_STATE_ENABLED, DEFAULT_COMMUTE_DAY_STATE_ENABLED)
    # Declared only when the model is on, exactly like the cordon block below: a workflow that
    # runs with the model off (every configs/fixtures/* config, whose reporting-day aliases are
    # pass-throughs of the pre-assignment stages) must not carry the whole donor/state chain in
    # its DAG for a value it never writes.
    if context.config(KEY_COMMUTE_DAY_STATE_ENABLED):
        context.stage(STATE_STAGE)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")
        context.stage("braunschweig.synthesis.student_incommuters")


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


def execute(context):
    output_path = "%s/population.xml.gz" % context.path()
    enable_urban_parking = bool(context.config("enable_urban_parking"))
    write_income_eur = bool(context.config("write_income_eur"))
    raw = base.load_raw(context)

    # The finished day replaces the pre-assignment one BEFORE the in-commuter injection below,
    # so the injected frames are aligned against the same schema either way.
    raw["trips"] = context.stage(DAY_TRIPS_STAGE)
    raw["activities"] = context.stage(DAY_ACTIVITIES_STAGE)
    if bool(context.config(KEY_COMMUTE_DAY_STATE_ENABLED)):
        raw["persons"] = attach_commute_day_state(
            raw["persons"], context.stage(STATE_STAGE)["states"])
    else:
        # No column -> matsim.scenario.population.effective_person_fields is unchanged -> no
        # commuteDayState attribute -> byte-identical plans.
        logger.info("%s %s is false -- no commuteDayState attribute is written.",
                    _LOG_TAG, KEY_COMMUTE_DAY_STATE_ENABLED)

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

    df_persons, df_activities, df_trips, df_vehicles = base.prepare_frames(
        raw["persons"], raw["activities"], raw["locations"], raw["trips"], raw["vehicles"])
    return base.write_population(output_path, df_persons, df_activities, df_trips,
                                df_vehicles, enable_urban_parking, context,
                                write_income_eur=write_income_eur)
