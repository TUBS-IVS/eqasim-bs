"""Reduce the per-link table of ``OsmHbefaParityDump.java`` to its distinct cases.

One row per combination of OSM class, freespeed and road type of the ORIGINAL that occurs on the
links usable by an emitting network mode, with the number of links. The result is committed as
``braunschweig/emissions/osm_hbefa_parity_cases.csv`` and is what the port is tested against.

Refuses a table in which the original threw on a usable link or read an ``allowed_speed``
attribute: the cases then would not describe the port's input completely.

Standard library only. Writes ``--out`` and refuses to overwrite it.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--java-table", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--emitting-network-modes", default="car,truck")
    args = parser.parse_args(argv)
    if not args.java_table.is_file():
        parser.error(f"input file not found: {args.java_table}")
    if args.out.exists():
        parser.error(f"refusing to overwrite {args.out}")
    modes = frozenset(mode for mode in args.emitting_network_modes.split(",") if mode)

    cases: Counter[tuple[str, str, str]] = Counter()
    links = 0
    with args.java_table.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if not modes.intersection(row["modes"].split(",")):
                continue
            links += 1
            if row["java_error"] or not row["java_road_type"]:
                raise ValueError(f"link {row['link_id']}: the original returned no road type ({row['java_error']!r})")
            if row["allowed_speed_attribute"]:
                raise ValueError(f"link {row['link_id']} carries allowed_speed; the cases would need that column")
            if not row["osm_highway"]:
                raise ValueError(f"link {row['link_id']} is usable but has no OSM class")
            cases[(row["osm_highway"], row["freespeed_meters_per_second"], row["java_road_type"])] += 1
    if not cases:
        raise ValueError(f"{args.java_table}: no link usable by {sorted(modes)}")

    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["osm_class", "freespeed_meters_per_second", "java_road_type", "links"])
        for (osm_class, speed, road_type), count in sorted(cases.items(), key=lambda item: (item[0][0], float(item[0][1]))):
            writer.writerow([osm_class, speed, road_type, count])
    print(f"[parity-cases] {len(cases)} cases from {links} usable links -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
