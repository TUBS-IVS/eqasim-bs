import numpy as np
import pandas as pd

FIELDS = ["person_id", "trip_index", "preceding_purpose", "following_purpose", "mode", "travel_time"]
# "escort_linked" (eqasim-bs #201 Phase 2): household-linked escort activities are
# pre-anchored at the linked child's education location; the purpose only ever occurs when the
# Braunschweig chainsolver stage injects it, so upstream-path behaviour is unchanged.
FIXED_PURPOSES = ["home", "work", "education", "escort_linked"]

def find_bare_assignment_problems(df):
    problem = None

    for row in df[FIELDS].itertuples(index = False):
        person_id, trip_index, preceding_purpose, following_purpose, mode, travel_time = row

        if not problem is None and person_id != problem["person_id"]:
            # We switch person, but we're still tracking a problem. This is a tail!
            yield problem
            problem = None

        if problem is None:
            # Start a new problem
            problem = dict(
                person_id = person_id, trip_index = trip_index, purposes = [preceding_purpose],
                modes = [], travel_times = []
            )

        problem["purposes"].append(following_purpose)
        problem["modes"].append(mode)
        problem["travel_times"].append(travel_time)

        if problem["purposes"][-1] in FIXED_PURPOSES:
            # The current chain (or initial tail) ends with a fixed activity.
            yield problem
            problem = None

    if not problem is None:
        yield problem

LOCATION_FIELDS = ["person_id", "home", "work", "education", "escort_linked"]

def find_assignment_problems(df, df_locations):
    """
        Enriches assignment problems with:
          - Locations of the fixed activities
          - Size of the problem
          - Reduces purposes to the variable ones
    """
    # Presence-based field list: the legacy/French path passes a location frame
    # without the "escort_linked" column (eqasim-bs #201 Phase 2) and must keep
    # today's behaviour exactly. A boundary purpose can only be "escort_linked"
    # when the caller injected that trip, which implies the column is present, so
    # the index lookups below are safe by construction.
    location_fields = [
        field for field in LOCATION_FIELDS
        if field == "person_id" or field in df_locations.columns
    ]
    location_iterator = df_locations[location_fields].itertuples(index = False)
    current_location = None

    for problem in find_bare_assignment_problems(df):
        origin_purpose = problem["purposes"][0]
        destination_purpose = problem["purposes"][-1]

        # Reduce purposes
        if origin_purpose in FIXED_PURPOSES and destination_purpose in FIXED_PURPOSES:
            problem["purposes"] = problem["purposes"][1:-1]

        elif origin_purpose in FIXED_PURPOSES:
            problem["purposes"] = problem["purposes"][1:]

        elif destination_purpose in FIXED_PURPOSES:
            problem["purposes"] = problem["purposes"][:-1]

        else:
            pass # Neither chain nor tail

        # Define size
        problem["size"] = len(problem["purposes"])

        if problem["size"] == 0:
            continue # We can skip if there are no variable activities

        # Advance location iterator until we arrive at the current problem's person
        while current_location is None or current_location[0] != problem["person_id"]:
            current_location = next(location_iterator)

        # Define origin and destination locations if they have fixed purposes
        problem["origin"] = None
        problem["destination"] = None

        if origin_purpose in FIXED_PURPOSES:
            problem["origin"] = current_location[location_fields.index(origin_purpose)] # Shapely POINT
            problem["origin"] = np.array([[problem["origin"].x, problem["origin"].y]])

        if destination_purpose in FIXED_PURPOSES:
            problem["destination"] = current_location[location_fields.index(destination_purpose)] # Shapely POINT
            problem["destination"] = np.array([[problem["destination"].x, problem["destination"].y]])

        if problem["origin"] is None:
            problem["activity_index"] = problem["trip_index"]
        else:
            problem["activity_index"] = problem["trip_index"] + 1

        yield problem
