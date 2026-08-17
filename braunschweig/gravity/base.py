"""The inherited eqasim-bavaria base gravity execution.

Two pieces of the ``braunschweig.gravity.model`` stage live here:

- ``DEFAULT_SLOPE`` / ``DEFAULT_CONSTANT`` / ``DEFAULT_DIAGONAL`` -- the
  IDF-derived default friction parameters of the Gemeinde x Gemeinde gravity
  model inherited from ``bavaria/gravity/model.py`` (Origin: eqasim-bavaria
  @ b20fbe6). Read only by ``braunschweig.gravity.model.configure`` to seed
  the ``gravity_slope`` / ``gravity_constant`` / ``gravity_diagonal`` config
  defaults.
- ``_execute_gravity_base`` -- runs that base gravity model once (Gemeinde
  universe) or twice (Gemeinde + TAZ, when ``taz_work_location_choice`` is
  on), returning the RAW work/education origin-destination frames before the
  BA-Pendleratlas Kreis-level calibration in
  ``braunschweig.gravity.kreis_calibration`` is applied. It calls into the
  already-extracted ``braunschweig.gravity.attraction_vector`` (destination
  attraction) and ``braunschweig.gravity.od`` (the pure gravity computation
  and the BA Gemeindedaten establishment-count reader) siblings for its
  sub-computations, and lazily imports ``braunschweig.gravity.production_mass``
  and ``braunschweig.gravity.taz_margins`` at call time (unchanged from the
  pre-split stage) to avoid a load-time import cycle with those two
  pre-existing sibling modules.

Extracted verbatim from ``braunschweig.gravity.model`` (issue #267 split): the
function, its signature, its arithmetic, its stage/config reads and its log
lines -- including the ``[braunschweig.gravity.model]`` message prefixes --
are unchanged, so the model output and the console/log output are
byte-identical to the pre-split stage.

``braunschweig.gravity.model`` re-exports every name defined here, so existing
imports of the stage module path (including the test suite, which imports
``_execute_gravity_base`` and the ``DEFAULT_*`` constants directly) keep
working. This module must NEVER depend on ``braunschweig.gravity.model`` in
any direction other than downward (that would close an import cycle): the
dependency runs strictly model -> base.
"""

from __future__ import annotations

from braunschweig.gravity.attraction_vector import build_destination_attraction
from braunschweig.gravity.od import _read_betriebe_per_commune, compute_work_od

# Defaults: -0.09 came from IDF, value -2.0 has been calibrated.
DEFAULT_SLOPE = -0.2
DEFAULT_CONSTANT = -2.4
DEFAULT_DIAGONAL = 1.0


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
