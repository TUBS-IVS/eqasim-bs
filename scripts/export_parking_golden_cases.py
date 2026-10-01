"""Regenerate the parking golden-case and fixture tariff-model JSON files shared with the Java tests (#249, #436).

The fixture tariff set is INSPIRED by the real tariff table but is a test set: it pins arithmetic, not
truth. The script

1. loads the fixture tariff table ``tests/fixtures/parking/parking_tariffs_fixture.csv`` with the production
   loader ``braunschweig.parking.zones.load_tariffs`` (the documented CSV: ``#`` comment lines, identifier
   columns kept as text), so the fixture passes through exactly the code path of the committed tariff table;
2. converts it with the production export (``braunschweig.parking.tariff_export.build_tariff_model``, schema 2)
   and evaluates every golden case of ``braunschweig.parking.golden_cases`` (``GOLDEN_CASES``: G01..G38,
   L01..L08, L01Z..L08Z, V01..V23, each priced under its own minimum stay ``minimum_stay_min``) with the Python
   reference ``braunschweig.parking.cost``; it writes nothing when a result differs from its hard-coded
   expectation;
3. writes two sorted-key LF JSON files into ``tests/fixtures/parking/``:
   ``parking_golden_cases.json`` (``schema_version`` ``GOLDEN_SCHEMA_VERSION``, the fixture ``tariffs`` in
   cents, the ``cases``; read by ``tests/test_parking_cost.py`` and the Java ``ParkingCostCalculatorTest``) and
   ``parking_tariffs_fixture.json`` (the spec 5.4 tariff model of the fixture set; ``sources`` holds the
   path and LF-normalised sha256 of the table it was built from).

Re-run it after changing the fixture table, the golden cases or the export; the sync tests in
``tests/test_parking_cost.py`` and ``tests/test_parking_tariff_export.py`` fail until the files are current.
The recorded source of the fixture model is the fixture CSV (path and LF-normalised sha256), so any edit of
that file needs a re-run too.

Usage:
    python scripts/export_parking_golden_cases.py [--tariffs-csv PATH] [--output-directory DIR]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Mapping

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.parking import tariff_export, zones  # noqa: E402
from braunschweig.parking.cost import ZoneTariff  # noqa: E402
from braunschweig.parking.golden_cases import GOLDEN_CASES, golden_case_mismatches  # noqa: E402

log = logging.getLogger("export_parking_golden_cases")

FIXTURE_DIRECTORY = REPO / "tests" / "fixtures" / "parking"
FIXTURE_TARIFFS_CSV = FIXTURE_DIRECTORY / "parking_tariffs_fixture.csv"
GOLDEN_CASES_FILE_NAME = "parking_golden_cases.json"
FIXTURE_MODEL_FILE_NAME = "parking_tariffs_fixture.json"
#: 2 since parking cost zones v2 (issue #436): every case carries ``minimum_stay_min`` and the ``tariffs`` are zone
#: entries of tariff schema 2. A reader of version 1 would price every case without its minimum stay.
GOLDEN_SCHEMA_VERSION = 2
#: Snapshot label of the fixture tariff model: the date the fixture set was defined. A test label, not a
#: real tariff state.
FIXTURE_SNAPSHOT_DATE = "2026-09-28"
FIXTURE_SOURCE_ID = "parking_tariffs_fixture"


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


def build_fixture_tariff_model(table: pd.DataFrame, sources: list[dict]) -> dict:
    """The spec 5.4 tariff model of the fixture set, built by the production export."""
    return tariff_export.build_tariff_model(table, snapshot_date=FIXTURE_SNAPSHOT_DATE, sources=sources)


def zones_from_model(model: Mapping) -> dict[str, ZoneTariff]:
    """The model's zone entries as ``ZoneTariff`` objects keyed by zone id (inverse of ``zone_to_json``)."""
    return {zone_id: ZoneTariff(zone_id=zone_id, **fields) for zone_id, fields in model["zones"].items()}


def fixture_zone_tariffs() -> dict[str, ZoneTariff]:
    """The fixture tariffs as the Java side reads them: table -> tariff model -> ``ZoneTariff``."""
    table, sources = load_fixture_tariffs()
    return zones_from_model(build_fixture_tariff_model(table, sources))


def build_golden_document(model: Mapping) -> dict:
    """The golden-case fixture: schema version, the fixture tariffs in cents and every golden case."""
    return {"schema_version": GOLDEN_SCHEMA_VERSION, "tariffs": dict(model["zones"]),
            "cases": [dict(case) for case in GOLDEN_CASES]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tariffs-csv", type=Path, default=None,
                        help=f"fixture tariff CSV (default: {_repository_path(FIXTURE_TARIFFS_CSV)})")
    parser.add_argument("--output-directory", type=Path, default=FIXTURE_DIRECTORY,
                        help=f"directory for the two JSON files (default: {_repository_path(FIXTURE_DIRECTORY)})")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    table, sources = load_fixture_tariffs(args.tariffs_csv)
    model = build_fixture_tariff_model(table, sources)
    problems = golden_case_mismatches(zones_from_model(model))
    if problems:
        for problem in problems:
            log.error("golden case differs from the Python reference: %s", problem)
        log.error("%d of %d golden cases differ; nothing was written", len(problems), len(GOLDEN_CASES))
        return 1
    log.info("all %d golden cases reproduce with braunschweig.parking.cost", len(GOLDEN_CASES))

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
