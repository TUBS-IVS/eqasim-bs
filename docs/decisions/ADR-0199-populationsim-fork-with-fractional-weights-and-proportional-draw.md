# ADR-0199 · 2026-11-17 · The PopulationSim fork carries fractional seed weights and a proportional integerizer draw as opt-in settings

- **Status:** accepted (owner, 2026-11-17: own fork instead of runtime patches; decision D1 on the key type, taken through the pre-registered rule of the arm-1 manifest). The production values of the two new options are NOT decided here: the arm-1 manifest owns that outcome.
- **Numbering:** ADR-0199 is the next free id; checked on 2026-11-17 across all local and remote branches and the open pull requests (0197 is on main, 0198 is taken by the parallel WP3 branch).
- **Issue:** #645 (WP2, fork core). Fork base: [ADR-0197](ADR-0197-the-populationsim-fork-branches-from-tag-v0-10-0.md).

## Context

- PopulationSim 0.10.0 casts the grouped survey weights to int64, and most MiD groups start at 0, so the cast zeroes the weight of many donors before the solver sees them. Both integerizer LPs then choose "largest residual first", which favours the heaviest donors again. Measurements: run manifests [`popsim-batch-diagnostics-prod100-20261031-2026-11-16`](../runs/popsim-batch-diagnostics-prod100-20261031-2026-11-16.yml) and [`popsim-ladder-baseline-2026-11-16`](../runs/popsim-ladder-baseline-2026-11-16.yml).
- The repair is work package WP2 of issue #645. The popsim stage of eqasim-bs must be able to switch it on per run and must know which solver path each batch took.

## Decision

1. **Own fork, no runtime patches.** The changes live in the group's fork (`bs/main`, base per ADR-0197), not in a patch layer applied to an installed PopulationSim (owner decision). Releases are tags `v0.10.0+bs.N` with the same package version; the pipeline pins the version it expects.
2. **Fractional weights and the draw are opt-in fork settings; with them off the fork behaves like v0.10.0.** Proof: a differential test against the upstream tag in the fork, and run R1 (neutral) of the arm-1 manifest. Settings, defaults and the path-record contract are documented in the [fork README](https://github.com/TUBS-IVS/populationsim/tree/bs/main) and in `populationsim/integerizing/path_record.py` there, not here.
3. **One integerizer seed per batch**, derived from the pipeline `random_seed` and the batch folder name (`braunschweig.popsim.folders.batch_integerizer_seed`). The batch seed files are byte-identical and the integerizer trace labels repeat in every batch, so one shared seed would repeat the same noise in all 60 batches. A collision of two batch seeds fails the run and names the folders.
4. **Draw method (fork):**
   - The call kind enters the noise hash: seed-level and KREIS-level calls share a trace label.
   - Upstream's `1e-6 * row` tie-break is omitted when the draw is on: it is a row-position bias.
   - The rounding after the LP follows the draw keys; otherwise `smart_round` undoes the draw.
   - Two key types are implemented: Pareto (logit plus logistic noise; the inclusion probability stays close to the residual in the single integerizer) and Gumbel (under-selects large residuals). The key type is chosen by the pre-registered rule of the arm-1 manifest [`popsim-ladder-arm1-2026-11-17`](../runs/popsim-ladder-arm1-2026-11-17.yml) (owner decision D1).
   - In the simultaneous integerizer, upstream's parent term `ln(sum of residuals over sub-zones)` stays.
5. **Known limitation:** when the simultaneous LP fails, sub-zones are rounded per zone (the keys still decide inside each zone's single integerizer).
6. **Solver-path record and stage gate.** The stage switches the fork's path record on and reads it after the batches (`braunschweig.popsim.stage.solver_path_gate`). The rate of parent zones not integerized simultaneously is logged per geography through the shared fallback helper. `solver_fallback_error_rate` (null = warn only) is an ASSUMPTION-grade threshold, to be set from the first full run.
7. **Tracked environment and version check.** The stage runs PopulationSim from the tracked uv project `environments/populationsim` and checks the installed version against the required key `populationsim_version` (`braunschweig/popsim/tool_version.py`). Details: [populationsim-fork.md](../codebase/notes/populationsim-fork.md).

## Rejected options (design section 4, issue #645)

- **Seed weights scaled by 1000** to keep the int cast useful: a unit change in every consumer of weights and controls, and it leaves the heaviest-first objective untouched.
- **Random donor pools per batch:** changes which donors exist per cell instead of fixing how they are weighted and rounded; sampling noise would hide the defect.
- **A copy cap per donor:** a hard cut that treats the symptom after the fact and biases the cell totals.
- **A separate re-draw stage** after PopulationSim: a second synthesis step that no longer honours the controls the solver fitted.
- **A runtime patch layer** over the installed package: ties the result to the installed upstream source and cannot be tested against a tag.

## Relation to ADR-0056

ADR-0056 keeps integer sub-balance seeds (`SUB_BALANCE_WITH_FLOAT_SEED_WEIGHTS` false) for run time. The new `keep_fractional_sample_weights` is a different switch: it keeps fractional grouped weights for the integerizer and is NOT that sub-balancing regime, which stays false.

## Consequences

- **One-time recompute.** The batch signature of the stage now includes the tool version and the options, so the first run after the merge purges and recomputes the existing work dirs.
- The production base config (`configs/base_bs.yml`) keeps the draw and the fractional weights OFF until the arm-1 manifest decides; the manifest owns the outcome.
- `populationsim_version` is a new required key of the popsim stage config.
- The tag `v0.10.0+bs.1` is cut only after arm 1 passes (procedure in the note).

## Evidence

- Run manifest `popsim-ladder-arm1-2026-11-17` (pre-registration; results are appended there).
- Run manifests `popsim-batch-diagnostics-prod100-20261031-2026-11-16` and `popsim-ladder-baseline-2026-11-16`.
