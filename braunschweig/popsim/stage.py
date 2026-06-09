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

The active donor source is controlled by ``braunschweig.population.popsim.source``
(default ``"mid"``). The default ``"mid"`` path is byte-identical to the pre-source
implementation. Switching to ``"entd"`` (Phase 2) will route the seed build, donor
loading, and attribute mapping through the ENTD adapter without changing the
structural PopulationSim orchestration.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from braunschweig.popsim import assembly
from braunschweig.popsim import batch
from braunschweig.popsim import mid
from braunschweig.popsim import sources

logger = logging.getLogger(__name__)

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
# Donor source identifier: "mid" (default) or a future registered source name.
KEY_SOURCE = "braunschweig.population.popsim.source"


def _resolve_source(source_name: str) -> sources.PopsimSource:
    """Return a PopsimSource adapter for the given source name.

    This thin helper is factored out of ``execute`` so it can be called and
    tested independently without running PopulationSim.

    Parameters
    ----------
    source_name:
        Short lowercase source identifier, e.g. ``"mid"``.  Passed through to
        :func:`braunschweig.popsim.sources.get_source`.

    Returns
    -------
    PopsimSource
        A fresh adapter instance for ``source_name``.

    Raises
    ------
    NotImplementedError
        If ``source_name`` is planned-but-not-yet-implemented (e.g. ``"entd"``).
    ValueError
        If ``source_name`` is not a known or planned source name.
    """
    return sources.get_source(source_name)


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
    context.config(KEY_SOURCE, "mid")


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
    source_name = context.config(KEY_SOURCE, "mid")

    source = _resolve_source(source_name)
    logger.info("[popsim.stage] active donor source: %s", source.name)

    controls_df = pd.read_csv(controls_path, sep=";")
    base_cols = mid.control_base_columns(controls_df, "ZENSUS100m")

    cells = mid.load_control_cells(cells_path, base_cols)
    cells = mid.filter_zgb_cells(cells, kreise)

    # The seed build uses the source's column mapping so the PopulationSim seed
    # schema can differ between survey sources.  For "mid" this delegates to
    # mid.load_mid_seed (unchanged), preserving byte-identity.
    seed_columns = source.seed_columns()
    seed_households, seed_persons, report = mid.load_mid_seed(
        mid_dir, columns=seed_columns
    )
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
        logger.warning(
            "[popsim.stage] %d/%d households could not be matched to an ARS after "
            "the cells join (unexpected; cells used in PopulationSim must be a subset "
            "of the loaded cells frame).",
            n_missing_ars, len(combined),
        )

    # Load the donor attribute tables through the active source adapter.
    # For source="mid" this calls MidSource.load_donor -> mid.load_mid_attributes
    # + mid.load_mid_wege, which is byte-identical to the previous direct call.
    # The trips table is not needed at this stage (it is used by trips_stage), so
    # we discard it here.
    donor_households, donor_persons, _donor_trips = source.load_donor(mid_dir)

    # Expand the merged donor households into the full eqasim persons frame:
    # join the donor persons, map demographics + attributes, and validate the
    # output against the shared population schema.
    # NOTE: assembly.build_persons calls map_mid_person_attributes internally,
    # which is MiD-specific.  For the current "mid" default this is correct and
    # byte-identical to the previous code.  A future "entd" source will require
    # a source-parameterised assembly path (Phase 2).
    persons, pseudonym_map = assembly.build_persons(combined, donor_households, donor_persons)
    context.set_info("popsim_n_persons", len(persons))

    # Write the local-only pseudonym map so internal re-linking is possible.
    # This file maps each surrogate source_person_id / source_household_id back
    # to the raw MiD H_ID / P_ID.  It MUST NOT be committed or published; it
    # lives in the pipeline work_dir which is a local-only, gitignored path.
    pseudonym_map_path = Path(work_dir) / "pseudonym_map.csv"
    pseudonym_map.to_csv(pseudonym_map_path, index=False)
    logger.info(
        "[popsim.stage] Pseudonym map written to %s (%d unique donor persons).",
        pseudonym_map_path, len(pseudonym_map),
    )

    return persons
