# ADR-0117 · 2026-09-11 · A survey non-answer never becomes a modelled category or a distance
- **Status:** active
- **Numbering:** ADR-0117 is the next free id after ADR-0116. Verified on 2026-09-11 with the check
  ADR-0111's Numbering paragraph records: `git fetch origin`, then
  `git for-each-ref --format='%(refname)' refs/remotes/origin refs/heads` (56 refs), each ref's
  decision tree listed with `git ls-tree -r --name-only <ref> -- docs/decisions` and grepped for
  `ADR-01[0-9][0-9]`. The highest id on any ref is ADR-0116, carried by this branch
  `feature/i373-purpose-correctness`; `origin/main` is at ADR-0110. Ids are append-only.
- **Context:** The owner asked, after ADR-0113 turned the two MiD W_ZWD no-detail codes into
  sentinels, whether a survey "keine Angabe" still reaches the model ANYWHERE, with the standing
  instruction to exclude every occurrence and let the remaining probabilities renormalise. A
  sweep of `braunschweig/` (2026-09-11; the analysis package and the tests excluded) looked at
  every categorical draw the model makes, every continuous distribution it builds and every
  control target it is fitted to. Most of the model already does the right thing, and two things
  do not.

  **What is already correct** (recorded so a later reader does not re-open it):
  `braunschweig.popsim.missing.resolve` is the project's uniform policy for person attributes --
  a structural code gets a deterministic value, an item non-response is IMPUTED from the valid
  pool of the same conditioning group, and an unenumerated code RAISES; no "unknown" category is
  ever assigned, across all 13 specs in `attributes.py`. The W_ZWD subtype models exclude their
  sentinels from numerator AND denominator, and the family is complete: 599 "Einkauf k.A."
  (`shop_subtype.SHOP_DETAIL_MISSING`, since #127), 699 "Erledigung k.A." and 799 "Freizeit k.A."
  (`purpose_subtype`, ADR-0113). The SrV escort destination weights exclude invalid `V_ZWECK_BHOL`
  legs and disclose the coverage (98.79 %); the SrV location-type draw excludes invalid mode and
  invalid length with their rates in the committed header; no committed control target carries an
  unknown category; `srv_participation_universe` RAISES rather than keep a person whose
  reporting-day state cannot be read.

  **Defect 1 -- a non-answer used as a purpose.** MiD `W_ZWECK` 99 is "keine Angabe": the
  respondent travelled but did not say why. `trips.PURPOSE_BY_W_ZWECK` maps it to the eqasim
  purpose `"other"`, which is right for the PLAN (a trip happened and it must have a purpose) but
  wrong for every ESTIMATION built on that purpose. Measured on the 2026-09 MiD 2023 B1 delivery
  (ad-hoc, 2026-09-11, not reproduced by committed code): 6,029 legs carry the code, 4,271 of them
  in the weekday diary universe -- 0.58 % of its legs, but **2.80 % of everything the model calls
  "other"**. They were feeding the `"other"` distance pool and, through
  `deciders._build_other_subtype_decider`, the coarse errand/rest split, whose local
  `SubtypeSpec` was built with `sentinels=frozenset()` while its two siblings in the same file
  correctly reuse the sentinel-carrying specs of `purpose_subtype`.

  **Defect 2 -- a design code that would become a 7,688 km leg.** Four sites turn MiD
  `wegkm_imp` into a distance with no guard: `distance_distributions.run` Step 4 (every layer),
  `trips_stage` (the `euclidean_distance` column of the trips CONTRACT),
  `commute_day.donor_pool.donor_trips` (values copied verbatim onto spliced rows) and, through the
  contract column, `popsim.commute_distance` (a person's own commute distance AND the pool other
  persons' missing distances are drawn from). MiD codes item non-response NUMERICALLY (>= 9994:
  "unplausibel", "keine Angabe"), so such a value would enter a cumulative distribution as a
  7,688 km straight-line leg and be drawable. The sibling modules `diary_facts` and
  `time_imputation` guard the very same columns against the very same threshold.
  **On the current delivery this is latent, not live**: measured over all 1,087,393 rows on
  2026-09-11, `wegkm_imp` has no NaN, no value <= 0, no value >= 9994, and a maximum of 950 km, so
  zero such values exist in any of the 16 committed layers.
- **Decision:** A non-answer is excluded from every ESTIMATION and can never enter a distance;
  the PLAN keeps the trip.

  1. **`exclude_no_answer_purpose_legs`** (single home
     `config_keys.KEY_EXCLUDE_NO_ANSWER_PURPOSE_LEGS` / `DEFAULT_... = True`, production `true`).
     `trips.W_ZWECK_NO_ANSWER_CODE = 99` names the code once. Declared by BOTH consumer stages and
     read with the one-argument execute form; they must resolve the same value, because one labels
     a leg and the other supplies that label's donor pool.
     - `distance_distributions.run(..., exclude_no_answer_purpose=False)` drops those legs in a
       new **Step 5b** -- AFTER the chain derivation of Step 2 and the primary-both filter of
       Step 5, so no other leg's `preceding_purpose` changes -- with the rate logged. The same step
       drops legs whose distance is missing, for the separate reason that a leg without a length
       cannot contribute one, and raises if nothing survives.
     - `deciders._build_other_subtype_decider` moves the code into the coarse spec's SENTINELS and
       out of the `"rest"` group in the same breath (`SubtypeSpec` refuses a code that is both),
       so errand/escort/rest renormalise over the legs whose purpose is known.
  2. **The trip build is deliberately NOT changed.** A W_ZWECK-99 leg stays an `"other"` trip in
     the plan. Deleting it would remove a trip the person actually made; imputing a purpose would
     fabricate behaviour that the survey explicitly does not report. The honest position is: the
     trip exists, its purpose is unknown, and nothing is estimated from it.
  3. **`diary_facts.validate_trip_length_km(values, *, log_tag)`** is the one guard every consumer
     that turns a trip-length column into a distance now calls (`distance_distributions` Step 4,
     `trips_stage`, `commute_day.donor_pool`). It separates two classes that must not be treated
     alike: an IMPOSSIBLE value (a design code >= `WEGKM_CODE_MIN`, or a non-positive length)
     RAISES naming the caller and the counts, because no delivery can legitimately contain one in
     a distance column; a MISSING value is COUNTED and logged and passed through, because it is
     structural -- the closure/dwell synthesis adds legs that were never surveyed and therefore
     carry no `wegkm_imp`. `popsim.commute_distance` is not a fourth call site: it consumes the
     contract column the guard already protects, and its own NaN imputation is counted and logged.
- **Rejected alternatives:**
  - **Drop the W_ZWECK-99 legs from the trip build as well (rejected).** They are real trips; the
    person's trip count and chain would change on the strength of a missing answer, and the
    synthetic day would lose mobility the survey observed.
  - **Impute a purpose for them from comparable legs, as `missing.resolve` does for person
    attributes (rejected here, deliberately).** It is defensible and it is what the project does
    one level up, but it would invent a purpose for 0.58 % of legs and change the plan, so it
    needs its own flag, its own arm and its own before/after measurement. Excluding them from the
    ESTIMATIONS is the part that is unambiguously right, and it is what this record does. If the
    owner later wants the imputation, this decision does not block it.
  - **Make the trip-length guard exclude instead of raise (rejected for the impossible class).**
    Two of its three call sites build a CONTRACT column or a donor pool whose caller cannot learn
    that rows went missing; a silent shortening is exactly the failure class the guard exists to
    prevent. ASSUMPTION: if a future delivery legitimately carries a small share of coded lengths,
    the policy becomes exclude-and-log with a rate, decided then.
  - **Guard `wegkm_imp` only where a distribution is built (rejected).** The contract column feeds
    consumers this repository cannot enumerate from one call site; the guard belongs at every
    place that CREATES the distance.
  - **Treat SrV `V_ZWECK` 70 "Sonstiges", MiD W_ZWD 720 "sonstiger Freizeitzweck" or the drawable
    `leisure_misc` / `other_misc` location categories the same way (rejected).** They are
    substantive residual ANSWERS ("something else"), not refusals. Excluding them would delete
    reported behaviour.
- **Consequences:**
  - **Measured effect of Decision 1** (ad-hoc, 2026-09-11, production flag set, raw delivery; the
    layer means are `wegkm_imp`-equivalent km clipped at 200):

    | quantity | before | after |
    |---|---|---|
    | `"other"` distance pool, legs | 72,183 | 67,912 |
    | `"other"` distance pool, mean | 9.062 km | 8.863 km |
    | coarse other split, `errand` | 0.5304 | 0.5540 |
    | coarse other split, `rest` | 0.4696 | 0.4460 |

    No other layer changes at all -- W_ZWECK 99 is the only no-answer purpose code, and it maps to
    `"other"` alone. The errand/rest shift of **2.36 pp** is the size of the correction: that much
    of what the model treated as "some other errand-ish purpose" was a refusal to answer.
  - **Decision 3 is byte-identical on the current delivery** (0 impossible values measured), so no
    layer, no contract column and no donor pool changes because of it. It is a guard against a
    future delivery, and the three call sites now fail loudly instead of silently.
  - **Cache devalidation** is the usual pair plus the trip build: `distance_distributions` and
    `secondary.locations` recompute (both declare the new key), and `synthesis.population.trips`
    recomputes because `trips.py` and `diary_facts.py` are inside its `validate()` token. No seed
    column changes, so PopulationSim does not re-run.
  - **Pre-registered A/B, arm E** = arm D + `exclude_no_answer_purpose_legs` (NOTHING has run).

    | metric | baseline | reference | expected in arm E (ASSUMPTION) |
    |---|---|---|---|
    | realised `other_errand_*` vs `other_rest` split | arm D | the decider's own printed coarse marginal | ASSUMPTION: errand rises by about 2.4 pp, matching the donor-side shift above |
    | realised `"other"` activity distances | arm D | the donor pool mean above | ASSUMPTION: the median moves DOWN, direction only |
    | every non-`"other"` purpose | arm D | -- | ASSUMPTION: unchanged, since no other purpose contains the code |

    Every "expected" cell is an ASSUMPTION until a run manifest records it; the feature record
    `docs/registry/features/no_answer_codes_excluded.yml` stays `validation.state: unvalidated`.
  - **Two smaller instances of the same pattern were MEASURED and left for the owner** rather than
    changed here, because both would move a committed reference or control target rather than an
    estimation, and both are an order of magnitude smaller than Decision 1:
    `calibration.commute_day_state_reference` keeps a `"missing"` state in the denominator of the
    workday-location shares (0.15 % of the mass overall, at most 0.82 % in one distance class;
    `share_at_workplace` would move 0.5209 -> about 0.5217), and `srv_participation_universe`
    classes a person with a missing `V_ERW` as NOT employed (9 persons, 0.098 % weighted; the
    employed control target would move 53.566 % -> 53.619 %). Both are disclosed in their own
    committed headers today. They are recorded here so the sweep's result is complete and the two
    are not re-discovered as new.
- **Assumptions (explicit):**
  1. **`W_ZWECK` 99 is the only no-answer code in the MiD purpose vocabulary** (MiD 2023 Handbuch
     Tab. 2, the 9/99/999 convention; `PURPOSE_BY_W_ZWECK` enumerates every other delivered code).
  2. **A leg whose purpose is unknown is still a real trip.** The plan keeps it; only the
     estimations drop it. If that turns out to bias the `"other"` share of the synthetic day
     itself, the imputation alternative above is the next step, not a revision of this one.
  3. **The current delivery's `wegkm_imp` is clean** (measured), so Decision 3 changes nothing
     today; the guard is a claim about future deliveries, not about this one.
  4. **Nothing has run:** every arm-E value is an ASSUMPTION until a run manifest records it.
- **Evidence:** issue **#373** (no separate issue: owner decision of 2026-09-10, "keine neuen
  Issues"); **ADR-0113** (the W_ZWD sentinels this completes), **ADR-0115** (the fifth leisure
  subtype, whose `"other"`-side sibling this is), **ADR-0116** (the weekday universe, the other
  filter applied to the same donor frame), **ADR-0091** (`explicit_round_trip_purposes`, which
  removed the SILENT fallback into `"other"` -- this record removes the non-answer that remained
  in it). Code: `braunschweig/popsim/trips.py` (`W_ZWECK_NO_ANSWER_CODE`),
  `braunschweig/popsim/diary_facts.py` (`validate_trip_length_km`),
  `braunschweig/popsim/stage/config_keys.py`, `braunschweig/popsim/distance_distributions.py`
  (Step 4 guard, Step 5b, `configure`, `execute`), `braunschweig/popsim/trips_stage.py`,
  `braunschweig/synthesis/commute_day/donor_pool.py`,
  `braunschweig/synthesis/locations/secondary_chainsolvers/{__init__,deciders}.py`. Tests
  `tests/test_diary_facts.py` (the guard's four cases),
  `tests/test_distance_distributions_subtypes.py` (excluded from every pool, kept when off, the
  other pools untouched, the logged rate), `tests/test_secondary_chainsolvers_subtypes.py` (the
  coarse split with and without the code, the shared default),
  `tests/test_configs_composed.py` (the production flag cannot disappear),
  `tests/test_escort_chainsolvers.py`. Feature record
  `docs/registry/features/no_answer_codes_excluded.yml`; stage records
  `synthesis.population.spatial.secondary.distance_distributions`,
  `synthesis.population.spatial.secondary.locations`, `synthesis.population.trips`; config
  `configs/base_bs.yml` and the README flag table; contributor note
  `docs/codebase/notes/mid-purpose-mapping.md`.
  **No run has executed this code as of this record.**
