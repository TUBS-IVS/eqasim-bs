"""Door-to-door travel time per arm of the #465 continued A/B (ADR-0169, review of PR #512).

ph_modestats counts the time of the car legs only. Spreading a site's car access over its parking lots lengthens the
walk between the car and the activity, which the access step routes as separate walk legs of the same car trip. This
probe therefore reads output_trips.csv.gz of the last iteration of each arm, where trav_time spans the whole trip from
the end of one activity to the start of the next (access walk + car leg + egress walk + waiting), and reports:

- car trips (main_mode car): count, total hours and mean minutes, door to door;
- all trips of all modes: total hours (the system-wide time spent travelling, mode shifts included);
- the same two figures for the trips that start or end at an in-scope site of the parking arm (in_scope true in
  large_facility_access_summary_1pct.csv; a trip end belongs to a site when its activity coordinate, rounded to the
  site key "%.2f_%.2f", equals the site key; the access step keeps the activity coordinates, so the match is exact
  in every arm).

Usage: python door_to_door_probe.py <arms root> <summary csv> > door_to_door_ab.json
"""
import csv
import gzip
import json
import sys
from pathlib import Path

ARMS = ["cont_control", "cont_insertion_only", "cont_fix", "cont_parking"]


def seconds(text):
    hours, minutes, secs = (int(part) for part in text.split(":"))
    return hours * 3600 + minutes * 60 + secs


def in_scope_site_keys(summary_csv):
    """Site keys ("%.2f_%.2f" of the site coordinate) of the in-scope sites of the access step."""
    with open(summary_csv, newline="", encoding="utf-8") as handle:
        keys = {row["site_key"] for row in csv.DictReader(handle, delimiter=";") if row["in_scope"] == "true"}
    if not keys:
        raise SystemExit(f"no in-scope site read from {summary_csv}: check its header")
    return keys


def site_key(x, y):
    return "%.2f_%.2f" % (float(x), float(y))


def summarise(trips_path, sites):
    out = {"car": [0, 0], "all": [0, 0], "car_at_sites": [0, 0], "all_at_sites": [0, 0]}
    with gzip.open(trips_path, "rt", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            duration = seconds(row["trav_time"])
            at_site = (site_key(row["start_x"], row["start_y"]) in sites
                       or site_key(row["end_x"], row["end_y"]) in sites)
            keys = ["all"] + (["all_at_sites"] if at_site else [])
            if row["main_mode"] == "car":
                keys += ["car"] + (["car_at_sites"] if at_site else [])
            for key in keys:
                out[key][0] += 1
                out[key][1] += duration
    return {key: {"trips": count, "hours": round(total / 3600, 1),
                  "mean_min": round(total / 60 / count, 2) if count else None}
            for key, (count, total) in out.items()}


def main():
    root, summary_csv = Path(sys.argv[1]), sys.argv[2]
    sites = in_scope_site_keys(summary_csv)
    result = {"source": "output_trips.csv.gz, last iteration; trav_time door to door", "in_scope_sites": len(sites)}
    for arm in ARMS:
        result[arm] = summarise(root / arm / "simulation_output" / "output_trips.csv.gz", sites)
    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
