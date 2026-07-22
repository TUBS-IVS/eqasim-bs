# CLAUDE.md

## Project context

This project uses MATSim and eqasim for scientific transport simulation research. Treat the codebase as research software: correctness, reproducibility, traceability, clarity, and maintainability outrank quick but fragile solutions. Follow the style and structure of MATSim and eqasim as closely as possible; implementations must be professional, modular, documented, and suitable for scientific use.

## Project navigation & status (read this first)

The living project-management layer. Consult these for orientation and **keep them current** (end every work session with `/close`, which updates them):

- **`PROJECT_STATUS.md`** (committed) — ≤120-line dashboard: live state, feature matrix (built / status / location / validated-against), branch-PR map, top-of-backlog pointer. **First stop** for "what exists / where / how far along".
- **`PROJECT_BACKLOG.md`** (committed) — the single ranked, **open-work-only** backlog (≤250 lines); canonical open-work source, do not start a competing list. Dated history: `docs/archive/BACKLOG_HISTORY.md`.
- **`SESSION_LOG.md`** (gitignored) — chronological log, newest on top; rotates at 10 entries (older -> `docs/archive/SESSION_LOG_*.md`, gitignored).
- **`docs/DECISIONS.md`** (committed) — ADRs, with a one-line-per-ADR index header.
- **`docs/codebase/`** (committed; only `.codebase-scan.txt` gitignored) — architecture/onboarding: `STACK`, `STRUCTURE`, `ARCHITECTURE`, `CONVENTIONS`, `INTEGRATIONS`, `TESTING`, `CONCERNS`.
- **`docs/archive/`** (committed) — rotated history (`BACKLOG_HISTORY.md`, older `SESSION_LOG_*.md`).
- **`docs/superpowers/{specs,plans}/`** (gitignored) — per-feature design specs + execution plans.
- **Claude memory** (`~/.claude/.../memory/`) — curated long-term facts: `MEMORY.md` index plus `ARCHIVE.md` (condensed completed work); travels with `~/.claude`, not the repo.

**Working discipline (one task, fully closed before the next):** the single canonical feature workflow is documented in `CONTRIBUTING.md` (brainstorm -> plan -> worktree -> TDD -> verify -> review -> `git pr` -> record). A branch is either merged-and-deleted or explicitly parked in the backlog with a status — never just left lying around.

**Mandatory at `/close` (end of every session):** update `PROJECT_STATUS.md`, `PROJECT_BACKLOG.md`, `SESSION_LOG.md`; add a `RUNS.md` row if a run happened; add/update an ADR in `docs/DECISIONS.md` if a decision was made; sync the GitHub Project board; apply the issue-first rule for newly discovered work.

**PRs ALWAYS via `git pr`** (a local alias pinned to base `TUBS-IVS/eqasim-bs`, the fork — never the `eqasim-org/eqasim-bavaria` upstream, which the GitHub web UI defaults to). To recreate the alias on a new machine:
`git config alias.pr '!gh pr create --repo TUBS-IVS/eqasim-bs --base main'`.
Never push without explicit per-push confirmation (see the git policy below).

## Fact ownership — one fact, one place

| Fact type | Owning file | Others get at most |
|---|---|---|
| Decision + rationale | docs/DECISIONS.md (ADR) | 1 status line / link |
| Open work item | PROJECT_BACKLOG.md | top-5 pointer in STATUS |
| Feature existence/status/location | PROJECT_STATUS.md matrix | — |
| Session narrative | SESSION_LOG.md (local) | — |
| Run record | RUNS.md | — |
| Durable lesson / working rule | memory (type: feedback) | — |
| Data-source pointer | memory (type: reference) or docs/features | — |
| Feature deep-dive | docs/features/*.md | link from STATUS matrix |

Layer budgets (enforced at /close): CLAUDE.md ≤ 12 KB · MEMORY.md ≤ 12 KB (one line per memory, hooks ≤ ~110 chars) · PROJECT_STATUS.md ≤ 120 lines · PROJECT_BACKLOG.md ≤ 250 lines · SESSION_LOG.md ≤ 10 entries.

## Feature detail

Deep per-feature documentation (data sources, flags, references, assumptions) lives in `docs/features/*.md` and is indexed, with current status, from the `PROJECT_STATUS.md` feature matrix. Per "Fact ownership" below, feature existence/status/location is owned by the STATUS matrix and each deep-dive by its `docs/features/` page; go to the STATUS matrix for the per-feature links (gravity, education/university gravity, household synthesis, regional control targets, MiD reference tables, building potentials, secondary distances, detour/circuity, student in-commuters, freight, cache-share, calibration corner, run analysis + SimWrapper dashboards).

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

Write code that is correct, reproducible, efficient, traceable, easy to review, easy to maintain, scientifically defensible, and consistent with the existing project structure.

Do not write clever code when clear code is possible. Do not introduce unnecessary abstraction. Do not duplicate logic. Do not silently change behavior. Do not remove existing functionality unless explicitly requested. Do not invent data assumptions; if an assumption is required, document it clearly.

## MATSim and eqasim style

Follow MATSim and eqasim conventions where applicable. Use Java naming conventions: classes and interfaces `UpperCamelCase`; methods and variables `lowerCamelCase`; constants `UPPER_CASE_WITH_UNDERSCORES`; package names lowercase; avoid abbreviations unless they are established domain terms.

Use braces consistently, also for single-line `if`, `else`, `for`, and `while` blocks. Prefer readable lines; lines up to 132 characters are acceptable when this improves readability. Keep code ASCII only where possible, especially in identifiers and string constants.

## Java version and dependencies

Use the Java version required by the active MATSim version. Do not add new dependencies unless they are clearly justified. Before adding one, check whether the task can be solved with standard Java, MATSim utilities, existing project utilities, or existing eqasim components. Document every new dependency and why it is needed.

## Architecture

Prefer small, focused classes; each class should have one clear responsibility. Keep these concerns separate: input parsing, scenario preparation, configuration, simulation execution, analysis, output writing, validation, and visualization preparation.

Avoid mixing analysis logic with simulation setup. Avoid mixing file system logic with domain logic. Avoid global mutable state. Use dependency injection where it is already used by MATSim or eqasim. Keep MATSim modules, config groups, bindings, and analysis components cleanly separated.

## Configuration

All relevant parameters must be configurable. Do not hard code paths, thresholds, random seeds, scenario names, modes, CRS definitions, or calibration parameters unless there is a strong reason. Prefer explicit configuration objects over scattered constants.

Every configuration option should have a clear name, a documented meaning, a default value where reasonable, a unit if applicable, and a valid range if applicable. Use descriptive, unit-bearing names (e.g. `maximumTransferDistanceMeters`, `randomSeed`); avoid unclear names (e.g. `tmp`, `value1`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#configuration-names).

## Paths and file handling

Use explicit and reproducible paths; do not rely on hidden working-directory assumptions. Validate that required input files exist before processing and fail early with clear error messages when inputs are missing or invalid (see "Error handling", "Validation and quality control"). Create output directories explicitly. Never overwrite important outputs silently; if overwriting is allowed, make it explicit in the configuration or log output.

## Scientific reproducibility

Every simulation or analysis run should be reproducible. Whenever possible, log: scenario name; run identifier; git commit hash if available; MATSim version; eqasim version if applicable; Java version; random seed; config file path; input file paths; output directory; main parameter values; start and end time; runtime; and the number of agents, links, facilities, vehicles, carriers, or shipments where relevant.

Do not use random processes without an explicit random seed. If deterministic behavior cannot be guaranteed, document why.

## Data provenance

All generated data must be traceable. When producing derived files, document which input files were used, which filters were applied, which assumptions were made, which coordinate reference system was used, which aggregation level was used, which time period was represented, and which software step generated the file.

Do not create output files with ambiguous names. Prefer descriptive names (e.g. `population_hanover_2025_sample_0.10.xml.gz`); avoid ambiguous names (e.g. `output.csv`, `final.csv`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#data-provenance-filenames).

## Documentation

Write concise but useful documentation. Document public classes and public methods with JavaDoc when their purpose is not obvious; JavaDoc should explain what the class/method does, its assumptions, expected input data, produced output, units used, and whether it has side effects. Do not write comments that only repeat the code (see "Comments and JavaDoc style").

## Logging

Use structured and meaningful logging that helps with debugging and scientific traceability. Log important processing steps, not every minor operation. Use appropriate log levels: `info` for major processing steps, `warn` for recoverable problems or assumptions, `error` for failures, `debug` for detailed diagnostics. Do not use `System.out.println` in production code.

## Error handling

Fail early when input data is invalid. Use clear exception messages that explain what failed, which input caused the problem, why the value is invalid, and how to fix it if obvious. Do not swallow exceptions silently. Do not catch broad exceptions unless there is a clear reason. Do not continue after invalid input if this can compromise scientific results.

## Validation and quality control

Every relevant processing step should include plausibility checks. Add validation for: missing files; empty datasets; invalid coordinates; invalid CRS definitions; negative travel times, distances, or demand values; invalid mode names, activity types, or vehicle capacities; invalid carrier or shipment identifiers; inconsistent totals after aggregation; and unexpected changes in population size or demand totals.

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

Add tests for non trivial logic. Prefer small unit tests for data transformations, filtering rules, assignment logic, cost calculations, aggregation logic, routing helper logic, and validation checks. Use integration tests when testing MATSim scenario setup or full pipeline behavior. Tests must be deterministic. Use small synthetic test data where possible; do not rely on large external datasets in unit tests.

## Performance

Efficiency matters, but correctness comes first. Avoid unnecessary nested loops over large MATSim populations, links, events, carriers, or shipments. Use maps, sets, indexes, and spatial indexes where appropriate. Avoid repeated file reads and repeated route calculations when caching is safe; document caching behavior clearly. Be careful with memory usage when processing large event files, populations, networks, or freight scenarios, and prefer streaming approaches for large files when possible.

## MATSim specific rules

Use MATSim APIs instead of manually parsing MATSim XML files unless there is a clear reason. Keep MATSim config handling explicit and reproducible. Do not silently modify MATSim config values. When modifying a MATSim scenario, clearly separate config creation, scenario loading, scenario modification, controller setup, module installation, simulation execution, and post processing.

Use established MATSim concepts correctly: Scenario, Config, Controler, Population, Network, ActivityFacility, Vehicle, Carrier, Plan, Leg, Activity, Events. Do not create custom replacements for standard MATSim functionality unless required.

## eqasim specific rules

Follow the pipeline logic and structure used by eqasim where applicable. Keep scenario generation, population synthesis, simulation setup, and analysis steps modular. Prefer reproducible pipeline stages over one-off scripts. If adapting logic from eqasim examples, keep the structure understandable and document the adaptation. Do not copy code blindly; adapt it to the project context and explain relevant assumptions.

## Geospatial processing

Always document the coordinate reference system. Use metric projected coordinate systems for distance-based calculations; do not calculate metric distances in WGS84 longitude and latitude coordinates. Validate geometry validity before spatial operations where relevant. Document buffer distances and spatial thresholds in meters, using explicit names (e.g. `maximumStopAccessDistanceMeters`). Full example list: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#geospatial-threshold-names).

## Units

Always make units explicit in variable names, method names, documentation, and output column names. Use explicit names (e.g. `travelTimeSeconds`, `distanceMeters`); avoid ambiguous names (e.g. `time`, `distance`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#units).

## Output tables

Output tables must be readable, documented, and stable. Column names should be explicit and consistent. Use snake_case for CSV column names (e.g. `person_id`, `departure_time_seconds`, `distance_meters`); full example list: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#output-table-column-names-snake_case). Do not mix naming styles in one file. Do not rename columns without updating downstream code and documentation.

## Research reporting

When generating analysis outputs, include enough information to reproduce the result. Every figure or table should be traceable to its input data, scenario, processing script, parameters, and date or run identifier.

Do not overstate results. Use cautious scientific language. Report limitations where relevant. Distinguish clearly between observed data, modeled data, assumptions, and derived indicators.

## Code changes

Before changing code, understand the existing structure. Make minimal necessary changes. Preserve existing behavior unless a behavior change is explicitly requested. When making a change, consider: does this break reproducibility? change scientific results? affect previous outputs? require a test? require documentation? require a configuration option?

## Refactoring

Refactoring is allowed when it improves clarity, maintainability, or performance. Do not refactor large unrelated parts of the codebase while solving a specific issue. Keep refactoring behavior preserving unless explicitly requested. If behavior changes, document the change clearly.

## Comments and JavaDoc style

Comments must be written in English. Use comments to explain why something is done, not only what is done. Prefer precise technical language.

Avoid vague comments:

```java
// Handle data.
```

Prefer specific comments:

```java
// Remove shipments without valid zone assignment because they cannot be assigned to a carrier service area.
```

## Naming examples

Use descriptive names (e.g. `CarrierDemandReader`, `FreightScenarioBuilder`); avoid vague names (e.g. `Helper`, `Utils`, `Manager`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#class--component-naming). Utility classes are acceptable only when they contain cohesive static helper methods.

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

Keep commits focused. Commit messages must be in English and explain the change and its purpose. Use clear messages (e.g. `Add validation for missing freight carrier capacities`); avoid unclear messages (e.g. `fix`, `update`, `changes`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#commit-message-examples).

## Review checklist

Before considering a task complete, check: the code compiles; all names, comments, and JavaDoc are in English; units are explicit; assumptions are documented; paths are configurable; random seeds are controlled; outputs are traceable; input files are validated; relevant tests are added or updated; logs are useful; the code is consistent with MATSim and eqasim style; the solution is efficient enough for large simulation datasets; the result is scientifically defensible; and the documentation is sufficient for another researcher to understand the workflow.

## Preferred response behavior for Claude

When modifying code, first inspect the surrounding code and project structure. Do not guess APIs if the relevant code can be inspected. Do not invent missing classes, methods, or dependencies. If information is missing, state the uncertainty clearly. When suggesting changes, explain the reason briefly. When producing code, provide complete and consistent code, not isolated fragments, unless a fragment is explicitly requested. When a task affects scientific results, explicitly state whether the change may alter outputs. When implementing performance improvements, explain the expected performance benefit and any trade off.

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
