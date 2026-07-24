"""Derive escort destination-type weights from SrV 2023 BS+RGB (issue #201).

SrV 2023 asks escort trips (V_ZWECK == 12, "Bringen oder Holen von Personen")
directly where the person was brought / picked up (V_ZWECK_BHOL). This script
computes the GEWICHT_W-weighted share per candidate category and writes the
pinned reference CSV consumed as the default for the ``escort_locations_*``
config keys.

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

import logging
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

WEGE_PATH = Path("eqasim-data/data/braunschweig/srv/srv2023_raw/SrV2023_Wege.csv")
OUTPUT_PATH = Path("eqasim-data/data/braunschweig/srv/srv2023_escort_destination_types.csv")

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


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not WEGE_PATH.exists():
        raise FileNotFoundError(
            f"[derive_escort_location_weights] input not found: {WEGE_PATH} "
            "(local-only SrV raw data; see eqasim-data README)."
        )
    df = pd.read_csv(
        WEGE_PATH, sep=None, engine="python", encoding="latin-1",
        usecols=["V_ZWECK", "V_ZWECK_BHOL", "E_ZWECK_OBHOL", "GEWICHT_W"],
    )
    df["GEWICHT_W"] = (
        df["GEWICHT_W"].astype(str).str.replace(",", ".", regex=False).astype(float)
    )
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
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="") as handle:
        handle.write(header)
        table.to_csv(handle, index=False)
    logger.info("[derive_escort_location_weights] wrote %s", OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
