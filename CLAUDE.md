# CLAUDE.md

## Project context

This project uses MATSim and eqasim for scientific transport simulation research.

The codebase must be treated as research software. Correctness, reproducibility, traceability, clarity, and maintainability are more important than quick but fragile solutions.

The project should follow the style and structure of MATSim and eqasim as closely as possible. The implementation should be professional, modular, documented, and suitable for scientific use.

## Reference data: MiD 2023 constraint tables (read this!)

Numerical reference values from the MiD 2023 *Großraum Braunschweig* report
are **not** stored as Python literals. They live as CSV files under
`eqasim-data/data/braunschweig/mid/` and are loaded by
`braunschweig.data.mid.reference_tables`:

| File | Source table | Consumed by |
|---|---|---|
| `mid2023_P19_car_constraints.csv` | A P19 'jederzeit' | `braunschweig.data.mid.data` |
| `mid2023_P22_bicycle_constraints.csv` | A P22 'ja' | `braunschweig.data.mid.data` |
| `mid2023_P24_1_pt_subscription_constraints.csv` | A P24.1 (sum of subscription columns) | `braunschweig.data.mid.data` |
| `mid2023_P24_1.csv` | A P24.1 raw 9-column ticket-type breakdown per Kreis | `braunschweig.synthesis.population.enriched` (categorical IPF) |
| `mid2023_P24_1_by_sex.csv` | A P24.1 sex margin (male/female × 9 ticket types) | same — IPF sex margin |
| `mid2023_P24_1_by_age.csv` | A P24.1 age margin (9 bands × 9 ticket types) | same — IPF age margin |
| `mid2023_H7_cars_by_kreis.csv` | H7 (Anzahl Autos im HH) | `braunschweig.synthesis.population.enriched` |
| `mid2023_H12_3_bikes_by_kreis.csv` | H12.3 (Fahrräder/Pedelecs im HH) | `braunschweig.synthesis.population.enriched` |
| `mid2023_H4_income_by_size.csv` | H4 (Ökonomischer Status × HH-Größe) | `braunschweig.data.census.household_income` |
| `mid2023_class_midpoint_eur.csv` | derived class-midpoint € lookup | `braunschweig.synthesis.population.enriched` |
| `mid2023_P17_1.csv` | A P17.1 raw 3-column licence breakdown per Kreis | `braunschweig.synthesis.population.enriched` (categorical IPF) |
| `mid2023_P17_1_by_sex.csv` | A P17.1 sex margin (male/female × {ja,nein,k.A.}) | same — IPF sex margin |
| `mid2023_P17_1_by_age.csv` | A P17.1 age margin (9 bands × {ja,nein,k.A.}) | same — IPF age margin |

The *additional* tables `mid2023_P9.csv`, `mid2023_P12_1.csv`, `mid2023_P13.csv`,
`mid2023_P17_1.csv`, `mid2023_P24_1.csv` are produced by
`scripts/extract_mid_tables.py` (PDF parser).

### PT ticket type (P24.1) — categorical & flatrate-derived `has_pt_subscription`

Each synthetic person receives a categorical attribute
`pt_subscription_type` ∈ `PT_TICKET_CATEGORIES` sampled from the per-Kreis
probability vector parsed from MiD 2023 P24.1 (Tabelle A, page 105).
The boolean `has_pt_subscription` is then derived as

```
has_pt_subscription = pt_subscription_type ∈ PT_TICKET_FLATRATE
```

with `PT_TICKET_FLATRATE = {deutschlandticket, monat_abo_jahreskarte,
jobticket_semesterticket, wochen_monat_ohne_abo}` — i.e. all ticket types
that grant unlimited rides on local PT during their validity. The set is
defined in `braunschweig.data.mid.reference_tables` and re-used by the
MATSim person-attribute writer (`ptSubscriptionType` is written alongside
`hasPtSubscription`). The flatrate sum per Kreis matches the legacy
single-target seeding in `mid2023_P24_1_pt_subscription_constraints.csv`
within ±1 percentage point (covered by
`test_pt_flatrate_set_matches_legacy_kreis_share`).

The probability vector for each person is determined by a **three-margin
IPF (raking)** on the 4-way contingency table
`X[kreis, sex, age_bin, ticket_type]` with marginal targets from MiD P24.1:

- `mid2023_P24_1.csv`        — Kreis × ticket type
- `mid2023_P24_1_by_sex.csv` — Sex × ticket type (male/female)
- `mid2023_P24_1_by_age.csv` — Age × ticket type (9 bands: 14–17, 18–29,
  30–39, 40–49, 50–59, 60–64, 65–74, 75–79, 80+)

After convergence (200 iterations) every person in cell `(k, s, a)` is
assigned `P[k,s,a,:] = X[k,s,a,:] / Σ_c X[k,s,a,:]` and sampled
categorically.  Persons below `braunschweig.minimum_age.pt_subscription`
(default 0; effective floor is the MiD basis age 14) are deterministically
assigned `fahre_nie`.  Convergence diagnostics (max |Δ| per margin) are
printed by the `braunschweig.synthesis.population.enriched` stage — note
that MiD's three margins are independently rounded to integer percent and
therefore not internally consistent, so raking finds a least-squares
compromise within ~5 pp on the worst-case Kreis × ticket cell.

To **regenerate** the constraint CSVs from their pinned values run:

```powershell
python scripts/seed_mid_constraint_tables.py
```

This is the only supported way to update the values. Hard-coding new
percentages in Python modules is prohibited — add them to the seed
script (with a provenance comment) and re-run it instead.

Tests: `tests/test_mid_reference_tables.py` covers schema, loader
identity vs. legacy values, and seed-script idempotency.

### Driving licence (P17.1) — categorical & 3-margin IPF

`has_license` (renamed downstream to `has_driving_license`) is no longer
taken from KBA FE4.x data via the IPF model.  Instead each person above the
legal driving age (`LICENSE_MIN_AGE = 18`, regular Pkw-Führerschein Klasse
B; the BF17 / begleitetes Fahren option in Niedersachsen is intentionally
ignored) is assigned a `license_type`
∈ `LICENSE_CATEGORIES = ("ja","nein","keine_angabe")` sampled from a
**three-margin IPF (raking)** on the 4-way contingency table

```
Xl[kreis, sex, age_bin, license_category]
```

with marginal targets parsed from MiD 2023 P17.1 (Tabelle A, page 87):

- `mid2023_P17_1.csv`        — Kreis × {ja,nein,k.A.}
- `mid2023_P17_1_by_sex.csv` — Sex × {ja,nein,k.A.}
- `mid2023_P17_1_by_age.csv` — Age × {ja,nein,k.A.} (9 MiD bands)

`has_license = (license_type == "ja")` (`keine_angabe` conservatively maps
to `False`, see `LICENSE_TRUE`).  Persons below 18 are forced to `"nein"`
deterministically.  The MiD margins are independently rounded to integer
percent and span 19 % … 94 %, so raking finds a least-squares compromise
within ~10 pp on the worst-case Kreis × age cell — diagnostics are printed
by the `braunschweig.synthesis.population.enriched` stage.

The legacy KBA-FE4-based `df["license"]` from
`braunschweig.ipf.attributed` is still produced (MiD overrides it inside
the enrichment stage), but is no longer the source of truth for
`has_license`.

Tests: `test_license_csv_has_all_kreise`,
`test_license_margin_csvs_exist_and_normalised`,
`test_license_margins_match_pdf_values`,
`test_license_ipf_three_margins_converges_on_synthetic_population`.

## Gravity model: per-RegioStaR-7 distance slope

`braunschweig.gravity.model` distributes work/education trips with a
distance-decay friction `exp(slope * d_ij)`. The `slope` is differentiated by
the **RegioStaR-7** class (BMV/BBSR urban-rural typology, codes 71-77) of the
origin Gemeinde, so urban origins (flatter slope, longer commutes) and rural
origins (steeper slope, shorter commutes) decay at their own rate. The
flow-weighted mean of the per-class slopes is held equal to `gravity_slope`
(-0.065), so the regional mean commute distance is unchanged; only the
sub-Kreis distribution is differentiated (the commute-distance KPI itself is
MiD-P13-overridden, see `commute_distance.py`).

Calibration (`scripts/calibrate_gravity_per_rs7.py --anchor-scope ring`) fits a
single **identified full-panel Poisson GLM** on the BA Pendleratlas Kreis-pair
flows:

```
log E[flow_ij] = origin_FE_i + dest_FE_j + sum_c delta_c * d_ij * 1[RS7(i)=c]
```

A per-origin fit with destination fixed effects is rank deficient on this data
(one flow row per origin-destination pair makes distance collinear with the
per-destination dummies), so the full panel is used: each `delta_c` is
identified from within-origin distance variation pooled across the many origins
of class `c`. The anchor Kreise are chosen by an adaptive ring that grows around
ZGB until every RS7 code present in ZGB has at least 5 anchors (225 km / 141
Kreise at present). Pinned values live in `config_*braunschweig*.yml` under
`gravity_slope_by_regiostar7` (do not hand-edit; re-run the script and paste its
YAML). `braunschweig.data.bbsr.regiostar` assigns every in-scope Gemeinde an RS7
code, filling Gemeinden absent from the RegioStaR-2020 reference (e.g.
Langelsheim, 03153019) by geographic nearest neighbour, so all 123 gravity
origins receive a typed slope.

Tests: `tests/test_gravity_ring_calibration.py` (ring selection + panel
recovery), `tests/test_regiostar_fill.py` (nearest-neighbour fill),
`tests/test_gravity_slope_config.py` (the `None` default / flatten contract).

## Education gravity model (NDS school data)

School-age pupils (ages 6-19) are assigned to **real Niedersachsen schools** by a
capacity-constrained distance-decay gravity model, instead of the generic OSM
hard-radius sampler. Kindergarten (0-5) and university (20+) stay on the OSM
sampler. The feature is flag-gated; with `education_gravity_enabled=false`
(default) the pipeline is byte-identical to the legacy OSM education assignment.

**Data.** The committed facilities table
`eqasim-data/data/braunschweig/schools/nds_schools_zgb.csv` (force-added; the
`eqasim-data` tree is gitignored) is built by `scripts/extract_nds_schools.py`
from the LSN directories `Schulverzeichnis_ABS_2025.xlsx` (allgemeinbildende
Schulen) + `Verzeichnis_der_BBS_2024.xlsx` (berufsbildende Schulen). One row per
**(school, level)**: a school offering several levels (e.g. a KGS) appears once
per level with that level's real pupil count as `capacity`. The script geocodes
addresses via OSM Nominatim (1 req/s, cached) and validates each point offline
against the local OSM education POIs (`osm_pois.parquet`, distance to the nearest
education feature; `validated = dist < 750 m`). Full provenance + the regenerate
command live in `eqasim-data/data/braunschweig/schools/README.md` and the
end-to-end trace in `.../schools/DATA_FLOW.md`. Hard-coding coordinates or
capacities in Python is prohibited - change the xlsx source or
`braunschweig/data/schools/typing.py` and re-run the script.

**Age -> level + capacity.** `braunschweig.data.schools.typing` maps each LSN
Schulgliederung (SGL) code to a level and sums the matching pupil counts:
Primarbereich (SGL 00,01,03,04) -> `grundschule` (6-9); Haupt/Real/Gym-SekI/IGS/
KGS (11-19) plus the Oberschule/Foerderschule block (40-69) -> `sekundar_1`
(10-15); Gym/IGS/KGS Sek II (23,24,28,29) **plus all BBS pupils** -> `sekundar_2`
(16-19). Adult forms (Abendgymnasium 30, Kolleg 31) are excluded. The
Gymnasium/Realschule/Hauptschule mix emerges automatically from the real
per-level capacity shares (no school-track choice is modelled). Note that the
gravity age bands (0-5 / 6-9 / 10-15 / 16-19 / 20+) reclassify the boundary ages
relative to the legacy OSM sampler's 0-6 / 7-17 / 18+ split: with the flag ON,
age 6 moves from kindergarten to `grundschule` and ages 18-19 from university to
`sekundar_2` (BBS/Oberstufe). This only affects the ON path; the OFF path keeps
the legacy bands. LSN internal codes
drop the Land prefix: official AGS-8 = `"03" + AGS6`, Kreis-5 = `"03" + Kreis3`;
the table is filtered to the ZGB-8 Kreise.

**The model (capacity-constrained distance decay).** Per level, the assignment is
a **rectangular doubly-constrained Furness balancing**
(`braunschweig.synthesis.locations.education_gravity_model.balance_doubly_constrained`,
the rectangular generalisation of `braunschweig.gravity.model.evaluate_gravity`):
pupils are rows (production target 1 each -> everyone is placed), schools are
columns (attraction target = real `capacity` **scaled to the pupil count** ->
schools fill in proportion to real Schuelerplaetze), friction
`f = exp(slope_level * d_km)`. Each pupil then draws a school proportional to the
**balanced flow row** - so distance decay shapes the assignment while the
double-constraint prevents a tiny nearby school from swallowing pupils that belong
in a larger one ("no 2-vs-10000"). A per-level max radius bounds the candidate set
(nearest-school fallback when a pupil has none in range). All randomness uses the
single `random_seed`. Kindergarten/university use the OSM radius sampler
(`assign_by_radius`). The per-person stage
`braunschweig.synthesis.locations.education_gravity` produces the legacy output
schema `[person_id, commune_id, location_id, geometry]` and is swapped in by the
flag-gated wrapper
`braunschweig.locations.synthesis.replacement_education_gravity` (aliased to
`synthesis.population.spatial.primary.locations`).

Config keys (defaults in the stage's `configure`):
`education_gravity_enabled` (false), `education_gravity_slope_by_level`
(`{grundschule:-0.3, sekundar_1:-0.15, sekundar_2:-0.08}`),
`education_gravity_max_radius_km_by_level` (`{grundschule:15, sekundar_1:30,
sekundar_2:60}`), `education_gravity_kindergarten_radius_m` (2000),
`education_gravity_university_radius_m` (10000),
`education_gravity_max_iterations` (50), `education_gravity_tolerance` (1e-3),
`nds_schools_path`.

**Enrollment report (debug / calibrate).**
`python -m braunschweig.analysis.run_education_validation --working-directory
<cache> --sampling-rate <r> --output-dir <out>` writes
`school_enrollment_vs_capacity.csv` (per school: capacity vs assigned pupils
scaled to 100 %, fill_ratio) and `level_summary.csv` (per level: pupil count,
mean/median straight-line school-commute km), so over-/under-filled schools and
the slope calibration are immediately visible.

**Per-(RegioStaR-7, level) slope calibration (MiD Tabelle 43).** The decay slope
is differentiated by the pupil's **home RegioStaR-7** class so urban pupils (short
trips) and rural pupils (long trips) decay at their own rate. Each pupil's home
RS7 comes from a spatial join of the home point to `data.spatial.municipalities`
(the 12-digit ARS is converted to the 8-digit AGS via
`braunschweig.data.bbsr.regiostar.ars_to_ags8` before the RS7 merge -- without
this every pupil silently falls back to the scalar slope). The per-RS7 slopes
live in `education_gravity_slope_by_level_rs7` (nested `{level: {rs7: slope}}`;
default `None` -> scalar `education_gravity_slope_by_level`, like
`gravity_slope_by_regiostar7`). They are calibrated against **MiD 2023 Tabelle 43**
("Kita- und Schulweglaengen nach Raumtyp und Altersgruppe", reference CSV
`eqasim-data/data/braunschweig/mid/mid2023_T43_school_distance_by_rs7.csv` seeded
by `scripts/seed_mid_t43_school_distance.py`, loaded by
`braunschweig.data.mid.school_distance`). The MiD age groups map 7-10 ->
grundschule, 11-13 -> sekundar_1, 14-17 -> sekundar_2; MiD routed lengths are
divided by a detour factor (1.3) to a straight-line target.

`scripts/calibrate_education_slopes.py` runs the calibration on the 25 % synthesis
(`cache_bs_25pct`): the WHOLE level is assigned each round (per-pupil slope vector
by home RS7) and each RS7's mean trip distance is secant-updated toward its target
(`calibrate_level_per_rs7`). Calibrating cells in isolation is wrong -- the
capacity constraint, scaled to a pupil subset, forces filling out-of-catchment
schools. The committed evaluation (`--output-dir
eqasim-data/data/braunschweig/mid/education_calibration/`:
`calibration_results.csv`, two figures, `calibration_summary.md`) shows grundschule
and sekundar_1 hit the targets; sekundar_2 rural cells (RS7 74/77) sit at the -3.0
floor -- sparse rural Oberstufe/BBS make the nearest school already ~10 km, and the
MiD 14-17 band mixes in nearer Sek-I pupils that our 16-19 band excludes, biasing
the target short. Re-run the script and paste its YAML to update the slopes; do not
hand-tune.

Tests: `tests/test_school_typing.py`, `tests/test_school_readers.py`,
`tests/test_school_facilities.py`, `tests/test_education_gravity_model.py`,
`tests/test_education_gravity_stage.py`, `tests/test_education_validation.py`,
`tests/test_mid_school_distance.py`, `tests/test_calibrate_education_slopes.py`,
`tests/test_regiostar_fill.py` (the `ars_to_ags8` helper).

## Run analysis (post-simulation)

The validation notebook `braunschweig/analysis/validation_mid2023.ipynb`
has a runnable counterpart that produces every table, figure and
`report.json` for one eqasim run output directory:

```powershell
python -m braunschweig.analysis.run_mid_validation `
    --output-dir eqasim-data/output_bs_25pct_parking `
    --label "25pct_parking"
```

Outputs land in `<output-dir>/analysis/mid_validation/`:

- `report.json` — headline KPIs (persons, trips, license/employment by
  Kreis, mean commute km vs MiD P13).
- `summary.md` — Markdown digest with three reference-comparison tables.
- `commute_bands_vs_p13.csv`, `commute_mean_vs_p13.csv`,
  `license_vs_p17_1.csv`, `employment_vs_p9.csv`,
  `secondary_success.csv`, `persons_with_kreis.csv` — intermediate
  long-form tables for downstream comparison scripts (e.g. parking-on
  vs. no-parking).
- `01_demographics.png` … `07_employment_rate.png` — figures.

Combined dashboard + MiD validation in one call:

```powershell
python -m braunschweig.analysis.run_full_analysis `
    --output-dir eqasim-data/output_bs_25pct_parking `
    --sim-cache  eqasim-data/cache_bs_25pct_parking `
    --label      "25pct_parking"
```

Tests: `tests/test_run_mid_validation.py` covers the helpers
(`band_share`, `_bool_share`, markdown rendering, CLI parser).

## Language policy

All code must be written in English.

This includes:

1. Class names
2. Method names
3. Variable names
4. Package names
5. File names
6. Comments
7. JavaDoc
8. Log messages
9. Commit messages
10. Configuration descriptions
11. Test names
12. Documentation inside the repository

German may only be used in external text outputs when explicitly requested. Code, comments, and technical documentation must remain English. Chat responses to the user are in German.

## General coding principles

Write code that is:

1. Correct
2. Reproducible
3. Efficient
4. Traceable
5. Easy to review
6. Easy to maintain
7. Scientifically defensible
8. Consistent with the existing project structure

Do not write clever code when clear code is possible.

Do not introduce unnecessary abstraction.

Do not duplicate logic.

Do not silently change behavior.

Do not remove existing functionality unless explicitly requested.

Do not invent data assumptions. If an assumption is required, document it clearly.

## MATSim and eqasim style

Follow MATSim and eqasim conventions where applicable.

Use Java naming conventions:

1. Classes and interfaces use `UpperCamelCase`
2. Methods use `lowerCamelCase`
3. Variables use `lowerCamelCase`
4. Constants use `UPPER_CASE_WITH_UNDERSCORES`
5. Package names use lowercase
6. Abbreviations should be avoided unless they are established domain terms

Use braces consistently, also for single line `if`, `else`, `for`, and `while` blocks.

Prefer readable lines. Lines up to 132 characters are acceptable when this improves readability.

Keep code ASCII only where possible. Avoid non ASCII characters in source code, especially in identifiers and string constants.

## Java version and dependencies

Use the Java version required by the active MATSim version.

Do not add new dependencies unless they are clearly justified.

Before adding a dependency, check whether the same task can be solved with:

1. Standard Java
2. MATSim utilities
3. Existing project utilities
4. Existing eqasim components

Document every new dependency and why it is needed.

## Architecture

Prefer small, focused classes.

Each class should have one clear responsibility.

Separate the following concerns:

1. Input parsing
2. Scenario preparation
3. Configuration
4. Simulation execution
5. Analysis
6. Output writing
7. Validation
8. Visualization preparation

Avoid mixing analysis logic with simulation setup.

Avoid mixing file system logic with domain logic.

Avoid global mutable state.

Use dependency injection where it is already used by MATSim or eqasim.

Keep MATSim modules, config groups, bindings, and analysis components cleanly separated.

## Configuration

All relevant parameters must be configurable.

Do not hard code paths, thresholds, random seeds, scenario names, modes, CRS definitions, or calibration parameters unless there is a strong reason.

Prefer explicit configuration objects over scattered constants.

Every configuration option should have:

1. A clear name
2. A documented meaning
3. A default value where reasonable
4. A unit if applicable
5. A valid range if applicable

Use descriptive names such as:

```java
maximumTransferDistanceMeters
sampleSize
randomSeed
inputPopulationPath
outputDirectory
```

Avoid unclear names such as:

```
x
tmp
value1
param
data
```

## Paths and file handling

Use explicit and reproducible paths.

Do not rely on hidden working directory assumptions.

Validate that required input files exist before processing.

Fail early with clear error messages when inputs are missing or invalid.

Create output directories explicitly.

Never overwrite important outputs silently.

If overwriting is allowed, make it explicit in the configuration or log output.

## Scientific reproducibility

Every simulation or analysis run should be reproducible.

Whenever possible, log:

- Scenario name
- Run identifier
- Git commit hash if available
- MATSim version
- eqasim version if applicable
- Java version
- Random seed
- Config file path
- Input file paths
- Output directory
- Main parameter values
- Start time and end time
- Runtime
- Number of agents, links, facilities, vehicles, carriers, or shipments where relevant

Do not use random processes without an explicit random seed.

If deterministic behavior cannot be guaranteed, document why.

## Data provenance

All generated data must be traceable.

When producing derived files, document:

- Which input files were used
- Which filters were applied
- Which assumptions were made
- Which coordinate reference system was used
- Which aggregation level was used
- Which time period was represented
- Which software step generated the file

Do not create output files with ambiguous names.

Prefer names such as:

```
population_hanover_2025_sample_0.10.xml.gz
carrier_tours_baseline_2025_weekday.csv
network_cleaned_epsg25832.xml.gz
validation_summary_b2b_share_by_zone.csv
```

Avoid names such as:

```
output.csv
final.csv
new_result.csv
test.xml
```

## Documentation

Write concise but useful documentation.

Document public classes and public methods with JavaDoc when their purpose is not obvious.

JavaDoc should explain:

- What the class or method does
- Which assumptions it makes
- Which input data it expects
- Which output it produces
- Which units are used
- Whether the method has side effects

Do not write comments that only repeat the code.

Bad example:

```java
// Set count to zero.
int count = 0;
```

Good example:

```java
// Trips without a valid destination are excluded because they cannot be assigned to a network route.
```

## Logging

Use structured and meaningful logging.

Prefer log messages that help with debugging and scientific traceability.

Log important processing steps, not every minor operation.

Use appropriate log levels:

- `info` for major processing steps
- `warn` for recoverable problems or assumptions
- `error` for failures
- `debug` for detailed diagnostics

Do not use `System.out.println` in production code.

## Error handling

Fail early when input data is invalid.

Use clear exception messages.

Exception messages should explain:

- What failed
- Which input caused the problem
- Why the value is invalid
- How the issue can be fixed if this is obvious

Do not swallow exceptions silently.

Do not catch broad exceptions unless there is a clear reason.

Do not continue after invalid input if this can compromise scientific results.

## Validation and quality control

Every relevant processing step should include plausibility checks.

Add validation for:

- Missing files
- Empty datasets
- Invalid coordinates
- Invalid CRS definitions
- Negative travel times
- Negative distances
- Negative demand values
- Invalid mode names
- Invalid activity types
- Invalid vehicle capacities
- Invalid carrier or shipment identifiers
- Inconsistent totals after aggregation
- Unexpected changes in population size
- Unexpected changes in demand totals

For scientific workflows, report validation results in a dedicated summary file where appropriate.

## Tests

Add tests for non trivial logic.

Prefer small unit tests for:

- Data transformations
- Filtering rules
- Assignment logic
- Cost calculations
- Aggregation logic
- Routing helper logic
- Validation checks

Use integration tests when testing MATSim scenario setup or full pipeline behavior.

Tests must be deterministic.

Use small synthetic test data where possible.

Do not rely on large external datasets in unit tests.

## Performance

Efficiency matters, but correctness comes first.

Avoid unnecessary nested loops over large MATSim populations, links, events, carriers, or shipments.

Use maps, sets, indexes, and spatial indexes where appropriate.

Avoid repeated file reads.

Avoid repeated route calculations when caching is safe.

Document caching behavior clearly.

Be careful with memory usage when processing large event files, populations, networks, or freight scenarios.

Prefer streaming approaches for large files when possible.

## MATSim specific rules

Use MATSim APIs instead of manually parsing MATSim XML files unless there is a clear reason.

Keep MATSim config handling explicit and reproducible.

Do not silently modify MATSim config values.

When modifying a MATSim scenario, clearly separate:

1. Config creation
2. Scenario loading
3. Scenario modification
4. Controller setup
5. Module installation
6. Simulation execution
7. Post processing

Use established MATSim concepts correctly:

- Scenario
- Config
- Controler
- Population
- Network
- ActivityFacility
- Vehicle
- Carrier
- Plan
- Leg
- Activity
- Events

Do not create custom replacements for standard MATSim functionality unless required.

## eqasim specific rules

Follow the pipeline logic and structure used by eqasim where applicable.

Keep scenario generation, population synthesis, simulation setup, and analysis steps modular.

Prefer reproducible pipeline stages over one off scripts.

If adapting logic from eqasim examples, keep the structure understandable and document the adaptation.

Do not copy code blindly. Adapt it to the project context and explain relevant assumptions.

## Geospatial processing

Always document the coordinate reference system.

Use metric projected coordinate systems for distance based calculations.

Do not calculate metric distances in WGS84 longitude and latitude coordinates.

Validate geometry validity before spatial operations where relevant.

Document buffer distances and spatial thresholds in meters.

Use explicit names for spatial thresholds, for example:

```
maximumStopAccessDistanceMeters
transferSearchRadiusMeters
zoneAssignmentBufferMeters
```

## Units

Always make units explicit in variable names, method names, documentation, and output column names.

Examples:

```
travelTimeSeconds
distanceMeters
speedMetersPerSecond
emissionsGrams
costEuro
durationHours
```

Avoid ambiguous names such as:

```
time
distance
speed
cost
```

## Output tables

Output tables must be readable, documented, and stable.

Column names should be explicit and consistent.

Use snake case for CSV column names.

Examples:

```
person_id
tour_id
carrier_id
vehicle_id
departure_time_seconds
travel_time_seconds
distance_meters
co2_grams
cost_euro
```

Do not mix naming styles in one file.

Do not rename columns without updating downstream code and documentation.

## Research reporting

When generating analysis outputs, include enough information to reproduce the result.

Every figure or table should be traceable to:

- Input data
- Scenario
- Processing script
- Parameters
- Date or run identifier

Do not overstate results.

Use cautious scientific language.

Report limitations where relevant.

Distinguish clearly between observed data, modeled data, assumptions, and derived indicators.

## Code changes

Before changing code, understand the existing structure.

Make minimal necessary changes.

Preserve existing behavior unless a behavior change is explicitly requested.

When making a change, consider:

- Does this break reproducibility?
- Does this change scientific results?
- Does this affect previous outputs?
- Does this require a test?
- Does this require documentation?
- Does this require a configuration option?

## Refactoring

Refactoring is allowed when it improves clarity, maintainability, or performance.

Do not refactor large unrelated parts of the codebase while solving a specific issue.

Keep refactoring behavior preserving unless explicitly requested.

If behavior changes, document the change clearly.

## Comments and JavaDoc style

Comments must be written in English.

Use comments to explain why something is done, not only what is done.

Prefer precise technical language.

Avoid vague comments such as:

```java
// Handle data.
```

Prefer specific comments such as:

```java
// Remove shipments without valid zone assignment because they cannot be assigned to a carrier service area.
```

## Naming examples

Use descriptive names:

```
CarrierDemandReader
PopulationValidationWriter
NetworkModeCleaner
FreightScenarioBuilder
TourDistanceAnalyzer
```

Avoid vague names:

```
Helper
Utils
Processor
Manager
Stuff
NewClass
```

Utility classes are acceptable only when they contain cohesive static helper methods.

## Git and version control

Keep commits focused.

Commit messages must be in English.

A good commit message explains the change and its purpose.

Examples:

```
Add validation for missing freight carrier capacities
Refactor zone based transport cost caching
Fix CRS handling in stop access distance calculation
Document baseline scenario configuration
```

Do not use unclear commit messages such as:

```
fix
update
changes
final
new stuff
```

## Review checklist

Before considering a task complete, check:

- Does the code compile?
- Are all names in English?
- Are comments and JavaDoc in English?
- Are units explicit?
- Are assumptions documented?
- Are paths configurable?
- Are random seeds controlled?
- Are outputs traceable?
- Are input files validated?
- Are relevant tests added or updated?
- Are logs useful?
- Is the code consistent with MATSim and eqasim style?
- Is the solution efficient enough for large simulation datasets?
- Is the result scientifically defensible?
- Is the documentation sufficient for another researcher to understand the workflow?

## Preferred response behavior for Claude

When modifying code, first inspect the surrounding code and project structure.

Do not guess APIs if the relevant code can be inspected.

Do not invent missing classes, methods, or dependencies.

If information is missing, state the uncertainty clearly.

When suggesting changes, explain the reason briefly.

When producing code, provide complete and consistent code, not isolated fragments, unless a fragment is explicitly requested.

When a task affects scientific results, explicitly state whether the change may alter outputs.

When implementing performance improvements, explain the expected performance benefit and any trade off.

## Non negotiable rules

- All code and comments must be in English.
- All scientific assumptions must be explicit.
- All relevant parameters must be configurable.
- All output must be traceable.
- All non trivial logic must be documented.
- All important processing steps must be logged.
- All input data must be validated.
- All changes must preserve scientific credibility.
- Completeness, consistency, reproducibility, and clarity are mandatory.
