# ADR-0106 · 2026-09-06 · Plan sources must be realisable normal-weekday diaries
- **Status:** active
- **Numbering:** ADR-0106 is the next free id. Checked on 2026-09-06 across the main checkout,
  every local worktree under `.claude/worktrees/*/docs/decisions/`, the remote default branch
  (`gh api repos/TUBS-IVS/eqasim-bs/contents/docs/decisions`) and the file lists of all open pull
  requests (no open PR adds an ADR); the highest id found anywhere was ADR-0105. Ids are
  append-only, so a colliding draft on a sibling branch is renumbered here rather than the other
  way round (the mechanism ADR-0099 records for the ADR-0098 collision).
- **Context:** In the popsim_mid workflow every synthetic person carries a MiD *plan source*
  (`source_H_ID`, `source_P_ID`) whose recorded travel day becomes that person's MATSim plan.
  Two properties of the MiD B1 donor pool make some of those sources unrealisable as a
  normal-weekday plan, and both were invisible until the plan chain was measured at every
  boundary against SrV 2023 (i329 100 % run of 2026-08-24; boundary measurement of 2026-09-05
  recorded in the design spec of this branch and in the run manifest of the A/B ladder).

  **1. Plan sources without a diary.** 159,728 persons (**14.1 %**) draw a plan source whose MiD
  diary was never collected: `anzwege1 == 803` ("Person ohne Wegeerfassung") or `804`
  ("Mobilitaet unbekannt"). `attributes.map_trip_class` and `attributes.map_participation`
  IMPUTE these two codes within the person's age band. For a **control** variable that is the
  right thing to do -- PopulationSim then sees a normally mobile seed and hits its target (seed
  immobile **11.5 %** against the target 11.2 %, every Kreis within 0.5 pp). For a **plan** it is
  not: the plan builder joins the Wege table on the same source keys, finds zero rows, and
  **100 %** of those persons end the pipeline immobile. The realised population is therefore
  **23.0 %** persons without a trip against SrV **15.6 %** (all persons, including those away
  from home for the whole day) and **11.2 %** on the at-home basis that a fully present synthetic
  population must be compared with.
  The deficit is entirely carried by these sources. Restricted to persons whose plan source DOES
  carry a diary, every hard SrV-anchored control is met: immobility 8.4-11.5 % per Kreis, work
  participation 0.314 against the 0.332 target, leisure 0.372/0.383, education 0.184/0.176,
  escort 0.104/0.099. The pipeline was not failing to hit its targets; it was hitting them in the
  seed and then discarding the day.
  These donors are not immobile people. **92 %** of the `803` donors report `mobil == 1`, i.e.
  they WERE mobile on their reporting day -- only their trips were not collected. The pool share
  of no-diary weekday persons in MiD is 10.9 % unweighted (14.8 % weighted); the synthesis draws
  14.1 %.

  **2. Reporting days that are not normal weekdays.** Every SrV-anchored target in this project
  is a **normal-weekday** level: the SrV 2023 reference days are Tuesday to Thursday OUTSIDE
  school and public holidays (TU Dresden method note; 102-113 eligible days per year). The MiD
  donor pool is year-round. **5.2 %** of weekday diary persons reported on a public holiday at
  their residence (`feiertag == 1`), and **18.3 %** of the synthetic plan sources come from July
  or August. The effect on the day is large and systematic: education participation of 6-17-year
  old diary donors is 65.5 % overall, **26 % in August**, 49 % in July, 69 % excluding July and
  August, and 73 % excluding public holidays as well -- against SrV **88-94 %**. Employed donors:
  work participation 52 % overall, 44 % in December, 47 % in August.
- **Decision:**
  - **A plan source must be a realisable normal-weekday diary.** After the weekend-plan match,
    `braunschweig.popsim.completed_donor` runs a second matching pass
    (`braunschweig/popsim/diary_plan_match.py`) that re-draws the plan source of every person
    whose source is not realisable. Only `source_H_ID` / `source_P_ID` change; the receiving
    person's own attributes are untouched, exactly as in the weekend match.
  - **Who is remapped, and to what:**
    - `anzwege1 == 803` **and** `mobil == 1` (mobile, diary not collected) -> a MOBILE weekday
      diary (`anzwege1 >= 1`).
    - `anzwege1 == 803` **and** `mobil != 1` -> **kept**. Zero trips is then the observed day.
    - `anzwege1 == 804` (mobility unknown) -> ANY weekday diary, mobile or immobile.
    - a source whose only legs are rbW legs, so that nothing remains of the plan once ADR-0107
      drops them -> a MOBILE weekday diary. Detected from the diary facts
      (`n_direct_legs == 0` and `n_rbw_legs > 0`), which is the same information as MiD
      `mobil_diff == 2` but derived from the Wege rows the pipeline actually uses; applied ONLY
      when `exclude_rbw_legs` is ON, because with it OFF the legs remain and the source is
      realisable.
    - `feiertag == 1` -> a non-holiday weekday diary with the same mobility state (mobile to the
      mobile pool, immobile to any pool), behind
      `braunschweig.population.popsim.exclude_holiday_plan_sources`.
    - everything else -> kept.
  - **The donor pool for every remap** is the real (non-filler) weekday persons (`kernwo` 1-3)
    with a realisable diary in the sense above, and excludes `feiertag == 1` persons whenever
    that flag is ON.
  - **The matcher is reused, not reinvented:** `weekend_plan_match.match_person` with its keys
    (`has_license`, `sex`, `age_band`, `employed`, `has_pt`), its hierarchical relaxation and its
    `P_GEW`-weighted draw. The rng is the completion rng continued, in deterministic frame-index
    order, so the OFF path stays byte-identical
    (`tests/test_completed_donor_stage.py::test_build_completed_donor_off_matches_pre_task_inline_pipeline`).
  - **The imputation policy for the CONTROL variables stays as it is.** It is correct for the
    person's own seed row and for the OFF path. What changes is that no plan source carries
    803/804 any more once the flag is ON, so for plan sources the imputation branch is never
    taken. **Defense in depth:** with the flag ON, `derive_trip_class_seed` RAISES if any
    resolved plan source still carries 803 or 804 -- a silent regression of this fix would
    otherwise be invisible, since the seed would keep hitting its target exactly as before.
  - **Universe statement (this is the record of it).** Every SrV-anchored target in this project
    is a normal-weekday level (Tue-Thu outside school and public holidays). The MiD donor pool is
    year-round. **Public-holiday reporting days are excluded from plan sources and from the donor
    pool. School-HOLIDAY PERIODS remain in the pool** and are not identifiable in MiD B1 (only
    the survey month and the Land are delivered, never the reporting date); they are steered
    towards normal-weekday levels by the participation controls instead. "The simulated day is a
    normal weekday" is therefore true by construction for public holidays and true only in
    EXPECTATION, via the controls, for school-holiday periods. Any statement about the modelled
    day must carry that distinction.
  - **Flag** `braunschweig.population.popsim.diary_plan_match`, default `true` (project
    convention), read by `braunschweig.popsim.completed_donor` and declared again by
    `braunschweig.popsim.stage`, where it arms the guard above.
  - **Observability** (CLAUDE.md fallback transparency): the remap is logged per resolution class
    with counts, rates and the match-level distribution under the marker `[diary_plan_match]`,
    the per-person decisions are written to `diary_plan_match_trace.parquet` next to the weekend
    trace, and a remapped share outside 5-25 % warns.
- **Rationale and rejected alternatives:** The argument is not that the borrowed day is the day
  the person would have had -- it is that a plan source with no diary produces a day that is
  demonstrably WRONG (empty) for 92 % of the affected donors, while the matched replacement is
  wrong only to the extent that the matching keys are coarse. That is the same assumption the
  already-merged weekend-plan match rests on, applied to a second unrealisable-source class.
  Two alternatives were considered and rejected:
  - **Exclude the affected households from the donor pool entirely.** Rejected: the no-diary
    persons are not a random subset (they are concentrated in specific household and age
    constellations), and removing their HOUSEHOLDS would also remove the diary-carrying members
    who share them, distorting the household-level controls the seed is built on. The remap
    changes only which day a person realises, never who exists.
  - **Accept the empty plans and raise the immobility target to the SrV 15.6 % all-persons
    level.** Rejected on universe grounds: the 15.6 % includes persons who were away from home
    over the whole reporting day, a state the model does not have -- every synthetic person
    exists and starts the day at home. The correct comparison basis is the SrV at-home level of
    **11.2 %**, and moving the target to 15.6 % would encode a survey artefact as a modelling
    goal. (General absence as a modelled state is follow-up issue **#370** on the commute-day-
    state model, not a target change.)
- **Consequences:**
  - `braunschweig.popsim.completed_donor` gains the MiD **Wege** table as an input (the diary
    facts are computed from it) and its cache hash changes once; everything downstream of the
    donor build recomputes once per A/B arm.
  - The eight `src_*` plan-source fact columns are attached to the completed-donor persons frame
    UNCONDITIONALLY and therefore propagate into the synthetic persons frame. This is intended
    (ADR-0107's rbW attributes and ADR-0108's closed-day seed both read them);
    `synthesis.output` selects its columns explicitly, so `persons.csv` gained only the two rbW
    columns ADR-0107 adds deliberately. Recorded in the stage record and in
    `docs/codebase/notes/popsim-plan-source-resolution.md`.
  - The residual participation deficit that `docs/registry/features/srv_participation_controls.yml`
    and the i329 run manifest describe as "donor-bound" is expected to be largely REMOVED by this
    change; that expectation is an ASSUMPTION until the A/B ladder measures it.
  - ADR-0104's "-15.67 pp work-participation gap is a separate open question" (#244) is partly
    this record's subject: how much of that gap is the empty plans measured here is not decomposed
    by any run yet.
  - The commute-day-state Phase B home-office donor pool
    (`braunschweig/synthesis/commute_day/donor_pool.py`) admits 803/804 donors and public-holiday
    diaries today; applying the same filters there is a REQUIRED follow-up on **#244**, to be
    executed in that branch after it merges, never concurrently in its worktree.
- **Evidence:** issue **#365**; the branch `feature/plan-structure-fix` (commits `7c5d5381`
  diary facts, `dcd1ea01` the matcher, `fe215682` + `6d43cd68` the stage wiring);
  `braunschweig/popsim/diary_facts.py`, `braunschweig/popsim/diary_plan_match.py`,
  `braunschweig/popsim/completed_donor.py`; tests `tests/test_diary_facts.py`,
  `tests/test_diary_plan_match.py`, `tests/test_completed_donor_diary_match.py`,
  `tests/test_completed_donor_stage.py`, `tests/test_trip_class_seed_closure.py`;
  feature record `docs/registry/features/diary_plan_match.yml`; stage record
  `docs/registry/stages/braunschweig.popsim.completed_donor.yml`; the SrV side of every number
  compared above is the committed
  `eqasim-data/data/braunschweig/srv/srv2023_plan_structure_reference.csv`
  (data record `docs/registry/data/srv2023_plan_structure_reference.yml`) and
  `eqasim-data/data/braunschweig/srv/srv2023_participation_by_kreis.csv`; the model side is the
  i329 production run `docs/runs/100pct-allfeat-i329-2026-08-24.yml` and the boundary measurement
  of 2026-09-05 recorded in this branch's design spec. ADR-0104 (register vs reporting-day
  universe), ADR-0107 (rbW legs), ADR-0108 (closed plans). **The A/B ladder that measures the
  effect of this decision has not run at the time of writing: nothing here is a validation
  claim.**
