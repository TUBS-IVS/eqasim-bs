# ADR-0089 · 2026-08-18 · The PT subscription gets a three-group per-Kreis control, blended MiD × SrV on a matched 14+ universe (issue #321)

- **Status:** active
- **Context:** ADR-0079 item 5 left the PT subscription as the explicit OPEN question: under
  `popsim_mid` it is donor-inherited from `P_FKARTE` and raked to nothing, and the ADR required
  the realised share to be MEASURED before designing a control. #307 measured it on the 100 %
  population (`docs/runs/i307-license-pt-measure-2026-08-18.yml`): the flatrate share is
  **+3.06 pp** too high regionally (22.06 % vs 19.0 %), **+4.18 pp** in Braunschweig, +8.50 pp in
  Wolfsburg, and the 9-category SHAPE is worse than the aggregate (Braunschweig `fahre_nie`
  +14.27 pp against `einzelfahrschein` −10.21 pp). `pt_ticket_type` also carries the worst SRMSE
  of all ten validated controls (0.457) at only 2.39 pp mean absolute deviation — a structure
  error, not a level error. It matters because
  `org.eqasim.braunschweig.mode_choice.BraunschweigPtCostModel.calculateCost_MU` returns `0.0`
  for holders, so the attribute acts on every PT decision rather than at a gate.
- **Decision:**
  1. **Steer THREE groups, not the nine P24.1 categories:** `deutschlandticket` /
     `other_flatrate` / `not_flatrate`. Because `calculateCost_MU` zeroes the fare for every
     flatrate holder, the four flatrate TYPES are simulation-equivalent and the split among the
     non-flatrate types has NO simulation effect at all. Nine categories × 8 Kreise would be 72
     control columns, most of them steering simulation-neutral structure; three groups are 24.
     The Deutschlandticket keeps its own group because it is the only flatrate category with a
     second independent survey and the natural policy lever.
  2. **Derive the group from the RESOLVED category, never a second time from the raw code.**
     `attributes.map_pt_ticket_group` collapses `pt_subscription_type`; only the
     Deutschlandticket is named and the rest of the flatrate set comes from
     `PT_TICKET_FLATRATE`, which stays the single owner of "grants unlimited rides". Therefore
     `deutschlandticket + other_flatrate == has_pt_subscription` by construction and the control
     cannot steer a quantity that differs from what the fare model reads (the ADR-0087 defect).
     The mapper raises when the resolved column is absent.
  3. **Blend MiD × SrV on a MATCHED universe** — this reverses an earlier draft of this ADR
     which had MiD as the sole source. That draft rested on two claims that measurement did not
     support:
     - *"SrV offers only one category."* True of the committed all-ages aggregate, false of the
       microdata: `E_OEV_FK` has six substantive codes, and code 3 "Zeitkarte (außer
       Deutschland-Ticket)" maps almost exactly onto `other_flatrate`. A new extractor table
       `srv2023_ticket_groups_14plus_by_kreis.csv` provides the three groups at 14+.
     - *"The surveys are far apart."* That gap was largely a universe artefact of the earlier
       comparison (all-ages SrV 6.08 % against 14+ MiD 10 %). On the matched 14+ base the
       **flatrate aggregate agrees to 1.63 pp regionally and 0.35 pp in Braunschweig**
       (SrV 17.38 % / 25.65 % vs MiD 19.00 % / 26.00 %).
     Per Kreis they still disagree materially (Goslar −7.99 pp, Salzgitter −6.71 pp, Helmstedt
     −6.09 pp; Peine +2.56 pp, Wolfenbüttel +2.87 pp), and SrV carries 1.6–2.2× the sample
     (15,746 vs 9,642 at 14+; Braunschweig 3,844 vs 1,774) with weights built for
     cross-municipality regional statements (ADR-0055). This is precisely the configuration
     `blended_targets.blend_kreis_target` exists for, so the target gets the SAME treatment as
     every other per-Kreis target here: precision blend within the 5 pp tolerance
     (Braunschweig, Peine, Wolfenbüttel), MiD shrunk toward the region row where the two
     disagree without an arbiter (Salzgitter, Gifhorn, Helmstedt, Goslar), MiD for Wolfsburg
     (outside the SrV survey area) and for the `Gesamt` row.
  4. **Tier `soft`, `min_age=14`.** Soft because the level is not pinned down: MiD's
     Deutschlandticket component sits 3.07 pp above SrV's on the same base, so its flatrate
     aggregate may be biased high, and a hard control would force a level the evidence does not
     establish. `min_age=14` because P24.1 is an "ab 14 Jahre" table — both the seed expression
     and the per-Kreis person total it partitions are restricted, avoiding the #97 universe trap.
  5. **ADR-0060's objection is narrowed, not overruled.** That ADR rejected an SrV
     PT-subscription control because `E_OEV_FK` is usage-conditional (asked of persons with PT
     use in the last 12 months) and "MiD is the better source". That objection stands for the
     ticket-type SHAPE. For the FLATRATE construct the extraction keeps non-users in the
     universe as `not_flatrate`, and the measured 0.35–1.63 pp agreement is the evidence that
     the two constructs are comparable at that level. SrV enters as one half of a blend, never
     as a raw override.
- **Assumptions, stated because they carry real weight:**
  - `E_OEV_FK == -8` ("nicht erhoben", no PT use in the past 12 months) is counted as
    `not_flatrate`. That is **5,213 of 15,746 persons (33.1 %)** of the SrV 14+ universe whose
    ticket status is inferred rather than reported. Defensible for the flatrate boolean (a
    subscription holder would use PT), but it is an assumption over a third of the sample.
  - `E_OEV_FK == 60` (Freifahrtberechtigung: children, severely disabled persons) is counted as
    `not_flatrate` although those persons pay nothing on PT. MiD P24.1 has no pendant category,
    and both halves of a blend must carry the same construct. Measured effect of the choice:
    **+0.78 pp** on the regional flatrate share if counted as flatrate instead. The
    simulation-side mismatch (they are fare-free in reality but not flagged in the model) is
    recorded here rather than silently absorbed.
  - **The blend weights the two surveys by RAW sample size, which overstates SrV's precision.**
    `blend_kreis_target` uses `n_unweighted` as the precision weight (Braunschweig: SrV 3,844
    against MiD 1,774). SrV is a stratified PSU design whose expansion factors vary 18x-70x
    across strata (ADR-0055), so its EFFECTIVE sample size is smaller than the raw count -- a
    design effect the framework does not model. This is the existing convention for all ten
    per-Kreis targets, not something introduced here, and two things bound its impact for this
    target: Braunschweig and Salzgitter are each a SINGLE stratum (`stratum 173` / `stratum 100`
    carry the same n as the Kreis row), so within-Kreis weight variation and hence the design
    effect are negligible there; and the precision blend fired for only three Kreise
    (Braunschweig, Peine, Wolfenbuettel) -- the four rural disagreement cases took the shrink
    path, where the raw-n weighting plays no role. It remains a real caveat for Peine and
    Wolfenbuettel, which are mixtures of rural strata.
  - **The SrV weighted totals are NOT Kreis populations.** The all-ages table sums to 787,251
    weighted persons against roughly 1,002,608 census inhabitants in the seven covered Kreise
    (~79 %), because `GEWICHT_*_ZENSUS` expands per SAMPLED municipality (~44 of them) rather
    than to the full Kreis. The SHARES are therefore coverage estimates for the Kreis under the
    assumption that the sampled municipalities represent it -- stated in the source table header
    as ASSUMPTION-grade, and the reason the per-stratum level is the design-safe one. Verified
    weighting on the extraction side: `GEWICHT_P_ZENSUS` (the expansion weight ADR-0055
    mandates, not the stratum-internal `GEWICHT_P`), applied to the weight-validated frame, and
    internally consistent (14+ total 688,010 against 17+ 662,733, i.e. +3.81 % for three
    additional cohorts).

- **Rejected alternatives:**
  - *Control all nine P24.1 categories.* 72 control columns to steer mostly simulation-neutral
    structure, competing for the same household weights as every other control.
  - *SrV as the sole target.* Wolfsburg has no SrV coverage at all, the per-Kreis rows are
    documented ASSUMPTION-grade coverage estimates (≈44 sampled municipalities extrapolated to
    full Kreise; the design-safe level is per-stratum), and the usage-conditional construct is
    weakest exactly where the disagreement is largest.
  - *Post-hoc raking of the ticket attribute* (mechanism C in the #307 brainstorm). Surgical for
    one number but it decouples the ticket from the household whose travel diary the person's
    trips come from — the very argument ADR-0079 used against porting the legacy draws.
  - *Leave it uncontrolled and report a limitation.* Defensible for the licence (ADR-0079 item 6,
    level unremarkable) but not here: the error is in the direction of making PT too cheap, on a
    quantity that acts continuously.
- **Consequences:** the synthetic population changes when the control is on — that is the point,
  and the size is NOT claimed here. Owed before this can claim more than "implemented":
  a 1 % smoke to show the balancer still converges with 24 additional KREIS control columns,
  then a 100 % run with the realised shares re-measured via
  `scripts/measure_license_pt_shares.py` and recorded in a run manifest, plus an A/B against the
  toggle OFF. Hitting a committed margin is control FIT, not behavioural validation. The
  measurement will also be confounded with ADR-0088 (fine teen age bands) unless the A/B varies
  one flag at a time, since the flatrate share is strongly age-graded.
