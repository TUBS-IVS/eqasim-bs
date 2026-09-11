# ADR-0118 · 2026-09-11 · The MiD donor match bands 6-13-year-olds finely (6-9 / 10-13) wherever it matches on age

- **Status:** active
- **Numbering:** ADR-0118 is the next free id after ADR-0117. Verified on 2026-09-11 before this
  record was written, by the check ADR-0111's, ADR-0113's, ADR-0115's and ADR-0116's Numbering
  paragraphs record: `git fetch origin`, then `git for-each-ref --format='%(refname)'
  refs/remotes/origin refs/heads` (57 refs -- 23 under `refs/remotes/origin`, including
  `origin/HEAD`, and 34 local `refs/heads`, i.e. every worktree branch of this checkout), each
  ref's decision tree listed with `git ls-tree -r --name-only <ref> -- docs/decisions` and grepped
  for `ADR-01[0-9][0-9]`. The highest id found on any ref is **ADR-0117**, already merged into
  `origin/main`, and `gh pr list --state open` returned no open pull request, so 0118 is free
  everywhere. Ids are append-only, so a colliding draft on a sibling branch is renumbered, never
  this one.

- **Context:** A synthetic person does not get a synthesised day plan; it gets a **plan source**,
  the pair `(source_H_ID, source_P_ID)` naming a real MiD person whose recorded travel day becomes
  that person's chain (`docs/codebase/notes/popsim-plan-source-resolution.md`). Inside
  `braunschweig.popsim.completed_donor` **three** passes decide that correspondence, and all three
  matched on an age band cut by the same coarse edges `(-1, 5, 13, 17, 200)`, i.e. 0-5 / **6-13** /
  14-17 / 18+:

  1. **Member completion** (`member_completion._match_present_members`). An incomplete seed
     household is filled from a structurally similar "mirror" household. The band decides which
     mirror members count as *already present*; the rest are copied in as fillers. A present
     7-year-old could consume the mirror's 13-year-old slot, so the host received a SECOND
     7-year-old instead of the 13-year-old sibling the mirror household actually has -- which
     contradicts the module's own stated assumption ("the missing members resemble the surplus
     members of a structurally similar complete household").
  2. **Weekend plan match** (`weekend_plan_match.align_members`, plus its person-level fallback and
     its mixed-household sweep, both `match_person`). A weekend-surveyed household is given a
     matched WEEKDAY household and then aligned member by member; each target member inherits its
     partner's plan source. A 7-year-old could be paired with the donor's 13-year-old and inherit
     that school day.
  3. **Diary plan match** (`diary_plan_match.reassign_diaryless_plan_sources` via `match_person`,
     ADR-0106, #365). Same defect on the re-draw; this is the one issue #386 was filed against.

  A first-grader and a 13-year-old are not interchangeable. Measured on the raw MiD 2023 B1
  delivery (2026-09-09, issue #386): a first education leg before 07:00 occurs for **5.4 %** of
  6-9-year-olds but **18.2 %** of 10-13-year-olds, and secondary-school ways are longer than
  primary-school ways. The timing half is largely subsumed by the departure-time model (#123,
  ADR-0114), which redraws first departures from an SrV-mapped distribution; the **trip-LENGTH**
  half is not, because the plan source's recorded distances survive into the chain.

  Scale, from the 100 % donor build on the raw delivery (2026-09-11): member completion filled
  36,253 of 218,097 households and added 65,042 filler persons; the weekend match remapped
  **138,777** persons; the diary match remapped ~74,900 of 485,709 plan sources on the arm-3 run.
  Pass 2 is therefore the largest of the three, not pass 3.

  Issue #386 asked for pass 3 only, on the assumption that touching pass 2 would break the
  weekend match's documented byte-identity contract -- the shared seeded RNG stream that member
  completion, the weekend match and the diary match draw from in that fixed order. **That
  assumption turned out to be false** (see Decision).

- **Decision:** One config key,
  `braunschweig.population.popsim.donor_match_fine_child_age_bands` (default **true**), selects
  the fine child bands for **all three** passes. `weekend_plan_match` owns both edge sets and the
  banding helper:

  - `AGE_BAND_EDGES = (-1, 5, 13, 17, 200)` -- today's coarse bands, still the DEFAULT of every
    primitive (`age_band_index`, `align_members`, `match_person`), so an un-passed call is
    byte-identical to before.
  - `FINE_CHILD_AGE_BAND_EDGES = (-1, 5, 9, 13, 17, 200)` -- a REFINEMENT: every coarse edge is
    kept and only the value 9 is added, so 0-5, 14-17 and 18+ are untouched and only 6-13 splits
    into **6-9** (primary school, Grundschule) and **10-13** (lower secondary).

  `member_completion` imports them instead of keeping its own copy (it had an identical second
  copy of the coarse edges, which is exactly how two matchers drift apart).

  **The RNG stream does not move.** This is the finding that made pass 2 cheap, and it is
  structural, not incidental:

  - `align_members` and `_match_present_members` consume **no** rng at all, and both return a
    band-INDEPENDENT number of pairs -- `min(len(target), len(donor))` and
    `min(len(present), len(mirror))` respectively, because every unmatched item falls through to
    an "any free" branch. The number of person-level fallback draws the caller makes afterwards is
    therefore unchanged.
  - `match_person` draws exactly ONE weighted value per call whichever branch returns, under any
    edge set (already pinned for the `hard_keys` parameter, now also for `age_band_edges`).

  So the flag changes WHICH donor is assigned, never the draw sequence. Pinned by
  `tests/test_weekend_plan_match.py::test_fine_child_bands_leave_the_rng_stream_untouched_on_a_randomised_population`
  (60 randomised households hitting the hh-match, person-fallback and sweep branches),
  by the same assertion on a hand-built fixture, and by
  `tests/test_popsim_member_completion.py::test_fine_child_bands_do_not_move_the_member_completion_rng_stream`.

  The diary match additionally counts and logs the crossing rate in BOTH arms, against the FINE
  edges either way: `DiaryMatchReport.n_crossed_fine_child_age_band` over
  `.n_remapped_in_split_child_band` (the remapped persons aged 6-13, i.e. the only ones the
  refinement can move). The flag-OFF arm therefore reports TODAY's rate.

- **Rejected alternatives:**

  1. **Change `AGE_BAND_EDGES` itself, with no flag.** Rejected: it removes the OFF arm that makes
     the A/B possible, and it silently re-bands `align_members`' and `_match_present_members`'
     defaults for any future caller that did not ask for it. The constant stays the primitives'
     default; the passes select.
  2. **Make `age_band` an un-relaxable hard key (like `employed` in ADR-0109) instead of refining
     it.** Rejected: a hard age band would push persons with no same-band donor into the
     whole-pool fallback, i.e. onto an ARBITRARY donor, which is strictly worse than a
     neighbouring-band one. Refining the band keeps the graceful ladder and removes only the worst
     pairing inside one band.
  3. **Three separate keys, one per pass.** Rejected: this is ONE decision (a 6-9-year-old and a
     10-13-year-old are not interchangeable donors), and three keys invite a half-applied state --
     a population whose member completion, weekend match and diary match disagree about who is
     interchangeable -- that is harder to defend than either extreme. Attribution within the
     single arm is still possible because each pass logs its own counters.
  4. **Fix only the diary match, as issue #386 asked.** Rejected once the RNG-invariance above was
     established and measured: the weekend match is the LARGER instance of the identical defect
     (138,777 vs ~74,900 remapped persons), and leaving it would have left the bug in place in the
     pass that moves the most people.
  5. **Split 14-17 as well, or use single-year bands for children.** Rejected without evidence:
     the measurement separates 6-9 from 10-13, not 14-15 from 16-17, and the donor pool thins with
     every extra band (a thinner pool relaxes MORE often, which crosses the band anyway). The
     edges stay a parameter, so a later measurement can refine further.

- **Consequences:**

  - Donor assignment changes in all three passes, so **scientific results change**. The key is
    declared in `braunschweig.popsim.completed_donor.configure()`, so it is part of that stage's
    config hash: flipping it rebuilds the donor and, as a descendant, re-runs popsim instead of
    serving a stale cache.
  - Unlike the other diary flags, this one is read **regardless of** `diary_plan_match`: member
    completion and the weekend match run either way.
  - The number of DIARY remaps itself differs between the two arms, because the upstream weekend
    match now pairs household members differently and a different set of persons therefore ends up
    sourced from a diary-less donor. An A/B must compare rates, not raw remap counts.
  - `weekend_plan_match._age_band` became public as `age_band_index(ages, edges=...)`, and
    `member_completion` dropped its duplicate `AGE_BAND_EDGES` in favour of importing it. There is
    now one owner for the donor-matching bands. They remain distinct from
    `control_spec.AGE_BANDS` / `FINE_TEEN_AGE_BANDS` (the IPF CONTROL bands, ADR #320) and from
    `braunschweig.calibration.srv_absence.AGE_BAND_EDGES` -- three different questions, three
    different cuts.
  - Two new report fields and two new `set_info` keys
    (`diary_plan_match_remapped_in_split_child_band`,
    `diary_plan_match_crossed_fine_child_age_band`) reach the run info.

- **Assumptions (explicit):**

  - ASSUMPTION: 6-9 vs 10-13 is the behaviourally relevant cut inside the 6-13 band. It is
    motivated by the measured early-departure gap (5.4 % vs 18.2 %) and by the German school
    system (Grundschule ends after grade 4, i.e. at about age 10), not by a committed reference
    table of trip lengths by single year of age. No such table is in the repository.
  - Because `age_band` remains a SOFT key on the `match_person` ladder, a crossing is a legitimate
    relaxation, NOT a defect. Unlike ADR-0109's employment counter, the fine-band counter is
    therefore NOT required to be 0 with the flag on, and **no threshold is asserted** -- asserting
    one would require a reference for how often the ladder should relax, which does not exist. The
    comparison is the A/B, not a bound.
  - NOT MEASURED YET at production scale: the realised effect on school-trip length by age band.
    The A/B the issue asks for has not been run, so this record documents a mechanism and a
    measured motivation, not a validated improvement. Flipping the single key in
    `configs/base_bs.yml` reverts it.

- **Evidence:** issue **#386** and its raw-MiD Finding (2026-09-09); the 100 % donor-build log of
  2026-09-11 for the pass sizes quoted above; the implementation in
  `braunschweig/popsim/weekend_plan_match.py` (`FINE_CHILD_AGE_BAND_EDGES`, `age_band_index`,
  `align_members(age_band_edges=...)`, `match_person(age_band_edges=...)`,
  `reassign_weekend_plan_sources(fine_child_age_bands=...)`),
  `braunschweig/popsim/member_completion.py` (`_match_present_members(age_band_edges=...)`,
  `complete_members(fine_child_age_bands=...)`), `braunschweig/popsim/mid/donor.py`
  (`load_completed_donor(fine_child_age_bands=...)`), `braunschweig/popsim/diary_plan_match.py`
  (`SPLIT_CHILD_AGE_RANGE`, `_count_fine_child_band_crossings`) and
  `braunschweig/popsim/completed_donor.py` (flag threading, `configure`, `execute`, `set_info`);
  the tests listed in `docs/registry/features/diary_plan_match.yml`. Related: ADR-0106 (realisable
  plan sources), ADR-0109 (the un-relaxable employment key whose shape this mirrors), ADR-0114
  (the departure-time model that subsumes the timing half of the defect).
