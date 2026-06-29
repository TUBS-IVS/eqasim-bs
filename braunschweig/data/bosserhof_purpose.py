"""Bosserhof building-class -> eqasim purpose mapping (committed reference table).

The CSV is the single source of truth for which building classes are genuine
`other` (errand) destinations. Hard-coding the mapping in Python is prohibited;
regenerate the CSV with scripts/seed_bosserhof_class_to_purpose.py.
"""
from __future__ import annotations

import pandas as pd

# The 11 errand-destination classes (W_ZWECK 5 private Erledigungen affine).
# Documented in docs/superpowers/specs/2026-06-28-smart-other-potential-design.md.
OTHER_CLASSES = (
    "services", "customer oriented services", "customer service",
    "business oriented services", "public facilities", "hospitals",
    "nursing homes", "vehicle electrical repair", "craft businesses",
    "craft courtyards", "transport",
)

_VALID_PURPOSES = {"work", "shop", "leisure", "education", "other"}
_COLUMNS = ["bosserhof_class", "eqasim_purpose", "other_destination"]


def load_mapping(path: str) -> pd.DataFrame:
    """Load + validate the class->purpose mapping CSV. Fail-fast on schema errors."""
    df = pd.read_csv(path)
    missing = [c for c in _COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"bosserhof mapping CSV missing columns {missing} in {path}")
    df = df[_COLUMNS].copy()
    df["bosserhof_class"] = df["bosserhof_class"].astype(str)
    bad = sorted(set(df["eqasim_purpose"]) - _VALID_PURPOSES)
    if bad:
        raise ValueError(f"bosserhof mapping CSV has unknown eqasim_purpose values {bad}")
    df["other_destination"] = df["other_destination"].astype(bool)
    # Internal consistency: other_destination iff eqasim_purpose == 'other'.
    inconsistent = df[df["other_destination"] != (df["eqasim_purpose"] == "other")]
    if len(inconsistent):
        raise ValueError(
            "bosserhof mapping: other_destination must equal (eqasim_purpose=='other'); "
            f"offending classes: {list(inconsistent['bosserhof_class'])}")
    return df.reset_index(drop=True)


def configure(context):
    context.config("bosserhof_class_purpose_path",
                   "eqasim-data/data/braunschweig/buildings/bosserhof_class_to_purpose.csv")


def execute(context):
    return load_mapping(context.config("bosserhof_class_purpose_path"))
