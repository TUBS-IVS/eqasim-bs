"""Vehicle-kilometres per vehicle type from the events of a run (post-run emissions, #688).

The emissions tool has no counter for how much driving was answered by which HBEFA table level,
so the summary weights the pre-check levels with the vehicle-km of each type, measured here.

Convention: every ``entered link`` event adds the full length of the entered link to the type of
the vehicle. The link a vehicle departs from is not counted, the link it arrives on is counted in
full. Values are SAMPLE values: nothing is scaled by the sampling rate here.

The events are streamed line by line (a 100 % run has several hundred million lines; about
20 minutes, no events held in memory).
"""
from __future__ import annotations

import gzip
import re
from pathlib import Path

#: Seconds after which the tool's total, per-link and grid outputs drop emissions (24:00).
DAY_END_SECONDS = 86400.0

_LINK = re.compile(rb'<link id="([^"]+)"[^>]* length="([^"]+)"')
_VEHICLE = re.compile(rb'<vehicle id="([^"]+)" type="([^"]+)"')
_ENTERED = re.compile(rb'time="([^"]+)" type="entered link" link="([^"]+)" vehicle="([^"]+)"')
_ENTERED_MARK = b'type="entered link"'


def _read_pairs(path: Path, pattern: re.Pattern) -> dict[bytes, bytes]:
    pairs: dict[bytes, bytes] = {}
    with gzip.open(path, "rb") as reader:
        for line in reader:
            match = pattern.search(line)
            if match:
                pairs[match.group(1)] = match.group(2)
    return pairs


def vehicle_km_by_type(folder: str | Path) -> dict:
    """Measure the driving per vehicle type in ``folder`` (a run archive or an emissions view).

    Returns ``{"by_type": {type id: {"meters", "meters_after_day_end", "link_entries",
    "vehicles", "vehicles_with_link_entry"}}, "event_lines", "unknown_vehicle_link_entries",
    "unknown_vehicle_meters"}``. Every vehicle type that owns a vehicle is listed, also with zero
    driving. A link entry of a vehicle that is in neither vehicles file is counted as unknown.
    Raises ``ValueError`` for an entered link that is not in the network, and when no link or no
    vehicle could be read at all.
    """
    folder = Path(folder)
    lengths = {link: float(length) for link, length in _read_pairs(folder / "output_network.xml.gz", _LINK).items()}
    types = _read_pairs(folder / "output_transitVehicles.xml.gz", _VEHICLE)
    types.update(_read_pairs(folder / "output_vehicles.xml.gz", _VEHICLE))
    # The files are read with line patterns, not an XML parser: an attribute order MATSim does not
    # write today would yield nothing, and all driving would then count as "unknown vehicle".
    if not lengths:
        raise ValueError(f"no link with a length was read from the network of {folder}")
    if not types:
        raise ValueError(f"no vehicle with a type was read from the vehicles files of {folder}")
    by_type: dict[bytes, dict] = {}
    for type_id in types.values():
        entry = by_type.get(type_id)
        if entry is None:
            entry = by_type[type_id] = {
                "meters": 0.0, "meters_after_day_end": 0.0, "link_entries": 0, "vehicles": 0, "moving": set(),
            }
        entry["vehicles"] += 1
    event_lines = unknown_entries = 0
    unknown_meters = 0.0
    with gzip.open(folder / "output_events.xml.gz", "rb") as reader:
        for line in reader:
            event_lines += 1
            if _ENTERED_MARK not in line:
                continue
            match = _ENTERED.search(line)
            if match is None:
                raise ValueError(f"unreadable 'entered link' event: {line[:200]!r}")
            time_text, link, vehicle = match.groups()
            length = lengths.get(link)
            if length is None:
                raise ValueError(f"event enters link {link.decode()!r}, which is not in the network of {folder}")
            type_id = types.get(vehicle)
            if type_id is None:
                unknown_entries += 1
                unknown_meters += length
                continue
            entry = by_type[type_id]
            entry["meters"] += length
            entry["link_entries"] += 1
            entry["moving"].add(vehicle)
            if float(time_text) >= DAY_END_SECONDS:
                entry["meters_after_day_end"] += length
    result = {}
    for type_id, entry in sorted(by_type.items()):
        moving = entry.pop("moving")
        entry["vehicles_with_link_entry"] = len(moving)
        result[type_id.decode("utf-8")] = entry
    return {
        "by_type": result,
        "event_lines": event_lines,
        "unknown_vehicle_link_entries": unknown_entries,
        "unknown_vehicle_meters": unknown_meters,
    }
