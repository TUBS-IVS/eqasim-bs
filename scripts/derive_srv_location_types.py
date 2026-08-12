"""Derive SrV-grounded location-type probabilities for leisure/other secondary
activities (issue #262).

SrV 2023 records the purpose of every leg via ``V_ZWECK``. For the leisure and
"other" (errand) secondary-activity families this script computes, per
(purpose, mode, euclidean-distance band) cell, the GEWICHT_W-weighted share of
each finer category -- e.g. how much of a "leisure" leg by car in the 1-1.5 km
band is a leisure_outdoor destination vs. a leisure_gastronomy one. These
per-cell probabilities let the location-choice model draw a location TYPE
before it draws a candidate location, refining today's flat category split.

Usage:
    python scripts/derive_srv_location_types.py [--wege <path>]
        [--out-probs <path>] [--out-shares <path>] [--min-obs 30]

Input : eqasim-data/data/braunschweig/srv/srv2023_raw/SrV2023_Wege.csv
        (latin-1 encoded, GEWICHT_W and GIS_LAENGE_GUELTIG use a decimal comma)
Output: eqasim-data/data/braunschweig/srv/srv2023_location_type_by_distance.csv
        eqasim-data/data/braunschweig/srv/srv2023_secondary_type_shares.csv

Category mapping (SrV2023_Datenkodierung_SciUse.xlsx, variable V_ZWECK):
    leisure_culture              13 (Kultureinrichtung)
    leisure_gastronomy           14 (Gaststaette)
    leisure_visit                15 (privater Besuch, fremde Wohnung)
    leisure_outdoor              16 (Erholung im Freien)
    leisure_sports               17 (Sportstaette)
    leisure_misc                 18 (andere Freizeit)
    errand_authority_medical     10 (Behoerde/Arzt)
    errand_service                11 (Dienstleistung)
    other_misc                   70 (Sonstiges)
    shop_daily (reference only)    8 (Einkauf taeglich)
    shop_non_daily (reference)     9 (sonstiger Einkauf)

ASSUMPTION: routed leg length is not observed directly on SrV; the GIS route
length (``GIS_LAENGE_GUELTIG``) is converted to a euclidean-equivalent
distance via a fixed detour factor (``DETOUR_FACTOR = 1.3``), the same factor
already used for the MiD-derived distance layers (see
scripts/derive_escort_location_weights.py and its distance-factor docstring
for the analogous SrV-side length handling). V_ZWECK codes outside
``CATEGORY_BY_V_ZWECK`` (e.g. work, education, escort -- handled by
derive_escort_location_weights.py) are simply out of scope for this script
and excluded before any category is assigned. Within the in-scope legs, an
unmapped mode (``E_HVM_5`` sentinel -7, "nicht erhoben") or an invalid GIS
length (sentinel -7, "wert ungueltig", or any non-positive value) is excluded
and EXPLICITLY counted (``n_excluded_invalid_mode``,
``n_excluded_invalid_length``) -- never silently folded into a category.
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
DEFAULT_PROBS_OUTPUT_PATH = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_location_type_by_distance.csv"
DEFAULT_SHARES_OUTPUT_PATH = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_secondary_type_shares.csv"

DEFAULT_MIN_OBS = 30

# routed -> euclidean-equivalent detour factor; same ASSUMPTION as the MiD
# distance layers (documented above and in derive_escort_location_weights.py).
DETOUR_FACTOR = 1.3

# Euclidean-distance band edges (km) used to bucket cells for the type
# probabilities. Upper-open bands: [0, 0.5), [0.5, 1.0), ..., [8.0, inf).
BAND_EDGES_EUCLID_KM = (0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 8.0, float("inf"))

MODE_BY_E_HVM_5 = {1: "walk", 2: "bicycle", 3: "car", 4: "car_passenger", 5: "pt"}

# (purpose, category) per V_ZWECK code -- the leisure/other secondary-activity
# families this script covers. Codes outside this map (including 12, escort,
# which is handled by derive_escort_location_weights.py) are out of scope and
# ignored, not raised on (scope filter first; see task-1 brief clarification).
CATEGORY_BY_V_ZWECK = {
    13: ("leisure", "leisure_culture"),
    14: ("leisure", "leisure_gastronomy"),
    15: ("leisure", "leisure_visit"),
    16: ("leisure", "leisure_outdoor"),
    17: ("leisure", "leisure_sports"),
    18: ("leisure", "leisure_misc"),
    10: ("other", "errand_authority_medical"),
    11: ("other", "errand_service"),
    70: ("other", "other_misc"),
}

# Validation-only reference categories (the #242 contribution): shop is
# already modelled elsewhere, these shares are for cross-checking only.
SHOP_SHARE_CODES = {8: "shop_daily", 9: "shop_non_daily"}


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


def _band_bounds(band_index: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Lower/upper edge (km) for each band index into BAND_EDGES_EUCLID_KM."""
    edges = np.asarray(BAND_EDGES_EUCLID_KM, dtype=float)
    lower = edges[band_index]
    upper = edges[band_index + 1]
    return lower, upper


def _filter_in_scope(df: pd.DataFrame, category_map: dict) -> tuple[pd.DataFrame, int]:
    """Filter to in-scope V_ZWECK codes and map (purpose, category). Deliberately
    does NOT touch mode or length: a leg's PURPOSE category is known regardless
    of whether its mode or its GIS length happen to be valid, so any weighted
    share computed over categories (the marginal row in
    ``derive_type_probabilities`` and every row of ``derive_type_shares``) must
    use this full in-scope universe, not a subset narrowed by fields it does
    not need. Narrowing it would silently bias the category mix towards
    whichever categories happen to have better mode/length reporting -- exactly
    the kind of unannounced fallback CLAUDE.md's fallback-transparency rule
    forbids. Mode and length filtering is applied separately, only where a
    mode or a distance is actually required (the per-cell band split; the
    weighted-median distance columns).

    Returns the scoped frame (columns: purpose, category, GEWICHT_W as float,
    plus the original columns) and n_in_scope.
    """
    in_scope = df["V_ZWECK"].isin(category_map)
    scoped = df[in_scope].copy()
    n_in_scope = int(len(scoped))
    if n_in_scope == 0:
        raise ValueError(
            "[derive_srv_location_types] no legs in scope for the given "
            "V_ZWECK category map; wrong input file or broken V_ZWECK parsing."
        )
    purpose_category = scoped["V_ZWECK"].map(category_map)
    scoped["purpose"] = purpose_category.map(lambda pc: pc[0])
    scoped["category"] = purpose_category.map(lambda pc: pc[1])
    scoped["GEWICHT_W"] = scoped["GEWICHT_W"].astype(float)
    return scoped, n_in_scope


def _apply_mode_length_filters(scoped: pd.DataFrame) -> tuple[pd.DataFrame, int, int]:
    """Given an in-scope frame (from ``_filter_in_scope``), drop legs with an
    unmapped mode or a non-positive/invalid GIS length, counting every
    exclusion explicitly (no silent fallback -- CLAUDE.md fallback-transparency
    rule). Adds ``mode`` and ``euclid_km`` columns to the surviving rows.

    Returns (filtered_df, n_excluded_invalid_mode, n_excluded_invalid_length).
    """
    mode = scoped["E_HVM_5"].map(MODE_BY_E_HVM_5)
    invalid_mode = mode.isna()
    n_excluded_invalid_mode = int(invalid_mode.sum())
    scoped = scoped[~invalid_mode].copy()
    scoped["mode"] = mode[~invalid_mode]

    length_km = pd.to_numeric(scoped["GIS_LAENGE_GUELTIG"], errors="coerce")
    invalid_length = length_km.isna() | (length_km <= 0.0)
    n_excluded_invalid_length = int(invalid_length.sum())
    scoped = scoped[~invalid_length].copy()
    scoped["euclid_km"] = length_km[~invalid_length] / DETOUR_FACTOR

    return scoped, n_excluded_invalid_mode, n_excluded_invalid_length


def derive_type_probabilities(df: pd.DataFrame, *, min_obs: int = DEFAULT_MIN_OBS
                              ) -> tuple[pd.DataFrame, dict]:
    """Per (purpose, mode, euclidean-distance band) GEWICHT_W-weighted category
    probabilities, plus a purpose-level marginal fallback for thin cells.

    Parameters
    ----------
    df:
        SrV Wege frame with numeric columns V_ZWECK, E_HVM_5 and float
        GEWICHT_W, GIS_LAENGE_GUELTIG (decimal point, already parsed; the
        CLI applies the decimal-comma conversion before calling this).
    min_obs:
        Minimum unweighted leg count for a (purpose, mode, band) cell to be
        reported. Cells below this are counted (never fabricated) and
        omitted; the marginal row for that purpose remains available as the
        model's fallback for a thin cell.

    Returns
    -------
    (cells_df, stats):
        cells_df columns: purpose, mode, band_lower_km, band_upper_km,
        is_marginal, category, probability, n_legs_unweighted. Marginal rows
        (is_marginal=1) cover the whole purpose (mode="all",
        band_lower_km=0, band_upper_km=inf) and are always present as long
        as the purpose has >= 1 in-scope leg; per-cell rows (is_marginal=0)
        exist only where the cell has >= min_obs legs.
        stats: n_in_scope, n_excluded_invalid_mode, n_excluded_invalid_length,
        n_thin_cells, n_cells_reported.
    """
    in_scope_all, n_in_scope = _filter_in_scope(df, CATEGORY_BY_V_ZWECK)
    cell_scope, n_excluded_invalid_mode, n_excluded_invalid_length = \
        _apply_mode_length_filters(in_scope_all)
    stats = {
        "n_in_scope": n_in_scope,
        "n_excluded_invalid_mode": n_excluded_invalid_mode,
        "n_excluded_invalid_length": n_excluded_invalid_length,
    }

    band_index = np.searchsorted(
        np.asarray(BAND_EDGES_EUCLID_KM[1:], dtype=float),
        cell_scope["euclid_km"].to_numpy(), side="right")
    cell_scope = cell_scope.copy()
    cell_scope["band_index"] = band_index

    rows = []
    n_thin_cells = 0
    n_cells_reported = 0
    # Marginal: weighted category shares over ALL in-scope legs of this
    # purpose (in_scope_all, NOT mode/length-filtered -- see
    # _filter_in_scope's docstring) -- the fallback used for thin cells.
    for purpose, purpose_group in in_scope_all.groupby("purpose"):
        w_total_purpose = float(purpose_group["GEWICHT_W"].sum())
        for category, category_group in purpose_group.groupby("category"):
            probability = float(category_group["GEWICHT_W"].sum() / w_total_purpose)
            rows.append({
                "purpose": purpose, "mode": "all",
                "band_lower_km": 0.0, "band_upper_km": float("inf"),
                "is_marginal": 1, "category": category,
                "probability": probability,
                "n_legs_unweighted": int(len(category_group)),
            })

    for purpose, purpose_group in cell_scope.groupby("purpose"):
        for (mode, band_index_value), cell_group in purpose_group.groupby(["mode", "band_index"]):
            n_cell = int(len(cell_group))
            if n_cell < min_obs:
                n_thin_cells += 1
                continue
            n_cells_reported += 1
            lower, upper = _band_bounds(np.array([band_index_value]))
            w_total_cell = float(cell_group["GEWICHT_W"].sum())
            for category, category_group in cell_group.groupby("category"):
                probability = float(category_group["GEWICHT_W"].sum() / w_total_cell)
                rows.append({
                    "purpose": purpose, "mode": mode,
                    "band_lower_km": float(lower[0]), "band_upper_km": float(upper[0]),
                    "is_marginal": 0, "category": category,
                    "probability": probability,
                    "n_legs_unweighted": int(len(category_group)),
                })

    cells_df = pd.DataFrame(rows, columns=[
        "purpose", "mode", "band_lower_km", "band_upper_km",
        "is_marginal", "category", "probability", "n_legs_unweighted"])
    stats["n_thin_cells"] = n_thin_cells
    stats["n_cells_reported"] = n_cells_reported
    stats["min_obs"] = int(min_obs)
    return cells_df, stats


def derive_type_shares(df: pd.DataFrame) -> pd.DataFrame:
    """Per (purpose, category) GEWICHT_W-weighted share within its purpose,
    plus weighted-median GIS and euclidean-equivalent distances.

    Includes the ``shop`` purpose (SHOP_SHARE_CODES: shop_daily = code 8,
    shop_non_daily = code 9) as a validation-only reference (the #242
    contribution) -- shop location choice is modelled elsewhere; these rows
    exist only to sanity-check that pipeline against the same SrV source.

    Returns
    -------
    shares_df columns: purpose, category, srv_v_zweck_codes, weight_share,
    n_legs_unweighted, weighted_median_gis_km, weighted_median_euclid_km.
    weight_share and n_legs_unweighted sum to 1.0 / the purpose's full
    in-scope leg count. A category is defined purely by its V_ZWECK code, so
    ``weight_share`` deliberately covers every in-scope leg regardless of
    whether that leg also has a valid mode or GIS length -- narrowing the
    share to the length-valid subset would silently bias the category mix
    towards better-reported categories (see _filter_in_scope's docstring).
    The weighted-median distance columns necessarily need a valid GIS length
    and are therefore computed on that narrower subset; the resulting
    coverage is logged per category (never silently applied).
    """
    combined_map = dict(CATEGORY_BY_V_ZWECK)
    combined_map.update({code: ("shop", category) for code, category in SHOP_SHARE_CODES.items()})
    in_scope_all, _ = _filter_in_scope(df, combined_map)
    gis_km = pd.to_numeric(in_scope_all["GIS_LAENGE_GUELTIG"], errors="coerce")
    valid_length = gis_km.notna() & (gis_km > 0.0)
    in_scope_all["gis_km"] = gis_km
    in_scope_all["euclid_km"] = gis_km / DETOUR_FACTOR
    in_scope_all["valid_length"] = valid_length

    rows = []
    for purpose, purpose_group in in_scope_all.groupby("purpose"):
        w_total_purpose = float(purpose_group["GEWICHT_W"].sum())
        for category, category_group in purpose_group.groupby("category"):
            codes = sorted(code for code, pc in combined_map.items() if pc[1] == category)
            length_valid_group = category_group[category_group["valid_length"]]
            n_legs = int(len(category_group))
            n_length_valid = int(len(length_valid_group))
            if n_length_valid == 0:
                raise ValueError(
                    f"[derive_srv_location_types] category '{category}' has no "
                    "legs with a valid GIS length; cannot compute a weighted "
                    "median distance for it."
                )
            if n_length_valid < n_legs:
                logger.info(
                    "[derive_srv_location_types] %-26s median distance based on "
                    "%d/%d legs with a valid GIS length (%.1f%% coverage); "
                    "weight_share itself uses all %d legs.",
                    category, n_length_valid, n_legs,
                    100.0 * n_length_valid / n_legs, n_legs,
                )
            rows.append({
                "purpose": purpose,
                "category": category,
                "srv_v_zweck_codes": "|".join(str(code) for code in codes),
                "weight_share": float(category_group["GEWICHT_W"].sum() / w_total_purpose),
                "n_legs_unweighted": n_legs,
                "weighted_median_gis_km": weighted_median(
                    length_valid_group["gis_km"], length_valid_group["GEWICHT_W"]),
                "weighted_median_euclid_km": weighted_median(
                    length_valid_group["euclid_km"], length_valid_group["GEWICHT_W"]),
            })
    shares_df = pd.DataFrame(rows, columns=[
        "purpose", "category", "srv_v_zweck_codes", "weight_share",
        "n_legs_unweighted", "weighted_median_gis_km", "weighted_median_euclid_km"])
    return shares_df


def _load_wege(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"[derive_srv_location_types] input not found: {path} "
            "(local-only SrV raw data; see eqasim-data README)."
        )
    df = pd.read_csv(
        path, sep=None, engine="python", encoding="latin-1",
        usecols=["V_ZWECK", "E_HVM_5", "GEWICHT_W", "GIS_LAENGE_GUELTIG"],
    )
    df["GEWICHT_W"] = (df["GEWICHT_W"].astype(str)
                       .str.replace(",", ".", regex=False).astype(float))
    # GIS_LAENGE_GUELTIG: valid-only GIS route length in km (decimal comma) or
    # the -7 sentinel -- convert to float; "value > 0" is treated as valid
    # (same idiom as derive_escort_location_weights.derive_distance_factors).
    df["GIS_LAENGE_GUELTIG"] = pd.to_numeric(
        df["GIS_LAENGE_GUELTIG"].astype(str).str.replace(",", ".", regex=False),
        errors="coerce")
    return df


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--wege", type=Path, default=DEFAULT_WEGE_PATH,
        help=f"Path to SrV2023_Wege.csv input (default: {DEFAULT_WEGE_PATH})",
    )
    parser.add_argument(
        "--out-probs", type=Path, default=DEFAULT_PROBS_OUTPUT_PATH,
        help=f"Path to the type-by-distance probabilities CSV (default: {DEFAULT_PROBS_OUTPUT_PATH})",
    )
    parser.add_argument(
        "--out-shares", type=Path, default=DEFAULT_SHARES_OUTPUT_PATH,
        help=f"Path to the secondary-type shares CSV (default: {DEFAULT_SHARES_OUTPUT_PATH})",
    )
    parser.add_argument("--min-obs", type=int, default=DEFAULT_MIN_OBS,
        help="Minimum unweighted legs per (purpose, mode, band) cell to report it.")
    args = parser.parse_args(argv)

    df = _load_wege(args.wege)

    cells_df, stats = derive_type_probabilities(df, min_obs=args.min_obs)
    logger.info(
        "[derive_srv_location_types] in-scope legs %d, excluded invalid mode %d, "
        "excluded invalid length %d, thin cells omitted %d, cells reported %d "
        "(min_obs=%d)",
        stats["n_in_scope"], stats["n_excluded_invalid_mode"],
        stats["n_excluded_invalid_length"], stats["n_thin_cells"],
        stats["n_cells_reported"], stats["min_obs"],
    )
    n_thin_total = stats["n_thin_cells"] + stats["n_cells_reported"]
    if n_thin_total:
        thin_rate = stats["n_thin_cells"] / n_thin_total
        logger.info(
            "[derive_srv_location_types] thin-cell rate %.1f%% (%d/%d cells below min_obs)",
            100.0 * thin_rate, stats["n_thin_cells"], n_thin_total,
        )

    probs_header = (
        "# Source: SrV 2023 Braunschweig+RGB SciUse (TU Dresden), Wege table, "
        "V_ZWECK in leisure/other category map, GEWICHT_W-weighted.\n"
        f"# Generated by scripts/derive_srv_location_types.py; "
        f"n_in_scope={stats['n_in_scope']}, "
        f"excluded_invalid_mode={stats['n_excluded_invalid_mode']}, "
        f"excluded_invalid_length={stats['n_excluded_invalid_length']}, "
        f"thin_cells_omitted={stats['n_thin_cells']}, "
        f"cells_reported={stats['n_cells_reported']}, min_obs={stats['min_obs']}.\n"
        f"# ASSUMPTION: euclid_km = GIS_LAENGE_GUELTIG / DETOUR_FACTOR "
        f"(DETOUR_FACTOR={DETOUR_FACTOR}), same routed->euclidean assumption as "
        "the MiD distance layers.\n"
        f"# Band edges (euclidean km): {list(BAND_EDGES_EUCLID_KM)}.\n"
        "# Marginal rows (is_marginal=1, mode=\"all\", band [0, inf)) are the "
        "per-purpose fallback used whenever a (mode, band) cell is thinner "
        f"than min_obs={stats['min_obs']} unweighted legs; cell rows "
        "(is_marginal=0) are reported only where the cell meets that threshold.\n"
        "# Regenerate there, never edit.\n"
    )
    args.out_probs.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_probs, "w", encoding="utf-8", newline="") as handle:
        handle.write(probs_header)
        cells_df.to_csv(handle, index=False)
    logger.info("[derive_srv_location_types] wrote %s", args.out_probs)

    shares_df = derive_type_shares(df)
    logger.info("[derive_srv_location_types] secondary-type shares:")
    for purpose, purpose_group in shares_df.groupby("purpose"):
        for _, row in purpose_group.iterrows():
            logger.info(
                "  %-8s %-26s share=%.3f n=%d median_euclid_km=%.2f",
                row["purpose"], row["category"], row["weight_share"],
                row["n_legs_unweighted"], row["weighted_median_euclid_km"],
            )

    shares_header = (
        "# Source: SrV 2023 Braunschweig+RGB SciUse (TU Dresden), Wege table, "
        "V_ZWECK in leisure/other/shop category map, GEWICHT_W-weighted.\n"
        "# weight_share = category weight / purpose weight (sums to 1.0 within "
        "each purpose); weighted_median_*_km = cumulative-weight median (see "
        "weighted_median in this script).\n"
        f"# ASSUMPTION: weighted_median_euclid_km uses "
        f"GIS_LAENGE_GUELTIG / DETOUR_FACTOR (DETOUR_FACTOR={DETOUR_FACTOR}).\n"
        "# shop_daily/shop_non_daily (V_ZWECK 8/9) are VALIDATION-ONLY reference "
        "rows (issue #242 contribution); shop location choice is modelled "
        "elsewhere in the pipeline.\n"
        "# Generated by scripts/derive_srv_location_types.py; regenerate there, never edit.\n"
    )
    args.out_shares.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_shares, "w", encoding="utf-8", newline="") as handle:
        handle.write(shares_header)
        shares_df.to_csv(handle, index=False)
    logger.info("[derive_srv_location_types] wrote %s", args.out_shares)
    return 0


if __name__ == "__main__":
    sys.exit(main())
