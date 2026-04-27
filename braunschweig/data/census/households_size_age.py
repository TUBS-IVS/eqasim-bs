"""Joint commune × sex × age × household-size marginal from Zensus 2022.

Loads DESTATIS table ``1000A-3082`` (``Bevölkerung kompakt``,
``Gemeinden × Alter (11 Altersklassen) × Geschlecht × Größe des privaten
Haushalts``) and emits a long-format DataFrame consumed by
``bavaria.ipf.prepare`` as the fifth IPF margin.

The table reports **persons** (``value_variable_code = PRS002``) — exactly
what the IPF model balances — so cell counts can be used directly as
targets without any HH→persons conversion.

Output schema::

    commune_id : str   (12-digit ARS, matches braunschweig.data.census.population)
    sex        : str   ("male" / "female")
    lower_age  : int   ALTKL2 lower bound (0, 5, 10, 15, 20, 25, 30, 40, 50, 60, 75)
    upper_age  : int   ALTKL2 upper bound exclusive (5, 10, …, 150)
    hh_size    : str   "1" / "2" / "3" / "4" / "5" / "6+"
    weight     : float persons in that cell (suppressed cells = 0)

Suppressed cells (``value_q == 'e'`` or ``value`` literal ``-`` / ``.``)
are interpreted as zero — that is the official DESTATIS convention for
disclosure-controlled cells in 1000A flat files.
"""

from __future__ import annotations

import os
import zipfile

import numpy as np
import pandas as pd


# ALTKL2 (11 Altersklassen) → (lower, upper) with upper exclusive.
ALTKL2_AGE_BOUNDS: dict[str, tuple[int, int]] = {
    "ALT000B005": (0, 5),
    "ALT005B009": (5, 10),
    "ALT010B014": (10, 15),
    "ALT015B019": (15, 20),
    "ALT020B024": (20, 25),
    "ALT025B029": (25, 30),
    "ALT030B039": (30, 40),
    "ALT040B049": (40, 50),
    "ALT050B059": (50, 60),
    "ALT060B074": (60, 75),
    "ALT075UM":   (75, 150),
}

# HSHGR2 → canonical hh_size bin (matches the 6 bins used downstream).
HSHGR2_SIZE: dict[str, str] = {
    "PERSON01":   "1",
    "PERSON02":   "2",
    "PERSON03":   "3",
    "PERSON04":   "4",
    "PERSON05":   "5",
    "PERSON06UM": "6+",
}

GESCH1_SEX: dict[str, str] = {"GESM": "male", "GESF": "female"}


def configure(context):
    context.config("data_path")
    context.config(
        "braunschweig.households_size_age_path",
        "braunschweig/1000A-3082_de_flat.zip",
    )


def _resolve_path(context) -> str:
    return os.path.join(
        context.config("data_path"),
        context.config("braunschweig.households_size_age_path"),
    )


def _read_csv_from_zip(path: str) -> pd.DataFrame:
    """Read the single CSV inside the 1000A-3082 ZIP with stable dtypes."""
    usecols = [
        "1_variable_attribute_code",  # ARS-12 (Gemeinde)
        "2_variable_attribute_code",  # ALTKL2 code
        "3_variable_attribute_code",  # GESCH1 code
        "4_variable_attribute_code",  # HSHGR2 code
        "value",
        "value_q",
    ]
    with zipfile.ZipFile(path) as zf:
        members = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not members:
            raise RuntimeError(f"No CSV inside {path}")
        with zf.open(members[0]) as fh:
            return pd.read_csv(fh, sep=";", dtype=str, usecols=usecols)


def _parse_value(series_value: pd.Series, series_q: pd.Series) -> pd.Series:
    """Parse the ``value`` column treating DESTATIS quality flags correctly.

    DESTATIS conventions for Zensus 2022 1000A flat files:

    * ``value == '-'``  → strict zero (cell is empty by design);
    * ``value_q == 'e'`` → SafeMosaic-perturbed: the value is a privacy-
      preserved estimate of the true count, but is the *best available*
      ground truth and must NOT be discarded;
    * ``value == '.'``  → not applicable / structurally undefined → zero.

    We therefore treat only un-numeric or dash/dot literals as zero, and
    keep ``e``-flagged values as-is.
    """
    raw = pd.to_numeric(series_value, errors="coerce")
    return raw.fillna(0.0).astype(float)


def execute(context) -> pd.DataFrame:
    path = _resolve_path(context)
    df = _read_csv_from_zip(path)

    df = df.rename(columns={
        "1_variable_attribute_code": "commune_id",
        "2_variable_attribute_code": "altkl2",
        "3_variable_attribute_code": "gesch1",
        "4_variable_attribute_code": "hshgr2",
    })

    # Drop rows that aggregate over any of the four cross-classifying
    # variables (``Insgesamt`` rows have NaN attribute codes in 1000A flat
    # files). We need fully-classified cells only.
    df = df.dropna(subset=["commune_id", "altkl2", "gesch1", "hshgr2"])

    df = df[df["altkl2"].isin(ALTKL2_AGE_BOUNDS)]
    df = df[df["gesch1"].isin(GESCH1_SEX)]
    df = df[df["hshgr2"].isin(HSHGR2_SIZE)]

    df["weight"] = _parse_value(df["value"], df["value_q"])

    # Map codes → human-readable values and explode age bounds to two cols.
    bounds = df["altkl2"].map(ALTKL2_AGE_BOUNDS)
    df["lower_age"] = bounds.map(lambda t: t[0]).astype(int)
    df["upper_age"] = bounds.map(lambda t: t[1]).astype(int)
    df["sex"] = df["gesch1"].map(GESCH1_SEX).astype("category")
    df["hh_size"] = df["hshgr2"].map(HSHGR2_SIZE).astype("category")

    out = (
        df[["commune_id", "sex", "lower_age", "upper_age", "hh_size", "weight"]]
        .reset_index(drop=True)
    )

    total_persons = float(out["weight"].sum())
    n_communes = out["commune_id"].nunique()
    print(
        "[braunschweig.data.census.households_size_age] "
        f"loaded {len(out):,} cells across {n_communes:,} communes, "
        f"total persons (post-suppression) = {total_persons:,.0f}"
    )

    if total_persons <= 0:
        raise RuntimeError(
            f"1000A-3082 returned zero persons — check {path}"
        )

    return out


def validate(context):
    path = _resolve_path(context)
    if not os.path.exists(path):
        raise RuntimeError(
            f"Missing Zensus 2022 1000A-3082 ZIP at {path}. "
            "Download from https://ergebnisse.zensus2022.de "
            "(DOWNLOAD_CHECKLIST_BS.md)."
        )
    return os.path.getsize(path)
