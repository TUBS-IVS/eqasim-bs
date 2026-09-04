"""Measure the donor-vs-assigned commute-distance-class mismatch (run on the run server).

Phase A measurement for the commute-day-state model (spec 2026-09-04, issue #244). The MiD
reporting-day workday-location table (``mid2023_workday_location_by_commute_distance.csv``) is
indexed by the MiD respondent's OWN commute distance, while the model would apply it to the
synthetic worker's ASSIGNED (synthesised) commute distance. This script measures how often the
two disagree on the finished 100% population, i.e. how large the population is for which a state
copied from the donor's reporting day would come from the wrong row of that table.

It joins three sources and never writes a single per-person row or MiD identifier:

    <run>_persons.csv (person_id, hts_id; ';'-separated eqasim output)
      -> assigned_class_by_person.csv (the analysis stage's per-worker assigned class)
      -> work_dir/pseudonym_map.csv (source_person_id -> H_ID, P_ID; local-only, never committed)
      -> MiD2023_Personen.csv (H_ID, P_ID -> P_ARB_ENTF, P_STARB1, starb2; server-only raw)

Outputs (aggregates only, see ``braunschweig.calibration.commute_day_state_reference.
donor_vs_assigned_class``):

    donor_vs_assigned_class.csv          cross-tab donor class x assigned class, with a
                                         provenance header
    donor_vs_assigned_diagnostics.json   the join rates and mismatch counts

Every join rate is logged; a rate below ``--min-join-rate`` (default 0.99) FAILS the script
rather than degrading silently (CLAUDE.md "Fallback transparency"): a broken pseudonym or MiD
join would otherwise produce a small, plausible-looking cross-tab that is not the measurement it
claims to be.

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
MID_PERSON_COLUMNS = ["H_ID", "P_ID", "P_ARB_ENTF", "P_STARB1", "starb2"]

MID_PERSON_FILE = "MiD2023_Personen.csv"
CROSS_TAB_FILE = "donor_vs_assigned_class.csv"
DIAGNOSTICS_FILE = "donor_vs_assigned_diagnostics.json"

DEFAULT_MIN_JOIN_RATE = 0.99

#: Column names that must NEVER reach an output file: raw MiD identifiers and the surrogate
#: donor id, which together would re-identify a MiD respondent from a committed aggregate.
FORBIDDEN_OUTPUT_COLUMNS = ("H_ID", "P_ID", "HP_ID", "hts_id", "person_id", "source_person_id",
                            "source_household_id")


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


def _header_lines(args, diagnostics: dict) -> list[str]:
    return [
        f"# Table: {CROSS_TAB_FILE} -- donor (MiD) commute-distance class x assigned (model) class,",
        "#   unweighted SAMPLE counts of synthetic workers plus per-donor-class row shares.",
        f"# Generated by scripts/measure_donor_distance_mismatch.py on {dt.date.today().isoformat()}.",
        f"# Code state: eqasim-bs {args.source_commit}, helper donor_vs_assigned_class in",
        "#   braunschweig/calibration/commute_day_state_reference.py.",
        f"# Population: {args.persons_csv}",
        f"# Assigned classes: {args.assigned_class_csv} (analysis stage",
        "#   braunschweig.analysis.synthesis.work_participation_by_kreis).",
        "# Donor link: work_dir pseudonym_map.csv (source_person_id -> H_ID, P_ID; server-only,",
        f"#   never committed) -> {MID_PERSON_FILE} (P_ARB_ENTF, P_STARB1, starb2).",
        "# Donor distance: P_ARB_ENTF cleaned by clean_mid_commute_distance_km (codes 996, 999 and",
        "#   every value >= 1000 become missing; 200.0 is a legitimate top-code, classed 100_200).",
        "# Classes (km): lt10 [0,10), 10_25, 25_50, 50_100, 100_200 (incl. the 200 top-code), gt200;",
        "#   'missing' = no usable distance (donor rows) or no assigned class (columns).",
        f"# Join rates: assigned->persons {100.0 * diagnostics['join_rate_assigned_to_persons']:.4f}%,",
        f"#   hts_id->pseudonym_map {100.0 * diagnostics['join_rate_hts_to_pseudonym_map']:.4f}%,",
        f"#   pseudonym_map->MiD {100.0 * diagnostics['join_rate_pseudonym_map_to_mid']:.4f}%",
        f"#   (required minimum {100.0 * diagnostics['min_join_rate']:.2f}%).",
        "# Purpose: Phase A measurement of the population whose assigned distance class differs from",
        "#   its donor's (spec 2026-09-04, issue #244). Measurement only -- decides nothing, and is",
        "#   NOT a validation against observed behaviour.",
        "# Contains aggregates only: no person, household or MiD identifier.",
    ]


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
                        help=f"directory containing the raw {MID_PERSON_FILE}")
    parser.add_argument("--out-dir", required=True, help="directory the two aggregates are written to")
    parser.add_argument("--source-commit", required=True,
                        help="short git SHA of the eqasim-bs checkout this run's code state "
                             "corresponds to; recorded verbatim in the output header")
    parser.add_argument("--persons-sep", default=";",
                        help="field separator of --persons-csv (default ';', the eqasim output "
                             "convention)")
    parser.add_argument("--min-join-rate", type=float, default=DEFAULT_MIN_JOIN_RATE,
                        help=f"minimum acceptable match rate of every join (default "
                             f"{DEFAULT_MIN_JOIN_RATE}); below it the script fails")
    args = parser.parse_args(argv)

    persons = _read_csv(Path(args.persons_csv), PERSONS_COLUMNS, sep=args.persons_sep)
    assigned = _read_csv(Path(args.assigned_class_csv), ASSIGNED_COLUMNS)
    pseudonym_map = _read_csv(Path(args.pseudonym_map), PSEUDONYM_COLUMNS)
    mid_persons = _read_csv(Path(args.mid_raw) / MID_PERSON_FILE, MID_PERSON_COLUMNS)

    persons["person_id"] = _normalise_id(persons["person_id"])
    persons["hts_id"] = _normalise_id(persons["hts_id"])
    assigned["person_id"] = _normalise_id(assigned["person_id"])
    pseudonym_map["source_person_id"] = _normalise_id(pseudonym_map["source_person_id"])
    for frame in (pseudonym_map, mid_persons):
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

    donors = pd.DataFrame({
        "hts_id": linked["source_person_id"].to_numpy(),
        "donor_distance_km": R.clean_mid_commute_distance_km(linked["P_ARB_ENTF"]).to_numpy(),
        "donor_worked_on_day": linked["P_STARB1"].to_numpy(),
        "donor_starb2": linked["starb2"].to_numpy(),
    })

    n_workers_in_map = int(workers["hts_id"].isin(set(pseudonym_map["source_person_id"])).sum())
    rate_hts_to_map = _enforce_join_rate(
        n_workers_in_map, len(workers), "workers (hts_id) -> pseudonym_map.source_person_id",
        args.min_join_rate)

    cross_tab, diagnostics = R.donor_vs_assigned_class(
        workers[["person_id", "hts_id", "assigned_distance_class"]], donors)
    diagnostics = dict(diagnostics)
    diagnostics.update({
        "min_join_rate": float(args.min_join_rate),
        "join_rate_assigned_to_persons": rate_assigned_to_persons,
        "join_rate_hts_to_pseudonym_map": rate_hts_to_map,
        "join_rate_pseudonym_map_to_mid": rate_pseudonym_to_mid,
        "n_pseudonym_map_rows": int(len(pseudonym_map)),
        "n_donor_rows": int(len(donors)),
        "source_commit": args.source_commit,
        "persons_csv": str(args.persons_csv),
        "assigned_class_csv": str(args.assigned_class_csv),
        "generated_on": dt.date.today().isoformat(),
    })

    _assert_no_identifiers(cross_tab, CROSS_TAB_FILE)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cross_tab_path = out_dir / CROSS_TAB_FILE
    with open(cross_tab_path, "w", encoding="utf-8", newline="") as handle:
        for line in _header_lines(args, diagnostics):
            handle.write(line + "\n")
        cross_tab.to_csv(handle, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s (%d rows)", cross_tab_path, len(cross_tab))

    diagnostics_path = out_dir / DIAGNOSTICS_FILE
    with open(diagnostics_path, "w", encoding="utf-8") as handle:
        json.dump(diagnostics, handle, indent=2, sort_keys=True)
        handle.write("\n")
    logger.info("wrote %s", diagnostics_path)
    logger.info("HEADLINE: %d/%d comparable workers have an assigned class above their donor's "
                "(%.2f%%); donor distance missing for %d of %d matched workers",
                diagnostics["n_assigned_gt_donor"], diagnostics["n_comparable"],
                100.0 * diagnostics["share_assigned_gt_donor"],
                diagnostics["n_donor_distance_missing"], diagnostics["n_matched_donor"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
