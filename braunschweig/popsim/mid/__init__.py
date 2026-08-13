"""popsim_mid orchestration: build + run PopulationSim folders from MiD + cells.

Folds the validated end-to-end logic (see ``scripts/popsim_mid_smoke.py``) into
small, focused, reusable functions:

- ``load_mid_seed``         -- the consistent (complete-household) MiD seed
- ``load_completed_donor``  -- attribute donor tables, day-filtered + member-completed
                               (the ONE completion pass feeding seed AND expansion)
- ``assemble_batch_folder`` -- write one PopulationSim run folder
- ``run_popsim_mid``        -- batch over 1 km parents, run, merge, handoff

It reuses the building blocks in ``braunschweig.popsim`` (cells / controls /
folders / seed / batch / merge / handoff) rather than re-implementing them.

Package layout (issue #267 split; formerly one ~1900-line module, itself the
rename of the legacy ``mid.py``): this ``__init__`` is a plain helper facade --
``mid`` is NOT a synpp stage, so unlike the ``enriched`` stage-package split
there is no ``configure``/``execute``/``validate()`` hook here. It re-exports
every extracted submodule name so external imports of
``braunschweig.popsim.mid`` keep working unchanged. No synpp stage currently
hashes this package's source, so the split is cache-neutral by construction;
closing that pre-existing helper-trap gap (a synpp ``validate()`` hashing the
whole package) is module 3's job (``popsim/stage.py``, issue #267). Submodules
extracted so far:

    control_cells  Control-cell loading (targeted parquet columns), ZGB Kreis
                   filtering, and per-geography integerized control totals
                   (``control_base_columns``, ``load_control_cells``,
                   ``filter_zgb_cells``, ``build_control_totals``)
    csv_format     MiD CSV field-separator detection (``detect_csv_separator``);
                   a small leaf module because both ``seed_loading`` and the
                   donor loaders (``donor``) call it
    donor          MiD donor attribute + trip table loading: the donor column
                   lists, the day-filtered + member-completed donor frames (the
                   ONE completion pass feeding seed AND expansion), and the full
                   Wege (trip) table load (``MID_PERSON_ATTR_COLS``,
                   ``MID_PERSON_OPTIONAL_COLS``, ``MID_HOUSEHOLD_ATTR_COLS``,
                   ``MID_WEGE_REQUIRED_COLS``, ``load_mid_attributes``,
                   ``drop_invalid_households``, ``load_completed_donor``,
                   ``load_mid_wege``)
    kreis_controls Tier-3 KREIS control tables (imported cleancensus kreis_*
                   parquets) and per-batch Kreis apportionment
                   (``merge_kreis_control_tables``, ``load_kreis_control_table``,
                   ``resolved_kreis_per_cell``)
    participation  Participation-control seed derivation from the realised
                   weekday plan (``derive_trip_class_seed``,
                   ``compute_has_purpose_trip``, ``compute_has_work_trip``,
                   ``derive_participation_seed``,
                   ``derive_work_participation_seed``)
    seed_loading   The consistent MiD seed load + the completed-donor
                   projection (``load_mid_seed``, ``project_completed_seed``)
    stratum        RegioStaR donor stratification (Phase 4B): dominant stratum
                   per 1 km parent by majority vote, and donor-seed filtering to
                   one stratum (``dominant_stratum_for_1km``,
                   ``filter_seed_to_stratum``)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, Mapping, Sequence, Union

import pandas as pd

from braunschweig.popsim import batch
from braunschweig.popsim import control_spec
from braunschweig.popsim import folders
from braunschweig.popsim import merge as mergemod
from braunschweig.popsim import seed as seedmod

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Package submodules (extracted stage sections). Every name is re-exported
# here so external consumers (pipeline stages, tests) keep importing from the
# braunschweig.popsim.mid module path unchanged.
# ---------------------------------------------------------------------------

from . import control_cells
from .control_cells import (  # noqa: F401  (re-exports)
    SUFFIX_100M,
    SUFFIX_1KM,
    _ARS_COLUMN,
    _EXTRA_CELL_COLUMNS,
    build_control_totals,
    cellmod,
    control_base_columns,
    ctrl,
    filter_zgb_cells,
    load_control_cells,
    pq,
    prepared_cells,
)

from . import csv_format
from .csv_format import (  # noqa: F401  (re-exports)
    detect_csv_separator,
)

from . import donor
from .donor import (  # noqa: F401  (re-exports)
    MID_HOUSEHOLD_ATTR_COLS,
    MID_PERSON_ATTR_COLS,
    MID_PERSON_OPTIONAL_COLS,
    MID_WEGE_REQUIRED_COLS,
    completion,
    drop_invalid_households,
    load_completed_donor,
    load_mid_attributes,
    load_mid_wege,
)

from . import kreis_controls
from .kreis_controls import (  # noqa: F401  (re-exports)
    Optional,
    _KREIS_CONTROL_FILES,
    _batch_kreis_apportion_weights,
    _kreis_pop_from_crosswalk,
    load_kreis_control_table,
    merge_kreis_control_tables,
    resolved_kreis_per_cell,
)

from . import participation
from .participation import (  # noqa: F401  (re-exports)
    PARTICIPATION_W_ZWECK,
    compute_has_purpose_trip,
    compute_has_work_trip,
    derive_participation_seed,
    derive_trip_class_seed,
    derive_work_participation_seed,
    trips,
)

from . import seed_loading
from .seed_loading import (  # noqa: F401  (re-exports)
    KREIS_CONTROL_REGISTRY,
    KreisAttributeControl,
    attributes,
    load_mid_seed,
    project_completed_seed,
)

from . import stratum
from .stratum import (  # noqa: F401  (re-exports)
    dominant_stratum_for_1km,
    filter_seed_to_stratum,
)

# Re-exported for convenience: callers that already import braunschweig.popsim.mid
# can access the canonical MiD seed column mapping without a separate import of
# braunschweig.popsim.seed.  The authoritative definition remains in seed.py.
MID_SEED_COLUMNS = seedmod.MID_SEED_COLUMNS

# A PopulationSim run is only scientifically usable if EVERY batch produced
# output: batches partition the 100 m cells, so one missing batch silently
# removes whole regions from the synthetic population. Decision 2026-07-10
# (after OOM-killed batch workers went unnoticed mid-run): no tolerated miss
# rate -- any missing batch raises instead of returning a partial population
# that would surface much later as an opaque "missing ZENSUS100m" merge error
# (no-silent-fallback policy).
MAX_MISSING_BATCH_RATE = 0.0

# Above this share of zone-integerizations falling back to smart-rounding
# (PopulationSim returning INFEASIBLE), the popsim stage logs a WARNING instead of
# INFO. This is a quality signal, not a hard failure: PopulationSim still produces a
# (smart-rounded) population. A high rate usually means the control set is
# over-constrained for small cells -- common at low sampling rates where a 100 m
# cell holds very few households (no-silent-fallback policy: surface the rate).
INTEGERIZER_INFEASIBLE_WARN_RATE = 0.05


def summarize_integerizer_feasibility(work_dir: Union[str, Path]) -> dict:
    """Aggregate the PopulationSim LP-integerizer feasibility across batch logs.

    PopulationSim integerizes each control zone with an LP; when the control set
    cannot be satisfied integer-simultaneously (typically tiny cells at a low
    sampling rate) it returns INFEASIBLE and falls back INTERNALLY to "smart-rounded
    original weights". That fallback is otherwise invisible to this pipeline -- it
    lives only in the per-batch ``populationsim.log`` -- so this parser surfaces it
    (CLAUDE.md: no silent fallbacks; treat a high rate as a quality signal).

    Parameters
    ----------
    work_dir:
        The popsim working directory containing the ``batch_*/output/populationsim.log``
        files.

    Returns
    -------
    dict
        ``n_logs`` (batch logs scanned), ``n_optimal`` (zones integerized optimally),
        ``n_infeasible`` (zones that fell back to smart-rounding),
        ``n_simul_retry_failed`` (zone-group simultaneous-integerize retries that
        failed), ``n_total`` (optimal + infeasible) and ``infeasible_rate``.
    """
    work_dir = Path(work_dir)
    n_logs = n_optimal = n_infeasible = n_simul_retry_failed = 0
    for log_path in sorted(work_dir.glob("*/output/populationsim.log")):
        n_logs += 1
        text = log_path.read_text(encoding="utf-8", errors="replace")
        # ": OPTIMAL" terminates each "Integerizer status for ...: OPTIMAL" line;
        # "Integerizer failed for ... status INFEASIBLE" is one smart-round fallback.
        n_optimal += text.count(": OPTIMAL")
        n_infeasible += text.count("Integerizer failed for")
        n_simul_retry_failed += text.count("do_simul_integerizing retry failed")
    n_total = n_optimal + n_infeasible
    infeasible_rate = (n_infeasible / n_total) if n_total else 0.0
    return {
        "n_logs": n_logs,
        "n_optimal": n_optimal,
        "n_infeasible": n_infeasible,
        "n_simul_retry_failed": n_simul_retry_failed,
        "n_total": n_total,
        "infeasible_rate": infeasible_rate,
    }


def _run_batches_and_merge(
    batch_folders: Sequence[str], run_one, *, num_workers: int
) -> "mergemod.MergeReport":
    """Run every batch, merge the outputs, and fail loudly on ANY missing batch.

    ``batch.run_batches`` already logs each failed batch's captured PopulationSim
    error. Here the merged report is checked: if any batch produced no output
    (miss rate above ``MAX_MISSING_BATCH_RATE``, which is 0), a ValueError is
    raised naming the miss count. Batches partition the 100 m cells, so a single
    missing batch (e.g. an OOM-killed worker) means whole regions are absent from
    the population -- scientifically unusable, never merely "a recoverable miss".
    """
    batch.run_batches(batch_folders, run_one, num_workers=num_workers)
    report = mergemod.merge_batch_folders(batch_folders)
    n_total = len(batch_folders)
    n_missing = int(getattr(report, "n_missing", 0) or 0)
    # A "missing" batch wrote no output file at all (the broken-PopulationSim /
    # killed-worker signal). This is distinct from a batch that ran but produced
    # an empty table, so the guard keys on the missing count, not on content.
    rate = n_missing / n_total if n_total else 1.0
    if n_total == 0 or rate > MAX_MISSING_BATCH_RATE:
        raise ValueError(
            f"PopulationSim batch run incomplete: {n_missing}/{n_total} batches "
            f"missing their output ({100.0 * rate:.1f}%; ALL batches are required "
            "-- each covers a disjoint set of 100 m cells). Check the per-batch "
            "failure messages logged above (captured PopulationSim stderr / an "
            "OOM-killed worker leaves no output). Re-running the pipeline skips "
            "completed batches and re-runs only the missing ones."
        )
    logger.info(
        "[popsim.mid] merged %d/%d batches (%d missing, %.1f%%).",
        n_total - n_missing, n_total, n_missing, 100.0 * rate,
    )
    return report


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
    kreis_table: pd.DataFrame | None = None,
    kreis_controls_map: Mapping[str, Sequence[str]] | None = None,
    ars_col: str = _ARS_COLUMN,
    kreis_weight_col: str = "POP_TOTAL_100m_adj",
    kreis_total_pop: Mapping[str, float] | None = None,
    kreis_total_hh: Mapping[str, float] | None = None,
    hh_weight_col: str = control_spec.HH_TOTAL_CENSUS_COLUMN,
    household_control_names: Iterable[str] | None = None,
) -> dict[str, Path]:
    """Assemble one PopulationSim run folder for a subset of cells.

    When ``kreis_table`` and ``kreis_controls_map`` are given (Tier-3 active), an
    additional KREIS control geography is built: the geo crosswalk gains a KREIS
    column (resolved to one dominant Kreis per 1 km parent so WELT>STAAT>KREIS>
    1km>100m nests strictly), and ``control_totals_KREIS.csv`` is written from the
    per-Kreis census table. Without them the folder is the tier0-2 baseline
    (byte-identical: no KREIS column, no KREIS control file).

    Per-batch Kreis apportionment
    -----------------------------
    A single Kreis can be split across several batches (RegioStaR stratification cuts
    the region into cell-disjoint strata). If every such batch targeted the FULL Kreis
    marginal, PopulationSim would be over-constrained N-fold and saturate the control
    (the observed 98.5% employment inflation in multi-Kreis runs). ``kreis_total_pop``
    is the total ``kreis_weight_col`` (POP_TOTAL_100m_adj) per resolved dominant Kreis
    over the WHOLE region, computed once by the caller. Given it, this batch's per-Kreis
    population (summed from THIS batch's cells, keyed by the SAME resolved dominant Kreis
    the geo crosswalk uses) is divided by the region-wide total to obtain the batch's
    population share of each Kreis; those shares are passed as ``apportion_weights`` to
    :func:`folders.build_kreis_control_totals`. Because the dominant Kreis is resolved
    per 1 km parent and parents are atomic to a batch, the shares sum to exactly 1 across
    batches, so the apportioned marginals reproduce the full Kreis marginal. When
    ``kreis_total_pop`` is None (single-batch / legacy), no apportionment is applied
    (full marginal).
    """
    tier3 = kreis_table is not None and bool(kreis_controls_map)
    if tier3 and ars_col not in cells_subset.columns:
        raise ValueError(
            f"Tier-3 KREIS controls requested but cells carry no ARS column "
            f"{ars_col!r}; cannot build the KREIS geography."
        )
    geo_crosswalk = folders.build_geo_crosswalk(
        cells_subset,
        id_col_100m="ZENSUS100m",
        parent_col="ZENSUS1km",
        ars_col=(ars_col if tier3 else None),
        resolve_parent_kreis=tier3,
        kreis_weight_col=(kreis_weight_col if tier3 else None),
    )
    targets = cells_subset[["ZENSUS100m", *base_cols]].copy()
    control_totals = build_control_totals(targets, geo_crosswalk, base_cols)
    if tier3:
        apportion_weights = None
        if kreis_total_pop is not None:
            apportion_weights = _batch_kreis_apportion_weights(
                cells_subset, geo_crosswalk, kreis_total_pop,
                weight_col=kreis_weight_col,
            )
        # Household-level KREIS controls are apportioned across batches by the batch's
        # HOUSEHOLD share, not the population share (issue #148): where persons-per-
        # household varies across a Kreis's batches, a pop-share split mis-allocates the
        # household-level targets. Person-level controls keep the pop share above.
        household_apportion_weights = None
        if kreis_total_hh is not None:
            household_apportion_weights = _batch_kreis_apportion_weights(
                cells_subset, geo_crosswalk, kreis_total_hh,
                weight_col=hh_weight_col,
            )
        control_totals[folders.GEO_KREIS] = folders.build_kreis_control_totals(
            kreis_table, geo_crosswalk, controls_map=kreis_controls_map,
            apportion_weights=apportion_weights,
            household_apportion_weights=household_apportion_weights,
            household_control_names=household_control_names,
        )
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
    kreis_table: pd.DataFrame | None = None,
    kreis_controls_map: Mapping[str, Sequence[str]] | None = None,
    household_control_names: Iterable[str] | None = None,
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
    # Resolve to an ABSOLUTE path: PopulationSim is launched with cwd set to the
    # popsimprep repo (not the synpp working directory), so a relative batch
    # folder path (e.g. "eqasim-data/.../batch_000") would resolve against
    # popsimprep and fail with "[WinError 3] path not found". The folders are
    # written and read here, then passed verbatim as "-w <folder>" to the
    # subprocess, so the path must be absolute to be cwd-independent.
    work_dir = Path(work_dir).resolve()
    groups = cell_groups(cells)

    # Tier-3 per-batch KREIS apportionment basis: the region-wide population per
    # RESOLVED dominant Kreis, computed ONCE over ALL cells. A Kreis split across
    # batches must target only each batch's share of its marginal (not the full
    # marginal in every batch), so each batch divides its own per-Kreis population
    # by this region-wide total. Built from a full-region geo crosswalk
    # (resolve_parent_kreis=True) so the Kreis assignment matches each batch's
    # crosswalk exactly (dominant Kreis is per-1km-parent; parents are atomic to a
    # batch, so the batch pops partition this total). None for tier0-2 (no KREIS).
    tier3 = kreis_table is not None and bool(kreis_controls_map)
    kreis_total_pop: dict[str, float] | None = None
    kreis_total_hh: dict[str, float] | None = None
    if tier3:
        if _ARS_COLUMN not in cells.columns:
            raise ValueError(
                f"Tier-3 KREIS controls requested but cells carry no ARS column "
                f"{_ARS_COLUMN!r}; cannot compute the region-wide Kreis population."
            )
        full_xwalk = folders.build_geo_crosswalk(
            cells,
            id_col_100m="ZENSUS100m",
            parent_col="ZENSUS1km",
            ars_col=_ARS_COLUMN,
            resolve_parent_kreis=True,
            kreis_weight_col="POP_TOTAL_100m_adj",
        )
        kreis_total_pop = _kreis_pop_from_crosswalk(cells, full_xwalk)
        # Household-level KREIS controls need the region-wide HOUSEHOLD total per
        # resolved Kreis so each batch can be apportioned by its household share, not
        # its population share (issue #148). Only computed when such controls are
        # active; a missing household-total column with household controls active is a
        # hard error, not a silent fall-back to the population share (CLAUDE.md).
        if household_control_names:
            _hh_col = control_spec.HH_TOTAL_CENSUS_COLUMN
            if _hh_col not in cells.columns:
                raise ValueError(
                    f"Household-level KREIS controls {sorted(household_control_names)} require "
                    f"the household-total column {_hh_col!r} for household-share apportionment "
                    f"(issue #148), but it is absent from the cells frame; refusing to fall back "
                    f"to the population share silently."
                )
            kreis_total_hh = _kreis_pop_from_crosswalk(cells, full_xwalk, weight_col=_hh_col)

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
                kreis_table=kreis_table, kreis_controls_map=kreis_controls_map,
                kreis_total_pop=kreis_total_pop,
                kreis_total_hh=kreis_total_hh,
                household_control_names=household_control_names,
            )
            batch_folders.append(str(folder))

        return _run_batches_and_merge(
            batch_folders, run_one, num_workers=num_workers
        )

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
                kreis_table=kreis_table, kreis_controls_map=kreis_controls_map,
                kreis_total_pop=kreis_total_pop,
                kreis_total_hh=kreis_total_hh,
                household_control_names=household_control_names,
            )
            batch_folders_stratified.append(str(folder))
            global_batch_index += 1

    return _run_batches_and_merge(
        batch_folders_stratified, run_one, num_workers=num_workers
    )
