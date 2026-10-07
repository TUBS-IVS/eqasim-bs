"""Curation QA of the municipal parking data in the zone release (parking cost zones v2, spec Amendments C and D, issue #436).

The committed table ``parking_zones_2026_municipal_qa.csv`` is written by the municipal step of the zone curation
(``scripts/curation/parking_zones_2026/municipal_zones.py``, run through ``assemble_parking_zones.py --municipal-dir``)
and, since spec Amendment D, by the regional step (``regional_zones.py``, ``--regional-dir``); it is no input of any
synpp stage. One row per comparison of a subject area with a reference area: the owner-supplied
Braunschweig fee zones 1a and 1b against the v1 tracings of zones Ia and Ib (C1), the Wolfsburg tariff zones against the
v1 polygon ``wob_innenstadt`` and against their own 50 m areas (C2), the Goslar fee polygon against the city's
resident and facility areas (C4, a cross-check only), and the zones of the regional evidence package of 2026-10-07
(D1 and D3: the BgA car parks, the TU campus unions and the single paid sites against their v1 polygons, their source
geometries and their 50 m areas, and every precedence cut). A row whose subject is a polygon of the release names it in
``release_zone_id``; ``validate_municipal_qa`` compares its recorded area with the polygon, so a regenerated release
cannot keep a stale table, and requires a row for every buffered-section, campus-union and single-site polygon and
every polygon with a reconstruction flag. Areas in m2 (EPSG:25832, written to 0.1 m2), shares from 0 to 1 (6 decimals,
from the unrounded areas), empty where a share is undefined (an empty denominator).
"""
from __future__ import annotations

import logging
import math
import re

import pandas as pd

from braunschweig.parking import zones as pz

log = logging.getLogger(__name__)

#: The layout of ``parking_zones_2026_municipal_qa.csv`` (the curation step's header defines every column).
MUNICIPAL_QA_COLUMNS = (
    "row_id", "municipality_ags", "release_zone_id", "subject", "reference", "subject_area_m2", "reference_area_m2",
    "overlap_area_m2", "subject_share_in_reference", "reference_share_in_subject", "reference_features",
    "reference_features_overlapping", "note",
)
_AREA_COLUMNS = ("subject_area_m2", "reference_area_m2", "overlap_area_m2")
_COUNT_COLUMNS = ("reference_features", "reference_features_overlapping")
#: A recorded release area may differ this much from the polygon as loaded (the table rounds to 0.1 m2; the step
#: measures the written and reloaded file, so only the rounding and floating noise remain).
RELEASE_AREA_TOLERANCE_M2 = 0.5
#: Half the rounding unit of the areas (0.1 m2) and of the shares (6 decimals).
_AREA_ROUNDING_M2 = 0.05
_SHARE_ROUNDING = 5e-7
_ROW_ID = re.compile(r"^[a-z0-9_]+$")
_AGS = re.compile(r"^\d{8}$")


def municipal_zone_ids(zones: pd.DataFrame) -> list:
    """The polygons that need rows of the municipal QA table: geometry_source ``municipal_street_sections_buffered``
    (spec Amendment C2), ``campus_detection_zones``, ``campus_outline_and_detection_zones`` or
    ``single_site_buffered`` (spec Amendment D), or a ``reconstructed_section_m2`` value (sorted ids)."""
    municipal = zones["geometry_source"].isin((pz.MUNICIPAL_SECTIONS_GEOMETRY_SOURCE,
                                               pz.CAMPUS_DETECTION_ZONES_GEOMETRY_SOURCE,
                                               pz.CAMPUS_OUTLINE_AND_DETECTION_ZONES_GEOMETRY_SOURCE,
                                               pz.SINGLE_SITE_BUFFERED_GEOMETRY_SOURCE))
    if pz.RECONSTRUCTED_SECTION_COLUMN in zones.columns:
        municipal = municipal | zones[pz.RECONSTRUCTED_SECTION_COLUMN].notna()
    return sorted(zones.loc[municipal, "zone_id"].astype(str))


def load_municipal_qa(path) -> pd.DataFrame:
    """Load the municipal QA table (``#`` lines skipped, text columns ``MUNICIPAL_QA_COLUMNS``, empty = "")."""
    qa = pz._read_documented_csv(path)
    pz._check_columns(qa, MUNICIPAL_QA_COLUMNS, str(path))
    qa = qa[list(MUNICIPAL_QA_COLUMNS)].apply(lambda column: column.str.strip())
    log.info("[parking-zones] loaded %d municipal QA rows from %s", len(qa), path)
    return qa


def _share_problem(prefix: str, column: str, text: str, overlap: float, denominator: float) -> str:
    """'' when the recorded share agrees with overlap / denominator up to the rounding of the table, else a message."""
    if denominator <= 0.0:
        return "" if text == "" else f"{prefix}: {column} must be empty when its denominator is 0, found {text!r}"
    value = pz._qa_number(text)
    expected = overlap / denominator
    tolerance = _AREA_ROUNDING_M2 / denominator * (1.0 + expected) + _SHARE_ROUNDING
    if value is None or not math.isfinite(value) or not 0.0 <= value <= 1.0 or abs(value - expected) > tolerance:
        return f"{prefix}: {column} = {text!r} but the recorded areas give {expected:.6f}"
    return ""


def validate_municipal_qa(qa: pd.DataFrame, zones: pd.DataFrame) -> None:
    """Check the municipal QA table against itself and the polygons; raise ``ValueError`` listing every violation.

    Rules: unique lower-case ``row_id``; an 8-digit ZGB ``municipality_ags``; ``subject`` and ``reference`` named;
    areas numbers >= 0 and the overlap at most the smaller area (up to the rounding); both shares recomputed from the
    recorded areas (empty when the denominator is 0); feature counts whole numbers with the overlapping ones at most
    all; a ``release_zone_id`` names a polygon of ``zones`` whose area (EPSG:25832) equals ``subject_area_m2`` within
    ``RELEASE_AREA_TOLERANCE_M2``; every ``municipal_street_sections_buffered``, ``campus_detection_zones``,
    ``campus_outline_and_detection_zones`` and ``single_site_buffered`` polygon and every polygon with a
    ``reconstructed_section_m2`` value is the release subject of at least one row (``municipal_zone_ids``).
    """
    pz._check_columns(qa, MUNICIPAL_QA_COLUMNS, "municipal QA table")
    problems = []
    if qa.empty:
        problems.append("no rows")
    duplicated = sorted(set(qa["row_id"][qa["row_id"].duplicated()]))
    if duplicated:
        problems.append(f"duplicate row_id(s) {duplicated}")
    areas = {str(zone_id): float(geometry.area) for zone_id, geometry in zip(zones["zone_id"], zones.geometry)}
    subjects = set()
    for _, row in qa.iterrows():
        prefix = f"row {row['row_id']!r}"
        if not _ROW_ID.match(str(row["row_id"])):
            problems.append(f"{prefix}: row_id must be lower-case ASCII letters, digits and '_'")
        if not _AGS.match(str(row["municipality_ags"])) or str(row["municipality_ags"])[:5] not in pz.ZGB_COUNTY_KEYS:
            problems.append(f"{prefix}: municipality_ags {row['municipality_ags']!r} is not an 8-digit AGS of the ZGB")
        for column in ("subject", "reference"):
            if not row[column]:
                problems.append(f"{prefix}: {column} is empty")
        numbers = {column: pz._qa_number(row[column]) for column in _AREA_COLUMNS}
        bad = [column for column, value in numbers.items() if value is None or not math.isfinite(value) or value < 0]
        if bad:
            problems.append(f"{prefix}: {', '.join(bad)} must be a number of m2 >= 0")
            continue
        subject, reference, overlap = (numbers[column] for column in _AREA_COLUMNS)
        if overlap > min(subject, reference) + 2 * _AREA_ROUNDING_M2:
            problems.append(f"{prefix}: overlap_area_m2 {overlap} exceeds the smaller of the subject ({subject}) and "
                            f"the reference ({reference})")
        for column, denominator in (("subject_share_in_reference", subject), ("reference_share_in_subject", reference)):
            message = _share_problem(prefix, column, row[column], overlap, denominator)
            if message:
                problems.append(message)
        counts = [row[column] for column in _COUNT_COLUMNS]
        if not all(re.fullmatch(r"\d+", str(count)) for count in counts):
            problems.append(f"{prefix}: {', '.join(_COUNT_COLUMNS)} must be whole numbers >= 0, found {counts}")
        elif int(counts[1]) > int(counts[0]):
            problems.append(f"{prefix}: reference_features_overlapping {counts[1]} exceeds reference_features "
                            f"{counts[0]}")
        zone_id = row["release_zone_id"]
        if zone_id:
            subjects.add(zone_id)
            if zone_id not in areas:
                problems.append(f"{prefix}: release_zone_id {zone_id!r} has no polygon")
            elif abs(areas[zone_id] - subject) > RELEASE_AREA_TOLERANCE_M2:
                problems.append(f"{prefix}: subject_area_m2 {subject} but the polygon {zone_id!r} has "
                                f"{areas[zone_id]:.1f} m2 (stale table: rerun the municipal step)")
    missing = sorted(set(municipal_zone_ids(zones)) - subjects)
    if missing:
        problems.append(f"zone(s) {missing} (municipal sections, campus unions, single sites or a reconstruction flag) "
                        "are the release subject of no row")
    if problems:
        raise ValueError("invalid municipal QA table:\n  " + "\n  ".join(problems))
