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

Example::

    python scripts/build_parking_zones_from_osm.py --ags 03157006 --bbox 52.30,10.20,52.34,10.26 \
        --out-dir eqasim-data/data/braunschweig/parking/raw_overpass
    python scripts/build_parking_zones_from_osm.py --ags 03153017 --bbox 51.8950,10.4100,51.9170,10.4500 \
        --out-dir eqasim-data/data/braunschweig/parking/raw_overpass --regulation --walk-m 250
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import math
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

# Running the file directly puts scripts/ on sys.path; the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ags", required=True, help="8-digit AGS of the municipality (file name prefix)")
    parser.add_argument("--bbox", required=True, help="south,west,north,east in WGS84 decimal degrees")
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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
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
