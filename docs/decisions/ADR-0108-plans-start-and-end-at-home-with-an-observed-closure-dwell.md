# ADR-0108 · 2026-09-06 · Plans start and end at home, with an observed closure dwell counted in the seed
- **Status:** active
- **Numbering:** ADR-0108 is the next free id after ADR-0107, allocated in the same 2026-09-06
  sweep (main checkout, every local worktree, the remote default branch, and the file lists of
  all open pull requests; highest id found anywhere ADR-0105). Ids are append-only.
- **Context:** The MATSim plans this project produces are closed day chains, but three parts of
  how they are closed were either unmeasured, inconsistent between the seed and the plan, or
  fabricating trips. Measured on the i329 100 % run (boundary measurement 2026-09-05, this
  branch's design spec) against the committed SrV 2023 plan-structure reference:

  - **Blind closure.** `plan_validation.repair_trips` appends a return-home trip for **236,727**
    persons (**248,355** trips, **7.3 %** of all trips, **18 %** of all home trips). MiD weekday
    diaries end at home in **80.9 %** of cases (**24.1 %** open ends once rbW legs are removed);
    SrV diaries end at home in **97.8 %**. The model therefore has **100 %** closed days.
  - **A constant one-hour dwell.** `plan_validation.HOME_CLOSURE_DWELL_S = 3600` gave EVERY
    activity preceding an appended closure a duration of exactly 1.0 h: **65,761** work
    activities, mean 1.00 h, std 0.04. Of the 122,651 work activities shorter than 2 h, **53.6 %**
    precede a synthetic closure (and 57.9 % are rbW legs, ADR-0107); removing both leaves 13.3 %
    under 2 h at a mean of 6.11 h, against SrV 7.4 % and 6.29 h. The realised model share of
    activities in the 1-2 h BAND was **19.5 %** against SrV **4.8 %** -- an artefact of the
    constant, not a behavioural finding.
    *Which SrV number is which* (so no reader mistakes one for the other): 4.8 % is the 1-2 h
    band and 7.4 % the share under 2 h, both computed ad hoc in the 2026-09-05 boundary
    measurement. The value the analysis stage actually compares against is the COMMITTED column
    `share_work_activities_lt_2h` of
    `eqasim-data/data/braunschweig/srv/srv2023_plan_structure_reference.csv`, which is **0.1096**
    for the primary `at_home_zero` universe, segment "all" (n = 6,787 measurable work activities;
    definition: next departure minus arrival after a trip TO work, only where a further trip
    follows and only for durations in [0, 20] h). The committed extraction and the ad-hoc one
    therefore do NOT agree on the under-2 h share, and the difference is not decomposed here.
    Only the committed value is a reference; the boundary figures are motivation for this record
    and must not be used as targets. Reconciling the two definitions is open work.
  - **A seed that counts a different day.** The `trip_class` control is built on MiD `anzwege1`,
    which counts the diary WITHOUT the appended closure. The plan realises the day WITH it, and
    the SrV target counts a day that is closed in 97.8 % of cases. Seed, plan and target were
    three different quantities.
  - **A fabricated first trip.** MiD marks a chain whose first leg ARRIVES home with `W_SO1 == 2`
    (the person came home from somewhere they had spent the night): **5.2 %** of MiD chains,
    0.87 % of mobile donors. The pipeline assumed unconditionally that the first trip LEAVES
    from home, turning those into degenerate home->home first trips: **18,231** of them, **2.1 %**
    of persons. In total the population carries **88,372** home->home trips (2.6 % of all trips),
    part of which are legitimate MiD round trips (a walk).
  - **Genuinely open days exist.** The MiD open ends are mostly incomplete daytime diaries
    (leisure/shop/errand arrivals between 14 and 19 h), but they also contain real open days:
    visits and night-shift work arriving 18-23 h, and overnight travellers (`P_STREISE` 2/3,
    4.3 % of mobile persons, of whom 51 % end elsewhere). SrV records **2.24 %** open-end days,
    **2.19 %** open-start days and **0.69 %** single-trip days.
- **Decision (the closed-plan decision is the USER's, taken 2026-09-05 in the design
  brainstorming; the mechanics below implement it):**
  - **MATSim plans in this project always start and end at home.** No open chains, no single-trip
    days. Realism is bought where it is cheap, not through a day-retention model.
  - **The closure dwell is drawn from observed durations, not fixed.** With
    `braunschweig.population.popsim.closure_dwell_model: empirical` (default) the dwell before the
    appended return-home trip is drawn, seeded with `random_seed + CLOSURE_SEED_OFFSET`
    (`74517`), from the empirical activity-duration distribution of the SAME purpose in the
    DIRECTLY CLOSED MiD weekday chains, binned by purpose x arrival-hour band (band edges
    `ARRIVAL_BANDS_H = (0, 14, 18, 20, 22, 48)`, i.e. **five** bands: <14 h, 14-18 h, 18-20 h,
    20-22 h, 22 h+). A cell with fewer than `min_obs` observations falls back to the purpose
    marginal, and that to a global marginal; both fallback counts and their rates are logged, and
    a global-fallback rate above `CLOSURE_GLOBAL_FALLBACK_WARN_RATE = 0.5` warns loudly, because a
    majority hitting the global pool means the primary estimate is broken rather than thin.
    `min_obs` is the config key `braunschweig.population.popsim.closure_dwell_min_obs` (default
    **30**): it trades cell resolution against cell occupancy, so a study may legitimately vary it.
    The complementary bound on which observations are pooled at all (`MAX_OBSERVED_DWELL_S` =
    16 h) stays a module constant on purpose -- it delimits what a closing activity can plausibly
    last (a longer "duration" means the donor's day spans a night, so the observation is not a
    within-day dwell at all), which is not a parameter a study varies.
    `closure_dwell_model: fixed_1h` reproduces the previous constant exactly, INCLUDING the
    absence of the plan-time cap (see the next bullet).

    **What the pool is made of, and its bias.** The pools hold the duration of EVERY activity that
    is followed by another trip of the same person (`next departure - this arrival`), not only of
    activities that a donor actually ended by going home. An activity that is followed by another
    trip is by construction not the donor's last one, so the pool is **length-biased downward**
    relative to a genuine day-closing activity: a person who is still going out again tends to have
    stayed put for less time than one who is finishing their day. The alternative -- pooling from
    open chains too -- is impossible (an open chain's terminal activity has no observed next
    departure, i.e. exactly the quantity being estimated), and restricting the pool to "last
    activity before a return home" would leave far thinner cells. The direction of the bias is
    therefore stated here and must be read into any comparison of realised activity durations: the
    model's closing activities are, if anything, too SHORT, not too long.
  - **The plan-time cap is an EMPIRICAL-path behaviour only** (fix wave 2026-09-06, controller
    ruling R19). Only a DRAWN dwell can push a chain past `MAX_PLAN_TIME_SECONDS` where the
    one-hour constant would not have, so only the empirical path caps; with `fixed_1h` (or no
    model at all) the behaviour is exactly the pre-#367 one -- no cap, and a chain that ends up
    beyond the bound flows to the existing post-repair bound validation, which resamples the
    person from a same-cell donor. Capping the constant path too would have made the OFF path
    non-byte-identical, would have changed the Phase B donor pool, and could produce a
    zero-duration final activity. When the cap does apply it never shortens the dwell below the
    `HOME_CLOSURE_DWELL_S` floor: if even that floor breaches the bound (the outbound travel time
    alone consumes the remaining budget), the row is left UNCAPPED for the same bound
    validation -- a plausible closing activity is worth more than a technically-inside-the-bound
    one-minute stop. The cap rate is logged either way and WARNs only above
    `CLOSURE_CAP_WARN_RATE = 0.05` (the level pre-registered in the design spec), INFO otherwise.
  - **Synthetic closure trips are marked, not invisible.** `_append_return_home` sets
    `is_synthetic_closure` and suffixes the `trip_key` with `_closure`; `trips_stage` logs the
    share of persons and trips affected, and the new analysis stage reports it. A closure the
    model invents must be distinguishable from a trip the donor reported. The closure REPORTING
    lives in those two places -- the `trips_stage` log and
    `braunschweig.analysis.synthesis.plan_structure_vs_srv` -- and deliberately NOT in
    `population_validation`: the closure share is a plan-structure quantity that only means
    something next to the SrV reference and the accepted-deviation rows, and
    `population_validation`'s trip-coherence section (which the design spec originally named)
    reports control fit, not structure. Anyone looking for the closure rate looks at the analysis
    stage's outputs or the stage log, not at the validation report.
  - **The trip_class seed counts the day the person will realise.** With
    `braunschweig.population.popsim.trip_class_seed_counts_closure` ON (default), the seed is
    `anzwege1 + 1` for a plan source whose last directly recorded, non-rbW leg does not end at
    home (`W_ZWECK` not in {8, 9}), clipped at **50** so the value stays inside
    `map_trip_class`'s value map (51 would fall in the same class as 50 anyway; the value map
    stays the single source of the class scheme). Seed, realised plan and SrV target then count
    the same closed day.
  - **A leading leg that ARRIVES home is dropped** (`braunschweig.population.popsim.
    drop_leading_arrive_home_leg`, default ON): the day starts at home and the rest of the chain
    is unchanged, instead of fabricating a home->home first trip. A chain that becomes EMPTY by
    this drop goes through the ADR-0106 remap, because the person was demonstrably mobile. The
    trip_class seed SUBTRACTS that dropped leg (`anzwege1 - 1`, floored at 0) whenever the same
    flag is on, for the same reason the closure adds one: the control must count the day the plan
    realises (fix wave 2026-09-06, controller ruling R20). The flag is therefore read by
    `braunschweig.popsim.stage` as well as by `trips_stage` and `completed_donor`, and the three
    must be configured consistently.
  - **Two seed-vs-plan residuals are ACCEPTED, not fixed** (fix wave 2026-09-06, ruling R20).
    Both are named here so no later reader mistakes them for oversights:
    - **(a) Closure-excluded persons still get the +1.** `repair_trips` excludes persons with NaN
      plan times or an over-bound chain from the closure append and routes them to the same-cell
      resample; the seed, which is derived earlier and knows nothing about either condition, has
      already counted the return trip for them. Their seed therefore counts one trip more than
      their (replaced) plan realises. Left as is because the seed would otherwise have to
      anticipate a repair decision taken two stages later, and because the resample replaces the
      whole chain anyway, so the "correct" count is not defined for them.
    - **(b) With `exclude_rbw_legs` OFF and `trip_class_seed_counts_closure` ON, the +1 can be
      wrong.** `src_ends_at_home` describes the last DIRECT (non-rbW) leg, while with rbW legs
      kept the realised chain ends on an rbW leg, whose purpose need not agree. The combination is
      not the production configuration (both flags default ON, where the two coincide by
      construction) and is only reachable in a deliberate A/B; the mismatch is documented rather
      than special-cased, because ADR-0107 already states that a plan containing rbW legs counts a
      different day by construction.
  - **Accepted deviations, recorded here as such, not silently absorbed.** Because plans are
    always closed:
    - the SrV **2.24 %** open-end days are modelled as returning home late -> model 0 %;
    - the SrV **2.19 %** open-start days are modelled as starting at home -> model 0 %;
    - the SrV **0.69 %** single-trip days cannot exist -> model 0 %; MiD's 7.8 % single-trip days
      become two-trip days.
    The analysis stage `braunschweig.analysis.synthesis.plan_structure_vs_srv` writes exactly
    these three rows to `accepted_deviations.csv` with both sides' value and a pointer to this
    record, so a reader of any comparison sees them as a decision rather than a defect. The same
    decision also means the SrV share of mobile persons with an ODD trip count (23 %) is not
    reachable: every closed day ends even-ended.
  - **Round trips are left alone.** The 88,372 home->home trips (some of them legitimate) are
    counted and reported, not removed; a virtual destination for MATSim is follow-up **#375**.
  - **Definitional note, so no reader mis-reads the headline metric:**
    `share_trips_followed_by_same_purpose` (model 10.6 %, SrV 4.5 %) COUNTS home->home pairs. Any
    change to the number of home trips -- including this record's closure -- moves it, and it must
    not be read as a pure "chained same-purpose activities" indicator.
  - **Measurement only, not decided here:** whether the 5 % of home trips coded MiD 9 ("Rueckweg
    vom vorherigen Weg") should return to the previous ORIGIN instead of home is an open
    question, recorded and not answered.
- **Rationale and rejected alternatives:**
  - **Model the genuinely open ~2 % of days (retention / overnight-away state).** Rejected by the
    user for this project: it needs a retention model, a place for the person to be, and a
    MATSim representation of a plan that does not return -- a substantially larger project than
    the ~2 pp of realism it buys. Recording the gap as an accepted deviation is honest and costs
    nothing; pretending the model has 97.8 % closure would not be.
  - **Keep the fixed one-hour dwell.** Rejected: it is not a modelling choice, it is a constant
    that manufactured 65,761 one-hour work activities and, with ADR-0107, produced most of the
    4x deviation in the "work activity under 2 h" metric. An observed distribution per purpose
    and arrival band is available from the same Wege table the plans come from.
  - **Draw the dwell from ALL MiD chains rather than the directly closed ones.** Rejected: an
    open chain has no observed NEXT departure, so its terminal activity has no measurable
    duration; including it would require imputing exactly the quantity being estimated.
  - **Keep assuming every first trip leaves from home and let the home->home first trips
    stand.** Rejected: those 18,231 trips are pure fabrication -- the donor recorded an ARRIVAL
    home, and MiD says so explicitly via `W_SO1 == 2`. Dropping the leg uses the information the
    survey gives instead of overriding it.
  - **Leave the seed on plain `anzwege1`.** Rejected: it guarantees a systematic seed-vs-plan
    disagreement for every open-ended donor, which no amount of control fitting can remove,
    because the control and the realisation measure different days.
- **Consequences:**
  - The closure dwell becomes stochastic and seeded; a run's plans change with the seed, as they
    already do for every other draw. `fixed_1h` reproduces the old behaviour exactly, pinned by
    `tests/test_popsim_trips_stage.py::test_run_default_flags_off_is_byte_identical_to_previous_signature`,
    which additionally asserts the closure row's dwell equals the pre-change constant.
  - The `trip_class` control target and the seed now describe the closed day; a comparison of
    trip counts against any pre-#367 measurement is not like-for-like.
  - Two model-side metrics are 0 % BY DECISION (open-end and single-trip days) and one is
    structurally bounded (odd trip counts). Every report that shows them must show the accepted-
    deviation rows next to them.
  - `is_synthetic_closure` is a trips-frame column and deliberately does NOT reach `trips.csv`
    (that writer has an explicit column list); the analysis stage reads the cached frame.
  - The two residuals above mean seed and plan agree for the production configuration but not for
    every reachable one; any A/B that turns `exclude_rbw_legs` off must not read a `trip_class`
    control gap as a fitting failure.
  - MiD-only: `EntdSource.build_trips` rejects a non-`fixed_1h` dwell model and the arrive-home
    drop (no MiD Wege table, no `W_SO1`), so the two popsim_open fixture configs set both keys to
    their OFF values explicitly.
- **Evidence:** issue **#367** (packages 3 and 5), analysis stage issue **#369**, follow-up
  **#375**; branch `feature/plan-structure-fix` (commits `bd7b0fae` + `d7f4eb7d` the empirical
  dwell and the closure marking, `74cb309b` + `48ca587b` the stage threading, `1c5ad2c8` +
  `a29e289b` the seed closure, `ec500224` + `8b889e9b` the arrive-home drop, `955bbe0f` the
  analysis stage, `0ea220e4` the fix wave that scoped the cap to the empirical path, added the
  arrive-home subtraction to the seed and made `min_obs` a config key); `braunschweig/popsim/closure_dwell.py`,
  `braunschweig/popsim/plan_validation.py`, `braunschweig/popsim/trips.py`,
  `braunschweig/popsim/trips_stage.py`, `braunschweig/popsim/mid/participation.py`,
  `braunschweig/analysis/synthesis/plan_structure_vs_srv.py`; tests
  `tests/test_closure_dwell.py`, `tests/test_popsim_trips_stage.py`,
  `tests/test_trip_class_seed_closure.py`, `tests/test_popsim_trips_rbw.py`,
  `tests/test_plan_structure_vs_srv.py`; feature record
  `docs/registry/features/home_closure_model.yml`; the SrV side of every number above is the
  committed `eqasim-data/data/braunschweig/srv/srv2023_plan_structure_reference.csv`
  (`share_mobile_last_to_home` 0.9776, `share_trips_followed_by_same_purpose` 0.0453,
  `mean_work_activity_h` 6.2904 for the "all" segment, primary universe `at_home_zero`), data
  record `docs/registry/data/srv2023_plan_structure_reference.yml`; the model side is
  `docs/runs/100pct-allfeat-i329-2026-08-24.yml` with the 2026-09-05 boundary measurement.
  ADR-0106 and ADR-0107 are the two records this one composes with. **The A/B ladder has not run:
  every expected effect stated in this record is an assumption, not a measurement.**
- **Proof (2026-09-07, arm 3, 100 %, run manifest `plan-structure-fix-arm3-100pct-2026-09-07`):**
  238,757 closure trips (6.53 % of 3,655,159; i329 7.3 %), 23.8 % of mobile persons closed. Dwell
  draws: 88.9 % from the (purpose, hour, duration-class) cell, 11.1 % purpose-marginal fallback,
  0 global fallback, 0.4 % capped -- the fixed 1 h spike is gone (work activities that precede a
  closure now have mean 4.66 h). Leading arrive-home legs dropped for 3,198 donors (1.07 %); no
  degenerate home->home first trip remains. `share_mobile_odd_trip_count` 0.2931 vs SrV 0.2325
  (i329 0.34), inside the pre-registered 25-30 % band. The accepted deviations were written by the
  stage as designed (open-end 0 vs 0.0224, open-start 0 vs 0.0219, single-trip days 0.0010 vs
  0.0058). `share_trips_followed_by_same_purpose` stayed at 0.1076 vs SrV 0.0453 and did NOT reach
  the pre-registered < 6 %: the unique MiD donors show 0.107 themselves (repeats at 14-16 % of the
  trips of leisure, other, work and education), so the gap is the MiD/SrV recording granularity,
  not a closure effect; the 86,761 home->home round trips (2.4 %) are follow-up issue #375. The
  decisions of this record stand.
