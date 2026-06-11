"""Extract the ZGB-relevant subset of the german-wide-freight v3 plans.

Runs the published matsim application-contrib extraction
(``ExtractRelevantFreightTrips``, Lu et al. 2022, via our
``RunExtractFreightTrips`` wrapper) against the study-area polygon:

- study area = dissolved in-scope municipalities, PLUS the cordon network
  buffer when ``cordon_enabled`` (the trimmed trip endpoints must lie where
  the simulation network exists);
- the tool routes every freight trip on the Germany-Europe network,
  classifies it (INTERNAL / INCOMING / OUTGOING / TRANSIT), trims routes at
  the boundary and shifts departure times by the access travel time;
- output is a 100% plans file with leg mode ``truck`` and subpopulation
  ``freight`` -- sampling to the pipeline rate happens later. This stage is
  sampling-rate independent and therefore cached across sampling-rate changes.

CRS: ``freight_crs`` (default EPSG:25832, the v3 network CRS).
"""
import gzip
import logging
import os
import xml.etree.ElementTree as ET

import geopandas as gpd

import matsim.runtime.eqasim as eqasim

logger = logging.getLogger(__name__)

OUTPUT_NAME = "zgb_freight.100pct.plans.xml.gz"
SUMMARY_NAME = "freight_extraction_summary.csv"
TRIP_TYPE_ATTRIBUTES = ("trip_type", "geographical_Trip_Type")


def configure(context):
    context.stage("braunschweig.data.freight.german_wide")
    context.stage("data.spatial.municipalities")
    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.eqasim")

    context.config("cordon_enabled", False)
    context.config("cordon_network_buffer_fraction", 0.10)
    context.config("freight_crs", "EPSG:25832")


def count_trip_types(plans_path):
    """Count persons per trip-type attribute in a (gzipped) MATSim plans file.

    Streaming parse, so the 100% file never sits in memory. Raises when the
    file contains no persons (extraction failure must not pass silently);
    persons without a recognised trip-type attribute are counted as
    ``unknown`` and reported (fallback-transparency rule).
    """
    counts = {}
    persons = 0
    with gzip.open(plans_path, "rb") as f:
        for _event, element in ET.iterparse(f, events=("end",)):
            if element.tag != "person":
                continue
            persons += 1
            trip_type = None
            for attribute in element.iter("attribute"):
                if attribute.get("name") in TRIP_TYPE_ATTRIBUTES:
                    trip_type = attribute.text
                    break
            counts[trip_type or "unknown"] = counts.get(trip_type or "unknown", 0) + 1
            element.clear()
    if persons == 0:
        raise RuntimeError("extracted freight plans contain no persons: %s" % plans_path)
    if counts.get("unknown", 0) > 0:
        logger.warning(
            "[freight.extraction] %d/%d persons carry no recognised trip-type attribute %s",
            counts["unknown"], persons, TRIP_TYPE_ATTRIBUTES)
    return persons, counts


def _study_area(context):
    """Dissolved municipalities, buffered like the cordon network when enabled."""
    from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction

    df_municipalities = context.stage("data.spatial.municipalities")
    if context.config("cordon_enabled"):
        buffer_m = buffer_m_from_fraction(
            df_municipalities, float(context.config("cordon_network_buffer_fraction")))
    else:
        buffer_m = 0.0
    polygon = build_cordon_polygon(df_municipalities, buffer_m)
    return gpd.GeoDataFrame({"id": [1]}, geometry=[polygon], crs=df_municipalities.crs)


def execute(context):
    paths = context.stage("braunschweig.data.freight.german_wide")
    crs = context.config("freight_crs")

    study_area = _study_area(context)
    study_area.to_file("%s/study_area.shp" % context.path())

    eqasim.run(context, "org.eqasim.braunschweig.scenario.RunExtractFreightTrips", [
        paths["plans_path"],
        "--network", paths["network_path"],
        "--shp", "study_area.shp",
        "--shp-crs", crs,
        "--input-crs", crs,
        "--target-crs", crs,
        "--cut-on-boundary",
        "--legMode", "truck",
        "--subpopulation", "freight",
        "--output", OUTPUT_NAME,
    ])

    output_path = "%s/%s" % (context.path(), OUTPUT_NAME)
    assert os.path.exists(output_path), "freight extraction did not write %s" % OUTPUT_NAME

    persons, counts = count_trip_types(output_path)
    logger.info("[freight.extraction] %d ZGB-relevant freight agents: %s", persons, counts)

    with open("%s/%s" % (context.path(), SUMMARY_NAME), "w", encoding="utf-8") as f:
        f.write("trip_type;count\n")
        for trip_type in sorted(counts):
            f.write("%s;%d\n" % (trip_type, counts[trip_type]))

    return OUTPUT_NAME
