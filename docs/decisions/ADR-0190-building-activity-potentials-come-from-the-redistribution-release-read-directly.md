# ADR-0190 · 2026-11-07 · The building activity potentials come from the redistribution release 08, read directly; the other potential is its errand value

- **Status:** accepted (implemented on branch `feature/building-potentials-redistribution`; no model run with the new release yet, so its effect on placements and distances is not measured)
- **Numbering:** 0190 checked free across all remote branches and the open PRs on 2026-11-07 (highest record anywhere: ADR-0189).
- **Issue:** none (owner request 2026-11-07; the owner preferred no new issue).
- **Supersedes:** the data source of ADR-0025 (building-level activity potentials) and the derived other potential of ADR-0048 (function-aware cap/floor on `potential_generic`). ADR-0025's REPLACE strategy itself is unchanged.
- **Relates to:** ADR-0075 (SrV location categories from the Bosserhof class), ADR-0096 (carla_sample with the linear combined scorer), the open scorer-scale question of issue #575.
- **Owner decisions:** 2026-11-07 replace the old potentials with `08_redistribution.gpkg`; the `value_*` columns replace the old other potential; read the file directly or convert it, whichever is more efficient.

## Context

- The owner received a new release of the TUBS-IVS building potentials (data record `building_potentials_redistribution`). It carries the same zonal totals as the old parquet (data record `building_activity_potentials`), but on the activity buildings only, and adds per-building values for other-purpose activities (`value_errands`, `value_business`, `value_sports`, `value_meetup`, `value_lessons`). The record lists its layers, columns, counts, what the `value_*` columns measure according to the producer, and what is unknown (the producer commit that wrote the file).
- The old release had no other-purpose potential. ADR-0048 therefore derived one from `potential_generic` (volume x class weight) with a whitelist share, a 99th-percentile cap and a 50 m³ volume floor, and ADR-0075's errand categories used the same cap/floor per category. The new release has neither `potential_generic` nor `volume_m3`.

## Decision

1. **Data source.** `braunschweig.data.building_potentials` reads the layer `buildings` of `<data_path>/braunschweig/buildings/08_redistribution.gpkg` (config `building_potentials_path`) directly. The delivered file is stored unchanged; its SHA-256 is in the data record and `scripts/verify_braunschweig_inputs.py` (entry F4) checks its presence. The stage fails fast on a missing file, layer or column, a CRS other than EPSG:25832, NaN or negative potentials, empty geometry or duplicate source ids.
2. **Internal schema.** The stage maps the source columns to the names the consumers already use (`potential_work` ← `assigned_Workers`, the education, retail and leisure columns likewise, `potential_errands` ← `value_errands`), keeps the source id as `source_building_id` and gives each building a deterministic integer `building_id` (sorted by source id), so candidate ids stay free of `/`. `bosserhof_class_clean` is the source class normalised to the keys of the committed class tables; buildings whose class has no table row are counted and logged.
3. **Other potential and candidates.** `pot_other` and the potentials of the errand categories `errand_authority_medical` and `errand_service` come from `potential_errands` (the errand categories keep their Bosserhof-class assignment). With `secondary_building_potentials` on, the `other` destinations are the buildings with `potential_errands > 0`, one `sec_b_<building_id>` row per building carrying all its offers, the same REPLACE rule ADR-0025 applies to shop and leisure; the legacy OSM/ALKIS catalog points are no longer `other` candidates (owner decision 2026-11-07, after the review found that most of them would lie in no activity building and fall to a zero potential). An empty `other` supply raises. Run manifest `building-potentials-release-comparison-2026-11-07` checks that `value_errands` sits exactly on the buildings labelled `errands` and records their classes and zone coverage. The cap, floor and share derivation of ADR-0048 and its five config keys (`secondary_other_smart_potential`, `secondary_other_broad_share`, `secondary_other_errand_share`, `secondary_other_min_volume_m3`, `secondary_other_cap_percentile`) are removed, not kept as an alternative.
4. **Carried, not consumed.** `value_business`, `value_sports`, `value_meetup` and `value_lessons` are loaded and kept on the frame, but no placement reads them: business trips are work activities placed at the work location, visits use the residential stock (`pot_visit`, unaffected), and sports and lessons have no own placement activity yet.
5. **Removed.** `scripts/import_building_activity_potentials.py` (the parquet conversion) and its tests. The old data record stays, marked superseded.

### Rejected alternatives

- **Convert the GeoPackage to parquet as before.** Reading the layer directly took 0.64 s against 0.17 s for a parquet copy (one measurement on the owner's laptop, 2026-11-07), and the stage result is cached; the conversion would add a script, a derived file with its own hash, and a stale-copy risk.
- **Keep the ADR-0048 derivation on the new data.** Its inputs (`potential_generic`, `volume_m3`) do not exist in the new release, and the owner chose the delivered errand value as the other potential.
- **Keep the catalog points as `other` candidates with the footprint's errand value.** Points outside the activity buildings would carry a zero potential (formerly a median prior), so the potential would no longer describe the supply; the owner chose the buildings.
- **Feed `value_sports` / `value_meetup` into the leisure categories now.** That changes the leisure placement beyond the requested replacement; left for a later decision.

## Consequences

- **Outputs change.** Every stage downstream of `braunschweig.data.building_potentials` is recomputed. Work, education, shop and leisure placements keep their zonal totals but draw from far fewer buildings with larger values; the other potential changes source and scale.
- **Scorer scale.** The secondary chain solver scores `potential - deviation in metres` (linear transform, ADR-0096); a change of potential scale shifts the balance between attraction and distance. This ADR does not change the scorer; the scale comparison of the old and new potentials is recorded under Evidence, and the scorer decision stays with issue #575.
- **Not validated.** No run with the new release exists yet; its effect on realised distances and concentration must be measured before any claim.

## Evidence

- Data facts: data record `building_potentials_redistribution`.
- Release comparison and errand plausibility: run manifest `building-potentials-release-comparison-2026-11-07` (offline measurement, no model run).
- Tests: `tests/test_building_activity_potentials_stage.py` (loader primary path and fail-fast cases) and the secondary-candidate tests named in the feature record `building_potentials_secondary`.
