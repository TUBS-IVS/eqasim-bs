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

Example::

    python scripts/build_parking_zones_from_osm.py --ags 03157006 --bbox 52.30,10.20,52.34,10.26 \
        --out-dir eqasim-data/data/braunschweig/parking/raw_overpass
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable, Optional, Sequence

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ags", required=True, help="8-digit AGS of the municipality (file name prefix)")
    parser.add_argument("--bbox", required=True, help="south,west,north,east in WGS84 decimal degrees")
    parser.add_argument("--out-dir", required=True, help="directory for the raw response and the candidates")
    parser.add_argument("--street-names-file", help="UTF-8 file, one street name per line, fetched as context")
    parser.add_argument("--street-bbox", help="south,west,north,east restricting the named streets")
    parser.add_argument("--extra-statement", action="append", default=[],
                        help="raw Overpass statement added to the union; '{bbox}' is replaced by --bbox")
    parser.add_argument("--from-raw", help="re-process a saved raw response instead of sending a request")
    parser.add_argument("--timeout-s", type=int, default=REQUEST_TIMEOUT_S)
    parser.add_argument("--pause-s", type=float, default=REQUEST_PAUSE_S)
    parser.add_argument("--overwrite", action="store_true", help="replace an existing raw file of today")
    parser.add_argument("--ca-bundle", default=default_ca_bundle(),
                        help="CA bundle for TLS verification (default: certifi when installed)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.from_raw:
        raw_path = Path(args.from_raw)
        payload = json.loads(raw_path.read_text(encoding="utf-8"))
        log.info("re-processing %s (no request sent)", raw_path)
    else:
        bbox = parse_bbox(args.bbox)
        street_bbox = parse_bbox(args.street_bbox) if args.street_bbox else None
        query = build_overpass_query(bbox, street_names=_read_names(args.street_names_file), street_bbox=street_bbox,
                                     extra_statements=args.extra_statement, timeout_s=args.timeout_s)
        stamp = dt.date.today().isoformat()
        raw_path = out_dir / f"{args.ags}_overpass_{stamp}.json"
        if raw_path.exists() and not args.overwrite:
            raise SystemExit(f"{raw_path} exists; pass --overwrite to replace it or --from-raw to re-process it")
        time.sleep(max(0.0, args.pause_s))
        log.info("sending one Overpass request for %s (TLS trust store: %s)", args.ags,
                 args.ca_bundle or "interpreter default")
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
