# How a synthetic person gets its plan source (popsim_mid)

In the `popsim_mid` workflow a synthetic person does not get a synthesised day plan.
It gets a **plan source**: the pair `(source_H_ID, source_P_ID)` naming a real MiD
person whose recorded travel day becomes that person's chain. This note walks the
chain that decides which MiD person that is, says which stage owns which step, and
records two contracts that are easy to break by accident — the rng ordering that
keeps the OFF paths byte-identical, and the `src_*` columns that propagate further
than one might expect.

The *why* of each rule lives in ADR-0106 (realisable plan sources), ADR-0107 (rbW
legs) and ADR-0108 (closed plans); this note is the *where* and *how*.

## The chain, in order

Everything up to and including the fact attachment happens inside
`braunschweig.popsim.completed_donor` (`build_completed_donor`), which is cached once
and shared across runs (Tier B2).

1. **Member completion** — `mid.load_completed_donor`. Mirror-samples MiD seed
   households up to their declared `H_GR`. The filler persons it adds are copies of
   real household members, so they arrive carrying a plan source already.
2. **Weekend-plan match** — `weekend_plan_match.reassign_weekend_plan_sources`, when
   `weekend_plan_match_on`. A person whose plan source reported on a weekend is
   re-drawn to a matched weekday donor. Writes `weekend_plan_match_trace.parquet`.
3. **Diary facts** — `diary_facts.compute_diary_facts(mid.load_mid_wege(mid_dir))`.
   Computed **unconditionally**, i.e. the MiD **Wege** table is now an input of the
   donor build, not only of the trips stage. One row per MiD person, columns
   `FACT_COLUMNS`: `n_direct_legs`, `n_rbw_legs`, `rbw_distance_km`, `first_so1`,
   `first_direct_zweck`, `last_direct_zweck`, `ends_at_home`, `starts_arriving_home`.
4. **Diary plan match** — `diary_plan_match.reassign_diaryless_plan_sources`, when
   `diary_plan_match` is on. Re-draws the plan source of a person whose source cannot
   be realised as a normal weekday: no collected diary (`anzwege1 == 803` with
   `mobil == 1`, or `804`), only rbW legs left once `exclude_rbw_legs` drops them, or
   a public-holiday reporting day (`feiertag == 1`) when
   `exclude_holiday_plan_sources` is on. `anzwege1 == 803` with `mobil != 1` is
   **kept** — zero trips is then the observed day. Writes
   `diary_plan_match_trace.parquet`. Reuses `weekend_plan_match.match_person`, so
   there is exactly one matcher implementation in the repository.
   Since #368 (ADR-0109) the diary caller passes `hard_keys={"employed"}` while
   `diary_match_hard_employment` is on: the key ladder relaxes only the SOFT keys and
   never drops `employed`, because the receiving person's employment attribute and the
   donor's work diary would otherwise come from two different MiD respondents — exactly
   the plan the employment-conditional work control has to fight. Two consequences worth
   knowing before reading a trace: (a) with one hard key `match_level` counts SOFT
   relaxations, so its scale is 0..4 instead of 0..5 and the log line states the flag; (b)
   a person for whom NO donor shares the employment class still gets a donor through the
   pre-existing whole-pool fallback, and that crossing is counted in
   `DiaryMatchReport.n_crossed_employment_boundary` and logged as a rate (WARNING when the
   guard is on). The weekend caller passes no hard keys, so its draw sequence is
   byte-identical.
   Since #386 (ADR-0118) the diary caller additionally passes
   `age_band_edges=weekend_plan_match.FINE_CHILD_AGE_BAND_EDGES` while
   `diary_match_fine_child_age_bands` is on, which splits the coarse 6-13 child band into
   6-9 and 10-13 so a primary-school child cannot inherit a 13-year-old's school day. The
   same two reading rules apply, with one important difference from the employment key:
   `age_band` stays a SOFT key, so the ladder may still relax it, and a crossing is
   therefore legitimate rather than a defect. The counter
   `DiaryMatchReport.n_crossed_fine_child_age_band` (over `.n_remapped_in_split_child_band`,
   the remapped 6-13-year-olds) is measured against the FINE edges in BOTH arms, so the
   flag-OFF arm reports today's rate and an A/B compares like with like; no threshold is
   asserted and the line is always INFO. The weekend caller keeps the coarse edges.
5. **Fact attachment** — `diary_facts.attach_plan_source_facts`. Joins the facts of
   the FINAL plan source onto every person as `src_<fact>` columns. Also
   unconditional (see the propagation contract below).

Downstream, on the population that the donor build feeds:

6. **Seed derivation** — `braunschweig.popsim.stage` (the `data.census.filtered`
   alias), via `mid.project_completed_seed`. Reads `src_ends_at_home` /
   `src_n_direct_legs` to count the CLOSED day in the `trip_class` seed
   (`trip_class_seed_counts_closure`), and raises if a resolved plan source still
   carries `803`/`804` while `diary_plan_match` is on. Since #368 it also derives the
   two participation-UNIVERSE seed columns from the FINAL plan source's Wege rows —
   `work_by_employment` (employment status × a directly recorded work leg) and
   `education_flag` (a directly recorded education leg, mapped with the same
   `escort_passive_education` setting the trip build uses) — but only when a matching
   control entry is active; with none active neither seed path touches the Wege table.
   An unresolved plan source RAISES here too: no flag is ever imputed.
7. **Trip building** — `braunschweig.popsim.trips_stage` (the
   `synthesis.population.trips` alias), via `trips.build_trip_table`. Drops
   `W_RBW == 1` legs and leading arrive-home legs (`W_SO1 == 2`), then closes the
   chain in `plan_validation` with the empirical closure dwell.
8. **Person attributes** — `braunschweig.popsim.enriched_adapter` reads
   `src_n_rbw_legs` / `src_rbw_distance_km` into `rbw_legs_count` /
   `rbw_distance_km`, which `synthesis.output` and the MATSim population writer then
   export.

## Contract 1: one rng stream, one order

`build_completed_donor` creates **one** `RandomState(random_seed + COMPLETION_RNG_OFFSET)`
and passes it to member completion, the weekend match *and* the diary match. The three
draws form one entangled stream. Two consequences that are not obvious from any single
call site:

- **Do not reseed anywhere in the middle.** Giving a step its own generator changes
  every subsequent draw and silently changes the whole donor build, including the OFF
  paths of the other flags.
- **Do not reorder the steps, and do not iterate in a different order inside one.**
  The diary match walks its candidates in deterministic frame-index order for the same
  reason. This is what lets the OFF path be *byte-identical*: with
  `diary_plan_match_on=False` the persons frame must equal a reference that stops after
  the weekend match. That claim is pinned by
  `tests/test_completed_donor_stage.py::test_build_completed_donor_off_matches_pre_task_inline_pipeline`
  — if you change the ordering, that test is what will tell you, and it is a real
  contract, not a formality.

`braunschweig.popsim.completed_donor.validate()` hashes the helper modules
(`diary_facts`, `diary_plan_match`, `weekend_plan_match`, `member_completion`,
`mid.donor`) because synpp hashes only the stage module itself; without it an edit to a
helper would serve a stale cached donor build. Edit a helper and the stage recomputes —
that is intended, and it is why a cosmetic edit to one of those files is not free.

## Contract 2: the `src_*` columns propagate (intended)

`attach_plan_source_facts` runs **regardless of the flags**, so the eight `src_*`
columns sit on the completed-donor persons frame and therefore reach the synthetic
persons frame as well. This was flagged during review and is deliberate (ruling R8 of
the plan-structure implementation):

- they are **facts about the plan source**, not behaviour, so they carry no flag;
- two downstream steps read them (the closed-day seed in `popsim.stage`, the rbW person
  attributes in `enriched_adapter`), and deriving them twice from a ~2 GB Wege table
  would be both slower and a second place to get the definition wrong;
- `synthesis.output` selects its columns **explicitly**, so `persons.csv` did not
  change when the columns appeared — it changed only when `rbw_legs_count` /
  `rbw_distance_km` were added on purpose.

If you add a step that copies "all columns" of the persons frame somewhere, remember
these eight are there.

## MiD-only, and what ENTD does instead

`diary_plan_match`, `exclude_holiday_plan_sources` and `trip_class_seed_counts_closure`
cannot change an ENTD result: `popsim_open` has no `completed_donor` stage (which is the
only declarer of `exclude_holiday_plan_sources`), and while `popsim.stage` declares and
reads the other two on every path, they are applied only in its member-completion seed
branch — the ENTD branch bypasses `project_completed_seed` entirely. The three
trip-side keys (`exclude_rbw_legs`, `drop_leading_arrive_home_leg`,
`closure_dwell_model`) DO reach `trips_stage` on both paths, and `EntdSource.build_trips`
**rejects** a non-default value for each with a message naming the key — ENTD has no
`W_RBW` / `W_SO1` pendant and no MiD Wege table to estimate a dwell from. That is why
`configs/fixtures/config_popsim_open_braunschweig.yml` and
`configs/fixtures/config_smoke_popsim_open_mini.yml` set those three explicitly to
`false` / `fixed_1h`: without it, the default-ON values would make both configs
unrunnable.

## Where to look when a number looks wrong

- Remap rate, reasons and match levels: the `[diary_plan_match]` log line and
  `diary_plan_match_trace.parquet`.
- Dropped legs: `[popsim.trips] rbW legs dropped` and the leading-arrive-home line next
  to it; both report the number of donor persons emptied by the drop, which must be 0
  when `diary_plan_match` is on.
- Closure: `[popsim.plan_validation] closure dwell:` for the draw and fallback rates,
  `[closure_dwell]` for the model build, and the `is_synthetic_closure` column on the
  cached trips frame (deliberately not in `trips.csv`).
- The whole realised structure against SrV 2023:
  `braunschweig.analysis.synthesis.plan_structure_vs_srv`.
