# ADR-0107 · 2026-09-06 · rbW legs are movement within the work activity, not trips of the day plan
- **Status:** active
- **Numbering:** ADR-0107 is the next free id after ADR-0106, allocated in the same 2026-09-06
  sweep (main checkout, every local worktree, the remote default branch, and the file lists of
  all open pull requests; highest id found anywhere ADR-0105). Ids are append-only.
- **Context:** MiD 2023 records a *regelmaessiger beruflicher Weg* (rbW) differently from every
  other leg. It does not come from the diary at all: the respondent answers an AGGREGATE question
  (how many such legs, how many kilometres), and MiD expands that answer into individual Wege
  rows flagged `W_RBW == 1`, carrying purpose code `701`, **without times**, appended after the
  person's last directly recorded leg -- in **100 %** of cases, measured on the pool this project
  uses. MiD itself excludes them from `anzwege1`, the trip count this project's `trip_class`
  PopulationSim control is built on.

  In the i329 100 % run these legs were realised as ordinary work trips. Measured
  (boundary measurement 2026-09-05, this branch's design spec):
  - **122,852** rbW legs = **3.6 %** of all trips, **26.4 %** of all work trips, and **71 %** of
    all legs with `W_ZWECK == 2`.
  - The pipeline IMPUTED departure times for them because MiD supplies none: **22 %** start after
    27:00 (i.e. after 03:00 of the following day), the rest spread over the afternoon and night.
    They were then routed to the person's fixed workplace.
  - **6.7 %** of mobile persons carry them (mean 2.1 legs, median 17 km). Of those persons,
    **72.4 %** also have a directly recorded work trip, **10.8 %** have direct legs but no work
    trip, and **16.8 %** have ONLY rbW legs (`mobil_diff == 2`, `anzwege1 == 0`) -- 1.6 % of all
    weekday donors.
  - Because MiD excludes them from `anzwege1`, the PopulationSim seed and the realised plan
    disagreed **by construction**: the seed counted the day without them, the plan realised the
    day with them.
  - Their share of the two worst plan-structure deviations against SrV 2023 is large: of the
    122,651 work activities shorter than 2 h, **57.9 %** are rbW legs (and 53.6 % precede a
    synthetic home closure -- ADR-0108); removing both leaves 13.3 % of work activities under 2 h
    (SrV 7.4 %) at a mean duration of 6.11 h (SrV 6.29 h). The 7.4 % is an ad-hoc boundary-
    measurement figure and differs from the committed reference column
    `share_work_activities_lt_2h` (0.1096); ADR-0108 records which number is which and that only
    the committed one is a reference.

  How the reference implementation handles the same thing (eqasim-france, read 2026-09-05):
  ENTD codes 9.91-9.96 (fixed workplace, work elsewhere, training, **tournee**, other
  professional) ALL map to `work`, and a tournee is **one** trip record carrying a stop count and
  a total distance (`V2_MNBARRETT`, `V2_MDISTT`); EGT codes 2 and 3 and EDGT codes 11-13 and 81
  (tournee professionnelle, one record with `D6` stops) likewise map to `work`. The French
  surveys collapse the professional round into the work day BEFORE eqasim sees it; MiD does the
  opposite and hands eqasim the exploded legs. There is **no `business` purpose anywhere in
  eqasim**.
- **Decision:**
  - **Convention: a regelmaessiger beruflicher Weg is not a trip of the person's day plan.** The
    round happens INSIDE the work activity, which is how ENTD/EGT/EDGT already record it. Rows
    with `W_RBW == 1` are dropped in `braunschweig.popsim.trips.build_trip_table` BEFORE purpose
    mapping and validation. The column is REQUIRED (a missing `W_RBW` fails fast with a message
    naming it), never silently assumed absent.
  - **The information is kept, not discarded.** Every person carries `rbw_legs_count` and
    `rbw_distance_km` (the sum of `wegkm_imp` over the plan source's rbW legs), derived from the
    plan-source diary facts of ADR-0106, exported as optional columns of `persons.csv` and as
    MATSim person attributes `rbwLegsCount` (Integer) / `rbwDistanceKm` (Double).
  - **A person without a MiD plan source gets NO such attribute** -- not a zero. Cordon
    in-commuters have no plan source, and writing `0` would encode "this person makes no
    professional rounds" where the truth is "we do not know". MATSim person attributes are
    optional per person; the writer logs the omission rate once per population.
  - **Nothing in this project consumes the attributes.** They exist so that a future
    business-traffic module can, per the user's decision that business traffic is a SEPARATE
    project (**#371**).
  - **Interaction with ADR-0106:** a donor whose only legs are rbW legs would become trip-less by
    this drop. Those sources are remapped upstream by the diary plan match, so the count of
    persons emptied by the drop must be 0 when both flags are ON; the builder counts it, logs it
    and warns when it is not.
  - **Flag** `braunschweig.population.popsim.exclude_rbw_legs`, default `true`, read by
    `braunschweig.popsim.trips_stage` (the stage behind the `synthesis.population.trips` alias).
    MiD-only: `EntdSource.build_trips` REJECTS a non-default value with a message naming the key,
    because ENTD has no `W_RBW` pendant; the two popsim_open fixture configs set it to `false`
    explicitly.
  - **Observability:** dropped count and share, plus the number of donor persons emptied by the
    drop, are logged under the marker `[popsim.trips] rbW legs dropped`.
- **Rationale and rejected alternatives:**
  - **Give rbW legs their own `business` purpose now.** Rejected. It would add a purpose that
    exists nowhere in eqasim (no scoring parameter, no location-choice pool, no mode-choice
    treatment, no SrV target to compare against), for legs that carry no observed times and whose
    destinations MiD does not record -- i.e. a fully fabricated activity chain. The user's
    decision is that business traffic is its own project (#371); until it exists, the honest
    representation of an untimed aggregate round is "it happens inside the work activity", which
    is also what the French surveys deliver.
  - **Keep the legs and impute better times.** Rejected: the times are not merely poorly
    imputed, they are ABSENT in the source, and no imputation makes a leg with an unobserved
    destination a defensible element of a daily plan. Note that this decision also removes most
    of the plan-validation NaN-time population (122k persons), so the `time_imputation` path
    becomes rare; it is kept, and its rate is logged, rather than deleted.
  - **Drop them and also drop the persons who then have nothing left.** Rejected: those persons
    were demonstrably mobile, and MiD's own `anzwege1 == 0` for them is an artefact of the same
    exclusion this record adopts. They are remapped to a realisable diary by ADR-0106 instead. On
    the target side the SrV DOES count professional trips, so the replacement day and the target
    agree; this is stated as an explicit assumption, not as a measured equivalence.
- **Consequences:**
  - The number of realised work trips falls and the work-activity duration distribution shifts;
    the expected directions (work->work repeats towards the SrV 4.5 % same-purpose share, 1-2 h
    work activities from 19.5 % towards single digits, night-time work departures vanishing) are
    **ASSUMPTIONS** pre-registered in this branch's A/B ladder, NOT measurements.
  - Anything measured on the previous population that counted rbW legs as work trips is affected:
    the commute-distance baselines (#357-#359, ADR-0103) and the BA-flow calibration must be
    re-measured after the ladder, because more employed persons with a genuine work trip means
    more workplace assignments.
  - The Phase B home-office donor pool (`braunschweig/synthesis/commute_day/donor_pool.py`) still
    keeps rbW legs; adopting this flag there is a REQUIRED follow-up on **#244**, in that
    branch, after it merges.
  - `persons.csv` gains two optional columns and the MATSim population two optional attributes;
    both are additive and absent on every non-MiD path.
- **Evidence:** issue **#366**; branch `feature/plan-structure-fix` (commits `ec500224` +
  `8b889e9b` the leg drop, `f1509186` + `84ba1026` the person attributes);
  `braunschweig/popsim/trips.py`, `braunschweig/popsim/enriched_adapter.py`,
  `synthesis/output.py`, `braunschweig/matsim/scenario/population.py`; tests
  `tests/test_popsim_trips_rbw.py`, `tests/test_rbw_attributes_output.py`,
  `tests/test_popsim_trips_stage.py`; feature record
  `docs/registry/features/rbw_leg_convention.yml`. The eqasim-france mappings above were read in
  the upstream eqasim source on 2026-09-05 (ENTD `data/hts/entd/cleaned.py`, EGT and EDGT
  equivalents); the MiD side is the codebook package recorded in
  `docs/registry/data/mid2023_b1.yml`. The model-side counts are the i329 production run
  `docs/runs/100pct-allfeat-i329-2026-08-24.yml` with the 2026-09-05 boundary measurement; the
  SrV comparison values come from the committed
  `eqasim-data/data/braunschweig/srv/srv2023_plan_structure_reference.csv`. ADR-0106 (realisable
  plan sources) and ADR-0108 (closed plans) are the two records this one composes with. **No A/B
  arm has run: no claim here is validated.**
