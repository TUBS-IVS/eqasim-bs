# ADR-0136 · 2026-09-28 · Synpp tokens are checked against a stage's whole import closure

- **Status:** accepted for every source-hashing stage (maintainer request, 2026-09-28). The
  first version of this record covered the enriched stage only and left the others open.
- **Numbering:** ADR-0136 is the next free id. Checked on 2026-09-28 across all local and remote
  branches: ADR-0134 is on the #435 branches and ADR-0135 on the local branch
  `fix/parallel-memory-robustness`.
- **Issue:** none; found in the #434 follow-up and fixed there on the maintainer's instruction.

## Context

- synpp hashes only a stage module's own source. Stages close that gap with a `validate()`
  token over the hand-kept tuples `_HELPER_MODULES` and `_DEFERRED_HELPER_MODULE_NAMES`, and
  `scripts/audit_synpp_helper_hash.py` checks the tuples, gated by
  `tests/test_audit_synpp_helper_hash.py`. The check covered the stage module's own imports
  only; `docs/codebase/notes/synpp-helper-hash-audit.md` stated that boundary as by design.
- The enriched stage's car-ownership draw reads its base table and raumtyp tilt from
  `braunschweig.data.mid.cars_by_status`, which `vehicle_ownership` imports inside a function.
  No token hashed that module, so an edit to it would have been served from a stale cache,
  and the one-level check could not see it.
- At commit `ed1f263a`, with the enriched stage already covered, 20 of the 25 source-hashing
  stages still missed modules that their code imports: 479 stage-module pairs over 154 modules
  (`python scripts/audit_synpp_helper_hash.py . --json <out.json>` at that commit).
- A few imports pulled large parts of that in for a single name: the `braunschweig.popsim.mid`
  package, whose `__init__` re-exports all eight MiD submodules; the enriched and the
  chainsolver stage packages; and the popsim stage package, which the MiD donor stage imported
  for one config key.

## Decision

1. The audit computes, for every source-hashing stage, its first-party import closure. Module
   level and lazy imports are followed through every module reached and stop at the stage's
   declared synpp dependencies, whose code the DAG already covers. What the token does not hash
   is reported as `transitive_uncovered`. The reading is static, so it over-approximates the
   code a run executes: it can ask to hash a module the stage never calls, never the reverse. A
   relative import resolves against the importing module's package, and a module object
   re-exported through another module counts as that module. A helper tuple counts only when
   the stage's `validate()` reads it, and a stage token hashes each module once.
2. Every source-hashing stage is gated on that closure
   (`test_every_source_hashing_stage_hashes_its_whole_import_closure`).
   `DEFERRED_TRANSITIVE_GAPS` holds deliberate exceptions with their reasons and is empty. A
   one-level exception in `EXPECTED_UNCOVERED` holds for the closure as well.
3. An import that reaches code the stage never runs is narrowed before anything is hashed:
   the importing module takes the name from the submodule that defines it, not from a package
   facade or a stage package. This is behaviour-preserving, because each name was a re-export
   of the same object. Four modules now import the MiD loaders from
   `braunschweig.popsim.mid.donor`. The ENTD adapters take `ECONOMIC_STATUS_BY_INCOME_CLASS`
   from the enriched `economic_status` submodule, and the popsim stage takes
   `_apply_housing_tenure` from `housing_tenure`. The candidates stage takes
   its builders from the chainsolver's `candidates` and `srv_candidates`, and the chainsolver's
   `srv_location_types` takes the inverse-CDF draw from the new leaf module `inverse_cdf`
   instead of from `deciders`. The MiD donor stage
   reads `KEY_MID` from `braunschweig.popsim.stage.config_keys` and no longer hashes the popsim
   stage package.
4. Two declarations end the walk, each with its reason written where it lives:
   - A module whose `_SYNPP_TOKEN_EXEMPTION` states that it never shapes a stage result is not
     hashed, and nor is what only it imports: `braunschweig.progress`, `braunschweig.theme`
     and `braunschweig.monitoring.process_tree`. An empty reason is an error.
   - A stage lists in `_TOKEN_CLOSURE_BOUNDARIES` a stage module that its imports reach but
     whose code it never runs. The only one is the trips stage, for the popsim stage and the
     MiD donor stage: their donor adapters import it for `build_trips`, and only the trips stage
     calls `build_trips`, which `test_only_the_trips_stage_builds_trips_through_a_donor_source`
     pins. A boundary must be a synpp stage that the stage does not import itself.
5. Everything else the walk reaches is hashed by dotted name in the stage's
   `_DEFERRED_HELPER_MODULE_NAMES`: the stage's own function-level imports first, then the rest
   of its closure as one commented group. That includes upstream eqasim stages the helpers run
   as libraries, such as `synthesis.population.matched` for `match_donors`.
6. Two shared rules moved into modules of their own, so that stages using them no longer reach
   another stage: `derive_socioprofessional_class` left the IPF attribute stage for
   `braunschweig.population.socioprofessional_class`, and the raumtyp tilt that four MiD
   couplings each carried went to `braunschweig.data.mid.raumtyp_tilt`.

## Rejected alternatives

- **Hash every closure without narrowing first.** Every stage's cache would then depend on code
  it never runs. The popsim stage alone would hash the trip and calibration code behind its
  donor adapters, so every calibration edit would recompute the most expensive stage.
- **Compute the closure inside `validate()` at run time.** Correct by construction and free of
  hand-kept tuples. It would put an import-graph walk into production code and drop the literal
  lists, which make every coverage change a visible diff; the popsim stage's token states that
  convention. The gate gives the same completeness with the lists kept explicit. This stays the
  fallback if keeping the lists costs too much.
- **Allow a boundary at any module.** A boundary claims that the stage never runs the module.
  Restricting it to stage modules keeps that claim rare and checkable.
- **Exempt `braunschweig.resources` and `braunschweig.parallelism`.** They set worker counts
  and BLAS thread pinning, which can change results through batching and floating-point order,
  so they stay hashed.
- **Keep the one-level check.** It is the check that missed `cars_by_status`.
- **Follow calls instead of imports.** More precise, but a static call graph of Python code is
  not reliable, and a missed edge would again mean a stale cache.

## Consequences

- The token of every source-hashing stage changes, so the synpp cache recomputes once. Where the
  newly hashed code has not changed since a cached run, the recompute reproduces that output.
- An edit to any module a stage runs now devalidates that stage. A new import fails the gate
  until the module is hashed, narrowed, exempted or declared a boundary, and the failure message
  names the modules per stage.
- The tuples are long; the popsim stage's token covers 81 modules. A new import in a shared
  helper makes the gate fail for every stage that reaches it.
- `python scripts/audit_synpp_helper_hash.py` prints each stage's remaining gap, the exempt
  modules and the boundaries.
