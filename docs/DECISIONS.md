# Architecture Decision Record (ADR) log — eqasim-bs

> The retrospective *why* of this project: when each major feature or decision happened,
> what problem forced it, what was chosen, and the evidence (commit / PR / spec / feature doc)
> that grounds it. This complements [PROJECT_STATUS.md](../PROJECT_STATUS.md) (the live
> at-a-glance feature/branch dashboard — the **authoritative current status**),
> [PROJECT_BACKLOG.md](../PROJECT_BACKLOG.md) (ranked open work), and
> [UPSTREAM_DELTA.md](UPSTREAM_DELTA.md) (the pinned bavaria baseline).

## What an ADR is here

Each entry records one substantive decision: the **context** (the problem), the **decision**
(what was chosen), the **rationale** (why — grounded in a committed source), the
**consequences** (what it enables or costs), and **evidence** (at least one committed reference:
a commit hash, a merged PR number, a spec/plan path, or a feature doc). Per the project rule in
`CLAUDE.md` ("no invented reference values"), every rationale and number traces to a committed
source actually read; where the *why* is not recoverable from the record, the entry says so
rather than guessing.

## Status vocabulary

- **active** — the decision is in force on `main`.
- **superseded by ADR-NNNN** — replaced by a later decision (the entry stays for traceability).
- **rejected** — tried or designed and deliberately not adopted (the "why we did NOT do it" is
  recorded so it is not re-attempted; see PROJECT_BACKLOG.md §1 Tier 5).

Flag note: nearly all model features are flag-gated with a **byte-identical OFF path** (the
codebase default is OFF/`None`/legacy); "ON in run configs" means enabled in the committed
real-data configs. Live per-feature status (✅/🟢/⚪/🟡) lives in PROJECT_STATUS.md §2.

---

### ADR-0000 · 2025-10-06 · eqasim-bavaria baseline (fork point)
- **Status:** active
- **Context:** A scientific MATSim/eqasim transport model was needed for the Zweckverband
  Großraum Braunschweig (ZGB-8, Niedersachsen). Rather than build from scratch, the project
  forks the closest existing regional eqasim configuration.
- **Decision:** Fork `eqasim-org/eqasim-bavaria` at merge-base commit `b20fbe6` ("Merge pull
  request #14 from eqasim-org/chore/rename", 2025-10-06) into `TUBS-IVS/eqasim-bs`. Inherit the
  entire eqasim machinery (Python synpp synthesis from the French ENTD trip donor + census,
  the Java MATSim modules for mode choice/scoring/simulation, and the Bavaria/Munich scenario
  configs) and add a new `braunschweig/` region module on top.
- **Rationale:** Upstream already provides the proven eqasim pipeline; eqasim-bs adds a new
  region plus data-driven realism as a *delta* on a known baseline, keeping the history
  traceable (UPSTREAM_DELTA.md).
- **Consequences:** ~776 commits and a 303-file `braunschweig/` module (~70k insertions) sit on
  top of the baseline; the French ENTD-2008 trip donor is inherited (and later flagged as the
  highest-value replacement lever — see ADR-0038). PRs always target the fork base, never the
  eqasim-org upstream.
- **Evidence:** `docs/UPSTREAM_DELTA.md` (pinned merge-base `b20fbe6`); `CHANGELOG.md`
  v0.1.0-bs (2026-04-27) first tagged regional release on top of `b20fbe6`.

### ADR-0001 · 2026-04-27 · Clean regional fork structure (`braunschweig/` + `eqasim_common/`)
- **Status:** active
- **Context:** The upstream code was fenced inside a `bavaria/` package; a Braunschweig model
  needed its own clearly separated module while keeping shared helpers reusable.
- **Decision:** Lock the region to ZGB-8 (ARS prefixes 03101/03102/03103/03151/03153/03154/
  03157/03158), introduce a region-neutral `eqasim_common/` package (shared OSM, gravity-distance,
  spatial-code, location helpers) and a new `braunschweig/` package (IPF, location, gravity,
  enrichment, MATSim simulation); migrate stage names `bavaria.* → braunschweig.*` with aliases
  only where the DAG still consumes upstream leaf modules.
- **Rationale:** Clean separation of region-specific from shared code, per the MATSim/eqasim
  modularity convention in `CLAUDE.md`.
- **Consequences:** First tagged release `v0.1.0-bs`; new configs (1%/10%/25%/dryrun), seed
  `1234`, gravity slope `-0.065`; test suite rewritten around BS configs.
- **Evidence:** `CHANGELOG.md` v0.1.0-bs (2026-04-27); produced by branch
  `refactor/braunschweig-clean-fork`.

---

## Population synthesis

### ADR-0002 · 2026-06-15 · Three population-synthesis workflows (`population.method`)
- **Status:** active
- **Context:** The inherited path is a single IPF-from-census synthesis. The project needed to
  fold PopulationSim-based synthesis (from a separate `popsimprep` repo) into the synpp pipeline
  while keeping the legacy IPF path intact and reproducible.
- **Decision:** Add a `population.method` switch with three paths: `simple_ipf_open` (legacy
  IPF, the default), `popsim_open` (PopulationSim on Zensus controls), and `popsim_mid`
  (PopulationSim + MiD 2023 donor). The all-features production configs use `popsim_mid`.
- **Rationale:** Mirror the proven eqasim ENTD pipeline structure exactly (reuse its helpers/
  schema/vocab) rather than approximate it; keep alternative paths flag-selected for
  reproducibility (memory `project-popsim-three-workflows`).
- **Consequences:** `braunschweig/popsim/` becomes the production synthesis path; downstream
  gravity/location/mode-choice all run on the popsim output, so popsim is "the foundation"
  (re-tuning popsim forces re-tuning gravity — PROJECT_BACKLOG.md §1).
- **Evidence:** PR #1 "Feature/population method workflows" (merged 2026-06-15); commit
  `cd9d217`; PROJECT_STATUS.md §2.1.

### ADR-0003 · 2026-06 · Per-commune household-size margin in the IPF
- **Status:** active
- **Context:** The base IPF balanced persons to census but did not pin household sizes per
  commune, so the synthesised size distribution drifted from Zensus.
- **Decision:** Add a flag-gated per-commune household-size margin
  (`ipf.use_household_size_margin`) from Zensus 2022 1000A-2081.
- **Rationale:** Anchor the synthetic size distribution to a committed Zensus table; the joint
  age×size margin (ADR-0004) and age-aware composition (ADR-0005) build on it
  (`docs/features/household-synthesis.md`).
- **Consequences:** Prerequisite for the joint age×size margin; OFF path byte-identical.
- **Evidence:** `docs/features/household-synthesis.md`; PROJECT_STATUS.md §2.1 (Zensus 1000A-2081).

### ADR-0004 · 2026-06 · Joint age×household-size margin (#3)
- **Status:** active
- **Context:** A flat size margin balances size independently of age, so the IPF would invent
  the joint distribution (not knowing large households skew toward school-age children while
  1-person households skew elderly).
- **Decision:** Add the observed age×size correlation to the IPF at Kreis resolution over coarse
  age groups `(15,30,40,50,60)`, 2D-raked to stay consistent with both the population age and the
  size margin (so it cannot make the IPF infeasible). Flag `ipf.use_joint_age_size_margin`,
  source Zensus 2022 1000A-3082.
- **Rationale:** All age-group edges are native ALTKL2 band edges, so aggregating the Zensus
  joint never splits a band (no assumption); the refined `[30,40)/[40,50)` split pins family-size
  households the old `[30,60)` group could not (`docs/features/household-synthesis.md`).
- **Consequences:** Once the composition routing fix (ADR-0005) is in place, the refined bounds
  reduce the parent-child gap>50 share from 2.70% to 0.77%; structural zero (children in 1-person
  HH) held at exactly zero so the IPF does not diverge.
- **Evidence:** `docs/features/household-synthesis.md`; `tests/test_joint_age_size.py`;
  PROJECT_STATUS.md §2.1 (Zensus 1000A-3082).

### ADR-0005 · 2026-06-04 · Age-aware household composition (#3b) with children-driven capacity
- **Status:** active
- **Context:** The legacy random within-bucket chunk + independent hh_type draw produced
  implausible "single parents": ~23% of placed children had a youngest household adult 55+ years
  older (mean gap 84 years), because surplus children spilled onto elderly childless-shell adults.
- **Decision:** Replace it with one coupled optimisation pass per `(commune, hh_size)` bucket
  (`form_households_age_aware`, flag `ipf.age_aware_chunking`): hard adult/child composition per
  hh_type, age-gap-minimising couple pairing, children placed by a sorted rank match around a
  target gap drawn `N(31.8, 5.5)`, and `_ensure_child_capacity` grows child-bearing capacity so
  no surplus child lands on an elderly adult.
- **Rationale:** The mother-age-at-birth target 31.8 is Destatis 2024 (committed reference); the
  sorted rank match is the same 1-D optimum as a Hungarian LAP but `O(n log n)` instead of
  `O(n^3)`, essential because formation runs on the full ~1.13M-person population
  (`docs/features/household-synthesis.md`).
- **Consequences:** gap>55 tail drops to ~0.3% (~0.03% with refined bounds), mean gap 39→26;
  `child_parent_age_target_weight=0.85` then lifts the realised mean back to 31.8. No person ever
  dropped; all-children households hard-blocked.
- **Evidence:** spec `docs/superpowers/specs/2026-06-04-age-aware-household-chunking-design.md`;
  plan `2026-06-04-age-aware-household-chunking.md`; `docs/features/household-synthesis.md`;
  `tests/test_household_composition.py`.

### ADR-0006 · 2026-06 · Sex-aware couple pairing (~1.1% same-sex)
- **Status:** active
- **Context:** Sex-blind age-adjacent couple pairing yields ~48% same-sex couples (every pair is
  sex-random), which is grossly unrealistic.
- **Decision:** Pair couples opposite-sex by default with a small calibrated same-sex share
  (`DEFAULT_SAME_SEX_COUPLE_SHARE=0.011`) via an opposite-first allocation `max(intended, forced)`
  that never drops anyone. Flag `chunking.sex_aware_couples` (OFF = legacy sex-blind, byte-identical).
- **Rationale:** 1.1% is Statistisches Bundesamt Mikrozensus 2025 (204,000 same-sex couples,
  ~50/50 male/female, vs ~18.9M couples) — a committed reference
  (`docs/features/household-synthesis.md`).
- **Consequences:** Realised share converges toward 1.1% as sampling rate rises (~2.9% at 25%,
  the residual being genuine local sex imbalance in small Gemeinden).
- **Evidence:** `docs/features/household-synthesis.md`; PROJECT_STATUS.md §2.1 (Destatis MZ 2025).

### ADR-0007 · 2026-06 · Cell-accurate (100m) home placement
- **Status:** active
- **Context:** PopulationSim expands households per 100m Zensus cell, so homes should be placed
  within the household's own 100m cell, not just its commune.
- **Decision:** Place each household in a real building inside its `ZENSUS100m` cell
  (`synthesis/locations/home_cell.py`), using intersection-based footprint→cell membership to
  reduce boundary orphans, with a commune-level area-weighted fallback for empty cells.
- **Rationale:** Uses the Zensus 100m grid (committed); intersection join reduces orphans vs a
  centroid test (commit `73c8acf`).
- **Consequences:** Active on the popsim path; the legacy area-weighted draw (with the 400m² cap)
  is retained only for the non-popsim path; later refined by ALKIS-typed matching (ADR-0008).
- **Evidence:** commits `73c8acf`, `88078d3`, `bf1be42`; PROJECT_STATUS.md §2.1 (Zensus 100m grid).

### ADR-0008 · 2026-06-17 · ALKIS-typed, capacity-aware home matching
- **Status:** active
- **Context:** The area-weighted home draw ignored both the household's `building_type_3class`
  (EFH/MFH/sonstiges) and the ALKIS building function/capacity, so EFH households landed on MFH
  footprints and vice versa; a 400m² area cap dropped exactly the apartment blocks MFH households
  should live in.
- **Decision:** Match households to buildings using the household building type and ALKIS-typed
  footprint capacity within each cell, producing the best realistic household↔building combination
  (data-driven; ZGB-8 scope only, national generalisation explicitly out of scope).
- **Rationale:** Rich per-cell Zensus 2022 building/dwelling/size data is now in the prepared
  parquet that was unavailable when the original placement was written (spec §1).
- **Consequences:** Removes the type-fidelity defect and the 400m² cap workaround.
- **Evidence:** spec `docs/superpowers/specs/2026-06-17-alkis-typed-home-matching-design.md`;
  PR #14 "Feature/alkis typed home matching" (merged 2026-06-18); PROJECT_STATUS.md §2.1.

### ADR-0009 · 2026-06-17 · LoD2 height/volume building typing
- **Status:** active
- **Context:** Building type and dwelling capacity were inferred from footprint area alone, which
  cannot distinguish a tall apartment block from a large flat building.
- **Decision:** Join LoD2 3D building heights by ALKIS `OI` (non-destructive, coverage logged) and
  type/size buildings by `building_volume(area, height)` end-to-end (volume-rank MFH typing,
  `MFH_MIN_FLOORS=4`, volume-weighted slots).
- **Rationale:** `MFH_MIN_FLOORS=4` was tuned on a Salzgitter real-population sweep; the consumer
  side is fully wired and verified 2026-06-27 (PROJECT_BACKLOG.md §2.2).
- **Consequences:** Better dwelling-capacity realism feeding ADR-0008.
- **Evidence:** spec `docs/superpowers/specs/2026-06-17-lod2-height-volume-capacity-design.md`;
  plan `2026-06-17-lod2-height-volume-capacity.md`; PROJECT_STATUS.md §2.1
  (verified 2026-06-27); `test_preprocess_alkis_oi.py`.

### ADR-0010 · 2026-06-15 · Income spatial tilt (Nettokaltmiete) — and the zero-rent gate fix
- **Status:** active
- **Context:** Household income within a Kreis was spatially flat; rent data offers a sub-Kreis
  signal. An initial implementation appeared to *flip* the income–rent correlation negative.
- **Decision:** Apply a within-Kreis income tilt by Nettokaltmiete (flag `popsim.income_spatial_tilt`,
  INKAR/Zensus rent), mean-preserving. The "flip" was diagnosed as a gate bug — the correlation
  filter did not exclude `rent==0` cells, so the owner-index in zero-rent cells dragged it negative;
  fixed to exclude zero-rent cells (commit `36ee20b`).
- **Rationale:** On non-zero-rent cells the tilt gives ΔPearson +0.032 with the mean preserved;
  a within-Kreis *extra* signal beyond size/tenure/age was deliberately dropped (ADR-0036)
  (memory `project-income-spatial-tilt`).
- **Consequences:** Active on the popsim path; INKAR regional scale 0.88–1.09 (03101=1.0014).
- **Evidence:** merge `c604653`; fix commit `36ee20b`; PROJECT_STATUS.md §2.1 (INKAR/Zensus rent).

---

## Attribute enrichment

### ADR-0011 · 2026-06 · Economic status via Bayes on household-type × region
- **Status:** active
- **Context:** Mapping economic status 1:1 from the income €-class is a weak predictor.
- **Decision:** Determine `economic_status` (5 BMDV classes) from the stronger Haushaltstyp×Region
  predictor by Bayes `P(status|hhtype,region) ∝ P(hhtype|status,region)·P(status|region)`, with the
  Niedersachsen Bundesland table as base and the national RegioStaR-7 raumtyp table as a within-NDS
  tilt; then re-derive `household_income` from the sampled status. Flag `status_from_hhtype`
  (code default true; OFF reproduces commit c65399d byte-identically).
- **Rationale:** Haushaltstyp×Region is the much stronger signal; the raumtyp table is national so it
  is applied only as a within-NDS tilt, not as a base (CLAUDE.md "Economic status from MiD").
- **Consequences:** Income and status agree by construction; primary/fallback classification rate is
  logged (no silent fallback).
- **Evidence:** CLAUDE.md "Economic status from MiD household-type × region";
  `tests/test_status_from_hhtype.py`; PROJECT_STATUS.md §2.2 (MiD status×hhtype×region).

### ADR-0012 · 2026-06 · PT subscription as a categorical 3-margin IPF (MiD P24.1)
- **Status:** active
- **Context:** A single boolean `has_pt_subscription` loses the ticket-type structure and is not
  conditioned on demographics.
- **Decision:** Assign each person a categorical `pt_subscription_type` from a 3-margin IPF (raking)
  on `X[kreis, sex, age_bin, ticket_type]` against MiD 2023 P24.1 Kreis/sex/age margins;
  derive `has_pt_subscription = type ∈ PT_TICKET_FLATRATE`. Flag `pt_subscription_conditioned`.
- **Rationale:** The flatrate set matches the legacy single-target Kreis share within ±1pp (tested);
  MiD's three margins are independently rounded to integer percent, so raking finds a least-squares
  compromise within ~5pp on the worst cell (CLAUDE.md "PT ticket type").
- **Consequences:** Reference CSVs are seeded only via `scripts/seed_mid_constraint_tables.py`
  (hard-coding percentages in Python is prohibited).
- **Evidence:** CLAUDE.md "PT ticket type (P24.1)"; `tests/test_mid_reference_tables.py`;
  PROJECT_STATUS.md §2.2 (MiD P24.1).

### ADR-0013 · 2026-06 · Driving licence as a categorical 3-margin IPF (MiD P17.1)
- **Status:** active
- **Context:** Licence was taken from KBA FE4.x via the IPF model; MiD P17.1 offers a directly
  conditioned categorical.
- **Decision:** Assign `license_type ∈ {ja,nein,keine_angabe}` to persons ≥18 from a 3-margin IPF
  on `Xl[kreis, sex, age_bin, license_category]` against MiD 2023 P17.1; `has_license = (type=="ja")`.
  The BF17/begleitetes-Fahren option is intentionally ignored.
- **Rationale:** MiD margins are integer-percent-rounded spanning 19–94%, so raking finds a
  least-squares compromise within ~10pp on the worst cell (CLAUDE.md "Driving licence (P17.1)").
- **Consequences:** The legacy KBA-FE4 `license` column is still produced but is no longer the source
  of truth; `keine_angabe` conservatively maps to False.
- **Evidence:** CLAUDE.md "Driving licence (P17.1)"; tests `test_license_ipf_three_margins_converges...`;
  PROJECT_STATUS.md §2.2 (MiD P17.1).

### ADR-0014 · 2026-06 · Employment margin raked to GENESIS SvB (not survey P9)
- **Status:** active
- **Context:** Employment could be controlled from the MiD P9 survey or the GENESIS register.
- **Decision:** Add an employment margin to the IPF (`ipf.use_employment_margin`) raked to GENESIS
  SvB (register data), and do NOT rake to MiD P9.
- **Rationale:** P9 is survey noise (~900/Kreis, 43–59% spread, ~4pp definitional difference);
  raking to it would overfit noise (PROJECT_BACKLOG.md §1 Tier 5). See the rejected ADR-0035.
- **Consequences:** Employment is anchored to register totals; P9 is used as a validation
  cross-check only.
- **Evidence:** PROJECT_STATUS.md §2.2 (GENESIS SvB); PROJECT_BACKLOG.md Tier 5
  ("Raking employment to MiD P9"); memory `synthesis-method-and-optimization`.

### ADR-0015 · 2026-06-16 · Tier-3 Kreis-level PopulationSim controls
- **Status:** active
- **Context:** The popsim controls were 100m/1km only; some marginals (e.g. education attributes)
  are better controlled at Kreis level via a Codeplan-B1 crosswalk.
- **Decision:** Add Tier-3 Kreis controls (`popsim.control_tiers: …tier3`, `popsim/control_spec.py`)
  sourced from Zensus + GENESIS, plumbed through a KREIS geography with the Codeplan-B1 crosswalk
  fix, landed dormant-first then live-wired across several PRs.
- **Rationale:** Built incrementally (foundation/dormant → live wiring → fixes) to keep each PR
  reviewable, per the working discipline in `CLAUDE.md`.
- **Consequences:** Adds 7 KREIS-level controls; measured fit at KREIS mean |%dev| 2.40%
  (PROJECT_BACKLOG.md step-1b).
- **Evidence:** PRs #3/#4/#5/#6/#7/#8 (merged 2026-06-16..06-17); PROJECT_STATUS.md §2.2.

### ADR-0016 · 2026-06-18 · Per-cell employment grid control (age×sex-resolved 100m)
- **Status:** active
- **Context:** Employment was controlled at Kreis level; a 100m age×sex-resolved employment target
  sharpens the spatial employment distribution.
- **Decision:** Add an opt-in age×sex-resolved 100m employment-grid control.
- **Rationale:** not recoverable from the committed record in detail beyond the PR title; the PR
  describes it as "age×sex-resolved 100m employment (opt-in)".
- **Consequences:** Opt-in; feeds the popsim controls used for the all-features run.
- **Evidence:** PR #9 "Employment grid control — age×sex-resolved 100m employment (opt-in)"
  (merged 2026-06-18).

### ADR-0017 · 2026-06 · Income €, income-aware car count, consistent car availability, tenure
- **Status:** active
- **Context:** Several enrichment attributes needed to be made internally consistent and
  data-grounded: household income in €, number of cars, car availability, and housing tenure.
- **Decision:** Add flag-gated stages: `income_eur_from_distribution` (MiD H4/brackets + INKAR
  class-midpoint scaling), `cars_income_aware` (MiD H7), `consistent_car_availability`
  (MiD P19/P17.1/H7), and `synthesise_housing_tenure` (MiD income×Wohnen, for completeness).
- **Rationale:** Each is grounded in a committed MiD/INKAR reference table (CLAUDE.md MiD reference
  table inventory); all flag-gated so OFF is byte-identical.
- **Consequences:** Internally consistent socio-economic attribute set for the synthetic population.
- **Evidence:** CLAUDE.md "Reference data: MiD 2023 constraint tables"; PROJECT_STATUS.md §2.2.

### ADR-0018 · 2026-06-07 · Reactivated person attributes (couple/studies/single-parent-child)
- **Status:** active
- **Context:** Some eqasim person attributes were dormant in the BS path and needed real-data
  grounding to be reactivated.
- **Decision:** Reactivate the attributes (flag `reactivate_person_attributes`) grounded on
  Destatis education data (e.g. student share).
- **Rationale:** spec "Tier-A attribute reactivation" (2026-06-07); grounded on Destatis education.
- **Consequences:** Restores attributes used downstream; flag-gated.
- **Evidence:** spec `docs/superpowers/specs/2026-06-07-tier-a-attribute-reactivation-design.md`;
  plan `2026-06-07-tier-a-attribute-reactivation.md`; PROJECT_STATUS.md §2.2 (Destatis education).

---

## Vehicle fleet

### ADR-0019 · 2026-06-07 · Household vehicle fleet (vs eqasim default car)
- **Status:** active
- **Context:** The inherited path gives every car owner a generic default car; a realistic German
  fleet (segments, brands, powertrains, engine attributes) is needed for emissions/realism.
- **Decision:** Build a per-household fleet (`vehicles_method: household`) grounded in MiD H7 and
  KBA registration data, with a German segment+brand mix (`fleet_model_enabled`/`_brands`, KBA FZ),
  BEV/electric calibration (`fleet_electric_calibration`, KBA FZ 27.15/27.17), and HSN/TSN engine
  attributes (kW/ccm/fuel, `fleet_hsn_tsn_attributes`).
- **Rationale:** Each layer is grounded in a committed KBA/MiD reference (spec
  `2026-06-07-fleet-kba-mid-design.md`); all flag-gated.
- **Consequences:** Enables fleet-level analysis (brand/powertrain maps); emissions wiring
  (HBEFA consumption) is parked (PROJECT_BACKLOG.md Tier 3.5).
- **Evidence:** spec `docs/superpowers/specs/2026-06-07-fleet-kba-mid-design.md`; plan
  `2026-06-07-fleet-kba-mid.md`; PROJECT_STATUS.md §2.3.

### ADR-0020 · 2026-06-18 · Fleet internal consistency v2 + income-coupled vehicle age
- **Status:** active
- **Context:** The first fleet draw produced physically inconsistent vehicles (e.g.
  "diesel Lamborghini") and an income-blind vehicle age.
- **Decision:** Add a brand-level HSN/TSN feasibility fallback (consistency v2) and an
  income-coupled vehicle-age tilt (`fleet_age_income_coupling`, AgeIncomeModel with a fallback ladder).
- **Rationale:** The consistency v2 kills impossible brand×powertrain×fuel combinations and forces
  Tesla→BEV; the income-age coupling asserts the MiD income-age gradient *spread* (not an absolute
  KBA-anchored level), so the OFF golden stays byte-identical (commits `42132d4`, `4ed63d3`).
- **Consequences:** Realistic, internally consistent per-household fleet; fleet evaluation panel
  added to population_validation.
- **Evidence:** spec `docs/superpowers/specs/2026-06-18-fleet-vehicle-consistency-and-income-age-design.md`;
  PR #12 (consistency, merged 2026-06-18) and PR #13 (income-age, merged 2026-06-18);
  PROJECT_STATUS.md §2.3.

### ADR-0021 · 2026-06 · HSN/TSN scraper for engine attributes
- **Status:** active
- **Context:** Real engine attributes (kW, ccm, fuel) per vehicle model require an HSN/TSN lookup
  not shipped with KBA aggregates.
- **Decision:** Scrape hsn-tsn.de (`scripts/scrape_hsn_tsn.py`, 1 request/brand) into a kW/ccm/fuel
  lookup and map scraped brands onto the fleet (62-brand coverage).
- **Rationale:** Provides the per-model engine attributes the HBEFA wiring will need; mapping
  table covers all fleet brands (memory `hsn-tsn-scraper`).
- **Consequences:** Engine attributes present on vehicles; not yet consumed for emissions (Tier 3.5).
- **Evidence:** commits `1092221`, `4e231f9`; memory `hsn-tsn-scraper`; PROJECT_STATUS.md §2.3
  (KBA HSN/TSN scraper).

### ADR-0022 · 2026-06-08 · Carless routing re-mode (routing/fleet consistency)
- **Status:** active
- **Context:** MATSim routing could assign car legs to agents whose household has no car (a
  household-fleet × routing gap).
- **Decision:** Re-mode car legs for carless agents (`remode_carless_car_legs`,
  `matsim/simulation/prepare.py`), and give every non-owner a routing `default_car` so eqasim-core
  car coverage holds.
- **Rationale:** Routing consistency — the fleet and the routed mode must agree (memory
  `allfeatures-run-fleet-routing-fix`).
- **Consequences:** Closes the fleet×routing gap; OFF path unaffected.
- **Evidence:** memory `allfeatures-run-fleet-routing-fix`; commit `b736953`; PROJECT_STATUS.md §2.3.

---

## Location choice / gravity

### ADR-0023 · 2026-06-01 · Per-RegioStaR-7 gravity distance slope
- **Status:** active
- **Context:** A single distance-decay slope `exp(slope·d)` decays urban and rural commutes at the
  same rate, which is unrealistic (urban origins have flatter slopes / longer commutes).
- **Decision:** Differentiate the slope by the origin Gemeinde's RegioStaR-7 class
  (`gravity_slope_by_regiostar7`), holding the flow-weighted mean equal to `gravity_slope=-0.065`
  so the regional mean commute is unchanged; only the sub-Kreis distribution is differentiated.
  Fill RS7-absent Gemeinden by geographic nearest neighbour.
- **Rationale:** A per-origin fit with destination FE is rank-deficient on the BA Pendleratlas
  data (distance collinear with per-destination dummies), so a single identified full-panel Poisson
  GLM pools within-origin distance variation; anchors chosen by an adaptive ring (CLAUDE.md
  "Gravity model").
- **Consequences:** Realistic sub-Kreis commute distribution; pinned values in run configs (re-run
  the script, do not hand-edit).
- **Evidence:** plan `docs/superpowers/plans/2026-06-01-per-regiostar7-gravity-slope.md`; spec
  `2026-06-01-per-regiostar7-gravity-slope-completion-design.md`; `tests/test_gravity_ring_calibration.py`;
  PROJECT_STATUS.md §2.4 (BA Pendleratlas Poisson GLM).

### ADR-0024 · 2026-06-03 · Education gravity (real schools / Kita / university)
- **Status:** active
- **Context:** The generic OSM hard-radius education sampler ignores real facility capacity and
  distance decay, and uses coarse age bands.
- **Decision:** Assign all education levels by real-data distance-decay gravity: school-age pupils to
  real Niedersachsen schools (doubly-constrained capacity Furness), kindergarten to real Kita Plätze
  (same model), and university students to real Hochschulen (singly-constrained decay). Flag
  `education_gravity_enabled` (OFF = legacy OSM, byte-identical).
- **Rationale:** Doubly-constrained prevents a tiny nearby school swallowing pupils; the
  singly-constrained university choice lets the distance tail reach far universities whose huge
  enrollment is mostly non-resident; the 16–19 BBS/Oberstufe split (`education_bbs_share=0.681`) is
  NDS enrollment (CLAUDE.md "Education gravity model").
- **Consequences:** Real facilities (local-only data, not committed); per-(RS7,level) slopes
  calibrated to MiD T43 / Destatis MZ 2024; legacy bands change only on the ON path.
- **Evidence:** spec `docs/superpowers/specs/2026-06-03-education-gravity-design.md`; plans
  `2026-06-03-{education-gravity,kita-education,university-education,bbs-oberstufe-split,education-slope-calibration}.md`;
  `docs/features/education-gravity.md`; PROJECT_STATUS.md §2.4.

### ADR-0025 · 2026-06-25 · Building-level activity potentials (work/secondary/education) — REPLACE
- **Status:** active
- **Context:** Without building-level potentials, every activity is placed at a zone centroid or a
  uniform random building, so large offices/shops/schools do not attract proportionally more trips.
- **Decision:** Redistribute work, secondary, and education locations to individual OSM/ALKIS
  buildings weighted by a floor-area-based activity potential (from the TUBS-IVS Activities-and-
  Potentials pipeline). For work and secondary the building set REPLACES the candidate set (real
  computed `potential_work`/`pot_*` from the parquet); education ATTACHES within the assigned
  facility. Flags `work_/secondary_/education_building_potentials` (OFF byte-identical).
- **Rationale:** A mid-session pivot chose REPLACE over the earlier ATTACH strategy (ADR-0037);
  aggregate controls (GENESIS SvB, OD flows, NDS enrollment) remain authoritative — potentials only
  govern within-zone/within-school placement (`docs/features/building-potentials.md`).
- **Consequences:** `area*floors` becomes only the OFF/legacy path; real `potential_work` Census-SvB
  cross-check printed. Reshaped within-zone placement (which made the old "0.47" commute figure stale).
- **Evidence:** spec `docs/superpowers/specs/2026-06-25-building-activity-potentials-design.md`;
  PR #16 (merged 2026-06-25) + PR #17 (Copilot follow-up); `docs/features/building-potentials.md`;
  PROJECT_STATUS.md §2.4.

### ADR-0026 · 2026-06-25 · Purpose-resolved secondary distances (Tier 1 + Tier 2 daily/non-daily)
- **Status:** active
- **Context:** `_sample_leg_distance` drew the desired distance per mode only, so a shop-by-car and
  a leisure-by-car leg drew the same distribution, diluting shop distances by the longer leisure tail.
- **Decision:** Tier 1 — build per-(purpose×mode×band) distributions (`secondary_distance_by_purpose`).
  Tier 2 — split shopping into daily/non-daily (`secondary_shop_daily_split`) via a seeded subtype
  imputation from MiD W_ZWD, with daily/non-daily distances and `retail_daily`/`retail_non_daily`
  building placement. Both flags ON in the all-features popsim configs.
- **Rationale:** MiD W_GEW means show ~3× shop and ~5× leisure subtype distance ranges; OFF baseline
  EMD (shop 0.053/leisure 0.064/other 0.018) is below the 0.08 threshold, so this is a realism
  *refinement*, not a broken-model fix; sparse-cell fallback rate is logged (no silent fallback)
  (`docs/features/secondary-distances.md`).
- **Consequences:** The eqasim output purpose stays shop/leisure/other; resolution is internal.
  A later leisure W12 fix (ADR-0033) corrected a double-counting interaction.
- **Evidence:** `docs/features/secondary-distances.md`; commits `c68c8df`, `706b87a`, `8e98e3d`;
  PROJECT_STATUS.md §2.4 (MiD W12 per-purpose).

### ADR-0027 · 2026-06-26 · External secondary candidates (long-distance trips)
- **Status:** active
- **Context:** Some leisure/other secondary trips exceed the ~50km study area (~6% leisure / ~3%
  other), so carla truncates them to the area edge instead of matching the long MiD desired distance.
- **Decision:** Append German Gemeinde centroids OUTSIDE ZGB (population-weighted) to the secondary
  candidate set (`secondary_external_candidates`, on only where `cordon_enabled`); eqasim's
  `RunScenarioCutter` converts the boundary-crossing trip into a fixed "outside" activity.
- **Rationale:** Reuses the existing out-commuter mechanism (`external_workplaces`); direction is a
  distance-only proxy (ASSUMPTION, no secondary OD data); a warning is logged if on without cordon
  (CLAUDE.md "External secondary candidates").
- **Consequences:** Matches the long desired-distance tail; OFF path byte-identical.
- **Evidence:** PR #19 (merged 2026-06-26); commits `0cc2ad2`, `c4fcdda`, `d1aa17c`;
  CLAUDE.md "External secondary candidates".

---

## Cordon / cross-border (Einpendler)

### ADR-0028 · 2026-06-02 · Cordon external-demand model — targeted crossing agents with full supply
- **Status:** active
- **Context:** The synthetic population is resident-only ZGB, so demand ENTERING the region
  (in-commuters, visitors, through-traffic) is not represented, undercounting network/PT load.
- **Decision:** Build a cordon/external-demand extension as **targeted cordon-crossing agents**
  (Approach B), with **full eqasim agents with external homes** and a MATSim **supply extension** to
  the cordon ring; decomposed and built in order: (1) supply extension → (2) in-commuters →
  (3) external visitors → (4) through-traffic, where 3 & 4 are out of scope.
- **Rationale:** Approach A (synthesise all of Hannover, discard non-crossers) wastes ~90% and needs
  structural data we have only for ZGB; Approach B reuses the `external_workplaces` pattern and the
  all-Germany BA Pendler matrix already on disk (spec D-1..D-5).
- **Consequences:** Supply must cover the ring (prerequisite); sub-projects 3 & 4 never started
  (PROJECT_BACKLOG.md Tier 3.4 — through-freight is covered separately by ADR-0030).
- **Evidence:** spec/roadmap `docs/superpowers/specs/2026-06-02-cordon-external-demand-roadmap.md`
  (Decisions D-1..D-5); PROJECT_STATUS.md §2.5.

### ADR-0029 · 2026-06-02..06-05 · Einpendler injection with road + PT/Bahnhof gates and mode balancer
- **Status:** active
- **Context:** In-commuter agents need a network entry point and a mode that matches observed
  cross-border travel.
- **Decision:** Inject in-commuters (`cordon_enabled`, `synthesis/incommuters.py`,
  `incommuter_merge/`) entering via road and PT/Bahnhof gates (OSM, GTFS), with a mode balancer
  grounded on Mikrozensus modes; cordon network built by enlarge-then-cut.
- **Rationale:** Gates give a realistic entry geometry; the mode reference and balancer ground the
  cross-cordon mode split on Mikrozensus (committed reference) (spec set 06-02..06-05).
- **Consequences:** `einpendler_extern` cross-cordon demand validated; uncalibrated gate
  gravity-beta/capacity-exponent parked (PROJECT_BACKLOG.md Tier 3.3).
- **Evidence:** specs `2026-06-02-incommuter-agents-v1*-design.md`,
  `2026-06-03-incommuter-mode-reference-design.md`, `2026-06-05-cross-cordon-external-demand-design.md`;
  plan `2026-06-05-cordon-einpendler-injection.md`; PROJECT_STATUS.md §2.5.

---

## Freight

### ADR-0030 · 2026-06-11 · Long-haul freight injection (german-wide-freight v3, hybrid Java→Python→Java)
- **Status:** active
- **Context:** Heavy-goods through-traffic on the ZGB motorways (A2/A7/A39) is not represented;
  correctly classifying TRANSIT vs INTERNAL/INCOMING/OUTGOING requires routing each freight trip on
  the German-wide network (a straight-line OD test would miss exactly the through-traffic).
- **Decision:** Inject long-haul road freight from the VSP german-wide-freight v3 model via a
  three-stage hybrid: (1) the published matsim `RunExtractFreightTrips` Java tool, run once per
  category (cached, 100%, sampling-rate independent); (2) a Python trips stage parsing the plans;
  (3) a Java `RunInjectFreight` hook after the cordon cut, Bernoulli-sampled to the run's sampling
  rate. Flag `freight_enabled` (code default true; OFF byte-identical). Freight agents are isolated
  from mode choice and excluded from all person-travel analysis.
- **Rationale:** The published, peer-reviewed Java tool routes+classifies+trims correctly; the build
  writes no category attribute, so the unmodified tool is run once per `--tripType` (verified on the
  real output: all 49,758 trips came back `unknown`). Freight sampling is required because the qsim
  flowCapacityFactor is scaled to the sampling rate (CLAUDE.md "Long-haul freight injection").
- **Consequences:** `freight_truck_pce=3.5` and `_max_velocity_kmh=80` are explicit ASSUMPTIONS
  (StVO / uncalibrated); a BASt HGV-count calibration is a parked follow-up (ADR-0034 / Tier 3.2).
- **Evidence:** plan `docs/superpowers/plans/2026-06-11-german-wide-freight-injection.md`;
  `docs/features/freight.md`; memory `project-freight-injection`; PROJECT_STATUS.md §2.6.

---

## Analysis / dashboards

### ADR-0031 · 2026-06-07 · MiD + population validation reporting
- **Status:** active
- **Context:** Runs need reproducible validation against the committed reference data.
- **Decision:** Add an MiD-validation report (`analysis/run_mid_validation.py` vs MiD
  P9/P12_1/P13/P17_1), a combined full analysis (`run_full_analysis.py`), a PopulationSim-style
  population validation (`analysis/population_validation/` vs Zensus: controls/quality/geo), and an
  education enrollment validation (vs LSN capacity).
- **Rationale:** Validation against committed references is mandatory (CLAUDE.md); population
  validation mirrors PopulationSim control validation (spec 2026-06-07).
- **Consequences:** `report.json`/`summary.md`/figures per run; default-on inside full analysis.
- **Evidence:** spec `docs/superpowers/specs/2026-06-07-population-validation-design.md`;
  `docs/features/run-analysis.md`; `tests/test_run_mid_validation.py`; PROJECT_STATUS.md §2.7.

### ADR-0032 · 2026-06-23 · Integerizer-quality analysis (per-cell error map)
- **Status:** active
- **Context:** PopulationSim hits the household total exactly but may squeeze out large/rare
  household types; this needs to be visible per cell.
- **Decision:** Add an integerizer-quality report (per-control split, per-cell %-dev, GPKG 100m-cell
  map, CLI) under `analysis/integerizer_quality/`.
- **Rationale:** Makes the 100m composition under-fit measurable (it later showed ZENSUS100m mean
  |%dev| 6.04%, max 27.87% — the evidence behind the rejected importance calibration, ADR-0039)
  (PROJECT_BACKLOG.md step-1b).
- **Consequences:** Provided the measurement that proved importance tuning would not help (controls
  already hit) and is donor-bound.
- **Evidence:** spec `docs/superpowers/specs/2026-06-23-integerizer-quality-analysis-design.md`;
  commits `7b09658`, `f9c9417`; PROJECT_STATUS.md §2.7.

### ADR-0033 · 2026-06-08 · SimWrapper dashboard export (Python emitter + Java contrib)
- **Status:** active
- **Context:** Run analytics should be viewable inside the MATSim/SimWrapper ecosystem, not only as
  the project's HTML dashboard.
- **Decision:** Two layers: (1) the MATSim simwrapper contrib behind `--simwrapper` (default off,
  byte-identical when off); (2) a Python emitter (`analysis/simwrapper/`) converting the existing
  dashboard `record` into SimWrapper CSV+YAML (8 chart/table tabs + 4 map tabs + a commuter tab),
  default-on inside full analysis and as a synpp stage writing only a new `simwrapper/` subfolder.
- **Rationale:** No scientific logic is duplicated (it reuses the existing dashboard `record` and
  spatial helpers); tabs whose source data is absent are skipped with an explicit log (no silent
  skip) (CLAUDE.md "SimWrapper dashboards").
- **Consequences:** Existing run outputs stay byte-identical; works in synthesis-only and full modes.
- **Evidence:** plan `docs/superpowers/plans/2026-06-08-simwrapper-dashboard-export.md`;
  memory `project-simwrapper-dashboard`; PROJECT_STATUS.md §2.7.

### ADR-0034 · 2026-06-27 · Secondary leisure W12 fix (leisure_correction_factor)
- **Status:** active
- **Context:** A full 100% synthesis-only validation run revealed the realised secondary leisure
  distribution was off (W12 leisure EMD 0.131).
- **Decision:** Apply the legacy `leisure_correction_factor=2.0` only on the legacy per-mode path,
  not when the Tier-1 purpose-resolved distances are active (it was double-counting with the
  purpose-resolved distances, a mode-only-era heuristic).
- **Rationale:** With the fix, W12 leisure EMD 0.131→0.050 at 100% (all purposes pass; shop/other
  unchanged) — measured, not assumed (SESSION_LOG 2026-06-27).
- **Consequences:** Corrects an interaction introduced by ADR-0026.
- **Evidence:** PR #20 (merged 2026-06-27); commit `ba734c9`; SESSION_LOG.md 2026-06-27.

---

## Calibration corner

### ADR-0035 · 2026-06-25 · Calibration corner (offline tooling, never imported by the runtime)
- **Status:** active
- **Context:** Several offline calibrators (gravity per-RS7 slope, gravity decay, education slopes)
  and new distribution-calibration loops needed a single, clearly separated home.
- **Decision:** Create `braunschweig/calibration/` as the single home for offline calibration:
  shared metrics (`band_shares`/`emd_on_bands`/`apply_detour`), MiD distribution targets (P13/T43/W12
  loaders), per-model loops + CLIs + reports. It consumes runtime components and emits pinned YAML;
  it is never imported by the runtime pipeline. The three legacy calibrators are migrated in as
  `_legacy_*` with thin `scripts/calibrate_*.py` shims preserving behaviour.
- **Rationale:** Runtime model components stay with the model (per-band friction in `gravity/friction.py`,
  the chainsolvers scorer in its own stage); the corner holds only the offline loops, keeping
  simulation setup separate from analysis (`docs/features/calibration-corner.md`).
- **Consequences:** A clean place to build (and measure) calibrations before pinning; per-band
  commute friction wired into the model but defaulting to `None` (legacy `exp(slope·d)`).
- **Evidence:** PR #18 (merged 2026-06-26); `docs/features/calibration-corner.md`;
  `tests/test_calibration_migration_shims.py`; PROJECT_STATUS.md §2.4.

---

## Infrastructure

### ADR-0036 · 2026-06-22 · Shared persistent stage-cache (prime-on-launch)
- **Status:** active
- **Context:** Expensive sampling-rate-independent synpp stages (above all the ~3h freight Java
  routing) are recomputed on every fresh run because synpp caches per `working_directory`.
- **Decision:** Add `braunschweig.cache_share` + a `scripts/run_synpp.py` launcher that PRIMES a
  shared store before synpp runs and EXPORTS after a successful run, by copying synpp's cache
  artifacts (never recomputing synpp's hash — synpp re-validates the hash on load). Flags
  `cache_share_enabled` (true) / `cache_share_export` (true); `enabled: false` is a pure no-op.
- **Rationale:** We copy artifacts and let synpp decide validity, so a primed entry whose hash does
  not match is ignored and recomputed (never corruption, only a forgone speedup, logged as a miss)
  (`docs/features/cache-share.md`).
- **Consequences:** Freight routing runs once and is reused across runs/machines; auto-export uses
  `skip_existing=True` so the store is never overwritten.
- **Evidence:** spec `docs/superpowers/specs/2026-06-22-shared-stage-cache-design.md`;
  spec `2026-06-23-auto-export-shared-cache-design.md`; `docs/features/cache-share.md`;
  `tests/test_cache_share.py`; PROJECT_STATUS.md §2.8.

### ADR-0037 · 2026-06-22 · Tier-A/B shareable-stage set + fixed popsim work_dir
- **Status:** active (config wiring partial)
- **Context:** Beyond the freight chain, many synpp stages (and the popsim donor build) are
  sampling-/path-independent and could be shared across runs, but sharing `popsim.stage` needs a
  single fixed work_dir so its hash is identical.
- **Decision:** Share the 32 empirically verified sampling-/path-independent stages plus
  `popsim.stage` and `popsim.completed_donor`, using a single fixed
  `braunschweig.population.popsim.work_dir` across all configs, protected by a stale-batch guard.
- **Rationale:** The MiD donor build depends only on MiD data/seed/day-filter/weekend flag (not on
  controls/sampling/work_dir), so it is computed once and reused across all runs (`docs/features/cache-share.md`).
- **Consequences:** Makes a 100% production run affordable; config wiring is partial
  (PROJECT_BACKLOG.md Tier 1.3).
- **Evidence:** spec `docs/superpowers/specs/2026-06-22-tier-a-b-caching-design.md`;
  plan `2026-06-22-tier-a-b-caching.md`; PROJECT_STATUS.md §2.8.

### ADR-0038 · 2026-06 · Own editable `eqasim-java-bs` fork
- **Status:** active
- **Context:** The MATSim/Java side needed project-specific changes (parking, freight injection,
  mode availability, SimWrapper contrib) not in the upstream bavaria Java.
- **Decision:** Build our own editable Java project (the `braunschweig` Java module) wired via
  `eqasim_source_path` (`../eqasim-java-bs`), pinned to MATSim `2025.0-PR3568`, instead of the
  upstream bavaria clone.
- **Rationale:** The pipeline builds our editable Java, so Java-side features land in our fork
  (memory `eqasim-java-bs-own-fork`; UPSTREAM_DELTA.md).
- **Consequences:** Java features (ADR-0039 parking, ADR-0030 freight injection) live in the fork.
- **Evidence:** `docs/UPSTREAM_DELTA.md`; memory `eqasim-java-bs-own-fork`; PROJECT_STATUS.md §2.8.

### ADR-0039 · 2026-06 · Urban parking (Braunschweig inner ring)
- **Status:** active
- **Context:** Realistic parking pressure is concentrated in the Braunschweig inner ring.
- **Decision:** Add urban parking (`enable_urban_parking`, `matsim/simulation/prepare.py` + Java),
  enabled in the 25%/100% server configs for realism, scoped to the BS inner ring only.
- **Rationale:** Enabled "for more realistic" behaviour in the server configs (commit `b005a0d`);
  inner-ring-only scope per memory `project-building-activity-potentials`.
- **Consequences:** Flag-gated; ON in the server real-data configs.
- **Evidence:** commits `b005a0d`, `bccb21f`; PROJECT_STATUS.md §2.8.

### ADR-0040 · 2026-06-28 · Professionalized PM / tracking layer
- **Status:** active
- **Context:** The project history and open work were spread across memory files and ad-hoc notes;
  a durable, committed PM layer was needed for traceability and onboarding.
- **Decision:** Add `PROJECT_STATUS.md` (feature matrix), `PROJECT_BACKLOG.md` (ranked open work),
  `docs/DECISIONS.md` (this ADR log), `docs/UPSTREAM_DELTA.md`, `docs/ONBOARDING.md`,
  `CONTRIBUTING.md`, `.github/` templates, `RUNS.md`, and split deep feature detail into
  `docs/features/*` (verbatim, no-loss), leaving CLAUDE.md as rules-only.
- **Rationale:** Single sources of truth, kept current via `/close`, per the working discipline in
  CLAUDE.md (spec 2026-06-28).
- **Consequences:** One canonical backlog/status; CLAUDE.md and git win on disagreement.
- **Evidence:** spec `docs/superpowers/specs/2026-06-28-pm-layer-professionalization-design.md`;
  PR #21 (merged 2026-06-28); commits `6b2bdd4`, `d2401a9`, `67a3cd5`.

---

## Rejected / not-adopted decisions

> Recorded so they are not re-attempted. The "why we did NOT do it" is half the value; each cites
> the measurement that killed it (PROJECT_BACKLOG.md §1 Tier 5).

### ADR-0041 · 2026-06-25 · REJECTED — Pin commute gravity friction factors
- **Status:** rejected
- **Context:** A per-band commute friction (`gravity_friction_factors`) was built to make the
  realised home→work distance distribution match MiD P13, motivated by a historical "EMD 0.47 FAIL".
- **Decision:** Do NOT pin any friction factors; leave them at the `None` default (legacy
  `exp(slope·d)`); keep the machinery as gated-off infrastructure.
- **Rationale:** Measured on `cache_bs_25pct_allfeat`, the model already matches P13 (donor targets
  EMD 0.0037, gravity OD EMD 0.037, realised straight-line ~0.065, all below the 0.08 threshold). The
  "0.47 FAIL" was a STALE figure on MATSim-routed distances from a run *before* the building-activity
  potentials (ADR-0025) reshaped placement (`docs/features/calibration-corner.md`).
- **Consequences:** Pipeline stays byte-identical to legacy friction; lesson "measure before
  calibrating" reinforced.
- **Evidence:** `docs/features/calibration-corner.md` (Finding 2026-06-25); commit `1a10e15`;
  PROJECT_BACKLOG.md Tier 5; memory `feedback-measure-before-calibrating`.

### ADR-0042 · 2026-06-25 · REJECTED — Distance-dependent detour curve f(d) as default
- **Status:** rejected
- **Context:** Circuity decays with distance (Giacomin & Levinson 2015), so a fitted curve
  `c(d)=c_inf+a·exp(-d/tau)` could in principle improve the euclidean→routed axis vs the constant 1.3.
- **Decision:** Keep the constant detour factor 1.3 as the DEFAULT; the fitted curve is opt-in
  infrastructure (`mode="curve"`) only.
- **Rationale:** Fitted on the 25% synthesis and measured: commute EMD vs P13 0.0878→0.0849
  (Δ~0.003), pooled secondary walk vs W12 0.0712→0.0729 (slightly worse) — both far below the 0.01
  materiality threshold (`docs/features/detour-circuity.md` VERDICT 2026-06-25).
- **Consequences:** No education re-pin; pipeline byte-identical to the pre-Tier-3 constant 1.3; the
  pt-uplift placeholder must be verified before any future curve activation.
- **Evidence:** `docs/features/detour-circuity.md` (VERDICT); commit `4de2d51`,
  `5aa7fe5` (`band_shift_impact.csv`); SESSION_LOG.md 2026-06-25; PROJECT_BACKLOG.md Tier 5.

### ADR-0043 · 2026-06-27 · REJECTED — Tune secondary scorer `pot_weight`
- **Status:** rejected
- **Context:** The combined chainsolvers scorer's `pot_weight` (pull toward large buildings) might
  add a residual distance distortion worth tuning.
- **Decision:** Keep `secondary_scorer_pot_weight` at the default 1.0; do not tune it.
- **Rationale:** A sweep at 100% showed `pot_weight` is a *concentration* knob — raising it makes the
  building-capacity fit WORSE (over-concentration), while distance never breaks even up to 128;
  default 1.0 is optimal (memory `feedback-capacity-fit-sampling-power`; SESSION_LOG 2026-06-27).
- **Consequences:** Scorer weights stay at config values; the real within-zone lever is a building
  worker-count dataset, not the scorer.
- **Evidence:** SESSION_LOG.md 2026-06-27; commit `8196ec3` (scorer-sweep bench);
  memory `feedback-capacity-fit-sampling-power`; PROJECT_BACKLOG.md Tier 5.

### ADR-0044 · 2026-06 · REJECTED — Rake employment to MiD P9
- **Status:** rejected
- **Context:** Employment could be controlled against the MiD P9 survey instead of the GENESIS
  register (see ADR-0014).
- **Decision:** Do NOT rake employment to P9; keep it raked to GENESIS 13111 (register).
- **Rationale:** P9 is survey noise (~900/Kreis, 43–59% spread, ~4pp definitional difference); raking
  to it would overfit noise. P9 is a validation cross-check, not a control (PROJECT_BACKLOG.md Tier 5).
- **Consequences:** Employment anchored to register totals.
- **Evidence:** PROJECT_BACKLOG.md Tier 5 ("Raking employment to MiD P9");
  memory `synthesis-method-and-optimization`.

### ADR-0045 · 2026-06-15 · REJECTED — Within-Kreis extra income signal beyond the rent tilt
- **Status:** rejected
- **Context:** Beyond the Nettokaltmiete rent tilt (ADR-0010), an additional sub-Kreis income signal
  was considered.
- **Decision:** Do NOT add a within-Kreis *extra* income signal; keep only the rent tilt (+0.032 Pearson).
- **Rationale:** No external sub-Kreis income ground truth exists (RWI-GEO-GRID is FDZ-restricted),
  and the size/tenure/age controls already dominate the within-Kreis income variation
  (PROJECT_BACKLOG.md Tier 5; memory `project-income-spatial-tilt`).
- **Consequences:** A Kreis-level income control (via INKAR targets) remains a deferred future option
  (PROJECT_BACKLOG.md Tier 3.1), distinct from this rejected within-Kreis extra signal.
- **Evidence:** PROJECT_BACKLOG.md Tier 5 + Tier 3.1; memory `project-income-spatial-tilt`.

### ADR-0046 · 2026-06-24 · REJECTED — PopulationSim importance/expansion calibration framework
- **Status:** rejected (design only; recommend formal close)
- **Context:** Every popsim control carries a uniform importance 1000; the PopulationSim docs
  recommend iterative importance/expansion tuning. A coordinate-descent calibration framework was
  designed (with donor KPIs held out and a baseline-vs-tuned verdict).
- **Decision:** Do NOT build/activate the importance calibration; keep it parked as design only.
- **Rationale:** Measured on the 100% run, the controls are already hit (HH total exact, 11/43,598
  cells off, +0.022%), so importance tuning "would not help"; bumping importance instead makes the
  simultaneous integerizer THRASH (no completion at 3×/10×) or hit INFEASIBLE even at the doc's own
  1e9 recommendation; the residual 100m composition under-fit is donor-bound (rare/large HH types are
  thin in the MiD seed), so the real lever is the German MiD donor, not importance
  (PROJECT_BACKLOG.md step-1b/proof iteration).
- **Consequences:** The 19KB design+plan stay on disk unbuilt; the recommended lever is ADR-0038
  (German MiD donor, deferred).
- **Evidence:** spec `docs/superpowers/specs/2026-06-24-popsim-importance-calibration-design.md`;
  plan `2026-06-24-popsim-importance-calibration.md`; PROJECT_BACKLOG.md §1 (step-1b, nachsteuern
  proof) + Tier 5; commits `841fe05`, `2619fd1`, `d31c7eb`; memory `project-popsim-importance-calibration`.

### ADR-0047 · 2026-06-25 · REJECTED — ATTACH strategy for building potentials
- **Status:** superseded by ADR-0025
- **Context:** Building-level activity potentials for work/secondary were first designed to ATTACH a
  potential weight to the existing zone-level candidate set.
- **Decision:** Replace ATTACH with REPLACE (use the gpkg buildings as the candidate set directly) for
  work and secondary, after a mid-session pivot.
- **Rationale:** not recoverable from the committed record beyond the pivot itself; recorded in the
  backlog as "Replaced by REPLACE (gpkg buildings as candidate set) after mid-session pivot"
  (PROJECT_BACKLOG.md Tier 5).
- **Consequences:** Work/secondary source candidates from real `potential_work`/`pot_*` buildings;
  education keeps ATTACH within the assigned facility (ADR-0025).
- **Evidence:** PROJECT_BACKLOG.md Tier 5 ("ATTACH strategy for building potentials");
  memory `project-building-activity-potentials`; PR #16.

### ADR-0048 · 2026-06-28 · Function-aware secondary `other` potential + scorer scale-alignment
- **Status:** active on PR #77 (open, Closes #27) — Part A active; Part B scorer calibration server-deferred.
- **Context:** The secondary `other` potential was the raw `potential_generic =
  volume_m3 × bosserhof_class_weight`, which is function-blind. The VW-Werk Wolfsburg (8.9M m³ →
  26.7M potential, a real building) and steel/wholesale giants dominated the chainsolvers `other`
  candidate score, concentrating errand activities on industrial mega-structures. The realised
  distance distribution was unaffected (carla's ring candidate generation bounds distance) — the
  defect was within-pool placement, not distance.
- **Decision:** (A) Derive `potential_other = min(generic, cap) × (broad_share + errand_share·1(class
  ∈ whitelist))`, zeroed below `min_volume_m3`, from a committed Bosserhof-class→eqasim-purpose
  mapping CSV; attach it to the legacy `other` candidates instead of raw `generic`. (B) Bump the
  chainsolvers pin to `d8d8ae7d` for the native `Scorer(attr_transform="log1p")` + `mnl` selection
  (use the library lever, no downstream pre-scaling) and add a measure-first calibration CLI; defer
  pinning `attr_transform`/weights and any `dp_sample`/`mnl` A/B to a server run.
- **Rationale:** `other` = MiD 2023 W_ZWECK 5 Erledigung (45.7%) + 6 Bringen/Holen (23.1%) + 10 anderer
  (31.2%) collapsed into one eqasim `other`, so it cannot be restricted to service buildings;
  broad_share=0.54/errand_share=0.46 are those W_GEW-weighted shares (`MiD2023_Wege.csv`). Whitelist =
  11 errand classes; research institutes + car dealerships excluded (user decision). A uniform cap
  (whitelist-generic percentile, applied to all) tames the volume tail. OFF byte-identical; no invented
  values; pinning gated on a measured W12 win (shop 0.053/leisure 0.064/other 0.018) — convergence ≠
  validation.
- **Consequences:** Errand placement no longer over-attracted to factories; Part A enabled in the 5
  real configs; chainsolvers bump backward-compatible (attr_transform defaults to "linear").
- **Evidence:** PR #77; issue #27; spec/plan `docs/superpowers/{specs,plans}/2026-06-28-smart-other-potential*`
  (gitignored); memory `project-smart-other-potential`; commits `8fdb2f3..4af644a` on `feature/smart-other-potential`.

### ADR-0049 — Wolfsburg commute "misfit" is an unreliable reference, not a model bug; sub-zonal (TAZ) is the real lever

- **Date:** 2026-06-30
- **Decision:** Do NOT calibrate the gravity to fix the Wolfsburg per-Kreis commute EMD (0.209
  vs MiD P13). A systematic-debugging pass ruled out every candidate cause and showed the model
  is defensible; the **per-Kreis MiD P13 target for Wolfsburg is n_weighted=39 / n_unweighted=126**
  and is **inconsistent with the authoritative BA Pendleratlas full count**. The genuine,
  scientifically-defensible improvement lever is **sub-zonal resolution** (eqasim IRIS-analog):
  run the work location choice at VISUM-Verkehrszellen (TAZ) resolution so the gravity forms
  distances *inside* the kreisfreie Staedte (BS/SZ/WOB = 1 Gemeinde each).
- **Why (ruled out, all checked against real data):** friction is moot (1 Gemeinde -> gravity
  cannot shape intra-city); real VW-concentrated worker data barely moves it (0.209->0.19; homes+
  jobs co-located ~4 km, centroids 1.2 km); the in/out split **= BA exactly** (78.2% intra, svb
  53,015 / out 11,550); Hannover/Berlin out-commuters ARE simulated (external workplaces ~7.4% ~= BA)
  and ARE in the EMD; excluding the far tail makes it WORSE (the gap is missing 10-30 km, not the
  tail); routing would need ~4x detour (RS7-72 circuity ~1.2). Arithmetic: BA caps out-commuting
  at 22% but MiD wants 53% at 10-30 km -> the surplus can be neither out-commuters nor intra-city
  in a 15-km town -> the n=39 MiD sample is unrepresentative. Calibrating to it would be overfitting
  to noise (forbidden: anti-overfitting / no-invented-references).
- **Consequence:** (1) evaluate per-Kreis fit only where `n_weighted >= ~80`, else use ROBUST
  references (ZGB-aggregate n=1583, per-RS7) -- drives the distance_fit module's n-awareness
  hardening; (2) TAZ sub-zonal work location choice approved (issues #79 / #80) -- flag-gated
  default OFF, TAZ data local-only (proprietary VISUM), reuse the zone-agnostic eqasim functions
  (distance_matrix via the zone stage, gravity / candidates / define_distance_ordering), BA stays
  the Kreis-level anchor.
- **Evidence:** this session's debugging; `eqasim-data/data/braunschweig/mid/mid2023_P13.csv`
  (WOB row n_weighted=39); BA Pendleratlas + census employment stages (svb/out); specs
  `docs/superpowers/specs/2026-06-29-distance-fit-diagnostics-design.md` and
  `2026-06-30-taz-subzonal-work-location-choice-design.md`; memory
  `feedback-robust-reference-not-perkreis-noise`, `project-taz-subzonal-work-location`.

### ADR-0050 — TAZ per-RS7 gravity friction: built, then measured unnecessary; commute distribution already fits; validate flag-ON at scale instead

- **Date:** 2026-07-01
- **Decision:** Do NOT pin the TAZ per-RS7 / per-band `gravity_friction_factors` calibration.
  The machinery was fully built and reviewed (branch `feature/taz-gravity-calibration`, 6 commits
  `c8655b1..3c2ebb5` — a `--taz` mode in `scripts/calibrate_gravity_distribution.py` re-fitting friction
  on the TAZ work-OD via `compute_work_od` + TAZ-aware `_calibrate`, work-pass-scoped so it cannot leak
  into the education Gemeinde pass), but the pre-calibration measurement showed friction is **not
  needed**. The branch is **PARKED (pushed to the fork as backup, not merged)** as gated-off infra,
  reusable only if a future measurement shows a real gap. The remaining Phase-3 work is to **validate the flag-ON TAZ
  feature at scale** (run the 100% population with `taz_work_location_choice: true`,
  `matsim_last_iteration: 0`), not to calibrate.
- **Why (measured, traceable references only):** (1) **Mechanism** — eqasim's two-stage location
  choice was verified adversarially to be a BIJECTION: `candidates.py` draws exactly one candidate zone
  per person from the (gravity-synthesised) OD, and `locations.py::define_distance_ordering` only
  RE-PAIRS candidates to persons to match each survey `commute_distance`. So the AGGREGATE distance
  distribution is set by the gravity candidate pool; friction is a legitimate lever on it, but the
  per-person matching does not change the aggregate. (2) **Fit** — on the CURRENT 100% `popsim_mid`
  population (flag-OFF, ZGB-resident-filtered commutes vs the committed `mid2023_P13.csv`), the
  aggregate EMD is **~0.054** (< the ~0.08 no-recalibration band). The earlier "0.47 FAIL" was a stale
  pre-building-potentials number. So the aggregate already fits — recalibrating would be fixing a
  working model. (3) **WOB** — per-Kreis Wolfsburg EMD ~0.21 is the n=39 noise outlier of ADR-0049, not
  a target. (4) **1% flag-ON A/B first-look** — flag-ON IMPROVES the aggregate (EMD 0.057 -> 0.033) by
  correcting commune-centroid over-concentration of <=5 km commutes toward P13; the compact-city
  intuition was backwards (centroids were too short, TAZ lengthens within-commune commutes to realistic
  building distances).
- **Consequence:** friction branch parked (infra only); Phase-3 becomes a **validation run** of the
  merged flag-ON TAZ (issue #83 re-scoped) + a spatial validation map (new issue); the flag-ON 100%
  run needs a multi-hour popsim rebuild because origin/main's popsim/secondary sources differ from the
  commit that built the existing 24G flag-OFF cache (the "cheap cache prime" premise is dead, per the
  Phase-3 measure-first note in `project-taz-subzonal-work-location`).
- **Evidence:** this session's measurement; branch `feature/taz-gravity-calibration` @ `3c2ebb5`
  (+ SDD ledger `.superpowers/sdd/progress.md`); `synthesis/population/spatial/primary/{candidates,locations}.py`;
  `eqasim-data/data/braunschweig/mid/mid2023_P13.csv`; memory `project-taz-subzonal-work-location`,
  `feedback-measure-before-calibrating`, `feedback-robust-reference-not-perkreis-noise`. Follows ADR-0049.

---

> **Live status note.** This log is the retrospective *why*. For the current state of every feature
> (merged / flag-on / infra-only / open PR), always defer to [PROJECT_STATUS.md](../PROJECT_STATUS.md)
> and `git log`; where this log and those disagree, `CLAUDE.md` and git win.
