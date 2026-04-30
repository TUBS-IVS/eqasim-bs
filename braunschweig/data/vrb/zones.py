"""VRB (Verkehrsverbund Region Braunschweig) tariff zones.

Braunschweig analogue of ``eqasim_common.data.mvg.zones`` (Munich MVG).
Produces the *exact* same output schema so the downstream Java class
``org.eqasim.bavaria.scenario.AddTransitZoneInformation`` (kept under
its bavaria namespace per Decision D-1c) and the MATSim mode-choice /
fare module consume it without changes:

    GeoDataFrame[zone: str, geometry: Polygon]  --  EPSG:25832

Algorithm (mirrored from MVG):

1. Read ``vrb/stations.json`` (list of station dicts with the same keys
   as the MVG REST export: ``name``, ``latitude``, ``longitude`` and
   ``tariffZones``; the tariff string is split on ``|`` and ``/``).
2. Build one Shapely ``Point`` per station, group by tariff zone into
   ``MultiPoint``, reproject to EPSG:25832 and buffer by 400 m. The
   buffer width matches the MVG stage so the resulting polygons have
   the same characteristic walk-shed semantics.
3. **Concentric-ring remap (Braunschweig-specific).** Raw VRB Waben IDs
   are non-ordinal and span 10..92, so the Bavaria Java cost model
   (``BavariaPtCostModel``) -- which uses ``|zoneA-zoneB|`` to index
   ``prices = [3.9, 3.9, 5.8, 7.7, 9.7, 11.6, ...]`` and clamps to
   ``stictMaximumZone=12`` -- would collapse every PT trip to a flat
   3.90 EUR. We therefore replace each VRB zone polygon's ``zone``
   attribute with a concentric ring index (1..7) computed from the
   geodesic distance of the polygon's centroid to Braunschweig Hbf.
   Bucket boundaries are calibrated against the VRB tariff flyer
   (page 8, ``data/braunschweig/vrb-tarifflyer.pdf``, Preisliste ab
   01.01.2026) so that the existing Bavaria ``prices`` array
   approximately reproduces the VRB Preisstufen 1..4. The *fare model*
   itself is left untouched -- this is a zone-id remap only.

The input JSON is *not* an authoritative VRB delivery -- it is built
from GTFS stops + VRB Waben polygons by ``scripts/build_vrb_stations_json.py``.
See ``eqasim-data/DOWNLOAD_CHECKLIST_BS.md`` (entry D3) for the data
requirements.
"""

import os
import json
import re

import geopandas as gpd
import pandas as pd
import shapely.geometry as sgeo


# --- Braunschweig-specific: concentric-ring remap ---------------------------
# Braunschweig Hauptbahnhof in EPSG:25832 (reprojected from WGS84
# 52.2528 N, 10.5407 E).
_BS_HBF_E = 605170.0
_BS_HBF_N = 5790274.0

# Ring boundaries in metres (centroid distance from BS-Hbf). The ring
# index is the position in this list at which the distance falls below
# the boundary; everything beyond the last entry maps to ring 7.
# Calibrated against VRB tariff flyer page 8 (Preisliste 01.01.2026)
# so that |ring_diff| through the Bavaria prices[] array yields:
#   diff 0 -> 3.90 EUR (Stadttarif BS,    actual 3.50)
#   diff 1 -> 3.90 EUR (Preisstufe 1,     actual 3.90)
#   diff 2 -> 5.80 EUR (Preisstufe 2,     actual 5.60)
#   diff 3 -> 7.70 EUR (Preisstufe 3,     actual 7.70)
#   diff 5 -> 11.60 EUR (Preisstufe 4,    actual 12.30)
_RING_BOUNDARIES_M = [
    8_000.0,    # ring 1: BS Stadtgebiet
    18_000.0,   # ring 2: P1 (angrenzende Waben)
    32_000.0,   # ring 3: P2
    50_000.0,   # ring 4: P3
    65_000.0,   # ring 5: zwischen P3 und P4
    80_000.0,   # ring 6: P4
]


def _ring_for_distance(distance_m: float) -> int:
    """Map a centroid distance to BS-Hbf to a 1-based concentric ring id."""
    for idx, boundary in enumerate(_RING_BOUNDARIES_M, start=1):
        if distance_m < boundary:
            return idx
    return len(_RING_BOUNDARIES_M) + 1  # ring 7 = jenseits P4
# --- end Braunschweig-specific ----------------------------------------------


def configure(context):
    context.config("data_path")
    context.config("vrb_stations_path", "vrb/stations.json")


def execute(context):
    # Load raw data
    with open("{}/{}".format(context.config("data_path"), context.config("vrb_stations_path"))) as f:
        data = json.load(f)

    # Extract all existing zones
    zones = set()
    split_expression = re.compile(r"[\|/]")

    for station in data:
        zones |= set(re.split(split_expression, station["tariffZones"]))

    zones = set(z for z in zones if len(z) > 0)

    # Convert to GeoDataFrame
    df_stations = []

    for station in data:
        station_zones = set(re.split(split_expression, station["tariffZones"]))
        record = {"geometry": sgeo.Point(station["longitude"], station["latitude"])}

        for z in zones:
            record["zone_{}".format(z)] = z in station_zones

        df_stations.append(record)

    df_stations = gpd.GeoDataFrame(pd.DataFrame.from_records(df_stations), crs="EPSG:4326")

    # Extract zone (buffered multi-point) geometries
    df_zones = []

    for zone in zones:
        df_partial = df_stations[df_stations["zone_{}".format(zone)]].copy()
        df_zones.append({"zone": zone, "geometry": sgeo.MultiPoint(df_partial["geometry"].values)})

    df_zones = gpd.GeoDataFrame(pd.DataFrame.from_records(df_zones), crs="EPSG:4326")
    df_zones = df_zones.to_crs("EPSG:25832")

    df_zones["geometry"] = df_zones["geometry"].buffer(400)

    # --- Braunschweig-specific: remap raw VRB Waben IDs to concentric rings ---
    # The Bavaria Java fare model interprets the ``zone`` attribute as an
    # ordinal ring index and uses ``|zoneA-zoneB|`` to look up the price.
    # Raw VRB IDs are non-ordinal (10..92) and would collapse to a flat
    # 3.90 EUR after the ``stictMaximumZone=12`` clamp downstream. We
    # therefore relabel each polygon by its centroid distance to BS-Hbf.
    centroids = df_zones.geometry.centroid
    distances = ((centroids.x - _BS_HBF_E) ** 2 + (centroids.y - _BS_HBF_N) ** 2) ** 0.5
    df_zones["zone"] = [str(_ring_for_distance(float(d))) for d in distances]
    # --- end Braunschweig-specific -------------------------------------------

    return df_zones[["zone", "geometry"]]


def validate(context):
    path = "{}/{}".format(context.config("data_path"), context.config("vrb_stations_path"))
    if not os.path.exists(path):
        raise RuntimeError(
            "VRB zone data is not available at {}. "
            "Run 'python scripts/build_vrb_stations_json.py' to generate it from "
            "GTFS stops + VRB Waben polygons (see DOWNLOAD_CHECKLIST_BS.md, entry D3).".format(path)
        )

    return os.path.getsize(path)
