"""Tiny synthetic HBEFA-shaped tables for the emissions tests (#688).

Column layout of the MATSim sample tables; every factor is the literal ``1`` and every speed
``10``, so no real or sample HBEFA value is involved. Not a test module (no ``test_`` prefix).
"""
from __future__ import annotations

from pathlib import Path

COMPONENTS = ("CO", "NOx")
SITUATIONS = ("Freeflow", "St+Go")
ROAD_TYPES = ("URB/Local/50", "URB/Access/30")
PETROL = ("petrol (4S)", "not specified", "PC P Euro-6")
DIESEL_AVERAGE = ("diesel", "average", "average")

_COLD_PATTERNS = [
    f"T,{parking},{distance}"
    for parking in [f"{hour}-{hour + 1}h" for hour in range(12)] + [">12h"]
    for distance in ("0-1km", "1-2km")
]


def _warm_rows(category, attributes, road_types, components=COMPONENTS, gradient="0%"):
    return [
        (category, component, f"{road_type}/{situation}", gradient, *attributes)
        for road_type in road_types for situation in SITUATIONS for component in components
    ]


def _cold_rows(category, attributes, components=COMPONENTS):
    return [(category, component, pattern, *attributes) for pattern in _COLD_PATTERNS for component in components]


def write_tables(folder: Path, *, hgv_cold=False, extra_warm_average=(), petrol_components=COMPONENTS) -> dict:
    """Write the four tables into ``folder`` and return ``{table kind: path}``.

    Content: the average tables hold ``pass. car`` and ``HGV`` on both road types (cold: ``HGV``
    only with ``hgv_cold``); the detailed tables hold one petrol sub-segment on both road types
    and a diesel technology average on ``URB/Local/50`` only (warm) and not at all (cold).
    """
    folder.mkdir(parents=True, exist_ok=True)
    warm_average = _warm_rows("pass. car", (), ROAD_TYPES) + _warm_rows("HGV", (), ROAD_TYPES)
    warm_average += list(extra_warm_average)
    cold_average = _cold_rows("pass. car", ()) + (_cold_rows("HGV", ()) if hgv_cold else [])
    warm_detailed = _warm_rows("pass. car", PETROL, ROAD_TYPES, petrol_components)
    warm_detailed += _warm_rows("pass. car", DIESEL_AVERAGE, ROAD_TYPES[:1])
    cold_detailed = _cold_rows("pass. car", PETROL)
    tables = {
        "warm_average": ("VehCat;Component;TrafficSit;Gradient;V_weighted;EFA_weighted", warm_average, "10;1"),
        "cold_average": ("VehCat;Component;AmbientCondPattern;EFA_weighted", cold_average, "1"),
        "warm_detailed": (
            "VehCat;Component;TrafficSit;Gradient;Technology;SizeClasse;EmConcept;V;EFA", warm_detailed, "10;1",
        ),
        "cold_detailed": (
            "VehCat;Component;AmbientCondPattern;Technology;SizeClasse;EmConcept;EFA", cold_detailed, "1",
        ),
    }
    paths = {}
    for kind, (header, rows, values) in tables.items():
        lines = ["Case;" + header] + [f"Synthetic[{kind}];" + ";".join(row) + ";" + values for row in rows]
        path = folder / f"EFA_{kind}.csv"
        # The warm average table carries a byte order mark, as one of the MATSim sample tables does.
        path.write_bytes((b"\xef\xbb\xbf" if kind == "warm_average" else b"") + ("\n".join(lines) + "\n").encode("utf-8"))
        paths[kind] = path
    return paths
