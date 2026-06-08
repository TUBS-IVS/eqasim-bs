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

from pathlib import Path
from typing import Optional, Sequence, Union

import pandas as pd

from braunschweig.popsim import cells as _cells

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
