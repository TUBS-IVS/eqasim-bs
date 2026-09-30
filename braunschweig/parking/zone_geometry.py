"""Rule-based parking zone geometry from OSM parking tags (lever 1 of parking cost zones v2, issue #436).

Rule (design addendum 2026-09-29, lever 1, binding): a point belongs to a paid zone when no free public street
parking exists within the walking tolerance ``W``. Construction (amendment A1, approved by the owner on 2026-09-30),
all in EPSG:25832 metres:

1. ``R`` = union of the regulated street segments (``classify_way`` in ``REGULATED_CLASSES``: paid, restricted or
   forbidden on at least one side) buffered ``street_buffer_m`` (25 m) and the paid or restricted parking objects
   (lots and separately mapped street-side areas) buffered ``lot_buffer_m`` (10 m);
2. ``fill(R)``: every hole of ``R`` up to ``maximum_filled_hole_m2`` is filled (20,000 m2, ASSUMPTION Q3: a block
   enclosed by regulated streets is regulated; a larger enclosed area is no block and stays open);
3. ``F`` = the street segments with at least one side tagged as free public parking (``classify_side`` =
   ``unregulated``) plus the free public street-side parking areas (``amenity=parking`` with a ``parking=*`` type in
   ``STREET_SIDE_LOT_TYPES``, ``lot_class`` unregulated; ruling R-T1-e of fix round 1). A way with one paid and one
   free side belongs to ``R`` AND to ``F``, and ``F`` wins spatially (conservative reading of "no free parking
   within W", ruling T1-a of the v2 ledger);
4. ``Z`` = erode(fill(``R``), ``W``) minus buffer(``F``, ``W``); parts below ``minimum_island_m2`` are dropped
   (1 ha, ASSUMPTION Q2). ``W`` = 250 m (ASSUMPTION Q1: five minutes at 3 km/h there and back).

The rev-1 construction "``Z`` = ``R`` eroded by ``W``" is empty in every street grid (street buffers are strips
separated by the blocks); the fill step is what makes a core possible (``tests/test_parking_zone_geometry.py`` pins
both). A street without any parking tag is no evidence of free parking (a mapping gap) and is ignored in ``F``; the
share of street length inside fill(``R``) that carries parking information is reported instead (tagging
completeness, streets = ``STREET_HIGHWAY_TYPES``). A side tagged ``separate`` (its parking is mapped as its own area)
carries no information about the street itself: it is neither ``R`` nor ``F`` (class ``separate``, which counts as
tagged), and the separately mapped street-side AREA decides by its own class (R-T1-e). Free public OFF-STREET lots are
no part of ``F`` (the rule speaks of street parking, ruling T1-b, narrowed to off-street by R-T1-e); they are counted
inside ``Z`` for the QA report. Parking objects with ``access=private|customers`` are neither paid nor free supply.
ASSUMPTION (F4 of fix round 1): a parking side or street-side area without any fee tag counts as free; the QA reports
how many free ways and areas rest on that assumption (``free_segments_without_fee_tag``,
``free_street_side_areas_without_fee_tag``).

Acceptance rule Q4 (ASSUMPTION, pre-registered in the v2 plan, Task 1 Step 4): a core is accepted when its area is at
least ``ACCEPTANCE_MINIMUM_CORE_M2`` (1 ha) and the tagging completeness inside fill(``R``) is at least
``ACCEPTANCE_MINIMUM_TAGGING_COMPLETENESS`` (60 %).

Tag schemes (``classify_side``; the value of ``<prefix>:<side>:*`` falls back to ``<prefix>:both:*``):

* paid: ``parking:<side>:fee=yes``, a ``parking:<side>:fee:conditional`` clause ``yes @ (...)`` (the day-time fee
  of the new scheme; the legacy equivalent below is paid regardless of its time interval), legacy
  ``parking:condition:<side>=ticket``;
* restricted: ``parking:<side>:access=permit|private``, ``parking:<side>:authentication:disc=yes`` (the new-scheme
  form of the legacy disc condition), legacy ``parking:condition:<side>=residents|disc|private``;
* separate (checked first, so it overrides every other tag of that side): ``parking:<side>=separate`` or legacy
  ``parking:lane:<side>=separate``;
* forbidden: ``parking:<side>=no``, ``parking:<side>:restriction=no_parking|no_stopping``,
  ``parking:<side>:access=no``, legacy ``parking:lane:<side>=no_parking|no_stopping|no`` and
  ``parking:condition:<side>=no_parking|no_stopping``;
* not public: any other side access or restriction value (customers, delivery, loading_only, ...) and any other
  legacy condition except ``free``; on the way itself ``access=private|customers`` (then the sides are not read);
* unregulated (free public supply): a parking side (``parking:<side>`` in ``PARKING_POSITIONS`` or legacy
  ``parking:lane:<side>`` in ``LEGACY_PARKING_LANE_TYPES``) with ``fee=no`` or no fee tag and no access limit;
* everything else: ``no_parking_info``.

The class of a way is the first of its two side classes in ``WAY_CLASSES`` order, so a way with any regulated side
is regulated; ``has_free_side`` answers the F question separately.

Parameters of a run (``ZoneCoreParameters``) name its output files (``ZoneCoreParameters.tag``); the pre-registered
values are ``PRE_REGISTERED_PARAMETERS`` (W 250 m, buffers 25 and 10 m, fill 20,000 m2, islands 10,000 m2), the only
ones the committed QA table may carry.

Pure functions: no file or network access. Every GeoDataFrame input must be in EPSG:25832 (``ValueError``
otherwise); shapely geometries passed between the steps (``R``, fill(``R``)) are EPSG:25832 by contract. Used by the
curation aid ``scripts/build_parking_zones_from_osm.py --regulation``; never a pipeline stage.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import re
from typing import Mapping, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

log = logging.getLogger(__name__)

_LOG_TAG = "[parking-geometry]"
METRIC_CRS = "EPSG:25832"

#: Classes of a street side and of a way, in precedence order (the class of a way is its first side class).
WAY_CLASSES = ("paid", "restricted", "forbidden", "unregulated", "not_public", "separate", "no_parking_info")
#: Classes that put a street segment into R.
REGULATED_CLASSES = ("paid", "restricted", "forbidden")
#: Classes of an ``amenity=parking`` object; paid and restricted lots enter R, unregulated lots are free lots.
LOT_CLASSES = ("paid", "restricted", "unregulated", "not_public", "no_parking_info")
REGULATED_LOT_CLASSES = ("paid", "restricted")
SIDES = ("left", "right")
#: New-scheme values of ``parking:<side>`` that place parking on the street (a parking side).
PARKING_POSITIONS = ("lane", "street_side", "on_kerb", "half_on_kerb", "shoulder")
#: New-scheme values of ``parking:<side>`` that forbid parking on that side.
NO_PARKING_POSITIONS = ("no",)
#: Value of ``parking:<side>`` / legacy ``parking:lane:<side>``: the side's parking is mapped as its own area, so the
#: street carries no information about it (ruling R-T1-e; the area is classified by ``classify_lot``).
SEPARATE_POSITION = "separate"
#: Legacy values of ``parking:lane:<side>`` that place parking on the street.
LEGACY_PARKING_LANE_TYPES = ("parallel", "diagonal", "perpendicular", "marked", "yes")
#: Legacy values of ``parking:lane:<side>`` without parking.
LEGACY_NO_PARKING_LANE_TYPES = ("no_parking", "no_stopping", "no")
FORBIDDEN_RESTRICTIONS = ("no_parking", "no_stopping")
#: Side access values that restrict parking to permit holders or to the owner's users.
RESTRICTED_ACCESS = ("permit", "private")
#: Legacy ``parking:condition:<side>`` values that restrict parking.
LEGACY_RESTRICTED_CONDITIONS = ("residents", "disc", "private")
#: Access values that do not limit the public (side, way or lot).
PUBLIC_ACCESS = ("yes", "permissive", "public", "destination")
#: Access values of a whole way or lot that make it no public supply.
NOT_PUBLIC_ACCESS = ("private", "customers")
#: Street classes that can carry kerbside parking: the denominator of the tagging completeness.
STREET_HIGHWAY_TYPES = ("primary", "secondary", "tertiary", "unclassified", "residential", "living_street")
#: ``parking=*`` values of an ``amenity=parking`` area that is street parking mapped as a separate object (``layby``
#: is the older value of a street-side bay).
STREET_SIDE_LOT_TYPES = ("street_side", "lane", "on_kerb", "half_on_kerb", "shoulder", "layby")

DEFAULT_STREET_BUFFER_M = 25.0
DEFAULT_LOT_BUFFER_M = 10.0
DEFAULT_MAXIMUM_FILLED_HOLE_M2 = 20_000.0
DEFAULT_WALK_M = 250.0
DEFAULT_MINIMUM_ISLAND_M2 = 10_000.0
#: Acceptance rule Q4 (ASSUMPTION, pre-registered): a core of at least 1 ha ...
ACCEPTANCE_MINIMUM_CORE_M2 = 10_000.0
#: ... with at least 60 % of the street length inside fill(R) carrying parking information.
ACCEPTANCE_MINIMUM_TAGGING_COMPLETENESS = 0.60
#: Sample spacing along the outlines for the largest outline distance (the error is at most half of it).
OUTLINE_SAMPLE_SPACING_M = 2.0

_CONDITIONAL_YES = re.compile(r"(?:^|;)\s*yes\s*@")


# --------------------------------------------------------------------------- tag classification


def _tag_text(tags: Mapping, key: str) -> Optional[str]:
    value = tags.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _side_tag(tags: Mapping, prefix: str, side: str, suffix: str = "") -> Optional[str]:
    """``<prefix>:<side>[:<suffix>]``, falling back to ``<prefix>:both[:<suffix>]``."""
    tail = f":{suffix}" if suffix else ""
    value = _tag_text(tags, f"{prefix}:{side}{tail}")
    return value if value is not None else _tag_text(tags, f"{prefix}:both{tail}")


def _tokens(value: Optional[str]) -> set:
    """The ';'-separated values of a tag, lower case (``ticket;residents`` -> {ticket, residents})."""
    if value is None:
        return set()
    return {part.strip().lower() for part in value.split(";") if part.strip()}


def _conditional_yes(value: Optional[str]) -> bool:
    """True when a ``*:conditional`` value has a clause ``yes @ (...)``."""
    return value is not None and bool(_CONDITIONAL_YES.search(value.lower()))


def classify_side(tags: Mapping, side: str) -> str:
    """Class of one street side (``left`` or ``right``) from the new and the legacy on-street parking scheme."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}, got {side!r}")
    position = _side_tag(tags, "parking", side)
    position = position.lower() if position else None
    fee = _tokens(_side_tag(tags, "parking", side, "fee"))
    access = _tokens(_side_tag(tags, "parking", side, "access"))
    restriction = _tokens(_side_tag(tags, "parking", side, "restriction"))
    disc = _tokens(_side_tag(tags, "parking", side, "authentication:disc"))
    lane = _tokens(_side_tag(tags, "parking:lane", side))
    condition = _tokens(_side_tag(tags, "parking:condition", side))

    if position == SEPARATE_POSITION or SEPARATE_POSITION in lane:
        return "separate"
    if "yes" in fee or _conditional_yes(_side_tag(tags, "parking", side, "fee:conditional")) or "ticket" in condition:
        return "paid"
    if access & set(RESTRICTED_ACCESS) or condition & set(LEGACY_RESTRICTED_CONDITIONS) or "yes" in disc:
        return "restricted"
    if (position in NO_PARKING_POSITIONS or restriction & set(FORBIDDEN_RESTRICTIONS) or "no" in access
            or lane & set(LEGACY_NO_PARKING_LANE_TYPES) or condition & set(FORBIDDEN_RESTRICTIONS)):
        return "forbidden"
    if access - set(PUBLIC_ACCESS) or restriction - {"none"} or condition - {"free"}:
        return "not_public"
    has_parking = position in PARKING_POSITIONS or bool(lane & set(LEGACY_PARKING_LANE_TYPES))
    if has_parking and fee <= {"no"}:
        return "unregulated"
    return "no_parking_info"


def _way_is_public(tags: Mapping) -> bool:
    return not (_tokens(_tag_text(tags, "access")) & set(NOT_PUBLIC_ACCESS))


def classify_way(tags: Mapping) -> str:
    """Class of a street way in ``WAY_CLASSES``: ``not_public`` for ``access=private|customers``, else the first of
    its two side classes in ``WAY_CLASSES`` order (a way with any regulated side is regulated)."""
    if not _way_is_public(tags):
        return "not_public"
    return min((classify_side(tags, side) for side in SIDES), key=WAY_CLASSES.index)


def is_regulated(tags: Mapping) -> bool:
    """At least one side paid, restricted or forbidden (the way enters R)."""
    return classify_way(tags) in REGULATED_CLASSES


def has_free_side(tags: Mapping) -> bool:
    """At least one side is free public parking (the way enters F), also on a way with a regulated other side."""
    return _way_is_public(tags) and any(classify_side(tags, side) == "unregulated" for side in SIDES)


def has_explicit_free_side(tags: Mapping) -> bool:
    """A free side that says so (``parking:<side>:fee=no`` or legacy ``parking:condition:<side>=free``); a free side
    without any fee tag rests on the ASSUMPTION "no fee tag = free"."""
    if not _way_is_public(tags):
        return False
    for side in SIDES:
        if classify_side(tags, side) != "unregulated":
            continue
        if "no" in _tokens(_side_tag(tags, "parking", side, "fee")) or \
                "free" in _tokens(_side_tag(tags, "parking:condition", side)):
            return True
    return False


def _side_pair(tags: Mapping) -> str:
    """The two side classes of a way, sorted and joined by '+' (a way that is not public: 'not_public+not_public')."""
    if not _way_is_public(tags):
        return "not_public+not_public"
    return "+".join(sorted(classify_side(tags, side) for side in SIDES))


def _has_separate_side(tags: Mapping) -> bool:
    return any((_side_tag(tags, "parking", side) or "").lower() == "separate"
               or "separate" in _tokens(_side_tag(tags, "parking:lane", side)) for side in SIDES)


def classify_lot(tags: Mapping) -> str:
    """Class of an ``amenity=parking`` object in ``LOT_CLASSES`` (``no_parking_info`` for anything else)."""
    if _tag_text(tags, "amenity") != "parking":
        return "no_parking_info"
    access = _tokens(_tag_text(tags, "access"))
    if access & set(NOT_PUBLIC_ACCESS):
        return "not_public"
    if "yes" in _tokens(_tag_text(tags, "fee")) or _conditional_yes(_tag_text(tags, "fee:conditional")):
        return "paid"
    if "permit" in access:
        return "restricted"
    if access - set(PUBLIC_ACCESS):
        return "not_public"
    return "unregulated"


def _require_tags(frame: gpd.GeoDataFrame, what: str) -> None:
    if "tags" not in frame.columns:
        raise ValueError(f"{what} need a 'tags' column (one dict of OSM tags per element)")


def classify_segments(ways: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Copy of the street ways with ``highway``, ``way_class``, ``regulated``, ``free_side`` and ``separate_side``."""
    _require_tags(ways, "street ways")
    frame = ways.copy()
    tags = [dict(value or {}) for value in frame["tags"]]
    frame["highway"] = [str(value.get("highway", "")) for value in tags]
    frame["way_class"] = [classify_way(value) for value in tags]
    frame["regulated"] = frame["way_class"].isin(REGULATED_CLASSES).astype(bool)
    frame["free_side"] = np.array([has_free_side(value) for value in tags], dtype=bool)
    frame["free_side_explicit"] = np.array([has_explicit_free_side(value) for value in tags], dtype=bool)
    frame["separate_side"] = np.array([_has_separate_side(value) for value in tags], dtype=bool)
    frame["side_pair"] = [_side_pair(value) for value in tags]
    return frame


def classify_lots(lots: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Copy of the parking objects with ``lot_class`` and ``street_side_lot`` (street parking mapped as an area)."""
    _require_tags(lots, "parking objects")
    frame = lots.copy()
    tags = [dict(value or {}) for value in frame["tags"]]
    frame["lot_class"] = [classify_lot(value) for value in tags]
    frame["street_side_lot"] = np.array([str(value.get("parking", "")).lower() in STREET_SIDE_LOT_TYPES
                                         for value in tags], dtype=bool)
    frame["fee_tagged"] = np.array([_tag_text(value, "fee") is not None for value in tags], dtype=bool)
    return frame


def free_street_side_areas(lots: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Free public street-side parking areas (``lot_class`` unregulated, ``parking=*`` in ``STREET_SIDE_LOT_TYPES``):
    street parking mapped as its own object, part of F (ruling R-T1-e)."""
    _require_columns(lots, ("lot_class", "street_side_lot"), "parking objects (run classify_lots first)")
    return lots[(lots["lot_class"] == "unregulated") & lots["street_side_lot"].astype(bool)]


def free_offstreet_lots(lots: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Free public off-street lots (``lot_class`` unregulated, not street-side): reported in the QA, never part of F
    (ruling T1-b: the rule speaks of street parking)."""
    _require_columns(lots, ("lot_class", "street_side_lot"), "parking objects (run classify_lots first)")
    return lots[(lots["lot_class"] == "unregulated") & ~lots["street_side_lot"].astype(bool)]


def free_supply(segments: gpd.GeoDataFrame, lots: Optional[gpd.GeoDataFrame] = None) -> gpd.GeoDataFrame:
    """F: the classified street segments with at least one free public side plus, when ``lots`` is given, the free
    public street-side areas (``free_street_side_areas``); one row per element with ``kind`` segment or
    street_side_area."""
    _require_columns(segments, ("free_side",), "street segments (run classify_segments first)")
    parts = [segments[segments["free_side"].astype(bool)].assign(kind="segment")]
    if lots is not None:
        _require_metric(lots, "parking objects")
        parts.append(free_street_side_areas(lots).assign(kind="street_side_area"))
    columns = ["kind", "geometry"] + (["osm_id"] if all("osm_id" in part.columns for part in parts) else [])
    frame = pd.concat([part[columns] for part in parts], ignore_index=True)
    return gpd.GeoDataFrame(frame, geometry="geometry", crs=segments.crs)


# --------------------------------------------------------------------------- geometry helpers


def _require_columns(frame: gpd.GeoDataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"{what}: missing column(s) {missing}")


def _require_metric(frame, what: str) -> None:
    """Every GeoDataFrame or GeoSeries input must be in EPSG:25832 (metres)."""
    if not isinstance(frame, (gpd.GeoDataFrame, gpd.GeoSeries)):
        raise TypeError(f"{what} must be a GeoDataFrame or GeoSeries in {METRIC_CRS}, got {type(frame).__name__}")
    if frame.crs is None or frame.crs.to_epsg() != 25832:
        raise ValueError(f"{what} must be in {METRIC_CRS} (metres), found {frame.crs}")


def _require_non_negative(**values: float) -> None:
    for name, value in values.items():
        if not (isinstance(value, (int, float)) and math.isfinite(value) and value >= 0):
            raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")


def _polygons(geometry: Optional[BaseGeometry]) -> list:
    """The polygon parts of a geometry (lines and points of a union are dropped)."""
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if geometry.geom_type == "MultiPolygon":
        return list(geometry.geoms)
    return [part for member in getattr(geometry, "geoms", []) for part in _polygons(member)]


def _union(geometries) -> BaseGeometry:
    geometries = [geometry for geometry in geometries if geometry is not None and not geometry.is_empty]
    return unary_union(geometries) if geometries else Polygon()


def _polygonal(geometry: BaseGeometry) -> BaseGeometry:
    parts = _polygons(geometry)
    return unary_union(parts) if parts else Polygon()


def _counts(values, order) -> dict:
    counted = {}
    for value in values:
        counted[value] = counted.get(value, 0) + 1
    return {key: int(counted[key]) for key in order if key in counted}


# --------------------------------------------------------------------------- construction (amendment A1)


def regulated_area(segments: gpd.GeoDataFrame, lots: gpd.GeoDataFrame, street_buffer_m: float = DEFAULT_STREET_BUFFER_M,
                   lot_buffer_m: float = DEFAULT_LOT_BUFFER_M) -> BaseGeometry:
    """R: the regulated street segments buffered ``street_buffer_m`` and the paid or restricted lots buffered
    ``lot_buffer_m``, dissolved (EPSG:25832 polygon or multipolygon, empty when nothing is regulated)."""
    _require_metric(segments, "street segments")
    _require_metric(lots, "parking objects")
    _require_columns(segments, ("way_class", "regulated"), "street segments (run classify_segments first)")
    _require_columns(lots, ("lot_class",), "parking objects (run classify_lots first)")
    _require_non_negative(street_buffer_m=street_buffer_m, lot_buffer_m=lot_buffer_m)
    regulated = segments[segments["regulated"].astype(bool)]
    supply_lots = lots[lots["lot_class"].isin(REGULATED_LOT_CLASSES)]
    area = _polygonal(_union(list(regulated.geometry.buffer(street_buffer_m))
                             + list(supply_lots.geometry.buffer(lot_buffer_m))))
    log.info("%s R: %d of %d street segments regulated (by class %s), %d of %d lots paid or restricted (by class %s); "
             "buffers %.0f m / %.0f m; R = %.0f m2", _LOG_TAG, len(regulated), len(segments),
             _counts(segments["way_class"], WAY_CLASSES), len(supply_lots), len(lots),
             _counts(lots["lot_class"], LOT_CLASSES), street_buffer_m, lot_buffer_m, area.area)
    return area


def hole_counts(area: BaseGeometry, maximum_hole_m2: float = DEFAULT_MAXIMUM_FILLED_HOLE_M2) -> tuple:
    """(holes filled, holes kept open) of ``fill_holes`` with the same maximum."""
    areas = [Polygon(ring).area for polygon in _polygons(area) for ring in polygon.interiors]
    filled = sum(1 for hole in areas if hole <= maximum_hole_m2)
    return filled, len(areas) - filled


def fill_holes(area: BaseGeometry, maximum_hole_m2: float = DEFAULT_MAXIMUM_FILLED_HOLE_M2) -> BaseGeometry:
    """fill(R): every hole up to ``maximum_hole_m2`` filled (ASSUMPTION Q3); larger holes stay open."""
    _require_non_negative(maximum_hole_m2=maximum_hole_m2)
    parts = [Polygon(polygon.exterior, [ring for ring in polygon.interiors if Polygon(ring).area > maximum_hole_m2])
             for polygon in _polygons(area)]
    filled = _polygonal(_union(parts))
    holes_filled, holes_kept = hole_counts(area, maximum_hole_m2)
    log.info("%s fill(R): %d holes <= %.0f m2 filled, %d larger holes kept open; %.0f m2 -> %.0f m2", _LOG_TAG,
             holes_filled, maximum_hole_m2, holes_kept, area.area if area is not None else 0.0, filled.area)
    return filled


def _core_parts(filled: BaseGeometry, free_segments: gpd.GeoDataFrame, walk_m: float) -> list:
    _require_metric(free_segments, "free street segments")
    _require_non_negative(walk_m=walk_m)
    if filled is None or filled.is_empty:
        return []
    eroded = filled.buffer(-walk_m)
    if len(free_segments) and not eroded.is_empty:
        eroded = eroded.difference(_union(list(free_segments.geometry)).buffer(walk_m))
    return _polygons(eroded)


def _core_frame(parts: list, minimum_island_m2: float) -> tuple:
    _require_non_negative(minimum_island_m2=minimum_island_m2)
    kept = sorted((part for part in parts if part.area >= minimum_island_m2),
                  key=lambda part: (part.centroid.x, part.centroid.y))
    frame = gpd.GeoDataFrame({"part_id": [f"z{number:03d}" for number in range(1, len(kept) + 1)],
                              "area_m2": [round(float(part.area), 1) for part in kept]},
                             geometry=kept, crs=METRIC_CRS)
    return frame, len(parts) - len(kept)


def unavoidable_core(filled: BaseGeometry, free_segments: gpd.GeoDataFrame, walk_m: float = DEFAULT_WALK_M,
                     minimum_island_m2: float = DEFAULT_MINIMUM_ISLAND_M2) -> gpd.GeoDataFrame:
    """Z = erode(fill(R), W) minus buffer(F, W), parts below ``minimum_island_m2`` dropped (ASSUMPTION Q2).

    Returns one row per part (``part_id``, ``area_m2``, polygon; EPSG:25832, ordered by centroid), empty allowed.
    """
    parts = _core_parts(filled, free_segments, walk_m)
    frame, dropped = _core_frame(parts, minimum_island_m2)
    log.info("%s Z: erode by W = %.0f m minus %d free segments buffered W; %d parts, %d below %.0f m2 dropped; "
             "Z = %.0f m2", _LOG_TAG, walk_m, len(free_segments), len(frame), dropped, minimum_island_m2,
             float(frame.geometry.area.sum()) if len(frame) else 0.0)
    return frame


def _street_lengths(streets: gpd.GeoDataFrame, filled: BaseGeometry) -> tuple:
    """(tagged, total) street length in metres inside ``filled``."""
    _require_metric(streets, "streets")
    _require_columns(streets, ("way_class",), "streets (run classify_segments first)")
    if streets.empty or filled is None or filled.is_empty:
        return 0.0, 0.0
    candidates = streets[streets.geometry.intersects(filled)]
    lengths = candidates.geometry.intersection(filled).length
    tagged = float(lengths[candidates["way_class"] != "no_parking_info"].sum())
    return tagged, float(lengths.sum())


def tagging_completeness(streets: gpd.GeoDataFrame, filled: BaseGeometry) -> float:
    """Share of the street length inside ``filled`` whose way carries parking information (any class but
    ``no_parking_info``); NaN when no street lies inside."""
    tagged, total = _street_lengths(streets, filled)
    return tagged / total if total > 0 else math.nan


def _union_of(frame, what: str) -> BaseGeometry:
    _require_metric(frame, what)
    geometries = frame.geometry if isinstance(frame, gpd.GeoDataFrame) else frame
    return _polygonal(_union(list(geometries)))


def _directed_outline_distance(source: BaseGeometry, target: BaseGeometry, spacing_m: float) -> float:
    """Largest distance of a point of ``source`` (sampled every ``spacing_m``) to ``target``."""
    count = max(2, int(math.ceil(source.length / spacing_m)) + 1)
    points = shapely.line_interpolate_point(source, np.linspace(0.0, source.length, count))
    return float(np.max(shapely.distance(points, target)))


def compare_outlines(core, reference, sample_spacing_m: float = OUTLINE_SAMPLE_SPACING_M) -> dict:
    """Plausibility of a core against a reference outline (e.g. a georeferenced ordinance map).

    Returns the areas, ``core_share_inside_reference`` (core inside the reference / core),
    ``reference_share_covered`` (core inside the reference / reference) and ``largest_outline_distance_m`` (the
    Hausdorff distance between the two outlines, sampled every ``sample_spacing_m``, error at most half of it);
    shares and the distance are NaN for an empty core or reference.
    """
    core_geometry = _union_of(core, "core")
    reference_geometry = _union_of(reference, "reference outline")
    intersection = core_geometry.intersection(reference_geometry).area if not (
        core_geometry.is_empty or reference_geometry.is_empty) else 0.0
    result = {"core_area_m2": float(core_geometry.area), "reference_area_m2": float(reference_geometry.area),
              "intersection_area_m2": float(intersection), "core_share_inside_reference": math.nan,
              "reference_share_covered": math.nan, "largest_outline_distance_m": math.nan}
    if core_geometry.is_empty or reference_geometry.is_empty:
        return result
    result["core_share_inside_reference"] = float(intersection / core_geometry.area)
    result["reference_share_covered"] = float(intersection / reference_geometry.area)
    core_outline, reference_outline = core_geometry.boundary, reference_geometry.boundary
    result["largest_outline_distance_m"] = max(
        _directed_outline_distance(core_outline, reference_outline, sample_spacing_m),
        _directed_outline_distance(reference_outline, core_outline, sample_spacing_m))
    return result


# --------------------------------------------------------------------------- acceptance rule Q4 and the whole chain


@dataclasses.dataclass(frozen=True)
class CoreAcceptance:
    accepted: bool
    decision: str
    reason: str


def core_acceptance(core_area_m2: float, completeness: float) -> CoreAcceptance:
    """Acceptance rule Q4: ``core_area_m2 >= ACCEPTANCE_MINIMUM_CORE_M2`` and ``completeness >=
    ACCEPTANCE_MINIMUM_TAGGING_COMPLETENESS``; an undefined (NaN) completeness is not accepted."""
    reasons = []
    if not (core_area_m2 >= ACCEPTANCE_MINIMUM_CORE_M2):
        reasons.append(f"core {core_area_m2:.0f} m2 below {ACCEPTANCE_MINIMUM_CORE_M2:.0f} m2")
    if not (completeness >= ACCEPTANCE_MINIMUM_TAGGING_COMPLETENESS):
        shown = "undefined" if completeness is None or math.isnan(completeness) else f"{completeness:.3f}"
        reasons.append(f"tagging completeness {shown} below {ACCEPTANCE_MINIMUM_TAGGING_COMPLETENESS:.2f}")
    accepted = not reasons
    return CoreAcceptance(accepted, "accepted" if accepted else "rejected", "; ".join(reasons))


@dataclasses.dataclass(frozen=True)
class ZoneCoreParameters:
    """Parameters of the construction; every value in metres or square metres."""
    street_buffer_m: float = DEFAULT_STREET_BUFFER_M
    lot_buffer_m: float = DEFAULT_LOT_BUFFER_M
    maximum_filled_hole_m2: float = DEFAULT_MAXIMUM_FILLED_HOLE_M2
    walk_m: float = DEFAULT_WALK_M
    minimum_island_m2: float = DEFAULT_MINIMUM_ISLAND_M2

    def __post_init__(self):
        _require_non_negative(**self.as_dict())
        if self.walk_m <= 0 or self.street_buffer_m <= 0:
            raise ValueError(f"walk_m and street_buffer_m must be > 0, got {self.walk_m} and {self.street_buffer_m}")

    def as_dict(self) -> dict:
        return {field.name: float(getattr(self, field.name)) for field in dataclasses.fields(self)}

    def tag(self) -> str:
        """File-name tag of a run: ``w<walk_m>_b<street_buffer_m>_l<lot_buffer_m>_h<maximum_filled_hole_m2>_i<minimum_
        island_m2>`` (metres and square metres), e.g. ``w250_b25_l10_h20000_i10000``; runs with other parameters never
        share a file."""
        def number(value: float) -> str:
            return str(int(value)) if float(value).is_integer() else repr(float(value))

        return (f"w{number(self.walk_m)}_b{number(self.street_buffer_m)}_l{number(self.lot_buffer_m)}"
                f"_h{number(self.maximum_filled_hole_m2)}_i{number(self.minimum_island_m2)}")


#: The parameters pre-registered in the v2 plan (Q1 W 250 m, Q2 islands 1 ha, Q3 holes up to 2 ha, buffers 25 / 10 m):
#: the only ones the committed QA table and the release may carry.
PRE_REGISTERED_PARAMETERS = ZoneCoreParameters()


def _finite_or_none(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@dataclasses.dataclass(frozen=True)
class ZoneCore:
    """Result of ``build_zone_core``: the three geometries of the construction plus the QA counts."""
    parameters: ZoneCoreParameters
    regulated: BaseGeometry
    filled: BaseGeometry
    core: gpd.GeoDataFrame
    segment_counts: dict
    lot_counts: dict
    regulated_segments: int
    free_segments: int
    mixed_segments: int
    separate_segments: int
    holes_filled: int
    holes_kept: int
    islands_dropped: int
    street_length_in_fill_m: float
    tagged_street_length_in_fill_m: float
    free_segments_without_fee_tag: int
    free_street_side_areas: int
    free_street_side_areas_without_fee_tag: int
    free_offstreet_lots: int
    free_offstreet_lots_in_core: int
    free_offstreet_lot_area_in_core_m2: float
    side_class_pairs: dict
    #: erode(fill(R), W) before F is subtracted: separates a fill too fragmented for W from a core carved by F.
    eroded_filled: BaseGeometry = dataclasses.field(default_factory=Polygon)

    @property
    def eroded_filled_area_m2(self) -> float:
        return round(float(self.eroded_filled.area), 1)

    @property
    def regulated_area_m2(self) -> float:
        return round(float(self.regulated.area), 1)

    @property
    def filled_area_m2(self) -> float:
        return round(float(self.filled.area), 1)

    @property
    def core_area_m2(self) -> float:
        return round(float(self.core.geometry.area.sum()), 1) if len(self.core) else 0.0

    @property
    def tagging_completeness(self) -> float:
        if self.street_length_in_fill_m <= 0:
            return math.nan
        return round(self.tagged_street_length_in_fill_m / self.street_length_in_fill_m, 6)

    def acceptance(self) -> CoreAcceptance:
        return core_acceptance(self.core_area_m2, self.tagging_completeness)

    def qa(self) -> dict:
        """JSON-serialisable QA numbers (NaN written as None) including the Q4 decision."""
        acceptance = self.acceptance()
        values = {
            "parameters": self.parameters.as_dict(),
            "segment_counts": dict(self.segment_counts),
            "lot_counts": dict(self.lot_counts),
            "regulated_segments": self.regulated_segments,
            "free_segments": self.free_segments,
            "mixed_segments": self.mixed_segments,
            "separate_segments": self.separate_segments,
            "regulated_area_m2": self.regulated_area_m2,
            "filled_area_m2": self.filled_area_m2,
            "eroded_filled_area_m2": self.eroded_filled_area_m2,
            "holes_filled": self.holes_filled,
            "holes_kept": self.holes_kept,
            "core_area_m2": self.core_area_m2,
            "core_parts": int(len(self.core)),
            "islands_dropped": self.islands_dropped,
            "street_length_in_fill_m": round(self.street_length_in_fill_m, 1),
            "tagged_street_length_in_fill_m": round(self.tagged_street_length_in_fill_m, 1),
            "tagging_completeness": self.tagging_completeness,
            "free_segments_without_fee_tag": self.free_segments_without_fee_tag,
            "free_street_side_areas": self.free_street_side_areas,
            "free_street_side_areas_without_fee_tag": self.free_street_side_areas_without_fee_tag,
            "free_offstreet_lots": self.free_offstreet_lots,
            "free_offstreet_lots_in_core": self.free_offstreet_lots_in_core,
            "free_offstreet_lot_area_in_core_m2": self.free_offstreet_lot_area_in_core_m2,
            "side_class_pairs": dict(self.side_class_pairs),
            "decision": acceptance.decision,
            "decision_reason": acceptance.reason,
        }
        return {key: _finite_or_none(value) for key, value in values.items()}


def build_zone_core(segments: gpd.GeoDataFrame, lots: gpd.GeoDataFrame,
                    parameters: ZoneCoreParameters = ZoneCoreParameters()) -> ZoneCore:
    """The whole construction on classified segments and lots (``classify_segments``, ``classify_lots``)."""
    regulated = regulated_area(segments, lots, parameters.street_buffer_m, parameters.lot_buffer_m)
    filled = fill_holes(regulated, parameters.maximum_filled_hole_m2)
    holes_filled, holes_kept = hole_counts(regulated, parameters.maximum_filled_hole_m2)
    free = free_supply(segments, lots)
    free_ways = segments[segments["free_side"].astype(bool)]
    street_side = free_street_side_areas(lots)
    core, dropped = _core_parts_frame(filled, free, parameters)
    streets = segments[segments["highway"].isin(STREET_HIGHWAY_TYPES)] if "highway" in segments.columns else segments
    tagged_m, total_m = _street_lengths(streets, filled)
    offstreet = free_offstreet_lots(lots)
    core_geometry = _union(list(core.geometry))
    inside = offstreet[offstreet.geometry.intersects(core_geometry)] if len(core) else offstreet.iloc[0:0]
    explicit = free_ways["free_side_explicit"].astype(bool) if "free_side_explicit" in free_ways.columns else \
        pd.Series(False, index=free_ways.index)
    fee_tagged = street_side["fee_tagged"].astype(bool) if "fee_tagged" in street_side.columns else \
        pd.Series(False, index=street_side.index)
    result = ZoneCore(
        parameters=parameters, regulated=regulated, filled=filled, core=core,
        segment_counts=_counts(segments["way_class"], WAY_CLASSES), lot_counts=_counts(lots["lot_class"], LOT_CLASSES),
        regulated_segments=int(segments["regulated"].sum()), free_segments=int(len(free_ways)),
        mixed_segments=int((segments["regulated"].astype(bool) & segments["free_side"].astype(bool)).sum()),
        separate_segments=int(segments["separate_side"].sum()) if "separate_side" in segments.columns else 0,
        holes_filled=holes_filled, holes_kept=holes_kept, islands_dropped=dropped,
        street_length_in_fill_m=total_m, tagged_street_length_in_fill_m=tagged_m,
        free_segments_without_fee_tag=int((~explicit).sum()), free_street_side_areas=int(len(street_side)),
        free_street_side_areas_without_fee_tag=int((~fee_tagged).sum()), free_offstreet_lots=int(len(offstreet)),
        free_offstreet_lots_in_core=int(len(inside)),
        free_offstreet_lot_area_in_core_m2=round(float(inside.geometry.intersection(core_geometry).area.sum()), 1)
        if len(inside) else 0.0,
        side_class_pairs=_counts(segments["side_pair"], sorted(set(segments["side_pair"])))
        if "side_pair" in segments.columns else {},
        eroded_filled=filled.buffer(-parameters.walk_m) if not filled.is_empty else Polygon())
    log.info("%s core: R %.0f m2, fill(R) %.0f m2, Z %.0f m2 in %d parts; tagging completeness %s; F = %d ways with a "
             "free side (%d without any fee tag) + %d free street-side areas (%d without any fee tag); %d free "
             "off-street lots, %d of them inside Z (%.0f m2); decision %s %s", _LOG_TAG, result.regulated_area_m2,
             result.filled_area_m2, result.core_area_m2, len(core), result.tagging_completeness, result.free_segments,
             result.free_segments_without_fee_tag, result.free_street_side_areas,
             result.free_street_side_areas_without_fee_tag, result.free_offstreet_lots,
             result.free_offstreet_lots_in_core, result.free_offstreet_lot_area_in_core_m2,
             result.acceptance().decision, result.acceptance().reason)
    return result


def _core_parts_frame(filled: BaseGeometry, free: gpd.GeoDataFrame, parameters: ZoneCoreParameters) -> tuple:
    return _core_frame(_core_parts(filled, free, parameters.walk_m), parameters.minimum_island_m2)
