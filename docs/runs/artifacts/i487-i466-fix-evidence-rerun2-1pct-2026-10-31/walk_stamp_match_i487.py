"""Planned-pt trips realised as walk vs trips stamped walkFallbackFrom=pt, per resident class. Read-only (#487).

Usage: python walk_stamp_match_i487.py <eqasim-bs checkout> <scenario population.xml.gz> <prepared population.xml.gz>

The plan check (braunschweig.matsim.simulation.plan_check.summarise) counts a pt-intended trip as "pt as walk" only
when one of its legs carries the stamp walkFallbackFrom=pt. This script checks that count against an independent
reference that does not read the stamp: every walk-only trip of the PREPARED plan (all legs walk/non_network_walk) is
matched to the SCENARIO-population trip (before prepare) of the same person whose preceding activity ends at the same
second, and the planned mode of that trip is read. Classes (frozen / in_region) and trip splitting use the plan
check's own helpers, so both counts use one definition. Walk-only trips without an exact departure match (the cutter
replaces outside activities) are reported separately, never guessed.
"""
import collections
import sys

checkout, scenario_path, prepared_path = sys.argv[1:4]
sys.path.append(checkout)
from braunschweig.matsim.simulation import plan_check as pc  # noqa: E402

RESIDENTS = "residents"

# Scenario population: (person, departure_s) -> planned mode of the trip leaving at that second.
planned = {}
trips_of = collections.defaultdict(list)  # person -> [(departure_s, mode, from_type, to_type)]
for person in pc.iter_person_plans(scenario_path):
    if person.subpopulation != RESIDENTS:
        continue
    for trip in pc.split_trips(person.activities, len(person.legs)):
        departure = person.activities[trip[0]][1]
        if departure is not None:
            planned[(person.person_id, departure)] = person.legs[trip[0]][0]
            trips_of[person.person_id].append((departure, person.legs[trip[0]][0], person.activities[trip[0]][0],
                                               person.activities[trip[-1] + 1][0]))

counts = collections.Counter()
mismatches = []
unmatched = []
for person in pc.iter_person_plans(prepared_path):
    if person.subpopulation != RESIDENTS:
        continue
    frozen = pc.leg_tour_frozen(person.activities)
    for trip in pc.split_trips(person.activities, len(person.legs)):
        legs = [person.legs[i] for i in trip]
        if not all(leg[0] in pc.WALK_MODES for leg in legs):
            continue
        klass = pc.FROZEN if frozen[trip[0]] else pc.IN_REGION
        stamped = any(leg[2] == "pt" for leg in legs)
        departure = person.activities[trip[0]][1]
        mode = planned.get((person.person_id, departure)) if departure is not None else None
        counts[(klass, "walk_only_trips")] += 1
        counts[(klass, "stamped")] += stamped
        if mode is None:
            counts[(klass, "no_exact_match")] += 1
            counts[(klass, "no_exact_match_stamped")] += stamped
            # Diagnostic only: the scenario trip with the same destination type and the nearest departure.
            to_type = person.activities[trip[-1] + 1][0]
            from_type = person.activities[trip[0]][0]
            candidates = [t for t in trips_of.get(person.person_id, []) if t[3] == to_type] or trips_of.get(person.person_id, [])
            near = min(candidates, key=lambda t: abs(t[0] - (departure or 0))) if candidates else None
            unmatched.append((klass, person.person_id, from_type, to_type, (departure or 0) / 3600.0,
                              near[1] if near else None, abs(near[0] - (departure or 0)) / 3600.0 if near else -1, stamped))
            continue
        planned_pt = mode == "pt"
        counts[(klass, "planned_pt")] += planned_pt
        counts[(klass, "planned_pt_stamped")] += planned_pt and stamped
        counts[(klass, "stamped_not_planned_pt")] += stamped and not planned_pt
        if planned_pt != stamped:
            mismatches.append((klass, person.person_id, round(departure / 3600.0, 2), mode, stamped))

for klass in (pc.FROZEN, pc.IN_REGION):
    print(f"{RESIDENTS}/{klass}: " + ", ".join(f"{key} {counts[(klass, key)]}" for key in (
        "walk_only_trips", "stamped", "planned_pt", "planned_pt_stamped", "stamped_not_planned_pt",
        "no_exact_match", "no_exact_match_stamped")))
print(f"mismatches (planned pt != stamped), exact matches only: {len(mismatches)}")
for row in mismatches[:40]:
    print("  class %s person %s dep_h %.2f planned %s stamped %s" % row)

print("unmatched walk-only trips (no exact departure match): from->to, planned mode of nearest same-destination trip")
print("  by (from, to, nearest planned, stamped):", dict(collections.Counter((u[0], u[2], u[3], u[5], u[7]) for u in unmatched)))
big = [u for u in unmatched if u[6] > 1.0]
print(f"  nearest match more than 1 h away: {len(big)}")
