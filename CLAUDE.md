# CLAUDE.md

## Project context

This project uses MATSim and eqasim for scientific transport simulation research.

The codebase must be treated as research software. Correctness, reproducibility, traceability, clarity, and maintainability are more important than quick but fragile solutions.

The project should follow the style and structure of MATSim and eqasim as closely as possible. The implementation should be professional, modular, documented, and suitable for scientific use.

## Project navigation & status (read this first)

The living project-management layer. Consult these for orientation and **keep them current**
(end every work session with `/close`, which updates them):

- **`PROJECT_STATUS.md`** (committed) — at-a-glance feature matrix: what is built, its status,
  where it lives, what it is validated against, plus the current branch/PR map. **First stop**
  for "what exists / where / how far along".
- **`PROJECT_BACKLOG.md`** (committed) — the single ranked backlog of open / partial /
  deliberately-dropped work. The canonical open-work source (do not start a competing list).
- **`SESSION_LOG.md`** (gitignored, local-only) — chronological work log; append one entry per
  session (newest on top).
- **`docs/codebase/`** (gitignored, local-only) — architecture/onboarding: `STACK`, `STRUCTURE`,
  `ARCHITECTURE`, `CONVENTIONS`, `INTEGRATIONS`, `TESTING`, `CONCERNS`.
- **`docs/superpowers/{specs,plans}/`** (gitignored) — per-feature design specs + execution plans.
- **Claude memory** (`~/.claude/.../memory/`) — curated long-term facts; travels with `~/.claude`,
  not the repo.

**Working discipline (one task, fully closed before the next):** the single canonical
feature workflow is documented in `CONTRIBUTING.md` (brainstorm → plan → worktree → TDD →
verify → review → `git pr` → record). A branch is either merged-and-deleted or explicitly
parked in the backlog with a status — never just left lying around.

**Mandatory at `/close` (end of every session):** update `PROJECT_STATUS.md`,
`PROJECT_BACKLOG.md`, `SESSION_LOG.md`; add a `RUNS.md` row if a run happened; add/update an
ADR in `docs/DECISIONS.md` if a decision was made; sync the GitHub Project board; and apply
the issue-first rule for any newly discovered work. These keep the PM layer from drifting.

**PRs ALWAYS via `git pr`** (a local alias pinned to base `TUBS-IVS/eqasim-bs`, the fork — never
the `eqasim-org/eqasim-bavaria` upstream, which the GitHub web UI defaults to). To recreate the
alias on a new machine:
`git config alias.pr '!gh pr create --repo TUBS-IVS/eqasim-bs --base main'`.
Never push without explicit per-push confirmation (see the git policy below).

## Feature detail

Deep per-feature documentation has been split out of this file (which is now rules + navigation).
Each feature's full description — data sources, flags, references, assumptions — lives in `docs/features/`:

- **MiD 2023 reference tables, economic status, PT/licence IPF** -> [docs/features/mid-reference-tables.md](docs/features/mid-reference-tables.md)
- **Blended regional control targets (MiD x SrV 2023 x LSN arbiter) + popsim registry wiring** -> [docs/features/regional-control-targets.md](docs/features/regional-control-targets.md)
- **IPF household synthesis (joint age x size, age-aware composition)** -> [docs/features/household-synthesis.md](docs/features/household-synthesis.md)
- **Gravity model: per-RegioStaR-7 slope** -> [docs/features/gravity.md](docs/features/gravity.md)
- **Calibration corner + commute distribution** -> [docs/features/calibration-corner.md](docs/features/calibration-corner.md)
- **Distance-dependent detour/circuity (Tier 3)** -> [docs/features/detour-circuity.md](docs/features/detour-circuity.md)
- **Education gravity model (NDS school data)** -> [docs/features/education-gravity.md](docs/features/education-gravity.md)
- **Building-level activity potentials** -> [docs/features/building-potentials.md](docs/features/building-potentials.md)
- **Purpose-resolved secondary activity distances (Tier 1 + Tier 2)** -> [docs/features/secondary-distances.md](docs/features/secondary-distances.md)
- **Long-haul freight injection (german-wide-freight v3)** -> [docs/features/freight.md](docs/features/freight.md)
- **Shared persistent stage-cache (cache_share)** -> [docs/features/cache-share.md](docs/features/cache-share.md)
- **Run analysis + SimWrapper dashboards** -> [docs/features/run-analysis.md](docs/features/run-analysis.md)


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

## Fallback transparency (no silent fallbacks) — MANDATORY

Silent fallbacks are a recurring source of hidden bugs in this project: a stage's
primary (real-data / proper) method quietly fails for some or all items and a
fallback catches it, so the pipeline runs and the tests stay green — but the
intended method never actually worked. Bugs then go undetected because "it ran".
This is unacceptable for research software.

Therefore, for **every** code path that has a fallback (nearest-neighbour fill,
whole-region pool, "rda"/"random" solver fallback, scalar-default-when-map-missing,
default-when-data-absent, except/try recovery, etc.):

1. **Make the fallback observable.** Count and `log` (info/warn) how many items used
   the PRIMARY method vs the FALLBACK, as an explicit rate, e.g.
   `"[stage] primary 9842/10000 (98.4%), fallback 158 (1.6%)"`. Never let a fallback
   fire silently.
2. **Treat a high fallback rate as a failure signal.** If most/all items hit the
   fallback (e.g. above a configurable threshold, and especially ~100%), that almost
   always means the primary method is broken (a format mismatch, an empty join, a
   wrong key) — surface it loudly (`warn`, or `raise` where a high rate cannot be
   scientifically defensible). Add the rate to the per-run validation summary where
   one exists.
3. **Test the primary method, not just the fallback.** A green test that only
   exercises (or silently tolerates) the fallback proves nothing about the real
   method. Add tests/assertions that the PRIMARY path is actually taken on
   representative input, and that the fallback rate stays below an expected bound.
4. **When adding or reviewing ANY stage with a fallback, verify primary-method
   coverage** and wire in the rate logging above. Do this proactively across the
   model, not only when a bug appears.

This applies to existing fallbacks too: when you touch a stage, add the rate
instrumentation if it is missing.

## No invented reference values; convergence is not validation — MANDATORY

Two related failure modes are strictly forbidden because they silently fabricate
scientific claims:

1. **Never invent or assert "target" / "reference" / "ground-truth" values.**
   A reference value (a modal split, a mean distance, a rate to compare against)
   may only be stated if it is traceable to a committed source in the repo (a
   pinned CSV under `eqasim-data/.../`, a documented table in CLAUDE.md, a cited
   external publication with the figure). If no such source exists, say so
   explicitly and label the number as an **assumption** ("ASSUMPTION: ...", with
   the reasoning) -- never as an established target. Do not carry numbers from
   chat / prompt context into a results report as if they were validated
   references. Comparing model output to a made-up target and calling the fit
   "excellent" is a fabricated result and is unacceptable.

2. **Convergence (stability) is NOT the same as validation (matching reality).**
   The eqasim mode-share termination criterion (`eqasim:termination`,
   `ModeShareTracker`) stops the MATSim run when the modal split **stops changing**
   between iterations (smoothed change `< threshold`, default 0.001) -- it has
   **no real-world reference shares** and says **nothing** about whether the
   equilibrium matches observed travel behaviour. Report it precisely: "the run
   converged (mode shares stabilised, change below threshold)". Never phrase a
   stabilised equilibrium as "hit the target" / "calibrated to the data" unless
   the realised shares were actually compared to a committed observed reference.

When unsure whether a number is a real reference or an assumption, treat it as an
assumption and flag it. Cautious, honest, traceable reporting always wins over a
confident-sounding but unsupported claim (see "Research reporting", "Do not
overstate results").

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

**Never run `git push` without explicit user confirmation.** Committing locally is
fine and expected, but every push to any remote must be approved by the user
first: ask before pushing and wait for an explicit "yes" / "push it" / equivalent
each time (a prior confirmation does not authorise later pushes). This applies to
all branches and remotes, including `origin/main`.

**Issue-first for newly discovered work.** When a new feature, gap, or idea surfaces
mid-session, PROPOSE it to the user; only after explicit confirmation, open a GitHub
issue — ALWAYS in the fork `TUBS-IVS/eqasim-bs` (never the `eqasim-org/eqasim-bavaria`
upstream). This guarantees incidental findings are tracked, not forgotten. All issues,
PRs, and the Project board live on the fork only. The canonical feature workflow that
ties this together (brainstorm -> plan -> worktree -> TDD -> verify -> review -> `git pr`
-> record) is documented in `CONTRIBUTING.md`.

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
