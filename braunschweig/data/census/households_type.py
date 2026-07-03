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
    weight     : float persons in that cell (Insgesamt rows excluded)

Note: the value is PERSONS living in a household of the given (size, type), not
household counts. This is pinned by the committed regression test
``tests/test_hh_size_margin.py::TestHouseholdTypeLoader.test_zgb_persons_match_zensus_reference``,
which asserts the ZGB total lands in the ~1.135 M person range (the Zensus 2022
ZGB population), well above the ZGB household count.
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


# Default 1000A-2081 archive name, kept in sync with the configure() default so a
# standalone (context-free) caller resolves the same file the stage uses.
DEFAULT_HOUSEHOLDS_TYPE_PATH = "braunschweig/1000A-2081_de_flat.zip"


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.households_type_path",
        DEFAULT_HOUSEHOLDS_TYPE_PATH,
    )


def _path_for_data_path(data_path: str) -> str:
    """Resolve the 1000A-2081 archive from a bare ``data_path`` string.

    This mirrors :func:`_resolve_path` but does not require a synpp ``context``,
    so the validation harness (``braunschweig.analysis.population_validation``)
    can read the same per-commune household-size source the IPF size margin uses
    without spinning up a synpp pipeline. The default archive name is the same
    one ``configure`` registers; a config override is therefore NOT reflected
    here, which is acceptable because no project config overrides this key.
    """
    return os.path.join(data_path, DEFAULT_HOUSEHOLDS_TYPE_PATH)


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
            # BUG-006 fix: GENESIS exports are UTF-8 with BOM; without explicit
            # encoding pandas uses the system locale (cp1252 on Windows) which
            # mangles ü/ö/ä in ARS Gemeinde labels and silently drops rows.
            return pd.read_csv(fh, sep=";", dtype=str, usecols=usecols,
                               encoding="utf-8-sig")


def _parse_long(df: pd.DataFrame) -> pd.DataFrame:
    """Parse the raw 1000A-2081 frame into ``commune_id x hh_size x hh_type``.

    Shared by :func:`execute` and the context-free
    :func:`load_household_size_by_commune` so both apply identical filtering and
    suppression handling.
    """
    df = df.rename(columns={
        "1_variable_attribute_code": "commune_id",
        "2_variable_attribute_code": "hshgr2",
        "3_variable_attribute_code": "hshtp1",
    })
    df = df.dropna(subset=["commune_id", "hshgr2", "hshtp1"])
    df = df[df["hshgr2"].isin(HSHGR2_SIZE)]
    df = df[df["hshtp1"].isin(HSHTP1_TYPE)]

    df = df.copy()
    df["weight"]  = _parse_value(df["value"], df["value_q"])
    df["hh_size"] = df["hshgr2"].map(HSHGR2_SIZE).astype("category")
    df["hh_type"] = df["hshtp1"].map(HSHTP1_TYPE).astype("category")

    return df[["commune_id", "hh_size", "hh_type", "weight"]].reset_index(drop=True)


def load_household_size_by_commune(data_path: str, scope_prefixes) -> pd.DataFrame:
    """Per-commune household-size distribution from Zensus 2022 1000A-2081.

    Context-free counterpart of the stage's per-commune size source (the same
    table :func:`braunschweig.ipf.prepare._build_household_size_margin` consumes).
    Aggregates over ``hh_type`` to give one weight per
    ``(commune_id, hh_size)`` cell, restricted to communes whose 5-digit Kreis
    prefix is in ``scope_prefixes``. Used by the validation harness target
    loader; no synpp context required.

    Returns columns ``[commune_id, hh_size, weight]`` where ``weight`` is PERSONS
    living in a household of that size class (the 1000A statistic reports persons,
    not household counts), suppressed cells = 0.
    """
    df = _parse_long(_read_csv_from_zip(_path_for_data_path(data_path)))
    df["commune_id"] = df["commune_id"].astype(str)
    scope = {str(p) for p in scope_prefixes}
    df = df[df["commune_id"].str[:5].isin(scope)]
    out = (df.groupby(["commune_id", "hh_size"], observed=True, as_index=False)["weight"]
             .sum())
    if out.empty or out["weight"].sum() <= 0:
        raise RuntimeError(
            f"No Zensus 1000A-2081 persons for scope {sorted(scope)} "
            f"under {data_path}"
        )
    return out


def execute(context) -> pd.DataFrame:
    path = _resolve_path(context)
    out = _parse_long(_read_csv_from_zip(path))
    print(
        "[braunschweig.data.census.households_type] "
        f"loaded {len(out):,} cells, total persons = {out['weight'].sum():,.0f}"
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
