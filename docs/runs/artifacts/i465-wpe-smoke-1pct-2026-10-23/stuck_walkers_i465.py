"""Residents stuck walking at the end of the day in frozen tours: walk-fallback stamp and planned mode. Read-only.

Usage: python stuck_walkers_i465.py <stuck_trace csv> <prepared population.xml.gz> <scenario population.xml.gz>

Input is the output of stuck_trace_i453.py (felix ~/wt-i453-ab, the #465 stuck trace). For every resident stuck at the
simulation end in a frozen tour with legMode walk it reports (1) whether the selected PREPARED plan (the file MATSim
read) carries a leg attribute walkFallbackFrom=pt (the WP-B stamp of eqasim-java-bs #36), and (2) the mode of the
SCENARIO-population leg (before prepare) whose preceding activity ends closest to the departure of the stuck leg,
with the time difference dt_h (dt_h 0 = exact match; the scenario population has no cut outside activities, so a
non-zero dt_h is an approximate match).
"""
import gzip
import sys
import xml.etree.ElementTree as ET

import pandas as pd

trace_csv, prepared_path, scenario_path = sys.argv[1:4]
d = pd.read_csv(trace_csv, sep=";")
sel = d[d["at_end"] & d["frozen"] & (d["leg_mode"] == "walk")].copy()
sel.index = sel["person"].astype(str)
ids = set(sel.index)


def hours(value):
    if value is None:
        return None
    h, m, s = value.split(":")
    return int(h) + int(m) / 60 + float(s) / 3600


def selected_plans(path):
    with gzip.open(path, "rb") as handle:
        for _, element in ET.iterparse(handle):
            if element.tag != "person":
                continue
            if element.get("id") in ids:
                yield element.get("id"), next(p for p in element.findall("plan") if p.get("selected") == "yes")
            element.clear()


stamped = {pid: any(a.get("name") == "walkFallbackFrom" and a.text == "pt"
                    for leg in plan.findall("leg") for a in leg.iter("attribute"))
           for pid, plan in selected_plans(prepared_path)}

rows = []
for pid, plan in selected_plans(scenario_path):
    elements = list(plan)
    departure = sel.loc[pid, "plan_leg_dep_h"]
    best = None
    for i, element in enumerate(elements):
        if element.tag == "leg":
            end = hours(elements[i - 1].get("end_time"))
            if end is not None and (best is None or abs(end - departure) < best[0]):
                best = (abs(end - departure), element.get("mode"), elements[i - 1].get("type"),
                        elements[i + 1].get("type"), end)
    rows.append(dict(person=pid, stuck_leg_dep_h=round(departure, 2), planned_mode=best[1], planned_from=best[2],
                     planned_to=best[3], planned_dep_h=round(best[4], 2), dt_h=round(best[0], 2),
                     prepared_plan_stamped_pt=stamped.get(pid, False)))

out = pd.DataFrame(rows).sort_values("person")
print(out.to_string(index=False))
print("stuck walkers at end, frozen:", len(ids))
print("prepared plan with walkFallbackFrom=pt:", int(out["prepared_plan_stamped_pt"].sum()))
print("planned mode (nearest departure):", out["planned_mode"].value_counts().to_dict())
print("planned pt with an exact departure match (dt_h == 0):", int(((out["planned_mode"] == "pt") & (out["dt_h"] == 0)).sum()))
