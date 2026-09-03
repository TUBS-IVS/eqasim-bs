"""SrV 2023 distance-distribution targets for the primary-activity location models.

Builders turn the local-only SrV 2023 "Braunschweig und RGB" scientific-use microdata
(trips + persons + households) into small committed aggregate tables per home Kreis:
work and education distance band shares (with an intra/inter-Gemeinde split for work),
and per-Kreis distance quantiles for the per-person commute-distance targets. Loaders
read the committed tables back. This module has no synpp dependency and is not
imported by any pipeline stage.

Conventions (spec docs/superpowers/specs/2026-09-03-srv-primary-distance-calibration-design.md):
- observation unit = person: first home->purpose trip, else first purpose->home trip;
- distance = GIS-routed km (``GIS_LAENGE``) where ``GIS_LAENGE_GUELTIG > 0``; invalid rows
  are excluded and their share is reported;
- weight = ``GEWICHT_W_ZENSUS`` (expansion weight), rows with negative weight dropped;
- levels follow the model's AGE banding because the model's education output has no
  level column (oberstufe and bbs are pooled into ``upper_secondary``).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.gravity.friction import BAND_EDGES_KM

logger = logging.getLogger(__name__)

WORK_BAND_EDGES_KM = BAND_EDGES_KM
WORK_BAND_LABELS = ("0_5", "5_10", "10_20", "20_30", "30_50", "50_100", "100_plus")
EDUCATION_BAND_EDGES_KM = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, float("inf"))
EDUCATION_BAND_LABELS = ("0_1", "1_2", "2_5", "5_10", "10_20", "20_plus")

# SrV V_ZWECK destination-purpose codes (codebook SrV2023_Datenkodierung_SciUse.xlsx).
PURPOSE_WORK = 1
PURPOSE_BUSINESS = 2          # excluded: "Anderer Dienstort/-weg"
PURPOSE_KITA = 3
PURPOSE_GRUNDSCHULE = 4
PURPOSE_SCHOOL_SECONDARY = 5  # "Weiterfuehrende Schule"
PURPOSE_TERTIARY = 6          # "Berufs-, Fach-, Hochschule"
PURPOSE_OTHER_EDUCATION = 7   # excluded: "Andere Bildungseinrichtung"
EDUCATION_PURPOSES = (PURPOSE_KITA, PURPOSE_GRUNDSCHULE, PURPOSE_SCHOOL_SECONDARY, PURPOSE_TERTIARY)

COMPARABLE_LEVELS = ("kindergarten", "grundschule", "sekundar_1", "upper_secondary", "university")
DESCRIPTIVE_ONLY_LEVELS = ("oberstufe", "bbs")

# Model age banding (braunschweig.synthesis.locations.education_gravity._SCHOOL_BANDS):
# kindergarten 0-5, grundschule 6-9, sekundar_1 10-15, upper_secondary 16-19, university 20+.
_MODEL_AGE_LEVELS = (
    (0, 5, "kindergarten"),
    (6, 9, "grundschule"),
    (10, 15, "sekundar_1"),
    (16, 19, "upper_secondary"),
    (20, 200, "university"),
)


def model_education_level(age) -> str | None:
    """Model-side education level from age alone (the education output carries no level)."""
    if pd.isna(age):
        return None
    a = int(age)
    for lower, upper, level in _MODEL_AGE_LEVELS:
        if lower <= a <= upper:
            return level
    return None


def education_level(purpose_code, age) -> str | None:
    """Comparable education level from the SrV purpose code and the person's age.

    Purpose decides the institution type; age also bounds the early childhood and
    primary codes (Kita 0-6, Grundschule 5-10) and splits the secondary-school and
    tertiary codes into the model's age bands. Combinations that the model cannot
    produce (e.g. secondary school at age 25, Kita at age 40) return None and are
    excluded upstream with a logged rate.
    """
    if pd.isna(age) or pd.isna(purpose_code):
        return None
    a = int(age)
    code = int(purpose_code)
    if code == PURPOSE_KITA:
        if 0 <= a <= 6:
            return "kindergarten"
        return None
    if code == PURPOSE_GRUNDSCHULE:
        if 5 <= a <= 10:
            return "grundschule"
        return None
    if code == PURPOSE_SCHOOL_SECONDARY:
        if 10 <= a <= 15:
            return "sekundar_1"
        if 16 <= a <= 19:
            return "upper_secondary"
        return None
    if code == PURPOSE_TERTIARY:
        if 16 <= a <= 19:
            return "upper_secondary"
        if a >= 20:
            return "university"
        return None
    return None


def education_level_descriptive(purpose_code, age) -> str | None:
    """Like :func:`education_level` but keeps the SrV-only oberstufe / bbs split at 16-19."""
    level = education_level(purpose_code, age)
    if level == "upper_secondary":
        return "oberstufe" if int(purpose_code) == PURPOSE_SCHOOL_SECONDARY else "bbs"
    return level


HOME_CODE = 19            # V_ZWECK / E_START_ZWECK "Eigene Wohnung"
START_AT_OWN_HOME = 1     # V_START_LAGE
DEST_AT_OWN_HOME = 1      # V_ZIEL_LAGE
DEFAULT_MAX_DISTANCE_KM = 300.0


def _kreis_from_ags(ags: pd.Series) -> pd.Series:
    """5-digit Kreis key from an 8-digit AGS that pandas may carry as int (leading 0 lost)."""
    return ags.astype("Int64").astype(str).str.zfill(8).str[:5]


def select_person_observations(trips, persons, households, purpose_codes,
                               max_distance_km=DEFAULT_MAX_DISTANCE_KM):
    """One home<->purpose distance observation per person for the given purpose codes.

    Selection mirrors eqasim's ``data.hts.commute_distance``: per person the FIRST
    home->purpose trip (start at own home, destination purpose in ``purpose_codes``);
    if that direction has no GIS-valid distance, the FIRST purpose->home trip (start
    purpose in ``purpose_codes``, destination at own home).

    GIS validity is resolved BEFORE the per-person pick, because it decides which
    DIRECTION represents the person (a data-quality substitution): the fallback
    direction is only used when the preferred one carries no routed distance at all.
    Negative weight and over-``max_distance_km`` are resolved AFTER the per-person
    pick and are terminal exclusions of that person's selected observation (no
    fallback to the other direction), because they flag the specific selected trip
    as unusable for calibration rather than merely GIS-unrouted. Mixing the two
    filters into a single upfront pass would silently swap in the other direction
    for a person whose preferred trip has a bad weight, understating the exclusion
    (caught by ``test_select_person_observations_drops_negative_weight_and_over_cap``).
    """
    purpose_codes = tuple(int(c) for c in purpose_codes)
    t = trips.copy()
    t["weight"] = pd.to_numeric(t["GEWICHT_W_ZENSUS"], errors="coerce")
    t["gis_valid"] = pd.to_numeric(t["GIS_LAENGE_GUELTIG"], errors="coerce") > 0
    t["distance_km"] = pd.to_numeric(t["GIS_LAENGE"], errors="coerce")

    outbound = (t["V_START_LAGE"] == START_AT_OWN_HOME) & t["V_ZWECK"].isin(purpose_codes)
    inbound = (t["V_ZIEL_LAGE"] == DEST_AT_OWN_HOME) & t["E_START_ZWECK"].isin(purpose_codes)
    cand = t[outbound | inbound].copy()
    cand["direction_rank"] = np.where(outbound[cand.index], 0, 1)
    cand["purpose_code"] = np.where(outbound[cand.index], cand["V_ZWECK"], cand["E_START_ZWECK"])
    log = {"n_candidate_trips": int(len(cand))}

    # Step 1: GIS-invalid trips cannot represent a person at all; drop them from the
    # pool, then pick the preferred-direction trip per person from what remains.
    n_gis = int((~cand["gis_valid"]).sum())
    log["n_excluded_gis_invalid"] = n_gis
    gis_valid_cand = cand[cand["gis_valid"]].sort_values(["HHNR", "PNR", "direction_rank", "WNR"])
    first = gis_valid_cand.drop_duplicates(["HHNR", "PNR"], keep="first")

    # Step 2: apply weight and distance-cap checks to the SELECTED observation only;
    # a failure here drops the person, it does not fall back to the other direction.
    n_neg = int((first["weight"] < 0).sum())
    first = first[first["weight"] >= 0]
    n_cap = int((first["distance_km"] > max_distance_km).sum())
    first = first[first["distance_km"] <= max_distance_km]
    log.update(n_excluded_weight_negative=n_neg, n_excluded_over_cap=n_cap)

    hh = households[["HHNR", "AGS"]].copy()
    hh["kreis"] = _kreis_from_ags(hh["AGS"])
    hh["household_ags8"] = hh["AGS"].astype("Int64").astype(str).str.zfill(8)
    first = first.merge(hh[["HHNR", "kreis", "household_ags8"]], on="HHNR", how="left")
    first = first.merge(persons[["HHNR", "PNR", "V_ALTER"]], on=["HHNR", "PNR"], how="left")

    n_no_kreis = int(first["kreis"].isna().sum())
    first = first[first["kreis"].notna()]
    log["n_excluded_no_kreis"] = n_no_kreis

    start_ags8 = first["V_START_AGS"].astype("Int64").astype(str).str.zfill(8)
    dest_ags8 = first["V_ZIEL_AGS"].astype("Int64").astype(str).str.zfill(8)
    home_ags8 = np.where(first["direction_rank"] == 0, start_ags8, dest_ags8)
    away_ags8 = np.where(first["direction_rank"] == 0, dest_ags8, start_ags8)
    agree = (pd.Series(home_ags8, index=first.index) == first["household_ags8"])
    log["share_start_ags_equals_household_ags"] = float(agree.mean()) if len(first) else float("nan")

    obs = pd.DataFrame({
        "hhnr": first["HHNR"].astype(int).values,
        "pnr": first["PNR"].astype(int).values,
        "kreis": first["kreis"].values,
        "regiostar7": pd.to_numeric(first["REGIOSTAR7"], errors="coerce").fillna(-1).astype(int).values,
        "purpose_code": first["purpose_code"].astype(int).values,
        "age": pd.to_numeric(first["V_ALTER"], errors="coerce").values,
        "distance_km": first["distance_km"].astype(float).values,
        "weight": first["weight"].astype(float).values,
        "intra_gemeinde": (pd.Series(home_ags8, index=first.index).values == away_ags8),
    })
    log["n_persons_selected"] = int(len(obs))
    logger.info(
        "[srv_distance_targets] purposes %s: %d candidate trips -> %d persons; excluded "
        "gis_invalid %d, weight<0 %d, >%.0f km %d, no Kreis %d; home AGS == household AGS %.1f%%",
        purpose_codes, log["n_candidate_trips"], log["n_persons_selected"], n_gis, n_neg,
        max_distance_km, n_cap, n_no_kreis, 100.0 * (log["share_start_ags_equals_household_ags"] or 0.0),
    )
    return obs, log
