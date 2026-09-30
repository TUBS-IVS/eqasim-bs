"""Majority rule over the public parking supply within walking distance (parking cost zones v2, issue #436).

Spec Amendment B (approved by the owner on 2026-09-30, "A mit Vorbereitung C"; ADR-0139), all in EPSG:25832 metres.
It replaces the erosion rule of lever 1 (``braunschweig.parking.zone_geometry``, amendment A1), which yields no zone in
any of the eight curated towns: a place lies in a paid zone when most of the usable public parking within ``W`` costs
money.

* **B1 supply inventory** (``supply_elements``): every street side (``classify_supply_side``, the new and the legacy
  on-street parking scheme, each side on its own), every separately mapped street-side area and every off-street lot or
  garage (``classify_supply_object``; ``amenity=parking``, told apart by ``parking=*`` in
  ``zone_geometry.STREET_SIDE_LOT_TYPES``). Classes ``SUPPLY_CLASSES``: ``paid`` (a fee applies), ``restricted``
  (resident permit: ``access=permit``, resident-only parking ``access=private`` + ``private=residents`` on the side or
  the object, or the legacy condition residents), ``free`` (street parking with ``fee=no`` or without any fee tag,
  ASSUMPTION B-a, street parking
  only; disc parking is free, ASSUMPTION B-b; an off-street lot only with an explicit ``fee=no``, ruling T1b-a),
  ``excluded`` (not public: private or customers; forbidden; a side mapped separately, whose area is counted instead;
  an off-street lot without an explicit fee tag, ASSUMPTION B-c; a fee value that is neither yes nor no; no parking
  information). The column ``reason`` names the rule behind every class (``SUPPLY_REASONS``).
* **B2 capacity** (``capacity_spaces``, ``capacity_source``): the ``capacity`` tag (street sides:
  ``parking:<side>:capacity``, legacy ``parking:lane:<side>:capacity`` and ``parking:condition:<side>:capacity``, each
  falling back to ``both``, which applies to each side) when it is a whole number, else (ASSUMPTION B-d) the side length
  / ``STREET_SIDE_METRES_PER_SPACE`` (5.5 m), the street-side area / ``STREET_SIDE_AREA_M2_PER_SPACE`` (12.5 m2) and
  the lot area / ``LOT_M2_PER_SPACE`` (25 m2) times the tagged levels (``LEVEL_KEYS``) of a multi-storey car park. A
  street-side area drawn as a line counts as a street side (length / 5.5 m); a node without a capacity tag has no extent
  and carries 0 spaces (``capacity_basis`` ``no_extent``, counted). The heuristic is the fallback: its share of the
  usable capacity is logged per call and reported per town (``summarise_supply``).
* **Fallback transparency:** B-a (free street capacity without a fee tag), B-c (public off-street capacity excluded for
  lack of a fee tag) and B-d (heuristic capacity) are default-when-absent rules; ``log_fallback_rates`` logs the three
  rates of every inventory with the region it covers and warns when a rate exceeds its reporting threshold
  (``FREE_WITHOUT_FEE_TAG_WARNING_SHARE``, ``OFFSTREET_WITHOUT_FEE_TAG_WARNING_SHARE``,
  ``HEURISTIC_CAPACITY_WARNING_SHARE``: 0.5 each, a majority of the class resting on the fallback; not gates).
* **Interpretations beyond B-a..B-f** (implementation choices, not owner rulings; recorded in the data record
  ``parking_zones_2026``): ``parking:<side>=yes`` (the fallback value of the street parking scheme) is a parking
  position, so such a side is free street parking without a fee tag (the literal B-a reading, decisive for B5 in
  Braunschweig; ``YES_SIDES_COUNTERFACTUAL`` reads it as no parking information, as lever 1 does, a POST HOC
  counterfactual only); precedence, access values, fee values, capacity keys, levels and edge rules as documented at
  ``classify_supply_side``, ``classify_supply_object`` and B2 above.
* **B3 paid-share raster** (``paid_share_raster``): cells of ``cell_m`` (25 m) on the EPSG:25832 lattice of multiples
  of ``cell_m``, covering the bounds; for every cell centre the usable spaces (paid + restricted + free) within
  ``walk_m`` (250 m, Euclidean) and ``paid_share`` = (paid + restricted) / usable. A cell is classified when it has at
  least ``minimum_usable_spaces`` (50, ASSUMPTION B-e) usable spaces.
* **B4 zone rule** (``zone_polygons``): a classified cell with ``paid_share`` >= ``share_threshold`` (0.5, ASSUMPTION
  B-f) is paid; the paid cells are united and smoothed (buffer +``smoothing_m`` then -``smoothing_m``, 12.5 m, round
  joins: a morphological closing that fills gaps and holes up to one cell and rounds concave corners), parts below
  ``minimum_island_m2`` (1 ha, ASSUMPTION Q2) are dropped.
* **B5 validation** (``validation_metrics``, ``passes_validation``), pre-registered: recall = area(rule inside the
  ordinance polygons Ia and Ib) / area(Ia and Ib); precision = area(rule inside the annex zones Ia, Ib and II, which
  include the southern Ia) / area(rule inside the annex map frame); both >= ``VALIDATION_MINIMUM`` (70 %, ASSUMPTION
  Q5) with ``PRE_REGISTERED_SUPPLY_PARAMETERS``. The arms ``SENSITIVITY_ARMS`` are information only.
* **B7 release** (``release_frame``, ``PAID_SHARE_RELEASE_COLUMNS``): the classified cells, preparation of the
  probabilistic variant C; no stage reads them yet.
* **Owner decision 2** (2026-09-30, after B5 failed with the pre-registered share 0.5; a POST HOC change of B-f,
  ADR-0139): the default share threshold is ``DEFAULT_SHARE_THRESHOLD`` 0.3 (``DEFAULT_SUPPLY_PARAMETERS``,
  ``DEFAULT_ARM``); Amendment B's pre-registered 0.5 stays as ``PRE_REGISTERED_SUPPLY_PARAMETERS``, the centre of its
  B5 arms (``AMENDMENT_B_ARMS``). The rule may be applied only when H1 (B5 recomputed with the default parameters) and
  the holdout check H2, pre-registered before any holdout overlap at 0.3 was computed, both pass. H2
  (``holdout_town_metrics``, ``holdout_pooled_metrics``, ``passes_holdout``): the references
  ``HOLDOUT_REFERENCE_ZONES`` (not used by B5, no approximations); per town recall = area(rule inside the references) /
  area(references) and, for the four towns of ``HOLDOUT_PRECISION_TOWNS`` only, precision = area(rule inside the
  references) / area(rule inside the town's query box); pass when the pooled recall (area-weighted over every
  reference) and the pooled precision (over the four towns) reach ``HOLDOUT_POOLED_MINIMUM`` (0.70, the set value of
  Q5) and every town's recall reaches ``HOLDOUT_TOWN_RECALL_MINIMUM`` (0.50, ASSUMPTION Q6).
* **Variants S and T** (owner decisions 2 and 3; information arms, never applied): ``SupplyVariant`` and
  ``variant_elements`` on the B1 inventory. S (``STREET_SUPPLY_ONLY``, ``street_supply_only``): street sides and
  street-side areas only, off-street lots and garages leave the inventory. T (``PAYMENT_EVIDENCE``,
  ``apply_payment_evidence``; pre-registered before any T result): a street side or street-side area that is free only
  because it carries no fee tag (B-a) becomes paid when its geometry lies within ``PAYMENT_EVIDENCE_DISTANCE_M`` (75 m,
  ASSUMPTION T-a: about one block face served by one machine) of a parking ticket machine
  (``is_parking_ticket_machine``) or of a parking element (``is_parking_element``) with an app-payment tag
  (``app_payment_keys``); a street-side area or lot with an app-payment tag of its own and no fee tag is paid (payment
  implies a fee); an explicit fee=no is never overridden, disc parking (B-b) stays free and the evidence adds no
  capacity. Ruling R-T1c-a corrected the evidence definition of the controller before the task review (not a tuning
  step; T is information only): phone wallets (``PHONE_WALLET_PAYMENT_KEYS``) and non-parking elements (shops,
  charging stations) never count. Arms: ``VARIANT_ARMS`` (``SupplyArm``: S at 0.3 and 0.5, T at 0.5 and 0.3, S+T at
  0.5 and 0.3); only ``DEFAULT_ARM`` (B) may be applied.

Discretisation (B3): the capacity of every usable element is spread over points so that the radius sum is length- and
area-correct (``discretise_capacity``). A line is cut into ``ceil(length / step_m)`` equal pieces, each carrying its
share of the capacity at its midpoint; an area is cut by a ``step_m`` grid aligned to its bounds, each piece carrying
capacity x piece area / area at its centroid; a node carries all of it. ``step_m`` is ``DISCRETISATION_STEP_M`` (5 m):
a point stands for at most a 5 m piece and lies within 2.5 m (lines) or 3.6 m (areas) of every part of it, so a radius
sum differs from the exact length- or area-weighted sum only through supply within that distance of the circle. The
radius sums use a KD-tree (``scipy.spatial.cKDTree.query_ball_point``, distance <= ``walk_m``) in chunks of
``RASTER_QUERY_CHUNK_CELLS`` cells.

Edge supply: a cell near the edge of the bounds needs the supply up to ``walk_m`` beyond it; the caller passes elements
covering the bounds buffered by ``walk_m`` plus one cell (``scripts/build_parking_zones_from_osm.py --supply-share``).

Pure functions except the two readers of the committed outputs (``load_paid_share_release``,
``load_supply_share_qa``). GeoDataFrame inputs must be in EPSG:25832 (``ValueError`` otherwise); shapely geometries are
EPSG:25832 by contract. Package-private helpers of ``zone_geometry`` (tag parsing, CRS guard, polygon parts) and
``zones`` (documented CSV reader) are shared, not copied. Used by the curation aid, never a pipeline stage.
"""
from __future__ import annotations

import dataclasses
import gzip
import io
import itertools
import logging
import math
import re
import time
from pathlib import Path
from typing import Mapping, Optional

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
from scipy.spatial import cKDTree
from shapely.geometry.base import BaseGeometry

from braunschweig.parking import zone_geometry as zg
from braunschweig.parking import zones as pz

log = logging.getLogger(__name__)

_LOG_TAG = "[parking-supply]"
METRIC_CRS = zg.METRIC_CRS

#: Classes of a supply element (B1), in reporting order.
SUPPLY_CLASSES = ("paid", "restricted", "free", "excluded")
#: Classes that enter the usable supply of a cell (B3) ...
USABLE_CLASSES = ("paid", "restricted", "free")
#: ... and the classes that make it paid (B3: paid_share = (paid + restricted) / usable).
CHARGED_CLASSES = ("paid", "restricted")
CAPACITY_SOURCES = ("tag", "heuristic")
#: ``tag``; ``length``, ``area``, ``area_levels`` (B-d); ``no_extent`` (a node without a capacity tag: 0 spaces);
#: ``excluded`` (an excluded street side carries no capacity).
CAPACITY_BASES = ("tag", "length", "area", "area_levels", "no_extent", "excluded")
ELEMENT_KINDS = ("street_side", "street_side_area", "lot")
#: Rule behind every class (column ``reason``).
SUPPLY_REASONS = {
    "fee": "paid: fee=yes, a fee:conditional clause 'yes @ (...)' or the legacy condition ticket",
    "permit": "restricted: access=permit (a permit is needed)",
    "residents": "restricted: resident-only parking (access=private with private=residents, or the legacy condition "
                 "residents)",
    "disc": "free: disc parking without a fee (ASSUMPTION B-b)",
    "fee_no": "free: fee=no or the legacy condition free",
    "no_fee_tag": "free: street parking without any fee tag (ASSUMPTION B-a)",
    "not_public": "excluded: private, customers or another access limit (also on the whole way)",
    "forbidden": "excluded: no parking, no stopping",
    "separate": "excluded: the side's parking is mapped as its own street-side area, which is counted instead",
    "no_fee_tag_offstreet": "excluded: an off-street lot without an explicit fee tag (ASSUMPTION B-c)",
    "fee_unrecognised": "excluded: a fee value that is neither yes nor no",
    "no_parking_info": "excluded: no parking information",
    "payment_evidence": "paid (variant T only): street parking without a fee tag within 75 m of a parking ticket machine "
                        "or of an element with an app-payment tag (ASSUMPTION T-a)",
    "app_payment_tag": "paid (variant T only): a street-side area or lot with an app-payment tag of its own and no fee "
                       "tag (payment implies a fee)",
}
#: New-scheme ``parking:<side>`` values that place parking on the street: the lever-1 positions plus ``yes``, the
#: fallback value of the street parking scheme (parking exists, its position is unknown). Reading ``yes`` as street
#: parking is the literal B-a reading (interpretation beyond B-a..B-f, decisive for B5 in Braunschweig).
SUPPLY_PARKING_POSITIONS = zg.PARKING_POSITIONS + ("yes",)
#: POST HOC counterfactual (never a default, never a release input): ``parking:<side>=yes`` read as no parking
#: information, as lever 1 reads it (``zone_geometry.PARKING_POSITIONS``).
YES_SIDES_COUNTERFACTUAL = "yes_sides_no_information"
#: Legacy ``parking:condition:<side>`` values of parking that is not public (B1: private or customers).
NOT_PUBLIC_CONDITIONS = ("private", "customers")
#: ``private=*`` / ``parking:<side>:private=*`` value of resident-only parking (with ``access=private``): B1 names
#: resident permits restricted (fix round 1, ruling R-T1b-d); any other private value stays not public.
RESIDENT_PRIVATE_VALUES = ("residents",)
#: ASSUMPTION B-d: metres of street side per space, square metres per space of a street-side area and of a lot.
STREET_SIDE_METRES_PER_SPACE = 5.5
STREET_SIDE_AREA_M2_PER_SPACE = 12.5
LOT_M2_PER_SPACE = 25.0
#: ``parking=*`` of a multi-storey car park, whose tagged levels multiply the lot area (B2).
MULTI_STOREY_TYPES = ("multi-storey",)
#: Level tags of a multi-storey car park, the first parseable one (>= 1) wins.
LEVEL_KEYS = ("levels", "building:levels")
#: Spread of the capacity over points (see the module docstring), metres.
DISCRETISATION_STEP_M = 5.0
DEFAULT_CELL_M = 25.0
DEFAULT_WALK_M = zg.DEFAULT_WALK_M
DEFAULT_MINIMUM_USABLE_SPACES = 50.0
#: ASSUMPTION B-f as pre-registered in Amendment B (B5 failed with it: recall 0.696112, precision 0.852189) ...
PRE_REGISTERED_SHARE_THRESHOLD = 0.5
#: ... and the default since owner decision 2 (2026-09-30, a POST HOC change of B-f; ADR-0139).
DEFAULT_SHARE_THRESHOLD = 0.3
DEFAULT_SMOOTHING_M = 12.5
DEFAULT_MINIMUM_ISLAND_M2 = zg.DEFAULT_MINIMUM_ISLAND_M2
#: ASSUMPTION Q5: recall and precision needed before the rule may be applied (B5).
VALIDATION_MINIMUM = 0.70
#: Float noise of the discretised radius sums: a sum within this many spaces of the minimum counts as reaching it ...
SPACE_TOLERANCE = 1e-6
#: ... and a share within this distance of the threshold as reaching it (a share of exactly 0.5 is paid).
SHARE_TOLERANCE = 1e-9
#: Cells per KD-tree query (bounds the memory of the neighbour lists).
RASTER_QUERY_CHUNK_CELLS = 2048
#: Reporting thresholds of the fallback rates (not gates): above them a majority of the class rests on the fallback
#: and ``log_fallback_rates`` warns. B-d: heuristic share of the usable capacity; B-a: share of the free street capacity
#: without a fee tag; B-c: share of the public off-street capacity excluded because it carries no fee tag.
HEURISTIC_CAPACITY_WARNING_SHARE = 0.5
FREE_WITHOUT_FEE_TAG_WARNING_SHARE = 0.5
OFFSTREET_WITHOUT_FEE_TAG_WARNING_SHARE = 0.5
#: Variant T (owner decision 3, pre-registered before any T result): ASSUMPTION T-a, metres (EPSG:25832) between the
#: geometry of a street side or street-side area and the payment evidence.
PAYMENT_EVIDENCE_DISTANCE_M = 75.0
#: A parking ticket machine: a node with amenity=vending_machine whose vending=* lists parking_tickets.
TICKET_MACHINE_VENDING = "parking_tickets"
#: App-payment keys (ruling R-T1c-a): a key starting with payment:app or payment:mobile, or a named parking-app key (a
#: closed list: the three the ruling names; none occurs in the inventory box of the pinned extract), whose value is set
#: and not "no" ...
APP_PAYMENT_KEY_PREFIXES = ("payment:app", "payment:mobile", "payment:easypark", "payment:parkster",
                            "payment:paybyphone")
#: ... never a phone-wallet key, although payment:apple_pay starts with payment:app.
PHONE_WALLET_PAYMENT_KEYS = ("payment:apple_pay", "payment:google_pay", "payment:android_pay", "payment:samsung_pay",
                             "payment:garmin_pay", "payment:huawei_pay", "payment:fitbit_pay")
#: Evidence behind a conversion by variant T (column ``payment_evidence``), in the order of attribution: a ticket
#: machine within the distance, else an app-payment tag on a parking element; ``own_app_payment_tag`` for a street-side
#: area or lot paid by its own tag.
PAYMENT_EVIDENCE_SOURCES = ("ticket_machine", "app_payment_parking", "own_app_payment_tag")
#: Reasons of B1 that variant T may override: street parking free only for lack of a fee tag (B-a) by proximity; a
#: street-side area or lot without a fee tag (B-a, B-b disc area, B-c) by its own app-payment tag.
PROXIMITY_CONVERTIBLE_REASONS = ("no_fee_tag",)
OWN_TAG_CONVERTIBLE_REASONS = ("no_fee_tag", "disc", "no_fee_tag_offstreet")

ELEMENT_COLUMNS = ("element_id", "osm_type", "osm_id", "kind", "side", "side_position", "parking_type", "class",
                   "reason", "capacity_spaces", "capacity_source", "capacity_basis", "capacity_tag_invalid", "levels",
                   "app_payment_keys", "payment_evidence", "geometry")
EVIDENCE_COLUMNS = ("evidence_id", "osm_type", "osm_id", "evidence", "app_payment_keys", "object", "geometry")
RASTER_COLUMNS = ("x_m", "y_m", "paid_share", "usable_spaces", "heuristic_capacity_share", "classified", "paid_spaces",
                  "restricted_spaces", "free_spaces")
#: B7: the committed release of the classified cells.
PAID_SHARE_RELEASE_COLUMNS = ("x_m", "y_m", "municipality_ags", "paid_share", "usable_spaces",
                              "heuristic_capacity_share")

_WHOLE_NUMBER = re.compile(r"^\d+$")
_AGS = re.compile(r"^\d{8}$")


# --------------------------------------------------------------------------- B1: classification


def classify_supply_side(tags: Mapping, side: str, *, yes_position_is_parking: bool = True) -> tuple:
    """(class, reason) of one street side (``left`` or ``right``) under B1; tags of the new and the legacy scheme,
    ``<prefix>:<side>:*`` falling back to ``<prefix>:both:*`` (``zone_geometry._side_tag``).

    Order: the way not public -> mapped separately -> private or customers on the side (restricted when
    ``access=private`` comes with ``private=residents``, excluded otherwise) -> paid -> resident permit -> forbidden ->
    disc (free, B-b) -> any other access, restriction or condition (not public) -> a parking position with ``fee=no``
    or without a fee tag (free, B-a) -> a fee value that is no yes/no -> no parking information. ``yes_position_is_parking``
    False is the POST HOC counterfactual ``YES_SIDES_COUNTERFACTUAL`` (``parking:<side>=yes`` is no parking position).
    """
    if side not in zg.SIDES:
        raise ValueError(f"side must be one of {zg.SIDES}, got {side!r}")
    if not zg._way_is_public(tags):
        return "excluded", "not_public"
    position = zg._side_tag(tags, "parking", side)
    position = position.lower() if position else None
    fee = zg._tokens(zg._side_tag(tags, "parking", side, "fee"))
    access = zg._tokens(zg._side_tag(tags, "parking", side, "access"))
    restriction = zg._tokens(zg._side_tag(tags, "parking", side, "restriction"))
    disc = zg._tokens(zg._side_tag(tags, "parking", side, "authentication:disc"))
    lane = zg._tokens(zg._side_tag(tags, "parking:lane", side))
    condition = zg._tokens(zg._side_tag(tags, "parking:condition", side))

    if position == zg.SEPARATE_POSITION or zg.SEPARATE_POSITION in lane:
        return "excluded", "separate"
    if access & set(zg.NOT_PUBLIC_ACCESS) or condition & set(NOT_PUBLIC_CONDITIONS):
        private = zg._tokens(zg._side_tag(tags, "parking", side, "private"))
        if "private" in access and private & set(RESIDENT_PRIVATE_VALUES):
            return "restricted", "residents"
        return "excluded", "not_public"
    if "yes" in fee or zg._conditional_yes(zg._side_tag(tags, "parking", side, "fee:conditional")) \
            or "ticket" in condition:
        return "paid", "fee"
    if "permit" in access:
        return "restricted", "permit"
    if "residents" in condition:
        return "restricted", "residents"
    if (position in zg.NO_PARKING_POSITIONS or restriction & set(zg.FORBIDDEN_RESTRICTIONS) or "no" in access
            or lane & set(zg.LEGACY_NO_PARKING_LANE_TYPES) or condition & set(zg.FORBIDDEN_RESTRICTIONS)):
        return "excluded", "forbidden"
    if "yes" in disc or "disc" in condition:
        return "free", "disc"
    if access - set(zg.PUBLIC_ACCESS) or restriction - {"none"} or condition - {"free"}:
        return "excluded", "not_public"
    positions = SUPPLY_PARKING_POSITIONS if yes_position_is_parking else zg.PARKING_POSITIONS
    if not (position in positions or lane & set(zg.LEGACY_PARKING_LANE_TYPES)):
        return "excluded", "no_parking_info"
    if fee - {"no"}:
        return "excluded", "fee_unrecognised"
    return "free", "fee_no" if ("no" in fee or "free" in condition) else "no_fee_tag"


def is_street_side_object(tags: Mapping) -> bool:
    """An ``amenity=parking`` object whose ``parking=*`` places it on the street (``STREET_SIDE_LOT_TYPES``)."""
    return str(tags.get("parking") or "").strip().lower() in zg.STREET_SIDE_LOT_TYPES


def classify_supply_object(tags: Mapping) -> tuple:
    """(class, reason) of an ``amenity=parking`` object under B1 (a street-side area or an off-street lot).

    Order: private or customers (restricted when ``access=private`` comes with ``private=residents``, excluded
    otherwise) -> paid -> resident permit -> any other access limit (not public) -> a fee value that is no yes/no ->
    ``fee=no`` (free) -> no fee tag: a street-side area is free (disc: B-b, else B-a), an off-street lot is excluded
    (B-c).
    """
    if zg._tag_text(tags, "amenity") != "parking":
        return "excluded", "no_parking_info"
    access = zg._tokens(zg._tag_text(tags, "access"))
    fee = zg._tokens(zg._tag_text(tags, "fee"))
    if access & set(zg.NOT_PUBLIC_ACCESS):
        if "private" in access and zg._tokens(zg._tag_text(tags, "private")) & set(RESIDENT_PRIVATE_VALUES):
            return "restricted", "residents"
        return "excluded", "not_public"
    if "yes" in fee or zg._conditional_yes(zg._tag_text(tags, "fee:conditional")):
        return "paid", "fee"
    if "permit" in access:
        return "restricted", "permit"
    if access - set(zg.PUBLIC_ACCESS):
        return "excluded", "not_public"
    if fee - {"no"}:
        return "excluded", "fee_unrecognised"
    if "no" in fee:
        return "free", "fee_no"
    if not is_street_side_object(tags):
        return "excluded", "no_fee_tag_offstreet"
    if "yes" in zg._tokens(zg._tag_text(tags, "authentication:disc")):
        return "free", "disc"
    return "free", "no_fee_tag"


def split_parking_objects(objects: gpd.GeoDataFrame) -> tuple:
    """(street-side areas, off-street lots) of a frame of ``amenity=parking`` objects (``is_street_side_object``)."""
    zg._require_tags(objects, "parking objects")
    street_side = np.array([is_street_side_object(dict(tags or {})) for tags in objects["tags"]], dtype=bool)
    return objects[street_side], objects[~street_side]


# --------------------------------------------------------------------------- B2: capacity


def parse_capacity(value) -> Optional[int]:
    """A capacity tag as a whole number of spaces >= 0; None when absent or not a plain whole number."""
    if value is None:
        return None
    text = str(value).strip()
    return int(text) if _WHOLE_NUMBER.match(text) else None


def _side_capacity_text(tags: Mapping, side: str) -> Optional[str]:
    for prefix in ("parking", "parking:lane", "parking:condition"):
        value = zg._side_tag(tags, prefix, side, "capacity")
        if value is not None:
            return value
    return None


def _levels(tags: Mapping) -> Optional[float]:
    for key in LEVEL_KEYS:
        text = zg._tag_text(tags, key)
        try:
            value = float(text) if text is not None else math.nan
        except ValueError:
            continue
        if math.isfinite(value) and value >= 1.0:
            return value
    return None


def _heuristic_capacity(kind: str, geometry: BaseGeometry, tags: Mapping) -> tuple:
    """(spaces, basis, levels) of B-d for an element without a usable capacity tag."""
    dimension = shapely.get_dimensions(geometry)
    if kind == "street_side" or (kind == "street_side_area" and dimension == 1):
        return float(geometry.length) / STREET_SIDE_METRES_PER_SPACE, "length", math.nan
    if dimension != 2:
        return 0.0, "no_extent", math.nan
    if kind == "street_side_area":
        return float(geometry.area) / STREET_SIDE_AREA_M2_PER_SPACE, "area", math.nan
    spaces = float(geometry.area) / LOT_M2_PER_SPACE
    if str(tags.get("parking") or "").strip().lower() in MULTI_STOREY_TYPES:
        levels = _levels(tags)
        if levels is not None:
            return spaces * levels, "area_levels", levels
    return spaces, "area", math.nan


def _capacity(kind: str, geometry: BaseGeometry, tags: Mapping, side: Optional[str], usable: bool) -> dict:
    if kind == "street_side" and not usable:
        return {"capacity_spaces": 0.0, "capacity_source": "heuristic", "capacity_basis": "excluded",
                "capacity_tag_invalid": False, "levels": math.nan}
    text = _side_capacity_text(tags, side) if kind == "street_side" else zg._tag_text(tags, "capacity")
    tagged = parse_capacity(text)
    if tagged is not None:
        return {"capacity_spaces": float(tagged), "capacity_source": "tag", "capacity_basis": "tag",
                "capacity_tag_invalid": False, "levels": math.nan}
    spaces, basis, levels = _heuristic_capacity(kind, geometry, tags)
    return {"capacity_spaces": spaces, "capacity_source": "heuristic", "capacity_basis": basis,
            "capacity_tag_invalid": text is not None, "levels": levels}


# --------------------------------------------------------------------------- B1 + B2: the element frame


def _identity(row, position: int) -> tuple:
    osm_type = str(getattr(row, "osm_type", "") or "")
    osm_id = getattr(row, "osm_id", None)
    osm_id = int(osm_id) if osm_id is not None and not pd.isna(osm_id) else None
    element = f"{osm_type}/{osm_id}" if osm_id is not None else f"row/{position}"
    return osm_type, osm_id, element


def _check_input(frame: gpd.GeoDataFrame, what: str, dimensions) -> None:
    zg._require_metric(frame, what)
    zg._require_tags(frame, what)
    if len(frame):
        found = shapely.get_dimensions(np.asarray(frame.geometry.values))
        wrong = ~np.isin(found, dimensions) | frame.geometry.isna().to_numpy() | frame.geometry.is_empty.to_numpy()
        if wrong.any():
            raise ValueError(f"{what}: {int(wrong.sum())} element(s) without a usable geometry of dimension "
                             f"{list(dimensions)}")


def supply_elements(ways: gpd.GeoDataFrame, areas: gpd.GeoDataFrame, lots: gpd.GeoDataFrame, *,
                    label: str = "", region: str = "the elements passed",
                    yes_position_is_parking: bool = True) -> gpd.GeoDataFrame:
    """The supply inventory B1 with the capacity B2: one row per street side (two per way), street-side area and lot.

    ``ways``: street ways (lines) with a ``tags`` column; ``areas``: ``amenity=parking`` objects whose ``parking=*`` is
    a street-side type; ``lots``: the other ``amenity=parking`` objects (``split_parking_objects`` tells them apart; a
    misplaced object raises, because B-a and B-c differ). All EPSG:25832; optional ``osm_type`` / ``osm_id`` columns
    name the elements. Returns ``ELEMENT_COLUMNS`` (EPSG:25832; ``side_position`` is the new-scheme ``parking:<side>``
    value of a street side; ``app_payment_keys`` the app-payment keys of the element's own tags, of the way for a street
    side; ``payment_evidence`` stays empty until variant T). Logs the counts per kind and class and the fallback rates
    B-a, B-c and B-d (``log_fallback_rates``), naming ``region``. ``yes_position_is_parking`` False is the POST HOC
    counterfactual ``YES_SIDES_COUNTERFACTUAL``.
    """
    _check_input(ways, "street ways", (1,))
    _check_input(areas, "street-side areas", (0, 1, 2))
    _check_input(lots, "off-street lots", (0, 1, 2))
    misplaced = [str(tags.get("parking")) for tags in areas["tags"] if not is_street_side_object(dict(tags or {}))]
    if misplaced:
        raise ValueError(f"{len(misplaced)} object(s) passed as street-side areas are no street-side type "
                         f"{list(zg.STREET_SIDE_LOT_TYPES)}: parking={sorted(set(misplaced))}")
    misplaced = [str(tags.get("parking")) for tags in lots["tags"] if is_street_side_object(dict(tags or {}))]
    if misplaced:
        raise ValueError(f"{len(misplaced)} street-side object(s) passed as off-street lots: "
                         f"parking={sorted(set(misplaced))}")
    rows = []
    for position, row in enumerate(ways.itertuples(index=False)):
        tags = dict(row.tags or {})
        osm_type, osm_id, element = _identity(row, position)
        payment = ";".join(app_payment_keys(tags))
        for side in zg.SIDES:
            supply_class, reason = classify_supply_side(tags, side, yes_position_is_parking=yes_position_is_parking)
            capacity = _capacity("street_side", row.geometry, tags, side, supply_class in USABLE_CLASSES)
            side_position = (zg._side_tag(tags, "parking", side) or "").lower()
            rows.append({"element_id": f"{element}:{side}", "osm_type": osm_type, "osm_id": osm_id,
                         "kind": "street_side", "side": side, "side_position": side_position, "parking_type": "",
                         "class": supply_class, "reason": reason, **capacity, "app_payment_keys": payment,
                         "payment_evidence": "", "geometry": row.geometry})
    for kind, frame in (("street_side_area", areas), ("lot", lots)):
        for position, row in enumerate(frame.itertuples(index=False)):
            tags = dict(row.tags or {})
            osm_type, osm_id, element = _identity(row, position)
            supply_class, reason = classify_supply_object(tags)
            rows.append({"element_id": element, "osm_type": osm_type, "osm_id": osm_id, "kind": kind, "side": "",
                         "side_position": "", "parking_type": str(tags.get("parking") or ""), "class": supply_class,
                         "reason": reason, **_capacity(kind, row.geometry, tags, None, True),
                         "app_payment_keys": ";".join(app_payment_keys(tags)), "payment_evidence": "",
                         "geometry": row.geometry})
    elements = gpd.GeoDataFrame(rows, columns=list(ELEMENT_COLUMNS), geometry="geometry", crs=METRIC_CRS)
    elements["osm_id"] = elements["osm_id"].astype("Int64")
    elements["capacity_tag_invalid"] = elements["capacity_tag_invalid"].astype(bool)
    elements["capacity_spaces"] = elements["capacity_spaces"].astype(float)
    elements["levels"] = elements["levels"].astype(float)
    _log_supply(elements, label, region)
    return elements


def summarise_supply(elements: gpd.GeoDataFrame) -> dict:
    """QA numbers of a supply inventory (JSON-serialisable; NaN for an undefined share).

    Counts per kind and class and per kind and reason; usable spaces per class and per capacity source; the heuristic
    share of the usable capacity (the B-d fallback rate); the share of the free street spaces (street sides and
    street-side areas) that rests on a missing fee tag (B-a); the elements behind the other fallbacks.
    """
    zg._require_columns(elements, ELEMENT_COLUMNS, "supply elements (run supply_elements first)")
    usable = elements[elements["class"].isin(USABLE_CLASSES)]
    spaces = usable.groupby("class")["capacity_spaces"].sum()
    by_source = usable.groupby("capacity_source")["capacity_spaces"].sum()
    total = float(usable["capacity_spaces"].sum())
    free_street = usable[(usable["class"] == "free") & usable["kind"].isin(("street_side", "street_side_area"))]
    free_street_spaces = float(free_street["capacity_spaces"].sum())
    without_fee_tag = float(free_street.loc[free_street["reason"] == "no_fee_tag", "capacity_spaces"].sum())
    offstreet_without_fee = elements[elements["reason"] == "no_fee_tag_offstreet"]
    # public off-street lots: usable ones and those excluded only for their fee tag (B-c or an unrecognised value)
    lots = elements[elements["kind"] == "lot"]
    public_lots = lots[lots["class"].isin(USABLE_CLASSES) | lots["reason"].isin(("no_fee_tag_offstreet",
                                                                                  "fee_unrecognised"))]
    public_lot_spaces = float(public_lots["capacity_spaces"].sum())
    offstreet_without_fee_spaces = float(offstreet_without_fee["capacity_spaces"].sum())
    yes_sides = free_street[(free_street["kind"] == "street_side") & (free_street["side_position"] == "yes")]
    multi_storey = elements[(elements["kind"] == "lot") & elements["parking_type"].isin(MULTI_STOREY_TYPES)]

    def reasons_by_kind():
        return {kind: {reason: int(count) for reason, count in
                       elements.loc[elements["kind"] == kind, "reason"].value_counts().sort_index().items()}
                for kind in ELEMENT_KINDS if (elements["kind"] == kind).any()}

    by_class = elements["class"].value_counts()
    return {
        "elements": int(len(elements)),
        "elements_by_class": {name: int(by_class.get(name, 0)) for name in SUPPLY_CLASSES},
        "elements_by_kind_and_class": {kind: {name: int(((elements["kind"] == kind) & (elements["class"] == name))
                                                        .sum()) for name in SUPPLY_CLASSES}
                                       for kind in ELEMENT_KINDS},
        "elements_by_kind_and_reason": reasons_by_kind(),
        "usable_spaces": round(total, 2),
        "spaces_by_class": {name: round(float(spaces.get(name, 0.0)), 2) for name in USABLE_CLASSES},
        "tagged_spaces": round(float(by_source.get("tag", 0.0)), 2),
        "heuristic_spaces": round(float(by_source.get("heuristic", 0.0)), 2),
        "heuristic_capacity_share": float(by_source.get("heuristic", 0.0)) / total if total > 0 else math.nan,
        "free_street_spaces": round(free_street_spaces, 2),
        "free_street_spaces_without_fee_tag": round(without_fee_tag, 2),
        "free_street_spaces_without_fee_tag_share": without_fee_tag / free_street_spaces if free_street_spaces > 0
        else math.nan,
        "usable_elements_without_extent": int((usable["capacity_basis"] == "no_extent").sum()),
        "capacity_tags_invalid": int(elements["capacity_tag_invalid"].sum()),
        "multi_storey_lots": int(len(multi_storey)),
        "multi_storey_lots_without_levels": int((multi_storey["capacity_basis"] == "area").sum()),
        "offstreet_lots_without_fee_tag": int(len(offstreet_without_fee)),
        "offstreet_lot_spaces_without_fee_tag": round(offstreet_without_fee_spaces, 2),
        "offstreet_public_lots": int(len(public_lots)),
        "offstreet_public_lot_spaces": round(public_lot_spaces, 2),
        "offstreet_without_fee_tag_share": len(offstreet_without_fee) / len(public_lots) if len(public_lots)
        else math.nan,
        "offstreet_spaces_without_fee_tag_share": offstreet_without_fee_spaces / public_lot_spaces
        if public_lot_spaces > 0 else math.nan,
        "free_street_sides_position_yes": int(len(yes_sides)),
        "free_street_side_spaces_position_yes": round(float(yes_sides["capacity_spaces"].sum()), 2),
    }


def _share_text(value) -> str:
    return "undefined" if value is None or not math.isfinite(value) else f"{100.0 * value:.1f} %"


def log_fallback_rates(summary: Mapping, *, label: str = "", region: str) -> None:
    """Log the three default-when-absent rates of an inventory (``summarise_supply``) in one line naming ``region``,
    and warn for every rate above its reporting threshold: B-a the free street capacity without a fee tag, B-c the
    public off-street capacity excluded for lack of a fee tag, B-d the heuristic capacity (primary: the tag)."""
    prefix = f"{label}, {region}" if label else region
    rates = (("B-a", "of the free street capacity rests on a missing fee tag (free by assumption)",
              summary["free_street_spaces_without_fee_tag_share"], FREE_WITHOUT_FEE_TAG_WARNING_SHARE),
             ("B-c", "of the public off-street capacity is excluded because it carries no fee tag",
              summary["offstreet_spaces_without_fee_tag_share"], OFFSTREET_WITHOUT_FEE_TAG_WARNING_SHARE),
             ("B-d", "of the usable capacity comes from the capacity heuristic (primary: the capacity tag)",
              summary["heuristic_capacity_share"], HEURISTIC_CAPACITY_WARNING_SHARE))
    log.info("%s %s: fallback rates B-a %s (%.0f of %.0f free street spaces without a fee tag), B-c %s (%d of %d public "
             "off-street lots, %.0f of %.0f spaces, without a fee tag), B-d %s (%.0f of %.0f usable spaces heuristic)",
             _LOG_TAG, prefix, _share_text(rates[0][2]), summary["free_street_spaces_without_fee_tag"],
             summary["free_street_spaces"], _share_text(rates[1][2]), summary["offstreet_lots_without_fee_tag"],
             summary["offstreet_public_lots"], summary["offstreet_lot_spaces_without_fee_tag"],
             summary["offstreet_public_lot_spaces"], _share_text(rates[2][2]), summary["heuristic_spaces"],
             summary["usable_spaces"])
    for name, what, value, threshold in rates:
        if value is not None and math.isfinite(value) and value > threshold:
            log.warning("%s %s: %s %s %s (above the reporting threshold %.0f %%)", _LOG_TAG, prefix, name,
                        _share_text(value), what, 100.0 * threshold)


def _log_supply(elements: gpd.GeoDataFrame, label: str, region: str) -> None:
    summary = summarise_supply(elements)
    counts = "; ".join(f"{kind} {', '.join(f'{name} {count}' for name, count in classes.items() if count)}"
                       for kind, classes in summary["elements_by_kind_and_class"].items() if any(classes.values()))
    log.info("%s %s%s: %d elements (%s); usable capacity %.0f spaces (%s): tagged %.0f, heuristic %.0f; %d usable nodes "
             "without a capacity tag carry 0 spaces; %d capacity tags not a whole number; %d of %d multi-storey car "
             "parks without a levels tag count one level", _LOG_TAG, f"{label}, " if label else "", region,
             summary["elements"], counts or "none", summary["usable_spaces"], summary["spaces_by_class"],
             summary["tagged_spaces"], summary["heuristic_spaces"], summary["usable_elements_without_extent"],
             summary["capacity_tags_invalid"], summary["multi_storey_lots_without_levels"],
             summary["multi_storey_lots"])
    log_fallback_rates(summary, label=label, region=region)


# --------------------------------------------------------------------------- variants S and T (owner decisions 2, 3)


def is_parking_ticket_machine(tags: Mapping) -> bool:
    """A parking ticket machine of variant T: ``amenity=vending_machine`` whose ``vending=*`` lists
    ``TICKET_MACHINE_VENDING`` (possibly among other goods); ``payment_evidence`` keeps nodes only."""
    return (zg._tag_text(tags, "amenity") or "").lower() == "vending_machine" and \
        TICKET_MACHINE_VENDING in zg._tokens(zg._tag_text(tags, "vending"))


def _set_payment_keys(tags: Mapping, prefixes) -> tuple:
    """Sorted keys starting with one of ``prefixes`` whose value is set and not ``no``."""
    keys = []
    for key, value in tags.items():
        text = "" if value is None else str(value).strip().lower()
        if str(key).startswith(tuple(prefixes)) and text and text != "no":
            keys.append(str(key))
    return tuple(sorted(keys))


def app_payment_keys(tags: Mapping) -> tuple:
    """The app-payment keys of an element (ruling R-T1c-a), sorted: keys starting with one of
    ``APP_PAYMENT_KEY_PREFIXES`` whose value is set and not ``no``, never a phone-wallet key
    (``PHONE_WALLET_PAYMENT_KEYS``)."""
    return tuple(key for key in _set_payment_keys(tags, APP_PAYMENT_KEY_PREFIXES)
                 if not key.startswith(PHONE_WALLET_PAYMENT_KEYS))


def is_parking_element(tags: Mapping) -> bool:
    """A parking element of ruling R-T1c-a, the only carrier of app-payment evidence: a parking facility
    (``amenity=parking`` of any geometry, i.e. street-side areas, lots and nodes; a highway way with street-parking tags
    ``parking:*``) or a parking payment device (``vending=*`` lists ``parking_tickets``). Shops, charging stations and
    every other element are none."""
    if (zg._tag_text(tags, "amenity") or "").lower() == "parking":
        return True
    if zg._tag_text(tags, "highway") and any(str(key).startswith("parking:") for key in tags):
        return True
    return TICKET_MACHINE_VENDING in zg._tokens(zg._tag_text(tags, "vending"))


def _object_label(tags: Mapping) -> str:
    for key in ("amenity", "shop", "highway", "leisure", "tourism", "office", "craft", "building"):
        value = zg._tag_text(tags, key)
        if value:
            return f"{key}={value}"
    return "other"


def payment_evidence(objects: gpd.GeoDataFrame, *, label: str = "") -> gpd.GeoDataFrame:
    """The payment evidence of variant T among OSM elements (``tags`` column, any geometry, EPSG:25832; optional
    ``osm_type`` / ``osm_id``): every parking ticket machine that is a node (``is_parking_ticket_machine``; owner
    decision 3) and every parking element (``is_parking_element``) with an app-payment tag (``app_payment_keys``),
    whatever its own parking class (ruling R-T1c-a).

    Returns ``EVIDENCE_COLUMNS``: ``evidence`` is ``ticket_machine``, ``app_payment`` or both (';'), ``object`` names
    the element by its main tag. Logs the counts and what the definition leaves out: ticket machines that are no node,
    elements with an app-payment tag that are no parking element (shops, charging stations) and elements whose only
    payment keys of that kind are phone wallets.
    """
    zg._require_metric(objects, "payment evidence candidates")
    zg._require_tags(objects, "payment evidence candidates")
    rows, not_nodes, not_parking, wallet_only, without_geometry = [], 0, 0, 0, 0
    for position, row in enumerate(objects.itertuples(index=False)):
        tags = dict(row.tags or {})
        osm_type, osm_id, element = _identity(row, position)
        machine = is_parking_ticket_machine(tags)
        if machine and osm_type and osm_type != "node":
            not_nodes += 1
            machine = False
        keys = app_payment_keys(tags)
        if not keys and _set_payment_keys(tags, PHONE_WALLET_PAYMENT_KEYS):
            wallet_only += 1
        app = bool(keys) and is_parking_element(tags)
        if keys and not app:
            not_parking += 1
        if not (machine or app):
            continue
        if row.geometry is None or row.geometry.is_empty:
            without_geometry += 1
            continue
        evidence = ";".join(name for name, flag in (("ticket_machine", machine), ("app_payment", app)) if flag)
        rows.append({"evidence_id": element, "osm_type": osm_type, "osm_id": osm_id, "evidence": evidence,
                     "app_payment_keys": ";".join(keys) if app else "", "object": _object_label(tags),
                     "geometry": row.geometry})
    frame = gpd.GeoDataFrame(rows, columns=list(EVIDENCE_COLUMNS), geometry="geometry", crs=METRIC_CRS)
    frame["osm_id"] = frame["osm_id"].astype("Int64")
    summary = summarise_payment_evidence(frame)["evidence"]
    log.info("%s %spayment evidence of variant T (ruling R-T1c-a): %d parking ticket machines (nodes), %d parking "
             "elements with an app-payment tag; left out: %d elements with an app-payment tag that are no parking "
             "element, %d elements with a phone-wallet key only, %d ticket machines that are no node, %d elements "
             "without geometry", _LOG_TAG, f"{label}: " if label else "", summary["ticket_machines"],
             summary["app_payment_elements"], not_parking, wallet_only, not_nodes, without_geometry)
    return frame


def summarise_payment_evidence(evidence: gpd.GeoDataFrame, elements: Optional[gpd.GeoDataFrame] = None,
                               distance_m: Optional[float] = None) -> dict:
    """QA numbers of variant T (JSON-serialisable): the evidence (``payment_evidence``: ticket machines, parking
    elements with an app-payment tag) and, for an inventory after ``apply_payment_evidence`` (``elements`` with
    ``distance_m``), the elements it turned paid per kind, their spaces and the evidence behind them
    (``PAYMENT_EVIDENCE_SOURCES``); the conversion keys are None otherwise."""
    zg._require_columns(evidence, EVIDENCE_COLUMNS, "payment evidence (run payment_evidence first)")
    machines = evidence["evidence"].str.contains("ticket_machine", regex=False).to_numpy(dtype=bool)
    app = evidence["evidence"].str.contains("app_payment", regex=False).to_numpy(dtype=bool)
    summary = {"distance_m": None if distance_m is None else float(distance_m),
               "evidence": {"ticket_machines": int(machines.sum()), "app_payment_elements": int(app.sum())},
               "converted": None, "converted_spaces": None, "converted_by": None}
    if elements is not None and distance_m is not None:
        zg._require_columns(elements, ELEMENT_COLUMNS, "supply elements (run supply_elements first)")
        converted = elements[elements["reason"].isin(("payment_evidence", "app_payment_tag"))]
        summary["converted"] = {kind: int((converted["kind"] == kind).sum()) for kind in ELEMENT_KINDS}
        summary["converted_spaces"] = round(float(converted["capacity_spaces"].sum()), 2)
        summary["converted_by"] = {source: int((converted["payment_evidence"] == source).sum())
                                   for source in PAYMENT_EVIDENCE_SOURCES}
    return summary


def apply_payment_evidence(elements: gpd.GeoDataFrame, evidence: gpd.GeoDataFrame,
                           distance_m: float = PAYMENT_EVIDENCE_DISTANCE_M, *, label: str = "",
                           region: str = "the elements passed") -> gpd.GeoDataFrame:
    """Variant T (owner decision 3, pre-registered before any T result) on a B1 inventory; returns a copy.

    A street side or street-side area free only for lack of a fee tag (reason ``no_fee_tag``, B-a) becomes ``paid``
    (reason ``payment_evidence``) when its geometry, not only its centroid, lies within ``distance_m`` (EPSG:25832,
    ``dwithin``) of any evidence element (``payment_evidence``: ticket machines and parking elements with an app-payment
    tag); ``payment_evidence`` names the evidence in the order of ``PAYMENT_EVIDENCE_SOURCES`` (a ticket machine
    first). A street-side area or lot with an app-payment tag of its own
    and no fee tag (reasons ``OWN_TAG_CONVERTIBLE_REASONS``: B-a, a disc area, B-c) becomes ``paid`` (reason
    ``app_payment_tag``). Never overridden: an explicit ``fee=no``, disc street sides (B-b), charged, not public or
    forbidden elements. The evidence adds no element and no capacity. Logs the conversions as a share of the elements
    each rule may convert, naming ``region``.
    """
    zg._require_columns(elements, ELEMENT_COLUMNS, "supply elements (run supply_elements first)")
    zg._require_metric(elements, "supply elements")
    zg._require_columns(evidence, EVIDENCE_COLUMNS, "payment evidence (run payment_evidence first)")
    zg._require_metric(evidence, "payment evidence")
    if not (isinstance(distance_m, (int, float)) and math.isfinite(distance_m) and distance_m > 0):
        raise ValueError(f"distance_m must be a distance > 0 in metres, got {distance_m!r}")
    result = elements.copy()
    kinds, reasons = result["kind"].to_numpy(dtype=object), result["reason"].to_numpy(dtype=object)
    own = (np.isin(kinds, ("street_side_area", "lot")) & np.isin(reasons, OWN_TAG_CONVERTIBLE_REASONS)
           & (result["app_payment_keys"].to_numpy(dtype=object) != ""))
    candidates = np.isin(kinds, ("street_side", "street_side_area")) & np.isin(reasons, PROXIMITY_CONVERTIBLE_REASONS)
    candidates &= ~own
    source = np.full(len(result), "", dtype=object)
    source[own] = "own_app_payment_tag"
    near = np.zeros(len(result), dtype=bool)
    positions = np.flatnonzero(candidates)
    if len(positions) and len(evidence):
        # rank of every evidence element in the attribution order: 0 ticket machine, 1 app payment on a parking element
        machine = evidence["evidence"].str.contains("ticket_machine", regex=False).to_numpy(dtype=bool)
        rank = np.where(machine, 0, 1)
        tree = shapely.STRtree(np.asarray(evidence.geometry.values))
        pairs = tree.query(np.asarray(result.geometry.values)[positions], predicate="dwithin", distance=distance_m)
        best = np.full(len(positions), 2)
        np.minimum.at(best, pairs[0], rank[pairs[1]])
        hit = best < 2
        near[positions[hit]] = True
        source[positions[hit]] = np.asarray(PAYMENT_EVIDENCE_SOURCES)[best[hit]]
    converted = own | near
    result.loc[converted, "class"] = "paid"
    result.loc[own, "reason"] = "app_payment_tag"
    result.loc[near, "reason"] = "payment_evidence"
    result["payment_evidence"] = source
    spaces = result["capacity_spaces"].to_numpy(dtype=float)
    own_possible = (np.isin(kinds, ("street_side_area", "lot")) & np.isin(reasons, OWN_TAG_CONVERTIBLE_REASONS))
    log.info("%s %s%s: variant T within %.0f m: %d of %d street sides and street-side areas free only for lack of a fee "
             "tag became paid (%s; %.0f of %.0f spaces), %d of %d street-side areas and lots without a fee tag by their "
             "own app-payment tag (%.0f spaces); by ticket machine %d, by an app-payment tag on a parking element %d",
             _LOG_TAG, f"{label}, " if label else "", region, distance_m, int(near.sum()), int(candidates.sum()),
             _share_text(near.sum() / candidates.sum() if candidates.sum() else math.nan), float(spaces[near].sum()),
             float(spaces[candidates].sum()), int(own.sum()), int(own_possible.sum()), float(spaces[own].sum()),
             int((source == "ticket_machine").sum()), int((source == "app_payment_parking").sum()))
    return result


def street_supply_only(elements: gpd.GeoDataFrame, *, label: str = "",
                       region: str = "the elements passed") -> gpd.GeoDataFrame:
    """Variant S (owner decision 2): the inventory without its off-street lots and garages (kind ``lot``), i.e. the
    street sides and the separately mapped street-side areas; a copy with a fresh index. Logs what leaves."""
    zg._require_columns(elements, ELEMENT_COLUMNS, "supply elements (run supply_elements first)")
    lots = (elements["kind"] == "lot").to_numpy()
    usable = elements["class"].isin(USABLE_CLASSES).to_numpy()
    log.info("%s %s%s: variant S keeps %d street sides and street-side areas and drops %d off-street lots and garages "
             "(%d usable, %.0f usable spaces)", _LOG_TAG, f"{label}, " if label else "", region, int((~lots).sum()),
             int(lots.sum()), int((lots & usable).sum()),
             float(elements.loc[lots & usable, "capacity_spaces"].sum()))
    return elements[~lots].reset_index(drop=True)


def variant_elements(elements: gpd.GeoDataFrame, variant, evidence: Optional[gpd.GeoDataFrame] = None, *,
                     label: str = "", region: str = "the elements passed") -> gpd.GeoDataFrame:
    """The inventory of ``variant`` (``SupplyVariant``) from the B1 inventory of ``supply_elements``: unchanged for the
    full inventory (B), ``street_supply_only`` for S, ``apply_payment_evidence`` with ``evidence`` for T, S first for
    S+T. ``ValueError`` when T lacks its evidence."""
    if not isinstance(variant, SupplyVariant):
        raise TypeError(f"variant must be a SupplyVariant, got {type(variant).__name__}")
    result = elements
    if variant.street_supply_only:
        result = street_supply_only(result, label=label, region=region)
    if variant.payment_evidence_m is not None:
        if evidence is None:
            raise ValueError("variant T needs the payment evidence (supply_share.payment_evidence of the inventory)")
        result = apply_payment_evidence(result, evidence, variant.payment_evidence_m, label=label, region=region)
    return result


# --------------------------------------------------------------------------- B3: discretisation and raster


def _spread(geometry: BaseGeometry, step_m: float) -> tuple:
    """(x, y, weight) arrays: points standing for ``geometry`` whose weights sum to 1 (see the module docstring)."""
    kind = geometry.geom_type
    if kind == "Point":
        return np.array([geometry.x]), np.array([geometry.y]), np.array([1.0])
    if kind == "MultiPoint":
        coordinates = shapely.get_coordinates(geometry)
        return coordinates[:, 0], coordinates[:, 1], np.full(len(coordinates), 1.0 / len(coordinates))
    if kind in ("LineString", "LinearRing", "MultiLineString"):
        parts = list(getattr(geometry, "geoms", [geometry]))
        total = sum(part.length for part in parts)
        if total <= 0:
            point = geometry.representative_point()
            return np.array([point.x]), np.array([point.y]), np.array([1.0])
        xs, ys, weights = [], [], []
        for part in parts:
            if part.length <= 0:
                continue
            count = max(1, int(math.ceil(part.length / step_m)))
            points = shapely.line_interpolate_point(part, (np.arange(count) + 0.5) * part.length / count)
            coordinates = shapely.get_coordinates(points)
            xs.append(coordinates[:, 0])
            ys.append(coordinates[:, 1])
            weights.append(np.full(count, part.length / total / count))
        return np.concatenate(xs), np.concatenate(ys), np.concatenate(weights)
    if kind in ("Polygon", "MultiPolygon"):
        area = geometry.area
        if area <= 0:
            point = geometry.representative_point()
            return np.array([point.x]), np.array([point.y]), np.array([1.0])
        minx, miny, maxx, maxy = geometry.bounds
        columns = max(1, int(math.ceil((maxx - minx) / step_m)))
        rows = max(1, int(math.ceil((maxy - miny) / step_m)))
        x0 = minx + step_m * np.arange(columns)
        y0 = miny + step_m * np.arange(rows)
        grid_x, grid_y = np.meshgrid(x0, y0)
        cells = shapely.box(grid_x.ravel(), grid_y.ravel(), grid_x.ravel() + step_m, grid_y.ravel() + step_m)
        shapely.prepare(geometry)
        inside = shapely.contains(geometry, cells)
        touching = ~inside & shapely.intersects(geometry, cells)
        pieces = shapely.intersection(cells[touching], geometry)
        piece_areas = shapely.area(pieces)
        keep = piece_areas > 0
        centres = shapely.get_coordinates(shapely.centroid(cells[inside]))
        cut = shapely.get_coordinates(shapely.centroid(pieces[keep]))
        xs = np.concatenate([centres[:, 0], cut[:, 0]])
        ys = np.concatenate([centres[:, 1], cut[:, 1]])
        weights = np.concatenate([shapely.area(cells[inside]), piece_areas[keep]]) / area
        return xs, ys, weights
    raise ValueError(f"cannot spread capacity over a {kind}")


def discretise_capacity(elements: gpd.GeoDataFrame, step_m: float = DISCRETISATION_STEP_M) -> pd.DataFrame:
    """Points carrying the capacity of the usable elements (classes ``USABLE_CLASSES``, capacity > 0): ``x_m``,
    ``y_m``, ``spaces``, ``class``, ``heuristic`` (capacity from B-d); the spaces of every element are preserved."""
    zg._require_metric(elements, "supply elements")
    zg._require_columns(elements, ("class", "capacity_spaces", "capacity_source"), "supply elements")
    zg._require_non_negative(step_m=step_m)
    if step_m <= 0:
        raise ValueError(f"step_m must be > 0, got {step_m}")
    usable = elements[elements["class"].isin(USABLE_CLASSES) & (elements["capacity_spaces"] > 0)]
    parts = []
    for geometry, spaces, supply_class, source in zip(usable.geometry, usable["capacity_spaces"], usable["class"],
                                                      usable["capacity_source"]):
        xs, ys, weights = _spread(geometry, step_m)
        parts.append(pd.DataFrame({"x_m": xs, "y_m": ys, "spaces": weights * float(spaces), "class": supply_class,
                                   "heuristic": source == "heuristic"}))
    if not parts:
        return pd.DataFrame({"x_m": pd.Series(dtype=float), "y_m": pd.Series(dtype=float),
                             "spaces": pd.Series(dtype=float), "class": pd.Series(dtype=object),
                             "heuristic": pd.Series(dtype=bool)})
    return pd.concat(parts, ignore_index=True)


def cell_centres(bounds, cell_m: float = DEFAULT_CELL_M) -> tuple:
    """(x, y) centre arrays of the ``cell_m`` lattice cells (multiples of ``cell_m`` in EPSG:25832) that cover the
    ``(minx, miny, maxx, maxy)`` bounds."""
    minx, miny, maxx, maxy = (float(value) for value in bounds)
    if not all(math.isfinite(value) for value in (minx, miny, maxx, maxy)) or not (minx < maxx and miny < maxy):
        raise ValueError(f"bounds must be finite (minx, miny, maxx, maxy) with minx < maxx and miny < maxy, got "
                         f"{bounds}")
    if not (math.isfinite(cell_m) and cell_m > 0):
        raise ValueError(f"cell_m must be > 0, got {cell_m}")
    first_x, last_x = math.floor(minx / cell_m), math.ceil(maxx / cell_m)
    first_y, last_y = math.floor(miny / cell_m), math.ceil(maxy / cell_m)
    xs = (np.arange(first_x, last_x) + 0.5) * cell_m
    ys = (np.arange(first_y, last_y) + 0.5) * cell_m
    return xs, ys


def _radius_sums(centres: np.ndarray, points: pd.DataFrame, walk_m: float) -> dict:
    """Per cell centre the sums of the point weights within ``walk_m`` (KD-tree, chunked)."""
    weights = {"usable": points["spaces"].to_numpy(dtype=float)}
    for name in USABLE_CLASSES:
        weights[name] = np.where(points["class"].to_numpy() == name, weights["usable"], 0.0)
    weights["heuristic"] = np.where(points["heuristic"].to_numpy(dtype=bool), weights["usable"], 0.0)
    sums = {name: np.zeros(len(centres)) for name in weights}
    if not len(points) or not len(centres):
        return sums
    tree = cKDTree(points[["x_m", "y_m"]].to_numpy(dtype=float))
    for start in range(0, len(centres), RASTER_QUERY_CHUNK_CELLS):
        chunk = centres[start:start + RASTER_QUERY_CHUNK_CELLS]
        neighbours = tree.query_ball_point(chunk, r=walk_m, workers=-1)
        lengths = np.fromiter((len(members) for members in neighbours), dtype=np.int64, count=len(neighbours))
        flat = np.fromiter(itertools.chain.from_iterable(neighbours), dtype=np.int64, count=int(lengths.sum()))
        owners = np.repeat(np.arange(len(chunk)), lengths)
        for name, values in weights.items():
            sums[name][start:start + len(chunk)] = np.bincount(owners, weights=values[flat], minlength=len(chunk))
    return sums


def paid_share_raster(elements: gpd.GeoDataFrame, bounds, cell_m: float = DEFAULT_CELL_M,
                      walk_m: float = DEFAULT_WALK_M, minimum_usable_spaces: float = DEFAULT_MINIMUM_USABLE_SPACES, *,
                      step_m: float = DISCRETISATION_STEP_M, label: str = "") -> pd.DataFrame:
    """The paid-share raster B3 over ``bounds`` (EPSG:25832 metres).

    One row per cell of ``cell_centres(bounds, cell_m)``, ordered by ``y_m`` then ``x_m``: ``RASTER_COLUMNS``, where
    ``usable_spaces`` and the spaces per class are the capacity within ``walk_m`` of the cell centre, ``paid_share`` =
    (paid + restricted) / usable (NaN without usable supply), ``heuristic_capacity_share`` the part of the usable spaces
    that comes from the B-d heuristic and ``classified`` = usable >= ``minimum_usable_spaces`` (``SPACE_TOLERANCE``).
    ``attrs`` carries the parameters. The elements must cover the bounds buffered by ``walk_m`` (edge cells otherwise
    undercount). Logs the classified and the unclassified cell share.
    """
    zg._require_non_negative(walk_m=walk_m, minimum_usable_spaces=minimum_usable_spaces)
    if walk_m <= 0:
        raise ValueError(f"walk_m must be > 0, got {walk_m}")
    started = time.perf_counter()
    points = discretise_capacity(elements, step_m)
    xs, ys = cell_centres(bounds, cell_m)
    grid_x, grid_y = np.meshgrid(xs, ys)
    centres = np.column_stack([grid_x.ravel(), grid_y.ravel()])
    sums = _radius_sums(centres, points, walk_m)
    usable = sums["usable"]
    charged = sum(sums[name] for name in CHARGED_CLASSES)
    with np.errstate(invalid="ignore", divide="ignore"):
        paid_share = np.where(usable > 0, charged / usable, np.nan)
        heuristic_share = np.where(usable > 0, sums["heuristic"] / usable, np.nan)
    raster = pd.DataFrame({"x_m": centres[:, 0], "y_m": centres[:, 1], "paid_share": np.clip(paid_share, 0.0, 1.0),
                           "usable_spaces": usable, "heuristic_capacity_share": np.clip(heuristic_share, 0.0, 1.0),
                           "classified": usable >= minimum_usable_spaces - SPACE_TOLERANCE,
                           "paid_spaces": sums["paid"], "restricted_spaces": sums["restricted"],
                           "free_spaces": sums["free"]}, columns=list(RASTER_COLUMNS))
    raster.attrs.update({"cell_m": float(cell_m), "walk_m": float(walk_m),
                         "minimum_usable_spaces": float(minimum_usable_spaces), "step_m": float(step_m)})
    classified = int(raster["classified"].sum())
    share = classified / len(raster) if len(raster) else math.nan
    log.info("%s %sraster of %d cells of %.0f m, W %.0f m, from %d supply points: classified %d (%.1f %%), "
             "unclassified %d (%.1f %%) below %.0f usable spaces; %.1f s", _LOG_TAG, f"{label}: " if label else "",
             len(raster), cell_m, walk_m, len(points), classified, 100.0 * share, len(raster) - classified,
             100.0 * (1.0 - share), minimum_usable_spaces, time.perf_counter() - started)
    if classified == 0:
        log.warning("%s %sno cell reaches %.0f usable spaces within %.0f m: the raster is entirely unclassified",
                    _LOG_TAG, f"{label}: " if label else "", minimum_usable_spaces, walk_m)
    return raster


def paid_cells(raster: pd.DataFrame, share_threshold: float = DEFAULT_SHARE_THRESHOLD) -> pd.Series:
    """B4: classified cells with ``paid_share`` >= ``share_threshold`` (a share of exactly the threshold is paid)."""
    zg._require_columns(raster, ("paid_share", "classified"), "raster (run paid_share_raster first)")
    classified = raster["classified"].to_numpy(dtype=bool)
    share = np.nan_to_num(raster["paid_share"].to_numpy(dtype=float), nan=-1.0)
    return pd.Series(classified & (share >= share_threshold - SHARE_TOLERANCE), index=raster.index, dtype=bool)


# --------------------------------------------------------------------------- B4: zone polygons


def _cell_size(raster: pd.DataFrame, cell_m: Optional[float]) -> float:
    if cell_m is not None:
        return float(cell_m)
    if "cell_m" in raster.attrs:
        return float(raster.attrs["cell_m"])
    spacing = [np.diff(np.unique(raster[column].to_numpy(dtype=float))) for column in ("x_m", "y_m")]
    spacing = [values[values > 0] for values in spacing if len(values)]
    if not spacing or not any(len(values) for values in spacing):
        raise ValueError("cell_m cannot be inferred from a raster of one cell; pass cell_m")
    return float(min(values.min() for values in spacing if len(values)))


def zone_polygons(raster: pd.DataFrame, share_threshold: float = DEFAULT_SHARE_THRESHOLD,
                  smoothing_m: float = DEFAULT_SMOOTHING_M, minimum_island_m2: float = DEFAULT_MINIMUM_ISLAND_M2, *,
                  cell_m: Optional[float] = None, label: str = "") -> gpd.GeoDataFrame:
    """The zone rule B4: the paid cells (``paid_cells``) as squares of ``cell_m`` (default: ``raster.attrs`` or the
    lattice spacing), united, smoothed by +``smoothing_m`` then -``smoothing_m`` (round joins), parts below
    ``minimum_island_m2`` dropped. Returns ``part_id``, ``area_m2``, geometry (EPSG:25832, ordered by centroid)."""
    zg._require_non_negative(smoothing_m=smoothing_m, minimum_island_m2=minimum_island_m2)
    size = _cell_size(raster, cell_m)
    paid = raster[paid_cells(raster, share_threshold)]
    half = size / 2.0
    xs, ys = paid["x_m"].to_numpy(dtype=float), paid["y_m"].to_numpy(dtype=float)
    squares = shapely.box(xs - half, ys - half, xs + half, ys + half)
    union = shapely.union_all(squares) if len(squares) else shapely.Polygon()
    smoothed = union.buffer(smoothing_m).buffer(-smoothing_m) if smoothing_m > 0 and not union.is_empty else union
    frame, dropped = zg._core_frame(zg._polygons(smoothed), minimum_island_m2)
    log.info("%s %s%d paid cells (classified, paid_share >= %.2f) = %.0f m2; smoothed +/-%.1f m to %.0f m2; %d parts "
             "kept, %d below %.0f m2 dropped; rule area %.0f m2", _LOG_TAG, f"{label}: " if label else "", len(paid),
             share_threshold, union.area, smoothing_m, smoothed.area, len(frame), dropped, minimum_island_m2,
             float(frame.geometry.area.sum()) if len(frame) else 0.0)
    return frame


# --------------------------------------------------------------------------- B5: validation


def _geometry(value, what: str) -> BaseGeometry:
    if isinstance(value, (gpd.GeoDataFrame, gpd.GeoSeries)):
        zg._require_metric(value, what)
        geometries = value.geometry if isinstance(value, gpd.GeoDataFrame) else value
        return zg._polygonal(zg._union(list(geometries)))
    if isinstance(value, BaseGeometry):
        return zg._polygonal(value)
    raise TypeError(f"{what} must be a GeoDataFrame, a GeoSeries or a shapely geometry, got {type(value).__name__}")


def validation_metrics(rule, legal_zones, annex_zones, frame) -> dict:
    """B5 recall and precision of the rule polygons (EPSG:25832 frames or geometries).

    recall = area(rule inside ``legal_zones``) / area(``legal_zones``), the ordinance polygons Ia and Ib; precision =
    area(rule inside ``annex_zones`` and the map ``frame``) / area(rule inside ``frame``), the annex outlines Ia, Ib and
    II (Ia includes the southern Ia). NaN where a denominator is 0. Also returns the areas behind both (m2).
    """
    rule_geometry = _geometry(rule, "rule polygons")
    legal = _geometry(legal_zones, "legal zones")
    annex = _geometry(annex_zones, "annex zones")
    map_frame = _geometry(frame, "annex map frame")
    inside_legal = rule_geometry.intersection(legal).area if not rule_geometry.is_empty else 0.0
    in_frame = rule_geometry.intersection(map_frame) if not rule_geometry.is_empty else shapely.Polygon()
    inside_annex = in_frame.intersection(annex).area if not in_frame.is_empty else 0.0
    return {"recall": inside_legal / legal.area if legal.area > 0 else math.nan,
            "precision": inside_annex / in_frame.area if in_frame.area > 0 else math.nan,
            "rule_area_m2": round(float(rule_geometry.area), 1), "legal_area_m2": round(float(legal.area), 1),
            "rule_inside_legal_m2": round(float(inside_legal), 1), "rule_inside_frame_m2": round(float(in_frame.area), 1),
            "rule_inside_annex_m2": round(float(inside_annex), 1)}


def _reaches(value, minimum: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            and value >= minimum)


def passes_validation(metrics: Mapping, minimum: float = VALIDATION_MINIMUM) -> bool:
    """The B5 gate: recall and precision both finite and >= ``minimum`` (pre-registered 0.70). With the default
    parameters of owner decision 2 (share 0.3) it is H1."""
    return bool(all(_reaches(metrics.get(name), minimum) for name in ("recall", "precision")))


# --------------------------------------------------------------------------- H2: the pre-registered holdout check


#: H2 (owner decision 2; pre-registered before any holdout overlap at share 0.3 was computed): per town the zone ids of
#: the zone release that serve as references, none used by B5 and none an approximation: the municipal street list of
#: Salzgitter-Lebenstedt, the v1 OSM fee-tag polygons of Wolfsburg, Peine and Helmstedt, and in Braunschweig the three
#: Parkscheininseln and the resident zone 132 of the Stadthalle concept (street lists).
HOLDOUT_REFERENCE_ZONES = {
    "03101000": ("bs_parkscheininsel_marthastrasse_koernerstrasse",
                 "bs_parkscheininsel_gerstaeckerstrasse_kleine_campestrasse", "bs_parkscheininsel_mentestrasse",
                 "bs_resident_stadthalle_132"),
    "03102000": ("sz_lebenstedt",),
    "03103000": ("wob_innenstadt",),
    "03157006": ("pe_innenstadt",),
    "03154028": ("he_innenstadt",),
}
#: The towns whose precision is measured, in the frame of their query box; the Braunschweig street lists enter recall
#: only.
HOLDOUT_PRECISION_TOWNS = ("03102000", "03103000", "03157006", "03154028")
#: H2 bounds: pooled recall and pooled precision (the set value of Q5) and every town's recall (ASSUMPTION Q6).
HOLDOUT_POOLED_MINIMUM = VALIDATION_MINIMUM
HOLDOUT_TOWN_RECALL_MINIMUM = 0.50
#: Share of the reference area of a precision town that may lie outside its query box (rounding of the stored outlines).
HOLDOUT_FRAME_TOLERANCE = 1e-4


def holdout_town_metrics(rule, references: gpd.GeoDataFrame, query_box=None) -> dict:
    """The H2 overlaps of one town (EPSG:25832 frames or geometries).

    Per reference (``zone_id`` of ``references``) its area and the area of the rule inside it; recall = area(rule inside
    the union of the references) / area(union). With ``query_box`` (a precision town): the area of the rule inside the
    box and precision = area(rule inside the references) / area(rule inside the box); every reference must lie inside
    the box (``HOLDOUT_FRAME_TOLERANCE``), so the numerator is part of the frame (``ValueError`` otherwise). Areas in m2
    (0.1), shares from the unrounded areas, NaN where a denominator is 0; ``precision`` and
    ``rule_inside_query_box_m2`` are None without a box.
    """
    zg._require_metric(references, "holdout references")
    zg._require_columns(references, ("zone_id",), "holdout references")
    if references.empty:
        raise ValueError("holdout references: no reference polygon")
    rule_geometry = _geometry(rule, "rule polygons")
    per_reference = {}
    for zone_id, geometry in zip(references["zone_id"], references.geometry):
        polygon = zg._polygonal(geometry)
        inside = rule_geometry.intersection(polygon).area if not rule_geometry.is_empty else 0.0
        per_reference[str(zone_id)] = {"area_m2": round(float(polygon.area), 1), "rule_inside_m2": round(float(inside), 1)}
    union = _geometry(references, "holdout references")
    inside = float(rule_geometry.intersection(union).area) if not rule_geometry.is_empty else 0.0
    result = {"references": per_reference, "reference_area_m2": round(float(union.area), 1),
              "rule_inside_reference_m2": round(inside, 1),
              "recall": inside / union.area if union.area > 0 else math.nan,
              "rule_inside_query_box_m2": None, "precision": None}
    if query_box is not None:
        frame = _geometry(query_box, "query box")
        outside = float(union.difference(frame).area)
        if outside > HOLDOUT_FRAME_TOLERANCE * union.area:
            raise ValueError(f"holdout references {sorted(per_reference)}: {outside:.1f} m2 lie outside the query box; "
                             "the precision frame must contain its references")
        in_box = float(rule_geometry.intersection(frame).area) if not rule_geometry.is_empty else 0.0
        result["rule_inside_query_box_m2"] = round(in_box, 1)
        result["precision"] = inside / in_box if in_box > 0 else math.nan
    return result


def holdout_pooled_metrics(towns: Mapping, *, reference_zones: Mapping = HOLDOUT_REFERENCE_ZONES,
                           precision_towns=HOLDOUT_PRECISION_TOWNS) -> dict:
    """H2 pooled over ``towns`` (ags -> ``holdout_town_metrics``), recomputed from their areas.

    pooled recall = sum(rule inside the references) / sum(reference area) over every town of ``reference_zones``
    (area-weighted over all references); pooled precision = sum(rule inside the references) / sum(rule inside the
    query box) over ``precision_towns``; the smallest town recall; ``passes`` (``passes_holdout``). ``ValueError`` when
    a town of ``reference_zones`` is missing or its block covers other references or the wrong frame: H2 is undefined
    then, never passed."""
    missing = sorted(ags for ags in reference_zones if not towns.get(ags))
    unexpected = sorted(set(towns) - set(reference_zones))
    if missing or unexpected:
        raise ValueError(f"H2 pools the holdout references of every town {sorted(reference_zones)}: holdout blocks "
                         f"missing for {missing}, unexpected for {unexpected}")
    problems = []
    for ags, block in towns.items():
        if sorted(block["references"]) != sorted(reference_zones[ags]):
            problems.append(f"{ags} covers the references {sorted(block['references'])}, expected "
                            f"{sorted(reference_zones[ags])}")
        if (ags in precision_towns) != (block.get("rule_inside_query_box_m2") is not None):
            problems.append(f"{ags}: a precision frame {'is required' if ags in precision_towns else 'is not used'}")
    if problems:
        raise ValueError("invalid holdout blocks: " + "; ".join(problems))
    area = sum(float(towns[ags]["reference_area_m2"]) for ags in reference_zones)
    inside = sum(float(towns[ags]["rule_inside_reference_m2"]) for ags in reference_zones)
    frame_inside = sum(float(towns[ags]["rule_inside_reference_m2"]) for ags in precision_towns)
    frame = sum(float(towns[ags]["rule_inside_query_box_m2"]) for ags in precision_towns)
    town_recall = {ags: float(towns[ags]["rule_inside_reference_m2"]) / float(towns[ags]["reference_area_m2"])
                   if float(towns[ags]["reference_area_m2"]) > 0 else math.nan for ags in sorted(reference_zones)}
    recalls = list(town_recall.values())
    pooled = {"pooled_recall": inside / area if area > 0 else math.nan,
              "pooled_precision": frame_inside / frame if frame > 0 else math.nan,
              "minimum_town_recall": min(recalls) if all(math.isfinite(value) for value in recalls) else math.nan,
              "town_recall": town_recall, "reference_area_m2": round(area, 1), "rule_inside_reference_m2": round(inside, 1),
              "precision_rule_inside_reference_m2": round(frame_inside, 1), "rule_inside_query_box_m2": round(frame, 1),
              "pooled_minimum": HOLDOUT_POOLED_MINIMUM, "town_recall_minimum": HOLDOUT_TOWN_RECALL_MINIMUM}
    pooled["passes"] = passes_holdout(pooled)
    return pooled


def passes_holdout(metrics: Mapping, pooled_minimum: float = HOLDOUT_POOLED_MINIMUM,
                   town_recall_minimum: float = HOLDOUT_TOWN_RECALL_MINIMUM) -> bool:
    """The H2 gate: pooled recall and pooled precision >= ``pooled_minimum`` (0.70) and the smallest town recall >=
    ``town_recall_minimum`` (0.50), all finite."""
    return bool(_reaches(metrics.get("pooled_recall"), pooled_minimum)
                and _reaches(metrics.get("pooled_precision"), pooled_minimum)
                and _reaches(metrics.get("minimum_town_recall"), town_recall_minimum))


def passes_application_gate(validation: Mapping, holdout: Mapping) -> bool:
    """Owner decision 2: the rule may be applied only when H1 (``passes_validation`` of the Braunschweig metrics with
    the default parameters) and H2 (``passes_holdout`` of the pooled holdout metrics) both pass."""
    return passes_validation(validation) and passes_holdout(holdout)


# --------------------------------------------------------------------------- parameters


def _number_text(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


@dataclasses.dataclass(frozen=True)
class SupplyShareParameters:
    """Parameters of the majority rule; metres, square metres, spaces and a share."""
    walk_m: float = DEFAULT_WALK_M
    share_threshold: float = DEFAULT_SHARE_THRESHOLD
    minimum_usable_spaces: float = DEFAULT_MINIMUM_USABLE_SPACES
    cell_m: float = DEFAULT_CELL_M
    smoothing_m: float = DEFAULT_SMOOTHING_M
    minimum_island_m2: float = DEFAULT_MINIMUM_ISLAND_M2

    def __post_init__(self):
        zg._require_non_negative(**self.as_dict())
        if self.walk_m <= 0 or self.cell_m <= 0:
            raise ValueError(f"walk_m and cell_m must be > 0, got {self.walk_m} and {self.cell_m}")
        if not 0.0 < self.share_threshold <= 1.0:
            raise ValueError(f"share_threshold must lie in (0, 1], got {self.share_threshold}")

    def as_dict(self) -> dict:
        return {field.name: float(getattr(self, field.name)) for field in dataclasses.fields(self)}

    def tag(self) -> str:
        """File-name tag of a run: ``w<walk_m>_t<share_threshold>_u<minimum_usable_spaces>_c<cell_m>_s<smoothing_m>
        _i<minimum_island_m2>``, e.g. ``w250_t0.5_u50_c25_s12.5_i10000``; runs with other parameters never share a
        file."""
        return (f"w{_number_text(self.walk_m)}_t{_number_text(self.share_threshold)}"
                f"_u{_number_text(self.minimum_usable_spaces)}_c{_number_text(self.cell_m)}"
                f"_s{_number_text(self.smoothing_m)}_i{_number_text(self.minimum_island_m2)}")


#: The defaults since owner decision 2 (W 250 m, B-f 0.3 POST HOC, B-e 50 spaces, 25 m cells, 12.5 m smoothing, Q2 1 ha):
#: the only parameters the committed QA table, the release file and applied polygons may carry.
DEFAULT_SUPPLY_PARAMETERS = SupplyShareParameters()
#: The pre-registered defaults of Amendment B (B-f 0.5; B5 failed with them): the centre of the B5 arms.
PRE_REGISTERED_SUPPLY_PARAMETERS = SupplyShareParameters(share_threshold=PRE_REGISTERED_SHARE_THRESHOLD)
#: B5 sensitivity arms of Amendment B (W 150 / 400 m, share 0.3 / 0.7 around the pre-registered defaults): information
#: only, never used to select a passing combination (the owner's choice of 0.3 is a recorded POST HOC decision).
SENSITIVITY_ARMS = (dataclasses.replace(PRE_REGISTERED_SUPPLY_PARAMETERS, walk_m=150.0),
                    dataclasses.replace(PRE_REGISTERED_SUPPLY_PARAMETERS, walk_m=400.0),
                    dataclasses.replace(PRE_REGISTERED_SUPPLY_PARAMETERS, share_threshold=0.3),
                    dataclasses.replace(PRE_REGISTERED_SUPPLY_PARAMETERS, share_threshold=0.7))


@dataclasses.dataclass(frozen=True)
class SupplyVariant:
    """Inventory variant of owner decisions 2 and 3 (information arms, never applied): ``street_supply_only`` is S
    (off-street lots and garages leave the inventory), ``payment_evidence_m`` is T (payment evidence within that many
    metres turns untagged street parking paid, ASSUMPTION T-a; None = no payment evidence)."""
    street_supply_only: bool = False
    payment_evidence_m: Optional[float] = None

    def __post_init__(self):
        if not isinstance(self.street_supply_only, bool):
            raise TypeError(f"street_supply_only must be a bool, got {self.street_supply_only!r}")
        distance = self.payment_evidence_m
        if distance is not None and not (isinstance(distance, (int, float)) and not isinstance(distance, bool)
                                         and math.isfinite(distance) and distance > 0):
            raise ValueError(f"payment_evidence_m must be None or a distance > 0 in metres, got {distance!r}")

    @property
    def label(self) -> str:
        """``full`` (B1 as is), ``S``, ``T`` or ``S+T``."""
        names = [name for name, used in (("S", self.street_supply_only), ("T", self.payment_evidence_m is not None))
                 if used]
        return "+".join(names) or "full"

    def suffix(self) -> str:
        """File-name suffix after the parameter tag: '' (full), ``_streetonly``, ``_parkingpayment<m>m`` or both. The T
        suffix names the evidence definition of ruling R-T1c-a (parking elements only); the files ``_payment<m>m`` of
        the superseded literal reading keep their names and are never read as T."""
        return ("_streetonly" if self.street_supply_only else "") + (
            f"_parkingpayment{_number_text(self.payment_evidence_m)}m" if self.payment_evidence_m is not None else "")

    def as_dict(self) -> dict:
        return {"label": self.label, "street_supply_only": self.street_supply_only,
                "payment_evidence_m": None if self.payment_evidence_m is None else float(self.payment_evidence_m)}


FULL_INVENTORY = SupplyVariant()
STREET_SUPPLY_ONLY = SupplyVariant(street_supply_only=True)
PAYMENT_EVIDENCE = SupplyVariant(payment_evidence_m=PAYMENT_EVIDENCE_DISTANCE_M)
STREET_SUPPLY_ONLY_PAYMENT_EVIDENCE = SupplyVariant(street_supply_only=True,
                                                    payment_evidence_m=PAYMENT_EVIDENCE_DISTANCE_M)


@dataclasses.dataclass(frozen=True)
class SupplyArm:
    """One run of the rule: its parameters and its inventory variant; ``tag`` names every file of the run."""
    parameters: SupplyShareParameters = DEFAULT_SUPPLY_PARAMETERS
    variant: SupplyVariant = FULL_INVENTORY

    def tag(self) -> str:
        """``SupplyShareParameters.tag`` plus ``SupplyVariant.suffix``, e.g. ``w250_t0.3_u50_c25_s12.5_i10000_payment75m``."""
        return self.parameters.tag() + self.variant.suffix()

    @property
    def label(self) -> str:
        """Short label for QA text, e.g. ``S share 0.3`` or ``full share 0.5 W 150 m``."""
        walk = "" if self.parameters.walk_m == DEFAULT_SUPPLY_PARAMETERS.walk_m else f" W {self.parameters.walk_m:g} m"
        return f"{self.variant.label} share {self.parameters.share_threshold:g}{walk}"


#: B: the owner's choice (share 0.3, full inventory, literal B-a reading, residents restricted), the only arm that may be
#: applied (H1 and H2).
DEFAULT_ARM = SupplyArm(DEFAULT_SUPPLY_PARAMETERS)
#: The information arms of owner decisions 2 and 3 in the order of the spec: S at 0.3 and 0.5, T at 0.5 and 0.3, S+T at
#: 0.5 and 0.3. Together with B seven arms are computed with H1 and H2 (multiplicity stated in the records).
VARIANT_ARMS = (SupplyArm(DEFAULT_SUPPLY_PARAMETERS, STREET_SUPPLY_ONLY),
                SupplyArm(PRE_REGISTERED_SUPPLY_PARAMETERS, STREET_SUPPLY_ONLY),
                SupplyArm(PRE_REGISTERED_SUPPLY_PARAMETERS, PAYMENT_EVIDENCE),
                SupplyArm(DEFAULT_SUPPLY_PARAMETERS, PAYMENT_EVIDENCE),
                SupplyArm(PRE_REGISTERED_SUPPLY_PARAMETERS, STREET_SUPPLY_ONLY_PAYMENT_EVIDENCE),
                SupplyArm(DEFAULT_SUPPLY_PARAMETERS, STREET_SUPPLY_ONLY_PAYMENT_EVIDENCE))
#: Context of Task 1b recomputed on the same code: Amendment B's pre-registered defaults (share 0.5) and its B5 arms
#: other than the new default (W 150 / 400 m at 0.5, share 0.7); information only.
AMENDMENT_B_ARMS = (SupplyArm(PRE_REGISTERED_SUPPLY_PARAMETERS),) + tuple(
    SupplyArm(parameters) for parameters in SENSITIVITY_ARMS if parameters != DEFAULT_SUPPLY_PARAMETERS)


# --------------------------------------------------------------------------- B7: the release of the classified cells


def release_frame(raster: pd.DataFrame, municipality_ags: str) -> pd.DataFrame:
    """The classified cells of one town as release rows (``PAID_SHARE_RELEASE_COLUMNS``): cell centres in EPSG:25832
    metres (0.1 m), ``paid_share`` and ``heuristic_capacity_share`` to 6 decimals, ``usable_spaces`` to 0.01 spaces."""
    if not _AGS.match(str(municipality_ags)):
        raise ValueError(f"municipality_ags must be an 8-digit AGS, got {municipality_ags!r}")
    zg._require_columns(raster, RASTER_COLUMNS, "raster (run paid_share_raster first)")
    classified = raster[raster["classified"].astype(bool)]
    return pd.DataFrame({"x_m": classified["x_m"].round(1).to_numpy(), "y_m": classified["y_m"].round(1).to_numpy(),
                         "municipality_ags": str(municipality_ags),
                         "paid_share": classified["paid_share"].round(6).to_numpy(),
                         "usable_spaces": classified["usable_spaces"].round(2).to_numpy(),
                         "heuristic_capacity_share": classified["heuristic_capacity_share"].round(6).to_numpy()},
                        columns=list(PAID_SHARE_RELEASE_COLUMNS))


def validate_paid_share_release(release: pd.DataFrame) -> None:
    """Raise ``ValueError`` listing every violation: the release columns in order, 8-digit AGS, finite coordinates, one
    row per cell, ``paid_share`` and ``heuristic_capacity_share`` in [0, 1], ``usable_spaces`` > 0."""
    if list(release.columns) != list(PAID_SHARE_RELEASE_COLUMNS):
        raise ValueError(f"paid-share release: columns {list(release.columns)}, expected "
                         f"{list(PAID_SHARE_RELEASE_COLUMNS)}")
    problems = []
    if release.empty:
        problems.append("no rows")
    bad_ags = sorted({str(value) for value in release["municipality_ags"] if not _AGS.match(str(value))})
    if bad_ags:
        problems.append(f"municipality_ags not an 8-digit AGS: {bad_ags[:5]}")
    for column, low, high in (("paid_share", 0.0, 1.0), ("heuristic_capacity_share", 0.0, 1.0)):
        values = pd.to_numeric(release[column], errors="coerce").to_numpy(dtype=float)
        invalid = ~(np.isfinite(values) & (values >= low) & (values <= high))
        if invalid.any():
            problems.append(f"{column} outside [{low}, {high}] in {int(invalid.sum())} row(s)")
    usable = pd.to_numeric(release["usable_spaces"], errors="coerce").to_numpy(dtype=float)
    if (~(np.isfinite(usable) & (usable > 0))).any():
        problems.append("usable_spaces must be > 0 (only classified cells are released)")
    coordinates = release[["x_m", "y_m"]].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    if (~np.isfinite(coordinates)).any():
        problems.append("x_m / y_m must be finite EPSG:25832 metres")
    duplicated = int(release.duplicated(subset=["x_m", "y_m"]).sum())
    if duplicated:
        problems.append(f"{duplicated} duplicate cell(s): one row per cell")
    if problems:
        raise ValueError("invalid paid-share release: " + "; ".join(problems))


def deterministic_gzip(text: str) -> bytes:
    """``text`` (UTF-8) gzip-compressed without a time stamp or file name in the header: equal text, equal bytes."""
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as stream:
        stream.write(text.encode("utf-8"))
    return buffer.getvalue()


def load_paid_share_release(path) -> pd.DataFrame:
    """Read the gzip CSV release (``#`` lines skipped), typed, and validate it (``validate_paid_share_release``)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"paid-share release missing: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        lines = [line for line in stream.read().splitlines() if not line.startswith("#")]
    release = pd.read_csv(io.StringIO("\n".join(lines)), dtype={"municipality_ags": str})
    validate_paid_share_release(release)
    return release


# --------------------------------------------------------------------------- the committed QA table


#: B5 is evaluated in Braunschweig against the ordinance polygons of zones Ia and Ib of the zone release.
B5_MUNICIPALITY_AGS = "03101000"
LEGAL_ZONE_IDS = ("bs_zone_ia", "bs_zone_ib")
#: ``parking_zones_2026_supply_share_qa.csv``: one row per curated town, written by
#: ``scripts/curation/parking_zones_2026/assemble_parking_zones.py --supply-share-dir`` (its header defines every
#: column). ``role`` says whether the rule may replace polygons (``zones_from_rule``) or is QA only; ``decision`` is
#: ``applied``, ``gate_failed`` (H1 or H2 of owner decision 2 failed, nothing applied anywhere), ``no_rule_polygon``
#: (both passed, nothing to apply) or ``qa_only``; ``applied`` and ``zone_ids`` name the ``osm_supply_majority``
#: polygons. The Braunschweig row carries H1 (the B5 columns, computed with the table's default parameters) and the
#: pooled H2; every holdout town its holdout overlaps; every town its payment evidence (variant T).
SUPPLY_SHARE_QA_COLUMNS = (
    "ags", "name", "role", "osm_extract", "osm_extract_md5", "osm_timestamp", "walk_m", "share_threshold",
    "minimum_usable_spaces", "cell_m", "smoothing_m", "minimum_island_m2", "street_ways", "street_side_areas",
    "offstreet_lots", "paid_elements", "restricted_elements", "free_elements", "excluded_elements", "overpass_response",
    "overpass_osm_timestamp", "overpass_paid_elements", "overpass_restricted_elements", "overpass_free_elements",
    "overpass_excluded_elements", "cross_check", "usable_spaces", "paid_spaces", "restricted_spaces", "free_spaces",
    "tagged_capacity_share", "heuristic_capacity_share", "free_street_spaces_without_fee_tag_share",
    "offstreet_lots_without_fee_tag", "offstreet_spaces_without_fee_tag_share", "ticket_machines",
    "app_payment_elements", "payment_evidence_paid_elements", "payment_evidence_paid_spaces", "cells",
    "classified_cells", "classified_cell_share", "paid_cells",
    "rule_area_m2", "rule_parts", "b5_recall", "b5_precision", "b5_passed", "h2_pooled_recall", "h2_pooled_precision",
    "h2_minimum_town_recall", "h2_passed", "holdout_references", "holdout_reference_area_m2",
    "holdout_rule_inside_reference_m2", "holdout_rule_inside_query_box_m2", "holdout_recall", "holdout_precision",
    "reference", "rule_share_inside_reference", "reference_share_covered_by_rule", "largest_outline_distance_m",
    "sensitivity", "variant_arms", "applied", "zone_ids", "decision", "note",
)
SUPPLY_SHARE_QA_ROLES = ("zones_from_rule", "qa_only")
SUPPLY_SHARE_DECISIONS = ("applied", "gate_failed", "no_rule_polygon", "qa_only")
_QA_COUNTS = ("street_ways", "street_side_areas", "offstreet_lots", "paid_elements", "restricted_elements",
              "free_elements", "excluded_elements", "overpass_paid_elements", "overpass_restricted_elements",
              "overpass_free_elements", "overpass_excluded_elements", "offstreet_lots_without_fee_tag", "cells",
              "classified_cells", "paid_cells", "rule_parts")
#: Counts that may stay empty (the payment evidence of an inventory read before Task 1c).
_QA_OPTIONAL_COUNTS = ("ticket_machines", "app_payment_elements", "payment_evidence_paid_elements")
_QA_AMOUNTS = ("usable_spaces", "paid_spaces", "restricted_spaces", "free_spaces", "rule_area_m2")
_QA_OPTIONAL_AMOUNTS = ("largest_outline_distance_m", "payment_evidence_paid_spaces", "holdout_reference_area_m2",
                        "holdout_rule_inside_reference_m2", "holdout_rule_inside_query_box_m2")
_QA_SHARES = ("tagged_capacity_share", "heuristic_capacity_share", "free_street_spaces_without_fee_tag_share",
              "offstreet_spaces_without_fee_tag_share",
              "classified_cell_share", "b5_recall", "b5_precision", "h2_pooled_recall", "h2_pooled_precision",
              "h2_minimum_town_recall", "holdout_recall", "holdout_precision", "rule_share_inside_reference",
              "reference_share_covered_by_rule")
#: Columns of the Braunschweig row only: H1 (B5 with the table's parameters) and the pooled H2.
QA_GATE_COLUMNS = ("b5_recall", "b5_precision", "b5_passed", "h2_pooled_recall", "h2_pooled_precision",
                   "h2_minimum_town_recall", "h2_passed")
#: Columns of the holdout towns only (the precision pair of the four towns with a query-box frame only).
QA_HOLDOUT_COLUMNS = ("holdout_references", "holdout_reference_area_m2", "holdout_rule_inside_reference_m2",
                      "holdout_recall")
QA_HOLDOUT_PRECISION_COLUMNS = ("holdout_rule_inside_query_box_m2", "holdout_precision")
#: QA column -> ``SupplyShareParameters`` field and the provenance column of an ``osm_supply_majority`` polygon.
QA_PARAMETER_COLUMNS = {"walk_m": "walk_m", "share_threshold": "share_threshold",
                        "minimum_usable_spaces": "minimum_usable_spaces", "cell_m": "cell_m",
                        "smoothing_m": "smoothing_m", "minimum_island_m2": "minimum_island_m2"}
QA_POLYGON_PROVENANCE = {"walk_m": "supply_walk_m", "share_threshold": "paid_share_threshold",
                         "minimum_usable_spaces": "minimum_usable_spaces"}


def load_supply_share_qa(path) -> pd.DataFrame:
    """Load ``parking_zones_2026_supply_share_qa.csv`` (``#`` lines skipped, every cell as stripped text)."""
    qa = pz._read_documented_csv(path)
    pz._check_columns(qa, SUPPLY_SHARE_QA_COLUMNS, str(path))
    qa = qa[list(SUPPLY_SHARE_QA_COLUMNS)].apply(lambda column: column.str.strip())
    log.info("%s loaded %d supply-share QA rows from %s", _LOG_TAG, len(qa), path)
    return qa


def validate_supply_share_qa(qa: pd.DataFrame, zones: pd.DataFrame, tariffs: pd.DataFrame) -> None:
    """Check the supply-share QA table against itself and the polygons; raise ``ValueError`` listing every violation.

    Rules: one row per 8-digit ZGB ``ags``; ``role``, ``decision`` and the literal ``applied`` valid; ``qa_only`` rows
    decide ``qa_only``; an applied row (``decision`` applied) has role ``zones_from_rule`` and zone ids, every other row
    none; snapshot, extract MD5, positive parameters, whole counts, non-negative amounts, shares in [0, 1]; only the
    Braunschweig row carries the gate columns ``QA_GATE_COLUMNS`` (H1 recall and precision, the literal ``b5_passed``,
    the pooled H2 and the literal ``h2_passed``) and it must carry them; the holdout overlaps sit on the holdout towns
    only (``HOLDOUT_REFERENCE_ZONES``, the precision pair on ``HOLDOUT_PRECISION_TOWNS`` only) and name their
    references; an applied row needs ``b5_passed`` and ``h2_passed`` true. Every zone id of an applied row is an
    ``osm_supply_majority`` polygon whose tariff row lies in the row's municipality and whose provenance equals the
    row's parameters and snapshot, and every ``osm_supply_majority`` polygon is listed by exactly one applied row. The
    gates themselves and the default parameters are re-applied by ``scripts/validate_parking_zones.py``.
    """
    pz._check_columns(qa, SUPPLY_SHARE_QA_COLUMNS, "supply-share QA table")
    problems = []
    if qa.empty:
        problems.append("no rows")
    duplicated = sorted(set(qa["ags"][qa["ags"].duplicated()]))
    if duplicated:
        problems.append(f"duplicate rows for ags {duplicated}; one row per town")
    b5_rows = qa[qa["ags"] == B5_MUNICIPALITY_AGS]
    gate_passed = (len(b5_rows) == 1 and b5_rows.iloc[0]["b5_passed"] == "true"
                   and b5_rows.iloc[0]["h2_passed"] == "true")
    polygons = zones.set_index("zone_id")
    municipality = tariffs.set_index("zone_id")["municipality_ags"]
    listed = {}
    for _, row in qa.iterrows():
        ags, prefix = row["ags"], f"ags {row['ags']}"
        if not _AGS.match(str(ags)) or str(ags)[:5] not in pz.ZGB_COUNTY_KEYS:
            problems.append(f"{prefix}: not an 8-digit AGS of the ZGB counties")
        if row["role"] not in SUPPLY_SHARE_QA_ROLES:
            problems.append(f"{prefix}: role {row['role']!r} is not one of {list(SUPPLY_SHARE_QA_ROLES)}")
        if row["decision"] not in SUPPLY_SHARE_DECISIONS:
            problems.append(f"{prefix}: decision {row['decision']!r} is not one of {list(SUPPLY_SHARE_DECISIONS)}")
        if (row["role"] == "qa_only") != (row["decision"] == "qa_only"):
            problems.append(f"{prefix}: role {row['role']!r} and decision {row['decision']!r} disagree (qa_only rows "
                            "decide qa_only)")
        if row["applied"] not in ("true", "false"):
            problems.append(f"{prefix}: applied {row['applied']!r}; use the literal 'true' or 'false'")
        if not pz._OSM_TIMESTAMP_PATTERN.match(str(row["osm_timestamp"])):
            problems.append(f"{prefix}: osm_timestamp {row['osm_timestamp']!r} is not 'YYYY-MM-DDTHH:MM:SSZ'")
        if not re.fullmatch(r"[0-9a-f]{32}", str(row["osm_extract_md5"])) or not row["osm_extract"]:
            problems.append(f"{prefix}: osm_extract and its 32-digit MD5 are required")
        for column in QA_PARAMETER_COLUMNS:
            value = pz._qa_number(row[column])
            positive = column in ("walk_m", "cell_m", "share_threshold")
            if value is None or not math.isfinite(value) or value < 0 or (positive and value <= 0) or \
                    (column == "share_threshold" and value > 1):
                problems.append(f"{prefix}: {column} = {row[column]!r} is not a valid parameter")
        for column in _QA_COUNTS + _QA_OPTIONAL_COUNTS:
            if (column in _QA_COUNTS or row[column]) and not re.fullmatch(r"\d+", str(row[column])):
                problems.append(f"{prefix}: {column} = {row[column]!r} must be a whole number >= 0")
        for column in _QA_AMOUNTS + _QA_OPTIONAL_AMOUNTS:
            value = pz._qa_number(row[column])
            required = column in _QA_AMOUNTS
            if (value is None and required) or (value is not None and not (math.isfinite(value) and value >= 0)):
                problems.append(f"{prefix}: {column} = {row[column]!r} must be a number >= 0")
        for column in _QA_SHARES:
            value = pz._qa_number(row[column])
            if value is not None and not (math.isfinite(value) and 0.0 <= value <= 1.0):
                problems.append(f"{prefix}: {column} = {row[column]!r} must lie in [0, 1] (or be empty)")
        gate = [row[column] for column in QA_GATE_COLUMNS]
        if ags == B5_MUNICIPALITY_AGS:
            if row["b5_passed"] not in ("true", "false") or row["h2_passed"] not in ("true", "false") or not all(gate):
                problems.append(f"{prefix}: the Braunschweig row carries {list(QA_GATE_COLUMNS)} with the literal "
                                "b5_passed and h2_passed")
        elif any(gate):
            problems.append(f"{prefix}: only the Braunschweig row ({B5_MUNICIPALITY_AGS}) carries the gate columns "
                            "(H1 and the pooled H2)")
        references = HOLDOUT_REFERENCE_ZONES.get(ags)
        framed = ags in HOLDOUT_PRECISION_TOWNS
        for column in QA_HOLDOUT_COLUMNS + QA_HOLDOUT_PRECISION_COLUMNS:
            expected = references is not None and (column in QA_HOLDOUT_COLUMNS or framed)
            if bool(row[column]) != expected:
                problems.append(f"{prefix}: {column} {'is required' if expected else 'must be empty'} (holdout "
                                f"{'town' if references else 'references only in ' + str(sorted(HOLDOUT_REFERENCE_ZONES))}"
                                f"{', precision frame' if framed else ''})")
        if references is not None and row["holdout_references"] != ";".join(references):
            problems.append(f"{prefix}: holdout_references {row['holdout_references']!r}, expected "
                            f"{';'.join(references)!r}")
        applied = row["applied"] == "true"
        zone_ids = [zone_id.strip() for zone_id in str(row["zone_ids"] or "").split(";") if zone_id.strip()]
        if applied != (row["decision"] == "applied"):
            problems.append(f"{prefix}: applied {row['applied']!r} and decision {row['decision']!r} disagree")
        if applied:
            if row["role"] != "zones_from_rule":
                problems.append(f"{prefix}: applied rows need role 'zones_from_rule', found {row['role']!r}")
            if not gate_passed:
                problems.append(f"{prefix}: applied although the Braunschweig row does not pass H1 and H2")
            if not zone_ids:
                problems.append(f"{prefix}: an applied row lists its zone_ids")
        elif zone_ids:
            problems.append(f"{prefix}: zone_ids {zone_ids} on a row that is not applied")
        for zone_id in zone_ids if applied else []:
            listed.setdefault(zone_id, []).append(ags)
            if zone_id not in polygons.index:
                problems.append(f"{prefix}: zone {zone_id!r} has no polygon")
                continue
            polygon = polygons.loc[zone_id]
            if polygon["geometry_source"] != pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE:
                problems.append(f"{prefix}: zone {zone_id!r} is not an osm_supply_majority polygon "
                                f"({polygon['geometry_source']})")
                continue
            if municipality.get(zone_id) != ags:
                problems.append(f"{prefix}: zone {zone_id!r} has municipality_ags {municipality.get(zone_id)!r}")
            for column, provenance in QA_POLYGON_PROVENANCE.items():
                value = pz._qa_number(row[column])
                if value is None or not math.isclose(float(polygon[provenance]), value, rel_tol=1e-9):
                    problems.append(f"{prefix}: zone {zone_id!r} has {provenance} {polygon[provenance]} but the QA row "
                                    f"{column} {row[column]!r}")
            if polygon["osm_timestamp"] != row["osm_timestamp"]:
                problems.append(f"{prefix}: zone {zone_id!r} has osm_timestamp {polygon['osm_timestamp']!r} but the QA "
                                f"row {row['osm_timestamp']!r}")
    if len(b5_rows) != 1:
        problems.append(f"the Braunschweig row ({B5_MUNICIPALITY_AGS}) with the H1 and H2 gates is missing")
    majority = sorted(zones.loc[zones["geometry_source"] == pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE, "zone_id"]
                      .astype(str))
    for zone_id in majority:
        if zone_id not in listed:
            problems.append(f"zone {zone_id!r} (osm_supply_majority) is not listed in the zone_ids of an applied row")
        elif len(listed[zone_id]) > 1:
            problems.append(f"zone {zone_id!r} is listed by several applied rows {listed[zone_id]}")
    if problems:
        raise ValueError("invalid supply-share QA table:\n  " + "\n  ".join(problems))
