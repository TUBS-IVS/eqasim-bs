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
- `--mid-households` loads the columns `run.MID_COLUMNS` (separator `--mid-separator`) and joins
  them to the seed households on the household id. The join rate is logged; below
  `run.MID_JOIN_WARN_RATE` it warns, below `run.MID_JOIN_FAIL_RATE` it raises (id mismatch).
- `--misfit` needs a kept checkpoint store `output/pipeline.h5` in every batch; a batch without one
  raises. Without it the run needs only the CSV inputs and the log.
- `--threshold NAME=VALUE` (repeatable) overrides a `Thresholds` field; an unknown name is an
  error. `--max-unmatched-zone-share` (default in the argparse setup of `run.main`) is the share of zones without a target
  row above which `control_fit` and `misfit_split` raise (zone-id mismatch).
- Warnings never change the exit code; exceptions do. `provenance.json` records the work
  directory, batches, seed groups, thresholds, the MiD file hash, a UTC timestamp and the
  repository commit (`git rev-parse HEAD`, only when git's top level is the package's own repository, `-dirty`
  appended with a warning when the tree has uncommitted changes; else the environment variable `EQASIM_BS_COMMIT`, else
  `unknown` with a warning).

## Seed groups

Batches with the same `data/seed_households.csv` and `data/seed_persons.csv` (sha256 of both)
form one seed group. Reuse, weight
shares, attribute shares and coverage are computed once per group from the group's summed usage,
so the shared seed is not counted once per batch; they carry a `seed_group` column (a sha256 prefix of length `run._SEED_GROUP_CHARS`). Batches of one group must have identical `configs/controls.csv`, household-total control,
weight column, household id column and geographies (`ValueError` otherwise). Ratios are always recomputed from sums.

## Tables

| File | Measures | Read from |
|---|---|---|
| `reuse.csv` | copies per seed household: scope `region` (per group), `batch`, `kreis` | expanded ids, seed |
| `weight_class_shares.csv` | seed weight share vs synthetic share per weight class | seed weights, expanded ids |
| `attribute_shares.csv` | the same per seed attribute and per MiD column | seed, optional MiD file |
| `control_coverage.csv` | seed records that no category of a control group counts | seed, controls |
| `resolved_record_shares.csv` | seed and synthetic shares of the records the stage resolution changed, selected by raw code (`raw_codes`: `3,9`, `not 1,2`, `95`) | seed `*_raw` columns, expanded ids |
| `control_universe.csv` | household group targets vs the household total | control totals |
| `control_fit.csv` | WAPE and bias per control; scopes `batch`, `region`, `region_geography` | totals, expanded ids |
| `solver_paths.csv` | which solver path each parent zone took, per level | `output/populationsim.log` |
| `misfit_split.csv`, `rounding_shift.csv` | balancing vs rounding part of the misfit (`--misfit`) | checkpoint store |

`resolved_record_shares.csv` is descriptive (no thresholds) and keeps the before/after comparison on the same records once the coverage table shows none uncovered; a seed without the raw columns gets no row.

The group declaration (which control belongs to which group, universe, exhaustive) lives in
`braunschweig/popsim/control_groups.py` and nowhere else; a control that falls in no group or in
two is reported as `unmatched_control`.

## Solver outcomes (PopulationSim 0.10.0)

How a failed simultaneous integerizing attempt ends, and which counter each outcome increments,
is stated once in the `solver_paths` module docstring; a failed retry and a "no infeasible sub
zones" parent are also counted in `sequential_fallback` when the fallback line follows.
`non_simultaneous` counts the
parents whose final weights did not come from the simultaneous solution (sequential, single
feasible sub zone, all-smart-rounded). The `sequential_fallback_rate` warning uses
`non_simultaneous / n_parents` summed over batches per level, because `sequential_fallback` alone
misses the last two kinds. `solver_paths.csv` keeps every counter. A batch whose log has no
first attempt at any level raises `ValueError` naming it (an unparsed log would read as "0
fallbacks"); the number of parsed batches is logged.

## Thresholds (`Thresholds`, all warnings)

| Field | A breach usually means |
|---|---|
| `weight_class_gap_pp` | the synthetic copies over- or under-represent a weight class of the seed |
| `coverage_ratio_low` / `_high` | records outside every category are over- or under-used (control group not exhaustive for the seed) |
| `universe_ratio_tolerance` | group targets do not add up to the household total (inconsistent control tables) |
| `coverage_multi_hit_max` | a record counted by two categories of an exhaustive control group (overlapping controls; value = records, threshold 0) |
| `sequential_fallback_rate` | the simultaneous integerizer often failed at a level |
| `rounding_shift_bound`, `rounding_min_target` | integerizing moves a control by more than the bound (only controls with at least `rounding_min_target` in target sum are judged) |

Warning subjects: `universe_ratio` is `<group>/<geography>`, rounding shift is `<batch>/<control>`;
with several seed groups the weight-class and coverage subjects get a `<seed_group>/` prefix.
An `unmatched_control` warning (one per control name) has NaN value and threshold by design.
`control_fit` `region_geography` rows leave out controls whose target sum is 0 (count logged).
For a control coarser than a batch (e.g. KREIS) the `region` and `region_geography` rows sum
per-batch absolute errors against per-batch apportioned targets: they are an upper bound of the
Kreis-level WAPE, because errors of opposite sign in different batches of one Kreis do not cancel.
`control_fit` counts the targets of lowest-level cells that cannot hold a household (household total
0, other targets non-zero) fully as error; its WAPE is therefore an upper bound of the reachable fit.
The full statement, including how these cells enter the parent levels of PopulationSim's own control
tables, is in the `control_fit` docstring (`fit.py`).

Defaults are in the dataclass; they are screening values, not scientific targets.
