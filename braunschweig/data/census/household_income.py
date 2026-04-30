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

The numeric H4 percentages and the class-midpoint € lookup live in
``eqasim-data/data/braunschweig/mid/mid2023_H4_income_by_size.csv`` and
``mid2023_class_midpoint_eur.csv`` (see scripts/seed_mid_constraint_tables.py).
"""

import pandas as pd

from braunschweig.data.mid.reference_tables import (
    load_class_midpoint_eur,
    load_income_by_size,
)


# Bavaria GENESIS €-class ↔ position in the H4 quintile vector.
# Only the "5000+" mapping is consumed downstream (high_income flag).
INCOME_CLASS_MAP = [
    ("0-500",     0),   # sehr niedrig
    ("1500-2000", 1),   # niedrig
    ("2600-3000", 2),   # mittel
    ("3600-4500", 3),   # hoch
    ("5000+",     4),   # sehr hoch
]


def configure(context):
    context.config("data_path")


def execute(context):
    data_path = context.config("data_path")
    income_by_size = load_income_by_size(data_path)

    rows = []
    for size, shares in income_by_size.items():
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


# Re-export for callers (e.g. braunschweig.synthesis.population.enriched)
# that expect a module-level ``CLASS_MIDPOINT_EUR`` constant. Loaded lazily
# the first time it is accessed so import works without a configured
# data_path.
def __getattr__(name):
    if name == "CLASS_MIDPOINT_EUR":
        return load_class_midpoint_eur(_default_data_path())
    raise AttributeError(name)


def _default_data_path() -> str:
    """Fallback ``data_path`` for module-level globals (tests / scripts)."""
    import os
    from pathlib import Path
    env = os.environ.get("EQASIM_DATA_PATH")
    if env:
        return env
    return str(Path(__file__).resolve().parents[3] / "eqasim-data" / "data")
