<!-- PRs ALWAYS target the fork TUBS-IVS/eqasim-bs (base: main), never the eqasim-org/eqasim-bavaria upstream. Use `git pr`. -->

## What & why
<!-- Summary of the change and its purpose. -->

## Linked issue / ADR
<!-- Closes #NN ; docs/decisions/ADR-NNNN-*.md -->

## Behaviour change?
<!-- Is a flag involved? Is the OFF path byte-identical to before? Does this alter
     scientific outputs? State explicitly. -->

## Validation evidence
<!-- Real evidence: test output, a 1% smoke, comparison vs a committed reference,
     recorded as a run manifest (docs/runs/) where significant.
     Convergence is not validation. -->

## Registry / documentation impact (docs/DOCUMENTATION_GOVERNANCE.md)
- [ ] Stage Registry impact assessed (`docs/registry/stages/`; DAG snapshots re-extracted if stages changed)
- [ ] Feature Registry impact assessed (`docs/registry/features/`)
- [ ] Data Registry impact assessed (`docs/registry/data/` + input verifier + README data setup)
- [ ] ADR added/updated for substantive decisions (`docs/decisions/`)
- [ ] Run manifest recorded if a significant run backs this PR (`docs/runs/`)
- [ ] README/setup impact assessed (new inputs, paths, commands)
- [ ] `python -m braunschweig.documentation build` re-run; generated docs committed
- [ ] `python -m braunschweig.documentation check` passes (0 FAIL)

## Reviewer checklist (CLAUDE.md)
- [ ] All code/comments/docs in English; units explicit in names
- [ ] Assumptions documented; no invented reference values
- [ ] Paths configurable; random seeds controlled
- [ ] No silent fallbacks (primary-vs-fallback rate logged + primary path tested)
- [ ] Tests added/updated and deterministic
- [ ] Outputs traceable; scientifically defensible
