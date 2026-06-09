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

SUFFIX_100M = "_ZENSUS100m"
SUFFIX_1KM = "_ZENSUS1km"

# Cell columns always loaded in addition to the control bases: the population
# total (for parent selection / diagnostics) and the ARS key (for the ZGB filter).
_EXTRA_CELL_COLUMNS = ("POP_TOTAL_100m_adj", "RegionalSchlussel_ARS")
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
    households = pd.read_csv(
        mid_dir / "MiD2023_Haushalte.csv",
        usecols=[columns.household_id, columns.household_weight],
    )
    person_cols = [
        columns.person_household_id, columns.person_id, columns.person_weight,
        columns.age, columns.sex,
    ]
    if columns.day_filter_col:
        person_cols.append(columns.day_filter_col)
    persons = pd.read_csv(
        mid_dir / "MiD2023_Personen.csv",
        usecols=list(dict.fromkeys(person_cols)),
    )

    households, persons, report = seedmod.filter_complete_households(
        households, persons, columns,
        day_filter_values=day_filter_values or columns.day_filter_values,
    )
    households, persons = seedmod.select_seed_columns(households, persons, columns)
    return households, persons, report


# MiD columns needed to enrich the synthetic persons (beyond the seed control cols).
MID_PERSON_ATTR_COLS = (
    "H_ID", "P_ID", "HP_ALTER", "HP_SEX", "P_TAET", "P_FSCHEIN", "P_FKARTE", "P_BKAT",
)
MID_HOUSEHOLD_ATTR_COLS = (
    "H_ID", "oek_status", "hheink_gr1", "H_ANZAUTO", "H_ANZRAD",
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
    households = pd.read_csv(
        mid_dir / "MiD2023_Haushalte.csv", usecols=list(MID_HOUSEHOLD_ATTR_COLS)
    )
    persons = pd.read_csv(
        mid_dir / "MiD2023_Personen.csv", usecols=list(MID_PERSON_ATTR_COLS)
    )
    return households, persons


def load_mid_wege(
    mid_dir: Union[str, Path],
) -> pd.DataFrame:
    """Load the MiD 2023 Wege (trip) table for the trips_stage.

    MiD2023_Wege.csv uses a semicolon field separator (German locale export
    convention, confirmed from the raw INFAS delivery). All columns are loaded
    (no usecols filter) so that every MiD Wege extra column is available as
    a traceability/analysis extra in the output trip table. The minimum columns
    required by ``braunschweig.popsim.trips.build_trip_table`` are listed in
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
    df = pd.read_csv(wege_path, sep=";", low_memory=False)
    missing = [c for c in MID_WEGE_REQUIRED_COLS if c not in df.columns]
    if missing:
        raise ValueError(
            f"MiD Wege file is missing required columns: {missing}. "
            f"Available columns: {list(df.columns[:20])} ..."
        )
    return df


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
) -> mergemod.MergeReport:
    """Batch the cells into PopulationSim runs, execute them, and merge the output.

    Partitions the 1 km parents into batches of at most ``max_cells`` 100 m cells
    (1 km atomic), assembles one PopulationSim folder per batch, runs them
    concurrently via the injected ``run_one`` (``batch.make_populationsim_run_one``
    in production; a fake in tests), and merges the cell-disjoint outputs. Returns
    the merge report (with the combined expanded-household table).

    The seed (households + persons) is shared by every batch; only the controls /
    crosswalk are batch-specific.
    """
    work_dir = Path(work_dir)
    groups = cell_groups(cells)
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
