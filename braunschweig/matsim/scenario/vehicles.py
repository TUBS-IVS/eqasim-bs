"""MATSim vehicles writer with cross-cordon in-commuter injection (terminal).

Overrides matsim.scenario.vehicles: appends the injected in-commuter car vehicles to
the resident vehicles before the SAME write (the default_car type already exists, so
the type table is unchanged). OFF -> in-commuter frame empty -> byte-identical.
"""
from __future__ import annotations

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
        inc = context.stage("braunschweig.synthesis.incommuters")["vehicles"]
        df_vehicles = concat_frame(df_vehicles, inc, "vehicle_id")
    return base.write_vehicles(output_path, df_vehicle_types, df_vehicles, context)
