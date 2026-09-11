# ADR-0118 · 2026-09-11 · The diary plan match bands 6-13-year-olds finely (6-9 / 10-13); the weekend match keeps the coarse bands

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

- **Context:** The plan source of a synthetic person is a real MiD person whose recorded travel day
  becomes that person's chain (`docs/codebase/notes/popsim-plan-source-resolution.md`). Two passes
  inside `braunschweig.popsim.completed_donor` re-draw that source, and BOTH used the one matcher
  `weekend_plan_match.match_person`:

  - the **weekend plan match**, which gives a weekend-surveyed donor a matched WEEKDAY plan, and
  - the **diary plan match** (ADR-0106, #365), which gives a person whose source has no realisable
    weekday diary a matched one.

  `match_person` matched on `age_band`, one of five keys, banded by the module constant
  `weekend_plan_match.AGE_BAND_EDGES = (-1, 5, 13, 17, 200)`: 0-5 / **6-13** / 14-17 / 18+. Inside
  the 6-13 band a first-grader and a 13-year-old were interchangeable donors, so a 6-year-old could
  receive a 13-year-old's school day. The two are not interchangeable behaviourally. Measured on
  the raw MiD 2023 B1 delivery (2026-09-09, issue #386): a first education leg before 07:00 occurs
  for **5.4 %** of 6-9-year-olds but **18.2 %** of 10-13-year-olds, and secondary-school ways are
  longer than primary-school ways. The timing consequence is largely subsumed by the departure-time
  model (#123, ADR-0114), which redraws first departures from an SrV-mapped distribution; the
  **trip-LENGTH** consequence is not, because the plan source's recorded distances survive into the
  chain.

  The blast radius is bounded by the remapped share -- on the arm-3 100 % run 74,898 of 485,709
  plan sources (15.4 %) were remapped by the diary match -- and, within that, by the 6-13-year-olds
  among them.

  The weekend match cannot simply be changed with it: its draw sequence is a documented
  byte-identity contract shared with member completion and the diary match in ONE seeded RNG
  stream, pinned by
  `tests/test_weekend_plan_match.py::test_match_person_with_empty_hard_keys_reproduces_todays_draw_sequence`.

- **Decision:** `match_person` gains an `age_band_edges` parameter whose default is exactly today's
  `AGE_BAND_EDGES`, so every existing call is byte-identical. A second constant
  `weekend_plan_match.FINE_CHILD_AGE_BAND_EDGES = (-1, 5, 9, 13, 17, 200)` REFINES it -- every
  coarse edge is kept and only the value 9 is added, so 0-5, 14-17 and 18+ are untouched and only
  6-13 splits into **6-9** (primary school, Grundschule) and **10-13** (lower secondary). The
  **diary plan match only** passes it, behind the config key
  `braunschweig.population.popsim.diary_match_fine_child_age_bands` (default **true**, project
  rule: new features default on). The weekend match keeps the coarse edges and its contract.

  This mirrors the shape of ADR-0109's `diary_match_hard_employment` exactly -- one un-relaxed or
  refined key on the diary caller, a counter, a rate in the log -- with one deliberate difference
  stated below.

  The crossing rate is counted and logged in BOTH arms, against the FINE edges either way:
  `DiaryMatchReport.n_crossed_fine_child_age_band` over `.n_remapped_in_split_child_band` (the
  remapped persons aged 6-13, i.e. the only ones the refinement can move). The flag-OFF arm
  therefore reports TODAY's rate and the two arms of an A/B measure the same quantity.

- **Rejected alternatives:**

  1. **Change `AGE_BAND_EDGES` for both matches.** Rejected: it breaks the weekend match's
     byte-identity contract and would silently reshuffle the shared RNG stream for member
     completion and every later consumer, so a change motivated purely by school-day behaviour
     would move the whole population. The parameter keeps the two callers independent.
  2. **Make `age_band` an un-relaxable hard key (like `employed` in ADR-0109) instead of refining
     it.** Rejected: a hard age band would push persons with no same-band donor into the
     whole-pool fallback, i.e. onto an ARBITRARY donor, which is strictly worse than a
     neighbouring-band one. Refining the band keeps the graceful ladder and simply removes the
     worst pairing inside one band.
  3. **Split 14-17 as well, or use single-year bands for children.** Rejected without evidence:
     the measurement behind this record separates 6-9 from 10-13, not 14-15 from 16-17, and the
     donor pool thins with every extra band (a thinner pool relaxes MORE often, which crosses the
     band anyway). The edges stay a parameter, so a later measurement can refine further.
  4. **Leave it OFF by default until the A/B is run.** Rejected: the counter makes the effect
     observable either way, the OFF arm of the A/B produces exactly the "today" rate the issue
     asks for, and the project rule is that new features default on. Flipping the single key in
     `configs/base_bs.yml` reverts it.

- **Consequences:**

  - The donor draw of remapped 6-13-year-olds changes, so **scientific results change**: this key
    is part of `braunschweig.popsim.completed_donor`'s config hash (declared in its `configure()`),
    so flipping it rebuilds the donor and, as a descendant, re-runs popsim rather than serving a
    stale cache.
  - `match_person` still draws exactly ONE weighted value per call under either edge set, so the
    shared completion RNG stream consumes the same number of values with the flag on or off; the
    weekend match and member completion are unaffected.
  - `weekend_plan_match._age_band` became public as `age_band_index(ages, edges=...)`, because
    `diary_plan_match`'s crossing counter needs the same banding and a cross-module private import
    would be worse. `member_completion` keeps its own, separate `_age_band`.
  - Two new report fields and two new `set_info` keys
    (`diary_plan_match_remapped_in_split_child_band`,
    `diary_plan_match_crossed_fine_child_age_band`) reach the run info.

- **Assumptions (explicit):**

  - ASSUMPTION: 6-9 vs 10-13 is the behaviourally relevant cut inside the 6-13 band. It is
    motivated by the measured early-departure gap (5.4 % vs 18.2 %) and by the German school
    system (Grundschule ends after grade 4, i.e. at about age 10), not by a committed reference
    table of trip lengths by single year of age. No such table is in the repository.
  - Because `age_band` remains a SOFT key, a crossing is a legitimate ladder relaxation, NOT a
    defect. Unlike ADR-0109's employment counter, the fine-band counter is therefore NOT required
    to be 0 with the flag on, and **no threshold is asserted** -- asserting one would require a
    reference for how often the ladder should relax, which does not exist. The comparison is the
    A/B, not a bound.

- **Evidence:** issue **#386**; the raw-MiD measurement recorded in its Finding section
  (2026-09-09); the implementation in `braunschweig/popsim/weekend_plan_match.py`
  (`FINE_CHILD_AGE_BAND_EDGES`, `age_band_index`, `match_person(age_band_edges=...)`),
  `braunschweig/popsim/diary_plan_match.py` (`SPLIT_CHILD_AGE_RANGE`,
  `_count_fine_child_band_crossings`, the `fine_child_age_bands` parameter) and
  `braunschweig/popsim/completed_donor.py` (flag threading, `configure`, `execute`, `set_info`);
  the tests listed in `docs/registry/features/diary_plan_match.yml`. Related: ADR-0106 (realisable
  plan sources), ADR-0109 (the un-relaxable employment key this mirrors), ADR-0114 (the
  departure-time model that subsumes the timing half of the defect).
