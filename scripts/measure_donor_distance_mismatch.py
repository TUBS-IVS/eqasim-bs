"""Measure the donor-vs-assigned commute-distance-class mismatch (run on the run server).

Phase A measurement for the commute-day-state model (spec 2026-09-04, issue #244). The MiD
reporting-day workday-location table (``mid2023_workday_location_by_commute_distance.csv``) is
indexed by the MiD respondent's OWN commute distance, while the model would apply it to the
synthetic worker's ASSIGNED (synthesised) commute distance. This script measures how often the
two disagree on the finished 100% population, i.e. how large the population is for which a state
copied from the donor's reporting day would come from the wrong row of that table.

TWO donor distance sources are measured side by side, because the spec foresees both:

1. ``P_ARB_ENTF`` (distance to the usual workplace). It is a question of the MiD HOME-OFFICE
   MODULE, so for a donor outside that module it is missing by construction -- the script
   measures the validity rate SPLIT BY the module flag rather than assuming one.
2. the donor's FIRST valid work-trip length (``wegkm`` of a ``W_ZWECK == 1`` trip). The spec
   treats this as the fallback for a missing ``P_ARB_ENTF``; measurement 1 is what tells us
   whether it is a fallback or in fact the main path.

It joins four sources and never writes a single per-person row or MiD identifier:

    <run>_persons.csv (person_id, hts_id; ';'-separated eqasim output)
      -> assigned_class_by_person.csv (the analysis stage's per-worker assigned class)
      -> work_dir/pseudonym_map.csv (source_person_id -> H_ID, P_ID; local-only, never committed)
      -> MiD2023_Personen.csv (H_ID, P_ID -> P_ARB_ENTF, P_STARB1, starb2, M_HOFF, arbwo, ST_WOTAG)
      -> MiD2023_Wege.csv     (H_ID, P_ID -> W_ZWECK, wegkm)

Outputs (aggregates only, see ``braunschweig.calibration.commute_day_state_reference.
donor_vs_assigned_class`` and ``donor_universe_diagnostics``):

    donor_vs_assigned_class.csv              cross-tab donor class (P_ARB_ENTF) x assigned class
    donor_vs_assigned_class_trip_length.csv  the same cross-tab with the donor's work-trip length
                                             as the donor distance
    donor_vs_assigned_diagnostics.json       join rates, mismatch counts, the donor universe
                                             (home-office module and reporting-day codes) and the
                                             field definitions of every emitted number

Every join rate is logged; a rate below ``--min-join-rate`` (default 0.99) FAILS the script
rather than degrading silently (CLAUDE.md "Fallback transparency"): a broken pseudonym or MiD
join would otherwise produce a small, plausible-looking cross-tab that is not the measurement it
claims to be. The rate of workers that actually reached a donor row is enforced too, so donors
lost in the MiD merge cannot silently shrink the measured cohort.

Usage (felix, conda env eqasim, from the repository/worktree root):
    python scripts/measure_donor_distance_mismatch.py \
        --persons-csv eqasim-data/output_bs_100pct_i329/braunschweig_100pct_i329_persons.csv \
        --assigned-class-csv eqasim-data/output_bs_100pct_i329/analysis/commute_day_state_phase_a/assigned_class_by_person.csv \
        --pseudonym-map eqasim-data/popsim_work_i329/pseudonym_map.csv \
        --mid-raw /home/felix/eqasim-bs/eqasim-data/data/braunschweig/popsim/mid2023_raw \
        --out-dir eqasim-data/output_bs_100pct_i329/analysis/commute_day_state_phase_a \
        --source-commit <short sha of this checkout>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.calibration import commute_day_state_reference as R  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("measure_donor_distance_mismatch")

PERSONS_COLUMNS = ["person_id", "hts_id"]
ASSIGNED_COLUMNS = ["person_id", "assigned_distance_class"]
PSEUDONYM_COLUMNS = ["source_person_id", "H_ID", "P_ID"]
MID_PERSON_COLUMNS = ["H_ID", "P_ID", "P_ARB_ENTF", "P_STARB1", "starb2", "M_HOFF", "arbwo",
                      "ST_WOTAG"]
MID_TRIP_COLUMNS = ["H_ID", "P_ID", "W_ZWECK", "wegkm"]

MID_PERSON_FILE = "MiD2023_Personen.csv"
MID_TRIP_FILE = "MiD2023_Wege.csv"
CROSS_TAB_FILE = "donor_vs_assigned_class.csv"
TRIP_LENGTH_CROSS_TAB_FILE = "donor_vs_assigned_class_trip_length.csv"
DIAGNOSTICS_FILE = "donor_vs_assigned_diagnostics.json"

DEFAULT_MIN_JOIN_RATE = 0.99

#: Column names that must NEVER reach an output file: raw MiD identifiers and the surrogate
#: donor id, which together would re-identify a MiD respondent from a committed aggregate.
FORBIDDEN_OUTPUT_COLUMNS = ("H_ID", "P_ID", "HP_ID", "hts_id", "person_id", "source_person_id",
                            "source_household_id")

#: Definition of every non-obvious number the script emits, written into the JSON so a reader
#: never has to reconstruct a definition from the code.
FIELD_DEFINITIONS = {
    "n_workers": "synthetic workers with an assigned commute-distance class (rows of "
                 "assigned_class_by_person.csv that matched a person in the population)",
    "n_matched_donor": "workers whose hts_id reached a donor row (pseudonym map AND MiD person "
                       "file); the cross-tabs are built over these",
    "n_comparable": "matched workers for which BOTH the donor class and the assigned class are "
                    "known; share_assigned_gt_donor is a share OF THIS SUBSET",
    "n_donor_distance_missing": "matched workers whose donor distance is missing for the source "
                                "of that table (P_ARB_ENTF code, or no valid work trip)",
    "share_donor_distance_missing": "n_donor_distance_missing / n_matched_donor -- the "
                                    "non-comparable share; above warn_missing_share the helper "
                                    "logs a warning because the comparable subset is not "
                                    "necessarily representative",
    "n_assigned_gt_donor": "matched, comparable workers whose ASSIGNED class is strictly higher "
                           "than the donor's, in COMMUTE_CLASS_LABELS order",
    "share_assigned_gt_donor": "n_assigned_gt_donor / n_comparable",
    "n_donor_worked_on_day": "matched workers whose donor has P_STARB1 == 1 (worked on the "
                             "reporting day); the residual to n_matched_donor beyond "
                             "n_donor_did_not_work_on_day are donors with a P_STARB1 code "
                             "outside (1, 2), i.e. not asked or no answer",
    "donor_universe.n_in_home_office_module": "matched workers whose donor has M_HOFF == 1 "
                                              "(asked the home-office module, the only donors "
                                              "for whom P_ARB_ENTF can be valid)",
    "donor_universe.share_distance_valid_in_module": "P_ARB_ENTF validity rate among the "
                                                     "in-module donors of these workers",
    "donor_universe.n_by_reporting_day_weekday": "worker counts by the donor's raw MiD arbwo "
                                                 "code (1 = reporting day is a weekday)",
    "donor_universe.n_by_reporting_day_of_week": "worker counts by the donor's raw MiD ST_WOTAG "
                                                 "code; no mapping from code to named weekday is "
                                                 "asserted here",
    "trip_length": "the same diagnostics computed with the donor's FIRST valid work-trip length "
                   "(wegkm of a W_ZWECK == 1 trip, 0 < wegkm < 1000) as the donor distance -- "
                   "the spec's fallback source for a missing P_ARB_ENTF",
}


def _read_csv(path: Path, required_columns: list[str], sep: str = ",") -> pd.DataFrame:
    """Read ``path`` restricted to ``required_columns``, failing early on a missing file/column."""
    if not path.exists():
        raise FileNotFoundError(f"Input file missing: {path}")
    header_only = pd.read_csv(path, sep=sep, nrows=0)
    missing = [column for column in required_columns if column not in header_only.columns]
    if missing:
        raise ValueError(f"Required column(s) missing from {path}: {missing}. Available columns: "
                         f"{list(header_only.columns)}")
    frame = pd.read_csv(path, sep=sep, usecols=required_columns, low_memory=False)
    logger.info("read %s (%d rows, columns %s)", path, len(frame), required_columns)
    return frame


def _normalise_id(series: pd.Series) -> pd.Series:
    """Normalise an id column so the same id joins across files written by different writers.

    eqasim writes ``person_id``/``hts_id`` as integers, the popsim pseudonym map and the MiD
    microdata write theirs as integers too, but a CSV round-trip can turn either into a float
    ("1234.0") or a string. Values that are numeric throughout become a nullable integer;
    anything else falls back to a stripped string, so a genuinely non-numeric id still joins.
    """
    numeric = pd.to_numeric(series, errors="coerce")
    if numeric.notna().all():
        return numeric.astype("int64")
    return series.astype(str).str.strip()


def _enforce_join_rate(n_matched: int, n_total: int, description: str, min_join_rate: float) -> float:
    rate = (float(n_matched) / n_total) if n_total else 0.0
    logger.info("join rate %s: %d/%d matched (%.4f%%)", description, n_matched, n_total, 100.0 * rate)
    if rate < min_join_rate:
        raise ValueError(
            f"Join rate too low for {description}: {n_matched}/{n_total} = {100.0 * rate:.2f}% "
            f"< the required {100.0 * min_join_rate:.2f}%. A broken join (wrong key column, a "
            f"stale pseudonym map, a different MiD vintage) must fail this measurement rather "
            f"than shrink it silently; check that the pseudonym map belongs to the run that "
            f"produced the population and that the MiD raw files are the ones it was built from.")
    return rate


def _assert_no_identifiers(frame: pd.DataFrame, what: str) -> None:
    """Fail if an output frame carries a person/household identifier (privacy contract).

    The pseudonym map and the raw MiD microdata are restricted, server-only artifacts; the
    outputs of this script are committed to the repository, so they must be aggregates only.
    """
    present = [column for column in frame.columns if column in FORBIDDEN_OUTPUT_COLUMNS]
    if present:
        raise ValueError(f"{what} carries identifier column(s) {present}; outputs of this script "
                         "are committed aggregates and must never contain person, household or "
                         "MiD identifiers")


def _assert_no_identifier_keys(mapping: dict, what: str, path: str = "") -> None:
    """Same privacy contract as :func:`_assert_no_identifiers`, applied to the JSON keys.

    Walks nested dictionaries: a code histogram or a per-class breakdown could otherwise smuggle
    an identifier in as a key without ever appearing as a column.
    """
    for key, value in mapping.items():
        full_key = f"{path}.{key}" if path else str(key)
        if str(key) in FORBIDDEN_OUTPUT_COLUMNS:
            raise ValueError(f"{what} carries the identifier key '{full_key}'; outputs of this "
                             "script are committed aggregates and must never contain person, "
                             "household or MiD identifiers")
        if isinstance(value, dict):
            _assert_no_identifier_keys(value, what, full_key)


def _shared_header_lines(args, diagnostics: dict) -> list[str]:
    """Provenance lines true for BOTH cross-tabs (per-table lines are added by the caller)."""
    return [
        f"# Generated by scripts/measure_donor_distance_mismatch.py on {dt.date.today().isoformat()}.",
        f"# Code state: eqasim-bs {args.source_commit}, helpers donor_vs_assigned_class /",
        "#   donor_universe_diagnostics / first_work_trip_length_km in",
        "#   braunschweig/calibration/commute_day_state_reference.py.",
        f"# Population: {args.persons_csv}",
        f"# Assigned classes: {args.assigned_class_csv} (analysis stage",
        "#   braunschweig.analysis.synthesis.work_participation_by_kreis).",
        "# Donor link: work_dir pseudonym_map.csv (source_person_id -> H_ID, P_ID; server-only,",
        f"#   never committed) -> {MID_PERSON_FILE} / {MID_TRIP_FILE}.",
        "# Classes (km): lt10 [0,10), 10_25, 25_50, 50_100, 100_200 (incl. the 200 top-code), gt200;",
        "#   'missing' = no usable donor distance (rows) or no assigned class (columns).",
        "# Columns: donor_distance_class, n_donor_total (workers with that donor class), one",
        "#   n_<assigned class> count per assigned class, and the matching share_<assigned class>",
        "#   row shares. Row 'all' totals over donor classes. Counts are unweighted SAMPLE counts",
        "#   of synthetic workers at the run's sampling rate, never expanded.",
        f"# Join rates: assigned->persons {100.0 * diagnostics['join_rate_assigned_to_persons']:.4f}%,",
        f"#   hts_id->pseudonym_map {100.0 * diagnostics['join_rate_hts_to_pseudonym_map']:.4f}%,",
        f"#   pseudonym_map->MiD {100.0 * diagnostics['join_rate_pseudonym_map_to_mid']:.4f}%,",
        f"#   workers->donor rows {100.0 * diagnostics['join_rate_workers_to_donor_rows']:.4f}%",
        f"#   (required minimum {100.0 * diagnostics['min_join_rate']:.2f}%).",
        "# Purpose: Phase A measurement of the population whose assigned distance class differs from",
        "#   its donor's (spec 2026-09-04, issue #244). Measurement only -- decides nothing, and is",
        "#   NOT a validation against observed behaviour.",
        "# Contains aggregates only: no person, household or MiD identifier.",
    ]


def _distance_source_lines(table: str, source: str, section: dict) -> list[str]:
    """Table-specific header lines: which donor distance the cross-tab was built from."""
    if source == "arb_entf":
        definition = [
            "# Donor distance: P_ARB_ENTF (distance to the usual workplace) cleaned by",
            "#   clean_mid_commute_distance_km -- codes 996, 999 and every value >= 1000 become",
            "#   missing; 200.0 is a legitimate top-code, classed 100_200. P_ARB_ENTF is a question",
            "#   of the MiD HOME-OFFICE MODULE, so it is missing by construction for donors with",
            "#   M_HOFF != 1; see donor_universe in the diagnostics JSON for the validity rate split",
            "#   by that flag.",
        ]
    else:
        definition = [
            "# Donor distance: the donor's FIRST valid work-trip length on the reporting day",
            f"#   ({MID_TRIP_FILE}: wegkm of a W_ZWECK == 1 trip with 0 < wegkm < 1000, file order).",
            "#   This is the source the spec foresees when P_ARB_ENTF is missing; it is reported",
            "#   beside the P_ARB_ENTF table, not merged with it. A donor who made no work trip on",
            "#   the reporting day has no length here, which is a property of the DAY, not of the",
            "#   respondent's commute.",
        ]
    return [f"# Table: {table}"] + definition + [
        f"# Coverage: {section['n_matched_donor']} matched workers, of which "
        f"{section['n_donor_distance_missing']} "
        f"({100.0 * section['share_donor_distance_missing']:.2f}%) have no donor distance from this",
        f"#   source; {section['n_comparable']} are comparable and "
        f"{section['n_assigned_gt_donor']} of them "
        f"({100.0 * section['share_assigned_gt_donor']:.2f}%) have an assigned class ABOVE the donor's.",
    ]


def _write_cross_tab(cross_tab: pd.DataFrame, path: Path, header_lines: list[str]) -> None:
    _assert_no_identifiers(cross_tab, path.name)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        for line in header_lines:
            handle.write(line + "\n")
        cross_tab.to_csv(handle, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s (%d rows)", path, len(cross_tab))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--persons-csv", required=True,
                        help="the run's <prefix>_persons.csv (';'-separated; person_id, hts_id)")
    parser.add_argument("--assigned-class-csv", required=True,
                        help="assigned_class_by_person.csv written by the analysis stage")
    parser.add_argument("--pseudonym-map", required=True,
                        help="work_dir/pseudonym_map.csv of the run that produced the population")
    parser.add_argument("--mid-raw", required=True,
                        help=f"directory containing the raw {MID_PERSON_FILE} and {MID_TRIP_FILE}")
    parser.add_argument("--out-dir", required=True, help="directory the aggregates are written to")
    parser.add_argument("--source-commit", required=True,
                        help="short git SHA of the eqasim-bs checkout this run's code state "
                             "corresponds to; recorded verbatim in the output header")
    parser.add_argument("--persons-sep", default=";",
                        help="field separator of --persons-csv (default ';', the eqasim output "
                             "convention)")
    parser.add_argument("--min-join-rate", type=float, default=DEFAULT_MIN_JOIN_RATE,
                        help=f"minimum acceptable match rate of every join (default "
                             f"{DEFAULT_MIN_JOIN_RATE}); below it the script fails")
    parser.add_argument("--warn-missing-share", type=float, default=R.DEFAULT_WARN_MISSING_SHARE,
                        help=f"share of matched workers without a usable donor distance above "
                             f"which a warning is logged (default {R.DEFAULT_WARN_MISSING_SHARE})")
    args = parser.parse_args(argv)

    persons = _read_csv(Path(args.persons_csv), PERSONS_COLUMNS, sep=args.persons_sep)
    assigned = _read_csv(Path(args.assigned_class_csv), ASSIGNED_COLUMNS)
    pseudonym_map = _read_csv(Path(args.pseudonym_map), PSEUDONYM_COLUMNS)
    mid_persons = _read_csv(Path(args.mid_raw) / MID_PERSON_FILE, MID_PERSON_COLUMNS)
    mid_trips = _read_csv(Path(args.mid_raw) / MID_TRIP_FILE, MID_TRIP_COLUMNS)

    persons["person_id"] = _normalise_id(persons["person_id"])
    persons["hts_id"] = _normalise_id(persons["hts_id"])
    assigned["person_id"] = _normalise_id(assigned["person_id"])
    pseudonym_map["source_person_id"] = _normalise_id(pseudonym_map["source_person_id"])
    for frame in (pseudonym_map, mid_persons, mid_trips):
        frame["H_ID"] = _normalise_id(frame["H_ID"])
        frame["P_ID"] = _normalise_id(frame["P_ID"])

    if persons["person_id"].duplicated().any():
        raise ValueError(f"{args.persons_csv} has duplicate person_id values; it must be one row "
                         "per synthetic person")
    if pseudonym_map["source_person_id"].duplicated().any():
        n_duplicated = int(pseudonym_map["source_person_id"].duplicated().sum())
        raise ValueError(f"{args.pseudonym_map} has {n_duplicated} duplicate source_person_id "
                         "value(s); the map must be one row per donor person")

    # 1. assigned classes -> the population, to attach each worker's donor id (hts_id).
    workers = assigned.merge(persons, on="person_id", how="left")
    n_with_hts = int(workers["hts_id"].notna().sum())
    rate_assigned_to_persons = _enforce_join_rate(
        n_with_hts, len(workers), "assigned_class_by_person.csv -> persons.csv (person_id)",
        args.min_join_rate)
    workers = workers[workers["hts_id"].notna()].copy()
    # The left merge above can widen an integer hts_id to float when some rows did not match;
    # re-normalise so the donor join below compares like with like.
    workers["hts_id"] = _normalise_id(workers["hts_id"])

    # 2. donor id -> the pseudonym map, to reach the raw MiD (H_ID, P_ID). ``validate`` fails
    # loudly if the MiD person file is not unique on (H_ID, P_ID), which would multiply donors.
    linked = pseudonym_map.merge(mid_persons, on=["H_ID", "P_ID"], how="left",
                                 validate="many_to_one", indicator=True)
    n_mid_matched = int((linked["_merge"] == "both").sum())
    rate_pseudonym_to_mid = _enforce_join_rate(
        n_mid_matched, len(linked), f"pseudonym_map.csv -> {MID_PERSON_FILE} (H_ID, P_ID)",
        args.min_join_rate)
    linked = linked[linked["_merge"] == "both"].copy()

    # 3. the donor's first valid work-trip length, the spec's second distance source.
    trip_lengths = R.first_work_trip_length_km(mid_trips)
    linked = linked.merge(trip_lengths, on=["H_ID", "P_ID"], how="left", validate="many_to_one")

    donor_frame = pd.DataFrame({
        "hts_id": linked["source_person_id"].to_numpy(),
        "donor_distance_km": R.clean_mid_commute_distance_km(linked["P_ARB_ENTF"]).to_numpy(),
        "donor_trip_length_km": linked[R.WORK_TRIP_LENGTH_COLUMN].to_numpy(),
        "donor_worked_on_day": linked["P_STARB1"].to_numpy(),
        "donor_starb2": linked["starb2"].to_numpy(),
        "donor_in_home_office_module": linked["M_HOFF"].to_numpy(),
        "donor_reporting_day_weekday": linked["arbwo"].to_numpy(),
        "donor_reporting_day_of_week": linked["ST_WOTAG"].to_numpy(),
    })

    n_workers_in_map = int(workers["hts_id"].isin(set(pseudonym_map["source_person_id"])).sum())
    rate_hts_to_map = _enforce_join_rate(
        n_workers_in_map, len(workers), "workers (hts_id) -> pseudonym_map.source_person_id",
        args.min_join_rate)

    worker_columns = ["person_id", "hts_id", "assigned_distance_class"]
    cross_tab, diagnostics = R.donor_vs_assigned_class(workers[worker_columns], donor_frame,
                                                       warn_missing_share=args.warn_missing_share)
    # Guard the composed chain, not only its individual links: a worker whose donor was dropped
    # anywhere upstream (pseudonym map, MiD merge) silently leaves the cross-tab, which would
    # shrink the measured cohort without any single join rate falling below the floor.
    rate_workers_to_donors = _enforce_join_rate(
        diagnostics["n_matched_donor"], diagnostics["n_workers"],
        "workers -> donor rows (composed chain)", args.min_join_rate)

    trip_donor_frame = donor_frame.drop(columns=["donor_distance_km"]).rename(
        columns={"donor_trip_length_km": "donor_distance_km"})
    trip_cross_tab, trip_diagnostics = R.donor_vs_assigned_class(
        workers[worker_columns], trip_donor_frame, warn_missing_share=args.warn_missing_share)

    worker_donors = workers[worker_columns].merge(donor_frame, on="hts_id", how="inner",
                                                  validate="many_to_one")
    universe = R.donor_universe_diagnostics(worker_donors)

    diagnostics = dict(diagnostics)
    diagnostics.update({
        "min_join_rate": float(args.min_join_rate),
        "join_rate_assigned_to_persons": rate_assigned_to_persons,
        "join_rate_hts_to_pseudonym_map": rate_hts_to_map,
        "join_rate_pseudonym_map_to_mid": rate_pseudonym_to_mid,
        "join_rate_workers_to_donor_rows": rate_workers_to_donors,
        "n_pseudonym_map_rows": int(len(pseudonym_map)),
        "n_donor_rows": int(len(donor_frame)),
        "donor_distance_source": "P_ARB_ENTF",
        "donor_universe": universe,
        "trip_length": trip_diagnostics,
        "field_definitions": FIELD_DEFINITIONS,
        "source_commit": args.source_commit,
        "persons_csv": str(args.persons_csv),
        "assigned_class_csv": str(args.assigned_class_csv),
        "generated_on": dt.date.today().isoformat(),
    })
    diagnostics["trip_length"]["donor_distance_source"] = (
        f"first valid work-trip length ({MID_TRIP_FILE}: W_ZWECK == 1, 0 < wegkm < "
        f"{R.MID_TRIP_LENGTH_MAX_KM:.0f})")
    _assert_no_identifier_keys(diagnostics, DIAGNOSTICS_FILE)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    shared = _shared_header_lines(args, diagnostics)
    _write_cross_tab(cross_tab, out_dir / CROSS_TAB_FILE,
                     _distance_source_lines(CROSS_TAB_FILE, "arb_entf", diagnostics) + shared)
    _write_cross_tab(trip_cross_tab, out_dir / TRIP_LENGTH_CROSS_TAB_FILE,
                     _distance_source_lines(TRIP_LENGTH_CROSS_TAB_FILE, "trip_length",
                                            diagnostics["trip_length"]) + shared)

    diagnostics_path = out_dir / DIAGNOSTICS_FILE
    with open(diagnostics_path, "w", encoding="utf-8") as handle:
        json.dump(diagnostics, handle, indent=2, sort_keys=True)
        handle.write("\n")
    logger.info("wrote %s", diagnostics_path)
    logger.info("HEADLINE P_ARB_ENTF: %d/%d comparable workers have an assigned class above their "
                "donor's (%.2f%%); no donor distance for %d of %d matched workers (%.2f%%)",
                diagnostics["n_assigned_gt_donor"], diagnostics["n_comparable"],
                100.0 * diagnostics["share_assigned_gt_donor"],
                diagnostics["n_donor_distance_missing"], diagnostics["n_matched_donor"],
                100.0 * diagnostics["share_donor_distance_missing"])
    logger.info("HEADLINE work-trip length: %d/%d comparable workers have an assigned class above "
                "their donor's (%.2f%%); no work trip for %d of %d matched workers (%.2f%%)",
                trip_diagnostics["n_assigned_gt_donor"], trip_diagnostics["n_comparable"],
                100.0 * trip_diagnostics["share_assigned_gt_donor"],
                trip_diagnostics["n_donor_distance_missing"], trip_diagnostics["n_matched_donor"],
                100.0 * trip_diagnostics["share_donor_distance_missing"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
