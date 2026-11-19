# PopulationSim input consistency: rules for maintainers

The control tables of a batch must not contradict each other. A KREIS target that disagrees with the
finer-level targets (1 km, 100 m) of the same batch asks PopulationSim for two different populations at
once. This note is the one record of the mechanism that keeps them consistent (issue #658). The decision, its
assumptions, the rejected options and the evidence (including the one missed pre-registered check) are in
[ADR-0203](../../decisions/ADR-0203-kreis-controls-of-a-batch-follow-their-own-universe-and-the-finer-level-controls.md);
the stage record is `docs/registry/stages/data.census.filtered.yml`. No figures are copied here.

## Rules

1. **A new KREIS control on a variable that has a finer-level control** (for example a new ownership class
   with a 1 km control) joins the pairs of `control_spec.kreis_grid_pairs` (read by `control_spec.kreis_controls_following_grids`), so that the batch's KREIS
   target is the sum of its finer-level controls. Otherwise the two layers contradict each other.
2. **A new person control declares its age universe**: `min_age` / `max_age` in its registry entry, or, for
   a Tier-3 control, the universe `control_spec.TIER3_PERSON_MIN_AGE`. The batch split of the KREIS
   target uses the persons of that universe (`controls_builder.add_universe_weight_columns`); a control
   without a declared universe is split by all persons and can contradict the age targets.
3. **A new pair of tables that must agree** is added to `input_consistency.KREIS_FINE_PAIRS`. Whether a
   pair is gated (grid-following pair or age-universe pair) is decided in
   `input_consistency_guard.guard_violations`; keep both in step.
4. **The keys** (`braunschweig.population.popsim.kreis_batch_apportionment`, `kreis_controls_follow_grids`,
   `employment_level_source`, `kreis_fine_tolerance`, `kreis_fine_min_persons`) are read and validated in
   `braunschweig/popsim/stage/config_keys.py`; production values are in `configs/base_bs.yml`. Code defaults
   are the legacy behaviour. Their bounds are ASSUMPTIONS (ADR-0203).

## Where the measurement appears

- **Stage table:** `<work_dir>/popsim_input_consistency.csv`, written for every batch by
  `input_consistency_guard.ConsistencyRecorder`, before any PopulationSim process starts. The guard raises
  when a gated pair is outside its bound.
- **Report file:** the entry `kreis_input_consistency` of `census_filtered_report.json` (stage
  `data.census.filtered`).
- **Diagnostics:** the table `input_consistency` of `braunschweig/analysis/popsim_batch_diagnostics`
  (reader `run._input_consistency`, measurement `input_consistency.summarize`), readable on any existing
  batch folder, also those made before the keys.

## Household universes (issue #659, [ADR-0204](../../decisions/ADR-0204-household-targets-use-the-universes-and-definitions-the-seed-can-match.md))

The household-level controls must count the same households the seed holds. Two repairs, both switched by keys
read in `braunschweig/popsim/stage/config_keys.py` (the owner of their meaning; production values in
`configs/base_bs.yml`):

- **Household-less 100 m cells** keep their persons out of reach of PopulationSim; the persons move to the household
  cells of their square: `braunschweig/popsim/household_less_cells.py`.
- **Family-type controls** count a child of any age, the seed sees only age: the under-18 definition of
  `braunschweig/popsim/minor_child_targets.py` and the seed column `hh_minor_class`
  (`braunschweig/popsim/seed.py::derive_hh_minor_class`) replace them in mode `minor_child_1km`.

Rule: **a new household-level control must use a definition the seed can observe** (an age, a count, a code of
the seed), otherwise target and seed contradict each other as before. Data limits of the two tables are in
`docs/registry/data/zensus2022_grid_cells.yml`.

Where the measurement appears: the report sections `household_less_cells` and `family_controls` of
`census_filtered_report.json`, and the diagnostics table `household_composition.csv`
(`braunschweig/analysis/popsim_batch_diagnostics/composition.py`). The pre-registered arms are in the run record
`docs/runs/popsim-ladder-inputs-b-2026-11-19.yml` (results pending).

Consistent tables are a precondition of a fit, not a validation: the realised population is judged only
against committed observed references, never against these tables.
