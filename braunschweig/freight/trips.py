"""Read the ZGB-relevant freight plans into a tidy trips table + gpkg.

Consumes the extraction stage output (Task 4) -- a MATSim plans file where each
person is one long-haul freight trip (activities ``freight_start`` ->
``freight_end`` + a ``truck`` leg). Emits:

- a tidy DataFrame with one row per trip (the injection-hook input), and
- ``freight_trips.gpkg`` -- an inspectable GeoDataFrame of the origin points in
  EPSG:25832 (the v3 CRS), so the freight demand is traceable inside eqasim.

The plans are parsed with a streaming ``xml.etree`` pass rather than the
``matsim`` Python package (matsim-tools): in the pipeline run environment the
repo-local ``matsim`` package (eqasim's ``matsim.runtime`` etc.) occupies the
``matsim`` namespace when run from the repo root, which SHADOWS the pip
``matsim-tools`` -- so ``matsim.plan_reader`` is not importable there (verified).
The ET parse is equally simple, dependency-free, and robust to that collision.

Sampling-rate independent: this is the full ZGB-relevant set; the injection hook
samples it per run.
"""
import csv
import gzip
import logging
import os
import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from braunschweig.freight import extraction

logger = logging.getLogger(__name__)

OUTPUT_GPKG = "freight_trips.gpkg"
TRIP_COLUMNS = (
    "person_id", "origin_x", "origin_y",
    "destination_x", "destination_y", "departure_time", "trip_type",
)
# Default CRS, used only when the caller does not supply ``freight_crs`` via
# ``context.config`` (the synpp stage always does; kept as a fallback default
# for direct/legacy callers of ``execute``-adjacent helpers).
FREIGHT_CRS = "EPSG:25832"
# The extraction tool tags each person with its category under this attribute
# name (matsim application contrib ExtractRelevantFreightTrips: constant
# GEOGRAPHICAL_TRIP_TYPE); older variants used "trip_type". Both are accepted.
TRIP_TYPE_ATTRIBUTES = ("trip_type", "geographical_Trip_Type")


def _seconds_of_day(clock):
    """Parse a MATSim activity end_time into seconds.

    MATSim writes times as an 'HH:MM:SS' (24h+) clock string; accept a plain
    float seconds value too, in case a writer emits the raw double.
    """
    text = str(clock)
    if ":" in text:
        hours, minutes, seconds = (int(part) for part in text.split(":"))
        return hours * 3600 + minutes * 60 + seconds
    return int(float(text))


def parse_freight_trips(plans_path, default_trip_type=None, allow_empty=False):
    """Stream the plans file into a tidy one-row-per-trip DataFrame.

    Each freight person is EXACTLY two activities (start/end) and one leg. The
    start activity carries the departure ``end_time``. The trip category comes
    from a person attribute when present (the matsim 2026 build writes
    ``geographical_Trip_Type``; the older 2025.0-PR3568 build wrote no category
    attribute); in either case the caller passes ``default_trip_type`` -- the
    category the file was extracted with. Without either, rows are labelled ``unknown`` and a
    warning is emitted (fallback-transparency rule). Raises on an empty file
    unless ``allow_empty`` (a single extraction category may legitimately be
    empty; the caller decides).

    Fallback transparency (CLAUDE.md "no silent fallbacks"):

    - a person with FEWER than 2 activities cannot yield an origin/destination
      pair and is skipped entirely; skipped persons are counted and logged as a
      WARNING if any occur (this should never happen for a well-formed
      extraction output, so a nonzero count signals a broken upstream tool run);
    - a person with MORE than 2 activities (unexpected -- the extraction tool is
      documented to emit exactly start/end) is still processed by picking the
      FIRST and LAST activity (selection logic unchanged), but is counted
      separately and logged as a WARNING so an unexpected multi-activity plan is
      visible rather than silently truncated;
    - a start activity with a missing/empty ``end_time`` falls back to departure
      time 0; such rows are counted and logged as a WARNING (mirroring the
      existing ``trip_type``-unknown pattern below).
    """
    records = []
    n_too_few_activities = 0
    n_too_many_activities = 0
    n_missing_end_time = 0
    opener = gzip.open if str(plans_path).endswith(".gz") else open
    with opener(plans_path, "rb") as f:
        for _event, element in ET.iterparse(f, events=("end",)):
            if element.tag != "person":
                continue
            activities = element.findall(".//activity")
            if len(activities) < 2:
                n_too_few_activities += 1
                element.clear()
                continue
            if len(activities) > 2:
                n_too_many_activities += 1
            start, end = activities[0], activities[-1]
            trip_type = None
            attributes = element.find("attributes")
            if attributes is not None:
                for attribute in attributes.findall("attribute"):
                    if attribute.get("name") in TRIP_TYPE_ATTRIBUTES:
                        trip_type = attribute.text
                        break
            end_time = start.get("end_time")
            if not end_time:
                n_missing_end_time += 1
            records.append({
                "person_id": element.get("id"),
                "origin_x": float(start.get("x")),
                "origin_y": float(start.get("y")),
                "destination_x": float(end.get("x")),
                "destination_y": float(end.get("y")),
                "departure_time": _seconds_of_day(end_time) if end_time else 0,
                "trip_type": trip_type or default_trip_type or "unknown",
            })
            element.clear()

    if n_too_few_activities > 0:
        logger.warning(
            "[freight.trips] %d person(s) in %s have fewer than 2 activities "
            "(docstring expects exactly 2) and were skipped entirely",
            n_too_few_activities, plans_path,
        )
    if n_too_many_activities > 0:
        logger.warning(
            "[freight.trips] %d person(s) in %s have more than 2 activities "
            "(docstring expects exactly 2); the FIRST and LAST activity were "
            "used as origin/destination",
            n_too_many_activities, plans_path,
        )

    if not records and not allow_empty:
        raise RuntimeError("no freight trips parsed from %s" % plans_path)
    df = pd.DataFrame.from_records(records, columns=list(TRIP_COLUMNS))

    if n_missing_end_time > 0:
        logger.warning(
            "[freight.trips] %d/%d trips have a missing/empty start end_time "
            "and were assigned departure_time=0", n_missing_end_time, len(df))

    unknown = int((df["trip_type"] == "unknown").sum())
    if unknown > 0:
        logger.warning("[freight.trips] %d/%d trips have no trip_type attribute",
                       unknown, len(df))
    return df


def configure(context):
    context.stage("braunschweig.freight.extraction")
    # Read via context.config so a config override (e.g. a differently-projected
    # network) is honoured; mirrors braunschweig.freight.extraction.configure,
    # which declares the same option with the same default. FREIGHT_CRS is kept
    # as the module-level default value only (not read directly here) so the two
    # stages cannot silently diverge.
    context.config("freight_crs", FREIGHT_CRS)


def _log_extraction_count_cross_check(extraction_path, category, n_realized):
    """Best-effort info log comparing the realized trip count to the extraction
    stage's own per-category count (``freight_extraction_summary.csv``), when
    that summary is present in the extraction stage's cache directory. This is
    an observational cross-check, not a fallback -- the summary may be absent
    for an older cached extraction run, in which case the check is skipped
    silently (nothing to compare against).
    """
    # Share the file name constant with the producer so a rename cannot silently
    # break this cross-check on one side only.
    summary_path = os.path.join(extraction_path, extraction.SUMMARY_NAME)
    if not os.path.exists(summary_path):
        return
    with open(summary_path, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        expected = {row["trip_type"]: int(row["count"]) for row in reader}
    if category in expected:
        n_expected = expected[category]
        logger.info(
            "[freight.trips] category %s: %d trips parsed vs %d reported by "
            "the extraction stage%s",
            category, n_realized, n_expected,
            "" if n_realized == n_expected else " (MISMATCH)",
        )


def execute(context):
    # The extraction stage runs the tool once per category and returns
    # {category: plans_filename}. Each per-category run renumbers persons from
    # freight_0, so the ids collide across files -- rewrite them to
    # freight_<category>_<n> (unique AND self-documenting in every downstream
    # table, events file and analysis).
    outputs = context.stage("braunschweig.freight.extraction")
    extraction_path = context.path("braunschweig.freight.extraction")
    freight_crs = context.config("freight_crs")

    frames = []
    for category, plans_name in outputs.items():
        df_category = parse_freight_trips(
            "%s/%s" % (extraction_path, plans_name),
            default_trip_type=category, allow_empty=True)
        _log_extraction_count_cross_check(extraction_path, category, len(df_category))
        if len(df_category) == 0:
            logger.warning("[freight.trips] category %s is empty", category)
            continue
        df_category["person_id"] = [
            "freight_%s_%s" % (category, person_id.removeprefix("freight_"))
            for person_id in df_category["person_id"]
        ]
        frames.append(df_category)

    if not frames:
        raise RuntimeError("no freight trips parsed from any extraction category")
    df = pd.concat(frames, ignore_index=True)

    if df["person_id"].duplicated().any():
        raise RuntimeError("duplicate freight person ids after category merge")
    logger.info("[freight.trips] %d ZGB-relevant freight trips; by type: %s",
                len(df), df["trip_type"].value_counts().to_dict())

    geometry = [Point(xy) for xy in zip(df["origin_x"], df["origin_y"])]
    gdf = gpd.GeoDataFrame(df.copy(), geometry=geometry, crs=freight_crs)
    gdf.to_file("%s/%s" % (context.path(), OUTPUT_GPKG), driver="GPKG")

    return df
