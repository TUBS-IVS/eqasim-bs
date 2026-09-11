# ADR-0111 · 2026-09-10 · MiD W_ZWECK 10 "anderer Zweck" follows MiD's own main-purpose fold
- **Status:** active
- **Numbering:** ADR-0111 is the next free id after ADR-0110. Allocated 2026-09-09 and RE-VERIFIED
  on 2026-09-10 before this record was written: `git fetch origin`, then every remote branch of
  `TUBS-IVS/eqasim-bs` (`git for-each-ref --format='%(refname:short)' refs/remotes/origin`, 21
  heads plus `origin` itself) AND every local branch of this checkout
  (`git for-each-ref refs/heads`, 36 heads incl. every worktree branch) had its `docs/decisions/`
  tree listed (`git ls-tree -r --name-only <ref> -- docs/decisions`) and grepped for `ADR-01[0-9][0-9]`:
  the highest id found anywhere is **ADR-0110** (on `origin/main`, `origin/feature/i370-general-day-absence`
  and the local branches that carry it). 0111, 0112 and 0113 are free on every ref. The working
  tree's own `ADR-0111` hits are the forward references Tasks 1-6 of this plan placed in code
  comments, docstrings, test comments and the committed CSV header before this record existed.
  Ids are append-only, so a colliding draft on a sibling branch is renumbered, never this one.
- **Context:** MiD's `W_ZWECK` code 10, "anderer Zweck", reaches the eqasim purpose vocabulary as
  `other` (`braunschweig.popsim.trips.PURPOSE_BY_W_ZWECK`, the documented codebook mapping since
  ADR-0091 / issue #241). It is not a residual: on the weekday non-rbW leg universe the model
  builds its days from it is **39,429 of 738,546 unweighted legs (5.34 %)** -- committed
  `eqasim-data/data/braunschweig/mid/mid2023_w_zweck_by_hwzweck1.csv` (5.36 % W_GEW-weighted, spec
  section 1.1, measured 2026-09-09 on the local raw file and not reproduced by committed code).

  The consequence is visible in the model's realised purpose mix. On the arm-4 100 % run
  (`participation-universe-controls-arm4-100pct-2026-09-08`, ADR-0109), universe `at_home_zero`,
  segment `all`, committed artefact
  `eqasim-data/data/braunschweig/calibration/participation_universe_controls_arm4_100pct_2026-09-08/comparison.csv`:

  | metric (segment `all`) | model | SrV 2023 | delta |
  |---|---|---|---|
  | `participation_other` | 0.2533 | 0.1712 | **+8.22 pp** |
  | `participation_leisure` | 0.3767 | 0.3832 | -0.65 pp |
  | `participation_shop` | 0.2175 | 0.2794 | -6.19 pp |

  SrV's own residual bucket (`V_ZWECK 70 "Sonstiges"`) is 1.3 % of trips (spec section 1.1, ad-hoc
  measurement on the local SrV raw file, not reproduced by committed code), so the SrV reference
  simply has no place where 5 % of legs could be filed as "other" -- SrV respondents file what MiD
  respondents call "anderer Zweck" under one of the named purposes.

  **The `W_ZWD` route is dead.** Issue #373 proposed resolving code 10 by crossing it with the MiD
  detail-purpose variable `W_ZWD`. Every code-10 leg carries a `W_ZWD` SENTINEL instead of a detail
  code: 2202 "im PAPI nicht erhoben" 44.8 %, 7704 "kein Einkaufs-, Erledigungs-, Freizeitweg"
  43.5 %, 4402 "Kind unter 14 Jahren" 11.7 % (spec section 1.1, ad-hoc measurement 2026-09-09, not
  reproduced by committed code; children's code-10 legs carry only 4402/2202). There is nothing to
  cross.

  **MiD answers the question itself.** The delivery carries MiD's own main-purpose derivation
  `hwzweck1` ("Hauptzweck des Weges"; codebook `MiD2023_Codeplaene_B1_Standard_v1.1.xlsx`, sheet
  Wege: 1 Arbeit, 2 dienstlich, 3 Ausbildung, 4 Einkauf, 5 Erledigung, 6 Freizeit, 7 Begleitung,
  99 k.A.). Committed evidence table `mid2023_w_zweck_by_hwzweck1.csv` (script
  `scripts/extract_mid_w_zweck_hwzweck1.py`; weekday `kernwo in (1, 2, 3)`, `W_RBW != 1`, W_GEW-weighted
  row shares, n per cell) states that **W_ZWECK 10 folds to `hwzweck1` 6 "Freizeit" for 100 % of the
  legs** (`share_weighted = 1.0000`, n = 39,429). Every other code except the two home codes folds
  to exactly one `hwzweck1` value as well; the eqasim mapping agrees with that fold everywhere it
  is defined:

  | W_ZWECK | `hwzweck1` (share 1.0000 unless noted) | eqasim purpose today |
  |---|---|---|
  | 1, 2 | 1 Arbeit / 2 dienstlich | work |
  | 3, 11, 12 | 3 Ausbildung | education |
  | 4 | 4 Einkauf | shop |
  | 5 | 5 Erledigung | other |
  | 6, 13 | 7 Begleitung | escort / education (13, see ADR-0112) |
  | 7, 14, 15, 16 | 6 Freizeit | leisure |
  | **10** | **6 Freizeit** | **other** (the disagreement this record resolves) |
  | 8, 9 | spread over 1-7 + 99 | home (MiD recodes a home leg to the previous leg's purpose) |
  | 99 | 99 k.A. | other |

  The behavioural profile of code-10 legs matches that fold: median `wegkm_imp` 2.94 km (leisure
  3.92, errands 3.80), median activity duration 75 min (leisure 115), 46 % are followed by a home
  leg, they are 7.4 % of children's legs (0-13) and 7.0 % of seniors' (65+) but only 4.4 % of
  working-age adults' -- short, local, disproportionately young-and-old activity: spec section 1.1,
  ad-hoc measurement 2026-09-09 on the local raw file, not reproduced by committed code, quoted here
  as corroboration and NOT as the basis of the decision (the committed fold table is).
- **Decision:** A flag, `w_zweck_10_as_leisure`, makes the whole model treat a code-10 leg as
  `leisure` -- the plan, the PopulationSim participation seeds, the secondary distance layers and
  the home-office donor pool together, never one without the others. Production value `true`
  (`configs/base_bs.yml`); the OFF path is byte-identical to the pre-#373 model.

  1. **One rule, one place.** `PURPOSE_BY_W_ZWECK[10]` STAYS `"other"` (it is the documented
     codebook mapping, and a record of what the code literally says); the relabel is a separate,
     logged step inside `braunschweig.popsim.trips.map_purpose`, the same pattern
     `explicit_round_trip_purposes` (ADR-0091) established. `trips.leisure_w_zweck_codes(*,
     w_zweck_10_as_leisure)` is the ONE definition of "which W_ZWECK codes are leisure" and is what
     every other consumer asks.
  2. **Seeds see what the plan sees.** `braunschweig.popsim.mid.participation.participation_w_zweck`
     delegates the `leisure` entry of `PARTICIPATION_W_ZWECK` to that same helper, so the
     `leisure_participation` Kreis control counts the same legs the realised plan carries. This is
     the seed-vs-plan mismatch class ADR-0109 named: a control whose seed universe differs from the
     plan's constrains a day the model never builds.
  3. **Every consumer reads the same key.** The flat config key `w_zweck_10_as_leisure` is declared
     with one shared constant pair (`braunschweig.popsim.stage.config_keys.KEY_W_ZWECK_10_AS_LEISURE`
     / `DEFAULT_W_ZWECK_10_AS_LEISURE`) by `braunschweig.popsim.trips_stage`,
     `braunschweig.popsim.stage`, `braunschweig.popsim.distance_distributions` and
     `braunschweig.synthesis.commute_day.home_office_donors_stage`; the threading list and the rule
     that produced it live in the contributor note `docs/codebase/notes/mid-purpose-mapping.md`.
     Declared default `true`; the CODE default of every `map_purpose` / `build_trip_table` /
     `participation_w_zweck` keyword stays `False` so a direct caller or test that omits it keeps
     today's behaviour.
  4. **ENTD rejects it.** `EntdSource.build_trips` raises `ValueError` naming the key for any
     non-default value (`config_keys.ENTD_REJECTED_KEYS`, the single home of that list): ENTD 2008
     has no `W_ZWECK` vocabulary at all, so a silently ignored flag would let a `popsim_open` config
     believe it ran a purpose rule that never existed. Both `popsim_open` fixtures set the OFF value
     explicitly and `tests/test_popsim_open_config.py` iterates `ENTD_REJECTED_KEYS` against every
     discovered fixture.
  5. **Subtype sentinels.** `purpose_subtype.LEISURE_SENTINELS` gains 7704 and 7705 unconditionally
     (defensive, not behaviour: a code-10 leg's `W_ZWD` is always a sentinel, so the coverage guard
     can never trip on it once a code-10 leg can reach a leisure spec).
- **Rejected alternatives:**
  - **Keep code 10 on `other` (the status quo, rejected).** It is the only option that needs no
    code, and it is what produced the +8.22 pp `participation_other` gap above while MiD's own
    derivation says these legs are Freizeit. Keeping it would also keep the *shape* problem: the
    model's `other` purpose would remain a mixture of Erledigung (code 5, which SrV's `V_ZWECK`
    10/11 do match) and a second, larger, differently-distributed population of short local legs,
    so no calibration of `other` could be right for both halves at once.
  - **Resolve code 10 through `W_ZWD` (the issue's own proposal, rejected as impossible).** Every
    code-10 leg carries a `W_ZWD` sentinel (Context above); the cross-tabulation the issue asked for
    has no informative cell. Recorded here so it is not re-attempted.
  - **Remap by age or by another covariate (rejected).** Code-10 legs are more frequent among
    children and seniors, so an age-conditional rule ("children's code-10 legs are leisure, adults'
    stay other") is constructible -- but it has no source: MiD's own derivation folds ALL code-10
    legs to Freizeit without an age condition, and inventing an age split would be exactly the
    unsourced reference value CLAUDE.md forbids. The uniform fold is what the data states.
  - **A new eqasim purpose for "anderer Zweck" (rejected).** It would need its own distance
    distribution, its own building potential, its own SrV counterpart for validation and its own
    MATSim activity type -- none of which exists, because the SrV reference the model is judged
    against has no such category (`V_ZWECK 70` is 1.3 %). It would move the problem from the purpose
    mix into the location model.
- **Consequences:**
  - **The `leisure_participation` control's MiD-side universe widens.** Counting on the committed
    fold table: the leisure leg universe grows from **108,940** unweighted weekday non-rbW legs
    (codes {7, 14, 15, 16}) to **148,369** (+36.2 %); code 10 is then 26.6 % of it. The SrV-side
    anchor is UNCHANGED (`target2026_leisure_participation_by_kreis.csv`, SrV `E_ZWECK_9 == 7`) --
    and that is the point: SrV's leisure bucket already contains what MiD respondents file as code
    10, so the widened MiD universe is the one that matches the target, not a new claim about the
    target. The committed target file is NOT edited by this record.
  - **Cache devalidation.** Two PopulationSim seed columns change (the `leisure_participation`
    universe; via ADR-0112 also `education_flag`), so `braunschweig.popsim.stage` recomputes and
    PopulationSim re-runs -- roughly 7 h on the 64c server, intended ONCE for arm A of the A/B
    below. `braunschweig.popsim.trips_stage`,
    `braunschweig.synthesis.commute_day.home_office_donors_stage` and
    `braunschweig.popsim.distance_distributions` recompute with it. No divergent branch is run
    against the shared server cache (memory `feedback-no-divergent-branch-against-shared-cache`).
    Known gap, NOT fixed here: `braunschweig.popsim.distance_distributions` has no `validate()`, so
    a future edit to a helper module it imports would not devalidate it by itself; its config keys
    DO enter the stage hash, which is what this flag needs.
  - **Pre-registered A/B (server, Task 9 of the #373/#372/#242 SDD plan; NOTHING has run at the time
    this record is written).** Arm A = this flag together with ADR-0112 (both change the seeds, so
    one PopulationSim run); baseline = sub-project B's arm 1. Measured with
    `braunschweig.analysis.synthesis.plan_structure_vs_srv` (view `final`, universe `at_home_zero`)
    and the population-validation escort/purpose reports:

    | metric | baseline (arm-4 committed comparison.csv) | reference | expected after arm A (ASSUMPTION) |
    |---|---|---|---|
    | `participation_other`, segment `all` | +8.22 pp | SrV 0.1712 | below +3 pp |
    | `participation_leisure`, segment `all` | -0.65 pp | SrV 0.3832 | moves up; stays within +/- 3 pp |
    | leisure subtype shares (`leisure_local/visit/activity/excursion`) | arm-1 artefact | -- | reported, not gated (the code-10 mass draws the code-7 subtype mix, see Assumptions) |
    | leisure distance distribution (p25/p50/p75) | arm-1 artefact | `mid2023_w_zwd_group_reference.csv` | shifts DOWN (code-10 legs are shorter); reported, not gated |
    | code-10 relabel rate in the run log | -- | 5.34 % of legs (committed table) | 5-6 % of legs |

    A metric moving the wrong way stops the ladder for diagnosis. The expected column is an
    ASSUMPTION until a run manifest under `docs/runs/` records the measurement; the feature record
    `docs/registry/features/purpose_main_fold_code_10.yml` stays `validation.state: unvalidated`
    until then.
  - **Reported, not silent.** `map_purpose` logs the relabelled leg count and its W_GEW-weighted
    share with the weighting basis named; a rate far from ~5 % is the visible signature of a broken
    join or a different delivery.
- **Assumptions (explicit):**
  1. **MiD's `hwzweck1` fold is the authoritative statement of what a code-10 leg is.** It is a
     derivation by the survey institute, not an independent observation; no external source
     confirms it. Everything in this record rests on that one table.
  2. **Code-10 leisure activities inherit the code-7 subtype mix AND the code-7 subtype distance
     layers.** A code-10 leg's `W_ZWD` is always a sentinel, so it enters neither the subtype
     estimation (`purpose_subtype.LEISURE_SPEC.zweck_values == {7}`) nor a subtype-specific donor
     pool; at APPLICATION time `distance_distributions.run`'s leisure subtype split filters on the
     eqasim purpose, so the widened leisure population draws its subtype and its subtype distance
     from the code-7 mix. Whether a code-10 activity really behaves like a code-7 activity is NOT
     measured (controller ruling C-R6); the A/B row above reports the resulting shift instead of
     assuming it away.
  3. **The SrV `leisure_participation` target is the right counterpart for the widened universe.**
     Verified only at the level of the coarse SrV bucket (`E_ZWECK_9 == 7`, ADR-0091's own
     cross-tabulation), not per fine purpose.
  4. **The expected effect sizes in the A/B table are assumptions**, not results (spec section 6).
- **Evidence:** issue **#373**; related **ADR-0091** / #241 (the explicit W_ZWECK mapping and its
  coverage guard this extends), **ADR-0109** / #368 (a control's seed universe must be the plan's),
  **ADR-0112** (the passive-escort rule shipped in the same package and the same A/B arm),
  **ADR-0113** (the W_ZWD subtype sentinels). Spec
  `docs/superpowers/specs/2026-09-09-purpose-correctness-design.md` sections 1.1 / 2.1 / 3 / 6.
  Committed data: `eqasim-data/data/braunschweig/mid/mid2023_w_zweck_by_hwzweck1.csv` (data record
  `mid2023_w_zweck_by_hwzweck1`, script `scripts/extract_mid_w_zweck_hwzweck1.py`) and the arm-4
  baseline artefact
  `eqasim-data/data/braunschweig/calibration/participation_universe_controls_arm4_100pct_2026-09-08/comparison.csv`
  (run manifest `participation-universe-controls-arm4-100pct-2026-09-08`). Code
  `braunschweig/popsim/trips.py`, `braunschweig/popsim/trips_stage.py`,
  `braunschweig/popsim/stage/config_keys.py`, `braunschweig/popsim/stage/__init__.py`,
  `braunschweig/popsim/mid/participation.py`, `braunschweig/popsim/mid/seed_loading.py`,
  `braunschweig/popsim/sources/{base,mid,entd}.py`,
  `braunschweig/popsim/distance_distributions.py`, `braunschweig/popsim/purpose_subtype.py`,
  `braunschweig/synthesis/commute_day/{donor_pool,home_office_donors_stage}.py`; tests
  `tests/test_w_zweck_hwzweck1_fold.py`, `tests/test_leisure_education_participation.py`,
  `tests/test_popsim_trips.py`, `tests/test_popsim_trips_stage.py`,
  `tests/test_distance_distributions_by_purpose.py`, `tests/test_commute_day_donor_pool.py`,
  `tests/test_popsim_open_config.py`, `tests/test_trips_adapter_signature_parity.py`. Feature record
  `docs/registry/features/purpose_main_fold_code_10.yml`; contributor note
  `docs/codebase/notes/mid-purpose-mapping.md`.
  **No run has executed this code as of this record.** The measured evidence above is the committed
  MiD fold table and the arm-4 baseline artefact; the A/B in Consequences is a plan, not a result,
  and convergence of a MATSim run is not validation and is not claimed here.
