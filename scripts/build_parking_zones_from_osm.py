"""Curation aid: fee-tagged parking of one municipality from OpenStreetMap -> candidate polygons (issue #249).

NOT a pipeline stage: synpp never runs it and no model input is read from its output directly. It sends
ONE Overpass API request per invocation (one municipality, after a courtesy pause) for

* parking areas and nodes tagged ``amenity=parking`` + ``fee=yes``,
* street ways with the new on-street parking scheme ``parking:{both,left,right}:fee=yes``,
* street ways with the legacy scheme ``parking:condition:{both,left,right}=ticket`` (``parking:lane:*``),
* optionally named street ways (``--street-names-file``, e.g. the street list of a municipal parking
  concept) and extra raw statements (``--extra-statement``, e.g. campus outlines), which are written as
  context features, not as fee evidence.

Outputs in ``--out-dir`` (under ``eqasim-data/data/braunschweig/parking/raw_overpass/``, gitignored):
``<ags>_overpass_<date>.json`` (the raw response, provenance) with ``<ags>_overpass_<date>.query.txt``,
``<ags>_candidates.geojson`` (fee evidence buffered -- streets by ``STREET_BUFFER_M``, parking objects by
``PARKING_BUFFER_M`` -- dissolved into connected candidate polygons, with the OSM ids and the tag evidence
as properties) and ``<ags>_context_features.geojson`` (named streets and extra features, unbuffered).
The candidates are reviewed by hand and merged into
``eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson``; the committed file records the
geometry source of every polygon. CRS: requests in WGS84, geometry work in EPSG:25832 (metres).

Regulation mode (``--regulation``, parking cost zones v2 lever 1, issue #436): instead of the fee evidence the
request fetches every kerbside street of the box (``zone_geometry.STREET_HIGHWAY_TYPES``, the denominator of the
tagging completeness), every other highway way with a ``parking:*`` key and every ``amenity=parking`` object, all
with their tags (``REGULATION_STATEMENTS``); restrict ``--bbox`` to the town centre. The response is classified and
turned into the unavoidable-paid-parking core by ``braunschweig.parking.zone_geometry.build_zone_core``
(``--walk-m``, ``--maximum-filled-hole-m2``, ``--minimum-island-m2``; buffers ``--street-buffer-m`` and
``--lot-buffer-m``). Outputs next to the v1 files: the raw response ``<ags>_regulation_overpass_<date>.json`` with
its query, and three files named by the parameter tag of the run (``zone_geometry.ZoneCoreParameters.tag``, e.g.
``w250_b25_l10_h20000_i10000``, so a sensitivity run never replaces the pre-registered one): ``<ags>_regulated_<tag>
.geojson`` (R, fill(R) and every classified way and lot), ``<ags>_core_<tag>.geojson`` (the parts of Z, possibly
none) and ``<ags>_qa_<tag>.json`` (counts, areas, tagging completeness, the Q4 decision, and with
``--reference-outline`` the comparison with a georeferenced ordinance map: per zone the paid ways, the ways with a free
side and the free street-side areas by name, the paid ways outside it and erode(fill(R), W) inside it; with
``--street-names-file`` the listed streets without a fee tag and the fee-tagged streets outside the list). An existing
output is replaced only with ``--overwrite`` (logged as a warning), because the out-dir is write-through data of the
main checkout. ``--all-kerbside-streets-regulated`` is a diagnostic counterfactual (every kerbside street counts as
regulated: the upper bound of what the fill cap Q3 allows whatever the tagging); its files carry the tag suffix
``_allstreets`` and the QA file names the counterfactual, so the assembly never takes it for a release input. The v1
candidate files are not written in this mode. The request is retried with exponential backoff (``--attempts``,
``--backoff-s``) after the courtesy pause; every failed attempt is appended to ``overpass_failures.log`` and a
municipality whose request still fails produces no output at all (it stays unchanged, never filled in).
``--offline-response`` takes a saved body instead of the network (tests); ``--from-raw`` re-processes a saved
response without writing a raw copy. An existing raw response is never overwritten without ``--overwrite``.

Supply-share mode (``--supply-share``, parking cost zones v2 spec Amendment B, issue #436): the majority rule over the
parking supply (``braunschweig.parking.supply_share``) for several towns in one invocation, from the pinned Geofabrik
extract instead of the Overpass API. ``--osm-extract`` is checked against its Geofabrik MD5 file first
(``check_extract_md5``; SHA-256 recorded), the snapshot timestamp comes from the PBF header (``pbf_header``,
``osmosis_replication_timestamp``; ``--osm-timestamp`` only for an extract without one), and each GDAL OSM layer
(points, lines, multipolygons; ``SUPPLY_LAYER_FILTERS``) is read ONCE over the union of the ``--town
AGS=south,west,north,east`` boxes grown by ``SUPPLY_INVENTORY_MARGIN_M`` (``read_supply_inventory``, reading time per
layer recorded); the parking-relevant features are saved as ``supply_inventory_<extract>.gpkg`` with a metadata
``.json``, which ``--from-inventory`` re-processes without reading the extract. Per town and arm
(``SupplyArm``: the parameters ``--walk-m``, ``--share-threshold``, ``--minimum-usable-spaces`` on the full
inventory; the arm options below): the supply inventory inside the query box (B1, B2;
the raster also sees the supply up to W plus one cell beyond it), the cross-check of its element counts per kind and
class with the saved Overpass regulation response of Task 1 (``--overpass-dir``, read only), the paid-share raster
(B3), the rule polygons (B4), in Braunschweig the pre-registered validation B5 (``--legal-zones`` with the
ordinance polygons ``bs_zone_ia`` and ``bs_zone_ib``, ``--reference 03101000=<annex zones>``, the annex map frame from
``--annex-affine`` and ``--annex-image``) and the comparison with a ``--reference AGS=<outline>``. Outputs, named by
the tag of the arm (``SupplyArm.tag``, e.g. ``w250_t0.3_u50_c25_s12.5_i10000``): ``<ags>_supply_qa_<tag>
.json``, ``<ags>_supply_zones_<tag>.geojson``, ``<ags>_paid_share_<tag>.csv.gz`` (every cell) and
``<ags>_supply_elements_<tag>.geojson`` (every classified element). Every existing output is refused before any
reading unless ``--overwrite`` is given (write-through data of the main checkout). With ``--from-inventory``,
``--osm-extract-md5`` checks that the inventory was read from the extract of that Geofabrik MD5 file. ``--counterfactual
yes_sides_no_information`` is a POST HOC diagnostic (``supply_share.YES_SIDES_COUNTERFACTUAL``: ``parking:<side>=yes``
read as no parking information, as lever 1 reads it, instead of the literal B-a reading): its files carry the suffix
``_yesnoinfo``, its QA file names the counterfactual, and the assembly never takes it for a release input.

Owner decisions 2 and 3 (Task 1c): the default share threshold is 0.3 (``supply_share.DEFAULT_SUPPLY_PARAMETERS``, a
POST HOC change of B-f; every output name carries it through the tag, e.g. ``w250_t0.3_u50_c25_s12.5_i10000``). Every
holdout town (``supply_share.HOLDOUT_REFERENCE_ZONES``) gets the overlaps of the holdout check H2 in its QA file
(``holdout``; the reference polygons from ``--holdout-zones``, the zone release; ``supply_share.holdout_town_metrics``,
precision in the frame of the town's query box for the four towns of ``HOLDOUT_PRECISION_TOWNS``), and once every
holdout town of an arm is run the pooled H2 is logged (``holdout_pooled_metrics``; the assembly re-applies it). The
inventory also holds the payment evidence of variant T (``SUPPLY_LAYER_FILTERS``, ``inventory_evidence``: parking ticket
machines and every parking element with an app-payment tag, ruling R-T1c-a; an inventory read before Task 1c has none
and is refused for T, one read with other layer filters is warned about); its counts and conversions inside the query
box are in the QA file of every T arm (``payment_evidence``). ``--variant-arms`` adds the information arms
``supply_share.VARIANT_ARMS`` (S at 0.3 and 0.5, T at 0.5 and 0.3, S+T at 0.5 and 0.3; tag suffixes ``_streetonly``
and ``_parkingpayment75m``; ``--variants`` restricts them to some variants), ``--sensitivity-arms`` the Amendment B arms
``supply_share.AMENDMENT_B_ARMS`` (its pre-registered share 0.5 and the B5 arms W 150 / 400 m and share 0.7),
``--arms-only`` skips the chosen parameters. The Overpass cross-check always compares the B1 classification of the full
inventory.

Example::

    python scripts/build_parking_zones_from_osm.py --ags 03157006 --bbox 52.30,10.20,52.34,10.26 \
        --out-dir eqasim-data/data/braunschweig/parking/raw_overpass
    python scripts/build_parking_zones_from_osm.py --ags 03153017 --bbox 51.8950,10.4100,51.9170,10.4500 \
        --out-dir eqasim-data/data/braunschweig/parking/raw_overpass --regulation --walk-m 250
    python scripts/build_parking_zones_from_osm.py --supply-share \
        --osm-extract eqasim-data/data/braunschweig/parking/raw_osm/niedersachsen-260929.osm.pbf \
        --town 03101000=52.233,10.498,52.28,10.587 --town 03153017=51.895,10.41,51.917,10.45 ... \
        --overpass-dir eqasim-data/data/braunschweig/parking/raw_overpass \
        --reference 03101000=eqasim-data/data/braunschweig/parking/raw_sources/bs_2_08_annex_zones_georeferenced.geojson \
        --legal-zones eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson \
        --holdout-zones eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson \
        --annex-affine scripts/curation/parking_zones_2026/parkgo_annex_affine.json \
        --annex-image eqasim-data/data/braunschweig/parking/raw_sources/bs_2_08_annex_north_up.png \
        --out-dir eqasim-data/data/braunschweig/parking/raw_osm/derived_supply_share_task_1c --variant-arms \
        --sensitivity-arms
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import math
import re
import ssl
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import LineString, Point, Polygon, box
from shapely.ops import polygonize, unary_union

# Running the file directly puts scripts/ on sys.path; the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from braunschweig.parking import supply_share  # noqa: E402
from braunschweig.parking import zone_geometry  # noqa: E402

log = logging.getLogger("build_parking_zones_from_osm")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "eqasim-bs-parking-curation/0.1"
REQUEST_TIMEOUT_S = 180
#: Courtesy pause before the single request, so that consecutive invocations are at least this far apart.
REQUEST_PAUSE_S = 5.0
METRIC_CRS = "EPSG:25832"
#: Half-width of a paid street corridor: carriageway plus parking lane and sidewalk of an urban street.
STREET_BUFFER_M = 15.0
#: Margin around a mapped parking area or node (mapping precision of the outline).
PARKING_BUFFER_M = 5.0
FEE_STATEMENTS = (
    'nwr["amenity"="parking"]["fee"="yes"]',
    'way["highway"]["parking:both:fee"~"yes"]',
    'way["highway"]["parking:left:fee"~"yes"]',
    'way["highway"]["parking:right:fee"~"yes"]',
    'way["highway"]["parking:condition:both"~"ticket"]',
    'way["highway"]["parking:condition:left"~"ticket"]',
    'way["highway"]["parking:condition:right"~"ticket"]',
)
#: Tag keys kept as evidence on the candidates (besides every key starting with ``parking``).
EVIDENCE_KEYS = ("amenity", "fee", "fee:conditional", "charge", "maxstay", "access", "operator", "name",
                 "opening_hours")
CANDIDATE_KINDS = ("parking", "street_fee")
#: Regulation mode: every kerbside street of the box (tagging completeness), every highway way with a parking:* key
#: and every parking object, with all tags.
REGULATION_STATEMENTS = (
    'way["highway"~"^(' + "|".join(zone_geometry.STREET_HIGHWAY_TYPES) + ')$"]',
    'way["highway"][~"^parking:"~"."]',
    'nwr["amenity"="parking"]',
)
#: Part of the raw file name of a regulation response, so that it never collides with a v1 response of the same day.
REGULATION_RAW_TAG = "regulation"
#: Name of the counterfactual of ``--all-kerbside-streets-regulated`` in the QA file and its file-name suffix.
COUNTERFACTUAL_ALL_STREETS = "all_kerbside_streets_regulated"
COUNTERFACTUAL_TAG_SUFFIX = "_allstreets"
DEFAULT_ATTEMPTS = 3
#: Pause before the second attempt; it doubles for every further attempt.
DEFAULT_BACKOFF_S = 60.0
_REGEX_SPECIAL = re.compile(r"([\\.^$|?*+()\[\]{}])")


def parse_bbox(text: str) -> tuple:
    """``south,west,north,east`` in decimal degrees (WGS84) -> tuple of floats, validated."""
    try:
        south, west, north, east = (float(part) for part in text.split(","))
    except ValueError:
        raise ValueError(f"bbox {text!r} must be 'south,west,north,east' in decimal degrees") from None
    if not (-90.0 <= south < north <= 90.0 and -180.0 <= west < east <= 180.0):
        raise ValueError(f"bbox {text!r} is not a valid south,west,north,east box")
    return south, west, north, east


def _bbox_text(bbox: Sequence[float]) -> str:
    """Coordinates with up to six decimals (about 0.1 m), trailing zeros dropped."""
    return ",".join(f"{value:.6f}".rstrip("0").rstrip(".") for value in bbox)


def _overpass_regex_literal(name: str) -> str:
    """A street name as a literal inside an Overpass QL regex string ("..." with backslash escapes)."""
    escaped = _REGEX_SPECIAL.sub(r"\\\1", name)          # regex escape: '(' -> '\('
    return escaped.replace("\\", "\\\\").replace('"', '\\"')  # QL string escape: '\(' -> '\\('


def build_overpass_query(bbox: Sequence[float], *, street_names: Iterable[str] = (), street_bbox=None,
                         extra_statements: Iterable[str] = (), timeout_s: int = REQUEST_TIMEOUT_S) -> str:
    """Overpass QL for the fee evidence in ``bbox`` plus optional named streets and extra statements.

    ``street_bbox`` restricts the named-street statement (default: ``bbox``); an extra statement may use
    the placeholder ``{bbox}``. The result is requested with ``out tags geom`` so that ways and relation
    members carry their coordinates.
    """
    box = _bbox_text(bbox)
    lines = [f"[out:json][timeout:{int(timeout_s)}];", "("]
    lines += [f"  {statement}({box});" for statement in FEE_STATEMENTS]
    names = sorted({name.strip() for name in street_names if name.strip()})
    if names:
        pattern = "|".join(_overpass_regex_literal(name) for name in names)
        lines.append(f'  way["highway"]["name"~"^({pattern})$"]({_bbox_text(street_bbox or bbox)});')
    for statement in extra_statements:
        statement = statement.strip().replace("{bbox}", box)
        lines.append(f"  {statement if statement.endswith(';') else statement + ';'}")
    lines += [");", "out tags geom;"]
    return "\n".join(lines) + "\n"


def default_ca_bundle() -> Optional[str]:
    """The certifi CA bundle when installed, else None (the interpreter's default trust store).

    The pinned Windows interpreter fails to parse one certificate of the Windows store while building the
    default TLS context (``ssl.SSLError ASN1 NOT_ENOUGH_DATA``, also noted in the Data Registry record
    ``vrb_tariff_zone_polygons``); an explicit bundle avoids that store while certificate checks stay on.
    """
    try:
        import certifi
    except ImportError:
        return None
    return certifi.where()


def fetch_overpass(query: str, *, url: str = OVERPASS_URL, timeout_s: int = REQUEST_TIMEOUT_S,
                   user_agent: str = USER_AGENT, ca_bundle: Optional[str] = None) -> bytes:
    """POST one query to the Overpass API and return the raw response body (no retry, no cache).

    TLS certificates are always verified; ``ca_bundle`` only selects the trust store.
    """
    context = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else ssl.create_default_context()
    request = urllib.request.Request(url, data=urllib.parse.urlencode({"data": query}).encode("utf-8"),
                                     headers={"User-Agent": user_agent}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_s, context=context) as response:
        return response.read()


FAILURE_LOG_NAME = "overpass_failures.log"


def check_remark(payload: dict) -> None:
    """Refuse a response whose ``remark`` reports a runtime error or timeout: the element list may be truncated."""
    remark = payload.get("remark")
    if remark:
        raise ValueError(f"Overpass response carries a remark, the result may be incomplete: {remark}")


def log_failed_request(out_dir: Path, ags: str, error: BaseException, *, now=None) -> Path:
    """Append one line (UTC time, AGS, HTTP status or error) to ``<out-dir>/overpass_failures.log``."""
    now = now or dt.datetime.now(dt.timezone.utc)
    detail = f"HTTP {error.code}" if isinstance(error, urllib.error.HTTPError) else f"{type(error).__name__}: {error}"
    path = Path(out_dir) / FAILURE_LOG_NAME
    with open(path, "a", encoding="utf-8", newline="\n") as stream:
        stream.write(f"{now.strftime('%Y-%m-%dT%H:%M:%SZ')}\t{ags}\t{detail}\n")
    return path


def _has_fee_evidence(tags: dict) -> bool:
    if str(tags.get("fee", "")).startswith("yes"):
        return True
    for side in ("both", "left", "right"):
        if "yes" in str(tags.get(f"parking:{side}:fee", "")):
            return True
        if "ticket" in str(tags.get(f"parking:condition:{side}", "")):
            return True
    return False


def _evidence(tags: dict) -> str:
    keys = sorted(key for key in tags if key.startswith("parking") or key in EVIDENCE_KEYS)
    return "; ".join(f"{key}={tags[key]}" for key in keys)


def _way_geometry(element: dict, *, as_area: bool):
    coordinates = [(point["lon"], point["lat"]) for point in element.get("geometry") or [] if point]
    if len(coordinates) < 2:
        return None
    if as_area and len(coordinates) >= 4 and coordinates[0] == coordinates[-1]:
        return Polygon(coordinates)
    return LineString(coordinates)


def _relation_geometry(element: dict):
    lines = []
    for member in element.get("members") or []:
        coordinates = [(point["lon"], point["lat"]) for point in member.get("geometry") or [] if point]
        if member.get("type") == "way" and member.get("role", "outer") in ("outer", "") and len(coordinates) >= 2:
            lines.append(LineString(coordinates))
    polygons = list(polygonize(unary_union(lines))) if lines else []
    return unary_union(polygons) if polygons else None


def elements_to_features(payload: dict) -> gpd.GeoDataFrame:
    """Overpass JSON -> one feature per element in EPSG:25832 with ``kind`` and the tag ``evidence``.

    Kinds: ``parking`` (amenity=parking with fee evidence), ``parking_no_fee``, ``street_fee`` (highway
    with an on-street fee tag), ``street_named`` (highway without fee tag, requested by name) and
    ``other`` (anything an extra statement returned).
    """
    rows = []
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        element_type = element.get("type")
        is_parking = tags.get("amenity") == "parking"
        is_street = "highway" in tags and element_type == "way"
        if element_type == "node":
            geometry = Point(element["lon"], element["lat"]) if "lon" in element else None
        elif element_type == "way":
            geometry = _way_geometry(element, as_area=not is_street)
        elif element_type == "relation":
            geometry = _relation_geometry(element)
        else:
            geometry = None
        if geometry is None or geometry.is_empty:
            log.warning("skipping %s/%s: no usable geometry in the response", element_type, element.get("id"))
            continue
        if is_parking:
            kind = "parking" if _has_fee_evidence(tags) else "parking_no_fee"
        elif is_street:
            kind = "street_fee" if _has_fee_evidence(tags) else "street_named"
        else:
            kind = "other"
        rows.append({"osm_type": element_type, "osm_id": int(element["id"]), "kind": kind,
                     "name": tags.get("name", ""), "evidence": _evidence(tags), "geometry": geometry})
    frame = gpd.GeoDataFrame(rows, columns=["osm_type", "osm_id", "kind", "name", "evidence", "geometry"],
                             geometry="geometry", crs="EPSG:4326")
    return frame.to_crs(METRIC_CRS)


def build_candidates(features: gpd.GeoDataFrame, *, street_buffer_m: float = STREET_BUFFER_M,
                     parking_buffer_m: float = PARKING_BUFFER_M) -> gpd.GeoDataFrame:
    """Buffer the fee evidence, dissolve it and return one candidate polygon per connected part."""
    if features.crs is None or features.crs.to_epsg() != 25832:
        raise ValueError(f"build_candidates needs features in {METRIC_CRS}, found {features.crs}")
    evidence = features[features["kind"].isin(CANDIDATE_KINDS)].copy()
    columns = ["candidate_id", "n_features", "osm_ids", "kinds", "names", "evidence", "area_m2", "geometry"]
    if evidence.empty:
        return gpd.GeoDataFrame(columns=columns, geometry="geometry", crs=METRIC_CRS)
    distances = evidence["kind"].map({"street_fee": street_buffer_m, "parking": parking_buffer_m})
    evidence["geometry"] = [geometry.buffer(distance) for geometry, distance in zip(evidence.geometry, distances)]
    dissolved = unary_union(list(evidence.geometry))
    parts = list(getattr(dissolved, "geoms", [dissolved]))
    rows = []
    for number, part in enumerate(sorted(parts, key=lambda polygon: (polygon.centroid.x, polygon.centroid.y)), 1):
        members = evidence[evidence.geometry.intersects(part)]
        rows.append({
            "candidate_id": f"c{number:03d}",
            "n_features": int(len(members)),
            "osm_ids": "; ".join(sorted(f"{row.osm_type}/{row.osm_id}" for row in members.itertuples())),
            "kinds": "; ".join(sorted(set(members["kind"]))),
            "names": "; ".join(sorted({name for name in members["name"] if name})),
            "evidence": " | ".join(sorted(set(members["evidence"]))),
            "area_m2": round(float(part.area), 1),
            "geometry": part,
        })
    return gpd.GeoDataFrame(rows, columns=columns, geometry="geometry", crs=METRIC_CRS)


def _read_names(path: Optional[str]) -> list:
    if not path:
        return []
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


# --------------------------------------------------------------------------- regulation mode (v2 lever 1, #436)


def build_regulation_query(bbox: Sequence[float], *, extra_statements: Iterable[str] = (),
                           timeout_s: int = REQUEST_TIMEOUT_S) -> str:
    """Overpass QL of the regulation mode for ``bbox`` (``REGULATION_STATEMENTS``) plus optional raw statements."""
    box = _bbox_text(bbox)
    lines = [f"[out:json][timeout:{int(timeout_s)}];", "("]
    lines += [f"  {statement}({box});" for statement in REGULATION_STATEMENTS]
    for statement in extra_statements:
        statement = statement.strip().replace("{bbox}", box)
        lines.append(f"  {statement if statement.endswith(';') else statement + ';'}")
    lines += [");", "out tags geom;"]
    return "\n".join(lines) + "\n"


class OverpassResponseError(RuntimeError):
    """A response that arrived but cannot be used: not JSON, or a ``remark`` (server-side timeout, truncated)."""


def fetch_checked(query: str, **options) -> bytes:
    """``fetch_overpass`` plus the checks of a usable body; raises ``OverpassResponseError`` otherwise."""
    body = fetch_overpass(query, **options)
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError as error:
        raise OverpassResponseError(f"response of {len(body)} bytes is not JSON ({error})") from error
    try:
        check_remark(payload)
    except ValueError as error:
        raise OverpassResponseError(str(error)) from error
    return body


def fetch_with_retry(query: str, *, ags: str, out_dir: Path, attempts: int = DEFAULT_ATTEMPTS,
                     backoff_s: float = DEFAULT_BACKOFF_S, fetch: Callable[..., bytes] = fetch_checked,
                     sleep: Callable[[float], None] = time.sleep, **fetch_options) -> bytes:
    """``fetch`` with up to ``attempts`` tries; the pause before try k+1 is ``backoff_s * 2**(k-1)``.

    Network errors, HTTP errors and unusable responses (``OverpassResponseError``, e.g. a server-side timeout that
    Overpass reports as HTTP 200 with a ``remark``) count as failed tries. Every failed try is appended to
    ``overpass_failures.log`` (``log_failed_request``); after the last failure the error is raised, so the
    municipality produces no output (it stays unchanged, never filled in).
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    for attempt in range(1, attempts + 1):
        try:
            body = fetch(query, **fetch_options)
        except (urllib.error.URLError, TimeoutError, OSError, OverpassResponseError) as error:
            failure_log = log_failed_request(out_dir, ags, error)
            if attempt == attempts:
                log.error("[parking-osm] %s: Overpass request failed on attempt %d of %d (%s); recorded in %s; the "
                          "municipality stays unchanged", ags, attempt, attempts, error, failure_log)
                raise
            pause = backoff_s * 2 ** (attempt - 1)
            log.warning("[parking-osm] %s: Overpass request failed on attempt %d of %d (%s); retrying in %.0f s", ags,
                        attempt, attempts, error, pause)
            sleep(pause)
        else:
            if attempt > 1:
                log.info("[parking-osm] %s: Overpass request succeeded on attempt %d of %d", ags, attempt, attempts)
            return body
    raise AssertionError("unreachable")


def _element_geometry(element: dict, *, as_area: bool):
    element_type = element.get("type")
    if element_type == "node":
        return Point(element["lon"], element["lat"]) if "lon" in element else None
    if element_type == "way":
        return _way_geometry(element, as_area=as_area)
    if element_type == "relation":
        return _relation_geometry(element)
    return None


def regulation_features(payload: dict) -> tuple:
    """Overpass JSON of the regulation query -> (classified street segments, classified parking objects).

    Segments: highway ways as lines (``zone_geometry.classify_segments``); parking objects: ``amenity=parking``
    nodes, ways and relations (``zone_geometry.classify_lots``). Both in EPSG:25832 with ``osm_type``, ``osm_id``,
    ``name``, ``evidence`` and ``tags``. Elements without usable geometry are skipped and counted.
    """
    segments, lots, skipped, other = [], [], 0, 0
    for element in payload.get("elements", []):
        tags = element.get("tags") or {}
        is_parking = tags.get("amenity") == "parking"
        is_street = "highway" in tags and element.get("type") == "way" and not is_parking
        if not (is_parking or is_street):
            other += 1
            continue
        geometry = _element_geometry(element, as_area=is_parking)
        if geometry is None or geometry.is_empty:
            skipped += 1
            log.warning("skipping %s/%s: no usable geometry in the response", element.get("type"), element.get("id"))
            continue
        row = {"osm_type": element.get("type"), "osm_id": int(element["id"]), "name": tags.get("name", ""),
               "evidence": _evidence(tags), "tags": dict(tags), "geometry": geometry}
        (lots if is_parking else segments).append(row)
    total = len(segments) + len(lots) + skipped
    log.info("[parking-osm] regulation response: %d street ways, %d parking objects, %d without geometry skipped "
             "(%.1f %% of %d), %d other elements ignored", len(segments), len(lots), skipped,
             100.0 * skipped / total if total else 0.0, total, other)
    columns = ["osm_type", "osm_id", "name", "evidence", "tags", "geometry"]

    def frame(rows):
        return gpd.GeoDataFrame(rows, columns=columns, geometry="geometry", crs="EPSG:4326").to_crs(METRIC_CRS)

    return zone_geometry.classify_segments(frame(segments)), zone_geometry.classify_lots(frame(lots))


def _json_safe(value):
    """Nested copy with NaN and infinity replaced by None (the QA file is strict JSON)."""
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _write_geojson(frame: gpd.GeoDataFrame, path: Path) -> None:
    """WGS84 GeoJSON, also for an empty frame (an empty FeatureCollection)."""
    path.write_text(frame.to_crs("EPSG:4326").to_json(drop_id=True), encoding="utf-8", newline="\n")


def _regulated_frame(core: zone_geometry.ZoneCore, segments: gpd.GeoDataFrame, lots: gpd.GeoDataFrame, *,
                     ags: str) -> gpd.GeoDataFrame:
    """R, fill(R) and every classified way and lot as one reviewable layer (``kind``)."""
    rows = [{"kind": "regulated_area", "osm_id": None, "name": "", "highway": "", "element_class": "",
             "free_side": None, "evidence": "", "area_m2": core.regulated_area_m2, "geometry": core.regulated},
            {"kind": "filled_area", "osm_id": None, "name": "", "highway": "", "element_class": "",
             "free_side": None, "evidence": "", "area_m2": core.filled_area_m2, "geometry": core.filled}]
    for row in segments.itertuples():
        rows.append({"kind": "segment", "osm_id": int(row.osm_id), "name": row.name, "highway": row.highway,
                     "element_class": row.way_class, "free_side": bool(row.free_side), "evidence": row.evidence,
                     "area_m2": None, "geometry": row.geometry})
    for row in lots.itertuples():
        rows.append({"kind": "lot", "osm_id": int(row.osm_id), "name": row.name, "highway": "",
                     "element_class": row.lot_class, "free_side": None, "evidence": row.evidence,
                     "area_m2": round(float(row.geometry.area), 1), "geometry": row.geometry})
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs=METRIC_CRS)
    frame.insert(0, "ags", ags)
    return frame[~frame.geometry.is_empty]


def _named_elements(frame: gpd.GeoDataFrame, outline, measure: str) -> dict:
    """Count, length (m) or area (m2) inside ``outline`` and the sorted names of the elements that touch it."""
    inside = frame[frame.geometry.intersects(outline)]
    parts = inside.geometry.intersection(outline)
    amount = parts.length.sum() if measure == "length_m" else parts.area.sum()
    names = {str((tags or {}).get("name", "")).strip() for tags in inside["tags"]}
    return {"count": int(len(inside)), measure: round(float(amount), 1), "names": sorted(name for name in names if name)}


def zone_conflicts(core: zone_geometry.ZoneCore, segments: gpd.GeoDataFrame, lots: gpd.GeoDataFrame, outline) -> dict:
    """Parking evidence inside one ordinance outline that the ordinance and OSM may disagree on (spec lever 1: the
    source resolves conflicts, the script reports them): the paid ways and the paid street-side areas (since ruling
    R-T1-e the fee of a side mapped separately sits on its area), the ways OSM makes free on a side (most of them only
    because they carry no fee tag, see ``free_side_explicit``) and the free street-side areas, with the length or area
    inside and the names; plus erode(fill(R), W) inside the outline."""
    free_ways = segments[segments["free_side"].astype(bool)]
    paid_areas = lots[(lots["lot_class"] == "paid") & lots["street_side_lot"].astype(bool)]
    report = {"paid_ways": _named_elements(segments[segments["way_class"] == "paid"], outline, "length_m"),
              "paid_street_side_areas": _named_elements(paid_areas, outline, "area_m2"),
              "free_side_ways": _named_elements(free_ways, outline, "length_m"),
              "free_side_ways_without_fee_tag": int((~free_ways[free_ways.geometry.intersects(outline)]
                                                     ["free_side_explicit"].astype(bool)).sum()),
              "free_street_side_areas": _named_elements(zone_geometry.free_street_side_areas(lots), outline, "area_m2"),
              "eroded_filled_inside_m2": round(float(core.eroded_filled.intersection(outline).area), 1)}
    return report


def reference_report(core: zone_geometry.ZoneCore, segments: gpd.GeoDataFrame, lots: gpd.GeoDataFrame,
                     path: str) -> dict:
    """Comparison with a reference outline (a georeferenced ordinance map) and the ordinance conflicts.

    ``zone_geometry.compare_outlines`` and ``zone_conflicts`` for the union of all reference features and, when the
    file carries a ``zone`` column, per zone; plus the paid ways that do not touch any reference feature (the
    ordinance map is the authority: such conflicts are resolved by the source, not by this script) and the untagged
    street length inside the reference.
    """
    reference = gpd.read_file(path).to_crs(METRIC_CRS)
    outline = unary_union(list(reference.geometry))
    report = {"path": str(path)}
    report.update(zone_geometry.compare_outlines(core.core, reference))
    report.update(zone_conflicts(core, segments, lots, outline))
    if "zone" in reference.columns:
        report["zones"] = {}
        for label, group in reference.groupby("zone"):
            zone_report = zone_geometry.compare_outlines(core.core, group)
            zone_report.update(zone_conflicts(core, segments, lots, unary_union(list(group.geometry))))
            report["zones"][str(label)] = zone_report
    paid = segments[segments["way_class"] == "paid"]
    outside = paid[~paid.geometry.intersects(outline)]
    report["paid_segments_outside"] = int(len(outside))
    report["paid_length_outside_m"] = round(float(outside.geometry.length.sum()), 1)
    report["paid_names_outside"] = sorted({name for name in outside["name"] if name})
    paid_areas = lots[(lots["lot_class"] == "paid") & lots["street_side_lot"].astype(bool)]
    report["paid_street_side_areas_outside"] = int((~paid_areas.geometry.intersects(outline)).sum())
    streets = segments[segments["highway"].isin(zone_geometry.STREET_HIGHWAY_TYPES)]
    inside = streets[streets.geometry.intersects(outline)]
    lengths = inside.geometry.intersection(outline).length
    report["street_length_inside_m"] = round(float(lengths.sum()), 1)
    report["untagged_street_length_inside_m"] = round(float(lengths[inside["way_class"] == "no_parking_info"].sum()), 1)
    # The completeness of Q4 is measured inside fill(R), which is built from tagged ways; inside the reference it
    # shows how much of the ordinance area carries parking information at all.
    total = report["street_length_inside_m"]
    report["tagging_completeness_inside"] = (
        round((total - report["untagged_street_length_inside_m"]) / total, 6) if total > 0 else None)
    return report


def street_list_report(segments: gpd.GeoDataFrame, names: Sequence[str]) -> dict:
    """Listed streets without a paid way and paid ways outside the list (the list is the authority)."""
    listed = sorted({name for name in names if name})
    paid = segments[segments["way_class"] == "paid"]
    found = set(segments["name"])
    paid_names = set(paid["name"])
    return {"listed": len(listed),
            "listed_not_in_response": [name for name in listed if name not in found],
            "listed_without_fee_tag": [name for name in listed if name in found and name not in paid_names],
            "fee_tagged_outside_list": sorted(name for name in paid_names if name and name not in set(listed)),
            "fee_tagged_unnamed_segments": int((paid["name"] == "").sum())}


def output_paths(out_dir: Path, ags: str, parameters, *, counterfactual: Optional[str] = None) -> dict:
    """The three derived files of a regulation run, named by the parameter tag (and the counterfactual suffix)."""
    tag = parameters.tag() + (COUNTERFACTUAL_TAG_SUFFIX if counterfactual else "")
    return {"regulated": out_dir / f"{ags}_regulated_{tag}.geojson", "core": out_dir / f"{ags}_core_{tag}.geojson",
            "qa": out_dir / f"{ags}_qa_{tag}.json"}


def guard_outputs(paths: dict, *, overwrite: bool) -> None:
    """Refuse to replace an existing derived output unless ``overwrite`` is set; every replacement is logged."""
    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        raise SystemExit(f"{', '.join(str(path) for path in existing)} exists; pass --overwrite to replace the outputs "
                         "of this parameter set (the out-dir may be write-through data of the main checkout)")
    for path in existing:
        log.warning("[parking-osm] --overwrite: replacing the existing output %s", path)


def run_regulation(payload: dict, *, ags: str, out_dir: Path, raw_name: str, bbox, parameters, reference: Optional[str],
                   street_names: Sequence[str], counterfactual: Optional[str] = None, overwrite: bool = False) -> dict:
    """Classify, build the core, write ``<ags>_regulated_<tag>.geojson``, ``<ags>_core_<tag>.geojson`` and
    ``<ags>_qa_<tag>.json`` (``output_paths``; existing outputs only with ``overwrite``).

    ``counterfactual`` ``COUNTERFACTUAL_ALL_STREETS`` counts every kerbside street (``STREET_HIGHWAY_TYPES``) as
    regulated before R is built: a diagnostic upper bound, never a release input.
    """
    if counterfactual not in (None, COUNTERFACTUAL_ALL_STREETS):
        raise ValueError(f"unknown counterfactual {counterfactual!r}")
    paths = output_paths(out_dir, ags, parameters, counterfactual=counterfactual)
    guard_outputs(paths, overwrite=overwrite)
    segments, lots = regulation_features(payload)
    if counterfactual == COUNTERFACTUAL_ALL_STREETS:
        kerbside = segments["highway"].isin(zone_geometry.STREET_HIGHWAY_TYPES)
        log.warning("[parking-osm] %s: COUNTERFACTUAL %s: %d of %d kerbside streets count as regulated in addition to "
                    "the %d regulated ways (diagnostic only)", ags, counterfactual,
                    int((kerbside & ~segments["regulated"]).sum()), int(kerbside.sum()), int(segments["regulated"].sum()))
        segments = segments.assign(regulated=segments["regulated"].astype(bool) | kerbside)
    core = zone_geometry.build_zone_core(segments, lots, parameters)
    timestamp = (payload.get("osm3s") or {}).get("timestamp_osm_base", "")
    qa = {"ags": ags, "raw_response": raw_name, "osm_timestamp": timestamp,
          "bbox": list(bbox) if bbox is not None else None, "segments": int(len(segments)), "lots": int(len(lots)),
          "counterfactual": counterfactual}
    qa.update(core.qa())
    if reference:
        qa["reference"] = reference_report(core, segments, lots, reference)
    if street_names:
        qa["street_list"] = street_list_report(segments, street_names)
    parts = core.core.copy()
    parts.insert(0, "ags", ags)
    parts["walk_m"] = parameters.walk_m
    parts["osm_timestamp"] = timestamp
    _write_geojson(_regulated_frame(core, segments, lots, ags=ags), paths["regulated"])
    _write_geojson(parts, paths["core"])
    qa_path = paths["qa"]
    qa_path.write_text(json.dumps(_json_safe(qa), indent=1, allow_nan=False, ensure_ascii=True) + "\n",
                       encoding="utf-8", newline="\n")
    log.info("[parking-osm] %s: %d street ways (%s), %d lots (%s); R %.0f m2, fill(R) %.0f m2, Z %.0f m2 in %d parts, "
             "tagging completeness %s, decision %s %s; written %s", ags, len(segments), qa["segment_counts"], len(lots),
             qa["lot_counts"], qa["regulated_area_m2"], qa["filled_area_m2"], qa["core_area_m2"], qa["core_parts"],
             qa["tagging_completeness"], qa["decision"], qa["decision_reason"], qa_path)
    return qa


# --------------------------------------------------------------------------- supply-share mode (v2 Amendment B, #436)

#: OGR SQL pre-filter of the payment evidence of variant T (owner decision 3, ruling R-T1c-a): an HSTORE key starting
#: with one of ``supply_share.APP_PAYMENT_KEY_PREFIXES`` (``supply_share.app_payment_keys`` and ``is_parking_element``
#: check the tags exactly).
PAYMENT_EVIDENCE_FILTER = " OR ".join(f"other_tags LIKE '%\"{prefix}%'" for prefix in supply_share.APP_PAYMENT_KEY_PREFIXES)
#: OGR SQL pre-filters of the three GDAL OSM layers read for the supply inventory (``read_supply_inventory``); they
#: keep a superset of the elements of the regulation query (``REGULATION_STATEMENTS``), which ``inventory_frames``
#: selects exactly: kerbside streets, every other highway way with a ``parking:*`` key, every ``amenity=parking``
#: object (node, closed way or multipolygon relation, and an open way drawn as a line); since Task 1c also the payment
#: evidence (``PAYMENT_EVIDENCE_FILTER`` and the parking ticket machines), which ``inventory_evidence`` selects exactly.
#: Tags outside the attribute columns of the default osmconf.ini arrive in ``other_tags`` (HSTORE text,
#: ``parse_other_tags``).
SUPPLY_LAYER_FILTERS = {
    "points": ("other_tags LIKE '%\"amenity\"=>\"parking\"%' OR other_tags LIKE '%parking_tickets%' OR "
               + PAYMENT_EVIDENCE_FILTER),
    "lines": ("highway IN (" + ", ".join(f"'{value}'" for value in zone_geometry.STREET_HIGHWAY_TYPES) + ") OR "
              "(highway IS NOT NULL AND other_tags LIKE '%\"parking:%') OR "
              "other_tags LIKE '%\"amenity\"=>\"parking\"%' OR " + PAYMENT_EVIDENCE_FILTER),
    "multipolygons": "amenity = 'parking' OR " + PAYMENT_EVIDENCE_FILTER,
}
#: Margin of the inventory box around the union of the town boxes, metres: covers the supply region of every town
#: (its EPSG:25832 bounds buffered by the largest walk distance of the arms, 400 m, plus one cell).
SUPPLY_INVENTORY_MARGIN_M = 1000.0
#: GDAL keeps the node index of the OSM driver in memory up to this size (MB) and writes temporary files beyond it.
DEFAULT_OSM_MAX_TMPFILE_MB = 4000
#: B5 is evaluated in Braunschweig against the ordinance polygons of zones Ia and Ib (spec Amendment B).
B5_AGS = supply_share.B5_MUNICIPALITY_AGS
LEGAL_ZONE_IDS = supply_share.LEGAL_ZONE_IDS
_OSM_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_HSTORE_PAIR = re.compile(r'"((?:[^"\\]|\\.)*)"=>"((?:[^"\\]|\\.)*)"')
_HSTORE_ESCAPE = re.compile(r"\\(.)")
_QUERY_BOX = re.compile(r"\((-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)\)")
#: Columns of a GDAL OSM layer that are no OSM tags.
_NON_TAG_COLUMNS = ("osm_id", "osm_way_id", "other_tags", "z_order", "geometry")


def extract_stem(path) -> str:
    """The name of an extract without ``.osm.pbf`` / ``.osm`` (``niedersachsen-260929``)."""
    name = Path(path).name
    for suffix in (".osm.pbf", ".pbf", ".osm"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return Path(path).stem


def check_extract_md5(path, md5_path=None) -> dict:
    """File name, size, MD5 and SHA-256 of the extract, after its MD5 was checked against the Geofabrik ``.md5`` file
    (default ``<extract>.md5``, one line ``<md5>  <file name>``); ``SystemExit`` when the file names another extract or
    the MD5 differs (the extract must not be used)."""
    path = Path(path)
    md5_path = Path(md5_path) if md5_path else path.with_name(path.name + ".md5")
    if not path.is_file() or not md5_path.is_file():
        raise SystemExit(f"extract {path} or its Geofabrik MD5 file {md5_path} is missing")
    fields = md5_path.read_text(encoding="ascii").split()
    if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{32}", fields[0]):
        raise SystemExit(f"{md5_path}: expected one line '<md5>  <file name>', found {fields}")
    if fields[1].lstrip("*") != path.name:
        raise SystemExit(f"{md5_path} names {fields[1]!r}, not the extract {path.name!r}")
    md5, sha256 = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            md5.update(chunk)
            sha256.update(chunk)
    if md5.hexdigest() != fields[0]:
        raise SystemExit(f"{path}: MD5 {md5.hexdigest()} differs from the Geofabrik MD5 {fields[0]} of {md5_path}; "
                         "the extract is corrupt or not the pinned one")
    digests = {"file": path.name, "bytes": path.stat().st_size, "md5": md5.hexdigest(), "sha256": sha256.hexdigest(),
               "md5_file": md5_path.name}
    log.info("[parking-supply] extract %s: %d bytes, MD5 %s = the Geofabrik MD5 of %s, SHA-256 %s", path,
             digests["bytes"], digests["md5"], md5_path.name, digests["sha256"])
    return digests


def _varint(data: bytes, position: int) -> tuple:
    value, shift = 0, 0
    while True:
        byte = data[position]
        position += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, position
        shift += 7


def _protobuf_fields(data: bytes):
    """(field number, wire type, value) of a protobuf message (varint, 64-bit, length-delimited, 32-bit)."""
    position = 0
    while position < len(data):
        key, position = _varint(data, position)
        number, wire = key >> 3, key & 7
        if wire == 0:
            value, position = _varint(data, position)
        elif wire == 1:
            value, position = data[position:position + 8], position + 8
        elif wire == 2:
            length, position = _varint(data, position)
            value, position = data[position:position + length], position + length
        elif wire == 5:
            value, position = data[position:position + 4], position + 4
        else:
            raise ValueError(f"unsupported protobuf wire type {wire}")
        yield number, wire, value


def pbf_header(path) -> dict:
    """The OSMHeader block of a PBF extract (first blob; OSM PBF format): ``required_features``,
    ``writingprogram``, ``source``, ``osmosis_replication_timestamp`` ('YYYY-MM-DDTHH:MM:SSZ', the snapshot of the
    data), ``osmosis_replication_sequence_number`` and ``osmosis_replication_base_url`` (None when absent)."""
    with open(path, "rb") as stream:
        size = struct.unpack(">I", stream.read(4))[0]
        blob_header = {number: value for number, _, value in _protobuf_fields(stream.read(size))}
        if blob_header.get(1) != b"OSMHeader":
            raise ValueError(f"{path}: the first blob is {blob_header.get(1)!r}, not the OSMHeader of a PBF file")
        blob = {number: value for number, _, value in _protobuf_fields(stream.read(blob_header[3]))}
    if 3 in blob:
        data = zlib.decompress(blob[3])
    elif 1 in blob:
        data = blob[1]
    else:
        raise ValueError(f"{path}: the OSMHeader blob is neither raw nor zlib-compressed")
    header = {"required_features": [], "optional_features": [], "writingprogram": None, "source": None,
              "osmosis_replication_timestamp": None, "osmosis_replication_sequence_number": None,
              "osmosis_replication_base_url": None}
    for number, _, value in _protobuf_fields(data):
        if number in (4, 5):
            header["required_features" if number == 4 else "optional_features"].append(value.decode("utf-8"))
        elif number in (16, 17, 34):
            header[{16: "writingprogram", 17: "source", 34: "osmosis_replication_base_url"}[number]] = \
                value.decode("utf-8")
        elif number == 32:
            header["osmosis_replication_timestamp"] = dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        elif number == 33:
            header["osmosis_replication_sequence_number"] = int(value)
    return header


def parse_other_tags(text) -> dict:
    """The ``other_tags`` field of the GDAL OSM driver (HSTORE text ``"key"=>"value",...``, quotes and backslashes
    escaped by a backslash) as a dict; empty for None."""
    if not isinstance(text, str) or not text:
        return {}
    return {_HSTORE_ESCAPE.sub(r"\1", key): _HSTORE_ESCAPE.sub(r"\1", value) for key, value in _HSTORE_PAIR.findall(text)}


def _layer_tags(frame) -> list:
    columns = [column for column in frame.columns if column not in _NON_TAG_COLUMNS]
    tags = []
    for record in frame[columns + (["other_tags"] if "other_tags" in frame.columns else [])].to_dict("records"):
        values = {column: str(record[column]) for column in columns
                  if record[column] is not None and not (isinstance(record[column], float) and math.isnan(record[column]))}
        values.update(parse_other_tags(record.get("other_tags")))
        tags.append(values)
    return tags


def read_supply_inventory(path, bbox, *, max_tmpfile_mb: int = DEFAULT_OSM_MAX_TMPFILE_MB) -> tuple:
    """Read the parking-relevant features of ``bbox`` (south, west, north, east, WGS84) from an OSM file (PBF or XML)
    with the GDAL OSM driver: each layer of ``SUPPLY_LAYER_FILTERS`` once (the driver scans the whole file per layer).

    Returns ({layer: EPSG:4326 frame with ``osm_type``, ``osm_id``, ``tags``}, {layer: reading seconds}). GDAL's
    temporary files go to a temporary directory that is removed afterwards.
    """
    import pyogrio
    import tempfile

    south, west, north, east = bbox
    layers, seconds = {}, {}
    with tempfile.TemporaryDirectory(prefix="gdal_osm_") as scratch:
        pyogrio.set_gdal_config_options({"CPL_TMPDIR": scratch, "OSM_MAX_TMPFILE_SIZE": str(int(max_tmpfile_mb))})
        try:
            for layer, where in SUPPLY_LAYER_FILTERS.items():
                started = time.perf_counter()
                frame = pyogrio.read_dataframe(str(path), layer=layer, bbox=(west, south, east, north), where=where)
                seconds[layer] = round(time.perf_counter() - started, 1)
                if layer == "multipolygons":
                    relation = frame["osm_id"].notna().to_numpy()
                    osm_type = np.where(relation, "relation", "way")
                    osm_id = np.where(relation, frame["osm_id"], frame["osm_way_id"])
                else:
                    osm_type = "node" if layer == "points" else "way"
                    osm_id = frame["osm_id"]
                layers[layer] = gpd.GeoDataFrame({"osm_type": osm_type, "osm_id": pd.Series(osm_id).astype("int64")
                                                  .to_numpy(), "tags": _layer_tags(frame)},
                                                 geometry=frame.geometry.values, crs=frame.crs or "EPSG:4326")
                log.info("[parking-supply] read layer %s of %s: %d features in %.1f s", layer, Path(path).name,
                         len(frame), seconds[layer])
        finally:
            pyogrio.set_gdal_config_options({"CPL_TMPDIR": None, "OSM_MAX_TMPFILE_SIZE": None})
    return layers, seconds


def inventory_frames(layers: dict) -> tuple:
    """(street ways, parking objects) in EPSG:25832 from the inventory layers: the ways are the highway lines that are
    no ``amenity=parking`` and are kerbside streets (``zone_geometry.STREET_HIGHWAY_TYPES``) or carry a ``parking:*``
    key (the selection of the regulation query), the objects every ``amenity=parking`` node, area and line."""
    columns = ["osm_type", "osm_id", "tags", "geometry"]
    lines = layers["lines"]
    parking_line = np.array([tags.get("amenity") == "parking" for tags in lines["tags"]], dtype=bool)
    street = np.array([bool(tags.get("highway")) and (tags.get("highway") in zone_geometry.STREET_HIGHWAY_TYPES or
                                                      any(key.startswith("parking:") for key in tags))
                       for tags in lines["tags"]], dtype=bool)
    ways = lines[street & ~parking_line][columns]
    parts = [layers["points"], layers["multipolygons"], lines[parking_line]]
    parts = [part[np.array([tags.get("amenity") == "parking" for tags in part["tags"]], dtype=bool)][columns]
             for part in parts]
    objects = gpd.GeoDataFrame(pd.concat(parts, ignore_index=True), geometry="geometry", crs=lines.crs)
    return ways.to_crs(METRIC_CRS).reset_index(drop=True), objects.to_crs(METRIC_CRS)


def inventory_evidence(layers: dict) -> gpd.GeoDataFrame:
    """The payment evidence of variant T in the inventory layers, EPSG:25832 (``supply_share.payment_evidence``:
    parking ticket machine nodes and every element with an app-payment tag, whatever its parking class)."""
    columns = ["osm_type", "osm_id", "tags", "geometry"]
    crs = layers["points"].crs
    frame = gpd.GeoDataFrame(pd.concat([layers[layer][columns] for layer in SUPPLY_LAYER_FILTERS], ignore_index=True),
                             geometry="geometry", crs=crs)
    return supply_share.payment_evidence(frame.to_crs(METRIC_CRS), label="inventory")


def supply_inventory_paths(out_dir: Path, extract) -> dict:
    stem = f"supply_inventory_{extract_stem(extract)}"
    return {"inventory": out_dir / f"{stem}.gpkg", "meta": out_dir / f"{stem}.json"}


def write_supply_inventory(layers: dict, meta: dict, paths: dict) -> None:
    """The inventory as a GeoPackage (one layer per GDAL layer, tags as JSON text) and its metadata as JSON."""
    import pyogrio

    if paths["inventory"].exists():
        # only reached with --overwrite (guard_outputs logged the replacement): a GeoPackage keeps its old layers
        paths["inventory"].unlink()
    for layer, frame in layers.items():
        pyogrio.write_dataframe(frame.assign(tags=[json.dumps(tags, sort_keys=True, ensure_ascii=True)
                                                   for tags in frame["tags"]]), paths["inventory"], layer=layer)
    paths["meta"].write_text(json.dumps(_json_safe(meta), indent=1, ensure_ascii=True) + "\n", encoding="utf-8",
                             newline="\n")


def read_inventory_meta(path) -> dict:
    """The metadata of a saved inventory (``<inventory>.json`` next to the GeoPackage)."""
    path = Path(path)
    meta_path = path.with_suffix(".json")
    if not path.is_file() or not meta_path.is_file():
        raise SystemExit(f"inventory {path} or its metadata {meta_path} is missing")
    return json.loads(meta_path.read_text(encoding="utf-8"))


def check_inventory_md5(meta: dict, md5_path) -> None:
    """The inventory must have been read from the extract of the Geofabrik MD5 file ``md5_path`` (same file name,
    same MD5); ``SystemExit`` otherwise. Keys a re-processed inventory to the pinned extract without reading it."""
    fields = Path(md5_path).read_text(encoding="ascii").split()
    if len(fields) != 2 or not re.fullmatch(r"[0-9a-f]{32}", fields[0]):
        raise SystemExit(f"{md5_path}: expected one line '<md5>  <file name>', found {fields}")
    extract = meta.get("extract") or {}
    if fields[1].lstrip("*") != extract.get("file") or fields[0] != extract.get("md5"):
        raise SystemExit(f"the inventory was read from {extract.get('file')} (MD5 {extract.get('md5')}), not from "
                         f"{fields[1]} (MD5 {fields[0]} of {md5_path})")
    log.info("[parking-supply] the inventory was read from %s, MD5 %s = the Geofabrik MD5 of %s", extract["file"],
             extract["md5"], Path(md5_path).name)


def load_supply_inventory(path) -> tuple:
    """(layers, metadata) of a saved inventory (``write_supply_inventory``; the metadata file next to it)."""
    import pyogrio

    path = Path(path)
    meta = read_inventory_meta(path)
    layers = {}
    for layer in SUPPLY_LAYER_FILTERS:
        frame = pyogrio.read_dataframe(path, layer=layer)
        frame["tags"] = [json.loads(text) for text in frame["tags"]]
        frame["osm_id"] = frame["osm_id"].astype("int64")
        layers[layer] = frame
    log.info("[parking-supply] re-processing the saved inventory %s (%s; no extract read)", path,
             ", ".join(f"{layer} {len(frame)}" for layer, frame in layers.items()))
    return layers, meta


def town_query_polygon(bbox):
    """The query box (south, west, north, east, WGS84) as an EPSG:25832 polygon."""
    south, west, north, east = bbox
    return gpd.GeoSeries([box(west, south, east, north)], crs="EPSG:4326").to_crs(METRIC_CRS).iloc[0]


def union_bbox(bboxes, margin_m: float) -> tuple:
    """(south, west, north, east) of the union of ``bboxes`` grown by about ``margin_m`` metres in every direction."""
    south, west = min(b[0] for b in bboxes), min(b[1] for b in bboxes)
    north, east = max(b[2] for b in bboxes), max(b[3] for b in bboxes)
    dlat = margin_m / 111_320.0
    dlon = margin_m / (111_320.0 * math.cos(math.radians(max(abs(south), abs(north)))))
    return south - dlat, west - dlon, north + dlat, east + dlon


def parse_town(text: str) -> tuple:
    """``AGS=south,west,north,east`` -> (ags, bbox)."""
    ags, _, bbox = text.partition("=")
    if not re.fullmatch(r"\d{8}", ags):
        raise SystemExit(f"--town {text!r}: expected AGS=south,west,north,east with an 8-digit AGS")
    return ags, parse_bbox(bbox)


def annex_map_frame(affine_path, image_path):
    """The annex map frame (EPSG:25832 polygon): the image rectangle mapped by the inverse of the annex affine
    (``extract_parkgo_annex_zones.inverse_affine``; EPSG:25832 -> pixels)."""
    from PIL import Image
    from shapely.affinity import affine_transform

    sys.path.insert(0, str(Path(__file__).resolve().parent / "curation" / "parking_zones_2026"))
    try:
        import extract_parkgo_annex_zones
    finally:
        sys.path.pop(0)
    fit = json.loads(Path(affine_path).read_text(encoding="utf-8"))
    with Image.open(image_path) as image:
        width, height = image.size
    return affine_transform(box(0, 0, width, height), extract_parkgo_annex_zones.inverse_affine(fit))


def overpass_supply_elements(payload: dict, *, label: str = "", yes_position_is_parking: bool = True
                             ) -> gpd.GeoDataFrame:
    """The supply elements of a saved Overpass regulation response (same classification as the extract)."""
    segments, lots = regulation_features(payload)
    areas, offstreet = supply_share.split_parking_objects(lots)
    return supply_share.supply_elements(segments, areas, offstreet, label=label,
                                        region="Overpass regulation response of the query box",
                                        yes_position_is_parking=yes_position_is_parking)


def _counts_by_kind_and_class(elements) -> dict:
    return supply_share.summarise_supply(elements)["elements_by_kind_and_class"]


def cross_check(extract_elements, overpass_elements, *, response: str, overpass_timestamp: str) -> dict:
    """Element counts per kind and class of the extract and of the Overpass response of the same box, and their
    difference (extract minus Overpass)."""
    extract_counts = _counts_by_kind_and_class(extract_elements)
    overpass_counts = _counts_by_kind_and_class(overpass_elements)
    difference = {kind: {name: extract_counts[kind][name] - overpass_counts[kind][name]
                         for name in supply_share.SUPPLY_CLASSES} for kind in supply_share.ELEMENT_KINDS}
    return {"overpass_response": response, "overpass_osm_timestamp": overpass_timestamp, "extract": extract_counts,
            "overpass": overpass_counts, "difference": difference}


#: File-name suffix of the POST HOC counterfactuals of the supply-share mode.
SUPPLY_COUNTERFACTUAL_SUFFIXES = {supply_share.YES_SIDES_COUNTERFACTUAL: "_yesnoinfo"}


def supply_output_paths(out_dir: Path, ags: str, arm, *, counterfactual: Optional[str] = None) -> dict:
    """The four derived files of one town and arm (``supply_share.SupplyArm``), named by its tag (the parameters, the
    inventory variant and the suffix of a counterfactual)."""
    tag = arm.tag() + (SUPPLY_COUNTERFACTUAL_SUFFIXES[counterfactual] if counterfactual else "")
    return {"qa": out_dir / f"{ags}_supply_qa_{tag}.json", "zones": out_dir / f"{ags}_supply_zones_{tag}.geojson",
            "raster": out_dir / f"{ags}_paid_share_{tag}.csv.gz",
            "elements": out_dir / f"{ags}_supply_elements_{tag}.geojson"}


def _overpass_response(directory, ags: str) -> Path:
    """The one saved regulation response of ``ags`` in ``directory`` (read only)."""
    found = sorted(Path(directory).glob(f"{ags}_{REGULATION_RAW_TAG}_overpass_*.json"))
    if len(found) != 1:
        raise SystemExit(f"{ags}: expected one saved regulation response {ags}_{REGULATION_RAW_TAG}_overpass_<date>"
                         f".json in {directory} for the cross-check, found {[path.name for path in found]}")
    return found[0]


def check_query_box(response: Path, bbox) -> None:
    """The query text saved next to a regulation response (``<name>.query.txt``) must use exactly the town box, so
    that the cross-check compares the same box; ``SystemExit`` otherwise."""
    query = response.with_suffix(".query.txt")
    if not query.is_file():
        raise SystemExit(f"{query} is missing: the query box of {response.name} cannot be checked")
    boxes = {tuple(float(value) for value in match) for match in _QUERY_BOX.findall(query.read_text(encoding="utf-8"))}
    if len(boxes) != 1 or not all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(next(iter(boxes)), bbox)):
        raise SystemExit(f"{response.name}: query box {sorted(boxes)} of {query.name} differs from the town box "
                         f"{tuple(bbox)}; the cross-check needs the same box")


def overpass_cross_check_input(path: Path, ags: str, *, yes_position_is_parking: bool = True) -> tuple:
    """(response name, OSM snapshot, supply elements) of a saved regulation response, for ``cross_check``."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    check_remark(payload)
    elements = overpass_supply_elements(payload, label=f"{ags} Overpass {path.name}",
                                        yes_position_is_parking=yes_position_is_parking)
    return path.name, (payload.get("osm3s") or {}).get("timestamp_osm_base", ""), elements


def holdout_references(path, towns) -> dict:
    """ags -> EPSG:25832 frame (``zone_id``, geometry) of the H2 reference polygons
    (``supply_share.HOLDOUT_REFERENCE_ZONES``) of every holdout town among ``towns``, read from the zone release
    ``path``; ``SystemExit`` when a reference polygon is missing."""
    zones = gpd.read_file(path).to_crs(METRIC_CRS)
    references = {}
    for ags, zone_ids in supply_share.HOLDOUT_REFERENCE_ZONES.items():
        if ags not in towns:
            continue
        found = zones[zones["zone_id"].isin(zone_ids)]
        missing = sorted(set(zone_ids) - set(found["zone_id"]))
        if missing:
            raise SystemExit(f"{path}: the holdout references {missing} of {ags} are missing (H2 needs "
                             f"{list(zone_ids)})")
        references[ags] = found[["zone_id", "geometry"]].reset_index(drop=True)
    return references


def run_supply_town(ags: str, bbox, ways, objects, arm, *, meta: dict, overpass: tuple, reference,
                    b5_inputs: Optional[tuple], out_dir: Path, holdout: Optional[gpd.GeoDataFrame] = None,
                    evidence: Optional[gpd.GeoDataFrame] = None, counterfactual: Optional[str] = None) -> dict:
    """The majority rule for one town and arm (``supply_share.SupplyArm``): inventory (B1 plus the arm's variant, S or
    T with ``evidence``), raster, rule polygons, cross-check of the B1 inventory (``overpass`` =
    ``overpass_cross_check_input``), B5 / H1 (when ``b5_inputs`` = (legal zones, annex zones, map frame)), the H2
    overlaps (``holdout``: the town's reference polygons), the payment evidence of the box and the reference
    comparison; writes the four files of ``supply_output_paths`` and returns the QA document. ``counterfactual``
    ``supply_share.YES_SIDES_COUNTERFACTUAL`` is the POST HOC diagnostic (tagged files, never a release input)."""
    if counterfactual not in (None, supply_share.YES_SIDES_COUNTERFACTUAL):
        raise ValueError(f"unknown counterfactual {counterfactual!r}")
    parameters, variant = arm.parameters, arm.variant
    started = time.perf_counter()
    query = town_query_polygon(bbox)
    bounds = query.bounds
    region = box(*bounds).buffer(parameters.walk_m + parameters.cell_m, join_style=2)
    inventory_box = town_query_polygon(meta["box"])
    if not inventory_box.contains(region):
        raise SystemExit(f"{ags}: the supply region (bounds buffered by W + one cell) leaves the inventory box "
                         f"{meta['box']}; read the extract with a larger margin")
    town_ways = ways[ways.intersects(region)]
    areas, lots = supply_share.split_parking_objects(objects[objects.intersects(region)])
    if counterfactual:
        log.warning("[parking-supply] %s: POST HOC counterfactual %s (parking:<side>=yes read as no parking "
                    "information): a diagnostic, not a validation, never a release input", ags, counterfactual)
    supply_region = f"supply region: query box + {parameters.walk_m:g} m + one cell"
    base = supply_share.supply_elements(town_ways, areas, lots, label=ags, region=supply_region,
                                        yes_position_is_parking=counterfactual is None)
    response, overpass_timestamp, overpass_elements = overpass
    checked = cross_check(base[base.intersects(query).to_numpy()], overpass_elements, response=response,
                          overpass_timestamp=overpass_timestamp)
    elements = supply_share.variant_elements(base, variant, evidence, label=f"{ags} {variant.label}",
                                             region=supply_region)
    in_box = elements.intersects(query).to_numpy()
    supply = supply_share.summarise_supply(elements[in_box])
    # the QA numbers are those of the query box (the log line above covers the supply region around it)
    supply_share.log_fallback_rates(supply, label=f"{ags} {variant.label}", region="query box (the QA numbers)")
    raster = supply_share.paid_share_raster(elements, bounds, parameters.cell_m, parameters.walk_m,
                                            parameters.minimum_usable_spaces, label=ags)
    rule = supply_share.zone_polygons(raster, parameters.share_threshold, parameters.smoothing_m,
                                      parameters.minimum_island_m2, cell_m=parameters.cell_m, label=ags)
    paid = supply_share.paid_cells(raster, parameters.share_threshold)
    payment = None
    if variant.payment_evidence_m is not None:
        # the payment evidence belongs to variant T: its counts and conversions inside the query box
        payment = supply_share.summarise_payment_evidence(evidence[evidence.intersects(query).to_numpy()],
                                                          elements[in_box], variant.payment_evidence_m)
    qa = {"ags": ags, "parameters": parameters.as_dict(), "tag": arm.tag(), "variant": variant.as_dict(),
          "counterfactual": counterfactual, "bbox": list(bbox),
          "bounds_m": [round(value, 1) for value in bounds], "osm_timestamp": meta["osm_timestamp"],
          "extract": meta["extract"], "inventory": {key: meta[key] for key in ("file", "box", "read_seconds",
                                                                               "features")},
          "supply": supply, "supply_region_elements": int(len(elements)),
          "street_ways": int(town_ways.intersects(query).sum()), "cross_check": checked,
          "raster": {"cells": int(len(raster)), "classified_cells": int(raster["classified"].sum()),
                     "classified_cell_share": float(raster["classified"].mean()) if len(raster) else math.nan,
                     "paid_cells": int(paid.sum())},
          "rule": {"area_m2": round(float(rule.geometry.area.sum()), 1) if len(rule) else 0.0,
                   "parts": int(len(rule))},
          "validation": None, "holdout": None, "payment_evidence": payment, "reference": None}
    if b5_inputs is not None:
        metrics = supply_share.validation_metrics(rule, *b5_inputs)
        qa["validation"] = dict(metrics, passes=supply_share.passes_validation(metrics),
                                minimum=supply_share.VALIDATION_MINIMUM)
        log.info("[parking-supply] %s B5 / H1 (%s): recall %.3f, precision %.3f, minimum %.2f: %s", ags, arm.tag(),
                 metrics["recall"], metrics["precision"], supply_share.VALIDATION_MINIMUM,
                 "passes" if qa["validation"]["passes"] else "FAILS")
    if holdout is not None:
        framed = ags in supply_share.HOLDOUT_PRECISION_TOWNS
        qa["holdout"] = supply_share.holdout_town_metrics(rule, holdout, query_box=query if framed else None)
        log.info("[parking-supply] %s H2 overlaps (%s): recall %.3f (%.0f of %.0f m2 of %s), precision %s", ags,
                 arm.tag(), qa["holdout"]["recall"], qa["holdout"]["rule_inside_reference_m2"],
                 qa["holdout"]["reference_area_m2"], ", ".join(qa["holdout"]["references"]),
                 f"{qa['holdout']['precision']:.3f} in the query box" if framed else "not measured (recall only)")
    if reference is not None:
        path, outline = reference
        qa["reference"] = dict(zone_geometry.compare_outlines(rule, outline), path=str(path))
    rule_out = rule.copy()
    rule_out.insert(0, "ags", ags)
    rule_out["tag"] = arm.tag()
    paths = supply_output_paths(out_dir, ags, arm, counterfactual=counterfactual)
    _write_geojson(rule_out, paths["zones"])
    paths["raster"].write_bytes(supply_share.deterministic_gzip(raster.to_csv(index=False, lineterminator="\n")))
    _write_geojson(elements.assign(in_query_box=in_box, osm_id=elements["osm_id"].astype(float)), paths["elements"])
    qa["seconds"] = round(time.perf_counter() - started, 1)
    paths["qa"].write_text(json.dumps(_json_safe(qa), indent=1, allow_nan=False, ensure_ascii=True) + "\n",
                           encoding="utf-8", newline="\n")
    log.info("[parking-supply] %s %s: %d elements in the box (%s), heuristic capacity share %s; %d of %d cells "
             "classified, %d paid; rule %.0f m2 in %d parts; %.1f s; written %s", ags, arm.tag(),
             supply["elements"], supply["elements_by_class"], supply["heuristic_capacity_share"],
             qa["raster"]["classified_cells"], qa["raster"]["cells"], qa["raster"]["paid_cells"], qa["rule"]["area_m2"],
             qa["rule"]["parts"], qa["seconds"], paths["qa"])
    return qa


def _arms(args) -> list:
    """The arms of an invocation: the chosen parameters (default: owner decision 2, share 0.3) on the full inventory,
    unless ``--arms-only``, plus ``AMENDMENT_B_ARMS`` (``--sensitivity-arms``) and ``VARIANT_ARMS`` (``--variant-arms``,
    restricted to the variant labels of ``--variants`` when given)."""
    chosen = supply_share.SupplyArm(supply_share.SupplyShareParameters(
        walk_m=args.walk_m, share_threshold=args.share_threshold, minimum_usable_spaces=args.minimum_usable_spaces))
    arms = [] if args.arms_only else [chosen]
    variant_arms = [arm for arm in supply_share.VARIANT_ARMS if not args.variants or arm.variant.label in args.variants]
    for flag, extra in ((args.sensitivity_arms, supply_share.AMENDMENT_B_ARMS), (args.variant_arms, variant_arms)):
        if flag:
            arms += [arm for arm in extra if arm not in arms]
    if not arms:
        raise SystemExit("--arms-only needs --sensitivity-arms or --variant-arms (nothing to run)")
    return arms


def log_pooled_holdout(arm, towns: dict) -> Optional[dict]:
    """Log H2 of one arm pooled over its holdout towns (``supply_share.holdout_pooled_metrics``; ags -> holdout block)
    and, with the Braunschweig H1 of the same arm, the application gate; None when a holdout town was not run (H2 is
    undefined then, the assembly re-applies it from the QA files)."""
    blocks = {ags: qa["holdout"] for ags, qa in towns.items() if qa.get("holdout")}
    missing = sorted(set(supply_share.HOLDOUT_REFERENCE_ZONES) - set(blocks))
    if missing:
        log.info("[parking-supply] %s: H2 not pooled, the holdout towns %s were not run", arm.tag(), missing)
        return None
    pooled = supply_share.holdout_pooled_metrics(blocks)
    h1 = (towns.get(B5_AGS) or {}).get("validation")
    log.info("[parking-supply] %s (%s): H2 pooled recall %.3f, pooled precision %.3f, smallest town recall %.3f "
             "(minimums %.2f / %.2f / %.2f): %s; H1 %s", arm.tag(), arm.label, pooled["pooled_recall"],
             pooled["pooled_precision"], pooled["minimum_town_recall"], supply_share.HOLDOUT_POOLED_MINIMUM,
             supply_share.HOLDOUT_POOLED_MINIMUM, supply_share.HOLDOUT_TOWN_RECALL_MINIMUM,
             "passes" if pooled["passes"] else "FAILS",
             "not computed" if not h1 else f"recall {h1['recall']:.3f}, precision {h1['precision']:.3f}: "
             f"{'passes' if h1['passes'] else 'FAILS'}")
    return pooled


def run_supply_share(args) -> int:
    """The supply-share mode: acquire (or re-load) the inventory, then every town and parameter set."""
    towns = dict(parse_town(text) for text in args.town)
    if not towns:
        raise SystemExit("--supply-share needs at least one --town AGS=south,west,north,east")
    if bool(args.osm_extract) == bool(args.from_inventory):
        raise SystemExit("--supply-share needs exactly one of --osm-extract and --from-inventory")
    references = {}
    for text in args.reference:
        ags, _, path = text.partition("=")
        references[ags] = Path(path)
    b5_needed = B5_AGS in towns
    if b5_needed and not (args.legal_zones and args.annex_affine and args.annex_image and B5_AGS in references):
        raise SystemExit(f"{B5_AGS}: B5 needs --legal-zones, --reference {B5_AGS}=<annex zones>, --annex-affine and "
                         "--annex-image")
    holdout_towns = sorted(ags for ags in towns if ags in supply_share.HOLDOUT_REFERENCE_ZONES)
    if holdout_towns and not args.holdout_zones:
        raise SystemExit(f"{holdout_towns}: the holdout check H2 needs --holdout-zones (the zone release with the "
                         f"reference polygons {dict(supply_share.HOLDOUT_REFERENCE_ZONES)})")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    arms = _arms(args)
    counterfactual = args.counterfactual
    targets = {}
    if args.osm_extract:
        targets.update(supply_inventory_paths(out_dir, args.osm_extract))
    for ags in towns:
        for arm in arms:
            targets.update({f"{ags}_{arm.tag()}_{key}": path for key, path in supply_output_paths(
                out_dir, ags, arm, counterfactual=counterfactual).items()})
    responses = {ags: _overpass_response(args.overpass_dir, ags) for ags in towns}
    for ags, bbox in towns.items():
        check_query_box(responses[ags], bbox)
    if args.from_inventory and args.osm_extract_md5:
        check_inventory_md5(read_inventory_meta(args.from_inventory), args.osm_extract_md5)
    guard_outputs(targets, overwrite=args.overwrite)

    if args.from_inventory:
        layers, meta = load_supply_inventory(args.from_inventory)
    else:
        digests = check_extract_md5(args.osm_extract, args.osm_extract_md5)
        header = None
        if str(args.osm_extract).endswith(".pbf"):
            header = pbf_header(args.osm_extract)
            log.info("[parking-supply] PBF header: %s", header)
        snapshot = (header or {}).get("osmosis_replication_timestamp")
        if snapshot and args.osm_timestamp and args.osm_timestamp != snapshot:
            raise SystemExit(f"--osm-timestamp {args.osm_timestamp} contradicts the PBF header timestamp {snapshot}")
        snapshot = snapshot or args.osm_timestamp
        if not snapshot or not _OSM_TIMESTAMP.match(snapshot):
            raise SystemExit("the OSM snapshot timestamp comes from the PBF header (osmosis_replication_timestamp) or, "
                             "for an extract without one, from --osm-timestamp YYYY-MM-DDTHH:MM:SSZ")
        inventory_bbox = union_bbox(list(towns.values()), SUPPLY_INVENTORY_MARGIN_M)
        layers, seconds = read_supply_inventory(args.osm_extract, inventory_bbox, max_tmpfile_mb=args.osm_max_tmpfile_mb)
        import pyogrio

        paths = supply_inventory_paths(out_dir, args.osm_extract)
        meta = {"file": paths["inventory"].name, "extract": digests, "pbf_header": header, "osm_timestamp": snapshot,
                "box": [round(value, 6) for value in inventory_bbox], "margin_m": SUPPLY_INVENTORY_MARGIN_M,
                "towns": {ags: list(bbox) for ags, bbox in towns.items()}, "read_seconds": seconds,
                "features": {layer: int(len(frame)) for layer, frame in layers.items()},
                "layer_filters": SUPPLY_LAYER_FILTERS, "payment_evidence": True,
                "gdal": pyogrio.__gdal_version_string__, "pyogrio": pyogrio.__version__}
        write_supply_inventory(layers, meta, paths)
        log.info("[parking-supply] inventory of %s written to %s: %s; reading %s s", Path(args.osm_extract).name,
                 paths["inventory"], meta["features"], seconds)
    ways, objects = inventory_frames(layers)
    # the payment evidence of variant T exists only in an inventory read with the layer filters of Task 1c
    evidence = inventory_evidence(layers) if meta.get("payment_evidence") else None
    runs_t = any(arm.variant.payment_evidence_m is not None for arm in arms)
    if evidence is None:
        log.warning("[parking-supply] the inventory %s was read without the payment evidence (before Task 1c): no "
                    "evidence counts, variant T cannot run", meta.get("file"))
        if runs_t:
            raise SystemExit(f"variant T needs an inventory read with the payment evidence; {meta.get('file')} has none "
                             "(re-read the extract with --osm-extract)")
    elif runs_t and meta.get("layer_filters") != SUPPLY_LAYER_FILTERS:
        changed = sorted(layer for layer, where in SUPPLY_LAYER_FILTERS.items()
                         if (meta.get("layer_filters") or {}).get(layer) != where)
        log.warning("[parking-supply] the inventory %s was read with other layer filters (%s differ from the current "
                    "ones): payment evidence that only the current filters select is missing from it; confirm by a "
                    "census that no such element exists before relying on variant T", meta.get("file"), changed)
    holdout = holdout_references(args.holdout_zones, towns) if holdout_towns else {}
    b5_inputs = None
    if b5_needed:
        legal = gpd.read_file(args.legal_zones).to_crs(METRIC_CRS)
        legal = legal[legal["zone_id"].isin(LEGAL_ZONE_IDS)]
        if sorted(legal["zone_id"]) != sorted(LEGAL_ZONE_IDS):
            raise SystemExit(f"{args.legal_zones}: B5 needs the polygons {list(LEGAL_ZONE_IDS)}, found "
                             f"{sorted(legal['zone_id'])}")
        annex = gpd.read_file(references[B5_AGS]).to_crs(METRIC_CRS)
        b5_inputs = (legal, annex, annex_map_frame(args.annex_affine, args.annex_image))
    documents = {arm: {} for arm in arms}
    for ags, bbox in towns.items():
        reference = None
        if ags in references:
            reference = (references[ags], gpd.read_file(references[ags]).to_crs(METRIC_CRS))
        overpass = overpass_cross_check_input(responses[ags], ags, yes_position_is_parking=counterfactual is None)
        for arm in arms:
            documents[arm][ags] = run_supply_town(
                ags, bbox, ways, objects, arm, meta=meta, overpass=overpass, reference=reference,
                b5_inputs=b5_inputs if ags == B5_AGS else None, out_dir=out_dir, holdout=holdout.get(ags),
                evidence=evidence, counterfactual=counterfactual)
    for arm in arms:
        log_pooled_holdout(arm, documents[arm])
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ags", help="8-digit AGS of the municipality (file name prefix; fee and regulation mode)")
    parser.add_argument("--bbox", help="south,west,north,east in WGS84 decimal degrees (fee and regulation mode)")
    parser.add_argument("--out-dir", required=True, help="directory for the raw response and the candidates")
    parser.add_argument("--street-names-file", help="UTF-8 file, one street name per line, fetched as context "
                                                    "(regulation mode: compared with the fee-tagged ways)")
    parser.add_argument("--street-bbox", help="south,west,north,east restricting the named streets")
    parser.add_argument("--extra-statement", action="append", default=[],
                        help="raw Overpass statement added to the union; '{bbox}' is replaced by --bbox")
    parser.add_argument("--from-raw", help="re-process a saved raw response instead of sending a request")
    parser.add_argument("--offline-response", help="use this saved response body instead of the network; the raw "
                                                   "copy and the query are written as for a request (tests)")
    parser.add_argument("--timeout-s", type=int, default=REQUEST_TIMEOUT_S)
    parser.add_argument("--pause-s", type=float, default=REQUEST_PAUSE_S)
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS, help="tries of the request (regulation mode)")
    parser.add_argument("--backoff-s", type=float, default=DEFAULT_BACKOFF_S,
                        help="pause before the second try, doubled for every further try (regulation mode)")
    parser.add_argument("--overwrite", action="store_true",
                        help="replace an existing raw file of today and, in regulation mode, the existing outputs of the "
                             "same parameter set (logged)")
    parser.add_argument("--ca-bundle", default=default_ca_bundle(),
                        help="CA bundle for TLS verification (default: certifi when installed)")
    parser.add_argument("--regulation", action="store_true",
                        help="v2 lever 1: fetch all parking regulation of the box and build the unavoidable core")
    parser.add_argument("--walk-m", type=float, default=zone_geometry.DEFAULT_WALK_M,
                        help="walking tolerance W in metres (ASSUMPTION Q1)")
    parser.add_argument("--maximum-filled-hole-m2", type=float, default=zone_geometry.DEFAULT_MAXIMUM_FILLED_HOLE_M2,
                        help="largest hole of R that is filled, square metres (ASSUMPTION Q3)")
    parser.add_argument("--minimum-island-m2", type=float, default=zone_geometry.DEFAULT_MINIMUM_ISLAND_M2,
                        help="smallest part of the core that is kept, square metres (ASSUMPTION Q2)")
    parser.add_argument("--street-buffer-m", type=float, default=zone_geometry.DEFAULT_STREET_BUFFER_M,
                        help="buffer of a regulated street segment in metres")
    parser.add_argument("--lot-buffer-m", type=float, default=zone_geometry.DEFAULT_LOT_BUFFER_M,
                        help="buffer of a paid or restricted lot in metres")
    parser.add_argument("--reference-outline", help="GeoJSON of a georeferenced ordinance map (optional 'zone' "
                                                    "column) the core is compared with (regulation mode)")
    parser.add_argument("--all-kerbside-streets-regulated", action="store_true",
                        help="diagnostic counterfactual: every kerbside street counts as regulated (upper bound of the "
                             "fill cap); outputs tagged '_allstreets', never a release input")
    supply = parser.add_argument_group("supply-share mode (v2 Amendment B)")
    supply.add_argument("--supply-share", action="store_true",
                        help="majority rule over the parking supply of the pinned extract for every --town")
    supply.add_argument("--osm-extract", help="OSM extract (Geofabrik PBF) read with the GDAL OSM driver")
    supply.add_argument("--osm-extract-md5", help="Geofabrik MD5 file of the extract (default: <extract>.md5); with "
                                                  "--from-inventory the inventory must come from that extract")
    supply.add_argument("--osm-timestamp", help="snapshot YYYY-MM-DDTHH:MM:SSZ for an extract without a PBF header "
                                                "timestamp (checked against the header otherwise)")
    supply.add_argument("--osm-max-tmpfile-mb", type=int, default=DEFAULT_OSM_MAX_TMPFILE_MB,
                        help="memory for the node index of the GDAL OSM driver before it writes temporary files, MB")
    supply.add_argument("--from-inventory", help="re-process a saved supply_inventory_<extract>.gpkg (no extract read)")
    supply.add_argument("--town", action="append", default=[], help="AGS=south,west,north,east (repeatable)")
    supply.add_argument("--overpass-dir", help="saved Overpass regulation responses <ags>_regulation_overpass_<date>"
                                               ".json for the cross-check (read only)")
    supply.add_argument("--reference", action="append", default=[],
                        help="AGS=outline GeoJSON the rule is compared with (Braunschweig: the annex zones; repeatable)")
    supply.add_argument("--legal-zones", help="zone release with the ordinance polygons bs_zone_ia and bs_zone_ib (B5)")
    supply.add_argument("--annex-affine", help="affine of the ParkGO annex map (parkgo_annex_affine.json; B5 frame)")
    supply.add_argument("--annex-image", help="the annex rendering the affine refers to (B5 frame)")
    supply.add_argument("--share-threshold", type=float, default=supply_share.DEFAULT_SHARE_THRESHOLD,
                        help="paid share from which a classified cell is paid (ASSUMPTION B-f; default 0.3 by owner "
                             "decision 2, a POST HOC change of the pre-registered 0.5)")
    supply.add_argument("--minimum-usable-spaces", type=float, default=supply_share.DEFAULT_MINIMUM_USABLE_SPACES,
                        help="usable spaces within W from which a cell is classified (ASSUMPTION B-e)")
    supply.add_argument("--holdout-zones", help="zone release with the reference polygons of the holdout check H2 "
                                                "(required when a holdout town is run)")
    supply.add_argument("--sensitivity-arms", action="store_true",
                        help="also run the Amendment B arms: its pre-registered share 0.5 and the B5 arms W 150 / 400 m "
                             "and share 0.7 (information only)")
    supply.add_argument("--variant-arms", action="store_true",
                        help="also run the information arms S (street supply only) at 0.3 and 0.5, T (payment "
                             "evidence within 75 m) at 0.5 and 0.3 and S+T at 0.5 and 0.3 (owner decisions 2 and 3)")
    supply.add_argument("--arms-only", action="store_true",
                        help="with --sensitivity-arms or --variant-arms: run only the arms")
    supply.add_argument("--variants", nargs="+", choices=sorted({arm.variant.label for arm in supply_share.VARIANT_ARMS}),
                        help="with --variant-arms: run only the information arms of these variants (default all)")
    supply.add_argument("--counterfactual", choices=sorted(SUPPLY_COUNTERFACTUAL_SUFFIXES),
                        help="POST HOC diagnostic: yes_sides_no_information reads parking:<side>=yes as no parking "
                             "information (files tagged '_yesnoinfo', never a release input)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.supply_share:
        if args.regulation or args.from_raw or args.offline_response:
            raise SystemExit("--supply-share excludes --regulation, --from-raw and --offline-response")
        if not args.overpass_dir:
            raise SystemExit("--supply-share needs --overpass-dir (the saved regulation responses of the cross-check)")
        return run_supply_share(args)
    if not (args.ags and args.bbox):
        parser.error("--ags and --bbox are required outside the supply-share mode")
    if args.from_raw and args.offline_response:
        raise SystemExit("--from-raw and --offline-response exclude each other")
    parameters = None
    if args.regulation:
        parameters = zone_geometry.ZoneCoreParameters(
            street_buffer_m=args.street_buffer_m, lot_buffer_m=args.lot_buffer_m,
            maximum_filled_hole_m2=args.maximum_filled_hole_m2, walk_m=args.walk_m,
            minimum_island_m2=args.minimum_island_m2)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    bbox = parse_bbox(args.bbox)
    street_names = _read_names(args.street_names_file)
    if args.regulation and not args.overwrite:
        # Fail before any request is sent when this parameter set was already written.
        guard_outputs(output_paths(out_dir, args.ags, parameters, counterfactual=COUNTERFACTUAL_ALL_STREETS
                                   if args.all_kerbside_streets_regulated else None), overwrite=False)
    if args.from_raw:
        raw_path = Path(args.from_raw)
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        log.info("re-processing %s (no request sent)", raw_path)
    else:
        if args.regulation:
            query = build_regulation_query(bbox, extra_statements=args.extra_statement, timeout_s=args.timeout_s)
        else:
            street_bbox = parse_bbox(args.street_bbox) if args.street_bbox else None
            query = build_overpass_query(bbox, street_names=street_names, street_bbox=street_bbox,
                                         extra_statements=args.extra_statement, timeout_s=args.timeout_s)
        stamp = dt.date.today().isoformat()
        tag = f"_{REGULATION_RAW_TAG}" if args.regulation else ""
        raw_path = out_dir / f"{args.ags}{tag}_overpass_{stamp}.json"
        if raw_path.exists() and not args.overwrite:
            raise SystemExit(f"{raw_path} exists; pass --overwrite to replace it or --from-raw to re-process it")
        if args.offline_response:
            body = Path(args.offline_response).read_bytes()
            log.info("offline: using %s as the response of %s (no request sent)", args.offline_response, args.ags)
        else:
            time.sleep(max(0.0, args.pause_s))
            log.info("sending one Overpass request for %s (TLS trust store: %s)", args.ags,
                     args.ca_bundle or "interpreter default")
            if args.regulation:
                body = fetch_with_retry(query, ags=args.ags, out_dir=out_dir, attempts=args.attempts,
                                        backoff_s=args.backoff_s, timeout_s=args.timeout_s, ca_bundle=args.ca_bundle)
            else:
                try:
                    body = fetch_overpass(query, timeout_s=args.timeout_s, ca_bundle=args.ca_bundle)
                except (urllib.error.URLError, TimeoutError, OSError) as error:
                    failure_log = log_failed_request(out_dir, args.ags, error)
                    log.error("Overpass request for %s failed (%s); recorded in %s", args.ags, error, failure_log)
                    raise
        raw_path.write_bytes(body)
        raw_path.with_suffix(".query.txt").write_text(query, encoding="utf-8")
        payload = json.loads(body.decode("utf-8"))
        log.info("saved the raw response (%d bytes) to %s", len(body), raw_path)
    check_remark(payload)
    if args.regulation:
        run_regulation(payload, ags=args.ags, out_dir=out_dir, raw_name=raw_path.name, bbox=bbox,
                       parameters=parameters, reference=args.reference_outline, street_names=street_names,
                       counterfactual=COUNTERFACTUAL_ALL_STREETS if args.all_kerbside_streets_regulated else None,
                       overwrite=args.overwrite)
        return 0
    features = elements_to_features(payload)
    candidates = build_candidates(features)
    candidates.insert(0, "ags", args.ags)
    candidates["osm_base_timestamp"] = (payload.get("osm3s") or {}).get("timestamp_osm_base", "")
    if candidates.empty:
        log.warning("[parking-osm] %s: no fee evidence in the response; no candidates file written", args.ags)
    else:
        candidates.to_crs("EPSG:4326").to_file(out_dir / f"{args.ags}_candidates.geojson", driver="GeoJSON")
    context = features[~features["kind"].isin(CANDIDATE_KINDS)]
    if not context.empty:
        context.to_crs("EPSG:4326").to_file(out_dir / f"{args.ags}_context_features.geojson", driver="GeoJSON")
    counts = features["kind"].value_counts().to_dict() if not features.empty else {}
    log.info("[parking-osm] %s: %d elements %s -> %d candidate polygons (%.0f m2); raw %s", args.ags, len(features),
             counts, len(candidates), float(candidates["area_m2"].sum()) if len(candidates) else 0.0, raw_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
