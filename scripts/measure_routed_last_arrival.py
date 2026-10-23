"""Count the plans of a routed MATSim population whose ROUTED last arrival is late (ADR-0160, eqasim-bs#465).

Every selected plan is replayed as the QSim plans it (``activityDurationInterpretation`` tryEndTimeThenDuration):
an activity with an end time ends at the later of its end time and the arrival, an activity with only a
``max_dur`` ends that long after the arrival, a leg takes its routed ``trav_time``. The same replay is
``org.eqasim.core.scenario.routing.DayEndShift.plannedLastArrival`` in eqasim-java-bs.

Prints, per subpopulation and class (``frozen``: the plan has an ``outside`` activity, the mode choice never
re-chooses it; ``in_region``: everything else), the persons and those whose routed last arrival is after 27, 28,
29 and 30 h, plus up to eight examples (person id, routed last arrival h, written start of the last activity h).
Only aggregate counts and ids are printed; the population is streamed.

Usage: python scripts/measure_routed_last_arrival.py <population.xml.gz>
"""
from __future__ import annotations

import argparse
import collections
import gzip
import xml.etree.ElementTree as ET

THRESHOLDS_H = (27, 28, 29, 30)
N_EXAMPLES = 8


def parse_time_s(text):
    if text is None:
        return None
    hours, minutes, seconds = text.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def routed_last_arrival_s(plan) -> tuple:
    """(routed last arrival s or None, frozen, written start of the last main activity s or None)."""
    time = 0.0
    last_arrival = None
    frozen = False
    last_start = None
    for item in plan:
        if item.tag == "activity":
            if item.get("type") == "outside":
                frozen = True
            if "interaction" not in item.get("type"):
                last_start = parse_time_s(item.get("start_time"))
            end, duration = parse_time_s(item.get("end_time")), parse_time_s(item.get("max_dur"))
            if end is not None:
                time = max(time, end)
            elif duration is not None:
                time += duration
        elif item.tag == "leg":
            time += parse_time_s(item.get("trav_time")) or 0.0
            last_arrival = time
    return last_arrival, frozen, last_start


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("population", help="routed MATSim population (.xml.gz)")
    args = parser.parse_args(argv)

    persons, late = collections.Counter(), collections.Counter()
    examples = collections.defaultdict(list)
    with gzip.open(args.population) as stream:
        for _, element in ET.iterparse(stream, events=("end",)):
            if element.tag != "person":
                continue
            subpopulation = next((a.text for a in element.iter("attribute") if a.get("name") == "subpopulation"),
                                 "residents")
            plan = next((p for p in element.findall("plan") if p.get("selected", "yes") == "yes"), None)
            if plan is not None:
                last_arrival, frozen, last_start = routed_last_arrival_s(plan)
                key = (subpopulation, "frozen" if frozen else "in_region")
                persons[key] += 1
                if last_arrival is not None:
                    for threshold in THRESHOLDS_H:
                        if last_arrival > threshold * 3600:
                            late[key + (threshold,)] += 1
                    if last_arrival > 29 * 3600 and len(examples[key]) < N_EXAMPLES:
                        examples[key].append((element.get("id"), round(last_arrival / 3600, 2),
                                              None if last_start is None else round(last_start / 3600, 2)))
            element.clear()

    for key in sorted(persons):
        print(key, "persons", persons[key], {f">{h}h": late[key + (h,)] for h in THRESHOLDS_H})
        print("   examples (id, routed last arrival h, written start of the last activity h)", examples[key])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
