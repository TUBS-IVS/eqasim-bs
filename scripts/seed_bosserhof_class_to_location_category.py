"""Seed the committed Bosserhof-class -> location-category mapping CSV.

Provenance: the 44 classes are the observed `bosserhof_class_clean` values in
building_activity_potentials.parquet (ZGB). The location-category assignment is
a curated domain mapping designed to partition leisure and errand eqasim purposes
into specific building-candidate cohorts. This script is the only supported way
to (re)generate the CSV; hard-coding the mapping in runtime modules is prohibited.

ASSUMPTION: hotels (lodging class) are excluded from leisure_gastronomy because
they are accommodations, not dining establishments, and are not SrV-grounded
location candidates.
"""
from __future__ import annotations

import argparse

# class -> location_category. Only 19 of the 44 classes are candidates;
# unmapped classes fall to the aggregate pools (outdoor/visit/misc).
CATEGORY_BY_CLASS = {
    # leisure_culture
    "entertainment culture": "leisure_culture",
    "large cinemas": "leisure_culture",
    "arenas large events": "leisure_culture",
    "theme parks": "leisure_culture",
    # leisure_gastronomy (hotels excluded: lodging, not a gastronomy destination)
    "restaurants gastronomy": "leisure_gastronomy",
    # leisure_sports
    "fitness wellness": "leisure_sports",
    "facilities for culture leisure and sports": "leisure_sports",
    "large discos fun leisure pools": "leisure_sports",
    # errand_authority_medical
    "public facilities": "errand_authority_medical",
    "hospitals": "errand_authority_medical",
    "nursing homes": "errand_authority_medical",
    # errand_service
    "services": "errand_service",
    "customer oriented services": "errand_service",
    "customer service": "errand_service",
    "business oriented services": "errand_service",
    "craft businesses": "errand_service",
    "craft courtyards": "errand_service",
    "vehicle electrical repair": "errand_service",
    "transport": "errand_service",
}


def write_mapping(path: str, purpose_path: str = None) -> None:
    import pandas as pd

    # Load the purpose mapping for consistency checks.
    if purpose_path is None:
        purpose_path = "eqasim-data/data/braunschweig/buildings/bosserhof_class_to_purpose.csv"
    purpose_df = pd.read_csv(purpose_path)
    purpose_map = dict(zip(purpose_df["bosserhof_class"], purpose_df["eqasim_purpose"]))
    other_dest_map = dict(zip(purpose_df["bosserhof_class"], purpose_df["other_destination"]))

    # Validate consistency rules.
    for cls, category in CATEGORY_BY_CLASS.items():
        if cls not in purpose_map:
            raise ValueError(f"class '{cls}' not found in {purpose_path}")

        if category.startswith("errand_"):
            # errand_* categories must have other_destination == True.
            if not other_dest_map[cls]:
                raise ValueError(
                    f"class '{cls}' mapped to '{category}' but has "
                    f"other_destination={other_dest_map[cls]} (expected True)")
        elif category.startswith("leisure_"):
            # leisure_* categories must have eqasim_purpose == "leisure".
            if purpose_map[cls] != "leisure":
                raise ValueError(
                    f"class '{cls}' mapped to '{category}' but has "
                    f"eqasim_purpose='{purpose_map[cls]}' (expected 'leisure')")

    # Write the CSV with provenance header.
    rows = [{"bosserhof_class": c, "location_category": cat}
            for c, cat in sorted(CATEGORY_BY_CLASS.items())]

    # Write with comment header lines.
    with open(path, "w") as f:
        f.write("# Source: design spec seed proposal 2026-08-12\n")
        f.write("# ASSUMPTION: hotels excluded from leisure_gastronomy (lodging, not dining)\n")
        f.write("# Do not edit manually; regenerate with scripts/seed_bosserhof_class_to_location_category.py\n")

    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, mode="a")
    print(f"[seed_bosserhof_location_category] wrote {len(rows)} classes to {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output",
                    default="eqasim-data/data/braunschweig/buildings/bosserhof_class_to_location_category.csv")
    ap.add_argument("--purpose",
                    default="eqasim-data/data/braunschweig/buildings/bosserhof_class_to_purpose.csv",
                    help="Path to bosserhof_class_to_purpose.csv for consistency checks")
    args = ap.parse_args()
    write_mapping(args.output, args.purpose)
