# CONVENTIONS

> **Staleness note (2026-06-26):** reflects the 2026-06-08 state; conventions are
> stable and remain governed by `CLAUDE.md` (English-only code, snake_case CSV
> columns, explicit units, flag-gated default-ON features with byte-identical OFF
> path, MANDATORY no-silent-fallbacks + no-invented-reference rules).

Coding and data conventions in force for `eqasim-bs`. Verified from `CLAUDE.md`,
`AGENTS.md`, the config files, and example stage/test source.

## Language

- **All code, comments, docstrings, log messages, commit messages, and in-repo
  documentation are English** (CLAUDE.md "Language policy"; AGENTS.md "Repository
  conventions"). German is permitted only in external text output when explicitly
  requested, and German statistical field names (Zensus / BA / MiD column labels)
  are kept verbatim for traceability.
- Source should be ASCII where possible; avoid non-ASCII in identifiers and string
  constants (CLAUDE.md "MATSim and eqasim style").
- Chat responses to the user are in German (CLAUDE.md "Language policy"). Note the
  config files contain German prose in comments (e.g. `configs/fixtures/config_local_braunschweig.yml`
  lines 100–124); this is the inherited fork state, not the stated target.

## Naming

- Python identifiers: `snake_case` for functions/variables/modules; constants in
  `UPPER_CASE_WITH_UNDERSCORES` (e.g. `_NDS_LEVELS`, `_SCHOOL_BANDS` in
  `braunschweig/synthesis/locations/education_gravity.py`).
- CSV column names use `snake_case` with **explicit units in the name**
  (CLAUDE.md "Output tables" / "Units"): e.g. `distance_meters`,
  `travel_time_seconds`, `departure_time_seconds`. Spatial thresholds carry the
  unit too (`maximumStopAccessDistanceMeters` style on the Java side).
- Java side follows MATSim conventions: `UpperCamelCase` classes,
  `lowerCamelCase` methods/vars (CLAUDE.md "MATSim and eqasim style").

## synpp stage pattern

Every pipeline stage is a module with `configure(context)` (declares config keys
+ upstream stages) and `execute(context)` (produces the cached output). Verified
in `braunschweig/synthesis/locations/education_gravity.py`. Stages are referenced
by dotted module path and remapped via the config `aliases:` block — never call a
fork module directly; go through the alias (AGENTS.md "Repository conventions").

## Reference data as committed CSVs, not Python literals

Numerical reference values (MiD 2023 constraint tables, Mikrozensus, school
distances, university enrollment) are **not** hard-coded in Python. They live as
CSV files under `eqasim-data/data/braunschweig/{mid,mikrozensus,schools}/` and are
loaded by dedicated reader modules. Hard-coding percentages/coordinates/capacities
in Python is explicitly prohibited; values are changed by editing a **seed script**
(e.g. `scripts/seed_mid_constraint_tables.py`,
`scripts/seed_mid_t43_school_distance.py`, `scripts/seed_nds_hochschulen.py`) or
the source xlsx and re-running it (CLAUDE.md "Reference data" and "Education
gravity model"). Each seeded value carries a provenance comment.

## Configuration over constants

Paths, thresholds, seeds, slopes, and calibration parameters are config keys, not
literals (CLAUDE.md "Configuration"; "Non negotiable rules"). Calibrated values
(`gravity_slope_by_regiostar7`, `education_gravity_slope_by_level_rs7`,
`education_university_slope`) are pinned in the config and annotated "do not
hand-edit; re-run the calibration script and paste its YAML".

## Determinism

- A single fixed `random_seed` (1234) drives all randomness; the education gravity
  assignment explicitly routes all sampling through it (CLAUDE.md "The model").
- Tests must be deterministic and use small synthetic data, not large external
  datasets (CLAUDE.md "Tests"). Example: `tests/test_education_gravity_stage.py`
  builds tiny in-memory GeoDataFrames with fixed coordinates.

## Inherited-code fencing

In files mixing Bavaria-inherited and Braunschweig-specific code, blocks are
fenced with `# --- Inherited from eqasim-bavaria ---` / `# --- Braunschweig-specific ---`
(AGENTS.md). Note: on the current branch the standalone `bavaria/` package has
already been removed (see STRUCTURE.md / CONCERNS.md), so this convention now
applies mainly to inline inherited blocks.

## Provenance and traceability

Derived files must document inputs, filters, assumptions, CRS, and the generating
step; output filenames must be explicit and stable (CLAUDE.md "Data provenance",
"Output tables"). Geometric/distance work uses a metric projected CRS
(EPSG:25832 / UTM 32N), never WGS84 lon/lat for distances (CLAUDE.md "Geospatial
processing"; bounding box in DOWNLOAD_CHECKLIST_BS.md).

## Evidence

- `CLAUDE.md` ("Language policy", "Configuration", "Output tables", "Units", "Tests", "Reference data")
- `AGENTS.md` ("Repository conventions enforced now")
- `braunschweig/synthesis/locations/education_gravity.py` (configure/execute, constant naming)
- `tests/test_education_gravity_stage.py` (deterministic synthetic data)
- `configs/fixtures/config_local_braunschweig.yml` (pinned calibrated values + "do not hand-edit" notes)

---

## Cross-repo addendum: conventions the popsimprep refactor must adopt

Added 2026-06-08. popsimprep currently violates several eqasim-bs conventions; the
refactor must bring its code into line.

- **No notebooks as production workflow.** popsimprep's entire pipeline is a
  Jupyter notebook (`PopSimPrep-StartHere-v2.ipynb`) with hidden state + in-place
  YAML mutation. Per CLAUDE.md "prefer reproducible pipeline stages over one-off
  scripts" and the brief, the logic must move into small, testable Python modules
  exposed as synpp stages; the notebook may remain as documentation/example only.
- **Config over constants** (already a hard eqasim-bs rule) is broadly broken in
  popsimprep: CRS, cell-id format, separators, geography names, control importance,
  and the kernwo filter are all hard-coded in code cells. All must become config
  keys under a `population.*` group.
- **One config source of truth.** Replace the 4-way split (Cell-2 vars /
  `prep_config.json` / mutated `settings.yaml` / `verification.yaml`) with one
  declarative config object; generate the PopulationSim `settings.yaml` from it in
  code rather than shipping a pre-edited, hand-mutated YAML.
- **Reference/control definitions as declarative data, not a hand-edited CSV.** The
  `_prep3_controls.csv` mid-pipeline manual edit must become a checked-in,
  documented control spec (mirrors eqasim-bs's "reference data as committed CSVs,
  not Python literals" + seed-script discipline).
- **English-only + units + snake_case columns** apply to the new code. German
  Zensus/MiD field names stay verbatim (as in eqasim-bs) for traceability, but new
  identifiers, comments, and output columns follow the eqasim-bs naming rules.
- **No silent fallbacks (MANDATORY).** Per CLAUDE.md "Fallback transparency": the
  building round-robin fallback (Step 5), orphan-cell handling, missing-control and
  failed-batch paths must log primary-vs-fallback rates and never fall back between
  workflows. Selecting a misconfigured `population.method` must fail loudly, not
  silently switch to another method (brief §9).

Evidence: `popsimprep/PopSimPrep-StartHere-v2.ipynb`,
`popsimprep/popsim/configs/_prep3_controls.csv`, `popsimprep/batch_run_popsim.py`,
`CLAUDE.md` ("Fallback transparency", "Configuration", "eqasim specific rules").

---

## Example lists (moved from CLAUDE.md)

Full good/bad example lists relocated from `CLAUDE.md` during the 2026-07 PM-layer
compression. `CLAUDE.md` keeps one good + one bad example per topic and links here
for the complete lists. These are illustrative examples; the binding rules remain in
`CLAUDE.md`.

### Configuration names

Descriptive, unit-bearing (CLAUDE.md "Configuration"):

```java
maximumTransferDistanceMeters
sampleSize
randomSeed
inputPopulationPath
outputDirectory
```

Avoid:

```
x
tmp
value1
param
data
```

### Data-provenance filenames

Prefer explicit, stable names (CLAUDE.md "Data provenance"):

```
population_hanover_2025_sample_0.10.xml.gz
carrier_tours_baseline_2025_weekday.csv
network_cleaned_epsg25832.xml.gz
validation_summary_b2b_share_by_zone.csv
```

Avoid ambiguous names:

```
output.csv
final.csv
new_result.csv
test.xml
```

### Units

Explicit units in the name (CLAUDE.md "Units"):

```
travelTimeSeconds
distanceMeters
speedMetersPerSecond
emissionsGrams
costEuro
durationHours
```

Avoid ambiguous names:

```
time
distance
speed
cost
```

### Output-table column names (snake_case)

CLAUDE.md "Output tables":

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

### Geospatial threshold names

Distances/thresholds in meters (CLAUDE.md "Geospatial processing"):

```
maximumStopAccessDistanceMeters
transferSearchRadiusMeters
zoneAssignmentBufferMeters
```

### Class / component naming

Descriptive (CLAUDE.md "Naming examples"):

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

### Commit-message examples

Clear messages that explain the change and its purpose (CLAUDE.md "Git and version control"):

```
Add validation for missing freight carrier capacities
Refactor zone based transport cost caching
Fix CRS handling in stop access distance calculation
Document baseline scenario configuration
```

Avoid unclear messages:

```
fix
update
changes
final
new stuff
```
