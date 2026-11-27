"""Cut the residents with a long mode-choice run out of the prepared population of prod100_20261031 (#678, B9 step 1).

Read-only on the run. A resident is a person without a ``subpopulation`` attribute whose uncut plan has no ``ic_``
facility (the rule of frozen_scope_probe.py of the "before" manifest). Runs are those of the FINER rule
(``isolate_outside_trips`` true): base tours split at ``home``/``outside``, then cut into maximal runs of border and
non-border trips; a run that starts or ends at ``outside`` is fixed at the border and never enumerated. The run
lengths computed here are cross-checked trip by trip against ``plan_check.leg_reach`` of the branch (isolate true,
limit 13, no restricted modes, so that the vehicle class does not hide the length class); any disagreement raises.

A person's longest ENUMERATED run is the longest non-border run of at most 13 trips (longer runs are dropped by the
TourLength filter at limit 13 and cost nothing). Persons whose longest enumerated run has more than 6 trips go to
one of the files ``long_7_8``, ``long_9_11``, ``long_12_13``. Persons with a 12-trip run and persons with a run of more
than 13 trips are counted. The enumeration size per person is an ESTIMATE: sum over its enumerated runs of
(available modes) ** (trips of the run), available modes as in long_tour_cost_probe.py (walk and pt always,
car_passenger with availability some/all, car with car availability not none and a licence, bicycle with bicycle
availability not none).

Usage: python i678_cut_long_tours.py <eqasim-bs clone> <run cache dir> <out dir>
"""
from __future__ import annotations

import collections
import gzip
import json
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

CLONE, CACHE, OUT = (Path(arg).expanduser() for arg in sys.argv[1:4])
sys.path.append(str(CLONE))
from braunschweig.matsim.simulation import plan_check  # noqa: E402

PREPARE_STEM = "braunschweig.matsim.simulation.prepare__57fd011e45464705ad66d48134be0b63"
POPULATION_STEM = "braunschweig.matsim.scenario.population__d66536c43b770db3cd52e1a76a1c4449"
PREPARED = CACHE / (PREPARE_STEM + ".cache") / "braunschweig_100pct_prod20261031_population.xml.gz"
UNCUT = CACHE / (POPULATION_STEM + ".cache") / "population.xml.gz"
LIMIT = 13
BINS = (("long_7_8", 7, 8), ("long_9_11", 9, 11), ("long_12_13", 12, 13))
ID_PATTERN = re.compile(r'<person id="([^"]+)"')


def iter_chunks(path):
    """(header text, None) once, then (person id, raw person text) for every person; streaming."""
    header, chunk, inside = [], [], False
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            if not inside:
                if "<person " in line:
                    inside = True
                    if header is not None:
                        yield "".join(header), None
                        header = None
                    chunk = [line]
                elif header is not None:
                    header.append(line)
                continue
            chunk.append(line)
            if "</person>" in line:
                inside = False
                text = "".join(chunk)
                yield ID_PATTERN.search(text).group(1), text


def parse(text):
    element = ET.fromstring(text)
    attrs = {}
    node = element.find("attributes")
    if node is not None:
        for attribute in node.findall("attribute"):
            attrs[attribute.get("name")] = (attribute.text or "").strip()
    plan = next((p for p in element.findall("plan") if p.get("selected") == "yes"), element.find("plan"))
    activities, legs = [], []
    if plan is not None:
        for child in plan:
            if child.tag == "activity":
                end = child.get("end_time")
                activities.append((child.get("type"), plan_check.parse_time_s(end) if end else None,
                                   float(child.get("x")), float(child.get("y"))))
            elif child.tag == "leg":
                travel = child.get("trav_time")
                legs.append((child.get("mode"), plan_check.parse_time_s(travel) if travel else None,
                             plan_check.leg_attribute(child, plan_check.WALK_FALLBACK_ATTRIBUTE),
                             plan_check.leg_attribute(child, plan_check.ROUTING_MODE_ATTRIBUTE)))
    return attrs, activities, legs


def runs_of(activities, n_legs, isolate):
    """(trips, list of (trip positions, border flag)) as plan_check.leg_reach builds them."""
    trips = plan_check.split_trips(activities, n_legs)
    kind = lambda i: activities[i][0]  # noqa: E731
    origin = [kind(t[0]) for t in trips]
    dest = [kind(t[-1] + 1) for t in trips]
    touches = [o == "outside" or d == "outside" for o, d in zip(origin, dest)]
    tours, current = [], []
    for position in range(len(trips)):
        current.append(position)
        if dest[position] in ("home", "outside"):
            tours.append(current)
            current = []
    if current:
        tours.append(current)
    runs = []
    for tour in tours:
        if not isolate:
            runs.append(tour)
            continue
        run = [tour[0]]
        for position in tour[1:]:
            if touches[position] == touches[run[-1]]:
                run.append(position)
            else:
                runs.append(run)
                run = [position]
        runs.append(run)
    return trips, [(run, origin[run[0]] == "outside" or dest[run[-1]] == "outside") for run in runs]


def n_modes(attrs):
    licence = attrs.get("hasLicense", "").lower() in ("yes", "true", "1")
    return (2 + (attrs.get("carPassengerAvailability") in ("some", "all"))
            + (attrs.get("carAvailability", "none") not in ("none", "") and licence)
            + (attrs.get("bicycleAvailability", "none") not in ("none", "")))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    incommuters = set()
    for person_id, text in iter_chunks(UNCUT):
        if text is not None and 'facility="ic_' in text:
            incommuters.add(person_id)
    print(f"pass 1: uncut in-commuters {len(incommuters)} in {time.time() - t0:.0f} s", flush=True)

    stats = collections.Counter()
    per_bin = {name: collections.Counter() for name, _, _ in BINS}
    run_hist = {name: collections.Counter() for name, _, _ in BINS}
    coarse_hist = collections.Counter()
    handles, header = {}, None
    for person_id, text in iter_chunks(PREPARED):
        if text is None:
            header = person_id
            for name, _, _ in BINS:
                handles[name] = gzip.open(OUT / f"{name}.xml.gz", "wt", encoding="utf-8", compresslevel=1)
                handles[name].write(header)
            continue
        stats["persons"] += 1
        if 'name="subpopulation"' in text:
            stats["persons_with_subpopulation"] += 1
            continue
        if person_id in incommuters:
            stats["incommuters"] += 1
            continue
        stats["residents"] += 1
        attrs, activities, legs = parse(text)
        n_legs = max(len(activities) - 1, 0)
        if n_legs == 0:
            continue
        trips, runs = runs_of(activities, n_legs, isolate=True)
        # Cross-check against the branch's classification (no restricted modes: the length class stays visible).
        reach = plan_check.leg_reach(activities, legs, isolate_outside_trips=True, max_tour_length=LIMIT,
                                     restricted_modes=frozenset())
        for run, border in runs:
            expected = (plan_check.FIXED_BORDER if border else
                        plan_check.FIXED_LENGTH if len(run) > LIMIT else plan_check.REACHED)
            for position in run:
                if reach[trips[position][0]] != expected:
                    raise AssertionError(f"person {person_id}: run {run} expected {expected}, leg_reach "
                                         f"{reach[trips[position][0]]}")
        enumerated = [len(run) for run, border in runs if not border and len(run) <= LIMIT]
        over_limit = [len(run) for run, border in runs if not border and len(run) > LIMIT]
        _, coarse = runs_of(activities, n_legs, isolate=False)
        coarse_longest = max([len(run) for run, border in coarse if not border] or [0])
        if over_limit:
            stats["residents_with_run_over_13"] += 1
        longest = max(enumerated or [0])
        if longest <= 6:
            if coarse_longest > 6:
                stats["residents_coarse_over_6_but_finer_at_most_6"] += 1
            continue
        if longest == 12:
            stats["residents_longest_12"] += 1
        name = next(name for name, low, high in BINS if low <= longest <= high)
        modes = n_modes(attrs)
        counter = per_bin[name]
        counter["persons"] += 1
        counter["trips"] += len(trips)
        counter["runs_enumerated"] += len(enumerated)
        counter["chains_estimate"] += sum(modes ** n for n in enumerated)
        counter["chains_estimate_runs_over_6"] += sum(modes ** n for n in enumerated if n > 6)
        counter[f"modes_{modes}"] += 1
        counter["coarse_longest_over_6"] += coarse_longest > 6
        for n in enumerated:
            if n > 6:
                run_hist[name][n] += 1
        coarse_hist[(name, coarse_longest)] += 1
        handles[name].write(text)
    for handle in handles.values():
        handle.write("\n</population>\n")
        handle.close()
    result = {"stats": dict(stats), "per_file": {k: dict(v) for k, v in per_bin.items()},
              "runs_over_6_by_length": {k: {str(n): c for n, c in sorted(v.items())} for k, v in run_hist.items()},
              "coarse_longest_by_file": {f"{k}|{n}": c for (k, n), c in sorted(coarse_hist.items())},
              "elapsed_s": round(time.time() - t0)}
    (OUT / "cut_summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
