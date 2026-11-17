import zipfile, io
import pandas as pd
import geopandas as gpd
import shapely.geometry as geo
import os
import numpy as np

REQUIRED_SLOTS = [
    "agency", "stops", "routes", "trips", "stop_times"
]

OPTIONAL_SLOTS = [
    "calendar", "calendar_dates", "fare_attributes", "fare_rules",
    "shapes", "frequencies", "transfers", "pathways", "levels",
    "feed_info", "translations", "attributions"
]

# Identifier columns read as strings so numeric-looking ids and empty cells do
# not flip column dtypes between feeds (upstream eqasim-france #427; same
# dtype=str-at-read pattern as our key-matching audit, issues #191/#194).
DTYPES = {
    "stops": {
        "stop_id": str, "parent_station": str
    },
    "agency": {
        "agency_id": str
    },
    "routes": {
        "agency_id": str
    }
}

def read_feed(path, stats = None):
    """Read a GTFS zip into a dict of DataFrames and apply the basic cleanup.

    When a dict is passed as ``stats``, the rows dropped for an empty service_id are
    recorded per slot under ``empty_service_id_removed`` and the transfers dropped for a
    missing min_transfer_time under ``transfers_without_min_transfer_time_removed``.
    """
    feed = {}
    if stats is not None:
        stats["empty_service_id_removed"] = {}

    with zipfile.ZipFile(path, "r") as zip:
        available_slots = zip.namelist()
        prefix = None

        if "agency.txt" in available_slots:
            prefix = ""
        else:
            for slot in available_slots:
                if slot.endswith("agency.txt"):
                    prefix = slot.replace("agency.txt", "")
                    print("Warning: GTFS files seem to be located in: %s" % prefix)
                    break

            if prefix is None:
                raise RuntimeError("No GTFS data found in archive")

        for slot in REQUIRED_SLOTS:
            if not "%s%s.txt" % (prefix, slot) in available_slots:
                raise RuntimeError("Missing GTFS information: %s" % slot)

        if not "%scalendar.txt" % prefix in available_slots and not "%scalendar_dates.txt" % prefix in available_slots:
            raise RuntimeError("At least calendar.txt or calendar_dates.txt must be specified.")

        print("Loading GTFS data from %s ..." % path)

        for slot in REQUIRED_SLOTS + OPTIONAL_SLOTS:
            if "%s%s.txt" % (prefix, slot) in available_slots:
                print("  Loading %s.txt ..." % slot)

                with zip.open("%s%s.txt" % (prefix, slot)) as f:
                    feed[slot] = pd.read_csv(f, skipinitialspace = True, dtype = DTYPES.get(slot, None))
            else:
                print("  Not loading %s.txt" % slot)

    # Some cleanup

    for slot in ("calendar", "calendar_dates", "trips"):
        if slot in feed and "service_id" in feed[slot] and pd.api.types.is_string_dtype(feed[slot]["service_id"]):
            initial_count = len(feed[slot])
            feed[slot] = feed[slot][feed[slot]["service_id"].str.len() > 0]
            final_count = len(feed[slot])

            if stats is not None:
                stats["empty_service_id_removed"][slot] = initial_count - final_count

            if final_count != initial_count:
                print("WARNING Removed %d/%d entries from %s with empty service_id" % (
                    initial_count - final_count, initial_count, slot
                ))

    if "stops" in feed:
        df_stops = feed["stops"]

        if not "parent_station" in df_stops:
            print("WARNING Missing parent_station in stops, setting to NaN")
            df_stops["parent_station"] = np.nan

        # Standalone stops (no parent station, location_type 0) are promoted to
        # stations so cut_feed's station-based spatial cut does not silently drop
        # them (upstream eqasim-france #521). Guarded on the column existing:
        # feeds without location_type are legal (see cut_feed, upstream #309);
        # upstream applies this unguarded and would raise a KeyError there.
        if "location_type" in df_stops:
            df_stops.loc[df_stops["parent_station"].isna() & (df_stops["location_type"] == 0), "location_type"] = 1

    if "transfers" in feed:
        df_transfers = feed["transfers"]

        if not "min_transfer_time" in df_transfers:
            df_transfers["min_transfer_time"] = 0

        f = df_transfers["min_transfer_time"].isna()
        if stats is not None:
            stats["transfers_without_min_transfer_time_removed"] = int(np.count_nonzero(f))
        if np.any(f):
            print("WARNING NaN numbers for min_transfer_time in transfers")
            df_transfers = df_transfers[~f]

        df_transfers["min_transfer_time"] = df_transfers["min_transfer_time"].astype(int)
        feed["transfers"] = df_transfers

    if "agency" in feed:
        df_agency = feed["agency"]
        # agency_id is optional for single-agency feeds (upstream eqasim-france #512)
        if "agency_id" not in df_agency.columns:
            df_agency["agency_id"] = "generic"
        df_agency.loc[df_agency["agency_id"].isna(), "agency_id"] = "generic"

    if "routes" in feed:
        df_routes = feed["routes"]
        agency_id = feed["agency"]["agency_id"].values[0]

        if not "agency_id" in df_routes:
            df_routes["agency_id"] = agency_id

        df_routes.loc[df_routes["agency_id"].isna(), "agency_id"] = agency_id

    if "shapes" in feed: del feed["shapes"]
    feed["trips"]["shape_id"] = np.nan

    # Fixes for Nantes PDL
    for item in feed.keys():
        feed[item] = feed[item].drop(columns = [
            c for c in feed[item].columns if c.startswith("ext_")
        ])

    return feed

def write_feed(feed, path):
    print("Writing GTFS data to %s ..." % path)

    if path.endswith(".zip"):
        with zipfile.ZipFile(path, "w") as zip:
            for slot in REQUIRED_SLOTS + OPTIONAL_SLOTS:
                if slot in feed:
                    print("  Writing %s.txt ..." % slot)

                    # We cannot write directly to the file handle as it
                    # is binary, but pandas only writes in text mode.
                    zip.writestr("%s.txt" % slot, feed[slot].to_csv(index = None))

    else:
        if not os.path.exists(path):
            os.mkdir(path)

        if not os.path.isdir(path):
            raise RuntimeError("Should be a directory: %s" % path)

        for slot in REQUIRED_SLOTS + OPTIONAL_SLOTS:
            if slot in feed:
                with open("%s/%s.txt" % (path, slot), "w+", encoding="utf-8") as f:
                    print("  Writing %s.txt ..." % slot)
                    feed[slot].to_csv(f, index = None, lineterminator='\n')

# GTFS-Flex columns that carry a booking window instead of a fixed arrival/departure time.
FLEX_WINDOW_COLUMNS = ["start_pickup_drop_off_window", "end_pickup_drop_off_window"]

def filter_flex_trips(feed, stats = None):
    """Remove on-demand trips encoded as GTFS-Flex (booking windows in stop_times).

    A stop_times row with a value in any FLEX_WINDOW_COLUMNS column describes a booking
    window of an on-demand service (Rufbus, AST, ...), not a departure every agent could
    board, so it must not be imported as scheduled public transport.

    Port of upstream eqasim-france #569 with two deliberate deviations:
      - the WHOLE trip is removed as soon as one of its rows has a window (upstream drops
        only the rows, which can leave a mixed trip with a truncated stop sequence);
      - the removed trips and stop_times are counted and logged (no silent filter).

    Trips are cascaded to stop_times and frequencies. Routes are left in place so the
    name-based exclusion in filter_routes (issue #200) still sees its routes and stays a
    second safety net; drop_routes_without_trips removes the routes emptied here afterwards
    (cut_feed does not touch routes).

    The input feed is not mutated. A feed without window columns is returned unchanged.
    When a dict is passed as ``stats``, the counts are also written into it so the stage
    can persist them in its report.
    """
    df_times = feed["stop_times"]
    window_columns = [c for c in FLEX_WINDOW_COLUMNS if c in df_times.columns]

    if stats is not None:
        stats.update(window_columns = window_columns, trips_before = len(feed["trips"]),
                     trips_removed = 0, stop_times_before = len(df_times), stop_times_removed = 0,
                     window_rows = 0)

    if len(window_columns) == 0:
        print("[gtfs-flex-filter] no GTFS-Flex booking-window columns in stop_times; feed unchanged")
        return feed

    flex_row_mask = df_times[window_columns].notna().any(axis = 1)
    flex_trip_ids = set(df_times.loc[flex_row_mask, "trip_id"].astype(str))

    feed = copy_feed(feed)
    n_trips_before = len(feed["trips"])
    n_stop_times_before = len(df_times)

    keep_trips = ~feed["trips"]["trip_id"].astype(str).isin(flex_trip_ids)
    feed["trips"] = feed["trips"][keep_trips].copy()

    keep_times = ~df_times["trip_id"].astype(str).isin(flex_trip_ids)
    feed["stop_times"] = df_times[keep_times].copy()

    if "frequencies" in feed:
        df_freq = feed["frequencies"]
        feed["frequencies"] = df_freq[~df_freq["trip_id"].astype(str).isin(flex_trip_ids)].copy()

    if stats is not None:
        stats.update(trips_removed = int((~keep_trips).sum()), stop_times_removed = int((~keep_times).sum()),
                     window_rows = int(flex_row_mask.sum()))

    print("[gtfs-flex-filter] removed %d/%d trips and %d/%d stop_times with GTFS-Flex booking windows "
          "(%d stop_times rows carried a window)" % (
              int((~keep_trips).sum()), n_trips_before,
              int((~keep_times).sum()), n_stop_times_before,
              int(flex_row_mask.sum())))

    return feed

def drop_routes_without_trips(feed, stats = None):
    """Remove routes that no trip references any more (e.g. after filter_flex_trips).

    A route without trips would be written to routes.txt and counted as a line downstream.
    The count is logged and, when a dict is passed as ``stats``, recorded in it. The input
    feed is not mutated.
    """
    df_routes = feed["routes"]
    keep = df_routes["route_id"].astype(str).isin(set(feed["trips"]["route_id"].astype(str)))
    n_removed = int((~keep).sum())

    if stats is not None:
        stats.update(routes_before = len(df_routes), routes_removed = n_removed)

    print("[gtfs-route-cleanup] removed %d/%d routes without trips" % (n_removed, len(df_routes)))

    if n_removed == 0:
        return feed

    feed = copy_feed(feed)
    feed["routes"] = df_routes[keep].copy()
    return feed

def filter_routes(feed,
                  excluded_route_short_name_patterns = None,
                  excluded_agency_ids = None,
                  excluded_agency_name_patterns = None,
                  stats = None):
    """Remove configured routes (and their trips, stop_times, frequencies) from a feed.

    This drops demand-responsive / placeholder services that must NOT be imported as
    scheduled public transport, because they enumerate bookable connections rather
    than fixed departures every agent could board. The motivating case is "Flexo",
    the demand-responsive service of the Regionalverband Grossraum Braunschweig
    (ZGB), which appears in the GTFS feed as a single route rolled out as thousands
    of placeholder trips (see issue #200).

    Matching is additive: a route is removed if it matches ANY of the rules.
      - excluded_route_short_name_patterns: regular expressions matched
        (case-insensitive, substring via pandas.str.contains) against
        routes.route_short_name. Anchor with ^...$ for exact names (e.g. "^Flexo$").
      - excluded_agency_ids: agency_id values (compared as strings).
      - excluded_agency_name_patterns: regular expressions matched
        (case-insensitive) against agency.agency_name; expanded to their agency_ids.

    Following the no-silent-fallback rule, the number of routes matched by each rule
    and the total removed routes/trips/stop_times are logged explicitly. A configured
    pattern that matches nothing is logged as a WARNING so a mistyped pattern is
    visible instead of silently doing nothing.

    The input feed is not mutated; a filtered copy is returned. Returns the feed
    unchanged when no exclusion rules are configured. When a dict is passed as ``stats``,
    the per-rule matches and removed counts are also written into it for the stage report.
    """
    excluded_route_short_name_patterns = excluded_route_short_name_patterns or []
    excluded_agency_ids = [str(a) for a in (excluded_agency_ids or [])]
    excluded_agency_name_patterns = excluded_agency_name_patterns or []

    if stats is None:
        stats = {}
    stats.update(rule_matches = {}, routes_before = len(feed["routes"]), routes_removed = 0,
                 trips_before = len(feed["trips"]), trips_removed = 0, stop_times_removed = 0)

    if (not excluded_route_short_name_patterns and not excluded_agency_ids
            and not excluded_agency_name_patterns):
        return feed

    feed = copy_feed(feed)
    df_routes = feed["routes"]

    # Resolve agency-name patterns to concrete agency_ids via the agency table.
    if excluded_agency_name_patterns and "agency" in feed:
        agency_names = feed["agency"]["agency_name"].astype(str)
        name_mask = pd.Series(False, index = feed["agency"].index)
        for pattern in excluded_agency_name_patterns:
            hits = agency_names.str.contains(pattern, case = False, regex = True, na = False)
            print("[gtfs-filter] agency_name /%s/ matched %d agencies" % (pattern, int(hits.sum())))
            stats["rule_matches"]["agency_name /%s/ (agencies)" % pattern] = int(hits.sum())
            name_mask |= hits
        excluded_agency_ids = list(
            set(excluded_agency_ids)
            | set(feed["agency"].loc[name_mask, "agency_id"].astype(str))
        )

    route_short = (df_routes["route_short_name"].astype(str)
                   if "route_short_name" in df_routes
                   else pd.Series("", index = df_routes.index))
    route_agency = (df_routes["agency_id"].astype(str)
                    if "agency_id" in df_routes
                    else pd.Series("", index = df_routes.index))

    remove_mask = pd.Series(False, index = df_routes.index)

    for pattern in excluded_route_short_name_patterns:
        hits = route_short.str.contains(pattern, case = False, regex = True, na = False)
        print("[gtfs-filter] route_short_name /%s/ matched %d routes" % (pattern, int(hits.sum())))
        stats["rule_matches"]["route_short_name /%s/" % pattern] = int(hits.sum())
        remove_mask |= hits

    if excluded_agency_ids:
        hits = route_agency.isin(excluded_agency_ids)
        print("[gtfs-filter] agency_id in %s matched %d routes" % (excluded_agency_ids, int(hits.sum())))
        stats["rule_matches"]["agency_id in %s" % sorted(excluded_agency_ids)] = int(hits.sum())
        remove_mask |= hits

    removed_route_ids = set(df_routes.loc[remove_mask, "route_id"].astype(str))

    if len(removed_route_ids) == 0:
        print("[gtfs-filter] WARNING no routes matched the configured exclusion rules; feed unchanged")
        return feed

    # Cascade the removal: routes -> trips -> stop_times / frequencies.
    df_trips = feed["trips"]
    removed_trip_mask = df_trips["route_id"].astype(str).isin(removed_route_ids)
    removed_trip_ids = set(df_trips.loc[removed_trip_mask, "trip_id"].astype(str))

    n_routes_before = len(df_routes)
    n_trips_before = len(df_trips)

    feed["routes"] = df_routes[~df_routes["route_id"].astype(str).isin(removed_route_ids)].copy()
    feed["trips"] = df_trips[~removed_trip_mask].copy()

    n_stop_times_removed = 0
    if "stop_times" in feed:
        df_times = feed["stop_times"]
        keep = ~df_times["trip_id"].astype(str).isin(removed_trip_ids)
        n_stop_times_removed = int((~keep).sum())
        feed["stop_times"] = df_times[keep].copy()

    if "frequencies" in feed:
        df_freq = feed["frequencies"]
        feed["frequencies"] = df_freq[
            ~df_freq["trip_id"].astype(str).isin(removed_trip_ids)
        ].copy()

    stats.update(routes_removed = len(removed_route_ids), trips_removed = len(removed_trip_ids),
                 stop_times_removed = n_stop_times_removed)

    print("[gtfs-filter] removed %d/%d routes, %d/%d trips, %d stop_times "
          "(demand-responsive / excluded services)" % (
              len(removed_route_ids), n_routes_before,
              len(removed_trip_ids), n_trips_before,
              n_stop_times_removed))

    return feed

def cut_feed(feed, df_area, crs = None):
    feed = copy_feed(feed)

    df_stops = feed["stops"]

    # Feeds without a location_type column are valid GTFS (upstream eqasim-france #309)
    if "location_type" not in df_stops.columns or np.count_nonzero(df_stops["location_type"] == 1) == 0:
        print("Warning! Location types seem to be malformatted. Keeping all stops.")
        df_stations = df_stops.copy()
    else:
        df_stations = df_stops[df_stops["location_type"] == 1].copy()

    df_stations["geometry"] = [
        geo.Point(*xy)
        for xy in zip(df_stations["stop_lon"], df_stations["stop_lat"])
    ]

    df_stations = gpd.GeoDataFrame(df_stations, crs = "EPSG:4326")

    if not crs is None:
        print("Converting stops to custom CRS", crs)
        df_stations = df_stations.to_crs(crs)
    elif not df_area.crs is None:
        print("Converting stops to area CRS", df_area.crs)
        df_stations = df_stations.to_crs(df_area.crs)

    print("Filtering stations ...")
    initial_count = len(df_stations)

    df_stations = gpd.sjoin(df_stations, df_area, predicate = "within")
    final_count = len(df_stations)

    print("Found %d/%d stations inside the specified area" % (final_count, initial_count))
    inside_stations = df_stations["stop_id"]

    # 1) Remove stations that are not inside stations and not have a parent stop
    df_stops = feed["stops"]

    df_stops = df_stops[
        df_stops["parent_station"].isin(inside_stations) |
        (
            df_stops["parent_station"].isna() &
            df_stops["stop_id"].isin(inside_stations)
        )
    ]

    feed["stops"] = df_stops.copy()
    remaining_stops = feed["stops"]["stop_id"].unique()

    # 2) Remove stop times
    df_times = feed["stop_times"]
    df_times = df_times[df_times["stop_id"].astype(str).isin(remaining_stops.astype(str))]
    feed["stop_times"] = df_times.copy()

    # 3) Remove transfers
    if "transfers" in feed:
        df_transfers = feed["transfers"]
        df_transfers = df_transfers[
            df_transfers["from_stop_id"].isin(remaining_stops) &
            df_transfers["to_stop_id"].isin(remaining_stops)
        ]
        feed["transfers"] = df_transfers.copy()

    # 4) Remove pathways
    if "pathways" in feed:
        df_pathways = feed["pathways"]
        df_pathways = df_pathways[
            df_pathways["from_stop_id"].isin(remaining_stops) &
            df_pathways["to_stop_id"].isin(remaining_stops)
        ]
        feed["pathways"] = df_pathways.copy()

    # 5) Remove trips
    trip_counts = feed["stop_times"]["trip_id"].value_counts()
    remaining_trips = trip_counts[trip_counts > 1].index.values

    df_trips = feed["trips"]
    df_trips = df_trips[
        df_trips["trip_id"].isin(remaining_trips)
    ]
    feed["trips"] = df_trips.copy()

    feed["stop_times"] = feed["stop_times"][
        feed["stop_times"]["trip_id"].isin(df_trips["trip_id"].unique())
    ]

    # 6) Remove frequencies
    if "frequencies" in feed:
        df_frequencies = feed["frequencies"]
        df_frequencies = df_frequencies[
            df_frequencies["trip_id"].isin(remaining_trips)
        ]
        feed["frequencies"] = df_frequencies.copy()

    return feed

SLOT_COLLISIONS = [
    { "slot": "agency", "identifier": "agency_id", "references": [
        ("routes", "agency_id"), ("fare_attributes", "agency_id")] },
    { "slot": "stops", "identifier": "stop_id", "references": [
        ("stops", "parent_station"), ("stop_times", "stop_id"),
        ("transfers", "from_stop_id"), ("transfers", "to_stop_id"),
        ("pathways", "from_stop_id"), ("pathways", "to_stop_id")] },
    { "slot": "routes", "identifier": "route_id", "references": [
        ("trips", "route_id"), ("fare_rules", "route_id"),
        ("attributions", "route_id")] },
    { "slot": "trips", "identifier": "trip_id", "references": [
        ("stop_times", "trip_id"), ("frequencies", "trip_id"),
        ("attributions", "trip_id")] },
    { "slot": "calendar", "identifier": "service_id", "references": [
        ("calendar_dates", "service_id"), ("trips", "service_id")] },
    { "slot": "calendar_dates", "identifier": "service_id", "references": [
        ("trips", "service_id"), ("calendar", "service_id")] },
    { "slot": "fare_attributes", "identifier": "fare_id", "references": [
        ("fare_rules", "fare_id")] },
    { "slot": "shapes", "identifier": "shape_id", "references": [
        ("trips", "shape_id")] },
    { "slot": "pathways", "identifier": "pathway_id", "references": [] },
    { "slot": "levels", "identifier": "level_id", "references": [
        ("stops", "level_id")] },
    { "slot": "attributions", "identifier": "attribution_id" },
]

def copy_feed(feed):
    return {
        slot: feed[slot].copy() for slot in feed
    }

def merge_feeds(feeds, stats = None):
    """Merge feeds pairwise. When a dict is passed as ``stats``, the number of identifiers
    renamed per slot to resolve collisions is accumulated under ``renamed_identifiers`` and
    the identical rows dropped per slot under ``duplicate_rows_removed``."""
    result = {}

    for k, feed in enumerate(feeds):
        result = merge_two_feeds(result, feed, "_m{}".format(k + 1), stats = stats)

    return result

def merge_two_feeds(first, second, suffix = "_merged", stats = None):
    feed = {}

    print("Merging GTFS data ...")

    first = copy_feed(first)
    second = copy_feed(second)

    for collision in SLOT_COLLISIONS:
        if collision["slot"] in first and collision["slot"] in second:
            df_first = first[collision["slot"]]
            df_second = second[collision["slot"]]

            # The identifier column can be absent (e.g. attributions without the
            # optional attribution_id; upstream eqasim-france #428). No astype(str)
            # coercion: it turned genuine NaN into the string "nan" and broke
            # isna()-based logic downstream (upstream eqasim-france #447).
            if collision["identifier"] in df_first and collision["identifier"] in df_second:
                df_concat = pd.concat([df_first, df_second], sort = True).drop_duplicates()
                duplicate_ids = list(df_concat[df_concat[collision["identifier"]].duplicated()][collision["identifier"]].unique())

                if len(duplicate_ids) > 0:
                    print("   Found %d duplicate identifiers in %s" % (
                        len(duplicate_ids), collision["slot"]))

                    replacement_ids = [str(id) + suffix for id in duplicate_ids]

                    if stats is not None:
                        renamed = stats.setdefault("renamed_identifiers", {})
                        renamed[collision["slot"]] = renamed.get(collision["slot"], 0) + len(duplicate_ids)

                    df_second[collision["identifier"]] = df_second[collision["identifier"]].replace(
                        duplicate_ids, replacement_ids
                    )

                    # Rewriting references only requires the second feed to carry the
                    # referencing slot (upstream eqasim-france #521).
                    for ref_slot, ref_identifier in collision["references"]:
                        if ref_slot in second:
                            second[ref_slot][ref_identifier] = second[ref_slot][ref_identifier].replace(
                                duplicate_ids, replacement_ids
                            )

    for slot in REQUIRED_SLOTS + OPTIONAL_SLOTS:
        if slot in first and slot in second:
            df_concat = pd.concat([first[slot], second[slot]], sort = True)
            feed[slot] = df_concat.drop_duplicates()

            if stats is not None:
                duplicates = stats.setdefault("duplicate_rows_removed", {})
                duplicates[slot] = duplicates.get(slot, 0) + len(df_concat) - len(feed[slot])
        elif slot in first:
            feed[slot] = first[slot].copy()
        elif slot in second:
            feed[slot] = second[slot].copy()

    return feed

def despace_stop_ids(feed, replacement = ":::", stats = None):
    """Replace spaces in stop ids (MATSim rejects them). When a dict is passed as ``stats``,
    the number of replaced stop ids is recorded under ``stop_ids_replaced``."""
    feed = copy_feed(feed)

    references = None

    for item in SLOT_COLLISIONS:
        if item["slot"] == "stops":
            references = item["references"]

    df_stops = feed["stops"]
    df_stops["stop_id"] = df_stops["stop_id"].astype(str)

    search_ids = list(df_stops[df_stops["stop_id"].str.contains(" ")]["stop_id"].unique())
    replacement_ids = [item.replace(" ", replacement) for item in search_ids]

    df_stops["stop_id"] = df_stops["stop_id"].replace(search_ids, replacement_ids)

    for reference_slot, reference_field in references:
        if reference_slot in feed:
            feed[reference_slot][reference_field] = feed[reference_slot][reference_field].astype(str).replace(search_ids, replacement_ids)

    if stats is not None:
        stats.update(stops = len(df_stops), stop_ids_replaced = len(search_ids))

    print("De-spaced %d/%d stops" % (len(search_ids), len(df_stops)))

    return feed
