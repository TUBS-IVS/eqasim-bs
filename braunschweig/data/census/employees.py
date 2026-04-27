"""Employees-at-workplace per municipality from GENESIS 13111-01-03-5.

Replaces `braunschweig.data.census.employees`. Used by `bavaria.gravity.model`
as the attraction vector for the work-flow gravity model.

Input (XLSX) 13111-01-03-5 — SvB **am Arbeitsort** nach Geschlecht und
Nationalitaet (Gemeinden, Stichtag 30.06.):
    col 0  AGS (8-digit Gemeinde, or 5-digit kreisfreie Stadt, or aggregates)
    col 1  name
    col 2  all_total (Insgesamt, Insgesamt)
    col 3  all_male
    col 4  all_female
    col 5  foreign_total
    col 6  foreign_male
    col 7  foreign_female

Output schema (matches braunschweig.data.census.employees):
    (commune_id, weight)
"""

import os
import pandas as pd


COLUMN_NAMES = [
    "ags", "name",
    "all_total", "all_male", "all_female",
    "foreign_total", "foreign_male", "foreign_female",
]


def configure(context):
    context.config("data_path")
    context.config("braunschweig.employees_path",
                   "braunschweig/13111-01-03-5.xlsx")
    context.stage("eqasim_common.spatial.codes")


def _coerce_int(series: pd.Series) -> pd.Series:
    return (pd.to_numeric(series, errors="coerce")
              .fillna(0)
              .astype(int))


def execute(context):
    path = os.path.join(context.config("data_path"),
                        context.config("braunschweig.employees_path"))

    df = pd.read_excel(path, header=None, skiprows=8, names=COLUMN_NAMES)

    df = df.dropna(subset=["ags"]).copy()
    df["ags"] = df["ags"].astype(str).str.strip()

    # Keep only Gemeinde rows (8 digits) and kreisfreie Staedte (5 digits).
    # Aggregate levels (Bundesland=2, Regbz=3, Kreis=5) overlap with kreisfreie
    # (also 5-digit AGS) — distinguish by whether the 5-digit entry is in scope.
    df_codes = context.stage("eqasim_common.spatial.codes")
    scope_kreise = set(df_codes["departement_id"].astype(str).unique())

    # Normalize 5-digit kreisfreie AGS to 8-digit (BS 03101 -> 03101000).
    mask_krfr = (df["ags"].str.len() == 5) & df["ags"].isin(scope_kreise)
    df.loc[mask_krfr, "ags"] = df.loc[mask_krfr, "ags"] + "000"

    # Restrict to 8-digit AGS inside ZGB scope.
    df = df[
        (df["ags"].str.len() == 8)
        & df["ags"].str[:5].isin(scope_kreise)
    ].copy()

    df["weight"] = _coerce_int(df["all_total"])

    # Map AGS (8-digit) -> ARS/commune_id (12-digit) via the codes table.
    df_codes_scope = df_codes[df_codes["departement_id"].astype(str).isin(scope_kreise)]
    df = df.merge(
        df_codes_scope[["ags", "commune_id"]].astype(str),
        on="ags", how="inner",
    )

    df = df[["commune_id", "weight"]].copy()
    df["weight"] = df["weight"].astype(float)

    total = df["weight"].sum()
    print(f"[braunschweig.employees] {len(df)} communes, "
          f"total SvB Arbeitsort = {total:,.0f}")

    return df


def validate(context):
    path = os.path.join(context.config("data_path"),
                        context.config("braunschweig.employees_path"))
    if not os.path.exists(path):
        raise RuntimeError(f"GENESIS 13111-01-03-5 XLSX missing: {path}")
    return os.path.getsize(path)
