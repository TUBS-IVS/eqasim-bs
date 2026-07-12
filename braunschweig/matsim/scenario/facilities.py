"""MATSim facilities writer: assembled secondary candidates + in-commuters.

Overrides matsim.scenario.facilities twice over the base behaviour:

1. **Assembled secondary candidates.** When ``secondary_building_potentials``
   is ON, the secondary chainsolvers place activities on the ASSEMBLED
   candidate set (gpkg ``sec_b_*`` buildings, legacy ``sec_*`` 'other' rows,
   external Gemeinde-centroid ids, residential ``sec_res_*`` visit rows) --
   not on the legacy ``synthesis.locations.secondary`` frame the base writer
   uses. Facilities therefore consume the SAME
   ``braunschweig.synthesis.locations.secondary_candidates`` stage, so every
   location id the population can reference exists as a facility (2026-07-11
   kreis5 fix: realised ``sec_b_*`` ids were missing from facilities.xml and
   crashed RunPreparation's LinkAssignment).

2. **In-commuter facilities (terminal).** Registers a home facility
   (``home_<household_id>``) and a work facility (``ic_work_<person_id>``)
   for each injected in-commuter. OFF -> no in-commuter frames ->
   byte-identical.

Additionally a flag-independent coverage validation compares the REALISED
secondary location ids against the written secondary facility ids and raises
before writing if any id would be dangling (fail-early instead of a Java
IllegalStateException 30+ minutes later).
"""
from __future__ import annotations

import logging

import geopandas as gpd
import pandas as pd

import matsim.scenario.facilities as base

logger = logging.getLogger(__name__)


def configure(context):
    base.configure(context)
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("braunschweig.synthesis.incommuters")

    # Default mirrors secondary_chainsolvers.configure (True there).
    if context.config("secondary_building_potentials", True):
        context.stage("braunschweig.synthesis.locations.secondary_candidates")

    # Realised secondary locations, for the dangling-id validation below.
    context.stage("synthesis.population.spatial.secondary.locations")


def secondary_facility_frame(df_candidates):
    """Map the assembled candidate frame onto the facilities SECONDARY_FIELDS.

    ``offers_visit`` rows (residential leisure_visit candidates) are folded
    into ``offers_leisure``: the population writes the BASE purpose
    ("leisure") for subtype legs, so the facility must offer "leisure" for a
    visit location to be consistent.
    """
    df = df_candidates.copy()
    if "offers_visit" in df.columns:
        df["offers_leisure"] = df["offers_leisure"] | df["offers_visit"]
    missing = [c for c in base.SECONDARY_FIELDS if c not in df.columns]
    if missing:
        raise ValueError(
            "[braunschweig.facilities] assembled secondary candidate frame is "
            "missing required column(s) %s; available: %s" % (missing, list(df.columns))
        )
    return df[base.SECONDARY_FIELDS]


def validate_secondary_coverage(df_realised, df_secondary):
    """Fail fast if a realised secondary location id has no facility row.

    Every secondary activity's ``location_id`` must exist in the written
    secondary facilities, otherwise MATSim's RunPreparation crashes much later
    with an opaque ``IllegalStateException`` (the 2026-07-11 kreis5 failure
    mode). Raises RuntimeError naming the miss count and a sample.
    """
    realised_ids = set(df_realised["location_id"].dropna().astype(str))
    written_ids = set(df_secondary["location_id"].astype(str))
    missing = realised_ids - written_ids
    if missing:
        sample = sorted(missing)[:5]
        raise RuntimeError(
            "[braunschweig.facilities] %d realised secondary location id(s) have "
            "no facility row (sample: %s). The candidate set used by the "
            "chainsolvers and the facilities writer have diverged -- both must "
            "consume braunschweig.synthesis.locations.secondary_candidates."
            % (len(missing), sample)
        )
    logger.info(
        "[braunschweig.facilities] secondary coverage OK: %d realised ids, "
        "%d facility rows, 0 dangling.", len(realised_ids), len(written_ids),
    )


def execute(context):
    output_path = "%s/facilities.xml.gz" % context.path()
    df_homes, df_primary, df_secondary = base.load_facility_frames(context)

    if context.config("secondary_building_potentials"):
        df_secondary = secondary_facility_frame(
            context.stage("braunschweig.synthesis.locations.secondary_candidates"))

    # Fail-early check: all realised secondary ids must be writable facilities.
    df_realised = context.stage("synthesis.population.spatial.secondary.locations")[0]
    validate_secondary_coverage(df_realised, df_secondary)

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
