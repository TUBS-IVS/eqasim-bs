"""Extract the four committed SrV 2023 full-day absence aggregates (issue #370, sub-project B).

Reads the LOCAL-ONLY SrV 2023 "Braunschweig und RGB" scientific-use microdata (persons,
households; cp1252, semicolon, decimal comma) and writes four small aggregate tables to
``--out-dir`` (default ``eqasim-data/data/braunschweig/srv``):

    srv2023_absence_by_age_band.csv           (share of persons away from home the whole
                                               reporting day, per age band, GEWICHT_P_ZENSUS)
    srv2023_absence_household_by_size.csv     (share of households in which EVERY member is
                                               away, per household size class, GEWICHT_HH_ZENSUS)
    srv2023_absence_composition_by_age_band.csv (issue #426: ABSENT persons per age band split
                                               into whole household away / part away WITH another
                                               absent adult / part away WITHOUT, GEWICHT_P_ZENSUS)
    srv2023_absence_partial_subset_size.csv   (issue #426: how many members travel together in a
                                               PARTIALLY absent household, per size class,
                                               GEWICHT_HH_ZENSUS)

The first two are a VALIDATION-ANCHORED DRAW REFERENCE for the general-day-absence state draw of
``braunschweig.synthesis.day_absence`` -- NOT a PopulationSim control target: no synthesis or
location stage's demand distribution is controlled to these shares, they only parameterise a
per-person / per-household absence draw; the last two are the issue-#426 acceptance and sizing
references, read by no pipeline stage (scripts/measure_absence_composition.py reads the
composition table). All definitions -- the universe, the age bands, the
household size classes, the guards -- live in ``braunschweig.calibration.srv_absence``; this
script owns only the raw I/O, the CLI and the provenance header. Each written file carries the
diagnostics produced by the builder, so the exclusion counts live IN the committed file and not
only in a run log.

Usage (eqasim env, from a worktree; point --raw at the local raw directory):
    python scripts/extract_srv_absence.py \
        --raw C:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/data/braunschweig/srv/srv2023_raw \
        --out-dir eqasim-data/data/braunschweig/srv --source-commit <sha>
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.calibration import srv_absence as A  # noqa: E402

RAW_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_raw"
OUT_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
CSV_READ_KWARGS = dict(sep=";", decimal=",", encoding="cp1252", low_memory=False)

PERSONS_FILE = "SrV2023_Personen.csv"
HOUSEHOLDS_FILE = "SrV2023_Haushalte.csv"

# Units of the diagnostics keys, written once into each header's "Exclusions:" line: the key
# names alone do not say whether a count is measured over the FULL raw Personen file or over an
# already-filtered subset (same note as scripts/extract_srv_participation_universe.py).
_EXCLUSIONS_UNITS_NOTE = (
    "(n_persons_raw is the raw Personen-file row count; n_persons_dropped_weight is measured on "
    "it; n_persons_universe is the resulting count with a valid positive GEWICHT_P_ZENSUS; "
    "n_absent and n_persons_missing_age are measured on n_persons_universe)"
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("extract_srv_absence")


def _require_columns(path: Path, required: list) -> None:
    """Fail early with the file name and the missing column(s) rather than a bare KeyError."""
    header = pd.read_csv(path, nrows=0, **CSV_READ_KWARGS)
    missing = [c for c in required if c not in header.columns]
    if missing:
        raise ValueError("%s: missing required column(s) %s" % (path, missing))


def _common_header(table_name: str, diagnostics: dict, source_commit: str,
                   role_lines: list = None, include_age_bands: bool = True) -> list:
    """Provenance lines shared by all four tables (source, role, universe, bands, guards).

    ``role_lines`` overrides the generic "Role: VALIDATION-ANCHORED DRAW REFERENCE ..." block:
    ``None`` (the default) keeps that block verbatim -- true for the by-age and by-size tables,
    which the general-day-absence draw actually reads; an explicit list (``[]`` to omit the
    generic block entirely) is used by the issue #426 tables, which the draw does NOT read, so
    the generic block would otherwise make an untrue claim in their own committed header
    (issue #426 review, finding 1). ``include_age_bands=False`` skips the "Age bands (V_ALTER
    ...)" line for the partial-subset table, which has no age dimension of its own."""
    band_bounds = ", ".join("%s: %d-%d" % (band, lo, hi)
                            for band, (lo, hi) in A.AGE_BAND_BOUNDS.items())
    if role_lines is None:
        role_lines = [
            "# Role: VALIDATION-ANCHORED DRAW REFERENCE for braunschweig.synthesis.day_absence; not",
            "#   a PopulationSim control target -- no synthesis or location stage's demand",
            "#   distribution is controlled to these shares, they parameterise the",
            "#   general-day-absence state draw only.",
        ]
    lines = [
        "# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig scientific-use",
        "#   microdata (local-only, see srv2023_raw/README.md; files %s, %s), generated by"
        % (PERSONS_FILE, HOUSEHOLDS_FILE),
        "#   scripts/extract_srv_absence.py on %s." % dt.date.today().isoformat(),
        "# Code state: eqasim-bs %s, module braunschweig/calibration/srv_absence.py."
        % source_commit,
        "# Table: %s" % table_name,
    ] + role_lines + [
        "# Universe: every delivered person with a valid positive GEWICHT_P_ZENSUS (the",
        "#   at_home_zero universe of braunschweig.calibration.srv_plan_structure -- absence IS",
        "#   the state being measured here, so away persons are KEPT, unlike the",
        "#   participation-universe tables of issue #368, which exclude them).",
        "# Away from home: E_ANZ_WEGE == %d over the WHOLE reporting day (the only negative"
        % A.AWAY_FROM_HOME_CODE,
        "#   E_ANZ_WEGE code this delivery carries).",
        "# Weights: GEWICHT_P_ZENSUS (persons) / GEWICHT_HH_ZENSUS (households); expansion to",
        "#   Zensus 2022 counts, the stratum-internal GEWICHT_P/GEWICHT_HH must not be used",
        "#   across strata (ADR-0055).",
    ]
    if include_age_bands:
        lines.append("# Age bands (V_ALTER, bounds INCLUSIVE): %s." % band_bounds)
    lines += [
        "# Universe guards (the extraction raises, it never adapts): every MITTL_WERKTAG == %d"
        % A.AVERAGE_WEEKDAY,
        "#   (average-weekday Tuesday-Thursday delivery) and every negative E_ANZ_WEGE == %d."
        % A.AWAY_FROM_HOME_CODE,
        "# Exclusions: " + ", ".join("%s=%s" % (key, value) for key, value in diagnostics.items())
        + " " + _EXCLUSIONS_UNITS_NOTE,
    ]
    return lines


def _age_header(table: pd.DataFrame, diagnostics: dict, source_commit: str) -> list:
    """Provenance header for the absence-by-age-band table."""
    lines = _common_header(A.ABSENCE_BY_AGE_TABLE, diagnostics, source_commit)
    all_row = table[table["band"] == A.ALL_BAND].iloc[0]
    lines += [
        "# Columns: band (%s + 'all'), age_min, age_max (INCLUSIVE bounds; the 'all' row spans"
        % list(A.AGE_BAND_LABELS),
        "#   0-200), n_unweighted, n_absent_unweighted, p_absent (weighted share of the band away",
        "#   the whole reporting day). Every band is emitted even if empty (n_unweighted=0,",
        "#   p_absent=NaN), so a consumer never has to guess whether a band is missing or empty.",
        "# 'all' row (n_unweighted=%d): n_absent_unweighted=%d, p_absent=%.4f."
        % (int(all_row["n_unweighted"]), int(all_row["n_absent_unweighted"]), all_row["p_absent"]),
        "# Rows: %d bands + 1 'all' row = %d total." % (len(A.AGE_BAND_LABELS), len(table)),
        "# Invariants (checked before writing, the extraction raises on a violation): exactly the",
        "#   bands %s + 'all' are present, in that order; the band person counts sum to at most"
        % list(A.AGE_BAND_LABELS),
        "#   the 'all' row's n_unweighted; every share is NaN or inside [0, 1].",
    ]
    return lines


def _size_header(table: pd.DataFrame, diagnostics: dict, source_commit: str, clustering: dict,
                 n_roster_checked: int) -> list:
    """Provenance header for the absence-household-by-size table."""
    lines = _common_header(A.ABSENCE_HOUSEHOLD_TABLE, diagnostics, source_commit)
    lines += [
        "# Clustering: %.4f (%d/%d) of absent persons live in a household in which EVERY member"
        % (clustering["unweighted_share"], clustering["n_absent_in_fully_absent_households"],
           clustering["n_absent_persons"]),
        "#   is absent, UNWEIGHTED; %.4f PERSON-WEIGHTED (GEWICHT_P_ZENSUS). Computed by"
        % clustering["weighted_share"],
        "#   braunschweig.calibration.srv_absence.clustering_share(prepared) -- the traceable",
        "#   source of the household-clustering figure ADR-0110 cites (issue #370 final-review",
        "#   fix wave, ruling R12).",
        "# Household size = the count of DELIVERED persons per HHNR in SrV2023_Personen.csv;",
        "#   VERIFIED IN CODE for this run: srv_absence.check_household_roster checked %d households against"
        % n_roster_checked,
        "#   SrV2023_Haushalte.csv's V_ANZ_PERS and RAISES on any mismatch, so this table cannot be written",
        "#   unless every checked household matched (build_absence_household_by_size re-runs the same guard).",
        "# Household size classes: 1..%d, class %d = '%d or more' members."
        % (A.HOUSEHOLD_SIZE_CLASS_TOP, A.HOUSEHOLD_SIZE_CLASS_TOP, A.HOUSEHOLD_SIZE_CLASS_TOP),
        "# Columns: size_class, n_households_unweighted, n_all_absent_unweighted, p_all_absent",
        "#   (weighted GEWICHT_HH_ZENSUS share of the size class's households in which EVERY",
        "#   member is away the whole reporting day). Because this table is weighted by the",
        "#   HOUSEHOLD weight rather than the person weight of the by-age table, its shares",
        "#   differ slightly from the person-weighted household-composition figures quoted",
        "#   elsewhere for the same universe; the values below are the ones this file commits.",
        "#   A size class with no household is still emitted, with n_households_unweighted=0 and",
        "#   a NaN share (never dropped).",
        "# Additional columns (issue #388, PERSON-level reporting reference, added on top of the",
        "#   four HOUSEHOLD-level columns above, which are UNCHANGED by this addition):",
        "#   n_persons_unweighted, n_absent_persons_unweighted, p_absent_person -- the",
        "#   GEWICHT_P_ZENSUS PERSON-weighted share of absent persons among ALL persons living in",
        "#   a household of that size class (every delivered person counted once, whether or not",
        "#   their own household is fully absent). This differs from p_all_absent, which is the",
        "#   HOUSEHOLD-weighted share of households where EVERY member is absent -- the two",
        "#   columns answer different questions and are not expected to be numerically close. A",
        "#   size class with no persons is still emitted, with n_persons_unweighted=0 and a NaN",
        "#   share (never dropped).",
        "# Additional columns (issue #426, HOUSEHOLD-level, GEWICHT_HH_ZENSUS, added on top of the",
        "#   seven columns above, which are UNCHANGED): n_partial_absent_unweighted, p_partial_absent",
        "#   -- the weighted share of the size class's households in which SOME but not ALL delivered",
        "#   members are away (0 < n_absent < n). Together with p_all_absent this partitions the",
        "#   class: p_none = 1 - p_all_absent - p_partial_absent. Size class 1 is 0 by construction.",
    ]
    lines += ["#   size %d: n_households_unweighted=%d, p_all_absent=%s, n_persons_unweighted=%d, "
              "p_absent_person=%s, n_partial_absent_unweighted=%d, p_partial_absent=%s"
              % (int(row["size_class"]), int(row["n_households_unweighted"]),
                 "NaN" if pd.isna(row["p_all_absent"]) else "%.4f" % row["p_all_absent"],
                 int(row["n_persons_unweighted"]),
                 "NaN" if pd.isna(row["p_absent_person"]) else "%.4f" % row["p_absent_person"],
                 int(row["n_partial_absent_unweighted"]),
                 "NaN" if pd.isna(row["p_partial_absent"]) else "%.4f" % row["p_partial_absent"])
              for _, row in table.iterrows()]
    lines += [
        "# Rows: %d size classes." % len(table),
        "# Invariants (checked before writing, the extraction raises on a violation): size",
        "#   classes 1..%d are present, in that order; every share is NaN or inside [0, 1];"
        % A.HOUSEHOLD_SIZE_CLASS_TOP,
        "#   n_absent_persons_unweighted <= n_persons_unweighted per row; sum(n_persons_unweighted)",
        "#   equals the by-age table's 'all' row n_unweighted.",
        "#   n_all_absent_unweighted + n_partial_absent_unweighted <= n_households_unweighted per row;",
        "#   size class 1 has n_partial_absent_unweighted 0.",
    ]
    return lines


def _composition_header(table: pd.DataFrame, diagnostics: dict, source_commit: str) -> list:
    """Provenance header for the absence-composition-by-age-band table (issue #426)."""
    lines = _common_header(A.ABSENCE_COMPOSITION_TABLE, diagnostics, source_commit, role_lines=[])
    children = table[table["band"] == A.CHILDREN_ROW].iloc[0]
    n_children = int(children["n_absent_unweighted"])
    interval_lines = []
    for pattern, count_column, share_column in zip(A.PATTERNS, A.COMPOSITION_COUNT_COLUMNS, A.COMPOSITION_SHARE_COLUMNS):
        low, high = A.wilson_interval(int(children[count_column]), n_children)
        interval_lines.append("#   %s: %d/%d unweighted, p_weighted=%s, Wilson 95%% on the unweighted counts [%.4f, %.4f]"
                              % (pattern, int(children[count_column]), n_children,
                                 "NaN" if pd.isna(children[share_column]) else "%.4f" % children[share_column],
                                 low, high))
    lines += [
        "# ROLE (issue #426): ACCEPTANCE REFERENCE for a partial-household absence pattern. The '%s'"
        % A.CHILDREN_ROW,
        "#   children row is the row the criterion is evaluated on; the seven age bands are DIAGNOSTIC",
        "#   ONLY (thin cells). Pre-registered bound: a model share is accepted when it lies inside the",
        "#   Wilson 95 % interval of this row's UNWEIGHTED counts (ASSUMPTION: the design effect of the",
        "#   expansion weights is unknown, so the interval is computed on unweighted counts and the",
        "#   weighted point estimate is reported beside it). Nothing in the pipeline reads this table.",
        "# Patterns (braunschweig.calibration.srv_absence.classify_absence_composition), evaluated per",
        "#   ABSENT person on their OWN household: whole_household = every delivered member absent;",
        "#   partial_with_absent_adult = not every member absent AND at least one OTHER member aged",
        "#   >= %d absent; partial_no_absent_adult = not every member absent and no other adult absent."
        % A.ADULT_MIN_AGE,
        "#   Child = age <= %d, adult = age >= %d; a person without a valid age is counted as a member,"
        % (A.CHILD_MAX_AGE, A.ADULT_MIN_AGE),
        "#   never as an adult, and appears in the 'all' row only. A child away 'without an absent",
        "#   adult' of its household may still be accompanied (class trip, grandparents, the other",
        "#   parent) -- the table measures the HOUSEHOLD pattern, not supervision.",
        "# Columns: band (%s + '%s' + 'all'), age_min, age_max (INCLUSIVE), n_absent_unweighted,"
        % (list(A.AGE_BAND_LABELS), A.CHILDREN_ROW),
        "#   %s (unweighted counts), %s (GEWICHT_P_ZENSUS-weighted shares of the row's ABSENT persons;"
        % (list(A.COMPOSITION_COUNT_COLUMNS), list(A.COMPOSITION_SHARE_COLUMNS)),
        "#   the three sum to 1 per row, NaN when the row has no absent person).",
        "# '%s' row (n_absent_unweighted=%d):" % (A.CHILDREN_ROW, n_children),
    ] + interval_lines + [
        "# Rows: %d bands + '%s' + 'all' = %d total." % (len(A.AGE_BAND_LABELS), A.CHILDREN_ROW, len(table)),
        "# Invariants (checked before writing, the extraction raises on a violation): exactly the rows",
        "#   above, in that order; pattern counts sum to n_absent_unweighted; shares in [0, 1] and",
        "#   summing to 1 in every populated row; the 'all' row's n_absent_unweighted equals the",
        "#   by-age table's 'all' row n_absent_unweighted; every count column of rows 0-5 + 6-17",
        "#   equals the '%s' row (a negative, i.e. missing, age code is in neither)." % A.CHILDREN_ROW,
    ]
    return lines


def _partial_subset_header(table: pd.DataFrame, by_size: pd.DataFrame, diagnostics: dict, source_commit: str) -> list:
    """Provenance header for the partial-subset-size table (issue #426)."""
    lines = _common_header(A.ABSENCE_PARTIAL_SUBSET_TABLE, diagnostics, source_commit,
                           role_lines=[], include_age_bands=False)
    lines += [
        "# ROLE (issue #426): sizes the 'a SUBSET of the household travels together' pattern for a",
        "#   future partial-household draw. Nothing in the pipeline reads this table today.",
        "# Universe of rows: PARTIALLY absent households only (0 < n_absent < n, the by-size table's",
        "#   n_partial_absent_unweighted per size class); size class 1 cannot be partial and has no row.",
        "# Columns: size_class (2..%d, %d = '%d or more' members), n_absent_members (1..%d, %d = '%d or"
        % (A.HOUSEHOLD_SIZE_CLASS_TOP, A.HOUSEHOLD_SIZE_CLASS_TOP, A.HOUSEHOLD_SIZE_CLASS_TOP,
           A.PARTIAL_SUBSET_TOP, A.PARTIAL_SUBSET_TOP, A.PARTIAL_SUBSET_TOP),
        "#   more'; only k <= size_class - 1 rows exist), n_households_unweighted, share_within_partial",
        "#   (GEWICHT_HH_ZENSUS-weighted share among the class's partially absent households; NaN when",
        "#   the class has no partial household). Every (class, k) row is emitted even when empty.",
    ]
    by_size_partial = by_size.set_index("size_class")["n_partial_absent_unweighted"]
    for size_class, group in table.groupby("size_class"):
        lines.append("#   size %d: n_partial=%d; " % (int(size_class), int(by_size_partial.loc[size_class]))
                     + ", ".join("k=%d: %d (%s)" % (int(r["n_absent_members"]), int(r["n_households_unweighted"]),
                                                    "NaN" if pd.isna(r["share_within_partial"])
                                                    else "%.4f" % r["share_within_partial"])
                                 for _, r in group.iterrows()))
    lines += [
        "# Rows: %d." % len(table),
        "# Invariants (checked before writing, the extraction raises on a violation): exactly the",
        "#   (size_class, n_absent_members) rows above; shares in [0, 1] and summing to 1 within every",
        "#   populated class; per class, the row counts sum to the by-size table's",
        "#   n_partial_absent_unweighted.",
    ]
    return lines


def _write(df: pd.DataFrame, path: Path, header: list) -> None:
    """Write the provenance-headed CSV. ``lineterminator="\\n"`` keeps the data rows LF-only
    regardless of platform; ``float_format="%.10g"`` keeps floats readable while staying
    lossless at the precision the shares carry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for line in header:
            fh.write(line + "\n")
        df.to_csv(fh, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s (%d rows)", path, len(df))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw", type=Path, default=RAW_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=OUT_DEFAULT)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args(argv)
    persons_path, households_path = args.raw / "SrV2023_Personen.csv", args.raw / "SrV2023_Haushalte.csv"
    _require_columns(persons_path, A.PERSON_COLUMNS)
    _require_columns(households_path, A.HOUSEHOLD_COLUMNS)
    persons = pd.read_csv(persons_path, usecols=A.PERSON_COLUMNS, **CSV_READ_KWARGS)
    households = pd.read_csv(households_path, usecols=A.HOUSEHOLD_COLUMNS, **CSV_READ_KWARGS)
    logger.info("read %d persons, %d households", len(persons), len(households))
    prepared, diagnostics = A.prepare_absence_persons(persons)
    by_age = A.build_absence_by_age_band(prepared)
    # The by-size builder runs FIRST: it attaches the household weights (a household missing from
    # the household file raises "no row / weight" there) and then runs the roster guard itself;
    # the explicit call below only yields the guard's own count for the provenance header.
    by_size = A.build_absence_household_by_size(prepared, households)
    n_roster_checked = A.check_household_roster(A.household_absence_patterns(prepared), households)
    composition = A.build_absence_composition_by_band(prepared)
    partial_subset = A.build_absence_partial_subset_size(prepared, households)
    clustering = A.clustering_share(prepared)
    A.check_invariants(by_age, by_size, composition, partial_subset)
    logger.info("invariants passed; diagnostics: %s; clustering: %s", diagnostics, clustering)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    _write(by_age, args.out_dir / A.ABSENCE_BY_AGE_TABLE, _age_header(by_age, diagnostics, args.source_commit))
    _write(by_size, args.out_dir / A.ABSENCE_HOUSEHOLD_TABLE,
           _size_header(by_size, diagnostics, args.source_commit, clustering, n_roster_checked))
    _write(composition, args.out_dir / A.ABSENCE_COMPOSITION_TABLE,
           _composition_header(composition, diagnostics, args.source_commit))
    _write(partial_subset, args.out_dir / A.ABSENCE_PARTIAL_SUBSET_TABLE,
           _partial_subset_header(partial_subset, by_size, diagnostics, args.source_commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
