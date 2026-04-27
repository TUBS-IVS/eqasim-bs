"""Employment marginals for the ZGB Braunschweig region from BA Gemeindedaten.

Replaces `bavaria.data.census.employment`. Reads the BA Statistik
"Gemeindedaten der sozialversicherungspflichtig Beschäftigten" (XLSX), filters
for the ZGB-8 Kreise and emits totals per (Kreis, sex) with a single age class.

Limitation: The BA Gemeindedaten tabulate sex and age class independently
(no sex × age cross-tabulation). For v1 we therefore drop the age dimension
for the employment constraint and rely on the population marginal to shape
the age distribution of employees.

Input (XLSX):
    eqasim-data/data/braunschweig/gemband-dlk-0-202506-xlsx.xlsx
    sheet "Gemeindedaten", header rows 0..7, data from row 8.
    Columns:
      0  AGS (8-digit)
      1  Gemeinde/Landkreis name
      2  SvB Wohnort (total)
      3  Männer
      4  Frauen
      5  Deutsche
      6  Ausländer
      7  unter 20 Jahre
      8  20 - unter 25 Jahre
      9  55 Jahre und älter
      10 SvB Arbeitsort
      11 Wohnort = Arbeitsort
      12 Einpendler
      13 Auspendler
      14 Zahl der Betriebe

Output schema (matches bavaria.data.census.employment):
    (departement_id, age_class, sex, weight)
"""

import os
import pandas as pd


COLUMN_NAMES = [
    "ags", "name", "svb_wohn",
    "svb_m", "svb_f",
    "svb_de", "svb_auslander",
    "svb_u20", "svb_20_25", "svb_55plus",
    "svb_arbeit", "wohn_eq_arbeit",
    "einpendler", "auspendler",
    "n_betriebe",
]


def configure(context):
    context.config("data_path")
    context.config("braunschweig.employment_gemband_path",
                   "braunschweig/gemband-dlk-0-202506-xlsx.xlsx")
    context.stage("bavaria.data.spatial.codes")


def _coerce_int(series: pd.Series) -> pd.Series:
    # BA uses "*" or "." for suppressed cells; treat as 0.
    return (pd.to_numeric(series, errors="coerce")
              .fillna(0)
              .astype(int))


def execute(context):
    path = os.path.join(context.config("data_path"),
                        context.config("braunschweig.employment_gemband_path"))

    df = pd.read_excel(
        path, sheet_name="Gemeindedaten",
        header=None, skiprows=8, names=COLUMN_NAMES,
    )

    df = df.dropna(subset=["ags"]).copy()
    df["ags"] = df["ags"].astype(str).str.strip().str.zfill(8)

    # Filter to ZGB-8 Kreise via the first 5 digits of the AGS
    df_codes = context.stage("bavaria.data.spatial.codes")
    scope_kreise = set(df_codes["departement_id"].astype(str).unique())
    df = df[df["ags"].str[:5].isin(scope_kreise)].copy()

    # Coerce numeric cols
    for col in ["svb_m", "svb_f"]:
        df[col] = _coerce_int(df[col])

    df["departement_id"] = df["ags"].str[:5]

    # Aggregate per Kreis — sum male / female Wohnort-SvB across Gemeinden
    # (kreisfreie Städte have a single row with ags ending 000).
    agg = (df.groupby("departement_id", as_index=False)[["svb_m", "svb_f"]]
             .sum())

    # Emit long format with a single age class (0 = all ages)
    df_male = agg[["departement_id", "svb_m"]].rename(columns={"svb_m": "weight"})
    df_male["sex"] = "male"
    df_male["age_class"] = 0

    df_female = agg[["departement_id", "svb_f"]].rename(columns={"svb_f": "weight"})
    df_female["sex"] = "female"
    df_female["age_class"] = 0

    df_out = pd.concat([df_male, df_female], ignore_index=True)
    df_out["sex"] = df_out["sex"].astype("category")
    df_out["departement_id"] = df_out["departement_id"].astype("category")
    df_out["age_class"] = df_out["age_class"].astype(int)
    df_out["weight"] = df_out["weight"].astype(int)

    total = df_out["weight"].sum()
    print(f"[braunschweig.employment] {len(df_out)} rows, "
          f"{df_out['departement_id'].nunique()} Kreise, total SvB Wohnort = {total:,}")

    return df_out[["departement_id", "age_class", "sex", "weight"]]


def validate(context):
    path = os.path.join(context.config("data_path"),
                        context.config("braunschweig.employment_gemband_path"))
    if not os.path.exists(path):
        raise RuntimeError(f"BA Gemeindedaten XLSX missing: {path}")
    return os.path.getsize(path)
