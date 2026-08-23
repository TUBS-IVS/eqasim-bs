# ADR-0099 · 2026-08-23 · `never_pt` becomes a fourth group in the PT-ticket per-Kreis control, and the ticket taxonomy turns English behind one raw-CSV boundary (issue #329)

- **Status:** active
- **Context:** The 2026-08-20 100 % run (`docs/runs/100pct-allfeat-i240-2026-08-20.yml`) showed
  that the composition INSIDE the uncontrolled `not_flatrate` group of the ADR-0089 three-group
  control is spatially inverted. Braunschweig-Stadt (03101) realises **30.17 %** "never uses PT"
  against a **14.00 %** MiD reading (**+16.17 pp**), traded against single/multi-ride tickets,
  while Goslar under-realises (−3.71 pp). The city with the best PT supply in the region therefore
  received the most never-PT people and the rural Kreis the fewest — the inversion, not the level,
  is the defect. The mechanism is the within-group-freedom defect class already recorded for #320
  and #240: the three-group control pins the group TOTAL per Kreis, and the balancer is then free
  to place never-PT donors wherever the remaining fine controls make them cheapest.
  A deep dive on 2026-08-21 established what is at stake. The split is **simulation-inert today**:
  a repo-wide grep of the `eqasim-java-bs` fork found **zero** Java readers of the population-XML
  attribute `ptSubscriptionType`. The only Java channel is the `hasPtSubscription` Boolean, read by
  `org.eqasim.braunschweig.mode_choice.BraunschweigPredictorUtils.hasSubscription` and consumed
  solely by `org.eqasim.braunschweig.mode_choice.BraunschweigPtCostModel.calculateCost_MU`
  (fare `0.0` for flatrate holders); `BraunschweigModeAvailability` grants `pt` unconditionally to
  every agent, so no ticket value gates PT. This ADR therefore records a **reference-fidelity fix,
  not a results lever**. The one indirect channel that remains is donor-diary correlation:
  `popsim_mid` copies whole MiD travel diaries, so which donors are chosen still shapes trips.
- **Decision:**
  1. **Split `not_flatrate` into `never_pt` and `occasional_ticket`** — a new person-level
     per-Kreis control `pt_ticket_group4` in `braunschweig.popsim.kreis_attribute_control.REGISTRY`
     (tier `soft`, `min_age=14`, `seed_column` `pt_ticket_group4`, consumed with `prior_n=0`).
     The four groups are `deutschlandticket / other_flatrate / never_pt / occasional_ticket`
     (8 Kreise × 4 = 32 control columns, inside the ADR-0089 budget). Tier and age bound are
     ADR-0089's verbatim, because the MiD level evidence has not improved: P24.1 is an
     "ab 14 Jahre" table, and a hard control would pin a level the evidence does not establish.
  2. **Config key `braunschweig.population.popsim.pt_ticket_never_group` (default `"on"`)**
     (`braunschweig.popsim.stage.config_keys.KEY_PT_TICKET_NEVER_GROUP`). ON, the stage renders
     `pt_ticket_group4` **INSTEAD OF** the three-group `pt_ticket_group` entry — never both, since
     the two control the same marginal at different resolutions and would double-constrain it.
     ON while the base `pt_ticket_kreis_control` is off raises a `ValueError` naming both keys
     (`braunschweig.popsim.stage.source_resolution`): fail-fast rather than a silent half-state.
     OFF restores the three-group behaviour, modulo the renames of item 4.
  3. **The target is a MiD × SrV blend with no arbiter**, exactly as ADR-0089's:
     `eqasim-data/data/braunschweig/targets/target2026_pt_ticket_group4_by_kreis.csv`, built by
     `build_pt_ticket_group4` in `scripts/build_blended_kreis_targets.py` from committed reference
     tables only. Its SrV half is the new
     `eqasim-data/data/braunschweig/srv/srv2023_ticket_groups4_14plus_by_kreis.csv`
     (`build_ticket_groups4_table` in `scripts/extract_srv_kreis_tables.py`, same 14+/`E_OEV_FK`
     universe as ADR-0089; unmapped codes raise). 03101 is a `blend` row (n_effective 5618:
     deutschlandticket 0.1088, other_flatrate 0.1488, never_pt 0.1232, occasional_ticket 0.6192),
     Wolfsburg (03103) and the `Gesamt` row fall back to MiD (Gesamt never_pt 0.36), and **SIX**
     disagreement cases take the `mid_shrunk` path: 03102, 03151, 03153, 03154, 03157, 03158. That
     is two MORE than the three-group table's four (03102, 03151, 03153, 03154) — 03157 (Peine) and
     03158 (Wolfenbüttel) were `blend` rows there and flip to `mid_shrunk` here, because the
     never/occasional split exposes a per-Kreis MiD-vs-SrV disagreement above the 5 pp tolerance
     that the three-group collapse into `not_flatrate` averaged away. The set is also not
     describable as "rural": 03102 is Salzgitter, a kreisfreie Stadt. The existing three-group
     table and target stay byte-identical, so the OFF path is unaffected on the data side — but
     the four-group table is NOT a pure re-partition of the three-group one at equal flatrate
     level; see the per-Kreis deltas under Consequences.
  4. **The synthesis taxonomy becomes English behind ONE raw-CSV boundary.**
     `PT_TICKET_CATEGORIES` in `braunschweig/data/mid/reference_tables.py` is now
     `single_ticket, multi_ride_ticket, deutschlandticket, weekly_monthly_no_subscription,
     monthly_or_annual_subscription, job_or_semester_ticket, other_ticket, never_pt, no_answer`,
     and `P24_RAW_COLUMN_BY_CATEGORY` in the same module maps them onto the committed reference
     CSVs' codebook-German headers. The CSVs themselves are deliberately UNCHANGED: their headers
     are the traceability link to the MiD instrument, so the translation happens once, at the
     loader boundary. `tests/test_no_german_pt_ticket_literals.py` keeps German PT-ticket literals
     out of code outside that mapping.
  5. **`no_answer` is renormalized OUT of the target and out of the acceptance metric.** MiD code
     99 is imputed pool-proportionally by the synthesis (see the corrected finding below), so the
     category is structurally unproducible; folding its mass into any other group would fabricate
     bias. `pt_ticket_target` in `braunschweig/analysis/population_validation/controls.py` now
     renormalizes over the 8 producible categories.
- **Corrected finding, recorded because the deep-dive report got it wrong:** MiD code 99
  ("keine Angabe") is **NOT** defaulted to `never_pt`. `map_pt_subscription_type`
  (`braunschweig/popsim/attributes.py`) resolves every nonresponse code pool-proportionally within
  age-group × RegioStaR7 via `braunschweig.popsim.missing`; the `spec.default` branch fires only on
  an EMPTY pool. Measured on the real donor (n = 420,979 MiD persons): valid **84.33 %**,
  structural under-14 (code 402) **9.60 %**, imputed nonresponse (99 + 202 + 206) **6.07 %** — of
  which 99 alone 0.24 %, 206 5.84 %, 202 0.00 % — and default (empty pool) **0.00 %**. In the
  executed 03101 smoke: valid 80.74 %, structural 13.08 %, imputed 6.18 %, default 0.0000 %. The
  mapper logs these four counts and rates and WARNs if the default ever fires (CLAUDE.md
  fallback-transparency rule). The structural inflation sources are therefore (a) the balancer
  freedom this ADR closes and (b) the hard-coded in-commuter assignment below — not the imputation.
- **Assumptions, stated because they carry real weight:**
  - **ASSUMPTION (construct match): MiD `never_pt` and SrV `E_OEV_FK == -8` are close but not
    identical constructs.** MiD `never_pt` is an ANSWER to the ticket question ("I never ride PT");
    SrV −8 ("nicht erhoben") is a usage-derived SKIP — the ticket question was not asked because the
    person reported no PT use in the past 12 months. They are treated as the same group here.
    Region-level corroboration is the only evidence available for that step: SrV weighted
    **28.64 %** never_pt on the 14+ universe (the unweighted respondent rate is 33.1 %) against
    MiD's **36 %** on the `Gesamt` row. The two are the same order and the same rank, not the same
    number, and SrV enters only as one half of a blend.
  - **ADR-0060's objection is NARROWED, not overruled** (as ADR-0089 §5 did for the flatrate
    construct). ADR-0060 rejected an SrV PT control because `E_OEV_FK` is usage-conditional; that
    objection **continues to stand** against controlling the full ticket-type SHAPE from SrV. The
    never/occasional boundary is a different claim: −8 is precisely the "no usage in 12 months"
    signal, which is the very quantity this group is about, so the usage-conditionality is the
    evidence here rather than the defect.
  - **`E_OEV_FK == 60` (Freifahrtberechtigung) counts as `occasional_ticket`, not `never_pt`,**
    because free-fare-entitled persons (children, severely disabled persons) do ride PT. This is
    the one place where the four-group extraction had to choose; ADR-0089 already records the
    simulation-side mismatch that they are fare-free in reality but carry no flatrate flag.
  - **Per-Kreis SrV rows remain ASSUMPTION-grade coverage estimates** and the blend still weights
    the two surveys by RAW sample size. Both caveats are ADR-0089's, unchanged and not re-litigated
    here: the survey is a stratified PSU design over ~44 sampled municipalities, so `level=kreis`
    rows extrapolate from those municipalities to the full Kreis, and `n_unweighted` overstates
    SrV's effective precision where a Kreis mixes strata.
  - **In-commuters are a control-external `never_pt` source.** Regular in-commuters
    (`_INCOMMUTER_PERSON_DEFAULTS`, `braunschweig/synthesis/incommuters.py`) and student
    in-commuters (`_STUDENT_PERSON_DEFAULTS`, `braunschweig/synthesis/student_incommuters.py`) are
    hard-coded `pt_subscription_type = "never_pt"` with `has_pt_subscription = False`. They live
    outside the ZGB cordon and therefore outside the 14+ RESIDENT universe this control partitions,
    so no control sees them and the realised regional never_pt share of a full run is the controlled
    resident share PLUS this injected mass. Each assignment site now emits one count log line so the
    mass is observable; the count is **unknown for the 03101 smoke**, which stops at
    `data.census.filtered`, upstream of in-commuter injection. Quantifying it is owed with the next
    full run, not claimed here.
- **Rejected alternatives:**
  - *Control all nine P24.1 categories.* ADR-0089's rejection **stands**: 72 control columns
    competing for the same household weights, steering structure that no Java reader consumes. The
    fourth group is the minimum resolution that fixes the measured inversion, not a step toward the
    nine.
  - *Fold the `no_answer` mass into `never_pt`.* It is the cheapest way to make the target sum to 1
    without renormalizing, and it would silently attribute an unknown-answer mass to a substantive
    behaviour the synthesis can never reproduce — a fabricated bias of unknown sign.
  - *Use the n-weighted blend as an ARBITER for the disagreeing Kreise.* Rejected for the same
    reason ADR-0089 gave: SrV's raw n overstates its effective precision under a stratified PSU
    design, so letting the larger sample win the disagreement would dress a design artefact up as
    evidence. The disagreement cases keep the shrink-toward-`Gesamt` path, where the raw-n weighting
    plays no role.
  - *Fix it in the balancer instead of with a control.* The within-group-freedom defect class
    (#320, #240) has one established remedy in this project — control the marginal at the resolution
    you care about — and the seed column is already derived from the resolved category, so no new
    draw is introduced.
- **Consequences:**
  - **Ablation overlays must now switch BOTH flags.** Any overlay or run that sets
    `braunschweig.population.popsim.pt_ticket_kreis_control: "off"` must ALSO set
    `braunschweig.population.popsim.pt_ticket_never_group: "off"`, or it hits the new fail-fast
    `ValueError`. This is deliberate (no silent half-state), but it is a breaking change for any
    ablation config written before this ADR.
  - **QUALIFICATION — switching target tables also moves the flatrate LEVEL in four Kreise.** The
    mechanism claim above stands unchanged: no Java code reads the `ptSubscriptionType` string, the
    only Java channel is the `hasPtSubscription` Boolean consumed by
    `BraunschweigPtCostModel.calculateCost_MU`, and the fourth group is a within-group split that
    leaves that Boolean's DEFINITION untouched. What the earlier framing missed is that the flag
    does not only re-partition `not_flatrate` — it swaps the whole target TABLE, and the two tables
    do not carry the same `deutschlandticket + other_flatrate` sum everywhere. Measured directly on
    the two committed CSVs (`target2026_pt_ticket_group_by_kreis.csv` vs
    `target2026_pt_ticket_group4_by_kreis.csv`), flatrate = `deutschlandticket + other_flatrate`:

    | ars5 | three-group | four-group | delta | `source` three → four |
    | --- | --- | --- | --- | --- |
    | Gesamt | 0.1900 | 0.1900 | 0.00 pp | mid → mid |
    | 03101 Braunschweig | 0.2576 | 0.2576 | 0.00 pp | blend → blend |
    | 03103 Wolfsburg | 0.1782 | 0.1782 | 0.00 pp | mid → mid |
    | 03102 Salzgitter | 0.1956 | 0.1956 | 0.00 pp | mid_shrunk → mid_shrunk |
    | 03151 Gifhorn | 0.1690 | 0.1690 | 0.00 pp | mid_shrunk → mid_shrunk |
    | 03157 Peine | 0.1596 | 0.1570 | **−0.26 pp** | **blend → mid_shrunk** |
    | 03154 Helmstedt | 0.1900 | 0.1913 | **+0.13 pp** | mid_shrunk → mid_shrunk |
    | 03158 Wolfenbüttel | 0.1592 | 0.1550 | **−0.42 pp** | **blend → mid_shrunk** |
    | 03153 Goslar | 0.1887 | 0.1900 | **+0.13 pp** | mid_shrunk → mid_shrunk |

    Four of nine rows move, in both directions, the largest being −0.42 pp (Wolfenbüttel). Two
    distinct mechanisms produce them, and they are independent:
    1. **Blend-rule flip (03157, 03158, the two largest deltas, both negative).** The
       never/occasional split exposes a >5 pp per-Kreis MiD-vs-SrV disagreement that the
       three-group collapse hid inside `not_flatrate`, so `blend_kreis_target`'s tolerance test
       fails and the rows fall from the precision `blend` to the `mid_shrunk` rule. The flatrate
       level then comes from shrunk MiD alone instead of a MiD×SrV mean, and their `n_effective`
       drops accordingly (03157 3178 → 1107, 03158 2437 → 815).
    2. **`no_answer` renormalization (03154, 03153, both +0.13 pp).** Item 5 divides the MiD row by
       the mass of the 8 producible categories, which changes the denominator in exactly those rows
       where P24.1's `keine_angabe` is non-zero. In `mid2023_P24_1.csv` that is precisely
       **03154 and 03153, each `keine_angabe` = 1.0 %** (every other row is 0.0). Both stay
       `mid_shrunk` in both tables, so the rule did not change — only the denominator did:
       19/99 = 0.19192 shrunk 0.7/0.3 toward the 0.19 `Gesamt` prior gives 0.1913 against 0.1900
       unrenormalized.

    So the honest statement is: this is a **target-table change that moves the simulation-relevant
    flatrate level in four of eight Kreise by up to 0.42 pp**, not a pure within-group
    redistribution at a fixed flatrate total. The magnitudes are small and well inside the
    ASSUMPTION-grade precision of the per-Kreis SrV inputs, and the flag remains a
    reference-fidelity fix rather than a results lever — but it is not exactly flatrate-neutral,
    and any A/B that reports a flatrate difference between the arms must attribute part of it to
    the target table rather than to the balancer.
  - **The population XML changes.** `ptSubscriptionType` now carries English values. This is
    **simulation-neutral** on the evidence above (zero Java readers of the string; the only channel
    is the `hasPtSubscription` Boolean, which is unchanged in meaning and in construction). Any
    external post-processing that matched the old German strings must be updated.
  - **Evidence, and its limits.** The 03101 OFF/ON A/B smoke (2026-08-21, one flag differing;
    `docs/runs/smoke-pt-never-group-03101-off-2026-08-21.yml`,
    `docs/runs/smoke-pt-never-group-03101-on-2026-08-21.yml`) moved realised never_pt from
    **30.82 %** (OFF, +18.50 pp against the 12.32 % target — reproducing the 100 %-run defect at
    30.17 %, which is what makes the A/B faithful) to **12.17 %** (ON, −0.15 pp). The realised
    flatrate aggregate — the only simulation-relevant quantity — moved only 25.57 % → 25.46 %
    (0.11 pp) **in 03101**, and that result does not generalise: 03101 is one of the five rows where
    the two target tables carry an IDENTICAL flatrate sum (0.2576 both, see the qualification under
    Consequences), so this arm is structurally incapable of showing the target-table effect. The
    general claim "the flag preserves the flatrate level" is therefore **unproven by this smoke** and
    is contradicted at the target level in 03157/03158/03154/03153.
    The integerizer stayed 96.8 % OPTIMAL in both arms (OFF 3892/4019, ON 4082/4217), so the fourth
    control column does not over-constrain. **This is a smoke, not a validation**: one Kreis, and a
    control FIT against a target the synthesis is steered toward. The full-region A/B is
    deliberately deferred to the next 100 % wave (user decision), together with the in-commuter
    count and a re-measurement via `scripts/measure_license_pt_shares.py`.
  - **ADR-0089 keeps its claim, with one boundary made explicit.** Its statement that the SPLIT
    among the non-flatrate types has no simulation effect is restated here as TRUE — no Java reader
    consumes the ticket-type string — and is the reason this fix is filed as reference fidelity.
    The boundary: that claim covers the split, not the switch of target tables that delivers it, and
    the latter does move the flatrate level in four Kreise (see the qualification above).
    ADR-0089 carries an amendment pointer to this record.
- **Evidence:** design `docs/superpowers/specs/2026-08-21-pt-never-group-control-design.md`; issue
  #329 (predecessors #321/ADR-0089, ADR-0060, ADR-0087, ADR-0079, the within-group-freedom issues
  #320/#240); the two run manifests named above; tests
  `tests/test_no_german_pt_ticket_literals.py`, `tests/test_blended_target_tables.py`,
  `tests/test_kreis_attribute_control.py`, `tests/test_kreis_control_stage_wiring.py`,
  `tests/test_popsim_seed_kreis_columns.py`, `tests/test_popsim_attributes_missing.py`; committed
  data `srv2023_ticket_groups4_14plus_by_kreis.csv` and
  `target2026_pt_ticket_group4_by_kreis.csv` (registry records of the same names).
- **Numbering note:** this record was drafted as ADR-0098 and renumbered to 0099 before its commit,
  because ADR-0098 is already claimed by the unmerged sibling branch
  `fix/eqasim-java-version-pin-2.3.1` (the java-pom jar-version record); ids are append-only, so
  the collision is resolved by taking the next free number rather than by renumbering that branch.
