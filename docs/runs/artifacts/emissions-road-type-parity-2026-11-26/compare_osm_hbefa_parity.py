"""Compare the Python port of ``OsmHbefaMapping`` with the Java original, link by link.

Inputs: the network of a run archive, and the table ``OsmHbefaParityDump.java`` wrote for the same
network with the contrib class of the pipeline jar. The port is exercised through
``braunschweig.emissions.road_types.RoadTypeTagger``, the object the emissions stages use, with a
sentinel as fallback road type so a fallback is told apart from a mapped link.

Run from the repository root with the project Python::

    python compare_osm_hbefa_parity.py --network <output_network.xml.gz> --java-table <dump.tsv> \
        --jar <pipeline jar> --out <result.json>

Reads only; writes ``--out`` and refuses to overwrite it. A link counts as EQUAL when both sides
give the same road type, or when both refuse the link (Java throws, the port falls back).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

from braunschweig.emissions import road_types, view

#: Returned by the tagger instead of a road type when the port takes its fallback.
FALLBACK_SENTINEL = "__PORT_FALLBACK__"
#: How many distinct difference patterns the result file lists in full.
MAX_LISTED_PATTERNS = 200


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_java_table(path: Path) -> dict[str, dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = {}
        for row in csv.DictReader(handle, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row["link_id"] in rows:
                raise ValueError(f"{path}: link {row['link_id']!r} occurs twice")
            rows[row["link_id"]] = row
    if not rows:
        raise ValueError(f"{path} holds no links")
    return rows


def outcome(java_road_type: str, java_error: str, port_road_type: str) -> str:
    java_refuses = java_road_type == ""
    port_refuses = port_road_type == FALLBACK_SENTINEL
    if java_refuses and not java_error:
        raise ValueError("Java table row has neither a road type nor an error")
    if java_refuses and port_refuses:
        return "equal_both_refuse"
    if java_refuses:
        return "different_java_refuses_port_maps"
    if port_refuses:
        return "different_java_maps_port_refuses"
    return "equal_same_road_type" if java_road_type == port_road_type else "different_road_type"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--network", type=Path, required=True)
    parser.add_argument("--java-table", type=Path, required=True)
    parser.add_argument("--jar", type=Path, required=True, help="the jar the Java table was produced with (hashed only)")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--emitting-network-modes", default="car,truck",
                        help="network modes of emitting vehicles; a link none of them may use is reported apart")
    parser.add_argument("--port-commit", default=None,
                        help="commit the port code was taken from, when it is not run inside a git checkout")
    args = parser.parse_args(argv)

    for path in (args.network, args.java_table, args.jar):
        if not path.is_file():
            parser.error(f"input file not found: {path}")
    if args.out.exists():
        parser.error(f"refusing to overwrite {args.out}")
    modes = frozenset(mode for mode in args.emitting_network_modes.split(",") if mode)

    java_rows = read_java_table(args.java_table)
    tagger = road_types.RoadTypeTagger(modes, FALLBACK_SENTINEL, max_fallback_length_share=1.0)

    counts = {"drivable": Counter(), "not_drivable": Counter()}
    length_km = {"drivable": Counter(), "not_drivable": Counter()}
    patterns: Counter[tuple] = Counter()
    examples: dict[tuple, str] = {}
    java_errors: Counter[str] = Counter()
    port_errors: Counter[str] = Counter()
    seen = set()

    with view._open_gz_read(args.network) as reader:
        for is_block, lines in view._blocks(reader, "<link ", "</link>"):
            if not is_block:
                continue
            record = view._link_record(lines)
            if record.link_id not in java_rows:
                raise ValueError(f"link {record.link_id!r} is in the network but not in the Java table")
            seen.add(record.link_id)
            java = java_rows[record.link_id]
            group = "drivable" if modes.intersection(record.modes.split(",")) else "not_drivable"
            try:
                port_road_type = tagger(record)
            except ValueError as error:
                # The tagger lets an invalid speed through as an error; the stage would stop on it.
                port_road_type = FALLBACK_SENTINEL
                port_errors[f"{group}: {type(error).__name__}"] += 1
            if java["java_error"]:
                java_errors[f"{group}: {java['java_error']}"] += 1
            result = outcome(java["java_road_type"], java["java_error"], port_road_type)
            counts[group][result] += 1
            length_km[group][result] += record.length_meters / 1000.0
            if result.startswith("different"):
                osm_class = record.attributes.get(road_types.OSM_HIGHWAY_ATTRIBUTE, "<none>")
                pattern = (group, result, osm_class, f"{record.freespeed_meters_per_second * 3.6:.2f}",
                           java["java_road_type"] or java["java_error"], port_road_type)
                patterns[pattern] += 1
                examples.setdefault(pattern, record.link_id)

    missing = sorted(set(java_rows) - seen)
    if missing:
        raise ValueError(f"{len(missing)} links are in the Java table but not in the network, e.g. {missing[:5]}")

    def summary(group: str) -> dict:
        total = sum(counts[group].values())
        equal = sum(n for key, n in counts[group].items() if key.startswith("equal"))
        return {
            "links": total,
            "equal_links": equal,
            "different_links": total - equal,
            "links_by_outcome": dict(sorted(counts[group].items())),
            "length_km_by_outcome": {key: round(value, 3) for key, value in sorted(length_km[group].items())},
        }

    commit = args.port_commit
    if commit is None:
        try:
            commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
        except (OSError, subprocess.CalledProcessError):
            commit = "unknown"

    drivable = summary("drivable")
    result = {
        "question": "does braunschweig.emissions.road_types give the road type of org.matsim.contrib.emissions.OsmHbefaMapping",
        "inputs": {
            "network": {"path": str(args.network), "sha256": sha256_of(args.network)},
            "java_table": {"path": str(args.java_table), "sha256": sha256_of(args.java_table)},
            "jar": {"path": str(args.jar), "sha256": sha256_of(args.jar)},
            "class_table": {"path": str(road_types._CLASSES_FILE.name), "sha256": sha256_of(road_types._CLASSES_FILE)},
            "port_modules": {
                Path(module.__file__).name: sha256_of(Path(module.__file__)) for module in (road_types, view)
            },
            "repository_commit": commit,
            "emitting_network_modes": sorted(modes),
        },
        "equal_on_every_drivable_link": drivable["different_links"] == 0 and drivable["links"] > 0,
        "drivable": drivable,
        "not_drivable": summary("not_drivable"),
        "java_exceptions": dict(java_errors.most_common()),
        "port_errors": dict(port_errors.most_common()),
        "difference_patterns_total": len(patterns),
        "difference_patterns": [
            {"group": p[0], "outcome": p[1], "osm_class": p[2], "freespeed_kmh": p[3], "java": p[4], "port": p[5],
             "links": n, "example_link_id": examples[p]}
            for p, n in patterns.most_common(MAX_LISTED_PATTERNS)
        ],
        "port_report": tagger.report(),
    }
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"[parity] drivable links {drivable['links']}: equal {drivable['equal_links']}, "
          f"different {drivable['different_links']}")
    print(f"[parity] not drivable links {result['not_drivable']['links']}: equal {result['not_drivable']['equal_links']}, "
          f"different {result['not_drivable']['different_links']}")
    print(f"[parity] equal on every drivable link: {result['equal_on_every_drivable_link']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
