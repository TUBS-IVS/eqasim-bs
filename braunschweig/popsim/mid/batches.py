"""Batch folder assembly and the PopulationSim runner for the popsim mid stage.

- ``summarize_integerizer_feasibility`` -- aggregate PopulationSim LP-integerizer
                                           feasibility across a run's batch logs
- ``_run_batches_and_merge``            -- run every batch, merge, and fail loudly
                                           on any missing batch output
- ``assemble_batch_folder``             -- write one PopulationSim run folder
                                           (with optional Tier-3 KREIS controls)
- ``cell_groups``                       -- map each 1 km parent to its 100 m children
- ``run_popsim_mid``                    -- batch the cells, run PopulationSim, merge,
                                           and (optionally) stratify by RegioStaR (4B)

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context. This is the last extraction of the #267
split: after this module, ``__init__.py`` holds only the package docstring,
imports, the ``MID_SEED_COLUMNS`` alias, the ``from . import ...`` line, and
the re-export blocks -- a pure facade with no logic of its own.

``MAX_MISSING_BATCH_RATE`` and ``INTEGERIZER_INFEASIBLE_WARN_RATE`` move here
alongside their sole consumers (``_run_batches_and_merge`` and
``summarize_integerizer_feasibility`` respectively).

Because ``__init__.py`` no longer uses them directly once this module's
functions are extracted, several parent-package import aliases and typing
names move here too (their only remaining consumers were the functions moved
in this task): ``batch``, ``control_spec``, ``folders``, ``mergemod``, ``pd``,
``Path``, ``Iterable``, ``Mapping``, ``Sequence``, ``Union``, ``logging`` and
``logger``. They are re-exported from ``__init__.py`` (facade block below) so
the public namespace is unchanged.

Sibling imports go directly to the sibling submodule, never through the
package ``__init__`` (#267 split constraint): ``build_control_totals`` and
``_ARS_COLUMN`` from ``control_cells.py`` (task 3); ``_batch_kreis_apportion_weights``
and ``_kreis_pop_from_crosswalk`` from ``kreis_controls.py`` (task 7).

``run_popsim_mid`` (below) has a local loop variable named ``stratum``
(``for stratum, km_ids in sorted(...)``). Importing the sibling module object
as ``from . import stratum`` would let that loop variable shadow the module
inside this file, so the RegioStaR stratification helpers are imported BY NAME
ONLY: ``from .stratum import dominant_stratum_for_1km, filter_seed_to_stratum``.
The loop variable itself is kept unchanged (verbatim-move constraint); only the
import style is adjusted to avoid the collision.
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

from .control_cells import _ARS_COLUMN, build_control_totals
from .kreis_controls import _batch_kreis_apportion_weights, _kreis_pop_from_crosswalk
from .stratum import dominant_stratum_for_1km, filter_seed_to_stratum

logger = logging.getLogger(__name__)


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
