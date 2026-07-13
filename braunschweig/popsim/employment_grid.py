"""Build a per-cell, age×sex-resolved employment target for a 100m PopulationSim control.

SHAPE  = Zensus 2000S-2001 Erwerbstätige by age-group×Kreis
         (5 groups: 16_29 / 30_39 / 40_49 / 50_59 / 60plus),
         loaded via braunschweig.popsim.zensus_employment_age.
LEVEL  = cleancensus Erwerbstaetige Kreis×sex totals (kreis_erwerbsstatus parquet).
DENOM  = the prepared cells' single-year {M,F}_AGE_<year> columns, summed to Kreis×group.

The per-cell employed target binds employment at the 100m grid (where the final
household weights are set) instead of only at KREIS, and respects each cell's age
composition. It rescales per Kreis×sex×group to census_level × age_share, so the
Zensus 2001 source is used only for the age SHAPE; the absolute LEVEL is census.

Produces 10 control columns: EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg.
"""
from __future__ import annotations

import logging

import pandas as pd

from braunschweig.popsim.zensus_employment_age import AGE_GROUPS

logger = logging.getLogger(__name__)

MIN_EMPLOYMENT_AGE = 16

_SEX = (("M", "ERWERBSTAT_KURZ_STP__11_M"), ("F", "ERWERBSTAT_KURZ_STP__11_W"))


def _group_cell_pop(cells, prefix, lo, hi, min_age, single_year_max):
    """Sum single-year columns for one sex prefix and one age group [lo, hi]."""
    top = single_year_max if hi >= single_year_max else hi
    cols = [f"{prefix}_AGE_{y}" for y in range(max(lo, min_age), top + 1)
            if f"{prefix}_AGE_{y}" in cells.columns]
    return cells[cols].sum(axis=1) if cols else pd.Series(0.0, index=cells.index)


def select_load_columns(
    load_cols,
    available_parquet_cols,
    *,
    computed_cols,
    min_age: int = MIN_EMPLOYMENT_AGE,
    single_year_max: int = 100,
):
    """Adjust the parquet load set for the employment-grid control.

    The six employment-grid targets (``EMPLOYED_{M,F}_{young,prime,old}_agg``) are
    COMPUTED per cell by :func:`per_cell_employment_targets`; they are not stored in
    the prepared-cell parquet, so they must be removed from ``load_cols`` (loading
    them would raise / yield bogus columns). In their place the single-year
    ``{M,F}_AGE_<year>`` input columns (the age SHAPE denominator) for
    ``min_age..single_year_max`` are added when present in ``available_parquet_cols``.

    Parameters
    ----------
    load_cols:
        The columns the stage would otherwise load from the parquet (typically
        ``source_cols_override or base_cols``), possibly containing the computed names.
    available_parquet_cols:
        The cleaned column names actually present in the parquet schema.
    computed_cols:
        Set of computed target names to strip out (the 6 ``EMPLOYED_*_agg`` names).
    min_age, single_year_max:
        Inclusive single-year range whose ``{M,F}_AGE_<year>`` columns are added.

    Returns
    -------
    list[str]
        De-duplicated, order-preserving list: the kept ``load_cols`` (computed names
        removed) first, then the added single-year input columns.
    """
    computed = set(computed_cols)
    available = set(available_parquet_cols)

    result: list[str] = []
    seen: set[str] = set()

    def _add(col: str) -> None:
        if col not in seen:
            seen.add(col)
            result.append(col)

    for col in load_cols:
        if col in computed:
            continue
        _add(col)

    for prefix in ("M", "F"):
        for year in range(min_age, single_year_max + 1):
            col = f"{prefix}_AGE_{year}"
            if col in available:
                _add(col)

    return result


def per_cell_employment_targets(
    cells: pd.DataFrame,
    census_levels: pd.DataFrame,
    age_shares_by_kreis: dict,
    *,
    kreis_col: str = "KREIS",
    min_age: int = 16,
    single_year_max: int = 100,
) -> pd.DataFrame:
    """Per-cell EMPLOYED_{M,F}_{16_29,...,60plus}_agg, rescaled per Kreis×sex×group.

    For each Kreis k, sex s, group g:
        sum_cells(EMPLOYED_{s}_{g}_agg) == census_Erwerbstätige[k,s] × age_share[k,g]

    Parameters
    ----------
    cells:
        Prepared cells frame carrying ``ZENSUS100m``, a Kreis column (``kreis_col``)
        and single-year ``{M,F}_AGE_<year>`` columns.
    census_levels:
        Per-Kreis sex-split Erwerbstaetige levels: ``ARS_kreis`` +
        ``ERWERBSTAT_KURZ_STP__11_M`` / ``ERWERBSTAT_KURZ_STP__11_W`` (the LEVEL).
    age_shares_by_kreis:
        Dict mapping Kreis string -> dict[group_name, share] where shares sum to 1.0
        (from zensus_employment_age.load_age_shares).
    kreis_col:
        Name of the Kreis column on ``cells`` (5-digit ARS).
    min_age, single_year_max:
        Age bounds for single-year column summation.

    Returns
    -------
    pandas.DataFrame
        Frame with ``ZENSUS100m`` + 6 columns: EMPLOYED_{M,F}_{young,prime,old}_agg.
    """
    out = pd.DataFrame({"ZENSUS100m": cells["ZENSUS100m"].to_numpy()}, index=cells.index)
    lv = census_levels.copy()
    lv["ARS_kreis"] = lv["ARS_kreis"].astype(str)
    lv = lv.set_index("ARS_kreis")

    # A cell whose Kreis key is absent from census_levels silently yields 0.0 for
    # all 10 EMPLOYED_* columns below (the ``if k in lv.index else 0.0`` branch),
    # which is a fallback and must be observable per project policy: a broken
    # Kreis-key join (e.g. a formatting mismatch) would otherwise pass silently
    # as "zero employed" cells instead of surfacing as a data defect.
    cell_kreis = cells[kreis_col].astype(str)
    matched_mask = cell_kreis.isin(lv.index)
    n_cells_total = len(cell_kreis)
    n_cells_matched = int(matched_mask.sum())
    n_cells_unmatched = n_cells_total - n_cells_matched
    if n_cells_unmatched > 0:
        unmatched_kreis = sorted(cell_kreis[~matched_mask].unique().tolist())
        if n_cells_matched == 0:
            raise ValueError(
                f"per_cell_employment_targets: ALL {n_cells_total} cells have a Kreis "
                f"key absent from census_levels (unmatched Kreis: {unmatched_kreis}). "
                "This is a 100% fallback rate and indicates a broken Kreis-key join "
                "(e.g. a zero-padding or ARS-length mismatch) rather than genuinely "
                "unemployed cells; fix the join before proceeding."
            )
        logger.warning(
            "[employment_grid] per_cell_employment_targets: %d/%d cells (%.1f%%) have a "
            "Kreis key absent from census_levels and receive 0.0 for all 10 EMPLOYED_* "
            "columns (fallback); unmatched Kreis keys: %s.",
            n_cells_unmatched, n_cells_total,
            100.0 * n_cells_unmatched / max(n_cells_total, 1),
            unmatched_kreis,
        )

    for prefix, level_col in _SEX:
        for gname, glo, ghi in AGE_GROUPS:
            pop = _group_cell_pop(cells, prefix, glo, ghi, min_age, single_year_max)
            pop_by_kreis = pop.groupby(cells[kreis_col]).transform("sum")
            level = cells[kreis_col].map(
                lambda k, _lc=level_col, _g=gname: (
                    float(lv.loc[k, _lc]) * age_shares_by_kreis.get(k, {}).get(_g, 0.0)
                    if k in lv.index else 0.0
                )
            )
            scaled = pd.Series(0.0, index=cells.index)
            mask = pop_by_kreis > 0
            scaled[mask] = pop[mask] / pop_by_kreis[mask] * level[mask]
            out[f"EMPLOYED_{prefix}_{gname}_agg"] = scaled.to_numpy()
    return out


def add_employment_grid_columns(
    cells: pd.DataFrame,
    census_levels: pd.DataFrame,
    age_shares_by_kreis: dict,
    *,
    kreis_col: str = "KREIS",
    min_age: int = 16,
    single_year_max: int = 100,
) -> pd.DataFrame:
    """Return a copy of ``cells`` with the 10 employment-grid columns added.

    Thin wrapper over :func:`per_cell_employment_targets` for the stage wiring: it
    computes the ten per-cell employment targets (Zensus 2001 age-shape rescaled per
    Kreis×sex×group to the census Erwerbstaetige level) and attaches them via merge
    on ZENSUS100m.

    Parameters
    ----------
    cells:
        Prepared cells frame carrying ``ZENSUS100m``, a Kreis column (``kreis_col``)
        and single-year ``{M,F}_AGE_<year>`` columns.
    census_levels:
        Per-Kreis sex-split Erwerbstaetige levels: ``ARS_kreis`` +
        ``ERWERBSTAT_KURZ_STP__11_M`` / ``ERWERBSTAT_KURZ_STP__11_W`` (the LEVEL).
    age_shares_by_kreis:
        Dict mapping Kreis string -> dict[group_name, share] (from load_age_shares).
    kreis_col:
        Name of the Kreis column on ``cells`` (5-digit ARS).
    min_age, single_year_max:
        Age bounds for single-year column summation.

    Returns
    -------
    pandas.DataFrame
        Copy of ``cells`` with 10 columns EMPLOYED_{M,F}_{16_29,...,60plus}_agg added.
    """
    t = per_cell_employment_targets(
        cells, census_levels, age_shares_by_kreis,
        kreis_col=kreis_col, min_age=min_age, single_year_max=single_year_max,
    )
    return cells.merge(t, on="ZENSUS100m", how="left")
