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
- the tool is run ONCE PER CATEGORY (``--geographicalTripType INTERNAL/...``)
  so downstream stages keep consuming one plans file per category. (The
  matsim 2026 build additionally tags persons with the
  ``geographical_Trip_Type`` attribute, which ``trips.py`` reads when
  present; the per-category runs remain the canonical partition and keep
  the stage outputs and caching unchanged.)
- output is one 100% plans file per category with leg mode ``truck`` and
  subpopulation ``freight`` (passed explicitly; the matsim 2026 build
  defaults to ``longDistanceFreight``) -- sampling to the pipeline rate
  happens later.
  This stage is sampling-rate independent and therefore cached across
  sampling-rate changes (~4 x 45 min one-time routing cost).

CRS: ``freight_crs`` (default EPSG:25832, the v3 network CRS).
"""
import gzip
import logging
import os
import xml.etree.ElementTree as ET

import geopandas as gpd

import matsim.runtime.eqasim as eqasim
import matsim.runtime.java as java

logger = logging.getLogger(__name__)

# The four geographic trip categories of the published extraction (partition of
# the ZGB-relevant trips). Lowercase keys are our canonical labels; the tool's
# --geographicalTripType option takes them uppercase.
TRIP_CATEGORIES = ("internal", "incoming", "outgoing", "transit")
OUTPUT_TEMPLATE = "zgb_freight.%s.100pct.plans.xml.gz"
SUMMARY_NAME = "freight_extraction_summary.csv"


def configure(context):
    context.stage("braunschweig.data.freight.german_wide")
    context.stage("data.spatial.municipalities")
    context.stage("matsim.runtime.java")
    context.stage("matsim.runtime.eqasim")

    # synpp scopes config per stage (issue #229): execute() calls eqasim.run() ->
    # java.run(), which reads the java binary/memory options AND the hang-watchdog
    # options (#330) from THIS stage's context. Delegate to java's own configure()
    # so the declares cannot drift from what java.run actually reads. Required
    # rather than optional for the watchdog keys: they are volatile, and synpp does
    # not propagate volatile options to downstream stages.
    java.configure(context)

    context.config("cordon_enabled", False)
    context.config("cordon_network_buffer_fraction", 0.10)
    context.config("freight_crs", "EPSG:25832")


def count_persons(plans_path):
    """Count persons in a (gzipped) MATSim plans file via a streaming parse."""
    persons = 0
    with gzip.open(plans_path, "rb") as f:
        for _event, element in ET.iterparse(f, events=("end",)):
            if element.tag == "person":
                persons += 1
            element.clear()
    return persons


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

    # The Java tool runs with cwd = the stage cache dir (java.run default), so the
    # input plans/network must be ABSOLUTE paths (the data stage returns them
    # relative to the repo root). The OUTPUT must also carry a directory
    # component: the tool calls output.getParent() and NPEs on a bare filename
    # (ExtractRelevantFreightTrips.java:217, observed on the real run).
    # study_area.shp stays relative because it lives in the cwd.
    plans_path = os.path.abspath(paths["plans_path"])
    network_path = os.path.abspath(paths["network_path"])

    outputs = {}
    counts = {}
    for category in TRIP_CATEGORIES:
        output_name = OUTPUT_TEMPLATE % category
        output_path = os.path.abspath("%s/%s" % (context.path(), output_name))

        eqasim.run(context, "org.eqasim.braunschweig.scenario.RunExtractFreightTrips", [
            plans_path,
            "--network", network_path,
            "--shp", "study_area.shp",
            "--shp-crs", crs,
            "--input-crs", crs,
            "--target-crs", crs,
            "--cut-on-boundary",
            # NOTE: option names of the matsim 2026 contrib build (verified
            # against the tool's --help): --legMode (was --LegMode) and
            # --geographicalTripType (was --tripType, same enum values).
            # --subpopulation must be passed explicitly: the tool now defaults
            # to "longDistanceFreight", while the whole downstream pipeline
            # (merge, replanning config, freight_filter, analysis) keys on
            # subpopulation "freight".
            "--legMode", "truck",
            "--geographicalTripType", category.upper(),
            "--subpopulation", "freight",
            "--output", output_path,
        ])

        assert os.path.exists(output_path), \
            "freight extraction did not write %s" % output_name

        counts[category] = count_persons(output_path)
        outputs[category] = output_name
        logger.info("[freight.extraction] category %s: %d trips", category, counts[category])

    total = sum(counts.values())
    if total == 0:
        raise RuntimeError(
            "freight extraction produced 0 trips across all categories -- "
            "study area / network / plans inputs are inconsistent")
    if counts["transit"] == 0:
        # A2 and A39 cross ZGB; zero through-traffic is implausible and almost
        # certainly signals a broken run (no-silent-fallback rule).
        logger.warning("[freight.extraction] 0 TRANSIT trips -- implausible for ZGB "
                       "(A2/A39); inspect the study area and tool output")
    logger.info("[freight.extraction] %d ZGB-relevant freight trips: %s", total, counts)

    with open("%s/%s" % (context.path(), SUMMARY_NAME), "w", encoding="utf-8") as f:
        f.write("trip_type;count\n")
        for category in TRIP_CATEGORIES:
            f.write("%s;%d\n" % (category, counts[category]))

    return outputs
