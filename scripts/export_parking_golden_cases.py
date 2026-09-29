"""Regenerate the parking golden-case and fixture tariff-model JSON files shared with the Java tests (#249).

The fixture tariff set is INSPIRED by the real tariff table but is a test set: it pins arithmetic, not
truth. The script

1. loads the fixture tariff table: ``tests/fixtures/parking/parking_tariffs_fixture.csv`` when that file
   exists, otherwise the inline copy ``FIXTURE_TARIFF_ROWS`` below. The inline copy is TEMPORARY: it stands
   in until the CSV of the zone-table task is merged, must be equal to it, and is removed afterwards;
2. converts it with the production export (``braunschweig.parking.tariff_export.build_tariff_model``) and
   evaluates the 26 golden cases of ``braunschweig.parking.golden_cases`` with the Python reference
   ``braunschweig.parking.cost``; it writes nothing when a result differs from its hard-coded expectation;
3. writes two sorted-key LF JSON files into ``tests/fixtures/parking/``:
   ``parking_golden_cases.json`` (``schema_version``, the fixture ``tariffs`` in cents, the ``cases``; read by
   ``tests/test_parking_cost.py`` and the Java ``ParkingCostCalculatorTest``) and
   ``parking_tariffs_fixture.json`` (the spec 5.4 tariff model of the fixture set; ``sources`` holds the
   path and LF-normalised sha256 of the table it was built from).

Re-run it after changing the fixture table, the golden cases or the export; the sync tests in
``tests/test_parking_cost.py`` and ``tests/test_parking_tariff_export.py`` fail until the files are current.
While the inline copy is in use the recorded source is this script, so ANY edit of it changes the recorded
sha256 and needs a re-run too.

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

from braunschweig.parking import tariff_export  # noqa: E402
from braunschweig.parking.cost import ZoneTariff  # noqa: E402
from braunschweig.parking.golden_cases import GOLDEN_CASES, golden_case_mismatches  # noqa: E402

log = logging.getLogger("export_parking_golden_cases")

FIXTURE_DIRECTORY = REPO / "tests" / "fixtures" / "parking"
FIXTURE_TARIFFS_CSV = FIXTURE_DIRECTORY / "parking_tariffs_fixture.csv"
GOLDEN_CASES_FILE_NAME = "parking_golden_cases.json"
FIXTURE_MODEL_FILE_NAME = "parking_tariffs_fixture.json"
GOLDEN_SCHEMA_VERSION = 1
#: Snapshot label of the fixture tariff model: the date the fixture set was defined. A test label, not a
#: real tariff state.
FIXTURE_SNAPSHOT_DATE = "2026-09-28"
FIXTURE_SOURCE_ID = "parking_tariffs_fixture"
# Identifier columns stay text when the CSV is read: workplace_class 03102 must keep its leading zero.
_TEXT_COLUMNS = {"zone_id": str, "zone_type": str, "workplace_class": str, "municipality_ags": str}

#: The fixture tariff set (plan section "Fixture tariff set and golden cases") with the spec 5.3 column
#: names of the plan's table, euros as decimals, fee hours as decimal hours, None for an empty cell.
#: TEMPORARY inline copy of FIXTURE_TARIFFS_CSV, used only while that file is absent (see the docstring).
FIXTURE_TARIFF_ROWS = [
    {"zone_id": "fx_bs_ia", "zone_type": "street_paid", "workplace_class": "bs_zentrum",
     "hourly_rate_eur": 1.80, "billing_unit_min": 1, "free_if_stay_at_most_min": None, "first_period_min": None,
     "first_period_eur": None, "daily_cap_eur": None, "max_stay_min": 180, "long_stay_product_eur": 9.00,
     "member_day_eur": None, "guest_day_eur": None, "fee_start_h": 9.0, "fee_end_h": 20.0,
     "resident_exempt": False},
    {"zone_id": "fx_bs_ib", "zone_type": "street_paid", "workplace_class": "bs_zentrum",
     "hourly_rate_eur": 1.80, "billing_unit_min": 1, "free_if_stay_at_most_min": None, "first_period_min": None,
     "first_period_eur": None, "daily_cap_eur": 9.00, "max_stay_min": None, "long_stay_product_eur": None,
     "member_day_eur": None, "guest_day_eur": None, "fee_start_h": 9.0, "fee_end_h": 20.0,
     "resident_exempt": False},
    {"zone_id": "fx_sz", "zone_type": "street_paid", "workplace_class": "03102",
     "hourly_rate_eur": 1.00, "billing_unit_min": 6, "free_if_stay_at_most_min": 30, "first_period_min": 60,
     "first_period_eur": 0.70, "daily_cap_eur": None, "max_stay_min": None, "long_stay_product_eur": None,
     "member_day_eur": None, "guest_day_eur": None, "fee_start_h": 10.0, "fee_end_h": 18.0,
     "resident_exempt": False},
    {"zone_id": "fx_wob", "zone_type": "street_paid", "workplace_class": "03103",
     "hourly_rate_eur": 1.20, "billing_unit_min": 60, "free_if_stay_at_most_min": None, "first_period_min": 60,
     "first_period_eur": 1.10, "daily_cap_eur": 6.00, "max_stay_min": None, "long_stay_product_eur": None,
     "member_day_eur": None, "guest_day_eur": None, "fee_start_h": 6.0, "fee_end_h": 24.0,
     "resident_exempt": False},
    {"zone_id": "fx_pe", "zone_type": "street_paid", "workplace_class": "03157",
     "hourly_rate_eur": 1.00, "billing_unit_min": 30, "free_if_stay_at_most_min": None, "first_period_min": None,
     "first_period_eur": None, "daily_cap_eur": None, "max_stay_min": 180, "long_stay_product_eur": 5.00,
     "member_day_eur": None, "guest_day_eur": None, "fee_start_h": 9.0, "fee_end_h": 17.0,
     "resident_exempt": False},
    {"zone_id": "fx_res_a", "zone_type": "resident_zone", "workplace_class": "bs_innenbereich",
     "hourly_rate_eur": 0.00, "billing_unit_min": 60, "free_if_stay_at_most_min": None, "first_period_min": None,
     "first_period_eur": None, "daily_cap_eur": None, "max_stay_min": 120, "long_stay_product_eur": 9.00,
     "member_day_eur": None, "guest_day_eur": None, "fee_start_h": 0.0, "fee_end_h": 24.0,
     "resident_exempt": True},
    {"zone_id": "fx_campus", "zone_type": "campus", "workplace_class": "bs_innenbereich",
     "hourly_rate_eur": None, "billing_unit_min": None, "free_if_stay_at_most_min": None, "first_period_min": None,
     "first_period_eur": None, "daily_cap_eur": None, "max_stay_min": None, "long_stay_product_eur": None,
     "member_day_eur": 3.50, "guest_day_eur": 9.00, "fee_start_h": 0.0, "fee_end_h": 24.0,
     "resident_exempt": False},
]


def inline_fixture_table() -> pd.DataFrame:
    """The inline copy as a DataFrame, shaped as a CSV read delivers it (None becomes NaN in numeric columns)."""
    return pd.DataFrame(FIXTURE_TARIFF_ROWS, columns=list(FIXTURE_TARIFF_ROWS[0]))


def read_fixture_tariffs_csv(path: Path) -> pd.DataFrame:
    """The fixture tariff CSV with its identifier columns kept as text."""
    return pd.read_csv(path, dtype=_TEXT_COLUMNS)


def _repository_path(path: Path) -> str:
    """POSIX path relative to the repository, so the recorded provenance is the same on every platform."""
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(REPO).as_posix()
    except ValueError:
        return resolved.as_posix()


def load_fixture_tariffs(csv_path=None) -> tuple[pd.DataFrame, list[dict]]:
    """The fixture tariff table and its provenance record (``sources`` of the fixture model).

    Without ``csv_path`` the default fixture CSV is read when it exists; otherwise the inline copy is used
    and a warning says so, and the provenance then names this script, the inline copy's home. An explicit
    ``csv_path`` must exist.
    """
    if csv_path is not None:
        path = Path(csv_path)
        if not path.is_file():
            raise FileNotFoundError(f"fixture tariff CSV not found: {path}")
    elif FIXTURE_TARIFFS_CSV.is_file():
        path = FIXTURE_TARIFFS_CSV
    else:
        path = None
    if path is None:
        log.warning("fixture tariff CSV %s is absent: using the inline copy FIXTURE_TARIFF_ROWS of %s "
                    "(temporary until the zone-table task's CSV is merged)",
                    _repository_path(FIXTURE_TARIFFS_CSV), _repository_path(Path(__file__)))
        table, source_path = inline_fixture_table(), Path(__file__)
    else:
        table, source_path = read_fixture_tariffs_csv(path), path
    source = {"source_id": FIXTURE_SOURCE_ID, "path": _repository_path(source_path),
              "sha256": tariff_export.content_sha256(source_path)}
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
    """The golden-case fixture: schema version, the fixture tariffs in cents and the 26 cases."""
    return {"schema_version": GOLDEN_SCHEMA_VERSION, "tariffs": dict(model["zones"]),
            "cases": [dict(case) for case in GOLDEN_CASES]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tariffs-csv", type=Path, default=None,
                        help=f"fixture tariff CSV (default: {_repository_path(FIXTURE_TARIFFS_CSV)} when it exists, "
                             "else the inline copy)")
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
