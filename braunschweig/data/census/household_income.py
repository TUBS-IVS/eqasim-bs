"""
Household-income distribution for the Braunschweig region, derived from the
MiD 2023 regional sample, Tabelle H4 'Ökonomischer
Status des Haushalts' (Zeilengruppe Haushaltsgröße).

MiD H4 reports the BMDV-defined quintile status (needs-adjusted net
equivalent income, OECD scale) crossed with household size; the enriched
synthesis samples household_income per household_size from it.

The quintile status is labelled with the € class labels of the legacy GENESIS
12211-101 vocabulary. Every class is consumed downstream: each label maps to
its economic-status category (``economic_status.py ::
_build_economic_status_by_income_class``) and to a € midpoint
(``load_class_midpoint_eur``, used for ``household_income_eur`` and by
``income_distribution.py :: _income_class_from_eur``). The label is a name for
the BMDV quintile, with these defining thresholds:

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


# € class label ↔ position in the H4 quintile vector. All five entries are
# consumed downstream (economic-status mapping and class-midpoint lookup).
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

    # Fallback transparency: each (household_size, income_class) cell takes its
    # quintile share from the PRIMARY H4 vector position ``idx`` when that
    # position exists in the loaded share tuple; a position absent from the
    # vector would be a FALLBACK (a malformed H4 table that does not carry all
    # five BMDV quintiles). We count the primary/fallback split and log the
    # fallback rate (escalated to WARNING above the threshold) so the mapping
    # health is observable. This is purely observational and does not change any
    # computed weight: a genuine miss still aborts via the explicit raise below
    # rather than being silently defaulted.
    rows = []
    n_primary = 0
    n_fallback = 0
    fallback_cells = []
    for size, shares in income_by_size.items():
        for income_class, idx in INCOME_CLASS_MAP:
            if idx < len(shares):
                n_primary += 1
                rows.append({
                    "household_size": size,
                    "income_class": income_class,
                    "weight": shares[idx] * 1e6,   # scale to 'persons per million'
                })
            else:
                n_fallback += 1
                fallback_cells.append((size, income_class, idx))

    n_total = n_primary + n_fallback
    fallback_rate = (n_fallback / n_total) if n_total else 0.0
    if n_fallback:
        print(
            f"WARNING: [braunschweig.household_income] INCOME_CLASS_MAP fallback "
            f"for {n_fallback}/{n_total} cells ({fallback_rate:.2%}); primary hit "
            f"{n_primary}. Missing H4 quintile positions {fallback_cells}."
        )
        raise ValueError(
            "[braunschweig.household_income] INCOME_CLASS_MAP references H4 "
            f"quintile positions absent from the loaded share vectors: "
            f"{fallback_cells}. The MiD H4 income-by-size table must carry all "
            f"{len(INCOME_CLASS_MAP)} BMDV quintiles per household size."
        )
    print(
        f"[braunschweig.household_income] INCOME_CLASS_MAP PRIMARY lookup hit all "
        f"{n_primary}/{n_total} cells (fallback rate 0.00%)."
    )

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
