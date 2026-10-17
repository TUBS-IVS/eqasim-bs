"""Inventory variants S and T and the arms of the parking supply majority rule (parking cost zones v2, issue #436).

Owner decisions 2 and 3 of spec Amendment B (2026-09-30; ADR-0139), information arms only, never applied; they act on
the B1 inventory of ``braunschweig.parking.supply_share.supply_elements`` (all EPSG:25832 metres):

* **Variant S** (``STREET_SUPPLY_ONLY``, ``street_supply_only``): street sides and street-side areas only; off-street
  lots and garages leave the inventory.
* **Variant T** (``PAYMENT_EVIDENCE``, ``apply_payment_evidence``; pre-registered before any T result): a street side or
  street-side area that is free only because it carries no fee tag (B-a) becomes paid when its geometry lies within
  ``PAYMENT_EVIDENCE_DISTANCE_M`` (75 m, ASSUMPTION T-a: about one block face served by one machine) of a parking
  ticket machine (``is_parking_ticket_machine``) or of a parking element (``is_parking_element``) with an app-payment
  tag (``supply_share.app_payment_keys``); a street-side area or lot with an app-payment tag of its own and no fee tag
  is paid (payment implies a fee); an explicit fee=no is never overridden, disc parking (B-b) stays free and the
  evidence adds no capacity (``payment_evidence``, ``summarise_payment_evidence``). Ruling R-T1c-a corrected the
  evidence definition of the controller before the task review (not a tuning step): phone wallets and non-parking
  elements (shops, charging stations) never count.
* **Arms** (``SupplyVariant``, ``SupplyArm``): ``DEFAULT_ARM`` (B, the only arm that may be applied),
  ``VARIANT_ARMS`` (S at 0.3 and 0.5, T at 0.5 and 0.3, S+T at 0.5 and 0.3) and ``AMENDMENT_B_ARMS`` (Amendment B's
  share 0.5 and its B5 arms other than the new default); ``variant_elements`` builds the inventory of a variant.

``app_payment_keys`` stays in ``supply_share``: the B1 element frame records the app-payment keys of every element
(column ``app_payment_keys``), and this module builds on ``supply_share``, which must therefore not import it.
Pure functions; used by the curation aid ``scripts/build_parking_zones_from_osm.py --supply-share``, never a pipeline
stage. Split out of ``supply_share`` in Task 1c fix round 1 (ruling R-T1c-b), behaviour-preserving.
"""
from __future__ import annotations

import dataclasses
import logging
import math
from typing import Mapping, Optional

import geopandas as gpd
import numpy as np
import shapely

from braunschweig.parking import supply_share as ss
from braunschweig.parking import zone_geometry as zg

log = logging.getLogger(__name__)

#: Variant T (owner decision 3, pre-registered before any T result): ASSUMPTION T-a, metres (EPSG:25832) between the
#: geometry of a street side or street-side area and the payment evidence.
PAYMENT_EVIDENCE_DISTANCE_M = 75.0
#: A parking ticket machine: a node with amenity=vending_machine whose vending=* lists parking_tickets.
TICKET_MACHINE_VENDING = "parking_tickets"
#: Evidence behind a conversion by variant T (column ``payment_evidence``), in the order of attribution: a ticket
#: machine within the distance, else an app-payment tag on a parking element; ``own_app_payment_tag`` for a street-side
#: area or lot paid by its own tag.
PAYMENT_EVIDENCE_SOURCES = ("ticket_machine", "app_payment_parking", "own_app_payment_tag")
#: Reasons of B1 that variant T may override: street parking free only for lack of a fee tag (B-a) by proximity; a
#: street-side area or lot without a fee tag (B-a, B-b disc area, B-c) by its own app-payment tag.
PROXIMITY_CONVERTIBLE_REASONS = ("no_fee_tag",)
OWN_TAG_CONVERTIBLE_REASONS = ("no_fee_tag", "disc", "no_fee_tag_offstreet")
EVIDENCE_COLUMNS = ("evidence_id", "osm_type", "osm_id", "evidence", "app_payment_keys", "object", "geometry")


# --------------------------------------------------------------------------- payment evidence and the variant inventory


def is_parking_ticket_machine(tags: Mapping) -> bool:
    """A parking ticket machine of variant T: ``amenity=vending_machine`` whose ``vending=*`` lists
    ``TICKET_MACHINE_VENDING`` (possibly among other goods); ``payment_evidence`` keeps nodes only."""
    return (zg._tag_text(tags, "amenity") or "").lower() == "vending_machine" and \
        TICKET_MACHINE_VENDING in zg._tokens(zg._tag_text(tags, "vending"))


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
        osm_type, osm_id, element = ss._identity(row, position)
        machine = is_parking_ticket_machine(tags)
        if machine and osm_type and osm_type != "node":
            not_nodes += 1
            machine = False
        keys = ss.app_payment_keys(tags)
        if not keys and ss._set_payment_keys(tags, ss.PHONE_WALLET_PAYMENT_KEYS):
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
    frame = gpd.GeoDataFrame(rows, columns=list(EVIDENCE_COLUMNS), geometry="geometry", crs=ss.METRIC_CRS)
    frame["osm_id"] = frame["osm_id"].astype("Int64")
    summary = summarise_payment_evidence(frame)["evidence"]
    log.info("%s %spayment evidence of variant T (ruling R-T1c-a): %d parking ticket machines (nodes), %d parking "
             "elements with an app-payment tag; left out: %d elements with an app-payment tag that are no parking "
             "element, %d elements with a phone-wallet key only, %d ticket machines that are no node, %d elements "
             "without geometry", ss._LOG_TAG, f"{label}: " if label else "", summary["ticket_machines"],
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
        zg._require_columns(elements, ss.ELEMENT_COLUMNS, "supply elements (run supply_elements first)")
        converted = elements[elements["reason"].isin(("payment_evidence", "app_payment_tag"))]
        summary["converted"] = {kind: int((converted["kind"] == kind).sum()) for kind in ss.ELEMENT_KINDS}
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
    zg._require_columns(elements, ss.ELEMENT_COLUMNS, "supply elements (run supply_elements first)")
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
             ss._LOG_TAG, f"{label}, " if label else "", region, distance_m, int(near.sum()), int(candidates.sum()),
             ss._share_text(near.sum() / candidates.sum() if candidates.sum() else math.nan), float(spaces[near].sum()),
             float(spaces[candidates].sum()), int(own.sum()), int(own_possible.sum()), float(spaces[own].sum()),
             int((source == "ticket_machine").sum()), int((source == "app_payment_parking").sum()))
    return result


def street_supply_only(elements: gpd.GeoDataFrame, *, label: str = "",
                       region: str = "the elements passed") -> gpd.GeoDataFrame:
    """Variant S (owner decision 2): the inventory without its off-street lots and garages (kind ``lot``), i.e. the
    street sides and the separately mapped street-side areas; a copy with a fresh index. Logs what leaves."""
    zg._require_columns(elements, ss.ELEMENT_COLUMNS, "supply elements (run supply_elements first)")
    lots = (elements["kind"] == "lot").to_numpy()
    usable = elements["class"].isin(ss.USABLE_CLASSES).to_numpy()
    log.info("%s %s%s: variant S keeps %d street sides and street-side areas and drops %d off-street lots and garages "
             "(%d usable, %.0f usable spaces)", ss._LOG_TAG, f"{label}, " if label else "", region, int((~lots).sum()),
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
            raise ValueError("variant T needs the payment evidence (supply_variants.payment_evidence of the inventory)")
        result = apply_payment_evidence(result, evidence, variant.payment_evidence_m, label=label, region=region)
    return result


# --------------------------------------------------------------------------- the variants and the arms


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
            f"_parkingpayment{ss._number_text(self.payment_evidence_m)}m" if self.payment_evidence_m is not None else "")

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
    parameters: ss.SupplyShareParameters = ss.DEFAULT_SUPPLY_PARAMETERS
    variant: SupplyVariant = FULL_INVENTORY

    def tag(self) -> str:
        """``supply_share.SupplyShareParameters.tag`` plus ``SupplyVariant.suffix``, e.g.
        ``w250_t0.3_u50_c25_s12.5_i10000_parkingpayment75m``."""
        return self.parameters.tag() + self.variant.suffix()

    @property
    def label(self) -> str:
        """Short label for QA text, e.g. ``S share 0.3`` or ``full share 0.5 W 150 m``."""
        walk = "" if self.parameters.walk_m == ss.DEFAULT_SUPPLY_PARAMETERS.walk_m else f" W {self.parameters.walk_m:g} m"
        return f"{self.variant.label} share {self.parameters.share_threshold:g}{walk}"


#: B: the owner's choice (share 0.3, full inventory, literal B-a reading, residents restricted), the only arm that may be
#: applied (H1 and H2).
DEFAULT_ARM = SupplyArm(ss.DEFAULT_SUPPLY_PARAMETERS)
#: The information arms of owner decisions 2 and 3 in the order of the spec: S at 0.3 and 0.5, T at 0.5 and 0.3, S+T at
#: 0.5 and 0.3. Together with B seven arms are computed with H1 and H2 (multiplicity stated in the records).
VARIANT_ARMS = (SupplyArm(ss.DEFAULT_SUPPLY_PARAMETERS, STREET_SUPPLY_ONLY),
                SupplyArm(ss.PRE_REGISTERED_SUPPLY_PARAMETERS, STREET_SUPPLY_ONLY),
                SupplyArm(ss.PRE_REGISTERED_SUPPLY_PARAMETERS, PAYMENT_EVIDENCE),
                SupplyArm(ss.DEFAULT_SUPPLY_PARAMETERS, PAYMENT_EVIDENCE),
                SupplyArm(ss.PRE_REGISTERED_SUPPLY_PARAMETERS, STREET_SUPPLY_ONLY_PAYMENT_EVIDENCE),
                SupplyArm(ss.DEFAULT_SUPPLY_PARAMETERS, STREET_SUPPLY_ONLY_PAYMENT_EVIDENCE))
#: Context of Task 1b recomputed on the same code: Amendment B's pre-registered defaults (share 0.5) and its B5 arms
#: other than the new default (W 150 / 400 m at 0.5, share 0.7); information only.
AMENDMENT_B_ARMS = (SupplyArm(ss.PRE_REGISTERED_SUPPLY_PARAMETERS),) + tuple(
    SupplyArm(parameters) for parameters in ss.SENSITIVITY_ARMS if parameters != ss.DEFAULT_SUPPLY_PARAMETERS)
