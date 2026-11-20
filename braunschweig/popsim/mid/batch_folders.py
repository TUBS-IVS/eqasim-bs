"""Batch folder assembly and the PopulationSim runner for the popsim mid stage.

Named ``batch_folders`` (not ``batches``) to stay distinct from the sibling
top-level ``braunschweig.popsim.batch`` module: that parent module does the
1 km-atomic bin-packing of 100 m cells into batches, while this module builds
each batch's PopulationSim run-FOLDER contents and invokes that parent
module's batch runner. The two names were one character apart (``batch`` vs
``batches``) before this module was renamed during the #267 final review --
the same ambiguity class as the ``stratum``/``donor_stratification`` rename
below.

- ``summarize_integerizer_feasibility`` -- aggregate PopulationSim LP-integerizer
                                           feasibility across a run's batch logs
- ``_run_batches_and_merge``            -- run every batch, merge, and fail loudly
                                           on any missing batch output
- ``_reconcile_run_batch_folders``      -- discard batch output whose inputs changed
                                           and remove batch folders not in this run
- ``assemble_batch_folder``             -- write one PopulationSim run folder
                                           (with optional Tier-3 KREIS controls)
- ``cell_groups``                       -- map each 1 km parent to its 100 m children
- ``run_popsim_mid``                    -- batch the cells, run PopulationSim, merge,
                                           and (optionally) stratify by RegioStaR (4B)
- ``MAX_MISSING_BATCH_RATE``            -- tolerated missing-batch rate for
                                           ``_run_batches_and_merge`` (0 by default)
- ``INTEGERIZER_INFEASIBLE_WARN_RATE``  -- LP-integerizer infeasibility rate above
                                           which ``summarize_integerizer_feasibility`` warns

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context. This is the last extraction of the
``mid`` package split: after this module, ``__init__.py`` holds only the
package docstring, imports, the ``MID_SEED_COLUMNS`` alias, the
``from . import ...`` line, and the re-export blocks -- a pure facade with no
logic of its own.

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
``_ARS_COLUMN`` from ``control_cells.py``; ``_batch_kreis_apportion_weights``
and ``_kreis_pop_from_crosswalk`` from ``kreis_controls.py``.

``run_popsim_mid`` (below) has a local loop variable named ``stratum``
(``for stratum, km_ids in sorted(...)``). The sibling module holding the
RegioStaR stratification helpers is named ``donor_stratification`` (renamed
from ``stratum`` in issue #267 to end a filename collision with the
pre-existing ``braunschweig.popsim.stratum`` module), so importing it as
``from . import donor_stratification`` no longer collides with the loop
variable; the helpers are still imported BY NAME
(``from .donor_stratification import dominant_stratum_for_1km,
filter_seed_to_stratum``) for readability at the call site, matching the style
of the other sibling imports above. The loop variable itself is kept
unchanged (verbatim-move constraint).
"""

from __future__ import annotations

import logging
import shutil
from collections import Counter
from pathlib import Path
from typing import Iterable, Mapping, NamedTuple, Sequence, Union

import pandas as pd

from braunschweig.popsim import batch
from braunschweig.popsim import control_spec
from braunschweig.popsim import employment_level
from braunschweig.popsim import folders
from braunschweig.popsim import merge as mergemod
from braunschweig.popsim.input_consistency_guard import ConsistencyRecorder

from braunschweig.popsim.fallback_rates import log_fallback_rate
from braunschweig.popsim.kreis_attribute_control import WORK_BY_EMPLOYMENT_MIN_AGE_YEARS

from .control_cells import _ARS_COLUMN, build_control_totals
from .kreis_controls import _batch_kreis_apportion_weights, _kreis_pop_from_crosswalk
from .donor_stratification import dominant_stratum_for_1km, filter_seed_to_stratum

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
    # Start the largest batches first (shorter tail; the order cannot change a result).
    batch.run_batches(batch.order_largest_first(batch_folders), run_one, num_workers=num_workers)
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


def _reconcile_run_batch_folders(
    work_dir: Path, batch_inputs: Mapping[str, Mapping[str, Path]]
) -> None:
    """Leave in ``work_dir`` only batch output built from this run's batch inputs.

    The work dir persists across runs outside the synpp stage cache, and the batch
    runner skips a folder whose completion marker exists. Called once per run after
    ALL batch folders were assembled (so an assembly error leaves every record and
    every output untouched) and before ``_run_batches_and_merge``:

    1. ``batch.reconcile_batch_outputs`` for every folder of this run
       (``batch_inputs`` maps the folder to the file mapping
       ``assemble_batch_folder`` returned), so output built from other inputs is
       discarded, each discard logged by name as a WARNING before it happens;
    2. every ``batch_*`` directory directly under ``work_dir`` that is not a folder
       of this run (left by an earlier, larger partition) is removed, each logged by
       name as a WARNING before it is removed: its output is not merged, but
       ``summarize_integerizer_feasibility`` would still read its log;
    3. one log line with the four reconcile counts and the removed-folder count,
       at WARNING when any output was discarded or any folder removed.

    Logging each deletion before it happens keeps every deletion on record even
    when a removal fails midway (for example a file still open on Windows) and the
    summary line is never reached.

    With no batch folder at all it does nothing, so ``_run_batches_and_merge``
    raises its "batch run incomplete" error before anything is deleted.
    """
    if not batch_inputs:
        return
    counts = Counter(
        batch.reconcile_batch_outputs(folder, input_paths)
        for folder, input_paths in batch_inputs.items()
    )
    run_folders = {Path(folder) for folder in batch_inputs}
    n_removed = 0
    for path in sorted(Path(work_dir).glob("batch_*")):
        if path.is_dir() and path not in run_folders:
            logger.warning(
                "[popsim.mid] removing batch folder %s: it is not part of this run "
                "(left by an earlier, larger partition).", path,
            )
            shutil.rmtree(path)
            n_removed += 1
    n_discarded = counts[batch.INPUTS_CHANGED] + counts[batch.INPUTS_UNRECORDED]
    log = logger.warning if (n_discarded or n_removed) else logger.info
    log(
        "[popsim.mid] batch output reuse by input fingerprint over %d batch(es): "
        "%d unchanged (output kept), %d changed and %d unrecorded (output discarded, "
        "batch re-runs), %d new; removed %d batch folder(s) not part of this run "
        "under %s.",
        len(batch_inputs), counts[batch.INPUTS_UNCHANGED], counts[batch.INPUTS_CHANGED],
        counts[batch.INPUTS_UNRECORDED], counts[batch.INPUTS_NEW], n_removed, work_dir,
    )


# --------------------------------------------------------------------------- #
# Folder assembly + orchestration
# --------------------------------------------------------------------------- #


class UniverseApportionment(NamedTuple):
    """Result of :func:`universe_apportion_weights` (counts are over (weight column, Kreis) parts)."""

    weights: dict
    n_parts: int
    n_empty_part: int
    n_empty_region: int


def universe_apportion_weights(
    cells_subset: pd.DataFrame,
    geo_crosswalk: pd.DataFrame,
    kreis_totals_by_weight_col: Mapping[str, Mapping[str, float]],
    weight_cols: Iterable[str],
) -> UniverseApportionment:
    """This batch's share of each Kreis per age-universe weight column (#658).

    ``weights`` is ``{weight column: {kreis: share}}`` with the shares of
    :func:`_batch_kreis_apportion_weights` (``kreis_totals_by_weight_col[col]`` holds the region
    totals of the column, computed once by the caller with :func:`_kreis_pop_from_crosswalk`).
    Two empty-universe cases get share 0 (control 0, never NaN/inf) and are counted separately:

    * ``n_empty_part``: the batch's part of the Kreis has no person of the universe although the
      Kreis has some elsewhere (a legitimate zero; its rate is logged and warns above the
      fallback threshold, because a mis-built universe column empties every part);
    * ``n_empty_region``: the Kreis has no person of the universe at all, so its whole target
      vanishes in every batch. The per-batch line is DEBUG; :func:`log_empty_region_universes` warns
      once per run with the dropped target mass.

    Direct callers of :func:`assemble_batch_folder` get only the DEBUG line: the once-per-run WARNING
    and the counts of the stage report come from :func:`run_popsim_mid`.
    """
    weights: dict[str, dict[str, float]] = {}
    n_parts = n_empty_part = n_empty_region = 0
    for col in sorted(set(weight_cols)):
        if col not in kreis_totals_by_weight_col:
            raise ValueError(
                f"No region totals for the universe weight column {col!r}; pass "
                "kreis_totals_by_weight_col for every column of apportion_weight_col_by_control.")
        region = kreis_totals_by_weight_col[col]
        weights[col] = _batch_kreis_apportion_weights(
            cells_subset, geo_crosswalk, region, weight_col=col)
        empty_region = sorted(k for k in weights[col] if not float(region.get(k, 0.0)) > 0)
        n_parts += len(weights[col])
        n_empty_region += len(empty_region)
        n_empty_part += sum(1 for k, share in weights[col].items()
                            if share == 0.0 and k not in empty_region)
        if empty_region:
            # DEBUG per batch: log_empty_region_universes warns once per run with the dropped mass.
            logger.debug(
                "[popsim.kreis_universe_apportion] %s: %d Kreis part(s) in this batch with a "
                "region total of 0 persons of the universe (Kreis %s): control value 0",
                col, len(empty_region), empty_region)
    log_fallback_rate(
        logger, "popsim.kreis_universe_apportion.empty_part", n_empty_part, n_parts,
        what="Kreis parts with no person of the control's age universe get the control value 0")
    return UniverseApportionment(weights, n_parts, n_empty_part, n_empty_region)


def log_empty_region_universes(
    kreis_totals_by_weight_col: Mapping[str, Mapping[str, float]],
    apportion_weight_col_by_control: Mapping[str, str],
    kreis_table: pd.DataFrame,
    kreis_controls_map: Mapping[str, Sequence[str]],
    *,
    ars_col: str = "ARS_kreis",
) -> dict[str, dict[str, float]]:
    """One WARNING per universe column whose Kreis have a region total of 0 (#658).

    Such a Kreis gets share 0 in every batch, so the targets of all controls on that universe
    vanish. Returns ``{weight column: {kreis: dropped target mass}}`` (persons, the row-sum of the
    census source columns of the controls on the column, NaN as 0); empty when nothing is dropped.
    """
    table = kreis_table.assign(**{ars_col: kreis_table[ars_col].astype(str)}).set_index(ars_col)
    dropped: dict[str, dict[str, float]] = {}
    for col, region in sorted(kreis_totals_by_weight_col.items()):
        controls = sorted(c for c, w in apportion_weight_col_by_control.items() if w == col)
        empty = sorted(k for k, total in region.items() if not float(total) > 0)
        if not empty:
            continue
        sources = [s for c in controls for s in kreis_controls_map.get(c, ())]
        mass = {k: (float(table.loc[k, sources].fillna(0.0).sum())
                    if k in table.index and sources else 0.0) for k in empty}
        dropped[col] = mass
        logger.warning(
            "[popsim.kreis_universe_apportion] %s: region total of 0 persons of the universe in "
            "Kreis %s; their targets (%.1f in total, %d control(s)) get share 0 in every batch "
            "and are dropped", col, empty, sum(mass.values()), len(controls))
    return dropped


#: Accepted values of ``run_popsim_mid(employment_level_source=...)`` (config key ``employment_level_source``).
EMPLOYMENT_LEVEL_SOURCES = ("survey", "zensus")


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
    apportion_weight_col_by_control: Mapping[str, str] | None = None,
    kreis_totals_by_weight_col: Mapping[str, Mapping[str, float]] | None = None,
    kreis_follow_grids: bool = False,
    employed_bridge: float | None = None,
    consistency_recorder: ConsistencyRecorder | None = None,
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

    Own-universe apportionment (#658): ``apportion_weight_col_by_control`` maps a KREIS control
    name to a cell column holding the persons of its age universe
    (``controls_builder.apportion_weight_columns``); ``kreis_totals_by_weight_col`` holds the
    region-wide per-Kreis total of each such column. A control in the map is then split by this
    batch's share of ITS universe (:func:`universe_apportion_weights`) instead of the population
    share. Both ``None`` (default) -> the legacy split, unchanged.

    Follow the grids (#658, Task A4): with ``kreis_follow_grids`` the KREIS car / bicycle ownership
    and employed controls of this batch are set to the batch sum of their finer-level control
    (:func:`folders.set_kreis_controls_from_fine_sums`), so the KREIS target and the grid targets
    of one variable agree exactly. A pair needs both sides in ``controls_df``; a KREIS control
    whose grid is off keeps its apportioned value and is logged. Default ``False`` -> unchanged.

    One employment level (#658, Task A5, D-emp): with ``employed_bridge`` (the share of the persons
    employed by ``P_TAET`` that are in a survey employed class,
    :func:`employment_level.employed_definition_bridge`) the employed classes of the KREIS families
    ``work_by_employment`` / ``employment_status`` are rescaled, after the step above, to
    ``employed_bridge`` x the batch sum of the employed grid per Kreis part
    (:func:`employment_level.reconcile_kreis_employment_levels`); their row totals are unchanged.
    Needs the Tier-3 KREIS geography and the employed grid (``ValueError`` otherwise). Default
    ``None`` -> unchanged.

    Input consistency (#658, Task A6): with a ``consistency_recorder`` (one per run, created by the
    stage) the batch's in-memory control tables are summarized after the folder is written, appended
    to the recorder's CSV and judged by its guard (``ValueError`` naming batch, Kreis and pair),
    and the counts of the three steps above are added to its report. The folder name is the batch
    name. Needs the Tier-3 KREIS geography (there is no KREIS table to compare without it).
    Default ``None`` -> nothing is measured.
    """
    if consistency_recorder is not None and not (kreis_table is not None and bool(kreis_controls_map)):
        raise ValueError(
            "consistency_recorder needs the Tier-3 KREIS geography (kreis_table and "
            "kreis_controls_map); there is no KREIS table to compare without it.")
    if employed_bridge is not None and not (kreis_table is not None and bool(kreis_controls_map)):
        raise ValueError(
            "employed_bridge needs the Tier-3 KREIS geography (kreis_table and "
            "kreis_controls_map); there is no KREIS employment control to reconcile without it.")
    if kreis_follow_grids and not (kreis_table is not None and bool(kreis_controls_map)):
        raise ValueError(
            "kreis_follow_grids needs the Tier-3 KREIS geography (kreis_table and "
            "kreis_controls_map); there is no KREIS control to set without it.")
    if apportion_weight_col_by_control and kreis_totals_by_weight_col is None:
        raise ValueError(
            "apportion_weight_col_by_control needs kreis_totals_by_weight_col (the region "
            "totals of each weight column).")
    tier3 = kreis_table is not None and bool(kreis_controls_map)
    if apportion_weight_col_by_control and not tier3:
        raise ValueError(
            "apportion_weight_col_by_control needs the Tier-3 KREIS geography (kreis_table and "
            "kreis_controls_map); there is no KREIS control to apportion without it.")
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
        weights_by_control = None
        if apportion_weight_col_by_control:
            apportioned = universe_apportion_weights(
                cells_subset, geo_crosswalk, kreis_totals_by_weight_col,
                apportion_weight_col_by_control.values())
            by_col = apportioned.weights
            weights_by_control = {
                control: by_col[col] for control, col in apportion_weight_col_by_control.items()}
            if consistency_recorder is not None:
                consistency_recorder.add(
                    "universe_apportionment", kreis_parts=apportioned.n_parts,
                    empty_parts=apportioned.n_empty_part, empty_regions=apportioned.n_empty_region)
        control_totals[folders.GEO_KREIS] = folders.build_kreis_control_totals(
            kreis_table, geo_crosswalk, controls_map=kreis_controls_map,
            apportion_weights=apportion_weights,
            household_apportion_weights=household_apportion_weights,
            household_control_names=household_control_names,
            apportion_weights_by_control=weights_by_control,
        )
        if kreis_follow_grids:
            control_totals[folders.GEO_KREIS] = _follow_grids(
                control_totals, geo_crosswalk, controls_df, consistency_recorder)
        if employed_bridge is not None:
            level_stats: dict = {}
            control_totals[folders.GEO_KREIS] = employment_level.reconcile_kreis_employment_levels(
                control_totals[folders.GEO_KREIS], control_totals, geo_crosswalk,
                bridge=employed_bridge, stats=level_stats)
            if consistency_recorder is not None:
                consistency_recorder.add("employment_level", **level_stats)
    written = folders.write_popsim_folder(
        folder,
        geo_crosswalk=geo_crosswalk,
        control_totals=control_totals,
        controls_csv=controls_df,
        seed_households=seed_households,
        seed_persons=seed_persons,
        settings_yaml=settings_yaml,
        logging_yaml=logging_yaml,
    )
    if consistency_recorder is not None:
        consistency_recorder.record_batch(Path(folder).name, control_totals, geo_crosswalk)
    return written


def _follow_grids(
    control_totals: Mapping[str, pd.DataFrame],
    geo_crosswalk: pd.DataFrame,
    controls_df: pd.DataFrame,
    consistency_recorder: ConsistencyRecorder | None = None,
) -> pd.DataFrame:
    """The KREIS table of a batch with its grid-following controls set to the grid sums (#658, A4).

    The "keep their apportioned value" INFO is logged once per run when a recorder is given (the
    answer is the same in every batch), else per call; the counts go to the recorder.
    """
    mapping = control_spec.kreis_controls_following_grids(controls_df)
    fields = set(controls_df["control_field"])
    unpaired = sorted(k for k in control_spec.kreis_grid_pairs() if k in fields and k not in mapping)
    if unpaired and (consistency_recorder is None
                     or consistency_recorder.first_time("kreis_follow_grids.unpaired")):
        logger.info(
            "[popsim.kreis_follow_grids] %d KREIS control(s) keep their apportioned value, their "
            "grid control is not active in this run: %s", len(unpaired), unpaired)
    stats: dict = {}
    out = folders.set_kreis_controls_from_fine_sums(
        control_totals[folders.GEO_KREIS], control_totals, geo_crosswalk, mapping, stats=stats)
    if consistency_recorder is not None:
        section = "kreis_follow_grids"
        consistency_recorder.add(
            section, controls_changed_over_batches=stats["n_changed"],
            controls_set_over_batches=stats["n_controls"], kreis_parts=stats["n_kreis_parts"])
        previous = consistency_recorder.sections[section].get("largest_relative_change", -1.0)
        if stats["largest_relative_change"] > previous:
            consistency_recorder.set(
                section, largest_relative_change=stats["largest_relative_change"],
                largest_relative_change_control=stats["largest_relative_change_control"])
        consistency_recorder.set(section, controls_without_grid=unpaired)
    return out


def cell_groups(cells_subset: pd.DataFrame) -> dict[str, list[str]]:
    """Map each 1 km parent to its 100 m children (for batching)."""
    return {
        str(parent): group["ZENSUS100m"].astype(str).tolist()
        for parent, group in cells_subset.groupby("ZENSUS1km", sort=True)
    }


def _batch_settings_text(settings_yaml: str, seed_base: int | None, folder: Path,
                         seeds: dict[str, int]) -> str:
    """Settings text of one batch folder: with its own integerizer seed when a base is given.

    Records the seed under the folder name in ``seeds`` (read by
    :func:`_log_integerizer_seeds`). ``seed_base=None`` returns the text unchanged.
    """
    if seed_base is None:
        return settings_yaml
    seed = folders.batch_integerizer_seed(seed_base, folder.name)
    seeds[folder.name] = seed
    return folders.with_integerizer_seed(settings_yaml, seed)


def _log_integerizer_seeds(seeds: Mapping[str, int], n_batches: int) -> None:
    """Log how many distinct per-batch integerizer seeds were written; raise on a clash.

    Two batches with the same seed would draw the same integerizer noise (reason:
    ADR-0199, decision 3). With 32-bit seeds that is a rare accident, so it is an error
    here, not a warning: change ``random_seed`` and the clash is gone.
    """
    if not seeds:
        return
    distinct = len(set(seeds.values()))
    logger.info(
        "[popsim.mid] integerizer seeds: %d distinct for %d batch folder(s), range %d..%d",
        distinct, n_batches, min(seeds.values()), max(seeds.values()))
    if distinct != n_batches:
        by_seed: dict[int, list[str]] = {}
        for name, seed in seeds.items():
            by_seed.setdefault(seed, []).append(name)
        clashes = "; ".join(
            f"seed {seed}: {', '.join(names)}"
            for seed, names in sorted(by_seed.items()) if len(names) > 1)
        raise ValueError(
            f"Only {distinct} distinct integerizer seeds for {n_batches} batch folders; "
            f"batches with one seed would draw identical integerizer noise. Colliding "
            f"batch folders ({clashes}). Change the pipeline random_seed.")


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
    integerizer_seed_base: int | None = None,
    apportion_weight_col_by_control: Mapping[str, str] | None = None,
    kreis_follow_grids: bool = False,
    employment_level_source: str = "survey",
    consistency_recorder: ConsistencyRecorder | None = None,
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
        Directory for batch folders. It persists across runs: existing batch output
        is reused only for the inputs it was built from, and ``batch_*`` folders
        that are not part of this run are removed (``_reconcile_run_batch_folders``).
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
    integerizer_seed_base:
        ``None`` (default): every batch folder gets ``settings_yaml`` unchanged. An int
        (the pipeline ``random_seed``): each batch folder's ``settings.yaml`` gains its own
        ``INTEGERIZER_RANDOM_SEED``, derived from this base and the folder name
        (:func:`braunschweig.popsim.folders.batch_integerizer_seed`; why: ADR-0199,
        decision 3). Required (``ValueError``) when ``settings_yaml`` switches the
        integerizer draw on.
    apportion_weight_col_by_control:
        ``None`` (default): every KREIS person control is split over the batches by the batch's
        population share of the Kreis (legacy). A ``{KREIS control name: cell column}`` map
        (``controls_builder.apportion_weight_columns`` + ``add_universe_weight_columns``): those
        controls are split by the batch's share of the persons of their own age universe
        (#658); the region total of each column is summed once here. Needs the Tier-3 KREIS
        geography (``ValueError`` otherwise).
    kreis_follow_grids:
        ``False`` (default): the KREIS car / bicycle and employed controls keep the value of the
        Kreis census table (apportioned). ``True``: each is set to its batch's sum of the grid
        control of the same variable (:func:`folders.set_kreis_controls_from_fine_sums`, #658);
        needs the Tier-3 KREIS geography (``ValueError`` otherwise).
    employment_level_source:
        ``"survey"`` (default): the employed classes of the KREIS families keep the level of their
        survey margin (legacy). ``"zensus"`` (#658, Task A5, D-emp): the bridge
        (:func:`employment_level.employed_bridge_report`) is computed once from the seed and
        passed to every batch (``employed_bridge``), which rescales those classes to the Zensus
        employment level; needs the Tier-3 KREIS geography and the employed grid. Any other value
        is a ``ValueError``.
    consistency_recorder:
        ``None`` (default): nothing is measured. A :class:`ConsistencyRecorder` (#658, Task A6):
        every batch folder's tables are summarized into its CSV and judged by its guard right
        after the folder is written, i.e. before any PopulationSim process starts (the folders of
        all batches are assembled before the first ``run_one`` call); the bridge and the dropped
        target mass of empty universes are stored in its report. Needs the Tier-3 KREIS geography.
    """
    if employment_level_source not in EMPLOYMENT_LEVEL_SOURCES:
        raise ValueError(f"employment_level_source must be one of {EMPLOYMENT_LEVEL_SOURCES}, "
                         f"got {employment_level_source!r}")
    draw = folders.integerizer_draw_in_settings(settings_yaml)
    if draw != "off" and integerizer_seed_base is None:
        raise ValueError(
            f"The batch settings set {folders.INTEGERIZER_DRAW_SETTING}: {draw!r} but "
            "integerizer_seed_base is None: every batch would draw the same noise "
            "(ADR-0199, decision 3). Pass the pipeline random_seed as integerizer_seed_base.")
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
    employed_bridge: float | None = None
    if employment_level_source == "zensus":
        if not tier3:
            raise ValueError(
                "employment_level_source='zensus' needs the Tier-3 KREIS geography (kreis_table and "
                "kreis_controls_map); there is no KREIS employment control without it.")
        # The seed is the same for every batch (the stratified path only filters donors), so the
        # bridge is a property of the whole seed and is computed once.
        bridge_report = employment_level.employed_bridge_report(
            seed_persons, seed_households, grid_min_age=employment_level.employed_grid_min_age(),
            family_min_age=WORK_BY_EMPLOYMENT_MIN_AGE_YEARS)
        employed_bridge = bridge_report.bridge
        if consistency_recorder is not None:
            consistency_recorder.set(
                "employment_level", bridge=bridge_report.bridge,
                bridge_family_universe=bridge_report.bridge_family_universe,
                below_grid_share=bridge_report.below_grid_share, bridge_source=bridge_report.source)
    kreis_total_pop: dict[str, float] | None = None
    kreis_total_hh: dict[str, float] | None = None
    kreis_totals_by_weight_col: dict[str, dict[str, float]] | None = None
    if apportion_weight_col_by_control and not tier3:
        raise ValueError(
            "apportion_weight_col_by_control needs the Tier-3 KREIS geography (kreis_table and "
            "kreis_controls_map); there is no KREIS control to apportion without it.")
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
        if apportion_weight_col_by_control:
            absent = sorted({c for c in apportion_weight_col_by_control.values()
                             if c not in cells.columns})
            if absent:
                raise ValueError(
                    f"Universe weight column(s) {absent} are absent from the cells frame; add "
                    "them with controls_builder.add_universe_weight_columns (no silent fall-back "
                    "to the population share).")
            kreis_totals_by_weight_col = {
                col: _kreis_pop_from_crosswalk(cells, full_xwalk, weight_col=col)
                for col in sorted(set(apportion_weight_col_by_control.values()))}
            dropped = log_empty_region_universes(
                kreis_totals_by_weight_col, apportion_weight_col_by_control, kreis_table,
                kreis_controls_map)
            if consistency_recorder is not None:
                consistency_recorder.set(
                    "universe_apportionment",
                    dropped_target_mass_by_universe_column={
                        col: {"total": sum(mass.values()), "by_kreis": mass}
                        for col, mass in dropped.items()})
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
        batch_inputs: dict[str, dict[str, Path]] = {}
        batch_seeds: dict[str, int] = {}
        for index, km_cells in enumerate(partitions):
            subset = cells[cells["ZENSUS1km"].isin(km_cells)].copy()
            folder = work_dir / f"batch_{index:03d}"
            batch_inputs[str(folder)] = assemble_batch_folder(
                folder, subset, base_cols, controls_df,
                seed_households, seed_persons,
                settings_yaml=_batch_settings_text(
                    settings_yaml, integerizer_seed_base, folder, batch_seeds),
                logging_yaml=logging_yaml,
                kreis_table=kreis_table, kreis_controls_map=kreis_controls_map,
                kreis_total_pop=kreis_total_pop,
                kreis_total_hh=kreis_total_hh,
                household_control_names=household_control_names,
                apportion_weight_col_by_control=apportion_weight_col_by_control,
                kreis_totals_by_weight_col=kreis_totals_by_weight_col,
                kreis_follow_grids=kreis_follow_grids,
                employed_bridge=employed_bridge,
                consistency_recorder=consistency_recorder,
            )
            batch_folders.append(str(folder))

        _log_integerizer_seeds(batch_seeds, len(batch_folders))
        _reconcile_run_batch_folders(work_dir, batch_inputs)
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
    batch_inputs_stratified: dict[str, dict[str, Path]] = {}
    batch_seeds_stratified: dict[str, int] = {}
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
            batch_inputs_stratified[str(folder)] = assemble_batch_folder(
                folder, subset, base_cols, controls_df,
                hh_stratum, p_stratum,
                settings_yaml=_batch_settings_text(
                    settings_yaml, integerizer_seed_base, folder, batch_seeds_stratified),
                logging_yaml=logging_yaml,
                kreis_table=kreis_table, kreis_controls_map=kreis_controls_map,
                kreis_total_pop=kreis_total_pop,
                kreis_total_hh=kreis_total_hh,
                household_control_names=household_control_names,
                apportion_weight_col_by_control=apportion_weight_col_by_control,
                kreis_totals_by_weight_col=kreis_totals_by_weight_col,
                kreis_follow_grids=kreis_follow_grids,
                employed_bridge=employed_bridge,
                consistency_recorder=consistency_recorder,
            )
            batch_folders_stratified.append(str(folder))
            global_batch_index += 1

    _log_integerizer_seeds(batch_seeds_stratified, len(batch_folders_stratified))
    _reconcile_run_batch_folders(work_dir, batch_inputs_stratified)
    return _run_batches_and_merge(
        batch_folders_stratified, run_one, num_workers=num_workers
    )
