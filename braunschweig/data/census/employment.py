"""Employment marginals for ZGB Braunschweig from GENESIS 13111-06-02-4.

Replaces `braunschweig.data.census.employment`. Reads the Regionalstatistik table
13111-06-02-4 "Sozialversicherungspflichtig Beschaeftigte am Wohnort nach
Geschlecht, Nationalitaet und Altersgruppen" (Kreise) and emits per
(Kreis, age_class, sex) totals, matching the Bavaria schema.

Input XLSX columns (header rows 0..7, data from row 8):
    0  AGS (5-digit Kreis)                  -- ffilled
    1  Kreisname                            -- ffilled
    2  Altersgruppe (text)
    3  all_total     (Geschlecht: Insgesamt, Nationalitaet: Insgesamt)
    4  all_male
    5  all_female
    6  foreign_total (Nationalitaet: auslaendisch)
    7  foreign_male
    8  foreign_female

Age classes in the source:
    "unter 20 Jahre"          -> 0   (lower bound; IPF applies minimum_age.employment=16)
    "20 bis unter 25 Jahre"   -> 20
    "25 bis unter 30 Jahre"   -> 25
    "30 bis unter 50 Jahre"   -> 30
    "50 bis unter 60 Jahre"   -> 50
    "60 bis unter 65 Jahre"   -> 60
    "65 Jahre und mehr"       -> 65
    "Insgesamt"               -> dropped

Output schema (matches braunschweig.data.census.employment):
    (departement_id, age_class, sex, weight)
"""

import os
import pandas as pd


COLUMN_NAMES = [
    "departement_id", "department_name", "age_class",
    "all_total", "all_male", "all_female",
    "foreign_total", "foreign_male", "foreign_female",
]

AGE_CLASS_MAP = {
    "unter 20 Jahre":          0,
    "20 bis unter 25 Jahre":  20,
    "25 bis unter 30 Jahre":  25,
    "30 bis unter 50 Jahre":  30,
    "50 bis unter 60 Jahre":  50,
    "60 bis unter 65 Jahre":  60,
    "65 Jahre und mehr":      65,
}


def configure(context):
    context.config("data_path")
    context.config("braunschweig.employment_path",
                   "braunschweig/13111-06-02-4.xlsx")
    context.stage("eqasim_common.spatial.codes")


# GENESIS legitimately uses "." (not surveyed) and "-" (exactly zero) as
# suppression markers, which coerce to 0 by design and are NOT counted below.
# Any OTHER non-numeric, non-empty cell that coerces to 0 (a shifted column, a
# stray unit label, a header row that slipped past skiprows) is a genuine
# parse failure masquerading as a real zero -- CLAUDE.md "Fallback
# transparency" requires that to be counted and surfaced, not silently
# swallowed by ``fillna(0)``.
GENESIS_SUPPRESSION_MARKERS = {".", "-"}
EMPLOYMENT_COERCE_WARN_FRACTION: float = 0.05
EMPLOYMENT_COERCE_RAISE_FRACTION: float = 0.50


def _coerce_int(series: pd.Series, column_name: str = "") -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    unexplained_mask = numeric.isna() & ~series.astype(str).str.strip().isin(
        GENESIS_SUPPRESSION_MARKERS | {"", "nan", "None"}
    )
    unexplained_count = int(unexplained_mask.sum())
    total = len(series)
    if unexplained_count:
        fraction = unexplained_count / total if total else 0.0
        label = f" in column '{column_name}'" if column_name else ""
        print(f"[braunschweig.employment] _coerce_int{label}: {unexplained_count}/{total} "
              f"({100.0 * fraction:.1f}%) cells were non-numeric and NOT a recognised "
              f"GENESIS suppression marker ({sorted(GENESIS_SUPPRESSION_MARKERS)}); "
              f"coerced to 0 anyway, which silently masks a genuine parse failure.")
        if fraction >= EMPLOYMENT_COERCE_RAISE_FRACTION:
            raise RuntimeError(
                f"_coerce_int{label} coerced {100.0 * fraction:.1f}% of cells to 0 for "
                f"reasons other than the recognised GENESIS suppression markers (limit "
                f"{100.0 * EMPLOYMENT_COERCE_RAISE_FRACTION:.0f}%). This almost certainly "
                f"means the table format/column offsets changed -- refusing to continue "
                f"with a corrupted employment marginal."
            )
        if fraction >= EMPLOYMENT_COERCE_WARN_FRACTION:
            print(f"[braunschweig.employment] WARNING: _coerce_int{label} unexplained-zero "
                  f"rate {100.0 * fraction:.1f}% exceeds the "
                  f"{100.0 * EMPLOYMENT_COERCE_WARN_FRACTION:.0f}% threshold; verify the "
                  f"input table still matches the documented column layout.")
    return numeric.fillna(0).astype(int)


def execute(context):
    path = os.path.join(context.config("data_path"),
                        context.config("braunschweig.employment_path"))

    df = pd.read_excel(
        path, header=None, skiprows=8, names=COLUMN_NAMES,
    )

    # AGS is present only on the first row per Kreis -> forward-fill.
    df["departement_id"] = df["departement_id"].ffill().astype(str).str.strip()
    df["department_name"] = df["department_name"].ffill()

    # Drop footer / empty tail rows.
    df = df.dropna(subset=["age_class"]).copy()

    # Keep only Kreis-level rows (5-digit AGS); drops Bundesland/Region aggregates.
    df = df[df["departement_id"].str.len() == 5].copy()

    # Filter to the active scope (ZGB-8 from eqasim_common.spatial.codes).
    df_codes = context.stage("eqasim_common.spatial.codes")
    scope = set(df_codes["departement_id"].astype(str).unique())
    df = df[df["departement_id"].isin(scope)].copy()

    # Drop "Insgesamt" totals, keep the seven bucket rows.
    df = df[df["age_class"].isin(AGE_CLASS_MAP)].copy()
    df["age_class"] = df["age_class"].map(AGE_CLASS_MAP).astype(int)

    for col in ["all_male", "all_female"]:
        df[col] = _coerce_int(df[col], column_name=col)

    # Long format, matching Bavaria.
    df_long = pd.melt(
        df[["departement_id", "age_class", "all_male", "all_female"]],
        id_vars=["departement_id", "age_class"],
        value_vars=["all_male", "all_female"],
        var_name="sex", value_name="weight",
    )
    df_long["sex"] = df_long["sex"].str[4:]

    df_long["departement_id"] = df_long["departement_id"].astype("category")
    df_long["sex"] = df_long["sex"].astype("category")
    df_long["age_class"] = df_long["age_class"].astype(int)
    df_long["weight"] = df_long["weight"].astype(int)

    total = df_long["weight"].sum()
    print(f"[braunschweig.employment] {len(df_long)} rows, "
          f"{df_long['departement_id'].nunique()} Kreise, "
          f"{df_long['age_class'].nunique()} age classes, "
          f"total SvB Wohnort = {total:,}")

    return df_long[["departement_id", "age_class", "sex", "weight"]]


def validate(context):
    path = os.path.join(context.config("data_path"),
                        context.config("braunschweig.employment_path"))
    if not os.path.exists(path):
        raise RuntimeError(f"GENESIS 13111-06-02-4 XLSX missing: {path}")
    return os.path.getsize(path)
