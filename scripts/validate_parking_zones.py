"""Validate the committed parking cost zones, tariffs and coverage register and print their coverage (issue #249).

Checks (via ``braunschweig.parking.zones``, plus the per-row contract of the tariff model through
``braunschweig.parking.tariff_export.tariff_row_to_zone``): the tariff table (types, required fields per zone type,
fee windows, paired fields, workplace classes, no test-set marker; every row must also convert into the tariff model
the MATSim side reads), the zone polygons (EPSG:25832 after loading, valid, provenance,
pairwise overlap <= ``OVERLAP_TOLERANCE_M2``), one tariff row per polygon and vice versa, the coverage register (one status
row per municipality, reasons, sources, ``zoned`` <=> tariff rows, hence a polygon for every zoned municipality) and its
size against the municipality universe of the pipeline (``--expected-municipality-count``), and the curation QA of the
rule-based cores (``--qa-path``, ``parking_zones_2026_qa.csv``; required as soon as a polygon has geometry_source
``osm_fee_erosion``): ``braunschweig.parking.zones.validate_zone_qa`` against the polygons, plus the pre-registered
acceptance rule Q4 (``braunschweig.parking.zone_geometry.core_acceptance``) re-applied to every row that has a
response. Prints counts per zone type, geometry source (with the area mix), fee-window source and municipality, the
register status counts and the QA decisions; exits 1 on any violation, 0 otherwise.

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
from braunschweig.parking import tariff_export  # noqa: E402
from braunschweig.parking import zone_geometry  # noqa: E402
from braunschweig.parking import zones as pz  # noqa: E402

DEFAULT_ZONES_PATH = "braunschweig/parking/parking_zones_2026.geojson"
DEFAULT_TARIFFS_PATH = "braunschweig/parking/parking_tariffs_2026.csv"
DEFAULT_REGISTER_PATH = "braunschweig/parking/parking_coverage_register_2026.csv"
DEFAULT_QA_PATH = "braunschweig/parking/parking_zones_2026_qa.csv"
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


def check_zone_qa(qa, zones, tariffs) -> None:
    """``validate_zone_qa`` plus rule Q4 re-applied to every row with a response; raise ``ValueError``."""
    pz.validate_zone_qa(qa, zones, tariffs)
    problems = []
    for _, row in qa.iterrows():
        if row["q4_decision"] == "request_failed":
            continue
        completeness = float(row["tagging_completeness"]) if row["tagging_completeness"] else math.nan
        expected = zone_geometry.core_acceptance(float(row["core_area_m2"]), completeness)
        if expected.decision != row["q4_decision"]:
            problems.append(f"ags {row['ags']}: q4_decision {row['q4_decision']!r} but rule Q4 gives "
                            f"{expected.decision!r} (core {row['core_area_m2']} m2, tagging completeness "
                            f"{row['tagging_completeness'] or 'undefined'}; {expected.reason or 'both thresholds met'})")
    if problems:
        raise ValueError("zone QA table contradicts the acceptance rule Q4:\n  " + "\n  ".join(problems))


def _print_geometry_source_mix(zones) -> None:
    areas = zones.geometry.area.groupby(zones["geometry_source"]).agg(["count", "sum"]).sort_index()
    total = float(areas["sum"].sum())
    print("[parking-validate] geometry_source mix: " + ", ".join(
        f"{source} {int(row['count'])} zones {row['sum'] / 1e6:.3f} km2 ({100.0 * row['sum'] / total:.1f} %)"
        for source, row in areas.iterrows()))


def validate(data_path: Path, zones_path: str, tariffs_path: str, register_path: str,
             expected_municipality_count: int, qa_path: str = DEFAULT_QA_PATH) -> None:
    """Run every check; raise ``ValueError`` on the first failing group and print the coverage summary."""
    tariffs = pz.load_tariffs(data_path / tariffs_path)
    pz.validate_tariffs(tariffs, allow_fixture_marker=False)
    check_tariff_model_rows(tariffs)
    zones = pz.load_zone_polygons(data_path / zones_path)
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-path", required=True, help="the eqasim-data/data directory (config key data_path)")
    parser.add_argument("--zones-path", default=DEFAULT_ZONES_PATH, help="relative to --data-path")
    parser.add_argument("--tariffs-path", default=DEFAULT_TARIFFS_PATH, help="relative to --data-path")
    parser.add_argument("--register-path", default=DEFAULT_REGISTER_PATH, help="relative to --data-path")
    parser.add_argument("--expected-municipality-count", type=int, default=DEFAULT_EXPECTED_MUNICIPALITY_COUNT)
    parser.add_argument("--qa-path", default=DEFAULT_QA_PATH, help="zone QA table, relative to --data-path")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
    try:
        validate(Path(args.data_path), args.zones_path, args.tariffs_path, args.register_path,
                 args.expected_municipality_count, args.qa_path)
    except (ValueError, FileNotFoundError) as error:
        print(f"[parking-validate] FAILED: {error}")
        return 1
    print("[parking-validate] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
