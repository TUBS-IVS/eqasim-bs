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

import logging
from pathlib import Path
from typing import Iterable, Mapping, Sequence, Union

import numpy as np
import pandas as pd

from braunschweig.popsim import cells, controls

logger = logging.getLogger(__name__)

# Geography level names, top (coarsest) to bottom (finest). Matches the
# PopulationSim settings.yaml geographies for this workflow.
GEO_WELT = "WELT"
GEO_STAAT = "STAAT"
GEO_KREIS = "KREIS"  # Tier-3 control geography (employment/education); ARS[:5]
GEO_1KM = "ZENSUS1km"
GEO_100M = "ZENSUS100m"

CONTROL_GEOGRAPHIES: Sequence[str] = (GEO_100M, GEO_1KM, GEO_STAAT, GEO_WELT)


def _resolve_parent_kreis(
    xwalk: pd.DataFrame, weights: np.ndarray
) -> pd.DataFrame:
    """Collapse each 1 km parent's ``KREIS`` to its single dominant Kreis.

    A 1 km grid cell may straddle a Kreis border (its 100 m children carry
    different ARS). PopulationSim's geography hierarchy requires strict nesting
    (each 1 km maps to exactly one parent Kreis), so each parent is assigned the
    Kreis with the largest summed ``weights`` across its cells; ties break on the
    higher Kreis id (deterministic). Reassigned (boundary) cells are logged.
    """
    work = xwalk[[GEO_1KM, GEO_KREIS]].copy()
    work["_w"] = weights
    by_pair = work.groupby([GEO_1KM, GEO_KREIS], sort=False)["_w"].sum().reset_index()
    dominant = (
        by_pair.sort_values(["_w", GEO_KREIS])
        .groupby(GEO_1KM, sort=False)
        .tail(1)
        .set_index(GEO_1KM)[GEO_KREIS]
    )
    new_kreis = xwalk[GEO_1KM].map(dominant).to_numpy()
    n_reassigned = int((new_kreis != xwalk[GEO_KREIS].to_numpy()).sum())
    if n_reassigned:
        logger.info(
            "[popsim.folders] geo_crosswalk: resolved %d/%d 100m cells to their 1km "
            "parent's dominant Kreis (border cells straddling Kreis boundaries).",
            n_reassigned, len(xwalk),
        )
    out = xwalk.copy()
    out[GEO_KREIS] = new_kreis
    return out


def build_geo_crosswalk(
    df_100m: pd.DataFrame,
    *,
    id_col_100m: str = "GITTER_ID_100m",
    parent_col: str = "GITTER_ID_1km",
    ars_col: str | None = None,
    resolve_parent_kreis: bool = False,
    kreis_weight_col: str | None = None,
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
    # Optional KREIS level for Tier-3 controls: each 100 m cell maps to its Kreis =
    # the first 5 digits of the cell's ARS. With ``resolve_parent_kreis`` the raw
    # per-cell Kreis is then collapsed to one dominant Kreis per 1 km parent so the
    # WELT > STAAT > KREIS > ZENSUS1km > ZENSUS100m hierarchy nests strictly.
    if ars_col is not None and ars_col in df_100m.columns:
        xwalk[GEO_KREIS] = df_100m[ars_col].astype(str).str[:5].to_numpy()
        if resolve_parent_kreis:
            if kreis_weight_col is not None and kreis_weight_col in df_100m.columns:
                weights = pd.to_numeric(
                    df_100m[kreis_weight_col], errors="coerce"
                ).fillna(0.0).to_numpy()
            else:
                weights = np.ones(len(df_100m))
            xwalk = _resolve_parent_kreis(xwalk, weights)
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


def build_kreis_control_totals(
    kreis_table: pd.DataFrame,
    geo_crosswalk: pd.DataFrame,
    *,
    controls_map: Mapping[str, Sequence[str]],
    ars_col: str = "ARS_kreis",
    apportion_weights: Mapping[str, float] | None = None,
    household_apportion_weights: Mapping[str, float] | None = None,
    household_control_names: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Build the KREIS control-totals table from an imported per-Kreis census table.

    Unlike :func:`build_control_totals` (which integerizes + aggregates CELL columns
    up the hierarchy), Tier-3 marginals (employment / education) do not live in the
    grid cells; they come from a separate per-Kreis census table (the cleancensus
    ``kreis_*`` tables). For each Kreis present in ``geo_crosswalk[KREIS]`` (deduped,
    sorted), look up its row in ``kreis_table`` (keyed by ``ars_col``) and set each
    control target to the row-sum of its census-source columns (a coarse class = the
    sum of its category columns). Suppressed (NaN) components are treated as 0 by the
    row-sum.

    Per-batch apportionment
    -----------------------
    RegioStaR stratification can split ONE Kreis across MULTIPLE batches (a batch is a
    cell-disjoint sub-region). Each such batch must target only ITS share of the Kreis
    marginal, not the full Kreis marginal -- otherwise every batch holding a fraction
    of the Kreis targets the whole count, and (summed over batches) PopulationSim is
    constrained to N times the true marginal, saturating the control. ``apportion_weights``
    is ``{kreis: share}`` (the batch's population fraction of that Kreis); each control's
    per-Kreis value is multiplied by that share. A Kreis absent from the map (or a
    ``None`` map) keeps weight ``1.0`` = the full marginal. When the map gives every
    batch its population share of the Kreis, the per-Kreis targets sum (across batches)
    to exactly the full marginal.

    Parameters
    ----------
    kreis_table:
        Per-Kreis census marginals (one row per Kreis; ``ars_col`` + the source
        category columns).
    geo_crosswalk:
        The crosswalk from :func:`build_geo_crosswalk` built with ``ars_col`` so it
        carries a ``KREIS`` column.
    controls_map:
        ``{control_name: census_source_cols}`` (e.g. the catalog's tier3 entries).
    ars_col:
        The Kreis-key column in ``kreis_table`` (default ``"ARS_kreis"``).
    apportion_weights:
        Optional ``{kreis: float share}`` to scale each Kreis's controls (the batch's
        population share of the Kreis). ``None`` (default) -> full marginal, legacy
        behaviour byte-identical (so single-batch / pre-Tier-3 callers are unchanged).
        A Kreis missing from the map defaults to ``1.0`` (full marginal). Used for
        PERSON-level controls (employment / education / trip_class), where the batch's
        share of the Kreis population is the construct-correct apportionment basis.
    household_apportion_weights:
        Optional ``{kreis: float share}`` = the batch's HOUSEHOLD share of the Kreis,
        applied instead of ``apportion_weights`` to the controls named in
        ``household_control_names`` (issue #148). Where persons-per-household varies
        across a Kreis's batches, a household-level target scaled by the population
        share is inconsistent with that batch's household backbone; the household share
        is the correct basis. ``None`` (default) -> household controls fall back to
        ``apportion_weights`` (legacy population-share behaviour, byte-identical).
    household_control_names:
        Optional set/iterable of control names (keys of ``controls_map``) that are household-level
        and must use ``household_apportion_weights``. Controls not listed here keep
        ``apportion_weights``. ``None`` (default) -> every control uses
        ``apportion_weights`` (legacy).

    Returns
    -------
    pandas.DataFrame
        Columns ``[KREIS, <control_name>...]``, one row per distinct crosswalk Kreis.

    Raises
    ------
    ValueError
        If a crosswalk Kreis is absent from ``kreis_table`` or a source column is
        missing (fail-fast: no silently under-constrained control).
    """
    kreise = sorted(pd.Series(geo_crosswalk[GEO_KREIS].astype(str).unique()))
    table = kreis_table.copy()
    table[ars_col] = table[ars_col].astype(str)
    table = table.set_index(ars_col)

    missing_kreise = [k for k in kreise if k not in table.index]
    if missing_kreise:
        raise ValueError(
            f"kreis_table is missing Kreis rows present in the crosswalk: {missing_kreise[:5]}"
        )
    all_sources = {col for cols in controls_map.values() for col in cols}
    missing_cols = sorted(c for c in all_sources if c not in table.columns)
    if missing_cols:
        raise ValueError(f"kreis_table is missing source column(s): {missing_cols}")

    household_names = set(household_control_names) if household_control_names else set()
    if household_names and household_apportion_weights is None and apportion_weights is not None:
        # Observability (CLAUDE.md no-silent-fallback): household controls were named but
        # no household share was supplied, so they fall back to the population share. Not
        # reachable via run_popsim_mid (which always supplies the household total when
        # household controls are active); only a direct caller can hit this.
        logger.debug(
            "[popsim.folders] household controls %s apportioned by POPULATION share "
            "(no household_apportion_weights supplied).", sorted(household_names),
        )
    out: dict[str, object] = {GEO_KREIS: kreise}
    for name, source_cols in controls_map.items():
        values = table.loc[kreise, list(source_cols)].sum(axis=1).to_numpy()
        # Household-level controls are apportioned by the batch's HOUSEHOLD share when
        # available; person-level controls (and the legacy path) use the population
        # share (issue #148). A Kreis missing from the chosen map keeps weight 1.0.
        use_household = name in household_names and household_apportion_weights is not None
        weight_map = household_apportion_weights if use_household else apportion_weights
        if weight_map is not None:
            weights = np.array(
                [float(weight_map.get(k, 1.0)) for k in kreise], dtype=float
            )
            values = values * weights
        out[name] = values
    return pd.DataFrame(out)


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
        Must contain all four required geographies (see :data:`CONTROL_GEOGRAPHIES`).
        May additionally contain the optional Tier-3 ``KREIS`` table, which is then
        written as ``control_totals_KREIS.csv`` (absent for tier0-2, byte-identical).

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
    # Optional Tier-3 KREIS geography: written only when present so tier0-2 runs
    # (no KREIS in control_totals) stay byte-identical to the pre-Tier-3 baseline.
    if GEO_KREIS in control_totals:
        _csv(control_totals[GEO_KREIS], data_dir / f"control_totals_{GEO_KREIS}.csv")
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
