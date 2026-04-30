import io, gzip
import itertools

import numpy as np
import pandas as pd

import matsim.writers as writers
from matsim.writers import backlog_iterator

def configure(context):
    context.stage("synthesis.population.enriched")

    context.stage("synthesis.population.activities")
    context.stage("synthesis.population.spatial.locations")

    context.stage("synthesis.population.trips")
    context.stage("synthesis.vehicles.vehicles")

    # Opt-in flag for the Bavaria "isParis" urban-parking code paths
    # (BavariaCarCostModel + munich.* utility shifts). When false (default),
    # neither the person-level nor the activity-level isParis attribute is
    # written, preserving pre-refactor behaviour (Decision D-5). When true,
    # the BS inner ring is emitted on activities and the resident flag on
    # persons, activating 3 EUR/h parking cost for non-residents to the
    # urban core.
    context.config("enable_urban_parking", False)

# is_urban_resident: boolean flag set by the regional enricher (e.g.
# braunschweig.synthesis.population.enriched sets it from inside_braunschweig).
# Written into MATSim XML under the Java-expected attribute key "isParis"
# (the Bavaria fork inherited the IDF/Paris attribute name without renaming
# it; BavariaPredictorUtils.isParisResident reads exactly that key). Setting
# this flag enables the BavariaCarCostModel parking-cost exemption for urban
# residents.
#
# Activity-side isParis: in addition to the person flag above, every activity
# whose location lies within the BS inner-ring polygon (<= _URBAN_RADIUS_M of
# the BS Hbf in EPSG:25832) is tagged with isParis=true. This activates the
# Bavaria Java code paths in BavariaCarCostModel.hasParisDestination (3.0
# EUR/h parking cost for non-resident commuters into the urban core) and the
# munich.{car_u, bicycle_u, carPassenger_u} utility shifts in the Bicycle/
# Car/CarPassenger utility estimators. Note that the upstream Bavaria
# defaults for those utility coefficients are 0.0, so only the parking cost
# is currently behaviourally relevant; the utility hooks become active once
# the coefficients are calibrated.
_URBAN_HBF_E = 605170.0  # BS Hbf (EPSG:25832)
_URBAN_HBF_N = 5790274.0
_URBAN_RADIUS_M = 8000.0  # Inner ring 1 (cf. braunschweig/data/vrb/zones.py)

PERSON_FIELDS = [
    "person_id", "household_income", "car_availability", "bicycle_availability",
    "census_household_id", "census_person_id", "household_id",
    "has_license", "has_pt_subscription",
    "hts_id", "hts_household_id",
    "age", "employed", "sex",
    "high_income", "is_urban_resident",  # Bavaria added (urban-area resident, written as isParis)
    "pt_subscription_type",  # Braunschweig added (MiD P24.1 ticket category)
]

ACTIVITY_FIELDS = [
    "person_id", "start_time", "end_time", "purpose", "geometry", "location_id"
]

TRIP_FIELDS = [
    "person_id", "mode", "departure_time", "travel_time"
]

VEHICLE_FIELDS = [
    "owner_id", "vehicle_id", "mode"
]

def add_person(writer, person, activities, trips, vehicles, enable_urban_parking = False):
    writer.start_person(person[PERSON_FIELDS.index("person_id")])

    writer.start_attributes()
    writer.add_attribute("householdId", "java.lang.Integer", person[PERSON_FIELDS.index("household_id")])
    writer.add_attribute("householdIncome", "java.lang.String", person[PERSON_FIELDS.index("household_income")]) # Bavaria updated
    writer.add_attribute("highIncome", "java.lang.Boolean", person[PERSON_FIELDS.index("high_income")]) # Bavaria added
    # NOTE: Bavaria/IDF Java reads this under the legacy attribute key "isParis"
    # (BavariaPredictorUtils.isParisResident). Keep the key as-is per Decision
    # D-1c -- only the Python source column was renamed. Gated behind
    # enable_urban_parking to keep the pre-refactor behaviour bit-identical
    # when the flag is off (D-5).
    if enable_urban_parking:
        writer.add_attribute("isParis", "java.lang.Boolean", person[PERSON_FIELDS.index("is_urban_resident")])

    writer.add_attribute("carAvailability", "java.lang.String", person[PERSON_FIELDS.index("car_availability")])
    writer.add_attribute("bicycleAvailability", "java.lang.String", person[PERSON_FIELDS.index("bicycle_availability")])

    writer.add_attribute("censusHouseholdId", "java.lang.Long", person[PERSON_FIELDS.index("census_household_id")])
    writer.add_attribute("censusPersonId", "java.lang.Long", person[PERSON_FIELDS.index("census_person_id")])

    writer.add_attribute("htsHouseholdId", "java.lang.Long", person[PERSON_FIELDS.index("hts_household_id")])
    writer.add_attribute("htsPersonId", "java.lang.Long", person[PERSON_FIELDS.index("hts_id")])

    writer.add_attribute("hasPtSubscription", "java.lang.Boolean", person[PERSON_FIELDS.index("has_pt_subscription")])
    writer.add_attribute("ptSubscriptionType", "java.lang.String", str(person[PERSON_FIELDS.index("pt_subscription_type")]))
    writer.add_attribute("hasLicense", "java.lang.String", writer.yes_no(person[PERSON_FIELDS.index("has_license")]))

    writer.add_attribute("age", "java.lang.Integer", person[PERSON_FIELDS.index("age")])
    writer.add_attribute("employed", "java.lang.String", person[PERSON_FIELDS.index("employed")])
    writer.add_attribute("sex", "java.lang.String", person[PERSON_FIELDS.index("sex")][0])

    writer.add_attribute("vehicles", "org.matsim.vehicles.PersonVehicles", "{{{content}}}".format(content = ",".join([
        "\"{mode}\":\"{id}\"".format(mode = v[VEHICLE_FIELDS.index("mode")], id = v[VEHICLE_FIELDS.index("vehicle_id")])
        for v in vehicles
    ])))

    writer.end_attributes()

    writer.start_plan(selected = True)

    for activity, trip in itertools.zip_longest(activities, trips):
        start_time = activity[ACTIVITY_FIELDS.index("start_time")]
        end_time = activity[ACTIVITY_FIELDS.index("end_time")]
        location_id = activity[ACTIVITY_FIELDS.index("location_id")]
        geometry = activity[ACTIVITY_FIELDS.index("geometry")]

        if activity[ACTIVITY_FIELDS.index("purpose")] == "home":
            location_id = "home_%s" % person[PERSON_FIELDS.index("household_id")]

        location = writer.location(
            geometry.x, geometry.y,
            None if location_id == -1 else location_id
        )

        # Activity-level isParis: tag activities whose location lies within
        # the BS inner ring (<= _URBAN_RADIUS_M from BS Hbf). See module
        # docstring above for the rationale. Off by default (D-5).
        if enable_urban_parking:
            dx = geometry.x - _URBAN_HBF_E
            dy = geometry.y - _URBAN_HBF_N
            is_urban_activity = (dx * dx + dy * dy) <= (_URBAN_RADIUS_M * _URBAN_RADIUS_M)
            activity_attributes = {
                "isParis": ("java.lang.Boolean", "true" if is_urban_activity else "false"),
            }
        else:
            activity_attributes = None

        writer.add_activity(
            type = activity[ACTIVITY_FIELDS.index("purpose")],
            location = location,
            start_time = None if np.isnan(start_time) else start_time,
            end_time = None if np.isnan(end_time) else end_time,
            attributes = activity_attributes,
        )

        if not trip is None:
            writer.add_leg(
                mode = trip[TRIP_FIELDS.index("mode")],
                departure_time = trip[TRIP_FIELDS.index("departure_time")],
                travel_time = trip[TRIP_FIELDS.index("travel_time")]
            )

    writer.end_plan()
    writer.end_person()

def execute(context):
    output_path = "%s/population.xml.gz" % context.path()

    enable_urban_parking = bool(context.config("enable_urban_parking"))

    df_persons = context.stage("synthesis.population.enriched")
    df_persons = df_persons.sort_values(by = ["household_id", "person_id"])
    df_persons = df_persons[PERSON_FIELDS]

    df_activities = context.stage("synthesis.population.activities").sort_values(by = ["person_id", "activity_index"])
    df_locations = context.stage("synthesis.population.spatial.locations")[[
        "person_id", "activity_index", "geometry", "location_id"]].sort_values(by = ["person_id", "activity_index"])

    df_activities = pd.merge(df_activities, df_locations, how = "left", on = ["person_id", "activity_index"])
    #df_activities["location_id"] = df_activities["location_id"].fillna(-1).astype(int)

    df_trips = context.stage("synthesis.population.trips")
    df_trips["travel_time"] = df_trips["arrival_time"] - df_trips["departure_time"]

    df_vehicles = context.stage("synthesis.vehicles.vehicles")[1]
    df_vehicles = df_vehicles.sort_values(by = ["owner_id"])

    with gzip.open(output_path, 'wb+') as writer:
        with io.BufferedWriter(writer, buffer_size = 2 * 1024**3) as writer:
            writer = writers.PopulationWriter(writer)
            writer.start_population()

            activity_iterator = backlog_iterator(iter(df_activities[ACTIVITY_FIELDS].itertuples(index = False)))
            trip_iterator = backlog_iterator(iter(df_trips[TRIP_FIELDS].itertuples(index = False)))
            vehicle_iterator = backlog_iterator(iter(df_vehicles[VEHICLE_FIELDS].itertuples(index = False)))

            with context.progress(total = len(df_persons), label = "Writing population ...") as progress:
                for person in df_persons.itertuples(index = False):
                    person_id = person[PERSON_FIELDS.index("person_id")]

                    activities = []
                    trips = []
                    vehicles = []

                    # Track all activities for person
                    while activity_iterator.has_next():
                        activity = activity_iterator.next()

                        if not activity[ACTIVITY_FIELDS.index("person_id")] == person_id:
                            activity_iterator.previous()
                            break
                        else:
                            activities.append(activity)

                    assert len(activities) > 0

                    # Track all trips for person
                    while trip_iterator.has_next():
                        trip = trip_iterator.next()

                        if not trip[TRIP_FIELDS.index("person_id")] == person_id:
                            trip_iterator.previous()
                            break
                        else:
                            trips.append(trip)

                    assert len(trips) == len(activities) - 1

                    # Track all vehicles for person
                    while vehicle_iterator.has_next():
                        vehicle = vehicle_iterator.next()

                        if not vehicle[VEHICLE_FIELDS.index("owner_id")] == person_id:
                            vehicle_iterator.previous()
                            break
                        else:
                            vehicles.append(vehicle)

                    add_person(writer, person, activities, trips, vehicles, enable_urban_parking)
                    progress.update()

            writer.end_population()

    return "population.xml.gz"
