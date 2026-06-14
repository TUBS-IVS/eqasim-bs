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

import numpy as np
import pandas as pd

from braunschweig.popsim import assembly
from braunschweig.popsim import batch
from braunschweig.popsim import income_spatial_tilt as _ist
from braunschweig.popsim import mid
from braunschweig.popsim import prepared_cells
from braunschweig.popsim import seed as seedmod
from braunschweig.popsim import sources
from braunschweig.popsim.income import HIGH_INCOME_THRESHOLD_EUR

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
# Phase 4B: RegioStaR donor stratification flag (default OFF = byte-identical).
KEY_STRATIFY = "braunschweig.population.popsim.stratify_regiostar"
# Member completion (decision D3, mid source only): fill member-incomplete MiD
# donor households by mirror-household sampling, in ONE pass on the attribute
# donor tables that feeds BOTH the PopulationSim seed and the expansion.
# Default ON (project rule: new features default on); False reproduces the
# legacy load_mid_seed + load_donor path byte-identically.
KEY_COMPLETE_MEMBERS = "braunschweig.population.popsim.complete_members"
# Controls source: "csv" (default, byte-identical, reads the external hand-edited
# file at KEY_CONTROLS) or "catalog" (renders from the typed control catalog via
# control_spec; Task 5 of the control-catalog plan).
KEY_CONTROLS_SOURCE = "braunschweig.population.popsim.controls_source"
# Control tiers: comma-separated tier names included when controls_source="catalog".
# Default "tier0" = byte-identical to the pre-Task-7 baseline.
KEY_CONTROL_TIERS = "braunschweig.population.popsim.control_tiers"
# Spatial income tilt (Nettokaltmiete GAMMA layer): default ON per project rule.
# When ON, applies a within-Kreis income redistribution scaled by the per-cell
# net cold rent index (renters) or Eigentümerquote index (owners), preserving the
# per-Kreis income mean exactly. When OFF, the income frame is unchanged (byte-identical).
KEY_INCOME_TILT = "braunschweig.population.popsim.income_spatial_tilt"
KEY_INCOME_TILT_BETA = "braunschweig.population.popsim.income_tilt_beta"
KEY_INCOME_TILT_CLIP = "braunschweig.population.popsim.income_tilt_clip"


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


def build_controls_df(*, controls_source="csv", controls_path=None, seed="mid", tiers=("tier0",)):
    """Return the PopulationSim controls.csv frame.

    controls_source="csv": read the external hand-edited file at controls_path (today's
    behaviour, byte-identical). controls_source="catalog": render the seed-filtered
    catalog via control_spec (the new source of truth).

    tiers: tuple of tier names to include when controls_source="catalog". Default
    ("tier0",) reproduces the pre-Task-7 baseline byte-identically.
    """
    if controls_source == "csv":
        return pd.read_csv(controls_path, sep=";")
    if controls_source == "catalog":
        from braunschweig.popsim import control_spec as cs
        catalog = cs.full_catalog(include_tiers=tiers)
        return cs.render_catalog_csv(cs.controls_for_seed(catalog, seed), seed)
    raise ValueError(f"unknown controls_source {controls_source!r}")


def build_aggregation_map(*, controls_source="csv", controls_path=None, seed="mid", tiers=("tier0",)):
    """Return the multi-column aggregation map for the active controls.

    For ``controls_source="catalog"``: derives the aggregation map from the typed
    catalog -- maps ``{derived_name: source_cols}`` for controls whose name is NOT
    a raw parquet column (i.e. multi-source / derived names like
    ``building_type_ein_zweifamilienhaus``).  Empty dict for tier0-only (all controls
    are single-source identity -> no aggregation needed).

    For ``controls_source="csv"``: returns an empty dict (the CSV hand-edited file
    does not carry census_source metadata; caller handles no aggregation).

    The returned map is consumed by
    :func:`braunschweig.popsim.prepared_cells.add_aggregated_controls`.
    """
    if controls_source != "catalog":
        return {}
    from braunschweig.popsim import control_spec as cs
    catalog = cs.full_catalog(include_tiers=tiers)
    active = cs.controls_for_seed(catalog, seed)
    return cs.build_aggregation_map(active)


def build_source_columns(*, controls_source="csv", controls_df=None, seed="mid", tiers=("tier0",)):
    """Return the RAW census parquet columns to load for the active controls.

    For ``controls_source="catalog"``: returns the union of all census_source columns
    of the active seed-filtered controls.  For single-source identity controls
    (tier0) this equals the current ``control_base_columns`` output exactly.

    For ``controls_source="csv"``: returns None (caller uses control_base_columns as
    today).
    """
    if controls_source != "catalog":
        return None
    from braunschweig.popsim import control_spec as cs
    catalog = cs.full_catalog(include_tiers=tiers)
    active = cs.controls_for_seed(catalog, seed)
    return cs.source_columns_union(active)


def configure(context):
    """Declare the popsim config keys.

    When the donor source is "entd" (popsim_open workflow), an additional
    dependency on ``data.hts.selected`` is registered so synpp wires the
    cleaned ENTD frames into the DAG.  For source="mid" (default) the stage
    depends only on the local config paths and no HTS stage is needed.

    The INKAR per-Kreis income scale (``braunschweig.data.inkar.household_income``)
    is registered as a dependency for BOTH sources so that ``household_income_eur``
    is scaled by the per-Kreis factor and ``high_income`` is set to the unified
    numeric threshold (>= 5000 EUR) for all popsim producers.
    """
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
    # Seeded attribute imputation in build_persons; declaring the key also makes
    # synpp invalidate the stage cache when the pipeline random_seed changes.
    context.config("random_seed")
    # Phase 4B: RegioStaR donor stratification.  Default False = byte-identical to
    # pre-4B: each batch receives the full seed, no stratum filtering.
    context.config(KEY_STRATIFY, False)
    # Member completion (D3). Default True; False -> legacy path (see execute()).
    context.config(KEY_COMPLETE_MEMBERS, True)
    # Controls source (Task 5). Default "csv" = byte-identical to today's behaviour.
    context.config(KEY_CONTROLS_SOURCE, "csv")
    # Control tiers (Task 7). Default "tier0" = byte-identical to pre-Task-7 baseline.
    context.config(KEY_CONTROL_TIERS, "tier0")
    # Spatial income tilt (Task 3). Default ON (project rule: features default on).
    # When OFF, the income frame is unchanged (byte-identical); no cells parquet
    # re-read occurs.
    context.config(KEY_INCOME_TILT, True)
    context.config(KEY_INCOME_TILT_BETA, 0.3)
    context.config(KEY_INCOME_TILT_CLIP, 0.30)

    # INKAR per-Kreis household income scale: used by both popsim_mid and popsim_open
    # to apply the same income scaling as the IPF/enriched path.
    context.stage("braunschweig.data.inkar.household_income", alias="inkar_income")

    source_name = context.config(KEY_SOURCE, "mid")
    if source_name == "entd":
        # popsim_open: the ENTD donor for the PopulationSim SEED + attribute/trip
        # mapping must carry the FULL household composition (multi-person households).
        # We deliberately use data.hts.entd.FILTERED, NOT data.hts.selected:
        # data.hts.selected resolves (via hts="entd") to data.hts.entd.reweighted,
        # which collapses the survey to ONE person per household for the eqasim IPF
        # person-matching path (49283 -> 17997 persons, mean 1.0/household). Seeding
        # PopulationSim with that would yield all-1-person synthetic households.
        # data.hts.entd.filtered keeps the full composition (20178 hh / 49283 persons,
        # mean 2.44) with the natural ENTD weights -- the unbiased seed PopulationSim
        # then reweights to the German Zensus controls. (ENTD-specific because
        # popsim_open is ENTD-only; extend this branch for another survey.)
        context.stage("data.hts.entd.filtered", alias="hts_donor")


def join_cell_attributes(
    combined: pd.DataFrame,
    cells: pd.DataFrame,
    *,
    ars_col: str = mid._ARS_COLUMN,
) -> pd.DataFrame:
    """Join the per-cell attributes onto the merged PopulationSim output.

    PopulationSim writes only ``ZENSUS100m`` + ``H_ID`` to its output CSV, so
    everything the downstream assembly needs per SYNTHETIC HOME cell must be
    recovered here from the loaded cells frame:

    - the 12-digit ARS (``RegionalSchlussel_ARS``): required by
      ``assembly.derive_zone_ids`` for commune_id / departement_id / iris_id
      (bug D1: spatial home.zones KeyError without it);
    - ``RegioStaR7`` (when the cells parquet carries it): the synthetic home's
      urban/rural class. ``assembly.build_persons`` expands it onto every
      synthetic person, where it serves as the SPATIAL stage-B matching key
      (``braunschweig.popsim.trips.MATCHED_REPLACEMENT_COLUMNS``). Note this is
      deliberately the CELL's RS7, not the MiD donor household's survey RS7:
      the donor frame's ``RegioStaR7`` is never merged onto persons (it is not
      part of ``assembly._HOUSEHOLD_ATTRS``), so no collision can occur.

    Parameters
    ----------
    combined:
        Merged PopulationSim output (one row per synthetic household, with
        ``ZENSUS100m``).
    cells:
        Loaded (ZGB-filtered) cells frame from ``mid.load_control_cells``.
    ars_col:
        Name of the 12-digit ARS column on the cells frame.

    Returns
    -------
    pandas.DataFrame
        ``combined`` with ``ars_col`` (always) and ``RegioStaR7`` (when
        available on ``cells``) joined per 100 m cell.
    """
    join_cols = ["ZENSUS100m", ars_col]
    has_rs7 = "RegioStaR7" in cells.columns
    if has_rs7:
        join_cols.append("RegioStaR7")
    else:
        logger.info(
            "[popsim.stage] cells frame carries no 'RegioStaR7' column (older "
            "parquet); synthetic persons get no home-cell RS7 and stage-B chain "
            "matching falls back to the non-spatial key list."
        )

    cell_attributes = cells[join_cols].drop_duplicates("ZENSUS100m")
    combined = combined.merge(cell_attributes, on="ZENSUS100m", how="left")

    n_missing_ars = int(combined[ars_col].isna().sum())
    if n_missing_ars:
        logger.warning(
            "[popsim.stage] %d/%d households could not be matched to an ARS after "
            "the cells join (unexpected; cells used in PopulationSim must be a subset "
            "of the loaded cells frame).",
            n_missing_ars, len(combined),
        )

    if has_rs7:
        n_missing_rs7 = int(combined["RegioStaR7"].isna().sum())
        logger.info(
            "[popsim.stage] cell RegioStaR7 joined onto %d households "
            "(%d missing -> NaN).",
            len(combined), n_missing_rs7,
        )

    return combined


def execute(context) -> pd.DataFrame:
    """Run popsim_mid and return the merged expanded-household table."""
    cells_path = context.config(KEY_CELLS)
    mid_dir = context.config(KEY_MID)
    controls_path = context.config(KEY_CONTROLS)
    settings_path = context.config(KEY_SETTINGS)
    logging_path = context.config(KEY_LOGGING)
    popsimprep_dir = context.config(KEY_POPSIMPREP)
    uv_path = context.config(KEY_UV)
    # synpp's ExecuteContext.config() takes only the key; the defaults are
    # registered in configure() (3000 / 3 / "mid" / False) and resolved here.
    max_cells = int(context.config(KEY_MAX_CELLS))
    num_workers = int(context.config(KEY_WORKERS))
    work_dir = context.config(KEY_WORK_DIR)
    kreise = list(context.config(KEY_KREISE))
    source_name = context.config(KEY_SOURCE)
    stratify_regiostar = bool(context.config(KEY_STRATIFY))
    # Member completion (D3) applies to the MiD donor source only: the ENTD
    # frames have no declared-size column and need no completion.
    complete_members = bool(context.config(KEY_COMPLETE_MEMBERS)) and source_name == "mid"

    # Seeded RNG for the stochastic attribute imputation in build_persons
    # (offset +74511 keeps the stream disjoint from the enriched-stage offsets).
    random_seed = int(context.config("random_seed"))
    rng = np.random.RandomState(random_seed + 74511)

    source = _resolve_source(source_name)
    logger.info("[popsim.stage] active donor source: %s", source.name)

    # Parse the comma-separated tier string (e.g. "tier0,tier1") into a tuple.
    control_tiers_str = context.config(KEY_CONTROL_TIERS)
    control_tiers = tuple(t.strip() for t in control_tiers_str.split(",") if t.strip())
    controls_source = context.config(KEY_CONTROLS_SOURCE)
    controls_df = build_controls_df(
        controls_source=controls_source,
        controls_path=controls_path,
        seed=source_name,
        tiers=control_tiers,
    )
    base_cols = mid.control_base_columns(controls_df, "ZENSUS100m")

    # For catalog-based controls with multi-column census sources (e.g. building_type),
    # load the raw source columns from the parquet (union of all census_source tuples)
    # rather than the derived control names.  For tier0-only or CSV-based controls,
    # source_cols == base_cols == current behaviour -> byte-identical.
    source_cols_override = build_source_columns(
        controls_source=controls_source,
        seed=source_name,
        tiers=control_tiers,
    )
    load_cols = source_cols_override if source_cols_override is not None else base_cols

    cells = mid.load_control_cells(cells_path, load_cols)
    cells = mid.filter_zgb_cells(cells, kreise)

    # Derive the multi-column aggregated control columns (e.g. building_type_*).
    # For tier0-only: agg_map is empty -> add_aggregated_controls returns cells
    # unchanged -> byte-identical.
    agg_map = build_aggregation_map(
        controls_source=controls_source,
        seed=source_name,
        tiers=control_tiers,
    )
    cells = prepared_cells.add_aggregated_controls(cells, agg_map)

    # Build the PopulationSim seed.
    # For source="mid": delegates to mid.load_mid_seed which reads the MiD CSV
    # files with MiD column names (H_ID/H_GEW/HP_ALTER/HP_SEX/P_GEW).
    # For source="entd": the ENTD donor frames are transformed to MiD column
    # schema by EntdSource.build_seed so the downstream (expand, map_demographics)
    # runs unchanged; only map_person_attributes is ENTD-specific.
    # The completed donor frames (member completion ON) are loaded here, ahead
    # of PopulationSim, because the SEED is derived from them; they are reused
    # verbatim as the expansion donor tables further below (ONE completion pass
    # -> seed and expansion contain the same fillers).
    completed_donor_households = None
    completed_donor_persons = None
    if source_name == "entd":
        # popsim_open: retrieve the cleaned ENTD frames from the synpp DAG
        # (registered in configure() as alias "hts_donor") and build the seed.
        # context.stage() is idempotent in synpp; retrieving the same alias twice
        # returns the same cached result, so this does not re-run the stage.
        hts_hh_seed, hts_persons_seed, _hts_trips_seed = context.stage("hts_donor")
        seed_households, seed_persons, report = source.build_seed(
            hts_hh_seed, hts_persons_seed
        )
    elif complete_members:
        # popsim_mid with member completion (D3, default ON): ONE completion pass
        # on the attribute-bearing donor tables (day-filtered like the legacy
        # seed), then the PopulationSim seed is projected out of the completed
        # frames. RNG offset +74513 keeps the mirror-draw stream disjoint from
        # the +74511 attribute-imputation stream below.
        (
            completed_donor_households, completed_donor_persons,
            report, completion_report,
        ) = mid.load_completed_donor(
            mid_dir, completion_rng=np.random.RandomState(random_seed + 74513),
        )
        seed_columns = source.seed_columns()
        seed_households, seed_persons = seedmod.select_seed_columns(
            completed_donor_households, completed_donor_persons, seed_columns,
            extra_household_cols=("RegioStaR7", "H_GR", "H_MIETE", "haustyp"),
        )
        context.set_info(
            "member_completion_filled", completion_report.n_households_filled
        )
        context.set_info(
            "member_completion_persons_added", completion_report.n_persons_added
        )
    else:
        # popsim_mid, complete_members=False: reads MiD CSV files directly from
        # mid_dir. This path is byte-identical to all prior versions.
        seed_columns = source.seed_columns()
        seed_households, seed_persons, report = mid.load_mid_seed(
            mid_dir, columns=seed_columns
        )
    context.set_info("seed_completeness_rate", report.completeness_rate)

    run_one = batch.make_populationsim_run_one(
        command_prefix=(str(uv_path), "run", "--no-sync", "populationsim"),
        cwd=popsimprep_dir,
    )

    logger.info(
        "[popsim.stage] stratify_regiostar=%s (Phase 4B donor stratification).",
        stratify_regiostar,
    )
    merge_report = mid.run_popsim_mid(
        cells, base_cols, controls_df, seed_households, seed_persons,
        work_dir=Path(work_dir),
        settings_yaml=Path(settings_path).read_text(encoding="utf-8"),
        logging_yaml=Path(logging_path).read_text(encoding="utf-8"),
        max_cells=max_cells,
        run_one=run_one,
        num_workers=num_workers,
        source=source,
        stratify_regiostar=stratify_regiostar,
    )
    context.set_info("popsim_n_households", merge_report.n_rows)
    context.set_info("popsim_n_cells", merge_report.n_cells)
    context.set_info("popsim_n_missing_batches", merge_report.n_missing)

    # Join the per-cell attributes (12-digit ARS + RegioStaR7 when available)
    # from the cells frame back onto the merged PopulationSim output: the ARS
    # feeds assembly.derive_zone_ids (commune/departement/iris ids, bug D1) and
    # the cell RS7 becomes the spatial stage-B chain-matching key on every
    # expanded synthetic person (see join_cell_attributes).
    combined = join_cell_attributes(merge_report.combined, cells)

    # Load the donor attribute tables through the active source adapter.
    # For source="mid": MidSource.load_donor reads from mid_dir (byte-identical).
    # For source="entd": EntdSource.load_donor receives the frames injected from
    # the data.hts.selected stage (no filesystem read).
    if source_name == "entd":
        # popsim_open: retrieve the cleaned ENTD frames from the synpp DAG
        # (registered in configure as alias "hts_donor") and inject them.
        hts_hh, hts_persons, hts_trips = context.stage("hts_donor")
        donor_households, donor_persons, _donor_trips = source.load_donor(
            mid_dir, injected=(hts_hh, hts_persons, hts_trips)
        )
    elif complete_members:
        # Member completion ON: the completed frames ARE the donor tables.
        # Reloading via source.load_donor would lose the fillers and their
        # source_H_ID / source_P_ID traceability (seed/expansion inconsistency).
        donor_households = completed_donor_households
        donor_persons = completed_donor_persons
        logger.info(
            "[popsim.stage] member completion ON: expansion uses the completed "
            "donor frames (%d households, %d persons incl. fillers).",
            len(donor_households), len(donor_persons),
        )
    else:
        # popsim_mid, complete_members=False (legacy): reads MiD CSV files
        # directly from mid_dir.
        donor_households, donor_persons, _donor_trips = source.load_donor(mid_dir)

    # Load the per-Kreis INKAR income scale (registered in configure).
    # Used by assembly.build_persons to scale household_income_eur and set
    # high_income with the unified numeric rule (>= 5000 EUR) for both sources.
    inkar_income = context.stage("inkar_income")

    # Expand the merged donor households into the full eqasim persons frame.
    # pseudonymise=True (MiD): replace raw H_ID/P_ID with sequential surrogates
    # (data-protection requirement for the restricted MiD scientific-use licence).
    # pseudonymise=False (ENTD): source_* are set directly to the open ENTD ids
    # by EntdSource.map_person_attributes; no surrogate mapping is needed.
    pseudonymise = (source_name == "mid")
    persons, pseudonym_map = assembly.build_persons(
        combined, donor_households, donor_persons,
        rng=rng,
        attribute_mapper=source.map_person_attributes,
        pseudonymise=pseudonymise,
        inkar_scale=inkar_income,
    )
    context.set_info("popsim_n_persons", len(persons))

    # --- Spatial income tilt (Nettokaltmiete GAMMA layer, Task 3) ---------------
    # Applies a within-Kreis income redistribution guided by per-cell net cold rent
    # (renters) and Eigentümerquote (owners), preserving each Kreis's income mean
    # exactly.  Controlled by KEY_INCOME_TILT (default ON per project rule).
    # When OFF, the income frame is byte-identical.
    income_tilt_enabled = bool(context.config(KEY_INCOME_TILT))
    income_tilt_beta = float(context.config(KEY_INCOME_TILT_BETA))
    income_tilt_clip = float(context.config(KEY_INCOME_TILT_CLIP))

    if income_tilt_enabled:
        # NaN income guard: unclassifiable households (MiD map_household_income_eur
        # default=None; ENTD class -1 midpoint=None) may carry NaN household_income_eur.
        # apply_inkar_income_eur preserves these NaNs intentionally (high_income uses
        # .fillna(0.0) but the eur column itself stays NaN for missing income class).
        # We log the count for fallback-transparency (CLAUDE.md no-silent-fallback rule)
        # and rely on maybe_apply_income_tilt to shield NaN-income rows from the tilt.
        n_nan = int(persons["household_income_eur"].isna().sum())
        if n_nan > 0:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: %d/%d persons (%.1f%%) have "
                "NaN household_income_eur (unclassifiable income class); these rows "
                "will be shielded from the spatial tilt (income stays NaN, excluded "
                "from per-Kreis re-normalization).",
                n_nan, len(persons), 100.0 * n_nan / max(len(persons), 1),
            )

        # Load the tilt-specific cell columns (rent + eigentümerquote) from the
        # 100 m parquet. These are NOT part of the PopulationSim control columns
        # and are NOT loaded by load_control_cells, so we read them separately.
        # The column names are the cleaned versions of the raw parquet names:
        #   raw: "durchschnMieteQM_Durchschn_Nettokaltmiete_100m-Gitter"
        #     -> clean: "durchschnMieteQM_Durchschn_Nettokaltmiete_100m_Gitter"
        #   raw: "Eigentuemerquote_Eigentuemerquote_100m-Gitter"
        #     -> clean: "Eigentuemerquote_Eigentuemerquote_100m_Gitter"
        #   HH weight column (always present in cells from load_control_cells):
        #     "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
        _TILT_RENT_COL = "durchschnMieteQM_Durchschn_Nettokaltmiete_100m_Gitter"
        _TILT_QUOTE_COL = "Eigentuemerquote_Eigentuemerquote_100m_Gitter"
        # Suppression-ADJUSTED household totals are the correct weight: the raw cell
        # totals suppress small cells (NaN), making them 0-weight and biasing the
        # Kreis-mean normalization toward large dense cells only. The _adj column
        # fills suppressed cells with the cleancensus imputed estimates so every cell
        # carries a proper (non-zero) weight in the index normalization.
        _TILT_HH_COL = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
        _TILT_ARS_COL = "RegionalSchlussel_ARS"

        # Load only the columns needed for the tilt from the raw parquet.
        # We cannot assume they are present in the already-loaded cells frame,
        # so we do a targeted partial-column read here. The cells frame itself
        # is ZGB-filtered; we replicate that by filtering on ZENSUS100m membership.
        import pyarrow.parquet as _pq

        _raw_schema_names = _pq.ParquetFile(cells_path).schema.names
        _clean_to_raw: dict[str, str] = {}
        for _rn in _raw_schema_names:
            _clean_to_raw.setdefault(prepared_cells.clean_col_name(_rn), _rn)

        _tilt_cols_needed = [
            _TILT_RENT_COL, _TILT_QUOTE_COL, _TILT_HH_COL, _TILT_ARS_COL,
        ]
        _id_raw = _raw_schema_names[0]
        _raw_tilt = [_id_raw]
        for _clean in _tilt_cols_needed:
            _r = _clean_to_raw.get(_clean)
            if _r is not None and _r not in _raw_tilt:
                _raw_tilt.append(_r)

        _tilt_cells_raw = pd.read_parquet(cells_path, columns=_raw_tilt)
        _tilt_cells_raw.columns = [
            prepared_cells.clean_col_name(c) for c in _tilt_cells_raw.columns
        ]
        _tilt_cells_raw = _tilt_cells_raw.rename(
            columns={prepared_cells.clean_col_name(_id_raw): "ZENSUS100m"}
        )
        # Filter to ZGB cells (same membership as the cells frame used by PopulationSim).
        _zgb_cell_ids = set(cells["ZENSUS100m"].tolist())
        _zgb_cells_mask = _tilt_cells_raw["ZENSUS100m"].isin(_zgb_cell_ids)
        _tilt_cells = _tilt_cells_raw[_zgb_cells_mask].copy()

        # Derive 5-digit Kreis ARS from the 12-digit ARS column.
        if _TILT_ARS_COL in _tilt_cells.columns:
            _tilt_cells["_ars5"] = _tilt_cells[_TILT_ARS_COL].astype(str).str[:5]
        else:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: ARS column %r absent from "
                "tilt cells; cannot derive Kreis code. Skipping tilt.",
                _TILT_ARS_COL,
            )
            income_tilt_enabled = False

    if income_tilt_enabled:
        _hh_weight_col = _TILT_HH_COL if _TILT_HH_COL in _tilt_cells.columns else None
        if _hh_weight_col is None:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: HH weight column %r absent "
                "from tilt cells; using uniform weight (n_households=1).",
                _TILT_HH_COL,
            )
            _tilt_cells = _tilt_cells.copy()
            _tilt_cells["_hh_weight"] = 1.0
            _hh_weight_col = "_hh_weight"

        # Build a working frame with all needed columns renamed for the index builders.
        _work = _tilt_cells.rename(columns={"_ars5": "ars5"}).copy()

        # Build the renter rent index from the per-cell net cold rent column.
        if _TILT_RENT_COL in _work.columns:
            _work = _ist.build_renter_rent_index(
                _work,
                rent_col=_TILT_RENT_COL,
                kreis_col="ars5",
                weight_col=_hh_weight_col,
                beta=income_tilt_beta,
            )
        else:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: rent column %r absent "
                "from tilt cells; renter_income_index set to 1.0 (neutral).",
                _TILT_RENT_COL,
            )
            _work["renter_income_index"] = 1.0

        # Build the owner income index from the per-cell Eigentümerquote column.
        if _TILT_QUOTE_COL in _work.columns:
            _work = _ist.build_owner_income_index(
                _work,
                quote_col=_TILT_QUOTE_COL,
                kreis_col="ars5",
                weight_col=_hh_weight_col,
                beta=income_tilt_beta,
            )
        else:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: eigentuemerquote column %r absent "
                "from tilt cells; owner_income_index set to 1.0 (neutral).",
                _TILT_QUOTE_COL,
            )
            _work["owner_income_index"] = 1.0

        # The cell_index needs only: ZENSUS100m, ars5, renter_income_index, owner_income_index.
        _cell_index = _work[["ZENSUS100m", "ars5", "renter_income_index", "owner_income_index"]]

        # Determine tenure and cell columns on the persons frame.
        # ZENSUS100m: always present (joined by join_cell_attributes via expand).
        # housing_tenure: present for MiD (map_housing_tenure runs on H_MIETE);
        #   absent on ENTD path (map_housing_tenure skips when H_MIETE absent).
        _PERSONS_CELL_COL = "ZENSUS100m"
        _PERSONS_KREIS_COL = "departement_id"
        _PERSONS_TENURE_COL = "housing_tenure"

        if _PERSONS_CELL_COL not in persons.columns:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: '%s' absent from persons "
                "frame; cannot apply spatial tilt. Skipping.",
                _PERSONS_CELL_COL,
            )
        elif _PERSONS_KREIS_COL not in persons.columns:
            logger.warning(
                "[popsim.stage] income_spatial_tilt: '%s' absent from persons "
                "frame; cannot apply spatial tilt. Skipping.",
                _PERSONS_KREIS_COL,
            )
        elif _PERSONS_TENURE_COL not in persons.columns:
            logger.info(
                "[popsim.stage] income_spatial_tilt: '%s' absent from persons "
                "frame (ENTD path?); skipping spatial tilt (no tenure signal).",
                _PERSONS_TENURE_COL,
            )
        else:
            persons, _tilt_diag = _ist.maybe_apply_income_tilt(
                persons, _cell_index,
                enabled=True,
                cell_col=_PERSONS_CELL_COL,
                kreis_col=_PERSONS_KREIS_COL,
                tenure_col=_PERSONS_TENURE_COL,
                income_col="household_income_eur",
                clip=income_tilt_clip,
                unknown_neutral=True,
            )
            # Update high_income from the tilted income values using the unified rule.
            persons["high_income"] = (
                persons["household_income_eur"].fillna(0.0) >= HIGH_INCOME_THRESHOLD_EUR
            ).astype(bool)
            logger.info(
                "[popsim.stage] income_spatial_tilt applied (beta=%.2f, clip=%.2f); "
                "high_income re-derived from tilted income. "
                "max_effective_dev=%.4f, kreis_mean_preserved=%s.",
                income_tilt_beta, income_tilt_clip,
                _tilt_diag.get("max_effective_dev", float("nan")),
                _tilt_diag.get("kreis_mean_preserved", "n/a"),
            )
            # Attach the tilt diagnostics to the persons frame so they survive pickling
            # in the synpp cache and can be read back by the gate harness without
            # re-running the tilt.  pandas DataFrame.attrs is preserved through pickle.
            # Only scalars and bools are stored (no numpy types) for JSON-safety.
            persons.attrs["income_tilt_diag"] = {
                k: (bool(v) if isinstance(v, (bool, np.bool_)) else float(v))
                for k, v in _tilt_diag.items()
                if v is not None
            }

    # Write the local-only pseudonym map for MiD so internal re-linking is possible.
    # This file maps each surrogate source_person_id / source_household_id back
    # to the raw MiD H_ID / P_ID.  It MUST NOT be committed or published; it
    # lives in the pipeline work_dir which is a local-only, gitignored path.
    # For ENTD (pseudonymise=False) the map is empty (no surrogates were assigned)
    # but is still written for consistency.
    pseudonym_map_path = Path(work_dir) / "pseudonym_map.csv"
    pseudonym_map.to_csv(pseudonym_map_path, index=False)
    logger.info(
        "[popsim.stage] Pseudonym map written to %s (%d unique donor persons; "
        "pseudonymise=%s).",
        pseudonym_map_path, len(pseudonym_map), pseudonymise,
    )

    return persons
