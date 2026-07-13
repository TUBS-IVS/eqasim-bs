"""Load the prepared Zensus 2022 cell parquet into PopulationSim control inputs.

The 100 m cell parquet is already prepared with the binned control marginals (the
gender backfill, ``_adj`` rescales, ``is_orphan`` handling, ...). As in the
popsimprep notebook Step 2, this module:

1. normalises the column names (``clean_col_name``: transliterate + drop spaces /
   dots / commas, ``-`` -> ``_``) so they are safe control-target identifiers;
2. loads the parquet, renames the grid-id column to ``ZENSUS100m`` and attaches
   the nested geographies (``ZENSUS1km`` derived from the 100 m id, ``STAAT`` /
   ``WELT`` = 1); and
3. selects the per-cell control-target table for a chosen set of (already
   prepared) target columns.

The resulting per-cell target table feeds
``braunschweig.popsim.folders.build_control_totals`` (which integerizes within each
1 km parent and builds the geography levels). The control TARGETS are the prepared
cell columns themselves -- there is no synthetic renaming.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence, Tuple, Union

import pandas as pd

from braunschweig.popsim import cells as _cells

logger = logging.getLogger(__name__)

# ``unidecode`` is a popsimprep dependency but is not part of the eqasim pipeline
# environment, so it is used only when available; otherwise a German
# transliteration covers the umlauts / sharp-s that occur in Zensus column names
# (identical output to ``unidecode`` for those characters), keeping this module
# dependency-free in the quaSIM environment.
try:  # pragma: no cover - import branch depends on the environment
    from unidecode import unidecode as _unidecode
except ImportError:  # pragma: no cover
    _unidecode = None

_GERMAN_TRANSLITERATION = str.maketrans(
    {"ä": "a", "ö": "o", "ü": "u", "Ä": "A", "Ö": "O", "Ü": "U", "ß": "ss"}
)

# Geography columns attached to the cells (and excluded from control targets).
GEO_COLUMNS = ("ZENSUS100m", "ZENSUS1km", "STAAT", "WELT")

# Ten-year age bands (lower, upper_inclusive, label); the last band is open-ended.
# These match the popsimprep control_field set (M_AGE_<label>_agg / F_AGE_...).
AGE_SEX_BANDS = (
    (0, 9, "0_9"),
    (10, 19, "10_19"),
    (20, 29, "20_29"),
    (30, 39, "30_39"),
    (40, 49, "40_49"),
    (50, 59, "50_59"),
    (60, 69, "60_69"),
    (70, 79, "70_79"),
    (80, None, "80_plus"),
)


def add_age_sex_band_aggregates(
    df: pd.DataFrame,
    *,
    prefixes: Sequence[str] = ("M", "F"),
    single_year_max: int = 100,
) -> pd.DataFrame:
    """Add the banded ``{sex}_AGE_<band>_agg`` columns from single-year columns.

    The prepared control set references ten-year age x sex bands
    (``M_AGE_0_9_agg`` ... ``M_AGE_80_plus_agg`` and the ``F_`` equivalents). The
    on-disk cell parquet carries only single-year columns (``M_AGE_0`` ...
    ``M_AGE_100`` / ``F_AGE_*``), so this derives the bands by summing the
    single-year columns -- reproducing the ``_with_aggs`` parquet the notebook
    config expects. A band with no source columns present yields 0.

    Parameters
    ----------
    df:
        Cells frame with single-year ``{prefix}_AGE_<year>`` columns.
    prefixes:
        Sex prefixes to aggregate (``"M"`` / ``"F"``).
    single_year_max:
        Highest single-year age column (the open-ended last band runs to it).

    Returns
    -------
    pandas.DataFrame
        A copy of ``df`` with the ``_agg`` band columns added.
    """
    result = df.copy()
    for prefix in prefixes:
        for lower, upper, label in AGE_SEX_BANDS:
            top = single_year_max if upper is None else upper
            source = [
                f"{prefix}_AGE_{year}"
                for year in range(lower, top + 1)
                if f"{prefix}_AGE_{year}" in result.columns
            ]
            target = f"{prefix}_AGE_{label}_agg"
            result[target] = result[source].sum(axis=1) if source else 0.0
    return result


def _transliterate(name: str) -> str:
    if _unidecode is not None:
        return _unidecode(name)
    return name.translate(_GERMAN_TRANSLITERATION)


def clean_col_name(name: str) -> str:
    """Normalise a (possibly German) column name to a safe identifier.

    Transliterates non-ASCII (umlauts / sharp-s) and drops spaces, dots and
    commas; hyphens become underscores. Faithful to the popsimprep notebook
    helper, so the cleaned names match the control targets the notebook produces.
    """
    return (
        _transliterate(str(name))
        .replace(" ", "")
        .replace(".", "")
        .replace(",", "")
        .replace("-", "_")
    )


def load_prepared_cells(
    parquet_path: Union[str, Path],
    *,
    id_col: Optional[str] = None,
) -> pd.DataFrame:
    """Load the prepared 100 m cell parquet with cleaned names + geographies.

    Parameters
    ----------
    parquet_path:
        Path to the prepared 100 m cell parquet.
    id_col:
        The grid-id column to use as ``ZENSUS100m``. Defaults to the first column
        (matching the notebook, whose id column is the first one).

    Returns
    -------
    pandas.DataFrame
        The cells with cleaned column names, the id column renamed to
        ``ZENSUS100m``, and ``ZENSUS1km`` / ``STAAT`` / ``WELT`` attached.
    """
    df = pd.read_parquet(parquet_path)
    df.columns = [clean_col_name(c) for c in df.columns]

    id_name = clean_col_name(id_col) if id_col is not None else df.columns[0]
    if id_name not in df.columns:
        raise ValueError(
            f"id column {id_name!r} not found in cells (columns: {list(df.columns)[:8]}...)."
        )
    df = df.rename(columns={id_name: "ZENSUS100m"})

    df["ZENSUS1km"] = df["ZENSUS100m"].map(_cells.derive_1km_parent_id)
    df["STAAT"] = 1
    df["WELT"] = 1
    return df


def add_aggregated_controls(
    cells: pd.DataFrame,
    aggregation_map: Mapping[str, Tuple[str, ...]],
) -> pd.DataFrame:
    """Derive multi-source aggregated control columns by row-summing census source columns.

    For each ``(derived_name, source_cols)`` pair in ``aggregation_map``:
    - Finds which source columns are present in ``cells`` (logs a WARNING for each
      missing source column; no silent fallback).
    - Sets ``cells[derived_name]`` to the row-sum of the present source columns via
      :func:`braunschweig.popsim.cells.sum_columns_logging_nan`, which logs any NaN
      cells suppressed by ``skipna`` (issue #150).
    - If *all* source columns are missing, raises ``ValueError`` instead of emitting a
      constant-0 target (issue #149): a permanently-zero control would be balanced to 0
      by PopulationSim unnoticed, violating the no-silent-fallback rule (CLAUDE.md).
    - A single-source entry (len==1) is an identity: if the source already exists
      as ``derived_name`` it is overwritten with itself (no-op).
    - An empty ``aggregation_map`` returns the cells frame unchanged (tier0-only
      path stays byte-identical).

    Parameters
    ----------
    cells:
        Cells frame (output of :func:`load_prepared_cells` or a synthetic frame).
        Must include at least the geography columns and the source control columns.
    aggregation_map:
        Mapping ``{derived_column_name: tuple_of_source_column_names}``.
        Passed by the stage from the active catalog's multi-source controls.

    Returns
    -------
    pandas.DataFrame
        A copy of ``cells`` with the derived columns added (source columns
        preserved; existing columns overwritten only if derived_name == source_name,
        which is the single-source identity case).
    """
    if not aggregation_map:
        return cells

    result = cells.copy()
    for derived_name, source_cols in aggregation_map.items():
        present = [c for c in source_cols if c in result.columns]
        missing = [c for c in source_cols if c not in result.columns]
        for col in missing:
            logger.warning(
                "[popsim.prepared_cells] Source column %r for derived control %r "
                "is not present in the cells frame and will be skipped (partial sum).",
                col, derived_name,
            )
        if present:
            # skipna suppression (NaN -> 0) is made observable per issue #150.
            result[derived_name] = _cells.sum_columns_logging_nan(
                result, present, f"derived control {derived_name!r}")
        else:
            # All sources missing: a renamed/removed census column would otherwise
            # yield a permanently-zero target that PopulationSim balances to 0
            # unnoticed. Per the mandatory no-silent-fallback rule (CLAUDE.md,
            # issue #149) this is a hard error, not a silent 0-column.
            raise ValueError(
                f"[popsim.prepared_cells] All {len(source_cols)} source column(s) "
                f"{list(source_cols)} for derived control {derived_name!r} are absent "
                f"from the cells frame; refusing to emit a constant-0 control. "
                f"Check the census parquet column names against the active catalog."
            )
    return result


def select_per_cell_targets(
    df_cells: pd.DataFrame,
    target_columns: Sequence[str],
) -> pd.DataFrame:
    """Return the per-cell control-target table ``[ZENSUS100m, *target_columns]``.

    Missing control values are filled with 0 (an empty cell contributes nothing to
    that control). Unknown target columns fail fast -- a control must reference a
    prepared cell column.

    Raises
    ------
    ValueError
        If any requested target column is not present in ``df_cells``.
    """
    missing = [c for c in target_columns if c not in df_cells.columns]
    if missing:
        raise ValueError(
            f"control target columns not present in the prepared cells: {missing}."
        )
    out = df_cells[["ZENSUS100m", *target_columns]].copy()
    out[list(target_columns)] = out[list(target_columns)].fillna(0)
    return out
