# Building-level activity potentials


Building-level activity potentials redistribute synthetic activity locations
(work, secondary, education) from the commune/zone (TAZ) level down to individual
buildings. Without this feature every person's activity is placed at a zone
centroid or a uniform random building; with it, buildings are weighted by a
per-activity-type potential (built from 3D building **volume** x an LLM-assigned
building-use weight, see below) so that major employment hubs, shopping centres,
and schools attract proportionally more trips.

**Data source & methodology.** The potentials are produced by the **TUBS-IVS
[Activities-and-Potentials-Calculation-Pipeline](https://github.com/TUBS-IVS/Activities-and-Potentials-Calculation-Pipeline)**
(separate, public repository), documented in Patel, Bienzeisler & Friedrich,
*"Deriving Activities and Their Potentials at Building-Level Using Large Language
Models"* (preprint, submitted to Transportation Research Procedia, EWGT2026). The
method is **not** a simple floor-area heuristic; it is a four-stage pipeline:

1. **Geometric preprocessing** — clean ALKIS authoritative 3D cadastral buildings,
   compute footprint area and **volume (`volume_m3`)**, merge duplicate/overlapping
   polygons (POI-aware), supplement missing buildings with OSM footprints.
2. **Semantic enrichment** — extract/clean OSM POIs, spatial-join ALKIS function
   labels + OSM land-use + OSM building tags, attach POIs to buildings (intersection
   + 100 m nearest-neighbour), aggregate to one record per building.
3. **LLM classification** — each building record is turned into a natural-language
   sentence; a Large Language Model assigns a **MiD activity label** plus a
   **Bosserhof building-use / worker-density class** (`bosserhof_class_clean`).
   Reported quality on 123 manually validated buildings: 82.4% F1 on activity
   labels, 81.3% accuracy on the dominant building-use class.
4. **Activity-informed disaggregation** — zone-level totals (workers, pupils,
   shoppers, ...) are redistributed to buildings **proportional to building volume x
   Bosserhof weight**, with a hierarchical spatial fallback (TAZ -> neighbours ->
   study area) and **percentile-based volume caps that prevent unrealistic
   concentrations**. This already concentrates worker potential around the real
   employment hubs (Volkswagen, Siemens, TU Braunschweig, municipal hospital).

The output is a local-only parquet file:

```
eqasim-data/data/braunschweig/buildings/building_activity_potentials.parquet
```

Its columns (verified 2026-07-18, 263,512 buildings): `building_id`,
`potential_work`, `potential_school`, `potential_university`,
`potential_kindergarten`, `potential_leisure`, `potential_retail_daily`,
`potential_retail_non_daily`, `potential_generic`, `gml_id`,
`bosserhof_class_clean`, `volume_m3`, `target_taz`, `geometry`. Each
`potential_*` is the disaggregated share of the zonal total for that activity
type; it is a modelled, geometry-and-use-informed weight (NOT an observed
per-building headcount).

This file is **not committed** (large, derived, local-only). The pipeline that
generates it is the canonical source of truth; hard-coding building coordinates
or capacity values in Python is prohibited. Regenerate with the
Activities-and-Potentials-Calculation-Pipeline and copy the output to the path
above.

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

**Run config split.** The composed all-features base (`configs/base_bs.yml`,
combined with any `configs/overlays/*.yml` scale) and the standalone fixture
`configs/fixtures/config_local_braunschweig.yml` set all four flags to `true`
and include `building_potentials_path`. (Prior to the config-composition cleanup, #230, this was set individually per
committed real-data run config -- `config_server_braunschweig_100pct.yml`,
`config_server_braunschweig_1pct_allfeat_popsim.yml`,
`config_server_braunschweig_25pct_allfeat_popsim.yml`,
`config_freight_validate.yml` -- all now superseded/removed in favour of the
single composed base.) The local-only
(gitignored) `config_local_braunschweig_1pct_allfeat_full.yml` also enables
the feature but is not committed. All other configs set the three boolean
flags to `false` and omit the path, so the feature is off and no local-only
parquet is required.

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
