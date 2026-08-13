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
whole package) is module 3's job (``popsim/stage.py``, issue #267). This
``batches`` extraction is the last of the #267 split: every remaining piece of
implementation has moved out, and this file is now a pure facade holding only
the docstring, imports, the ``MID_SEED_COLUMNS`` alias, the sibling imports,
and the re-export blocks below. Submodules extracted:

    batches        Batch folder assembly and the PopulationSim runner: builds
                   one PopulationSim run folder per batch (with optional Tier-3
                   KREIS controls), groups 1 km parents into batches, executes
                   and merges them, and reports LP-integerizer feasibility and
                   the missing-batch rate (``assemble_batch_folder``,
                   ``cell_groups``, ``run_popsim_mid``,
                   ``summarize_integerizer_feasibility``,
                   ``MAX_MISSING_BATCH_RATE``,
                   ``INTEGERIZER_INFEASIBLE_WARN_RATE``)
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
    donor_stratification
                   RegioStaR donor stratification (Phase 4B): dominant stratum
                   per 1 km parent by majority vote, and donor-seed filtering to
                   one stratum (``dominant_stratum_for_1km``,
                   ``filter_seed_to_stratum``). Named distinctly from the
                   sibling top-level ``braunschweig.popsim.stratum`` module
                   (Phase-4A stratum-KEY mapping) to avoid an exact-filename
                   collision (issue #267).
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
"""

from __future__ import annotations

from typing import Optional  # noqa: F401  (namespace parity: re-exported for consumers)

from braunschweig.popsim import seed as seedmod

# ---------------------------------------------------------------------------
# Package submodules (extracted stage sections). Every name is re-exported
# here so external consumers (pipeline stages, tests) keep importing from the
# braunschweig.popsim.mid module path unchanged.
# ---------------------------------------------------------------------------

from . import batches
from .batches import (  # noqa: F401  (re-exports)
    INTEGERIZER_INFEASIBLE_WARN_RATE,
    Iterable,
    MAX_MISSING_BATCH_RATE,
    Mapping,
    Path,
    Sequence,
    Union,
    _run_batches_and_merge,
    assemble_batch_folder,
    batch,
    cell_groups,
    control_spec,
    folders,
    logger,
    logging,
    mergemod,
    pd,
    run_popsim_mid,
    summarize_integerizer_feasibility,
)

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

from . import donor_stratification
from .donor_stratification import (  # noqa: F401  (re-exports)
    dominant_stratum_for_1km,
    filter_seed_to_stratum,
)

from . import kreis_controls
from .kreis_controls import (  # noqa: F401  (re-exports)
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

# Re-exported for convenience: callers that already import braunschweig.popsim.mid
# can access the canonical MiD seed column mapping without a separate import of
# braunschweig.popsim.seed.  The authoritative definition remains in seed.py.
MID_SEED_COLUMNS = seedmod.MID_SEED_COLUMNS
