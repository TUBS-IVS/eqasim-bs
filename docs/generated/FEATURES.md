<!-- THIS FILE IS GENERATED. DO NOT EDIT MANUALLY.
     Rebuild: python -m braunschweig.documentation build
     Sources: docs/registry/** + docs/decisions/ + docs/runs/ -->

# Feature registry (generated)

One row per declaration in `docs/registry/features/`. Evidence columns
report what the checker RESOLVES, not scientific quality; `validation`
may only name run manifests (no run, no claim).

| Feature | Area | Lifecycle | Prod | mid/open/ipf | Tests | OFF byte-id | Fallback | Reference | Validation | Assessment |
|---|---|---|---|---|---|---|---|---|---|---|
| [age_aware_composition](../registry/features/age_aware_composition.yml) | population | supported | off | -/-/A | 1 | -- | marker | assumption | unvalidated | pending |
| [alkis_home_matching](../registry/features/alkis_home_matching.yml) | home | active | ON | A/A/- | 2 | -- | marker | none | unvalidated | pending |
| [building_potentials_education](../registry/features/building_potentials_education.yml) | education | active | ON | A/A/- | 1 | -- | none | none | unvalidated | pending |
| [building_potentials_secondary](../registry/features/building_potentials_secondary.yml) | secondary | active | ON | A/i/i | 1 | -- | none | committed | unvalidated | pending |
| [building_potentials_work](../registry/features/building_potentials_work.yml) | work | active | ON | A/i/i | 2 | not claimed | none | none | unvalidated | pending |
| [carless_routing_remode](../registry/features/carless_routing_remode.yml) | matsim | active | ON | A/A/s | 1 | -- | none | none | unvalidated | pending |
| [cell_accurate_homes](../registry/features/cell_accurate_homes.yml) | home | active | ON | A/A/- | 2 | -- | marker | none | unvalidated | pending |
| [consistent_car_availability](../registry/features/consistent_car_availability.yml) | attributes | active | off | -/-/A | 2 | proven | marker | committed | unvalidated | pending |
| [cordon_gates](../registry/features/cordon_gates.yml) | cordon | active | ON | A/A/- | 5 | -- | marker | none | unvalidated | pending |
| [cordon_mode_balancer](../registry/features/cordon_mode_balancer.yml) | cordon | active | ON | A/A/- | 2 | -- | marker | committed | unvalidated | pending |
| [cordon_network_ring](../registry/features/cordon_network_ring.yml) | cordon | active | ON | A/A/- | 4 | proven | none | none | unvalidated | pending |
| [detour_circuity_curve](../registry/features/detour_circuity_curve.yml) | secondary | supported | off | i/i/i | 3 | proven | none | committed | unvalidated | pending |
| [driving_licence_enrichment](../registry/features/driving_licence_enrichment.yml) | attributes | active | off | -/-/A | 1 | -- | none | committed | unvalidated | pending |
| [economic_status_bayes](../registry/features/economic_status_bayes.yml) | attributes | active | off | -/-/A | 2 | proven | marker | committed | unvalidated | pending |
| [education_enrollment_validation](../registry/features/education_enrollment_validation.yml) | validation | active | ON | A/s/- | 1 | -- | marker | none | unvalidated | pending |
| [education_gravity](../registry/features/education_gravity.yml) | education | active | ON | A/A/- | 4 | not claimed | none | committed | unvalidated | pending |
| [einpendler_injection](../registry/features/einpendler_injection.yml) | cordon | active | ON | A/A/- | 5 | -- | none | none | unvalidated | pending |
| [employment_margin](../registry/features/employment_margin.yml) | attributes | parked | off | -/-/i | 1 | -- | marker | none | unvalidated | pending |
| [eqasim_java_fork](../registry/features/eqasim_java_fork.yml) | infrastructure | active | ON | A/A/A | 0 | -- | none | none | unvalidated (`matsim-e2e-2.2.0-kreis03101-2026-07-23`) | pending |
| [escort_purpose](../registry/features/escort_purpose.yml) | behavior | active | ON | A/-/- | 5 | proven | none | committed | measured_vs_reference (`escort-AB-5pct-2026-08-11`, `escort-anchorfix-5pct-2026-08-12`) | pending |
| [explicit_w_zweck_purposes](../registry/features/explicit_w_zweck_purposes.yml) | behavior | active | ON | A/-/- | 2 | proven | marker | none | unvalidated (`smoke-control-fit-03101-2026-08-19`, `smoke-control-fit-03101-v2-2026-08-19`) | pending |
| [fine_teen_age_bands](../registry/features/fine_teen_age_bands.yml) | population | active | ON | A/A/- | 5 | proven | marker | none | unvalidated (`smoke-control-fit-03101-2026-08-19`, `smoke-control-fit-03101-v2-2026-08-19`) | pending |
| [fleet_consistency_v2](../registry/features/fleet_consistency_v2.yml) | fleet | active | ON | A/A/- | 4 | proven | marker | committed | unvalidated | pending |
| [fleet_electric_calibration](../registry/features/fleet_electric_calibration.yml) | fleet | active | ON | A/A/- | 1 | -- | marker | committed | unvalidated | pending |
| [fleet_hsn_tsn_attributes](../registry/features/fleet_hsn_tsn_attributes.yml) | fleet | active | ON | A/A/- | 1 | -- | marker | none | unvalidated | pending |
| [fleet_realism_upgrade](../registry/features/fleet_realism_upgrade.yml) | fleet | active | ON | A/A/- | 12 | not claimed | marker | committed | unvalidated | merge of issue, 2026-08-17 |
| [fleet_segment_brand_mix](../registry/features/fleet_segment_brand_mix.yml) | fleet | active | ON | A/A/- | 1 | -- | marker | committed | unvalidated | pending |
| [fleet_wohnmobile_age_tilt](../registry/features/fleet_wohnmobile_age_tilt.yml) | fleet | active | ON | A/A/- | 1 | proven | marker | committed | unvalidated | pending |
| [freight_analysis_exclusion](../registry/features/freight_analysis_exclusion.yml) | freight | active | ON | A/-/- | 1 | -- | marker | none | unvalidated | pending |
| [freight_assumptions](../registry/features/freight_assumptions.yml) | freight | active | ON | A/i/i | 0 | -- | none | assumption | unvalidated | pending |
| [freight_longhaul_v3](../registry/features/freight_longhaul_v3.yml) | freight | active | ON | A/-/- | 4 | not claimed | none | assumption | unvalidated | pending |
| [full_analysis](../registry/features/full_analysis.yml) | analysis | active | ON | A/s/s | 1 | -- | none | none | unvalidated | pending |
| [gravity_od](../registry/features/gravity_od.yml) | work | active | ON | A/A/A | 4 | -- | none | none | unvalidated | pending |
| [gravity_slope_by_rs7](../registry/features/gravity_slope_by_rs7.yml) | work | active | ON | A/A/A | 3 | -- | marker | none | unvalidated | pending |
| [household_fleet](../registry/features/household_fleet.yml) | fleet | active | ON | A/A/A | 1 | proven | marker | committed | unvalidated | pending |
| [household_income_distribution](../registry/features/household_income_distribution.yml) | attributes | active | off | -/-/A | 1 | proven | marker | committed | unvalidated | pending |
| [household_size_margin](../registry/features/household_size_margin.yml) | population | active | off | -/-/A | 1 | -- | none | none | unvalidated | pending |
| [housing_tenure](../registry/features/housing_tenure.yml) | attributes | active | ON | A/A/A | 1 | -- | marker | committed | unvalidated | pending |
| [income_aware_cars](../registry/features/income_aware_cars.yml) | attributes | active | off | -/-/A | 1 | proven | marker | committed | unvalidated | pending |
| [income_spatial_tilt](../registry/features/income_spatial_tilt.yml) | population | active | off | i/i/- | 3 | proven | marker | none | unvalidated | pending |
| [integerizer_quality](../registry/features/integerizer_quality.yml) | analysis | active | ON | A/s/- | 1 | -- | marker | none | unvalidated | pending |
| [ipf_synthesis_legacy](../registry/features/ipf_synthesis_legacy.yml) | population | supported | off | -/-/A | 3 | -- | marker | none | unvalidated | pending |
| [java_hang_watchdog](../registry/features/java_hang_watchdog.yml) | infrastructure | active | ON | A/A/A | 2 | proven | marker | none | unvalidated | pending |
| [joint_age_size_margin](../registry/features/joint_age_size_margin.yml) | population | supported | off | -/-/A | 1 | -- | none | none | unvalidated | pending |
| [kreis_income_control](../registry/features/kreis_income_control.yml) | attributes | active | off | i/i/- | 2 | proven | marker | committed | unvalidated | pending |
| [lod2_height_volume_typing](../registry/features/lod2_height_volume_typing.yml) | home | active | ON | A/A/A | 3 | -- | marker | none | unvalidated | pending |
| [matsim_output_archive](../registry/features/matsim_output_archive.yml) | infrastructure | active | ON | A/A/A | 1 | -- | marker | none | unvalidated | pending |
| [mid_validation_report](../registry/features/mid_validation_report.yml) | validation | active | ON | A/s/s | 1 | -- | none | committed | unvalidated | pending |
| [mode_choice](../registry/features/mode_choice.yml) | matsim | supported | off | i/i/i | 0 | -- | none | none | unvalidated | pending |
| [ownership_grid_1km](../registry/features/ownership_grid_1km.yml) | population | active | ON | A/-/- | 6 | proven | marker | committed | unvalidated (`smoke-ownership-grid-03101-2026-08-19`, `100pct-allfeat-i240-2026-08-20`) | pending |
| [parallel_chainsolvers](../registry/features/parallel_chainsolvers.yml) | infrastructure | active | ON | A/A/A | 4 | -- | marker | none | unvalidated | pending |
| [per_band_commute_friction](../registry/features/per_band_commute_friction.yml) | work | supported | off | i/i/i | 1 | proven | marker | committed | unvalidated | pending |
| [placement_income_l2](../registry/features/placement_income_l2.yml) | attributes | active | ON | A/A/- | 4 | proven | marker | committed | measured_vs_reference (`placement-income-l2-gate-2026-07-18`) | pending |
| [popsim_method](../registry/features/popsim_method.yml) | population | active | ON | A/A/- | 4 | -- | none | none | measured_vs_reference (`synth-100pct-2.2.0-2026-07-23`) | pending |
| [population_validation](../registry/features/population_validation.yml) | validation | active | ON | A/s/s | 1 | -- | marker | none | unvalidated | pending |
| [pt_subscription_conditioned](../registry/features/pt_subscription_conditioned.yml) | attributes | active | off | -/-/A | 1 | -- | marker | committed | unvalidated | pending |
| [pt_ticket_group_kreis_control](../registry/features/pt_ticket_group_kreis_control.yml) | attributes | active | ON | A/-/- | 4 | not claimed | marker | committed | unvalidated (`smoke-control-fit-03101-2026-08-19`, `smoke-control-fit-03101-v2-2026-08-19`) | pending |
| [reactivated_person_attributes](../registry/features/reactivated_person_attributes.yml) | attributes | active | off | -/-/A | 3 | proven | marker | committed | unvalidated | pending |
| [run_config_composition](../registry/features/run_config_composition.yml) | infrastructure | active | ON | A/s/s | 2 | -- | none | none | unvalidated | pending |
| [run_resource_recorder](../registry/features/run_resource_recorder.yml) | infrastructure | active | ON | A/A/A | 8 | proven | marker | none | unvalidated | pending |
| [secondary_distance_by_purpose](../registry/features/secondary_distance_by_purpose.yml) | secondary | active | ON | A/i/i | 4 | -- | none | committed | unvalidated | pending |
| [secondary_srv_location_types](../registry/features/secondary_srv_location_types.yml) | secondary | active | ON | A/-/- | 5 | proven | marker | committed | measured_vs_reference (`srv262-AB-5pct-2026-08-12`) | pending |
| [sector_aware_attraction_tilt](../registry/features/sector_aware_attraction_tilt.yml) | work | parked | off | i/i/i | 1 | -- | none | none | measured_vs_reference (`sector-aware-ab-2026-07-15`) | pending |
| [sex_aware_couples](../registry/features/sex_aware_couples.yml) | population | supported | off | -/-/A | 1 | proven | none | assumption | unvalidated | pending |
| [shared_stage_cache](../registry/features/shared_stage_cache.yml) | infrastructure | active | ON | A/s/s | 3 | -- | marker | none | unvalidated | pending |
| [simwrapper_export](../registry/features/simwrapper_export.yml) | analysis | active | ON | A/-/- | 5 | not claimed | marker | none | unvalidated | pending |
| [simwrapper_layer1](../registry/features/simwrapper_layer1.yml) | analysis | supported | ON | A/A/A | 1 | not claimed | none | none | unvalidated | pending |
| [srv_participation_controls](../registry/features/srv_participation_controls.yml) | attributes | active | ON | A/i/- | 6 | proven | none | committed | unvalidated | pending |
| [student_incommuters](../registry/features/student_incommuters.yml) | cordon | active | ON | A/A/- | 3 | proven | none | none | unvalidated | pending |
| [svb_wohn_work_production_mass](../registry/features/svb_wohn_work_production_mass.yml) | work | parked | off | i/i/i | 1 | proven | marker | none | measured_vs_reference (`verbindungen-ab-2026-07-16`) | pending |
| [taz_work_location_choice](../registry/features/taz_work_location_choice.yml) | work | parked | off | i/i/i | 3 | proven | none | committed | unvalidated | pending |
| [tier3_kreis_controls](../registry/features/tier3_kreis_controls.yml) | attributes | active | ON | A/i/- | 3 | -- | none | committed | unvalidated | pending |
| [tier_ab_caching](../registry/features/tier_ab_caching.yml) | infrastructure | active | ON | A/s/s | 3 | -- | none | none | unvalidated | pending |
| [urban_parking](../registry/features/urban_parking.yml) | matsim | active | ON | A/A/i | 4 | -- | none | none | unvalidated | pending |
| [verbindungen_anchor](../registry/features/verbindungen_anchor.yml) | work | active | ON | A/A/A | 1 | proven | none | committed | measured_vs_reference (`anchor-holdout-2026-07-17`) | pending |
| [verbindungen_od_validation](../registry/features/verbindungen_od_validation.yml) | validation | active | ON | A/-/- | 2 | -- | none | none | measured_vs_reference (`verbindungen-ab-2026-07-16`) | pending |
