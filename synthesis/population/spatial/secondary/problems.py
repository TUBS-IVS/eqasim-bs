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

def _anchor_coordinates(activity_anchors, person_id, activity_index):
    """Coordinates of a pre-anchored escort_linked activity. The caller that
    injects escort_linked trips must derive the trip rewrite and the anchor
    table from the SAME assignment, so a miss is a bug -- fail fast."""
    try:
        point = activity_anchors[(person_id, activity_index)]
    except KeyError:
        raise KeyError(
            f"escort_linked activity (person_id={person_id}, "
            f"activity_index={activity_index}) has no entry in activity_anchors; "
            "the escort_linked trip rewrite and the anchor table must be built "
            "from the same assignment (see "
            "braunschweig/synthesis/locations/escort_links.py)."
        ) from None
    return np.array([[point.x, point.y]])

def find_assignment_problems(df, df_locations, activity_anchors = None):
    """
        Enriches assignment problems with:
          - Locations of the fixed activities
          - Size of the problem
          - Reduces purposes to the variable ones

        activity_anchors (eqasim-bs #201 multi-child fix): optional mapping
        {(person_id, activity_index): shapely Point} consulted for the
        "escort_linked" boundary purpose INSTEAD of a per-person location
        column, so consecutive escort activities can anchor at different
        children's schools. Origin activity index = trip_index of the
        problem's first trip; destination = trip_index + number of trips.
        The legacy path (None, all upstream callers) is unchanged.
    """
    # Presence-based field list: the legacy/French path passes a location frame
    # without the "escort_linked" column (eqasim-bs #201 Phase 2) and must keep
    # today's behaviour exactly. Since the multi-child fix, the Braunschweig
    # chainsolver resolves "escort_linked" boundaries via the activity_anchors
    # table instead of a per-person column; the column path below remains for
    # callers that attach one. A boundary purpose can only be "escort_linked"
    # when the caller injected that trip, which implies it passed anchors (or
    # the column), so the lookups below are safe by construction.
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
            if activity_anchors is not None and origin_purpose == "escort_linked":
                problem["origin"] = _anchor_coordinates(
                    activity_anchors, problem["person_id"], problem["trip_index"])
            else:
                problem["origin"] = current_location[location_fields.index(origin_purpose)] # Shapely POINT
                problem["origin"] = np.array([[problem["origin"].x, problem["origin"].y]])

        if destination_purpose in FIXED_PURPOSES:
            if activity_anchors is not None and destination_purpose == "escort_linked":
                problem["destination"] = _anchor_coordinates(
                    activity_anchors, problem["person_id"],
                    problem["trip_index"] + len(problem["modes"]))
            else:
                problem["destination"] = current_location[location_fields.index(destination_purpose)] # Shapely POINT
                problem["destination"] = np.array([[problem["destination"].x, problem["destination"].y]])

        if problem["origin"] is None:
            problem["activity_index"] = problem["trip_index"]
        else:
            problem["activity_index"] = problem["trip_index"] + 1

        yield problem
