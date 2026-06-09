"""synpp stage for the popsim_mid population producer.

Runs the validated popsim_mid chain (prepared cells -> control totals ->
complete-household MiD seed -> PopulationSim per 1 km batch -> merge) and returns
the merged expanded-household table (one row per synthetic household, located by
100 m cell). PopulationSim runs in its own uv environment as a subprocess
(``uv run populationsim``), so the heavy synthesizer stays out of the eqasim
process.

This stage is the popsim_mid *producer*; the selector
(``braunschweig.population.selector``) routes ``population.method == popsim_mid``
here. After the merge it expands the donor households into the full eqasim persons
frame (``braunschweig.popsim.assembly.build_persons``: join the MiD donor persons,
map demographics + attributes, validate against ``braunschweig.population.schema``)
and returns that. The home-location placement (``braunschweig.popsim.handoff``) and
the activity-chain construction (``braunschweig.popsim.trips``) are layered on top
when feeding the spatial / trip stages.

Config keys (all under ``braunschweig.population.popsim.*``); defaults point at the
canonical local-only layout (docs/population/DATA_LAYOUT.md) and the committed
popsimprep PopulationSim config.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from braunschweig.popsim import assembly
from braunschweig.popsim import batch
from braunschweig.popsim import mid

# Config keys.
KEY_CELLS = "braunschweig.population.popsim.cells_100m_path"
KEY_MID = "braunschweig.population.popsim.mid_raw_path"
KEY_CONTROLS = "braunschweig.population.popsim.controls_path"
KEY_SETTINGS = "braunschweig.population.popsim.settings_path"
KEY_LOGGING = "braunschweig.population.popsim.logging_path"
KEY_POPSIMPREP = "braunschweig.population.popsim.popsimprep_dir"
KEY_UV = "braunschweig.population.popsim.uv_path"
KEY_MAX_CELLS = "braunschweig.population.popsim.max_cells"
KEY_WORKERS = "braunschweig.population.popsim.num_workers"
KEY_WORK_DIR = "braunschweig.population.popsim.work_dir"
KEY_KREISE = "braunschweig.political_prefix"


def configure(context):
    """Declare the popsim_mid config keys."""
    context.config(KEY_CELLS)
    context.config(KEY_MID)
    context.config(KEY_CONTROLS)
    context.config(KEY_SETTINGS)
    context.config(KEY_LOGGING)
    context.config(KEY_POPSIMPREP)
    context.config(KEY_UV)
    context.config(KEY_MAX_CELLS, 3000)
    context.config(KEY_WORKERS, 3)
    context.config(KEY_WORK_DIR)
    context.config(KEY_KREISE)


def execute(context) -> pd.DataFrame:
    """Run popsim_mid and return the merged expanded-household table."""
    cells_path = context.config(KEY_CELLS)
    mid_dir = context.config(KEY_MID)
    controls_path = context.config(KEY_CONTROLS)
    settings_path = context.config(KEY_SETTINGS)
    logging_path = context.config(KEY_LOGGING)
    popsimprep_dir = context.config(KEY_POPSIMPREP)
    uv_path = context.config(KEY_UV)
    max_cells = int(context.config(KEY_MAX_CELLS, 3000))
    num_workers = int(context.config(KEY_WORKERS, 3))
    work_dir = context.config(KEY_WORK_DIR)
    kreise = list(context.config(KEY_KREISE))

    controls_df = pd.read_csv(controls_path, sep=";")
    base_cols = mid.control_base_columns(controls_df, "ZENSUS100m")

    cells = mid.load_control_cells(cells_path, base_cols)
    cells = mid.filter_zgb_cells(cells, kreise)

    seed_households, seed_persons, report = mid.load_mid_seed(mid_dir)
    context.set_info("seed_completeness_rate", report.completeness_rate)

    run_one = batch.make_populationsim_run_one(
        command_prefix=(str(uv_path), "run", "--no-sync", "populationsim"),
        cwd=popsimprep_dir,
    )

    merge_report = mid.run_popsim_mid(
        cells, base_cols, controls_df, seed_households, seed_persons,
        work_dir=Path(work_dir),
        settings_yaml=Path(settings_path).read_text(encoding="utf-8"),
        logging_yaml=Path(logging_path).read_text(encoding="utf-8"),
        max_cells=max_cells,
        run_one=run_one,
        num_workers=num_workers,
    )
    context.set_info("popsim_n_households", merge_report.n_rows)
    context.set_info("popsim_n_cells", merge_report.n_cells)
    context.set_info("popsim_n_missing_batches", merge_report.n_missing)

    # Join the 12-digit ARS from the cells frame back onto the merged PopulationSim
    # output.  PopulationSim writes only ZENSUS100m + H_ID to its output CSV, so the
    # ARS column must be recovered here before assembly.build_persons can derive the
    # commune_id / departement_id / iris_id columns required by
    # synthesis.population.spatial.home.zones (bug D1: KeyError on missing columns).
    ars_col = mid._ARS_COLUMN  # "RegionalSchlussel_ARS"
    cell_ars = cells[["ZENSUS100m", ars_col]].drop_duplicates("ZENSUS100m")
    combined = merge_report.combined.merge(cell_ars, on="ZENSUS100m", how="left")
    n_missing_ars = int(combined[ars_col].isna().sum())
    if n_missing_ars:
        import logging
        logging.getLogger(__name__).warning(
            "[popsim.stage] %d/%d households could not be matched to an ARS after "
            "the cells join (unexpected; cells used in PopulationSim must be a subset "
            "of the loaded cells frame).",
            n_missing_ars, len(combined),
        )

    # Expand the merged donor households into the full eqasim persons frame:
    # join the MiD donor persons, map demographics + attributes, and validate the
    # output against the shared population schema.
    mid_households, mid_persons = mid.load_mid_attributes(mid_dir)
    persons, pseudonym_map = assembly.build_persons(combined, mid_households, mid_persons)
    context.set_info("popsim_n_persons", len(persons))

    # Write the local-only pseudonym map so internal re-linking is possible.
    # This file maps each surrogate source_person_id / source_household_id back
    # to the raw MiD H_ID / P_ID.  It MUST NOT be committed or published; it
    # lives in the pipeline work_dir which is a local-only, gitignored path.
    pseudonym_map_path = Path(work_dir) / "pseudonym_map.csv"
    pseudonym_map.to_csv(pseudonym_map_path, index=False)
    import logging as _logging
    _logging.getLogger(__name__).info(
        "[popsim.stage] Pseudonym map written to %s (%d unique donor persons).",
        pseudonym_map_path, len(pseudonym_map),
    )

    return persons
