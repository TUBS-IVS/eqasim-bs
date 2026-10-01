"""The committed outputs of the parking supply majority rule (parking cost zones v2, issue #436): the B7 release of the
classified cells and the supply-share QA table, with their schema, file I/O and validation.

* **B7 release** (``release_frame``, ``PAID_SHARE_RELEASE_COLUMNS``, ``validate_paid_share_release``,
  ``deterministic_gzip``, ``load_paid_share_release``): the classified cells of ``supply_share.paid_share_raster``,
  preparation of the probabilistic variant C; no stage reads them yet.
* **QA table** ``parking_zones_2026_supply_share_qa.csv`` (``SUPPLY_SHARE_QA_COLUMNS``, ``load_supply_share_qa``,
  ``validate_supply_share_qa``): one row per curated town, written by
  ``scripts/curation/parking_zones_2026/assemble_parking_zones.py --supply-share-dir`` (which holds the column glossary
  and the row builder); ``scripts/validate_parking_zones.py`` re-applies the gates on it.

Release rows, the deterministic gzip encoding, readers and validators; the two readers are the module's only
file-system access (the assembly script writes both files with their headers). Split out of ``supply_share`` in
Task 1c fix round 1 (ruling R-T1c-b), behaviour-preserving.
"""
from __future__ import annotations

import gzip
import io
import logging
import math
import re
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.parking import supply_share as ss
from braunschweig.parking import zone_geometry as zg
from braunschweig.parking import zones as pz

log = logging.getLogger(__name__)

#: B7: the committed release of the classified cells.
PAID_SHARE_RELEASE_COLUMNS = ("x_m", "y_m", "municipality_ags", "paid_share", "usable_spaces",
                              "heuristic_capacity_share")

_AGS = re.compile(r"^\d{8}$")


# --------------------------------------------------------------------------- B7: the release of the classified cells


def release_frame(raster: pd.DataFrame, municipality_ags: str) -> pd.DataFrame:
    """The classified cells of one town as release rows (``PAID_SHARE_RELEASE_COLUMNS``): cell centres in EPSG:25832
    metres (0.1 m), ``paid_share`` and ``heuristic_capacity_share`` to 6 decimals, ``usable_spaces`` to 0.01 spaces."""
    if not _AGS.match(str(municipality_ags)):
        raise ValueError(f"municipality_ags must be an 8-digit AGS, got {municipality_ags!r}")
    zg._require_columns(raster, ss.RASTER_COLUMNS, "raster (run paid_share_raster first)")
    classified = raster[raster["classified"].astype(bool)]
    return pd.DataFrame({"x_m": classified["x_m"].round(1).to_numpy(), "y_m": classified["y_m"].round(1).to_numpy(),
                         "municipality_ags": str(municipality_ags),
                         "paid_share": classified["paid_share"].round(6).to_numpy(),
                         "usable_spaces": classified["usable_spaces"].round(2).to_numpy(),
                         "heuristic_capacity_share": classified["heuristic_capacity_share"].round(6).to_numpy()},
                        columns=list(PAID_SHARE_RELEASE_COLUMNS))


def validate_paid_share_release(release: pd.DataFrame) -> None:
    """Raise ``ValueError`` listing every violation: the release columns in order, 8-digit AGS, finite coordinates, one
    row per cell, ``paid_share`` and ``heuristic_capacity_share`` in [0, 1], ``usable_spaces`` > 0."""
    if list(release.columns) != list(PAID_SHARE_RELEASE_COLUMNS):
        raise ValueError(f"paid-share release: columns {list(release.columns)}, expected "
                         f"{list(PAID_SHARE_RELEASE_COLUMNS)}")
    problems = []
    if release.empty:
        problems.append("no rows")
    bad_ags = sorted({str(value) for value in release["municipality_ags"] if not _AGS.match(str(value))})
    if bad_ags:
        problems.append(f"municipality_ags not an 8-digit AGS: {bad_ags[:5]}")
    for column, low, high in (("paid_share", 0.0, 1.0), ("heuristic_capacity_share", 0.0, 1.0)):
        values = pd.to_numeric(release[column], errors="coerce").to_numpy(dtype=float)
        invalid = ~(np.isfinite(values) & (values >= low) & (values <= high))
        if invalid.any():
            problems.append(f"{column} outside [{low}, {high}] in {int(invalid.sum())} row(s)")
    usable = pd.to_numeric(release["usable_spaces"], errors="coerce").to_numpy(dtype=float)
    if (~(np.isfinite(usable) & (usable > 0))).any():
        problems.append("usable_spaces must be > 0 (only classified cells are released)")
    coordinates = release[["x_m", "y_m"]].apply(pd.to_numeric, errors="coerce").to_numpy(dtype=float)
    if (~np.isfinite(coordinates)).any():
        problems.append("x_m / y_m must be finite EPSG:25832 metres")
    duplicated = int(release.duplicated(subset=["x_m", "y_m"]).sum())
    if duplicated:
        problems.append(f"{duplicated} duplicate cell(s): one row per cell")
    if problems:
        raise ValueError("invalid paid-share release: " + "; ".join(problems))


def deterministic_gzip(text: str) -> bytes:
    """``text`` (UTF-8) gzip-compressed without a time stamp or file name in the header: equal text, equal bytes."""
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as stream:
        stream.write(text.encode("utf-8"))
    return buffer.getvalue()


def load_paid_share_release(path) -> pd.DataFrame:
    """Read the gzip CSV release (``#`` lines skipped), typed, and validate it (``validate_paid_share_release``)."""
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"paid-share release missing: {path}")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        lines = [line for line in stream.read().splitlines() if not line.startswith("#")]
    release = pd.read_csv(io.StringIO("\n".join(lines)), dtype={"municipality_ags": str})
    validate_paid_share_release(release)
    return release


# --------------------------------------------------------------------------- the committed QA table


#: ``parking_zones_2026_supply_share_qa.csv``: one row per curated town, written by
#: ``scripts/curation/parking_zones_2026/assemble_parking_zones.py --supply-share-dir`` (its header defines every
#: column). ``role`` says whether the rule may replace polygons (``zones_from_rule``) or is QA only; ``decision`` is
#: ``applied``, ``gate_failed`` (H1 or H2 of owner decision 2 failed, nothing applied anywhere), ``no_rule_polygon``
#: (both passed, nothing to apply) or ``qa_only``; ``applied`` and ``zone_ids`` name the ``osm_supply_majority``
#: polygons. The Braunschweig row carries H1 (the B5 columns, computed with the table's default parameters) and the
#: pooled H2; every holdout town its holdout overlaps; every town its payment evidence (variant T).
SUPPLY_SHARE_QA_COLUMNS = (
    "ags", "name", "role", "osm_extract", "osm_extract_md5", "osm_timestamp", "walk_m", "share_threshold",
    "minimum_usable_spaces", "cell_m", "smoothing_m", "minimum_island_m2", "street_ways", "street_side_areas",
    "offstreet_lots", "paid_elements", "restricted_elements", "free_elements", "excluded_elements", "overpass_response",
    "overpass_osm_timestamp", "overpass_paid_elements", "overpass_restricted_elements", "overpass_free_elements",
    "overpass_excluded_elements", "cross_check", "usable_spaces", "paid_spaces", "restricted_spaces", "free_spaces",
    "tagged_capacity_share", "heuristic_capacity_share", "free_street_spaces_without_fee_tag_share",
    "offstreet_lots_without_fee_tag", "offstreet_spaces_without_fee_tag_share", "ticket_machines",
    "app_payment_elements", "payment_evidence_paid_elements", "payment_evidence_paid_spaces", "cells",
    "classified_cells", "classified_cell_share", "paid_cells",
    "rule_area_m2", "rule_parts", "b5_recall", "b5_precision", "b5_passed", "h2_pooled_recall", "h2_pooled_precision",
    "h2_minimum_town_recall", "h2_passed", "holdout_references", "holdout_reference_area_m2",
    "holdout_rule_inside_reference_m2", "holdout_rule_inside_query_box_m2", "holdout_recall", "holdout_precision",
    "reference", "rule_share_inside_reference", "reference_share_covered_by_rule", "largest_outline_distance_m",
    "sensitivity", "variant_arms", "applied", "zone_ids", "decision", "note",
)
SUPPLY_SHARE_QA_ROLES = ("zones_from_rule", "qa_only")
SUPPLY_SHARE_DECISIONS = ("applied", "gate_failed", "no_rule_polygon", "qa_only")
_QA_COUNTS = ("street_ways", "street_side_areas", "offstreet_lots", "paid_elements", "restricted_elements",
              "free_elements", "excluded_elements", "overpass_paid_elements", "overpass_restricted_elements",
              "overpass_free_elements", "overpass_excluded_elements", "offstreet_lots_without_fee_tag", "cells",
              "classified_cells", "paid_cells", "rule_parts")
#: Counts that may stay empty (the payment evidence of an inventory read before Task 1c).
_QA_OPTIONAL_COUNTS = ("ticket_machines", "app_payment_elements", "payment_evidence_paid_elements")
_QA_AMOUNTS = ("usable_spaces", "paid_spaces", "restricted_spaces", "free_spaces", "rule_area_m2")
_QA_OPTIONAL_AMOUNTS = ("largest_outline_distance_m", "payment_evidence_paid_spaces", "holdout_reference_area_m2",
                        "holdout_rule_inside_reference_m2", "holdout_rule_inside_query_box_m2")
_QA_SHARES = ("tagged_capacity_share", "heuristic_capacity_share", "free_street_spaces_without_fee_tag_share",
              "offstreet_spaces_without_fee_tag_share",
              "classified_cell_share", "b5_recall", "b5_precision", "h2_pooled_recall", "h2_pooled_precision",
              "h2_minimum_town_recall", "holdout_recall", "holdout_precision", "rule_share_inside_reference",
              "reference_share_covered_by_rule")
#: Columns of the Braunschweig row only: H1 (B5 with the table's parameters) and the pooled H2.
QA_GATE_COLUMNS = ("b5_recall", "b5_precision", "b5_passed", "h2_pooled_recall", "h2_pooled_precision",
                   "h2_minimum_town_recall", "h2_passed")
#: Columns of the holdout towns only (the precision pair of the four towns with a query-box frame only).
QA_HOLDOUT_COLUMNS = ("holdout_references", "holdout_reference_area_m2", "holdout_rule_inside_reference_m2",
                      "holdout_recall")
QA_HOLDOUT_PRECISION_COLUMNS = ("holdout_rule_inside_query_box_m2", "holdout_precision")
#: QA column -> ``SupplyShareParameters`` field and the provenance column of an ``osm_supply_majority`` polygon.
QA_PARAMETER_COLUMNS = {"walk_m": "walk_m", "share_threshold": "share_threshold",
                        "minimum_usable_spaces": "minimum_usable_spaces", "cell_m": "cell_m",
                        "smoothing_m": "smoothing_m", "minimum_island_m2": "minimum_island_m2"}
QA_POLYGON_PROVENANCE = {"walk_m": "supply_walk_m", "share_threshold": "paid_share_threshold",
                         "minimum_usable_spaces": "minimum_usable_spaces"}


def load_supply_share_qa(path) -> pd.DataFrame:
    """Load ``parking_zones_2026_supply_share_qa.csv`` (``#`` lines skipped, every cell as stripped text)."""
    qa = pz._read_documented_csv(path)
    pz._check_columns(qa, SUPPLY_SHARE_QA_COLUMNS, str(path))
    qa = qa[list(SUPPLY_SHARE_QA_COLUMNS)].apply(lambda column: column.str.strip())
    log.info("%s loaded %d supply-share QA rows from %s", ss._LOG_TAG, len(qa), path)
    return qa


def validate_supply_share_qa(qa: pd.DataFrame, zones: pd.DataFrame, tariffs: pd.DataFrame) -> None:
    """Check the supply-share QA table against itself and the polygons; raise ``ValueError`` listing every violation.

    Rules: one row per 8-digit ZGB ``ags``; ``role``, ``decision`` and the literal ``applied`` valid; ``qa_only`` rows
    decide ``qa_only``; an applied row (``decision`` applied) has role ``zones_from_rule`` and zone ids, every other row
    none; snapshot, extract MD5, positive parameters, whole counts, non-negative amounts, shares in [0, 1]; only the
    Braunschweig row carries the gate columns ``QA_GATE_COLUMNS`` (H1 recall and precision, the literal ``b5_passed``,
    the pooled H2 and the literal ``h2_passed``) and it must carry them; the holdout overlaps sit on the holdout towns
    only (``supply_share.HOLDOUT_REFERENCE_ZONES``, the precision pair on ``supply_share.HOLDOUT_PRECISION_TOWNS``
    only) and name their references; an applied row needs ``b5_passed`` and ``h2_passed`` true. Every zone id of an
    applied row is an ``osm_supply_majority`` polygon whose tariff row lies in the row's municipality and whose
    provenance equals the row's parameters and snapshot, and every ``osm_supply_majority`` polygon is listed by
    exactly one applied row. The gates themselves and the default parameters are re-applied by
    ``scripts/validate_parking_zones.py``.
    """
    pz._check_columns(qa, SUPPLY_SHARE_QA_COLUMNS, "supply-share QA table")
    problems = []
    if qa.empty:
        problems.append("no rows")
    duplicated = sorted(set(qa["ags"][qa["ags"].duplicated()]))
    if duplicated:
        problems.append(f"duplicate rows for ags {duplicated}; one row per town")
    b5_rows = qa[qa["ags"] == ss.B5_MUNICIPALITY_AGS]
    gate_passed = (len(b5_rows) == 1 and b5_rows.iloc[0]["b5_passed"] == "true"
                   and b5_rows.iloc[0]["h2_passed"] == "true")
    polygons = zones.set_index("zone_id")
    municipality = tariffs.set_index("zone_id")["municipality_ags"]
    listed = {}
    for _, row in qa.iterrows():
        ags, prefix = row["ags"], f"ags {row['ags']}"
        if not _AGS.match(str(ags)) or str(ags)[:5] not in pz.ZGB_COUNTY_KEYS:
            problems.append(f"{prefix}: not an 8-digit AGS of the ZGB counties")
        if row["role"] not in SUPPLY_SHARE_QA_ROLES:
            problems.append(f"{prefix}: role {row['role']!r} is not one of {list(SUPPLY_SHARE_QA_ROLES)}")
        if row["decision"] not in SUPPLY_SHARE_DECISIONS:
            problems.append(f"{prefix}: decision {row['decision']!r} is not one of {list(SUPPLY_SHARE_DECISIONS)}")
        if (row["role"] == "qa_only") != (row["decision"] == "qa_only"):
            problems.append(f"{prefix}: role {row['role']!r} and decision {row['decision']!r} disagree (qa_only rows "
                            "decide qa_only)")
        if row["applied"] not in ("true", "false"):
            problems.append(f"{prefix}: applied {row['applied']!r}; use the literal 'true' or 'false'")
        if not pz._OSM_TIMESTAMP_PATTERN.match(str(row["osm_timestamp"])):
            problems.append(f"{prefix}: osm_timestamp {row['osm_timestamp']!r} is not 'YYYY-MM-DDTHH:MM:SSZ'")
        if not re.fullmatch(r"[0-9a-f]{32}", str(row["osm_extract_md5"])) or not row["osm_extract"]:
            problems.append(f"{prefix}: osm_extract and its 32-digit MD5 are required")
        for column in QA_PARAMETER_COLUMNS:
            value = pz._qa_number(row[column])
            positive = column in ("walk_m", "cell_m", "share_threshold")
            if value is None or not math.isfinite(value) or value < 0 or (positive and value <= 0) or \
                    (column == "share_threshold" and value > 1):
                problems.append(f"{prefix}: {column} = {row[column]!r} is not a valid parameter")
        for column in _QA_COUNTS + _QA_OPTIONAL_COUNTS:
            if (column in _QA_COUNTS or row[column]) and not re.fullmatch(r"\d+", str(row[column])):
                problems.append(f"{prefix}: {column} = {row[column]!r} must be a whole number >= 0")
        for column in _QA_AMOUNTS + _QA_OPTIONAL_AMOUNTS:
            value = pz._qa_number(row[column])
            required = column in _QA_AMOUNTS
            if (value is None and required) or (value is not None and not (math.isfinite(value) and value >= 0)):
                problems.append(f"{prefix}: {column} = {row[column]!r} must be a number >= 0")
        for column in _QA_SHARES:
            value = pz._qa_number(row[column])
            if value is not None and not (math.isfinite(value) and 0.0 <= value <= 1.0):
                problems.append(f"{prefix}: {column} = {row[column]!r} must lie in [0, 1] (or be empty)")
        gate = [row[column] for column in QA_GATE_COLUMNS]
        if ags == ss.B5_MUNICIPALITY_AGS:
            if row["b5_passed"] not in ("true", "false") or row["h2_passed"] not in ("true", "false") or not all(gate):
                problems.append(f"{prefix}: the Braunschweig row carries {list(QA_GATE_COLUMNS)} with the literal "
                                "b5_passed and h2_passed")
        elif any(gate):
            problems.append(f"{prefix}: only the Braunschweig row ({ss.B5_MUNICIPALITY_AGS}) carries the gate columns "
                            "(H1 and the pooled H2)")
        references = ss.HOLDOUT_REFERENCE_ZONES.get(ags)
        framed = ags in ss.HOLDOUT_PRECISION_TOWNS
        for column in QA_HOLDOUT_COLUMNS + QA_HOLDOUT_PRECISION_COLUMNS:
            expected = references is not None and (column in QA_HOLDOUT_COLUMNS or framed)
            if bool(row[column]) != expected:
                problems.append(f"{prefix}: {column} {'is required' if expected else 'must be empty'} (holdout "
                                f"{'town' if references else 'references only in ' + str(sorted(ss.HOLDOUT_REFERENCE_ZONES))}"
                                f"{', precision frame' if framed else ''})")
        if references is not None and row["holdout_references"] != ";".join(references):
            problems.append(f"{prefix}: holdout_references {row['holdout_references']!r}, expected "
                            f"{';'.join(references)!r}")
        applied = row["applied"] == "true"
        zone_ids = [zone_id.strip() for zone_id in str(row["zone_ids"] or "").split(";") if zone_id.strip()]
        if applied != (row["decision"] == "applied"):
            problems.append(f"{prefix}: applied {row['applied']!r} and decision {row['decision']!r} disagree")
        if applied:
            if row["role"] != "zones_from_rule":
                problems.append(f"{prefix}: applied rows need role 'zones_from_rule', found {row['role']!r}")
            if not gate_passed:
                problems.append(f"{prefix}: applied although the Braunschweig row does not pass H1 and H2")
            if not zone_ids:
                problems.append(f"{prefix}: an applied row lists its zone_ids")
        elif zone_ids:
            problems.append(f"{prefix}: zone_ids {zone_ids} on a row that is not applied")
        for zone_id in zone_ids if applied else []:
            listed.setdefault(zone_id, []).append(ags)
            if zone_id not in polygons.index:
                problems.append(f"{prefix}: zone {zone_id!r} has no polygon")
                continue
            polygon = polygons.loc[zone_id]
            if polygon["geometry_source"] != pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE:
                problems.append(f"{prefix}: zone {zone_id!r} is not an osm_supply_majority polygon "
                                f"({polygon['geometry_source']})")
                continue
            if municipality.get(zone_id) != ags:
                problems.append(f"{prefix}: zone {zone_id!r} has municipality_ags {municipality.get(zone_id)!r}")
            for column, provenance in QA_POLYGON_PROVENANCE.items():
                value = pz._qa_number(row[column])
                if value is None or not math.isclose(float(polygon[provenance]), value, rel_tol=1e-9):
                    problems.append(f"{prefix}: zone {zone_id!r} has {provenance} {polygon[provenance]} but the QA row "
                                    f"{column} {row[column]!r}")
            if polygon["osm_timestamp"] != row["osm_timestamp"]:
                problems.append(f"{prefix}: zone {zone_id!r} has osm_timestamp {polygon['osm_timestamp']!r} but the QA "
                                f"row {row['osm_timestamp']!r}")
    if len(b5_rows) != 1:
        problems.append(f"the Braunschweig row ({ss.B5_MUNICIPALITY_AGS}) with the H1 and H2 gates is missing")
    majority = sorted(zones.loc[zones["geometry_source"] == pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE, "zone_id"]
                      .astype(str))
    for zone_id in majority:
        if zone_id not in listed:
            problems.append(f"zone {zone_id!r} (osm_supply_majority) is not listed in the zone_ids of an applied row")
        elif len(listed[zone_id]) > 1:
            problems.append(f"zone {zone_id!r} is listed by several applied rows {listed[zone_id]}")
    if problems:
        raise ValueError("invalid supply-share QA table:\n  " + "\n  ".join(problems))
