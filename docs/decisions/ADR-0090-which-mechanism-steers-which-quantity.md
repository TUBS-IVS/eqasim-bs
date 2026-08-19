# ADR-0090 · 2026-08-18 · Donor-joint quantities are steered through the weights, not by rewriting attributes

- **Status:** active
- **Context:** The `popsim_mid` path has grown three distinct mechanisms that can move a
  synthetic quantity, and nothing wrote down which is responsible for what. The #307 follow-up
  work made the gap concrete: two attributes (driving licence, PT subscription) turned out to be
  steered by NO mechanism at all, so their realised distribution was a by-product of weights
  fitted to other quantities — and when designing a control for one of them there was no rule
  saying where it belongs.
  The inventory (2026-08-18):
  1. **Backbone grid controls** (`control_spec` tier0-tier2, `ZENSUS100m` / `ZENSUS1km`):
     household total, age × sex, household size, household type, tenure, building type,
     employment grid. Acts on the WEIGHTS — how often each real donor household is copied into
     each cell.
  2. **Kreis attribute controls** (`kreis_attribute_control.REGISTRY`, ten entries): rendered as
     additional PopulationSim controls at `KREIS` geography, so they act on the SAME weights in
     the SAME balancing. `hard` / `soft` is an importance weighting inside that one optimisation,
     not a separate mechanism.
  3. **Post-hoc attribute rewriting**: `income_spatial_tilt`, `income_kreis_control`,
     `placement_income`, the fleet powertrain / EV / vehicle-age tilts. These change attribute
     VALUES on finished persons and households after the balancing, so they can move a quantity
     mechanism 1 or 2 already fitted, with nothing turning red.
- **Decision:** the responsibility line is the ORIGIN of the quantity, not its convenience.
  - **A quantity that is OBSERVED jointly in the donor household is steered through the weights
    (mechanism 1 or 2).** Every synthetic household under `popsim_mid` is a copy of a real MiD
    household; its licence, its ticket, its cars, its employment and its travel diary belong to
    the same real people. Re-weighting which households get copied preserves that joint
    distribution. Rewriting one attribute breaks it: the person then carries a ticket that is not
    the ticket of the respondent whose trips the model gives them.
  - **Mechanism 3 stays reserved for quantities that are MODELLED rather than observed** — the
    income amount in euro (donor income CLASS × INKAR factor, so the euro value was never
    observed), vehicle age, powertrain. There the joint distribution being disturbed does not
    exist in the data to begin with.
  - **A new steering requirement therefore starts as a Kreis control**, and reaching for a
    post-hoc rake requires an explicit ADR arguing why the quantity is modelled rather than
    observed.
- **Consequences and limits, stated because they bound the rule:**
  - **Mechanisms 1 and 2 compete.** They are one optimisation over one set of weights, so every
    added control trades against the others' fit. A control is not free, and its cost is
    measurable only by an A/B — see ADR-0089, which spends 24 columns instead of 72 for exactly
    this reason.
  - **A Kreis control cannot steer a pattern without also steering the level.**
    `kreis_attribute_control.attribute_kreis_count_table` computes
    `counts = shares × per-Kreis total`, so the category sum is precisely what gets pinned. Any
    requirement of the form "reproduce the ratio between categories but leave their total free"
    (issue #322 for the licence sex gradient) is NOT expressible in this framework and needs its
    own decision rather than a workaround.
  - **Mechanism 3 can silently undo 1 and 2** for the attributes it touches. That is tolerated
    only because those attributes are not controlled — the moment a modelled quantity also gets
    a control, the interaction has to be resolved explicitly, not left to execution order.
- **Rejected alternatives:**
  - *Prefer post-hoc rakes generally, because they hit their target exactly.* Precision on one
    margin bought by destroying the observed joint structure, which is the core asset of a
    donor-based synthesis and the reason ADR-0079 refused to port the legacy marginal draws.
  - *Forbid mechanism 3 entirely.* It is the right tool where no joint observation exists
    (income in euro), and banning it would push those quantities into controls whose targets do
    not exist.
  - *Decide per case without a written rule.* That is the status quo that produced two unsteered
    attributes and a design discussion starting from scratch each time.
- **Origin:** #307 follow-up brainstorm, 2026-08-18 (item V3). First applications: ADR-0089
  (PT subscription → Kreis control) and the parked analysis on #322 (licence gradient → not
  expressible, see the limit above).
