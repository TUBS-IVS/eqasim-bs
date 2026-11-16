# PopulationSim batch diagnostics: what each table measures and how to run it

`braunschweig/analysis/popsim_batch_diagnostics/` reads a PopulationSim work directory (one folder
`batch_*` per batch) and writes tables that show where the synthetic population departs from the
seed survey and from the control totals. It only reads; it never changes a batch. It holds no
reference values: every number is derived from the batch folders. A run that finishes says nothing
about validity, and a table row is a measurement, not a target.

## Run

```bash
python -m braunschweig.analysis.popsim_batch_diagnostics.run --work-dir <dir with batch_*> \
    --out-dir <new directory> [--batches batch_000 batch_032] \
    [--mid-households <MiD2023_Haushalte.csv>] [--misfit] [--chunk-rows N]
```

- `--out-dir` must not exist or be empty (`FileExistsError`); nothing is overwritten.
- `--mid-households` loads `H_ID, BASISAUF, BLAND, MODE, TEILSTP` (separator `--mid-separator`,
  default `;`) and joins them to the seed households on the household id. The join rate is
  logged; below 99 % it warns, below 50 % it raises (id mismatch).
- `--misfit` needs a kept checkpoint store `output/pipeline.h5` in every batch; a batch without one
  raises. Without it the run needs only the CSV inputs and the log.
- Warnings never change the exit code; exceptions do. `provenance.json` records the work
  directory, batches, seed groups, thresholds, the MiD file hash, a UTC timestamp and the
  repository commit (`git rev-parse HEAD`, else the environment variable `EQASIM_BS_COMMIT`, else
  `unknown` with a warning).

## Seed groups

Batches with the same `data/seed_households.csv` (sha256) form one seed group. Reuse, weight
shares, attribute shares and coverage are computed once per group from the group's summed usage,
so the shared seed is not counted once per batch; they carry a `seed_group` column (first 12 hex
characters). Batches of one group must have identical `configs/controls.csv` (`ValueError`
otherwise). Ratios are always recomputed from sums.

## Tables

| File | Measures | Read from |
|---|---|---|
| `reuse.csv` | copies per seed household: scope `region` (per group), `batch`, `kreis` | expanded ids, seed |
| `weight_class_shares.csv` | seed weight share vs synthetic share per weight class | seed weights, expanded ids |
| `attribute_shares.csv` | the same per seed attribute and per MiD column | seed, optional MiD file |
| `control_coverage.csv` | seed records that no category of a control group counts | seed, controls |
| `control_universe.csv` | household group targets vs the household total | control totals |
| `control_fit.csv` | WAPE and bias per control; scopes `batch`, `region`, `region_geography` | totals, expanded ids |
| `solver_paths.csv` | which solver path each parent zone took, per level | `output/populationsim.log` |
| `misfit_split.csv`, `rounding_shift.csv` | balancing vs rounding part of the misfit (`--misfit`) | checkpoint store |

The group declaration (which control belongs to which group, universe, exhaustive) lives in
`braunschweig/popsim/control_groups.py` and nowhere else; a control that falls in no group or in
two is reported as `unmatched_control`.

## Solver outcomes (PopulationSim 0.10.0)

A failed simultaneous integerizing attempt ends in one of five ways: a successful retry, a fall
back to sequential integerizing, "found no infeasible sub zones" (single feasible sub zone), all
sub zones infeasible (smart rounding), or a retry that fails again. `non_simultaneous` counts the
parents whose final weights did not come from the simultaneous solution (sequential, single
feasible sub zone, all-smart-rounded). The `sequential_fallback_rate` warning uses
`non_simultaneous / n_parents` summed over batches per level, because `sequential_fallback` alone
misses the last two kinds. `solver_paths.csv` keeps every counter.

## Thresholds (`Thresholds`, all warnings)

| Field | A breach usually means |
|---|---|
| `weight_class_gap_pp` | the synthetic copies over- or under-represent a weight class of the seed |
| `coverage_ratio_low` / `_high` | records outside every category are over- or under-used (control group not exhaustive for the seed) |
| `universe_ratio_tolerance` | group targets do not add up to the household total (inconsistent control tables) |
| `sequential_fallback_rate` | the simultaneous integerizer often failed at a level |
| `rounding_shift_bound`, `rounding_min_target` | integerizing moves a control by more than the bound (only controls with at least `rounding_min_target` in target sum are judged) |

Defaults are in the dataclass; they are screening values, not scientific targets.
