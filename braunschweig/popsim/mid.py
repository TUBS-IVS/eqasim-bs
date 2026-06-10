"""popsim_mid orchestration: build + run PopulationSim folders from MiD + cells.

Folds the validated end-to-end logic (see ``scripts/popsim_mid_smoke.py``) into
small, focused, reusable functions:

- ``control_base_columns``  -- the control_field base columns from the control spec
- ``load_control_cells``    -- a TARGETED load of only the needed cell columns
- ``filter_zgb_cells``      -- restrict the national grid to the ZGB Kreise
- ``build_control_totals``  -- per-geography suffixed, hierarchically integerized
- ``load_mid_seed``         -- the consistent (complete-household) MiD seed
- ``assemble_batch_folder`` -- write one PopulationSim run folder
- ``run_popsim_mid``        -- batch over 1 km parents, run, merge, handoff

It reuses the building blocks in ``braunschweig.popsim`` (cells / controls /
folders / seed / batch / merge / handoff) rather than re-implementing them.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Union

import pandas as pd
import pyarrow.parquet as pq

from braunschweig.popsim import batch
from braunschweig.popsim import cells as cellmod
from braunschweig.popsim import controls as ctrl
from braunschweig.popsim import folders
from braunschweig.popsim import merge as mergemod
from braunschweig.popsim import prepared_cells
from braunschweig.popsim import seed as seedmod

logger = logging.getLogger(__name__)

SUFFIX_100M = "_ZENSUS100m"
SUFFIX_1KM = "_ZENSUS1km"

# Re-exported for convenience: callers that already import braunschweig.popsim.mid
# can access the canonical MiD seed column mapping without a separate import of
# braunschweig.popsim.seed.  The authoritative definition remains in seed.py.
MID_SEED_COLUMNS = seedmod.MID_SEED_COLUMNS

# Cell columns always loaded in addition to the control bases: the population
# total (for parent selection / diagnostics), the ARS key (for the ZGB filter),
# and RegioStaR7 (for Phase 4B donor stratification by urban/rural class).
_EXTRA_CELL_COLUMNS = ("POP_TOTAL_100m_adj", "RegionalSchlussel_ARS", "RegioStaR7")
_ARS_COLUMN = "RegionalSchlussel_ARS"


def detect_csv_separator(path: Union[str, Path]) -> str:
    """Detect the field separator of a MiD CSV from its header line.

    The MiD 2023 scientific-use delivery has been observed with BOTH separators:
    a comma-separated export (the ZGB regional sample used here) and a
    semicolon-separated German-locale export. Hard-coding one separator silently
    mis-parses the other -- the whole header collapses into a single column,
    which then fails much later with a misleading "missing required columns"
    error (observed for ``MiD2023_Wege.csv``). The separator is therefore
    detected from the header rather than assumed.

    Parameters
    ----------
    path:
        Path to the MiD CSV file.

    Returns
    -------
    str
        ``","`` if the header contains at least as many commas as semicolons,
        otherwise ``";"``.

    Raises
    ------
    ValueError
        If the header line contains neither ``,`` nor ``;`` (so no separator can
        be inferred and a silent mis-parse must be avoided).
    """
    with open(path, "r", encoding="utf-8") as handle:
        header = handle.readline()
    n_comma = header.count(",")
    n_semicolon = header.count(";")
    if n_comma == 0 and n_semicolon == 0:
        raise ValueError(
            f"Cannot detect a ',' or ';' field separator in the header of {path}: "
            f"{header[:120]!r}. The MiD CSV delivery must be comma- or "
            "semicolon-separated."
        )
    return "," if n_comma >= n_semicolon else ";"


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


# --------------------------------------------------------------------------- #
# Seed
# --------------------------------------------------------------------------- #

def load_mid_seed(
    mid_dir: Union[str, Path],
    *,
    columns: seedmod.SeedColumns = seedmod.MID_SEED_COLUMNS,
    day_filter_values: Optional[Sequence[int]] = None,
) -> tuple[pd.DataFrame, pd.DataFrame, seedmod.CompletenessReport]:
    """Load the consistent MiD seed (complete-household filtered) -- performant.

    Reads only the seed columns the controls need (not all 79 / 261 MiD columns),
    applies the complete-household (``kernwo``) filter so every kept household has
    its persons (no NaN incidence in PopulationSim), and returns
    ``(households, persons, report)`` with the essentials + ``STAAT``.
    """
    mid_dir = Path(mid_dir)
    # Load household id, weight, and RegioStaR7 (Phase 4A plumbing: the RS7 code
    # is carried onto the seed households so Phase 4B donor stratification can use
    # the cell's urban/rural class to restrict the donor pool without an extra join).
    households_path = mid_dir / "MiD2023_Haushalte.csv"
    persons_path = mid_dir / "MiD2023_Personen.csv"
    households = pd.read_csv(
        households_path,
        usecols=[columns.household_id, columns.household_weight, "RegioStaR7"],
        sep=detect_csv_separator(households_path),
    )
    person_cols = [
        columns.person_household_id, columns.person_id, columns.person_weight,
        columns.age, columns.sex,
    ]
    if columns.day_filter_col:
        person_cols.append(columns.day_filter_col)
    persons = pd.read_csv(
        persons_path,
        usecols=list(dict.fromkeys(person_cols)),
        sep=detect_csv_separator(persons_path),
    )

    households, persons, report = seedmod.filter_complete_households(
        households, persons, columns,
        day_filter_values=day_filter_values or columns.day_filter_values,
    )
    households, persons = seedmod.select_seed_columns(
        households, persons, columns,
        extra_household_cols=("RegioStaR7",),
    )
    return households, persons, report


# MiD columns needed to enrich the synthetic persons (beyond the seed control cols).
MID_PERSON_ATTR_COLS = (
    "H_ID", "P_ID", "HP_ALTER", "HP_SEX", "P_TAET", "P_FSCHEIN", "P_FKARTE", "P_BKAT",
)
MID_HOUSEHOLD_ATTR_COLS = (
    "H_ID", "oek_status", "hheink_gr1", "H_ANZAUTO", "H_ANZRAD",
    "RegioStaR7",  # Phase 4A: RegioStaR-7 code for donor urban/rural stratification
)

# Minimum columns required by build_trip_table / trips_stage.
# All remaining columns are carried as extras (loaded via usecols=None -> all).
MID_WEGE_REQUIRED_COLS = (
    "H_ID", "P_ID", "W_ID",
    "W_ZWECK", "hvm",
    "W_SZS", "W_SZM",
    "W_AZS", "W_AZM",
    "wegkm_imp",
)


def load_mid_attributes(
    mid_dir: Union[str, Path],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the MiD donor attribute columns (households + persons) for enrichment.

    Reads only the columns the eqasim attribute mapping needs (not all MiD
    columns); returns ``(households, persons)`` for
    ``braunschweig.popsim.assembly.build_persons``.
    """
    mid_dir = Path(mid_dir)
    households_path = mid_dir / "MiD2023_Haushalte.csv"
    persons_path = mid_dir / "MiD2023_Personen.csv"
    households = pd.read_csv(
        households_path, usecols=list(MID_HOUSEHOLD_ATTR_COLS),
        sep=detect_csv_separator(households_path),
    )
    persons = pd.read_csv(
        persons_path, usecols=list(MID_PERSON_ATTR_COLS),
        sep=detect_csv_separator(persons_path),
    )
    return households, persons


def load_mid_wege(
    mid_dir: Union[str, Path],
) -> pd.DataFrame:
    """Load the MiD 2023 Wege (trip) table for the trips_stage.

    The field separator is detected from the header (``detect_csv_separator``)
    because the MiD 2023 delivery occurs in both comma- and semicolon-separated
    variants; assuming one silently mis-parses the other into a single column.
    All columns are loaded (no usecols filter) so that every MiD Wege extra
    column is available as a traceability/analysis extra in the output trip
    table. The minimum columns required by
    ``braunschweig.popsim.trips.build_trip_table`` are listed in
    ``MID_WEGE_REQUIRED_COLS``; the file is validated to contain them.

    Parameters
    ----------
    mid_dir:
        Directory containing ``MiD2023_Wege.csv``.

    Returns
    -------
    pd.DataFrame
        Full Wege table, one row per (household, person, trip).
    """
    mid_dir = Path(mid_dir)
    wege_path = mid_dir / "MiD2023_Wege.csv"
    if not wege_path.exists():
        raise FileNotFoundError(
            f"MiD Wege file not found: {wege_path}. "
            "Ensure the MiD 2023 delivery is present in the configured mid_dir."
        )
    df = pd.read_csv(wege_path, sep=detect_csv_separator(wege_path), low_memory=False)
    missing = [c for c in MID_WEGE_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"MiD Wege file is missing required columns: {missing}. "
            f"Available columns: {list(df.columns[:20])} ..."
        )
    return df


# --------------------------------------------------------------------------- #
# Donor stratification helpers (Phase 4B)
# --------------------------------------------------------------------------- #

def dominant_stratum_for_1km(
    cells: pd.DataFrame,
    source,
) -> tuple[dict, float]:
    """Compute the dominant stratum per 1 km parent cell by majority vote.

    Each 100 m cell contributes its source-specific stratum label (via
    ``source.cell_stratum``).  The dominant stratum for a 1 km parent is the
    most-frequent label among its 100 m children.  Ties are broken by the
    smallest label (sort-stable).

    A 1 km cell that straddles a Gemeinde or RegioStaR boundary is assigned
    the dominant stratum of its 100 m children; the fraction of children whose
    stratum differs from their 1 km parent's dominant is the **border
    approximation rate** (logged by the caller; returned here for transparency).

    Parameters
    ----------
    cells:
        100 m cells frame.  Must carry ``ZENSUS1km`` and ``RegioStaR7`` (or
        whatever column the source's ``cell_stratum`` reads).
    source:
        Active :class:`braunschweig.popsim.sources.base.PopsimSource` instance.
        Provides :meth:`cell_stratum` to map per-cell RS7 codes to stratum labels.

    Returns
    -------
    tuple[dict[str, Any], float]
        ``(dominant_map, border_rate)`` where:
        - ``dominant_map`` maps each ``ZENSUS1km`` id to its dominant stratum.
        - ``border_rate`` is the fraction of 100 m cells whose stratum differs
          from their 1 km parent's dominant stratum (0.0 = all cells homogeneous).
    """
    cells_work = cells[["ZENSUS1km", "ZENSUS100m"]].copy()
    cells_work["_stratum"] = source.cell_stratum(cells).values

    # Count stratum occurrences per 1 km parent.
    grouped = cells_work.groupby(["ZENSUS1km", "_stratum"], sort=True).size()
    # For each 1 km parent, pick the stratum with the highest count; stable sort
    # means ties resolve to the smallest label alphabetically / numerically.
    dominant_series = grouped.groupby(level="ZENSUS1km").idxmax()
    # idxmax returns (ZENSUS1km, _stratum) tuples as the value; extract stratum.
    dominant_map = {km: idx[1] for km, idx in dominant_series.items()}

    # Compute border rate: fraction of 100m cells whose stratum != parent dominant.
    cells_work["_dominant"] = cells_work["ZENSUS1km"].map(dominant_map)
    n_total = len(cells_work)
    n_border = int((cells_work["_stratum"] != cells_work["_dominant"]).sum())
    border_rate = n_border / n_total if n_total > 0 else 0.0

    return dominant_map, border_rate


def filter_seed_to_stratum(
    seed_households: pd.DataFrame,
    seed_persons: pd.DataFrame,
    stratum_value,
    source,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Filter the donor seed to households matching a single stratum value.

    Retains only the households whose :meth:`source.donor_stratum` equals
    ``stratum_value``, then filters ``seed_persons`` to those household ids.
    The join key is ``source.seed_columns().household_id`` for the MiD path
    (``"H_ID"``); for ENTD it is ``"household_id"``.

    Parameters
    ----------
    seed_households:
        Full donor household frame (returned by the source's ``load_donor``).
    seed_persons:
        Full donor person frame.
    stratum_value:
        The stratum label to retain (e.g. RS7 code ``72`` for MiD, or ``"urban"``
        for ENTD).
    source:
        Active :class:`braunschweig.popsim.sources.base.PopsimSource` instance.

    Returns
    -------
    tuple[pd.DataFrame, pd.DataFrame]
        ``(filtered_households, filtered_persons)`` retaining only the matching
        stratum.

    Raises
    ------
    ValueError
        If no households match ``stratum_value`` (zero-donor guard: the caller
        MUST NOT assemble a PopulationSim batch with an empty seed).
    """
    stratum_series = source.donor_stratum(seed_households)
    mask = stratum_series == stratum_value
    filtered_hh = seed_households[mask].copy()

    if len(filtered_hh) == 0:
        raise ValueError(
            f"[popsim.mid] Donor stratification: no donor households found for "
            f"stratum '{stratum_value}'. "
            f"The donor seed contains strata: {sorted(stratum_series.unique())}. "
            f"To disable stratification set "
            f"'braunschweig.population.popsim.stratify_regiostar' to False."
        )

    # Determine the household-id join column for this source.
    hh_id_col = source.seed_columns().household_id  # e.g. "H_ID" or "household_id"
    retained_hids = set(filtered_hh[hh_id_col])
    person_hh_id_col = source.seed_columns().person_household_id
    filtered_persons = seed_persons[
        seed_persons[person_hh_id_col].isin(retained_hids)
    ].copy()

    return filtered_hh, filtered_persons


# --------------------------------------------------------------------------- #
# Folder assembly + orchestration
# --------------------------------------------------------------------------- #

def assemble_batch_folder(
    folder: Union[str, Path],
    cells_subset: pd.DataFrame,
    base_cols: Sequence[str],
    controls_df: pd.DataFrame,
    seed_households: pd.DataFrame,
    seed_persons: pd.DataFrame,
    *,
    settings_yaml: str,
    logging_yaml: str,
) -> dict[str, Path]:
    """Assemble one PopulationSim run folder for a subset of cells."""
    geo_crosswalk = folders.build_geo_crosswalk(
        cells_subset, id_col_100m="ZENSUS100m", parent_col="ZENSUS1km"
    )
    targets = cells_subset[["ZENSUS100m", *base_cols]].copy()
    control_totals = build_control_totals(targets, geo_crosswalk, base_cols)
    return folders.write_popsim_folder(
        folder,
        geo_crosswalk=geo_crosswalk,
        control_totals=control_totals,
        controls_csv=controls_df,
        seed_households=seed_households,
        seed_persons=seed_persons,
        settings_yaml=settings_yaml,
        logging_yaml=logging_yaml,
    )


def cell_groups(cells_subset: pd.DataFrame) -> dict[str, list[str]]:
    """Map each 1 km parent to its 100 m children (for batching)."""
    return {
        str(parent): group["ZENSUS100m"].astype(str).tolist()
        for parent, group in cells_subset.groupby("ZENSUS1km", sort=True)
    }


def run_popsim_mid(
    cells: pd.DataFrame,
    base_cols: Sequence[str],
    controls_df: pd.DataFrame,
    seed_households: pd.DataFrame,
    seed_persons: pd.DataFrame,
    *,
    work_dir: Union[str, Path],
    settings_yaml: str,
    logging_yaml: str,
    max_cells: int,
    run_one,
    num_workers: int = 3,
    source=None,
    stratify_regiostar: bool = False,
) -> mergemod.MergeReport:
    """Batch the cells into PopulationSim runs, execute them, and merge the output.

    Partitions the 1 km parents into batches of at most ``max_cells`` 100 m cells
    (1 km atomic), assembles one PopulationSim folder per batch, runs them
    concurrently via the injected ``run_one`` (``batch.make_populationsim_run_one``
    in production; a fake in tests), and merges the cell-disjoint outputs. Returns
    the merge report (with the combined expanded-household table).

    When ``stratify_regiostar`` is False (default): the full seed is shared by
    every batch; only the controls / crosswalk are batch-specific. This is
    byte-identical to the pre-4B behaviour.

    When ``stratify_regiostar`` is True: each 1 km parent is assigned its
    dominant RegioStaR stratum (majority vote among its 100 m children); batches
    are partitioned WITHIN each stratum (so a batch never mixes strata); and each
    batch receives only the donor households whose stratum matches the batch's
    stratum.  A zero-donor batch raises :class:`ValueError`.

    Parameters
    ----------
    cells:
        100 m cells frame (ZGB-filtered, with ``ZENSUS1km`` and ``RegioStaR7``).
    base_cols:
        Control base column names (without geography suffix).
    controls_df:
        PopulationSim controls CSV as a DataFrame.
    seed_households:
        Donor household frame (from :func:`load_mid_seed` or the ENTD adapter).
    seed_persons:
        Donor person frame.
    work_dir:
        Directory for batch folders.
    settings_yaml / logging_yaml:
        PopulationSim configuration files as strings.
    max_cells:
        Maximum 100 m cells per batch (soft; a single oversize 1 km parent may
        exceed it).
    run_one:
        Injectable callable ``(folder: str) -> BatchResult``.
    num_workers:
        Thread-pool size for concurrent batch execution.
    source:
        Active :class:`braunschweig.popsim.sources.base.PopsimSource` instance.
        Required when ``stratify_regiostar=True``; ignored when False.
    stratify_regiostar:
        Flag-gate.  Default False (OFF path is byte-identical).
    """
    work_dir = Path(work_dir)
    groups = cell_groups(cells)

    if not stratify_regiostar:
        # Default OFF path: unchanged behaviour (byte-identical to pre-4B).
        partitions = batch.partition_by_1km(groups, max_cells)
        batch_folders: list[str] = []
        for index, km_cells in enumerate(partitions):
            subset = cells[cells["ZENSUS1km"].isin(km_cells)].copy()
            folder = work_dir / f"batch_{index:03d}"
            assemble_batch_folder(
                folder, subset, base_cols, controls_df,
                seed_households, seed_persons,
                settings_yaml=settings_yaml, logging_yaml=logging_yaml,
            )
            batch_folders.append(str(folder))

        batch.run_batches(batch_folders, run_one, num_workers=num_workers)
        return mergemod.merge_batch_folders(batch_folders)

    # Stratified ON path (Phase 4B).
    # Requires a source with cell_stratum and donor_stratum.
    if source is None:
        raise ValueError(
            "[popsim.mid] stratify_regiostar=True requires a source adapter "
            "(pass source=<PopsimSource instance>)."
        )

    # (1) Compute dominant stratum per 1 km parent; log border approximation rate.
    dominant_map, border_rate = dominant_stratum_for_1km(cells, source)
    logger.info(
        "[popsim.mid] RegioStaR stratification: %d 1km parents; "
        "border approximation rate %.1f%% (fraction of 100m cells whose stratum "
        "differs from their 1km parent's dominant).",
        len(dominant_map), 100.0 * border_rate,
    )

    # (2) Group 1km parents by dominant stratum.
    stratum_to_km_ids: dict = {}
    for km_id, stratum in dominant_map.items():
        stratum_to_km_ids.setdefault(stratum, []).append(km_id)

    logger.info(
        "[popsim.mid] strata: %s",
        {str(s): len(ids) for s, ids in stratum_to_km_ids.items()},
    )

    # (3) Partition WITHIN each stratum + filter seed per batch.
    batch_folders_stratified: list[str] = []
    global_batch_index = 0
    for stratum, km_ids in sorted(stratum_to_km_ids.items(), key=lambda x: str(x[0])):
        # Build the per-stratum cell_groups (only 1km parents in this stratum).
        stratum_groups = {km: groups[km] for km in km_ids if km in groups}
        partitions_s = batch.partition_by_1km(stratum_groups, max_cells)

        # Filter donor seed once per stratum; reuse across all batches of the stratum.
        hh_stratum, p_stratum = filter_seed_to_stratum(
            seed_households, seed_persons, stratum, source
        )
        logger.info(
            "[popsim.mid] stratum %s: %d batches, %d donor households.",
            stratum, len(partitions_s), len(hh_stratum),
        )

        for km_cells in partitions_s:
            subset = cells[cells["ZENSUS1km"].isin(km_cells)].copy()
            folder = work_dir / f"batch_{global_batch_index:03d}"
            assemble_batch_folder(
                folder, subset, base_cols, controls_df,
                hh_stratum, p_stratum,
                settings_yaml=settings_yaml, logging_yaml=logging_yaml,
            )
            batch_folders_stratified.append(str(folder))
            global_batch_index += 1

    batch.run_batches(batch_folders_stratified, run_one, num_workers=num_workers)
    return mergemod.merge_batch_folders(batch_folders_stratified)
