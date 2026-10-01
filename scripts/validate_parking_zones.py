"""Validate the committed parking cost zones, tariffs and coverage register and print their coverage (issue #249).

Checks (via ``braunschweig.parking.zones``, plus the per-row contract of the tariff model through
``braunschweig.parking.tariff_export.tariff_row_to_zone``): the tariff table (types, required fields per zone type,
fee windows, paired fields, workplace classes, no test-set marker; every row must also convert into the tariff model
the MATSim side reads), the zone polygons (EPSG:25832 after loading, valid as stored: no polygon may need the repair of
the loader, ``max_repairs=0``; provenance, pairwise overlap <= ``OVERLAP_TOLERANCE_M2``), one tariff row per polygon and
vice versa, the coverage register (one status
row per municipality, reasons, sources, ``zoned`` <=> tariff rows, hence a polygon for every zoned municipality) and its
size against the municipality universe of the pipeline (``--expected-municipality-count``), and the curation QA of the
rule-based cores (``--qa-path``, ``parking_zones_2026_qa.csv``; required as soon as a polygon has geometry_source
``osm_fee_erosion``): ``braunschweig.parking.zones.validate_zone_qa`` against the polygons, plus the pre-registered
acceptance rule Q4 (``braunschweig.parking.zone_geometry.core_acceptance``) re-applied to every row that has a
response. The QA table of the majority rule over the parking supply (``--supply-qa-path``,
``parking_zones_2026_supply_share_qa.csv``; spec Amendment B; required as soon as a polygon has geometry_source
``osm_supply_majority``): ``braunschweig.parking.supply_share_qa.validate_supply_share_qa`` against the polygons, the
default parameters of owner decision 2 (share 0.3) on every row and the application gate re-applied: H1 (the B5 gate
on the Braunschweig recall and precision) and H2 (the pre-registered holdout check recomputed from the holdout overlaps
of the town rows); the recorded ``b5_passed`` and ``h2_passed`` and every decision must follow from them; when
present, the release of the classified cells (``--paid-share-path``, ``parking_paid_share_2026.csv.gz``,
``supply_share_qa.load_paid_share_release``); and the municipal QA table (``--municipal-qa-path``,
``parking_zones_2026_municipal_qa.csv``; spec Amendment C; required as soon as a polygon has geometry_source
``municipal_street_sections_buffered`` or a ``reconstructed_section_m2`` value):
``braunschweig.parking.municipal_zone_qa.validate_municipal_qa`` against the polygons. Prints counts per zone type,
geometry source (with the area mix), fee-window source and municipality, the register status counts, the QA decisions,
the H1 and H2 results and the municipal QA rows; exits 1 on any violation, 0 otherwise.

Usage::

    python scripts/validate_parking_zones.py --data-path eqasim-data/data
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from pathlib import Path

# Running the file directly puts scripts/ on sys.path; the repository root holds the braunschweig package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from braunschweig.parking import municipal_zone_qa  # noqa: E402
from braunschweig.parking import supply_share  # noqa: E402
from braunschweig.parking import supply_share_qa  # noqa: E402
from braunschweig.parking import tariff_export  # noqa: E402
from braunschweig.parking import zone_geometry  # noqa: E402
from braunschweig.parking import zones as pz  # noqa: E402

DEFAULT_ZONES_PATH = "braunschweig/parking/parking_zones_2026.geojson"
DEFAULT_TARIFFS_PATH = "braunschweig/parking/parking_tariffs_2026.csv"
DEFAULT_REGISTER_PATH = "braunschweig/parking/parking_coverage_register_2026.csv"
DEFAULT_QA_PATH = "braunschweig/parking/parking_zones_2026_qa.csv"
DEFAULT_SUPPLY_QA_PATH = "braunschweig/parking/parking_zones_2026_supply_share_qa.csv"
DEFAULT_PAID_SHARE_PATH = "braunschweig/parking/parking_paid_share_2026.csv.gz"
DEFAULT_MUNICIPAL_QA_PATH = "braunschweig/parking/parking_zones_2026_municipal_qa.csv"
#: The spatial units of data.spatial.municipalities for the eight ZGB counties (113 Gemeinden and 10 gemeindefreie
#: Gebiete, VG250 as cached by the pipeline); the register must carry exactly one status row for each.
DEFAULT_EXPECTED_MUNICIPALITY_COUNT = 123


def _print_counts(title: str, counts) -> None:
    print(f"[parking-validate] {title}: " + ", ".join(f"{key} {value}" for key, value in counts.items()))


def check_tariff_model_rows(tariffs) -> None:
    """Raise ``ValueError`` listing every row the tariff-model export rejects (``tariff_row_to_zone``).

    ``validate_tariffs`` checks the table rules of spec 3.1/5.3; the export additionally converts every row into
    the integer-cent ``braunschweig.parking.cost.ZoneTariff`` whose construction is the contract the Java side
    mirrors. Running both here keeps the CLI from printing OK for a table the prepared scenario would refuse.
    """
    problems = []
    for row in tariffs.to_dict(orient="records"):
        try:
            tariff_export.tariff_row_to_zone(row)
        except ValueError as error:
            problems.append(f"zone {row['zone_id']!r}: {error}")
    if problems:
        raise ValueError("tariff rows the tariff model rejects:\n  " + "\n  ".join(problems))


#: QA column -> pre-registered construction parameter (``zone_geometry.PRE_REGISTERED_PARAMETERS``).
PRE_REGISTERED_QA_COLUMNS = {"walk_m": "walk_m", "maximum_filled_hole_m2": "maximum_filled_hole_m2",
                             "minimum_island_m2": "minimum_island_m2"}


def check_zone_qa(qa, zones, tariffs) -> None:
    """``validate_zone_qa`` plus, for every row with a response, the pre-registered parameters (a sensitivity arm
    never stands in for the pre-registered run) and rule Q4 re-applied; raise ``ValueError``."""
    pz.validate_zone_qa(qa, zones, tariffs)
    problems = []
    pre_registered = zone_geometry.PRE_REGISTERED_PARAMETERS.as_dict()
    for _, row in qa.iterrows():
        if row["q4_decision"] == "request_failed":
            continue
        for column, parameter in PRE_REGISTERED_QA_COLUMNS.items():
            expected = pre_registered[parameter]
            if not math.isclose(float(row[column]), expected, rel_tol=1e-9):
                problems.append(f"ags {row['ags']}: {column} = {row[column]} is not the pre-registered {expected:g}")
        completeness = float(row["tagging_completeness"]) if row["tagging_completeness"] else math.nan
        expected = zone_geometry.core_acceptance(float(row["core_area_m2"]), completeness)
        if expected.decision != row["q4_decision"]:
            problems.append(f"ags {row['ags']}: q4_decision {row['q4_decision']!r} but rule Q4 gives "
                            f"{expected.decision!r} (core {row['core_area_m2']} m2, tagging completeness "
                            f"{row['tagging_completeness'] or 'undefined'}; {expected.reason or 'both thresholds met'})")
    if problems:
        raise ValueError("zone QA table contradicts the pre-registered parameters or the acceptance rule Q4:\n  "
                         + "\n  ".join(problems))


#: Recorded H2 shares (6 decimals, from unrounded areas) may differ this much from the shares recomputed from the
#: recorded areas (0.1 m2).
H2_RECOMPUTE_TOLERANCE = 1e-5


def recompute_holdout(qa) -> tuple:
    """(pooled H2, problems): H2 recomputed from the holdout overlaps of the town rows
    (``supply_share.holdout_pooled_metrics``) and every town share or recorded pooled value that contradicts it."""
    rows = qa.set_index("ags")
    problems, blocks = [], {}
    for ags in supply_share.HOLDOUT_REFERENCE_ZONES:
        if ags not in rows.index:
            problems.append(f"ags {ags}: the holdout town is missing, H2 cannot be recomputed")
            continue
        row = rows.loc[ags]
        framed = ags in supply_share.HOLDOUT_PRECISION_TOWNS
        blocks[ags] = {"references": dict.fromkeys(zone_id for zone_id in row["holdout_references"].split(";")),
                       "reference_area_m2": float(row["holdout_reference_area_m2"]),
                       "rule_inside_reference_m2": float(row["holdout_rule_inside_reference_m2"]),
                       "rule_inside_query_box_m2": float(row["holdout_rule_inside_query_box_m2"]) if framed else None}
        shares = {"holdout_recall": blocks[ags]["rule_inside_reference_m2"] / blocks[ags]["reference_area_m2"]}
        if framed:
            shares["holdout_precision"] = (blocks[ags]["rule_inside_reference_m2"]
                                           / blocks[ags]["rule_inside_query_box_m2"])
        for column, value in shares.items():
            if not math.isclose(float(row[column]), value, abs_tol=H2_RECOMPUTE_TOLERANCE):
                problems.append(f"ags {ags}: {column} {row[column]} but {value:.6f} recomputed from the holdout "
                                "overlaps of its row")
    if problems:
        return None, problems
    pooled = supply_share.holdout_pooled_metrics(blocks)
    gate_row = rows.loc[supply_share.B5_MUNICIPALITY_AGS]
    for column, key in (("h2_pooled_recall", "pooled_recall"), ("h2_pooled_precision", "pooled_precision"),
                        ("h2_minimum_town_recall", "minimum_town_recall")):
        if not math.isclose(float(gate_row[column]), pooled[key], abs_tol=H2_RECOMPUTE_TOLERANCE):
            problems.append(f"ags {supply_share.B5_MUNICIPALITY_AGS}: {column} {gate_row[column]} but {pooled[key]:.6f} "
                            "recomputed from the holdout overlaps of the town rows")
    return pooled, problems


def check_supply_share_qa(qa, zones, tariffs) -> None:
    """``supply_share_qa.validate_supply_share_qa`` plus the default parameters of owner decision 2 on every row (an arm
    never stands in for B) and the application gate re-applied: H1 (the B5 gate on the Braunschweig recall and
    precision) and H2 (recomputed from the holdout overlaps of the town rows, ``recompute_holdout``); the recorded
    ``b5_passed`` and ``h2_passed`` and the decision of every ``zones_from_rule`` row must follow from them; raise
    ``ValueError``."""
    supply_share_qa.validate_supply_share_qa(qa, zones, tariffs)
    problems = []
    default = supply_share.DEFAULT_SUPPLY_PARAMETERS.as_dict()
    for _, row in qa.iterrows():
        for column, parameter in supply_share_qa.QA_PARAMETER_COLUMNS.items():
            expected = default[parameter]
            if not math.isclose(float(row[column]), expected, rel_tol=1e-9):
                problems.append(f"ags {row['ags']}: {column} = {row[column]} is not the default {expected:g} (owner "
                                "decision 2)")
    b5 = qa[qa["ags"] == supply_share.B5_MUNICIPALITY_AGS].iloc[0]
    h1_passed = supply_share.passes_validation({"recall": float(b5["b5_recall"]),
                                                "precision": float(b5["b5_precision"])})
    gate = "true" if h1_passed else "false"
    if b5["b5_passed"] != gate:
        problems.append(f"ags {b5['ags']}: b5_passed {b5['b5_passed']!r} but the H1 gate gives {gate!r} (recall "
                        f"{b5['b5_recall']}, precision {b5['b5_precision']}, minimum "
                        f"{supply_share.VALIDATION_MINIMUM:.2f})")
    pooled, holdout_problems = recompute_holdout(qa)
    problems += holdout_problems
    h2_passed = bool(pooled and pooled["passes"])
    gate = "true" if h2_passed else "false"
    if pooled is not None and b5["h2_passed"] != gate:
        problems.append(f"ags {b5['ags']}: h2_passed {b5['h2_passed']!r} but the H2 gate gives {gate!r} (pooled recall "
                        f"{pooled['pooled_recall']:.6f}, pooled precision {pooled['pooled_precision']:.6f}, smallest town "
                        f"recall {pooled['minimum_town_recall']:.6f})")
    passed = h1_passed and h2_passed
    allowed = ("applied", "no_rule_polygon") if passed else ("gate_failed",)
    for _, row in qa[qa["role"] == "zones_from_rule"].iterrows():
        if row["decision"] not in allowed:
            problems.append(f"ags {row['ags']}: decision {row['decision']!r} although the gate (H1 and H2) "
                            f"{'passed' if passed else 'failed'} (allowed {list(allowed)})")
    if problems:
        raise ValueError("supply-share QA table contradicts the default parameters or the H1 / H2 gates:\n  "
                         + "\n  ".join(problems))


def _print_geometry_source_mix(zones) -> None:
    areas = zones.geometry.area.groupby(zones["geometry_source"]).agg(["count", "sum"]).sort_index()
    total = float(areas["sum"].sum())
    print("[parking-validate] geometry_source mix: " + ", ".join(
        f"{source} {int(row['count'])} zones {row['sum'] / 1e6:.3f} km2 ({100.0 * row['sum'] / total:.1f} %)"
        for source, row in areas.iterrows()))


def validate(data_path: Path, zones_path: str, tariffs_path: str, register_path: str,
             expected_municipality_count: int, qa_path: str = DEFAULT_QA_PATH,
             supply_qa_path: str = DEFAULT_SUPPLY_QA_PATH, paid_share_path: str = DEFAULT_PAID_SHARE_PATH,
             municipal_qa_path: str = DEFAULT_MUNICIPAL_QA_PATH) -> None:
    """Run every check; raise ``ValueError`` on the first failing group and print the coverage summary."""
    tariffs = pz.load_tariffs(data_path / tariffs_path)
    pz.validate_tariffs(tariffs, allow_fixture_marker=False)
    check_tariff_model_rows(tariffs)
    # the committed polygons must be valid as stored: a polygon the loader has to repair is not the polygon the file states
    zones = pz.load_zone_polygons(data_path / zones_path, max_repairs=0)
    markers = sorted(zones.loc[zones["geometry_source"] == pz.FIXTURE_MARKER, "zone_id"])
    if markers:
        raise ValueError(f"zone polygons carry the test-set marker {pz.FIXTURE_MARKER!r}: {markers}")
    pz.cross_validate(zones, tariffs)
    register = pz.load_coverage_register(data_path / register_path)
    pz.validate_coverage_register(register, tariffs)
    status_rows = register[register["status"] != "excluded"]
    if len(status_rows) != expected_municipality_count:
        raise ValueError(f"coverage register has {len(status_rows)} municipality rows, expected "
                         f"{expected_municipality_count} (one per spatial unit of the ZGB counties)")
    # A zoned municipality always owns a polygon here: cross_validate pairs every tariff row with a polygon and
    # validate_coverage_register pairs every zoned municipality with a tariff row.
    qa_file = data_path / qa_path
    eroded = sorted(zones.loc[zones["geometry_source"] == pz.EROSION_GEOMETRY_SOURCE, "zone_id"])
    qa = None
    if qa_file.is_file():
        qa = pz.load_zone_qa(qa_file)
        check_zone_qa(qa, zones, tariffs)
    elif eroded:
        raise ValueError(f"{len(eroded)} osm_fee_erosion zone(s) {eroded} but no zone QA table at {qa_file}")
    supply_file = data_path / supply_qa_path
    majority = sorted(zones.loc[zones["geometry_source"] == pz.SUPPLY_MAJORITY_GEOMETRY_SOURCE, "zone_id"])
    supply_qa = None
    if supply_file.is_file():
        supply_qa = supply_share_qa.load_supply_share_qa(supply_file)
        check_supply_share_qa(supply_qa, zones, tariffs)
    elif majority:
        raise ValueError(f"{len(majority)} osm_supply_majority zone(s) {majority} but no supply-share QA table at "
                         f"{supply_file}")
    release_file = data_path / paid_share_path
    release = supply_share_qa.load_paid_share_release(release_file) if release_file.is_file() else None
    municipal_file = data_path / municipal_qa_path
    municipal = municipal_zone_qa.municipal_zone_ids(zones)
    municipal_qa = None
    if municipal_file.is_file():
        municipal_qa = municipal_zone_qa.load_municipal_qa(municipal_file)
        municipal_zone_qa.validate_municipal_qa(municipal_qa, zones)
    elif municipal:
        raise ValueError(f"{len(municipal)} zone(s) {municipal} from municipal sources but no municipal QA table at "
                         f"{municipal_file}")

    merged = zones.merge(tariffs, on="zone_id", suffixes=("_polygon", ""))
    merged["area_km2"] = merged.geometry.area / 1e6
    print(f"[parking-validate] {len(zones)} zones, {len(tariffs)} tariff rows, {len(register)} register rows "
          f"({len(status_rows)} municipalities)")
    _print_counts("zone_type", tariffs["zone_type"].value_counts().sort_index().to_dict())
    _print_counts("geometry_source", zones["geometry_source"].value_counts().sort_index().to_dict())
    _print_geometry_source_mix(zones)
    _print_counts("fee_window_source", tariffs["fee_window_source"].value_counts().sort_index().to_dict())
    names = status_rows.set_index("ags")["name"]
    per_municipality = merged.groupby("municipality_ags").agg(zones=("zone_id", "count"), area_km2=("area_km2", "sum"))
    for ags, row in per_municipality.iterrows():
        print(f"[parking-validate] municipality {ags} {names.get(ags, '?')}: {int(row['zones'])} zones, "
              f"{row['area_km2']:.3f} km2")
    _print_counts("register status", register["status"].value_counts().reindex(pz.REGISTER_STATUSES, fill_value=0).to_dict())
    if qa is None:
        print(f"[parking-validate] zone QA: no table at {qa_file} (no osm_fee_erosion zones)")
    else:
        _print_counts("zone QA q4_decision", qa["q4_decision"].value_counts().reindex(pz.ZONE_QA_DECISIONS,
                                                                                       fill_value=0).to_dict())
        applied = qa[qa["applied"] == "true"]
        print(f"[parking-validate] zone QA: {len(qa)} municipalities, applied {len(applied)}: " + (", ".join(
            f"{row['ags']} ({row['zone_ids']})" for _, row in applied.iterrows()) or "none"))
    if supply_qa is None:
        print(f"[parking-validate] supply-share QA: no table at {supply_file} (no osm_supply_majority zones)")
    else:
        b5 = supply_qa[supply_qa["ags"] == supply_share.B5_MUNICIPALITY_AGS].iloc[0]
        decisions = supply_qa["decision"].value_counts().reindex(supply_share_qa.SUPPLY_SHARE_DECISIONS, fill_value=0)
        print(f"[parking-validate] supply-share QA: {len(supply_qa)} towns at the share {b5['share_threshold']}, H1 "
              f"recall {b5['b5_recall']}, precision {b5['b5_precision']}, passed {b5['b5_passed']}; H2 pooled recall "
              f"{b5['h2_pooled_recall']}, pooled precision {b5['h2_pooled_precision']}, smallest town recall "
              f"{b5['h2_minimum_town_recall']}, passed {b5['h2_passed']}; decision " + ", ".join(
                  f"{name} {count}" for name, count in decisions.items()))
    if release is not None:
        counts = release["municipality_ags"].value_counts().sort_index()
        print(f"[parking-validate] paid-share release: {len(release)} classified cells (" + ", ".join(
            f"{ags} {count}" for ags, count in counts.items()) + ")")
    if municipal_qa is None:
        print(f"[parking-validate] municipal QA: no table at {municipal_file} (no zone from municipal sources)")
    else:
        rows = municipal_qa["municipality_ags"].value_counts().sort_index()
        print(f"[parking-validate] municipal QA: {len(municipal_qa)} rows (" + ", ".join(
            f"{ags} {count}" for ags, count in rows.items()) + "); zones from municipal sources: "
              + (", ".join(municipal) or "none"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-path", required=True, help="the eqasim-data/data directory (config key data_path)")
    parser.add_argument("--zones-path", default=DEFAULT_ZONES_PATH, help="relative to --data-path")
    parser.add_argument("--tariffs-path", default=DEFAULT_TARIFFS_PATH, help="relative to --data-path")
    parser.add_argument("--register-path", default=DEFAULT_REGISTER_PATH, help="relative to --data-path")
    parser.add_argument("--expected-municipality-count", type=int, default=DEFAULT_EXPECTED_MUNICIPALITY_COUNT)
    parser.add_argument("--qa-path", default=DEFAULT_QA_PATH, help="zone QA table, relative to --data-path")
    parser.add_argument("--supply-qa-path", default=DEFAULT_SUPPLY_QA_PATH,
                        help="supply-share QA table (spec Amendment B), relative to --data-path")
    parser.add_argument("--paid-share-path", default=DEFAULT_PAID_SHARE_PATH,
                        help="release of the classified cells (spec Amendment B7), relative to --data-path")
    parser.add_argument("--municipal-qa-path", default=DEFAULT_MUNICIPAL_QA_PATH,
                        help="municipal QA table (spec Amendment C), relative to --data-path")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        validate(Path(args.data_path), args.zones_path, args.tariffs_path, args.register_path,
                 args.expected_municipality_count, args.qa_path, args.supply_qa_path, args.paid_share_path,
                 args.municipal_qa_path)
    except (ValueError, FileNotFoundError) as error:
        print(f"[parking-validate] FAILED: {error}")
        return 1
    print("[parking-validate] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
