import io, gzip

import numpy as np
import pandas as pd

import matsim.writers as writers

def configure(context):
    context.stage("synthesis.locations.secondary")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.locations")

HOME_FIELDS = [
    "household_id", "geometry"
]

PRIMARY_FIELDS = [
    "location_id", "geometry", "is_work"
]

SECONDARY_FIELDS = [
    "location_id", "geometry", "offers_leisure", "offers_shop", "offers_other"
]

def load_facility_frames(context):
    """Load the (homes, primary, secondary) facility frames. Factored out so a
    regional override can append injected in-commuter facilities before writing.
    Returns (df_homes[HOME_FIELDS], df_primary[PRIMARY_FIELDS], df_secondary[SECONDARY_FIELDS])."""
    df_homes = context.stage("synthesis.population.spatial.home.locations")[HOME_FIELDS]

    df_work, df_education = context.stage("synthesis.population.spatial.primary.locations")
    df_work = df_work.drop_duplicates("location_id").copy()
    df_education = df_education.drop_duplicates("location_id").copy()
    df_work["is_work"] = True
    df_education["is_work"] = False
    df_primary = pd.concat([df_work, df_education])[PRIMARY_FIELDS]

    df_secondary = context.stage("synthesis.locations.secondary")[SECONDARY_FIELDS]
    return df_homes, df_primary, df_secondary


def write_facilities(output_path, df_homes, df_primary, df_secondary, context):
    """Write the facilities XML from prepared (homes, primary, secondary) frames."""
    with gzip.open(output_path, 'wb+') as writer:
        with io.BufferedWriter(writer, buffer_size = 2 * 1024**3) as writer:
            writer = writers.FacilitiesWriter(writer)
            writer.start_facilities()

            with context.progress(total = len(df_homes), label = "Writing home facilities ...") as progress:
                for item in df_homes.itertuples(index = False):
                    geometry = item[HOME_FIELDS.index("geometry")]
                    writer.start_facility(
                        "home_%s" % item[HOME_FIELDS.index("household_id")],
                        geometry.x, geometry.y
                    )
                    writer.add_activity("home")
                    writer.end_facility()
                    progress.update()

            with context.progress(total = len(df_primary), label = "Writing primary facilities ...") as progress:
                for item in df_primary.itertuples(index = False):
                    geometry = item[PRIMARY_FIELDS.index("geometry")]
                    writer.start_facility(
                        str(item[PRIMARY_FIELDS.index("location_id")]),
                        geometry.x, geometry.y
                    )
                    writer.add_activity("work" if item[PRIMARY_FIELDS.index("is_work")] else "education")
                    writer.end_facility()
                    progress.update()

            with context.progress(total = len(df_secondary), label = "Writing secondary facilities ...") as progress:
                for item in df_secondary.itertuples(index = False):
                    geometry = item[SECONDARY_FIELDS.index("geometry")]
                    writer.start_facility(
                        item[SECONDARY_FIELDS.index("location_id")],
                        geometry.x, geometry.y
                    )
                    for purpose in ("shop", "leisure", "other"):
                        if item[SECONDARY_FIELDS.index("offers_%s" % purpose)]:
                            writer.add_activity(purpose)
                    writer.end_facility()
                    progress.update()

            writer.end_facilities()

    return "facilities.xml.gz"


def execute(context):
    output_path = "%s/facilities.xml.gz" % context.path()
    df_homes, df_primary, df_secondary = load_facility_frames(context)
    return write_facilities(output_path, df_homes, df_primary, df_secondary, context)
