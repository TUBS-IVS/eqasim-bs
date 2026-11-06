"""#575 ADR-0189 smoke check: did MATSim route and simulate the lifted legs of the sample?

Read-only. Compares the lifted input sample with the smoke's ``output_plans.xml.gz`` and ``output_events.xml.gz``:
per lifted input leg (attribute ``liftedFrom``), the output plan's trip at the same departure time (its legs' modes,
route type and travel time) and, from the events, the person's ``departure``/``arrival`` events of mode
``car_passenger`` and any ``stuckAndAbort``. Prints counts and one line per lifted leg.

Usage: python i575_smoke_check.py <lifted_sample.xml.gz> <smoke output dir>
"""
import collections
import gzip
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def plans(path):
    out = {}
    for _, element in ET.iterparse(gzip.open(path, "rb")):
        if element.tag == "person":
            plan = next((p for p in element.findall("plan") if p.get("selected") == "yes"), element.find("plan"))
            out[element.get("id")] = list(plan)
            element.clear()
    return out


def main() -> int:
    sample, smoke = Path(sys.argv[1]), Path(sys.argv[2])
    before = plans(sample)
    after = plans(smoke / "output_plans.xml.gz")
    events = collections.Counter()
    stuck = []
    for _, element in ET.iterparse(gzip.open(smoke / "output_events.xml.gz", "rb")):
        if element.tag == "event":
            kind, mode = element.get("type"), element.get("legMode") or element.get("mode")
            if kind in ("departure", "arrival") and mode == "car_passenger":
                events[(element.get("person"), kind)] += 1
            if kind == "stuckAndAbort":
                stuck.append((element.get("person"), element.get("legMode"), element.get("time")))
            element.clear()
    n_lifted = n_routed = 0
    for person, elements in before.items():
        for leg in (e for e in elements if e.tag == "leg"):
            if not any(a.get("name") == "liftedFrom" for a in leg.iter("attribute")):
                continue
            n_lifted += 1
            dep = leg.get("dep_time")
            match = [e for e in after.get(person, []) if e.tag == "leg" and e.get("dep_time") == dep]
            route = match[0].find("route") if match else None
            modes = [e.get("mode") for e in after.get(person, []) if e.tag == "leg"]
            routed = bool(match) and route is not None
            n_routed += routed
            print(f"person {person} dep {dep}: output leg {match[0].get('mode') if match else None} "
                  f"route {route.get('type') if route is not None else None} trav {match[0].get('trav_time') if match else None}; "
                  f"car_passenger events dep/arr {events[(person, 'departure')]}/{events[(person, 'arrival')]}; plan modes {modes}")
    print(f"lifted legs {n_lifted}, with a routed output leg at the same departure {n_routed}; persons {len(before)}; "
          f"stuckAndAbort {len(stuck)} {stuck[:5]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
