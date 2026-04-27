"""
Household-income distribution for the Braunschweig region, derived from the
MiD 2023 regional report 'Großraum Braunschweig', Tabelle H4 'Ökonomischer
Status des Haushalts' (Zeilengruppe Haushaltsgröße).

This replaces bavaria/data/census/household_income.py which depends on
bavaria/12211-101.xlsx — a GENESIS extract that we do not have for Lower
Saxony. MiD H4 reports the BMDV-defined quintile status (needs-adjusted
net equivalent income, OECD scale) crossed with household size, which
matches exactly what bavaria/synthesis/population/enriched.py requires
(it samples household_income per household_size).

Schema compatibility: Bavaria emits the € classes of GENESIS 12211-101.
Downstream code in enriched.py only uses income_class to set
    high_income = (household_income == "5000+")
so the mapping MiD-Status → Bavaria-€-Class only needs to be accurate at
the top bracket. We use BMDV's defining thresholds for the mid-brackets
as a best-effort approximation:

    very_low    → "0-500"        (< 90% median equivalent net income)
    low         → "1500-2000"    (90-110%)
    medium      → "2600-3000"    (110-150%)
    high        → "3600-4500"    (150-200%)
    very_high   → "5000+"        (> 200%)
"""

import numpy as np
import pandas as pd


# MiD 2023 Tabelle H4 — Zeilengruppe 'Haushaltsgröße', Zeilen% in households.
# (Rows from the PDF extraction — 5P+ needed a third parse since it trails.)
#   size  : (sehr_niedrig, niedrig, mittel, hoch, sehr_hoch)
# R-C splits the upstream household_size IPF target into 5 + 6+ bins; MiD
# H4 lumps them as "5 Personen und mehr", so we duplicate the same income
# distribution onto both keys to preserve the join.
INCOME_BY_SIZE = {
    "1":  (0.16, 0.14, 0.39, 0.26, 0.03),
    "2":  (0.04, 0.15, 0.28, 0.38, 0.15),
    "3":  (0.03, 0.03, 0.17, 0.55, 0.22),
    "4":  (0.02, 0.04, 0.23, 0.51, 0.19),
    "5":  (0.04, 0.08, 0.30, 0.44, 0.14),   # from H4 row "5 Personen und mehr"
    "6+": (0.04, 0.08, 0.30, 0.44, 0.14),   # H4 lumps with 5P
}

# Approximation: MiD 5-class BMDV status → Bavaria GENESIS € bucket.
# Only the "5000+" mapping is consumed downstream (high_income flag).
INCOME_CLASS_MAP = [
    ("0-500",     0),   # sehr niedrig
    ("1500-2000", 1),   # niedrig
    ("2600-3000", 2),   # mittel
    ("3600-4500", 3),   # hoch
    ("5000+",     4),   # sehr hoch
]

# Class midpoints for computing a numeric €-income per person. Open upper
# bound "5000+" is set to 6000 € (conservative estimate of the mean HH
# income above the BMDV 5000 threshold per DESTATIS 2022 tables).
# Consumed by braunschweig.synthesis.population.enriched to derive the
# INKAR-scaled ``household_income_eur`` column.
CLASS_MIDPOINT_EUR = {
    "0-500":     250.0,
    "1500-2000": 1750.0,
    "2600-3000": 2800.0,
    "3600-4500": 4050.0,
    "5000+":     6000.0,
}


def configure(context):
    pass


def execute(context):
    rows = []
    for size, shares in INCOME_BY_SIZE.items():
        for income_class, idx in INCOME_CLASS_MAP:
            rows.append({
                "household_size": size,
                "income_class": income_class,
                "weight": shares[idx] * 1e6,   # scale to 'persons per million'
            })

    df = pd.DataFrame(rows)
    df["household_size"] = df["household_size"].astype("category")
    df["income_class"] = df["income_class"].astype("category")
    return df[["household_size", "income_class", "weight"]]
