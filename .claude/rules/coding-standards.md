---
paths:
  - "**/*.py"
  - "**/*.java"
  - "**/*.ipynb"
  - "**/pom.xml"
  - "configs/**/*.yml"
---

# Coding standards (path-scoped rule)

Claude Code loads this file when a matching file is read, written or edited (`paths` above);
it is not part of the always-loaded `CLAUDE.md`. The rules are binding for all code and
configuration in this repository. The section headings were moved verbatim from `CLAUDE.md`
(ADR-0143), so older code comments citing `CLAUDE.md "<heading>"` resolve to the same heading
here.

## General coding principles

Write code that is correct, reproducible, efficient, traceable, easy to review, easy to maintain, scientifically defensible, and consistent with the existing project structure. Do not: write clever code when clear code is possible; introduce unnecessary abstraction; duplicate logic; silently change behavior; remove existing functionality unless explicitly requested; invent data assumptions (if an assumption is required, document it clearly).

## MATSim and eqasim style

Follow MATSim and eqasim conventions where applicable. Java naming: classes/interfaces `UpperCamelCase`, methods/variables `lowerCamelCase`, constants `UPPER_CASE_WITH_UNDERSCORES`, packages lowercase; avoid abbreviations unless they are established domain terms. Use braces consistently, also for single-line `if`/`else`/`for`/`while` blocks. Prefer readable lines; up to 132 characters is acceptable when it improves readability. Keep code ASCII only where possible, especially in identifiers and string constants.

## Java version and dependencies

Use the Java version required by the active MATSim version. Do not add new dependencies unless clearly justified; before adding one, check whether the task can be solved with standard Java, MATSim utilities, existing project utilities, or existing eqasim components. Document every new dependency and why it is needed.

## Architecture

Prefer small, focused classes, each with one clear responsibility. Keep these concerns separate: input parsing, scenario preparation, configuration, simulation execution, analysis, output writing, validation, visualization preparation. Do not mix analysis logic with simulation setup, or file-system logic with domain logic. Avoid global mutable state. Use dependency injection where MATSim or eqasim already does. Keep MATSim modules, config groups, bindings, and analysis components cleanly separated.

## Configuration

All relevant parameters must be configurable. Do not hard code paths, thresholds, random seeds, scenario names, modes, CRS definitions, or calibration parameters unless there is a strong reason; prefer explicit configuration objects over scattered constants. Every option should have a clear name, documented meaning, default value where reasonable, unit if applicable, and valid range if applicable. Use descriptive, unit-bearing names (e.g. `maximumTransferDistanceMeters`, `randomSeed`); avoid unclear names (e.g. `tmp`, `value1`). Full example lists: [CONVENTIONS.md](../../docs/codebase/CONVENTIONS.md#configuration-names).

## Paths and file handling

Use explicit, reproducible paths; do not rely on hidden working-directory assumptions. Validate that required input files exist before processing and fail early with clear error messages when inputs are missing or invalid (see "Error handling", "Validation and quality control"). Create output directories explicitly. Never overwrite important outputs silently; if overwriting is allowed, make it explicit in the config or log output.

## Scientific reproducibility

Every run should be reproducible. Whenever possible log: scenario name, run identifier, git commit hash if available, MATSim version, eqasim version if applicable, Java version, random seed, config file path, input file paths, output directory, main parameter values, start and end time, runtime, and the number of agents/links/facilities/vehicles/carriers/shipments where relevant. Do not use random processes without an explicit random seed; if deterministic behavior cannot be guaranteed, document why.

## Data provenance

All generated data must be traceable. For derived files, document the input files used, filters applied, assumptions made, coordinate reference system, aggregation level, time period represented, and software step that generated the file. Do not create output files with ambiguous names: prefer descriptive names (e.g. `population_hanover_2025_sample_0.10.xml.gz`), avoid ambiguous ones (e.g. `output.csv`, `final.csv`). Full example lists: [CONVENTIONS.md](../../docs/codebase/CONVENTIONS.md#data-provenance-filenames).

## Documentation

Write concise but useful documentation. Document public classes and methods with JavaDoc when their purpose is not obvious; JavaDoc should explain what it does, its assumptions, expected input data, produced output, units used, and whether it has side effects. Do not write comments that only repeat the code (see "Comments and JavaDoc style").

## Logging

Use structured, meaningful logging that helps debugging and scientific traceability. Log important processing steps, not every minor operation. Levels: `info` for major steps, `warn` for recoverable problems or assumptions, `error` for failures, `debug` for detailed diagnostics. Do not use `System.out.println` in production code.

## Error handling

Fail early when input data is invalid. Use clear exception messages that explain what failed, which input caused it, why the value is invalid, and how to fix it if obvious. Do not swallow exceptions silently. Do not catch broad exceptions unless there is a clear reason. Do not continue after invalid input if this can compromise scientific results.

## Validation and quality control

Every relevant processing step should include plausibility checks. Add validation for: missing files, empty datasets, invalid coordinates, invalid CRS definitions, negative travel times/distances/demand values, invalid mode names/activity types/vehicle capacities, invalid carrier or shipment identifiers, inconsistent totals after aggregation, and unexpected changes in population size or demand totals. For scientific workflows, report validation results in a dedicated summary file where appropriate.

## Performance

Efficiency matters, but correctness comes first. Avoid unnecessary nested loops over large MATSim populations, links, events, carriers, or shipments. Use maps, sets, indexes, and spatial indexes where appropriate. Avoid repeated file reads and repeated route calculations when caching is safe; document caching behavior clearly. Watch memory usage with large event files, populations, networks, or freight scenarios, and prefer streaming for large files when possible.

## MATSim specific rules

Use MATSim APIs instead of manually parsing MATSim XML files unless there is a clear reason. Keep MATSim config handling explicit and reproducible; do not silently modify MATSim config values. When modifying a scenario, clearly separate config creation, scenario loading, scenario modification, controller setup, module installation, simulation execution, and post processing. Use established MATSim concepts correctly (Scenario, Config, Controler, Population, Network, ActivityFacility, Vehicle, Carrier, Plan, Leg, Activity, Events); do not create custom replacements for standard MATSim functionality unless required.

## eqasim specific rules

Follow the eqasim pipeline logic and structure where applicable. Keep scenario generation, population synthesis, simulation setup, and analysis steps modular. Prefer reproducible pipeline stages over one-off scripts. When adapting logic from eqasim examples, keep the structure understandable and document the adaptation. Do not copy code blindly; adapt it to the project context and explain relevant assumptions.

## Geospatial processing

Always document the coordinate reference system. Use metric projected coordinate systems for distance-based calculations; never compute metric distances in WGS84 longitude/latitude coordinates. Validate geometry validity before spatial operations where relevant. Document buffer distances and spatial thresholds in meters, using explicit names (e.g. `maximumStopAccessDistanceMeters`). Full example list: [CONVENTIONS.md](../../docs/codebase/CONVENTIONS.md#geospatial-threshold-names).

## Units

Always make units explicit in variable names, method names, documentation, and output column names. Use explicit names (e.g. `travelTimeSeconds`, `distanceMeters`); avoid ambiguous names (e.g. `time`, `distance`). Full example lists: [CONVENTIONS.md](../../docs/codebase/CONVENTIONS.md#units).

## Output tables

Output tables must be readable, documented, and stable, with explicit, consistent column names. Use snake_case for CSV column names (e.g. `person_id`, `departure_time_seconds`, `distance_meters`); full example list: [CONVENTIONS.md](../../docs/codebase/CONVENTIONS.md#output-table-column-names-snake_case). Do not mix naming styles in one file. Do not rename columns without updating downstream code and documentation.

## Code changes

Before changing code, understand the existing structure. Make minimal necessary changes. Preserve existing behavior unless a behavior change is explicitly requested. For each change, consider: does it break reproducibility? change scientific results? affect previous outputs? require a test? require documentation? require a configuration option?

When modifying code, first inspect the surrounding code and project structure. Do not guess APIs if the relevant code can be inspected. Do not invent missing classes, methods, or dependencies. If information is missing, state the uncertainty clearly. When suggesting changes, explain the reason briefly. Produce complete, consistent code, not isolated fragments, unless a fragment is explicitly requested. When a task affects scientific results, explicitly state whether the change may alter outputs. For performance improvements, explain the expected benefit and any trade off.

## Refactoring

Refactoring is allowed when it improves clarity, maintainability, or performance. Do not refactor large unrelated parts of the codebase while solving a specific issue. Keep refactoring behavior-preserving unless explicitly requested; if behavior changes, document it clearly.

## Comments and JavaDoc style

Comments must be written in English. Use comments to explain why something is done, not only what is done; prefer precise technical language. Avoid vague comments (`// Handle data.`); prefer specific ones (`// Remove shipments without valid zone assignment because they cannot be assigned to a carrier service area.`).

## Naming examples

Use descriptive names (e.g. `CarrierDemandReader`, `FreightScenarioBuilder`); avoid vague names (e.g. `Helper`, `Utils`, `Manager`). Full example lists: [CONVENTIONS.md](../../docs/codebase/CONVENTIONS.md#class--component-naming). Utility classes are acceptable only when they contain cohesive static helper methods.

## Review checklist

Before considering a task complete, check: code compiles; all names, comments, and JavaDoc in English; units explicit; assumptions documented; paths configurable; random seeds controlled; outputs traceable; input files validated; relevant tests added or updated; logs useful; code consistent with MATSim/eqasim style; solution efficient enough for large simulation datasets; result scientifically defensible; documentation sufficient for another researcher to understand the workflow.
