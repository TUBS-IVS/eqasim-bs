"""Control-cell loading and control-total construction for the popsim mid stage.

- ``control_base_columns``  -- the control_field base columns from the control spec
- ``load_control_cells``    -- a TARGETED load of only the needed cell columns
- ``filter_zgb_cells``      -- restrict the national grid to the ZGB Kreise
- ``build_control_totals``  -- per-geography suffixed, hierarchically integerized

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.

``SUFFIX_100M`` / ``SUFFIX_1KM`` moved here alongside ``build_control_totals``
(their only consumer in the original module) rather than staying in
``__init__.py``: submodules must not import from the package ``__init__``
(#267 split constraint), so a constant used exclusively by one moved function
travels with it. Both are re-exported from ``__init__.py`` so the public
namespace is unchanged.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Sequence, Union

import pandas as pd
import pyarrow.parquet as pq

from braunschweig.popsim import cells as cellmod
from braunschweig.popsim import controls as ctrl
from braunschweig.popsim import prepared_cells

logger = logging.getLogger(__name__)

SUFFIX_100M = "_ZENSUS100m"
SUFFIX_1KM = "_ZENSUS1km"

# Cell columns always loaded in addition to the control bases: the population
# total (for parent selection / diagnostics), the ARS key (for the ZGB filter),
# and RegioStaR7 (for Phase 4B donor stratification by urban/rural class).
_EXTRA_CELL_COLUMNS = ("POP_TOTAL_100m_adj", "RegionalSchlussel_ARS", "RegioStaR7")
_ARS_COLUMN = "RegionalSchlussel_ARS"


# --------------------------------------------------------------------------- #
# Control spec
# --------------------------------------------------------------------------- #

def control_base_columns(controls_df: pd.DataFrame, geography: str) -> list[str]:
    """Return the distinct control_field base names for a geography (suffix off).

    The control spec's ``control_field`` is ``<base>_<geography>`` (e.g.
    ``M_AGE_0_9_agg_ZENSUS100m``); the base (``M_AGE_0_9_agg``) is the prepared
    cell column the control counts.
    """
    rows = controls_df[controls_df["geography"] == geography]
    suffix = f"_{geography}"
    bases = [
        cf[: -len(suffix)] if cf.endswith(suffix) else cf
        for cf in rows["control_field"]
    ]
    return list(dict.fromkeys(bases))


# --------------------------------------------------------------------------- #
# Cells
# --------------------------------------------------------------------------- #

def load_control_cells(
    parquet_path: Union[str, Path],
    base_cols: Sequence[str],
) -> pd.DataFrame:
    """Load ONLY the needed columns of the prepared cell parquet (performant).

    Reads the grid id + the control base columns + the population total + the ARS
    key (matching cleaned names back to the raw parquet columns), instead of all
    ~570 columns x 3.1 M rows. Attaches ``ZENSUS1km`` / ``STAAT`` / ``WELT``.
    """
    raw_cols = pq.ParquetFile(parquet_path).schema.names
    clean_to_raw: dict[str, str] = {}
    for raw in raw_cols:
        clean_to_raw.setdefault(prepared_cells.clean_col_name(raw), raw)

    id_raw = raw_cols[0]  # GITTER_ID_100m is the first column
    raw_needed = [id_raw]
    for clean in [*base_cols, *_EXTRA_CELL_COLUMNS]:
        raw = clean_to_raw.get(clean)
        if raw is not None and raw not in raw_needed:
            raw_needed.append(raw)

    # RegioStaR7 is optional (graceful): older prepared-cell parquets do not
    # carry it. Without it the stage-B chain matching loses its spatial key and
    # falls back to the 4-key attribute list, so the absence is logged (info,
    # not warn -- the load itself is fully usable).
    if "RegioStaR7" not in clean_to_raw:
        logger.info(
            "[popsim.mid] cells parquet %s carries no 'RegioStaR7' column; "
            "proceeding without it (synthetic persons get no home-cell RS7; "
            "stage-B chain matching falls back to the non-spatial key list).",
            parquet_path,
        )

    df = pd.read_parquet(parquet_path, columns=raw_needed)
    df.columns = [prepared_cells.clean_col_name(c) for c in df.columns]
    df = df.rename(columns={prepared_cells.clean_col_name(id_raw): "ZENSUS100m"})
    df["ZENSUS1km"] = df["ZENSUS100m"].map(cellmod.derive_1km_parent_id)
    df["STAAT"] = 1
    df["WELT"] = 1
    return df


def filter_zgb_cells(
    cells: pd.DataFrame,
    kreis_ars5: Iterable[str],
    *,
    ars_col: str = _ARS_COLUMN,
) -> pd.DataFrame:
    """Restrict the national grid to the cells whose Kreis (ARS-5) is in scope.

    The cell ARS is the 12-digit Regionalschluessel; the Kreis is its first five
    digits.
    """
    if ars_col not in cells.columns:
        raise ValueError(
            f"cells frame has no ARS column {ars_col!r}; cannot filter to ZGB Kreise."
        )
    kreise = {str(k) for k in kreis_ars5}
    ars = cells[ars_col].astype(str).str.zfill(12)
    return cells[ars.str[:5].isin(kreise)].copy()


# --------------------------------------------------------------------------- #
# Control totals (notebook-faithful: per-geography suffix, integerized)
# --------------------------------------------------------------------------- #

def build_control_totals(
    per_cell_targets: pd.DataFrame,
    geo_crosswalk: pd.DataFrame,
    base_cols: Sequence[str],
    *,
    cell_col: str = "ZENSUS100m",
    parent_col: str = "ZENSUS1km",
) -> dict[str, pd.DataFrame]:
    """Build the four control-total tables with per-geography suffixed columns.

    Each base column is integerized within its 1 km parent (largest-remainder), so
    the integer 100 m values sum exactly to the 1 km total; the 100 m columns are
    suffixed ``_ZENSUS100m`` and the 1 km totals ``_ZENSUS1km``. STAAT / WELT carry
    only the geography key (no controls), matching the notebook + control spec.
    """
    parent_of = geo_crosswalk.set_index(cell_col)[parent_col]
    work = per_cell_targets.copy()
    work[parent_col] = work[cell_col].map(parent_of)

    # Zensus 2022 suppresses (Geheimhaltung) some per-cell aggregates: an inhabited
    # 100 m cell can carry a NaN in a control count column. largest_remainder_round
    # cannot integerize NaN, so the missing counts are filled with 0 (the cell
    # contributes no recorded units of that category to the control). This is made
    # observable per the no-silent-fallback policy: the affected cell count and rate
    # are logged, and a high rate (> 1 %) is flagged as a likely data/load problem
    # rather than genuine Zensus suppression.
    n_cells = len(work)
    for col in base_cols:
        n_nan = int(work[col].isna().sum())
        if n_nan:
            rate = n_nan / n_cells if n_cells else 0.0
            message = (
                "[popsim.controls] control column %r has %d/%d (%.3f%%) NaN cells "
                "(Zensus suppression); filling with 0."
            )
            if rate > 0.01:
                logger.warning(
                    message + " High rate -- check the prepared cell parquet load.",
                    col, n_nan, n_cells, 100.0 * rate,
                )
            else:
                logger.info(message, col, n_nan, n_cells, 100.0 * rate)
            work[col] = work[col].fillna(0)

    df_100m = pd.DataFrame({cell_col: work[cell_col].to_numpy()})
    for col in base_cols:
        df_100m[f"{col}{SUFFIX_100M}"] = ctrl.integerize_within_parents(
            work, value_col=col, parent_col=parent_col
        ).to_numpy()

    df_100m[parent_col] = work[parent_col].to_numpy()
    cols_100m = [f"{c}{SUFFIX_100M}" for c in base_cols]
    df_1km = df_100m.groupby(parent_col, sort=False)[cols_100m].sum().reset_index()
    df_1km = df_1km.rename(
        columns={f"{c}{SUFFIX_100M}": f"{c}{SUFFIX_1KM}" for c in base_cols}
    )

    return {
        "ZENSUS100m": df_100m.drop(columns=[parent_col]),
        "ZENSUS1km": df_1km,
        "STAAT": pd.DataFrame([{"STAAT": 1, "WELT": 1}]),
        "WELT": pd.DataFrame([{"WELT": 1}]),
    }
