# ADR-0113 · 2026-09-10 · W_ZWD labels verified against the codeplan; the no-detail codes become sentinels
- **Status:** active
- **Numbering:** ADR-0113 is the id after ADR-0112 (this package allocates 0111-0113 together).
  Allocated 2026-09-09 and RE-VERIFIED on 2026-09-10 by the check ADR-0111's Numbering paragraph
  records: `git fetch origin`, then `git ls-tree -r --name-only <ref> -- docs/decisions` grepped for
  `ADR-01[0-9][0-9]` on every one of the 21 `refs/remotes/origin` heads and every one of the 36
  local `refs/heads`; the highest id anywhere is ADR-0110, so 0113 is free on every ref. The
  `ADR-0113` strings already in the working tree are the forward references Task 5 of this plan
  placed in `braunschweig/popsim/purpose_subtype.py`, the two consumer stages and their tests. Ids
  are append-only.
- **Context:** The W_ZWD subtype models of issue #127 (`braunschweig/popsim/purpose_subtype.py`,
  leisure in four groups, other-errand in two) were built in July 2026 WITHOUT the MiD codeplan: the
  grouping criterion was the measured `wegkm_imp` clustering of the detail codes, and every
  per-code semantic label carried a literal `label to verify (codeplan)` marker. The module stated
  its own reassignment rule for the two codes whose provisional membership depended on the missing
  labels: *if 799 turns out to be a no-assignment code, move it to `LEISURE_SENTINELS`* (and the
  analogous question for 601 in `other_errand_short`).

  The codeplan has now been read: `MiD2023_Codeplaene_B1_Standard_v1.1.xlsx`, sheet "Wege",
  variable `W_ZWD` (restricted local-only file, data record `mid2023_b1`). Every group member's
  label was verified cell by cell (spec section 1.3): 501 Einkauf taeglicher Bedarf · 502 sonstige
  Waren · 503 Stadt-/Einkaufsbummel · 504 Dienstleistungen · 505 sonstiger Einkaufszweck · 599
  Einkauf k.A. · 601 Arztbesuch/medizinisch · 602 Behoerde, Bank, Post · 603 private Erledigung fuer
  andere Person · 604 sonstiger Erledigungszweck · 605 Betreuung Familienmitglieder · 699 Erledigung
  k.A. · 701 Besuch/Treffen Freunde, Verwandte · 702 kulturelle Einrichtung · 703 Veranstaltung ·
  704 Sport selbst aktiv · 706 Restaurant/Gaststaette · 707 Schrebergarten/Wochenendhaus · 708
  Tagesausflug · 709 Urlaub · 710 Spaziergang · 711 Hund ausfuehren · 713 Kirche/Friedhof · 716
  Begleitung von Kindern (Spielplatz) · 720 sonstiger Freizeitzweck · 721 andere Treffen
  (Kurse, Hobby, Verein) · 722 Kurzreise · sentinels 2202 im PAPI nicht erhoben, 4402 Kind unter 14,
  7704 kein Einkaufs-/Erledigungs-/Freizeitweg, 7705 Weg ohne Info zum Wegezweck.

  Three outcomes:
  1. **The semantic grouping holds.** Every group is plausible under its verified labels (local =
     Gaststaette/Spaziergang/Hund/Kirche/Spielplatz; visit = 701; activity =
     Kultur/Veranstaltung/Sport/Garten/sonstiges/Kurse; excursion = Tagesausflug/Urlaub/Kurzreise;
     errand short = Arzt + Behoerde/Bank/Post; long = fuer andere/sonstiges/Betreuung). The 601
     boundary question is answered by the label: 601 "Arztbesuch/medizinisch" belongs with 602 in
     `other_errand_short`, which is also what the SrV crosswalk pairs it with (SrV `V_ZWECK` 10
     "Behoerdengang, Arztbesuch").
  2. **Two members are NO-DETAIL codes** and must be sentinels by the module's own rule: **799
     "Freizeit k.A."** (in `leisure_activity`) and **699 "Erledigung k.A."** (in
     `other_errand_long`). Sizes on the weekday non-rbW leg universe, from the committed
     `eqasim-data/data/braunschweig/mid/mid2023_w_zwd_group_reference.csv` header (labelled-leg
     counts per variant): 799 = **5,230 legs** (64,012 labelled leisure legs with the flag off vs
     58,782 with it on; 8.2 % of the labelled leisure legs, 5.1 % of all 102,558 leisure legs); 699 =
     **2,060 legs** (22,524 vs 20,464 labelled errand legs; 9.1 % of the labelled errand legs, 3.1 %
     of all 66,755). Spec section 2.3 quotes 3.7 % / 2.4 % from an ad-hoc measurement whose
     denominator it does not record; the committed counts above are the traceable figures and
     supersede them here.
  3. **Code 999 has no label in the verified excerpt.** It stays a sentinel in both specs, with the
     module stating exactly that ("label not in the verified codeplan excerpt; kept as a sentinel
     pending a dedicated codeplan lookup") rather than inventing a meaning.

  Issue #242 additionally asked whether the MiD-derived subtype mix is regionally plausible at all.
  SrV 2023 (Braunschweig + RGB) carries its own fine purposes (`V_ZWECK`) for shop, errand and
  leisure, so a crosswalk is possible for part of the taxonomy -- but only part: SrV 18 "Andere
  Freizeitaktivitaet" is 17.96 % of SrV leisure trips and had no MiD counterpart when this package
  was built (ADR-0115 pairs it with the new `leisure_unspecified` group under the `residual` grade:
  reported, but deliberately outside the comparable universe on both sides), and
  `leisure_excursion` has no SrV counterpart at all.
- **Decision:** Three things, one flag.

  1. **Every group comment carries the verified codebook label** (`purpose_subtype.py`); no "label
     to verify" marker is left anywhere in the module. The module docstring records the verification
     outcome and the cross-purpose intrusion note (a W_ZWD detail code can occur on a leg of a
     DIFFERENT `W_ZWECK`: 7 % of code-5 legs carry 701, 5 % 503, 5.1 % 504 -- spec section 1.3,
     ad-hoc measurement, not reproduced by committed code).
  2. **The two no-detail codes become sentinels under `purpose_subtype_codeplan_sentinels`**
     (declared default `true`, production `true` in `configs/base_bs.yml`). Mechanism:
     `LEISURE_SPEC_CODEPLAN` / `OTHER_ERRAND_SPEC_CODEPLAN` are built from the UNCHANGED
     `LEISURE_SPEC` / `OTHER_ERRAND_SPEC` by moving 799 / 699 out of their group into the spec's
     sentinel set; the selectors `leisure_spec(codeplan_sentinels)` / `other_errand_spec(...)`
     return the unflagged object BY IDENTITY when the flag is off
     (`leisure_spec(False) is LEISURE_SPEC`), so the OFF path is byte-identical by construction
     rather than by re-deriving an equal object. A sentinel leg leaves the estimation universe
     entirely -- numerator AND denominator -- exactly as every other sentinel does; it is still
     IMPUTED a group at application time, like every leg that carries a design sentinel -- 37.58 %
     of leisure legs, 66.26 % of other-errand legs and 40.10 % of shop legs with the flag OFF,
     rising to 42.68 % / 69.34 % / 40.10 % with it ON (committed Coverage header of
     `mid2023_w_zwd_group_reference.csv`; shop has no codeplan variant).
  3. **Both consumers read the same key.** `braunschweig.synthesis.locations.secondary_chainsolvers`
     (the leisure/other subtype deciders' ESTIMATION, via `deciders.py`'s two builders) and
     `braunschweig.popsim.distance_distributions` (the leisure/other subtype DISTANCE-layer donor
     pools, `run()` steps 8/9) declare it from the one constant pair
     `config_keys.KEY_PURPOSE_SUBTYPE_CODEPLAN_SENTINELS` / `DEFAULT_...`. Threading only one of them
     would make the two disagree about what `leisure_activity` means -- the decider would label a
     leg into a group whose distance layer still contained the excluded legs. The two DECIDERS'
     build-time log lines (`deciders.py`, leisure and other-errand) append `(codeplan no-detail
     sentinels: on/off)`, so the active spec is visible in the run log on the estimation side;
     `braunschweig.popsim.distance_distributions` logs the pool sizes without the marker, so on
     the distance side the setting has to be read from the resolved config (parked issue, no code
     change here). This is NOT a trip-build flag (no `trips_stage` / ENTD path reads it), so it is
     deliberately absent from `ENTD_REJECTED_KEYS`. `braunschweig.popsim.purpose_subtype` is folded
     into `secondary_chainsolvers._HELPER_MODULES` (controller ruling C-R15) so a future
     group-boundary edit cannot be served from a stale chainsolver cache.
  4. **The SrV comparison is a MEASUREMENT package, not a re-estimation.** Committed:
     `eqasim-data/data/braunschweig/srv/srv2023_fine_purpose_reference.csv` (per fine `V_ZWECK` the
     `GEWICHT_W_ZENSUS` share within its coarse purpose, n, GIS-length p25/p50/p75, `E_DAUER` p50;
     universe `MITTL_WERKTAG == 1`, GIS length valid for 14,639/19,106 = 76.62 % of measured trips),
     `eqasim-data/data/braunschweig/mid/mid2023_w_zwd_group_reference.csv` (per group the W_GEW share
     among LABELLED legs of its purpose and `wegkm_imp` percentiles, under BOTH settings of the flag)
     and `eqasim-data/data/braunschweig/calibration/purpose_subtype_vs_srv_2026-09-10/{comparison.csv,summary.md}`.
     The crosswalk `braunschweig.calibration.srv_fine_purpose.SUBTYPE_TO_SRV_FINE` grades every
     mapping `exact` / `approximate` / `residual` / `aggregate_only` (the `residual` grade and the
     third spec variant `codeplan_unspecified` were added by ADR-0115), and the reporting rule is
     stated verbatim in the committed summary, `scripts/compare_purpose_subtypes_srv.CANDIDATE_RULE`:

     ```
     candidate_for_reestimation = (exactness == "exact") and any over spec variants of (abs(delta_pp_comparable) > 10); delta_pp_comparable = delta_pp_renormalised where the purpose is asymmetric, else delta_pp, and EMPTY for a row outside the comparable universe (grade not in exact + approximate)
     ```

     The flag therefore reads the COMPARABLE universe -- each side renormalised to the mass of the
     rows both surveys name concretely (`exact` and `approximate`; a `residual` or `aggregate_only`
     row is unmapped mass on its own side and carries no `delta_pp_comparable` at all) -- and it is
     GROUP-level: a group whose comparable delta crosses the threshold in AT LEAST ONE spec variant
     is flagged on every one of its rows, and `candidate_variants` names the crossing variants,
     because the group is the unit of a possible re-estimation and a 1 pp difference between the
     variants must not give two answers for one group. **Nothing is re-estimated in this package**,
     and a flagged group triggers nothing automatic.

     Result (committed `comparison.csv`, `default` / `codeplan` variant; `delta_pp` = raw MiD share
     minus SrV share, `delta_pp_comparable` = the reading the flag uses -- identical to the raw one
     wherever the block is symmetric, which is every shop and other-errand row): `shop_daily`
     +7.90 / +7.90 (exact) · `shop_non_daily` -7.90 / -7.90 (exact) · `other_errand_short`
     -8.22 / -4.44 (approximate) · `other_errand_long` +8.22 / +4.44 (approximate) ·
     `leisure_visit` raw -7.01 / -6.10 but comparable **-10.88 / -9.86** (exact) · `leisure_local`
     raw -6.62 / -4.47, comparable -13.56 / -11.15 (approximate) · `leisure_activity` raw
     +26.12 / +22.72, comparable +24.44 / +21.00 (approximate) · `leisure_excursion` no SrV
     counterpart. **`leisure_visit` is the one flagged group**, via the `default` variant (-10.88 pp
     comparable, with `codeplan` -9.86 pp reported beside it); `leisure_local` and
     `leisure_activity` are further from SrV still but are graded `approximate`, so the rule leaves
     them unflagged by design. The flag ON (codeplan) variant is closer to SrV on all five cells
     whose delta moves at all (the two shop cells cannot move -- the shop split has no codeplan
     variant -- and `leisure_excursion` has no SrV counterpart).
  5. **`other_errand_short` is graded `approximate`, not `exact`** (controller ruling C-R16, a
     documented deviation from the plan's own interface block): the labels overlap the other member
     of the pair (MiD 602 "Behoerde, Bank, Post" sits in `other_errand_short` while SrV 11
     "Dienstleistungseinrichtung (z. B. Post, Bank, Friseur, Apotheke)" feeds `other_errand_long`),
     and with exactly two complementary groups `delta_short == -delta_long` identically, so the pair
     cannot carry two different grades for one and the same number.
  6. **The leisure comparison is reported with BOTH denominators, and the COMPARABLE one feeds the
     flag.** The mapped mass is symmetric for shop (1.0000/1.0000) and errand (1.0000/1.0000) but
     not for leisure (comparable mass MiD 0.9454 default / 0.9419 codeplan vs SrV 0.8204), so
     `comparison.csv` carries `share_*_renormalised` / `delta_pp_renormalised` for the leisure rows.
     The RAW delta is kept as information in `delta_pp`; the comparable reading is what
     `candidate_for_reestimation` computes on (Decision 4), because two shares conditional on
     different universes are not a difference. Consequence on the committed data:
     **`leisure_visit` IS flagged**, via the `default` variant (-7.01 pp raw, -10.88 pp comparable;
     `codeplan` -6.10 raw, -9.86 comparable). `summary.md` lists every row whose flag differs
     between the two readings in both directions, so the choice of reading stays visible rather
     than silently applied.
- **Rejected alternatives:**
  - **Keep 799 and 699 as ordinary group members (the status quo, rejected).** They are "keine
    Angabe" codes: counting them as `leisure_activity` / `other_errand_long` inflates exactly those
    two groups with legs whose detail purpose the respondent did not give, which is what the +26.12
    pp `leisure_activity` gap partly reflects (it falls to +22.72 pp when they leave). A no-answer
    code silently voting for one group is the same defect class the sentinel mechanism exists to
    prevent.
  - **Re-estimate the group boundaries from the SrV fine purposes now (rejected for this package).**
    The crosswalk exactness is an assumption (see Assumptions), and MiD-national vs SrV-regional
    differences are confounded with the taxonomy. A re-estimation would change the population on the
    strength of a measurement whose own caveats say it cannot carry that weight. The flag is now set
    on `leisure_visit` (Decision 4/6), and it still triggers no re-estimation: the level decision is
    taken from the arm-B measurement of the REALISED share, recorded in the feature record
    `w_zwd_codeplan_sentinels.yml`.
  - **Keep the raw-delta candidate rule (rejected 2026-09-10, owner decision recorded in ADR-0115).**
    `candidate_for_reestimation = (exactness == "exact") and (abs(delta_pp) > 10)` on the raw
    within-purpose shares compares two DIFFERENT universes wherever a purpose's crosswalk is
    asymmetric, and it sits on a knife edge: `leisure_visit` is -7.0 pp raw (not a candidate) but
    -10.9 pp on the comparable universe (a candidate), and the third spec variant added by ADR-0115
    moves its raw delta to -12.8 pp while its comparable delta stays at -9.9 pp. A rule that depends
    on 1 pp and on which spec variant one reads gives two answers for one group. Both readings stay
    committed; only the flag moved.
  - **Grade `other_errand_short` `exact` as the plan specified (rejected).** The codebooks contradict
    it (Decision 5). The plan's grade was an assumption, and an assumption a source disproves is a
    correction, not a deviation to be argued away.
  - **Move 999 out of the sentinel set (rejected).** Its label is not in the verified excerpt;
    inventing one would be an unsourced value. It stays a sentinel and the module says why.
- **Consequences:**
  - **The estimated group probabilities shift.** The leisure and other-errand subtype deciders and
    the corresponding distance layers are estimated on a smaller labelled universe (leisure 64,012 ->
    58,782 legs, errand 22,524 -> 20,464), so both the group marginals and the per-cell probabilities
    change; the imputed share of legs rises correspondingly (leisure sentinel share 37.58 % ->
    42.68 %, errand 66.26 % -> 69.34 %, committed header). This is a population change and is
    therefore flag-gated like every other one.
  - **Cache devalidation is CHEAP.** No seed column changes, so PopulationSim does NOT re-run: only
    `synthesis.population.spatial.secondary.locations` (the chainsolver deciders) and
    `synthesis.population.spatial.secondary.distance_distributions` recompute -- which is exactly why
    this flag is arm B of the A/B, toggled on top of arm A's cache.
  - **Pre-registered A/B (server, Task 9 of the SDD plan; NOTHING has run at the time this record is
    written).** Arm B = arm A + this flag.

    | metric | baseline | reference | expected in arm B (ASSUMPTION) |
    |---|---|---|---|
    | realised leisure subtype shares | arm A | `mid2023_w_zwd_group_reference.csv`, `codeplan` rows | within 2 pp per group of the codeplan estimation shares |
    | realised other-errand subtype shares | arm A | same file | within 2 pp per group |
    | subtype-conditional distance medians | arm A | same file (`km_p50`) | shift < 1 km per group |
    | `candidate_for_reestimation` cells | 1 (`leisure_visit`, committed comparison) | the stated rule | still 1 (`leisure_visit`) -- a survey-mix result, unaffected by any run |

    A metric moving the wrong way stops the ladder. Every "expected" cell is an ASSUMPTION until a
    run manifest records it; the feature record `docs/registry/features/w_zwd_codeplan_sentinels.yml`
    stays `validation.state: unvalidated`. Arm B must set `leisure_unspecified_subtype: false`
    explicitly in its overlay: that key's CODE default is `True` (ADR-0115), so leaving it unset
    would make arm B carry the fifth leisure subtype as well and stop measuring this flag alone.
    Arm C = arm B + `leisure_unspecified_subtype: true` (ADR-0115 Consequences).
  - **The two committed reference tables are measurement references, never targets.** Both headers
    say so; no synthesis, location or distribution stage reads either, and their only consumer is
    `scripts/compare_purpose_subtypes_srv.py`.
- **Assumptions (explicit):**
  1. **The crosswalk grades are a READING of two codebooks, not a published or validated crosswalk**
     (`SUBTYPE_TO_SRV_FINE`'s own docstring says so). The `candidate_for_reestimation` flag inherits
     that assumption twice over: the grade decides which rows enter the comparable universe AND
     which groups may be flagged at all, so the "`leisure_visit` is the one flagged group" result
     rests on it.
  2. **MiD is national, the SrV delivery is Braunschweig + RGB**, so every delta mixes a regional
     effect with a survey-instrument effect and cannot be attributed to either from these tables.
  3. **The MiD share is conditional on a leg being LABELLED** (30.7-62.4 % of a purpose's legs are;
     the rest carry a design sentinel). That is the universe the model estimates on, so it is the
     right comparison for "the mix the model reproduces", but it is NOT the mix of all MiD legs of
     that purpose.
  4. **The leisure comparison is structurally asymmetric** (SrV 18 outside the comparable universe,
     `leisure_excursion` without a counterpart), so the four leisure `share_srv` values do not sum
     to 1. BOTH readings stay committed -- `delta_pp` raw as information, `delta_pp_comparable` on
     the comparable universe -- and the comparable one is the reading the candidate flag uses
     (Decision 4/6). That choice is itself an assumption: it asserts that the mass each survey does
     NOT name concretely is an instrument artefact rather than a behavioural difference.
  5. **Code 999's meaning is unknown** and is treated as a sentinel on that basis.
- **Evidence:** issue **#242**; related **#127** (the W_ZWD subtype models this verifies),
  **ADR-0026 / ADR-0057** (purpose-resolved secondary distances), **ADR-0055** (`GEWICHT_W_ZENSUS`
  is the cross-stratum SrV weight), **ADR-0111** (the sibling flags of the same package),
  **ADR-0115** (issue #373: the fifth leisure subtype, the `residual` grade, the third spec variant
  and the comparable-universe candidate rule this record now states in Decision 4). Spec
  `docs/superpowers/specs/2026-09-09-purpose-correctness-design.md` sections 1.3 / 2.3 / 2.4 / 3.
  Committed data: `eqasim-data/data/braunschweig/mid/mid2023_w_zwd_group_reference.csv` (data record
  `mid2023_w_zwd_group_reference`), `eqasim-data/data/braunschweig/srv/srv2023_fine_purpose_reference.csv`
  (data record `srv2023_fine_purpose_reference`),
  `eqasim-data/data/braunschweig/calibration/purpose_subtype_vs_srv_2026-09-10/{comparison.csv,summary.md}`;
  codebooks `MiD2023_Codeplaene_B1_Standard_v1.1.xlsx` (sheet Wege, `W_ZWD`) and
  `SrV2023_Datenkodierung_SciUse.xlsx` (sheet Tabelle1, `V_ZWECK`, value labels in column F, cells
  F8236-F8256), both restricted local-only. Code `braunschweig/popsim/purpose_subtype.py`,
  `braunschweig/popsim/distance_distributions.py`,
  `braunschweig/synthesis/locations/secondary_chainsolvers/{__init__,deciders}.py`,
  `braunschweig/popsim/stage/config_keys.py`, `braunschweig/calibration/srv_fine_purpose.py`,
  `scripts/extract_srv_fine_purpose_reference.py`, `scripts/extract_mid_w_zwd_groups.py`,
  `scripts/compare_purpose_subtypes_srv.py`; tests `tests/test_purpose_subtype.py`,
  `tests/test_distance_distributions_subtypes.py`, `tests/test_secondary_chainsolvers_subtypes.py`,
  `tests/test_srv_fine_purpose.py`, `tests/test_extract_mid_w_zwd_groups.py`. Feature record
  `docs/registry/features/w_zwd_codeplan_sentinels.yml`; feature doc
  `docs/features/secondary-distances.md`; contributor note
  `docs/codebase/notes/mid-purpose-mapping.md`.
  **No run has executed this code as of this record**, and no model output is compared in the
  measurement package: it compares two SURVEY mixes. The A/B in Consequences is a plan, not a result.
