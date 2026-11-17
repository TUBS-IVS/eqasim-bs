import data.gtfs.utils as gtfs
import json, os, pathlib

from braunschweig import synpp_tokens

# Helpers whose code this stage runs: synpp hashes only this module's source, so their
# source goes into the stage token (audit C2, ADR-0136). ``network_clip`` is imported inside
# ``execute`` and ``spatial.cordon`` is reached through it, so both are hashed by name.
_HELPER_MODULES = (gtfs,)

# Stage report (written into context.path()): per feed, the counts of every step that drops,
# filters or replaces records, plus the identifier renames of the merge.
REPORT_FILE_NAME = "gtfs_cleaned_report.json"

_DEFERRED_HELPER_MODULE_NAMES = (
    "braunschweig.data.cordon.network_clip",
    "braunschweig.data.spatial.cordon",
)

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
    # A key set to null disables its rule, like an empty list (filter_routes accepts None).
    excluded_route_patterns = list(context.config("gtfs_excluded_route_short_name_patterns") or [])
    excluded_agency_ids = list(context.config("gtfs_excluded_agency_ids") or [])
    excluded_agency_name_patterns = list(context.config("gtfs_excluded_agency_name_patterns") or [])

    # Counts of every step that drops, filters or replaces records, persisted next to the
    # output so each run records what was removed (not only the log).
    report = dict(
        excluded_route_short_name_patterns = excluded_route_patterns,
        excluded_agency_ids = excluded_agency_ids,
        excluded_agency_name_patterns = excluded_agency_name_patterns,
        feeds = [])

    # Load and cut feeds
    feeds = []
    for path in input_files:
        feed_report = dict(feed = os.path.basename(path), read = {}, gtfs_flex_filter = {},
                           route_filter = {}, routes_without_trips = {}, spatial_cut = {}, despace = {})
        report["feeds"].append(feed_report)

        feed = gtfs.read_feed(path, stats = feed_report["read"])

        # Drop on-demand trips encoded as GTFS-Flex (booking windows in stop_times,
        # upstream eqasim-france #569). Runs first so on-demand services the name-based
        # rule below does not list are caught too; that rule stays as a second net.
        feed = gtfs.filter_flex_trips(feed, stats = feed_report["gtfs_flex_filter"])

        # Drop demand-responsive / placeholder services (e.g. ZGB "Flexo") so they are
        # not imported as scheduled PT. Applied before the spatial cut so the logged
        # match counts reflect the full feed rather than only the in-area remainder.
        feed = gtfs.filter_routes(
            feed,
            excluded_route_short_name_patterns = excluded_route_patterns,
            excluded_agency_ids = excluded_agency_ids,
            excluded_agency_name_patterns = excluded_agency_name_patterns,
            stats = feed_report["route_filter"])

        # Routes whose trips were all removed above would stay in routes.txt with no trips
        # (cut_feed does not touch routes).
        feed = gtfs.drop_routes_without_trips(feed, stats = feed_report["routes_without_trips"])

        rows_before_cut = {slot: len(feed[slot]) for slot in feed}
        feed = gtfs.cut_feed(feed, df_area)
        feed_report["spatial_cut"] = {
            slot: dict(before = count, after = len(feed[slot]) if slot in feed else 0)
            for slot, count in rows_before_cut.items()}

        # This was fixed in pt2matsim, so we can remove one a new release (> 20.7) is available.
        # Necessary as MATSim does not like stops/links with spaces
        feed = gtfs.despace_stop_ids(feed, stats = feed_report["despace"])

        feeds.append(feed)

    # Merge feeds
    merge_stats = dict(renamed_identifiers = {}, duplicate_rows_removed = {})
    merged_feed = gtfs.merge_feeds(feeds, stats = merge_stats) if len(feeds) > 1 else feeds[0]
    report["merge"] = merge_stats

    # Fix for pt2matsim (will be fixed after PR #173)
    # Order of week days must be fixed
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    columns = list(merged_feed["calendar"].columns)
    for day in days: columns.remove(day)
    columns += ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    merged_feed["calendar"] = merged_feed["calendar"][columns]

    # Write feed (not as a ZIP, but as files, for pt2matsim)
    gtfs.write_feed(merged_feed, "%s/output" % context.path())

    with open(os.path.join(context.path(), REPORT_FILE_NAME), "w", encoding = "utf-8") as handle:
        json.dump(report, handle, indent = 2)

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

    return {
        "gtfs_input_size": total_size,
        "helpers": synpp_tokens.source_token(
            _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
            stage_label=__name__ + ".validate()"),
    }
