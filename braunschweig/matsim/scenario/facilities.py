"""MATSim facilities writer with cross-cordon in-commuter facilities (terminal).

Overrides matsim.scenario.facilities: registers a home facility (home_<household_id>)
and a work facility (ic_work_<person_id>) for each injected in-commuter, so the
population's in-commuter activities resolve to real facilities in RunPreparation's
LinkAssignment. OFF -> no in-commuter frames -> byte-identical.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

import matsim.scenario.facilities as base


def configure(context):
    base.configure(context)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")


def execute(context):
    output_path = "%s/facilities.xml.gz" % context.path()
    df_homes, df_primary, df_secondary = base.load_facility_frames(context)

    if context.config("cordon_enabled"):
        inc = context.stage("braunschweig.synthesis.incommuters")
        loc = inc["locations"]
        persons = inc["persons"][["person_id", "household_id"]]

        # Home facilities: home_<household_id> at the home (activity 0) coordinate.
        home_rows = loc[loc["activity_index"] == 0].merge(persons, on="person_id")
        inc_homes = gpd.GeoDataFrame(home_rows[["household_id", "geometry"]],
                                     geometry="geometry", crs=df_homes.crs)
        df_homes = pd.concat([df_homes, inc_homes[base.HOME_FIELDS]], ignore_index=True)

        # Work facilities: the unique ic_work_<person_id> at the work (activity 1) coord.
        work_rows = loc[loc["activity_index"] == 1][["location_id", "geometry"]].copy()
        work_rows["is_work"] = True
        inc_work = gpd.GeoDataFrame(work_rows, geometry="geometry", crs=df_primary.crs)
        df_primary = pd.concat([df_primary, inc_work[base.PRIMARY_FIELDS]], ignore_index=True)

    return base.write_facilities(output_path, df_homes, df_primary, df_secondary, context)
