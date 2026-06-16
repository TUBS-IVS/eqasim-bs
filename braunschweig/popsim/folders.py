"""Assemble a PopulationSim run folder from the cell grid, controls and seed.

A PopulationSim run reads a self-contained folder:

    <folder>/
      data/    geo_cross_walk.csv, control_totals_<geo>.csv, seed_households.csv,
               seed_persons.csv
      configs/ controls.csv, settings.yaml, logging.yaml
      output/  (created empty; PopulationSim writes here)

This module builds the deterministic, schema-agnostic pieces of that folder: the
geography crosswalk (WELT > STAAT > ZENSUS1km > ZENSUS100m), the
hierarchically-consistent control totals (100 m integerized within each 1 km
parent via the largest-remainder rule, 1 km = the exact sum of its children,
STAAT/WELT = national totals), and the folder writer.

The mapping from the Zensus cell parquet's (German-named) binned columns to the
control targets is intentionally NOT done here: callers pass a per-cell target
table whose columns are already the control target names. That binding depends on
the real cell parquet schema and is a separate, data-specific concern.

Replaces the in-notebook folder generation of popsimprep's Step 2/3.
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence, Union

import pandas as pd

from braunschweig.popsim import cells, controls

# Geography level names, top (coarsest) to bottom (finest). Matches the
# PopulationSim settings.yaml geographies for this workflow.
GEO_WELT = "WELT"
GEO_STAAT = "STAAT"
GEO_KREIS = "KREIS"  # Tier-3 control geography (employment/education); ARS[:5]
GEO_1KM = "ZENSUS1km"
GEO_100M = "ZENSUS100m"

CONTROL_GEOGRAPHIES: Sequence[str] = (GEO_100M, GEO_1KM, GEO_STAAT, GEO_WELT)


def build_geo_crosswalk(
    df_100m: pd.DataFrame,
    *,
    id_col_100m: str = "GITTER_ID_100m",
    parent_col: str = "GITTER_ID_1km",
    ars_col: str | None = None,
) -> pd.DataFrame:
    """Build the PopulationSim geo crosswalk for the given 100 m cells.

    One row per 100 m cell with its nested geography keys:
    ``[ZENSUS100m, ZENSUS1km, STAAT, WELT]``. STAAT and WELT are constant 1 (a
    single national / world seed geography). The 1 km parent is taken from the
    explicit parent column when present, else derived from the 100 m id.

    When ``ars_col`` is given (and present), an additional ``KREIS`` column is
    appended: each 100 m cell maps unambiguously to its Kreis = the first 5 digits
    of the cell's Amtlicher Regionalschluessel (ARS). This is the Tier-3
    (employment/education) control geography. Omitted when ``ars_col`` is None, so
    the default 4-column output is unchanged.

    Parameters
    ----------
    df_100m:
        Frame of 100 m cells (must carry ``id_col_100m``; ``parent_col`` optional;
        ``ars_col`` optional, needed only for the KREIS level).
    id_col_100m / parent_col:
        Source column names in ``df_100m``.
    ars_col:
        Optional source column holding the cell's 12-digit ARS; when present a
        ``KREIS`` column (ARS[:5]) is added.

    Returns
    -------
    pandas.DataFrame
        Columns ``[ZENSUS100m, ZENSUS1km, STAAT, WELT]`` (plus ``KREIS`` when
        ``ars_col`` is given).
    """
    if parent_col in df_100m.columns:
        parent = df_100m[parent_col].astype(str)
    else:
        parent = df_100m[id_col_100m].map(cells.derive_1km_parent_id)

    xwalk = pd.DataFrame(
        {
            GEO_100M: df_100m[id_col_100m].astype(str).to_numpy(),
            GEO_1KM: parent.to_numpy(),
            GEO_STAAT: 1,
            GEO_WELT: 1,
        }
    )
    # Optional KREIS level for Tier-3 controls: each 100 m cell maps unambiguously
    # to its Kreis = the first 5 digits of the cell's ARS.
    if ars_col is not None and ars_col in df_100m.columns:
        xwalk[GEO_KREIS] = df_100m[ars_col].astype(str).str[:5].to_numpy()
    return xwalk


def build_control_totals(
    per_cell_targets: pd.DataFrame,
    geo_crosswalk: pd.DataFrame,
    *,
    target_cols: Sequence[str],
    cell_id_col: str = GEO_100M,
    parent_col: str = GEO_1KM,
) -> dict[str, pd.DataFrame]:
    """Build hierarchically-consistent control totals for all geographies.

    For each target column the 100 m values are integerized within their 1 km
    parent (largest-remainder), so the integer 100 m values sum exactly to the
    rounded 1 km target. The coarser geographies are then exact aggregates:

    - ``ZENSUS100m``: cell id + integerized target columns.
    - ``ZENSUS1km``: parent id + sum of the integerized 100 m children.
    - ``STAAT`` / ``WELT``: a single row (code 1) with the national totals.

    Parameters
    ----------
    per_cell_targets:
        Frame with ``cell_id_col`` and one float column per control target.
    geo_crosswalk:
        The crosswalk from :func:`build_geo_crosswalk` (gives the 1 km parent of
        each 100 m cell).
    target_cols:
        The control target columns to integerize and aggregate.

    Returns
    -------
    dict[str, pandas.DataFrame]
        Keyed by geography name (``ZENSUS100m`` / ``ZENSUS1km`` / ``STAAT`` /
        ``WELT``).
    """
    parent_of = geo_crosswalk.set_index(GEO_100M)[parent_col]
    merged = per_cell_targets.copy()
    merged[parent_col] = merged[cell_id_col].map(parent_of)
    if merged[parent_col].isna().any():
        missing = merged.loc[merged[parent_col].isna(), cell_id_col].tolist()
        raise ValueError(
            f"per_cell_targets has cells absent from the crosswalk: {missing[:5]}"
        )

    df_100m = pd.DataFrame({cell_id_col: merged[cell_id_col].to_numpy()})
    for col in target_cols:
        df_100m[col] = controls.integerize_within_parents(
            merged, value_col=col, parent_col=parent_col
        ).to_numpy()

    # 1 km = exact sum of the integerized 100 m children.
    df_100m_with_parent = df_100m.copy()
    df_100m_with_parent[parent_col] = merged[parent_col].to_numpy()
    df_1km = (
        df_100m_with_parent.groupby(parent_col, sort=False)[list(target_cols)]
        .sum()
        .reset_index()
        .rename(columns={parent_col: GEO_1KM})
    )

    national = {col: int(df_100m[col].sum()) for col in target_cols}
    df_staat = pd.DataFrame([{GEO_STAAT: 1, **national}])
    df_welt = pd.DataFrame([{GEO_WELT: 1, **national}])

    return {GEO_100M: df_100m, GEO_1KM: df_1km, GEO_STAAT: df_staat, GEO_WELT: df_welt}


def write_popsim_folder(
    folder: Union[str, Path],
    *,
    geo_crosswalk: pd.DataFrame,
    control_totals: Mapping[str, pd.DataFrame],
    controls_csv: pd.DataFrame,
    seed_households: pd.DataFrame,
    seed_persons: pd.DataFrame,
    settings_yaml: str | None = None,
    logging_yaml: str | None = None,
) -> dict[str, Path]:
    """Write a complete PopulationSim run folder; return the paths written.

    Creates ``data/``, ``configs/`` and an empty ``output/`` directory and writes
    the crosswalk, the four control-total tables, the seed tables, the controls
    spec and (when provided) the settings / logging YAML.

    Parameters
    ----------
    folder:
        Target run-folder path (created, with parents).
    control_totals:
        Must contain all four geographies (see :data:`CONTROL_GEOGRAPHIES`).

    Raises
    ------
    ValueError
        If ``control_totals`` is missing any required geography (fail-fast: a
        partial control set would silently under-constrain the run).
    """
    missing = [g for g in CONTROL_GEOGRAPHIES if g not in control_totals]
    if missing:
        raise ValueError(
            f"control_totals is missing geographies {missing}; expected all of "
            f"{list(CONTROL_GEOGRAPHIES)}."
        )

    base = Path(folder)
    data_dir = base / "data"
    configs_dir = base / "configs"
    output_dir = base / "output"
    for directory in (data_dir, configs_dir, output_dir):
        directory.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}

    def _csv(df: pd.DataFrame, path: Path) -> None:
        df.to_csv(path, index=False)
        written[path.name] = path

    _csv(geo_crosswalk, data_dir / "geo_cross_walk.csv")
    for geography in CONTROL_GEOGRAPHIES:
        _csv(control_totals[geography], data_dir / f"control_totals_{geography}.csv")
    _csv(seed_households, data_dir / "seed_households.csv")
    _csv(seed_persons, data_dir / "seed_persons.csv")
    _csv(controls_csv, configs_dir / "controls.csv")

    if settings_yaml is not None:
        path = configs_dir / "settings.yaml"
        path.write_text(settings_yaml, encoding="utf-8")
        written[path.name] = path
    if logging_yaml is not None:
        path = configs_dir / "logging.yaml"
        path.write_text(logging_yaml, encoding="utf-8")
        written[path.name] = path

    return written
