<!-- THIS FILE IS GENERATED. DO NOT EDIT MANUALLY.
     Rebuild: python -m braunschweig.documentation build
     Sources: docs/registry/** + docs/decisions/ + docs/runs/ -->

# Stage registry (generated)

One row per synpp stage record in `docs/registry/stages/` (stage ids are
the DAG node names; aliased seams list their per-workflow resolution).

| Stage | Layer | Lineage | Prod | mid/open/ipf | Resolves to | Features |
|---|---|---|---|---|---|---|
| [braunschweig.analysis.analysis_suite](../registry/stages/braunschweig.analysis.analysis_suite.yml) | analysis | braunschweig_new | x | A/-/- | -- | freight_analysis_exclusion |
| [braunschweig.analysis.cordon_validation](../registry/stages/braunschweig.analysis.cordon_validation.yml) | validation | braunschweig_new | x | A/-/- | -- | -- |
| [braunschweig.analysis.simwrapper_export](../registry/stages/braunschweig.analysis.simwrapper_export.yml) | analysis | braunschweig_new | x | A/-/- | -- | simwrapper_export |
| [braunschweig.analysis.verbindungen_validation](../registry/stages/braunschweig.analysis.verbindungen_validation.yml) | validation | braunschweig_new | x | A/-/- | -- | verbindungen_od_validation |
| [braunschweig.data.alkis](../registry/stages/braunschweig.data.alkis.yml) | spatial | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.bbsr.regiostar](../registry/stages/braunschweig.data.bbsr.regiostar.yml) | spatial | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.bosserhof_location_category](../registry/stages/braunschweig.data.bosserhof_location_category.yml) | secondary | braunschweig_new | x | A/-/- | -- | secondary_srv_location_types |
| [braunschweig.data.bosserhof_purpose](../registry/stages/braunschweig.data.bosserhof_purpose.yml) | secondary | braunschweig_new | x | A/-/- | -- | -- |
| [braunschweig.data.building_potentials](../registry/stages/braunschweig.data.building_potentials.yml) | work | braunschweig_new | x | A/-/- | -- | building_potentials_secondary, building_potentials_work |
| [braunschweig.data.buildings](../registry/stages/braunschweig.data.buildings.yml) | home | braunschweig_new | x | A/A/A | -- | alkis_home_matching, lod2_height_volume_typing |
| [braunschweig.data.census.employees](../registry/stages/braunschweig.data.census.employees.yml) | work | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.census.employment](../registry/stages/braunschweig.data.census.employment.yml) | attributes | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.census.household_income](../registry/stages/braunschweig.data.census.household_income.yml) | attributes | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.data.census.household_size](../registry/stages/braunschweig.data.census.household_size.yml) | population | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.data.census.households_size_age](../registry/stages/braunschweig.data.census.households_size_age.yml) | population | braunschweig_new | -- | -/-/A | -- | joint_age_size_margin |
| [braunschweig.data.census.households_type](../registry/stages/braunschweig.data.census.households_type.yml) | population | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.data.census.licenses](../registry/stages/braunschweig.data.census.licenses.yml) | attributes | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.data.census.pendler](../registry/stages/braunschweig.data.census.pendler.yml) | cordon | braunschweig_new | x | A/A/A | -- | einpendler_injection |
| [braunschweig.data.census.population](../registry/stages/braunschweig.data.census.population.yml) | population | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.data.cordon_gemeinden](../registry/stages/braunschweig.data.cordon_gemeinden.yml) | cordon | braunschweig_new | x | A/A/- | -- | -- |
| [braunschweig.data.cordon_network](../registry/stages/braunschweig.data.cordon_network.yml) | cordon | braunschweig_new | x | A/A/- | -- | cordon_network_ring |
| [braunschweig.data.cordon_pt_gates](../registry/stages/braunschweig.data.cordon_pt_gates.yml) | cordon | braunschweig_new | x | A/A/- | -- | cordon_gates |
| [braunschweig.data.education.student_share](../registry/stages/braunschweig.data.education.student_share.yml) | education | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.data.external_secondary_points](../registry/stages/braunschweig.data.external_secondary_points.yml) | secondary | braunschweig_new | x | A/-/- | -- | secondary_srv_location_types |
| [braunschweig.data.external_workplaces](../registry/stages/braunschweig.data.external_workplaces.yml) | work | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.freight.german_wide](../registry/stages/braunschweig.data.freight.german_wide.yml) | freight | braunschweig_new | x | A/-/- | -- | freight_longhaul_v3 |
| [braunschweig.data.hts.mid_donor](../registry/stages/braunschweig.data.hts.mid_donor.yml) | population | braunschweig_new | x | A/A/- | -- | popsim_method |
| [braunschweig.data.inkar.household_income](../registry/stages/braunschweig.data.inkar.household_income.yml) | attributes | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.landuse](../registry/stages/braunschweig.data.landuse.yml) | spatial | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.locations](../registry/stages/braunschweig.data.locations.yml) | secondary | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.mid.data](../registry/stages/braunschweig.data.mid.data.yml) | behavior | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.data.mid.references](../registry/stages/braunschweig.data.mid.references.yml) | validation | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.data.mid.zones](../registry/stages/braunschweig.data.mid.zones.yml) | behavior | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.data.osm](../registry/stages/braunschweig.data.osm.yml) | spatial | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.schools.facilities](../registry/stages/braunschweig.data.schools.facilities.yml) | education | braunschweig_new | x | A/A/- | -- | education_gravity |
| [braunschweig.data.schools.kita_facilities](../registry/stages/braunschweig.data.schools.kita_facilities.yml) | education | braunschweig_new | x | A/A/- | -- | building_potentials_education |
| [braunschweig.data.schools.university_facilities](../registry/stages/braunschweig.data.schools.university_facilities.yml) | education | braunschweig_new | x | A/A/- | -- | building_potentials_education |
| [braunschweig.data.spatial.taz](../registry/stages/braunschweig.data.spatial.taz.yml) | spatial | braunschweig_new | -- | -/-/- | -- | taz_work_location_choice |
| [braunschweig.data.verbindungen.margins](../registry/stages/braunschweig.data.verbindungen.margins.yml) | validation | braunschweig_new | x | A/-/- | -- | -- |
| [braunschweig.data.verbindungen.work_od](../registry/stages/braunschweig.data.verbindungen.work_od.yml) | work | braunschweig_new | x | A/A/A | -- | verbindungen_anchor, verbindungen_od_validation |
| [braunschweig.data.verbindungen.zones](../registry/stages/braunschweig.data.verbindungen.zones.yml) | spatial | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.vrb.zones](../registry/stages/braunschweig.data.vrb.zones.yml) | matsim | braunschweig_new | x | A/A/A | -- | -- |
| [braunschweig.data.zensus_grid.population](../registry/stages/braunschweig.data.zensus_grid.population.yml) | home | braunschweig_new | -- | -/-/A | -- | -- |
| [braunschweig.freight.extraction](../registry/stages/braunschweig.freight.extraction.yml) | freight | braunschweig_new | x | A/-/- | -- | freight_longhaul_v3 |
| [braunschweig.freight.trips](../registry/stages/braunschweig.freight.trips.yml) | freight | braunschweig_new | x | A/-/- | -- | freight_longhaul_v3 |
| [braunschweig.gravity.distance_matrix_taz](../registry/stages/braunschweig.gravity.distance_matrix_taz.yml) | work | braunschweig_new | -- | -/-/- | -- | taz_work_location_choice |
| [braunschweig.ipf.model](../registry/stages/braunschweig.ipf.model.yml) | population | braunschweig_new | -- | -/-/A | -- | employment_margin, household_size_margin, ipf_synthesis_legacy |
| [braunschweig.ipf.prepare](../registry/stages/braunschweig.ipf.prepare.yml) | population | braunschweig_new | -- | -/-/A | -- | household_size_margin, ipf_synthesis_legacy, joint_age_size_margin |
| [braunschweig.locations.work](../registry/stages/braunschweig.locations.work.yml) | work | braunschweig_new | x | A/-/- | -- | building_potentials_work, taz_work_location_choice |
| [braunschweig.popsim.completed_donor](../registry/stages/braunschweig.popsim.completed_donor.yml) | population | braunschweig_new | x | A/-/- | -- | popsim_method |
| [braunschweig.synthesis.cordon_gates](../registry/stages/braunschweig.synthesis.cordon_gates.yml) | cordon | braunschweig_new | x | A/A/- | -- | cordon_gates |
| [braunschweig.synthesis.incommuters](../registry/stages/braunschweig.synthesis.incommuters.yml) | cordon | braunschweig_new | x | A/A/- | -- | cordon_mode_balancer, einpendler_injection |
| [braunschweig.synthesis.locations.education_gravity](../registry/stages/braunschweig.synthesis.locations.education_gravity.yml) | education | braunschweig_new | x | A/A/- | -- | education_gravity |
| [braunschweig.synthesis.locations.secondary_candidates](../registry/stages/braunschweig.synthesis.locations.secondary_candidates.yml) | secondary | braunschweig_new | x | A/-/- | -- | building_potentials_secondary |
| [braunschweig.synthesis.student_incommuters](../registry/stages/braunschweig.synthesis.student_incommuters.yml) | cordon | braunschweig_new | x | A/A/- | -- | student_incommuters |
| [braunschweig.synthesis.vehicles.cars.household](../registry/stages/braunschweig.synthesis.vehicles.cars.household.yml) | fleet | braunschweig_new | x | A/A/A | -- | fleet_consistency_v2, fleet_electric_calibration, fleet_hsn_tsn_attributes, fleet_realism_upgrade, fleet_segment_brand_mix, household_fleet |
| [data.census.filtered](../registry/stages/data.census.filtered.yml) | population | overridden | x | A/A/A | popsim_mid: `braunschweig.popsim.stage`<br>popsim_open: `braunschweig.popsim.stage`<br>simple_ipf_open: `braunschweig.ipf.attributed` | age_aware_composition, housing_tenure, income_spatial_tilt, ipf_synthesis_legacy, kreis_income_control, ownership_grid_1km, placement_income_l2, popsim_method, sex_aware_couples, srv_participation_controls, tier3_kreis_controls |
| [data.gtfs.cleaned](../registry/stages/data.gtfs.cleaned.yml) | matsim | configured | x | A/A/A | -- | -- |
| [data.hts.commute_distance](../registry/stages/data.hts.commute_distance.yml) | behavior | inherited | -- | -/-/A | -- | -- |
| [data.hts.entd.cleaned](../registry/stages/data.hts.entd.cleaned.yml) | behavior | configured | x | A/A/A | -- | -- |
| [data.hts.entd.filtered](../registry/stages/data.hts.entd.filtered.yml) | behavior | inherited | -- | -/A/A | -- | -- |
| [data.hts.entd.raw](../registry/stages/data.hts.entd.raw.yml) | behavior | configured | x | A/A/A | -- | -- |
| [data.hts.entd.reweighted](../registry/stages/data.hts.entd.reweighted.yml) | behavior | inherited | -- | -/A/A | -- | -- |
| [data.hts.selected](../registry/stages/data.hts.selected.yml) | behavior | inherited | -- | -/A/A | -- | -- |
| [data.od.weighted](../registry/stages/data.od.weighted.yml) | work | overridden | x | A/A/A | popsim_mid: `braunschweig.gravity.model`<br>popsim_open: `braunschweig.gravity.model`<br>simple_ipf_open: `braunschweig.gravity.model` | gravity_od, gravity_slope_by_rs7, per_band_commute_friction, sector_aware_attraction_tilt, svb_wohn_work_production_mass, taz_work_location_choice, verbindungen_anchor |
| [data.osm.cleaned](../registry/stages/data.osm.cleaned.yml) | spatial | configured | x | A/A/A | -- | -- |
| [data.osm.osmosis](../registry/stages/data.osm.osmosis.yml) | spatial | inherited | x | A/A/A | -- | -- |
| [data.spatial.codes](../registry/stages/data.spatial.codes.yml) | spatial | overridden | x | A/A/A | popsim_mid: `eqasim_common.spatial.entd_codes`<br>popsim_open: `eqasim_common.spatial.entd_codes`<br>simple_ipf_open: `eqasim_common.spatial.entd_codes` | -- |
| [data.spatial.departments](../registry/stages/data.spatial.departments.yml) | spatial | configured | x | A/A/A | -- | -- |
| [data.spatial.iris](../registry/stages/data.spatial.iris.yml) | spatial | overridden | x | A/A/A | popsim_mid: `eqasim_common.data.spatial.iris`<br>popsim_open: `eqasim_common.data.spatial.iris`<br>simple_ipf_open: `eqasim_common.data.spatial.iris` | -- |
| [data.spatial.municipalities](../registry/stages/data.spatial.municipalities.yml) | spatial | configured | x | A/A/A | -- | -- |
| [documentation.meta_output](../registry/stages/documentation.meta_output.yml) | infrastructure | inherited | x | A/A/A | -- | -- |
| [eqasim_common.data.osm.chunked](../registry/stages/eqasim_common.data.osm.chunked.yml) | spatial | inherited | x | A/A/A | -- | -- |
| [eqasim_common.data.osm.locations](../registry/stages/eqasim_common.data.osm.locations.yml) | secondary | inherited | x | A/A/A | -- | -- |
| [eqasim_common.data.osm.osmconvert](../registry/stages/eqasim_common.data.osm.osmconvert.yml) | spatial | inherited | x | A/A/A | -- | -- |
| [eqasim_common.data.population.raw](../registry/stages/eqasim_common.data.population.raw.yml) | population | inherited | x | A/A/A | -- | -- |
| [eqasim_common.gravity.distance_matrix](../registry/stages/eqasim_common.gravity.distance_matrix.yml) | work | extended | x | A/A/A | -- | -- |
| [eqasim_common.locations.synthesis.education](../registry/stages/eqasim_common.locations.synthesis.education.yml) | education | inherited | -- | -/-/A | -- | -- |
| [eqasim_common.spatial.codes](../registry/stages/eqasim_common.spatial.codes.yml) | spatial | inherited | x | A/A/A | -- | -- |
| [matsim.output](../registry/stages/matsim.output.yml) | matsim | extended | x | A/A/A | -- | matsim_output_archive |
| [matsim.runtime.eqasim](../registry/stages/matsim.runtime.eqasim.yml) | infrastructure | extended | x | A/A/A | -- | eqasim_java_fork |
| [matsim.runtime.git](../registry/stages/matsim.runtime.git.yml) | infrastructure | inherited | x | A/A/A | -- | -- |
| [matsim.runtime.java](../registry/stages/matsim.runtime.java.yml) | infrastructure | configured | x | A/A/A | -- | -- |
| [matsim.runtime.maven](../registry/stages/matsim.runtime.maven.yml) | infrastructure | inherited | x | A/A/A | -- | -- |
| [matsim.runtime.pt2matsim](../registry/stages/matsim.runtime.pt2matsim.yml) | matsim | inherited | x | A/A/A | -- | -- |
| [matsim.scenario.facilities](../registry/stages/matsim.scenario.facilities.yml) | matsim | overridden | x | A/A/A | popsim_mid: `braunschweig.matsim.scenario.facilities`<br>popsim_open: `braunschweig.matsim.scenario.facilities` | -- |
| [matsim.scenario.households](../registry/stages/matsim.scenario.households.yml) | matsim | overridden | x | A/A/A | popsim_mid: `braunschweig.matsim.scenario.households`<br>popsim_open: `braunschweig.matsim.scenario.households` | -- |
| [matsim.scenario.population](../registry/stages/matsim.scenario.population.yml) | matsim | overridden | x | A/A/A | popsim_mid: `braunschweig.matsim.scenario.population`<br>popsim_open: `braunschweig.matsim.scenario.population` | carless_routing_remode, urban_parking |
| [matsim.scenario.supply.gtfs](../registry/stages/matsim.scenario.supply.gtfs.yml) | matsim | configured | x | A/A/A | -- | -- |
| [matsim.scenario.supply.osm](../registry/stages/matsim.scenario.supply.osm.yml) | matsim | configured | x | A/A/A | -- | -- |
| [matsim.scenario.supply.processed](../registry/stages/matsim.scenario.supply.processed.yml) | matsim | inherited | x | A/A/A | -- | -- |
| [matsim.scenario.vehicles](../registry/stages/matsim.scenario.vehicles.yml) | matsim | overridden | x | A/A/A | popsim_mid: `braunschweig.matsim.scenario.vehicles`<br>popsim_open: `braunschweig.matsim.scenario.vehicles` | -- |
| [matsim.simulation.prepare](../registry/stages/matsim.simulation.prepare.yml) | matsim | overridden | x | A/A/A | popsim_mid: `braunschweig.matsim.simulation.prepare`<br>popsim_open: `braunschweig.matsim.simulation.prepare`<br>simple_ipf_open: `braunschweig.matsim.simulation.prepare` | cordon_network_ring, freight_assumptions, mode_choice |
| [matsim.simulation.run](../registry/stages/matsim.simulation.run.yml) | matsim | extended | x | A/A/A | -- | simwrapper_layer1 |
| [synthesis.locations.education](../registry/stages/synthesis.locations.education.yml) | education | overridden | x | A/A/A | popsim_mid: `eqasim_common.locations.education`<br>popsim_open: `eqasim_common.locations.education`<br>simple_ipf_open: `eqasim_common.locations.education` | building_potentials_education |
| [synthesis.locations.home.locations](../registry/stages/synthesis.locations.home.locations.yml) | home | overridden | -- | -/-/A | popsim_mid: `braunschweig.locations.home`<br>popsim_open: `braunschweig.locations.home`<br>simple_ipf_open: `braunschweig.locations.home` | -- |
| [synthesis.locations.secondary](../registry/stages/synthesis.locations.secondary.yml) | secondary | overridden | x | A/A/A | popsim_mid: `braunschweig.locations.secondary`<br>popsim_open: `braunschweig.locations.secondary`<br>simple_ipf_open: `braunschweig.locations.secondary` | -- |
| [synthesis.locations.work](../registry/stages/synthesis.locations.work.yml) | work | overridden | -- | -/A/A | popsim_mid: `braunschweig.locations.work`<br>popsim_open: `braunschweig.locations.work`<br>simple_ipf_open: `braunschweig.locations.work` | -- |
| [synthesis.output](../registry/stages/synthesis.output.yml) | infrastructure | extended | x | A/A/A | -- | -- |
| [synthesis.population.activities](../registry/stages/synthesis.population.activities.yml) | behavior | inherited | x | A/A/A | -- | -- |
| [synthesis.population.enriched](../registry/stages/synthesis.population.enriched.yml) | attributes | overridden | x | A/A/A | popsim_mid: `braunschweig.popsim.enriched_adapter`<br>popsim_open: `braunschweig.popsim.enriched_adapter`<br>simple_ipf_open: `braunschweig.synthesis.population.enriched` | consistent_car_availability, driving_licence_enrichment, economic_status_bayes, household_income_distribution, housing_tenure, income_aware_cars, pt_subscription_conditioned, reactivated_person_attributes |
| [synthesis.population.income.selected](../registry/stages/synthesis.population.income.selected.yml) | attributes | overridden | -- | -/-/A | popsim_mid: `braunschweig.synthesis.income`<br>popsim_open: `braunschweig.synthesis.income`<br>simple_ipf_open: `braunschweig.synthesis.income` | -- |
| [synthesis.population.matched](../registry/stages/synthesis.population.matched.yml) | behavior | inherited | -- | -/-/A | -- | reactivated_person_attributes |
| [synthesis.population.sampled](../registry/stages/synthesis.population.sampled.yml) | population | inherited | x | A/A/A | -- | -- |
| [synthesis.population.spatial.commute_distance](../registry/stages/synthesis.population.spatial.commute_distance.yml) | work | overridden | x | A/A/A | popsim_mid: `braunschweig.popsim.commute_distance`<br>popsim_open: `braunschweig.popsim.commute_distance`<br>simple_ipf_open: `braunschweig.synthesis.spatial.commute_distance` | -- |
| [synthesis.population.spatial.home.locations](../registry/stages/synthesis.population.spatial.home.locations.yml) | home | overridden | x | A/A/A | popsim_mid: `braunschweig.synthesis.locations.home_cell`<br>popsim_open: `braunschweig.synthesis.locations.home_cell` | alkis_home_matching, cell_accurate_homes |
| [synthesis.population.spatial.home.zones](../registry/stages/synthesis.population.spatial.home.zones.yml) | home | overridden | x | A/A/A | popsim_mid: `braunschweig.synthesis.spatial.home_zones`<br>popsim_open: `braunschweig.synthesis.spatial.home_zones`<br>simple_ipf_open: `braunschweig.synthesis.spatial.home_zones` | -- |
| [synthesis.population.spatial.locations](../registry/stages/synthesis.population.spatial.locations.yml) | secondary | inherited | x | A/A/A | -- | -- |
| [synthesis.population.spatial.primary.candidates](../registry/stages/synthesis.population.spatial.primary.candidates.yml) | work | inherited | x | A/A/A | -- | -- |
| [synthesis.population.spatial.primary.locations](../registry/stages/synthesis.population.spatial.primary.locations.yml) | education | overridden | x | A/A/A | popsim_mid: `braunschweig.locations.synthesis.replacement_education_gravity`<br>popsim_open: `braunschweig.locations.synthesis.replacement_education_gravity`<br>simple_ipf_open: `eqasim_common.locations.synthesis.replacement` | education_gravity |
| [synthesis.population.spatial.secondary.distance_distributions](../registry/stages/synthesis.population.spatial.secondary.distance_distributions.yml) | secondary | overridden | x | A/A/A | popsim_mid: `braunschweig.popsim.distance_distributions` | secondary_distance_by_purpose |
| [synthesis.population.spatial.secondary.locations](../registry/stages/synthesis.population.spatial.secondary.locations.yml) | secondary | overridden | x | A/A/A | popsim_mid: `braunschweig.synthesis.locations.secondary_chainsolvers`<br>popsim_open: `braunschweig.synthesis.locations.secondary_chainsolvers`<br>simple_ipf_open: `braunschweig.synthesis.locations.secondary_chainsolvers` | building_potentials_secondary, detour_circuity_curve, escort_purpose, parallel_chainsolvers, secondary_distance_by_purpose, secondary_srv_location_types |
| [synthesis.population.trips](../registry/stages/synthesis.population.trips.yml) | behavior | overridden | x | A/A/A | popsim_mid: `braunschweig.popsim.trips_stage`<br>popsim_open: `braunschweig.popsim.trips_stage` | escort_purpose |
| [synthesis.vehicles.passengers.default](../registry/stages/synthesis.vehicles.passengers.default.yml) | fleet | inherited | x | A/A/A | -- | -- |
| [synthesis.vehicles.vehicles](../registry/stages/synthesis.vehicles.vehicles.yml) | fleet | extended | x | A/A/A | -- | household_fleet |
