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

Consistent tables are a precondition of a fit, not a validation: the realised population is judged only
against committed observed references, never against these tables.
