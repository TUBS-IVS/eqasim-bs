"""Links of the facilities placed at the cordon road gates (#465, ADR-0167).

Two kinds of facility sit at a road gate of ``braunschweig.synthesis.cordon_gates``: the portal stays
``portal_<gate_id>`` (ADR-0141) and the homes of the in-commuters anchored at the gate point
(``braunschweig.synthesis.incommuters``, ``braunschweig.synthesis.student_incommuters``). Written with
coordinates only, eqasim's ``FacilityPlacement`` attached them to the closest link its OSM voter accepts, and that
voter excludes every motorway, trunk and ``*_link`` link: in the 1 % rerun 64 of 67 gate facilities ended on another
link than their gate link, all 8 motorway gates on local roads (6 on 600 veh/h residential or tertiary streets).

Rule (all distances in metres in the metric CRS of the network, EPSG:25832 here):

1. **Entry link.** The car link that crosses INTO the cut extent (from-node outside, to-node inside; the cutter's own
   ``ScenarioExtent.isInside`` test, i.e. ``polygon.contains``, boundary points are outside) with at least
   ``minimum_capacity_veh_per_h`` and the smallest perpendicular distance to the gate point, within
   ``maximum_distance_m``. Ties go to the lower link id.
2. **Facility link.** Among the car links that end at the entry link's from-node, start outside the extent and have
   at least ``minimum_capacity_veh_per_h``: the one with the highest capacity (the carriageway, not an on-ramp),
   ties to the lower link id. It lies wholly outside the extent.

A trip that departs from the facility link enters through the entry link; a trip that arrives at it leaves the extent
on the router's outward path and turns outside. eqasim's ``RunScenarioCutter`` then places the outside activities on
those two crossing links, the same representation every other cross-boundary trip gets. Gates for which either step
finds no link keep eqasim's placement (the fallback); they are counted and the facility stage fails above
``maximum_fallback_share``.
"""
from __future__ import annotations

import gzip
import logging
import re
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import shapely

from braunschweig.synthesis.portal_trips.config_keys import PORTAL_LOCATION_ID_PREFIX

logger = logging.getLogger(__name__)

STATUS_LINKED = "linked"
STATUS_FALLBACK = "fallback"
REASON_NO_ENTRY_LINK = "no_entry_link"
REASON_NO_FEEDING_LINK = "no_feeding_link"

#: Defaults of the synpp options of ``braunschweig.matsim.scenario.facilities`` (measured, not calibrated: ADR-0167).
DEFAULT_MINIMUM_CAPACITY_VEH_PER_H = 1000.0
DEFAULT_MAXIMUM_DISTANCE_M = 300.0
DEFAULT_MAXIMUM_FALLBACK_SHARE = 0.05

SELECTION_COLUMNS = ["gate_id", "status", "reason", "entry_link_id", "entry_distance_m", "facility_link_id",
                     "facility_link_capacity_veh_per_h"]


def _seconds(hms: str) -> float:
    hours, minutes, seconds = (float(part) for part in hms.split(":"))
    return hours * 3600.0 + minutes * 60.0 + seconds


def read_car_links(network_path: str) -> pd.DataFrame:
    """Parse a MATSim network XML(.gz) into one row per link with its node coordinates.

    Returns ``[link_id, from_node, to_node, from_x, from_y, to_x, to_y, capacity_veh_per_h, allows_car]``. The
    capacity is converted from the network's ``capperiod`` (default one hour) to vehicles per hour. Streams the file
    like ``braunschweig.data.cordon.network.read_matsim_links``, which is not reused because it drops the node ids
    and the modes this rule needs.
    """
    opener = gzip.open if network_path.endswith(".gz") else open
    nodes: dict[str, tuple[float, float]] = {}
    rows = []
    capacity_period_s = 3600.0
    with opener(network_path, "rb") as handle:
        for event, elem in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                if elem.tag == "links" and elem.get("capperiod"):
                    capacity_period_s = _seconds(elem.get("capperiod"))
                continue
            if elem.tag == "node":
                nodes[elem.get("id")] = (float(elem.get("x")), float(elem.get("y")))
                elem.clear()
            elif elem.tag == "link":
                source, target = elem.get("from"), elem.get("to")
                if source not in nodes or target not in nodes:
                    raise ValueError("[gate_facility_links] link %s references an unknown node in %s"
                                     % (elem.get("id"), network_path))
                modes = {mode.strip() for mode in (elem.get("modes") or "").split(",")}
                rows.append((elem.get("id"), source, target) + nodes[source] + nodes[target]
                            + (float(elem.get("capacity")), "car" in modes))
                elem.clear()
    if not rows:
        raise ValueError("[gate_facility_links] no links parsed from %s" % network_path)
    links = pd.DataFrame(rows, columns=["link_id", "from_node", "to_node", "from_x", "from_y", "to_x", "to_y",
                                        "capacity_veh_per_h", "allows_car"])
    links["capacity_veh_per_h"] *= 3600.0 / capacity_period_s
    return links


def _segment_distance_m(px, py, links):
    dx = links["to_x"].to_numpy() - links["from_x"].to_numpy()
    dy = links["to_y"].to_numpy() - links["from_y"].to_numpy()
    length_sq = dx * dx + dy * dy
    with np.errstate(invalid="ignore", divide="ignore"):
        t = ((px - links["from_x"].to_numpy()) * dx + (py - links["from_y"].to_numpy()) * dy) / length_sq
    t = np.where(length_sq > 0.0, np.clip(t, 0.0, 1.0), 0.0)
    return np.hypot(links["from_x"].to_numpy() + t * dx - px, links["from_y"].to_numpy() + t * dy - py)


def select_gate_facility_links(gates, links, extent, minimum_capacity_veh_per_h, maximum_distance_m) -> pd.DataFrame:
    """Apply the rule of the module docstring to every gate.

    Parameters
    ----------
    gates:
        GeoDataFrame ``[gate_id, geometry(Point)]`` in the metric CRS of ``links``.
    links:
        Output of :func:`read_car_links` (the pre-cut network the cutter runs on).
    extent:
        The cut extent polygon (``build_cordon_polygon`` with the cutter's buffer).

    Returns one row per gate with ``SELECTION_COLUMNS``; ``facility_link_id`` is missing for a fallback gate.
    """
    if minimum_capacity_veh_per_h < 0.0 or maximum_distance_m <= 0.0:
        raise ValueError("[gate_facility_links] minimum capacity must be >= 0 and maximum distance > 0, got %r, %r"
                         % (minimum_capacity_veh_per_h, maximum_distance_m))
    eligible = links[links["allows_car"].astype(bool)
                     & (links["capacity_veh_per_h"] >= minimum_capacity_veh_per_h)].copy()
    eligible["link_id"] = eligible["link_id"].astype(str)
    from_inside = shapely.contains_xy(extent, eligible["from_x"].to_numpy(), eligible["from_y"].to_numpy())
    to_inside = shapely.contains_xy(extent, eligible["to_x"].to_numpy(), eligible["to_y"].to_numpy())
    entering = eligible[~from_inside & to_inside]
    feeding = eligible[~from_inside & ~to_inside]

    rows = []
    for gate in gates.itertuples(index=False):
        row = dict.fromkeys(SELECTION_COLUMNS)
        row["gate_id"] = gate.gate_id
        distance_m = _segment_distance_m(gate.geometry.x, gate.geometry.y, entering)
        candidates = entering.assign(distance_m=distance_m)
        candidates = candidates[candidates["distance_m"] <= maximum_distance_m]
        if candidates.empty:
            row.update(status=STATUS_FALLBACK, reason=REASON_NO_ENTRY_LINK)
            rows.append(row)
            continue
        entry = candidates.sort_values(["distance_m", "link_id"]).iloc[0]
        row.update(entry_link_id=entry["link_id"], entry_distance_m=float(entry["distance_m"]))
        feeders = feeding[feeding["to_node"] == entry["from_node"]]
        if feeders.empty:
            row.update(status=STATUS_FALLBACK, reason=REASON_NO_FEEDING_LINK)
            rows.append(row)
            continue
        feeder = feeders.assign(_negative_capacity=-feeders["capacity_veh_per_h"]) \
            .sort_values(["_negative_capacity", "link_id"]).iloc[0]
        row.update(status=STATUS_LINKED, facility_link_id=feeder["link_id"],
                   facility_link_capacity_veh_per_h=float(feeder["capacity_veh_per_h"]))
        rows.append(row)
    return pd.DataFrame(rows, columns=SELECTION_COLUMNS)


def attach_gate_facility_links(df_homes, df_portal, gates, selection, maximum_fallback_share):
    """Add a ``link_id`` column to the home and portal facility frames for the facilities at a road gate.

    A home is at a gate when its coordinate equals a gate point exactly: the in-commuter stages copy the gate
    geometry into the home coordinate. A portal facility is at a gate when its id is ``portal_<gate_id>`` of a road
    gate; other portal ids (rail exit stations) are not touched. Every other row gets a missing ``link_id``, i.e.
    eqasim places it as before.

    Logs the primary/fallback counts as rates and raises ``RuntimeError`` when the fallback share of the gate
    facilities exceeds ``maximum_fallback_share``. Returns ``(df_homes, df_portal, summary)``.
    """
    if not 0.0 <= maximum_fallback_share <= 1.0:
        raise ValueError("[gate_facility_links] maximum fallback share must lie in [0, 1], got %r"
                         % maximum_fallback_share)
    link_by_gate = dict(zip(selection["gate_id"], selection["facility_link_id"]))
    gate_by_xy = {(point.x, point.y): gate_id for gate_id, point in zip(gates["gate_id"], gates.geometry)}

    df_homes = df_homes.copy()
    home_gates = [gate_by_xy.get((point.x, point.y)) for point in df_homes.geometry]
    df_homes["link_id"] = pd.Series([link_by_gate.get(g) if g else None for g in home_gates],
                                    index=df_homes.index, dtype=object)

    df_portal = df_portal.copy()
    portal_gates = []
    for location_id in df_portal["location_id"].astype(str):
        suffix = location_id[len(PORTAL_LOCATION_ID_PREFIX):] if location_id.startswith(PORTAL_LOCATION_ID_PREFIX) \
            else None
        portal_gates.append(suffix if suffix in link_by_gate else None)
    df_portal["link_id"] = pd.Series([link_by_gate.get(g) if g else None for g in portal_gates],
                                     index=df_portal.index, dtype=object)

    at_gate = [g for g in home_gates + portal_gates if g]
    n_facilities = len(at_gate)
    n_linked = sum(1 for g in at_gate if pd.notna(link_by_gate.get(g)))
    n_fallback = n_facilities - n_linked
    share = n_fallback / n_facilities if n_facilities else 0.0
    n_gates_linked = int((selection["status"] == STATUS_LINKED).sum())
    summary = {"n_gates": len(selection), "n_gates_linked": n_gates_linked, "n_facilities": n_facilities,
               "n_linked": n_linked, "n_fallback": n_fallback, "fallback_share": share}

    message = ("[gate_facility_links] gate facilities: primary %d/%d (%.1f%%), fallback %d (%.1f%%, eqasim placement);"
               " gates linked %d/%d" % (n_linked, n_facilities, 100.0 * (1.0 - share) if n_facilities else 100.0,
                                        n_fallback, 100.0 * share, n_gates_linked, len(selection)))
    if n_fallback:
        fallback_gates = selection.loc[selection["status"] == STATUS_FALLBACK, ["gate_id", "reason"]]
        logger.warning("%s; fallback gates: %s", message, fallback_gates.to_dict("records"))
    else:
        logger.info(message)
    if share > maximum_fallback_share:
        raise RuntimeError(
            "%s exceeds the maximum fallback share %.3f: the gate link rule is broken (a wrong CRS, a minimum capacity "
            "above the network's major roads, or a too small maximum distance)." % (message, maximum_fallback_share))
    return df_homes, df_portal, summary


_FACILITY_ID = re.compile(r'<facility\b[^>]*\bid="([^"]*)"')
_FACILITY_LINK = re.compile(r'<facility\b[^>]*\blinkId="([^"]*)"')


def _preset_links(path):
    """Map facility id -> linkId for every facility line of a facilities XML(.gz) that carries a link."""
    opener = gzip.open if str(path).endswith(".gz") else open
    links = {}
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            link = _FACILITY_LINK.search(line)
            if link:
                links[_FACILITY_ID.search(line).group(1)] = link.group(1)
    return links


def verify_preset_links_kept(written_facilities_path, prepared_facilities_path):
    """Fail when eqasim's preparation did not keep the links the facilities stage pinned.

    An eqasim-java-bs jar without the preset-link change of ``FacilityPlacement`` overwrites every link with the
    nearest non-motorway link: the gate facilities would silently fall back to the old placement. Compares each
    facility that carries a ``linkId`` in the written file with the prepared file; returns the number checked.
    """
    pinned = _preset_links(written_facilities_path)
    if not pinned:
        return 0
    opener = gzip.open if str(prepared_facilities_path).endswith(".gz") else open
    prepared = {}
    with opener(prepared_facilities_path, "rt", encoding="utf-8") as handle:
        for line in handle:
            facility = _FACILITY_ID.search(line)
            if facility and facility.group(1) in pinned:
                link = _FACILITY_LINK.search(line)
                prepared[facility.group(1)] = link.group(1) if link else None
    changed = sorted(f for f, link in pinned.items() if prepared.get(f) != link)
    if changed:
        raise RuntimeError(
            "[gate_facility_links] %d of %d pinned gate facilities lost their preset link in the eqasim preparation "
            "(sample: %s): the eqasim-java-bs jar does not keep preset facility links (ADR-0167); rebuild it from a "
            "commit with the FacilityPlacement change." % (len(changed), len(pinned),
                                                           [(f, pinned[f], prepared.get(f)) for f in changed[:5]]))
    logger.info("[gate_facility_links] preparation kept all %d pinned gate facility links", len(pinned))
    return len(pinned)
