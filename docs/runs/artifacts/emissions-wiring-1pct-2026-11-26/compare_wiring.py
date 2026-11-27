"""Why do the tool outputs of the stage chain differ from the hand-built wiring run? (issue #688)

Prints structure only: settings, vehicle-type attributes, and per vehicle type whether its rows
differ and by which RATIO. No emission value is printed (the tables are the altered sample tables).
"""
import csv
import gzip
import re
from collections import defaultdict
from pathlib import Path

HOME = Path.home() / "felix_emissions"
OLD_VIEW = HOME / "wiring_1pct" / "view_run1"
OLD_OUT = HOME / "wiring_1pct" / "out_run2"
NEW = HOME / "chain_wiring_1pct" / "pass_b_all_links_on_fallback"
NEW_VIEW = NEW / "braunschweig.emissions.inputs" / "view"
NEW_OUT = NEW / "braunschweig.emissions.events"


def emissions_module(view):
    text = (view / "output_config.xml").read_text(encoding="utf-8")
    block = re.search(r'<module name="emissions"\s*>(.*?)</module>', text, re.S).group(1)
    params = dict(re.findall(r'<param name="([^"]+)" value="([^"]*)"', block))
    return {k: (Path(v).name if "File" in k else v) for k, v in params.items()}


def type_keys(path):
    types = {}
    current = None
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            match = re.search(r'<vehicleType id="([^"]+)"', line)
            if match:
                current = match.group(1)
                types[current] = {}
            attribute = re.search(r'<attribute name="(Hbefa[A-Za-z]+)"[^>]*>([^<]*)</attribute>', line)
            if attribute and current:
                types[current][attribute.group(1)] = attribute.group(2)
            if "</vehicleType>" in line:
                current = None
    return types


def rows(path):
    with open(path, newline="", encoding="utf-8") as handle:
        return {(r["vehicleType"], r["pollutant"]): float(r["value"]) for r in csv.DictReader(handle)}


old_settings, new_settings = emissions_module(OLD_VIEW), emissions_module(NEW_VIEW)
print("SETTINGS that differ (old hand-built -> new stage view):")
for key in sorted(set(old_settings) | set(new_settings)):
    if old_settings.get(key) != new_settings.get(key):
        print("  ", key, ":", old_settings.get(key), "->", new_settings.get(key))

for name in ("output_vehicles.xml.gz", "output_transitVehicles.xml.gz"):
    old_types, new_types = type_keys(OLD_VIEW / name), type_keys(NEW_VIEW / name)
    changed = {t: (old_types.get(t), new_types.get(t)) for t in sorted(set(old_types) | set(new_types))
               if old_types.get(t) != new_types.get(t)}
    print(f"{name}: {len(old_types)} / {len(new_types)} types, {len(changed)} with different HBEFA attributes")
    for type_id, (old, new) in list(changed.items())[:12]:
        diff = {k: (old or {}).get(k) for k in set(old or {}) | set(new or {}) if (old or {}).get(k) != (new or {}).get(k)}
        print("   ", type_id, {k: (v, (new or {}).get(k)) for k, v in diff.items()})

old_rows = rows(OLD_OUT / "emissions_per_vehicle_type.csv")
new_rows = rows(NEW_OUT / "emissions_per_vehicle_type.csv")
print("per-vehicle-type rows:", len(old_rows), "->", len(new_rows),
      "| only old:", len(set(old_rows) - set(new_rows)), "| only new:", len(set(new_rows) - set(old_rows)))
ratios = defaultdict(list)
for key in set(old_rows) & set(new_rows):
    old, new = old_rows[key], new_rows[key]
    if old == new:
        continue
    ratios[key[0]].append(new / old if old else float("inf"))
print("types with any differing row:", len(ratios), "of", len({k[0] for k in old_rows}))
for type_id in sorted(ratios)[:12]:
    values = ratios[type_id]
    print("   ", type_id, "rows differing:", len(values), "ratio new/old min %.6f max %.6f" % (min(values), max(values)))
all_ratios = [v for values in ratios.values() for v in values]
if all_ratios:
    print("ratio new/old over all differing rows: min %.9f max %.9f" % (min(all_ratios), max(all_ratios)))
old_text = (OLD_OUT / "emissions_per_vehicle_type.csv").read_text().splitlines()
new_text = (NEW_OUT / "emissions_per_vehicle_type.csv").read_text().splitlines()
print("same header:", old_text[0] == new_text[0], "| same row order of keys:",
      [l.rsplit(",", 1)[0] for l in old_text] == [l.rsplit(",", 1)[0] for l in new_text])
