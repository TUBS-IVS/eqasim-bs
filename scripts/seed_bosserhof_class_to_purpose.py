"""Seed the committed Bosserhof-class -> eqasim-purpose mapping CSV.

Provenance: the 44 classes are the observed `bosserhof_class_clean` values in
building_activity_potentials.parquet (ZGB). The purpose assignment is a curated
domain mapping documented in
docs/superpowers/specs/2026-06-28-smart-other-potential-design.md. `other` =
errand destinations (MiD W_ZWECK 5 private Erledigungen affine). This script is
the only supported way to (re)generate the CSV; hard-coding the mapping in
runtime modules is prohibited.
"""
from __future__ import annotations

import argparse

# class -> eqasim purpose. other_destination is derived as (purpose == 'other').
CLASS_PURPOSE = {
    # work
    "normal office": "work", "open plan office": "work",
    "industrial operations production": "work",
    "highly productive industries machine material or space intensive": "work",
    "yards depots storage areas construction yards": "work",
    "others industrial": "work", "wholesale": "work",
    "suppliers for car dealerships": "work", "research institutes": "work",
    "car dealerships": "work",
    # shop
    "retail small scale": "shop", "retail": "shop", "discount stores": "shop",
    "diy stores": "shop", "shopping centers": "shop", "furniture stores": "shop",
    "hypermarkets superstores": "shop", "department stores": "shop",
    "self service department stores": "shop", "factory outlet centers": "shop",
    # leisure
    "facilities for culture leisure and sports": "leisure",
    "entertainment culture": "leisure", "restaurants gastronomy": "leisure",
    "hotels": "leisure", "hotels with conference areas": "leisure",
    "fitness wellness": "leisure", "arenas large events": "leisure",
    "large discos fun leisure pools": "leisure", "large cinemas": "leisure",
    "theme parks": "leisure",
    # education
    "schools": "education", "kindergartens": "education", "universities": "education",
    # other (errand destinations -- the whitelist)
    "services": "other", "customer oriented services": "other",
    "customer service": "other", "business oriented services": "other",
    "public facilities": "other", "hospitals": "other", "nursing homes": "other",
    "vehicle electrical repair": "other", "craft businesses": "other",
    "craft courtyards": "other", "transport": "other",
}


def write_mapping(path: str) -> None:
    import pandas as pd
    rows = [{"bosserhof_class": c, "eqasim_purpose": p,
             "other_destination": (p == "other")}
            for c, p in sorted(CLASS_PURPOSE.items())]
    pd.DataFrame(rows).to_csv(path, index=False)
    print(f"[seed_bosserhof] wrote {len(rows)} classes to {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--output",
                    default="eqasim-data/data/braunschweig/buildings/bosserhof_class_to_purpose.csv")
    write_mapping(ap.parse_args().output)
