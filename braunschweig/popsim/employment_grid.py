"""Build a per-cell, age×sex-resolved employment target for a 100m PopulationSim control.

SHAPE  = GENESIS 13111 SvB employment counts by age-class×sex×Kreis
         (braunschweig.data.census.employment).
LEVEL  = cleancensus Erwerbstaetige Kreis×sex totals (kreis_erwerbsstatus parquet).
DENOM  = the prepared cells' single-year {M,F}_AGE_<year> columns, summed to Kreis.

The per-cell employed target binds employment at the 100m grid (where the final
household weights are set) instead of only at KREIS, and respects each cell's age
composition. It rescales per Kreis×sex to the census Erwerbstaetige level, so the
SvB (narrower) source is used only for the age SHAPE; the absolute LEVEL is census.
"""
from __future__ import annotations

import pandas as pd

MIN_EMPLOYMENT_AGE = 16

# (age_class, lo_inclusive, hi_exclusive_or_None) -- GENESIS 13111 bands, floored at 16.
GENESIS_EMPLOYMENT_BANDS: tuple[tuple[int, int, int | None], ...] = (
    (0, 16, 20),
    (20, 20, 25),
    (25, 25, 30),
    (30, 30, 50),
    (50, 50, 60),
    (60, 60, 65),
    (65, 65, None),
)


def band_for_age(age: int) -> int | None:
    """Return the GENESIS age_class for a single year, or None if not employable."""
    if age < MIN_EMPLOYMENT_AGE:
        return None
    for age_class, lo, hi in GENESIS_EMPLOYMENT_BANDS:
        if age >= lo and (hi is None or age < hi):
            return age_class
    return None


def employable_population_by_kreis(
    cells: pd.DataFrame,
    *,
    sex_prefix: str,
    kreis_col: str = "KREIS",
    single_year_max: int = 100,
) -> pd.DataFrame:
    """Kreis population per GENESIS band from single-year ``{sex_prefix}_AGE_<year>`` cols."""
    rows = []
    for age_class, lo, hi in GENESIS_EMPLOYMENT_BANDS:
        top = single_year_max if hi is None else hi - 1
        cols = [
            f"{sex_prefix}_AGE_{year}"
            for year in range(lo, top + 1)
            if f"{sex_prefix}_AGE_{year}" in cells.columns
        ]
        if not cols:
            continue
        band_pop = cells[cols].sum(axis=1)
        grouped = band_pop.groupby(cells[kreis_col]).sum()
        for kreis, pop in grouped.items():
            rows.append({"KREIS": kreis, "age_class": age_class, "pop": float(pop)})
    return pd.DataFrame(rows, columns=["KREIS", "age_class", "pop"])


def employment_rates(svb: pd.DataFrame, pop: pd.DataFrame, *, sex: str) -> pd.DataFrame:
    """Rate = SvB / population per (Kreis, age_class) for one sex; 0 where pop==0."""
    s = svb[svb["sex"] == sex].copy()
    s["KREIS"] = s["departement_id"].astype(str)
    merged = pop.merge(
        s[["KREIS", "age_class", "weight"]], on=["KREIS", "age_class"], how="left"
    )
    merged["weight"] = merged["weight"].fillna(0.0)
    merged["rate"] = 0.0
    mask = merged["pop"] > 0
    merged.loc[mask, "rate"] = merged.loc[mask, "weight"] / merged.loc[mask, "pop"]
    return merged[["KREIS", "age_class", "rate"]]
