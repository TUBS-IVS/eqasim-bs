"""Bosserhof building-class -> location-category mapping (committed reference table).

The CSV is the single source of truth for which building classes map to which
location categories. Hard-coding the mapping in Python is prohibited;
regenerate the CSV with scripts/seed_bosserhof_class_to_location_category.py.
"""
from __future__ import annotations

import pandas as pd

# The 5 location categories that have building candidates. Outdoor, visit,
# and misc are not building categories and are served by the aggregate pools.
BUILDING_CATEGORIES = (
    "leisure_culture",
    "leisure_gastronomy",
    "leisure_sports",
    "errand_authority_medical",
    "errand_service",
)

_COLUMNS = ["bosserhof_class", "location_category"]


def load_category_mapping(path: str) -> pd.DataFrame:
    """Load + validate the class->location-category mapping CSV. Fail-fast on schema errors."""
    df = pd.read_csv(path, comment="#")
    missing = [c for c in _COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"bosserhof mapping CSV missing columns {missing} in {path}")
    df = df[_COLUMNS].copy()
    df["bosserhof_class"] = df["bosserhof_class"].astype(str)
    bad = sorted(set(df["location_category"]) - set(BUILDING_CATEGORIES))
    if bad:
        raise ValueError(f"bosserhof mapping CSV has unknown location_category values {bad}")
    # Check for duplicate classes.
    duplicates = df[df.duplicated(subset=["bosserhof_class"], keep=False)]
    if len(duplicates):
        raise ValueError(
            f"bosserhof mapping CSV has duplicate bosserhof_class entries: "
            f"{list(duplicates['bosserhof_class'])}")
    return df.reset_index(drop=True)


def configure(context):
    context.config("bosserhof_class_location_category_path",
                   "eqasim-data/data/braunschweig/buildings/bosserhof_class_to_location_category.csv")


def execute(context):
    return load_category_mapping(context.config("bosserhof_class_location_category_path"))
