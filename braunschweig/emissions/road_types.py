"""HBEFA road type per network link (post-run emissions, #688).

Method: a port of MATSim's ``org.matsim.contrib.emissions.OsmHbefaMapping`` (matsim-libs; the
class in the pinned ``2026w12`` run jar has the same constants and branches, checked with
``javap``). The class table is the committed ``osm_hbefa_road_classes.csv``. The contrib class
itself cannot be used on this network: it reads the link attribute ``type``, which the
Braunschweig network does not carry; the OSM class is in ``osm:way:highway``.

The port is deliberately faithful, including behaviour that is debatable (documented so a
reader does not mistake it for a choice made here):

* every link is ``URB/...``; the contrib has no rural classes;
* every ``motorway`` and ``motorway_link`` becomes ``MW-Nat.`` (operator precedence in the
  original condition), so the ``motorway`` -> ``MW-City`` row is never reached;
* ``unclassified`` / ``road`` are re-classed by speed alone, up to ``motorway`` above 90 km/h;
* the speed is rounded to 10 km/h and then clamped into the class range, so a link slower than
  the class minimum is raised to it.

One deviation: an infinite speed (seen only on transit-only links) is clamped to the class
maximum here; the Java original overflows on it.

Speed source, as in the contrib: the link attribute ``allowed_speed`` when present, else the
link's freespeed. LIMITATION: the Braunschweig freespeeds are not legal limits everywhere (many
residential links carry 15 km/h, tertiary links 25 km/h); the clamp hides most of this, and the
tagger counts how many links were raised or lowered so the effect is visible in the report.

Fallback (project rule "no silent fallbacks"): a link whose class is not in the table gets
``fallback_road_type`` and is counted. For links an emitting vehicle can drive on, the share of
link length on the fallback is checked against ``max_fallback_length_share`` and the build
aborts above it. Links no emitting mode may use (transit-only) never matter to the result; they
are tagged too, because the tool expects the attribute on every link, and counted separately.
"""
from __future__ import annotations

import csv
import logging
import math
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from braunschweig.emissions.view import LinkRecord

logger = logging.getLogger("braunschweig.emissions.road_types")

#: Link attribute holding the OSM highway class in the Braunschweig network.
OSM_HIGHWAY_ATTRIBUTE = "osm:way:highway"
#: Link attribute the contrib prefers over the freespeed (``NetworkUtils.getAllowedSpeed``).
ALLOWED_SPEED_ATTRIBUTE = "allowed_speed"

_CLASSES_FILE = Path(__file__).with_name("osm_hbefa_road_classes.csv")


class UnmappedRoadClass(KeyError):
    """The OSM class (after the contrib's normalisation) is not in the class table."""


@dataclass(frozen=True)
class _RoadClass:
    name: str
    min_speed_kmh: int
    max_speed_kmh: int


@lru_cache(maxsize=1)
def _load_classes() -> dict[str, _RoadClass]:
    with _CLASSES_FILE.open(newline="", encoding="utf-8") as handle:
        return {
            row["osm_class"]: _RoadClass(
                row["hbefa_road_class"], int(row["min_speed_kmh"]), int(row["max_speed_kmh"])
            )
            for row in csv.DictReader(handle)
        }


def _rounded_speed_kmh(speed_meters_per_second: float) -> float:
    """Speed in km/h rounded to a multiple of 10 the way Java's ``Math.round`` does (half up)."""
    speed_kmh = speed_meters_per_second * 3.6
    if math.isinf(speed_kmh) or math.isnan(speed_kmh):
        return speed_kmh
    return math.floor(speed_kmh / 10.0 + 0.5) * 10


def osm_hbefa_road_type(osm_class: str, speed_meters_per_second: float) -> tuple[str, int]:
    """Return ``(hbefa road type, speed shift in km/h)`` for an OSM class and a speed.

    The shift is the clamped speed minus the rounded input speed (0 when the speed lies inside
    the class range; ``+`` raised to the class minimum, ``-`` lowered to the maximum; an
    infinite speed counts as lowered by ``inf``). Raises :class:`UnmappedRoadClass` for a class
    the table does not hold and ``ValueError`` for a speed that is not a positive number.
    """
    if math.isnan(speed_meters_per_second) or speed_meters_per_second <= 0:
        raise ValueError(f"link speed must be positive, got {speed_meters_per_second!r}")
    speed_kmh = _rounded_speed_kmh(speed_meters_per_second)
    road_class = osm_class
    if road_class in ("unclassified", "road"):
        if speed_kmh <= 50:
            road_class = "living"
        elif speed_kmh == 60:
            road_class = "tertiary"
        elif speed_kmh == 70:
            road_class = "secondary"
        elif speed_kmh <= 90:
            road_class = "primary"
        else:
            road_class = "motorway"
    # 'motorway_link', 'living_street', 'primary|railway.tram': only the first part counts.
    cut = min((i for i in (road_class.find(c) for c in "._|,") if i >= 0), default=len(road_class))
    road_class = road_class[:cut]
    if road_class == "motorway" or (road_class == "primary" and speed_kmh >= 90):
        road_class += "-Nat."
    classes = _load_classes()
    if road_class not in classes:
        raise UnmappedRoadClass(f"OSM class {osm_class!r} (normalised {road_class!r}) is not in {_CLASSES_FILE.name}")
    entry = classes[road_class]
    clamped = int(min(max(entry.min_speed_kmh, speed_kmh), entry.max_speed_kmh))
    return f"URB/{entry.name}/{clamped}", clamped - speed_kmh


class RoadTypeTagger:
    """Callable for :func:`braunschweig.emissions.view.build_emissions_view` that also counts.

    ``emitting_network_modes`` are the network modes of vehicles that emit (a link none of them
    may use cannot influence the result). ``max_fallback_length_share`` is the highest share of
    drivable link length allowed on ``fallback_road_type``; :meth:`check` raises above it.
    """

    def __init__(
        self,
        emitting_network_modes: frozenset[str],
        fallback_road_type: str,
        max_fallback_length_share: float,
    ) -> None:
        if not emitting_network_modes:
            raise ValueError("emitting_network_modes must not be empty")
        if not 0.0 <= max_fallback_length_share <= 1.0:
            raise ValueError("max_fallback_length_share must lie in [0, 1]")
        self._modes = emitting_network_modes
        self._fallback = fallback_road_type
        self._max_share = max_fallback_length_share
        self.drivable_links = 0
        self.drivable_length_meters = 0.0
        self.fallback_links = 0
        self.fallback_length_meters = 0.0
        self.fallback_classes: Counter[str] = Counter()
        self.not_drivable_links = 0
        self.speed_raised_links = 0
        self.speed_lowered_links = 0
        self.speed_from_allowed_speed_links = 0
        self.links_by_road_type: Counter[str] = Counter()
        self.length_meters_by_road_type: Counter[str] = Counter()

    def __call__(self, link: LinkRecord) -> str:
        drivable = bool(self._modes.intersection(link.modes.split(",")))
        osm_class = link.attributes.get(OSM_HIGHWAY_ATTRIBUTE)
        allowed = link.attributes.get(ALLOWED_SPEED_ATTRIBUTE)
        speed = float(allowed) if allowed is not None else link.freespeed_meters_per_second
        try:
            if osm_class is None:
                raise UnmappedRoadClass(f"link {link.link_id} has no {OSM_HIGHWAY_ATTRIBUTE}")
            road_type, shift = osm_hbefa_road_type(osm_class, speed)
        except UnmappedRoadClass:
            if not drivable:
                self.not_drivable_links += 1
                return self._fallback
            self.drivable_links += 1
            self.drivable_length_meters += link.length_meters
            self.fallback_links += 1
            self.fallback_length_meters += link.length_meters
            self.fallback_classes[osm_class or "<none>"] += 1
            self._count(self._fallback, link)
            return self._fallback
        if not drivable:
            self.not_drivable_links += 1
            return road_type
        self.drivable_links += 1
        self.drivable_length_meters += link.length_meters
        self.speed_from_allowed_speed_links += allowed is not None
        self.speed_raised_links += shift > 0
        self.speed_lowered_links += shift < 0
        self._count(road_type, link)
        return road_type

    def _count(self, road_type: str, link: LinkRecord) -> None:
        self.links_by_road_type[road_type] += 1
        self.length_meters_by_road_type[road_type] += link.length_meters

    @property
    def fallback_length_share(self) -> float:
        if self.drivable_length_meters == 0:
            return 0.0
        return self.fallback_length_meters / self.drivable_length_meters

    def check(self) -> None:
        """Log primary vs fallback and raise when the fallback share is above the bound."""
        if self.drivable_links == 0:
            raise ValueError(f"no link is usable by the emitting network modes {sorted(self._modes)}")
        primary = self.drivable_links - self.fallback_links
        logger.info(
            "[emissions.road_types] primary %d/%d drivable links (%.1f%%), fallback %d (%.1f%% of links, "
            "%.2f%% of drivable length); speed raised to class minimum on %d, lowered to maximum on %d; "
            "%d links not drivable by %s",
            primary, self.drivable_links, 100.0 * primary / self.drivable_links,
            self.fallback_links, 100.0 * self.fallback_links / self.drivable_links,
            100.0 * self.fallback_length_share, self.speed_raised_links, self.speed_lowered_links,
            self.not_drivable_links, sorted(self._modes),
        )
        if self.fallback_length_share > self._max_share:
            raise ValueError(
                f"{100.0 * self.fallback_length_share:.2f}% of drivable link length has no HBEFA road "
                f"class (allowed {100.0 * self._max_share:.2f}%); unmapped OSM classes: "
                f"{dict(self.fallback_classes.most_common(10))}. Extend {_CLASSES_FILE.name} or raise the bound."
            )

    def report(self) -> dict:
        """Counts for the stage report file."""
        return {
            "method": "port of org.matsim.contrib.emissions.OsmHbefaMapping",
            "emitting_network_modes": sorted(self._modes),
            "drivable_links": self.drivable_links,
            "drivable_length_meters": self.drivable_length_meters,
            "fallback_road_type": self._fallback,
            "fallback_links": self.fallback_links,
            "fallback_length_meters": self.fallback_length_meters,
            "fallback_length_share": self.fallback_length_share,
            "max_fallback_length_share": self._max_share,
            "fallback_osm_classes": dict(self.fallback_classes),
            "not_drivable_links": self.not_drivable_links,
            "speed_raised_to_class_minimum_links": self.speed_raised_links,
            "speed_lowered_to_class_maximum_links": self.speed_lowered_links,
            "speed_from_allowed_speed_links": self.speed_from_allowed_speed_links,
            "links_by_road_type": dict(sorted(self.links_by_road_type.items())),
            "length_meters_by_road_type": dict(sorted(self.length_meters_by_road_type.items())),
        }
