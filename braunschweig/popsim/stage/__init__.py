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

Package layout (issue #267 split; formerly one ~1900-line module, itself the
rename of the legacy ``stage.py``): this ``__init__`` is the synpp stage
(``configure``/``execute``/``validate``) and re-exports every extracted submodule
name, so external imports of the stage module path keep working unchanged.
Submodules extracted so far:

    config_keys   All ``KEY_*`` config-key constants (all under
                  ``braunschweig.population.popsim.*``) plus the
                  ``_KREIS_CONTROL_TOGGLE_KEY`` / ``_KREIS_CONTROL_DEFAULT``
                  per-attribute KREIS control lookup dicts. A LEAF submodule
                  (imports nothing from this package), so every other
                  submodule can import these names as a plain sibling import.
    source_resolution
                  Donor-source resolution (``_resolve_source``, a thin factory
                  wrapper around ``braunschweig.popsim.sources.get_source``) and
                  KREIS attribute-control activation (``active_kreis_entries``,
                  MiD-only), reading ``_KREIS_CONTROL_TOGGLE_KEY`` from
                  ``config_keys``.
    tilt_columns  Income-tilt cell-column selection (issue #136): extends the
                  parquet load column list with the tilt cell columns
                  (rent, Eigentuemerquote, HH weight) in the SAME
                  ``load_control_cells`` read, and builds the tilt working
                  frame from the already-loaded cells (``tilt_extra_load_columns``,
                  ``extract_tilt_cells``).
    controls_builder
                  Control-frame assembly: the PopulationSim ``controls.csv``
                  frame (``build_controls_df``), the per-Kreis census-column
                  map (``_kreis_controls_map``), the per-Kreis PERSON totals
                  (``person_band_census_columns``, ``person_total_by_kreis``,
                  ``person_total_by_kreis_min_age``), the grid-geography
                  control filter (``_grid_geography_controls``) and the
                  aggregation-map / source-column builders
                  (``build_aggregation_map``, ``build_source_columns``).
    cell_attributes
                  Per-cell attribute joining (``join_cell_attributes``: the
                  12-digit ARS + RegioStaR7 join from the loaded cells frame
                  onto the merged PopulationSim output) and Kreis-code
                  derivation (``derive_geo_kreis_from_ars``).
    batch_cache   Work-dir batch cache invalidation: the config-signature
                  filename (``WORK_DIR_SIGNATURE_FILE``), the stale
                  ``batch_*`` folder purge
                  (``purge_stale_batches_on_config_change``) and the
                  work-dir batch-input signature
                  (``compute_batch_config_signature``, backed by
                  ``_frame_content_signature``).

``execute()`` itself is decomposed into the named private orchestration steps
defined directly above it (see the banner comment there): each step is a
verbatim move of one commented block, called in the original order and threading
its data through parameters and return values only, so the call order and the
seeded RNG draw order are unchanged. Two blocks stay inline in ``execute``,
each with its reason documented at the block.

``validate()`` folds the sources of the helper modules this stage's result
depends on into the synpp validation token, because synpp's ``get_stage_hash``
covers only THIS file's source: without the hook a change confined to a helper
devalidates nothing and the stale cached stage output is silently reused. The
token covers this package's own six submodules, all nine
``braunschweig.popsim.mid`` modules, the ``braunschweig.popsim.sources`` package
one level deep (registry ``__init__`` plus the ``base`` / ``entd`` / ``mid``
donor adapters), the other non-stage module-level helper imports (the two
``braunschweig.data.mid`` income tables, ``assembly``, ``batch``, ``income``,
``income_kreis_control``, ``income_spatial_tilt``, ``plausibility``,
``prepared_cells``) and the two synpp stages used here as plain function
libraries without a declared dependency
(``braunschweig.data.census.household_size``,
``braunschweig.synthesis.population.enriched``). It deliberately does NOT cover
this stage's DECLARED stage dependencies (synpp hashes those through the DAG
edge), the DEFERRED function-level first-party imports, or the TRANSITIVE import
surface. See ``validate()`` for the full statement, including which residual gaps
that leaves.

DELIBERATE BEHAVIOUR CHANGE (issue #267): this stage previously had NO
``validate()`` at all, so it carried no validation token. Adding one is a
one-off cache event -- synpp sees a token where there was none, so the FIRST run
after this change recomputes this stage and everything downstream exactly once.
Every run after that is cache-stable again, and from then on a helper-only edit
correctly recomputes the stage instead of silently reusing stale output.
Widening the covered set (as done when the non-stage first-party helpers above
were added) changes the token's VALUE once for the same reason, with the same
one-off recompute and no other behaviour change.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd

from braunschweig.data.census.household_size import kreis_household_stats
from braunschweig.data.mid.income_by_size import (
    load_income_by_size_bundesland,
    load_income_by_size_raumtyp,
)
from braunschweig.data.mid.income_by_status import (
    load_income_by_status_bundesland,
    load_income_by_status_raumtyp,
)

# The two ``braunschweig.data.mid`` income-table modules are ALSO bound as
# modules (not only via their loader functions above) so their sources
# participate in the validation token built by validate() below. The
# ``_data_mid_`` prefix distinguishes them from the ``_mid_`` aliases of the
# ``braunschweig.popsim.mid`` submodules further down; neither prefix collides
# with any name this facade re-exports.
from braunschweig.data.mid import income_by_size as _data_mid_income_by_size
from braunschweig.data.mid import income_by_status as _data_mid_income_by_status

# The two modules that ARE themselves synpp stages but are used HERE as plain
# function libraries, bound as MODULES so their sources participate in the
# validation token built by validate() below. A synpp stage legitimately belongs
# in this helper token when -- and only when -- BOTH of these hold:
#   (a) this stage calls into it as a LIBRARY (``kreis_household_stats`` /
#       ``_apply_housing_tenure``), not via ``context.stage(...)``, and
#   (b) it is NOT among the stage dependencies configure() declares, so synpp
#       never propagates its own stage hash into this stage's cache key.
# Neither mechanism therefore covers these two: without the entries below, an
# edit confined to either helper function leaves this stage's cached output
# silently stale. ``household_size`` is the only such stage imported at MODULE
# level; ``enriched`` is imported at FUNCTION level inside
# _apply_housing_tenure_parity (an import site does not change the cache
# residual, so it is listed on the same grounds). The stages configure() DOES
# declare (inkar_income, regiostar_tenure, completed_donor,
# data.hts.entd.filtered) remain deliberately unlisted -- synpp hashes those
# through the declared DAG edge, so listing them would only add churn.
from braunschweig.data.census import household_size as _census_household_size
from braunschweig.synthesis.population import enriched as _population_enriched

from braunschweig.popsim import assembly
from braunschweig.popsim import batch
from braunschweig.popsim import income as _income
from braunschweig.popsim import income_kreis_control as _kic
from braunschweig.popsim import income_spatial_tilt as _ist
from braunschweig.popsim import plausibility as _plausibility
from braunschweig.popsim import mid
from braunschweig.popsim import prepared_cells
from braunschweig.popsim.income import HIGH_INCOME_THRESHOLD_EUR

# The eight submodules of the braunschweig.popsim.mid helper package, imported
# EXPLICITLY (never via dir() or a glob) so their sources participate in the
# synpp validation token built by validate() below. The package itself is
# already imported above as ``mid``; only these submodule bindings are new.
# The ``_mid_`` prefix is deliberate: it keeps every alias clear of the stage
# facade's re-exported names (e.g. the sibling top-level ``batch`` /
# ``prepared_cells`` modules and ``_kreis_controls_map``), which some of these
# submodule names come close to.
from braunschweig.popsim.mid import batch_folders as _mid_batch_folders
from braunschweig.popsim.mid import control_cells as _mid_control_cells
from braunschweig.popsim.mid import csv_format as _mid_csv_format
from braunschweig.popsim.mid import donor as _mid_donor
from braunschweig.popsim.mid import donor_stratification as _mid_donor_stratification
from braunschweig.popsim.mid import kreis_controls as _mid_kreis_controls
from braunschweig.popsim.mid import participation as _mid_participation
from braunschweig.popsim.mid import seed_loading as _mid_seed_loading

# The braunschweig.popsim.sources donor-adapter package enumerated ONE level
# deep, again EXPLICITLY (never via dir() or a glob). The package ``__init__``
# (re-exported below as ``sources`` via source_resolution) is only the small
# name -> adapter registry; the behaviour that shapes this stage's RESULT lives
# in the adapter submodules: ``mid`` is the default donor path, ``entd`` the
# popsim_open path, ``base`` the shared adapter protocol they implement. Listing
# the registry alone would leave a seed-build or attribute-mapping edit invisible
# to the token. Deliberately NOT recursed deeper: the covered set stays bounded
# to one level, exactly as for the mid package above.
from braunschweig.popsim.sources import base as _sources_base
from braunschweig.popsim.sources import entd as _sources_entd
from braunschweig.popsim.sources import mid as _sources_mid

# ---------------------------------------------------------------------------
# Package submodules (extracted stage sections). Every name is re-exported
# here so external consumers (calibration scripts, tests) keep importing from
# the stage module path unchanged.
# Each submodule MUST also be listed in _HELPER_MODULES below so its source
# participates in the synpp cache-validation token.
# ---------------------------------------------------------------------------

from . import batch_cache
# ``hashlib`` is NOT re-exported from batch_cache any more: validate() below
# needs it directly, so it is imported from the standard library at the top of
# this file (as the pre-split module did). The module-level ``hashlib`` name --
# and hence the namespace seen by consumers -- is the same object either way.
from .batch_cache import (  # noqa: F401  (re-exports)
    WORK_DIR_SIGNATURE_FILE,
    _frame_content_signature,
    compute_batch_config_signature,
    json,
    purge_stale_batches_on_config_change,
    shutil,
)
from . import cell_attributes
from .cell_attributes import (  # noqa: F401  (re-exports)
    derive_geo_kreis_from_ars,
    join_cell_attributes,
)
from . import config_keys
from .config_keys import (  # noqa: F401  (re-exports)
    KEY_BATCH_TIMEOUT,
    KEY_BIKES_KREIS_CONTROL,
    KEY_CARS_KREIS_CONTROL,
    KEY_CELLS,
    KEY_CLEANUP_H5,
    KEY_COMPLETE_MEMBERS,
    KEY_CONTROL_TIERS,
    KEY_CONTROLS,
    KEY_CONTROLS_SOURCE,
    KEY_EBIKE_KREIS_CONTROL,
    KEY_EBIKE_SEED_COLUMN,
    KEY_EDUCATION_PARTICIPATION_CONTROL,
    KEY_EMPLOYMENT_GRID,
    KEY_EMPLOYMENT_STATUS_KREIS_CONTROL,
    KEY_IMPORTANCE_PROFILE,
    KEY_INCOME_KC,
    KEY_INCOME_KC_HHSIZE,
    KEY_INCOME_KC_METHOD,
    KEY_INCOME_KC_PARETO,
    KEY_INCOME_KC_PARETO_ALPHA,
    KEY_INCOME_TILT,
    KEY_INCOME_TILT_BETA,
    KEY_INCOME_TILT_CLIP,
    KEY_KREIS_CONTROLS,
    KEY_KREISE,
    KEY_LEISURE_PARTICIPATION_CONTROL,
    KEY_LOGGING,
    KEY_MAX_CELLS,
    KEY_MID,
    KEY_PLACEMENT_INCOME,
    KEY_POPSIMPREP,
    KEY_SEED_DAY_FILTER,
    KEY_SETTINGS,
    KEY_SOURCE,
    KEY_STATUS_KREIS_CONTROL,
    KEY_STATUS_KREIS_SHRINKAGE_N,
    KEY_STRATIFY,
    KEY_TRIPS_KREIS_CONTROL,
    KEY_UV,
    KEY_WEEKEND_PLAN_MATCH,
    KEY_WORK_DIR,
    KEY_WORK_PARTICIPATION_CONTROL,
    KEY_WORKERS,
    _KREIS_CONTROL_DEFAULT,
    _KREIS_CONTROL_TOGGLE_KEY,
)
from . import controls_builder
from .controls_builder import (  # noqa: F401  (re-exports)
    _grid_geography_controls,
    _kreis_controls_map,
    build_aggregation_map,
    build_controls_df,
    build_source_columns,
    person_band_census_columns,
    person_total_by_kreis,
    person_total_by_kreis_min_age,
)
from . import source_resolution
from .source_resolution import (  # noqa: F401  (re-exports)
    _resolve_source,
    active_kreis_entries,
    sources,
)
from . import tilt_columns
from .tilt_columns import (  # noqa: F401  (re-exports)
    _TILT_ARS_COL,
    _TILT_HH_COL,
    _TILT_QUOTE_COL,
    _TILT_RENT_COL,
    extract_tilt_cells,
    tilt_extra_load_columns,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# synpp cache validation
# ---------------------------------------------------------------------------

# Every FIRST-PARTY module whose source can change this stage's RESULT without
# changing this file, and whose hash no other mechanism already carries:
#
#   1. the six submodules extracted from this package,
#   2. the whole ``braunschweig.popsim.mid`` package (its ``__init__`` --
#      imported above as ``mid`` -- and its eight submodules), which carries the
#      seed / donor / control / batch-folder logic execute() orchestrates,
#   3. the whole ``braunschweig.popsim.sources`` package one level deep (its
#      registry ``__init__`` plus the ``base`` / ``entd`` / ``mid`` donor
#      adapters), which carries the seed build, donor loading and attribute
#      mapping of the active source,
#   4. the remaining non-stage first-party helper modules imported at module
#      level here or by a submodule: the two ``braunschweig.data.mid`` income
#      tables, the popsim persons assembly, the PopulationSim batch runner, the
#      income / income-Kreis-control / income-spatial-tilt helpers, the
#      plausibility checks and the prepared-cells loader,
#   5. the two modules that ARE synpp stages but are used here as plain function
#      libraries WITHOUT being declared as dependencies
#      (``braunschweig.data.census.household_size``,
#      ``braunschweig.synthesis.population.enriched``); see the import comment
#      above for why a synpp stage legitimately appears in a helper token.
#
# synpp's get_stage_hash only hashes THIS file's source, so without the
# validate() hook below a change confined to any of these helpers would
# silently reuse the stale cached stage output on a partial rerun. Listed
# EXPLICITLY (never dir() / globbing, never a transitive import walk) so both
# dropping and adding a module is a visible diff, and iterated in the order
# written so the digest is deterministic.
#
# Modules that are themselves synpp stages AND are DECLARED dependencies of this
# stage (``context.stage(...)`` in configure: inkar_income, regiostar_tenure,
# completed_donor, data.hts.entd.filtered) are deliberately NOT listed: synpp
# hashes those from their own source and propagates it through the declared DAG
# edge, so double-covering them would only add churn. Group 5 above is the
# complement of that rule, not an exception to it.
#
# Every module extracted from this package MUST be listed here.
_HELPER_MODULES = (
    # this package's submodules
    batch_cache,
    cell_attributes,
    config_keys,
    controls_builder,
    source_resolution,
    tilt_columns,
    # the braunschweig.popsim.mid helper package: __init__ + its submodules
    mid,
    _mid_batch_folders,
    _mid_control_cells,
    _mid_csv_format,
    _mid_donor,
    _mid_donor_stratification,
    _mid_kreis_controls,
    _mid_participation,
    _mid_seed_loading,
    # the braunschweig.popsim.sources donor-adapter package: __init__ + its
    # submodules (enumerated ONE level deep, not recursed further)
    sources,
    _sources_base,
    _sources_entd,
    _sources_mid,
    # other first-party, non-stage helper modules imported at module level,
    # ordered by dotted module path
    _data_mid_income_by_size,
    _data_mid_income_by_status,
    assembly,
    batch,
    _income,
    _kic,
    _ist,
    _plausibility,
    prepared_cells,
    # synpp stages used here as plain function libraries whose dependency this
    # stage does NOT declare, so their own stage hash never reaches this stage
    # (see the import comment above), ordered by dotted module path
    _census_household_size,
    _population_enriched,
)


def validate(context):
    """synpp validation token: md5 over every helper module's source.

    synpp stores the value returned here alongside the cached stage output and
    devalidates that cache when the value changes, so a helper-only source
    change recomputes the stage exactly like an edit to this file. The hook is
    needed because synpp's ``get_stage_hash`` hashes ONLY the stage module's own
    source (``inspect.getsource`` of this file): without it, an edit to one of
    this package's submodules or to a ``braunschweig.popsim.mid`` module -- i.e.
    to the code that actually builds the seed, controls and batches -- would
    leave the token unchanged and the stale cached output would be reused
    silently.

    COVERED (``_HELPER_MODULES``): its own six submodules; all nine
    ``braunschweig.popsim.mid`` modules; the ``braunschweig.popsim.sources``
    package one level deep (registry ``__init__`` plus the ``base`` / ``entd`` /
    ``mid`` donor adapters); the remaining non-stage first-party modules imported
    at module level (the two ``braunschweig.data.mid`` income tables,
    ``braunschweig.popsim.assembly`` / ``batch`` / ``income`` /
    ``income_kreis_control`` / ``income_spatial_tilt`` / ``plausibility`` /
    ``prepared_cells``); and the two synpp stages this package uses as plain
    function libraries without declaring the dependency
    (``braunschweig.data.census.household_size`` for ``kreis_household_stats``,
    ``braunschweig.synthesis.population.enriched`` for ``_apply_housing_tenure``)
    -- for those two neither synpp's own stage hashing nor a declared DAG edge
    reaches this stage, so the token is the only mechanism that can see them.

    DELIBERATELY NOT COVERED:

    * Modules that are themselves synpp stages AND are DECLARED dependencies of
      this stage (``context.stage(...)`` in ``configure``:
      ``braunschweig.data.inkar.household_income``,
      ``braunschweig.data.bbsr.regiostar``,
      ``braunschweig.popsim.completed_donor``, ``data.hts.entd.filtered``).
      synpp derives their hash from their own source and propagates it through
      the declared edge, so listing one here would only add churn.
      ``braunschweig.data.census.household_size`` is the only synpp stage this
      package imports at MODULE level and
      ``braunschweig.synthesis.population.enriched`` the only one imported at
      FUNCTION level; both are undeclared, hence COVERED above rather than
      excluded here. Residual on the ``enriched`` entry: ``inspect.getsource``
      of a package yields its ``__init__`` only, and ``_apply_housing_tenure``
      itself lives in ``enriched.housing_tenure``, so an edit confined to that
      submodule is still invisible to this token (closing it means listing that
      submodule too, or declaring the dependency).
    * The DEFERRED (function-level) first-party import surface. These modules are
      DIRECT dependencies of this stage's result -- not transitive ones -- but
      they are imported inside a function body (to keep the import cost off the
      module-import path and out of ``configure``-only runs) and are therefore
      absent from the module-level set this token was built from:
      ``braunschweig.popsim.control_spec`` (the control catalog itself),
      ``kreis_attribute_control``, ``placement_income``, ``employment_grid``,
      ``zensus_employment_age``, ``folders``, ``braunschweig.parallelism`` and
      ``braunschweig.data.mid.tenure_by_income``. An edit confined to any of them
      changes this stage's output without changing the token; this is the largest
      remaining gap and is accepted only because listing a deferred import here
      would re-introduce, at module level, the import cost the deferral avoids.
    * The TRANSITIVE import surface. The set is bounded to modules imported
      directly by this package; a module imported only by one of the listed
      helpers is not walked, so an edit deep inside such a dependency is not
      reflected in the token.

    ``_HELPER_MODULES`` is iterated in the order written -- not a set, not
    ``dir()`` output -- so the digest is reproducible across processes and
    platforms. ``context`` is unused: the token depends on source text only.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


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
    context.config(KEY_BATCH_TIMEOUT, batch.DEFAULT_POPSIM_TIMEOUT_S)
    # Cleanup of the dead per-batch pipeline.h5 checkpoint store (issue #153).
    context.config(KEY_CLEANUP_H5, True)
    context.config(KEY_KREISE)
    context.config(KEY_SOURCE, "mid")
    # Seeded attribute imputation in build_persons; declaring the key also makes
    # synpp invalidate the stage cache when the pipeline random_seed changes.
    context.config("random_seed")
    # RegioStaR donor stratification (Phase 4B): default OFF (user decision 2026-07-09).
    # The FULL donor pool is the core of the popsim_mid design: PopulationSim selects
    # suitable donor households by their mobility-relevant characteristics from the
    # ENTIRE national pool; restricting donors to the cell's RegioStaR class breaks
    # that (e.g. Salzgitter's mobility character is rural-like despite its statistical
    # urban class). Set True to opt back into the Phase 4B stratified path.
    context.config(KEY_STRATIFY, False)
    # Member completion (D3). Default True; False -> legacy path (see execute()).
    context.config(KEY_COMPLETE_MEMBERS, True)
    # Controls source (Task 5). Default "csv" = byte-identical to today's behaviour.
    context.config(KEY_CONTROLS_SOURCE, "csv")
    # Control tiers (Task 7). Default "tier0" = byte-identical to pre-Task-7 baseline.
    context.config(KEY_CONTROL_TIERS, "tier0")
    # Tier-3 KREIS controls directory. Default "" = not configured (no Tier-3).
    context.config(KEY_KREIS_CONTROLS, "")
    # Employment grid control (Task 5). Default "off" = byte-identical to today.
    context.config(KEY_EMPLOYMENT_GRID, "off")
    # PopulationSim per-control importance profile (control_spec.IMPORTANCE_PROFILES).
    # Default "uniform" = every control importance untouched (byte-identical). Set to
    # "optimized_2026_06_30" to apply the searched per-group weights (see control_spec).
    context.config(KEY_IMPORTANCE_PROFILE, "uniform")
    # When the employment grid control is ON, the per-cell employment targets are
    # built from the Zensus 2000S-2001 employment-by-age SHAPE (a committed reference
    # CSV under data_path; see braunschweig.popsim.zensus_employment_age) rescaled per
    # Kreis×sex×group to the census Erwerbstaetige Kreis level. No synpp stage
    # dependency is needed (the former GENESIS SvB stage dependency is gone). We only
    # ensure data_path is declared so the reference CSV can be located; this is a
    # no-op when data_path is already declared (KEY_INCOME_KC / housing_tenure).
    if str(context.config(KEY_EMPLOYMENT_GRID, "off")).strip().lower() == "on":
        context.config("data_path")
    # KREIS attribute controls (issue #109 + S1c). Each defaults per _KREIS_CONTROL_DEFAULT
    # (project rule: new features default on) -- all four entries default "on"; has_ebike's
    # source column (H_ANZPED) was server-verified 2026-07-08, so it is wired on both seed
    # paths (see mid.load_mid_seed / mid.project_completed_seed). When any is on, the
    # committed per-Kreis target CSV under data_path is needed, so ensure data_path is
    # declared (no-op if already). economic_status also carries a configurable Dirichlet
    # shrinkage prior; the S1c targets are FINAL (prior_n = 0, no key).
    context.config(KEY_STATUS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["economic_status"])
    context.config(KEY_STATUS_KREIS_SHRINKAGE_N, 0.0)
    context.config(KEY_CARS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["number_of_cars"])
    context.config(KEY_BIKES_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["number_of_bicycles"])
    context.config(KEY_EBIKE_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["has_ebike"])
    # trip_class (first PERSON-level KREIS control, 2026-07-08). Default "on"; its
    # committed SrV target lives under data_path (declared below via the any()-gate).
    context.config(KEY_TRIPS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["trip_class"])
    # employment_status (second PERSON-level KREIS control, feature #172 task 4).
    # Default "on"; its committed MiD-P9 x SrV-V_ERW blended target lives under
    # data_path (declared below via the any()-gate). 14+ universe restriction (min_age)
    # is carried on the REGISTRY entry itself, not a separate config key.
    context.config(KEY_EMPLOYMENT_STATUS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["employment_status"])
    # work_participation (third PERSON-level KREIS control, feature #224 task 4). Default
    # "on"; its committed SrV-2023 participation blended target lives under data_path
    # (declared below via the any()-gate). Seed derivation reads the MiD Wege table
    # (mid.load_mid_wege) at the two seed call sites, gated on this control being active.
    context.config(KEY_WORK_PARTICIPATION_CONTROL, _KREIS_CONTROL_DEFAULT["work_participation"])
    # leisure_participation / education_participation (fourth/fifth PERSON-level KREIS
    # controls, feature #224 task 5). Default "on"; their committed SrV-2023
    # participation blended targets live under data_path (declared below via the
    # any()-gate). Seed derivation reads the MiD Wege table (mid.load_mid_wege) at the
    # two seed call sites, gated on either control being active -- mirrors
    # work_participation exactly (parametrized by purpose, not duplicated).
    context.config(KEY_LEISURE_PARTICIPATION_CONTROL, _KREIS_CONTROL_DEFAULT["leisure_participation"])
    context.config(KEY_EDUCATION_PARTICIPATION_CONTROL, _KREIS_CONTROL_DEFAULT["education_participation"])
    # Default "H_ANZPED": the server-verified MiD household e-bike column (see
    # KEY_EBIKE_SEED_COLUMN above); configurable in case a future MiD delivery renames it.
    context.config(KEY_EBIKE_SEED_COLUMN, "H_ANZPED")
    _kreis_control_keys_and_defaults = (
        (KEY_STATUS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["economic_status"]),
        (KEY_CARS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["number_of_cars"]),
        (KEY_BIKES_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["number_of_bicycles"]),
        (KEY_EBIKE_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["has_ebike"]),
        (KEY_TRIPS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["trip_class"]),
        (KEY_EMPLOYMENT_STATUS_KREIS_CONTROL, _KREIS_CONTROL_DEFAULT["employment_status"]),
        (KEY_WORK_PARTICIPATION_CONTROL, _KREIS_CONTROL_DEFAULT["work_participation"]),
        (KEY_LEISURE_PARTICIPATION_CONTROL, _KREIS_CONTROL_DEFAULT["leisure_participation"]),
        (KEY_EDUCATION_PARTICIPATION_CONTROL, _KREIS_CONTROL_DEFAULT["education_participation"]),
    )
    if any(
        str(context.config(k, default)).strip().lower() == "on"
        for k, default in _kreis_control_keys_and_defaults
    ):
        context.config("data_path")
    # Seed reporting-day filter. Default "default" = legacy (1,2,3) Mo-Fr.
    context.config(KEY_SEED_DAY_FILTER, "default")
    # Spatial income tilt (Task 3). Default ON (project rule: features default on).
    # When OFF, the income frame is unchanged (byte-identical); no cells parquet
    # re-read occurs.
    context.config(KEY_INCOME_TILT, True)
    context.config(KEY_INCOME_TILT_BETA, 0.3)
    context.config(KEY_INCOME_TILT_CLIP, 0.30)
    # Kreis-Income-Control (default ON; OFF = byte-identical midpoint x INKAR_scale).
    context.config(KEY_INCOME_KC, True)
    context.config(KEY_INCOME_KC_METHOD, "combined")
    context.config(KEY_INCOME_KC_HHSIZE, True)
    context.config(KEY_INCOME_KC_PARETO, True)
    context.config(KEY_INCOME_KC_PARETO_ALPHA, 3.0)
    # placement_income (L2, issue #108). Default ON (project rule). ON overrides both
    # income_kreis_control and income_spatial_tilt (logged); OFF is byte-identical.
    # MiD-only (needs the hheink_gr1 donor income); inactive for source="entd".
    context.config(KEY_PLACEMENT_INCOME, True)
    # Weekend-plan match (default ON; OFF = byte-identical to pre-feature donor build).
    context.config(KEY_WEEKEND_PLAN_MATCH, True)
    if context.config(KEY_INCOME_KC, True):
        context.config("data_path")  # MiD income tables + Zensus household file
        context.config("braunschweig.zensus_households_path",
                       "braunschweig/5000H-2001_de_flat.csv")

    # INKAR per-Kreis household income scale: used by both popsim_mid and popsim_open
    # to apply the same income scaling as the IPF/enriched path.
    context.stage("braunschweig.data.inkar.household_income", alias="inkar_income")

    # housing_tenure parity (legacy enriched feature, parity gap P2): sample the
    # completeness attribute per household from P(tenure | income_bracket,
    # raumtyp) using the SAME _apply_housing_tenure implementation as the
    # IPF/enriched path (no duplicated logic; dedicated RNG offset +83947).
    # Default ON; False -> column absent, byte-identical to the pre-parity output.
    context.config("synthesise_housing_tenure", True)
    if context.config("synthesise_housing_tenure", True):
        context.config("data_path")
        context.stage("braunschweig.data.bbsr.regiostar", alias="regiostar_tenure")

    source_name = context.config(KEY_SOURCE, "mid")
    # Member completion (D3) runs for the MiD source only. When active, the donor
    # build (member completion + weekend-plan match) is delegated to the cached
    # braunschweig.popsim.completed_donor stage so it runs ONCE across all runs
    # (it depends only on mid/seed/day_filter/weekend, not controls/sampling/work_dir).
    if source_name == "mid" and bool(context.config(KEY_COMPLETE_MEMBERS, True)):
        context.stage("braunschweig.popsim.completed_donor", alias="completed_donor")
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


# --------------------------------------------------------------------------- #
# Orchestration steps of execute()
#
# Named private steps of the synpp ``execute`` below. Every step is a VERBATIM
# move of one commented block out of ``execute``; the block's leading comment is
# kept in the step docstring, comments inside a block stay where they were. The
# steps are called in exactly the original order and thread their data
# explicitly through parameters and return values (no module-level state), so
# the call order -- and with it the seeded RNG draw order (``rng`` /
# ``kreis_seed_rng``) -- is unchanged: inlining the calls back reproduces the
# pre-split statement stream one-for-one.
#
# A step parameter always carries the SAME name as the caller's local, so a
# rebinding step returns the value and ``execute`` reassigns it; steps that only
# mutate an object in place say so in their ``Mutates:`` line.
#
# Two blocks stay INLINE in ``execute`` on purpose; each carries its reason
# there. In short: the placement_income reallocation and its own-income consumer,
# because ``_pi_diag`` is bound only when placement runs and is read again behind
# a different guard further down, so neither block can move without adding an
# initialiser statement the original does not have.
# --------------------------------------------------------------------------- #

def _read_stage_paths(context):
    """Read the popsim input/output path config keys.

    Returns: ``(cells_path, mid_dir, controls_path, settings_path, logging_path,
    popsimprep_dir, uv_path)``.
    Mutates: nothing.
    """
    cells_path = context.config(KEY_CELLS)
    mid_dir = context.config(KEY_MID)
    controls_path = context.config(KEY_CONTROLS)
    settings_path = context.config(KEY_SETTINGS)
    logging_path = context.config(KEY_LOGGING)
    popsimprep_dir = context.config(KEY_POPSIMPREP)
    uv_path = context.config(KEY_UV)
    return cells_path, mid_dir, controls_path, settings_path, logging_path, popsimprep_dir, uv_path


def _read_batching_and_scope_config(context):
    """Read the batching, worker, work-dir and regional-scope config keys.

    synpp's ExecuteContext.config() takes only the key; the defaults are
    registered in configure() (3000 / 3 / "mid" / False) and resolved here.

    Returns: ``(max_cells, num_workers, work_dir, kreise, source_name,
    stratify_regiostar, complete_members)``.
    Mutates: CREATES ``work_dir`` on disk (``mkdir(parents=True,
    exist_ok=True)``) and logs the resolved worker count.
    """
    max_cells = int(context.config(KEY_MAX_CELLS))
    # Worker count honours the auto sentinel (0/null/"auto" -> cores - reserve), so
    # the batch runner scales with the box it lands on. An explicit positive integer
    # is used verbatim (pin it when byte-reproducibility across machines matters).
    from braunschweig.parallelism import resolve_workers
    _requested_workers = context.config(KEY_WORKERS)
    num_workers = resolve_workers(_requested_workers)
    logger.info(
        "[popsim.stage] PopulationSim batch workers: %d (requested=%r, cpu_count=%s)",
        num_workers, _requested_workers, os.cpu_count(),
    )
    work_dir = context.config(KEY_WORK_DIR)
    # Create the PopulationSim working directory up front so the stage can write
    # its intermediate artefacts (pseudonym map, per-batch PopulationSim folders)
    # into it. On a FRESH cache this directory does not exist yet; the first writer
    # below (the pseudonym map / per-batch folders) would otherwise fail with
    # "Cannot save file into a non-existent directory". (The weekend_plan_match trace
    # is now written into the completed_donor stage's own cache dir, not here.)
    # Creating it here keeps the stage self-contained (CLAUDE.md: create output
    # directories explicitly).
    Path(work_dir).mkdir(parents=True, exist_ok=True)
    kreise = list(context.config(KEY_KREISE))
    source_name = context.config(KEY_SOURCE)
    stratify_regiostar = bool(context.config(KEY_STRATIFY))
    # Member completion (D3) applies to the MiD donor source only: the ENTD
    # frames have no declared-size column and need no completion.
    complete_members = bool(context.config(KEY_COMPLETE_MEMBERS)) and source_name == "mid"
    return (
        max_cells, num_workers, work_dir, kreise, source_name, stratify_regiostar,
        complete_members,
    )


def _create_seeded_rngs(context):
    """Create the two seeded RNG streams this stage draws from.

    Seeded RNG for the stochastic attribute imputation in build_persons
    (offset +74511 keeps the stream disjoint from the enriched-stage offsets).

    Returns: ``(random_seed, rng, kreis_seed_rng)``. Both ``RandomState``
    objects are created here and consumed IN THE ORIGINAL ORDER by the steps
    below (``kreis_seed_rng`` in the seed build, ``rng`` in the persons
    expansion), so no draw moves across a step boundary.
    Mutates: nothing.
    """
    random_seed = int(context.config("random_seed"))
    rng = np.random.RandomState(random_seed + 74511)
    # Seeded RNG for the count-style KREIS-control seed-column derivations
    # (number_of_cars / number_of_bicycles / has_ebike group-wise 99-code imputation in
    # load_mid_seed). Offset +24680 keeps the stream disjoint from the +74511 imputation
    # stream above; derived from the pipeline random_seed so the seed is reproducible.
    kreis_seed_rng = np.random.RandomState(random_seed + 24680)
    return random_seed, rng, kreis_seed_rng


def _read_control_config(context, source_name: str):
    """Read the control-set, seed-day-filter and KREIS-control config keys.

    Parse the comma-separated tier string (e.g. "tier0,tier1") into a tuple.

    Returns: ``(control_tiers, seed_day_filter, controls_source,
    employment_grid_on, active_entries, active_entry_names, status_prior_n,
    ebike_seed_column_cfg, importance_profile)``.
    Mutates: nothing.
    """
    control_tiers_str = context.config(KEY_CONTROL_TIERS)
    control_tiers = tuple(t.strip() for t in control_tiers_str.split(",") if t.strip())
    # Seed reporting-day filter: "off"/"all" -> keep all kernwo (no day filter, ()),
    # else None -> the loader's default (1,2,3) Mo-Fr.
    _day_filter_cfg = str(context.config(KEY_SEED_DAY_FILTER)).strip().lower()
    seed_day_filter = () if _day_filter_cfg in ("off", "all", "none", "") else None
    controls_source = context.config(KEY_CONTROLS_SOURCE)
    # Employment grid control (Task 5): default "off" -> byte-identical path.
    employment_grid_on = str(context.config(KEY_EMPLOYMENT_GRID)).strip().lower() == "on"
    # KREIS attribute controls (issue #109 + S1c): the active REGISTRY entries whose toggle
    # is "on" (each default "on"), MiD-only (their seed columns have no ENTD pendant), so an
    # ENTD run is unaffected (empty list). economic_status is one of them; it alone carries
    # the configurable Dirichlet shrinkage prior. The three S1c targets are FINAL (prior_n=0).
    active_entries = active_kreis_entries(context, source_name)
    active_entry_names = tuple(c.name for c in active_entries)
    status_prior_n = float(context.config(KEY_STATUS_KREIS_SHRINKAGE_N))
    # E-bike seed column (server-verified default "H_ANZPED", declared in configure;
    # ExecuteContext.config takes no default argument). An empty string (explicitly
    # cleared config) -> None; the loader fail-fasts if has_ebike is active without it
    # (no silent fallback).
    ebike_seed_column_cfg = str(context.config(KEY_EBIKE_SEED_COLUMN)).strip() or None
    # Importance profile: default "uniform" -> importance untouched (byte-identical).
    importance_profile = str(context.config(KEY_IMPORTANCE_PROFILE)).strip()
    return (
        control_tiers, seed_day_filter, controls_source, employment_grid_on,
        active_entries, active_entry_names, status_prior_n, ebike_seed_column_cfg,
        importance_profile,
    )


def _build_control_frame(controls_source, controls_path, source_name: str, control_tiers,
        employment_grid_on: bool, active_entry_names, importance_profile: str):
    """Build the PopulationSim ``controls.csv`` frame and its base cell columns.

    Returns: ``(controls_df, base_cols)``.
    Mutates: nothing.
    """
    controls_df = build_controls_df(
        controls_source=controls_source,
        controls_path=controls_path,
        seed=source_name,
        tiers=control_tiers,
        employment_grid=employment_grid_on,
        kreis_control_names=active_entry_names,
        importance_profile=importance_profile,
    )
    if importance_profile and importance_profile != "uniform":
        logger.info("[popsim.stage] importance profile applied: %s", importance_profile)
    base_cols = mid.control_base_columns(controls_df, "ZENSUS100m")
    return controls_df, base_cols


def _load_tier3_kreis_controls(context, control_tiers, controls_source, source_name: str, kreise):
    """Load the Tier-3 per-Kreis control table and its census-column map.

    Tier-3 KREIS controls (employment / education): when active, load the imported
    per-Kreis census table and derive the {control_name: census_source} map from the
    catalog's KREIS-geography controls expressible by the active seed. Passed to
    run_popsim_mid, which builds control_totals_KREIS.csv per batch. When tier3 is
    absent both stay None -> the tier0-2 folder is byte-identical.

    Returns: ``(kreis_table, kreis_controls_map, household_control_names)`` --
    the first two are ``None`` when Tier-3 is inactive, the third is the
    (possibly empty) set of household-level KREIS control names that
    :func:`_derive_kreis_attribute_control_targets` fills in place below.
    Mutates: nothing.
    """
    kreis_table = None
    kreis_controls_map = None
    # Household-level KREIS control names (keys of kreis_controls_map) that must be
    # apportioned across batches by the batch's HOUSEHOLD share, not the population
    # share (issue #148). Populated from the active household-level attribute controls
    # below; empty otherwise (person-level / tier0-2 -> pop-share everywhere, unchanged).
    household_control_names: set[str] = set()
    if "tier3" in control_tiers and controls_source == "catalog":
        from braunschweig.popsim import control_spec as _cs
        tier3 = [c for c in _cs.tier3_controls() if c.expression_for(source_name) is not None]
        kreis_controls_map = _kreis_controls_map(tier3)
        kreis_dir = context.config(KEY_KREIS_CONTROLS)
        if not kreis_dir:
            raise ValueError(
                "control_tiers includes 'tier3' but "
                f"{KEY_KREIS_CONTROLS} is not set; cannot source the KREIS controls."
            )
        # Restrict the national Tier-3 table (~400 Kreise) to the run's Kreise at load
        # time so the accumulator carries only rows looked up downstream (issue #147);
        # the resolved dominant Kreis of any in-scope cell is itself in `kreise`
        # (cells are filtered to `kreise` before the crosswalk is built), so no needed
        # Kreis row is dropped.
        kreis_table = mid.load_kreis_control_table(kreis_dir, restrict_to_kreise=kreise)
        logger.info(
            "[popsim.stage] Tier-3 KREIS controls active: %d controls, kreis table %d rows "
            "(restricted to the run's %d Kreise) from %s",
            len(kreis_controls_map), len(kreis_table), len(kreise), kreis_dir,
        )
    return kreis_table, kreis_controls_map, household_control_names


def _resolve_cell_load_columns(context, controls_source, source_name: str, control_tiers, base_cols,
        employment_grid_on: bool, cells_path):
    """Resolve the column set loaded from the prepared-cells parquet.

    For catalog-based controls with multi-column census sources (e.g. building_type),
    load the raw source columns from the parquet (union of all census_source tuples)
    rather than the derived control names.  For tier0-only or CSV-based controls,
    source_cols == base_cols == current behaviour -> byte-identical.

    Returns: ``load_cols`` (rebound by the employment-grid and income-tilt
    blocks, so the caller MUST reassign it).
    Mutates: nothing; reads only the parquet SCHEMA when the employment grid
    control is on.
    """
    source_cols_override = build_source_columns(
        controls_source=controls_source,
        seed=source_name,
        tiers=control_tiers,
    )
    load_cols = source_cols_override if source_cols_override is not None else base_cols

    # Employment grid control (Task 5): the ten EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg
    # targets are COMPUTED per cell (not stored in the parquet), so strip them from the
    # parquet load set and add the single-year {M,F}_AGE_<year> input columns (the age
    # SHAPE denominator, y>=16) that ARE present in the parquet. When OFF, load_cols is
    # untouched (byte-identical).
    if employment_grid_on:
        from braunschweig.popsim import employment_grid as _eg
        import pyarrow.parquet as _pq_eg

        _eg_raw_names = _pq_eg.ParquetFile(cells_path).schema.names
        _eg_available = [prepared_cells.clean_col_name(_n) for _n in _eg_raw_names]
        load_cols = _eg.select_load_columns(
            load_cols, _eg_available,
            computed_cols={
                "EMPLOYED_M_16_29_agg", "EMPLOYED_M_30_39_agg", "EMPLOYED_M_40_49_agg",
                "EMPLOYED_M_50_59_agg", "EMPLOYED_M_60plus_agg",
                "EMPLOYED_F_16_29_agg", "EMPLOYED_F_30_39_agg", "EMPLOYED_F_40_49_agg",
                "EMPLOYED_F_50_59_agg", "EMPLOYED_F_60plus_agg",
            },
        )

    # Income spatial tilt (issue #136): fetch the tilt cell columns (rent /
    # Eigentuemerquote / HH weight) in this SINGLE read instead of re-scanning
    # the national parquet later. When the tilt is OFF, load_cols is returned
    # unchanged (byte-identical).
    load_cols = tilt_extra_load_columns(
        bool(context.config(KEY_INCOME_TILT)), list(load_cols)
    )
    return load_cols


def _add_aggregated_control_columns(cells: pd.DataFrame, controls_source, source_name: str, control_tiers) -> pd.DataFrame:
    """Derive the multi-column aggregated control columns on the cells frame.

    Derive the multi-column aggregated control columns (e.g. building_type_*).
    For tier0-only: agg_map is empty -> add_aggregated_controls returns cells
    unchanged -> byte-identical.

    Returns: the cells frame with the aggregated control columns (MUST be
    reassigned).
    Mutates: nothing in place.
    """
    agg_map = build_aggregation_map(
        controls_source=controls_source,
        seed=source_name,
        tiers=control_tiers,
    )
    cells = prepared_cells.add_aggregated_controls(cells, agg_map)
    return cells


def _inject_employment_grid_columns(context, cells: pd.DataFrame, employment_grid_on: bool) -> pd.DataFrame:
    """Inject the ten per-cell employment-grid target columns (Task 5).

    Employment grid control (Task 5): inject the ten per-cell
    EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg target columns. The age SHAPE comes from the
    committed Zensus 2000S-2001 employment-by-age reference (zensus_employment_age.
    load_age_shares; exact for the kreisfreie Staedte, national fallback for the
    Landkreise) and is rescaled per Kreis x sex x group to the census Erwerbstaetige
    Kreis level (kreis_erwerbsstatus.parquet). The former GENESIS SvB synpp stage
    dependency is no longer used. When OFF, none of this runs -> byte-identical.

    Returns: the cells frame with the ten ``EMPLOYED_*_agg`` columns (MUST be
    reassigned); returned unchanged when the control is off.
    Mutates: nothing in place (the block copies the frame before adding the
    Kreis column); reads the Kreis employment parquet and the committed
    age-share reference CSV.
    """
    if employment_grid_on:
        from braunschweig.popsim import employment_grid as _eg
        from braunschweig.popsim import zensus_employment_age as _za
        from braunschweig.popsim import folders as _folders

        # Census LEVEL: the sex-split Erwerbstaetige Kreis totals. Sourced from the
        # imported cleancensus kreis_erwerbsstatus table (same dir as the Tier-3
        # KREIS controls). Fail fast (Tier-3 style) if the dir is not configured.
        _eg_kreis_dir = context.config(KEY_KREIS_CONTROLS)
        if not _eg_kreis_dir:
            raise ValueError(
                f"{KEY_EMPLOYMENT_GRID} is 'on' but {KEY_KREIS_CONTROLS} is not set; "
                "cannot source the census Erwerbstaetige Kreis levels "
                "(kreis_erwerbsstatus.parquet)."
            )
        _eg_levels_path = os.path.join(_eg_kreis_dir, "kreis_erwerbsstatus.parquet")
        if not os.path.exists(_eg_levels_path):
            raise FileNotFoundError(
                f"employment grid control requires {_eg_levels_path}; import the "
                "cleancensus kreis_erwerbsstatus parquet into the kreis_controls dir."
            )
        _eg_levels = pd.read_parquet(_eg_levels_path)
        _eg_levels["ARS_kreis"] = _eg_levels["ARS_kreis"].astype(str).str.zfill(5)
        _eg_census_levels = _eg_levels[
            ["ARS_kreis", "ERWERBSTAT_KURZ_STP__11_M", "ERWERBSTAT_KURZ_STP__11_W"]
        ].copy()

        # Derive the 5-digit Kreis on the cells frame from the 12-digit ARS column,
        # matching folders.GEO_KREIS = ARS[:5].
        if mid._ARS_COLUMN not in cells.columns:
            raise ValueError(
                f"{KEY_EMPLOYMENT_GRID} is 'on' but the cells frame carries no "
                f"{mid._ARS_COLUMN!r} column; cannot derive the Kreis code."
            )
        cells = cells.copy()
        cells[_folders.GEO_KREIS] = derive_geo_kreis_from_ars(cells[mid._ARS_COLUMN])

        # SHAPE: Zensus 2000S-2001 employment-by-age-group shares per Kreis, loaded from
        # the committed reference CSV under data_path. Built once per distinct Kreis on
        # the cells frame (exact for 03101/02/03, national fallback for the Landkreise).
        _eg_data_path = context.config("data_path")
        _eg_ref = os.path.join(
            _eg_data_path, "braunschweig/popsim/zensus2022_employment_by_age_ref.csv"
        )
        if not os.path.exists(_eg_ref):
            raise FileNotFoundError(
                f"employment grid control requires the Zensus age-share reference "
                f"{_eg_ref}; import zensus2022_employment_by_age_ref.csv into "
                "data/braunschweig/popsim/."
            )
        _eg_kreise = sorted(cells[_folders.GEO_KREIS].astype(str).unique())
        _eg_age_shares = {k: _za.load_age_shares(_eg_ref, k) for k in _eg_kreise}

        # Fallback observability (CLAUDE.md, no silent fallback): load_age_shares
        # silently substitutes the national "DE_large_gemeinden" shape for a Kreis absent
        # from the reference. The Landkreise fall back BY DESIGN (only the kreisfreie
        # Staedte 03101/02/03 have an exact 2001 shape), so log the exact-vs-fallback split
        # at info; escalate to warning ONLY if a known-exact kreisfreie Stadt fell back
        # (that signals a broken/renamed reference, not the expected Landkreis fallback).
        _eg_ref_regions = set(
            pd.read_csv(_eg_ref, dtype={"region": str})["region"].astype(str).unique()
        )
        _eg_exact = [k for k in _eg_kreise if k in _eg_ref_regions]
        _eg_fallback = [k for k in _eg_kreise if k not in _eg_ref_regions]
        _eg_kreisfrei_exact = {"03101", "03102", "03103"}
        _eg_unexpected = sorted(_eg_kreisfrei_exact.intersection(_eg_fallback))
        _eg_msg = (
            "[popsim.stage] employment grid age-shape: %d/%d Kreise exact, %d used the "
            "national DE_large_gemeinden fallback %s"
        )
        if _eg_unexpected:
            logger.warning(
                _eg_msg + " -- INCLUDING kreisfreie Stadt/Staedte %s that should be exact; "
                "check the reference CSV region coding.",
                len(_eg_exact), len(_eg_kreise), len(_eg_fallback), _eg_fallback, _eg_unexpected,
            )
        else:
            logger.info(
                _eg_msg + " (Landkreis fallback is by design).",
                len(_eg_exact), len(_eg_kreise), len(_eg_fallback), _eg_fallback,
            )

        cells = _eg.add_employment_grid_columns(
            cells, _eg_census_levels, _eg_age_shares, kreis_col=_folders.GEO_KREIS,
        )
        logger.info(
            "[popsim.stage] employment grid control ON: injected 10 "
            "EMPLOYED_{M,F}_{16_29,30_39,40_49,50_59,60plus}_agg per-cell targets "
            "(census levels from %s, age shape from %s, %d Kreise).",
            _eg_levels_path, _eg_ref, _eg_census_levels["ARS_kreis"].nunique(),
        )
    return cells


def _build_populationsim_seed(context, source, source_name: str, mid_dir, complete_members: bool,
        seed_day_filter, active_entries, kreis_seed_rng, ebike_seed_column_cfg):
    """Build the PopulationSim seed through the active donor source.

    Build the PopulationSim seed.
    For source="mid": delegates to mid.load_mid_seed which reads the MiD CSV
    files with MiD column names (H_ID/H_GEW/HP_ALTER/HP_SEX/P_GEW).
    For source="entd": the ENTD donor frames are transformed to MiD column
    schema by EntdSource.build_seed so the downstream (expand, map_demographics)
    runs unchanged; only map_person_attributes is ENTD-specific.
    The completed donor frames (member completion ON) are loaded here, ahead
    of PopulationSim, because the SEED is derived from them; they are reused
    verbatim as the expansion donor tables further below (ONE completion pass
    -> seed and expansion contain the same fillers).

    Returns: ``(completed_donor_households, completed_donor_persons,
    seed_households, seed_persons)``. The first two stay ``None`` on every path
    except member completion, where ``_load_donor_tables`` further down reuses
    them verbatim as the expansion donor tables.
    Mutates: draws from ``kreis_seed_rng`` on the two MiD paths (the count-style
    KREIS-control 99-code imputation inside the seed loaders) in the unchanged
    order, and reports the seed completeness rate plus the member-completion
    counts via ``context.set_info``.
    """
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
        # popsim_mid with member completion (D3, default ON): the donor build
        # (member completion + weekend-plan match) is produced by the cached
        # braunschweig.popsim.completed_donor stage (ONE pass, shared across runs).
        # The same completed frames feed BOTH the PopulationSim seed (projected
        # here) AND the expansion donor tables below.
        donor = context.stage("completed_donor")
        completed_donor_households = donor.households
        completed_donor_persons = donor.persons
        report = donor.completeness_report
        completion_report = donor.completion_report
        seed_columns = source.seed_columns()
        # project_completed_seed derives hh_type5 (Tier-1 household_type) like
        # load_mid_seed does, so the seed carries it for the household_type control.
        # number_of_cars / number_of_bicycles / has_ebike are derived here too, from
        # the raw H_ANZAUTO / anzpedrad / H_ANZPED columns the completed_donor stage
        # already carries (mid.MID_HOUSEHOLD_ATTR_COLS). has_ebike is fully wired
        # (server-verified 2026-07-08, issue #116 resolved); project_completed_seed
        # only raises if has_ebike is active AND ebike_seed_column_cfg is unset.
        seed_households, seed_persons = mid.project_completed_seed(
            completed_donor_households, completed_donor_persons, seed_columns,
            kreis_control_entries=active_entries,
            kreis_seed_rng=kreis_seed_rng,
            ebike_seed_column=ebike_seed_column_cfg,
            mid_dir=mid_dir,
        )
        # Surface the build reports on THIS run too (so they are present even when
        # the completed_donor stage was served from cache and its execute did not run).
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
            mid_dir, columns=seed_columns, day_filter_values=seed_day_filter,
            kreis_control_entries=active_entries,
            kreis_seed_rng=kreis_seed_rng,
            ebike_seed_column=ebike_seed_column_cfg,
        )
    context.set_info("seed_completeness_rate", report.completeness_rate)
    return (
        completed_donor_households, completed_donor_persons, seed_households,
        seed_persons,
    )


def _prepare_batch_runner(context, uv_path, popsimprep_dir, stratify_regiostar: bool):
    """Build the per-batch PopulationSim runner and log the stratification flag.

    Returns: ``run_one`` -- the per-batch subprocess callable.
    Mutates: nothing.
    """
    run_one = batch.make_populationsim_run_one(
        command_prefix=(str(uv_path), "run", "--no-sync", "populationsim"),
        cwd=popsimprep_dir,
        timeout_s=int(context.config(KEY_BATCH_TIMEOUT)),
        cleanup_pipeline_h5=bool(context.config(KEY_CLEANUP_H5)),
    )

    logger.info(
        "[popsim.stage] stratify_regiostar=%s (Phase 4B donor stratification).",
        stratify_regiostar,
    )
    return run_one


def _derive_kreis_attribute_control_targets(context, cells: pd.DataFrame, active_entries, status_prior_n: float,
        kreis_table, kreis_controls_map, household_control_names: set):
    """Derive the per-Kreis targets of the active KREIS attribute controls.

    KREIS attribute controls (issue #109 + S1c): derive each ACTIVE registered attribute's
    per-Kreis household targets from its committed blended MiD shares x the per-Kreis
    household total (summed cell HH_TOTAL, so the category targets partition EXACTLY the
    household total PopulationSim controls per Kreis -> IPF-consistent). Merge into the KREIS
    control totals + map so run_popsim_mid emits them in control_totals_KREIS.csv. Runs BEFORE
    the config signature below so a control toggle invalidates stale batches. MiD-only
    (active_kreis_entries returns [] for non-MiD sources). With only economic_status active,
    this is byte-identical to the L1 wiring except the target now comes from the blended CSV.

    Returns: ``(kreis_table, kreis_controls_map)`` -- both are REBOUND here
    (the accumulator starts from the Tier-3 pair and merges one table per
    active entry), so the caller MUST reassign both.
    Mutates: ``household_control_names`` IN PLACE -- every household-level
    entry's control names are added to the set the caller passed in (the
    batch apportionment then uses the household share for them, issue #148).
    """
    if active_entries:
        from braunschweig.popsim import kreis_attribute_control as _kac
        from braunschweig.popsim import control_spec as _cs_kac
        _kac_data_path = context.config("data_path")
        _kac_hh_col = _cs_kac.HH_TOTAL_CENSUS_COLUMN
        if _kac_hh_col not in cells.columns:
            raise RuntimeError(
                f"KREIS attribute control is ON but the household-total column {_kac_hh_col!r} is "
                f"absent from the cells frame; cannot derive the per-Kreis targets (no silent fallback).")
        # Align the per-Kreis attribute-control universe with the batch KREIS backbone
        # (issue #147, sub-item 1): partition the household/person targets over the SAME
        # RESOLVED dominant Kreis per 1 km parent that folders.build_kreis_control_totals
        # keys on, not the raw ARS[:5]. NOTE (scientific output change): this reassigns the
        # target attribution of the ~0.1% of cells that sit on a Kreis border and whose
        # 1 km parent is dominated by a neighbouring Kreis; region-wide per-Kreis sums are
        # unchanged (a 1 km parent is atomic to one resolved Kreis).
        _kac_kreis = mid.resolved_kreis_per_cell(cells)
        _kac_hh_by_kreis = cells.groupby(_kac_kreis)[_kac_hh_col].sum().to_dict()
        # Per-Kreis PERSON totals (sum over the 18 age-x-sex 100m band columns) are needed
        # only for PERSON-level entries (e.g. trip_class), so compute them LAZILY the first
        # time such an entry is seen (fail-fast on a missing band column; no silent fallback).
        _kac_persons_by_kreis = None
        # Age-restricted PERSON totals (min_age is not None, e.g. employment_status: 14+)
        # use the single-year age columns instead (person_total_by_kreis_min_age) and are
        # cached PER min_age value, so two entries sharing the same min_age reuse one
        # computation while a different min_age recomputes correctly (no cross-entry reuse
        # of the wrong universe -- the #97 universe trap this whole field exists to avoid).
        _kac_persons_by_kreis_min_age: dict = {}
        # The crosswalk Kreise the per-Kreis control totals are built over; each active
        # target CSV must cover them (load_kreis_target fail-fasts on a missing Kreis row).
        _kac_expected_ars5 = sorted(_kac_hh_by_kreis)
        # The committed blended targets (target2026_*) store shares rounded to 4 decimals, so a
        # row can sum to 0.9999 / 1.0001 (max observed deviation 1e-4). Accept that rounding via
        # a 1e-3 share tolerance (still catches a genuinely mis-normalised row, e.g. 0.9 / 1.1);
        # the per-Kreis counts are renormalised + integer-partitioned downstream regardless.
        _kac_share_tol = 1e-3
        for _ctl in active_entries:
            # economic_status keeps the configurable Dirichlet shrinkage prior (status_prior_n);
            # the S1c blended targets are FINAL (CONSUMER NOTE in the CSV headers) -> prior_n = 0.
            _entry_prior_n = status_prior_n if _ctl.name == "economic_status" else 0.0
            _tgt = _kac.load_kreis_target(
                _kac_data_path, _ctl, expected_ars5=_kac_expected_ars5,
                share_tolerance=_kac_share_tol)
            # Per-entry total by level: household entries partition the per-Kreis household
            # total; person entries (e.g. trip_class) partition the per-Kreis PERSON total
            # (sum of the 18 age-x-sex 100m band columns), so the category targets partition
            # EXACTLY the population PopulationSim controls per Kreis -> IPU-consistent.
            # Entries with min_age set (e.g. employment_status: 14+, feature #172 task 4)
            # instead partition the min_age-restricted PERSON total (single-year age
            # columns) -- using the ALL-ages total here would let <min_age persons distort
            # the category counts (the #97 universe trap).
            if _ctl.level == "person":
                _entry_min_age = getattr(_ctl, "min_age", None)
                if _entry_min_age is not None:
                    if _entry_min_age not in _kac_persons_by_kreis_min_age:
                        _kac_persons_by_kreis_min_age[_entry_min_age] = person_total_by_kreis_min_age(
                            cells, _kac_kreis, _entry_min_age)
                    _total_by_kreis = _kac_persons_by_kreis_min_age[_entry_min_age]
                    _total_label = f"persons (age>={_entry_min_age})"
                else:
                    if _kac_persons_by_kreis is None:
                        _kac_persons_by_kreis = person_total_by_kreis(cells, _kac_kreis)
                    _total_by_kreis = _kac_persons_by_kreis
                    _total_label = "persons"
            else:
                _total_by_kreis = _kac_hh_by_kreis
                _total_label = "households"
            _tbl = _kac.attribute_kreis_count_table(
                _ctl, _tgt, _total_by_kreis, prior_n=_entry_prior_n)
            _map = _kreis_controls_map(_cs_kac.attribute_kreis_controls([_ctl]))
            # Household-level entries (economic_status, number_of_cars, number_of_bicycles,
            # has_ebike) are apportioned across batches by the household share (issue #148);
            # person-level entries (e.g. trip_class) keep the population share.
            if _ctl.level == "household":
                household_control_names.update(_map.keys())
            logger.info(
                "[popsim.stage] KREIS attribute control ON: %s (%s, %s-level), %d Kreise, "
                "prior_n=%.1f, total %s=%d", _ctl.name, _ctl.tier, _ctl.level, len(_tbl),
                _entry_prior_n, _total_label, int(sum(_total_by_kreis.values())))
            if kreis_table is None:
                kreis_table = _tbl
                kreis_controls_map = dict(_map)
            else:
                kreis_table = kreis_table.merge(
                    _tbl, on="ARS_kreis", how="left", validate="one_to_one")
                kreis_controls_map = {**kreis_controls_map, **_map}
                # Fail-fast: every Kreis the run actually uses (_kac_expected_ars5 = the
                # cells' Kreise, which is exactly what build_kreis_control_totals looks up
                # via the crosswalk) must carry a non-NaN target for this control after the
                # LEFT merge (no silently under-constrained control). We mask to those
                # Kreise defensively: the tier3 kreis-control table is now restricted to the
                # run's Kreise at load time (issue #147), so national reference rows no longer
                # enter the accumulator, but masking keeps the guard correct even if a future
                # caller reintroduces out-of-scope rows (whose NaN would be harmless -- they
                # are never looked up downstream).
                _new_cols = list(_kac.control_columns(_ctl))
                _relevant = kreis_table["ARS_kreis"].astype(str).isin(_kac_expected_ars5)
                _relevant_na = _relevant & kreis_table[_new_cols].isna().any(axis=1)
                if _relevant_na.any():
                    _bad = kreis_table.loc[_relevant_na, "ARS_kreis"].tolist()
                    raise RuntimeError(
                        f"KREIS attribute control merge left NaN targets for {_ctl.name} at "
                        f"ARS_kreis {_bad} (missing from this control's target; refusing to "
                        f"under-constrain a Kreis the run synthesises).")
    return kreis_table, kreis_controls_map


def _purge_stale_batches_for_changed_config(controls_df: pd.DataFrame, settings_path, max_cells: int,
        stratify_regiostar: bool, source_name: str, employment_grid_on: bool,
        kreis_controls_map, seed_day_filter, seed_households: pd.DataFrame,
        seed_persons: pd.DataFrame, kreis_table, active_entries,
        status_prior_n: float, work_dir) -> None:
    """Purge work-dir batch folders whose batch inputs changed since the last run.

    Purge stale batch folders if the popsim config/control set changed since the last
    run that used this work_dir (the work_dir persists outside synpp's stage cache, so
    a config change would otherwise leave old completion markers that the batch runner
    skips -> stale-config population for those cells). Signature = everything that
    determines a batch's inputs (the full control set, the PopulationSim settings, the
    batching/stratification, the donor source, the KREIS controls, the seed-day filter).

    Returns: nothing.
    Mutates: DELETES stale ``batch_*`` folders under ``work_dir`` and rewrites
    the config-signature file there.
    """
    _config_signature = compute_batch_config_signature(
        controls_df=controls_df,
        settings_text=Path(settings_path).read_text(encoding="utf-8"),
        max_cells=max_cells,
        stratify_regiostar=stratify_regiostar,
        source_name=source_name,
        employment_grid_on=employment_grid_on,
        kreis_controls_map=kreis_controls_map,
        seed_day_filter=seed_day_filter,
        seed_households=seed_households,
        seed_persons=seed_persons,
        kreis_table=kreis_table,
        active_entries=active_entries,
        status_prior_n=status_prior_n,
    )
    purge_stale_batches_on_config_change(work_dir, _config_signature)


def _run_populationsim_batches(context, cells: pd.DataFrame, base_cols, controls_df: pd.DataFrame,
        seed_households: pd.DataFrame, seed_persons: pd.DataFrame, work_dir,
        settings_path, logging_path, max_cells: int, run_one, num_workers: int,
        source, stratify_regiostar: bool, kreis_table, kreis_controls_map,
        household_control_names: set):
    """Run PopulationSim per 1 km batch and merge the expanded households.

    Returns: ``merge_report`` (its ``combined`` frame is the merged expanded
    household table).
    Mutates: writes the per-batch PopulationSim folders under ``work_dir`` and
    reports the merge counts via ``context.set_info``.
    """
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
        kreis_table=kreis_table,
        kreis_controls_map=kreis_controls_map,
        household_control_names=household_control_names,
    )
    context.set_info("popsim_n_households", merge_report.n_rows)
    context.set_info("popsim_n_cells", merge_report.n_cells)
    context.set_info("popsim_n_missing_batches", merge_report.n_missing)
    return merge_report


def _log_integerizer_feasibility(context, work_dir) -> None:
    """Aggregate and log the PopulationSim integerizer feasibility.

    Surface the PopulationSim integerizer feasibility (no-silent-fallback): some
    zones return INFEASIBLE and fall back to smart-rounded weights inside
    PopulationSim. That is otherwise buried in the per-batch logs; aggregate and
    log it here (WARNING above INTEGERIZER_INFEASIBLE_WARN_RATE). A high rate is a
    quality signal (control set over-constrained for small cells -- common at low
    sampling rates), not a hard failure: a smart-rounded population is still produced.

    Returns: nothing.
    Mutates: nothing (reads the per-batch PopulationSim logs); reports the
    infeasible rate/count via ``context.set_info``.
    """
    feas = mid.summarize_integerizer_feasibility(work_dir)
    context.set_info("popsim_integerizer_infeasible_rate", feas["infeasible_rate"])
    context.set_info("popsim_integerizer_n_infeasible", feas["n_infeasible"])
    _feas_log = (
        logger.warning
        if feas["infeasible_rate"] > mid.INTEGERIZER_INFEASIBLE_WARN_RATE
        else logger.info
    )
    _feas_log(
        "[popsim.stage] PopulationSim integerizer: %d/%d zones OPTIMAL (%.1f%%), "
        "%d INFEASIBLE -> smart-rounded (%.1f%%), %d simul-retry-failed, across %d "
        "batch log(s). A high INFEASIBLE rate means the control set is "
        "over-constrained for small cells (expected to shrink at higher sampling "
        "rates where cells hold more households).",
        feas["n_optimal"], feas["n_total"],
        100.0 * (1.0 - feas["infeasible_rate"]),
        feas["n_infeasible"], 100.0 * feas["infeasible_rate"],
        feas["n_simul_retry_failed"], feas["n_logs"],
    )


def _load_inkar_income_scale(context):
    """Load the per-Kreis INKAR income scale from the synpp DAG.

    Load the per-Kreis INKAR income scale (registered in configure).
    Used by assembly.build_persons to scale household_income_eur and set
    high_income with the unified numeric rule (>= 5000 EUR) for both sources.
    Hoisted above the cell-attribute join so the placement_income reallocation
    below can also consume it (build_persons still receives it further down).

    Returns: ``inkar_income``.
    Mutates: nothing.
    """
    inkar_income = context.stage("inkar_income")
    return inkar_income


def _join_cell_attributes_onto_merged_output(merge_report, cells: pd.DataFrame) -> pd.DataFrame:
    """Join the per-cell attributes onto the merged PopulationSim output.

    Join the per-cell attributes (12-digit ARS + RegioStaR7 when available)
    from the cells frame back onto the merged PopulationSim output: the ARS
    feeds assembly.derive_zone_ids (commune/departement/iris ids, bug D1) and
    the cell RS7 becomes the spatial stage-B chain-matching key on every
    expanded synthetic person (see join_cell_attributes).

    Returns: ``combined`` -- the merged household table with the per-cell
    attributes joined on.
    Mutates: nothing.
    """
    combined = join_cell_attributes(merge_report.combined, cells)
    return combined


def _load_donor_tables(context, source, source_name: str, mid_dir, complete_members: bool,
        completed_donor_households, completed_donor_persons):
    """Load the donor attribute tables through the active source adapter.

    For source="mid": MidSource.load_donor reads from mid_dir (byte-identical).
    For source="entd": EntdSource.load_donor receives the frames injected from
    the data.hts.selected stage (no filesystem read).
    Loaded here, directly above the placement_income reallocation, because that
    block needs the donor income + household size; it has no dependency on the
    cell-attribute join above (donor loading and the join are independent).

    Returns: ``(donor_households, donor_persons)``.
    Mutates: nothing.
    """
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
    return donor_households, donor_persons


def _resolve_placement_income_flag(context, source_name: str) -> bool:
    """Resolve whether the placement_income donor reallocation is active.

    --- placement_income (L2, issue #108): signature-preserving donor reallocation --
    Runs BEFORE expansion so every downstream attribute/trip join follows the donor.
    Permutes WHICH donor sits in which Kreis inside exact control-signature groups, so
    every PopulationSim control aggregate and every donor's clone count are preserved
    while the per-Kreis income mean is pushed toward the construct-corrected INKAR
    relativity. MiD-only (needs the hheink_gr1 donor income). OFF -> combined unchanged.

    Returns: ``placement_income_on``.
    Mutates: nothing.
    """
    _placement_flag = bool(context.config(KEY_PLACEMENT_INCOME))
    placement_income_on = _placement_flag and source_name == "mid"
    if _placement_flag and source_name != "mid":
        logger.info("[popsim.stage] placement_income requested but source=%s carries no "
                    "hheink_gr1 donor income; feature inactive for this source.", source_name)
    return placement_income_on


def _expand_donor_households_to_persons(context, combined: pd.DataFrame, donor_households: pd.DataFrame,
        donor_persons: pd.DataFrame, rng, source, source_name: str, inkar_income,
        placement_income_on: bool):
    """Expand the merged donor households into the full eqasim persons frame.

    pseudonymise=True (MiD): replace raw H_ID/P_ID with sequential surrogates
    (data-protection requirement for the restricted MiD scientific-use licence).
    pseudonymise=False (ENTD): source_* are set directly to the open ENTD ids
    by EntdSource.map_person_attributes; no surrogate mapping is needed.

    Returns: ``(persons, pseudonym_map, pseudonymise, income_path, _pi_path)``.
    The last element is the ``placement_income`` module alias bound by this
    block's function-local import; it is returned (rather than re-imported)
    because the caller's own placement block below still calls
    ``_pi_path.apply_own_income`` on it, and re-importing would add a statement
    the original does not have.
    Mutates: nothing in place; draws from ``rng`` inside
    ``assembly.build_persons`` (the only RNG use in this step) and reports the
    person count via ``context.set_info``.
    """
    pseudonymise = (source_name == "mid")
    # When income_kreis_control is ON it OVERWRITES household_income_eur with a fresh
    # per-Kreis continuous draw further below, so the INKAR midpoint scaling inside
    # build_persons would be redundant -- tell build_persons to skip it (no-op on the
    # final output; see assembly.build_persons skip_inkar_income_scale).
    income_kreis_control_on = bool(context.config(KEY_INCOME_KC))
    _income_tilt_flag = bool(context.config(KEY_INCOME_TILT))
    # placement_income (issue #108) resolves the mutually exclusive income mechanisms
    # into one explicit decision: ON keeps each donor's OWN income and SKIPS both the
    # per-Kreis redraw and the spatial tilt (each override logged inside
    # resolve_income_path); OFF reproduces the legacy booleans (redraw / tilt /
    # skip_inkar_scale) bit-for-bit, so the OFF path is byte-identical to before.
    from braunschweig.popsim import placement_income as _pi_path
    income_path = _pi_path.resolve_income_path(
        placement_income_on, income_kreis_control_on, _income_tilt_flag)
    persons, pseudonym_map = assembly.build_persons(
        combined, donor_households, donor_persons,
        rng=rng,
        attribute_mapper=source.map_person_attributes,
        pseudonymise=pseudonymise,
        inkar_scale=inkar_income,
        skip_inkar_income_scale=income_path["skip_inkar_scale"],
    )
    context.set_info("popsim_n_persons", len(persons))
    return persons, pseudonym_map, pseudonymise, income_path, _pi_path


def _apply_housing_tenure_parity(context, persons: pd.DataFrame, random_seed: int) -> pd.DataFrame:
    """Apply the enriched-path housing_tenure sampler as a FALLBACK (parity P2).

    housing_tenure parity (P2): main wired the enriched-path tenure sampler
    (braunschweig.synthesis.population.enriched._apply_housing_tenure, categories
    rent/own/other, RNG offset +83947) into the popsim stage. On THIS branch the
    popsim build_persons ALREADY derives ``housing_tenure`` directly from the MiD
    donor flag H_MIETE via attributes.map_housing_tenure (categories
    owner/renter/unknown) -- that donor-derived column is the AUTHORITATIVE tenure
    source consumed by (a) the Tier-2 tenure CONTROL catalog (control_spec, which
    matches owner/renter) and (b) the spatial income tilt below (which routes on
    tenure == "owner" / "renter"). Letting _apply_housing_tenure run
    unconditionally would OVERWRITE owner/renter/unknown with rent/own/other,
    silently turning the income tilt into a no-op (no row would equal "owner"/
    "renter") and changing the control-aligned attribute vocabulary. We therefore
    keep main's mechanism only as a FALLBACK: run it solely when build_persons did
    NOT already provide housing_tenure (e.g. the ENTD path, where H_MIETE is
    absent). When the donor column is present (MiD path) it is preserved verbatim.

    Returns: the persons frame (rebound when the fallback runs, so the caller
    MUST reassign); unchanged on the MiD path, where the donor-derived
    ``housing_tenure`` column is already present.
    Mutates: nothing in place.
    """
    if context.config("synthesise_housing_tenure") and "housing_tenure" not in persons.columns:
        from braunschweig.data.mid.tenure_by_income import (
            load_tenure_by_income_bundesland,
            load_tenure_by_income_raumtyp,
        )
        from braunschweig.synthesis.population.enriched import _apply_housing_tenure

        data_path = context.config("data_path")
        persons = _apply_housing_tenure(
            persons,
            load_tenure_by_income_bundesland(data_path),
            load_tenure_by_income_raumtyp(data_path),
            context.stage("regiostar_tenure"),
            random_seed,
        )
    return persons


def _apply_kreis_income_control(context, persons: pd.DataFrame, inkar_income, income_path: dict,
        random_seed: int) -> pd.DataFrame:
    """Apply the Kreis-Income-Control redraw (real MiD draw + per-Kreis fit).

    --- Kreis-Income-Control (real MiD draw + max-entropy per-Kreis calibration) ---
    Replaces the build_persons midpoint x INKAR_scale income with a real continuous
    draw reshaped per Kreis to the construct-corrected INKAR target. Runs BEFORE the
    within-Kreis spatial tilt (which is Kreis-mean-preserving and layers on top).

    Returns: the persons frame (rebound when the redraw runs, so the caller
    MUST reassign); unchanged when ``income_path['redraw']`` is False.
    Mutates: sets ``persons.attrs['kreis_income_control_diag']`` in place on
    the rebound frame; draws from the seeded ``random_seed`` stream inside
    ``apply_kreis_income_control``.
    """
    if income_path["redraw"]:
        _kc_data_path = context.config("data_path")
        _kc_scope = [str(p) for p in context.config(KEY_KREISE)]
        _income_tables = {
            "size_bl": load_income_by_size_bundesland(_kc_data_path),
            "size_rt": load_income_by_size_raumtyp(_kc_data_path),
            "status_bl": load_income_by_status_bundesland(_kc_data_path),
            "status_rt": load_income_by_status_raumtyp(_kc_data_path),
        }
        _kreis_stats = kreis_household_stats(
            os.path.join(_kc_data_path,
                         context.config("braunschweig.zensus_households_path")),
            _kc_scope,
        )
        persons, _kc_diag = _kic.apply_kreis_income_control(
            persons,
            inkar_df=inkar_income,
            kreis_stats_df=_kreis_stats,
            income_tables=_income_tables,
            enabled=True,
            method=str(context.config(KEY_INCOME_KC_METHOD)),
            hhsize_correct=bool(context.config(KEY_INCOME_KC_HHSIZE)),
            open_top_pareto=bool(context.config(KEY_INCOME_KC_PARETO)),
            pareto_alpha=float(context.config(KEY_INCOME_KC_PARETO_ALPHA)),
            random_seed=random_seed,
        )
        persons.attrs["kreis_income_control_diag"] = {
            "region_mean": float(_kc_diag["region_mean"]),
            "pmf_fallback_rate": float(_kc_diag["pmf_fallback_rate"]),
            "kreis_realized_mean": {k: float(v) for k, v in _kc_diag["kreis_realized_mean"].items()},
            "kreis_target_factor": {k: float(v) for k, v in _kc_diag["kreis_target_factor"].items()},
            "any_clamped": bool(any(_kc_diag["kreis_clamped"].values())),
        }
        logger.info(
            "[popsim.stage] Kreis-Income-Control applied (method=%s, hhsize_correct=%s); "
            "household_income_eur + label + high_income re-derived from the real draw. "
            "Realized per-Kreis means: %s",
            context.config(KEY_INCOME_KC_METHOD), context.config(KEY_INCOME_KC_HHSIZE),
            persons.attrs["kreis_income_control_diag"]["kreis_realized_mean"],
        )
    return persons


def _apply_spatial_income_tilt(context, persons: pd.DataFrame, cells: pd.DataFrame, income_path: dict) -> pd.DataFrame:
    """Apply the within-Kreis spatial income tilt (Nettokaltmiete GAMMA layer).

    --- Spatial income tilt (Nettokaltmiete GAMMA layer, Task 3) ---------------
    Applies a within-Kreis income redistribution guided by per-cell net cold rent
    (renters) and Eigentümerquote (owners), preserving each Kreis's income mean
    exactly.  Controlled by KEY_INCOME_TILT (default ON per project rule), unless
    placement_income is active -- resolve_income_path then forces income_path["tilt"]
    False (the tilt would rescale the donor's own income). When OFF, the income frame
    is byte-identical.

    Returns: the persons frame (rebound when the tilt is applied, so the caller
    MUST reassign); unchanged when the tilt is off or shielded.
    Mutates: sets ``persons['high_income']`` and
    ``persons.attrs['income_tilt_diag']`` in place on the rebound frame.
    BOTH original ``if income_tilt_enabled`` blocks live in this one step
    deliberately: the first block can DISABLE the tilt mid-section (missing ARS
    column) and binds ``_tilt_cells`` only when enabled, so splitting them
    would need an initialiser statement the original does not have.
    """
    income_tilt_enabled = income_path["tilt"]
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

        # Build the tilt working frame (rent + eigentuemerquote + HH weight +
        # ARS) from the already-loaded, already-ZGB-filtered cells frame. The
        # tilt columns were fetched by the single load_control_cells read via
        # tilt_extra_load_columns (issue #136) -- no second parquet scan.
        # Columns absent from the parquet are simply absent here; the checks
        # below then warn and fall back to a neutral index / uniform weight.
        _tilt_cells = extract_tilt_cells(cells)

        # Derive 5-digit Kreis ARS from the 12-digit ARS column.
        if _TILT_ARS_COL in _tilt_cells.columns:
            _tilt_cells["_ars5"] = derive_geo_kreis_from_ars(_tilt_cells[_TILT_ARS_COL])
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
    return persons


def _add_derived_income_views(persons: pd.DataFrame) -> pd.DataFrame:
    """Add the per-capita and equivalised income views.

    Per-capita income view alongside the per-household household_income_eur.
    Computed on the FINAL income (after Kreis-Income-Control + spatial tilt) so both
    the per-household construct (household_income_eur) and the per-capita construct
    (≈ INKAR income-je-Einwohner ordering) are available downstream.

    Returns: the persons frame with the additive income-view columns (MUST be
    reassigned).
    Mutates: nothing in place.
    """
    if {"household_income_eur", "household_size"}.issubset(persons.columns):
        persons = _kic.add_per_capita_income(persons)

    # Equivalised income view (issue #130): FINAL household_income_eur divided by
    # the OECD-modified consumption_units set in assembly.build_persons. Additive
    # column only -- high_income deliberately keeps the household-level 5000 EUR
    # rule (no traceable per-consumption-unit threshold reference exists; no
    # invented references).
    if {"household_income_eur", "consumption_units"}.issubset(persons.columns):
        persons = _income.add_income_per_consumption_unit(persons)
    return persons


def _write_pseudonym_map(work_dir, pseudonym_map: pd.DataFrame, pseudonymise: bool) -> None:
    """Write the local-only pseudonym map into the work dir.

    Write the local-only pseudonym map for MiD so internal re-linking is possible.
    This file maps each surrogate source_person_id / source_household_id back
    to the raw MiD H_ID / P_ID.  It MUST NOT be committed or published; it
    lives in the pipeline work_dir which is a local-only, gitignored path.
    For ENTD (pseudonymise=False) the map is empty (no surrogates were assigned)
    but is still written for consistency.

    Returns: nothing.
    Mutates: WRITES ``pseudonym_map.csv`` into ``work_dir``.
    """
    pseudonym_map_path = Path(work_dir) / "pseudonym_map.csv"
    pseudonym_map.to_csv(pseudonym_map_path, index=False)
    logger.info(
        "[popsim.stage] Pseudonym map written to %s (%d unique donor persons; "
        "pseudonymise=%s).",
        pseudonym_map_path, len(pseudonym_map), pseudonymise,
    )


def _attach_joint_plausibility_report(persons: pd.DataFrame) -> None:
    """Run the joint (cross-attribute) plausibility invariants on the final frame.

    Joint (cross-attribute) plausibility invariants (issue #133): run LAST so
    every attribute overwrite above (income control, tilt, tenure parity) is
    covered. WARN-only (measure-before-harden, like the minor-employment
    guard); the report is attached to persons.attrs so it survives the synpp
    cache and can feed a validation summary without re-running the stage.

    Returns: nothing.
    Mutates: sets ``persons.attrs['joint_plausibility']`` IN PLACE (the frame
    itself is not rebound, so the caller keeps using its own reference).
    """
    persons.attrs["joint_plausibility"] = _plausibility.check_joint_plausibility(persons)


def execute(context) -> pd.DataFrame:
    """Run popsim_mid and return the merged expanded-household table."""
    (
        cells_path, mid_dir, controls_path, settings_path, logging_path,
        popsimprep_dir, uv_path,
    ) = _read_stage_paths(context)
    (
        max_cells, num_workers, work_dir, kreise, source_name, stratify_regiostar,
        complete_members,
    ) = _read_batching_and_scope_config(context)
    random_seed, rng, kreis_seed_rng = _create_seeded_rngs(context)

    source = _resolve_source(source_name)
    logger.info("[popsim.stage] active donor source: %s", source.name)

    (
        control_tiers, seed_day_filter, controls_source, employment_grid_on,
        active_entries, active_entry_names, status_prior_n, ebike_seed_column_cfg,
        importance_profile,
    ) = _read_control_config(context, source_name)
    controls_df, base_cols = _build_control_frame(
        controls_source, controls_path, source_name, control_tiers,
        employment_grid_on, active_entry_names, importance_profile,
    )
    kreis_table, kreis_controls_map, household_control_names = _load_tier3_kreis_controls(
        context, control_tiers, controls_source, source_name, kreise,
    )
    load_cols = _resolve_cell_load_columns(
        context, controls_source, source_name, control_tiers, base_cols,
        employment_grid_on, cells_path,
    )

    cells = mid.load_control_cells(cells_path, load_cols)
    cells = mid.filter_zgb_cells(cells, kreise)
    cells = _add_aggregated_control_columns(
        cells, controls_source, source_name, control_tiers,
    )
    cells = _inject_employment_grid_columns(context, cells, employment_grid_on)

    (
        completed_donor_households, completed_donor_persons, seed_households,
        seed_persons,
    ) = _build_populationsim_seed(
        context, source, source_name, mid_dir, complete_members, seed_day_filter,
        active_entries, kreis_seed_rng, ebike_seed_column_cfg,
    )

    run_one = _prepare_batch_runner(
        context, uv_path, popsimprep_dir, stratify_regiostar,
    )
    kreis_table, kreis_controls_map = _derive_kreis_attribute_control_targets(
        context, cells, active_entries, status_prior_n, kreis_table,
        kreis_controls_map, household_control_names,
    )
    _purge_stale_batches_for_changed_config(
        controls_df, settings_path, max_cells, stratify_regiostar, source_name,
        employment_grid_on, kreis_controls_map, seed_day_filter, seed_households,
        seed_persons, kreis_table, active_entries, status_prior_n, work_dir,
    )
    merge_report = _run_populationsim_batches(
        context, cells, base_cols, controls_df, seed_households, seed_persons,
        work_dir, settings_path, logging_path, max_cells, run_one, num_workers,
        source, stratify_regiostar, kreis_table, kreis_controls_map,
        household_control_names,
    )
    _log_integerizer_feasibility(context, work_dir)
    inkar_income = _load_inkar_income_scale(context)
    combined = _join_cell_attributes_onto_merged_output(merge_report, cells)
    donor_households, donor_persons = _load_donor_tables(
        context, source, source_name, mid_dir, complete_members,
        completed_donor_households, completed_donor_persons,
    )
    placement_income_on = _resolve_placement_income_flag(context, source_name)

    # The donor reallocation stays INLINE: it binds ``_pi_diag`` only when
    # placement is active, and that diagnostic is consumed by the own-income
    # block further down (behind ``income_path["placement"]``, the same
    # condition). Extracting it would need either an initialiser statement the
    # original does not have or a call nested inside this guard, so the block is
    # left where it was rather than risking the call/RNG order.
    if placement_income_on:
        from braunschweig.popsim import placement_income as _pi
        from braunschweig.popsim import control_spec as _cs_pi
        _pi.check_controls_source_compatible(placement_income_on, controls_source)
        _pi_catalog = _cs_pi.full_catalog(
            include_tiers=control_tiers,
            include_employment_grid=employment_grid_on,
            kreis_control_names=active_entry_names,
        )
        _pi_controls = _cs_pi.controls_for_seed(_pi_catalog, source_name)
        _signatures = _pi.donor_control_signatures(
            _pi_controls, seed_households, seed_persons, seed=source_name)
        _expected = _pi.donor_expected_income_eur(donor_households)
        _ars5 = derive_geo_kreis_from_ars(combined[mid._ARS_COLUMN])
        _slots = pd.DataFrame({"H_ID": combined["H_ID"].to_numpy(), "ars5": _ars5.to_numpy()},
                              index=combined.index)
        _stats = _pi.slots_kreis_stats(_slots, donor_households)
        _rf = _kic.build_kreis_income_targets(
            inkar_income, _stats, sorted(_slots["ars5"].unique()), hhsize_correct=True)
        _assignment, _pi_diag = _pi.reallocate_slots(
            _slots, signatures=_signatures, expected_income_eur=_expected, target_factor=_rf)
        combined = combined.assign(H_ID=_assignment.to_numpy())
        # Traceable per-run diagnostic (research-reporting rule): one row per Kreis.
        # "converged" (in _pi_diag) refers ONLY to the continuous lambda solve; the
        # achieved per-Kreis fit is realized_after vs target_mean (never call this
        # "calibrated to INKAR" -- convergence is not validation).
        _sorted_kreise = sorted(_pi_diag["kreis_target_mean"])
        _diag_rows = pd.DataFrame({
            "ars5": _sorted_kreise,
            "target_mean_eur": [_pi_diag["kreis_target_mean"][k] for k in _sorted_kreise],
            "realized_before_eur": [_pi_diag["kreis_realized_before"].get(k) for k in _sorted_kreise],
            "realized_after_eur": [_pi_diag["kreis_realized_after"].get(k) for k in _sorted_kreise],
            "lambda": [_pi_diag["kreis_lambda"][k] for k in _sorted_kreise],
            "clamped": [_pi_diag["kreis_clamped"][k] for k in _sorted_kreise],
        })
        _diag_rows.to_csv(Path(work_dir) / "placement_income_diag.csv", index=False)
        _residuals = [
            abs(_pi_diag["kreis_realized_after"].get(k, float("nan")) - v) / max(_pi_diag["region_mean"], 1.0)
            for k, v in _pi_diag["kreis_target_mean"].items()
        ]
        _finite = [r for r in _residuals if not np.isnan(r)]
        _worst = max(_finite) if _finite else float("nan")
        context.set_info("placement_income_moved_share", _pi_diag["moved_share"])
        context.set_info("placement_income_no_freedom_share", _pi_diag["no_freedom_slot_share"])
        context.set_info("placement_income_worst_residual_pct", 100.0 * _worst)

    (
        persons, pseudonym_map, pseudonymise, income_path, _pi_path,
    ) = _expand_donor_households_to_persons(
        context, combined, donor_households, donor_persons, rng, source,
        source_name, inkar_income, placement_income_on,
    )
    persons = _apply_housing_tenure_parity(context, persons, random_seed)

    # --- placement_income (L2, issue #108): keep each donor's OWN income ------------
    # When placement is active, household_income_eur becomes a seeded continuous draw
    # WITHIN each household's own MiD hheink_gr1 bracket (one draw per household); the
    # per-Kreis relativity was already imposed by the donor reallocation above, so the
    # redraw and the spatial tilt below are both skipped (see resolve_income_path).
    # _pi_diag is defined iff placement is active (same condition as income_path
    # ["placement"], which resolve_income_path returns True only when placement_income_on).
    if income_path["placement"]:
        persons, _own_diag = _pi_path.apply_own_income(persons, random_seed=random_seed)
        persons.attrs["placement_income_diag"] = {**_pi_diag, **_own_diag}

    persons = _apply_kreis_income_control(
        context, persons, inkar_income, income_path, random_seed,
    )
    persons = _apply_spatial_income_tilt(context, persons, cells, income_path)
    persons = _add_derived_income_views(persons)
    _write_pseudonym_map(work_dir, pseudonym_map, pseudonymise)
    _attach_joint_plausibility_report(persons)

    return persons
