# ADR-0136 · 2026-09-28 · Synpp tokens are checked against a stage's whole import closure

- **Status:** accepted for the enriched stage (maintainer request, 2026-09-28); adoption for
  the other source-hashing stages is open.
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
- Following the imports through every module reached, 21 of the 25 source-hashing stages miss
  modules their code imports: 533 stage-module pairs over 156 modules before this record, and
  the largest stages reach about 100 first-party modules.

## Decision

1. The audit computes, for every source-hashing stage, its first-party import closure. Module
   level and lazy imports are followed through every module reached and stop at the stage's
   declared synpp dependencies, whose code the DAG already covers. What the token does not hash
   is reported as `transitive_uncovered`. The reading is static, so it over-approximates the
   code a run executes: it can ask to hash a module the stage never calls, never the reverse.
2. Stages in `FULL_CLOSURE_STAGES` are gated on that closure. `DEFERRED_TRANSITIVE_GAPS` holds
   deliberate exceptions with their reasons, and the gate fails when an entry closes, so the
   register only shrinks, like `EXPECTED_UNCOVERED`.
3. The enriched stage is the first such stage. Eleven modules joined its token: `cars_by_status`,
   the Haushaltstyp classifier, `rake_2d` (the rake to the MiD H7 control, which lives in the IPF
   package), the census income table, the MiD zone table, the eqasim enrichment it extends with
   that module's HTS readers, and the attribute mappers with their missing-value handling.
4. The IPF attribute stage and the three modules reached only through it stay outside the token.
   The maintainer keeps the IPF path as a courtesy and postpones its coverage. `popsim.attributes`
   imports `derive_socioprofessional_class` from `braunschweig.ipf.attributed`. The fix there is
   to move that function out of the IPF stage module, not to hash the IPF stage into this token.

## Rejected alternatives

- **Hash every closure in every stage now.** 523 pairs remain. Every stage's cache would depend
  on large parts of the code base, so an edit in a shared module would recompute most of the
  pipeline. Where a stage reaches a large module for one function, narrowing the import is the
  better fix, and that is production refactoring stage by stage.
- **Compute the closure inside `validate()` at run time.** Correct by construction and free of
  hand-kept tuples, but it replaces the token mechanism of every source-hashing stage at once.
  Kept for the decision on the other stages.
- **Keep the one-level check.** It is the check that missed `cars_by_status`.
- **Follow calls instead of imports.** More precise, but a static call graph of Python code is
  not reliable, and a missed edge would again mean a stale cache.

## Consequences

- The enriched stage's token changes, so its synpp cache and everything downstream recompute
  once. From now on an edit to any of the eleven modules devalidates the stage, as it should.
- `python scripts/audit_synpp_helper_hash.py` prints each stage's transitive gap. For the other
  20 stages with a gap, the one-level gate stays the enforced bound, and their tokens can still
  serve output built by an older version of a helper's helper.
- Extending coverage means adding a stage to `FULL_CLOSURE_STAGES` and closing or registering
  its gaps.
