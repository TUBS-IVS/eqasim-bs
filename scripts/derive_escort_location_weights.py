"""Derive escort destination-type weights from SrV 2023 BS+RGB (issue #201).

SrV 2023 asks escort trips (V_ZWECK == 12, "Bringen oder Holen von Personen")
directly where the person was brought / picked up (V_ZWECK_BHOL). This script
computes the GEWICHT_W-weighted share per candidate category and writes the
pinned reference CSV consumed as the default for the ``escort_locations_*``
config keys.

Usage:
    python scripts/derive_escort_location_weights.py [--wege <path>] [--out <path>]

Input : eqasim-data/data/braunschweig/srv/srv2023_raw/SrV2023_Wege.csv
        (latin-1 encoded, GEWICHT_W uses a decimal comma)
Output: eqasim-data/data/braunschweig/srv/srv2023_escort_destination_types.csv

Category mapping (SrV2023_Datenkodierung_SciUse.xlsx, variable V_ZWECK_BHOL):
    edu_kindergarten  3 (Kinderkrippe/-garten)
    edu_school        4 (Grundschule), 5 (weiterfuehrende Schule), 7 (andere Bildung)
    edu_university    6 (Berufs-/Fach-/Hochschule)
    shop              8 (Einkauf taeglich), 9 (sonstiger Einkauf)
    leisure           13 (Kultur), 14 (Gaststaette), 16 (Erholung im Freien),
                      17 (Sportstaette), 18 (andere Freizeit)
    residential       15 (privater Besuch, fremde Wohnung)
    other             1 (Arbeitsplatz), 2 (anderer Dienstort), 10 (Behoerde/Arzt),
                      11 (Dienstleistung), 70 (Sonstiges)

ASSUMPTION (documented, spec 2026-07-24-escort-purpose-design.md §2.2): work-type
destinations (codes 1, 2) fold into "other" because work facilities are not part
of the secondary candidate universe. Codes -8/-10 are "nicht erhoben"/"unplausibel"
and count as invalid (coverage is reported, ~98.8 % weighted on the real data).
Cross-check: the French eqasim-france #495 aggregate (education 60 / leisure 13 /
home 12 / rest 14 / shop 1) is closely consistent; it is NOT used as input.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]
DEFAULT_WEGE_PATH = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_raw" / "SrV2023_Wege.csv"
DEFAULT_OUTPUT_PATH = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_escort_destination_types.csv"

ESCORT_V_ZWECK = 12

BHOL_CATEGORY = {
    3: "edu_kindergarten",
    4: "edu_school", 5: "edu_school", 7: "edu_school",
    6: "edu_university",
    8: "shop", 9: "shop",
    13: "leisure", 14: "leisure", 16: "leisure", 17: "leisure", 18: "leisure",
    15: "residential",
    1: "other", 2: "other", 10: "other", 11: "other", 70: "other",
}

CATEGORY_ORDER = (
    "edu_kindergarten", "edu_school", "edu_university",
    "other", "leisure", "residential", "shop",
)

# E_ZWECK_OBHOL is SrV's own derived purpose with Bringen/Holen split out; on
# valid escort legs it must (almost) always equal V_ZWECK_BHOL. Below this
# share the two derivations disagree and the mapping must be re-checked.
OBHOL_CONSISTENCY_WARN_SHARE = 0.99


DEFAULT_DISTANCE_FACTOR_MIN_OBS = 30

# W12 length bands (km upper edges) used for the SrV-vs-MiD coherence gate.
COHERENCE_BAND_EDGES = [0.0, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, float("inf")]


def weighted_median(values, weights) -> float:
    """Cumulative-weight median: smallest value whose cumulative weight reaches
    half the total. Raises on empty input or non-positive total weight."""
    v = np.asarray(values, dtype=float)
    w = np.asarray(weights, dtype=float)
    if len(v) == 0 or float(w.sum()) <= 0.0:
        raise ValueError("weighted_median: empty input or non-positive total weight.")
    order = np.argsort(v)
    v, w = v[order], w[order]
    cum = np.cumsum(w)
    return float(v[int(np.searchsorted(cum, 0.5 * cum[-1]))])


def derive_distance_factors(df: pd.DataFrame,
                            min_obs: int = DEFAULT_DISTANCE_FACTOR_MIN_OBS
                            ) -> tuple[pd.DataFrame, dict]:
    """Per-BHOL-category escort length factors from SrV GIS route lengths.

    Length source: ``GIS_LAENGE_GUELTIG`` -- SrV's valid-only COPY of the GIS route
    length in km (sentinel -7 where invalid; verified on the raw file 2026-08-11).
    Validity is therefore ``value > 0``, NOT a 0/1 flag. factor =
    weighted_median(length | category) / weighted_median(length | all escort legs),
    GEWICHT_W-weighted, restricted to V_ZWECK == 12 legs with a valid BHOL
    destination. Categories with 0 < n < ``min_obs`` unweighted legs get
    factor_applied = 1.0 (documented neutralization, logged by the runtime);
    categories with n == 0 are reported SEPARATELY (``absent_categories`` -- on real
    data an absent category is a data problem worth its own loud signal). Fails
    early on < 50% GIS coverage or an implausible unit scale (overall median
    outside 0.1..100 km). Rows come out in CATEGORY_ORDER so the pinned CSV column
    can be copied straight into DEFAULT_ESCORT_DISTANCE_FACTORS.
    """
    escort = df[df["V_ZWECK"] == ESCORT_V_ZWECK].copy()
    if len(escort) == 0:
        raise ValueError("[derive_distance_factors] no V_ZWECK == 12 legs found.")
    length_km = pd.to_numeric(escort["GIS_LAENGE_GUELTIG"], errors="coerce")
    valid_dest = escort["V_ZWECK_BHOL"] >= 1
    gis_ok = length_km.notna() & (length_km > 0)
    sub = escort[valid_dest & gis_ok].copy()
    sub["length_km"] = length_km[valid_dest & gis_ok]

    weight_all = escort.loc[valid_dest, "GEWICHT_W"].astype(float).sum()
    coverage = float(sub["GEWICHT_W"].astype(float).sum() / weight_all) if weight_all else 0.0
    if coverage < 0.5:
        raise ValueError(
            f"[derive_distance_factors] GIS length coverage {coverage:.1%} < 50% of "
            "escort legs; refusing to derive factors from a minority subsample."
        )

    overall_median = weighted_median(sub["length_km"], sub["GEWICHT_W"])
    if not (0.1 <= overall_median <= 100.0):
        raise ValueError(
            f"[derive_distance_factors] implausible overall median {overall_median} "
            "for km-scaled GIS_LAENGE_GUELTIG; check the unit of the input column."
        )

    sub["category"] = sub["V_ZWECK_BHOL"].astype(int).map(BHOL_CATEGORY)
    rows, neutralized, absent = [], [], []
    for category in CATEGORY_ORDER:
        cat = sub[sub["category"] == category]
        n = int(len(cat))
        if n == 0:
            median_km, factor = float("nan"), float("nan")
            applied = 1.0
            absent.append(category)
        else:
            median_km = weighted_median(cat["length_km"], cat["GEWICHT_W"])
            factor = median_km / overall_median
            applied = factor if n >= min_obs else 1.0
            if n < min_obs:
                neutralized.append(category)
        rows.append({
            "category": category, "n_legs_unweighted": n,
            "weighted_median_km": round(median_km, 4) if n else median_km,
            "factor": round(factor, 4) if n else factor,
            "factor_applied": round(applied, 4),
        })
    table = pd.DataFrame(rows,
        columns=["category", "n_legs_unweighted", "weighted_median_km",
                 "factor", "factor_applied"])
    stats = {
        "coverage_weighted": coverage,
        "overall_weighted_median_km": overall_median,
        "n_valid": int(len(sub)),
        "min_obs": int(min_obs),
        "neutralized_categories": neutralized,
        "absent_categories": absent,
    }
    return table, stats


def _sniff_separator(path) -> str:
    with open(path, "r", encoding="latin-1") as handle:
        first = handle.readline()
    return ";" if first.count(";") > first.count(",") else ","


def compute_length_coherence(df_srv: pd.DataFrame, mid_wege_path,
                             l1_threshold_pp: float = 25.0,
                             ratio_low: float = 0.67,
                             ratio_high: float = 1.5) -> dict:
    """Coherence gate (spec section 3): do SrV GIS lengths and MiD reported lengths
    scale similarly for the ACTIVE escort side? Compares SrV V_ZWECK == 12
    (GIS_LAENGE_GUELTIG > 0 as the valid-only km length, GEWICHT_W) against MiD
    W_ZWECK == 6 (wegkm_imp, W_GEW) on the nine W12 bands + overall weighted
    medians. Thresholds are ASSUMPTIONS (documented, configurable); PASS enables
    the SrV factors, FAIL pivots to the A1 layer aliases.
    """
    srv = df_srv[df_srv["V_ZWECK"] == ESCORT_V_ZWECK].copy()
    srv["length_km"] = pd.to_numeric(srv["GIS_LAENGE_GUELTIG"], errors="coerce")
    srv = srv[srv["length_km"].notna() & (srv["length_km"] > 0)]
    mid = pd.read_csv(mid_wege_path, sep=_sniff_separator(mid_wege_path),
                      usecols=["W_ZWECK", "W_GEW", "wegkm_imp"], low_memory=False)
    for column in ("W_ZWECK", "W_GEW", "wegkm_imp"):
        mid[column] = pd.to_numeric(mid[column], errors="coerce")
    mid = mid[(mid["W_ZWECK"] == 6) & mid["wegkm_imp"].notna()
              & (mid["wegkm_imp"] >= 0) & (mid["wegkm_imp"] < 1000.0)]

    def band_shares(values, weights):
        bins = pd.cut(np.asarray(values, dtype=float), COHERENCE_BAND_EDGES, right=False)
        share = pd.DataFrame({"b": bins, "w": np.asarray(weights, dtype=float)}) \
            .groupby("b", observed=False)["w"].sum()
        return (share / share.sum()).to_numpy()

    srv_shares = band_shares(srv["length_km"], srv["GEWICHT_W"])
    mid_shares = band_shares(mid["wegkm_imp"], mid["W_GEW"])
    band_l1_pp = float(np.abs(srv_shares - mid_shares).sum() * 100.0)
    srv_median = weighted_median(srv["length_km"], srv["GEWICHT_W"])
    mid_median = weighted_median(mid["wegkm_imp"], mid["W_GEW"])
    median_ratio = srv_median / mid_median
    return {
        "band_l1_pp": band_l1_pp,
        "median_ratio": median_ratio,
        "srv_median_km": srv_median,
        "mid_median_km": mid_median,
        "passed": bool(band_l1_pp <= l1_threshold_pp
                       and ratio_low <= median_ratio <= ratio_high),
    }


def derive_weights(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Weighted escort destination-category shares + coverage stats.

    Parameters
    ----------
    df:
        SrV Wege frame with numeric columns V_ZWECK, V_ZWECK_BHOL,
        E_ZWECK_OBHOL and float GEWICHT_W (decimal point, already parsed).

    Returns
    -------
    (table, stats):
        table: DataFrame [category, weight_share, n_legs_unweighted] in
        CATEGORY_ORDER, weight_share summing to 1.0 over valid legs.
        stats: dict with n_escort_legs, n_valid, coverage_weighted,
        obhol_consistency_share.
    """
    escort = df[df["V_ZWECK"] == ESCORT_V_ZWECK].copy()
    n_escort_legs = len(escort)
    if n_escort_legs == 0:
        raise ValueError(
            "[derive_escort_location_weights] no V_ZWECK == 12 legs found; "
            "wrong input file or broken V_ZWECK parsing."
        )

    weight = escort["GEWICHT_W"].astype(float)
    valid = escort["V_ZWECK_BHOL"] >= 1
    coverage_weighted = float(weight[valid].sum() / weight.sum())

    sub = escort[valid].copy()
    unmapped = sorted(set(sub["V_ZWECK_BHOL"].astype(int)) - set(BHOL_CATEGORY))
    if unmapped:
        raise ValueError(
            f"[derive_escort_location_weights] unmapped V_ZWECK_BHOL code(s) "
            f"{unmapped}; extend BHOL_CATEGORY explicitly (no silent bucket)."
        )
    sub["category"] = sub["V_ZWECK_BHOL"].astype(int).map(BHOL_CATEGORY)

    obhol_match = (sub["E_ZWECK_OBHOL"].astype(int) == sub["V_ZWECK_BHOL"].astype(int))
    obhol_consistency_share = float(obhol_match.mean()) if len(sub) else float("nan")
    if obhol_consistency_share < OBHOL_CONSISTENCY_WARN_SHARE:
        logger.warning(
            "[derive_escort_location_weights] E_ZWECK_OBHOL agrees with "
            "V_ZWECK_BHOL on only %.1f%% of valid escort legs (expected >= %.0f%%); "
            "re-check the category mapping against the codebook.",
            100.0 * obhol_consistency_share, 100.0 * OBHOL_CONSISTENCY_WARN_SHARE,
        )

    w_total = float(sub["GEWICHT_W"].astype(float).sum())
    if not len(sub) or w_total <= 0.0:
        raise ValueError(
            "[derive_escort_location_weights] no escort legs with a valid "
            "V_ZWECK_BHOL remain (or their GEWICHT_W sums to zero); cannot "
            "derive destination-type shares from zero valid observations."
        )
    rows = []
    for category in CATEGORY_ORDER:
        mask = sub["category"] == category
        rows.append({
            "category": category,
            "weight_share": float(sub.loc[mask, "GEWICHT_W"].astype(float).sum() / w_total),
            "n_legs_unweighted": int(mask.sum()),
        })
    table = pd.DataFrame(rows, columns=["category", "weight_share", "n_legs_unweighted"])

    stats = {
        "n_escort_legs": n_escort_legs,
        "n_valid": int(valid.sum()),
        "coverage_weighted": coverage_weighted,
        "obhol_consistency_share": obhol_consistency_share,
    }
    return table, stats


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wege",
        type=Path,
        default=DEFAULT_WEGE_PATH,
        help=f"Path to SrV2023_Wege.csv input (default: {DEFAULT_WEGE_PATH})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Path to output CSV (default: {DEFAULT_OUTPUT_PATH})",
    )
    parser.add_argument("--mid-wege-path", type=Path,
        default=REPO / "eqasim-data" / "data" / "braunschweig" / "popsim"
                / "mid2023_raw" / "MiD2023_Wege.csv",
        help="MiD Wege CSV for the SrV-vs-MiD length coherence gate.")
    parser.add_argument("--distance-factors-output", type=Path,
        default=REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
                / "srv2023_escort_distance_factors.csv")
    parser.add_argument("--distance-factor-min-obs", type=int,
        default=DEFAULT_DISTANCE_FACTOR_MIN_OBS)
    parser.add_argument("--coherence-l1-threshold-pp", type=float, default=25.0,
        help="Gate ASSUMPTION threshold: max W12-band L1 (pp).")
    parser.add_argument("--coherence-ratio-low", type=float, default=0.67)
    parser.add_argument("--coherence-ratio-high", type=float, default=1.5)
    args = parser.parse_args(argv)

    if not args.wege.exists():
        raise FileNotFoundError(
            f"[derive_escort_location_weights] input not found: {args.wege} "
            "(local-only SrV raw data; see eqasim-data README)."
        )
    df = pd.read_csv(
        args.wege, sep=None, engine="python", encoding="latin-1",
        usecols=["V_ZWECK", "V_ZWECK_BHOL", "E_ZWECK_OBHOL", "GEWICHT_W",
                 "GIS_LAENGE_GUELTIG"],
    )
    df["GEWICHT_W"] = (df["GEWICHT_W"].astype(str)
                       .str.replace(",", ".", regex=False).astype(float))
    # GIS_LAENGE_GUELTIG: valid-only GIS route length in km (decimal comma) or
    # the -7 sentinel -- convert to float; derive_distance_factors treats
    # "value > 0" as valid (see its docstring).
    df["GIS_LAENGE_GUELTIG"] = pd.to_numeric(
        df["GIS_LAENGE_GUELTIG"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce")
    table, stats = derive_weights(df)

    logger.info(
        "[derive_escort_location_weights] escort legs %d, valid BHOL %d, "
        "weighted coverage %.1f%%, OBHOL consistency %.1f%%",
        stats["n_escort_legs"], stats["n_valid"],
        100.0 * stats["coverage_weighted"], 100.0 * stats["obhol_consistency_share"],
    )
    for _, row in table.iterrows():
        logger.info("  %-18s %.3f (n=%d)", row["category"], row["weight_share"],
                    row["n_legs_unweighted"])

    header = (
        "# Source: SrV 2023 Braunschweig+RGB SciUse (TU Dresden), Wege table, "
        "V_ZWECK==12 legs with valid V_ZWECK_BHOL, GEWICHT_W-weighted.\n"
        f"# Generated by scripts/derive_escort_location_weights.py; "
        f"coverage_weighted={stats['coverage_weighted']:.4f}, "
        f"n_valid={stats['n_valid']}/{stats['n_escort_legs']}, "
        f"obhol_consistency={stats['obhol_consistency_share']:.4f}.\n"
        "# Categories: edu_* from BHOL codes 3-7; shop 8/9; leisure 13/14/16/17/18; "
        "residential 15; other 1/2/10/11/70 (work folded into other: ASSUMPTION, "
        "no work facilities in the secondary candidate universe).\n"
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as handle:
        handle.write(header)
        table.to_csv(handle, index=False)
    logger.info("[derive_escort_location_weights] wrote %s", args.out)

    factors_table, factor_stats = derive_distance_factors(
        df, min_obs=args.distance_factor_min_obs)
    gate = compute_length_coherence(
        df, args.mid_wege_path,
        l1_threshold_pp=args.coherence_l1_threshold_pp,
        ratio_low=args.coherence_ratio_low,
        ratio_high=args.coherence_ratio_high)
    gate_line = ("PASS" if gate["passed"] else "FAIL")
    factors_header = (
        "# Source: SrV 2023 Braunschweig+RGB SciUse (TU Dresden), Wege table, V_ZWECK==12,\n"
        "# length = GIS_LAENGE_GUELTIG (valid-only GIS route length, km; -7 sentinel = invalid;\n"
        "# validity = value > 0), GEWICHT_W-weighted.\n"
        f"# factor = category weighted median / overall weighted median "
        f"({factor_stats['overall_weighted_median_km']:.3f} km); "
        f"coverage_weighted={factor_stats['coverage_weighted']:.4f}; "
        f"n_valid={factor_stats['n_valid']}.\n"
        f"# min_obs={factor_stats['min_obs']}: thin categories get factor_applied=1.0 "
        f"(neutralized: {', '.join(factor_stats['neutralized_categories']) or 'none'}; "
        f"absent: {', '.join(factor_stats['absent_categories']) or 'none'}).\n"
        f"# Coherence gate vs MiD W_ZWECK==6 wegkm_imp (ASSUMPTION thresholds "
        f"L1<={args.coherence_l1_threshold_pp}pp, ratio in "
        f"[{args.coherence_ratio_low},{args.coherence_ratio_high}]): {gate_line} -- "
        f"band_l1_pp={gate['band_l1_pp']:.2f}, median_ratio={gate['median_ratio']:.3f} "
        f"(SrV {gate['srv_median_km']:.2f} km / MiD {gate['mid_median_km']:.2f} km).\n"
        "# Generated by scripts/derive_escort_location_weights.py; regenerate there, never edit.\n"
    )
    with open(args.distance_factors_output, "w", encoding="utf-8", newline="") as handle:
        handle.write(factors_header)
        factors_table.to_csv(handle, index=False)
    print(f"[derive_escort_location_weights] distance factors -> "
          f"{args.distance_factors_output} (gate: {gate_line})")
    if not gate["passed"]:
        print("[derive_escort_location_weights] WARNING: coherence gate FAILED -- "
              "do not ship the SrV factors; pivot to A1 (spec section 3).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
