"""MATSim vehicles writer with cross-cordon in-commuter injection (terminal).

Overrides matsim.scenario.vehicles: appends the injected in-commuter car vehicles to
the resident vehicles before the SAME write. With the German fleet active
(``vehicles_method:"household"``) the in-commuter cars are drawn from the NDS fleet
and introduce their own distinct HBEFA :class:`VehicleType` records, so those are
merged into the resident type table (de-duplicated by ``type_id``) before the write;
with the legacy fleet the in-commuter cars reuse the existing ``default_car`` type, so
the merged type table is unchanged. OFF (cordon disabled) -> in-commuter frame empty
-> byte-identical.

NOTE (#140 Task 5): ``braunschweig.synthesis.student_incommuters`` is intentionally NOT
wired in here -- that stage's output has no ``vehicles``/``vehicle_types`` key at all
(only persons, households, trips, activities, locations), so there is nothing to merge
for students at this stage. This is a known gap: a car-mode student in-commuter (the
stage does assign a "car" leg for some agents, see student_incommuters._inject) gets no
"car" (or "car_passenger") vehicle registered anywhere, which would make that agent's
initial car leg unroutable by MATSim. Flagged for a follow-up; not fixed here, since it
requires extending Task 4's stage contract, not this merge task.
"""
from __future__ import annotations

import pandas as pd

import matsim.scenario.vehicles as base
from braunschweig.synthesis.incommuter_merge._base import concat_frame


def configure(context):
    base.configure(context)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")


def execute(context):
    output_path = "%s/vehicles.xml.gz" % context.path()
    df_vehicle_types, df_vehicles = context.stage("synthesis.vehicles.vehicles")
    if context.config("cordon_enabled"):
        inc = context.stage("braunschweig.synthesis.incommuters")
        df_vehicles = concat_frame(df_vehicles, inc["vehicles"], "vehicle_id")
        # Register the in-commuter cars' distinct HBEFA types (German fleet) that are
        # not already in the resident type table; de-duplicate by type_id so a type
        # shared with the resident fleet is written once. Empty for the legacy fleet
        # (in-commuter cars reuse default_car) -> the type table is unchanged.
        inc_types = inc.get("vehicle_types")
        if inc_types is not None and len(inc_types) > 0:
            df_vehicle_types = pd.concat([df_vehicle_types, inc_types], ignore_index=True)
            df_vehicle_types = df_vehicle_types.drop_duplicates(
                subset="type_id").reset_index(drop=True)
    return base.write_vehicles(output_path, df_vehicle_types, df_vehicles, context)
