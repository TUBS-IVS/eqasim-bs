<!-- THIS FILE IS GENERATED. DO NOT EDIT MANUALLY.
     Rebuild: python -m braunschweig.documentation build
     Sources: docs/registry/** + docs/decisions/ + docs/runs/ -->

# Bavaria -> Braunschweig lineage (generated)

How the current model relates to its `eqasim-org/eqasim-bavaria` baseline
(fork point `b20fbe6`, 2025-10-06 -- ADR-0000; delta record: `docs/UPSTREAM_DELTA.md`;
upstream eqasim-france fix sweeps: `docs/UPSTREAM_FIX_SWEEP.md`).

Lineage classes: `inherited` (upstream code, possibly relocated),
`configured` (upstream code, regional inputs/config only), `extended`
(upstream mechanism with added behavior), `overridden` (upstream stage
name resolved to a regional implementation via the config alias table),
`braunschweig_new` (built for this model), `upstream_port` (mechanism
ported from another eqasim project), `retired`.

| Lineage | Stages |
|---|---|
| inherited | 22 |
| configured | 8 |
| extended | 7 |
| overridden | 22 |
| braunschweig_new | 58 |
| upstream_port | 0 |
| retired | 0 |

## Override seams (upstream stage -> regional implementation)

| Upstream stage name | popsim_mid | popsim_open | simple_ipf_open |
|---|---|---|---|
| `data.census.filtered` | `braunschweig.popsim.stage` | `braunschweig.popsim.stage` | `braunschweig.ipf.attributed` |
| `data.od.weighted` | `braunschweig.gravity.model` | `braunschweig.gravity.model` | `braunschweig.gravity.model` |
| `data.spatial.codes` | `eqasim_common.spatial.entd_codes` | `eqasim_common.spatial.entd_codes` | `eqasim_common.spatial.entd_codes` |
| `data.spatial.iris` | `eqasim_common.data.spatial.iris` | `eqasim_common.data.spatial.iris` | `eqasim_common.data.spatial.iris` |
| `matsim.scenario.facilities` | `braunschweig.matsim.scenario.facilities` | `braunschweig.matsim.scenario.facilities` | `=` |
| `matsim.scenario.households` | `braunschweig.matsim.scenario.households` | `braunschweig.matsim.scenario.households` | `=` |
| `matsim.scenario.population` | `braunschweig.matsim.scenario.population` | `braunschweig.matsim.scenario.population` | `=` |
| `matsim.scenario.vehicles` | `braunschweig.matsim.scenario.vehicles` | `braunschweig.matsim.scenario.vehicles` | `=` |
| `matsim.simulation.prepare` | `braunschweig.matsim.simulation.prepare` | `braunschweig.matsim.simulation.prepare` | `braunschweig.matsim.simulation.prepare` |
| `synthesis.locations.education` | `eqasim_common.locations.education` | `eqasim_common.locations.education` | `eqasim_common.locations.education` |
| `synthesis.locations.home.locations` | `braunschweig.locations.home` | `braunschweig.locations.home` | `braunschweig.locations.home` |
| `synthesis.locations.secondary` | `braunschweig.locations.secondary` | `braunschweig.locations.secondary` | `braunschweig.locations.secondary` |
| `synthesis.locations.work` | `braunschweig.locations.work` | `braunschweig.locations.work` | `braunschweig.locations.work` |
| `synthesis.population.enriched` | `braunschweig.popsim.enriched_adapter` | `braunschweig.popsim.enriched_adapter` | `braunschweig.synthesis.population.enriched` |
| `synthesis.population.income.selected` | `braunschweig.synthesis.income` | `braunschweig.synthesis.income` | `braunschweig.synthesis.income` |
| `synthesis.population.spatial.commute_distance` | `braunschweig.popsim.commute_distance` | `braunschweig.popsim.commute_distance` | `braunschweig.synthesis.spatial.commute_distance` |
| `synthesis.population.spatial.home.locations` | `braunschweig.synthesis.locations.home_cell` | `braunschweig.synthesis.locations.home_cell` | `=` |
| `synthesis.population.spatial.home.zones` | `braunschweig.synthesis.spatial.home_zones` | `braunschweig.synthesis.spatial.home_zones` | `braunschweig.synthesis.spatial.home_zones` |
| `synthesis.population.spatial.primary.locations` | `braunschweig.locations.synthesis.replacement_education_gravity` | `braunschweig.locations.synthesis.replacement_education_gravity` | `eqasim_common.locations.synthesis.replacement` |
| `synthesis.population.spatial.secondary.distance_distributions` | `braunschweig.popsim.distance_distributions` | `=` | `=` |
| `synthesis.population.spatial.secondary.locations` | `braunschweig.synthesis.locations.secondary_chainsolvers` | `braunschweig.synthesis.locations.secondary_chainsolvers` | `braunschweig.synthesis.locations.secondary_chainsolvers` |
| `synthesis.population.trips` | `braunschweig.popsim.trips_stage` | `braunschweig.popsim.trips_stage` | `=` |

## Braunschweig-new stages per model area

- **Analysis**: `braunschweig.analysis.analysis_suite`, `braunschweig.analysis.simwrapper_export`
- **Person & household attributes**: `braunschweig.data.census.employment`, `braunschweig.data.census.household_income`, `braunschweig.data.census.licenses`, `braunschweig.data.inkar.household_income`
- **Travel / activity behavior**: `braunschweig.data.mid.data`, `braunschweig.data.mid.zones`
- **Cordon / external demand**: `braunschweig.data.census.pendler`, `braunschweig.data.cordon_gemeinden`, `braunschweig.data.cordon_network`, `braunschweig.data.cordon_pt_gates`, `braunschweig.synthesis.cordon_gates`, `braunschweig.synthesis.incommuters`, `braunschweig.synthesis.student_incommuters`
- **Education**: `braunschweig.data.education.student_share`, `braunschweig.data.schools.facilities`, `braunschweig.data.schools.kita_facilities`, `braunschweig.data.schools.university_facilities`, `braunschweig.synthesis.locations.education_gravity`
- **Vehicle fleet**: `braunschweig.synthesis.vehicles.cars.household`
- **Freight**: `braunschweig.data.freight.german_wide`, `braunschweig.freight.extraction`, `braunschweig.freight.trips`
- **Home locations**: `braunschweig.data.buildings`, `braunschweig.data.zensus_grid.population`
- **MATSim**: `braunschweig.data.vrb.zones`
- **Population synthesis**: `braunschweig.data.census.household_size`, `braunschweig.data.census.households_size_age`, `braunschweig.data.census.households_type`, `braunschweig.data.census.population`, `braunschweig.data.hts.mid_donor`, `braunschweig.ipf.model`, `braunschweig.ipf.prepare`, `braunschweig.popsim.completed_donor`
- **Secondary locations**: `braunschweig.data.bosserhof_location_category`, `braunschweig.data.bosserhof_purpose`, `braunschweig.data.external_secondary_points`, `braunschweig.data.locations`, `braunschweig.synthesis.locations.secondary_candidates`
- **Spatial base data**: `braunschweig.data.alkis`, `braunschweig.data.bbsr.regiostar`, `braunschweig.data.landuse`, `braunschweig.data.osm`, `braunschweig.data.spatial.taz`, `braunschweig.data.verbindungen.zones`
- **Validation**: `braunschweig.analysis.cordon_validation`, `braunschweig.analysis.reference.srv.commute_distance`, `braunschweig.analysis.synthesis.commute_distance_by_kreis`, `braunschweig.analysis.verbindungen_validation`, `braunschweig.data.mid.references`, `braunschweig.data.verbindungen.margins`
- **Work locations**: `braunschweig.data.building_potentials`, `braunschweig.data.census.employees`, `braunschweig.data.external_workplaces`, `braunschweig.data.verbindungen.work_od`, `braunschweig.gravity.distance_matrix_taz`, `braunschweig.locations.work`
