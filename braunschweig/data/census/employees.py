"""Employees-at-workplace per municipality from GENESIS 13111-01-03-5.

Used by `braunschweig.gravity.model`
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

Output schema:
    (commune_id, weight)
"""

import os
import pandas as pd

# Share the unexplained-cell policy of the sibling GENESIS loader
# (``braunschweig.data.census.employment._coerce_int``): same suppression
# markers, same warn / raise fractions.
from braunschweig.data.census.employment import (
    EMPLOYMENT_COERCE_RAISE_FRACTION,
    EMPLOYMENT_COERCE_WARN_FRACTION,
    GENESIS_SUPPRESSION_MARKERS,
)


COLUMN_NAMES = [
    "ags", "name",
    "all_total", "all_male", "all_female",
    "foreign_total", "foreign_male", "foreign_female",
]

# An 8-digit AGS from the GENESIS export that does not match a ``commune_id``
# in the eqasim_common codes table is dropped by the inner merge below without
# any signal -- its SvB-Arbeitsort total simply disappears from the attraction
# vector. A small drop rate is expected (Gemeinde mergers / territorial
# reform lag between the two sources); a large one means the AGS scope
# filtering above is broken and the gravity model would run on an incomplete
# attraction vector.
EMPLOYEES_AGS_DROP_WARN_FRACTION: float = 0.02
EMPLOYEES_AGS_DROP_RAISE_FRACTION: float = 0.25


def configure(context):
    context.config("data_path")
    context.config("braunschweig.employees_path",
                   "braunschweig/13111-01-03-5.xlsx")
    context.stage("eqasim_common.spatial.codes")


def _coerce_int(series: pd.Series, column_name: str = "") -> pd.Series:
    """Coerce a GENESIS column to int, counting unexplained non-numeric cells.

    Mirrors ``braunschweig.data.census.employment._coerce_int``: "." and "-"
    are documented GENESIS suppression markers (coerce to 0 silently); any
    other non-numeric, non-empty cell is a parse failure that is counted,
    printed, warned about and, above the raise fraction, fatal.
    """
    numeric = pd.to_numeric(series, errors="coerce")
    unexplained_mask = numeric.isna() & ~series.astype(str).str.strip().isin(
        GENESIS_SUPPRESSION_MARKERS | {"", "nan", "None"}
    )
    unexplained_count = int(unexplained_mask.sum())
    total = len(series)
    if unexplained_count:
        fraction = unexplained_count / total if total else 0.0
        label = f" in column '{column_name}'" if column_name else ""
        print(f"[braunschweig.employees] _coerce_int{label}: {unexplained_count}/{total} "
              f"({100.0 * fraction:.1f}%) cells were non-numeric and NOT a recognised "
              f"GENESIS suppression marker ({sorted(GENESIS_SUPPRESSION_MARKERS)}); "
              f"coerced to 0 anyway, which silently masks a genuine parse failure.")
        if fraction >= EMPLOYMENT_COERCE_RAISE_FRACTION:
            raise RuntimeError(
                f"_coerce_int{label} coerced {100.0 * fraction:.1f}% of cells to 0 for "
                f"reasons other than the recognised GENESIS suppression markers (limit "
                f"{100.0 * EMPLOYMENT_COERCE_RAISE_FRACTION:.0f}%). This almost certainly "
                f"means the table format/column offsets changed -- refusing to continue "
                f"with a corrupted attraction vector."
            )
        if fraction >= EMPLOYMENT_COERCE_WARN_FRACTION:
            print(f"[braunschweig.employees] WARNING: _coerce_int{label} unexplained-zero "
                  f"rate {100.0 * fraction:.1f}% exceeds the "
                  f"{100.0 * EMPLOYMENT_COERCE_WARN_FRACTION:.0f}% threshold; verify the "
                  f"input table still matches the documented column layout.")
    return numeric.fillna(0).astype(int)


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

    # Normalize 5-digit kreisfreie AGS to 8-digit (BS 03101 -> 03101000). Only
    # a 5-digit AGS whose padded form is a real Gemeinde in the codes table is
    # a kreisfreie Stadt. Other 5-digit scope rows are LANDKREIS aggregate
    # totals; padding those fabricates non-existent AGS that the merge below
    # would drop and the loss accounting would misreport as lost SvB weight
    # (~27% on the full ZGB-8 scope -> spurious raise, #128 follow-up).
    valid_gemeinde_ags = set(df_codes["ags"].astype(str))
    mask_5digit_scope = (df["ags"].str.len() == 5) & df["ags"].isin(scope_kreise)
    mask_krfr = mask_5digit_scope & (df["ags"] + "000").isin(valid_gemeinde_ags)
    n_aggregate_rows = int((mask_5digit_scope & ~mask_krfr).sum())
    if n_aggregate_rows:
        print(f"[braunschweig.employees] excluded {n_aggregate_rows} five-digit "
              f"Landkreis aggregate rows (Kreis totals, not Gemeinden)")
    df.loc[mask_krfr, "ags"] = df.loc[mask_krfr, "ags"] + "000"

    # Restrict to 8-digit AGS inside ZGB scope.
    df = df[
        (df["ags"].str.len() == 8)
        & df["ags"].str[:5].isin(scope_kreise)
    ].copy()

    df["weight"] = _coerce_int(df["all_total"], column_name="all_total")

    # Map AGS (8-digit) -> ARS/commune_id (12-digit) via the codes table.
    df_codes_scope = df_codes[df_codes["departement_id"].astype(str).isin(scope_kreise)]
    rows_before = len(df)
    ags_before = set(df["ags"].unique())
    weight_before = df["weight"].sum()
    df = df.merge(
        df_codes_scope[["ags", "commune_id"]].astype(str),
        on="ags", how="inner",
    )

    ags_unmatched = sorted(ags_before - set(df["ags"].unique()))
    weight_after = df["weight"].sum()
    lost_weight = weight_before - weight_after
    if weight_before == 0:
        # An all-zero (or empty) input would make the loss fraction 0/0 and
        # pass every check below with an empty attraction vector.
        raise RuntimeError(
            f"Employees input has zero total SvB Arbeitsort weight over {rows_before} "
            f"in-scope rows -- the file is empty, mis-parsed or outside the ZGB scope; "
            f"refusing to continue with an empty attraction vector."
        )
    lost_fraction = lost_weight / weight_before
    print(f"[braunschweig.employees] AGS->commune_id merge: {len(df)}/{rows_before} rows "
          f"matched, {len(ags_unmatched)} AGS unmatched, lost SvB Arbeitsort = "
          f"{lost_weight:,.0f} ({100.0 * lost_fraction:.2f}%)")
    if ags_unmatched:
        print(f"[braunschweig.employees] unmatched AGS (sample up to 20): "
              f"{ags_unmatched[:20]}")
    if lost_fraction >= EMPLOYEES_AGS_DROP_RAISE_FRACTION:
        raise RuntimeError(
            f"AGS->commune_id merge lost {100.0 * lost_fraction:.1f}% of the SvB "
            f"Arbeitsort total (limit {100.0 * EMPLOYEES_AGS_DROP_RAISE_FRACTION:.0f}%). "
            f"This implausibly high loss almost certainly means the eqasim_common codes "
            f"table does not cover the ZGB Gemeinden -- refusing to continue with a "
            f"corrupted attraction vector."
        )
    if lost_fraction >= EMPLOYEES_AGS_DROP_WARN_FRACTION:
        print(f"[braunschweig.employees] WARNING: AGS->commune_id merge dropped "
              f"{100.0 * lost_fraction:.2f}% of the SvB Arbeitsort total, above the "
              f"{100.0 * EMPLOYEES_AGS_DROP_WARN_FRACTION:.0f}% threshold. Verify the "
              f"eqasim_common codes table is complete for the ZGB scope.")

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
