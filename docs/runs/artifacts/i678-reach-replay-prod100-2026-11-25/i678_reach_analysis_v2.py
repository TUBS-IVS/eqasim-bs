"""#678 B9 step 2: residents-only counts of the replay of the reach steps on prod100_20261031.

Reads four populations of the replay (all read-only) and classifies every resident trip with the branch's own rules
(``plan_check.leg_reach``, ``plan_check.unfit_mode``, ``plan_check.home_tours``, ``plan_check.split_trips``):

- UNCUT: the run's uncut population. Gives the person kind: a resident has no subpopulation attribute and no ``ic_``
  facility in its plan (the rule of the "before" probe); an in-commuter has an ``ic_`` facility (prod100_20261031
  predates #566, so its in-commuters carry no subpopulation attribute). Also the home-based tours without an
  ``outside`` activity with more than 6 trips (the tours of the "before" manifest's tour length row).
- INPUT: the run's prepared population (what the replay started from).
- AFTER_INITIAL: the population after the initial mode choice (before the lift).
- FINAL: the population after the lift (what MATSim would read).

Settings compared: TODAY = (isolate false, length 6), the loop rule of the run; LOOP = the branch's loop settings
(isolate false, length 11); INITIAL = the branch's initial settings (isolate true, length 11).

A trip's mode is ``plan_check.trip_mode`` (routing mode of its first leg, else its mode). A "plain walk" is a trip
``unfit_mode`` judges as walk (mode walk, only walk legs, not pt-intended) when it is over the walk bound. "Border
tour" is a home-based tour (``plan_check.home_tours``) with a trip whose origin or destination is ``outside``.

Relaxed vehicle legs (``BorderTourVehicleConstraint.Factory`` of eqasim-java-bs at ae6a10a1): a trip of a border tour
whose initial mode (here: ``trip_mode`` of the INPUT plan) is car or bicycle, while ``BraunschweigModeAvailability``
does not offer that mode to the person: car needs carAvailability != none and hasLicense != no, bicycle needs
bicycleAvailability != none (a missing attribute counts as available, as in BraunschweigPredictorUtils). Counted for
every person kind, because the runner replanned every person (no excluded subpopulation).

Second version (after the freight fix, commit 884627f0): also compares the plans of the other person kinds between
the files (freight must stay unchanged; the pre-#566 in-commuters are re-chosen), stores the mode and unfit class of
every resident trip after the initial choice, and splits the lifted trips into unfit ones and bicycle trips taken
along with their tour (lifted although their own mode fitted after the initial choice).

Usage: python i678_reach_analysis.py <eqasim-bs clone> <uncut> <input> <after_initial> <final> <out json> [limit]
Pass ``-`` for both <after_initial> and <final> to count UNCUT and INPUT only. With ``limit`` only the first persons of every file are read (smoke test; persons missing in a later file are
skipped instead of raising).
"""
from __future__ import annotations

import collections
import gzip
import json
import math
import sys
import time
import xml.etree.ElementTree as ET

clone, uncut_path, input_path, after_path, final_path, out_path = sys.argv[1:7]
LIMIT = int(sys.argv[7]) if len(sys.argv) > 7 else None
sys.path.append(clone)

from braunschweig.matsim.simulation import plan_check as pc  # noqa: E402
from braunschweig.matsim.simulation.plan_check import FIXED_BORDER, FIXED_LENGTH, FIXED_VEHICLE, REACHED  # noqa: E402

SETTINGS = {"today": (False, 6), "loop": (False, 11), "initial": (True, 11)}
WALK_BOUND_M, BICYCLE_BOUND_M, PT_BOUND_M = 5000.0, 30000.0, 5000.0
RESTRICTED = ("car", "bicycle")
T0 = time.time()


def log(message):
    print(f"[{time.time() - T0:7.0f} s] {message}", flush=True)


def iter_persons(path):
    """(id, subpopulation or None, attributes dict, activities, legs, lifted_from per leg) of the selected plan.

    activities: (type, end_s, x, y, facility); legs: plan_check tuples (mode, None, walkFallbackFrom, routingMode).
    """
    root, seen = None, 0
    with gzip.open(path, "rb") as handle:
        for event, element in ET.iterparse(handle, events=("start", "end")):
            if event == "start":
                root = element if root is None else root
                continue
            if element.tag != "person":
                continue
            attributes = {}
            node = element.find("attributes")
            if node is not None:
                for attribute in node.findall("attribute"):
                    attributes[attribute.get("name")] = (attribute.text or "").strip()
            plan = next((p for p in element.findall("plan") if p.get("selected") == "yes"), element.find("plan"))
            activities, legs, lifted = [], [], []
            if plan is not None:
                for child in plan:
                    if child.tag == "activity":
                        end = child.get("end_time")
                        x, y = child.get("x"), child.get("y")
                        activities.append((child.get("type"), pc.parse_time_s(end) if end else None,
                                           float(x) if x is not None else math.nan,
                                           float(y) if y is not None else math.nan, child.get("facility")))
                    elif child.tag == "leg":
                        legs.append((child.get("mode"), None, pc.leg_attribute(child, pc.WALK_FALLBACK_ATTRIBUTE),
                                     pc.leg_attribute(child, pc.ROUTING_MODE_ATTRIBUTE)))
                        lifted.append(pc.leg_attribute(child, "liftedFrom"))
            yield element.get("id"), attributes.get("subpopulation") or None, attributes, activities, legs, lifted
            element.clear()
            root.clear()
            seen += 1
            if LIMIT and seen >= LIMIT:
                return


def main_tours(activities):
    """Home-based tours of the main activities (interaction activities dropped), split at every home activity after
    the first activity (the rule of the "before" probe): list of (n_trips, touches_outside)."""
    main = [a for a in activities if not a[0].endswith(pc.INTERACTION_SUFFIX)]
    tours, current, touches = [], 0, False
    for index in range(len(main) - 1):
        if index > 0 and main[index][0] == pc.HOME:
            tours.append((current, touches))
            current, touches = 0, False
        current += 1
        touches = touches or main[index][0] == pc.OUTSIDE or main[index + 1][0] == pc.OUTSIDE
    if current:
        tours.append((current, touches))
    return tours


def longest_free_run(activities, trips):
    """Length of the longest non-border run of the finer rule (IsolatedOutsideTrips, as ``leg_reach`` cuts runs)."""
    def origin(trip):
        return activities[trip[0]][0]

    def destination(trip):
        return activities[trip[-1] + 1][0]
    touches = [origin(t) == pc.OUTSIDE or destination(t) == pc.OUTSIDE for t in trips]
    base, current = [], []
    for position, trip in enumerate(trips):
        current.append(position)
        if destination(trip) in (pc.HOME, pc.OUTSIDE):
            base.append(current)
            current = []
    if current:
        base.append(current)
    best = 0
    for tour in base:
        run = [tour[0]]
        runs = []
        for position in tour[1:]:
            if touches[position] == touches[run[-1]]:
                run.append(position)
            else:
                runs.append(run)
                run = [position]
        runs.append(run)
        for r in runs:
            border = origin(trips[r[0]]) == pc.OUTSIDE or destination(trips[r[-1]]) == pc.OUTSIDE
            if not border:
                best = max(best, len(r))
    return best


def trip_records(activities, legs):
    """Per trip: (mode, unfit, distance_m, touches_outside, home tour index, trip legs)."""
    trips = pc.split_trips(activities, len(legs))
    tours = pc.home_tours(activities, trips)
    records = []
    for position, trip in enumerate(trips):
        trip_legs = [legs[i] for i in trip]
        o, d = activities[trip[0]], activities[trip[-1] + 1]
        distance = math.hypot(d[2] - o[2], d[3] - o[3])
        unfit = pc.unfit_mode(trip_legs, distance, pt_as_walk_bound_min_distance_m=PT_BOUND_M,
                              walk_max_distance_m=WALK_BOUND_M, bicycle_max_distance_m=BICYCLE_BOUND_M)
        records.append((pc.trip_mode(trip_legs), unfit, distance, o[0] == pc.OUTSIDE or d[0] == pc.OUTSIDE,
                        tours[position]))
    return trips, records


def classes(activities, legs, trips, setting):
    isolate, length = SETTINGS[setting]
    per_leg = pc.leg_reach([a[:4] for a in activities], legs, isolate_outside_trips=isolate, max_tour_length=length)
    return [per_leg[trip[0]] for trip in trips]


def length_bucket(n):
    return "7_8" if n <= 8 else ("9_11" if n <= 11 else str(n))


def main() -> int:
    out = {"settings": {k: {"isolate_outside_trips": v[0], "max_tour_length": v[1]} for k, v in SETTINGS.items()},
           "limit": LIMIT}
    # ---- UNCUT: kinds and long tours without outside ----
    kind_of, uncut_long = {}, {}
    uncut_long_tours = collections.Counter()
    for pid, subpop, attrs, activities, legs, _ in iter_persons(uncut_path):
        incommuter = any((a[4] or "").startswith("ic_") for a in activities)
        kind = subpop or ("incommuter" if incommuter else "resident")
        kind_of[pid] = kind
        if kind != "resident":
            continue
        long_tours = [(index, n) for index, (n, touches) in enumerate(main_tours(activities)) if not touches and n > 6]
        if long_tours:
            uncut_long[pid] = (len(main_tours(activities)), long_tours)
            for _, n in long_tours:
                uncut_long_tours[length_bucket(n)] += 1
    out["uncut"] = {"persons_by_kind": dict(collections.Counter(kind_of.values())),
                    "long_tours_without_outside_by_length": dict(uncut_long_tours),
                    "residents_with_such_tour": len(uncut_long)}
    log(f"uncut: {out['uncut']}")

    # ---- INPUT ----
    stored = {}
    persons_in = collections.Counter()
    unreached = {s: collections.Counter() for s in SETTINGS}
    walks_before = collections.Counter()
    vehicle = collections.Counter()
    gap = collections.Counter()
    step1_like = collections.Counter()
    n_trips_in = 0
    other_signature, other_trip_modes = {}, {}
    for pid, subpop, attrs, activities, legs, _ in iter_persons(input_path):
        kind = subpop or kind_of.get(pid) or "unknown_not_in_uncut"
        persons_in[kind] += 1
        if kind != "resident":
            other_signature[pid] = (tuple(legs), tuple(a[0] for a in activities))
            other_trip_modes[pid] = tuple(r[0] for r in trip_records(activities, legs)[1]) if legs else ()
        if not legs:
            if kind == "resident":
                stored[pid] = ()
            continue
        trips, records = trip_records(activities, legs)
        border_tours = {r[4] for r in records if r[3]}
        # relaxed vehicle legs (all kinds)
        for mode, _, _, _, tour in records:
            if tour in border_tours and mode in RESTRICTED:
                if mode == "car":
                    available = attrs.get("carAvailability") != "none" and attrs.get("hasLicense") != "no"
                else:
                    available = attrs.get("bicycleAvailability") != "none"
                vehicle[(kind, mode, "available" if available else "relaxed")] += 1
        if kind != "resident":
            continue
        n_trips_in += len(trips)
        cls = {s: classes(activities, legs, trips, s) for s in SETTINGS}
        for s in SETTINGS:
            for c in cls[s]:
                unreached[s][c] += 1
        for i, (mode, unfit, distance, touches, tour) in enumerate(records):
            if unfit == "walk":
                walks_before[("today", cls["today"][i], "touches_outside" if touches else "inside")] += 1
                walks_before[("loop", cls["loop"][i])] += 1
                walks_before[("initial", cls["initial"][i])] += 1
        stored[pid] = tuple((r[0], r[1], r[4] in border_tours, cls["today"][i], cls["loop"][i], cls["initial"][i])
                            for i, r in enumerate(records))
        if pid in uncut_long:
            n_uncut_tours, long_tours = uncut_long[pid]
            prepared = main_tours(activities)
            for index, n in long_tours:
                bucket = length_bucket(n)
                if len(prepared) != n_uncut_tours:
                    gap[(bucket, "home tour count differs")] += 1
                    continue
                m, touches = prepared[index]
                if touches:
                    gap[(bucket, "prepared tour touches outside")] += 1
                elif m == n:
                    gap[(bucket, "same length, no outside")] += 1
                else:
                    gap[(bucket, f"no outside, {m} trips")] += 1
        run = longest_free_run(activities, trips)
        if run > 6:
            step1_like[length_bucket(run)] += 1
    out["input"] = {
        "persons_by_kind": dict(persons_in), "resident_trips": n_trips_in,
        "resident_trips_by_class": {s: dict(c) for s, c in unreached.items()},
        "resident_plain_walks_over_5km": {" | ".join(k): v for k, v in sorted(walks_before.items())},
        "border_tour_vehicle_legs": {" | ".join(k): v for k, v in sorted(vehicle.items())},
        "uncut_long_tours_in_prepared": {" | ".join(k): v for k, v in sorted(gap.items())},
        "residents_by_longest_free_finer_run": dict(step1_like)}
    log(f"input: {json.dumps(out['input'])}")

    if after_path == "-" and final_path == "-":
        # The replay stopped before the initial mode choice wrote a population: only UNCUT and INPUT exist.
        out["after_initial"] = out["final"] = "not run: no population after the initial mode choice"
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(out, handle, indent=2)
        return 0

    # ---- AFTER_INITIAL ----
    persons_after = collections.Counter()
    newly = collections.Counter()
    reached_init_changed = collections.Counter()
    border_unfit = collections.Counter()
    walks_after = collections.Counter()
    missing = 0
    after_modes = {}
    other_after = collections.Counter()
    for pid, subpop, attrs, activities, legs, _ in iter_persons(after_path):
        kind = subpop or kind_of.get(pid) or "unknown_not_in_uncut"
        persons_after[kind] += 1
        if kind != "resident":
            if pid in other_signature:
                same = other_signature[pid] == (tuple(legs), tuple(a[0] for a in activities))
                other_after[(kind, "plan identical" if same else "plan differs")] += 1
                modes_after = tuple(r[0] for r in trip_records(activities, legs)[1]) if legs else ()
                other_after[(kind, "trips")] += len(modes_after)
                other_after[(kind, "trips with changed mode")] += sum(
                    a != b for a, b in zip(other_trip_modes[pid], modes_after))
            continue
        if pid not in stored:
            if LIMIT:
                missing += 1
                continue
            raise RuntimeError(f"resident {pid} of the population after the initial choice is not in the input")
        before = stored[pid]
        trips, records = trip_records(activities, legs) if legs else ([], [])
        if len(records) != len(before):
            raise RuntimeError(f"resident {pid}: {len(before)} trips before, {len(records)} after the initial choice")
        after_modes[pid] = tuple((r[0], r[1]) for r in records)
        for (mode_b, unfit_b, border, c_today, c_loop, c_init), (mode_a, unfit_a, distance, touches, tour) in zip(
                before, records):
            changed = mode_b != mode_a
            if c_init == REACHED:
                reached_init_changed["changed" if changed else "kept"] += 1
                if c_today != REACHED:
                    newly[("all", c_today)] += 1
                    if changed:
                        newly[("changed", c_today)] += 1
                        newly[("from_to", f"{mode_b}->{mode_a}")] += 1
                if border:
                    border_unfit[("reached_by_initial_in_border_tour", "all")] += 1
                    if unfit_a is not None:
                        border_unfit[("unfit_after_initial", unfit_a, "loop class " + c_loop)] += 1
                    if unfit_b is not None:
                        border_unfit[("unfit_before_initial", unfit_b)] += 1
            elif changed:
                reached_init_changed["changed_although_not_reached"] += 1
            if unfit_a == "walk":
                walks_after[("loop", c_loop)] += 1
    out["after_initial"] = {
        "persons_by_kind": dict(persons_after), "residents_missing_in_input_smoke_only": missing,
        "other_kinds_against_input": {" | ".join(k): v for k, v in sorted(other_after.items())},
        "newly_reached": {" | ".join(k): v for k, v in sorted(newly.items())},
        "trips_reached_by_initial_mode_kept_or_changed": dict(reached_init_changed),
        "border_tours": {" | ".join(k): v for k, v in sorted(border_unfit.items())},
        "resident_plain_walks_over_5km": {" | ".join(k): v for k, v in sorted(walks_after.items())}}
    log(f"after_initial: {json.dumps(out['after_initial'])}")

    # ---- FINAL ----
    persons_final = collections.Counter()
    final = collections.Counter()
    for pid, subpop, attrs, activities, legs, lifted in iter_persons(final_path):
        kind = subpop or kind_of.get(pid) or "unknown_not_in_uncut"
        persons_final[kind] += 1
        if kind != "resident":
            if pid in other_signature:
                same = other_signature[pid] == (tuple(legs), tuple(a[0] for a in activities))
                final[(kind, "plan identical to input" if same else "plan differs from input")] += 1
            final[(kind, "lifted legs")] += sum(1 for value in lifted if value)
            continue
        if pid not in stored:
            continue
        before = stored[pid]
        trips, records = trip_records(activities, legs) if legs else ([], [])
        if len(records) != len(before):
            raise RuntimeError(f"resident {pid}: {len(before)} trips before, {len(records)} after the lift")
        lifted_by_trip = [next((lifted[i] for i in trip if lifted[i]), None) for trip in trips]
        for i, ((mode_b, _, border, c_today, c_loop, c_init), (mode_f, unfit_f, _, _, _)) in enumerate(
                zip(before, records)):
            if lifted_by_trip[i]:
                mode_a, unfit_a = after_modes[pid][i]
                final[("lifted", lifted_by_trip[i], c_loop)] += 1
                final[("lifted", lifted_by_trip[i], "unfit after initial" if unfit_a else "taken along (fitted)")] += 1
                final[("lifted_from_vs_after_initial_mode", lifted_by_trip[i], mode_a)] += 1
                if border:
                    final[("lifted in border tour", lifted_by_trip[i])] += 1
            if unfit_f is not None:
                final[("unfit", unfit_f, "unreached" if c_loop != REACHED else "reached")] += 1
    out["final"] = {"persons_by_kind": dict(persons_final),
                    "residents": {" | ".join(k): v for k, v in sorted(final.items())}}
    log(f"final: {json.dumps(out['final'])}")
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(out, handle, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
