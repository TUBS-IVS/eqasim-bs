"""Auxiliary commune × hh-size × hh-type table from Zensus 2022 (1000A-2081).

Used **only** by the validation harness (``scripts.validate_bs_10pct``) to
cross-check the post-IPF synthesis against the Familien-Haushaltstyp-axis.
NOT consumed by the IPF itself — adding it as a balanced margin would
roughly double the cell count for marginal benefit (the dominant signal —
1P-HH share — is already captured exactly via 1000A-3082).

Output schema::

    commune_id : str   (12-digit ARS)
    hh_size    : str   "1" / "2" / "3" / "4" / "5" / "6+"
    hh_type    : str   "single" / "couple" / "couple_with_children" /
                       "single_parent" / "other_multi"
    weight     : float households in that cell (Insgesamt rows excluded)
"""

from __future__ import annotations

import os
import zipfile

import pandas as pd

from .households_size_age import HSHGR2_SIZE, _parse_value


# HSHTP1 (HH-Typ nach Familien) — 5 mutually exclusive classes.
# NOTE: code labels differ from the GENESIS schema (1000A-2087 uses
# "HSH-PAAR-KIND" etc.); 1000A-2081 ships shorter codes.
HSHTP1_TYPE: dict[str, str] = {
    "HSH-EIN":    "single",                # Einpersonenhaushalt
    "PAAR-KINDX": "couple",                # Paare ohne Kind(er)
    "PAAR-KIND":  "couple_with_children",  # Paare mit Kind(ern)
    "ALLEINERZ":  "single_parent",         # Alleinerziehende
    "HSH-MEHR":   "other_multi",           # Mehrpersonenhaushalte ohne Kernfamilie
}


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.households_type_path",
        "braunschweig/1000A-2081_de_flat.zip",
    )


def _resolve_path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("braunschweig.households_type_path"),
    )


def _read_csv_from_zip(path: str) -> pd.DataFrame:
    usecols = [
        "1_variable_attribute_code",  # ARS-12
        "2_variable_attribute_code",  # HSHGR2
        "3_variable_attribute_code",  # HSHTP1
        "value",
        "value_q",
    ]
    with zipfile.ZipFile(path) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not members:
            raise RuntimeError(f"No CSV inside {path}")
        with zf.open(members[0]) as fh:
            return pd.read_csv(fh, sep=";", dtype=str, usecols=usecols)


def execute(context) -> pd.DataFrame:
    path = _resolve_path(context)
    df = _read_csv_from_zip(path)

    df = df.rename(columns={
        "1_variable_attribute_code": "commune_id",
        "2_variable_attribute_code": "hshgr2",
        "3_variable_attribute_code": "hshtp1",
    })
    df = df.dropna(subset=["commune_id", "hshgr2", "hshtp1"])
    df = df[df["hshgr2"].isin(HSHGR2_SIZE)]
    df = df[df["hshtp1"].isin(HSHTP1_TYPE)]

    df["weight"]  = _parse_value(df["value"], df["value_q"])
    df["hh_size"] = df["hshgr2"].map(HSHGR2_SIZE).astype("category")
    df["hh_type"] = df["hshtp1"].map(HSHTP1_TYPE).astype("category")

    out = df[["commune_id", "hh_size", "hh_type", "weight"]].reset_index(drop=True)
    print(
        "[braunschweig.data.census.households_type] "
        f"loaded {len(out):,} cells, total HHs = {out['weight'].sum():,.0f}"
    )
    return out


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            f"Missing Zensus 2022 1000A-2081 ZIP at {path}. "
            "Download from https://ergebnisse.zensus2022.de."
        )
    return os.path.getsize(path)
