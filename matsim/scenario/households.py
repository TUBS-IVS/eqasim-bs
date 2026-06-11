import io, gzip

import numpy as np
import pandas as pd

import matsim.writers as writers

def configure(context):
    context.stage("synthesis.population.enriched")

# Bavaria: high_income added
FIELDS = ["household_id", "person_id", "household_income", "high_income", "car_availability", "bicycle_availability", "census_household_id"]

def add_household(writer, household, member_ids, census_id_type = None):
    # ``census_id_type`` is the Java type of the censusId attribute, decided ONCE
    # per column from the pandas dtype (writers.column_java_type) by the frame
    # writer so the whole file emits a single consistent type. Per-value fallback
    # only for legacy callers without frame context.
    writer.start_household(household[FIELDS.index("household_id")])
    writer.add_members(member_ids)

    writer.start_attributes()
    writer.add_attribute("carAvailability", "java.lang.String", household[FIELDS.index("car_availability")])
    writer.add_attribute("bicycleAvailability", "java.lang.String", household[FIELDS.index("bicycle_availability")])
    writer.add_attribute("household_income", "java.lang.String", household[FIELDS.index("household_income")]) # Bavaria update
    writer.add_attribute("high_income", "java.lang.Boolean", household[FIELDS.index("high_income")]) # Bavaria added
    _census_hh_id = household[FIELDS.index("census_household_id")]
    if census_id_type is None:
        census_id_type = writers.long_or_string_type(_census_hh_id)
    writer.add_attribute("censusId", census_id_type, _census_hh_id)
    writer.end_attributes()

    writer.end_household()

def execute(context):
    output_path = "%s/households.xml.gz" % context.path()
    df_persons = context.stage("synthesis.population.enriched")
    return write_households(output_path, df_persons, context)


def write_households(output_path, df_persons, context):
    """Write the households XML from a persons frame. Factored out so a regional
    override can concatenate injected in-commuters before writing."""
    df_persons = df_persons.sort_values(by = ["household_id", "person_id"])
    df_persons = df_persons[FIELDS]

    # Java type for censusId, decided ONCE from the pandas dtype (a mixed
    # Long/String attribute within one file crashes Java readers; a float id
    # column raises here instead of producing unparseable '123.0').
    census_id_type = writers.column_java_type(df_persons["census_household_id"])

    current_members = []
    current_household_id = None
    current_household = None

    with gzip.open(output_path, 'wb+') as writer:
        with io.BufferedWriter(writer, buffer_size = 2 * 1024**3) as writer:
            writer = writers.HouseholdsWriter(writer)
            writer.start_households()

            with context.progress(total = len(df_persons), label = "Writing households ...") as progress:
                for item in df_persons.itertuples(index = False):
                    if current_household_id != item[FIELDS.index("household_id")]:
                        if not current_household_id is None:
                            add_household(writer, current_household, current_members,
                                          census_id_type = census_id_type)

                        current_household = item
                        current_household_id = item[FIELDS.index("household_id")]
                        current_members = [item[FIELDS.index("person_id")]]
                    else:
                        current_members.append(item[FIELDS.index("person_id")])

                    progress.update()

            if not current_household_id is None:
                add_household(writer, current_household, current_members,
                              census_id_type = census_id_type)

            writer.end_households()

    return "households.xml.gz"
