# ADR-0110 · 2026-09-09 · General day absence as a two-stage state draw
- **Status:** active
- **Numbering:** ADR-0110 is the next free id after ADR-0109. Allocated 2026-09-09 and
  RE-VERIFIED on 2026-09-09 before this record was written: every remote branch of
  `TUBS-IVS/eqasim-bs` was listed (`git for-each-ref --format='%(refname:short)' refs/remotes/origin`,
  19 heads including `origin/main`) and each one's `docs/decisions/` tree was grepped for
  `ADR-011*` (`git ls-tree -r --name-only <branch> -- docs/decisions | grep ADR-011`); the highest
  id found anywhere is ADR-0109, and the working tree (including every local branch and worktree of
  this checkout) was grepped for `ADR-0110` -- the only hits are the forward references this
  branch's own Tasks 1-7 already placed in code comments, docstrings and `configs/base_bs.yml`
  before this record was written. Ids are append-only, so a colliding draft on a sibling branch is
  renumbered rather than this one.
- **Context:** Every synthetic person exists and starts the reporting day at home; the model has no
  concept of a person being away for the WHOLE day. The SrV 2023 reference the model is judged
  against (`braunschweig.analysis.synthesis.plan_structure_vs_srv`, issue #369) records that 4.98 %
  of persons were away from home over the whole reporting day (`E_ANZ_WEGE == -7`, 954 of 18,223
  delivered persons, `GEWICHT_P_ZENSUS`) -- vacation, travel, hospital stay, overnight elsewhere.
  The only existing absence concept is the far-commuter `absent` state of ADR-0104 (issue #244),
  which applies to 0.51 % of employed persons on the 2026-09-06 proof and says nothing about
  non-workers or near commuters.

  Measured on the arm-4 run (`participation-universe-controls-arm4-100pct-2026-09-08`, ADR-0109,
  stage `braunschweig.analysis.synthesis.plan_structure_vs_srv`, universe `at_home_zero`): the
  model's mobility rate sits **+4.4 pp** above SrV, and every controlled participation segment
  (ADR-0109) overshoots the `at_home_zero` reference by 2.4-3.0 pp although it MEETS its
  `at_home_only` target -- the two universes differ by exactly the persons SrV records as away from
  home the whole day, which the model cannot represent. The user decided on 2026-09-05 (spec
  `2026-09-05-day-structure-programme.md` section 2) to keep the `trip_class` immobility target at
  the SrV at-home basis (11.2 %) rather than raise it to the all-persons basis (15.6 %), and to
  MODEL the missing absence state instead -- issue #370, executed as sub-project B of the
  day-structure programme.

  **Evidence (SrV 2023 BS+RGB, local raw, `GEWICHT_P_ZENSUS`, measured 2026-09-09; committed by
  `scripts/extract_srv_absence.py` into `eqasim-data/data/braunschweig/srv/srv2023_absence_by_age_band.csv`
  and `srv2023_absence_household_by_size.csv`, data records `srv2023_absence_by_age_band` /
  `srv2023_absence_household_by_size`).** Absent share by age band (bands chosen so every band has
  `n >= 1,081` persons and `>= 31` absent):

  | age band | n persons | n absent | absent share |
  |---|---|---|---|
  | 0-5 | 1,081 | 31 | 3.34 % |
  | 6-17 | 2,062 | 42 | 1.89 % |
  | 18-29 | 1,639 | 148 | 8.31 % |
  | 30-44 | 3,279 | 143 | 4.42 % |
  | 45-64 | 5,681 | 298 | 4.89 % |
  | 65-74 | 2,722 | 197 | 5.88 % |
  | 75+ | 1,757 | 95 | 5.81 % |
  | all | 18,223 | 954 | 4.98 % |

  Employment (14+) separates little: employed 4.82 %, not employed 6.04 %. Per Kreis the share
  ranges from 3.20 % (Salzgitter, n 1,794) to 6.42 % (Peine, n 2,446) -- assumption-grade cells
  under SrV's stratified PSU design, reported but not used for the draw.

  Household clustering: **55.8 %** (person-weighted; 49.4 % unweighted) of absent persons live in a
  household in which EVERY member is absent. Share of households in which all members are absent,
  by household size (household-weighted, `GEWICHT_HH_ZENSUS`): 1 person 5.29 %, 2 persons 3.40 %, 3
  persons 1.31 %, 4 persons 1.49 %, 5+ persons 0.18 % (n households 1,969 / 3,750 / 1,166 / 934 /
  287; the sibling person-weighted composition figures quoted in the spec differ slightly because
  they use the person, not the household, weight -- both tables commit the numbers each actually
  uses, see `srv2023_absence_household_by_size.csv`'s own header). Of 73 absent children (0-17), 47
  have at least one absent adult in their household. Partially absent households are dominated by
  18-29-year-olds (113 of 190 in that band are absent in such households): young adults away alone.

  **Why MiD `P_STREISE` is not the donor** (issue text had suggested "consuming `P_STREISE` donors
  where available"). Codebook: `P_STREISE` ("Reise am Stichtag") = 1 Tagesreise, 2 Reise mit 1-3
  Uebernachtungen, 3 mind. 4 Uebernachtungen, 402 Kind unter 14 Jahren (not asked), 609 im
  gewohnten Umfeld. Measured on weekday persons: codes 2/3 = 5.7 % weighted, but (a) children under
  14 are never asked (0.00 % for the 0-5 band), (b) 71 % of the code-2/3 persons were MOBILE on the
  reporting day (15,145 of 21,387: the day they leave or return), (c) `anzwege1` of these persons is
  0 for only 6,093 of them. An absent day has no trips by definition, so no donor chain is needed at
  all; the MiD variable would only add noise to a state the SrV tables already parameterise
  directly.
- **Decision:** A new day state, `day_absence_state` in `{present, absent_household,
  absent_individual}`, drawn for EVERY synthetic person -- independent of employment, workplace
  assignment, or the commute-day-state model -- so that both the SrV age-band rates and the SrV
  household clustering are reproduced. Flag default ON (project convention); OFF path
  byte-identical.

  1. **State model** (pure module `braunschweig/synthesis/day_absence/absence.py`,
     `draw_absence`). Household stage: every household with `n` members gets `size_class =
     min(n, 5)`; with probability `p_household(size_class)` (committed
     `srv2023_absence_household_by_size.csv`) the WHOLE household is `absent_household`.
     Individual residual stage: for every age band `a` (the seven bands above) the target
     person-level rate `r(a)` (committed `srv2023_absence_by_age_band.csv`) and the rate the
     household stage already realised in that band, `r_hh(a)`, give the residual probability
     `p_individual(a) = max(0, (r(a) - r_hh(a)) / (1 - r_hh(a)))`; every still-present person in
     that band is `absent_individual` with that probability. If `r_hh(a) > r(a)` the residual is 0
     and the overshoot is logged as a WARNING naming the band and both rates
     (`_check_reference_coverage` / the overshoot branch in `draw_absence`) -- expected nowhere in
     practice, since the household rate stays under ~1.5 % in the size classes (3+) that dominate
     family households, well below every band target.
  2. **Determinism.** One `numpy.random.RandomState(random_seed + DAY_ABSENCE_SEED_OFFSET)` with a
     new offset constant, `DAY_ABSENCE_SEED_OFFSET = 7351` (verified free of every other
     `*_SEED_OFFSET` / `*_RNG_OFFSET` constant in the repository before being fixed); households
     sorted by `household_id`, persons by `(household_id, person_id)`, one draw vector per stage
     over the sorted frame -- the same reproducibility discipline as
     `braunschweig.synthesis.commute_day.state.COMMUTE_DAY_SEED_OFFSET` (7301).
  3. **Composition with ADR-0104** (`braunschweig.synthesis.commute_day.plan_replacement.build_day_trips`,
     new keyword `general_absence: pd.DataFrame | None`). Independent of `commute_day_state`: in the
     reporting-day trips a person is trip-less when `commute_day_state == "absent"` OR
     `day_absence_state != "present"`; the union is counted three ways in the returned
     diagnostics (`n_persons_absent_commute`, `n_persons_absent_general`, `n_persons_absent_both`,
     `n_persons_absent_total`, `n_trips_removed_general`). A `home` worker whose donor chain was
     about to be spliced in and who is also generally absent ends trip-less (absence wins over the
     splice; excluded from the matched set before the splice loop runs, so no donor block is ever
     built for them). No escort protection in the general draw -- ADR-0104 Assumption 4 was that an
     escort leg evidences presence at home for a FAR COMMUTER specifically; a family vacation takes
     the escorted child along by the household stage, and solo absence of an escorting adult exists
     genuinely in the SrV data.
  4. **Stage** `braunschweig.synthesis.day_absence.absence_stage` (`STAGE_NAME`). Inputs:
     `synthesis.population.enriched` (`person_id`, `household_id`, `age`), config `random_seed`,
     `data_path`, `day_absence_enabled` (`KEY_ENABLED`, default `true`),
     `day_absence_household_stage_enabled` (`KEY_HOUSEHOLD_STAGE`, default `true`; `false` runs an
     individual-only draw anchored to the same band table -- the pre-registered sensitivity arm),
     `day_absence_max_band_deviation_pp` (`KEY_MAX_BAND_DEVIATION_PP`, default `1.0`: realised minus
     reference per band, WARNING above it for bands with `>= 1,000` persons,
     `MIN_PERSONS_FOR_BAND_GUARD` -- a guard against a broken join, not a target). Output
     `{"absence": frame, "diagnostics": dict}` with exactly one row per enriched person, asserted.
     OFF: every person `present` / `reason = "disabled"` (`_disabled_frame`), no reference file
     read, diagnostics `{"enabled": False}` -- but the frame's schema and its derived `age_band` /
     `household_size_class` columns are identical to the ON path, so a consumer never has to branch
     on the flag. `validate()` folds an md5 of the pure module's source into the synpp cache token,
     the same mechanism `braunschweig.synthesis.commute_day.state_stage.validate` uses.
  5. **Reporting-day trips** (`braunschweig.synthesis.commute_day.trips_day_stage`). Two
     INDEPENDENT flags gate what is composed into the reporting day: `commute_day_state_enabled`
     (`KEY_ENABLED`) and `day_absence_enabled` (`KEY_DAY_ABSENCE_ENABLED`, reading
     `braunschweig.synthesis.day_absence.absence_stage` via `ABSENCE_STAGE`).
     `trips_day_stage.configure` declares all four stages -- `synthesis.population.trips`,
     `state_stage`, `home_office_donors_stage`, `ABSENCE_STAGE` -- UNCONDITIONALLY; the gating is in
     `execute`, not `configure` (the test-harness stub context used across
     `tests/test_commute_day_stages.py` does not resolve a config value the way real synpp's
     `ConfigureContext` does, so a conditional `context.stage(...)` there would never be recorded as
     declared). With both flags false the stage returns the pre-assignment frame BY IDENTITY (the
     same object, not a copy); with only `day_absence_enabled` true, empty commute-day placeholders
     (`empty_states()`, `empty_matches()`, `_empty_donor_trips()`) keep `build_day_trips`'s column
     contract satisfied without touching the state/donor stage outputs at all.
  6. **Exports.** `synthesis.output.PERSON_OPTIONAL_OUTPUT_COLUMNS` gains `day_absence_state`;
     `braunschweig.synthesis.commute_day.output_day.attach_day_absence_state` merges it into the
     enriched persons frame next to `commute_day_state`, gated by its own
     `KEY_DAY_ABSENCE_ENABLED` / `ABSENCE_STAGE`, coverage rate logged, zero coverage raises (same
     fallback-transparency contract as `attach_commute_day_state`).
     `matsim/scenario/population.py::OPTIONAL_PERSON_FIELDS` gains `day_absence_state`, written as
     the MATSim person attribute `dayAbsenceState` (the same camel-case convention as
     `commuteDayState`); `braunschweig/matsim/scenario/population.py::attach_day_absence_state`
     attaches it the same way `attach_commute_day_state` does.
  7. **Analysis.** `braunschweig.analysis.synthesis.plan_structure_vs_srv` gains
     `plan_structure_trips_view` (`KEY_TRIPS_VIEW`, `{"final", "pre_assignment"}`, default
     `"final"` -- the model's reporting day; `"pre_assignment"` reproduces the pre-#370 numbers).
     When the `"final"` view AND `day_absence_enabled` are both on, the absence stage's
     away-from-home person ids reach `braunschweig.analysis.plan_structure.harmonise_model`'s
     `absent_person_ids` keyword, which sets `away_from_home` / `reported_at_home` accordingly, so
     the model side can ALSO be restricted to the `at_home_only` universe (today it is always
     `at_home_zero`); the stage logs a WARNING when `plan_structure_srv_universe == "at_home_zero"`
     while the absence flag is off or the view is `pre_assignment` (the universe trap of ADR-0109,
     in reverse: comparing a zero-trip-for-away-persons reference against a model that has no
     away-from-home concept at all). `braunschweig.analysis.synthesis.work_participation_by_kreis`
     (ADR-0104 check 1): generally absent employed persons count as `absent` in
     `commute_day_state_shares.csv` via `apply_general_absence`, which overrides the drawn
     `commute_day_state` to `absent` for every generally absent employed person BEFORE the shares
     are summed (including one without an assigned workplace, folding them out of
     `share_no_workplace`); the count is reported as the appended column `n_absent_general`,
     present (as 0) even when the flag is off, so the schema never depends on the flag.
- **Rationale and rejected alternatives:**
  - **MiD `P_STREISE` as the donor (rejected).** Recorded in Context above: children under 14 are
    never asked the question (a structural blind spot for the 0-5 band, which the SrV-anchored draw
    does not have), 71 % of the candidate donors were themselves mobile on the reporting day (the
    variable answers "did this person travel overnight around the reporting day", not "is this
    person absent on it"), and an absent day carries no trips by construction, so no donor CHAIN is
    needed at all -- a donor-matching design would add sampling noise and matching-coarsening
    machinery to solve a problem the two committed SrV aggregates already solve directly by
    parameterising the draw.
  - **Individual-only draw, no household stage (rejected as the DEFAULT, kept as the pre-registered
    sensitivity arm `day_absence_household_stage_enabled: false`).** Simpler (one Bernoulli draw per
    person at the band rate) and still hits every band marginal in expectation, but it cannot
    reproduce the 55.8 % household clustering: with an independent per-person draw at a ~5 % rate,
    the chance that every member of even a 2-person household is drawn absent is `~0.25 %`, three
    orders of magnitude below the observed household-fully-absent shares (3.40 % for size 2).
    Family vacations and joint travel are a real, measured pattern (partially absent households are
    dominated by lone 18-29-year-olds, the OPPOSITE signature), and an individual-only draw would
    silently erase it while still passing every per-band rate check -- exactly the kind of green
    test that proves nothing about the mechanism CLAUDE.md's fallback-transparency rule warns
    against. The two-stage construction is kept as the default for this reason; the individual-only
    arm is retained as arm 2 of the pre-registered A/B (see Consequences) precisely so the
    clustering's contribution can be measured, not assumed.
  - **Raising the `trip_class` immobility target from the at-home basis (11.2 %) to the all-persons
    basis (15.6 %) instead of modelling absence (rejected, user decision 2026-09-05).** It would
    have been a one-line target change with no new stage, but it would fold two different phenomena
    -- a present person who happens not to travel, and a person who is not in the region at all --
    into one immobility number, and it would still leave the model with no explicit away-from-home
    state for any OTHER stage (the analysis universes, the two commute analyses) to condition on.
    Modelling the state explicitly keeps `trip_class` measuring what it always measured (immobility
    among persons who are actually present) and makes the away-from-home population a first-class,
    inspectable quantity instead of an implicit dilution of a different target.
  - **Per-Kreis absence rates (rejected).** SrV's per-Kreis absence shares (3.20-6.42 %, Context
    above) rest on the same stratified-PSU cells as every other per-Kreis SrV rate in this
    codebase (ADR-0104, ADR-0109) and are assumption-grade for a full Kreis; the region-level rate
    is applied everywhere and the per-Kreis realised shares are reported, never gated, following the
    precedent `braunschweig.analysis.synthesis.work_participation_by_kreis`'s ADR-0104 check 1
    already set for per-Kreis SrV cells of this size.
- **Consequences:**
  - **Universe comparability.** The general day-absence draw is what lets
    `braunschweig.analysis.synthesis.plan_structure_vs_srv` compare the model against the SrV
    `at_home_only` universe for the first time (ADR-0109 introduced that universe for the
    PopulationSim participation controls, which target the plan AS SYNTHESISED; this package
    supplies the corresponding AWAY-FROM-HOME state so the plan-structure VALIDATION comparison can
    use the same universe on the reporting-day view). Reading `at_home_zero` without the draw
    applied is now an explicitly warned universe mismatch, closing the "universe trap" this ADR's
    Context describes for the participation controls (ADR-0109, memory
    `project-participation-universe-controls`) in its plan-structure form.
  - **Cache devalidation.** `synthesis.population.trips.final` and everything downstream of it
    (secondary locations, the MATSim population, the synthesis output) recompute once per changed
    arm, the same cost class ADR-0104's `.final` alias switch already documented; no divergent
    branch is run against the shared server cache (memory
    `feedback-no-divergent-branch-against-shared-cache`).
  - **Pre-registered A/B (server, cached arm-4 population; NOT run as part of this task -- Task 10
    of the SDD plan).** Arms: (0) `day_absence_enabled: false` = today; (1) ON, two-stage (the
    default); (2) ON, `day_absence_household_stage_enabled: false` (the sensitivity arm above). Only
    `trips.final` and downstream recompute. Measured with `plan_structure_vs_srv` (view `"final"`,
    universe `at_home_zero` primary and `at_home_only` sensitivity) and
    `work_participation_by_kreis`:

    | metric | arm 0 (arm-4 numbers, `pre_assignment` view) | SrV | expected arm 1 (ASSUMPTION) |
    |---|---|---|---|
    | mobility rate, all | +4.4 pp vs SrV | `at_home_zero` | within +/- 1.0 pp |
    | absent share per age band | 0 | table above | within +/- 1.0 pp per band |
    | absent persons in fully absent households | -- | 55.8 % | 45-65 % |
    | employed with a work trip (`at_home_zero`) | +2.25 pp | 0.6511 (ADR-0104 check 1 basis) | within +/- 1.5 pp |
    | `participation_*` on `at_home_only` (model restricted) | met (arm 4) | -- | unchanged within +/- 0.5 pp |

    A metric moving the wrong way stops the ladder for diagnosis (no fix stacking). The OFF arm must
    be byte-identical to arm 4 on `trips.final`
    (`tests/test_commute_day_stages.py::test_trips_day_stage_off_off_returns_the_identical_object`
    pins the identity object; a server run additionally compares `trips.csv` md5). No arm has run
    yet at the time this record is written -- see the Assumptions and Evidence sections below for
    what is and is not yet known.
  - **Risks named but not resolved here:** the trip_class / participation controls of ADR-0109 were
    fitted on the `at_home_only` universe, so the employed-with-work-trip share on `at_home_zero`
    moves BY CONSTRUCTION once absence is modelled -- that is the intended comparability this
    package restores, pre-registered above, not a regression to chase. Escort coherence: a
    generally absent adult with escort legs simply has no trips; the escorted child's own plan is
    untouched (a present adult escorting an absent child anchors at the child's school as today,
    harmless and counted). SrV per-Kreis absence (3.20-6.42 %) is not modelled (rejected above); the
    region rate is applied everywhere.
- **Assumptions (explicit, spec `2026-09-09-general-day-absence-design.md` section 6):** Absence
  probability depends on age band and household size only -- employment and Kreis are reported (in
  the Context evidence and via `braunschweig.analysis.synthesis.work_participation_by_kreis`), not
  modelled. The household stage reproduces the "whole household away" mass and the individual stage
  the remainder; the two-stage construction hits the band marginals IN EXPECTATION (the residual
  formula is exact in expectation, so any realised per-band deviation is sampling noise, guarded at
  `day_absence_max_band_deviation_pp`, not a defect signal by itself). Absence is assumed
  independent of the person's trip pattern WITHIN a band (an absent person is drawn without regard
  to whether they would otherwise have travelled). The SrV Tuesday-Thursday reporting-day absence
  level is assumed to be the right level for the simulated normal weekday (no seasonal or
  day-of-week adjustment is applied). None of these assumptions has been checked against an
  independent reference; they are the explicit, named cost of anchoring a two-parameter draw to two
  small committed tables rather than a richer, unavailable microdata source.
- **Evidence:** issue **#370** (this package); related **ADR-0104** / #244 (the far-commuter
  `absent` state this composes with), **ADR-0106** (plan sources must be realisable; the
  `at_home` / `at_home_zero` universe statement), **ADR-0109** / #368 (the participation controls
  target the `at_home_only` universe -- the universe rule this package's plan-structure comparison
  now shares); spec `docs/superpowers/specs/2026-09-09-general-day-absence-design.md` (sections 1-6
  are the source of every number and rule in this record) and its programme document
  `2026-09-09-day-structure-programme.md`; code
  `braunschweig/calibration/srv_absence.py`, `scripts/extract_srv_absence.py`,
  `braunschweig/synthesis/day_absence/absence.py`, `braunschweig/synthesis/day_absence/absence_stage.py`,
  `braunschweig/synthesis/commute_day/plan_replacement.py`,
  `braunschweig/synthesis/commute_day/trips_day_stage.py`, `synthesis/output.py`,
  `braunschweig/synthesis/commute_day/output_day.py`, `matsim/scenario/population.py`,
  `braunschweig/matsim/scenario/population.py`,
  `braunschweig/analysis/synthesis/plan_structure_vs_srv.py`,
  `braunschweig/analysis/plan_structure.py`,
  `braunschweig/analysis/synthesis/work_participation_by_kreis.py`; tests
  `tests/test_srv_absence.py`, `tests/test_day_absence.py`, `tests/test_day_absence_stage.py`,
  `tests/test_commute_day_plan_replacement.py`, `tests/test_commute_day_stages.py`,
  `tests/test_commute_day_consumers.py`, `tests/test_plan_structure_vs_srv.py`,
  `tests/test_work_participation_by_kreis.py`; feature record
  `docs/registry/features/general_day_absence.yml`; stage record
  `docs/registry/stages/braunschweig.synthesis.day_absence.absence_stage.yml`; data records
  `docs/registry/data/srv2023_absence_by_age_band.yml`,
  `docs/registry/data/srv2023_absence_household_by_size.yml`; contributor note
  `docs/codebase/notes/day-absence-state.md`.
  **No run has executed this code as of this record.** The measured evidence above is entirely the
  SrV 2023 tables and their extraction; the pre-registered A/B in Consequences is a plan, not a
  result, and the feature record's `validation.state` is `unvalidated` until Task 10 of the SDD
  plan runs it and records a manifest. Convergence of a MATSim run is not validation and is not
  claimed here.
