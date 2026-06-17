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


_SEX_SPEC = (
    ("M", "male", "ERWERBSTAT_KURZ_STP__11_M"),
    ("F", "female", "ERWERBSTAT_KURZ_STP__11_W"),
)


def _raw_cell_employment(cells, rates, *, sex_prefix, kreis_col, single_year_max):
    """Per-cell raw expected employed = Σ_year cell_pop[year] × rate[band(year)]."""
    rate_lookup = {(r.KREIS, r.age_class): r.rate for r in rates.itertuples()}
    raw = pd.Series(0.0, index=cells.index)
    for age_class, lo, hi in GENESIS_EMPLOYMENT_BANDS:
        top = single_year_max if hi is None else hi - 1
        cols = [f"{sex_prefix}_AGE_{y}" for y in range(lo, top + 1)
                if f"{sex_prefix}_AGE_{y}" in cells.columns]
        if not cols:
            continue
        band_pop = cells[cols].sum(axis=1)
        rate = cells[kreis_col].map(lambda k: rate_lookup.get((k, age_class), 0.0))
        raw = raw + band_pop * rate
    return raw


def per_cell_employment_targets(
    cells: pd.DataFrame,
    svb: pd.DataFrame,
    census_levels: pd.DataFrame,
    *,
    kreis_col: str = "KREIS",
    single_year_max: int = 100,
) -> pd.DataFrame:
    """Per-cell EMPLOYED_M_agg / EMPLOYED_F_agg, rescaled per Kreis×sex to census level."""
    out = pd.DataFrame({"ZENSUS100m": cells["ZENSUS100m"].to_numpy()}, index=cells.index)
    levels = census_levels.copy()
    levels["ARS_kreis"] = levels["ARS_kreis"].astype(str)
    levels = levels.set_index("ARS_kreis")
    for prefix, sex, level_col in _SEX_SPEC:
        pop = employable_population_by_kreis(
            cells, sex_prefix=prefix, kreis_col=kreis_col, single_year_max=single_year_max
        )
        rates = employment_rates(svb, pop, sex=sex)
        raw = _raw_cell_employment(
            cells, rates, sex_prefix=prefix, kreis_col=kreis_col,
            single_year_max=single_year_max,
        )
        raw_by_kreis = raw.groupby(cells[kreis_col]).transform("sum")
        target_level = cells[kreis_col].map(
            lambda k: float(levels.loc[k, level_col]) if k in levels.index else 0.0
        )
        scaled = pd.Series(0.0, index=cells.index)
        mask = raw_by_kreis > 0
        scaled[mask] = raw[mask] / raw_by_kreis[mask] * target_level[mask]
        out[f"EMPLOYED_{prefix}_agg"] = scaled.to_numpy()
    return out
