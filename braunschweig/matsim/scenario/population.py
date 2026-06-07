"""MATSim population writer with cross-cordon in-commuter injection (terminal).

Overrides matsim.scenario.population: loads the resident synthesis frames, and -- when
cordon_enabled -- concatenates the injected in-commuter frames (persons, activities,
locations, trips, vehicles) before the SAME prepare + write. Terminal injection
downstream of the whole synthesis chain, so there is no alias cycle and the resident
chain stays resident-only. OFF -> in-commuter frames empty -> byte-identical output.
"""
from __future__ import annotations

import matsim.scenario.population as base
from braunschweig.synthesis.incommuter_merge._base import concat_frame


def configure(context):
    base.configure(context)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")


def execute(context):
    output_path = "%s/population.xml.gz" % context.path()
    enable_urban_parking = bool(context.config("enable_urban_parking"))
    write_income_eur = bool(context.config("write_income_eur"))
    raw = base.load_raw(context)

    if context.config("cordon_enabled"):
        inc = context.stage("braunschweig.synthesis.incommuters")
        raw["persons"] = concat_frame(raw["persons"], inc["persons"], "person_id")
        raw["activities"] = concat_frame(raw["activities"], inc["activities"],
                                         ["person_id", "activity_index"])
        raw["locations"] = concat_frame(raw["locations"], inc["locations"],
                                        ["person_id", "activity_index"])
        raw["trips"] = concat_frame(raw["trips"], inc["trips"],
                                    ["person_id", "trip_index"])
        raw["vehicles"] = concat_frame(raw["vehicles"], inc["vehicles"], "owner_id")

    df_persons, df_activities, df_trips, df_vehicles = base.prepare_frames(
        raw["persons"], raw["activities"], raw["locations"], raw["trips"], raw["vehicles"])
    return base.write_population(output_path, df_persons, df_activities, df_trips,
                                df_vehicles, enable_urban_parking, context,
                                write_income_eur=write_income_eur)
