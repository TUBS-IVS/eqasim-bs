"""Assembled secondary-candidate set (single source of truth).

Materialises the EXACT candidate frame the secondary chainsolvers solve on
when building potentials are ON:

- ``sec_b_<building_id>``   gpkg shop/leisure buildings (REPLACE, PR #77),
- ``sec_<n>``               legacy 'other' catalog rows,
- ``<commune_id>``          external Gemeinde centroids (``secondary_external_candidates``),
- ``sec_res_<building_id>`` residential leisure_visit / escort_residential candidates
  (``leisure_visit_building_potential`` and/or ``escort_purpose``, issues #127/#201),
- ``sec_edu_<n>``           education escort candidates (``escort_purpose``, issue #201).

Both consumers depend on THIS stage so they can never diverge again:
``braunschweig.synthesis.locations.secondary_chainsolvers`` (placement) and
``braunschweig.matsim.scenario.facilities`` (facility writing). Background
(2026-07-11, kreis5 100% run): the chainsolvers replaced the candidate set
internally while the facilities writer kept writing only the legacy frame, so
every realised ``sec_b_*`` id was missing from facilities.xml and MATSim's
RunPreparation crashed in LinkAssignment ("Facility sec_b_... does not
exist"). The gap was invisible before because no MATSim-bearing run had the
building-potential candidates active in the scenario path.

When ``secondary_building_potentials`` is OFF the legacy frame is returned
unchanged (byte-identical OFF path).
"""
from __future__ import annotations


def configure(context):
    context.stage("synthesis.locations.secondary")

    # Defaults mirror secondary_chainsolvers.configure EXACTLY (sec/external
    # default True there); a divergent default here would silently split the
    # two consumers onto different candidate sets again.
    sec_enabled = context.config("secondary_building_potentials", True)
    if sec_enabled:
        context.stage("braunschweig.data.building_potentials")

        if context.config("secondary_external_candidates", True):
            context.stage("braunschweig.data.external_secondary_points")
        # Only for the external-candidates-without-cordon warning.
        context.config("cordon_enabled", False)

        context.config("secondary_other_smart_potential", False)
        context.config("secondary_other_broad_share", 0.54)
        context.config("secondary_other_errand_share", 0.46)
        context.config("secondary_other_min_volume_m3", 50.0)
        context.config("secondary_other_cap_percentile", 0.99)
        if context.config("secondary_other_smart_potential"):
            context.stage("braunschweig.data.bosserhof_purpose")

        # Escort candidate universe (issue #201): the education facilities are
        # only needed as candidates once escort_purpose is ON, and only make
        # sense as REPLACE candidates (with_potentials), so this STAGE
        # dependency is declared inside the sec_enabled block. The config
        # option itself gets its own unconditional declaration below (next to
        # leisure_visit_building_potential) so configure() always knows it,
        # even when this block is skipped.
        if context.config("escort_purpose", False):
            context.stage("synthesis.locations.education")

    # Needed for the fail-fast cross-flag guard below (the flag itself is
    # owned by the chainsolvers stage; read-only here).
    context.config("secondary_leisure_subtype_split", False)
    context.config("secondary_other_subtype_split", False)
    context.config("leisure_visit_building_potential", False)
    # Unconditional declaration (issue #201 fix): escort_purpose must not be
    # declared ONLY inside `if sec_enabled:` above or as the right operand of
    # the `or` below -- Python's `or` short-circuits and never evaluates the
    # right operand once leisure_visit_building_potential is True, and
    # combined with secondary_building_potentials=False (which skips the
    # sec_enabled block too) escort_purpose was then never added to
    # configure()'s required config. execute() then crashed on its one-arg
    # config() read with synpp's PipelineError instead of reaching the
    # intended ValueError guard below.
    context.config("escort_purpose", False)
    if context.config("leisure_visit_building_potential") or context.config("escort_purpose"):
        context.stage("braunschweig.data.buildings")

    # SrV-grounded location-category candidates (issue #262): declare
    # UNCONDITIONALLY, exactly like escort_purpose above -- never move this
    # inside `if sec_enabled:` or make it the right operand of a short-
    # circuited `or`, or the same #201 trap recurs (execute()'s one-arg
    # config() read would crash with synpp's PipelineError instead of
    # reaching the intended ValueError guard below).
    srv_location_types = context.config("secondary_srv_location_types", False)
    context.config("secondary_landuse_grid_spacing_meters", 150.0)
    if srv_location_types:
        context.stage("braunschweig.data.landuse")
        context.stage("braunschweig.data.bosserhof_location_category")
        context.stage("data.spatial.municipalities")


def execute(context):
    # Import from the chainsolvers module so the assembly logic exists exactly
    # once; this stage only orchestrates it.
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        append_residential_visit_candidates,
        build_secondary_candidates,
        external_candidates_cordon_warning,
    )

    df_secondary = context.stage("synthesis.locations.secondary")

    sec_enabled = context.config("secondary_building_potentials")
    leisure_visit = bool(context.config("leisure_visit_building_potential"))
    escort_on = bool(context.config("escort_purpose"))
    srv_location_types = bool(context.config("secondary_srv_location_types"))
    leisure_subtype_split = bool(context.config("secondary_leisure_subtype_split"))
    other_subtype_split = bool(context.config("secondary_other_subtype_split"))

    # Same fail-fast guards as the chainsolvers (they must hold wherever the
    # candidate set is assembled; no silent fallback to a degenerate set).
    if leisure_visit and not leisure_subtype_split:
        raise ValueError(
            "[braunschweig.secondary_candidates] leisure_visit_building_potential "
            "requires secondary_leisure_subtype_split to be ON (there is no "
            "'leisure_visit' activity without the leisure subtype split)."
        )
    if leisure_visit and not sec_enabled:
        raise ValueError(
            "[braunschweig.secondary_candidates] leisure_visit_building_potential "
            "requires secondary_building_potentials to be ON (the residential visit "
            "placement needs the pot_visit candidate column)."
        )
    if srv_location_types and not (
        sec_enabled and leisure_subtype_split and other_subtype_split and leisure_visit
    ):
        raise ValueError(
            "[braunschweig.secondary_candidates] secondary_srv_location_types requires "
            "secondary_building_potentials, secondary_leisure_subtype_split, "
            "secondary_other_subtype_split, AND leisure_visit_building_potential to all "
            "be ON (the SrV location-category candidates are built on top of the "
            "building-potential candidate set, the leisure/other subtype split, and "
            "the residential visit machinery that type-15 escort-leisure legs route "
            "onto); no silent fallback to a partial category set is performed."
        )

    if not sec_enabled:
        # OFF path: legacy candidates pass through unchanged (byte-identical).
        return df_secondary

    external_on = context.config("secondary_external_candidates")
    df_external = (context.stage("braunschweig.data.external_secondary_points")
                   if external_on else None)
    warning = external_candidates_cordon_warning(
        external_on, context.config("cordon_enabled"))
    if warning:
        print(warning, flush=True)

    smart_other = bool(context.config("secondary_other_smart_potential"))
    if smart_other:
        mapping = context.stage("braunschweig.data.bosserhof_purpose")
        other_params = dict(
            broad_share=float(context.config("secondary_other_broad_share")),
            errand_share=float(context.config("secondary_other_errand_share")),
            min_volume_m3=float(context.config("secondary_other_min_volume_m3")),
            cap_percentile=float(context.config("secondary_other_cap_percentile")),
        )
        df_secondary = build_secondary_candidates(
            df_secondary,
            context.stage("braunschweig.data.building_potentials"),
            df_external=df_external,
            mapping=mapping,
            other_potential_params=other_params,
        )
    else:
        df_secondary = build_secondary_candidates(
            df_secondary,
            context.stage("braunschweig.data.building_potentials"),
            df_external=df_external,
        )

    if leisure_visit or escort_on:
        # The residential rows are needed for "escort_residential" even when
        # the leisure feature itself is off (issue #201): both features share
        # the SAME candidate set (sec_res_<building_id>), so escort_on alone
        # already triggers the append.
        try:
            df_residential_buildings = context.stage("braunschweig.data.buildings")
        except Exception as exc:
            raise ValueError(
                "[braunschweig.secondary_candidates] leisure_visit_building_potential "
                "or escort_purpose is ON but the residential candidate source "
                "(braunschweig.data.buildings) could not be resolved (%s); no silent "
                "fallback to pot_leisure is performed." % exc
            ) from exc
        df_secondary = append_residential_visit_candidates(
            df_secondary, df_residential_buildings)

    if escort_on:
        from braunschweig.synthesis.locations.secondary_chainsolvers import (
            append_escort_candidates,
        )
        df_secondary = append_escort_candidates(
            df_secondary, context.stage("synthesis.locations.education"))

    if srv_location_types:
        from braunschweig.data.bosserhof_location_category import BUILDING_CATEGORIES
        from braunschweig.synthesis.locations.landuse_candidates import (
            LANDUSE_LAYER_TO_CATEGORY,
            grid_seed_polygons,
        )
        from braunschweig.synthesis.locations.secondary_chainsolvers import (
            append_landuse_candidates,
            append_location_category_columns,
            check_category_supply,
        )

        # min_volume_m3 / cap_percentile mirror secondary_other_min_volume_m3 /
        # secondary_other_cap_percentile (the same cap-and-floor formula
        # secondary_other_potential.derive_other_potential uses) -- both are
        # declared unconditionally whenever secondary_building_potentials is ON
        # (configure(), directly inside the `if sec_enabled:` block, not nested
        # under secondary_other_smart_potential), which is guaranteed here
        # because the guard above already requires secondary_building_potentials
        # ON whenever secondary_srv_location_types is ON.
        df_secondary = append_location_category_columns(
            df_secondary,
            context.stage("braunschweig.data.building_potentials"),
            context.stage("braunschweig.data.bosserhof_location_category"),
            min_volume_m3=float(context.config("secondary_other_min_volume_m3")),
            cap_percentile=float(context.config("secondary_other_cap_percentile")),
        )

        df_landuse = context.stage("braunschweig.data.landuse")
        df_landuse_seedable = df_landuse[df_landuse["layer"].isin(LANDUSE_LAYER_TO_CATEGORY.keys())]
        spacing_m = float(context.config("secondary_landuse_grid_spacing_meters"))
        df_landuse_points = grid_seed_polygons(df_landuse_seedable, spacing_m)

        df_secondary = append_landuse_candidates(
            df_secondary, df_landuse_points, LANDUSE_LAYER_TO_CATEGORY,
            context.stage("data.spatial.municipalities"),
        )

        # Non-empty check (CLAUDE.md fallback transparency): every SrV
        # location category must carry at least one positive-potential
        # candidate, or the wiring (mapping / grid seeding / potential join)
        # is broken. All six categories are checked -- the three leisure_*
        # categories (masking pot_leisure on sec_b_* buildings), the two
        # errand_* categories (derived from df_potentials by
        # append_location_category_columns via the cap-and-floor formula
        # above, including newly appended sec_b_* rows for errand-class
        # buildings absent from the input candidates), and leisure_outdoor
        # (landuse-only, no building counterpart).
        check_category_supply(df_secondary, BUILDING_CATEGORIES + ("leisure_outdoor",))

    return df_secondary
