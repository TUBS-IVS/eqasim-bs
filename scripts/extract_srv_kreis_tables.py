"""
Extract small, committed aggregate reference tables from the SrV 2023
"Braunschweig und RGB" scientific-use microdata (households + persons).

Source: SrV 2023 city/region add-on survey ("Aufstockungsstudie") for the city
of Braunschweig and the Regionalverband Grossraum Braunschweig (RGB),
scientific-use file SciUse_v4. See
``eqasim-data/data/braunschweig/srv/srv2023_raw/README.md`` for full
provenance. The raw microdata is LOCAL-ONLY and never committed; this script
reads it and writes only small, aggregated, weighted CSVs.

Key data properties handled here (see the module docstrings of the
individual ``build_*`` functions for details):

* Weight columns (``GEWICHT_HH_ZENSUS``, ``GEWICHT_P_ZENSUS`` -- "fuer
  stadtuebergreifende Auswertungen", full expansion to Zensus 2022 counts per
  municipality) are used for ALL aggregation levels. The stratum-internal
  standard weights (``GEWICHT_HH``, ``GEWICHT_P``) are normalized to mean ~1
  WITHIN each ``ST_CODE`` stratum and therefore weight strata by sample share
  instead of population share for any cross-stratum aggregate (total/kreis
  rows, and per-municipality within the two "kleinstaedtisch-doerflich"
  strata); they must not be used here (see docs/DECISIONS.md, fixed
  2026-07-08). Both ZENSUS weight columns carry negative missing codes in
  principle (-9, -6); this delivery has zero such rows, but the filter and
  drop-rate logging are kept as a defensive guard (see CLAUDE.md "No silent
  fallbacks").
* Several categorical/metric variables carry negative missing codes
  (-10 Unplausibel, -9 Keine Angabe, -8 Nicht erhoben, -7 Berechnung nicht
  moeglich, -6 Nicht definiert, -5 Weiss nicht). These are excluded from the
  relevant share before aggregation, and the excluded share is logged.
* The Kreis is derived as the first 5 digits of the zero-padded 8-digit AGS
  (household file); persons and trips inherit it via an HHNR join. Exactly
  the 7 ZGB Kreise other than Wolfsburg are expected to appear; the script
  fails loudly if this does not hold.
* The sample is a stratified PSU design covering ~44 selected municipalities
  within those Kreise (strata = ST_CODE / ST_CODE_NAME). The ZENSUS weight
  expands each respondent to the true Zensus 2022 population per
  municipality, so it is the correct weight for total/kreis/stratum
  aggregates alike; the remaining caveat is one of COVERAGE, not weighting:
  per-Kreis (and total) rows extrapolate from the sampled municipalities to
  the full Kreis (ASSUMPTION-grade), while per-stratum rows are the
  design-safe aggregation level (ST_CODE partitions exactly the sampled
  municipalities). Every output table carries rows at all three levels
  (``level`` column: 'total' | 'kreis' | 'stratum') and states this caveat
  in its provenance header.

Writes (to ``--out-dir``, default ``eqasim-data/data/braunschweig/srv``):
    srv2023_cars_by_kreis.csv
    srv2023_bikes_incl_ebikes_by_kreis.csv
    srv2023_ebike_household_by_kreis.csv
    srv2023_income5_by_kreis.csv
    srv2023_car_license_17plus_by_kreis.csv
    srv2023_dticket_by_kreis.csv
    srv2023_covered_municipalities.csv

Usage:
    python scripts/extract_srv_kreis_tables.py [--raw <dir>] [--out-dir <dir>]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
RAW_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_raw"
OUT_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv"

HOUSEHOLDS_FILE = "SrV2023_Haushalte.csv"
PERSONS_FILE = "SrV2023_Personen.csv"

# Raw SrV CSVs are semicolon-separated, decimal-comma, cp1252-encoded.
CSV_READ_KWARGS = dict(sep=";", decimal=",", encoding="cp1252", low_memory=False)

logger = logging.getLogger("extract_srv_kreis_tables")

# Kreis names use ASCII spelling per CLAUDE.md ("Keep code ASCII only where
# possible"). Wolfsburg (03103) is deliberately absent: the SrV BS+RGB
# add-on survey does not cover it.
KREIS_NAMES = {
    "03101": "Braunschweig",
    "03102": "Salzgitter",
    "03151": "Gifhorn",
    "03153": "Goslar",
    "03154": "Helmstedt",
    "03157": "Peine",
    "03158": "Wolfenbuettel",
}
EXPECTED_KREIS_CODES = frozenset(KREIS_NAMES)
WOLFSBURG_KREIS_CODE = "03103"

STRATUM_CODE_COLUMN = "ST_CODE"
STRATUM_NAME_COLUMN = "ST_CODE_NAME"

TOTAL_LEVEL_CODE = "total"
TOTAL_LEVEL_NAME = "Gesamt"

# Cross-check oracle values from a verified first-pass analysis of the raw
# microdata (see the extraction session that produced this script). These
# are ASSUMPTION-free, directly re-derivable facts about the committed
# source file, not external targets; they only guard against a silent
# regression in the extraction logic (mirrors the pattern used in
# extract_mid_p13_rs7.py for the MiD PDF oracle).
_CROSS_CHECK_TOLERANCE = 0.003  # 0.3 percentage points, as fractions.
_CROSS_CHECK_CARS = {
    "03101": {"cars_0": 0.215, "cars_1": 0.584, "cars_2": 0.173, "cars_3plus": 0.028},
    "03102": {"cars_0": 0.143},
}
_CROSS_CHECK_EBIKE_HOUSEHOLD = {"03151": 0.3108, "03101": 0.181}
_CROSS_CHECK_LICENSE = {"03101": 0.921}
_CROSS_CHECK_DTICKET = {"03101": 0.088}


def _read_srv_csv(path: Path) -> pd.DataFrame:
    """Read one raw SrV microdata CSV, failing early if it is missing."""
    if not path.exists():
        raise FileNotFoundError(
            f"SrV microdata file not found: {path}. Download it per "
            f"{path.parent / 'README.md'} before running this script."
        )
    return pd.read_csv(path, **CSV_READ_KWARGS)


def load_households(raw_dir: Path) -> tuple[pd.DataFrame, pd.Series]:
    """Load the SrV household file, derive Kreis, and validate the weight.

    Returns a tuple ``(households_valid, kreis_by_hhnr)`` where
    ``households_valid`` has rows with a negative ``GEWICHT_HH_ZENSUS``
    (missing codes -9/-6) dropped, and ``kreis_by_hhnr`` is a Kreis lookup
    built from the FULL (weight-unfiltered) household frame -- Kreis is a
    geography attribute derived from AGS, not from the weight, so persons
    must be joinable to it even if their household's weight was invalid.
    """
    df = _read_srv_csv(raw_dir / HOUSEHOLDS_FILE)
    df["ags8"] = df["AGS"].astype(str).str.zfill(8)
    df["kreis"] = df["ags8"].str[:5]

    observed_kreise = set(df["kreis"].unique())
    if observed_kreise != set(EXPECTED_KREIS_CODES):
        raise RuntimeError(
            "Unexpected Kreis coverage in SrV household file "
            f"'{HOUSEHOLDS_FILE}': expected exactly {sorted(EXPECTED_KREIS_CODES)}, "
            f"got {sorted(observed_kreise)}. Wolfsburg ({WOLFSBURG_KREIS_CODE}) is "
            "known to be absent from this survey by design; any other deviation "
            "indicates a data delivery or AGS-parsing problem and must be "
            "investigated before trusting downstream aggregates."
        )
    if WOLFSBURG_KREIS_CODE in observed_kreise:
        raise RuntimeError(
            f"Wolfsburg ({WOLFSBURG_KREIS_CODE}) unexpectedly present in the SrV "
            "household file; this survey is documented to exclude Wolfsburg."
        )

    kreis_by_hhnr = df.set_index("HHNR")["kreis"]

    n_total = len(df)
    n_negative_weight = int((df["GEWICHT_HH_ZENSUS"] < 0).sum())
    if n_negative_weight:
        logger.warning(
            "[households] GEWICHT_HH_ZENSUS: dropping %d/%d (%.1f%%) rows with a negative "
            "missing code (-9/-6) before any weighted aggregation",
            n_negative_weight, n_total, 100.0 * n_negative_weight / n_total,
        )
    else:
        logger.info(
            "[households] GEWICHT_HH_ZENSUS: primary weight valid for %d/%d (100.0%%) rows, "
            "0 fallback/dropped",
            n_total, n_total,
        )
    households_valid = df[df["GEWICHT_HH_ZENSUS"] >= 0].copy()
    logger.info(
        "[households] loaded %d households (%d after weight validation) across %d Kreise",
        n_total, len(households_valid), len(EXPECTED_KREIS_CODES),
    )
    return households_valid, kreis_by_hhnr


def load_persons(raw_dir: Path, kreis_by_hhnr: pd.Series) -> pd.DataFrame:
    """Load the SrV person file, attach Kreis via HHNR, and validate the
    ``GEWICHT_P_ZENSUS`` weight."""
    df = _read_srv_csv(raw_dir / PERSONS_FILE)

    df["kreis"] = df["HHNR"].map(kreis_by_hhnr)
    n_unmatched = int(df["kreis"].isna().sum())
    if n_unmatched:
        raise RuntimeError(
            f"{n_unmatched} persons in '{PERSONS_FILE}' could not be matched to a "
            "household via HHNR; the person and household files are expected to be "
            "fully joinable and this indicates a data integrity problem."
        )

    n_total = len(df)
    n_negative_weight = int((df["GEWICHT_P_ZENSUS"] < 0).sum())
    if n_negative_weight:
        logger.warning(
            "[persons] GEWICHT_P_ZENSUS: dropping %d/%d (%.1f%%) rows with a negative "
            "missing code (-9/-6) before any weighted aggregation",
            n_negative_weight, n_total, 100.0 * n_negative_weight / n_total,
        )
    else:
        logger.info(
            "[persons] GEWICHT_P_ZENSUS: primary weight valid for %d/%d (100.0%%) rows, "
            "0 fallback/dropped",
            n_total, n_total,
        )
    persons_valid = df[df["GEWICHT_P_ZENSUS"] >= 0].copy()
    logger.info(
        "[persons] loaded %d persons (%d after weight validation)",
        n_total, len(persons_valid),
    )
    return persons_valid


def _iter_levels(df: pd.DataFrame):
    """Yield ``(level, code, name, group_df)`` for the total/kreis/stratum levels.

    ``df`` must carry a ``kreis`` column and the raw ``ST_CODE`` /
    ``ST_CODE_NAME`` stratum columns.
    """
    yield "total", TOTAL_LEVEL_CODE, TOTAL_LEVEL_NAME, df
    for code in sorted(EXPECTED_KREIS_CODES):
        yield "kreis", code, KREIS_NAMES[code], df[df["kreis"] == code]
    strata = (
        df[[STRATUM_CODE_COLUMN, STRATUM_NAME_COLUMN]]
        .drop_duplicates()
        .sort_values(STRATUM_CODE_COLUMN)
    )
    for _, stratum_row in strata.iterrows():
        stratum_code = stratum_row[STRATUM_CODE_COLUMN]
        code = str(int(stratum_code))
        name = stratum_row[STRATUM_NAME_COLUMN]
        yield "stratum", code, name, df[df[STRATUM_CODE_COLUMN] == stratum_code]


def _weighted_class_shares(
    group_df: pd.DataFrame, weight_col: str, class_col: str, class_values: list,
) -> dict:
    """Return ``{class_value: weighted_share}`` for a single group.

    All classes share the same denominator (the total weight of
    ``group_df``), so the returned shares sum to 1 as long as every row's
    ``class_col`` value is one of ``class_values`` (i.e. missing codes have
    already been filtered out by the caller).
    """
    total_weight = group_df[weight_col].sum()
    shares = {}
    for value in class_values:
        class_weight = group_df.loc[group_df[class_col] == value, weight_col].sum()
        shares[value] = (class_weight / total_weight) if total_weight > 0 else float("nan")
    return shares


def _provenance_header(universe: str, weight_col: str, missing_handling: str) -> list[str]:
    """Common provenance header lines shared by all Kreis/stratum tables."""
    return [
        "# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig",
        "#   (RGB) scientific-use microdata, file SciUse_v4 (delivered 2026-07).",
        "#   See eqasim-data/data/braunschweig/srv/srv2023_raw/README.md.",
        f"# Universe: {universe}",
        f"# Weight used: {weight_col}",
        f"# Missing-code handling: {missing_handling}",
        "# Coverage: 7 of the 8 ZGB Kreise; Wolfsburg (03103) is NOT covered by",
        "#   this survey and therefore never appears in this table.",
        "# ASSUMPTION (coverage, not weighting): the survey is a stratified PSU",
        "#   design over ~44 selected municipalities within those Kreise (strata =",
        "#   ST_CODE/ST_CODE_NAME; see srv2023_covered_municipalities.csv). The",
        "#   GEWICHT_*_ZENSUS weight expands per municipality to the true Zensus",
        "#   2022 population, so it is the correct weight for total/kreis/stratum",
        "#   rows alike. Per-Kreis (level=kreis) rows still extrapolate from the",
        "#   sampled municipalities to the full Kreis and are therefore an",
        "#   ASSUMPTION-grade coverage estimate; per-stratum (level=stratum) rows",
        "#   are the design-safe aggregation level for this survey.",
        "# Generated by: scripts/extract_srv_kreis_tables.py",
    ]


def write_csv(df: pd.DataFrame, out_path: Path, header_lines: list[str]) -> None:
    """Write ``df`` to ``out_path`` with a leading ``#``-comment provenance header."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="") as fh:
        for line in header_lines:
            fh.write(line + "\n")
        df.to_csv(fh, index=False)


# ---------------------------------------------------------------------------
# Table 1: household car ownership by Kreis (V_ANZ_PKW)
# ---------------------------------------------------------------------------

def build_cars_table(households_valid: pd.DataFrame) -> pd.DataFrame:
    """Household shares of car ownership (V_ANZ_PKW), clipped to {0,1,2,3+}.

    V_ANZ_PKW carries no negative missing codes in this file, so no
    additional rows are excluded beyond the GEWICHT_HH_ZENSUS weight validation
    already applied to ``households_valid``.
    """
    df = households_valid.copy()
    df["cars_class"] = df["V_ANZ_PKW"].clip(upper=3)
    class_values = [0, 1, 2, 3]
    class_columns = {0: "cars_0", 1: "cars_1", 2: "cars_2", 3: "cars_3plus"}

    rows = []
    for level, code, name, group in _iter_levels(df):
        shares = _weighted_class_shares(group, "GEWICHT_HH_ZENSUS", "cars_class", class_values)
        row = {
            "level": level, "code": code, "name": name,
            "n_unweighted": len(group),
            "n_weighted": round(float(group["GEWICHT_HH_ZENSUS"].sum()), 2),
        }
        for value, column in class_columns.items():
            row[column] = round(shares[value], 4)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Table 2: household bike/e-bike ownership by Kreis (E_ANZ_RAD_ALLE_6)
# ---------------------------------------------------------------------------

def build_bikes_table(households_valid: pd.DataFrame) -> pd.DataFrame:
    """Household shares of bike ownership incl. e-bikes (E_ANZ_RAD_ALLE_6).

    Clipped to {0,1,2,3,4+}. E_ANZ_RAD_ALLE_6 carries the -7 "Berechnung
    nicht moeglich" missing code; those rows are excluded and the excluded
    rate is logged per Kreis/overall.
    """
    n_before = len(households_valid)
    valid = households_valid[households_valid["E_ANZ_RAD_ALLE_6"] >= 0].copy()
    n_missing = n_before - len(valid)
    if n_missing:
        logger.warning(
            "[bikes] E_ANZ_RAD_ALLE_6: excluding %d/%d (%.1f%%) households with a "
            "missing/invalid code (-7) before computing shares",
            n_missing, n_before, 100.0 * n_missing / n_before,
        )

    valid["bikes_class"] = valid["E_ANZ_RAD_ALLE_6"].clip(upper=4)
    class_values = [0, 1, 2, 3, 4]
    class_columns = {0: "bikes_0", 1: "bikes_1", 2: "bikes_2", 3: "bikes_3", 4: "bikes_4plus"}

    rows = []
    for level, code, name, group in _iter_levels(valid):
        shares = _weighted_class_shares(group, "GEWICHT_HH_ZENSUS", "bikes_class", class_values)
        row = {
            "level": level, "code": code, "name": name,
            "n_unweighted": len(group),
            "n_weighted": round(float(group["GEWICHT_HH_ZENSUS"].sum()), 2),
        }
        for value, column in class_columns.items():
            row[column] = round(shares[value], 4)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Table 3: e-bike-owning household share by Kreis (V_ANZ_ERAD)
# ---------------------------------------------------------------------------

def build_ebike_household_table(households_valid: pd.DataFrame) -> pd.DataFrame:
    """Share of households owning at least one e-bike (V_ANZ_ERAD >= 1).

    V_ANZ_ERAD carries the -10 "Unplausibel" missing code; those rows are
    excluded and the excluded rate is logged.
    """
    n_before = len(households_valid)
    valid = households_valid[households_valid["V_ANZ_ERAD"] >= 0].copy()
    n_missing = n_before - len(valid)
    if n_missing:
        logger.warning(
            "[ebike-household] V_ANZ_ERAD: excluding %d/%d (%.1f%%) households with a "
            "missing/invalid code (-10) before computing the share",
            n_missing, n_before, 100.0 * n_missing / n_before,
        )

    valid["has_ebike"] = (valid["V_ANZ_ERAD"] >= 1).astype(int)

    rows = []
    for level, code, name, group in _iter_levels(valid):
        shares = _weighted_class_shares(group, "GEWICHT_HH_ZENSUS", "has_ebike", [0, 1])
        rows.append({
            "level": level, "code": code, "name": name,
            "n_unweighted": len(group),
            "n_weighted": round(float(group["GEWICHT_HH_ZENSUS"].sum()), 2),
            "share_hh_with_ebike": round(shares[1], 4),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Table 4: household income class by Kreis (E_EINK_5)
# ---------------------------------------------------------------------------

def build_income_table(households_valid: pd.DataFrame) -> pd.DataFrame:
    """Household shares of the 5-class net income band (E_EINK_5).

    E_EINK_5 carries the -9 "Keine Angabe" and -5 "Weiss nicht" missing
    codes. Unlike the other tables, the 5 income-class shares are reported
    over the VALID subset only (so they sum to 1 among respondents), while
    ``share_income_missing`` is reported over ALL weighted households in the
    group (n_unweighted / n_weighted refer to the valid subset used for the
    income-class shares; the missing-share denominator is computed
    separately and documented here).
    """
    class_values = [1, 2, 3, 4, 5]
    class_columns = {
        1: "income_lt_1500", 2: "income_1500_2600", 3: "income_2600_3600",
        4: "income_3600_5600", 5: "income_ge_5600",
    }

    n_total = len(households_valid)
    n_missing_total = int((households_valid["E_EINK_5"] < 0).sum())
    logger.warning(
        "[income] E_EINK_5: %d/%d (%.1f%%) households have a missing income response "
        "(-9 Keine Angabe / -5 Weiss nicht) across the whole sample; reported per-group "
        "as share_income_missing",
        n_missing_total, n_total, 100.0 * n_missing_total / n_total,
    )

    rows = []
    for level, code, name, group in _iter_levels(households_valid):
        total_weight = group["GEWICHT_HH_ZENSUS"].sum()
        valid_group = group[group["E_EINK_5"] > 0]
        valid_weight = valid_group["GEWICHT_HH_ZENSUS"].sum()
        missing_weight = total_weight - valid_weight
        shares = _weighted_class_shares(valid_group, "GEWICHT_HH_ZENSUS", "E_EINK_5", class_values)

        row = {
            "level": level, "code": code, "name": name,
            "n_unweighted": len(valid_group),
            "n_weighted": round(float(valid_weight), 2),
        }
        for value, column in class_columns.items():
            row[column] = round(shares[value], 4)
        row["share_income_missing"] = round(
            float(missing_weight / total_weight) if total_weight > 0 else float("nan"), 4
        )
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Table 5: driving-licence holders aged 17+ by Kreis (V_FUEHR_PKW)
# ---------------------------------------------------------------------------

def build_license_table(persons_valid: pd.DataFrame) -> pd.DataFrame:
    """Share of persons aged >= 17 holding a car driving licence (V_FUEHR_PKW).

    Universe: persons with V_ALTER >= 17 AND V_FUEHR_PKW in {1, 2} (i.e. a
    valid response). V_FUEHR_PKW == -8 "Nicht erhoben" is excluded from the
    universe; the excluded rate among the age-eligible pool is logged.
    """
    age_eligible = persons_valid[persons_valid["V_ALTER"] >= 17]
    universe = age_eligible[age_eligible["V_FUEHR_PKW"].isin([1, 2])].copy()

    n_age_eligible = len(age_eligible)
    n_no_response = n_age_eligible - len(universe)
    if n_no_response:
        logger.warning(
            "[license] V_FUEHR_PKW: excluding %d/%d (%.1f%%) persons aged 17+ with a "
            "missing response (-8 Nicht erhoben) before computing the licence share",
            n_no_response, n_age_eligible, 100.0 * n_no_response / n_age_eligible,
        )

    rows = []
    for level, code, name, group in _iter_levels(universe):
        shares = _weighted_class_shares(group, "GEWICHT_P_ZENSUS", "V_FUEHR_PKW", [1, 2])
        rows.append({
            "level": level, "code": code, "name": name,
            "n_unweighted": len(group),
            "n_weighted": round(float(group["GEWICHT_P_ZENSUS"].sum()), 2),
            "share_with_license": round(shares[1], 4),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Table 6: Deutschlandticket holders by Kreis (E_OEV_FK)
# ---------------------------------------------------------------------------

def build_dticket_table(persons_valid: pd.DataFrame) -> pd.DataFrame:
    """Share of persons holding a Deutschlandticket (E_OEV_FK == 50).

    Universe: all persons with E_OEV_FK != -10 (Unplausibel). Per the
    extraction spec, E_OEV_FK == -8 "Nicht erhoben" (no PT use in the past
    12 months) is deliberately KEPT in the universe and counted as "no
    Deutschlandticket", not excluded -- this reflects a real absence of PT
    use rather than a missing survey response.
    """
    n_before = len(persons_valid)
    universe = persons_valid[persons_valid["E_OEV_FK"] != -10].copy()
    n_excluded = n_before - len(universe)
    if n_excluded:
        logger.warning(
            "[dticket] E_OEV_FK: excluding %d/%d (%.1f%%) persons with an implausible "
            "code (-10) before computing the Deutschlandticket share",
            n_excluded, n_before, 100.0 * n_excluded / n_before,
        )
    else:
        logger.info(
            "[dticket] E_OEV_FK: no implausible (-10) codes found; universe = all "
            "%d persons (E_OEV_FK == -8 'nicht erhoben' is kept and counted as 'no "
            "Deutschlandticket')",
            n_before,
        )

    rows = []
    for level, code, name, group in _iter_levels(universe):
        total_weight = group["GEWICHT_P_ZENSUS"].sum()
        dticket_weight = group.loc[group["E_OEV_FK"] == 50, "GEWICHT_P_ZENSUS"].sum()
        share = (dticket_weight / total_weight) if total_weight > 0 else float("nan")
        rows.append({
            "level": level, "code": code, "name": name,
            "n_unweighted": len(group),
            "n_weighted": round(float(total_weight), 2),
            "share_deutschlandticket": round(float(share), 4),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Ticket groups at 14+ by Kreis (E_OEV_FK) -- the SrV half of the #321 blend
# ---------------------------------------------------------------------------

# E_OEV_FK code -> three-group collapse (SrV2023_Datenkodierung_SciUse.xlsx):
#   50 Deutschland-Ticket           -> deutschlandticket
#    3 Zeitkarte (ausser D-Ticket)  -> other_flatrate  (the unlimited-rides-within-
#      validity concept, matching MiD P24.1's wochen_monat_ohne_abo +
#      monat_abo_jahreskarte + jobticket_semesterticket)
#    1 Einzel-/Mehrfachfahrkarte    -> not_flatrate
#    2 Tageskarte                   -> not_flatrate
#   70 Sonstige Fahrkarte           -> not_flatrate
#   -8 Nicht erhoben                -> not_flatrate  (no PT use in the past 12 months;
#      kept, same convention as the Deutschlandticket table)
#   60 Freifahrtberechtigung        -> not_flatrate  (see below)
#  -10 Unplausibel                  -> excluded
#
# Code 60 (free-travel entitlement for children / severely disabled persons) is
# deliberately NOT counted as flatrate even though those persons pay nothing on PT:
# MiD P24.1 has no pendant category, and this table exists to be BLENDED with MiD, so
# both sides must carry the same construct. Measured effect of the choice: +0.78pp on
# the regional flatrate share (2026-08-18). The simulation-side consequence is recorded
# in the ADR, not silently absorbed here.
TICKET_GROUP_MIN_AGE = 14
TICKET_GROUP_DEUTSCHLANDTICKET_CODE = 50
TICKET_GROUP_OTHER_FLATRATE_CODES = (3,)
TICKET_GROUP_NOT_FLATRATE_CODES = (1, 2, 70, 60, -8)


def build_ticket_groups_table(persons: pd.DataFrame) -> pd.DataFrame:
    """Three-group PT ticket shares for persons aged >= 14 (issue #321).

    Universe: persons with a valid GEWICHT_P_ZENSUS weight, ``E_OEV_FK != -10``
    (Unplausibel) and ``V_ALTER >= 14`` -- the 14+ restriction makes this table
    universe-compatible with MiD P24.1 ("ab 14 Jahre"), which the committed
    all-ages ``srv2023_dticket_by_kreis.csv`` is NOT.
    """
    universe = persons[(persons["E_OEV_FK"] != -10)
                       & (persons["V_ALTER"] >= TICKET_GROUP_MIN_AGE)].copy()
    unknown = set(universe["E_OEV_FK"].unique()) - {
        TICKET_GROUP_DEUTSCHLANDTICKET_CODE, *TICKET_GROUP_OTHER_FLATRATE_CODES,
        *TICKET_GROUP_NOT_FLATRATE_CODES}
    if unknown:
        raise RuntimeError(
            f"[ticket_groups] unmapped E_OEV_FK code(s) {sorted(unknown)} in the "
            "universe; every code must be assigned to a group or the shares would not "
            "sum to 1 (no silent drop).")
    logger.info(
        "[ticket_groups] universe = %d persons aged >= %d with a plausible E_OEV_FK; "
        "code -8 (no PT use in 12 months) covers %d of them (%.1f%%) and is counted as "
        "not_flatrate -- an ASSUMPTION carrying that share of the sample",
        len(universe), TICKET_GROUP_MIN_AGE,
        int((universe["E_OEV_FK"] == -8).sum()),
        100.0 * (universe["E_OEV_FK"] == -8).sum() / max(len(universe), 1))

    rows = []
    for level, code, name, group in _iter_levels(universe):
        w = group["GEWICHT_P_ZENSUS"]
        total = float(w.sum())
        if total <= 0:
            raise RuntimeError(f"[ticket_groups] {level} {code}: zero total weight")
        dt = float(w[group["E_OEV_FK"] == TICKET_GROUP_DEUTSCHLANDTICKET_CODE].sum())
        other = float(w[group["E_OEV_FK"].isin(TICKET_GROUP_OTHER_FLATRATE_CODES)].sum())
        rest = float(w[group["E_OEV_FK"].isin(TICKET_GROUP_NOT_FLATRATE_CODES)].sum())
        rows.append({
            "level": level, "code": code, "name": name,
            "n_unweighted": len(group),
            "n_weighted": round(total, 2),
            "deutschlandticket": round(dt / total, 4),
            "other_flatrate": round(other / total, 4),
            "not_flatrate": round(rest / total, 4),
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Table 7: covered municipalities (household sample composition)
# ---------------------------------------------------------------------------

def build_municipalities_table(households_valid: pd.DataFrame) -> pd.DataFrame:
    """One row per sampled municipality (kreis_code, ags, name, household count)."""
    grouped = (
        households_valid
        .groupby(["kreis", "ags8", "AGS_NAME"])
        .size()
        .reset_index(name="n_households_unweighted")
        .rename(columns={"kreis": "kreis_code", "ags8": "ags", "AGS_NAME": "municipality_name"})
        .sort_values(["kreis_code", "ags"])
        .reset_index(drop=True)
    )
    return grouped[["kreis_code", "ags", "municipality_name", "n_households_unweighted"]]


# ---------------------------------------------------------------------------
# Cross-check validation (guards against a silent extraction-logic regression)
# ---------------------------------------------------------------------------

def _assert_cross_checks(
    cars_df: pd.DataFrame,
    ebike_household_df: pd.DataFrame,
    license_df: pd.DataFrame,
    dticket_df: pd.DataFrame,
) -> None:
    """Verify the per-Kreis results reproduce the verified first-pass values.

    These are internal consistency checks against a prior, independently
    verified pass over the same committed source file -- NOT external
    validation targets (see CLAUDE.md "No invented reference values"). A
    mismatch beyond +-0.3pp means the extraction logic itself regressed and
    must be investigated, not adjusted to match.
    """
    def _kreis_row(df, code):
        row = df[(df["level"] == "kreis") & (df["code"] == code)]
        if row.empty:
            raise RuntimeError(f"Cross-check failed: no kreis row for code {code!r}")
        return row.iloc[0]

    for code, expected in _CROSS_CHECK_CARS.items():
        row = _kreis_row(cars_df, code)
        for column, expected_share in expected.items():
            actual = float(row[column])
            assert abs(actual - expected_share) < _CROSS_CHECK_TOLERANCE, (
                f"cars cross-check failed for Kreis {code} column {column}: "
                f"got {actual:.4f}, expected {expected_share:.4f} (+-{_CROSS_CHECK_TOLERANCE})"
            )

    for code, expected_share in _CROSS_CHECK_EBIKE_HOUSEHOLD.items():
        row = _kreis_row(ebike_household_df, code)
        actual = float(row["share_hh_with_ebike"])
        assert abs(actual - expected_share) < _CROSS_CHECK_TOLERANCE, (
            f"ebike-household cross-check failed for Kreis {code}: "
            f"got {actual:.4f}, expected {expected_share:.4f} (+-{_CROSS_CHECK_TOLERANCE})"
        )

    for code, expected_share in _CROSS_CHECK_LICENSE.items():
        row = _kreis_row(license_df, code)
        actual = float(row["share_with_license"])
        assert abs(actual - expected_share) < _CROSS_CHECK_TOLERANCE, (
            f"license cross-check failed for Kreis {code}: "
            f"got {actual:.4f}, expected {expected_share:.4f} (+-{_CROSS_CHECK_TOLERANCE})"
        )

    for code, expected_share in _CROSS_CHECK_DTICKET.items():
        row = _kreis_row(dticket_df, code)
        actual = float(row["share_deutschlandticket"])
        assert abs(actual - expected_share) < _CROSS_CHECK_TOLERANCE, (
            f"dticket cross-check failed for Kreis {code}: "
            f"got {actual:.4f}, expected {expected_share:.4f} (+-{_CROSS_CHECK_TOLERANCE})"
        )

    logger.info("[cross-check] all verified first-pass values reproduced within +-0.3pp")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Extract small, committed aggregate reference tables from the SrV 2023 "
            "Braunschweig+RGB scientific-use microdata."
        )
    )
    parser.add_argument("--raw", type=Path, default=RAW_DEFAULT,
                        help="Directory containing the raw SrV CSV files.")
    parser.add_argument("--out-dir", type=Path, default=OUT_DEFAULT,
                        help="Output directory for the derived aggregate CSVs.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

    if not args.raw.exists():
        sys.stderr.write(f"[srv-extract] Raw data directory not found: {args.raw}\n")
        return 2

    households_valid, kreis_by_hhnr = load_households(args.raw)
    persons_valid = load_persons(args.raw, kreis_by_hhnr)

    cars_df = build_cars_table(households_valid)
    bikes_df = build_bikes_table(households_valid)
    ebike_household_df = build_ebike_household_table(households_valid)
    income_df = build_income_table(households_valid)
    license_df = build_license_table(persons_valid)
    dticket_df = build_dticket_table(persons_valid)
    ticket_groups_df = build_ticket_groups_table(persons_valid)
    municipalities_df = build_municipalities_table(households_valid)

    _assert_cross_checks(cars_df, ebike_household_df, license_df, dticket_df)

    write_csv(
        cars_df, args.out_dir / "srv2023_cars_by_kreis.csv",
        _provenance_header(
            universe="households with a valid GEWICHT_HH_ZENSUS weight",
            weight_col="GEWICHT_HH_ZENSUS",
            missing_handling="V_ANZ_PKW has no missing codes in this file; classes "
                              "clipped to {0,1,2,3+}",
        ),
    )
    write_csv(
        bikes_df, args.out_dir / "srv2023_bikes_incl_ebikes_by_kreis.csv",
        _provenance_header(
            universe="households with a valid GEWICHT_HH_ZENSUS weight and a valid "
                     "E_ANZ_RAD_ALLE_6 response",
            weight_col="GEWICHT_HH_ZENSUS",
            missing_handling="E_ANZ_RAD_ALLE_6 == -7 (Berechnung nicht moeglich) "
                              "excluded; classes clipped to {0,1,2,3,4+}",
        ),
    )
    write_csv(
        ebike_household_df, args.out_dir / "srv2023_ebike_household_by_kreis.csv",
        _provenance_header(
            universe="households with a valid GEWICHT_HH_ZENSUS weight and a valid "
                     "V_ANZ_ERAD response",
            weight_col="GEWICHT_HH_ZENSUS",
            missing_handling="V_ANZ_ERAD == -10 (Unplausibel) excluded",
        ),
    )
    write_csv(
        income_df, args.out_dir / "srv2023_income5_by_kreis.csv",
        _provenance_header(
            universe="households with a valid GEWICHT_HH_ZENSUS weight; the 5 income-class "
                     "shares are computed over respondents with a valid E_EINK_5 "
                     "code only (n_unweighted/n_weighted refer to this valid subset)",
            weight_col="GEWICHT_HH_ZENSUS",
            missing_handling="E_EINK_5 in {-9 Keine Angabe, -5 Weiss nicht} excluded "
                              "from the income-class shares and reported separately "
                              "as share_income_missing (fraction of ALL weighted "
                              "households in the group, valid + missing)",
        ),
    )
    write_csv(
        license_df, args.out_dir / "srv2023_car_license_17plus_by_kreis.csv",
        _provenance_header(
            universe="persons aged >= 17 (V_ALTER) with a valid GEWICHT_P_ZENSUS weight "
                     "and V_FUEHR_PKW in {1 ja, 2 nein}",
            weight_col="GEWICHT_P_ZENSUS",
            missing_handling="V_FUEHR_PKW == -8 (Nicht erhoben) excluded from the "
                              "universe",
        ),
    )
    write_csv(
        dticket_df, args.out_dir / "srv2023_dticket_by_kreis.csv",
        _provenance_header(
            universe="persons with a valid GEWICHT_P_ZENSUS weight and E_OEV_FK != -10 "
                     "(Unplausibel); E_OEV_FK == -8 'nicht erhoben' (no PT use in "
                     "the past 12 months) is KEPT and counted as 'no "
                     "Deutschlandticket', not excluded",
            weight_col="GEWICHT_P_ZENSUS",
            missing_handling="only E_OEV_FK == -10 (Unplausibel) excluded",
        ),
    )
    write_csv(
        ticket_groups_df, args.out_dir / "srv2023_ticket_groups_14plus_by_kreis.csv",
        _provenance_header(
            universe="persons aged >= 14 (V_ALTER) with a valid GEWICHT_P_ZENSUS weight "
                     "and E_OEV_FK != -10 (Unplausibel); the 14+ restriction makes this "
                     "table universe-compatible with MiD P24.1 ('ab 14 Jahre'). "
                     "E_OEV_FK == -8 'nicht erhoben' (no PT use in the past 12 months) "
                     "is KEPT and counted as not_flatrate -- an ASSUMPTION that carries "
                     "roughly a third of the sample. Code 60 "
                     "(Freifahrtberechtigung) is counted as not_flatrate because MiD "
                     "P24.1 has no pendant and this table is meant to be BLENDED with "
                     "MiD (effect of that choice: +0.78pp on the regional flatrate "
                     "share)",
            weight_col="GEWICHT_P_ZENSUS",
            missing_handling="only E_OEV_FK == -10 (Unplausibel) excluded",
        ),
    )
    write_csv(
        municipalities_df, args.out_dir / "srv2023_covered_municipalities.csv",
        [
            "# Source: SrV 2023 Braunschweig + Regionalverband Grossraum Braunschweig",
            "#   (RGB) scientific-use microdata, file SciUse_v4 (delivered 2026-07).",
            "#   See eqasim-data/data/braunschweig/srv/srv2023_raw/README.md.",
            "# One row per municipality (AGS) sampled by the household survey.",
            "# Universe: households with a valid GEWICHT_HH_ZENSUS weight.",
            "# n_households_unweighted is a raw sample count (not weight-expanded).",
            "# ASSUMPTION: this is the full list of ~44 municipalities forming the",
            "#   stratified PSU sample; it defines the coverage that all other",
            "#   srv2023_*_by_kreis.csv tables' per-Kreis rows implicitly average over.",
            "# Generated by: scripts/extract_srv_kreis_tables.py",
        ],
    )

    logger.info("[srv-extract] wrote 7 aggregate CSVs to %s", args.out_dir)

    print("\n=== srv2023_cars_by_kreis.csv (level=kreis) ===")
    print(cars_df[cars_df["level"] == "kreis"].to_string(index=False))
    print("\n=== srv2023_ebike_household_by_kreis.csv (level=kreis) ===")
    print(ebike_household_df[ebike_household_df["level"] == "kreis"].to_string(index=False))
    print("\n=== srv2023_car_license_17plus_by_kreis.csv (level=kreis) ===")
    print(license_df[license_df["level"] == "kreis"].to_string(index=False))
    print("\n=== srv2023_dticket_by_kreis.csv (level=kreis) ===")
    print(dticket_df[dticket_df["level"] == "kreis"].to_string(index=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
