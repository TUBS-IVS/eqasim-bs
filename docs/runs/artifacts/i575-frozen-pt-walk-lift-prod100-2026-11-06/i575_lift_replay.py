"""#575 group 3 (ADR-0189): replay of the frozen pt walk lift on the prepared population of prod100_20261031.

Read-only on the run directory. Applies ``frozen_pt_walk_lift.lift_frozen_pt_walks`` of the GIVEN code to the
prepared population the run's MATSim read (``--population``) and writes the lifted copy and the report to ``--out``.
Then writes a small population of the first ``--sample`` persons that got a lifted trip (``lifted_sample.xml.gz``,
the run's header kept) and the same persons from the unlifted file (``original_sample.xml.gz``), for a MATSim
start-up smoke with ``lastIteration 0``. The step's distance bound is the plan check's long-walk bound
(``braunschweig.plan_check.pt_as_walk_bound_min_distance_m``), passed as ``--min-distance-m``.

Usage on felix (eqasim env):
  python i575_lift_replay.py --code ~/i575p_replay/eqasim-bs --population <prepare cache>/<prefix>population.xml.gz \
      --out ~/scratch_i575p/replay --min-distance-m 5000 --warn-share 0.05 --sample 30
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path


def sample(source: Path, target: Path, header: str, persons: set) -> int:
    """Copy the persons ``persons`` of ``source`` into ``target`` under ``header``; returns how many were written."""
    written, root = 0, None
    with gzip.open(source, "rb") as handle, gzip.open(target, "wt", encoding="utf-8") as out:
        out.write(header)
        for event, element in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                root = root if root is not None else element
                continue
            if element.tag == "person":
                if element.get("id") in persons:
                    out.write(ET.tostring(element, encoding="unicode"))
                    written += 1
                element.clear()
                root.clear()
        out.write("</population>\n")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", required=True)
    parser.add_argument("--population", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--min-distance-m", type=float, required=True)
    parser.add_argument("--warn-share", type=float, required=True)
    parser.add_argument("--sample", type=int, default=30)
    args = parser.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(Path(args.code).expanduser()))
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    from braunschweig.matsim.simulation import frozen_pt_walk_lift as lift

    source = Path(args.population).expanduser()
    lifted = out / "lifted_population.xml.gz"
    t0 = time.time()
    report = lift.lift_frozen_pt_walks(source, lifted, min_distance_m=args.min_distance_m, warn_share=args.warn_share)
    report["seconds"] = round(time.time() - t0)
    (out / "frozen_pt_walk_lift.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)

    persons, root = [], None
    with gzip.open(lifted, "rb") as handle:
        for event, element in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                root = root if root is not None else element
                continue
            if element.tag == "person":
                if len(persons) < args.sample and any(
                        a.get("name") == "liftedFrom" for a in element.iter("attribute")):
                    persons.append(element.get("id"))
                element.clear()
                root.clear()
    with gzip.open(source, "rt", encoding="utf-8") as handle:
        text = handle.read(20000)
    header = text[:text.index(">", text.index("<population")) + 1] + "\n"
    chosen = set(persons)
    n_lifted = sample(lifted, out / "lifted_sample.xml.gz", header, chosen)
    n_original = sample(source, out / "original_sample.xml.gz", header, chosen)
    (out / "sample_persons.txt").write_text("\n".join(persons) + "\n", encoding="utf-8")
    print(f"sample persons {len(persons)}: lifted file {n_lifted}, original file {n_original}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
