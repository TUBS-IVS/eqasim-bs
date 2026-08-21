# CLAUDE.md

## Project context

MATSim + eqasim scientific transport-simulation research. Treat the codebase as research software: correctness, reproducibility, traceability, clarity, and maintainability outrank quick but fragile solutions. Follow MATSim/eqasim style and structure closely; implementations must be professional, modular, documented, and suitable for scientific use.

## Model documentation governance (read this first)

Information is maintained ONCE, machine-readable, and rendered into views (full ownership model: `docs/DOCUMENTATION_GOVERNANCE.md`; migration decision: ADR-0077). Truth hierarchy:

- **Code** = implementation truth; **synpp DAG** (`synpp.run(dryrun=True)`, committed snapshots `docs/registry/dag/*.json`) = stage existence/dependency truth.
- **Resolved canonical production config** (`configs/base_bs.yml` + `configs/overlays/test_100pct.yml`; flags live ONLY in the base, overlays are scale-only) = active-state truth.
- **Stage Registry** `docs/registry/stages/*.yml` = stage semantics + Bavaria lineage (inherited/configured/extended/overridden/braunschweig_new).
- **Feature Registry** `docs/registry/features/*.yml` = feature semantics, evidence pointers, lifecycle × production × per-pipeline applicability.
- **Data Registry** `docs/registry/data/*.yml` = dataset provenance, licensing, exact expected paths (README data setup is checked against it).
- **ADRs** `docs/decisions/ADR-NNNN-*.md` (one file per record; ids append-only; `docs/decisions/README.md` has numbering notes) = scientific/architectural rationale incl. rejected approaches.
- **GitHub issues** on `TUBS-IVS/eqasim-bs` = the ONLY backlog (issue-first rule below).
- **Run manifests** `docs/runs/<run_id>.yml` = executed runs + validation evidence. Never claim validation without reference + run + comparison evidence recorded there.
- **README.md** = public setup/install/data-acquisition contract; assess README impact whenever repository dependencies, environment, required inputs, input paths, downloader/import scripts, the canonical config, verification, run commands, or outputs change.
- **`docs/generated/*.md`** (STATUS/PIPELINE/STAGES/FEATURES/DATA/LINEAGE/DECISIONS/RUNS) = generated views — NEVER edit manually; rebuild with `python -m braunschweig.documentation build`; `... check` must show 0 FAIL (CI runs it metadata-only).
- **Retired (pointer stubs only, content archived under `docs/archive/`):** `PROJECT_STATUS.md`, `PROJECT_BACKLOG.md`, the `RUNS.md` ledger, the monolithic `docs/DECISIONS.md`. Never resurrect them or any parallel STATUS-style state; the readiness register (`docs/readiness/`, branch `feature/readiness-register`) was generalized into the Feature Registry and stays historical.
- **`docs/codebase/`** = contributor notes: shared `*.md` files are curated whole-system overviews, `notes/<slug>.md` is one note per module/package/mechanism (see the shape rule below).
- **`SESSION_LOG.md`** (gitignored) = local session narrative; **Claude memory** (`~/.claude/.../memory/`) = durable lessons; **`docs/superpowers/{specs,plans}/`** (gitignored) = per-feature designs; **`docs/features/*.md`** = per-feature scientific method (no live state — production state lives in the registry/generated views).

**Shape rule — one fact, one file, one owner.** Every home that receives PER-INSTANCE facts is one-file-per-record: the three registries, ADRs, run manifests, and `docs/codebase/notes/<slug>.md` for a contributor note about ONE module/package/mechanism. Concurrent branches then never write the same file, so there is nothing to merge. Three binding consequences:
- **Shared prose files are curated overviews, never append targets.** `docs/codebase/{ARCHITECTURE,STRUCTURE,CONCERNS,TESTING,...}.md` describe the system as a whole and LINK to records. If the edit you are about to make is "append a section about the thing I just did", it belongs in a record instead. (Seven parallel #267 branches each appended a per-module section to `ARCHITECTURE.md`, and earlier a row to the retired `PROJECT_STATUS.md`; both produced merge conflicts that no amount of care avoids, because the SHAPE was wrong, not the writing.)
- **No fact lives in two files.** If it must appear in a second place, that place gets a link, not a copy.
- **Reference code by symbol, never by line number.** Path + function/constant name, never `model.py:307`: line pins rot on the next edit, and `braunschweig/documentation/checks.py` cannot check its own references because it IS the gate.

**Where a new fact goes:** stage exists/changed → Stage Registry · feature semantics/state → Feature Registry · dataset → Data Registry · WHY a choice was made, incl. rejected options → ADR · what a run produced or proved → run manifest · work still to do → GitHub issue · module/package layout, or a rule maintainers must follow → `docs/codebase/notes/<slug>.md` · public setup contract → README. A chat summary, a PR body or `SESSION_LOG.md` is never a fact's only home.

**Parallel branches:** merge `origin/main` into your branch before `git pr`, and re-run the verification gate AFTER that merge — a green A/B against a stale base says nothing about the merge state (#284's coverage guard plus #287's split turned `main` red exactly this way, each PR having been green on its own). While sibling branches are open, do not edit a shared prose file at all.

Maintenance duties: every new/changed stage → Stage Registry (+ `... dag` if the graph changed); every feature → Feature Registry; every dataset → Data Registry + `scripts/verify_braunschweig_inputs.py` + README; every substantive decision → ADR; every significant run → run manifest; every substantial PR → `documentation build` + `check` (the PR template carries this checklist). Never invent history: `unknown` is a valid value; convergence ≠ validation; a smoke ≠ validation.

**Working discipline (one task, fully closed before the next):** the canonical feature workflow is in `CONTRIBUTING.md` (brainstorm -> plan -> worktree -> TDD -> verify -> review -> `git pr` -> record). A branch is either merged-and-deleted or explicitly parked in a GitHub issue — never just left lying around.

**Mandatory at `/close` (end of every session):** update the registries/ADRs/run manifests for what happened (step 9 of `CONTRIBUTING.md`), rebuild + check the generated docs, update `SESSION_LOG.md`, sync the GitHub Project board, and apply the issue-first rule for newly discovered work.

**PRs ALWAYS via `git pr`** (a local alias pinned to base `TUBS-IVS/eqasim-bs`, the fork — never the `eqasim-org/eqasim-bavaria` upstream, which the GitHub web UI defaults to). To recreate the alias on a new machine:
`git config alias.pr '!gh pr create --repo TUBS-IVS/eqasim-bs --base main'`.
Never push without explicit per-push confirmation (see the git policy below).

Layer budgets (checked only at /close — exceeding one never blocks work): CLAUDE.md ≤ 23 KB · MEMORY.md ≤ 12 KB (one line per memory, hooks ≤ ~110 chars) · SESSION_LOG.md ≤ 10 entries. Registries have no budget (one fact per file scales).

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

Write code that is correct, reproducible, efficient, traceable, easy to review, easy to maintain, scientifically defensible, and consistent with the existing project structure. Do not: write clever code when clear code is possible; introduce unnecessary abstraction; duplicate logic; silently change behavior; remove existing functionality unless explicitly requested; invent data assumptions (if an assumption is required, document it clearly).

## MATSim and eqasim style

Follow MATSim and eqasim conventions where applicable. Java naming: classes/interfaces `UpperCamelCase`, methods/variables `lowerCamelCase`, constants `UPPER_CASE_WITH_UNDERSCORES`, packages lowercase; avoid abbreviations unless they are established domain terms. Use braces consistently, also for single-line `if`/`else`/`for`/`while` blocks. Prefer readable lines; up to 132 characters is acceptable when it improves readability. Keep code ASCII only where possible, especially in identifiers and string constants.

## Java version and dependencies

Use the Java version required by the active MATSim version. Do not add new dependencies unless clearly justified; before adding one, check whether the task can be solved with standard Java, MATSim utilities, existing project utilities, or existing eqasim components. Document every new dependency and why it is needed.

## Architecture

Prefer small, focused classes, each with one clear responsibility. Keep these concerns separate: input parsing, scenario preparation, configuration, simulation execution, analysis, output writing, validation, visualization preparation. Do not mix analysis logic with simulation setup, or file-system logic with domain logic. Avoid global mutable state. Use dependency injection where MATSim or eqasim already does. Keep MATSim modules, config groups, bindings, and analysis components cleanly separated.

## Configuration

All relevant parameters must be configurable. Do not hard code paths, thresholds, random seeds, scenario names, modes, CRS definitions, or calibration parameters unless there is a strong reason; prefer explicit configuration objects over scattered constants. Every option should have a clear name, documented meaning, default value where reasonable, unit if applicable, and valid range if applicable. Use descriptive, unit-bearing names (e.g. `maximumTransferDistanceMeters`, `randomSeed`); avoid unclear names (e.g. `tmp`, `value1`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#configuration-names).

## Paths and file handling

Use explicit, reproducible paths; do not rely on hidden working-directory assumptions. Validate that required input files exist before processing and fail early with clear error messages when inputs are missing or invalid (see "Error handling", "Validation and quality control"). Create output directories explicitly. Never overwrite important outputs silently; if overwriting is allowed, make it explicit in the config or log output.

## Scientific reproducibility

Every run should be reproducible. Whenever possible log: scenario name, run identifier, git commit hash if available, MATSim version, eqasim version if applicable, Java version, random seed, config file path, input file paths, output directory, main parameter values, start and end time, runtime, and the number of agents/links/facilities/vehicles/carriers/shipments where relevant. Do not use random processes without an explicit random seed; if deterministic behavior cannot be guaranteed, document why.

## Data provenance

All generated data must be traceable. For derived files, document the input files used, filters applied, assumptions made, coordinate reference system, aggregation level, time period represented, and software step that generated the file. Do not create output files with ambiguous names: prefer descriptive names (e.g. `population_hanover_2025_sample_0.10.xml.gz`), avoid ambiguous ones (e.g. `output.csv`, `final.csv`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#data-provenance-filenames).

## Documentation

Write concise but useful documentation. Document public classes and methods with JavaDoc when their purpose is not obvious; JavaDoc should explain what it does, its assumptions, expected input data, produced output, units used, and whether it has side effects. Do not write comments that only repeat the code (see "Comments and JavaDoc style").

## Logging

Use structured, meaningful logging that helps debugging and scientific traceability. Log important processing steps, not every minor operation. Levels: `info` for major steps, `warn` for recoverable problems or assumptions, `error` for failures, `debug` for detailed diagnostics. Do not use `System.out.println` in production code.

## Error handling

Fail early when input data is invalid. Use clear exception messages that explain what failed, which input caused it, why the value is invalid, and how to fix it if obvious. Do not swallow exceptions silently. Do not catch broad exceptions unless there is a clear reason. Do not continue after invalid input if this can compromise scientific results.

## Validation and quality control

Every relevant processing step should include plausibility checks. Add validation for: missing files, empty datasets, invalid coordinates, invalid CRS definitions, negative travel times/distances/demand values, invalid mode names/activity types/vehicle capacities, invalid carrier or shipment identifiers, inconsistent totals after aggregation, and unexpected changes in population size or demand totals. For scientific workflows, report validation results in a dedicated summary file where appropriate.

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

Add tests for non trivial logic. Prefer small unit tests for data transformations, filtering rules, assignment logic, cost calculations, aggregation logic, routing helper logic, and validation checks. Use integration tests for MATSim scenario setup or full pipeline behavior. Tests must be deterministic. Use small synthetic test data where possible; do not rely on large external datasets in unit tests.

## Performance

Efficiency matters, but correctness comes first. Avoid unnecessary nested loops over large MATSim populations, links, events, carriers, or shipments. Use maps, sets, indexes, and spatial indexes where appropriate. Avoid repeated file reads and repeated route calculations when caching is safe; document caching behavior clearly. Watch memory usage with large event files, populations, networks, or freight scenarios, and prefer streaming for large files when possible.

## MATSim specific rules

Use MATSim APIs instead of manually parsing MATSim XML files unless there is a clear reason. Keep MATSim config handling explicit and reproducible; do not silently modify MATSim config values. When modifying a scenario, clearly separate config creation, scenario loading, scenario modification, controller setup, module installation, simulation execution, and post processing. Use established MATSim concepts correctly (Scenario, Config, Controler, Population, Network, ActivityFacility, Vehicle, Carrier, Plan, Leg, Activity, Events); do not create custom replacements for standard MATSim functionality unless required.

## eqasim specific rules

Follow the eqasim pipeline logic and structure where applicable. Keep scenario generation, population synthesis, simulation setup, and analysis steps modular. Prefer reproducible pipeline stages over one-off scripts. When adapting logic from eqasim examples, keep the structure understandable and document the adaptation. Do not copy code blindly; adapt it to the project context and explain relevant assumptions.

## Geospatial processing

Always document the coordinate reference system. Use metric projected coordinate systems for distance-based calculations; never compute metric distances in WGS84 longitude/latitude coordinates. Validate geometry validity before spatial operations where relevant. Document buffer distances and spatial thresholds in meters, using explicit names (e.g. `maximumStopAccessDistanceMeters`). Full example list: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#geospatial-threshold-names).

## Units

Always make units explicit in variable names, method names, documentation, and output column names. Use explicit names (e.g. `travelTimeSeconds`, `distanceMeters`); avoid ambiguous names (e.g. `time`, `distance`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#units).

## Output tables

Output tables must be readable, documented, and stable, with explicit, consistent column names. Use snake_case for CSV column names (e.g. `person_id`, `departure_time_seconds`, `distance_meters`); full example list: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#output-table-column-names-snake_case). Do not mix naming styles in one file. Do not rename columns without updating downstream code and documentation.

## Research reporting

When generating analysis outputs, include enough information to reproduce the result: every figure or table should be traceable to its input data, scenario, processing script, parameters, and date or run identifier. Do not overstate results; use cautious scientific language and report limitations where relevant. Distinguish clearly between observed data, modeled data, assumptions, and derived indicators.

## Code changes

Before changing code, understand the existing structure. Make minimal necessary changes. Preserve existing behavior unless a behavior change is explicitly requested. For each change, consider: does it break reproducibility? change scientific results? affect previous outputs? require a test? require documentation? require a configuration option?

## Refactoring

Refactoring is allowed when it improves clarity, maintainability, or performance. Do not refactor large unrelated parts of the codebase while solving a specific issue. Keep refactoring behavior-preserving unless explicitly requested; if behavior changes, document it clearly.

## Comments and JavaDoc style

Comments must be written in English. Use comments to explain why something is done, not only what is done; prefer precise technical language. Avoid vague comments (`// Handle data.`); prefer specific ones (`// Remove shipments without valid zone assignment because they cannot be assigned to a carrier service area.`).

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

**Working hygiene (each rule cost real time once; reasoning in `docs/codebase/notes/git-working-hygiene.md`):**
- **One worktree per TASK, not per session.** `git worktree add -b <branch> .claude/worktrees/<task> origin/main`. Reusing the warm worktree for a second task is what put a data script in the main checkout (it rewrote seven committed CSVs on the user's branch) and what lost track of `HEAD`.
- **After any `git rebase`, check `git log --oneline -1` IS your commit before amending.** "Successfully rebased" also prints when your commit was DROPPED as already-upstream — then `HEAD` is someone else's commit, and `--amend` rewrites it.
- **Stage explicit paths, never `git add -A` or a directory**, then verify with `git show --stat HEAD`. `eqasim-data/` is ignored by design; committed reference tables need a deliberate `git add -f` per the `.gitignore` allowlist.
- **The `.git` is shared across worktrees**, so local branches are visible to the user and may already be pushed with a PR open. Check `git ls-remote --heads origin <branch>` and `gh pr list --head <branch> --state all` before assuming otherwise; never `git switch` in a directory you do not own.
- **A worktree has no gitignored data.** Scripts reading raw inputs get explicit `--raw <main-checkout path>` and `--out-dir <scratch>`; copy only the intended file back and diff regenerated siblings to prove the change was additive.
- **Merging a PR is the user's action**, not ours — the permission layer blocks `gh pr merge` deliberately.

Keep commits focused. Commit messages must be in English and explain the change and its purpose: use clear messages (e.g. `Add validation for missing freight carrier capacities`), avoid unclear ones (e.g. `fix`, `update`, `changes`). Full example lists: [CONVENTIONS.md](docs/codebase/CONVENTIONS.md#commit-message-examples).

## Review checklist

Before considering a task complete, check: code compiles; all names, comments, and JavaDoc in English; units explicit; assumptions documented; paths configurable; random seeds controlled; outputs traceable; input files validated; relevant tests added or updated; logs useful; code consistent with MATSim/eqasim style; solution efficient enough for large simulation datasets; result scientifically defensible; documentation sufficient for another researcher to understand the workflow.

## Preferred response behavior for Claude

When modifying code, first inspect the surrounding code and project structure. Do not guess APIs if the relevant code can be inspected. Do not invent missing classes, methods, or dependencies. If information is missing, state the uncertainty clearly. When suggesting changes, explain the reason briefly. Produce complete, consistent code, not isolated fragments, unless a fragment is explicitly requested. When a task affects scientific results, explicitly state whether the change may alter outputs. For performance improvements, explain the expected benefit and any trade off.

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
