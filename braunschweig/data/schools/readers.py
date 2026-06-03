"""Parse the LSN ABS + BBS directories into a tidy typed long frame.

These functions take already-loaded raw DataFrames (the column headers as named
in the cleaned sheets) and return one row per (school, level) with capacity from
the real pupil counts. Geocoding is NOT done here (see scripts/extract_nds_schools.py).
"""
from __future__ import annotations

import pandas as pd

from braunschweig.data.schools.typing import (
    capacity_by_level_from_sgl, nds_ags8, nds_kreis5,
)

_LONG_COLUMNS = [
    "school_id", "name", "level", "capacity",
    "ags8", "kreis5", "street", "plz", "city",
]


def _coerce_count(value):
    """LSN BBS pupil cells use '[n]' for none; everything else is an int."""
    if value is None:
        return 0.0
    s = str(value).strip()
    if s in ("", "[n]", "nan", "None"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def build_abs_long(raw, scope):
    """Tidy long frame from the ABS raw sheet, filtered to ``scope`` (5-digit
    Kreis ids). One row per (school, level)."""
    rows = []
    sgl_cols = [f"SGL{i}" for i in range(1, 7)]
    sch_cols = [f"Sch{i}" for i in range(1, 7)]
    for _, r in raw.iterrows():
        kreis5 = nds_kreis5(r["Kreis"])
        if kreis5 not in scope:
            continue
        cap = capacity_by_level_from_sgl(
            [r[c] for c in sgl_cols], [r[c] for c in sch_cols],
        )
        for level, capacity in cap.items():
            rows.append({
                "school_id": "abs_" + str(r["SNR"]).strip(),
                "name": str(r["Schulname"]).strip(),
                "level": level,
                "capacity": float(capacity),
                "ags8": nds_ags8(r["AGS"]),
                "kreis5": kreis5,
                "street": str(r["Strasse"]).strip(),
                "plz": str(r["PLZ"]).strip(),
                "city": str(r["Ort"]).strip(),
            })
    return pd.DataFrame(rows, columns=_LONG_COLUMNS)


def build_bbs_long(raw, scope, pupil_cols):
    """Tidy long frame from the BBS raw sheet; all BBS pupils sum into
    ``sekundar_2``. ``pupil_cols`` are the per-Schulform 'Anzahl der Schueler'
    column names."""
    rows = []
    for _, r in raw.iterrows():
        kreis5 = nds_kreis5(r["Kreis"])
        if kreis5 not in scope:
            continue
        capacity = sum(_coerce_count(r[c]) for c in pupil_cols)
        if capacity <= 0:
            continue
        rows.append({
            "school_id": "bbs_" + str(r["Schulnummer"]).strip(),
            "name": str(r["Schulbezeichnung"]).strip(),
            "level": "sekundar_2",
            "capacity": float(capacity),
            "ags8": nds_ags8(r["AGS"]),
            "kreis5": kreis5,
            "street": str(r["Strasse"]).strip(),
            "plz": str(r["PLZ"]).strip(),
            "city": str(r["Ort"]).strip(),
        })
    return pd.DataFrame(rows, columns=_LONG_COLUMNS)


def build_schools_long(abs_raw, bbs_raw, scope, bbs_pupil_cols):
    """Concatenated ABS + BBS long frame in ``scope``."""
    df = pd.concat(
        [build_abs_long(abs_raw, scope),
         build_bbs_long(bbs_raw, scope, bbs_pupil_cols)],
        ignore_index=True,
    )
    return df[_LONG_COLUMNS]
