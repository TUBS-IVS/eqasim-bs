<!-- PRs ALWAYS target the fork TUBS-IVS/eqasim-bs (base: main), never the eqasim-org/eqasim-bavaria upstream. Use `git pr`. -->

## What & why
<!-- Summary of the change and its purpose. -->

## Linked issue / ADR
<!-- Closes #NN ; docs/DECISIONS.md ADR-NNNN -->

## Behaviour change?
<!-- Is a flag involved? Is the OFF path byte-identical to before? Does this alter
     scientific outputs? State explicitly. -->

## Validation evidence
<!-- Real evidence: test output, a 1% smoke, comparison vs a committed reference.
     Convergence is not validation. -->

## Reviewer checklist (CLAUDE.md)
- [ ] All code/comments/docs in English; units explicit in names
- [ ] Assumptions documented; no invented reference values
- [ ] Paths configurable; random seeds controlled
- [ ] No silent fallbacks (primary-vs-fallback rate logged + primary path tested)
- [ ] Tests added/updated and deterministic
- [ ] Outputs traceable; scientifically defensible
