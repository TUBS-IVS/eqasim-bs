# ADR-0109 · 2026-09-07 · Participation controls are conditional on their universe
- **Status:** active
- **Numbering:** ADR-0109 is the next free id after ADR-0108. Allocated 2026-09-07 and
  RE-VERIFIED on 2026-09-07 before the record was written: `docs/decisions/` was listed on every
  remote branch of `TUBS-IVS/eqasim-bs` (17 heads, `git ls-remote --heads origin` piped through
  `git ls-tree -r origin/<branch> -- docs/decisions/`), on every local branch and worktree of this
  checkout, and the working tree was grepped for `ADR-0109`; the highest id found anywhere is
  ADR-0108, and the only `ADR-0109` mentions are the forward references the earlier commits of
  this branch already placed in code comments and in `configs/base_bs.yml`. Ids are append-only,
  so a colliding draft on a sibling branch is renumbered rather than this one.
- **Context:** The popsim_mid workflow steers trip participation with four per-Kreis PopulationSim
  controls (`work_participation`, `education_participation`, `leisure_participation`,
  `escort_participation`, issues #224 / #227). Each targets a share of **all** persons. Measured on
  the 100 % arm-3 run (run manifest `plan-structure-fix-arm3-100pct-2026-09-07`, analysis stage
  `braunschweig.analysis.synthesis.plan_structure_vs_srv`, artifact `comparison.csv`, against the
  committed `eqasim-data/data/braunschweig/srv/srv2023_plan_structure_reference.csv` **as it stood
  at commit `1a841550`**, i.e. before the realignment this record decides), the all-persons level
  is met and the persons who make the trips are the wrong ones:

  | segment (SrV plan-structure reference, universe `at_home_zero`) | model, arm 3 | SrV | delta |
  |---|---|---|---|
  | work participation, all persons | 0.3176 | 0.3316 | -1.4 pp |
  | work participation, employed | 0.5838 | 0.6755 | -9.2 pp |
  | work participation, seniors 65+ not employed | 0.0782 | 0.0175 | +6.1 pp |
  | education participation, all persons | 0.1799 | 0.1761 | +0.4 pp |
  | education participation, 6-17 not employed | 0.8427 | 0.8997 | -5.7 pp |
  | education participation, 10-17 | 0.7971 | 0.8822 | -8.5 pp |
  | education participation, 0-5 | 0.7344 | 0.6558 | +7.9 pp |

  A control that pins only the marginal cannot distinguish these cases: the same all-persons work
  share is reachable by giving work trips to employed persons or to pensioners, and the model
  reaches it the second way. Four mechanisms, all located in the code before this record was
  written:

  1. **The control cannot see who makes the trip.** Workplaces are assigned to whoever carries a
     work activity in the plan (`synthesis/population/spatial/primary/candidates.py`,
     `has_work_trip`), never to the `employed` attribute. The residual GREW when package 1
     (ADR-0106) recovered diaries for plan sources that had none: workers not flagged employed went
     from 37,705 of 304,900 (12.37 %) to **41,680 of 317,848 (13.11 %)** (arm-3 manifest, artifact
     `work_participation_by_kreis.csv`), because recovering a diary recovers work activities for
     employed and non-employed persons alike while the control is blind to the difference.
  2. **The participation seed disagreed with the realised plan.**
     `braunschweig/popsim/mid/participation.py::compute_has_purpose_trip` /
     `derive_participation_seed` filtered on `W_ZWECK` only, so it counted rbW work legs that
     `trips.build_trip_table` drops (ADR-0107); it carried none of `derive_trip_class_seed`'s
     corrections, so a kept-immobile MiD 803 source received an age-band-IMPUTED participation flag
     while its plan is empty by construction (ADR-0106); and its education code set was static, so
     with `escort_passive_education` ON the plan carries education for `W_ZWECK 13` while the seed
     did not. All three are the 803/804 defect class: the target is met in the seed and missed in
     the realisation.
  3. **The employed axis and the work-trip axis came from two different MiD respondents.** For a
     remapped plan source, `employed` comes from the attribute person's own `P_TAET` and the trip
     from `(source_H_ID, source_P_ID)`; `weekend_plan_match.match_person` relaxes its key ladder
     from the end and drops `employed` second-from-last, so a non-employed person could inherit an
     employed donor's work diary.
  4. **The two sides did not mean the same thing by "employed".** MiD `attributes.EMPLOYED_TAET`
     includes apprentices (`P_TAET 8`) and helping family members; the committed SrV plan-structure
     reference used `EMPLOYED_V_ERW = (9, 10, 11)`, which excludes apprentices (`V_ERW 8`). A
     16-year-old apprentice was `employed` on the model side and
     `school_age_6_17_not_employed` on the SrV side of the very segments compared above.

  Downstream, ADR-0104 check 1 (employed persons without a work trip, +8.06 pp against SrV on the
  ADR-0104 denominator; same manifest) cannot pass on any commute-day parameter, because the
  residual is the participation control, not the day-state model.
- **Decision (taken by the user on 2026-09-07, one decision per question, in every case the
  recommended option of the design; the mechanics below implement them):**
  - **One universe rule for every popsim participation control.** A control targets the plan **as
    synthesised**, on the SrV universe of persons who were **at home or mobile** on the reporting
    day (`at_home_only`: `E_ANZ_WEGE >= 0`). The persons who were away from home over the whole
    reporting day (`E_ANZ_WEGE == -7`, 954 of 18,223 = 5.24 % of the delivery) are outside every
    target denominator, because a synthetic population has no such state: every synthetic person
    exists and starts the day at home, so an all-persons target asks the model to reproduce a rate
    diluted by a state it cannot represent. This is the basis the `trip_class` target already uses
    and the user's 2026-09-05 decision; it also fixes the universe question of #370 in advance, so
    that a later absence model needs no target re-derivation. The predicate is reused from
    `braunschweig/calibration/srv_plan_structure.py` (`at_home_only`), never re-coded. Post-control
    DAY STATES (Phase B's far-commuter `home` / `absent`, ADR-0104) stay deliberate, documented
    deviations measured in the full universe and are never folded into a target.
  - **`work_by_employment` replaces `work_participation`** (config keys
    `braunschweig.population.popsim.work_by_employment_kreis_control` on,
    `...work_participation_kreis_control` off). Four MECE cells --
    `employed_work` / `employed_nowork` / `nonemployed_work` / `nonemployed_nowork` -- over persons
    **14+**, the universe of the existing `employment_status` control and of SrV `V_ERW`. `work` is
    the REALISED plan source having a directly recorded (`W_RBW == 0`) leg with `W_ZWECK in {1, 2}`,
    never an imputed flag, so a kept-immobile 803 source is `nowork` by construction.
  - **Q1 -- the target is `margin x conditional`, not four shares from SrV.** The employed margin
    per Kreis comes from the existing blended `target2026_employment_status_by_kreis.csv`; the two
    conditional rates `P(work | employed)` and `P(work | not employed)` come from the new committed
    aggregate `srv2023_work_by_employment_by_kreis.csv`. The two hard controls then agree on the
    employed margin **by construction** (data record `target2026_work_by_employment_by_kreis`).
  - **Q2 -- three 2-cell education controls on AGE-RANGE universes**, not one all-persons control:
    `education_0_5`, `education_6_17`, `education_18plus` (`edu` / `noedu`), behind one toggle
    `...education_by_age_kreis_control`, with `...education_participation_kreis_control` off. Each
    is MECE within its own band and each band total is exact census data (the cell parquet carries
    single-year columns), so no external age margin is committed. `education` is the realised plan
    source having a directly recorded leg whose MAPPED purpose is education under the ACTIVE flags,
    i.e. the seed uses the same `map_purpose` the trip builder uses (closing mechanism 2c).
  - **Q3 -- riders:** #374 (the Phase B home-office donor pool builds its trips WITHOUT the
    plan-structure keyword arguments `trips_stage.run` passes, and admits no-diary, only-rbW and
    holiday donors) is IN this package, because it is the same defect class in the same trip
    builder. #372 (passive escort, MiD `W_ZWECK 13`) is OUT to its own issue: it needs a
    cross-person pass inside `map_purpose`'s row-wise contract and a data question about the
    companion columns first. #373 (MiD `W_ZWD` subtypes for code 10) runs as a parallel DATA task,
    because the MiD `W_ZWD` codeplan has never been in the repository.
  - **Q4 -- the proof run is arm 4 at 100 %** on a hardlink copy of the arm-3 cache (popsim
    recomputes), not a 25 % pre-check: the quantities under test are per-Kreis conditional shares
    whose small cells (non-employed with a work trip in a small Landkreis) are exactly what a
    quarter-scale run cannot resolve, and the arm-3 cache makes the 100 % run affordable.
  - **Q5 -- "employed" means the same thing on both sides, and that meaning INCLUDES apprentices.**
    On the model side the control reads the P_BKAT-based `employment_status` classes
    `{vollzeit, teilzeit, geringfuegig, in_ausbildung}` (`attributes.EMPLOYED_EMPLOYMENT_STATUS_CLASSES`)
    -- the same seed column the `employment_status` control already constrains, so seed and target
    margins cannot disagree. On the SrV side `V_ERW in {8, 9, 10, 11}`. ADR-0060 treats MiD
    `in_ausbildung` (1.93 %) and SrV `V_ERW 8` (1.87 %) as apples-to-apples, and MiD's
    `EMPLOYED_TAET` includes apprentices already. `srv_plan_structure.EMPLOYED_V_ERW` was realigned
    to the same set in the same branch, so the control and the validation reference measure ONE
    universe. The P_TAET-based `employed` person attribute written to the population is NOT changed.
  - **Q6 -- the diary plan match keeps the employment boundary hard.**
    `weekend_plan_match.match_person` gained a keyword-only `hard_keys` (default `frozenset()`, so
    the weekend caller's draw sequence is byte-identical), and
    `diary_plan_match.reassign_diaryless_plan_sources` passes `{"employed"}` while the new flag
    `braunschweig.population.popsim.diary_match_hard_employment` (default true) is on. A crossing
    that survives (the whole-pool fallback) is counted and logged as a rate. It is a GUARD, not a
    correction: of the 74,898 remaps of the arm-3 run only **2** relaxed past `employed`
    (`match_level_counts={0: 74896, 3: 2}`, committed log excerpt
    `run_log_excerpts_part1.txt` in the arm-3 artifact directory), so the expected population
    effect is nil and the value is that the boundary can no longer be crossed silently as the pool
    composition changes.
- **Rationale and rejected alternatives:**
  - **Four shares straight from SrV (rejected, Q1).** It is the simpler construction and needs no
    second table, but it would put two HARD controls about **1.4 pp** apart on the same employed
    margin: SrV `V_ERW {8, 9, 10, 11}` gives an employed share of **0.5389** for the region
    (committed `srv2023_work_by_employment_by_kreis.csv`, 03ZGB row), while the blended
    `target2026_employment_status_by_kreis.csv` Gesamt row gives **0.5252** for the same class set.
    PopulationSim would then be asked to satisfy two incompatible marginals of equal importance, and
    which one wins would be an artefact of the solver rather than a decision. `margin x conditional`
    keeps the disagreement OUT of the control system and confines it to a documented difference
    between two surveys.
  - **A DESTATIS age margin (12411) under one all-persons education control (rejected, Q2).** It
    would let a single control carry the age structure, but it commits a new external table to the
    repository for a quantity the Zensus cells already carry exactly, and its age classes conflict
    with the census bands the grid controls use.
  - **One 4- or 6-cell education control over all persons (rejected, Q2).** Its cell shares embed an
    age margin, and the census cell controls cannot pin the 6-17 share (their bands are `0_9`,
    `10_14`, `15_17`, `18_19`, ... in `control_spec.AGE_BANDS` / `FINE_TEEN_AGE_BANDS`), so two hard
    controls would fight over that share. Age-RANGE universes avoid any external margin: a per-Kreis
    6-17 total is summed from the single-year census columns and is exact.
  - **`V_ERW {9, 10, 11}` with MiD `EMPLOYED_TAET` (rejected, Q5).** It would have left the
    committed plan-structure reference untouched, but it keeps a 16-year-old apprentice `employed`
    on the model side and `school_age_6_17_not_employed` on the SrV side -- the asymmetry that
    made mechanism 4 measurable in the first place. Whichever set was chosen, the design required
    `EMPLOYED_V_ERW` to follow it in the same branch; realigning the reference is the honest cost of
    making the two sides comparable.
  - **Deferring the hard employment key (rejected, Q6).** Two crossings in 74,898 remaps make the
    change cheap now and invisible later; a pool whose composition shifts would reintroduce the
    defect silently, and the counter is what makes it observable either way.
  - **Leaving the all-persons controls ON alongside the replacements (rejected).** Two controls on
    overlapping universes would double-constrain the same persons with no way to attribute a
    residual, so `source_resolution.active_kreis_entries` RAISES at config time when a replacement
    and its predecessor are both `"on"` -- and equally when a replacement is on while
    `employment_status_kreis_control` (whose margin the work target is built from) is off.
- **Consequences:**
  - **Five config keys**, all in `configs/base_bs.yml` (the single home of every flag and default):
    `work_by_employment_kreis_control` (on) and `work_participation_kreis_control` (off),
    `education_by_age_kreis_control` (on) and `education_participation_kreis_control` (off), and
    `diary_match_hard_employment` (true). The two retired entries stay REGISTERED and their target
    files stay committed: they remain available for ablation configs, and
    `population_validation/participation_fit.py` still reads every
    `target2026_<purpose>_participation_by_kreis.csv`.
  - **The committed SrV plan-structure reference MOVED** when apprentices entered the employed set
    (`EMPLOYED_V_ERW`), and every comparison against it must state which vintage it used:
    `group_employed` `participation_work` **0.6755 -> 0.6694** on the primary `at_home_zero`
    universe (0.7087 -> 0.7033 on `at_home_only`) and `group_school_age_6_17_not_employed`
    `participation_education` **0.8997 -> 0.9042** (0.9171 -> 0.9215), with the segment sizes moving
    accordingly (8,066 -> 8,306 and 2,062 -> 2,039 persons). The `all` / `age_*` / `sex_*` /
    `kreis_*` rows are unaffected. The arm-3 manifest records this explicitly, so the -9.2 pp
    employed gap quoted above is a gap against the OLD reference and will not reproduce exactly
    against the current one. Blast radius: data record
    `docs/registry/data/srv2023_plan_structure_reference.yml`.
  - **Two universes now coexist and must never be mixed.** The CONTROL targets the at-home-or-mobile
    rate (SrV `p_work_employed` 0.7032 for the region, 14+); the ANALYSIS stage measures the model
    on its primary `at_home_zero` universe (`group_employed` `participation_work` 0.6694), which
    includes the away-from-home persons as zero-trip. The two differ by about 3.4 pp for this
    segment by construction, so a proof run must be judged against the denominator its own metric
    uses. `population_validation` writes the control-universe fit separately
    (`participation_universe_fit.csv`, feature record `participation_universe_controls`).
  - **Caches recompute once:** `braunschweig.popsim.completed_donor` (the diary-match change) and
    the popsim stage (new seed columns, new controls, and `KEY_EXCLUDE_RBW_LEGS` now declared there
    because the seed reads it). The four new target CSVs also purge the PopulationSim batch cache
    through `batch_cache.compute_batch_config_signature`.
  - **Owed re-measurements, none of them done here:** ADR-0104 check 1 and the Phase B pool
    statistics (the rider changes the pool), the commute-distance baselines (#357-#359) and the
    BA-flow calibration -- all of which describe a worker population this package is meant to
    change -- and `work_participation_by_kreis`'s `n_workers_not_employed`.
  - **Three hard controls now sit on overlapping margins** (`employment_status`,
    `work_by_employment`, the employment-grid age shape). The control-fit report must show all of
    them converging; if it does not, the importance group is revisited rather than the target.
    Convergence of the controls is FIT, never validation.
- **Proof (arm 4, measured 2026-09-08; run manifest
  `participation-universe-controls-arm4-100pct-2026-09-08`, 100 % ZGB-8 at commit `30cde0a1`):**
  arm 4 is arm 3 plus this package, which is the `configs/base_bs.yml` default state, so the
  overlay overrode not one flag. **Eight of the nine pre-registered rows are MET; one is missed by
  0.28 pp.** Employed persons with a work trip rose 58.38 % -> 69.36 % (bound >= 62 %); education
  participation among the 6-17 not employed rose 84.27 % -> 93.05 % (bound >= 88 %); education
  among the 0-5 fell 73.44 % -> 68.62 % (band 63-69 %); the ADR-0104 check-1 deviation moved
  +8.06 pp -> +2.25 pp (bound +/- 3 pp); workers with a workplace who are not flagged employed fell
  41,680 (13.1 % of workers) -> 18,672 (5.0 %) while the worker pool GREW 17 %; diary remaps
  crossing the employment boundary fell 2 -> 0 of 74,898; the donor-pool filters removed 1,182 of
  8,026 home-office donors (14.7 %), of which 1,017 had no diary at all. The MECE claim of decision
  Q2/Q3 held to 0.0013 pp: persons without a trip moved 11.2794 % -> 11.2780 %, and the two arms'
  `comparison.csv` differ in md5 and person count, so that near-identity is a measurement and not a
  cache hit.
  **Read on the universe the controls TARGET (`at_home_only`) the three controlled segments land
  within 1 pp** -- employed work -0.97, education 6-17 +0.89, education 0-5 +0.77 -- which is the
  direct confirmation of decision Q1's margin-times-conditional construction and of the ONE
  universe rule of this ADR. Read on `at_home_zero`, the universe the analysis stage prints, the
  same three sit 2.4 to 3.0 pp ABOVE the reference, because the model has no away-from-home state
  while that reference counts such persons as zero-trip: the deviation this ADR itself documents,
  not a new finding.
  **The one miss is a mechanism, not a tuning problem.** Seniors 65+ not employed with a work trip
  fell 7.82 % -> 3.28 % against a <= 3 % bound. `work_by_employment` pins the TOTAL
  `nonemployed_work` mass over persons 14+ and leaves the age composition inside that cell free, so
  the control cannot stop the residual concentrating in one age band. An age split of that cell
  would be the fix; it is deliberately NOT in this package.
  **Two limits on the above.** (1) These controls were RAKED to the committed targets, so agreement
  with them is convergence toward a target and NOT independent agreement with reality: on the
  controls' own universe the mean absolute deviation over ten aggregate cells falls from 4.90 pp to
  0.48 pp and every cell improves, which proves the controls steer the quantity they name and not
  that the steered quantity matches the world. (2) The risk this ADR named -- three hard controls
  sitting on overlapping mass -- is CLOSED, by running the identical validation code on both
  populations rather than by assuming a baseline: no systematic degradation across the ten
  validated controls, both employment controls improved on their worst cell (decision Q1 removing
  the disagreement by construction), `trip_class` closed through its own quantity with the mobility
  rate unmoved at 87.94 -> 87.93 %, and one small real exception in `household_size`, whose worst
  cell grew 1.88 pp in five of six categories.

- **Evidence:** issues **#368** (this package), **#374** (rider, IN), **#369** (the measuring
  analysis stage), **#370** (universe rule fixed here), **#372** and **#373** (assessed and kept
  out); branch `feature/participation-universe-controls` (commits `0b97939e` age-range universes,
  `de6aa965` + `53ed8da5` + `b4e76d2d` the two seed derivations, `b737b726` + `da1abd50` the SrV
  aggregates and the `EMPLOYED_V_ERW` realignment, `4ce67d77` + `2f5264e7` the four targets,
  `3cefec7d` + `304d815a` the registry entries and toggles, `8ba32aaf` the hard employment key,
  `ec8a5ea1` + `6d07c531` + `cf7e56bd` the #374 donor pool, `c6690197` + `03328350` the
  control-fit reporting); code `braunschweig/popsim/kreis_attribute_control.py`,
  `braunschweig/popsim/mid/participation.py`, `braunschweig/popsim/stage/`,
  `braunschweig/popsim/diary_plan_match.py`, `braunschweig/popsim/weekend_plan_match.py`,
  `braunschweig/calibration/srv_participation_universe.py`,
  `braunschweig/analysis/population_validation/participation_fit.py`,
  `braunschweig/synthesis/commute_day/donor_pool.py`,
  `scripts/extract_srv_participation_universe.py`,
  `scripts/build_participation_universe_targets.py`; tests
  `tests/test_participation_universe_controls.py`, `tests/test_participation_universe_targets.py`,
  `tests/test_srv_participation_universe.py`, `tests/test_kreis_control_stage_wiring.py`,
  `tests/test_weekend_plan_match.py`, `tests/test_diary_plan_match.py`,
  `tests/test_participation_fit.py`, `tests/test_commute_day_donor_pool.py`; feature record
  `docs/registry/features/participation_universe_controls.yml`; data records
  `srv2023_work_by_employment`, `srv2023_education_by_age`,
  `target2026_work_by_employment_by_kreis`, `target2026_education_by_age_by_kreis`; the measured
  side is `docs/runs/plan-structure-fix-arm3-100pct-2026-09-07.yml` with its committed artifact
  directory `eqasim-data/data/braunschweig/calibration/plan_structure_fix_arm3_100pct_2026-09-07/`.
  ADR-0104, ADR-0106, ADR-0107, ADR-0108 and ADR-0060 are the records this one composes with.
  ARM 4 HAS RUN (2026-09-08): the measured side is now
  `docs/runs/participation-universe-controls-arm4-100pct-2026-09-08.yml` with its committed artifact
  directory
  `eqasim-data/data/braunschweig/calibration/participation_universe_controls_arm4_100pct_2026-09-08/`,
  summarised in the Proof bullet above, and the arm-3 manifest is the BEFORE state.
  **What is measured is not the same as validated: the controls converged toward committed targets
  they were raked to, one pre-registered row is NOT met, and the overlapping-mass risk is still
  recorded as unknown.**
