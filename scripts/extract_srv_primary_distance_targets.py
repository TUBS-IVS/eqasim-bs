"""Extract committed SrV 2023 distance-distribution targets for the primary activities.

Reads the LOCAL-ONLY SrV 2023 "Braunschweig und RGB" scientific-use microdata (trips,
persons, households; cp1252, semicolon, decimal comma) and writes four small aggregate
tables to ``--out-dir`` (default ``eqasim-data/data/braunschweig/srv``):

    srv2023_commute_distance_by_kreis.csv
    srv2023_education_distance_by_kreis_level.csv
    srv2023_commute_distance_quantiles_by_kreis.csv
    srv2023_commute_distance_sensitivity_by_kreis.csv   (SENSITIVITY variants, NOT a target;
        addendum Task 15, item 3: quantifies the polygon-external-destination and
        GIS-invalid-tail caveats of the commute table as measured band-share variants)

Definitions live in ``braunschweig.calibration.srv_distance_targets`` (person-level
observation, GIS-routed length only, GEWICHT_W_ZENSUS, shrinkage n/(n+k), Wolfsburg =
RS7-72 pool). Every table carries a provenance header.

Usage (eqasim env, from a worktree; point --raw at the main checkout's raw directory):
    python scripts/extract_srv_primary_distance_targets.py \
        --raw C:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/data/braunschweig/srv/srv2023_raw \
        --out-dir eqasim-data/data/braunschweig/srv

Pass ``--bias-check`` (ruling R25) to instead compute and log the GIS-validity bias check
(``braunschweig.calibration.srv_distance_targets.gis_validity_bias_check``) for home-based
work candidate trips and exit 0 WITHOUT writing the three tables -- the numbers behind
ADR-0102 Assumption 2 are reproduced this way, not hard-coded in this script.
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

from braunschweig.calibration import srv_distance_targets as T  # noqa: E402

RAW_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_raw"
OUT_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
CSV_READ_KWARGS = dict(sep=";", decimal=",", encoding="cp1252", low_memory=False)
TRIP_COLUMNS = ["HHNR", "PNR", "WNR", "V_ZWECK", "E_START_ZWECK", "V_START_LAGE", "V_ZIEL_LAGE",
                "V_START_AGS", "V_ZIEL_AGS", "V_LAENGE", "GIS_LAENGE", "GIS_LAENGE_GUELTIG",
                "GEWICHT_W_ZENSUS", "REGIOSTAR7", "STICHTAG_WTAG"]

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("extract_srv_primary_distance_targets")

# Units of the `select_person_observations` log-dict keys, written once into the
# "Exclusions:" header line (IMPORTANT 2 -- the key names alone do not say whether a count
# is over TRIPS or PERSONS, and mixing them silently invites a wrong ratio downstream).
_EXCLUSIONS_UNITS_NOTE = (
    "(units: n_candidate_trips, n_pool_weight_negative, n_pool_over_cap, "
    "n_excluded_gis_invalid are candidate TRIPS; n_persons_dropped_gis_invalid, "
    "n_excluded_weight_negative, n_excluded_over_cap, n_excluded_no_kreis, n_missing_* and "
    "n_persons_selected are PERSONS; share_start_ags_equals_household_ags is a share over "
    "persons with a known trip AGS)"
)


def _gis_invalid_assumption_note(log: dict, include_100_plus_sentence: bool) -> list[str]:
    """ASSUMPTION note shared by the commute and education table headers (ruling R13/R25).

    The GIS-invalid TRIP rate is read from ``log`` (THIS table's own
    ``select_person_observations`` result) rather than hard-coded, so work and education --
    whose rates differ (16.9% vs 15.7%) -- each state their own number instead of a shared
    "~17%" approximation. ``include_100_plus_sentence`` is True only for the commute (work)
    table, the only one with a 100_plus band at all (Ruling R24 review comment).
    """
    n_candidate = log["n_candidate_trips"]
    n_invalid_trips = log["n_excluded_gis_invalid"]
    rate = 100.0 * n_invalid_trips / n_candidate if n_candidate else 0.0
    lines = [
        f"ASSUMPTION: GIS-invalid trips ({rate:.1f}% of {n_candidate} candidate trips,",
        f"  {n_invalid_trips} trips) are treated as missing at random with respect to",
        "  distance. Bias check: see ADR-0102 Assumption 2 (reproducible with",
        "  scripts/extract_srv_primary_distance_targets.py --bias-check). A person drops out",
        "  when EVERY candidate trip of that person (in either direction) is GIS-invalid;",
        "  persons observed in only one direction drop out when that trip is invalid",
        f"  ({log['n_persons_dropped_gis_invalid']} persons here; n_excluded_gis_invalid above",
        "  is the TRIP-level count of both directions, see the Exclusions units note).",
    ]
    if include_100_plus_sentence:
        lines += [
            "  The commute band 100_plus is 0.0 in every row: no GIS-valid home-based trip",
            "  >= 100 km exists in this delivery, so that band cannot be calibrated from SrV.",
        ]
    return lines


def _header(table_name: str, universe: str, extra: list[str], logs: dict, *,
            quantile_table: bool = False, n_bootstrap: int | None = None, seed: int | None = None) -> list[str]:
    """Build the provenance header shared by all three tables.

    ``quantile_table`` selects the shrinkage sentence (the quantile table has no
    ``share_*_shrunk`` columns, it has ``distance_km_euclid_shrunk``). ``n_bootstrap`` /
    ``seed`` gate the "Noise floor" block on/off: the commute and education tables both
    carry ``emd_noise_95_*`` columns and must both state the bootstrap parameters that
    make that column reproducible (IMPORTANT 1); the quantile table has no such column
    and omits the block entirely (Minor 6) rather than describing a column it lacks.
    """
    lines = [
        "# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig scientific-use",
        "#   microdata (local-only, see srv2023_raw/README.md), generated by",
        f"#   scripts/extract_srv_primary_distance_targets.py on {dt.date.today().isoformat()}.",
        f"# Table: {table_name}",
        f"# Universe: {universe}",
        "# Observation unit: PERSON -- first home->purpose trip (V_START_LAGE == 1), else first",
        "#   purpose->home trip (V_ZIEL_LAGE == 1); business trips (V_ZWECK 2) and 'andere",
        "#   Bildungseinrichtung' (V_ZWECK 7) excluded. Reporting days are Tue-Thu only.",
        "# Distance: GIS_LAENGE (routed km) where GIS_LAENGE_GUELTIG > 0; GIS-invalid trips fall",
        "#   back to the other direction (R5); a negative weight or an over-cap distance on the",
        "#   SELECTED trip excludes the person (R6, no fallback -- see the module docstring).",
        "# Weight: GEWICHT_W_ZENSUS >= 0 (expansion to Zensus 2022; the stratum-internal",
        "#   GEWICHT_W must not be used across strata; GEWICHT_W_WERKTAG is undefined here).",
        "# Kreis: first 5 digits of the household AGS. Wolfsburg (03103) is NOT surveyed: its row",
        "#   is the RegioStaR-7 type-72 pool (Braunschweig + Salzgitter), source = proxy_rs7_72",
        "#   -- an ASSUMPTION recorded in the reference ADR. Per R11, the Wolfsburg proxy row",
        "#   carries the RS7-72 pool's RAW shares/quantiles in BOTH the raw and the shrunk",
        "#   columns (a pool is not shrunk further).",
    ]
    if quantile_table:
        lines += [
            "# Shrinkage: distance_km_euclid_shrunk = n/(n+k)-mix of the Kreis and its pool",
            "#   quantile function, quantile-wise (pool = dominant RS7 type, itself shrunk",
            f"#   toward ZGB), k = {T.DEFAULT_PRIOR_STRENGTH:.0f} persons.",
        ]
    else:
        lines += [
            "# Shrinkage: share_*_shrunk = n/(n+k) * Kreis + k/(n+k) * pool, pool = dominant RS7 type",
            f"#   (itself shrunk toward ZGB), k = {T.DEFAULT_PRIOR_STRENGTH:.0f} persons.",
        ]
    lines += [
        "# Coverage caveat: stratified PSU design over ~44 selected municipalities; per-Kreis rows",
        "#   represent the covered municipalities (ASSUMPTION-grade for the full Kreis).",
    ]
    if n_bootstrap is not None:
        lines += [
            "# Noise floor: emd_noise_95_* is the 95th percentile of the bootstrap EMD",
            f"#   (n_bootstrap={n_bootstrap}, seed={seed}), normalised to [0, 1] exactly like",
            "#   braunschweig.calibration.metrics.emd_on_bands (same formula, re-implemented",
            "#   locally to avoid a pipeline import).",
        ]
    lines += [f"# {line}" for line in extra]
    lines.append("# Exclusions: " + ", ".join(f"{k}={v}" for k, v in logs.items()) + " " + _EXCLUSIONS_UNITS_NOTE)
    return lines


def _sensitivity_header(log_work_gis: dict, log_work_fallback: dict) -> list[str]:
    """Provenance header for the SENSITIVITY table (addendum Task 15, item 3) -- NOT a
    calibration target. Documents each variant precisely and carries BOTH selection-log
    dicts (the GIS-only selection that feeds ``inter_zgb``, and the
    ``gis_or_self_reported`` selection that feeds ``all_gis_fallback`` /
    ``inter_gis_fallback``) so exclusion counts for either underlying selection can be
    audited without re-running the extraction.
    """
    lines = [
        "# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig scientific-use",
        "#   microdata (local-only, see srv2023_raw/README.md), generated by",
        f"#   scripts/extract_srv_primary_distance_targets.py on {dt.date.today().isoformat()}.",
        f"# Table: {T.SENSITIVITY_TABLE}",
        "# THIS TABLE IS A SENSITIVITY MEASUREMENT, NOT A CALIBRATION TARGET (addendum Task 15,",
        "#   item 3). It quantifies how much two known caveats of",
        "#   srv2023_commute_distance_by_kreis.csv move the reference if resolved differently; the",
        "#   committed TARGET stays that file's all/inter/intra GIS-only definition.",
        "# Universe: persons with a home<->own-workplace trip (V_ZWECK 1), same person/trip/weight",
        "#   selection rule as the commute table (see that table's own header for the definitions).",
        "# Variants (one full kreis/rs7/zgb row set each, column `variant`):",
        "#   inter_zgb: from the GIS-only selection, inter-Gemeinde persons (destination Gemeinde",
        "#     != home Gemeinde) whose destination Kreis (first 5 digits of the trip's",
        "#     away-direction AGS, column dest_ags8) is one of the 8 ZGB Kreise -- i.e.",
        "#     inter-Gemeinde commutes that stay WITHIN the surveyed ZGB polygon. Compare against",
        "#     the commute table's `inter` scope: the gap between the two measures how much",
        "#     `inter` is diluted by destinations leaving the polygon (the 'polygon-external",
        "#     destinations' caveat). Persons with an unknown destination AGS are excluded from",
        "#     both the numerator and denominator of this variant (see the run log).",
        "#   all_gis_fallback: every person of the gis_or_self_reported selection (distance =",
        "#     GIS_LAENGE where GIS_LAENGE_GUELTIG > 0, else the self-reported V_LAENGE where",
        "#     V_LAENGE > 0; a trip with neither is excluded, n_excluded_no_length below).",
        "#     Compare against the commute table's `all` scope: the gap measures how much",
        "#     recovering the GIS-invalid tail via the self-reported length would move the target",
        "#     (the 'GIS-invalid tail' caveat).",
        "#   inter_gis_fallback: the gis_or_self_reported selection restricted to inter-Gemeinde",
        "#     persons; compare against the commute table's `inter` scope.",
        f"# Bands (routed km): {list(T.WORK_BAND_EDGES_KM)} -> labels {list(T.WORK_BAND_LABELS)}.",
        "# Shrinkage: share_shrunk = n/(n+k) * Kreis + k/(n+k) * pool, pool = dominant RS7 type",
        f"#   (itself shrunk toward ZGB), k = {T.DEFAULT_PRIOR_STRENGTH:.0f} persons -- computed",
        "#   SEPARATELY per variant (a Kreis's dominant RS7 type can differ between the GIS-only",
        "#   and the gis_or_self_reported selection).",
        "# Noise floor: emd_noise_95 is the 95th percentile of the bootstrap EMD (n_bootstrap=500,",
        "#   seed=0), normalised to [0, 1] exactly like braunschweig.calibration.metrics.emd_on_bands",
        "#   (same formula, re-implemented locally to avoid a pipeline import).",
        "# Exclusions, GIS-only selection (feeds inter_zgb; this selection has no self-reported",
        "#   fallback at all, so n_persons_self_reported_distance is omitted below by construction): "
        + ", ".join(f"{k}={v}" for k, v in log_work_gis.items()),
        "# Exclusions, gis_or_self_reported selection (feeds all_gis_fallback / inter_gis_fallback;",
        "#   n_persons_self_reported_distance / share_persons_self_reported_distance is the",
        "#   primary(GIS)-vs-fallback(self-reported) rate, CLAUDE.md no-silent-fallback rule): "
        + ", ".join(f"{k}={v}" for k, v in log_work_fallback.items()),
        "# " + _EXCLUSIONS_UNITS_NOTE + " (both Exclusions lines above use this same convention;",
        "#   n_excluded_no_length / n_persons_dropped_no_length are the gis_or_self_reported",
        "#   analogues of n_excluded_gis_invalid / n_persons_dropped_gis_invalid).",
    ]
    return lines


def _write(df: pd.DataFrame, path: Path, header: list[str]) -> None:
    """Write one provenance-headed CSV.

    ``lineterminator="\\n"`` keeps the data rows LF-only regardless of platform (Minor 4);
    ``float_format="%.10g"`` keeps floats readable while staying lossless at the precision
    the shares/quantiles/distances carry (Minor 12; the `open(..., newline="")` above the
    header loop already writes the header lines with a literal ``\\n``, matching the CSV
    body so the whole file is uniformly LF).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for line in header:
            fh.write(line + "\n")
        df.to_csv(fh, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s (%d rows)", path, len(df))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default=str(RAW_DEFAULT),
                         help="SrV raw directory (local-only; must contain SrV2023_Wege.csv, "
                              "SrV2023_Personen.csv, SrV2023_Haushalte.csv)")
    parser.add_argument("--out-dir", default=str(OUT_DEFAULT),
                         help="Directory to write the three committed tables into")
    parser.add_argument("--prior-strength", type=float, default=T.DEFAULT_PRIOR_STRENGTH,
                         help="Shrinkage strength k in n/(n+k) toward the pool, in persons (>= 0)")
    parser.add_argument("--max-distance-km", type=float, default=T.DEFAULT_MAX_DISTANCE_KM,
                         help="Plausibility cap on GIS-routed trip distance in km (> 0); a selected "
                              "trip above the cap excludes that person")
    parser.add_argument("--detour-factor", type=float, default=T.DEFAULT_DETOUR_FACTOR,
                         help="Routed-to-euclidean distance ratio used for the quantile table (> 0)")
    parser.add_argument("--n-bootstrap", type=int, default=500,
                         help="Number of bootstrap resamples for the EMD noise floor (>= 1)")
    parser.add_argument("--seed", type=int, default=0,
                         help="Random seed for the bootstrap noise floor (reproducibility)")
    parser.add_argument("--bias-check", action="store_true",
                         help="Ruling R25: compute and log the GIS-validity bias check "
                              "(braunschweig.calibration.srv_distance_targets.gis_validity_bias_check) "
                              "for home-based work candidate trips, then exit 0 WITHOUT writing "
                              "the three committed target tables")
    args = parser.parse_args(argv)

    if args.prior_strength < 0:
        parser.error("--prior-strength must be >= 0 (got %r)" % args.prior_strength)
    if args.n_bootstrap < 1:
        parser.error("--n-bootstrap must be >= 1 (got %r)" % args.n_bootstrap)
    if args.detour_factor <= 0:
        parser.error("--detour-factor must be > 0 (got %r)" % args.detour_factor)
    if args.max_distance_km <= 0:
        parser.error("--max-distance-km must be > 0 (got %r)" % args.max_distance_km)

    raw = Path(args.raw)
    for name in ("SrV2023_Wege.csv", "SrV2023_Personen.csv", "SrV2023_Haushalte.csv"):
        if not (raw / name).exists():
            raise FileNotFoundError(f"SrV raw file missing: {raw / name}")
    trips = pd.read_csv(raw / "SrV2023_Wege.csv", usecols=TRIP_COLUMNS, **CSV_READ_KWARGS)
    persons = pd.read_csv(raw / "SrV2023_Personen.csv", usecols=["HHNR", "PNR", "V_ALTER"], **CSV_READ_KWARGS)
    households = pd.read_csv(raw / "SrV2023_Haushalte.csv", usecols=["HHNR", "AGS"], **CSV_READ_KWARGS)
    weekdays = sorted(trips["STICHTAG_WTAG"].dropna().unique().tolist())
    if not set(weekdays) <= {2, 3, 4}:
        raise ValueError(f"Expected Tue-Thu reporting days only, found STICHTAG_WTAG codes {weekdays}")

    if args.bias_check:
        # Ruling R25: a pure diagnostic run -- logs the result and exits without touching
        # the committed tables, so it can be re-run at any time to check whether ADR-0102
        # Assumption 2's missing-at-random reasoning still holds on a refreshed extract.
        T.gis_validity_bias_check(trips)
        return 0

    obs_work, log_work = T.select_person_observations(trips, persons, households, (T.PURPOSE_WORK,),
                                                      max_distance_km=args.max_distance_km)
    obs_edu, log_edu = T.select_person_observations(trips, persons, households, T.EDUCATION_PURPOSES,
                                                    max_distance_km=args.max_distance_km)
    expected = sorted(k for k in T.ZGB_KREISE if k != T.WOLFSBURG_KREIS)
    found_work = sorted(set(obs_work["kreis"]))
    if found_work != expected:
        raise ValueError(f"Expected the 7 surveyed ZGB Kreise {expected}, found {found_work} (work)")
    found_edu = sorted(set(obs_edu["kreis"]))
    if found_edu != expected:
        raise ValueError(f"Expected the 7 surveyed ZGB Kreise {expected}, found {found_edu} (education)")

    out = Path(args.out_dir)
    commute = T.build_commute_table(obs_work, prior_strength=args.prior_strength,
                                    n_bootstrap=args.n_bootstrap, seed=args.seed)
    _write(commute, out / T.COMMUTE_TABLE, _header(
        T.COMMUTE_TABLE, "persons with a home<->own-workplace trip (V_ZWECK 1)",
        [f"Bands (routed km): {list(T.WORK_BAND_EDGES_KM)} -> labels {list(T.WORK_BAND_LABELS)}; scopes all / inter / intra",
         "(intra = start AGS == destination AGS, i.e. same Gemeinde)."]
        + _gis_invalid_assumption_note(log_work, include_100_plus_sentence=True),
        log_work, n_bootstrap=args.n_bootstrap, seed=args.seed))
    education = T.build_education_table(obs_edu, prior_strength=args.prior_strength,
                                        n_bootstrap=args.n_bootstrap, seed=args.seed)
    _write(education, out / T.EDUCATION_TABLE, _header(
        T.EDUCATION_TABLE, "persons with a home<->education trip (V_ZWECK 3,4,5,6)",
        [f"Bands (routed km): {list(T.EDUCATION_BAND_EDGES_KM)} -> labels {list(T.EDUCATION_BAND_LABELS)}.",
         "Levels follow the MODEL age banding (R4): Kita (V_ZWECK 3) counts only at age 0-6 and",
         "Grundschule (4) only at age 5-10 (model age band +/- 1 year); sekundar_1 = code 5 age",
         "10-15; upper_secondary = code 5 or 6 age 16-19 (oberstufe + bbs pooled); university =",
         "code 6 age 20+. comparable=False rows (oberstufe, bbs) are the SrV-only split of",
         "upper_secondary and are descriptive only; (purpose, age) combinations the model cannot",
         "produce map to no level and are excluded (rate logged, see the script's run log).",
         "Cells with n_persons < 3 carry a degenerate emd_noise_95 of 0.0 (a single observation",
         "  resamples to itself); their shrunk shares collapse to the pool, so they do not steer",
         "  the Kreis targets."]
        + _gis_invalid_assumption_note(log_edu, include_100_plus_sentence=False),
        log_edu, n_bootstrap=args.n_bootstrap, seed=args.seed))
    quantiles = T.build_quantile_table(obs_work, detour_factor=args.detour_factor,
                                       prior_strength=args.prior_strength)
    _write(quantiles, out / T.QUANTILE_TABLE, _header(
        T.QUANTILE_TABLE, "persons with a home<->own-workplace trip (V_ZWECK 1)",
        [f"distance_km_euclid_* = GIS routed km / {args.detour_factor} (euclidean-equivalent, the unit of",
         "synthesis.population.spatial.commute_distance targets). Percentiles 1..99, long format."],
        log_work, quantile_table=True))

    # Task 15 (addendum, item 3, reference side): a SENSITIVITY measurement, not a target --
    # obs_work_fallback reruns the SAME work selection with the self-reported length as a
    # fallback for GIS-invalid trips (distance_source="gis_or_self_reported"), so its gap
    # against obs_work (GIS-only) quantifies the "GIS-invalid tail" caveat; the "inter_zgb"
    # variant reuses obs_work (GIS-only) filtered by destination Kreis.
    obs_work_fallback, log_work_fallback = T.select_person_observations(
        trips, persons, households, (T.PURPOSE_WORK,), max_distance_km=args.max_distance_km,
        distance_source="gis_or_self_reported")
    found_work_fallback = sorted(set(obs_work_fallback["kreis"]))
    if found_work_fallback != expected:
        raise ValueError(
            f"Expected the 7 surveyed ZGB Kreise {expected}, found {found_work_fallback} "
            "(work, gis_or_self_reported)")
    sensitivity = T.build_commute_sensitivity_table(
        obs_work, obs_work_fallback, prior_strength=args.prior_strength,
        n_bootstrap=args.n_bootstrap, seed=args.seed)
    _write(sensitivity, out / T.SENSITIVITY_TABLE,
          _sensitivity_header(log_work, log_work_fallback))
    return 0


if __name__ == "__main__":
    sys.exit(main())
