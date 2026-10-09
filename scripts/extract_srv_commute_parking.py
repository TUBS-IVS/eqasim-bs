"""Extract the committed SrV 2023 commute-parking and city-centre parking tables (issue #249).

Reads the LOCAL-ONLY SrV 2023 "Braunschweig und RGB" scientific-use microdata (SciUse_v4;
cp1252, semicolon, decimal comma) -- the person file, the trip file and the two P2 add-on person
modules -- and writes two small aggregate tables to ``--out-dir`` (default
``eqasim-data/data/braunschweig/srv``):

    srv2023_commute_parking_by_workplace_class.csv
    srv2023_city_center_parking.csv

Definitions live in ``braunschweig.calibration.srv_parking``. Each table carries a ``#``
provenance header: script, run date, code state, file name and SHA-256 of every local raw file
read, weight, universe, workplace-class rule, ``min_cell_n`` and the exclusion counts with their
rates. Only aggregates are written; the free-text columns of the add-on modules are never read.

The add-on modules are the delivery's ``..._SciUse_v4_P2_BRAU.csv`` and ``..._SciUse_v4_P2_RGB.csv``,
copied into ``srv2023_raw/`` as ``SrV2023_Personen_Zusatz_Braunschweig.csv`` and
``SrV2023_Personen_Zusatz_RGB.csv`` (see docs/registry/data/srv2023_raw.yml).

Usage (eqasim env; from a worktree, point --raw at the main checkout's raw directory):
    python scripts/extract_srv_commute_parking.py \
        --raw <main checkout>/eqasim-data/data/braunschweig/srv/srv2023_raw \
        --out-dir eqasim-data/data/braunschweig/srv --source-commit <sha>
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import logging
import sys
import textwrap
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.calibration import srv_parking as sp  # noqa: E402

#: The "Use:" lines of the header of srv2023_city_center_parking.csv (spec Amendment E5): the garage share became the
#: calibration target of the garage decay length, so "never a model input" no longer holds for it. The committed table
#: carries these lines (tests/test_srv_parking.py compares them).
CITY_CENTER_USE_LINES = (
    "# Use: the rows garage_large_lot and street are the CALIBRATION TARGET of the decay length of the garage options",
    "#   (parking-cost-zones v2, spec Amendment E5: garage_large_lot / (garage_large_lot + street), read by",
    "#   scripts/parking/calibrate_garage_decay.py); the garage share therefore no longer validates the model.",
    "#   paid_share_overall and the other rows stay comparison quantities (parking-cost-zones design, section 6),",
    "#   never a model input. A comparison with model output carries a universe caveat: SrV asks Braunschweig",
    "#   residents about their usual city-centre parking, the model counts all car arrivals.",
)
RAW_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_raw"
OUT_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv"

PERSONS_FILE = "SrV2023_Personen.csv"
TRIPS_FILE = "SrV2023_Wege.csv"
ADDON_BRAUNSCHWEIG_FILE = "SrV2023_Personen_Zusatz_Braunschweig.csv"
ADDON_RGB_FILE = "SrV2023_Personen_Zusatz_RGB.csv"
#: Local file name -> file name in the untouched delivery.
DELIVERY_FILE_NAMES = {
    PERSONS_FILE: "SrV2023_Einzeldaten_Braunschweig und RGB_SciUse_v4_P.csv",
    TRIPS_FILE: "SrV2023_Einzeldaten_Braunschweig und RGB_SciUse_v4_W.csv",
    ADDON_BRAUNSCHWEIG_FILE: "SrV2023_Einzeldaten_Braunschweig und RGB_SciUse_v4_P2_BRAU.csv",
    ADDON_RGB_FILE: "SrV2023_Einzeldaten_Braunschweig und RGB_SciUse_v4_P2_RGB.csv",
}
COMMUTE_SOURCES = (PERSONS_FILE, TRIPS_FILE, ADDON_BRAUNSCHWEIG_FILE, ADDON_RGB_FILE)
CITY_CENTER_SOURCES = (PERSONS_FILE, ADDON_BRAUNSCHWEIG_FILE)

# Raw SrV CSVs are semicolon-separated, decimal-comma, cp1252-encoded.
CSV_READ_KWARGS = dict(sep=";", decimal=",", encoding="cp1252", low_memory=False)


def _question_columns(place_column: str) -> list[str]:
    return [place_column] + [sp.payment_followup_column(place_column, code)
                             for code in sp.PAYMENT_FOLLOWUP_CODES]


PERSON_COLUMNS = [*sp.PERSON_KEYS, sp.WEIGHT_COLUMN]
TRIP_COLUMNS = [*sp.PERSON_KEYS, sp.TRIP_NUMBER_COLUMN, sp.PURPOSE_COLUMN,
                sp.DESTINATION_AGS_COLUMN, sp.DESTINATION_OBERBEZIRK_COLUMN]
ADDON_BRAUNSCHWEIG_COLUMNS = [*sp.PERSON_KEYS,
                              *_question_columns(f"{sp.MODULE_PREFIXES['BRAU']}_PARKENAPL"),
                              *_question_columns(sp.CITY_CENTER_PLACE_COLUMN)]
ADDON_RGB_COLUMNS = [*sp.PERSON_KEYS, *_question_columns(f"{sp.MODULE_PREFIXES['RGB']}_PARKENAPL")]

SHARE_DECIMALS = 4
N_EFF_DECIMALS = 1
HEADER_WIDTH_CHARACTERS = 100

A1_SENTENCE = (
    "Shares are observed among current car commuters (SrV 2023 BS+RGB SciUse_v4); assumption A1 of "
    "the parking-cost-zones design transfers them to all workers of a class.")

logger = logging.getLogger("extract_srv_commute_parking")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_raw(raw: Path, name: str, columns: list[str]) -> pd.DataFrame:
    """Read the named columns of one raw file, failing early on a missing file or column."""
    path = raw / name
    if not path.exists():
        raise FileNotFoundError(
            f"SrV raw file missing: {path}. Copy '{DELIVERY_FILE_NAMES[name]}' from the delivery "
            "under this name (docs/registry/data/srv2023_raw.yml).")
    header = pd.read_csv(path, nrows=0, **CSV_READ_KWARGS)
    missing = [column for column in columns if column not in header.columns]
    if missing:
        raise ValueError(f"{path}: missing required column(s) {missing}")
    frame = pd.read_csv(path, usecols=columns, **CSV_READ_KWARGS)
    logger.info("[srv parking] read %s: %d rows, %d columns", name, len(frame), len(columns))
    return frame


def _rate(count: int, total: int) -> str:
    return f"{count}/{total} ({100.0 * count / total:.1f}%)" if total else f"{count}/0"


def _wrapped(statement: str) -> list[str]:
    """One header statement as '#' comment lines of at most HEADER_WIDTH_CHARACTERS."""
    return textwrap.wrap(statement, width=HEADER_WIDTH_CHARACTERS, initial_indent="# ",
                         subsequent_indent="#   ", break_long_words=False, break_on_hyphens=False)


def _missing_place_codes(frames_and_columns) -> str:
    """Counts of the SrV missing codes of the place question(s) in the raw input.

    They describe the INPUT (the -8 code is the survey's question filter, -10 an implausible
    answer); the universe report counts each dropped respondent once, weight checks first.
    """
    not_asked = implausible = 0
    for frame, column in frames_and_columns:
        codes = pd.to_numeric(frame[column], errors="coerce")
        not_asked += int((codes == -8).sum())
        implausible += int((codes == -10).sum())
    return f"-8={not_asked} (not asked), -10={implausible} (implausible)"


def _common_header(table_file: str, sources, hashes: dict, run_date: str, source_commit: str):
    lines = [
        f"# Table: {table_file}",
        "# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig (RGB) scientific-use",
        "#   microdata SciUse_v4 (TU Dresden); local-only, see docs/registry/data/srv2023_raw.yml.",
        f"# Generated by: scripts/extract_srv_commute_parking.py on {run_date} (builders in",
        f"#   braunschweig/calibration/srv_parking.py; code state eqasim-bs {source_commit}).",
        "# Input files (local copies in srv2023_raw/, SHA-256):",
    ]
    for name in sources:
        lines.append(f"#   {name} (delivery '{DELIVERY_FILE_NAMES[name]}') sha256={hashes[name]}")
    lines += [
        "# Weight: GEWICHT_P_ZENSUS (person expansion to Zensus 2022 counts).",
    ]
    return lines


def _commute_header(table: pd.DataFrame, universe: pd.DataFrame, report: dict, place_codes: str,
                    hashes: dict, min_cell_n: int, run_date: str, source_commit: str) -> list[str]:
    labels = universe["workplace_class"]
    n_universe = len(universe)
    outside = ", ".join(f"{label} {_rate(int((labels == label).sum()), n_universe)}"
                        for label in sp.UNCLASSIFIED_LABELS)
    # Selection check: the free share of the car commuters that no row can hold, reported only
    # for groups of at least min_cell_n respondents (smaller groups by count only).
    checks = []
    for label in sp.UNCLASSIFIED_LABELS:
        group = universe[labels == label]
        if len(group) >= min_cell_n:
            share_free = sp.parking_shares(group)["share_free_total"]
            checks.append(f"{label} {share_free:.{SHARE_DECIMALS}f} (n={len(group)})")
    total = table[table["level"] == sp.LEVEL_TOTAL].iloc[0]
    exclusions = ", ".join(f"{key}={value}" for key, value in report.items())
    lines = _common_header(sp.COMMUTE_TABLE_FILE, COMMUTE_SOURCES, hashes, run_date, source_commit)
    lines += [
        "#   n_eff = (sum w)^2 / sum(w^2) (Kish effective sample size).",
        "# Universe: car commuters = respondents of the two P2 add-on person modules whose usual parking",
        "#   place when driving to work/education (V_BRAU_PARKENAPL / V_RGB_PARKENAPL) is 1 employer or",
        "#   institution lot, 2 public street, 3 underground/multi-storey garage or large lot, or 5 other",
        "#   place, with a valid payment follow-up V_*_PARKENAPL<k>_ENTGELT (1 free, 2 paid) for that",
        "#   place and GEWICHT_P_ZENSUS > 0. Code 4 (does not drive to work/education) is outside the",
        "#   universe by definition; -8 (not asked) and -10 (implausible) are dropped.",
        "# Workplace class: destination of the person's first trip of the survey day (lowest WNR) with",
        "#   V_ZWECK 1 (own workplace) or 6 (vocational school/university); Kreis = first five digits of",
        "#   the zero-padded 8-digit V_ZIEL_AGS; Braunschweig (03101) is split by V_ZIEL_OBERBEZIRK:",
        "#   1 -> bs_zentrum, 2-3 -> bs_innenbereich, 4-6 -> bs_outer (SrV2023_Teilraumkodierung);",
        "#   every other ZGB Kreis is its own class. Read workplace_class as text (leading zero).",
        "# Payment per place (ruling R-4h-1): share_<place>_<paid|free> for the four places is the weighted",
        "#   fraction of the whole universe of the row that reports the place and its payment, so the eight",
        "#   columns sum to 1 and share_<place>_paid + share_<place>_free = share_<place>. The PAID-ONLY garage",
        "#   share share_garage_large_lot_paid / (share_garage_large_lot_paid + share_street_paid) is the",
        "#   like-for-like quantity of a model universe without free parkers (the parking-cost-zones commuter",
        "#   decay is calibrated on it); share_garage_large_lot / (share_garage_large_lot + share_street)",
        "#   keeps the free parkers in its denominator and is not like-for-like with such a universe.",
        "# Rows: level=class, one per workplace class; level=total (workplace_class=total) pools exactly",
        "#   the class rows, so the class n_unweighted sum to the total n_unweighted.",
        *_wrapped(f"Exclusions from the universe (each respondent once, in this order): {exclusions}. "
                  f"Place codes in the input: {place_codes}."),
        *_wrapped(f"Car commuters in no row (of n_universe={n_universe}): {outside}."),
        *_wrapped("Selection check (share_free_total of those groups, never used by the model; groups "
                  f"below min_cell_n by count only): {'; '.join(checks) if checks else 'none'}; total "
                  f"row {total['share_free_total']:.{SHARE_DECIMALS}f} "
                  f"(n={int(total['n_unweighted'])})."),
        f"# Guard: min_cell_n={min_cell_n} (a class with fewer unweighted respondents raises).",
        "# Coverage: respondents are residents of seven of the eight ZGB Kreise, Braunschweig and the",
        "#   six other surveyed Kreise (stratified PSU sample of ~44 municipalities). Wolfsburg (03103)",
        "#   residents are not surveyed, so the 03103 row describes in-commuters from the surveyed area",
        "#   only; in-commuters from outside the Verbandsgebiet are represented in no row.",
        f"# {A1_SENTENCE}",
        f"# Shares are weighted fractions in [0, 1], rounded to {SHARE_DECIMALS} decimals (n_eff to "
        f"{N_EFF_DECIMALS}); per row the",
        "#   four place shares, the two payment shares and the eight per-place payment shares each sum to 1",
        "#   before rounding.",
    ]
    return lines


def _city_center_header(report: dict, place_codes: str, hashes: dict, run_date: str,
                        source_commit: str) -> list[str]:
    exclusions = ", ".join(f"{key}={value}" for key, value in report.items())
    lines = _common_header(sp.CITY_CENTER_TABLE_FILE, CITY_CENTER_SOURCES, hashes, run_date,
                           source_commit)
    lines += [
        "# Universe: Braunschweig residents (P2 add-on module BRAU) whose usual parking place in the",
        "#   Braunschweig city centre (V_BRAU_PARKENCITY) is 1 public street, 2 underground/multi-storey",
        "#   garage or large lot, 3 employer lot or 5 other place, with GEWICHT_P_ZENSUS > 0. Code 4",
        "#   (does not drive to the city centre) is outside the universe; -8 (not asked) and -10",
        "#   (implausible) are dropped.",
        "# Codes: V_BRAU_PARKENCITY orders its places differently from V_*_PARKENAPL; the labels follow",
        "#   the codebook (SrV2023_Datenkodierung_SciUse.xlsx), not the PARKENAPL order.",
        "# Rows: one per parking place (share = weighted share of the universe, n_unweighted =",
        "#   respondents naming that place), then paid_share_overall (share = weighted share paying",
        "#   among the universe respondents with a valid follow-up V_BRAU_PARKENCITY<k>_ENTGELT,",
        "#   n_unweighted = those respondents).",
        *CITY_CENTER_USE_LINES,
        *_wrapped(f"Exclusions from the universe (each respondent once, in this order): {exclusions}. "
                  f"Place codes in the input: {place_codes}."),
        f"# Shares are weighted fractions in [0, 1], rounded to {SHARE_DECIMALS} decimals.",
    ]
    return lines


def _write(table: pd.DataFrame, path: Path, header: list[str]) -> None:
    """Write the provenance-headed CSV with LF line endings."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        logger.info("[srv parking] overwriting the existing %s (regeneration)", path)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        for line in header:
            handle.write(line + "\n")
        table.to_csv(handle, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("[srv parking] wrote %s (%d rows)", path, len(table))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default=str(RAW_DEFAULT),
                        help="SrV raw directory (local-only) holding the person, trip and add-on files")
    parser.add_argument("--out-dir", default=str(OUT_DEFAULT),
                        help="directory to write the two committed tables into")
    parser.add_argument("--min-cell-n", type=int, default=sp.MIN_CELL_N,
                        help="minimum unweighted car commuters per workplace class (default "
                             f"{sp.MIN_CELL_N}); a smaller class raises")
    parser.add_argument("--source-commit", default="unknown",
                        help="git commit of the code that runs, recorded in the provenance header")
    args = parser.parse_args(argv)
    if args.min_cell_n < 1:
        parser.error("--min-cell-n must be a positive integer")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    raw = Path(args.raw)
    out = Path(args.out_dir)
    run_date = dt.date.today().isoformat()

    persons = _read_raw(raw, PERSONS_FILE, PERSON_COLUMNS)
    trips = _read_raw(raw, TRIPS_FILE, TRIP_COLUMNS)
    addon_bs = _read_raw(raw, ADDON_BRAUNSCHWEIG_FILE, ADDON_BRAUNSCHWEIG_COLUMNS)
    addon_rgb = _read_raw(raw, ADDON_RGB_FILE, ADDON_RGB_COLUMNS)
    hashes = {name: _sha256(raw / name) for name in DELIVERY_FILE_NAMES}

    unified = sp.unify_addon_modules(addon_bs, addon_rgb)
    universe, report = sp.commute_parking_universe(unified, persons)
    destinations = sp.first_commute_destination(trips)
    universe = sp.attach_workplace_class(universe, destinations)
    table = sp.build_commute_parking_table(universe, min_cell_n=args.min_cell_n)

    class_rows = table[table["level"] == sp.LEVEL_CLASS]
    total_row = table[table["level"] == sp.LEVEL_TOTAL].iloc[0]
    if int(class_rows["n_unweighted"].sum()) != int(total_row["n_unweighted"]):
        raise ValueError("the total row must pool exactly the class rows")

    city_universe, city_report = sp.city_center_universe(addon_bs, persons)
    city_table = sp.city_center_shares(city_universe)

    commute_out = table.copy()
    share_columns = [column for column in commute_out.columns if column.startswith("share_")]
    commute_out[share_columns] = commute_out[share_columns].round(SHARE_DECIMALS)
    commute_out["n_eff"] = commute_out["n_eff"].round(N_EFF_DECIMALS)
    city_out = city_table.copy()
    city_out["share"] = city_out["share"].round(SHARE_DECIMALS)

    commute_place_codes = _missing_place_codes(
        [(addon_bs, f"{sp.MODULE_PREFIXES['BRAU']}_PARKENAPL"),
         (addon_rgb, f"{sp.MODULE_PREFIXES['RGB']}_PARKENAPL")])
    city_place_codes = _missing_place_codes([(addon_bs, sp.CITY_CENTER_PLACE_COLUMN)])
    _write(commute_out, out / sp.COMMUTE_TABLE_FILE,
           _commute_header(table, universe, report, commute_place_codes, hashes, args.min_cell_n,
                           run_date, args.source_commit))
    _write(city_out, out / sp.CITY_CENTER_TABLE_FILE,
           _city_center_header(city_report, city_place_codes, hashes, run_date, args.source_commit))

    logger.info("[srv parking] commute parking by workplace class:\n%s",
                commute_out.to_string(index=False))
    logger.info("[srv parking] city-centre parking:\n%s", city_out.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
