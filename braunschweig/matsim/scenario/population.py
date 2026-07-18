"""MATSim population writer with cross-cordon in-commuter injection (terminal).

Overrides matsim.scenario.population: loads the resident synthesis frames, and -- when
cordon_enabled -- concatenates the injected in-commuter frames before the SAME prepare +
write. Two independent in-commuter sources are merged: the SvB cross-cordon commuters
(braunschweig.synthesis.incommuters; persons, activities, locations, trips, vehicles) and
the student in-commuters (braunschweig.synthesis.student_incommuters, #140 Task 5;
persons, activities, locations, trips -- this stage emits no vehicles frame, see the
concern noted at the vehicles line below). Terminal injection downstream of the whole
synthesis chain, so there is no alias cycle and the resident chain stays resident-only.
OFF -> both in-commuter frames empty -> byte-identical output.
"""
from __future__ import annotations

import matsim.scenario.population as base
from braunschweig.synthesis.incommuter_merge._base import (assert_unique_ids,
                                                            concat_frame)


def configure(context):
    base.configure(context)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")
        context.stage("braunschweig.synthesis.student_incommuters")


def execute(context):
    output_path = "%s/population.xml.gz" % context.path()
    enable_urban_parking = bool(context.config("enable_urban_parking"))
    write_income_eur = bool(context.config("write_income_eur"))
    raw = base.load_raw(context)

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
        # NOTE (#140 Task 5 concern): student_incommuters emits no "vehicles"
        # frame at all (unlike the SvB stage, which builds a car vehicle for
        # car-mode agents and a car_passenger vehicle for every agent), so only
        # the SvB in-commuter vehicles are concatenated here. A car-mode
        # student in-commuter therefore currently has NO registered vehicle,
        # which would make its initial car leg unroutable by MATSim -- flagged
        # for a follow-up, not fixed here (out of this task's scope: Task 4's
        # stage contract and this task's brief both list only five frames --
        # persons, households, trips, activities, locations -- for the student
        # stage, with no vehicles key).
        raw["vehicles"] = concat_frame(raw["vehicles"], inc["vehicles"], "owner_id")

    df_persons, df_activities, df_trips, df_vehicles = base.prepare_frames(
        raw["persons"], raw["activities"], raw["locations"], raw["trips"], raw["vehicles"])
    return base.write_population(output_path, df_persons, df_activities, df_trips,
                                df_vehicles, enable_urban_parking, context,
                                write_income_eur=write_income_eur)
