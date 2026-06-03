"""Pure typing helpers for the Niedersachsen (LSN) school directories.

Maps Schulgliederung (SGL) structure codes and BBS Schulformen to the three
school-age levels used by the education gravity model, and converts the LSN
internal area codes (which drop the federal-state prefix) to official IDs.

Sources: ``Schulverzeichnis_ABS_2025.xlsx`` sheet ``SGL`` (structure legend);
``Verzeichnis_der_BBS_2024.xlsx`` (vocational). See
docs/superpowers/specs/2026-06-03-education-gravity-design.md.
"""
from __future__ import annotations

import math

SCHOOL_LEVELS = ("grundschule", "sekundar_1", "sekundar_2")

# SGL structure code -> level. Codes absent here (e.g. 30 Abendgymnasium,
# 31 Kolleg) are not school-age and map to None.
_SGL_LEVEL = {}
for _c in ("00", "01", "03", "04"):
    _SGL_LEVEL[_c] = "grundschule"
# Sekundarbereich I: Haupt/Real/Gymnasium-SekI/IGS-SekI/KGS branches (11-19)
# plus the contiguous Oberschule + Foerderschule block (40-69). The full 40..69
# range is mapped (not only the codes present in the 2025 legend) so the typing
# stays robust if LSN adds Foerderschule structure codes in that block.
# Code 15 is intentionally absent -- it is not a defined SGL structure in the LSN
# legend (the Sek-I block runs 11,12,13,14 then 16-19 for the KGS branches).
for _c in ("11", "12", "13", "14", "16", "17", "18", "19"):
    _SGL_LEVEL[_c] = "sekundar_1"
for _code in range(40, 70):
    _SGL_LEVEL["%02d" % _code] = "sekundar_1"
for _c in ("23", "24", "28", "29"):
    _SGL_LEVEL[_c] = "sekundar_2"


def sgl_to_level(code):
    """Return the school level for an SGL structure code, or None if not
    school-age. ``code`` may be int or str; it is normalised to a zero-padded
    two-character string."""
    if code is None:
        return None
    if isinstance(code, float) and math.isnan(code):
        return None
    key = str(code).strip()
    if key in ("", "nan"):
        return None
    # numeric codes lose their leading zero when read from Excel as ints
    if key.replace(".0", "").isdigit():
        key = key.replace(".0", "").zfill(2)
    return _SGL_LEVEL.get(key)


def capacity_by_level_from_sgl(sgl_codes, pupil_counts):
    """Sum pupil counts per level for one ABS school.

    ``sgl_codes`` and ``pupil_counts`` are parallel iterables (the SGL1..SGL6 /
    Sch1..Sch6 pairs). Pairs whose SGL maps to None, or whose count is missing,
    are skipped. Returns a dict {level: capacity_float} containing only levels
    with positive capacity.
    """
    out = {}
    for code, count in zip(sgl_codes, pupil_counts):
        level = sgl_to_level(code)
        if level is None:
            continue
        if count is None:
            continue
        if isinstance(count, float) and math.isnan(count):
            continue
        value = float(count)
        if value <= 0:
            continue
        out[level] = out.get(level, 0.0) + value
    return out


def nds_ags8(ags6):
    """Official 8-digit AGS = '03' + the LSN 6-digit AGS (Niedersachsen)."""
    return "03" + str(ags6).strip().zfill(6)


def nds_kreis5(kreis3):
    """Official 5-digit Kreis = '03' + the LSN 3-digit Kreis code."""
    return "03" + str(kreis3).strip().zfill(3)
