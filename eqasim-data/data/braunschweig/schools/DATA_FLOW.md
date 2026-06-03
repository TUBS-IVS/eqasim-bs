# Education gravity — end-to-end data flow

```
LSN xlsx (ABS 2025 + BBS 2024)            [not committed]
   |
   |  scripts/extract_nds_schools.py
   |    - read_abs_raw / read_bbs_raw      (header rows; column renames)
   |    - braunschweig.data.schools.readers.build_schools_long
   |        * braunschweig.data.schools.typing.sgl_to_level / capacity_by_level
   |        * _clean_plz (float "38122.0" -> "38122")
   |        * nds_ags8 / nds_kreis5  ("03" + LSN code), filter to ZGB-8
   |    - geocode addresses (OSM Nominatim, 1 req/s, cached; PLZ-centroid fallback)
   |    - validate offline vs osm_pois.parquet education (dist_to_osm_edu_m)
   v
nds_schools_zgb.csv                        [committed, this directory]
   |
   |  braunschweig.data.schools.facilities  (synpp loader, build_facilities_frame)
   v
GeoDataFrame[school_id, level, capacity, commune_id, geometry]  (EPSG:25832)
   |
   |  braunschweig.synthesis.locations.education_gravity  (synpp stage)
   |    - persons with has_education_trip + age + home geometry
   |    - age_to_level: 0-5 kindergarten | 6-9 grundschule | 10-15 sekundar_1
   |                    | 16-19 sekundar_2 | 20+ university
   |    - school-age levels -> assign_by_capacity_gravity
   |        (rectangular doubly-constrained Furness on pupils x schools;
   |         attraction = capacity scaled to pupil count; friction = exp(slope*d);
   |         per-pupil draw from the balanced flow; max-radius + nearest fallback)
   |    - kindergarten/university -> assign_by_radius on OSM education POIs
   |        (eqasim_common.locations.education)
   v
DataFrame[person_id, commune_id, location_id, geometry]
   |
   |  braunschweig.locations.synthesis.replacement_education_gravity  (flag-gated)
   |    education_gravity_enabled = false -> legacy OSM sampler (byte-identical)
   |    education_gravity_enabled = true  -> the stage above
   v
synthesis.population.spatial.primary.locations  (work + education)
   -> downstream synthesis.output / matsim.scenario.population (unchanged)

Reporting (offline, post-run):
   braunschweig.analysis.run_education_validation
     - enrollment_vs_capacity  -> school_enrollment_vs_capacity.csv
     - level_summary           -> level_summary.csv
     - figures (enrollment-vs-capacity scatter, commute histogram)
```

## Config keys (defaults in the stages' `configure`)

| key | default |
|---|---|
| `education_gravity_enabled` | `false` |
| `education_gravity_slope_by_level` | `{grundschule: -0.3, sekundar_1: -0.15, sekundar_2: -0.08}` |
| `education_gravity_max_radius_km_by_level` | `{grundschule: 15, sekundar_1: 30, sekundar_2: 60}` |
| `education_gravity_kindergarten_radius_m` | `2000` |
| `education_gravity_university_radius_m` | `10000` |
| `education_gravity_max_iterations` | `50` |
| `education_gravity_tolerance` | `1e-3` |
| `nds_schools_path` | `braunschweig/schools/nds_schools_zgb.csv` |

## Tests

`tests/test_school_typing.py`, `tests/test_school_readers.py`,
`tests/test_school_facilities.py`, `tests/test_education_gravity_model.py`,
`tests/test_education_gravity_stage.py`, `tests/test_education_validation.py`.
