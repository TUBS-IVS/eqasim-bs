"""Build a per-cell, age×sex-resolved employment target for a 100m PopulationSim control.

SHAPE  = Zensus 2022 Erwerbstätige by age-group per Kreis -- per Kreis AND sex from the
         Regionaltabelle since ADR-0137, or one shape for both sexes from the previous
         2000S-2001 reference (5 groups: 16_29 / 30_39 / 40_49 / 50_59 / 60plus),
         loaded via braunschweig.popsim.zensus_employment_age.
LEVEL  = cleancensus Erwerbstaetige Kreis×sex totals (kreis_erwerbsstatus parquet).
DENOM  = the prepared cells' single-year {M,F}_AGE_<year> columns, summed to Kreis×group.

The per-cell employed target binds employment at the 100m grid (where the final
household weights are set) instead of only at KREIS, and respects each cell's age
composition. It rescales per Kreis×sex×group to census_level × age_share, so the
age-shape source is used only for the age SHAPE; the absolute LEVEL is census.

STUDENTS = optionally (design A2 of the student layer, ADR-0211) the students the Zensus counts
         as employed, subtracted from the LEVEL of the grid groups 16_29 and 30_39 per Kreis and
         sex before the spread over the cells (A12, A13; braunschweig.popsim.student_level).

Produces 10 control columns: EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Mapping

import pandas as pd

from braunschweig.popsim import cells as _cells
from braunschweig.popsim.zensus_employment_age import AGE_GROUPS

logger = logging.getLogger(__name__)

MIN_EMPLOYMENT_AGE = 16

_SEX = (("M", "ERWERBSTAT_KURZ_STP__11_M"), ("F", "ERWERBSTAT_KURZ_STP__11_W"))


def _group_cell_pop(cells, prefix, lo, hi, min_age, single_year_max):
    """Sum single-year columns for one sex prefix and one age group [lo, hi]."""
    top = single_year_max if hi >= single_year_max else hi
    cols = [f"{prefix}_AGE_{y}" for y in range(max(lo, min_age), top + 1)
            if f"{prefix}_AGE_{y}" in cells.columns]
    # skipna suppression (a Zensus-suppressed single-year cell -> 0 inside the
    # row-sum) is made observable per issue #150 (fallback-transparency rule,
    # CLAUDE.md); the helper preserves the identical skipna sum and returns an
    # all-zero Series when no single-year column is present.
    return _cells.sum_columns_logging_nan(
        cells, cols, f"employment-grid single-year sum {prefix} {lo}-{hi}")


def select_load_columns(
    load_cols,
    available_parquet_cols,
    *,
    computed_cols,
    min_age: int = MIN_EMPLOYMENT_AGE,
    single_year_max: int = 100,
):
    """Adjust the parquet load set for the employment-grid control.

    The employment-grid targets (``EMPLOYED_{M,F}_{16_29,...,60plus}_agg``) are
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
        Set of computed target names to strip out (the ``EMPLOYED_*_agg`` names).
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


def _age_share(age_shares_by_kreis: dict, kreis, sex_prefix: str, group: str) -> float:
    """Share of ``group`` in ``kreis``'s employed persons of one sex.

    ``age_shares_by_kreis[kreis]`` is either ONE shape for both sexes (``{group: share}``) or one
    per sex (``{"M": {group: share}, "F": {group: share}}``, ADR-0137); the sex keys cannot collide
    with the group names. A Kreis without an entry contributes 0.0, as before.
    """
    shape = age_shares_by_kreis.get(kreis, {})
    if sex_prefix in shape:
        shape = shape[sex_prefix]
    return shape.get(group, 0.0)


#: The grid groups the students counted as employed are subtracted from (A13).
STUDENT_GRID_GROUPS = ("16_29", "30_39")
#: Columns of :func:`students_lowered_kreis_levels`.
LOWERED_LEVEL_COLUMNS = ["KREIS", "sex", "grid_age_group", "zensus_employed_persons",
                         "students_counted_employed_persons", "employed_target_persons"]


def students_lowered_kreis_levels(
    census_levels: pd.DataFrame,
    age_shares_by_kreis: dict,
    students_employed_by_group: Mapping[tuple[str, str, str], float],
    kreise: Iterable[str],
) -> pd.DataFrame:
    """The employed target of the student grid groups per Kreis and sex, lowered by the students.

    Design A2 (ADR-0211): for every Kreis of ``kreise`` that has a census level, sex s and group g of
    :data:`STUDENT_GRID_GROUPS`, ``employed_target = census[k, s] x age_share[k, s, g] -
    students_employed_by_group[(k, s, g)]`` (a missing key counts 0).
    ``students_employed_by_group`` is keyed ``(Kreis, "M"|"F", group)``
    (``student_level.employed_students_by_grid_group``).

    Returns a frame with :data:`LOWERED_LEVEL_COLUMNS`, one row per Kreis, sex and group.

    Raises ``ValueError`` when a Kreis of ``kreise`` is absent from the mapping's Kreise, a key names
    a sex other than M/F or a group outside :data:`STUDENT_GRID_GROUPS`, a count is negative or not
    finite, or an employed target is negative (naming Kreis, sex, group and both numbers).
    """
    sexes = [prefix for prefix, _ in _SEX]
    bad_keys = [key for key in students_employed_by_group
                if key[1] not in sexes or key[2] not in STUDENT_GRID_GROUPS]
    if bad_keys:
        raise ValueError(
            f"[employment_grid] students counted as employed: keys {sorted(map(repr, bad_keys))} "
            f"name a sex outside {sexes} or a group outside {list(STUDENT_GRID_GROUPS)} (A13).")
    bad_values = sorted(key for key, value in students_employed_by_group.items()
                        if not (math.isfinite(value) and value >= 0.0))
    if bad_values:
        raise ValueError(f"[employment_grid] students counted as employed: negative or non-finite "
                         f"count for {bad_values}")
    lv = census_levels.copy()
    lv["ARS_kreis"] = lv["ARS_kreis"].astype(str)
    lv = lv.set_index("ARS_kreis")
    mapped_kreise = {key[0] for key in students_employed_by_group}
    kreise = sorted({str(k) for k in kreise if str(k) in lv.index})
    absent = [k for k in kreise if k not in mapped_kreise]
    if absent:
        raise ValueError(
            f"[employment_grid] the cells hold Kreis/Kreise {absent} without students counted as "
            f"employed (mapping Kreise: {sorted(mapped_kreise)}); the student level table must "
            "cover every Kreis of the run.")
    rows, negative = [], []
    for kreis in kreise:
        for prefix, level_col in _SEX:
            for group in STUDENT_GRID_GROUPS:
                zensus = float(lv.loc[kreis, level_col]) * _age_share(
                    age_shares_by_kreis, kreis, prefix, group)
                students = float(students_employed_by_group.get((kreis, prefix, group), 0.0))
                target = zensus - students
                rows.append((kreis, prefix, group, zensus, students, target))
                if target < 0.0:
                    negative.append(f"Kreis {kreis}, sex {prefix}, group {group}: Zensus employed "
                                    f"{zensus:.6g} < students counted as employed {students:.6g}")
    if negative:
        raise ValueError(
            "[employment_grid] the students counted as employed exceed the Zensus employed persons "
            "(design A2: a negative employed target is an error): " + "; ".join(negative))
    return pd.DataFrame(rows, columns=LOWERED_LEVEL_COLUMNS)


def per_cell_employment_targets(
    cells: pd.DataFrame,
    census_levels: pd.DataFrame,
    age_shares_by_kreis: dict,
    *,
    kreis_col: str = "KREIS",
    min_age: int = 16,
    single_year_max: int = 100,
    students_employed_by_group: Mapping[tuple[str, str, str], float] | None = None,
    lowered_kreis_levels: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Per-cell EMPLOYED_{M,F}_{16_29,...,60plus}_agg, rescaled per Kreis×sex×group.

    For each Kreis k, sex s, group g:
        sum_cells(EMPLOYED_{s}_{g}_agg) == census_Erwerbstätige[k,s] × age_share[k,s,g]
    where age_share[k,s,g] is the same for both sexes when only one shape per Kreis is given.

    Parameters
    ----------
    cells:
        Prepared cells frame carrying ``ZENSUS100m``, a Kreis column (``kreis_col``)
        and single-year ``{M,F}_AGE_<year>`` columns.
    census_levels:
        Per-Kreis sex-split Erwerbstaetige levels: ``ARS_kreis`` +
        ``ERWERBSTAT_KURZ_STP__11_M`` / ``ERWERBSTAT_KURZ_STP__11_W`` (the LEVEL).
    age_shares_by_kreis:
        Dict mapping Kreis string -> either dict[group_name, share] (one shape for both sexes,
        zensus_employment_age.load_age_shares) or {"M": dict, "F": dict} (one per sex,
        zensus_employment_age.load_kreis_sex_age_shares); shares sum to 1.0 per shape.
    kreis_col:
        Name of the Kreis column on ``cells`` (5-digit ARS).
    min_age, single_year_max:
        Age bounds for single-year column summation.
    students_employed_by_group:
        Design A2 (ADR-0211): the students the Zensus counts as employed, keyed
        ``(Kreis, "M"|"F", grid group)`` for the groups 16_29 and 30_39. The Kreis level of such a
        group becomes ``census x age_share - students`` before it is spread over the cells
        (:func:`students_lowered_kreis_levels`, which raises on a negative target or a cell Kreis
        missing from the mapping). ``None`` (default) runs the code path without students unchanged.
    lowered_kreis_levels:
        The same lowering, already computed by :func:`students_lowered_kreis_levels` (the stage
        computes it once and reports exactly the levels applied here). Must hold a row for every
        Kreis of the cells that has a census level, both sexes and both student groups
        (``ValueError`` otherwise). Mutually exclusive with ``students_employed_by_group``.

    Returns
    -------
    pandas.DataFrame
        Frame with ``ZENSUS100m`` + 10 columns: EMPLOYED_{M,F}_{16_29,...,60plus}_agg.
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

    if students_employed_by_group is not None and lowered_kreis_levels is not None:
        raise ValueError("per_cell_employment_targets: pass students_employed_by_group OR "
                         "lowered_kreis_levels, not both")
    lowered = None
    if students_employed_by_group is not None:
        lowered_kreis_levels = students_lowered_kreis_levels(
            census_levels, age_shares_by_kreis, students_employed_by_group, cell_kreis.unique())
    if lowered_kreis_levels is not None:
        lowered = {(str(row.KREIS), row.sex, row.grid_age_group): float(row.employed_target_persons)
                   for row in lowered_kreis_levels.itertuples(index=False)}
        needed = [(k, prefix, group) for k in sorted(set(cell_kreis[matched_mask]))
                  for prefix, _ in _SEX for group in STUDENT_GRID_GROUPS]
        absent = [key for key in needed if key not in lowered]
        if absent:
            raise ValueError(f"per_cell_employment_targets: lowered_kreis_levels has no row for "
                             f"{absent[:12]} (a Kreis of the cells must carry both sexes and groups)")

    for prefix, level_col in _SEX:
        for gname, glo, ghi in AGE_GROUPS:
            pop = _group_cell_pop(cells, prefix, glo, ghi, min_age, single_year_max)
            pop_by_kreis = pop.groupby(cells[kreis_col]).transform("sum")
            if lowered is not None and gname in STUDENT_GRID_GROUPS:
                # A2: the Kreis level of a student grid group, lowered by the students counted as
                # employed (an unmatched Kreis keeps the 0.0 of the fallback logged above).
                level = cells[kreis_col].map(
                    lambda k, _g=gname, _s=prefix: (
                        lowered[(str(k), _s, _g)] if k in lv.index else 0.0))
            else:
                level = cells[kreis_col].map(
                    lambda k, _lc=level_col, _g=gname, _s=prefix: (
                        float(lv.loc[k, _lc]) * _age_share(age_shares_by_kreis, k, _s, _g)
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
    students_employed_by_group: Mapping[tuple[str, str, str], float] | None = None,
    lowered_kreis_levels: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return a copy of ``cells`` with the 10 employment-grid columns added.

    Thin wrapper over :func:`per_cell_employment_targets` for the stage wiring: it
    computes the ten per-cell employment targets (Zensus 2022 age shape rescaled per
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
        Dict mapping Kreis string -> one shape or one shape per sex (see
        :func:`per_cell_employment_targets`).
    kreis_col:
        Name of the Kreis column on ``cells`` (5-digit ARS).
    min_age, single_year_max:
        Age bounds for single-year column summation.
    students_employed_by_group, lowered_kreis_levels:
        Passed through to :func:`per_cell_employment_targets` (design A2); ``None`` = unchanged.

    Returns
    -------
    pandas.DataFrame
        Copy of ``cells`` with 10 columns EMPLOYED_{M,F}_{16_29,...,60plus}_agg added.
    """
    t = per_cell_employment_targets(
        cells, census_levels, age_shares_by_kreis,
        kreis_col=kreis_col, min_age=min_age, single_year_max=single_year_max,
        students_employed_by_group=students_employed_by_group,
        lowered_kreis_levels=lowered_kreis_levels,
    )
    return cells.merge(t, on="ZENSUS100m", how="left")
