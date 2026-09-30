"""Give every person of an older plans file a routable vehicle for car and car_passenger.

The June 2026 scenario predates the writer convention that emits both vehicles for every person; the
current BraunschweigModeAvailability offers car_passenger to everyone with car availability, and the
MATSim router then needs a vehicle id per mode. Missing entries are added to the PersonVehicles
attribute and matching vehicles are appended to vehicles.xml.gz using the file's own line template
(types default_car / default_car_passenger). Applied identically to every smoke arm.
"""
from __future__ import annotations

import argparse
import gzip
import re
from pathlib import Path

PERSON_RE = re.compile(r'^\s*<person id="([^"]+)"')
VEHICLES_RE = re.compile(r'^(\s*)<attribute name="vehicles" class="org\.matsim\.vehicles\.PersonVehicles">(\{.*\})</attribute>\s*$')
ENTRY_RE = re.compile(r'"([a-z_]+)":"([^"]+)"')
MODES = ("car", "car_passenger")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--population", required=True)
    parser.add_argument("--vehicles", required=True)
    args = parser.parse_args(argv)
    pop_path, veh_path = Path(args.population), Path(args.vehicles)

    with gzip.open(pop_path, "rt", encoding="utf-8") as stream:
        lines = stream.readlines()
    person, added, new_vehicles = None, 0, []
    for index, line in enumerate(lines):
        m = PERSON_RE.match(line)
        if m:
            person = m.group(1)
            continue
        m = VEHICLES_RE.match(line)
        if m and person is not None:
            entries = dict(ENTRY_RE.findall(m.group(2)))
            missing = [mode for mode in MODES if mode not in entries]
            if not missing:
                continue
            for mode in missing:
                entries[mode] = f"{person}:{mode}"
                new_vehicles.append((entries[mode], mode))
            content = ",".join(f'"{mode}":"{vid}"' for mode, vid in entries.items())
            lines[index] = f'{m.group(1)}<attribute name="vehicles" class="org.matsim.vehicles.PersonVehicles">{{{content}}}</attribute>\n'
            added += len(missing)
    with gzip.open(pop_path, "wt", encoding="utf-8") as stream:
        stream.writelines(lines)

    # Every vehicle id referenced by any person must exist in the vehicles file; the ones that do not
    # are appended as SELF-CLOSING elements (the file's own vehicle elements carry attribute children,
    # which a new plain vehicle does not need).
    referenced = []
    for line in lines:
        m = VEHICLES_RE.match(line)
        if m:
            referenced.extend(ENTRY_RE.findall(m.group(2)))
    with gzip.open(veh_path, "rt", encoding="utf-8") as stream:
        vlines = stream.readlines()
    existing = {re.search(r'<vehicle id="([^"]+)"', line).group(1) for line in vlines if "<vehicle id=" in line}
    close = next(i for i, line in enumerate(vlines) if "</vehicleDefinitions>" in line)
    inserts, seen = [], set()
    for mode, vid in referenced:
        if vid in existing or vid in seen:
            continue
        seen.add(vid)
        inserts.append(f'  <vehicle id="{vid}" type="default_{mode}"/>\n')
    vlines[close:close] = inserts
    with gzip.open(veh_path, "wt", encoding="utf-8") as stream:
        stream.writelines(vlines)
    print(f"person vehicle entries added: {added}; vehicles appended: {len(inserts)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
