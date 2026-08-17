import data.gtfs.utils as gtfs
import os, pathlib

"""
This file reads GTFS schedules, cuts them to the scenario area (defined by the
selected regions and departments) and merges them together.
"""

def configure(context):
    context.config("data_path")
    context.config("gtfs_path", "gtfs_idf")

    # Cross-cordon: cut the (wider, Germany-wide) GTFS to the ENLARGED extent so the
    # transit schedule includes the regional lines + external stops that PT
    # in-commuters board (mirrors the OSM network enlargement). Default off -> ZGB cut.
    context.config("cordon_enabled", False)
    context.config("cordon_network_source_buffer_m", 0.0)

    # Demand-responsive / placeholder service exclusion (issue #200). "Flexo", the
    # demand-responsive service of the Regionalverband Grossraum Braunschweig, appears
    # in the ZGB GTFS feed as a single route rolled out as thousands of placeholder
    # trips. Imported unchanged it would overstate scheduled PT supply, so it is
    # dropped at preprocessing. The exclusion is data-driven and configurable so other
    # feeds carrying similar on-demand placeholders (e.g. AST / Rufbus elsewhere) can be
    # handled without code changes. Patterns are case-insensitive regular expressions;
    # anchor with ^...$ for an exact route_short_name match. Defaults exclude Flexo only.
    context.config("gtfs_excluded_route_short_name_patterns", ["^Flexo$"])
    context.config("gtfs_excluded_agency_ids", [])
    context.config("gtfs_excluded_agency_name_patterns", [])

    context.stage("data.spatial.municipalities")

def execute(context):
    input_files = get_input_files("{}/{}".format(context.config("data_path"), context.config("gtfs_path")))

    # Prepare bounding area. With the cross-cordon feature on, enlarge the cut area by
    # the network source buffer so regional lines reaching ZGB from the surrounding
    # Kreise stay in the schedule (real PT entry corridors, no road-gate fallback).
    df_area = context.stage("data.spatial.municipalities")
    if context.config("cordon_enabled") and context.config("cordon_network_source_buffer_m"):
        from braunschweig.data.cordon.network_clip import osm_clip_geometry
        df_area = osm_clip_geometry(
            df_area, context.config("cordon_enabled"),
            context.config("cordon_network_source_buffer_m"))

    # Route exclusion rules (demand-responsive / placeholder services, issue #200)
    excluded_route_patterns = context.config("gtfs_excluded_route_short_name_patterns")
    excluded_agency_ids = context.config("gtfs_excluded_agency_ids")
    excluded_agency_name_patterns = context.config("gtfs_excluded_agency_name_patterns")

    # Load and cut feeds
    feeds = []
    for path in input_files:
        feed = gtfs.read_feed(path)

        # Drop demand-responsive / placeholder services (e.g. ZGB "Flexo") so they are
        # not imported as scheduled PT. Applied before the spatial cut so the logged
        # match counts reflect the full feed rather than only the in-area remainder.
        feed = gtfs.filter_routes(
            feed,
            excluded_route_short_name_patterns = excluded_route_patterns,
            excluded_agency_ids = excluded_agency_ids,
            excluded_agency_name_patterns = excluded_agency_name_patterns)

        feed = gtfs.cut_feed(feed, df_area)

        # This was fixed in pt2matsim, so we can remove one a new release (> 20.7) is available.
        feed = gtfs.despace_stop_ids(feed) # Necessary as MATSim does not like stops/links with spaces

        feeds.append(feed)

    # Merge feeds
    merged_feed = gtfs.merge_feeds(feeds) if len(feeds) > 1 else feeds[0]

    # Fix for pt2matsim (will be fixed after PR #173)
    # Order of week days must be fixed
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    columns = list(merged_feed["calendar"].columns)
    for day in days: columns.remove(day)
    columns += ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    merged_feed["calendar"] = merged_feed["calendar"][columns]

    # Write feed (not as a ZIP, but as files, for pt2matsim)
    gtfs.write_feed(merged_feed, "%s/output" % context.path())

    return "gtfs"

def get_input_files(base_path):
    gtfs_paths = [
        str(child)
        for child in pathlib.Path(base_path).glob("*")
        if child.suffix.lower() == ".zip"
    ]

    if len(gtfs_paths) == 0:
        raise RuntimeError("Did not find any GTFS data (.zip) in {}".format(base_path))
    
    return gtfs_paths

def validate(context):
    input_files = get_input_files("{}/{}".format(context.config("data_path"), context.config("gtfs_path")))
    total_size = 0

    for path in input_files:
        total_size += os.path.getsize(path)

    return total_size
