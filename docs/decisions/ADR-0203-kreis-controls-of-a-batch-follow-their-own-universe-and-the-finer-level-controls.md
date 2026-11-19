# ADR-0203 · 2026-11-19 · KREIS controls of a batch follow their own universe and the finer-level controls (issue #658)

- **Status:** accepted (implemented on branch `fix/i658-popsim-kreis-consistency`; keys switched on by owner decision 2026-11-19 with a documented deviation from one pre-registered check, see "Evidence").
- **Numbering:** ADR-0203. Highest id on any local or remote branch on 2026-11-19: ADR-0202 (issue #657); ADR-0201 stays reserved for the pending decision record of issue #646.
- **Issue:** #658. Extends [ADR-0062](ADR-0062-popsim-apportion-household-level-kreis-controls-by-household.md) (batch split of the KREIS controls) and applies [ADR-0092](ADR-0092-1km-ownership-shape-controls-raked-to-blended-kreis-anchors.md) (1 km ownership grid raked to the Kreis anchors) at batch scale; Tier-3 controls: [ADR-0015](ADR-0015-tier-3-kreis-level-populationsim-controls.md).

## Context

- `data.census.filtered` runs PopulationSim in batches. A Kreis that spans several batches has its KREIS control targets split over them. Before this decision every person control was split by the batch's share of the Kreis POPULATION and every household control by its household share (ADR-0062).
- A person control has its own age universe (children 0-5 and 6-17 for the education classes, 14+ for the employment families, 15+ for the Tier-3 controls). Split by population share, a family suburb got the Kreis-average number of children, and the KREIS target contradicted the 100 m age targets of the same batch. PopulationSim resolved the contradiction by importance: in the arm-4 baseline the synthetic persons 0-17 of a batch ranged from 0.86 to 1.32 times their 100 m target.
- The KREIS car and bicycle classes carried the Kreis-average shares while the 1 km ownership grid of the same batch carries the local shares (both raked to the same Kreis anchors, ADR-0092); the KREIS employed target likewise carried the Kreis average against the 100 m employed grid.
- The employment families of the KREIS table (`work_by_employment`, `employment_status`) carried a survey employment level, a different level from the Zensus count of the employed grid and of `employed`.
- Measured contradictions on the production tables: run record [`popsim-input-consistency-2026-11-18`](../runs/popsim-input-consistency-2026-11-18.yml).

## Decision

1. **Universe apportionment** (`braunschweig.population.popsim.kreis_batch_apportionment: "universe"`): every KREIS person control with an age universe is split over the batches by the batch's share of the persons of THAT universe (single-year age columns of the cells; `controls_builder.add_universe_weight_columns`, `apportion_weight_columns`; `mid.batch_folders.universe_apportion_weights`). Controls without an age universe keep the population share, household controls the household share (ADR-0062). A Kreis part without a person of a universe gets 0 and is counted; a Kreis whose region total of a universe is 0 loses that target, which is logged with the dropped mass.
2. **KREIS follows the grids** (`kreis_controls_follow_grids: true`): the KREIS car classes, bicycle classes and `employed` of a batch are set to the batch's sum of their finer-level integer controls (`OWN_CARS_*` / `OWN_BIKES_*` at 1 km, the ten `EMPLOYED_*` at 100 m; `control_spec.kreis_controls_following_grids`, `folders.set_kreis_controls_from_fine_sums`). Each fine cell belongs to the one Kreis the resolved crosswalk gives it, the assignment PopulationSim balances against. Region-wide the grids keep the Kreis anchors of ADR-0092.
3. **One employment level, D-emp** (`employment_level_source: "zensus"`; owner decision 2026-11-18): the Zensus count is the level of every employment control. Per Kreis part the employed classes of `work_by_employment` and `employment_status` are scaled to bridge × the part's employed-grid sum and the other classes to the rest, proportions inside each group kept (`braunschweig.popsim.employment_level`). The survey keeps the conditional shares (work trip given employed, status mix inside the employed).
4. **Guard and report:** the stage writes `<work_dir>/popsim_input_consistency.csv` for every batch (`braunschweig.popsim.input_consistency.summarize`) and raises before any PopulationSim process starts when a grid-following pair differs by more than 1e-6 relative, or an age-universe pair by more than `kreis_fine_tolerance` (0.05) in a Kreis part with at least `kreis_fine_min_persons` (1,000) persons (`braunschweig.popsim.input_consistency_guard`). The counts of all three steps go to the `kreis_input_consistency` entry of `census_filtered_report.json`. The diagnostics write the same measurement as `input_consistency.csv`.
5. **Keys:** the five keys of `braunschweig/popsim/stage/config_keys.py` (meaning and valid values there); code defaults are the legacy values, which reproduce the control tables value for value (golden tables `tests/data/popsim_input_consistency/`). They enter the batch config signature only when a table-shaping key is off its legacy value, so legacy work directories keep their batches.

## Assumptions

- ASSUMPTION (D-emp bridge): the share of persons employed by `P_TAET` but outside the employed classes of the survey families is as in the household-weighted MiD seed; the bridge is computed over the employed grid's universe (16+), and employed persons aged 14-15 of the families stay inside the scaled employed group, so the 16+ employed come out lower than bridge × Zensus by about their weighted share (logged).
- ASSUMPTION: the universe of `employed`, `schulabschluss_*` and `beruflabschluss_*` is persons aged 15 and over (`control_spec.TIER3_PERSON_MIN_AGE`); neither ADR-0015 nor the loader of the cleancensus Kreis tables states it.
- ASSUMPTION-grade guard bounds: 0.05 above the rounding residual (the 100 m age bands are integers rounded inside each 1 km square, the KREIS share comes from float age counts) and below the defect it guards against, both measured in `popsim-input-consistency-2026-11-18`; 1,000 persons as the smallest judged part.
- Known limit: an attribute with a KREIS control only (economic status, trip class, participation, PT ticket, degrees) still gets the Kreis-average category shares in every batch part; the split is now consistent with the batch's persons and households, but there is no spatial variation inside a Kreis.

## Rejected options

- **Dropping the KREIS car and bicycle controls:** removes the contradiction but also the Kreis anchor ADR-0092 keeps; the batch part as the sum of the 1 km grid keeps both.
- **A survey employment level for all employment controls:** would leave the employed grid and `employed` (Zensus) contradicting the survey families; the Zensus count is the census level of the region.
- **A 2 % guard from 200 persons:** fails on the rounding residual alone.

## Consequences

- The KREIS control tables of every batch change (36 of 52 KREIS control columns in each of the four evidence batches); the 100 m, 1 km, STAAT and WELT tables do not. Every batch runs again and every stage downstream of PopulationSim recomputes.
- A new KREIS control on a variable that has a finer-level control joins `control_spec.kreis_controls_following_grids`; a new person control declares its age universe (maintainer note `docs/codebase/notes/popsim-input-consistency.md`).

## Evidence

- Run record [`popsim-ladder-inputs-a-2026-11-18`](../runs/popsim-ladder-inputs-a-2026-11-18.yml): ladder arms 5 (universe apportionment and KREIS follows the grids) and 6 (plus the employment level) on four batches against the arm-4 baseline, with pre-registered checks, results, the post hoc location of the one miss and the owner decision. Arm 6 meets every check against arm 5; arm 5 meets every check against arm 4 except household conservation at 100 m in batch_000, whose whole deviation lies in one 1 km square whose 100 m targets ask for more households than persons (the square that already carried the G4 miss of ladder arm 3). The owner switched the keys on with this deviation documented. Four batches and one draw; the full population is the full rerun (#648).
