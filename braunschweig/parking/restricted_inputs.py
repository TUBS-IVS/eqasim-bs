"""The parking inputs that are not distributed in the repository, and the one message that a missing one produces.

The zone polygons, the resident parking districts and the garage dataset rest on sources that are not cleared for
redistribution (owner decision 2026-10-08, issue #436; ``storage.local_only`` in their data records). They stay on disk
under ``eqasim-data/`` like the other raw data and are available on request. Every place that reads one of them
(``braunschweig.parking.zones_stage``, ``scripts/validate_parking_zones.py``, ``scripts/parking/compare_parking_targets.py``,
``scripts/parking/calibrate_garage_decay.py``) fails with the message of ``missing_restricted_input_message``: the file, the
data record that describes it and states the SHA-256 for verification, and the route to obtain it.

``zones_stage`` imports this module and lists it in its hashed helper modules (the whole import closure is hashed, ADR-0136),
so editing it recomputes that cheap stage once.
"""
from __future__ import annotations

from pathlib import Path

#: Data Registry dataset ids of the three restricted inputs, by file name.
RESTRICTED_RECORDS = {
    "parking_zones_2026.geojson": "parking_zones_2026",
    "parking_resident_districts_2026.geojson": "parking_resident_districts_2026",
    "parking_garages_2026.geojson": "parking_garages_2026",
}
RESTRICTED_RECORD_IDS = frozenset(RESTRICTED_RECORDS.values())

REQUEST_ROUTE = ("it is not distributed in the repository (restricted source licences) and is available on request: "
                 "open an issue in TUBS-IVS/eqasim-bs; verify a received file against the SHA-256 in")


def missing_restricted_input_message(record_id: str, label: str, path) -> str:
    """The message for a restricted input that is absent: what is missing (``label``, the config key or the role), where
    it was expected, the data record and the request route."""
    return (f"{label} does not exist ({path}); {REQUEST_ROUTE} docs/registry/data/{record_id}.yml")


def require_restricted_file(record_id: str, label: str, path) -> Path:
    """``path`` as a Path when the file exists; raises ``FileNotFoundError`` with the shared message otherwise."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(missing_restricted_input_message(record_id, label, path))
    return path
