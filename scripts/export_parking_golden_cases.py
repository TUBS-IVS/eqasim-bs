"""Regenerate the parking golden-case and fixture tariff-model JSON files shared with the Java tests (#249, #436).

The fixture tariff set is INSPIRED by the real tariff table but is a test set: it pins arithmetic, not
truth. The script

1. loads the fixture tariff table ``tests/fixtures/parking/parking_tariffs_fixture.csv`` with the production
   loader ``braunschweig.parking.zones.load_tariffs`` (the documented CSV: ``#`` comment lines, identifier
   columns kept as text), so the fixture passes through exactly the code path of the committed tariff table;
2. converts it with the production export (``braunschweig.parking.tariff_export.build_tariff_model``, schema 5)
   together with the fixture resident districts ``tests/fixtures/parking/parking_resident_districts_fixture.geojson``
   (loaded by ``braunschweig.parking.zones.load_resident_districts``; the model lists their ids as
   ``resident_districts``) and the fixture garages ``tests/fixtures/parking/parking_garages_fixture.geojson`` (loaded and
   validated by ``braunschweig.parking.garages``; the model lists the priced ones as ``garages`` with the illustrative
   decays ``golden_cases.FIXTURE_GARAGE_DECAY_M`` and ``FIXTURE_GARAGE_DECAY_COMMUTE_M`` (work and education, spec
   Amendment H), the maximum distance 1000 m and, since schema 5 of the model, the money coefficient
   ``golden_cases.FIXTURE_OPTION_PRICE_BETA_U_PER_EUR``; the three fixture garages with a maximum stay get the dataset column
   ``max_stay_min`` from the sidecar ``tests/fixtures/parking/parking_garages_fixture_max_stay_min.json``, because the geojson is
   a legacy layout whose recorded SHA-256 must not change) and evaluates every
   golden case of ``braunschweig.parking.golden_cases`` (``GOLDEN_CASES``: G01..G38, L01..L08, L01Z..L08Z, V01..V23,
   R01..R13, E01..E38, F01..F08, P01..P06, X01..X09, each priced under its own minimum stay ``minimum_stay_min``, its own
   resident district flag ``resident_of_district``, its EV flag ``ev_exempt``, its price coefficient
   ``option_price_beta_u_per_eur``, its destination and decays, and under the ``resident_permits_valid`` of its fixture zone)
   and every garage option case (``GOLDEN_GARAGE_OPTION_CASES``: O01..O69, S01..S25) with the Python reference
   ``braunschweig.parking.cost``; it writes nothing when a result differs from its hard-coded expectation;
3. writes two sorted-key LF JSON files into ``tests/fixtures/parking/``:
   ``parking_golden_cases.json`` (``schema_version`` ``GOLDEN_SCHEMA_VERSION``, the fixture ``tariffs`` in
   cents, the fixture ``garages``, ``garage_max_distance_m``, ``option_price_beta_u_per_eur``, the ``cases`` and the
   ``garage_option_cases``; read by
   ``tests/test_parking_cost.py`` and the Java ``ParkingCostCalculatorTest``) and ``parking_tariffs_fixture.json`` (the
   spec 5.4 tariff model of the fixture set; ``sources`` holds the path and LF-normalised sha256 of the table, of the
   districts file and of the garages file it was built from).

Re-run it after changing the fixture table, the golden cases or the export; the sync tests in
``tests/test_parking_cost.py`` and ``tests/test_parking_tariff_export.py`` fail until the files are current.
The recorded source of the fixture model is the fixture CSV (path and LF-normalised sha256), so any edit of
that file needs a re-run too.

Usage:
    python scripts/export_parking_golden_cases.py [--tariffs-csv PATH] [--districts-geojson PATH] [--garages-geojson PATH]
                                                  [--output-directory DIR]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Mapping

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.parking import cost, garages as parking_garages, tariff_export, zones  # noqa: E402
from braunschweig.parking.cost import ZoneTariff  # noqa: E402
from braunschweig.parking.golden_cases import (FIXTURE_GARAGE_DECAY_COMMUTE_M, FIXTURE_GARAGE_DECAY_M,  # noqa: E402
                                               FIXTURE_OPTION_PRICE_BETA_U_PER_EUR, GOLDEN_CASES,
                                               GOLDEN_GARAGE_OPTION_CASES, golden_case_mismatches, option_case_mismatches)

log = logging.getLogger("export_parking_golden_cases")

FIXTURE_DIRECTORY = REPO / "tests" / "fixtures" / "parking"
FIXTURE_TARIFFS_CSV = FIXTURE_DIRECTORY / "parking_tariffs_fixture.csv"
FIXTURE_DISTRICTS_GEOJSON = FIXTURE_DIRECTORY / "parking_resident_districts_fixture.geojson"
FIXTURE_GARAGES_GEOJSON = FIXTURE_DIRECTORY / "parking_garages_fixture.geojson"
GOLDEN_CASES_FILE_NAME = "parking_golden_cases.json"
FIXTURE_MODEL_FILE_NAME = "parking_tariffs_fixture.json"
#: 2 since parking cost zones v2 (issue #436): every case carries ``minimum_stay_min`` and the ``tariffs`` are zone
#: entries of tariff schema 2. A reader of version 1 would price every case without its minimum stay. 3 since the
#: resident district rule R2 (spec Amendment C3): every case also carries ``resident_of_district``; a reader of
#: version 2 would price every R case as a stay outside the person's district. 4 since the garage options of spec
#: Amendment E: the document carries ``garages``, ``garage_max_distance_m`` and ``garage_option_cases``, and every case
#: carries ``destination_x_m``, ``destination_y_m``, ``garage_decay_m`` and ``expected_garage_probability``; a reader of
#: version 3 would price every E case without its garage options. 5 since the separate commuter decay length of spec
#: Amendment H: every case also carries ``garage_decay_commute_m``; a reader of version 4 would price every work and
#: education stay with the decay of the visitors. 6 since the v3 mechanics (spec v3 A1, A2, A5, issue #436): every case also
#: carries ``ev_exempt`` and ``option_price_beta_u_per_eur``, the document carries ``option_price_beta_u_per_eur``, the fixture
#: garages and zones carry ``max_stay_min`` and ``ev_free_minutes`` and the case lists gained F, P, X and S; a reader of version
#: 5 would price every P case without the price weights and every X case without the exemption.
GOLDEN_SCHEMA_VERSION = 6
#: Snapshot label of the fixture tariff model: the date the fixture set was defined. A test label, not a
#: real tariff state.
FIXTURE_SNAPSHOT_DATE = "2026-09-28"
FIXTURE_SOURCE_ID = "parking_tariffs_fixture"
FIXTURE_DISTRICTS_SOURCE_ID = "parking_resident_districts_fixture"
FIXTURE_GARAGES_SOURCE_ID = "parking_garages_fixture"
#: The maximum stay in whole minutes (spec v3 A1, ASSUMPTION M3) of the fixture garages that have one (golden F04..F08), read from
#: this sidecar file (key ``max_stay_min``). The garage dataset has the column since spec v3 B (ruling R-B-2), but the fixture
#: geojson is a legacy layout without it and its LF SHA-256 is recorded in the ``sources`` of the fixture model, which the Java
#: tests pin byte for byte, so the geojson is left as it is and the exporter sets the column on the loaded frame, which
#: ``tariff_export.garage_row_to_tariff`` reads.
FIXTURE_GARAGE_MAX_STAY_JSON = FIXTURE_DIRECTORY / "parking_garages_fixture_max_stay_min.json"


def read_fixture_tariffs_csv(path: Path) -> pd.DataFrame:
    """The fixture tariff table read by the production loader (``#`` comment lines, identifiers as text)."""
    return zones.load_tariffs(path)


def _repository_path(path: Path) -> str:
    """POSIX path relative to the repository, so the recorded provenance is the same on every platform."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_fixture_tariffs(csv_path=None) -> tuple[pd.DataFrame, list[dict]]:
    """The fixture tariff table and its provenance record (``sources`` of the fixture model).

    ``csv_path`` defaults to ``FIXTURE_TARIFFS_CSV``; the file must exist (``FileNotFoundError`` otherwise).
    """
    path = Path(csv_path) if csv_path is not None else FIXTURE_TARIFFS_CSV
    if not path.is_file():
        raise FileNotFoundError(f"fixture tariff CSV not found: {path}")
    table = read_fixture_tariffs_csv(path)
    source = {"source_id": FIXTURE_SOURCE_ID, "path": _repository_path(path),
              "sha256": tariff_export.content_sha256(path)}
    log.info("fixture tariffs: %d zones from %s (sha256 %s)", len(table), source["path"], source["sha256"])
    return table, [source]


def load_fixture_districts(geojson_path=None) -> tuple:
    """The fixture resident districts and their provenance record (one more ``sources`` entry of the fixture model).

    ``geojson_path`` defaults to ``FIXTURE_DISTRICTS_GEOJSON``; the file must exist (``FileNotFoundError`` otherwise). It
    is read by the production loader, which rejects an invalid layer.
    """
    path = Path(geojson_path) if geojson_path is not None else FIXTURE_DISTRICTS_GEOJSON
    if not path.is_file():
        raise FileNotFoundError(f"fixture resident districts not found: {path}")
    districts = zones.load_resident_districts(path)
    source = {"source_id": FIXTURE_DISTRICTS_SOURCE_ID, "path": _repository_path(path),
              "sha256": tariff_export.content_sha256(path)}
    log.info("fixture resident districts: %d districts from %s (sha256 %s)", len(districts), source["path"],
             source["sha256"])
    return districts, source


def read_fixture_maximum_stays(path=None) -> dict:
    """The maximum stays of the fixture garages (garage id -> whole minutes >= 1) of the sidecar ``FIXTURE_GARAGE_MAX_STAY_JSON``.
    ``FileNotFoundError`` when the file is absent, ``ValueError`` for a value that is no whole number of minutes >= 1."""
    path = Path(path) if path is not None else FIXTURE_GARAGE_MAX_STAY_JSON
    if not path.is_file():
        raise FileNotFoundError(f"fixture maximum stays not found: {path}")
    stays = json.loads(path.read_text(encoding="utf-8"))["max_stay_min"]
    bad = {garage_id: value for garage_id, value in stays.items()
           if isinstance(value, bool) or not isinstance(value, int) or value < 1}
    if bad:
        raise ValueError(f"{path}: max_stay_min must be whole minutes >= 1, found {bad}")
    return stays


def with_fixture_maximum_stays(frame, stays: Mapping[str, int]):
    """The fixture garages with the dataset column ``max_stay_min`` (``Int64``, NA = no maximum) set from ``stays``.

    The fixture geojson is a legacy layout, so ``load_garages(allow_legacy_columns=True)`` gives the column empty. Raises
    ``ValueError`` when the frame already carries a maximum (the fixture file has gained the column: the sidecar is then obsolete
    and would silently overwrite it) or when an id of ``stays`` is not a garage of the frame (a renamed or removed fixture garage
    must not silently lose its maximum)."""
    if "max_stay_min" in frame.columns and frame["max_stay_min"].notna().any():
        raise ValueError("the fixture garages already carry max_stay_min values: drop the sidecar "
                         f"{FIXTURE_GARAGE_MAX_STAY_JSON.name} and keep the column of the fixture file")
    unknown = sorted(set(stays) - set(frame["garage_id"]))
    if unknown:
        raise ValueError(f"the fixture maximum stays name garages that the fixture file does not have: {unknown}")
    return frame.assign(max_stay_min=pd.array([stays.get(garage_id) for garage_id in frame["garage_id"]], dtype="Int64"))


def load_fixture_garages(geojson_path=None) -> tuple:
    """The fixture garages and their provenance record (one more ``sources`` entry of the fixture model).

    ``geojson_path`` defaults to ``FIXTURE_GARAGES_GEOJSON``; the file must exist (``FileNotFoundError`` otherwise). It is
    read by the production loader and validated by the production validator, which reject an invalid dataset. The fixture
    file is written in EPSG:25832 (legacy ``crs`` member) so that its distances are exact numbers.
    """
    path = Path(geojson_path) if geojson_path is not None else FIXTURE_GARAGES_GEOJSON
    if not path.is_file():
        raise FileNotFoundError(f"fixture garages not found: {path}")
    frame = parking_garages.load_garages(path, allow_legacy_columns=True)
    parking_garages.validate_garages(frame)
    source = {"source_id": FIXTURE_GARAGES_SOURCE_ID, "path": _repository_path(path),
              "sha256": tariff_export.content_sha256(path)}
    stays = read_fixture_maximum_stays()
    log.info("fixture garages: %d garages from %s (sha256 %s), %d with a maximum stay", len(frame), source["path"],
             source["sha256"], len(stays))
    return with_fixture_maximum_stays(frame, stays), source


def build_fixture_tariff_model(table: pd.DataFrame, sources: list[dict], districts_geojson_path=None,
                               garages_geojson_path=None) -> dict:
    """The spec 5.4 tariff model of the fixture set, built by the production export: the tariff table, the fixture
    resident districts (listed as ``resident_districts``) and the fixture garages (listed as ``garages``, with the fixture
    decays and the default maximum distance); their files are two more sources after ``sources``."""
    districts, districts_source = load_fixture_districts(districts_geojson_path)
    garages, garages_source = load_fixture_garages(garages_geojson_path)
    return tariff_export.build_tariff_model(table, snapshot_date=FIXTURE_SNAPSHOT_DATE,
                                            sources=[*sources, districts_source, garages_source],
                                            resident_districts=districts, garages=garages,
                                            garage_decay_m=FIXTURE_GARAGE_DECAY_M,
                                            garage_decay_commute_m=FIXTURE_GARAGE_DECAY_COMMUTE_M,
                                            garage_max_distance_m=cost.GARAGE_MAX_DISTANCE_M,
                                            option_price_beta_u_per_eur=FIXTURE_OPTION_PRICE_BETA_U_PER_EUR)


def zones_from_model(model: Mapping) -> dict[str, ZoneTariff]:
    """The model's zone entries as ``ZoneTariff`` objects keyed by zone id (inverse of ``zone_to_json``)."""
    return {zone_id: ZoneTariff(zone_id=zone_id, **fields) for zone_id, fields in model["zones"].items()}


def fixture_zone_tariffs() -> dict[str, ZoneTariff]:
    """The fixture tariffs as the Java side reads them: table -> tariff model -> ``ZoneTariff``."""
    table, sources = load_fixture_tariffs()
    return zones_from_model(build_fixture_tariff_model(table, sources))


def fixture_garage_tariffs() -> list[cost.GarageTariff]:
    """The fixture garages as the Java side reads them: dataset -> tariff model -> ``GarageTariff``."""
    table, sources = load_fixture_tariffs()
    return tariff_export.garages_from_model(build_fixture_tariff_model(table, sources))


def build_golden_document(model: Mapping) -> dict:
    """The golden-case fixture: schema version, the fixture tariffs and garages in cents, the maximum garage distance, the
    money coefficient of the fixture model (every case carries the coefficient it is priced with, 0.0 or this value), every
    golden case and every garage option case."""
    return {"schema_version": GOLDEN_SCHEMA_VERSION, "tariffs": dict(model["zones"]), "garages": list(model["garages"]),
            "garage_max_distance_m": model["garage_max_distance_m"],
            "option_price_beta_u_per_eur": model["option_price_beta_u_per_eur"],
            "cases": [dict(case) for case in GOLDEN_CASES],
            "garage_option_cases": [dict(case) for case in GOLDEN_GARAGE_OPTION_CASES]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tariffs-csv", type=Path, default=None,
                        help=f"fixture tariff CSV (default: {_repository_path(FIXTURE_TARIFFS_CSV)})")
    parser.add_argument("--districts-geojson", type=Path, default=None,
                        help=f"fixture resident districts (default: {_repository_path(FIXTURE_DISTRICTS_GEOJSON)})")
    parser.add_argument("--garages-geojson", type=Path, default=None,
                        help=f"fixture garages (default: {_repository_path(FIXTURE_GARAGES_GEOJSON)})")
    parser.add_argument("--output-directory", type=Path, default=FIXTURE_DIRECTORY,
                        help=f"directory for the two JSON files (default: {_repository_path(FIXTURE_DIRECTORY)})")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    table, sources = load_fixture_tariffs(args.tariffs_csv)
    model = build_fixture_tariff_model(table, sources, args.districts_geojson, args.garages_geojson)
    garages = tariff_export.garages_from_model(model)
    problems = golden_case_mismatches(zones_from_model(model), GOLDEN_CASES, garages, model["garage_max_distance_m"])
    option_problems = option_case_mismatches(garages)
    if problems or option_problems:
        for problem in problems + option_problems:
            log.error("golden case differs from the Python reference: %s", problem)
        log.error("%d of %d golden cases and %d of %d garage option cases differ; nothing was written", len(problems),
                  len(GOLDEN_CASES), len(option_problems), len(GOLDEN_GARAGE_OPTION_CASES))
        return 1
    log.info("all %d golden cases and %d garage option cases reproduce with braunschweig.parking.cost", len(GOLDEN_CASES),
             len(GOLDEN_GARAGE_OPTION_CASES))

    args.output_directory.mkdir(parents=True, exist_ok=True)
    for file_name, document in ((GOLDEN_CASES_FILE_NAME, build_golden_document(model)),
                                (FIXTURE_MODEL_FILE_NAME, model)):
        path = args.output_directory / file_name
        replaced = path.exists()
        tariff_export.write_json_document(path, document)
        log.info("wrote %s (%s)", path, "replaced the previous file" if replaced else "new file")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
