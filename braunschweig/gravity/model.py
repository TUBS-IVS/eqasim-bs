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
``verbindungen_anchor`` and ``distance_matrix_taz``) and every name they define
is re-exported here, so external imports keep working unchanged. Siblings
extracted so far:

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

Because this module is a synpp STAGE, ``validate()`` below folds every sibling
source into the stage's cache-validation token -- see the comment on
``_HELPER_MODULES``.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import os  # noqa: F401  (unused here; kept for facade namespace parity, see below)

import numpy as np
import pandas as pd

from braunschweig.data.bbsr.regiostar import ars_to_ags8  # noqa: F401  (namespace parity, see below)
from braunschweig.gravity import attraction_vector, balancing, od
from braunschweig.gravity.friction import build_friction_matrix  # noqa: F401  (namespace parity, see below)

# ``os``, ``ars_to_ags8`` and ``build_friction_matrix`` above are no longer
# used by this module's own code: their sole call sites (``_read_betriebe_per_commune``
# and ``compute_work_od``) moved verbatim to ``braunschweig.gravity.od`` (issue
# #267), which now imports them directly for its own use. They are kept bound
# here anyway so ``dir(braunschweig.gravity.model)`` still contains every name
# it did before the split (the namespace-parity contract this stage's split
# is held to -- see ``.superpowers/sdd/2026-08-14-split-gravity-model/check_namespace.py``);
# removing them would silently break any external code doing
# ``from braunschweig.gravity.model import ars_to_ags8`` (etc.), however unlikely.

# ---------------------------------------------------------------------------
# Sibling modules extracted from this stage. Every module-level name they
# define is re-exported here so external consumers (the pipeline, calibration
# scripts, tests) keep importing from ``braunschweig.gravity.model`` unchanged.
# Each sibling MUST also be listed in _HELPER_MODULES below so its source
# participates in the synpp cache-validation token.
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

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# synpp cache validation
# ---------------------------------------------------------------------------

# Sibling modules whose source is part of this stage's behaviour. synpp's
# ``get_stage_hash`` hashes only THIS file's source (``inspect.getsource`` of
# the stage module), so code that moved into a sibling left that hash: without
# the ``validate()`` hook below, a change confined to a sibling would silently
# reuse the STALE cached stage output on a partial rerun. Listed explicitly and
# in a deterministic order (never via ``dir()`` or a directory glob) so the
# token depends only on the sources, not on import or filesystem order. Every
# module extracted from this stage MUST be appended here.
_HELPER_MODULES = (attraction_vector, balancing, od)


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


# --- Inherited from eqasim-bavaria -----------------------------------------
# Gemeinde x Gemeinde gravity model with IDF-derived defaults.

# Defaults: -0.09 came from IDF, value -2.0 has been calibrated.
DEFAULT_SLOPE = -0.2
DEFAULT_CONSTANT = -2.4
DEFAULT_DIAGONAL = 1.0

# --- Sector-aware destination attraction: see the sibling module ------------
# ``SECTOR_AWARE_TILT_EXPONENT``, ``apply_sector_aware_attraction`` and
# ``build_destination_attraction`` were moved verbatim to
# ``braunschweig.gravity.attraction_vector`` (issue #267) and are re-exported
# above, so ``model.build_destination_attraction`` still resolves.

# --- Gravity balancing and per-origin friction slope: see the sibling module -
# ``DEFAULT_GRAVITY_MAX_ITERATIONS``, ``evaluate_gravity``,
# ``ORIGIN_SLOPE_FALLBACK_WARN_THRESHOLD`` and ``_build_origin_slope_vector``
# were moved verbatim to ``braunschweig.gravity.balancing`` (issue #267) and
# are re-exported above, so ``model.evaluate_gravity`` still resolves.

# --- Work-OD computation: see the sibling module -----------------------------
# ``ZERO_TOTAL_SELF_LOOP_WARN_PERCENT``, ``_GEMBAND_COLUMN_NAMES``,
# ``_read_betriebe_per_commune`` and ``compute_work_od`` were moved verbatim to
# ``braunschweig.gravity.od`` (issue #267) and are re-exported above, so
# ``model.compute_work_od`` still resolves.


def _execute_gravity_base(context):
    """Run the bavaria-style Gemeinde x Gemeinde gravity model.

    Returns a 4-tuple ``(df_work_od, df_education_od, pop_taz,
    df_work_production)``. The first two are row-normalised conditional
    probabilities; ``pop_taz`` is the TAZ origin margin (non-None only on the
    TAZ ON path); ``df_work_production`` is the #132 work production frame
    (schema ``origin_id``, ``population``; non-None only when
    ``braunschweig.gravity.work_production_mass`` is ``svb_wohn`` AND the TAZ
    flag is OFF -- on the TAZ-ON + svb_wohn path it is None because the
    production mass travels inside ``pop_taz`` instead).

    When ``taz_work_location_choice`` is OFF (default) the function runs the
    gravity once on the Gemeinde universe and returns the same frame for both
    work and education -- byte-identical to the pre-TAZ behaviour. With
    ``work_production_mass: svb_wohn`` a SECOND Gemeinde gravity is run for
    work using employed residents (svb_wohn) as the production mass; education
    always keeps the population-based OD.

    When ON the gravity is run TWICE:
    - Gemeinde pass (``education_od``): standard Gemeinde x Gemeinde gravity.
    - TAZ pass (``work_od``): TAZ x TAZ gravity using TAZ-aggregated population
      and building-potential-weighted employee attraction.
    """
    # B1: read the flag with the key alone at execute time.  synpp's
    # ExecuteContext.config() takes only the key; passing a default here raises
    # "config() takes 2 positional arguments but 3 were given".  The default
    # False is declared in configure().
    taz_on = context.config("taz_work_location_choice")

    # #132: work production mass, read once and validated BEFORE any gravity
    # computation (build_work_production_mass validates again -- belt and
    # braces). The default "population" is declared in configure().
    production_mode = context.config("braunschweig.gravity.work_production_mass")
    from braunschweig.gravity.production_mass import PRODUCTION_MASS_MODES  # noqa: PLC0415
    if production_mode not in PRODUCTION_MASS_MODES:
        raise ValueError(
            "[braunschweig.gravity.model] unknown "
            f"braunschweig.gravity.work_production_mass {production_mode!r}; "
            f"expected one of {PRODUCTION_MASS_MODES}"
        )

    df_distances = context.stage("eqasim_common.gravity.distance_matrix")
    # data.census.filtered resolves to the configured population producer
    # (braunschweig.ipf.attributed in the legacy config -- unchanged behaviour --
    # or braunschweig.popsim.stage in the popsim configs), so the gravity weights
    # always come from the SAME population as the demand.
    df_population_raw = context.stage("data.census.filtered")
    df_employees_raw = context.stage("braunschweig.data.census.employees")
    df_regiostar = context.stage("braunschweig.data.bbsr.regiostar")

    # Sector-aware destination attraction (flag-gated; OFF -> byte-identical).
    # ``build_destination_attraction`` renames the stage's ``weight`` column to
    # ``employees`` BEFORE the flag-gated tilt (issue #128: tilting the raw
    # stage frame crashed with KeyError 'employees') and tilts the per-Gemeinde
    # attraction by establishment density while preserving Kreis totals.
    # synpp's ExecuteContext.config() takes only the key (no default argument);
    # the default False is declared in configure(). Passing a default here raises
    # "config() takes 2 positional arguments but 3 were given" and aborts the run.
    sector_aware_enabled = context.config("braunschweig.gravity.sector_aware_enabled")
    df_betriebe = _read_betriebe_per_commune(context) if sector_aware_enabled else None
    df_employees_gemeinde = build_destination_attraction(
        df_employees_raw, df_betriebe, sector_aware_enabled,
    )

    # Rename to the schema expected by compute_work_od.
    df_pop_gemeinde = df_population_raw.rename(columns={
        "commune_id": "origin_id",
        "weight": "population",
    })[["origin_id", "population"]]

    df_emp_gemeinde = df_employees_gemeinde.rename(columns={
        "commune_id": "destination_id",
    })[["destination_id", "employees"]]

    # #132: svb_wohn production needs a Gemeinde-AGGREGATED population frame.
    # data.census.filtered is per-PERSON (multiple rows per origin_id), but
    # build_work_production_mass documents a one-row-per-Gemeinde input: merging
    # svb per person-row would multiply each Gemeinde's svb_wohn by its person
    # count downstream. Aggregate ONCE here so both the Gemeinde svb path (OFF)
    # and the TAZ svb tilt (ON) consume the SAME frame. Left None on the
    # "population" default so the byte-identical path does no extra work.
    df_pop_gemeinde_aggregated = None
    if production_mode != "population":
        df_pop_gemeinde_aggregated = (
            df_pop_gemeinde.groupby("origin_id", as_index=False)["population"].sum()
        )

    slope = context.config("gravity_slope")
    constant = context.config("gravity_constant")
    diagonal = context.config("gravity_diagonal")
    slope_overrides = context.config("gravity_slope_by_regiostar7")
    friction_factors = context.config("gravity_friction_factors")
    # ExecuteContext.config() takes the key alone (the default is declared in
    # configure()); passing a default here would raise.
    max_iterations = context.config("gravity_max_iterations")

    # Gemeinde pass (used for education, and also for work when TAZ is OFF).
    education_od = compute_work_od(
        df_population=df_pop_gemeinde,
        df_employees=df_emp_gemeinde,
        df_distances=df_distances,
        df_regiostar=df_regiostar,
        rs7_by_zone=None,
        slope=slope,
        constant=constant,
        diagonal=diagonal,
        slope_overrides=slope_overrides,
        friction_factors=friction_factors,
        max_iterations=max_iterations,
    )

    if not taz_on:
        if production_mode == "population":
            # OFF path: byte-identical to the pre-extraction behaviour.
            # Trailing elements are None so execute() can unpack uniformly.
            return education_od, education_od, None, None
        # svb_wohn: run a SEPARATE work gravity with the svb production mass;
        # education keeps the population-based OD computed above.
        from braunschweig.gravity.production_mass import (  # noqa: PLC0415
            build_work_production_mass, read_svb_wohn_per_commune,
        )
        df_svb = read_svb_wohn_per_commune(context)
        # Read with the key alone: configure() declares this key only inside
        # the "production_mode != population" conditional we are already in,
        # so the OFF path never has to request it.
        warn_share = context.config("braunschweig.gravity.svb_wohn_fallback_warn_share")
        # df_pop_gemeinde_aggregated (one row per Gemeinde) was built above so
        # the SAME aggregated mass seeds the Gemeinde svb path here and the TAZ
        # tilt on the ON path.
        df_work_production = build_work_production_mass(
            df_pop_gemeinde_aggregated, df_svb, mode=production_mode,
            warn_share=warn_share)
        work_od = compute_work_od(
            df_population=df_work_production,
            df_employees=df_emp_gemeinde,
            df_distances=df_distances,
            df_regiostar=df_regiostar,
            rs7_by_zone=None,
            slope=slope,
            constant=constant,
            diagonal=diagonal,
            slope_overrides=slope_overrides,
            friction_factors=friction_factors,
            max_iterations=max_iterations,
        )
        return work_od, education_od, None, df_work_production

    # TAZ pass for work-location gravity (ON path).
    # The origin margin splits each commune's census weight across its TAZ by the
    # home-point distribution, keyed on the 12-digit ARS commune_id that BOTH
    # data.census.filtered and home.locations carry (their household_id spaces are
    # disjoint -- FULL vs SAMPLED population -- so a household_id join cannot work).
    from braunschweig.gravity.taz_margins import (  # noqa: PLC0415
        build_dest_attraction_per_taz,
        build_origin_population_per_taz,
    )

    df_taz = context.stage("braunschweig.data.spatial.taz")
    df_dist_taz = context.stage("braunschweig.gravity.distance_matrix_taz")
    df_homes = context.stage("synthesis.population.spatial.home.locations")
    df_buildings = context.stage("braunschweig.data.building_potentials")
    # Census Gemeinde polygons (commune_id = 12-digit ARS, the key both
    # data.census.filtered and the employees frame use). The dest margin assigns
    # each TAZ to its census commune by LOCATION against these polygons, which
    # reconciles the RVB gpkg AGS-8 codes with the census communes geometrically
    # (the ~10 Gemeinde-code mismatches vanish; no AGS->ARS crosswalk needed).
    df_municipalities = context.stage("data.spatial.municipalities")

    pop_taz, _, _ = build_origin_population_per_taz(df_homes, df_population_raw, df_taz)

    # #132: svb_wohn production tilt for the TAZ path. svb_wohn carries NO
    # sub-Gemeinde information, so each TAZ's population is scaled by its parent
    # Gemeinde's employment rate (svb_wohn_gem / pop_gem): this shifts the
    # BETWEEN-Gemeinde masses while preserving the within-Gemeinde home
    # distribution. Tilting pop_taz HERE -- before df_pop_taz (gravity margin)
    # and the returned pop_taz_from_base (Kreis-IPF _calibrate, _append_outbound_flows)
    # both derive from it -- keeps all three mass entry points consistent with a
    # single edit. The Gemeinde-aggregated frame is reused (see above).
    if production_mode != "population":
        from braunschweig.gravity.production_mass import (  # noqa: PLC0415
            read_svb_wohn_per_commune, tilt_taz_production_by_gemeinde_rate,
        )
        df_svb = read_svb_wohn_per_commune(context)
        # Same key-only read as the non-TAZ svb branch above (declared under
        # the same "production_mode != population" conditional in configure()).
        warn_share = context.config("braunschweig.gravity.svb_wohn_fallback_warn_share")
        pop_taz = tilt_taz_production_by_gemeinde_rate(
            pop_taz, df_pop_gemeinde_aggregated, df_svb, warn_share=warn_share)

    att_taz, _, _ = build_dest_attraction_per_taz(
        df_buildings, df_employees_raw, df_taz, df_municipalities)

    # TAZ origin population frame (schema: origin_id, population).
    df_pop_taz = pop_taz.rename(columns={"taz_id": "origin_id"})[["origin_id", "population"]]

    # TAZ destination attraction frame (schema: destination_id, employees).
    # att_taz carries commune_id (ARS-12) -- rename to destination_id and use
    # the ``attraction`` column as the employees analogue.
    df_emp_taz = att_taz.rename(columns={
        "taz_id": "destination_id",
        "attraction": "employees",
    })[["destination_id", "employees"]]

    # Per-origin RS7: resolved directly from the TAZ frame's regiostar7 column.
    rs7_by_zone = dict(zip(df_taz["taz_id"].astype(str), df_taz["regiostar7"].astype(int)))

    work_od = compute_work_od(
        df_population=df_pop_taz,
        df_employees=df_emp_taz,
        df_distances=df_dist_taz,
        df_regiostar=df_regiostar,
        rs7_by_zone=rs7_by_zone,
        slope=slope,
        constant=constant,
        diagonal=diagonal,
        slope_overrides=slope_overrides,
        friction_factors=friction_factors,
        max_iterations=max_iterations,
    )

    # Return the (possibly svb-tilted) pop_taz as the third element so execute()
    # can reuse it without calling build_origin_population_per_taz a second time
    # (sjoin is expensive). The fourth element (Gemeinde work production frame) is
    # None on the TAZ path: the #132 production mass travels INSIDE pop_taz, so
    # _calibrate / _append_outbound_flows read it via df_population_for_od (#132).
    return work_od, education_od, pop_taz, None


# --- Braunschweig-specific -------------------------------------------------
# BA-Pendleratlas calibration: IPF the Gemeinde-level OD so Kreis aggregates
# match observed SvB flows; inject ZGB -> external Kreis outbound rows.

# IPF convergence parameters for the Kreis-level calibration step.
MAX_IPF_ITERATIONS = 20
IPF_TOLERANCE = 1e-3


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


def _zone_to_kreis(series: pd.Series, lookup: dict | None = None) -> pd.Series:
    """Map a zone id to its 5-digit Kreis ARS. lookup is None -> legacy commune_id
    str[:5] (byte-identical). lookup given -> map each taz_id, raise on unmapped.

    On the ON path an unmapped taz_id is a TAZ coverage gap (the lookup must map
    every zone). Raise a descriptive ``RuntimeError`` naming the offending ids
    instead of letting a bare ``KeyError`` from the dict lookup bubble up, so the
    failure is actionable (consistent with the other explicit guards here and the
    no-silent-fallback contract)."""
    if lookup is None:
        return series.astype(str).str[:5]
    zones = series.astype(str)
    unmapped = sorted(set(zones) - set(lookup))
    if unmapped:
        raise RuntimeError(
            "%d zone id(s) have no Kreis in the taz->kreis lookup, e.g. %s "
            "(TAZ coverage gap; the lookup must map every taz_id)"
            % (len(unmapped), ", ".join(unmapped[:5]))
        )
    return zones.map(lookup)


def _gemeinde_to_kreis(series: pd.Series) -> pd.Series:
    """Backwards-compatible shim (tests/braunschweig/test_stages.py imports this)."""
    return _zone_to_kreis(series)


def _synthesise_intra_kreis(df_pendler: pd.DataFrame,
                            df_employment: pd.DataFrame,
                            scope: list[str]) -> pd.DataFrame:
    """Inject intra-Kreis SvB flows (``K -> K``) into the Pendler frame."""
    wohnort = (df_employment.groupby("departement_id")["weight"]
                            .sum()
                            .rename("svb_wohnort")
                            .reset_index()
                            .rename(columns={"departement_id": "kreis"}))
    wohnort["kreis"] = wohnort["kreis"].astype(str)
    wohnort = wohnort[wohnort["kreis"].isin(scope)]

    auspendler = (df_pendler[df_pendler["orig_ars"].isin(scope)]
                  .groupby("orig_ars")["flow"].sum()
                  .rename("auspendler")
                  .reset_index()
                  .rename(columns={"orig_ars": "kreis"}))

    merged = wohnort.merge(auspendler, on="kreis", how="left")
    merged["auspendler"] = merged["auspendler"].fillna(0)
    merged["intra"] = (merged["svb_wohnort"] - merged["auspendler"]).clip(lower=0)

    intra = pd.DataFrame({
        "orig_ars": merged["kreis"],
        "dest_ars": merged["kreis"],
        "flow": merged["intra"].astype(int),
    })

    print(
        "[braunschweig.gravity.model] synthesised intra-Kreis flows: "
        + ", ".join(f"{r.orig_ars}={int(r.flow):,}" for r in intra.itertuples())
    )
    return pd.concat([df_pendler, intra], ignore_index=True)


def _calibrate(df_od: pd.DataFrame,
               df_population: pd.DataFrame,
               df_pendler: pd.DataFrame,
               zone_to_kreis: dict | None = None,
               population_key: str = "commune_id",
               population_value: str = "weight") -> pd.DataFrame:
    """IPF-scale zone-level OD so Kreis aggregates match BA Pendler.

    OFF path (zone_to_kreis=None): uses legacy commune_id[:5] -> byte-identical.
    ON path (zone_to_kreis=dict): maps each taz_id via the lookup, raises if no
    in-scope flows are found after mapping (silent BA-skip guard).

    Parameters
    ----------
    df_od:
        Origin-destination frame with columns ``origin_id``, ``destination_id``, ``weight``.
    df_population:
        Population frame; grouped by ``population_key``, summed on ``population_value``.
    df_pendler:
        BA Pendleratlas Kreis-pair flows (columns ``orig_ars``, ``dest_ars``, ``flow``).
    zone_to_kreis:
        None -> legacy str[:5] mapping (OFF path). dict -> explicit taz_id->Kreis map (ON path).
    population_key:
        Column to group ``df_population`` by. OFF: ``"commune_id"``; ON: ``"taz_id"``.
    population_value:
        Column to sum from ``df_population``. OFF: ``"weight"``; ON: ``"population"``.
    """
    df = df_od.copy()
    df["orig_kreis"] = _zone_to_kreis(df["origin_id"], zone_to_kreis)
    df["dest_kreis"] = _zone_to_kreis(df["destination_id"], zone_to_kreis)

    pop = (
        df_population.groupby(population_key)[population_value].sum()
                     .rename("pop")
                     .reset_index()
                     .rename(columns={population_key: "origin_id"})
    )
    df = pd.merge(df, pop, on="origin_id", how="left")
    df["pop"] = df["pop"].fillna(0.0)
    df["flow"] = df["weight"] * df["pop"]

    obs = (
        df_pendler.rename(columns={
            "orig_ars": "orig_kreis",
            "dest_ars": "dest_kreis",
        })
        .groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
        .rename("obs")
        .reset_index()
    )

    def kreis_flows(frame):
        return (
            frame.groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
                 .rename("cur")
                 .reset_index()
        )

    scope_pairs = obs[["orig_kreis", "dest_kreis"]].drop_duplicates()
    df_scope = df.merge(scope_pairs, on=["orig_kreis", "dest_kreis"], how="inner")
    df_rest = df.merge(scope_pairs, on=["orig_kreis", "dest_kreis"],
                       how="left", indicator=True)
    df_rest = df_rest[df_rest["_merge"] == "left_only"].drop(columns=["_merge"])

    if len(df_scope) == 0:
        if zone_to_kreis is not None:
            raise RuntimeError(
                "[gravity TAZ] no in-scope flow after taz->kreis mapping; "
                "BA calibration would be silently skipped"
            )
        print("[braunschweig.gravity.model] no scope overlap; returning raw gravity")
        return df_od

    for it in range(MAX_IPF_ITERATIONS):
        cur = kreis_flows(df_scope)
        merged = cur.merge(obs, on=["orig_kreis", "dest_kreis"], how="inner")
        merged["ratio"] = np.where(
            merged["cur"] > 0, merged["obs"] / merged["cur"], 1.0
        )

        df_scope = df_scope.merge(
            merged[["orig_kreis", "dest_kreis", "ratio"]],
            on=["orig_kreis", "dest_kreis"], how="left",
        )
        df_scope["ratio"] = df_scope["ratio"].fillna(1.0)
        df_scope["flow"] = df_scope["flow"] * df_scope["ratio"]

        max_delta = float(np.abs(merged["ratio"] - 1.0).max())
        df_scope = df_scope.drop(columns=["ratio"])

        if max_delta < IPF_TOLERANCE:
            print(f"[braunschweig.gravity.model] IPF converged after {it+1} iter (delta={max_delta:.4g})")
            break
    else:
        print(f"[braunschweig.gravity.model] IPF stopped at {MAX_IPF_ITERATIONS} iter (delta={max_delta:.4g})")

    df_out = pd.concat([df_scope, df_rest], ignore_index=True)
    return df_out[["origin_id", "destination_id", "flow"]]


def _append_outbound_flows(df_od: pd.DataFrame,
                           df_population: pd.DataFrame,
                           df_pendler: pd.DataFrame,
                           df_external: pd.DataFrame,
                           scope: list[str],
                           zone_to_kreis: dict | None = None,
                           population_key: str = "commune_id",
                           population_value: str = "weight") -> pd.DataFrame:
    """Add rows ``(origin_zone, synthetic_external_commune, weight)``.

    Each outbound BA Kreis flow is split across the per-Gemeinde EXT points
    in ``df_external`` proportional to their ``employees`` share, so the
    emitted ``destination_id`` equals the per-Gemeinde ``commune_id``
    (``"EXT" + gem_ags``, 8-digit AGS).  This ensures exact-string matching
    against the work-pool ``commune_id`` in the downstream candidate sampler
    (``synthesis/population/spatial/primary/candidates.py``).

    Mass is conserved: for every (origin, Kreis) pair the sum of per-zone
    flows equals the original Kreis-level outbound flow.

    OFF path (zone_to_kreis=None): groups ``df_population`` by ``commune_id``,
    derives Kreis via ``str[:5]`` -- byte-identical to the prior behaviour.
    ON path (zone_to_kreis=dict): groups by ``taz_id`` (population_key), maps
    each taz_id to its Kreis via the lookup; origin_id in injected rows is the
    taz_id so the key space is consistent with the calibrated work OD.
    """
    ext_ars = set(df_external["ars5"].astype(str))
    df_out_pendler = df_pendler[
        df_pendler["orig_ars"].isin(scope)
        & df_pendler["dest_ars"].isin(ext_ars)
    ].copy()

    if zone_to_kreis is None:
        # OFF path: classic commune_id[:5] grouping and origin key.
        pop = (
            df_population.groupby("commune_id")["weight"].sum()
                         .rename("pop").reset_index()
        )
        pop["orig_ars"] = pop["commune_id"].astype(str).str[:5]
        pop["kreis_total"] = pop.groupby("orig_ars")["pop"].transform("sum")
        pop["share"] = np.where(pop["kreis_total"] > 0,
                                pop["pop"] / pop["kreis_total"], 0.0)
        pop = pop[pop["orig_ars"].isin(scope)]
        # origin_id for injected rows is the Gemeinde commune_id (OFF behaviour).
        pop_origin_col = "commune_id"
    else:
        # ON path: group by taz_id, map to Kreis via lookup; origin_id = taz_id.
        pop = (
            df_population.groupby(population_key)[population_value].sum()
                         .rename("pop").reset_index()
                         .rename(columns={population_key: "taz_id"})
        )
        pop["orig_ars"] = pop["taz_id"].astype(str).map(zone_to_kreis)
        if pop["orig_ars"].isna().any():
            missing_n = int(pop["orig_ars"].isna().sum())
            raise RuntimeError(
                "[gravity TAZ _append_outbound_flows] %d taz_id values have no "
                "Kreis mapping in zone_to_kreis; cannot build outbound shares" % missing_n
            )
        pop["kreis_total"] = pop.groupby("orig_ars")["pop"].transform("sum")
        pop["share"] = np.where(pop["kreis_total"] > 0,
                                pop["pop"] / pop["kreis_total"], 0.0)
        pop = pop[pop["orig_ars"].isin(scope)]
        pop_origin_col = "taz_id"

    # Build per-Gemeinde employee shares keyed by Kreis ars5, carrying commune_id.
    # gem_share = employees / Sigma_{Gemeinde in Kreis} employees.
    gem_emp = (
        df_external[["ars5", "commune_id", "employees"]]
        .copy()
        .astype({"ars5": str, "commune_id": str, "employees": float})
    )
    kreis_total_emp = gem_emp.groupby("ars5")["employees"].transform("sum")
    gem_emp["gem_share"] = np.where(
        kreis_total_emp > 0, gem_emp["employees"] / kreis_total_emp, 0.0
    )

    n_ext_rows = 0
    ext_svb = 0.0
    if df_out_pendler.empty:
        print("[braunschweig.gravity.model] no outbound flows to inject")
        df_all = df_od.copy()
    else:
        # origin_zone x dest_kreis rows, with each origin's flow share.
        df_inj = pop.merge(df_out_pendler, on="orig_ars", how="inner")
        df_inj["flow"] = df_inj["share"] * df_inj["flow"].astype(float)
        df_inj = df_inj[df_inj["flow"] > 0]
        # df_inj now has columns: <pop_origin_col>, orig_ars, dest_ars, flow (Kreis-level split).

        # Expand each Kreis-level flow to per-Gemeinde rows via the employee share.
        # Join on ars5 == dest_ars (many-to-many: one origin->Kreis row becomes N rows).
        df_inj = df_inj.merge(
            gem_emp[["ars5", "commune_id", "gem_share"]].rename(
                columns={"commune_id": "destination_id"}
            ),
            left_on="dest_ars",
            right_on="ars5",
            how="inner",
        )

        # Guard: warn if any Kreis present in outbound had no Gemeinde points.
        kreise_in_inj_before = set(df_out_pendler["dest_ars"].astype(str).unique())
        kreise_expanded = set(gem_emp["ars5"].unique())
        kreise_missing = kreise_in_inj_before - kreise_expanded
        if kreise_missing:
            print(
                "WARNING: [braunschweig.gravity.model] "
                f"{len(kreise_missing)} Kreis(e) in outbound have NO per-Gemeinde "
                f"EXT point in df_external and their flow is lost: "
                + ", ".join(sorted(kreise_missing))
            )

        df_inj["flow"] = df_inj["flow"] * df_inj["gem_share"]
        df_inj = df_inj[df_inj["flow"] > 0]
        df_inj = df_inj.rename(columns={pop_origin_col: "origin_id"})
        df_inj = df_inj[["origin_id", "destination_id", "flow"]]

        n_ext_rows = len(df_inj)
        ext_svb = float(df_inj["flow"].sum())

        df_all = pd.concat([df_od, df_inj], ignore_index=True)

    totals = df_all.groupby("origin_id")["flow"].sum().rename("total").reset_index()
    df_all = df_all.merge(totals, on="origin_id", how="left")
    f_missing = df_all["total"] <= 0.0
    # No-silent-fallback (CLAUDE.md): an origin with zero total flow (no BA-Pendler
    # row, no EXT injection) would otherwise divide 0/0 below; the self-loop is
    # forced to weight 1.0 instead. Count and log the rate so a systematically
    # empty outbound-flow set (e.g. a broken scope/ars join) is visible.
    n_missing_origins = int(df_all.loc[f_missing, "origin_id"].nunique())
    n_total_origins = int(df_all["origin_id"].nunique())
    if n_missing_origins:
        share = 100.0 * n_missing_origins / max(n_total_origins, 1)
        level = logger.warning if share > ZERO_TOTAL_SELF_LOOP_WARN_PERCENT else logger.info
        level(
            "[gravity] zero-total origins forced to self-loop: %d/%d (%.1f%%)",
            n_missing_origins, n_total_origins, share,
        )
    df_all.loc[f_missing & (df_all["origin_id"] == df_all["destination_id"]), "flow"] = 1.0
    df_all.loc[f_missing, "total"] = 1.0
    df_all["weight"] = df_all["flow"] / df_all["total"]

    if n_ext_rows:
        print(
            "[braunschweig.gravity.model] "
            f"injected {n_ext_rows:,} outbound rows (per-Gemeinde EXT destinations) "
            f"({ext_svb:,.0f} synthetic SvB distributed to external Gemeinden)"
        )
    return df_all[["origin_id", "destination_id", "weight"]]


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
