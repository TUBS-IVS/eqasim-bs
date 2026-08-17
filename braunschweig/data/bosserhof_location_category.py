"""Bosserhof building-class -> location-category mapping (committed reference table).

The CSV is the single source of truth for which building classes map to which
location categories. Hard-coding the mapping in Python is prohibited;
regenerate the CSV with scripts/seed_bosserhof_class_to_location_category.py.

The loader validates consistency against bosserhof_class_to_purpose.csv to detect
drift: errand_* categories must have other_destination==True; leisure_* categories
must have eqasim_purpose=="leisure". Mapped classes must exist in the purpose mapping.
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


def load_category_mapping(path: str, purpose_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Load + validate the class->location-category mapping CSV. Fail-fast on schema errors.

    If purpose_df is provided (from bosserhof_purpose.load_mapping), enforces consistency:
    - errand_* categories must have other_destination==True in purpose_df
    - leisure_* categories must have eqasim_purpose=="leisure" in purpose_df
    - all mapped classes must exist in purpose_df
    """
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

    # Validate consistency against purpose mapping if provided.
    if purpose_df is not None:
        purpose_map = dict(zip(purpose_df["bosserhof_class"], purpose_df["eqasim_purpose"]))
        other_dest_map = dict(zip(purpose_df["bosserhof_class"], purpose_df["other_destination"]))

        for idx, row in df.iterrows():
            cls = row["bosserhof_class"]
            category = row["location_category"]

            # Check that mapped class exists in purpose mapping.
            if cls not in purpose_map:
                raise ValueError(
                    f"bosserhof class '{cls}' in location mapping not found in purpose mapping")

            if category.startswith("errand_"):
                # errand_* categories must have other_destination == True.
                if not other_dest_map[cls]:
                    raise ValueError(
                        f"class '{cls}' mapped to '{category}' but has "
                        f"other_destination={other_dest_map[cls]} in purpose mapping (expected True)")
            elif category.startswith("leisure_"):
                # leisure_* categories must have eqasim_purpose == "leisure".
                if purpose_map[cls] != "leisure":
                    raise ValueError(
                        f"class '{cls}' mapped to '{category}' but has "
                        f"eqasim_purpose='{purpose_map[cls]}' in purpose mapping (expected 'leisure')")

    return df.reset_index(drop=True)


def configure(context):
    context.config("bosserhof_class_location_category_path",
                   "eqasim-data/data/braunschweig/buildings/bosserhof_class_to_location_category.csv")
    context.config("bosserhof_class_purpose_path",
                   "eqasim-data/data/braunschweig/buildings/bosserhof_class_to_purpose.csv")


def execute(context):
    # Load the purpose mapping for consistency validation.
    from braunschweig.data.bosserhof_purpose import load_mapping as load_purpose_mapping
    purpose_df = load_purpose_mapping(context.config("bosserhof_class_purpose_path"))
    return load_category_mapping(
        context.config("bosserhof_class_location_category_path"),
        purpose_df=purpose_df)
