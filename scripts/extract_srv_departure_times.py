"""Extract the committed SrV 2023 departure-time and activity-duration reference tables.

Reads the LOCAL-ONLY SrV 2023 "Braunschweig und RGB" scientific-use microdata (persons, trips,
households; cp1252, semicolon, decimal comma) via ``srv_plan_structure.harmonise_srv`` and writes
two aggregate tables to ``--out-dir`` (default ``eqasim-data/data/braunschweig/srv``):

    srv2023_departure_time_reference.csv
    srv2023_activity_duration_reference.csv

Definitions live in ``braunschweig.calibration.srv_departure_times`` (the 15-minute departure bin
grid, the de-rounding rule, the activity-duration bands, the segment/purpose/position dimensions).
These are the SrV side of the departure-time model of issue #123 / ADR-0114: the departure-time
table is the model's CALIBRATED target (spec ``2026-09-09-departure-time-srv-mapping-design.md``
section 2.2), the activity-duration table its HOLD-OUT reference. The files carry a provenance
header including an ``# Exclusions:`` line (same convention as
``scripts/extract_srv_plan_structure.py``, ruling R9: exclusion counts live IN the committed file).

Usage (eqasim env, from a worktree; point --raw at the local raw directory):
    python scripts/extract_srv_departure_times.py \
        --raw C:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/data/braunschweig/srv/srv2023_raw \
        --out-dir eqasim-data/data/braunschweig/srv --source-commit <sha>
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.calibration import srv_departure_times as D  # noqa: E402
from braunschweig.calibration import srv_plan_structure as SRV  # noqa: E402

RAW_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_raw"
OUT_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
CSV_READ_KWARGS = dict(sep=";", decimal=",", encoding="cp1252", low_memory=False)

# The headline check of spec section 1.2: hour-6 / hour-7 shares of FIRST education legs of the
# school_age_6_17_not_employed group, AS REPORTED. This is an expectation to VERIFY against this
# extraction, never a value to fabricate or adjust the code towards (CLAUDE.md "no invented
# reference values") -- a deviation beyond 1 pp is reported, not hidden or corrected.
_HEADLINE_SEGMENT = "school_age_6_17_not_employed"
_HEADLINE_PURPOSE = "education"
_HEADLINE_POSITION = "first"
_HEADLINE_EXPECTED_HOUR6_PCT = 9.9
_HEADLINE_EXPECTED_HOUR7_PCT = 82.5
_HEADLINE_DEVIATION_TOLERANCE_PP = 1.0
_BINS_PER_HOUR = 60 // D.BIN_MINUTES

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("extract_srv_departure_times")


def _require_columns(path: Path, required: list) -> None:
    """Fail early with the file name and the missing column(s) rather than a bare KeyError."""
    header = pd.read_csv(path, nrows=0, **CSV_READ_KWARGS)
    missing = [c for c in required if c not in header.columns]
    if missing:
        raise ValueError("%s: missing required column(s) %s" % (path, missing))


def _hour_share(table: pd.DataFrame, hour: int, column: str) -> float:
    """As-reported/de-rounded share of the headline (segment, purpose, position) cell in one
    clock hour (the sum of its four 15-minute bins)."""
    subset = table[(table["segment"] == _HEADLINE_SEGMENT) & (table["purpose"] == _HEADLINE_PURPOSE)
                   & (table["position"] == _HEADLINE_POSITION)]
    in_hour = subset[subset["bin_15min"] // _BINS_PER_HOUR == hour]
    return float(in_hour[column].sum())


def _headline(table: pd.DataFrame) -> dict:
    """Measure the spec 1.2 headline cell and compare it against the (assumption-labelled)
    expectation, never adjusting either the measurement or the code to force a match."""
    subset = table[(table["segment"] == _HEADLINE_SEGMENT) & (table["purpose"] == _HEADLINE_PURPOSE)
                   & (table["position"] == _HEADLINE_POSITION)]
    n_unweighted = int(subset["n_unweighted"].iloc[0]) if len(subset) else 0
    hour6 = 100.0 * _hour_share(table, 6, "share_as_reported")
    hour7 = 100.0 * _hour_share(table, 7, "share_as_reported")
    hour8 = 100.0 * _hour_share(table, 8, "share_as_reported")
    deviation6 = hour6 - _HEADLINE_EXPECTED_HOUR6_PCT
    deviation7 = hour7 - _HEADLINE_EXPECTED_HOUR7_PCT
    return {
        "n_unweighted": n_unweighted, "hour6_pct": hour6, "hour7_pct": hour7, "hour8_pct": hour8,
        "deviation6_pp": deviation6, "deviation7_pp": deviation7,
        "matches_expectation": (abs(deviation6) <= _HEADLINE_DEVIATION_TOLERANCE_PP
                               and abs(deviation7) <= _HEADLINE_DEVIATION_TOLERANCE_PP),
    }


def _departure_time_diagnostics(table: pd.DataFrame) -> dict:
    attrs = table.attrs
    return {
        "n_legs_total": attrs["n_legs_total"],
        "n_legs_excluded_invalid_dep_min": attrs["n_legs_excluded_invalid_dep_min"],
        "n_legs_excluded_nan": attrs["n_legs_excluded_nan"],
        "n_legs_excluded_negative": attrs["n_legs_excluded_negative"],
        "n_legs_excluded_non_integer": attrs["n_legs_excluded_non_integer"],
        "n_bins_clipped_derounded": attrs["n_bins_clipped_derounded"],
        "n_bins_clipped_as_reported": attrs["n_bins_clipped_as_reported"],
    }


def _activity_duration_diagnostics(table: pd.DataFrame) -> dict:
    attrs = table.attrs
    return {
        "n_legs_total": attrs["n_legs_total"],
        "n_legs_excluded_no_next_or_missing_time": attrs["n_legs_excluded_no_next_or_missing_time"],
        "n_legs_excluded_out_of_range": attrs["n_legs_excluded_out_of_range"],
        "n_legs_included": attrs["n_legs_included"],
    }


def _departure_time_header(table: pd.DataFrame, diagnostics: dict, headline: dict,
                           harmonisation_diagnostics: dict, source_commit: str) -> list:
    headline_line = (
        "#   as reported: hour 6 = %.2f%% (expected 9.9%%, deviation %+.2f pp), hour 7 = %.2f%% "
        "(expected 82.5%%, deviation %+.2f pp), hour 8 = %.2f%%; n_unweighted=%d."
        % (headline["hour6_pct"], headline["deviation6_pp"], headline["hour7_pct"],
           headline["deviation7_pp"], headline["hour8_pct"], headline["n_unweighted"]))
    if not headline["matches_expectation"]:
        headline_line += (
            " DEVIATION > %.1f pp from the spec 1.2 expectation on at least one of the two hours "
            "-- reported as measured, NOT adjusted to match (CLAUDE.md 'no invented reference "
            "values')." % _HEADLINE_DEVIATION_TOLERANCE_PP)
    lines = [
        "# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig scientific-use",
        "#   microdata (local-only, see srv2023_raw/README.md), generated by",
        "#   scripts/extract_srv_departure_times.py on %s." % dt.date.today().isoformat(),
        "# Code state: eqasim-bs %s, module braunschweig/calibration/srv_departure_times.py."
        % source_commit,
        "# Table: %s" % D.DEPARTURE_TIME_TABLE,
        "# Role: the SrV side of the departure-time model (issue #123, ADR-0114) -- the",
        "#   CALIBRATED target (spec 2026-09-09-departure-time-srv-mapping-design.md section 2.2):",
        "#   'srv_mapped' quantile-maps a person's de-rounded first departure onto this",
        "#   distribution for the person's (purpose, group) cell. NOT a control target for",
        "#   PopulationSim; no synthesis or location stage other than the departure-time model",
        "#   reads this file.",
        "# Universe: %s only (every delivered person with a valid GEWICHT_P_ZENSUS; matches a" % D.UNIVERSE,
        "#   synthetic population, in which every person exists and starts the day at home). The",
        "#   at_home_only sensitivity of the plan-structure reference is NOT built here (a",
        "#   departure/arrival time cannot be observed for a person who reported no trip at all).",
        "# Weights: GEWICHT_W_ZENSUS (trip-level expansion to Zensus 2022 counts; the",
        "#   stratum-internal GEWICHT_W must not be used across strata, ADR-0055).",
        "# Segments: %s (harmonised employment x life-phase groups, srv_plan_structure.GROUPS; no"
        % (list(D.SEGMENTS),),
        "#   age-band, sex or Kreis segmentation here -- the departure-time model's mapping cell",
        "#   is keyed on (purpose, group) only). Purposes: %s (unknown-purpose legs excluded)."
        % (list(SRV.PURPOSES),),
        "# Positions: 'first' = the person's seq-minimum trip; 'later' = every other trip; 'all'",
        "#   = both combined. Long format: one row per (segment, purpose, position, bin_15min),",
        "#   SPARSE over bin_15min -- only bins with non-zero weight in EITHER share column get a",
        "#   row; a (segment, purpose, position) with zero contributing legs is omitted entirely.",
        "#   bin_15min = floor(minute_of_day / %d), 0..%d (0-28 h). share_derounded and, "
        % (D.BIN_MINUTES, D.N_BINS - 1),
        "#   independently, share_as_reported each sum to 1.0 over a (segment, purpose, position)",
        "#   triple's rows; n_unweighted repeats the triple's unweighted leg count on every row.",
        "# De-rounding (issue #123 Task 1, braunschweig.calibration.reported_time_precision): each",
        "#   valid leg's reported departure minute is de-rounded ONCE by drawing an offset",
        "#   uniformly inside its reporting-precision cell -- +/- 7.5 min for a quarter-hour",
        "#   report (minute-of-hour a multiple of 15), +/- 2.5 min for a five-minute report",
        "#   (multiple of 5 but not 15), 0 for an exact (to-the-minute) report -- seeded with the",
        "#   fixed constant SRV_DEROUNDING_SEED = %d, so this table is reproducible from the raw"
        % D.SRV_DEROUNDING_SEED,
        "#   microdata. The SAME de-rounded value is reused for every (segment, purpose, position)",
        "#   the leg contributes to (its own segment AND 'all'; its own position AND 'all').",
        "# Guards inherited from srv_plan_structure.harmonise_srv (the extraction raises rather",
        "#   than adapting): every STICHTAG_WTAG in %s (pure Tuesday-Thursday delivery) and"
        % (list(SRV.EXPECTED_STICHTAG_WTAG),),
        "#   GEWICHT_P_WERKTAG == %s everywhere; the reconstructed per-person trip count is NOT"
        % SRV.EXPECTED_GEWICHT_P_WERKTAG,
        "#   re-checked here (that check lives in srv_plan_structure.person_level, not used by",
        "#   this table). A leg whose dep_min is NaN, negative, or not integer-valued cannot be",
        "#   de-rounded (reported_time_precision.deround_minutes_of_day raises on exactly those",
        "#   inputs) and is filtered out beforehand, counted below, never silently kept or",
        "#   defaulted. A bin computed outside [0, %d] (a departure beyond the 28 h window) is" % (D.N_BINS - 1),
        "#   CLIPPED into range and counted below, rather than dropped.",
        "# Headline (spec section 1.2, school-age children's first education leg, weekday):",
        headline_line,
        "# Harmonisation exclusions (srv_plan_structure.harmonise_srv, full raw microdata): "
        + ", ".join("%s=%s" % (k, v) for k, v in harmonisation_diagnostics.items()),
        "# Departure-time exclusions (this table, on the harmonised universe): "
        + ", ".join("%s=%s" % (k, v) for k, v in diagnostics.items()),
        "# Rows: %d (%d segments x %d purposes x %d positions, sparse over %d possible bins)."
        % (len(table), len(D.SEGMENTS), len(SRV.PURPOSES), len(D.POSITIONS), D.N_BINS),
    ]
    return lines


def _activity_duration_header(table: pd.DataFrame, diagnostics: dict,
                              harmonisation_diagnostics: dict, source_commit: str) -> list:
    lines = [
        "# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig scientific-use",
        "#   microdata (local-only, see srv2023_raw/README.md), generated by",
        "#   scripts/extract_srv_departure_times.py on %s." % dt.date.today().isoformat(),
        "# Code state: eqasim-bs %s, module braunschweig/calibration/srv_departure_times.py."
        % source_commit,
        "# Table: %s" % D.ACTIVITY_DURATION_TABLE,
        "# Role: the HOLD-OUT reference for the departure-time model (issue #123, ADR-0114) --",
        "#   activity durations are never mapped or touched by the model (spec",
        "#   2026-09-09-departure-time-srv-mapping-design.md section 2.2); this table only",
        "#   measures whether the model's realised durations still resemble SrV's after the",
        "#   first-departure mapping changes upstream chain timing.",
        "# Universe: %s only (see srv2023_departure_time_reference.csv for the reasoning)."
        % D.UNIVERSE,
        "# Weights: GEWICHT_W_ZENSUS (trip-level expansion to Zensus 2022 counts, ADR-0055).",
        "# Segments: %s. Purposes: %s (unknown-purpose destinations excluded); purpose is the"
        % (list(D.SEGMENTS), list(SRV.PURPOSES)),
        "#   CURRENT trip's destination purpose, i.e. the activity that trip leads into. Long",
        "#   format: one row per (segment, purpose, band), DENSE over band -- every (segment,",
        "#   purpose) with >= 1 included leg emits all %d of %s, most 0.0; share sums to 1.0 per"
        % (len(D.DURATION_BAND_LABELS), list(D.DURATION_BAND_LABELS)),
        "#   (segment, purpose); n_unweighted repeats the group's unweighted leg count.",
        "# Duration = the NEXT trip's dep_min minus the CURRENT trip's arr_min, within the same",
        "#   person. A trip with no following trip (the day's last trip) or a missing arr_min /",
        "#   following dep_min has no measurable duration; a measured duration outside",
        "#   [0, %g] h (srv_plan_structure.WORK_ACTIVITY_MAX_H, generalised here from work" % SRV.WORK_ACTIVITY_MAX_H,
        "#   activities to every purpose -- ASSUMPTION: an implausible duration is implausible",
        "#   regardless of activity type) is EXCLUDED and counted, never clipped.",
        "# Guards inherited from srv_plan_structure.harmonise_srv: every STICHTAG_WTAG in %s and"
        % (list(SRV.EXPECTED_STICHTAG_WTAG),),
        "#   GEWICHT_P_WERKTAG == %s everywhere (the extraction raises rather than adapting)."
        % SRV.EXPECTED_GEWICHT_P_WERKTAG,
        "# Harmonisation exclusions (srv_plan_structure.harmonise_srv, full raw microdata): "
        + ", ".join("%s=%s" % (k, v) for k, v in harmonisation_diagnostics.items()),
        "# Activity-duration exclusions (this table, on the harmonised universe): "
        + ", ".join("%s=%s" % (k, v) for k, v in diagnostics.items()),
        "# Rows: %d." % len(table),
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


def _check_departure_time_invariants(table: pd.DataFrame) -> None:
    """Invariants that would silently corrupt the reference if violated."""
    if list(table.columns) != D.DEPARTURE_TIME_COLUMNS:
        raise ValueError("unexpected columns %s" % list(table.columns))
    if set(table["universe"]) != {D.UNIVERSE}:
        raise ValueError("unexpected universe value(s) %s" % set(table["universe"]))
    bad_position = set(table["position"]) - set(D.POSITIONS)
    if bad_position:
        raise ValueError("unexpected position value(s) %s" % bad_position)
    if not table["bin_15min"].between(0, D.N_BINS - 1).all():
        raise ValueError("bin_15min outside [0, %d] present" % (D.N_BINS - 1))

    grouped = table.groupby(["segment", "purpose", "position"])
    for column in ("share_derounded", "share_as_reported"):
        totals = grouped[column].sum()
        off = totals[(totals - 1.0).abs() > 1e-6]
        if len(off):
            raise ValueError("%s does not sum to 1.0 for %d (segment, purpose, position) "
                             "triple(s): %s" % (column, len(off), off.head().to_dict()))


def _check_activity_duration_invariants(table: pd.DataFrame) -> None:
    if list(table.columns) != D.ACTIVITY_DURATION_COLUMNS:
        raise ValueError("unexpected columns %s" % list(table.columns))
    if set(table["universe"]) != {D.UNIVERSE}:
        raise ValueError("unexpected universe value(s) %s" % set(table["universe"]))
    bad_band = set(table["band"]) - set(D.DURATION_BAND_LABELS)
    if bad_band:
        raise ValueError("unexpected band value(s) %s" % bad_band)

    grouped = table.groupby(["segment", "purpose"])
    bad_band_sets = {key: sorted(g["band"]) for key, g in grouped
                     if sorted(g["band"]) != sorted(D.DURATION_BAND_LABELS)}
    if bad_band_sets:
        raise ValueError("%d (segment, purpose) group(s) do not carry the complete band set: %s"
                         % (len(bad_band_sets), dict(list(bad_band_sets.items())[:5])))
    totals = grouped["share"].sum()
    off = totals[(totals - 1.0).abs() > 1e-6]
    if len(off):
        raise ValueError("share does not sum to 1.0 for %d (segment, purpose) group(s): %s"
                         % (len(off), off.head().to_dict()))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default=str(RAW_DEFAULT),
                        help="SrV raw directory (local-only; must contain SrV2023_Personen.csv, "
                             "SrV2023_Wege.csv, SrV2023_Haushalte.csv)")
    parser.add_argument("--out-dir", default=str(OUT_DEFAULT),
                        help="Directory to write the committed tables into")
    parser.add_argument("--source-commit", default="unknown",
                        help="git commit hash of this worktree, recorded in the provenance "
                             "header for reproducibility (CLAUDE.md scientific-reproducibility "
                             "rule)")
    args = parser.parse_args(argv)

    raw = Path(args.raw)
    for name in ("SrV2023_Personen.csv", "SrV2023_Wege.csv", "SrV2023_Haushalte.csv"):
        if not (raw / name).exists():
            raise FileNotFoundError("SrV raw file missing: %s" % (raw / name))
    _require_columns(raw / "SrV2023_Personen.csv", SRV.PERSON_COLUMNS)
    _require_columns(raw / "SrV2023_Wege.csv", SRV.TRIP_COLUMNS)
    _require_columns(raw / "SrV2023_Haushalte.csv", SRV.HOUSEHOLD_COLUMNS)

    raw_persons = pd.read_csv(raw / "SrV2023_Personen.csv", usecols=SRV.PERSON_COLUMNS,
                              **CSV_READ_KWARGS)
    raw_trips = pd.read_csv(raw / "SrV2023_Wege.csv", usecols=SRV.TRIP_COLUMNS, **CSV_READ_KWARGS)
    households = pd.read_csv(raw / "SrV2023_Haushalte.csv", usecols=SRV.HOUSEHOLD_COLUMNS,
                             **CSV_READ_KWARGS)
    logger.info("read %d persons, %d trips, %d households", len(raw_persons), len(raw_trips),
                len(households))

    persons, trips = SRV.harmonise_srv(raw_persons, raw_trips, households)
    harmonisation_diagnostics = SRV.harmonisation_diagnostics(raw_persons, raw_trips, persons,
                                                              trips)
    logger.info("harmonisation diagnostics: %s", harmonisation_diagnostics)

    rng = np.random.RandomState(D.SRV_DEROUNDING_SEED)
    departure_time = D.departure_time_table(persons, trips, rng)
    activity_duration = D.activity_duration_table(persons, trips)

    _check_departure_time_invariants(departure_time)
    _check_activity_duration_invariants(activity_duration)

    departure_time_diagnostics = _departure_time_diagnostics(departure_time)
    activity_duration_diagnostics = _activity_duration_diagnostics(activity_duration)
    headline = _headline(departure_time)
    logger.info("headline (as reported, first education legs, %s): hour6=%.2f%% (expected 9.9%%, "
               "deviation %+.2f pp), hour7=%.2f%% (expected 82.5%%, deviation %+.2f pp), "
               "n_unweighted=%d", _HEADLINE_SEGMENT, headline["hour6_pct"], headline["deviation6_pp"],
               headline["hour7_pct"], headline["deviation7_pp"], headline["n_unweighted"])
    if not headline["matches_expectation"]:
        logger.warning("headline deviates from the spec 1.2 expectation by more than %.1f pp on "
                       "at least one hour -- reported as measured, the code is NOT adjusted to "
                       "match", _HEADLINE_DEVIATION_TOLERANCE_PP)

    out = Path(args.out_dir)
    _write(departure_time, out / D.DEPARTURE_TIME_TABLE,
          _departure_time_header(departure_time, departure_time_diagnostics, headline,
                                 harmonisation_diagnostics, args.source_commit))
    _write(activity_duration, out / D.ACTIVITY_DURATION_TABLE,
          _activity_duration_header(activity_duration, activity_duration_diagnostics,
                                    harmonisation_diagnostics, args.source_commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
