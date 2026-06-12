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
import gzip
import logging
import xml.etree.ElementTree as ET

import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

logger = logging.getLogger(__name__)

OUTPUT_GPKG = "freight_trips.gpkg"
TRIP_COLUMNS = (
    "person_id", "origin_x", "origin_y",
    "destination_x", "destination_y", "departure_time", "trip_type",
)
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

    Each freight person has exactly two activities (start/end) and one leg. The
    start activity carries the departure ``end_time``. The trip category comes
    from a person attribute when present (newer tool versions); for the
    per-category extraction outputs (matsim 2025.0-PR3568 writes no category
    attribute) the caller passes ``default_trip_type`` -- the category the file
    was extracted with. Without either, rows are labelled ``unknown`` and a
    warning is emitted (fallback-transparency rule). Raises on an empty file
    unless ``allow_empty`` (a single extraction category may legitimately be
    empty; the caller decides).
    """
    records = []
    opener = gzip.open if str(plans_path).endswith(".gz") else open
    with opener(plans_path, "rb") as f:
        for _event, element in ET.iterparse(f, events=("end",)):
            if element.tag != "person":
                continue
            activities = element.findall(".//activity")
            if len(activities) >= 2:
                start, end = activities[0], activities[-1]
                trip_type = None
                attributes = element.find("attributes")
                if attributes is not None:
                    for attribute in attributes.findall("attribute"):
                        if attribute.get("name") in TRIP_TYPE_ATTRIBUTES:
                            trip_type = attribute.text
                            break
                end_time = start.get("end_time")
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
    if not records and not allow_empty:
        raise RuntimeError("no freight trips parsed from %s" % plans_path)
    df = pd.DataFrame.from_records(records, columns=list(TRIP_COLUMNS))
    unknown = int((df["trip_type"] == "unknown").sum())
    if unknown > 0:
        logger.warning("[freight.trips] %d/%d trips have no trip_type attribute",
                       unknown, len(df))
    return df


def configure(context):
    context.stage("braunschweig.freight.extraction")


def execute(context):
    # The extraction stage runs the tool once per category and returns
    # {category: plans_filename}. Each per-category run renumbers persons from
    # freight_0, so the ids collide across files -- rewrite them to
    # freight_<category>_<n> (unique AND self-documenting in every downstream
    # table, events file and analysis).
    outputs = context.stage("braunschweig.freight.extraction")
    extraction_path = context.path("braunschweig.freight.extraction")

    frames = []
    for category, plans_name in outputs.items():
        df_category = parse_freight_trips(
            "%s/%s" % (extraction_path, plans_name),
            default_trip_type=category, allow_empty=True)
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
    gdf = gpd.GeoDataFrame(df.copy(), geometry=geometry, crs=FREIGHT_CRS)
    gdf.to_file("%s/%s" % (context.path(), OUTPUT_GPKG), driver="GPKG")

    return df
