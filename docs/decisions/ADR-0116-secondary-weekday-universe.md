# ADR-0116 · 2026-09-10 · The secondary distance layers and the MiD subtype deciders estimate on the weekday diary universe
- **Status:** active
- **Numbering:** ADR-0116 is the next free id after ADR-0115. Verified on 2026-09-10 before this
  record was written, by the check ADR-0111's, ADR-0113's and ADR-0115's Numbering paragraphs
  record: `git fetch origin`, then `git for-each-ref --format='%(refname)' refs/remotes/origin
  refs/heads` (52 refs -- 19 under `refs/remotes/origin`, including `origin/HEAD`, and 33 local
  `refs/heads`, i.e. every worktree branch of this checkout), each ref's decision tree listed with
  `git ls-tree -r --name-only <ref> -- docs/decisions` and grepped for `ADR-01[0-9][0-9]`. The
  highest id found on any ref is **ADR-0115**, carried by exactly one ref -- this branch
  `feature/i373-purpose-correctness` -- with **ADR-0114** on the local head
  `feature/i123-departure-time-model` (sibling branch A of this wave) and `origin/main` at
  ADR-0110, so 0116 is free everywhere. The `ADR-0116` strings that were already in the working
  tree before this file existed are the forward references Task 1 of this plan placed in the code,
  the tests, the two extraction scripts and the regenerated reference header -- 35 occurrences in
  17 files (grep 2026-09-10; the files are listed in Evidence). Ids are append-only, so a
  colliding draft on a sibling branch is renumbered, never this one.
- **Context:** The synthetic population is ONE WEEKDAY. The PopulationSim seed, the plan donors and
  every committed MiD reference table are built from MiD diaries whose reporting day is in the
  seed's day filter (`braunschweig.popsim.seed.MID_SEED_COLUMNS.day_filter_values`) and without the
  rbW summary records (`W_RBW == 1`, `braunschweig.popsim.trips.rbw_leg_mask`). Two stages did not
  share that universe:

  - `braunschweig.popsim.distance_distributions.run` (stage
    `synthesis.population.spatial.secondary.distance_distributions`) built EVERY secondary distance
    layer -- the mode-only, the per-purpose and all subtype layers (shop daily/non-daily, leisure
    local/visit/activity/excursion/unspecified, other errand short/long, `other_escort`) -- from
    every delivered MiD Wege row (`mid.load_mid_wege`: no day filter, rbW summary records
    included).
  - The three MiD-based deciders in
    `braunschweig.synthesis.locations.secondary_chainsolvers.deciders`
    (`_build_shop_subtype_decider`, `_build_leisure_subtype_decider`,
    `_build_other_subtype_decider`) estimated `P(group | mode, tt_band)` on the same unfiltered
    frame.

  **Size of the universe difference, from the committed
  `eqasim-data/data/braunschweig/mid/mid2023_w_zwd_group_reference.csv` header** (data record
  `mid2023_w_zwd_group_reference`): `n_legs_raw = 1087393` delivered Wege rows, of which
  `n_legs_after_weekday_rbw = 738546` (67.92 %) are weekday non-rbW legs. The two drop reasons come
  from the new helper's own INFO line on the same 2026-09-10 delivery (AD-HOC MEASUREMENT, Task 1
  of this plan, not reproduced by committed code): `kept 738546/1087393 legs (67.9%); dropped
  251215 non-weekday (kernwo outside [1, 2, 3]), 97632 rbW summary records`. After `run()`'s own
  primary-both filter the layers are built from 527,108 weekday legs instead of 749,634 all-day
  legs (AD-HOC, `run()`'s own Step-5 log, same measurement).

  **What that did to the estimated mix** (AD-HOC PROBES of 2026-09-10 -- the final review of the
  ADR-0115 plan and Task 1 of this plan; each reproduced twice, none reproduced by committed code,
  except where a COMMITTED row is named):

  - production `leisure_unspecified` marginal **0.3867 on the all-day estimation universe** against
    the committed weekday reference row **0.4324** (committed row
    `leisure,codeplan_unspecified,leisure_unspecified`).
  - `leisure_activity` under the `codeplan` composition **0.3930 all-day** against the committed
    weekday row **0.4253** (-3.22 pp); `leisure_visit` +1.80 pp, `leisure_local` +1.05 pp and
    `leisure_excursion` +0.38 pp in the same direction (ADR-0113 Assumption 3 carries the same four
    figures).
  - the `leisure_unspecified` donor mean **12.7 km all-day (n = 54,658)** against **12.0 km weekday
    (n = 39,429)**.

  Weekend leisure has more excursions and visits and longer distances, so a Tuesday plan drew its
  leisure subtypes and its desired distances partly from Sunday behaviour. The defect is
  PRE-EXISTING -- the #127 layers and deciders were never day-filtered -- and became visible only
  once ADR-0113/ADR-0115 committed a weekday reference table next to them; both records state it as
  their "Two universes" consequence and neither changed it.
- **Decision:** Both stages estimate on the WEEKDAY DIARY universe, defined once, behind one config
  key.

  1. **One universe function**, in `braunschweig/popsim/trips.py` next to the existing
     `rbw_leg_mask` / `legs_kept_by_the_trip_build`:
     `weekday_diary_leg_mask(wege, *, kernwo_col="kernwo") -> pd.Series` is
     `kernwo in WEEKDAY_DIARY_KERNWO` AND NOT `rbw_leg_mask(wege)`, and
     `WEEKDAY_DIARY_KERNWO = tuple(MID_SEED_COLUMNS.day_filter_values)` is READ from the seed, not
     re-typed, so the model cannot end up with two weekday definitions. `WEEKDAY_DIARY_COLUMNS`
     names the two columns the universe needs. The mask RAISES `ValueError` naming the missing
     column when `kernwo` or `W_RBW` is absent -- a universe must never be applied silently to a
     frame that cannot express it, because a missing `kernwo` would read as "every leg is a weekday
     leg". `kernwo` is coerced with `errors="coerce"` and an uncoercible value counts as NOT
     weekday (a leg whose reporting day cannot be read must not enter a weekday universe). The
     companion `restrict_to_weekday_diary_legs(wege, *, log_tag)` applies the mask, logs the kept
     rate with BOTH drop reasons at INFO and RAISES when nothing is kept. The two reasons PARTITION
     the dropped legs (`kept + non-weekday + rbW == total`, pinned by a test), so the log line
     reads as a decomposition rather than as two overlapping counts.
  2. **The two committed reference extractions use the same function.**
     `scripts/extract_mid_w_zwd_groups.filter_weekday_legs` is
     `frame[weekday_diary_leg_mask(frame)]` after its numeric coercion, and
     `scripts/extract_mid_w_zweck_hwzweck1.py` imports `WEEKDAY_DIARY_KERNWO` / `RBW_LEG_FLAG` /
     the mask and keeps `KERNWO_WEEKDAY_CODES` / `RBW_SUMMARY_LEG_CODE` as aliases of the trips
     constants (so the sibling script's import of both names is unchanged). Their diagnostics,
     invalid-weight raise and coercion logging are untouched.
  3. **Config key**, single home `braunschweig/popsim/stage/config_keys.py`:
     `KEY_SECONDARY_MID_WEEKDAY_LEGS_ONLY = "secondary_mid_weekday_legs_only"` /
     `DEFAULT_SECONDARY_MID_WEEKDAY_LEGS_ONLY = True`, production `true` in `configs/base_bs.yml`.
     Declared in BOTH stages' `configure()` from that one constant pair and read with the
     one-argument execute-context form. Both stages must resolve the SAME value: the decider labels
     a leg and the layer supplies that label's donor pool, so a configuration in which they
     disagreed would pair a label from one universe with a pool from the other. NOT in
     `ENTD_REJECTED_KEYS` -- `popsim_open` keeps the ENTD distance CDFs and never estimates on MiD,
     so the key is inert there (the same reasoning as `KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS`,
     ADR-0113, and `KEY_LEISURE_UNSPECIFIED_SUBTYPE`, ADR-0115).
  4. **Where it applies.** `distance_distributions.run(..., weekday_legs_only: bool = False)`
     applies the helper in a new **Step 0a**, immediately after `df = mid_wege.copy()` and BEFORE
     the pairing mask of Step 0b and the purpose mapping of Step 1 -- so the aggregate, the
     per-purpose and every subtype layer are built from ONE universe rather than a half-filtered
     mixture. `kernwo` / `W_RBW` are deliberately NOT in `REQUIRED_COLUMNS` (they are needed only
     on this path). The three decider builders apply `restrict_to_weekday_diary_legs` right after
     `load_mid_wege` -- in `_build_shop_subtype_decider`'s ESTIMATION branch only (the pinned-share
     branch loads no MiD frame), in `_build_leisure_subtype_decider` before `code_coverage_guard`
     and the estimation, and in `_build_other_subtype_decider` before `map_mode`, so BOTH of its
     composed estimation stages read the same frame. The SrV escort-location decider and the SrV
     location-type decider are not MiD estimations and are untouched.
  5. **The sanity pins are re-measured on the new frame.** Every entry of
     `braunschweig.calibration.secondary_measurement.SUBTYPE_DONOR_MEAN_KM_RANGE` is a POINT pin
     `(m, m)` measured on the frame the LAYER is built from with the flag ON, and the code comment
     names the frame, the route and the date. The previous values and the all-day measurements are
     listed in Consequences below, so the OFF path is documented rather than lost.
- **Rejected alternatives:**
  - **A weekday filter without the rbW exclusion (rejected).** The plans never contain rbW summary
    records (`exclude_rbw_legs: true` in production) and both committed reference tables exclude
    them, so keeping them in the layers would leave a second universe difference in place. One
    function, one flag (controller ruling W1 of this plan).
  - **Filter only the subtype layers / the deciders, not the aggregate and per-purpose layers
    (rejected).** The whole stage describes one weekday; a half-filtered stage would make the
    FALLBACK layer (aggregate leisure) and its subtypes disagree about the universe, which is the
    same defect one level down.
  - **Keep the all-day estimation and document it -- the status quo after ADR-0115 (rejected).**
    Rejected by the owner on 2026-09-10: a weekday plan must not draw its leisure types and
    distances from Sunday diaries. Documenting a known universe mismatch is not a substitute for
    removing it when the fix is a filter.
  - **Reuse `exclude_rbw_legs` to control the rbW half (rejected).** That key describes the TRIP
    BUILD; coupling the estimation universe to it would make the estimation change whenever the
    trip build's leg drop is toggled for an unrelated reason.
  - **Route `scripts/derive_escort_w_zweck_split.py` through the same helper (rejected here, out of
    scope).** Its share table is measured on a day-filter-only universe (no rbW exclusion) and its
    header says so; moving it would change a committed reference table ADR-0112 depends on. It is
    the last committed MiD Wege aggregate whose universe is not the shared helper's, recorded here
    rather than silently left unmentioned.
- **Consequences:**
  - **The "Two universes" finding of ADR-0113 / ADR-0115 is CLOSED by this record.** With
    `secondary_mid_weekday_legs_only` on, the estimation and the committed reference share
    `braunschweig.popsim.trips.weekday_diary_leg_mask`; the residual differences are `run()`'s own
    validity filters (a usable travel time, and the two leg ends not both primary activities), not
    the universe. The closure is recorded by reference in ADR-0113 (Assumption 3 and the arm rows),
    ADR-0115 (Consequences, Decision 9 and the arm rows), the feature records
    `leisure_unspecified_subtype` and `w_zwd_codeplan_sentinels`, the data record
    `mid2023_w_zwd_group_reference` and the contributor note
    `docs/codebase/notes/mid-purpose-mapping.md`. With the flag OFF the old mismatch is back, and
    every one of those records says so.
  - **Cache devalidation is CHEAP and bounded to the same two stages** as ADR-0113 and ADR-0115:
    `synthesis.population.spatial.secondary.distance_distributions` and
    `synthesis.population.spatial.secondary.locations` recompute -- each declares the new key, and
    both already hash `braunschweig.popsim.trips` in their `validate()` helper-module tuple, so the
    new helper itself is covered. **`braunschweig.popsim.seed` was added to both tokens** (the
    final review of Task 1, controller ruling W4): the weekday DEFINITION lives there
    (`WEEKDAY_DIARY_KERNWO` reads `MID_SEED_COLUMNS.day_filter_values`), and `inspect.getsource`
    of `trips` cannot see a change on the other side of that import, so without the entry an edit
    to the seed's day filter would have served stale layers and stale deciders. Two tests in
    `tests/test_synpp_helper_hash_invariant.py` perturb the seed module's source and assert each
    stage's token changes. PopulationSim and the trip build are NOT affected:
    no seed column changes and no trip-build flag is read differently, which is what makes the A/B
    arm cheap.
  - **Step 0a also changes which trips Step 5 excludes, because it filters BEFORE the chain is
    re-derived** (final review of Task 1, ruling W5). `run()` derives `preceding_purpose` in
    Step 2 (`_build_preceding_purpose`) from the legs that are still in the frame, per
    `(H_ID, P_ID)` chain in `W_ID` order and with the diary-starts-at-home convention; Step 5 then
    excludes the trips whose BOTH ends are primary activities. So a chain that contained a dropped
    leg -- a weekend leg or an rbW summary record, the latter sitting INSIDE an otherwise kept
    diary -- has its preceding purposes re-derived from the remaining legs, and the set of
    primary-both trips excluded in Step 5 can change with it. This is not a side effect to be
    argued away: with `exclude_rbw_legs: true` (production) the trip build drops the very same rbW
    records from the plans, so re-deriving the chain without them moves the distance donor pool
    CLOSER to the plan's own leg universe rather than away from it. The same statement is carried
    by the Step 0a code comment, so the ordering cannot be changed without meeting the reasoning.
    No number is attached to it: nothing has run, and the effect is a re-derivation, not a filter
    with a countable rate.
  - **Every secondary activity's desired distance changes on the next run, and the subtype mix
    moves with it.** The re-measured donor means (AD-HOC MEASUREMENT of 2026-09-10, Task 1 of this
    plan; frame, route and flag set recorded at `SUBTYPE_DONOR_MEAN_KM_RANGE` itself):

    | group | pin BEFORE (its source) | all-day mean, n | pin NOW (weekday) | weekday mean, n |
    |---|---|---|---|---|
    | `leisure_local` | (4.0, 7.0) -- 2026-07-09 spec taxonomy | 6.1724 km, n 37,682 | (5.5, 5.5) | 5.5039 km, n 21,538 |
    | `leisure_visit` | (19.1, 19.1) -- 2026-07-09 spec taxonomy | 19.1397 km, n 15,205 | (17.1, 17.1) | 17.0599 km, n 8,003 |
    | `leisure_activity` | (10.0, 18.0) -- 2026-07-09 spec taxonomy | 12.1241 km, n 40,135 | (9.9, 9.9) | 9.8898 km, n 25,650 |
    | `leisure_excursion` | (45.0, 100.0) -- 2026-07-09 spec taxonomy | 65.9119 km, n 6,425 | (68.0, 68.0) | 68.0147 km, n 3,591 |
    | `leisure_unspecified` | (12.7, 12.7) -- ad-hoc, ADR-0115 Decision 9 | 12.6951 km, n 54,658 | (12.0, 12.0) | 12.0073 km, n 39,429 |
    | `other_errand_short` | (5.0, 9.0) -- 2026-07-09 spec taxonomy | 7.5117 km, n 10,859 | (7.6, 7.6) | 7.6347 km, n 10,257 |
    | `other_errand_long` | (11.0, 16.0) -- 2026-07-09 spec taxonomy | 11.5314 km, n 14,414 | (10.1, 10.1) | 10.1416 km, n 10,207 |
    | `other_escort` | (4.5, 8.5) -- 2026-07-09 spec taxonomy | 8.0275 km, n 41,648 | (7.0, 7.0) | 6.9831 km, n 35,288 |

    Both measured columns are AD-HOC MEASUREMENTS on the local-only raw MiD 2023 B1 Wege delivery,
    taken on 2026-09-10, NOT reproduced by committed code; the "pin BEFORE" column names each
    previous value's own source, read from
    `git show 935c2c0e:braunschweig/calibration/secondary_measurement.py`. Six of the eight
    previous pins were RANGES carried over from the 2026-07-09 issue-#127 design spec's taxonomy
    tables, so the before/after step is a pin-SOURCE change as much as a universe change -- only
    `leisure_visit` and `leisure_unspecified` were point measurements before. Direction: five group
    means fall (`leisure_activity` 12.1 -> 9.9 km, `leisure_visit` 19.1 -> 17.1, `leisure_local`
    6.2 -> 5.5, `other_errand_long` 11.5 -> 10.1, `other_escort` 8.0 -> 7.0), `leisure_excursion`
    RISES (65.9 -> 68.0) and `other_errand_short` is nearly flat (7.5 -> 7.6). These are IN-SAMPLE
    donor means and never validation gates, exactly as before. `other_escort` is the one entry not
    measured under the production flag set: with `escort_purpose` ON no `W_ZWECK`-6 leg reaches
    `following_purpose == "other"`, so the group is empty and `run()` skips its layer (its own INFO
    line says so); its pin is measured with `escort_purpose` and the two flags requiring it OFF,
    which is stated at the dict itself.
  - **Thin cells (spec Assumption 3, now quantified).** The universe is 67.9 % of the rows, so
    per-group donor n falls with it -- `leisure_excursion` 6,425 -> 3,591 and `leisure_visit`
    15,205 -> 8,003 in the table above. `secondary_distance_min_obs` (30) and the deciders'
    per-cell `min_obs` keep their values, so MORE `(mode, tt_band)` cells fall back to the marginal
    and more travel-time bins are thin. That is observable, not silent:
    `purpose_subtype.estimate_group_probabilities` logs
    `"[purpose_subtype:<purpose>] N/M (mode, tt_band) cells are below min_obs=30 and fall back to
    the marginal"` at INFO and WARNs when ALL cells are thin, and `impute_groups` logs
    `"[purpose_subtype] marginal fallback used for N/M legs (r%)"`. Arm D must record those rates.
    MEASURED before any run (AD-HOC real-data smoke of 2026-09-10 on the raw delivery, with the
    production flag set): on the weekday universe the leisure spec has **1 of 20** thin
    `(mode, tt_band)` cells and the other-errand spec **2 of 19** -- so the effect this assumption
    was about is small at the unchanged `min_obs = 30`. The same smoke reproduced every committed
    donor pin of the table above from `run()`'s own layers and found the three deciders' marginals
    EQUAL to the committed `codeplan_unspecified` reference rows to 0.000 pp, which is arm-D row 1
    verified on the survey side (the model side still needs the run).
  - **The flag's CODE default is `True`, so the weekday universe is ACTIVE from the implementing
    commit in every configuration that leaves the key unset** -- `configs/base_bs.yml` sets it
    explicitly anyway. That is the project's "new features default on" rule, and it has one
    consequence for the pre-registered A/B: **arms A, B and C must set
    `secondary_mid_weekday_legs_only: false` EXPLICITLY in their overlays**, because leaving the
    key unset now means ON. **Arm D = arm C + `secondary_mid_weekday_legs_only: true`** (or unset)
    -- the same pattern ADR-0115 states for `leisure_unspecified_subtype`. The CODE default of
    `run()`'s keyword stays `False`, so a direct caller or test that omits it keeps the pre-feature
    behaviour.
  - **Pre-registered A/B, arm D (server; NOTHING has run at the time this record is written).**

    | metric | baseline | reference | expected in arm D (ASSUMPTION) |
    |---|---|---|---|
    | the deciders' printed subtype marginals | arm C (all-day marginals) | the `codeplan_unspecified` rows of `mid2023_w_zwd_group_reference.csv` -- now the SAME leg universe, so this stops being a cross-universe comparison | ASSUMPTION: within 0.5 pp per group, the residual being `run()`'s travel-time validity and primary-both filters, which the reference does not apply |
    | realised leisure distance median | arm C | direction only | ASSUMPTION: moves DOWN versus arm C |
    | realised per-group leisure and other-errand means | arm C | `SUBTYPE_DONOR_MEAN_KM_RANGE` (the weekday point pins above; in-sample sanity, never a gate) | ASSUMPTION: at the pin, sanity check only |
    | thin-cell fallback rates of the three deciders and of the layers | arm C | the rates arm C's own log printed | ASSUMPTION: they RISE with the smaller universe; the arm-D manifest records the numbers whatever they are |

    A metric moving the wrong way stops the ladder. Every "expected" cell above is an ASSUMPTION
    until a run manifest records it; the feature record
    `docs/registry/features/secondary_mid_weekday_universe.yml` stays `validation.state:
    unvalidated` with `runs: []`.
  - **The committed reference tables did not change except in provenance.** The extractions route
    through the shared helper now, so `mid2023_w_zwd_group_reference.csv` and
    `mid2023_w_zweck_by_hwzweck1.csv` were regenerated and their DATA rows proven byte-identical
    (md5 `e3eee749df76d75a5784399db3b6a467` and `8d46b78eec40eaaf51638d5528fd5bfb`, both sides;
    Task 1 of this plan, and re-proven for the group reference when it was committed). The group
    reference was then committed once, header only, so its Universe paragraph names the helper and
    this record instead of the now-false sentence "the labelling RULE is shared; the leg UNIVERSE
    is NOT"; the derived comparison package
    (`purpose_subtype_vs_srv_2026-09-10/{comparison.csv,summary.md}`) was regenerated from the two
    committed tables for the same provenance line, its data rows likewise byte-identical (md5
    `b602ab1d0f65f29d0072959ff4a870c8`).
  - **Nothing has run.** No model output is compared anywhere in this record: every measurement
    above is a survey-side donor statistic and the arm-D table is a plan.
- **Assumptions (explicit):**
  1. **The seed's day filter is the model's definition of a weekday**
     (`MID_SEED_COLUMNS.day_filter_values`); this decision does not define its own and reads that
     constant, so the two cannot drift apart. A test pins
     `WEEKDAY_DIARY_KERNWO == seed.WEEKDAY_KERNWO`, so an edit to only one home is caught.
  2. **Weekend legs differ enough in leisure type and distance that the filter matters.** MEASURED
     for the leisure groups (the ad-hoc figures in Context and the pin table above); the shop and
     errand effects are to be MEASURED by arm D, not assumed.
  3. **Thin cells stay tolerable at the unchanged `min_obs` values.** The universe is about 68 % of
     the rows, so more cells fall back to the marginal; the existing fallback-rate logs make that
     visible and arm D records the rates. This is an assumption about the SIZE of the effect, not
     about its direction.
  4. **The rbW exclusion belongs to the weekday diary universe** (controller ruling W1): the rbW
     summary records are never realised in a plan and both committed reference tables exclude them,
     so a layer estimated with them in would describe legs the model cannot produce. If the owner
     ever wants them back, the mask splits into two flags -- the cost of being wrong here is one
     small refactor, not a data change.
  5. **Nothing has run:** every expected arm-D value is an ASSUMPTION until a run manifest records
     it.
- **Evidence:** issue **#373** (the purpose-correctness story this closes; no separate issue was
  opened -- owner decision of 2026-09-10, "implement immediately"); **ADR-0113** and **ADR-0115**
  (the "Two universes" finding this record closes, both edited in place by this wave), **ADR-0026 /
  ADR-0057** (the purpose-resolved secondary distances and the subtype split this universe now
  applies to), **ADR-0111** (`w_zweck_10_as_leisure`, without which the fifth leisure group is
  empty), **ADR-0112** (the passive-escort pairing whose candidate mask Step 0b applies right after
  Step 0a). Design spec
  `docs/superpowers/specs/2026-09-10-secondary-weekday-universe-design.md` (sections 1-5).
  Code: `braunschweig/popsim/trips.py` (`WEEKDAY_DIARY_KERNWO`, `WEEKDAY_DIARY_COLUMNS`,
  `weekday_diary_leg_mask`, `restrict_to_weekday_diary_legs`),
  `braunschweig/popsim/stage/config_keys.py` (`KEY_SECONDARY_MID_WEEKDAY_LEGS_ONLY` /
  `DEFAULT_SECONDARY_MID_WEEKDAY_LEGS_ONLY`), `braunschweig/popsim/distance_distributions.py`
  (Step 0a of `run`, `configure`, `execute`),
  `braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py` (`configure`) and
  `.../deciders.py` (`_build_shop_subtype_decider`, `_build_leisure_subtype_decider`,
  `_build_other_subtype_decider`), `braunschweig/popsim/purpose_subtype.py` (`label_legs`'s
  universe paragraph; `estimate_group_probabilities` / `impute_groups` own the fallback-rate logs),
  `braunschweig/calibration/secondary_measurement.py` (`SUBTYPE_DONOR_MEAN_KM_RANGE`),
  `scripts/extract_mid_w_zwd_groups.py`, `scripts/extract_mid_w_zweck_hwzweck1.py`,
  `scripts/validate_secondary_distances.py`. Tests `tests/test_popsim_trips.py` (the helper: the
  seed-constant pin, the mask, the missing-column raise, the text-`kernwo` coercion, the logged
  rate, the drop-reason partition), `tests/test_distance_distributions_subtypes.py` and
  `tests/test_distance_distributions_by_purpose.py` (every layer under the flag, the OFF-path
  byte-identity, the keyword default, the raise on an absent universe column),
  `tests/test_secondary_chainsolvers_subtypes.py` (per builder an ON and an OFF test, the logged
  rate `"weekday diary universe: kept 40/240"`, the pinned-share branch loading no MiD frame, and
  the declared default in BOTH stages' `configure()`), `tests/test_extract_mid_w_zwd_groups.py`
  (the extraction calls the shared helper and its result is unchanged),
  `tests/test_escort_chainsolvers.py`, `tests/test_synpp_helper_hash_invariant.py`,
  `tests/test_popsim_open_config.py`, `tests/test_popsim_config_parity.py`.
  Committed data: `eqasim-data/data/braunschweig/mid/mid2023_w_zwd_group_reference.csv` and
  `eqasim-data/data/braunschweig/mid/mid2023_w_zweck_by_hwzweck1.csv` (data records of the same
  names) plus the derived
  `eqasim-data/data/braunschweig/calibration/purpose_subtype_vs_srv_2026-09-10/{comparison.csv,summary.md}`.
  Feature record `docs/registry/features/secondary_mid_weekday_universe.yml`; stage records
  `docs/registry/stages/synthesis.population.spatial.secondary.distance_distributions.yml` and
  `docs/registry/stages/synthesis.population.spatial.secondary.locations.yml`; feature doc
  `docs/features/secondary-distances.md`; contributor note
  `docs/codebase/notes/mid-purpose-mapping.md`; config `configs/base_bs.yml`
  (`secondary_mid_weekday_legs_only: true`) and the README flag table.
  **No run has executed this code as of this record.**
