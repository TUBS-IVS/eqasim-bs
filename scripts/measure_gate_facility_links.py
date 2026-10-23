"""Measure the gate facility link rule (#465, ADR-0167) on a real pre-cut network.

For every road gate of ``braunschweig.synthesis.cordon_gates`` and every combination of the swept minimum capacity
and maximum distance, applies ``braunschweig.data.cordon.gate_facility_links.select_gate_facility_links`` and writes

- ``<out>/gate_facility_links_sweep.csv``: one row per (minimum capacity, maximum distance, gate) with the status,
  reason, entry link, facility link and their road classes and capacities;
- ``<out>/gate_facility_links_sweep_summary.csv``: the fallback share of the gates per parameter pair.

Inputs are the cached synpp artefacts of a run (read only): the ``matsim.scenario.supply.processed`` network, the
pickled ``braunschweig.synthesis.cordon_gates`` output and the pickled ``data.spatial.municipalities`` output. The
extent is built exactly as ``matsim.simulation.prepare`` builds it for the cutter.

Usage::

    python scripts/measure_gate_facility_links.py --network <supply cache>/network.xml.gz \\
        --gates <cache>/braunschweig.synthesis.cordon_gates__<hash>.p \\
        --municipalities <cache>/data.spatial.municipalities__<hash>.p --buffer-fraction 0.10 --out <dir>
"""
from __future__ import annotations

import argparse
import gzip
import os
import pickle
import sys
import xml.etree.ElementTree as ET

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from braunschweig.data.cordon import gate_facility_links as gfl  # noqa: E402
from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction  # noqa: E402

MINIMUM_CAPACITIES_VEH_PER_H = (600.0, 1000.0, 1500.0)
MAXIMUM_DISTANCES_M = (50.0, 100.0, 200.0, 300.0, 500.0)


def _road_classes(network_path):
    classes = {}
    with gzip.open(network_path, "rb") as handle:
        link_id = None
        for event, elem in ET.iterparse(handle, events=("start", "end")):
            if event == "start" and elem.tag == "link":
                link_id = elem.get("id")
            elif event == "end" and elem.tag == "attribute" and link_id and elem.get("name") == "osm:way:highway":
                classes[link_id] = elem.text
            elif event == "end" and elem.tag == "link":
                link_id = None
                elem.clear()
    return classes


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--network", required=True)
    parser.add_argument("--gates", required=True)
    parser.add_argument("--municipalities", required=True)
    parser.add_argument("--buffer-fraction", type=float, required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    with open(args.gates, "rb") as handle:
        gates = pickle.load(handle)["gates"]
    with open(args.municipalities, "rb") as handle:
        municipalities = pickle.load(handle)
    extent = build_cordon_polygon(municipalities, buffer_m_from_fraction(municipalities, args.buffer_fraction))
    links = gfl.read_car_links(args.network)
    classes = _road_classes(args.network)
    capacity = dict(zip(links["link_id"].astype(str), links["capacity_veh_per_h"]))

    rows = []
    for minimum_capacity in MINIMUM_CAPACITIES_VEH_PER_H:
        for maximum_distance in MAXIMUM_DISTANCES_M:
            selection = gfl.select_gate_facility_links(gates, links, extent, minimum_capacity, maximum_distance)
            selection = selection.merge(gates[["gate_id", "link_id", "road_class", "capacity"]]
                                        .rename(columns={"link_id": "gate_link_id", "road_class": "gate_road_class",
                                                         "capacity": "gate_link_capacity"}), on="gate_id")
            selection["entry_road_class"] = selection["entry_link_id"].map(classes)
            selection["facility_road_class"] = selection["facility_link_id"].map(classes)
            selection["entry_capacity_veh_per_h"] = selection["entry_link_id"].map(capacity)
            selection.insert(0, "maximum_distance_m", maximum_distance)
            selection.insert(0, "minimum_capacity_veh_per_h", minimum_capacity)
            rows.append(selection)
    sweep = pd.concat(rows, ignore_index=True)
    os.makedirs(args.out, exist_ok=True)
    sweep.to_csv(os.path.join(args.out, "gate_facility_links_sweep.csv"), index=False)
    summary = (sweep.assign(fallback=sweep["status"] == gfl.STATUS_FALLBACK)
               .groupby(["minimum_capacity_veh_per_h", "maximum_distance_m"])
               .agg(n_gates=("gate_id", "size"), n_fallback=("fallback", "sum")).reset_index())
    summary["fallback_share"] = summary["n_fallback"] / summary["n_gates"]
    summary.to_csv(os.path.join(args.out, "gate_facility_links_sweep_summary.csv"), index=False)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
