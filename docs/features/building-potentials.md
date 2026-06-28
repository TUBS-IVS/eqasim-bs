# Building-level activity potentials


Building-level activity potentials redistribute synthetic activity locations
(work, secondary, education) from the commune/zone level down to individual
OSM/ALKIS buildings. Without this feature every person's activity is placed at
a zone centroid or a uniform random building; with it, buildings are weighted
by their floor-area-based activity potential so that large offices, shopping
centres, and schools attract proportionally more trips.

**Data source.** The potentials are derived from OSM footprints and ALKIS
building attributes by the **TUBS-IVS
Activities-and-Potentials-Calculation-Pipeline** (separate repository). The
output is a local-only parquet file:

```
eqasim-data/data/braunschweig/buildings/building_activity_potentials.parquet
```

This file is **not committed** (large, derived, local-only). The pipeline that
generates it is the canonical source of truth; hard-coding building coordinates
or capacity values in Python is prohibited. Regenerate with the Activities-and-
Potentials-Calculation-Pipeline and copy the output to the path above.

**Stage.** `braunschweig.data.building_potentials` validates the parquet on load
by calling `validate()`, which **raises** if the file is absent or malformed
(fail-fast, no silent fallback). The stage is consumed by the three downstream
feature stages that redistribute locations.

**Feature flags (all default true in code; OFF in non-real-data configs).**

| Config key | Stage / effect |
|---|---|
| `work_building_potentials` | `braunschweig.synthesis.locations.work` — weighted building draw for work locations |
| `secondary_building_potentials` | `braunschweig.synthesis.locations.secondary` — weighted building draw for secondary locations |
| `secondary_scorer_mode` | `"combined"` uses both potential and distance deviation; `"distance"` reverts to distance-only |
| `secondary_scorer_pot_weight` | Weight on the potential term in the combined scorer (default `1.0`) |
| `secondary_scorer_dist_dev_weight` | Weight on the distance-deviation term in the combined scorer (default `1.0`) |
| `education_building_distribution` | `braunschweig.synthesis.locations.education_gravity` — weighted building draw within the assigned school/facility |

**Run config split.** The five committed real-data run configs
(`config_local_braunschweig.yml`, `config_server_braunschweig_100pct.yml`,
`config_server_braunschweig_1pct_allfeat_popsim.yml`,
`config_server_braunschweig_25pct_allfeat_popsim.yml`,
`config_freight_validate.yml`) set all four flags to `true` and include
`building_potentials_path`. The local-only (gitignored)
`config_local_braunschweig_1pct_allfeat_full.yml` also enables the feature
but is not committed. All other configs set the three boolean flags to `false`
and omit the path, so the feature is off and no local-only parquet is required.

**Aggregate controls are unaffected.** Work-zone totals (GENESIS SvB), OD
gravity flows, and NDS school enrollment totals remain the authoritative
controls. The building potentials only govern the *within-zone / within-school*
spatial distribution of already-placed activities.

**OFF path** (`work_building_potentials: false`, `secondary_building_potentials:
false`, `education_building_distribution: false`) is byte-identical to the
pre-feature pipeline: activity locations are placed by the existing zone-level
or uniform-random-building logic, no building parquet is loaded, and the
`braunschweig.data.building_potentials` stage is never requested.

Tests: `tests/test_building_activity_potentials_import.py`,
`tests/test_building_activity_potentials_stage.py`,
`tests/test_building_potential_attach.py`,
`tests/test_work_building_potentials.py`,
`tests/test_secondary_building_potentials.py`,
`tests/test_education_building_distribution.py`.
