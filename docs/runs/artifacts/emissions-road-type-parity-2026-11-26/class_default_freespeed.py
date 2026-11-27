"""Count the network links whose freespeed equals the pt2matsim default of their OSM class.

pt2matsim sets a link's freespeed to the OSM ``maxspeed`` tag when the way has one it can parse,
otherwise to the default of the way's class (``wayDefaultParams`` of its OSM converter config).
The network does not keep the tag, so "freespeed equals the class default" is the only trace.

What the count means:

* It is an UPPER bound of the links without a ``maxspeed`` tag only where the default could also
  be a signed limit. ASSUMPTION: a default that is a multiple of 10 km/h can be a signed limit
  (``ambiguous``); any other default (15, 25 km/h) cannot (``unambiguous``).
* An exact count needs the ``maxspeed`` tag per way id from the OSM extract.

The defaults are read from the converter config the network was built with (``--osm-config``,
the ``config.xml`` of stage ``matsim.scenario.supply.osm``), never from a table in this script.

Standard library only. Reads only; writes ``--out`` and refuses to overwrite it.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import re
import sys
import xml.etree.ElementTree as ElementTree
from collections import defaultdict
from pathlib import Path

OSM_HIGHWAY_ATTRIBUTE = "osm:way:highway"
#: A freespeed within this distance of the default counts as the default (metres per second).
SPEED_TOLERANCE_METERS_PER_SECOND = 1e-6

_HEAD_ATTRIBUTE = re.compile(r'([\w:]+)="([^"]*)"')
_ATTRIBUTE_LINE = re.compile(r'<attribute name="([^"]+)"[^>]*>([^<]*)</attribute>')


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_class_defaults(path: Path) -> dict[str, float]:
    """``highway`` class -> default freespeed in m/s (default times ``freespeedFactor``)."""
    defaults: dict[str, float] = {}
    for parameter_set in ElementTree.parse(path).getroot().iter("parameterset"):
        if parameter_set.get("type") != "wayDefaultParams":
            continue
        values = {param.get("name"): param.get("value") for param in parameter_set.findall("param")}
        if values.get("osmKey") != "highway":
            continue
        osm_class = values["osmValue"]
        if osm_class in defaults:
            raise ValueError(f"{path}: highway class {osm_class!r} has two wayDefaultParams")
        defaults[osm_class] = float(values["freespeed"]) * float(values.get("freespeedFactor", "1.0"))
    if not defaults:
        raise ValueError(f"{path} holds no wayDefaultParams for highway classes")
    return defaults


def is_ambiguous(default_meters_per_second: float) -> bool:
    kmh = default_meters_per_second * 3.6
    return math.isclose(kmh / 10.0, round(kmh / 10.0), abs_tol=1e-6)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--osm-config", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--emitting-network-modes", default="car,truck")
    args = parser.parse_args(argv)
    for path in (args.network, args.osm_config):
        if not path.is_file():
            parser.error(f"input file not found: {path}")
    if args.out.exists():
        parser.error(f"refusing to overwrite {args.out}")
    modes = frozenset(mode for mode in args.emitting_network_modes.split(",") if mode)
    defaults = read_class_defaults(args.osm_config)

    per_class = defaultdict(lambda: {"links": 0, "length_km": 0.0, "links_on_default": 0, "length_km_on_default": 0.0})
    links = drivable = 0
    head: dict[str, str] | None = None
    osm_class: str | None = None
    with gzip.open(args.network, "rt", encoding="utf-8") as reader:
        for line in reader:
            if "<link " in line:
                head = dict(_HEAD_ATTRIBUTE.findall(line))
                osm_class = None
                links += 1
            if head is not None and OSM_HIGHWAY_ATTRIBUTE in line:
                match = _ATTRIBUTE_LINE.search(line)
                if match and match.group(1) == OSM_HIGHWAY_ATTRIBUTE:
                    osm_class = match.group(2)
            if head is not None and ("</link>" in line or line.rstrip().endswith("/>") and "<link " in line):
                if modes.intersection(head.get("modes", "").split(",")):
                    drivable += 1
                    entry = per_class[osm_class or "<none>"]
                    length_km = float(head["length"]) / 1000.0
                    entry["links"] += 1
                    entry["length_km"] += length_km
                    default = defaults.get(osm_class or "")
                    if default is not None and abs(float(head["freespeed"]) - default) <= SPEED_TOLERANCE_METERS_PER_SECOND:
                        entry["links_on_default"] += 1
                        entry["length_km_on_default"] += length_km
                head = None
    if links == 0 or drivable == 0:
        raise ValueError(f"{args.network}: {links} links, {drivable} drivable by {sorted(modes)}")

    classes = {}
    totals = {kind: {"links_on_default": 0, "length_km_on_default": 0.0} for kind in ("unambiguous", "ambiguous")}
    for name, entry in sorted(per_class.items()):
        default = defaults.get(name)
        kind = None if default is None else ("ambiguous" if is_ambiguous(default) else "unambiguous")
        classes[name] = {
            "class_default_kmh": None if default is None else round(default * 3.6, 3),
            "default_kind": kind or "no_default_for_class",
            "links": entry["links"],
            "length_km": round(entry["length_km"], 3),
            "links_on_default": entry["links_on_default"],
            "length_km_on_default": round(entry["length_km_on_default"], 3),
            "share_of_class_links_on_default": round(entry["links_on_default"] / entry["links"], 6),
        }
        if kind:
            totals[kind]["links_on_default"] += entry["links_on_default"]
            totals[kind]["length_km_on_default"] += entry["length_km_on_default"]
    total_length_km = sum(entry["length_km"] for entry in per_class.values())
    for kind in totals:
        totals[kind]["length_km_on_default"] = round(totals[kind]["length_km_on_default"], 3)
        totals[kind]["share_of_drivable_links"] = round(totals[kind]["links_on_default"] / drivable, 6)
        totals[kind]["share_of_drivable_length"] = round(totals[kind]["length_km_on_default"] / total_length_km, 6)

    result = {
        "question": "how many drivable links carry the pt2matsim class default as freespeed",
        "inputs": {
            "network": {"path": str(args.network), "sha256": sha256_of(args.network)},
            "osm_config": {"path": str(args.osm_config), "sha256": sha256_of(args.osm_config)},
            "emitting_network_modes": sorted(modes),
            "speed_tolerance_meters_per_second": SPEED_TOLERANCE_METERS_PER_SECOND,
        },
        "assumption": "a class default that is a multiple of 10 km/h can also be a signed limit (ambiguous: upper bound); "
                      "any other default cannot (unambiguous)",
        "links": links,
        "drivable_links": drivable,
        "drivable_length_km": round(total_length_km, 3),
        "on_class_default": totals,
        "by_osm_class": classes,
    }
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    for kind, total in totals.items():
        print(f"[class-default] {kind}: {total['links_on_default']} of {drivable} drivable links "
              f"({100.0 * total['share_of_drivable_links']:.1f}%), {100.0 * total['share_of_drivable_length']:.1f}% of length")
    return 0


if __name__ == "__main__":
    sys.exit(main())
