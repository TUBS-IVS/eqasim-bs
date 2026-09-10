# ADR-0115 · 2026-09-10 · Leisure legs without a detail purpose form their own subtype
- **Status:** active
- **Numbering:** ADR-0115 is the next free id after ADR-0114. Verified on 2026-09-10 before this
  record was written, by the check ADR-0111's and ADR-0113's Numbering paragraphs record:
  `git fetch origin`, then `git for-each-ref --format='%(refname)' refs/remotes/origin refs/heads`
  (52 refs -- 19 under `refs/remotes/origin`, including `origin/HEAD`, and 33 local
  `refs/heads`, i.e. every worktree branch of this checkout), each ref's decision tree listed with
  `git ls-tree -r --name-only <ref> -- docs/decisions` and grepped for `ADR-01[0-9][0-9]`. The
  highest id found on any ref is **ADR-0114**, carried by exactly one ref -- the LOCAL head
  `feature/i123-departure-time-model` (sibling branch A of this wave) -- so 0115 is free
  everywhere. The `ADR-0115` strings that were already in the working tree before this file
  existed are the forward references Tasks 2-4 of this plan placed in
  `braunschweig/popsim/purpose_subtype.py`, the two consumer stages, the crosswalk, the tests and
  the committed reference/summary headers. Ids are append-only, so a colliding draft on a sibling
  branch is renumbered, never this one.
- **Context:** With `w_zweck_10_as_leisure` on (ADR-0111, production `true`), MiD `W_ZWECK` 10
  "anderer Zweck" legs are leisure legs of the model. They carry no `W_ZWD` leisure detail code,
  so the four `W_ZWD` leisure groups of ADR-0026/ADR-0057 (`leisure_local`, `leisure_visit`,
  `leisure_activity`, `leisure_excursion`) cannot describe them at all.

  **Size and distance profile, from the committed
  `eqasim-data/data/braunschweig/mid/mid2023_w_zwd_group_reference.csv`** (weekday non-rbW leg
  universe of `scripts/extract_mid_w_zwd_groups.py`; `share_within_purpose` is the `W_GEW` share
  among the LABELLED legs of the purpose, `km_*` are `wegkm_imp` percentiles). Under the
  `codeplan_unspecified` spec variant -- the PRODUCTION composition of this decision -- the
  leisure block reads:

  | group | n unweighted | share within labelled leisure | km p25 / p50 / p75 |
  |---|---:|---:|---|
  | `leisure_local` | 21,538 | 0.2058 | 1.90 / 2.94 / 4.90 |
  | `leisure_visit` | 8,003 | 0.0874 | 1.96 / 4.90 / 14.25 |
  | `leisure_activity` | 25,650 | 0.2414 | 1.96 / 4.75 / 9.80 |
  | `leisure_excursion` | 3,591 | 0.0330 | 9.73 / 36.18 / 108.43 |
  | **`leisure_unspecified`** | **39,429** | **0.4324** | **1.27 / 3.26 / 9.50** |

  So the code-10 legs are 43.24 % of the labelled leisure mass on the WEEKDAY reference universe
  (the committed row above). The decider estimates on ALL MiD Wege rows, where the marginal is
  about 0.39 (0.3867; ad-hoc probe 2026-09-10, final review -- see the "Two universes" consequence
  below). Their distance profile differs from every named group except `leisure_local` -- their p75
  (9.5 km) is nearly twice `leisure_local`'s (4.9 km) while their p50 (3.26 km) sits between
  `leisure_local` and `leisure_visit`. Their distance information is complete
  (`n_missing_distance = 0` for all 39,429 legs). The same file's Coverage header records the
  block: `codeplan_unspecified/leisure: 98211/141987 legs labelled (69.17%), 43776 sentinel
  (30.83%)`.

  The regional survey has the same KIND of leg: SrV 2023 (Braunschweig + RGB) `V_ZWECK` 18
  "Andere Freizeitaktivitaet", committed in
  `eqasim-data/data/braunschweig/srv/srv2023_fine_purpose_reference.csv` as
  `leisure,18,Andere Freizeitaktivitaet,1700,0.1795671611,0.9893404658,2.95581787,7.414065784,15`
  -- 17.96 % of SrV leisure trips, GIS-length p25/p50/p75 = 0.99 / 2.96 / 7.41 km. Neither
  residual was compared before this decision.

  **What the model did until now.** The leisure subtype decider
  (`secondary_chainsolvers.deciders._build_leisure_subtype_decider`) estimates
  `P(group | mode, tt_band)` on the LABELLED code-7 legs and imputes one of the four `W_ZWD`
  groups onto EVERY synthetic leisure activity; the leg then draws its desired distance from that
  group's layer (`distance_distributions.run`, Step 8). The code-10 legs enter only the aggregate
  `leisure` layer, which the split never reaches. Their share of the leisure universe -- the mass
  that therefore drew its distance from layers estimated on OTHER legs -- has two denominators,
  and both are named here because they differ: **31.1 % of the `W_GEW` mass of all `W_ZWECK`
  {7, 10} weekday legs** (ad-hoc probe, 2026-09-10) and **43.24 % of the LABELLED weekday
  reference mass** (the committed row above, which is conditional on a leg being labelled).

  **Ad-hoc probes, 2026-09-10, not reproduced by committed code** (quoted only where the argument
  below needs them, and labelled as such wherever they appear):
  - The code-10 legs carry only design sentinels in `W_ZWD` -- 2202 "im PAPI nicht erhoben"
    (44.8 % of their `W_GEW` mass), 7704 "kein Einkaufs-/Erledigungs-/Freizeitweg" (43.5 %) and
    4402 "Kind unter 14" (11.7 %). On the same probe they are 31.1 % of the `W_GEW` mass of the
    whole leisure universe `W_ZWECK in {7, 10}` (a different, larger denominator than the 43.24 %
    committed row above, which is conditional on being labelled).
  - The AGE universe does not explain the `leisure_visit` gap the measurement package flags: SrV
    persons aged 14+ have a visit share of 20.9 % (all persons 21.5 %), and the
    comparable-universe delta computed at 14+ on both sides is -10.0 pp, i.e. essentially the
    full-universe value.
  - The gap is age-structured on the MiD side only: the MiD labelled visit share falls from
    22-24 % (14-29 years) to about 10 % (50+), while SrV stays at 20-23 % in every adult band.
    The labelled MiD universe is YOUNGER than the full MiD (73 % of the PAPI-sentinel legs are
    50+), so the national visit share over ALL leisure legs is if anything lower than the
    labelled 14.5 %.

  ADR-0113 additionally stated the candidate rule of the SrV measurement package on the RAW
  within-purpose shares. For leisure the two sides do not cover the same activity mass, so those
  shares are conditional on different universes; that rule is replaced by the comparable-universe
  rule in ADR-0113 itself (edited in place, Decision 4/6), and this record carries the residual
  pair that made the asymmetry explicit.
- **Decision:** The code-10 legs become a fifth leisure subtype of their own, and the measurement
  package reports the two surveys' residuals as a pair.

  1. **Definition.** `leisure_unspecified` is a leisure leg whose RAW MiD purpose is `W_ZWECK` 10
     -- labelled by `W_ZWECK`, never by `W_ZWD`. Constants in
     `braunschweig/popsim/purpose_subtype.py`: `LEISURE_UNSPECIFIED_GROUP` and
     `LEISURE_UNSPECIFIED_ZWECK`, the latter built from the existing
     `braunschweig.popsim.trips.W_ZWECK_OTHER_CODE` rather than retyping the literal 10.
  2. **Spec mechanism.** `SubtypeSpec` gains `zweck_groups` (group name -> frozenset of raw
     `W_ZWECK` codes) with `__post_init__` validation (a zweck-group name may not clash with a
     `group_col` group, every code must be in `zweck_values`, code sets must be disjoint), the
     property `group_names` (the vocabulary every consumer iterates) and the derived
     `zweck_group_codes`. One shared helper, `purpose_subtype.label_legs`, implements the
     labelling rule for BOTH callers (`estimate_group_probabilities` and the reference extraction
     `scripts/extract_mid_w_zwd_groups.py`): the zweck group wins over the `W_ZWD` detail code.
     `code_coverage_guard` and `impute_groups` are unchanged as FUNCTIONS, and the guard is now
     CALLED on both sides of the rule (final review M-4, ruling R14): the reference extraction
     called it already, and `_build_leisure_subtype_decider` now calls it on the prepared frame
     before estimating, so an unknown `W_ZWD` code on a leg of the spec's `W_ZWECK` universe
     raises in the stage instead of being dropped into the unlabelled share. Measured on the
     2026-09-10 raw delivery: the guard passes for all four leisure specs (no unmapped code on
     the full-year universe). `_build_other_subtype_decider` is deliberately UNCHANGED -- it has
     never guarded its specs (pre-existing since issue #127) and widening it there is a separate
     change, out of this decision's scope.
  3. **Spec selection.** `leisure_spec(codeplan_sentinels, unspecified_subtype=False)` returns one
     of four module constants BY IDENTITY (`LEISURE_SPEC`, `LEISURE_SPEC_CODEPLAN`,
     `LEISURE_SPEC_UNSPECIFIED`, `LEISURE_SPEC_CODEPLAN_UNSPECIFIED`), the two new ones built from
     the unchanged base by `_add_zweck_group`. The keyword default `False` keeps every existing
     call byte-identical by construction, not by a golden.
  4. **Config key.** `config_keys.KEY_LEISURE_UNSPECIFIED_SUBTYPE = "leisure_unspecified_subtype"`
     / `DEFAULT_LEISURE_UNSPECIFIED_SUBTYPE = True` is the single home; production `true` in
     `configs/base_bs.yml`. Declared in BOTH consumers' `configure()`
     (`braunschweig.popsim.distance_distributions`,
     `braunschweig.synthesis.locations.secondary_chainsolvers`) from that one constant pair and
     read with the one-argument execute-context form. NOT in `ENTD_REJECTED_KEYS` (the trip build
     never reads it -- the same reasoning as the codeplan flag, ADR-0113).
  5. **Configure-time guard.** Both stages raise a `ValueError` naming both keys when
     `leisure_unspecified_subtype` is true, `w_zweck_10_as_leisure` is false AND
     `secondary_leisure_subtype_split` is on: with the fold off no code-10 leg is leisure, so the
     class would be estimated and never realised. The guard is deliberately SCOPED to the split
     (controller ruling R8): with the split off there is no decider and no subtype layer at all,
     the flag is inert, and a `popsim_open` fixture that never sets it must not be aborted by it.
     The chainsolver stage therefore also declares `KEY_W_ZWECK_10_AS_LEISURE` -- purely so the
     requirement is checkable at configure time; that stage never applies the fold itself.
  6. **Distance layer.** `distance_distributions.run(..., leisure_unspecified_subtype=False)`
     builds `out["leisure_unspecified"]` in a new Step 8b via
     `_build_leisure_unspecified_layer(df)`, AFTER the four `W_ZWD` layers and OUTSIDE the
     `W_ZWD`-present branch -- the group is defined by the raw `W_ZWECK` code, so no `W_ZWD`
     column is needed. An absent `W_ZWECK` column RAISES (the flag being on IS the request for
     this layer; no silent skip). The four `W_ZWD` layers and the aggregate `leisure` layer are
     unchanged.
  7. **Decider.** `_build_leisure_subtype_decider` reads the key and estimates from
     `leisure_spec(codeplan_sentinels, unspecified_subtype)`, so `group_names` carries the fifth
     name and the build-time marginal line shows its share. One uniform draw per leg as before;
     the RNG stream is untouched.
  8. **Chainsolver vocabulary.** `secondary_chainsolvers/activity_types.LEISURE_SUBTYPE_ACTIVITIES`
     gains `"leisure_unspecified"` UNCONDITIONALLY (it is a vocabulary, not a flag): the potential
     column map sends it to `pot_leisure`, `plans.py` counts it in `subtype_stats`,
     `results._extract_locations` maps it back to the eqasim purpose `leisure`, and the
     excursion clip report ignores it. It never uses the residential `pot_visit` pool. With the
     flag off the name exists and counts 0.
  9. **Sanity range.** `secondary_measurement.SUBTYPE_DONOR_MEAN_KM_RANGE["leisure_unspecified"]`
     is the point `(12.7, 12.7)` km. AD-HOC MEASUREMENT of 2026-09-10 on the local-only raw MiD
     2023 B1 `MiD2023_Wege.csv`, taken on the frame the LAYER is built from (final review I-1,
     ruling R13): `distance_distributions.run`'s own `"leisure_unspecified"` layer under the
     production flag set, i.e. every delivered Wege row (`mid.load_mid_wege`; no `kernwo` and no
     `W_RBW` filter), mapped by `trips.map_purpose` with `w_zweck_10_as_leisure`, reduced to the
     legs with a usable travel time and to those whose two ends are not both primary activities,
     then `following_purpose == "leisure"` and `W_ZWECK` in `LEISURE_UNSPECIFIED_ZWECK`. On that
     frame, `wegkm_imp < 9994` (design codes; none of these legs carries one) clipped at 200 km,
     `W_GEW`-weighted: **12.69505 km over n = 54,658 legs**. The measurement was taken twice
     independently -- once from `run()`'s own layer arrays (`values` reconverted to km by
     `* DETOUR_FACTOR / 1000`, `weights` = `W_GEW`) and once from a replication of the stage's
     Steps 1-5 -- and the two agree to 1e-10 km.

     The WEEKDAY reference universe of `scripts/extract_mid_w_zwd_groups.py` gives 12.00732 km
     over n = 39,429 legs instead (`kernwo in {1, 2, 3}`, `W_RBW != 1`, `W_ZWECK == 10`, same
     filters otherwise; issue #373 task 3). That is the SAME method on a DIFFERENT universe, not
     a different measurement rule, and the band is pinned on the layer's own universe because
     that is the pool a realised leg actually draws from. Like every other entry of that dict it
     is an IN-SAMPLE donor mean and never a validation gate.
  10. **Measurement package: the residual pair.** `srv_fine_purpose.EXACTNESS_VALUES` gains the
      grade `residual` and `SUBTYPE_TO_SRV_FINE["leisure_unspecified"] = ((18,), "residual")`;
      `COMPARABLE_EXACTNESS = ("exact", "approximate")`. A `residual` pair is REPORTED in full
      (shares, counts and medians on both sides) but excluded from the comparable mass on BOTH
      sides, carries no `delta_pp_comparable`, and can never be a candidate. Committed row of
      `purpose_subtype_vs_srv_2026-09-10/comparison.csv`: MiD n 39,429 / share 0.4324 vs SrV 18
      n 1,700 / share 0.1796, raw `delta_pp` +25.29 pp REPORTED, medians 3.26 km (MiD
      `wegkm_imp`) vs 2.956 km (SrV GIS length). The two shares must not be differenced as a
      finding: they are the same KIND of leg but not the same SIZE (MiD 10 is a TOP-LEVEL answer
      -- the respondent did not choose "Freizeit" at all -- while SrV 18 is a sixth option offered
      after five named leisure activities).
  11. **Measurement package: the third spec variant.** `scripts/extract_mid_w_zwd_groups.py`
      gains `SPEC_VARIANTS = ("default", "codeplan", "codeplan_unspecified")`, the third being the
      PRODUCTION composition `leisure_spec(True, True)` / `other_errand_spec(True)` / shop
      unchanged, with `SPEC_VARIANT_DESCRIPTIONS` as the ONE home of the variant vocabulary
      (imported by the comparison script). The `default` and `codeplan` data rows are
      byte-identical to the previous generation (proof in the task report: an empty `diff` of the
      two blocks).
- **Rejected alternatives:**
  - **Spread the code-10 legs' distances into the four named layers by imputing them a group
    (rejected).** It has to GUESS which named group a leg without a detail belongs to, and it
    destroys an observed class whose distance profile differs from every named one but
    `leisure_local` (committed percentiles in Context).
  - **Leave the code-10 legs in the aggregate layer only, i.e. the status quo (rejected).** The
    share named in Context (31.1 % of the whole weekday leisure universe, ad-hoc; 43.24 % of the
    labelled weekday reference mass, committed row) keeps drawing its desired distance from layers
    estimated on other legs, and its own complete distance information stays unused.
  - **Map `leisure_unspecified` <-> SrV 18 as `approximate` and keep both inside the comparable
    universe (rejected).** The residual SIZES are instrument artefacts (0.4324 vs 0.1796 on the
    committed rows); including them would re-introduce exactly the denominator problem the
    comparable-universe rule removes.
  - **Keep the raw-delta candidate rule (rejected 2026-09-10; recorded in ADR-0113).** It compares
    two different universes and sits on a knife edge -- the same group answers differently by
    1 pp depending on the spec variant read.
  - **A relative (ratio) candidate measure (rejected).** It invents a second threshold and flags
    `shop_non_daily` as well.
  - **Age-conditioning the decider, PopulationSim controls on the visit share, or raking the
    decider's margin to SrV (all rejected here, deferred to the arm-B measurement).** Controls
    cannot reach a draw made inside the chainsolver and the seed lacks the subtype label for the
    sentinel legs; age-conditioning would move the model towards MiD's own age gradient and AWAY
    from SrV's flat adult level (ad-hoc probes above); raking is a level calibration, and the
    project rule is measure before calibrating. The realised visit share is measured by arm B of
    ADR-0113's pre-registered A/B and the level decision is taken from that measurement, recorded
    in the feature records `w_zwd_codeplan_sentinels` / `leisure_unspecified_subtype` -- this is
    deliberately NOT a separate issue, because the decision has an owner, a measurement and a
    written home already.
- **Consequences:**
  - **Two universes (final review I-1, ruling R13).** The leisure subtype DECIDER and the
    distance LAYERS estimate on every MiD Wege row their stage loads (`mid.load_mid_wege`), while
    the committed reference `mid2023_w_zwd_group_reference.csv` measures the WEEKDAY non-rbW legs
    (`filter_weekday_legs`). The mismatch is PRE-EXISTING -- the #127 layers and deciders have
    never been weekday-filtered -- and this decision does not change either universe. It does
    change what the records claim: on the all-day universe the production `leisure_unspecified`
    marginal is about 0.387 and the clipped donor mean about 12.7 km, against 0.4324 and 12.0 km
    on the weekday reference universe (ad-hoc probe 2026-09-10, final review). Consequently the
    arm-C rows below compare the realised shares against the DECIDER's own printed marginals
    (an IN-SAMPLE comparison on the estimation universe) and quote the weekday reference rows as
    CONTEXT only; a cross-universe comparison would show a difference even for a perfectly
    correct model. Whether the decider and the layers SHOULD be weekday-filtered is a model
    decision for the owner, recorded in the assessment of the feature record
    `docs/registry/features/leisure_unspecified_subtype.yml` (deliberately not a new issue: it
    has an owner, a measurement and a written home).
  - **The OFF path is output-identical, not byte-identical.** With `leisure_unspecified_subtype`
    off no leg can be tagged with the fifth name, so placement, distances, purposes and the RNG
    streams are identical to the pre-feature run
    (`tests/test_secondary_chainsolvers_subtypes.py::test_leisure_unspecified_offer_is_inert_when_the_flag_is_off`
    solves the same problems twice at one seed and asserts identical identifiers, coordinates,
    potentials, distances and activity types). Two artefacts DO differ and are recorded rather
    than hidden: the candidate frame carries one inert `leisure_unspecified` offer per
    leisure-offering building when the SrV location types are off (the vocabulary
    `LEISURE_SUBTYPE_ACTIVITIES` is unconditional by design, ruling R9), and the decider's
    build-time log line gains the `unspecified subtype: on|off` suffix.
  - **Cache devalidation is CHEAP and bounded.** Both
    `synthesis.population.spatial.secondary.distance_distributions` and
    `synthesis.population.spatial.secondary.locations` recompute: each declares the new config
    key, and both hash `braunschweig.popsim.purpose_subtype` (the chainsolver stage additionally
    hashes `braunschweig.popsim.trips` since this wave, because the new constant crosses that
    module boundary). PopulationSim and the trip build are NOT affected -- no seed column and no
    plan changes.
  - **The flag's CODE default is `True`, so the fifth subtype is ACTIVE from the implementing
    commit in every configuration that leaves the key unset and has
    `secondary_leisure_subtype_split` and `w_zweck_10_as_leisure` on** -- `configs/base_bs.yml`
    does. That is the project's "new features default on" rule working as intended, and it has
    one consequence for the pre-registered A/B: **arms A and B must set
    `leisure_unspecified_subtype: false` EXPLICITLY in their overlays**, because leaving the key
    unset now means ON. Arm C = arm B + `leisure_unspecified_subtype: true` (or unset).
  - **Pre-registered A/B, arm C (server; NOTHING has run at the time this record is written).**

    | metric | baseline | reference | expected in arm C (ASSUMPTION) |
    |---|---|---|---|
    | realised `leisure_unspecified` share of leisure legs | arm B (0 by construction) | the decider's own build-time marginal, printed by the chainsolver stage (IN-SAMPLE, all-day estimation universe) | within 2 pp of that marginal |
    | realised per-group leisure mean distances | arm B | `secondary_measurement.SUBTYPE_DONOR_MEAN_KM_RANGE` (in-sample sanity bands; `leisure_unspecified` = 12.7-12.7 km, measured on the LAYER's own all-day donor frame, Decision 9; the seven older entries are the 2026-07-09 spec taxonomy figures) | inside the band, sanity check only |
    | realised per-group leisure distance medians | arm B | `mid2023_w_zwd_group_reference.csv`, `codeplan_unspecified` rows (`km_p50`) -- the WEEKDAY reference universe, so this is a CROSS-UNIVERSE comparison and a difference is expected even for a correct model | shift < 1 km per named group |
    | realised whole-leisure distance median | arm B | direction only | moves DOWN versus arm B |
    | `candidate_for_reestimation` cells | 1 (`leisure_visit`, committed comparison) | the committed rule | still 1 -- a survey-mix result, unaffected by any run |

    SrV 18's GIS median of 2.956 km is reported beside the realised `leisure_unspecified` median
    as regional CONTEXT only: a GIS route length and the model's desired distance are different
    measures, and ADR-0075's Consequences record a MiD-vs-SrV distance level gap of about
    1.5-2x present in both arms of the #262 A/B. Every "expected" cell above is an ASSUMPTION
    until a run manifest records it; the feature record
    `docs/registry/features/leisure_unspecified_subtype.yml` stays `validation.state:
    unvalidated` with `runs: []`.
  - **The measurement package gained rows, not verdicts.** `comparison.csv` grew from 16 to 25
    rows (the nine `codeplan_unspecified` rows), every pre-existing measured value unchanged; its
    row ORDER changed, because the new variant sorts between `codeplan` and `default`, so a
    consumer must address a row by `(subtype_group, spec_variant)` and never by position. The
    committed reference tables remain measurement references that no synthesis, location or
    distribution stage reads.
  - **Nothing has run.** No model output is compared anywhere in this record: the committed
    package compares two SURVEY mixes, and the arm-C table is a plan.
- **Assumptions (explicit):**
  1. **The fold of code 10 to leisure is right** (ADR-0111, MiD's own `hwzweck1`); this decision
     does not revisit it and is inert without it.
  2. **Code-10 legs are modelled as ONE latent class conditional on (mode, tt_band).** They are
     the respondent's "other" answer and are heterogeneous in meaning; what is modelled is their
     DISTANCE distribution, not their activity. The grouping is an assumption, not a distance
     clustering like the four `W_ZWD` groups -- there is no detail code to cluster on.
  3. **"A code-10 leg carries no usable `W_ZWD` detail" is MEASURED on the 2026-09-10 raw
     delivery, not merely assumed:** the reference regeneration reports
     `39429 by W_ZWECK group (0 of those overriding a valid W_ZWD group code)`, i.e. not one of
     the 39,429 legs also carried a `W_ZWD` code that belongs to a named group. It remains an
     assumption for FUTURE deliveries, which is why `label_legs` counts the override rate and
     WARNs when it is non-zero (CLAUDE.md "Fallback transparency").
  4. **`CANDIDATE_DELTA_PP_THRESHOLD = 10.0` is a relevance line, not a derived bound** (ADR-0113
     Assumption; the summary states it verbatim).
  5. **The crosswalk grades, including the new `residual` grade, are a READING of two codebooks**,
     not a published or validated crosswalk.
  6. **The SrV GIS length, the MiD `wegkm_imp` and the model's desired distance are three
     different measures.** SrV 18 is CONTEXT for the unspecified layer, never a target.
  7. **Nothing has run:** every expected arm-C value is an ASSUMPTION until a run manifest
     records it.
- **Evidence:** issues **#373** (the code-10 story this completes) and **#242** (item 8 of the
  measurement review); **ADR-0111** (code 10 -> leisure), **ADR-0113** (codeplan sentinels, the
  SrV measurement package and -- edited in place by this wave -- the comparable-universe candidate
  rule), **ADR-0026 / ADR-0057** (purpose-resolved secondary distances), **ADR-0075** (SrV
  location types own the placement substrate; the GIS-vs-desired level gap). Design spec
  `docs/superpowers/specs/2026-09-10-leisure-unspecified-subtype-design.md` (sections 1, 2, 3, 5).
  Committed data:
  `eqasim-data/data/braunschweig/mid/mid2023_w_zwd_group_reference.csv` (data record
  `mid2023_w_zwd_group_reference`; the `codeplan_unspecified` block),
  `eqasim-data/data/braunschweig/srv/srv2023_fine_purpose_reference.csv` (data record
  `srv2023_fine_purpose_reference`; the `leisure,18` row) and
  `eqasim-data/data/braunschweig/calibration/purpose_subtype_vs_srv_2026-09-10/{comparison.csv,summary.md}`.
  Code: `braunschweig/popsim/purpose_subtype.py` (`SubtypeSpec.zweck_groups`, `group_names`,
  `zweck_group_codes`, `label_legs`, `leisure_spec`, `LEISURE_UNSPECIFIED_GROUP`,
  `LEISURE_UNSPECIFIED_ZWECK`, `_add_zweck_group`), `braunschweig/popsim/stage/config_keys.py`
  (`KEY_LEISURE_UNSPECIFIED_SUBTYPE` / `DEFAULT_LEISURE_UNSPECIFIED_SUBTYPE`),
  `braunschweig/popsim/distance_distributions.py` (`_build_leisure_unspecified_layer`, Step 8b,
  the `configure()` guard), `braunschweig/synthesis/locations/secondary_chainsolvers/`
  (`__init__.configure`, `deciders._build_leisure_subtype_decider`,
  `activity_types.LEISURE_SUBTYPE_ACTIVITIES`), `braunschweig/calibration/secondary_measurement.py`
  (`SUBTYPE_DONOR_MEAN_KM_RANGE`), `braunschweig/calibration/srv_fine_purpose.py`
  (`EXACTNESS_VALUES`, `COMPARABLE_EXACTNESS`, `SUBTYPE_TO_SRV_FINE`),
  `scripts/extract_mid_w_zwd_groups.py` (`SPEC_VARIANTS`, `SPEC_VARIANT_DESCRIPTIONS`),
  `scripts/compare_purpose_subtypes_srv.py` (`CANDIDATE_RULE`). Tests
  `tests/test_purpose_subtype.py`, `tests/test_distance_distributions_subtypes.py`,
  `tests/test_secondary_chainsolvers_subtypes.py`, `tests/test_srv_fine_purpose.py`,
  `tests/test_extract_mid_w_zwd_groups.py`, `tests/test_secondary_subtype_validation.py`.
  Feature record `docs/registry/features/leisure_unspecified_subtype.yml`; stage records
  `docs/registry/stages/synthesis.population.spatial.secondary.distance_distributions.yml` and
  `docs/registry/stages/synthesis.population.spatial.secondary.locations.yml`; feature doc
  `docs/features/secondary-distances.md`; contributor note
  `docs/codebase/notes/mid-purpose-mapping.md`.
  **No run has executed this code as of this record.**
