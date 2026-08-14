"""Gravity model for Braunschweig - base + Kreis-level calibration.

This module is the merged successor of:
- ``bavaria/gravity/model.py`` (Origin: eqasim-bavaria @ b20fbe6) - the
  Gemeinde x Gemeinde gravity model with IDF-derived parameters.
- ``braunschweig/gravity/model.py`` - the BA Pendleratlas IPF calibration
  layer that scales the Gemeinde flows so Kreis aggregates match observed
  SvB-Pendlerstroeme.

Phase 2.11 of the eqasim-bs refactor merged both into a single module so
the BS pipeline no longer delegates through ``braunschweig.gravity.model``. The
behaviour is unchanged.

Output schema is identical to ``braunschweig.gravity.model``::

    origin_id          str   commune_id (8-digit AGS)
    destination_id     str
    weight             float row-normalised P(destination | origin)

Returned as ``(df_work_od, df_education_od)`` tuple. Education uses the
uncalibrated gravity result (no equivalent observed data).

Module layout (issue #267 split): this module remains the synpp stage
(``configure``/``execute``/``validate``) and the import path
``braunschweig.gravity.model`` for every consumer. Sections extracted from it
live in SIBLING modules of the ``braunschweig.gravity`` package (alongside the
pre-existing ``friction``, ``production_mass``, ``taz_margins``,
``verbindungen_anchor`` and ``distance_matrix_taz``) and every module-level
name they define -- except each sibling's own ``logger`` object, where one
exists (``od`` and ``kreis_calibration`` bind it to the literal
``"braunschweig.gravity.model"`` name rather than defining a distinct logger;
see the re-export block below) -- is re-exported here, so external imports keep
working unchanged. Siblings extracted so far:

    attraction_vector   the gravity destination attraction vector: the
                        employees-at-workplace headcount and the flag-gated
                        sector-aware establishment-density tilt
    balancing           the doubly-constrained Furness/IPF balancing loop
                        (``evaluate_gravity``) and the per-origin RegioStaR-7
                        friction-slope override resolution
    od                  the pure work-OD gravity computation
                        (``compute_work_od``) and the BA Gemeindedaten
                        establishment-count reader it uses via the
                        sector-aware attraction path

    kreis_calibration   the BA-Pendleratlas Kreis-level IPF calibration
                        (``_calibrate``) that scales the gravity OD so Kreis
                        aggregates match observed SvB-Pendlerstroeme, plus the
                        zone-to-Kreis mapping (``_zone_to_kreis``), the
                        intra-Kreis flow synthesis (``_synthesise_intra_kreis``)
                        and the outbound-flow injection to external Kreise
                        (``_append_outbound_flows``)
    base                the inherited eqasim-bavaria base gravity execution
                        (``_execute_gravity_base``): the Gemeinde (and
                        optionally TAZ) gravity pass(es) that produce the RAW
                        work/education OD before the Kreis-level calibration
                        above is applied, plus its IDF-derived default friction
                        parameters (``DEFAULT_SLOPE``, ``DEFAULT_CONSTANT``,
                        ``DEFAULT_DIAGONAL``)

With this sibling's extraction, this module now holds only its docstring,
imports, re-export blocks, the module ``logger``, the
``_HELPER_MODULES``/``validate()`` cache-token machinery, and the
``configure``/``execute`` synpp hooks themselves.

Because this module is a synpp STAGE, ``validate()`` below folds every sibling
source into the stage's cache-validation token -- see the comment on
``_HELPER_MODULES``.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import os  # noqa: F401  (unused here; kept for facade namespace parity, see below)

import numpy as np  # noqa: F401  (unused here; kept for facade namespace parity, see below)
import pandas as pd  # noqa: F401  (unused here; kept for facade namespace parity, see below)

from braunschweig.data.bbsr.regiostar import ars_to_ags8  # noqa: F401  (namespace parity, see below)
from braunschweig.gravity import attraction_vector, balancing, base, kreis_calibration, od
from braunschweig.gravity.friction import build_friction_matrix  # noqa: F401  (namespace parity, see below)

# ``os``, ``np``, ``pd``, ``ars_to_ags8`` and ``build_friction_matrix`` above are
# no longer used by this module's own code (issue #267 split): ``os``,
# ``ars_to_ags8`` and ``build_friction_matrix`` had a single call site each,
# formerly ``_read_betriebe_per_commune``/``compute_work_od``, which moved
# verbatim to ``braunschweig.gravity.od``; ``np`` and ``pd`` had MANY call
# sites and moved to every computational sibling (``attraction_vector``,
# ``balancing``, ``od`` and ``kreis_calibration`` each import their own
# ``numpy``/``pandas`` -- only the orchestration-only ``base`` sibling needs
# neither). Every sibling that needs one of these five names now imports it
# directly for its own use. They are kept bound here anyway so
# ``dir(braunschweig.gravity.model)`` still contains every name it did before
# the split (the namespace-parity contract this stage's split is held to --
# see ``.superpowers/sdd/2026-08-14-split-gravity-model/check_namespace.py``);
# removing them would silently break any external code doing
# ``from braunschweig.gravity.model import ars_to_ags8`` (etc.), however unlikely.

# ---------------------------------------------------------------------------
# Sibling modules extracted from this stage. Every module-level name they
# define is re-exported here so external consumers (the pipeline, calibration
# scripts, tests) keep importing from ``braunschweig.gravity.model`` unchanged
# -- EXCEPT each sibling's own ``logger`` object (``od.logger`` and
# ``kreis_calibration.logger`` are bound to the literal
# ``"braunschweig.gravity.model"`` name, so they resolve to the SAME
# ``logging.Logger`` instance as this module's own ``logger`` below via
# Python's name-keyed logger cache; nothing currently imports a sibling's
# ``logger`` attribute directly, so this is a documented non-goal rather than
# a gap). Each sibling MUST also be listed in _HELPER_MODULES below so its
# source participates in the synpp cache-validation token.
# ---------------------------------------------------------------------------

from braunschweig.gravity.attraction_vector import (  # noqa: F401  (re-exports)
    SECTOR_AWARE_TILT_EXPONENT,
    apply_sector_aware_attraction,
    build_destination_attraction,
)
from braunschweig.gravity.balancing import (  # noqa: F401  (re-exports)
    DEFAULT_GRAVITY_MAX_ITERATIONS,
    ORIGIN_SLOPE_FALLBACK_WARN_THRESHOLD,
    _build_origin_slope_vector,
    evaluate_gravity,
)
from braunschweig.gravity.od import (  # noqa: F401  (re-exports)
    ZERO_TOTAL_SELF_LOOP_WARN_PERCENT,
    _GEMBAND_COLUMN_NAMES,
    _read_betriebe_per_commune,
    compute_work_od,
)
from braunschweig.gravity.kreis_calibration import (  # noqa: F401  (re-exports)
    IPF_TOLERANCE,
    MAX_IPF_ITERATIONS,
    _append_outbound_flows,
    _calibrate,
    _gemeinde_to_kreis,
    _synthesise_intra_kreis,
    _zone_to_kreis,
)
from braunschweig.gravity.base import (  # noqa: F401  (re-exports)
    DEFAULT_CONSTANT,
    DEFAULT_DIAGONAL,
    DEFAULT_SLOPE,
    _execute_gravity_base,
)

# Unused by this module's own code since the #267 split (both former
# ``logger.*`` call sites moved to ``od.py``/``kreis_calibration.py``, which
# bind the identical logger name -- see the re-export block above); kept
# solely for facade namespace parity (the same contract as the ``os``/``np``/
# ``pd``/``ars_to_ags8``/``build_friction_matrix`` bindings above) -- do not
# remove.
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# synpp cache validation
# ---------------------------------------------------------------------------

# The five sibling modules EXTRACTED FROM THIS STAGE by the #267 split. synpp's
# ``get_stage_hash`` hashes only THIS file's source (``inspect.getsource`` of
# the stage module), so code that moved into a sibling left that hash: without
# the ``validate()`` hook below, a change confined to a sibling would silently
# reuse the STALE cached stage output on a partial rerun. Listed explicitly and
# in a deterministic order (never via ``dir()`` or a directory glob) so the
# token depends only on the sources, not on import or filesystem order. Every
# module extracted from this stage MUST be appended here.
#
# This tuple is NOT a complete list of this stage's behaviour dependencies.
# The pre-existing siblings ``friction.py``, ``production_mass.py``,
# ``taz_margins.py`` and ``verbindungen_anchor.py`` (default ON, ADR-0068) are
# also read by ``execute()`` but are not folded into this token -- a known,
# pre-existing gap. The #267 split did not create it (this stage had NO
# ``validate()`` hook at all before the split, so those four were already
# outside the hash) and did not widen it; closing it would mean adding them
# here, which is a behaviour-affecting cache change and belongs in its own,
# separate change, not this one.
_HELPER_MODULES = (attraction_vector, balancing, base, kreis_calibration, od)


def validate(context):
    """synpp validation token: md5 over the sibling modules' sources.

    synpp calls ``validate`` for every stage, stores the returned token
    alongside the cached stage output and devalidates that cache when the token
    changes on a later run, so a sibling-only source change recomputes this
    stage exactly like an edit to this file would.

    This stage had NO ``validate`` hook before the #267 split, so the token is
    new: the first run after this change has no stored token to compare against
    and therefore recomputes this stage and everything downstream of it ONCE.
    Subsequent runs reuse the cache normally.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def configure(context):
    # TAZ work-location gravity branch.  Default False -> the OFF path is
    # byte-identical to the pre-TAZ behaviour (single Gemeinde pass returned
    # for both work and education).  When True a second TAZ-keyed gravity pass
    # is computed for work location choice.
    context.config("taz_work_location_choice", False)

    # Base stages and configs are declared unconditionally so the OFF path
    # needs no new keys (and all existing pipeline configs remain valid).
    context.stage("eqasim_common.gravity.distance_matrix")
    # data.census.filtered resolves to the configured population producer
    # (braunschweig.ipf.attributed in the legacy config -- unchanged behaviour --
    # or braunschweig.popsim.stage in the popsim configs), so the gravity weights
    # always come from the SAME population as the demand.
    context.stage("data.census.filtered")
    context.stage("braunschweig.data.census.employees")
    context.stage("braunschweig.data.bbsr.regiostar")
    context.config("gravity_slope", DEFAULT_SLOPE)
    context.config("gravity_constant", DEFAULT_CONSTANT)
    context.config("gravity_diagonal", DEFAULT_DIAGONAL)
    # Iteration cap for the doubly-constrained balancing (``evaluate_gravity``).
    # The default reproduces the prior magic 1e6 literal, so convergence is
    # reached exactly as before; exposed only so a non-converging run can be
    # bounded explicitly.
    context.config("gravity_max_iterations", DEFAULT_GRAVITY_MAX_ITERATIONS)
    # Optional dict {regiostar7_code: slope}. None/absent = use scalar slope.
    # The default MUST be ``None`` and not ``{}``: synpp's ``flatten()`` drops
    # empty-dict values entirely, so an absent override with a ``{}`` default
    # vanishes from ``required_config`` and ``context.config(...)`` then raises
    # "Config option ... is not requested" at execute time. ``None`` survives
    # flattening and is treated as "no overrides" by ``_build_origin_slope_vector``.
    context.config("gravity_slope_by_regiostar7", None)
    # Optional per-distance-band friction factors. None/absent = legacy
    # exp(slope*d) friction (byte-identical OFF path). {band: f} = global per-band;
    # {rs7: {band: f}} = per-origin-RS7 per-band. Written by
    # scripts/calibrate_gravity_distribution.py; do not hand-edit. Must default to
    # None (not {}) so synpp flatten() does not drop it (see gravity_slope_by_regiostar7).
    context.config("gravity_friction_factors", None)
    context.stage("braunschweig.data.census.pendler")
    context.stage("braunschweig.data.census.employment")
    context.stage("braunschweig.data.external_workplaces")
    context.config("braunschweig.political_prefix")
    # Sector-aware destination attraction (model-improvement item #8). Default
    # False -> the ``employees`` attraction is the legacy headcount and the
    # gravity result is byte-identical to before. When True the per-Gemeinde
    # establishment density (BA Gemeindedaten ``n_betriebe``) tilts the
    # within-Kreis attraction (see ``apply_sector_aware_attraction``).
    context.config("braunschweig.gravity.sector_aware_enabled", False)
    if context.config("braunschweig.gravity.sector_aware_enabled", False):
        # Only declared as required when the flag is on, so the legacy OFF path
        # needs no new config keys or stages.
        context.config("data_path")
        context.config(
            "braunschweig.employment_gemband_path",
            "braunschweig/gemband-dlk-0-202506-xlsx.xlsx",
        )
        context.stage("eqasim_common.spatial.codes")

    # #132: production mass for the WORK gravity ("population" reproduces the
    # legacy behaviour byte-identically; "svb_wohn" uses employed residents,
    # see braunschweig/gravity/production_mass.py). Education always uses
    # population (pupils/students are not SvB).
    context.config("braunschweig.gravity.work_production_mass", "population")
    if context.config("braunschweig.gravity.work_production_mass", "population") != "population":
        # Only declared as required when the svb_wohn mode is active, so the
        # default "population" path needs no new config keys or stages. These
        # duplicate the sector-aware declarations above on purpose (synpp
        # tolerates repeated declaration); either flag alone must suffice.
        context.config("data_path")
        context.config(
            "braunschweig.employment_gemband_path",
            "braunschweig/gemband-dlk-0-202506-xlsx.xlsx",
        )
        context.stage("eqasim_common.spatial.codes")
        # Fallback-transparency threshold (CLAUDE.md): share of Gemeinden
        # falling back to the Kreis-mean employment rate above which
        # build_work_production_mass / tilt_taz_production_by_gemeinde_rate
        # escalate their rate log to a WARNING. Declared only under this
        # conditional -- like the keys above -- so the "population" OFF path
        # needs no new config key. The default is the single source of truth
        # in production_mass (lazy import, consistent with the execute paths).
        from braunschweig.gravity.production_mass import (  # noqa: PLC0415
            SVB_FALLBACK_WARN_SHARE,
        )
        context.config(
            "braunschweig.gravity.svb_wohn_fallback_warn_share",
            SVB_FALLBACK_WARN_SHARE,
        )

    # #193: inner VerBindungen calibration anchor. Default False -> byte-
    # identical work OD (the anchor CHANGES scientific output when ON; the
    # default flips only via the pre-registered decision rule + ADR --
    # see docs/superpowers/specs/2026-07-16-verbindungen-calibration-anchor-design.md).
    # Default ON since 2026-07-17 (ADR-0068, HUMAN OVERRIDE of the
    # pre-registered gate v2 whose verdict was default_flip_supported=False):
    # evidence judged net-positive -- 5/6 P13-by-RS7 classes and the P38.2
    # ZGB aggregate improve; the AO axis is neutral within fold noise; the
    # single class-72 shift (+0.0036 EMD) is a small systematic shortening
    # toward the LOCALLY OBSERVED 2019 QZM destination structure (diagnosed
    # in scripts/diagnose_anchor_p13.py, 2026-07-17). Checked against BOTH
    # reference flavours: the NATIONAL MiD RS7-72 class AND the REGIONAL
    # per-Kreis P38.2 tables -- the three cities also worsen slightly vs
    # their own regional refs (+0.002..+0.005, thin-n directional range)
    # while all five Landkreise improve (up to -0.026) and the ZGB aggregate
    # improves; the trade is documented in ADR-0068. Set False per config to
    # disable; requires the verbindungen raw data when ON.
    context.config("braunschweig.gravity.verbindungen_anchor_enabled", True)
    if context.config("braunschweig.gravity.verbindungen_anchor_enabled", True):
        # Reference stages only required when the anchor is ON, so the OFF
        # path needs no new stages or data files.
        context.stage("braunschweig.data.verbindungen.zones")
        context.stage("braunschweig.data.verbindungen.work_od")
        # Default measured on the 2019 QZM ZGB coverage distribution
        # (holdout run 2026-07-17, scripts/run_anchor_holdout.py on the
        # 100pct cache; coverage_row_observed_commuters.csv). Criterion:
        # 3x the QZM censoring bound of 10 commuters (rows near the bound
        # carry coarse small-count noise), with the measured consequences
        # at 30: 205/239 (origin zone, dest Kreis) rows anchorable (85.8%)
        # covering 98.2% of the anchorable observed mass; the row-mass
        # distribution is p10=18.8, p25=73, p50=277 (n=239), so only the
        # bottom decile falls below this guard.
        context.config("braunschweig.verbindungen.anchor_min_observed_commuters", 30)

    # TAZ-specific stages: only declared when the flag is ON so the OFF path
    # (all existing configs) needs no new keys or stages.
    if context.config("taz_work_location_choice", False):
        context.stage("braunschweig.data.spatial.taz")
        context.stage("braunschweig.gravity.distance_matrix_taz")
        context.stage("synthesis.population.spatial.home.locations")
        context.stage("braunschweig.data.building_potentials")
        # Census Gemeinde polygons (ARS-12) for the geometric TAZ -> census
        # commune assignment in the dest margin (build_dest_attraction_per_taz).
        context.stage("data.spatial.municipalities")


def execute(context):
    # _execute_gravity_base returns a 4-tuple:
    # (work_od, education_od, pop_taz, df_work_production).
    # pop_taz is the TAZ origin-margin DataFrame (non-None only on the ON path);
    # it is threaded out here so execute() does not call build_origin_population_per_taz
    # a second time (the sjoin is expensive). df_work_production is the #132 work
    # production frame (non-None only when work_production_mass=svb_wohn); it is
    # threaded out so the SAME mass that seeded the gravity also seeds the
    # Kreis-IPF (_calibrate) and the outbound flows (_append_outbound_flows).
    df_work_od, df_education_od, pop_taz_from_base, df_work_production = \
        _execute_gravity_base(context)
    # data.census.filtered resolves to the configured population producer
    # (braunschweig.ipf.attributed in the legacy config -- unchanged behaviour --
    # or braunschweig.popsim.stage in the popsim configs), so the gravity weights
    # always come from the SAME population as the demand.
    df_population = context.stage("data.census.filtered")
    df_pendler = context.stage("braunschweig.data.census.pendler")
    df_employment = context.stage("braunschweig.data.census.employment")
    df_external = context.stage("braunschweig.data.external_workplaces")

    scope = [str(p) for p in context.config("braunschweig.political_prefix")]
    mask = df_pendler["orig_ars"].isin(scope) | df_pendler["dest_ars"].isin(scope)
    df_pendler = df_pendler[mask].copy()

    df_pendler = _synthesise_intra_kreis(df_pendler, df_employment, scope)

    # ExecuteContext.config() takes the key alone; the default is declared in configure().
    # Passing a default here raises "config() takes 2 positional arguments but 3 were given".
    taz_on = context.config("taz_work_location_choice")

    if taz_on:
        # ON path: reuse the TAZ population margin already computed by
        # _execute_gravity_base (pop_taz_from_base) -- no second sjoin needed.
        from braunschweig.gravity.taz_margins import taz_to_kreis_lookup  # noqa: PLC0415
        df_taz = context.stage("braunschweig.data.spatial.taz")
        zone_to_kreis = taz_to_kreis_lookup(df_taz)
        population_key = "taz_id"
        population_value = "population"
        # pop_taz schema: taz_id, commune_id, population -- the _calibrate and
        # _append_outbound_flows functions group by population_key so they receive
        # the correct per-TAZ margin.
        df_population_for_od = pop_taz_from_base
    else:
        # OFF path: defaults -> byte-identical behaviour.
        zone_to_kreis = None
        population_key = "commune_id"
        population_value = "weight"
        if df_work_production is None:
            df_population_for_od = df_population
        else:
            # #132: the SAME production mass that seeded the gravity must
            # seed the Kreis-IPF and the outbound flows (consistency across
            # all three mass entry points).
            df_population_for_od = df_work_production.rename(columns={
                "origin_id": "commune_id",
                "population": "weight",
            })

    print(
        "[braunschweig.gravity.model] calibrating {:,} zone-pairs "
        "against {:,} BA Kreis-pair flows".format(len(df_work_od), len(df_pendler))
    )

    df_work_calibrated = _calibrate(
        df_work_od, df_population_for_od, df_pendler,
        zone_to_kreis=zone_to_kreis,
        population_key=population_key,
        population_value=population_value,
    )
    # #193: inner VerBindungen anchor (flag-gated, default OFF). Runs on the
    # CALIBRATED OD so the outer Kreis anchor is already satisfied; the inner
    # step preserves every Kreis-pair block total exactly (asserted inside).
    if context.config("braunschweig.gravity.verbindungen_anchor_enabled"):
        from braunschweig.gravity.verbindungen_anchor import run_inner_anchor  # noqa: PLC0415
        df_cells_vb, df_cell_commune_vb = context.stage(
            "braunschweig.data.verbindungen.zones")
        df_ref_od_vb = context.stage("braunschweig.data.verbindungen.work_od")
        df_work_calibrated, _anchor_stats = run_inner_anchor(
            df_work_calibrated, df_cells_vb, df_cell_commune_vb, df_ref_od_vb,
            min_observed_commuters=context.config(
                "braunschweig.verbindungen.anchor_min_observed_commuters"),
        )
    df_work_extended = _append_outbound_flows(
        df_work_calibrated, df_population_for_od, df_pendler, df_external, scope,
        zone_to_kreis=zone_to_kreis,
        population_key=population_key,
        population_value=population_value,
    )

    return df_work_extended, df_education_od
