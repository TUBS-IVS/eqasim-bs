# ADR-0201 · 2026-11-19 · PopulationSim rounds to whole households with controlled rounding; production switches it on with one pre-registered check missed

- **Status:** accepted (implemented on branch `fix/i646-popsim-fair-rounding` and in fork release `0.10.0+bs.2`). Owner decision of 2026-11-19: the production value is `"controlled"` although the pre-registered gate of ladder arm 3 was not passed; the deviation and its reasons are decision 5.
- **Numbering:** ADR-0201 is the next free id; checked on 2026-11-19 across all local and remote branches and the open pull requests (0200 and 0202 to 0204 are taken by the stacked branches of issues #656 to #659).
- **Issue:** #646 (WP4 of the PopulationSim repair). Fork base: [ADR-0197](ADR-0197-the-populationsim-fork-branches-from-tag-v0-10-0.md); earlier fork options: [ADR-0199](ADR-0199-populationsim-fork-with-fractional-weights-and-proportional-draw.md).

## Context

- With upstream's rounding the simultaneous integerizer of a parent zone draws the households of every cell again, so a row's parent weight is not returned to the cells, and where its problem is infeasible PopulationSim takes a sequential path. Rare household and person groups lose members on the way and the cell totals move away from the balanced weights. Measurements on two batches (solver path, household conservation, rare groups, rounding shift): the arm-2 columns of the run manifest [`popsim-ladder-arm3-2026-11-18`](../runs/popsim-ladder-arm3-2026-11-18.yml).
- The repair is work package WP4 of issue #646: distribute the households a parent zone already holds over its cells instead of drawing them again, and make every case where that is not possible visible.

## Decision

1. **One opt-in fork setting, default upstream.** `INTEGERIZER_ROUNDING` is `round` (upstream's behaviour, the default) or `controlled`. In `controlled` mode the simultaneous integerizer keeps the household count of every cell that upstream's own rounding enforces, keeps the parent weight of every row, and under these two rules lowers the importance-weighted distance between the integer and the float cell totals. Rules, options, record columns and limits are documented in the fork README (section "Controlled rounding") on [`bs/main`](https://github.com/TUBS-IVS/populationsim/tree/bs/main), not here.
2. **The method is a search; the linear program stays for comparison.** `INTEGERIZER_CONTROLLED_METHOD` is `search` (default: a minimum-cost flow as the start, then deterministic pairwise swaps) or `lp` (the same problem as a linear program with a repair step). Why the search is the default: the method replay recorded in the run manifest.
3. **Tracked keys.** `braunschweig.population.popsim.integerizer_rounding`, `.integerizer_controlled_method` and their option keys (meaning: `braunschweig/popsim/stage/config_keys.py`) are written into every batch settings file and enter the batch signature.
4. **No silent fallback.** With `controlled` asked for, the stage fails unless every batch proves from its solver-path record that the controlled path ran; an older fork that ignores the setting fails here. It counts the calls that did not end successfully or did not converge and the households the method could not place under both rules, and warns when any of these is positive (`braunschweig.popsim.stage.solver_path_gate`).
5. **Production value `"controlled"`, as a documented deviation from the gate.** Ladder arm 3 met every pre-registered check except one: design goal G4 on one of the two batches, by one control (verdict and figures: the run manifest). The bound and the verdict stay as recorded; nothing was tuned. The owner switches the rounding on for these reasons:
   - The miss is located. One 1 km parent of that batch carries the whole rounding shift of the control, every defect of the solver-path record and the batch's whole household excess, and the float weights of its cells contradict their controls; in every other parent the rounding returns the float sums of the control (post-hoc entry of the run manifest).
   - Upstream's rounding misses the same check on the same batch by more controls (run manifest), so keeping `round` does not meet the bound either.
   - The control targets of that parent are repaired by issue #657. Its run record `popsim-ladder-tenure-2026-11-18` repeats the check on the repaired targets and lands with that issue's pull request; the owner decided after that record showed the check met on both batches. This ADR states no figure from it.

## Rejected options

- **`lp` as the default method:** on the replayed parents it is slower and, after its repair step, farther from the float totals than the search (run manifest).
- **Keeping `round` in production until the input repairs are merged:** the repairs of issues #657 to #659 were measured with the controlled rounding only; with `round`, the production configuration after their merge would be a combination no ladder arm ran.
- **Changing the bound, or leaving the control out of the check, after the run:** excluded by the pre-registration (no tuning).

## Consequences

- The batch signature includes the rounding keys, so the switch recomputes existing work dirs.
- Where the cell controls of a parent cannot be met together, the rounding keeps the household counts and reports what it could not keep (solver-path record; counted and warned by the stage). Until the repair of issue #657 is on main, one parent of one batch shows this.
- The search returns a local optimum, not a proven one; the solver-path record says so by its status.
- `controlled` needs the OR-Tools integerizer and the simultaneous integerizer (fork README).
- Fork release `0.10.0+bs.2` is pinned in `environments/populationsim`; how a release is cut and checked: [populationsim-fork.md](../codebase/notes/populationsim-fork.md).

## Evidence

- Run manifest [`popsim-ladder-arm3-2026-11-18`](../runs/popsim-ladder-arm3-2026-11-18.yml): pre-registered checks and verdict, post-hoc location of the miss, method replay, development record. Two batches and one draw; the verdict for the full population is the full rerun (#648).
- The fork's test suite at the release commit in three environments: `fork_test_suites.txt` of that manifest.
- The repeat of the missed check on repaired targets: run record `popsim-ladder-tenure-2026-11-18` of issue #657 (not on this branch).
