# ADR-0147 · 2026-10-16 · The analysis collector stages hash their whole import closure

- **Status:** accepted (maintainer request, 2026-10-16, issue #454)
- **Numbering:** ADR-0147 is the next free id. Checked on 2026-10-16 across all local and
  remote branches: the highest record anywhere is ADR-0146.
- **Issue:** #454. **Applies:** ADR-0136 to two more stages.

## Context

- `braunschweig.analysis.analysis_suite` and `braunschweig.analysis.simwrapper_export` had no
  `validate()` token. synpp hashes only a stage module's own source, and both stages import
  every sub-analysis lazily inside `execute()`. An edit to, say, `run_mid_validation` therefore
  left the cached suite result in place, and the dashboard and validation reports could
  describe old code.
- A stage without a token is not "source-hashing", so the ADR-0136 closure gate
  (`test_every_source_hashing_stage_hashes_its_whole_import_closure`) skipped both stages
  entirely. Only the one-level own-package gate saw them, and it tolerated them through
  `ALLOWED_VIOLATIONS` (twelve modules for the suite after #443, two for the export).
- With a token, the static closure (`scripts/audit_synpp_helper_hash.py`, at the merge of
  `origin/main` 3123cc4f) is 85 modules for the suite (11 direct, including `stuck_agents`
  from #443, plus 74 reached through them) and 32 for the export (2 plus 30).

## Decision

1. Both stages get a `validate()` that hashes every module of their import closure by
   dotted name (`_DEFERRED_HELPER_MODULE_NAMES`), with the direct imports first and the rest of
   the closure as one commented group. Hashing by name keeps the stage import light, which is
   why the suite imports lazily in the first place. A module that cannot be imported fails the
   token instead of being skipped.
2. Their entries leave `ALLOWED_VIOLATIONS`. A new gate,
   `test_the_analysis_collector_stages_hash_their_source`, pins that both stages stay
   source-hashing, so deleting the token cannot silently drop them out of the closure gate.
3. Before hashing, three imports that went through the `braunschweig.popsim.mid` package
   facade now take the name from the submodule that defines it (ADR-0136 decision 3; the
   objects are the same, so behaviour is unchanged):
   - `popsim_validation.controls`: `load_kreis_control_table` from `mid.kreis_controls`;
   - `population_validation.participation_fit`: `PARTICIPATION_W_ZWECK` from
     `mid.participation`;
   - `integerizer_quality.cell_error`: `load_completed_donor` from `mid.donor`.
   This removed 7 modules from the suite's closure (80 -> 73, measured before #443 merged).
4. The remaining closure is hashed as it is. It includes the MiD donor chain, which
   `cell_error` really calls, and the popsim control and attribute modules, which the
   population and popsim validations read.

## Cost

- An edit to any module in the closure re-runs the stage. The suite reads archived run
  outputs, so a re-run takes minutes, not hours. Most of the hashed popsim modules also shape
  `synthesis.output`, which the suite depends on, so an edit to them re-ran the suite already.
- Computing the token costs about 5 s on the first call in a fresh interpreter (importing the
  sub-analyses), 0.03 s afterwards; the export token costs 0.06 s (measured on Windows,
  conda eqasim, 2026-10-16).

## Rejected alternatives

- **Narrow the donor chain too.** It would need a redesign of what `load_completed_donor`
  imports, which is out of scope ("changing what the sub-analyses compute" is excluded by
  #454). The over-approximation only costs extra minute-scale re-runs.
- **Keep the register entries and add one-level tokens.** That is the gap ADR-0136 closed for
  the synthesis stages: a one-level token misses everything the sub-analyses import.
- **Compute the closure at run time inside `validate()`.** Rejected in ADR-0136 for the same
  reasons; the literal list keeps every coverage change visible in the diff.
