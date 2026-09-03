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
