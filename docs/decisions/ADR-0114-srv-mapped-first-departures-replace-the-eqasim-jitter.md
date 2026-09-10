# ADR-0114 · 2026-09-10 · SrV-mapped first departures replace the inherited eqasim +/-30 min jitter
- **Status:** active
- **Numbering:** ADR-0114 is the next free id after ADR-0113 (which this branch's sibling package,
  issues #373/#372/#242, allocated together with 0111 and 0112). Allocated 2026-09-09 and
  RE-VERIFIED on 2026-09-10 before this record was written, by the check ADR-0110's and ADR-0112's
  Numbering paragraphs record: `git fetch origin --prune`, then
  `git ls-tree -r --name-only <ref> -- docs/decisions` grepped for `ADR-[0-9]{4}` on every one of
  the 19 `refs/remotes/origin` heads and every one of the 33 local `refs/heads` (52 refs, the
  local set including every worktree of this checkout). The highest id found anywhere is
  **ADR-0113** (on `feature/i123-departure-time-model` and `feature/i373-purpose-correctness`);
  `origin/main` carries up to ADR-0110, and NO ref carries an `ADR-0114` file. The `ADR-0114`
  strings already present in the working tree are the forward references Tasks 1-5 of this
  package placed in `braunschweig/popsim/departure_time_model.py`,
  `braunschweig/popsim/trips_stage.py`, the source adapters, `config_keys.py`,
  `plan_replacement.py`, `trips_day_stage.py`, the two analysis modules, the two committed CSV
  headers and the two `popsim_open` fixture configs before this record existed. Ids are
  append-only, so a colliding draft on a sibling branch is renumbered rather than this one.
- **Context:** The vendored eqasim stage ("Diversify departure times", reproduced exactly by
  `braunschweig.popsim.trips_stage.apply_per_person_jitter`) shifts EVERY person's whole day by
  one uniform random offset in `[-interval, +interval)` with `interval = min(1800 s, first
  departure)`; departure and arrival of every trip move together, and
  `braunschweig.synthesis.commute_day.plan_replacement` applies the same jitter once more to
  spliced home-office chains. Its purpose upstream is mechanical: survey times cluster on a clock
  grid, and thousands of agents departing at exactly 7:00:00 would pulse MATSim's queues. The
  1800 s is a hard-coded constant nobody in this project decided, it is FOUR TIMES wider than the
  reporting grid it is meant to break, and being symmetric with a `min(1800, first departure)`
  clip it cannot move a start time toward the observed morning peak -- it can only flatten it.

  **The gap it produces** (school-age children, first education leg, weekday; spec
  `2026-09-09-departure-time-srv-mapping-design.md` section 1.2). Every row below except the first
  is an AD-HOC measurement taken on 2026-09-09 on the local raw microdata / a seeded simulation
  and is NOT reproduced by committed code; only the SrV row is committed evidence, and it is
  quoted here from the committed table, not from the spec:

  | source | hour 6 | hour 7 | hour 8 |
  |---|---|---|---|
  | SrV 2023 BS+RGB, `school_age_6_17_not_employed`, first education legs, AS REPORTED (committed `srv2023_departure_time_reference.csv`, `n_unweighted` 1,810) | **9.91 %** | **82.57 %** | 5.87 % |
  | MiD 2023 raw national pool, 6-17, first legs, unchanged (ad hoc) | 13.8 % | 78.7 % | 5.6 % |
  | the same MiD legs with the eqasim +/-30 min shift (ad hoc, seeded simulation) | 22.7 % | 66.0 % | 9.0 % |
  | the same with +/-5 / +/-7.5 / +/-10 / +/-15 min (ad hoc) | 18.8 / 18.6 / 18.2 / 19.2 % | 74.4 / 74.7 / 75.0 / 73.7 % | ~5 % |
  | model, arm 4 (2026-09-08), all education legs of 6-17 not employed (ad hoc) | 21.0 % | 54.5 % | -- |

  The spec's own table states `n 1,811` for the SrV row where the committed table says 1,810; the
  two gated shares agree to within 0.1 pp. The one-leg difference has NOT been explained and is
  recorded as an open discrepancy rather than silently harmonised (issue-first).

  Reading: the +/-30 min shift alone moves about 9 pp from hour 7 into hour 6 and about 4 pp into
  hour 8 -- most of the model's hour-6 excess. But ANY symmetric de-rounding moves half of the
  mass reported as exactly "7:00" into hour 6, because within hour 7 the raw reports sit at
  7:00-7:14 (20 %), 7:15-7:29 (27 %), 7:30-7:44 (41 %) and 7:45-7:59 (13 %), with 12.9 % of them
  at `7:0x` (ad hoc, spec 1.2). **An hour-band comparison of de-rounded model times against
  as-reported reference times is therefore biased by construction**; both sides must be treated
  alike, which is what the committed reference's `share_derounded` column and the new analysis
  stage exist for.

  **The rounding grid** (weekday legs, minute of hour, unweighted; ad hoc, spec 1.3):

  | grid | MiD `W_SZM` | SrV `V_BEGINN_MINUTE` |
  |---|---|---|
  | `:00` | 26.4 % | 22.3 % |
  | `:30` | 19.7 % | 17.6 % |
  | `:15` / `:45` | 17.9 % | 16.6 % |
  | other 5-minute grid | 33.1 % | 33.9 % |
  | off-grid minutes | 2.9 % | 9.7 % |

  Both surveys report the same way, which is what makes ONE precision rule defensible for both.

  **The residual national-vs-regional gap** (spec 1.4, all ad hoc). After de-rounding, the
  remaining difference for school children is the raw-pool difference between the national MiD
  donors and the regional SrV reference. Two contributors are measured but small: (a) Bundesland
  composition -- first education legs before 7:00: east total 28.0 % (n 987) vs west 11.6 %
  (n 13,038), Niedersachsen 11.6 %, worth about 2 pp in the national pool; (b) the diary-match age
  band 6-13 mixes 6-9-year-olds (5.4 % at hour 6) with 10-13-year-olds (18.2 %), bounded by the
  14 % remapped share. Work legs show the same sign pattern at a third of the magnitude
  (+3.9 pp at hour 6, -4.3 pp at hour 7). The user's goal is to reproduce the SrV distribution as
  well as possible, so the model targets the regional distribution DIRECTLY rather than fixing the
  contributors one by one.
- **Decision:** A configurable departure-time model with three modes behind one dispatch
  (`braunschweig.popsim.departure_time_model.apply_departure_time_model`, key
  `departure_time_model`), production value `srv_mapped` in `configs/base_bs.yml`, OFF path
  (`eqasim_uniform`) byte-identical. The MECHANISM -- the precision rule, the mapping, the
  coarsening ladder, the offset column, the two call sites and the guards -- is documented ONCE in
  the contributor note `docs/codebase/notes/departure-time-model.md` and is not repeated here;
  this record states what was decided and why.

  1. **Measure first, apples-to-apples (spec 2.1).** Every model writes the per-person shift it
     applied as `OFFSET_COLUMN` (`departure_time_offset_seconds`) on the trips frame, so a
     realised time decomposes into the donor's raw MiD report, the post-repair pre-offset time and
     the realised time without re-deriving anything from an RNG stream. Two committed SrV 2023
     aggregates (`scripts/extract_srv_departure_times.py`, pure module
     `braunschweig/calibration/srv_departure_times.py`, data records
     `srv2023_departure_time_reference` / `srv2023_activity_duration_reference`) carry, per
     segment x purpose x position x 15-minute bin, BOTH a `share_derounded` and a
     `share_as_reported` column plus `n_unweighted`; the de-rounding of the reference is seeded
     (`SRV_DEROUNDING_SEED = 20260909`) so the table is reproducible. A new analysis stage
     `braunschweig.analysis.synthesis.departure_time_vs_srv` reports the decomposition, the
     15-minute comparison against both reference columns (`emd_on_bands`) and the
     activity-duration hold-out. `plan_structure_vs_srv`'s `dep_hour_share_*` rows stay as they
     are (as-reported, hour level) and its summary gains one sentence pointing at the unbiased
     comparison -- see the note for why those rows are biased by construction.
  2. **The model (spec 2.2).** `eqasim_uniform` = today's jitter (code default);
     `derounded` = one per-person offset drawn inside the reporting-precision cell of the person's
     FIRST departure (+/- 7.5 / 2.5 / 0 min, `braunschweig/calibration/reported_time_precision.py`),
     which replaces eqasim's arbitrary +/- 30 min with the survey's ACTUAL precision and nothing
     else; `srv_mapped` = `derounded` plus a rank-preserving quantile mapping of the de-rounded
     first departure onto the de-rounded SrV distribution of the person's
     `(first purpose x harmonised group)` cell, applied as one further WHOLE-CHAIN offset. Thin
     cells climb a coarsening ladder `(purpose, group) -> (purpose, all) -> (all, all) ->
     unmapped` whose rungs are gated by `departure_time_mapping_min_reference_n` (200) and
     `departure_time_mapping_min_model_n` (50); a cell whose median absolute shift exceeds
     `departure_time_mapping_max_median_shift_hours` (2.0) WARNS, as does an unmapped share above
     5 % and a coarsened share above 25 %. The chain shift preserves trip and activity durations
     bitwise.
  3. **Where it runs.** Exactly twice: in `braunschweig.popsim.trips_stage.run` on the
     pre-assignment view for the whole population, and in
     `plan_replacement.build_day_trips` on the spliced home-office chains of the reporting-day
     view (ADR-0104), keyed by the RECEIVING person's group and the donor chain's first purpose --
     the generalisation of ADR-0104's "jitter exactly once, keyed by the receiving person" rule.
     Times do not feed the location assignment, so the two-view architecture is untouched.
     `EntdSource.build_trips` REJECTS a non-default `departure_time_model` naming the key (the
     ENTD path has no SrV cell structure), the same treatment ADR-0111/ADR-0112's MiD-only keys
     get; both `popsim_open` fixtures set the OFF value explicitly.
  4. **What is deliberately NOT modelled.** Later departures, activity durations, arrival times
     beyond the chain shift, and per-Kreis differences. These are the HOLD-OUT dimensions of the
     validation.
- **Rejected alternatives (spec 2.3):**
  - **Keeping the inherited 1800 s jitter.** It is a modelling distortion four times wider than
    the reporting precision it exists to break, it is symmetric (so it cannot correct a
    misplaced peak, only flatten it), and the ad-hoc simulation above attributes about 9 pp of
    the model's hour-6 excess for school children to it. Keeping it would also leave the project
    with an inherited constant nobody decided sitting in the middle of a dimension it now
    validates against a regional reference.
  - **De-rounding only (`derounded`, kept as arm 1 of the A/B, not as the default).** It removes
    the grid spikes with the survey's own precision and touches nothing else, which is the more
    conservative change -- but it leaves the national-vs-regional gap intact (about 4 pp at hour 6
    for school children, ad hoc), because MiD donors are a national pool. It is retained as a
    pre-registered arm precisely so the mapping's contribution can be MEASURED rather than
    assumed.
  - **An east-Laender donor filter for school-age plan sources.** The Bundesland composition
    effect is real but small (about 2 pp, ad hoc), it is subsumed by mapping onto the regional
    distribution directly, and it would shrink the child donor pool by about 7 % -- paying
    sampling noise for a fraction of the gap.
  - **SrV as a time donor.** Rejected in #226 already: SrV is not a chain donor for this model and
    its n is far too small to supply whole diaries; the aggregate distribution is exactly the part
    of it that IS usable.
  - **Mapping every trip independently.** It would break chain consistency: departures would no
    longer be ordered by the donor's own durations, and trip/activity durations -- the hold-out --
    would be destroyed by the very mechanism meant to be validated against them.
  - **Mapping by Kreis.** SrV per-Kreis cells are assumption-grade under its stratified PSU
    design, the same caveat ADR-0104 and ADR-0109 already record for every per-Kreis SrV rate in
    this codebase.
  - **The child age-band refinement** (the 6-13 diary-match band mixing 6-9 with 10-13-year-olds)
    is NOT rejected but deferred: it affects school-trip LENGTH as well as time, so it belongs in
    its own issue rather than in this model.
- **Consequences:**
  - **Cache devalidation, once and deliberately.** The model module and its helpers are folded
    into the `_HELPER_MODULES` hash of `braunschweig.popsim.trips_stage` and
    `braunschweig.synthesis.commute_day.trips_day_stage`, so `synthesis.population.trips` and
    everything downstream of it recompute -- **PopulationSim does not**: no control, seed or
    balancing input changes, which is what keeps the A/B affordable (arm 1 pays the rebuild once).
    No divergent branch is run against the shared server cache (memory
    `feedback-no-divergent-branch-against-shared-cache`).
  - **The mapping is SELF-CALIBRATING, and that bounds what a good fit proves.** It is estimated
    on the MODEL's own realised first departures and mapped onto the SrV first-departure
    distribution, which is therefore a CALIBRATION TARGET, not an independent reference: after
    `srv_mapped`, the first-departure distribution matching SrV proves that the wiring, the cells
    and the ladder work -- it is NOT evidence that the model predicts departure behaviour, and it
    must never be reported as validation (CLAUDE.md "convergence is not validation"; the same
    distinction ADR-0109 draws between a control that is FITTED and a reference that is
    COMPARED). It also means the departure profile can no longer be used as evidence for or
    against upstream changes (the day-absence draw of ADR-0110, the purpose corrections of
    ADR-0111/ADR-0112/ADR-0113, future control changes) once the mapping is on; the analysis
    stage's raw-donor and pre-offset columns are what remains available for that.
  - **Calibration vs validation, stated as a split.** CALIBRATED: the first departure per
    `(purpose, group)` cell (reference position `first`). HOLD-OUT: later-trip departures
    (positions `later` / `all` of the same committed table), activity durations (the whole
    `srv2023_activity_duration_reference.csv`), and per-Kreis structure. The analysis stage labels
    every EMD row `calibrated` or `holdout` for exactly this reason, and a hold-out metric moving
    the wrong way is the signal that stops the ladder for diagnosis.
  - **Pre-registered A/B (server, cached population; NOT run as part of this package -- Task 8 of
    the #123 SDD plan).** Arms: (0) `eqasim_uniform` = today, (1) `derounded`, (2) `srv_mapped`.
    Only `trips` and downstream recompute. Measured with `departure_time_vs_srv` and
    `plan_structure_vs_srv` (view `final`). **Every "expected" cell below is an ASSUMPTION**
    (spec section 3), not a result -- nothing has run:

    | metric | arm 0 (today) | expected arm 1 (ASSUMPTION) | expected arm 2 (ASSUMPTION) |
    |---|---|---|---|
    | first education legs 6-17, hour 6 share (as reported, `plan_structure`) | to be measured | 15-20 % | within +/- 1.5 pp of the SrV 9.91 % after de-rounding BOTH sides (`comparison.csv`) |
    | first-trip 15-min EMD vs de-rounded SrV, every `(purpose, group)` cell with `n_srv >= 200` | to be measured | lower than arm 0 in every cell | < 0.02 in every cell (CALIBRATED dimension -- meeting it proves wiring, not prediction) |
    | later-trip 15-min EMD per purpose (HOLD-OUT) | to be measured | not worse than arm 0 | not worse than arm 1 by more than 0.01 |
    | activity-duration bands per purpose (HOLD-OUT) | to be measured | unchanged within 1 pp | unchanged within 1 pp (durations are not touched at all) |
    | unmapped share | -- | -- | < 5 % |

    A hold-out metric deteriorating beyond its bound stops the ladder for diagnosis (no fix
    stacking). The OFF arm must reproduce today's times byte-identically, which four tests already
    pin at the dispatch, the stage, the plan replacement and against the pre-#123 golden values
    (feature record `departure_time_model`). Task 8 must additionally RECORD the share of persons
    whose `P_TAET` was item non-response (99) and therefore IMPUTED by `attributes.map_employed`
    -- the group input every consumer now shares (Assumption 9, ruling A-R17) -- so the run
    manifest states how many persons' harmonised group rests on an imputed rather than an observed
    employment code.
  - **`min_model_n` governs both call sites (known limitation).** The same threshold (50) gates
    the whole-population trip build and the much smaller spliced home-office set of the plan
    replacement, so at small sampling rates the spliced chains coarsen or stay unmapped while the
    main build maps at `purpose_group`. Both call sites log their own level split, and the
    validation run records the reporting-day stage's realised split. Whether the splice needs its
    own threshold is an OPEN QUESTION, deliberately not answered here (issue-first).
  - **Nothing has run.** Every number in this record is either the committed SrV tables or an
    ad-hoc 2026-09-09 measurement labelled as such; the feature record's `validation.state` is
    `unvalidated` with `runs: []` until Task 8 records a manifest under `docs/runs/`.
- **Assumptions (explicit; spec section 6 plus what implementation added):**
  1. The precision of a survey report is read from its MINUTE VALUE (quarter hour / five minutes /
     exact). A person who genuinely departed at 7:00 is de-rounded as if they had rounded.
  2. The same precision rule applies to MiD and to SrV (supported by the grid table in Context,
     which is itself an ad-hoc measurement).
  3. The chain shift preserves the donor's internal timing: a person's whole day moves rigidly, so
     trip and activity durations are unchanged by construction.
  4. The SrV first-departure distribution per `(purpose, group)` is the right regional target for
     the model's first departures.
  5. Later trips and activity durations transfer from the national MiD donor without regional
     adjustment.
  6. The expected effects of the pre-registered A/B are assumptions until measured.
  7. `WORK_ACTIVITY_MAX_H` (20 h), inherited from `srv_plan_structure`, is generalised from work
     activities to EVERY purpose as the duration-reference plausibility ceiling: no
     purpose-specific ceiling is available.
  8. `COARSENED_SHARE_WARN` (0.25) and `UNMAPPED_SHARE_WARN_THRESHOLD` (0.05) are OBSERVABILITY
     thresholds, not scientific bounds; only `departure_time_mapping_max_median_shift_hours` is
     caller-set.
  9. **The group input is ONE attribute source at every call site (ruling A-R17, final fix wave
     item 1).** The harmonised group a person is calibrated in (`braunschweig.popsim.
     departure_time_model.person_groups`) is fed by the population's own IMPUTED `employed`
     (`braunschweig.popsim.attributes.map_employed`, which imputes an unknown `P_TAET`=99 from the
     valid pool within the same age group) via `persons_from_synthetic_schema`, at the
     pre-assignment trip build (`trips_stage.run`), the reporting-day plan replacement
     (`plan_replacement.build_day_trips`) AND the comparison stage
     (`departure_time_vs_srv.execute`) alike -- so the analysis measures the SAME groups the model
     was calibrated against. The whole-branch review found the trip build had instead used
     `persons_from_mid_schema` (raw `P_TAET`, NO imputation, an unknown code treated as NOT
     employed), which could put the SAME person in a different group at the trip build than at the
     plan replacement / comparison stage. `persons_from_mid_schema` is kept as a TESTED UTILITY,
     not a production call site.
  10. `min_model_n` is assumed adequate for BOTH call sites (see the limitation above).
  11. The purposes the mapping cells are keyed on are the CORRECTED ones of ADR-0111 (W_ZWECK 10
     folds to leisure) and ADR-0112 (a paired passive escort leg takes the adult's purpose), and
     the population the model runs on carries the general day-absence state of ADR-0110 (an absent
     person simply has no trips to shift). This package therefore runs on top of those decisions
     and its measured effects are not separable from them.
- **Evidence:** issue **#123** (validate departure times and activity durations -- the measurement
  half) and **#384** (the model); spec
  `docs/superpowers/specs/2026-09-09-departure-time-srv-mapping-design.md` (sections 1-6 are the
  source of every number and rule in this record) and its programme document
  `2026-09-09-day-structure-programme.md`; related **ADR-0104** (the reporting-day view and the
  "jitter exactly once, keyed by the receiving person" rule this generalises), **ADR-0110** (the
  general day-absence draw the population carries), **ADR-0111** / **ADR-0112** / **ADR-0113**
  (the purpose corrections the mapping cells are keyed on), **ADR-0102** / **ADR-0103** (SrV as
  the regional distance-calibration reference -- the distance analogue of this decision),
  **ADR-0105** (deterministic stage hashes, which the helper-hash tokens rely on); code
  `braunschweig/calibration/reported_time_precision.py`,
  `braunschweig/calibration/srv_departure_times.py`, `scripts/extract_srv_departure_times.py`,
  `braunschweig/popsim/departure_time_model.py`, `braunschweig/popsim/trips_stage.py`,
  `braunschweig/popsim/stage/config_keys.py`, `braunschweig/popsim/sources/entd.py`,
  `braunschweig/synthesis/commute_day/plan_replacement.py`,
  `braunschweig/synthesis/commute_day/trips_day_stage.py`,
  `braunschweig/analysis/departure_time.py`,
  `braunschweig/analysis/synthesis/departure_time_vs_srv.py`; tests
  `tests/test_reported_time_precision.py`, `tests/test_srv_departure_times.py`,
  `tests/test_departure_time_model.py`, `tests/test_popsim_trips_stage.py`,
  `tests/test_commute_day_plan_replacement.py`, `tests/test_commute_day_stages.py`,
  `tests/test_departure_time_vs_srv.py`; committed references
  `eqasim-data/data/braunschweig/srv/srv2023_departure_time_reference.csv` and
  `srv2023_activity_duration_reference.csv`; feature record
  `docs/registry/features/departure_time_model.yml`; stage records
  `docs/registry/stages/synthesis.population.trips.yml`,
  `synthesis.population.trips.final.yml`,
  `braunschweig.analysis.synthesis.departure_time_vs_srv.yml`; data records
  `docs/registry/data/srv2023_departure_time_reference.yml`,
  `srv2023_activity_duration_reference.yml`; contributor note
  `docs/codebase/notes/departure-time-model.md`.
  **No run has executed this model as of this record.** The pre-registered A/B in Consequences is
  a plan, not a result; convergence of a MATSim run is not validation and is not claimed here.
