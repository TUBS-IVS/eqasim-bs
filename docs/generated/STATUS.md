<!-- THIS FILE IS GENERATED. DO NOT EDIT MANUALLY.
     Rebuild: python -m braunschweig.documentation build
     Sources: docs/registry/** + docs/decisions/ + docs/runs/ -->

# Model status (generated)

Grouped by model area; every cell is derived from the registries and the
resolved canonical production configuration (`configs/base_bs.yml` + `configs/overlays/test_100pct.yml`).
Pipeline cells: popsim_mid/popsim_open/simple_ipf_open with
A=active, s=supported, i=inactive (wired, off), -=not used.

- Production population method (resolved config): `popsim_mid`
- `mode_choice` in the resolved production config: `False` -- no calibrated modal split exists; run mode shares are not behaviourally validated, and mode-share convergence is stability, not validation.
- Features: 79 | stages: 122 | datasets: 63 | ADRs: 105 | run manifests: 32

## Population synthesis

11 stage(s), 5 in the production DAG. Datasets: `cleancensus_kreis_controls`, `destatis_population_kreis`, `mid2023_b1`, `mid2023_bikes_by_rs7_haustyp`, `mid2023_cars_by_rs7_haustyp`, `srv2023_reference_tables`, `urbistat_gemeinde_age`, `zensus2022_age_sex_size`, `zensus2022_grid_cells`, `zensus2022_households_size`, `zensus2022_households_type`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Age-aware composition (#3b)](../registry/features/age_aware_composition.yml) | supported | off | -/-/A | unvalidated |  |
| [Fine teen age bands in the tier0 backbone (10-14 / 15-17 / 18-19)](../registry/features/fine_teen_age_bands.yml) | active | ON | A/A/- | unvalidated (`smoke-control-fit-03101-2026-08-19`, `smoke-control-fit-03101-v2-2026-08-19`) | [#320](https://github.com/TUBS-IVS/eqasim-bs/issues/320) |
| [Household-size margin](../registry/features/household_size_margin.yml) | active | off | -/-/A | unvalidated |  |
| [Income spatial tilt (Nettokaltmiete)](../registry/features/income_spatial_tilt.yml) | active | off | i/i/- | unvalidated |  |
| [IPF synthesis (legacy default)](../registry/features/ipf_synthesis_legacy.yml) | supported | off | -/-/A | unvalidated |  |
| [Joint age×size margin (#3)](../registry/features/joint_age_size_margin.yml) | supported | off | -/-/A | unvalidated |  |
| [1 km car/bike ownership shape controls (MiD-conditional, raked to the blended Kreis anchors)](../registry/features/ownership_grid_1km.yml) | active | ON | A/-/- | unvalidated (`smoke-ownership-grid-03101-2026-08-19`, `100pct-allfeat-i240-2026-08-20`) | [#240](https://github.com/TUBS-IVS/eqasim-bs/issues/240) |
| [popsim_open / popsim_mid](../registry/features/popsim_method.yml) | active | ON | A/A/- | measured_vs_reference (`synth-100pct-2.2.0-2026-07-23`) |  |
| [Sex-aware couples (~1.1%)](../registry/features/sex_aware_couples.yml) | supported | off | -/-/A | unvalidated |  |

## Person & household attributes

6 stage(s), 3 in the production DAG. Datasets: `genesis_svb_residence`, `inkar_household_income`, `kba_fe4_licences`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Consistent car_availability](../registry/features/consistent_car_availability.yml) | active | off | -/-/A | unvalidated |  |
| [Driving licence (P17.1, 3-margin IPF)](../registry/features/driving_licence_enrichment.yml) | active | off | -/-/A | unvalidated |  |
| [Economic status (Bayes hhtype×region)](../registry/features/economic_status_bayes.yml) | active | off | -/-/A | unvalidated |  |
| [Employment margin (IPF)](../registry/features/employment_margin.yml) | PARKED | off | -/-/i | unvalidated |  |
| [Household income € + distribution](../registry/features/household_income_distribution.yml) | active | off | -/-/A | unvalidated |  |
| [Housing tenure (completeness)](../registry/features/housing_tenure.yml) | active | ON | A/A/A | unvalidated |  |
| [Income-aware #cars](../registry/features/income_aware_cars.yml) | active | off | -/-/A | unvalidated |  |
| [Kreis income control (popsim)](../registry/features/kreis_income_control.yml) | active | off | i/i/- | unvalidated |  |
| [Placement income L2 (#108)](../registry/features/placement_income_l2.yml) | active | ON | A/A/- | measured_vs_reference (`placement-income-l2-gate-2026-07-18`) | [#108](https://github.com/TUBS-IVS/eqasim-bs/issues/108) |
| [PT subscription (P24.1, 3-margin IPF)](../registry/features/pt_subscription_conditioned.yml) | active | off | -/-/A | unvalidated |  |
| [PT-subscription per-Kreis control (four groups by default, three under the flag; MiD x SrV blend)](../registry/features/pt_ticket_group_kreis_control.yml) | active | ON | A/-/- | unvalidated (`smoke-control-fit-03101-2026-08-19`, `smoke-control-fit-03101-v2-2026-08-19`, `smoke-pt-never-group-03101-off-2026-08-21`, `smoke-pt-never-group-03101-on-2026-08-21`) | [#321](https://github.com/TUBS-IVS/eqasim-bs/issues/321) |
| [Reactivated attrs (couple/studies/SPC)](../registry/features/reactivated_person_attributes.yml) | active | off | -/-/A | unvalidated |  |
| [SrV participation controls (#224)](../registry/features/srv_participation_controls.yml) | active | ON | A/i/- | unvalidated | [#224](https://github.com/TUBS-IVS/eqasim-bs/issues/224) |
| [Tier-3 Kreis controls](../registry/features/tier3_kreis_controls.yml) | active | ON | A/i/- | unvalidated |  |

## Travel / activity behavior

15 stage(s), 8 in the production DAG. Datasets: `entd_2008`, `mid2023_b1`, `mid2023_home_office_day_donors`, `mid2023_reference_tables`, `mid2023_workday_location`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Commute-day-state model (far/weekly out-commuters)](../registry/features/commute_day_state.yml) | active | ON | A/-/- | unvalidated | [#244](https://github.com/TUBS-IVS/eqasim-bs/issues/244) |
| [Escort purpose family: dedicated purpose, household anchoring, distance-by-type, passive education (#201/#256/#257)](../registry/features/escort_purpose.yml) | active | ON | A/-/- | measured_vs_reference (`escort-AB-5pct-2026-08-11`, `escort-anchorfix-5pct-2026-08-12`) | [#201](https://github.com/TUBS-IVS/eqasim-bs/issues/201) |
| [Explicit W_ZWECK purpose mapping with a coverage guard](../registry/features/explicit_w_zweck_purposes.yml) | active | ON | A/-/- | unvalidated (`smoke-control-fit-03101-2026-08-19`, `smoke-control-fit-03101-v2-2026-08-19`) | [#241](https://github.com/TUBS-IVS/eqasim-bs/issues/241) |

## Vehicle fleet

3 stage(s), 3 in the production DAG. Datasets: `hsn_tsn_lookup`, `kba_fleet_derived`, `kba_wohnmobile_holder_age`, `mid2023_b1`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Fleet consistency v2 + income-age](../registry/features/fleet_consistency_v2.yml) | active | ON | A/A/- | unvalidated |  |
| [BEV/electric calibration](../registry/features/fleet_electric_calibration.yml) | active | ON | A/A/- | unvalidated |  |
| [HSN/TSN engine attrs (kW/ccm/fuel)](../registry/features/fleet_hsn_tsn_attributes.yml) | active | ON | A/A/- | unvalidated |  |
| [Fleet realism upgrade (all-Kreise fuel/euro, EV-income tilt, Euro-6 substage, RS7 cross-check)](../registry/features/fleet_realism_upgrade.yml) | active | ON | A/A/- | unvalidated | [#277](https://github.com/TUBS-IVS/eqasim-bs/issues/277) |
| [German fleet segment+brand mix](../registry/features/fleet_segment_brand_mix.yml) | active | ON | A/A/- | unvalidated |  |
| [Wohnmobile holder-age tilt (exact-marginal Bayes)](../registry/features/fleet_wohnmobile_age_tilt.yml) | active | ON | A/A/- | unvalidated | [#315](https://github.com/TUBS-IVS/eqasim-bs/issues/315) |
| [Household fleet (vs default car)](../registry/features/household_fleet.yml) | active | ON | A/A/A | unvalidated |  |

## Home locations

5 stage(s), 3 in the production DAG. Datasets: `alkis_buildings`, `lod2_heights`, `zensus2022_grid_cells`, `zensus2022_grid_open`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [ALKIS-typed home matching](../registry/features/alkis_home_matching.yml) | active | ON | A/A/- | unvalidated |  |
| [Cell-accurate homes (100m)](../registry/features/cell_accurate_homes.yml) | active | ON | A/A/- | unvalidated |  |
| [LoD2 height/volume typing](../registry/features/lod2_height_volume_typing.yml) | active | ON | A/A/A | unvalidated |  |

## Work locations

11 stage(s), 9 in the production DAG. Datasets: `ba_pendleratlas`, `building_activity_potentials`, `genesis_svb_workplace`, `mid2023_reference_tables`, `verbindungen`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Building potentials — work](../registry/features/building_potentials_work.yml) | active | ON | A/i/i | unvalidated |  |
| [Gravity OD (work/edu)](../registry/features/gravity_od.yml) | active | ON | A/A/A | unvalidated |  |
| [Per-RS7 gravity slope](../registry/features/gravity_slope_by_rs7.yml) | active | ON | A/A/A | unvalidated |  |
| [Calibration: per-band commute friction](../registry/features/per_band_commute_friction.yml) | supported | off | i/i/i | unvalidated |  |
| [Sector-aware attraction tilt (#128)](../registry/features/sector_aware_attraction_tilt.yml) | PARKED | off | i/i/i | measured_vs_reference (`sector-aware-ab-2026-07-15`) | [#128](https://github.com/TUBS-IVS/eqasim-bs/issues/128) |
| [svb_wohn work production mass (#132)](../registry/features/svb_wohn_work_production_mass.yml) | PARKED | off | i/i/i | measured_vs_reference (`verbindungen-ab-2026-07-16`) | [#132](https://github.com/TUBS-IVS/eqasim-bs/issues/132) |
| [TAZ sub-zonal work location choice](../registry/features/taz_work_location_choice.yml) | PARKED | off | i/i/i | unvalidated |  |
| [Inner VerBindungen calibration anchor (#193)](../registry/features/verbindungen_anchor.yml) | active | ON | A/A/A | measured_vs_reference (`anchor-holdout-2026-07-17`) | [#193](https://github.com/TUBS-IVS/eqasim-bs/issues/193) |

## Education

8 stage(s), 6 in the production DAG. Datasets: `lsn_bbs_share_by_age`, `lsn_kitas`, `lsn_schools`, `lsn_universities`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Building potentials — education](../registry/features/building_potentials_education.yml) | active | ON | A/A/- | unvalidated |  |
| [Education gravity (schools/Kita/uni)](../registry/features/education_gravity.yml) | active | ON | A/A/- | unvalidated |  |

## Secondary locations

10 stage(s), 10 in the production DAG. Datasets: `bosserhof_mappings`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Building potentials — secondary](../registry/features/building_potentials_secondary.yml) | active | ON | A/i/i | unvalidated |  |
| [Calibration: Tier-3 detour/circuity curve](../registry/features/detour_circuity_curve.yml) | supported | off | i/i/i | unvalidated |  |
| [Calibration: purpose-resolved secondary](../registry/features/secondary_distance_by_purpose.yml) | active | ON | A/i/i | unvalidated |  |
| [SrV-grounded secondary location types for leisure/other (#262)](../registry/features/secondary_srv_location_types.yml) | active | ON | A/-/- | measured_vs_reference (`srv262-AB-5pct-2026-08-12`) | [#262](https://github.com/TUBS-IVS/eqasim-bs/issues/262) |

## Cordon / external demand

7 stage(s), 7 in the production DAG. Datasets: `ba_pendleratlas`, `destatis_population_kreis`, `gtfs_feed`, `lsn_universities`, `mikrozensus_pendler_modes`, `osm_cordon_ring`, `vg250_ew`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Gates (road + PT/Bahnhof)](../registry/features/cordon_gates.yml) | active | ON | A/A/- | unvalidated |  |
| [Mode balancer](../registry/features/cordon_mode_balancer.yml) | active | ON | A/A/- | unvalidated |  |
| [Cordon network ring + cut](../registry/features/cordon_network_ring.yml) | active | ON | A/A/- | unvalidated |  |
| [Einpendler injection](../registry/features/einpendler_injection.yml) | active | ON | A/A/- | unvalidated |  |
| [Student in-commuters (#140)](../registry/features/student_incommuters.yml) | active | ON | A/A/- | unvalidated | [#140](https://github.com/TUBS-IVS/eqasim-bs/issues/140) |

## Freight

3 stage(s), 3 in the production DAG. Datasets: `german_wide_freight_v3`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Freight analysis exclusion](../registry/features/freight_analysis_exclusion.yml) | active | ON | A/-/- | unvalidated |  |
| [Assumptions (truck PCE / max velocity)](../registry/features/freight_assumptions.yml) | active | ON | A/i/i | unvalidated |  |
| [Long-haul freight injection (v3)](../registry/features/freight_longhaul_v3.yml) | active | ON | A/-/- | unvalidated |  |

## MATSim

13 stage(s), 13 in the production DAG. Datasets: `gtfs_feed`, `osm_niedersachsen`, `vrb_tariff_zones`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Carless routing re-mode](../registry/features/carless_routing_remode.yml) | active | ON | A/A/s | unvalidated |  |
| [Mode choice](../registry/features/mode_choice.yml) | supported | off | i/i/i | unvalidated |  |
| [Urban parking (BS inner ring)](../registry/features/urban_parking.yml) | active | ON | A/A/i | unvalidated |  |

## Analysis

2 stage(s), 2 in the production DAG. Datasets: --

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Full analysis (dashboard+MiD)](../registry/features/full_analysis.yml) | active | ON | A/s/s | unvalidated |  |
| [Integerizer quality (per-cell error map)](../registry/features/integerizer_quality.yml) | active | ON | A/s/- | unvalidated |  |
| [SimWrapper export (8 chart + 4 map + commuter tabs)](../registry/features/simwrapper_export.yml) | active | ON | A/-/- | unvalidated |  |
| [SimWrapper Layer-1 (MATSim Java contrib)](../registry/features/simwrapper_layer1.yml) | supported | ON | A/A/A | unvalidated |  |

## Validation

7 stage(s), 6 in the production DAG. Datasets: `mid2023_reference_tables`, `srv2023_primary_distance_targets`, `srv2023_work_participation`, `verbindungen`, `vg250_ew`

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Commute day state -- Phase A measurement](../registry/features/commute_day_state_measurement.yml) | active | ON | A/s/s | measured_vs_reference (`commute-day-state-phase-a-2026-09-05`) | [#244](https://github.com/TUBS-IVS/eqasim-bs/issues/244) |
| [Education enrollment validation](../registry/features/education_enrollment_validation.yml) | active | ON | A/s/- | unvalidated |  |
| [MiD validation report](../registry/features/mid_validation_report.yml) | active | ON | A/s/s | unvalidated |  |
| [Population validation (controls/quality/geo)](../registry/features/population_validation.yml) | active | ON | A/s/s | unvalidated |  |
| [SrV primary-distance validation per Kreis](../registry/features/srv_primary_distance_validation.yml) | active | ON | A/s/s | measured_vs_reference (`srv-primary-distance-baseline-2026-09-03`, `srv-primary-distance-baseline-2026-09-04`) | [#358](https://github.com/TUBS-IVS/eqasim-bs/issues/358) |
| [VerBindungen sub-Kreis OD validation (#124)](../registry/features/verbindungen_od_validation.yml) | active | ON | A/-/- | measured_vs_reference (`verbindungen-ab-2026-07-16`) | [#124](https://github.com/TUBS-IVS/eqasim-bs/issues/124) |

## Infrastructure

6 stage(s), 6 in the production DAG. Datasets: --

| Feature | Lifecycle | Prod | Pipelines | Validation | Issue |
|---|---|---|---|---|---|
| [Own eqasim-java-bs fork (2.3.1, matsim.output e2e-green 2026-07-23 at 2.2.0)](../registry/features/eqasim_java_fork.yml) | active | ON | A/A/A | unvalidated (`matsim-e2e-2.2.0-kreis03101-2026-07-23`) |  |
| [CPU-accumulation hang watchdog for the pipeline's Java subprocesses](../registry/features/java_hang_watchdog.yml) | active | ON | A/A/A | unvalidated | [#330](https://github.com/TUBS-IVS/eqasim-bs/issues/330) |
| [MATSim output archive (run-named durable copy)](../registry/features/matsim_output_archive.yml) | active | ON | A/A/A | unvalidated |  |
| [Parallel chainsolvers](../registry/features/parallel_chainsolvers.yml) | active | ON | A/A/A | unvalidated |  |
| [Run-config composition (base + per-scale overlay)](../registry/features/run_config_composition.yml) | active | ON | A/s/s | unvalidated |  |
| [Per-run resource time series (tree CPU, peak per-process RSS, RAM/swap/disk/IO, stage-tagged)](../registry/features/run_resource_recorder.yml) | active | ON | A/A/A | unvalidated | [#350](https://github.com/TUBS-IVS/eqasim-bs/issues/350) |
| [Shared stage-cache (prime-on-launch)](../registry/features/shared_stage_cache.yml) | active | ON | A/s/s | unvalidated |  |
| [Tier-A/B caching (32 stages + popsim)](../registry/features/tier_ab_caching.yml) | active | ON | A/s/s | unvalidated |  |

## Spatial base data

15 stage(s), 14 in the production DAG. Datasets: `alkis_buildings`, `atkis_landuse`, `osm_niedersachsen`, `regiostar`, `verbindungen`, `vg250_ew`
