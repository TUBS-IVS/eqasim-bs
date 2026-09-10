# ADR-0112 · 2026-09-10 · Passive escort legs take the purpose of the paired adult leg
- **Status:** active
- **Numbering:** ADR-0112 is the id after ADR-0111 (this package allocates 0111-0113 together).
  Allocated 2026-09-09 and RE-VERIFIED on 2026-09-10 by the same check ADR-0111's Numbering
  paragraph records: `git fetch origin`, then `git ls-tree -r --name-only <ref> -- docs/decisions`
  grepped for `ADR-01[0-9][0-9]` on every one of the 21 `refs/remotes/origin` heads and every one of
  the 36 local `refs/heads`; the highest id anywhere is ADR-0110, so 0112 is free on every ref. The
  `ADR-0112` strings already in the working tree are the forward references Tasks 3-6 of this plan
  placed in `braunschweig/popsim/escort_pairing.py`, the trip-build docstrings, the validation
  module and the committed CSV header before this record existed. Ids are append-only.
- **Context:** MiD `W_ZWECK` 13 is "Begleitung, passiv" -- the ESCORTED person's own leg, not the
  escorter's (code 6, "Bringen/Holen", is the active side). Since issue #256 / ADR-0072 the model
  relabels EVERY code-13 leg to `education` and anchors it at the child's own assigned Kita/school
  (`escort_passive_education`, flag ON in `configs/base_bs.yml`), on the reasoning that the escorted
  person is a minor being brought to their own institution.

  On the weekday non-rbW leg universe the trip build uses, code 13 is **6,781 of 738,546 unweighted
  legs (0.92 %)** and folds to `hwzweck1` 7 "Begleitung" for 100 % of them (committed
  `eqasim-data/data/braunschweig/mid/mid2023_w_zweck_by_hwzweck1.csv`; 1.35 % W_GEW-weighted per
  spec section 1.2, an ad-hoc measurement on the local raw file not reproduced by committed code).
  They are 100 % minors (0-5: 70 %, 6-9: 20 %, 10-13: 11 %; spec section 1.2, ad-hoc).

  **The relabel is wrong in kind for most of these legs.** MiD carries no explicit companion link
  (`W_BEGL_HH` is a yes/no flag, `W_BEGL_1..6` are per-person "genannt" flags, `W_ANZBEGL` a count),
  but pairing a code-13 leg with the leg of a same-household adult departing at nearly the same
  minute resolves it: of the 6,759 weekday code-13 legs with a valid departure time, 91.9 % have a
  same-household adult (18+) leg departing in the SAME minute, 93.3 % within 5 min, **94.8 % within
  15 min**, 95.9 % within 30 min; 23 legs have no adult leg at all, and a paired leg shares the
  adult's `wegkm_imp` in 88.4 % of cases while the MODE differs in about half (driver vs passenger
  of one car trip) -- spec section 1.2, ad-hoc measurement 2026-09-09 on the local raw file, not
  reproduced by committed code. The W_GEW-weighted distribution of the adult's own `W_ZWECK` over
  the <= 15 min pairs (same source, same caveat):

  | adult `W_ZWECK` | share | what it means for the child's leg |
  |---|---|---|
  | 7 Freizeit | 22.0 % | joint leisure |
  | 6 Bringen/Holen | 21.1 % | the child IS being brought somewhere -- its own activity |
  | 4 Einkauf | 18.9 % | joint shopping |
  | 5 Erledigung | 14.9 % | joint errand |
  | 8 nach Hause | 13.2 % | the child goes HOME with the adult |
  | 1, 2 Arbeit / dienstlich | 4.2 % | the child comes along to the adult's workplace |
  | 10 anderer Zweck | 3.7 % | joint "other" (= leisure under ADR-0111) |
  | 3, 9, 99 | 2.0 % | rest |

  Only the adult-6 pairs are the case the current rule assumes. The committed reference table
  measured on the PRODUCTION universe (`eqasim-data/data/braunschweig/mid/mid2023_escort_w_zweck_split.csv`,
  script `scripts/derive_escort_w_zweck_split.py`; the WEEKDAY reporting days the PopulationSim seed
  keeps, then rbW legs excluded and the leading arrive-home leg dropped exactly as the trip build
  does -- controller rulings C-R12 and C-R18) states it as one number: with pairing, the share of the
  passive mass that stays `education` is **0.2684** (of which 0.1947 pairs with an active escort leg
  and 0.0738 stays unpaired and therefore keeps the existing rule), pairing rate **6,384/6,781 legs
  = 0.9415** within 15 minutes. **73.2 % of the passive mass is currently labelled `education`
  although the accompanying adult travelled for something else**, and 12.8 % of it is a trip HOME
  being modelled as a school trip -- the mechanism behind the education->education same-purpose
  repeats and the odd-hour school trips issue #372 reports.
- **Decision:** A flag, `escort_passive_from_adult`, gives a PAIRED code-13 leg the purpose derived
  from the accompanying adult's `W_ZWECK`; an UNPAIRED leg keeps the existing
  `escort_passive_education` rule. Production value `true` (`configs/base_bs.yml`), OFF path
  byte-identical.

  1. **Pairing (pure module `braunschweig/popsim/escort_pairing.py`, `pair_passive_legs`).** For each
     code-13 leg, the leg of a same-household person aged >= `adult_min_age` (18) whose departure is
     nearest in time. Tie order, in this sequence: smallest absolute gap -> the candidate whose
     `wegkm_imp` equals the passive leg's (a shared distance is the only independent signal that two
     legs are the same trip; a missing value counts as a mismatch) -> lowest adult `P_ID` -> lowest
     `W_ID`. Status precedence is own-time invalidity first: `unpaired_no_time` > `unpaired_no_adult`
     > `unpaired_gap`, so a leg can never fall through to a default status. An adult leg whose own
     `W_ZWECK` is 13 is EXCLUDED from the candidate pool (controller ruling C-R8): a person who is
     themselves being escorted cannot be the escorter, and 13 is not a destination purpose the rule
     below could map. Paired share, the three unpaired reasons and the adult-`W_ZWECK` distribution
     are logged; below 80 % paired the module WARNs, naming the committed 94.2 % production-universe
     reference.
  2. **Purpose rule (`trips.PASSIVE_PURPOSE_BY_ADULT_W_ZWECK` / `trips.passive_purpose_for_pairs`),
     applied inside `map_purpose` AFTER the existing escort block, so it only overwrites the legs it
     could pair:** adult 4 -> `shop`; 5 -> `other`; 7, 14, 15, 16 -> `leisure`; 10 -> `leisure` if
     `w_zweck_10_as_leisure` else `other` (ADR-0111); 8, 9 -> `home` (the child goes home with the
     adult); 6 -> the EXISTING passive rule (`education` under `escort_passive_education`, else
     `escort`); 1, 2, 3 -> `other`; 99 -> `other`; a missing/unknown adult code -> `DEFAULT_PURPOSE`,
     counted and WARNed about, never silently mapped. Five `passive_pair_*` traceability columns
     (status, adult `W_ZWECK`, adult `P_ID`/`W_ID`, gap in minutes) travel with the trip table so a
     downstream analysis can see WHICH adult leg a purpose came from.
  3. **Requires `escort_purpose`.** `map_purpose` raises `ValueError` naming BOTH keys when
     `escort_passive_from_adult` is on and `escort_purpose` is off (without a dedicated escort
     purpose there is no passive side to re-derive). Because of that dependency the DECLARED/CODE
     default of this flag is `False` (controller ruling C-R9, mirroring `escort_passive_education`):
     a `True` declared default would make the declared default set internally inconsistent and abort
     inside `braunschweig.popsim.trips_stage` AFTER the full PopulationSim balancing. The production
     ON state is realised ONLY by the `configs/base_bs.yml` line this record's package adds --
     `braunschweig/popsim/stage/config_keys.py` carries the single statement of both defaults.
  4. **The pairing window is a configured parameter**, `escort_passive_pair_max_gap_minutes` (unit:
     minutes, valid > 0, default 15.0, equal to `escort_pairing.DEFAULT_MAX_GAP_MINUTES` and pinned
     equal by `tests/test_popsim_trips.py::test_passive_pair_gap_default_agrees_across_its_three_homes`).
  5. **The seed sees exactly what the plan realises (ADR-0109's principle).**
     `mid.participation.derive_education_flag_seed` runs the SAME pairing and the SAME rule before
     counting a child's education legs, on the SAME leg universe: `_wege_without_non_education_passive_legs`
     reduces the frame with `trips.legs_kept_by_the_trip_build` (the ONE definition of the rbW and
     leading-arrive-home drops, extracted for this purpose) BEFORE pairing, so a leg the trip build
     drops also leaves the ADULT candidate pool (controller ruling C-R12).
  6. **The validation reference follows the rule.** `mid2023_escort_w_zweck_split.csv` gains one
     `code_13_to_<purpose>_share_under_pairing` column per fold member, derived from the same raw
     pairing on the same universe. `trip_coherence.apply_escort_active_adjustment` MOVES the passive
     Begleitung remainder onto those measured purposes instead of dropping it (controller ruling
     C-R11): mass-preserving, raising when the fold does not sum to 1. The measured fold (W_GEW
     shares over the 6,781 weekday passive legs, committed):

     | eqasim purpose | share | MiD W1 name |
     |---|---|---|
     | education | 0.2684 | ausbildung |
     | leisure | 0.2451 | freizeit |
     | other | 0.1804 | sonstiges |
     | shop | 0.1778 | einkauf |
     | home | 0.1283 | heimweg |
     | escort | 0.0000 | begleitung |

  7. **Every consumer reads the same two keys**, declared from one shared constant pair
     (`config_keys.KEY_ESCORT_PASSIVE_FROM_ADULT` / `KEY_PASSIVE_PAIR_MAX_GAP_MINUTES`) by
     `braunschweig.popsim.trips_stage`, `braunschweig.popsim.stage`,
     `braunschweig.popsim.distance_distributions` and
     `braunschweig.synthesis.commute_day.home_office_donors_stage`; `EntdSource.build_trips` rejects
     both (`ENTD_REJECTED_KEYS`) because ENTD has no `W_ZWECK` vocabulary and no household/time
     columns to pair on. The threading list is the contributor note
     `docs/codebase/notes/mid-purpose-mapping.md`.
  8. **`HP_ALTER` becomes a hard requirement** of `mid.donor.load_mid_wege` and of
     `home_office_donors_stage.WEGE_COLUMNS`: without the member's age nothing can pair, and a
     missing column must fail at LOAD time naming the column rather than silently pairing nothing.
- **Rejected alternatives:**
  - **Keep every code-13 leg on `education` (the status quo of #256/ADR-0072, rejected).** It is
    right for the 26.84 % of the passive mass the committed table attributes to education under
    pairing and wrong for the other 73.16 %, including the 12.8 % that are trips HOME. Keeping it
    also keeps a second-order defect: those legs are anchored at the child's school, so the
    secondary location model places a shopping or leisure destination at a Kita.
  - **Draw the destination for ALL passive legs from the SrV `V_ZWECK_BHOL` mix (rejected).**
    ADR-0072 derived that mix (63 % Kita/school) for the ACTIVE escorter's destination, and it is
    what the adult-6 pairs still use. Applying it to every passive leg would replace one uniform
    assumption with another and would DISCARD the household-and-time evidence that says where the
    child actually went -- a drawn distribution where an observed pairing exists.
  - **Drop the unpairable passive legs (rejected).** The child demonstrably travelled; deleting the
    leg would shrink the population's trip count to make a rule look clean. Unpaired legs (~5 %)
    keep the previous rule and are counted and logged as a rate.
  - **Pair on the household flag `W_BEGL_HH` alone (rejected).** It states only THAT a household
    member came along, not which one and not for what purpose; it cannot yield a destination purpose.
  - **Phase 2 -- anchor the child's joint activity at the ADULT's chosen secondary location
    (deferred, not rejected).** The inverse of the #201 household link. It needs the synthetic
    household to still contain the donor pair, which must be MEASURED first (member completion and
    the diary match can separate them). Tracked as issue **#385**; not in this package.
- **Consequences:**
  - **The `education_by_age` control changes what it pulls.** The 0-5 education participation the
    control targets (SrV `E_ZWECK_9 in {3, 4}` for the child's OWN leg) is unchanged as a TARGET,
    but the model's supply of 0-5 education legs shrinks, so the control pulls more children onto
    education diaries. `education_0_5` in `participation_fit` is the row to watch in the A/B; the
    seed change is exactly why the seed had to be re-derived under the same rule (Decision 5).
  - **Cache devalidation.** The `education_flag` seed column changes, so
    `braunschweig.popsim.stage` recomputes and PopulationSim re-runs (~7 h on the server), together
    with ADR-0111 in ONE arm-A run. `escort_pairing` is folded into the cache tokens of the three
    stages that can reach it (`trips_stage._HELPER_MODULES`,
    `popsim.stage._DEFERRED_HELPER_MODULE_NAMES`, `home_office_donors_stage._HELPER_MODULES`), so a
    change to the pairing rule cannot be served from a stale cache. Devalidation is intended, once.
  - **The escort validation reference is no longer "all passive -> education".** Any report built
    with `escort_passive_from_adult` ON must declare it (`run_population_validation.py
    --escort-passive-from-adult`, `scripts/measure_trip_coherence.py`), exactly as #256 made the
    `escort_passive_education` baseline an explicit declaration; scoring one population against the
    other baseline is the ambiguity that flag exists to remove.
  - **Pre-registered A/B (server, Task 9 of the SDD plan; NOTHING has run at the time this record is
    written).** Arm A = ADR-0111 + this record (one PopulationSim run, both change the seeds);
    baseline = sub-project B's arm 1.

    | metric | baseline | reference | expected after arm A (ASSUMPTION) |
    |---|---|---|---|
    | paired share of code-13 legs (run log) | -- | 0.9415 committed (weekday trip-build universe) | >= 0.90 |
    | purpose fold of the relabelled legs (run log) | -- | the committed fold table above | within a few pp of it |
    | education participation, children 0-5 | model 0.6862 = +3.04 pp (arm-4 `comparison.csv`, `at_home_zero`, segment `group_child_0_5`, n 1,081) | SrV 0.6558 | moves DOWN toward the reference |
    | education->education same-purpose repeats | 10,253 (issue #329, arm-1 report) | -- | the code-13 share of them disappears |
    | escort purpose share (W1 `begleitung`) | arm-1 report | MiD W1 8.0 % ZGB | unchanged (the ACTIVE side is untouched) |
    | trips HOME among former passive legs | 0 by construction | committed fold 0.1283 | ~13 % of the passive mass |

    A metric moving the wrong way stops the ladder for diagnosis. Every "expected" cell is an
    ASSUMPTION until a run manifest records it; the feature record
    `docs/registry/features/escort_passive_from_adult.yml` stays `validation.state: unvalidated`.
- **Assumptions (explicit):**
  1. **A code-13 leg departing within 15 minutes of a same-household adult's leg IS that adult's
     trip.** Supported by the 88.4 % identical `wegkm_imp` among pairs (ad-hoc measurement), not
     proven; MiD carries no companion identifier that could prove it.
  2. **The adult-6 pairs are the child's own activity** and keep the ADR-0072 destination mix (63 %
     Kita/school) -- the accepted deviation ADR-0072 already records, unchanged here.
  3. **A child paired with an adult travelling for work, business or education gets `other`**
     (4.2 % of pairs): the child has no anchor of its own at the adult's workplace. Dropping the leg
     was rejected above; `other` is the least-committed purpose available, and it is an ASSUMPTION,
     not a measurement.
  4. **The committed fold is measured on MiD DONOR legs, not on the synthetic population.** It
     describes the donors' passive-escort destinations; the synthesis re-weighting can move the
     realised mix, in a direction that is not signed a priori and with a magnitude bounded by
     `W1_begleitung * (1 - active_share)`. Task 9 measures the realised fold; until then the
     validation reference is a donor-side reference.
  5. **18 years is the adult threshold and 15 minutes the window** -- both configured, both chosen
     from the pairing-rate curve above (91.9 % / 93.3 % / 94.8 % / 95.9 % at 0 / 5 / 15 / 30 min on
     the raw weekday legs), which flattens after 15 minutes; no external source prescribes either.
     The committed rate is 94.15 % rather than the raw 94.8 % for three reasons that are NOT
     decomposed here: the denominator differs (the committed rate is over ALL 6,781 weekday passive
     legs, including the 22 with no usable departure time, while the ad-hoc figure is over the 6,759
     with a valid one), this module excludes adult candidate legs whose own `W_ZWECK` is 13
     (controller ruling C-R8), and the trip build's leg filters remove a further two pairs
     (6,386 -> 6,384 on the weekday legs). The leg filters alone are therefore the SMALLEST of the
     three effects.
  6. **`explicit_round_trip_purposes` is deliberately NOT threaded into
     `trips.passive_purpose_for_pairs`** (reasoning on that function's docstring): with the flag ON
     -- its production value -- an adult W_ZWECK 14/15/16 leg is `leisure` on both sides, so the
     child's purpose agrees with the adult's and the omission is inert. It would bite only on the
     pre-#241 A/B arm (flag OFF), where the adult's own leg reverts to `other` while the child would
     still receive `leisure`; that arm does not use `escort_passive_from_adult`.
  7. **Cleanup wave (issue #373 task 2, ruling C-R20/C-R21):** `braunschweig.popsim.distance_distributions.run`
     used to pair on the UNFILTERED Wege frame while the trip build pairs on
     `trips.legs_kept_by_the_trip_build`'s output (assumption 5's 6,386 vs 6,384 gap); it now takes a
     `map_purpose(..., pairing_candidate_mask=...)` built from the SAME helper and the SAME
     `exclude_rbw_legs`/`drop_leading_arrive_home_leg` config keys the trip build reads, so the two
     stages' pairings agree on which legs exist to be paired. The DISTANCE POOL itself is unaffected
     -- every leg still contributes a distance under whichever purpose it resolves to.
  7. **Every household member with `HP_ALTER >= adult_min_age` counts as an adult**, without a guard
     against coded age values. It holds on this delivery (max `HP_ALTER` 85, no missing values), but
     the pairing would silently treat a future top-code or missing-value code above the threshold as
     an adult; the module reads the column as delivered rather than validating a code range.
- **Evidence:** issue **#372**; related **ADR-0072 / ADR-0073** (#201/#256/#257: the escort purpose
  family, the passive-as-education rule this replaces for paired legs, the SrV `V_ZWECK_BHOL`
  destination mix), **ADR-0109** / #368 (seed and plan must count the same legs), **ADR-0111** (the
  code-10 rule that decides what an adult-10 pair gives the child, shipped in the same arm),
  **#385** (Phase 2 joint location, deferred). Spec
  `docs/superpowers/specs/2026-09-09-purpose-correctness-design.md` sections 1.2 / 2.2 / 3 / 5 / 6.
  Committed data `eqasim-data/data/braunschweig/mid/mid2023_escort_w_zweck_split.csv` (data record
  `mid2023_reference_tables`, script `scripts/derive_escort_w_zweck_split.py`) and
  `mid2023_w_zweck_by_hwzweck1.csv`; baseline artefact
  `eqasim-data/data/braunschweig/calibration/participation_universe_controls_arm4_100pct_2026-09-08/comparison.csv`
  (run manifest `participation-universe-controls-arm4-100pct-2026-09-08`). Code
  `braunschweig/popsim/escort_pairing.py`, `braunschweig/popsim/trips.py`,
  `braunschweig/popsim/trips_stage.py`, `braunschweig/popsim/stage/config_keys.py`,
  `braunschweig/popsim/stage/__init__.py`, `braunschweig/popsim/mid/participation.py`,
  `braunschweig/popsim/mid/seed_loading.py`, `braunschweig/popsim/mid/donor.py`,
  `braunschweig/popsim/sources/{base,mid,entd}.py`, `braunschweig/popsim/distance_distributions.py`,
  `braunschweig/synthesis/commute_day/{donor_pool,home_office_donors_stage}.py`,
  `braunschweig/analysis/population_validation/trip_coherence.py`,
  `braunschweig/analysis/population_validation/run_population_validation.py`,
  `scripts/measure_trip_coherence.py`; tests `tests/test_escort_pairing.py`,
  `tests/test_popsim_trips.py`, `tests/test_popsim_trips_stage.py`,
  `tests/test_participation_universe_controls.py`, `tests/test_escort_validation.py`,
  `tests/test_derive_escort_w_zweck_split.py`, `tests/test_commute_day_donor_pool.py`,
  `tests/test_popsim_stage_validate_token.py`, `tests/test_popsim_open_config.py`. Feature record
  `docs/registry/features/escort_passive_from_adult.yml`; feature doc
  `docs/features/escort-purpose.md`; contributor note `docs/codebase/notes/mid-purpose-mapping.md`.
  **No run has executed this code as of this record.** The measured evidence is the committed MiD
  tables and the arm-4 baseline artefact; the A/B is a plan, not a result.
