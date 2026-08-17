# ADR-0078 · 2026-08-17 · Activate the IPF household-realism flags and take Gemeinde population shares from the open Zensus table (issue #251); delete dead config keys instead of documenting them
- **Status:** active
- **Context:** Issue #251 reported three household-synthesis features that the
  (since retired) `PROJECT_STATUS.md` matrix marked ON while no committed config
  set them: `braunschweig.ipf.use_joint_age_size_margin` (ADR-0004),
  `braunschweig.ipf.age_aware_chunking` (ADR-0005) and
  `braunschweig.chunking.sex_aware_couples` (ADR-0006). ADR-0077's registry had
  already corrected the false claim by recording them honestly as off, which left
  the substantive half open: three refinements were designed, implemented and
  unit-tested, yet no run had ever executed them. Investigating the surrounding
  configs surfaced two neighbouring instances of the same class of drift — a
  config asserting something no run can use. First, five config keys were set
  across ten committed configs while no live code could read them; two pointed at
  files that do not exist on disk (`12111-0001_population_ni.xlsx`,
  `pendler_ni.xlsx`), and `docs/codebase/CONCERNS.md` documented them as a known
  concern naming a single fixture. Second,
  `braunschweig.census.use_zensus_gemeinde_shares` — the open-data replacement for
  the scraped, redistribution-forbidden urbistat Gemeinde share key — was
  implemented with unit tests in 2026-06 and likewise never switched on, and its
  guard test existed uncommitted in a form that could only pass vacuously (it
  globbed root-level `config_local_*.yml`, which `.gitignore` excludes).
- **Decision:** Treat "a committed config states something no run uses" as a
  defect to remove, not a caveat to record, and prove activation before claiming
  it. Concretely:
  1. **Enable the three household-realism flags** in all four `simple_ipf_open`
     *run* configs (`config_local_braunschweig.yml`, `..._10pct.yml`,
     `..._25pct.yml`, `config_smoke_simple_ipf.yml`), pinned by
     `tests/test_ipf_config_parity.py` as the IPF-side counterpart of
     `test_popsim_config_parity.py`. That test also runs each shipped combination
     through `braunschweig.ipf.config_validation`, so a config cannot ship a flag
     without its prerequisite. Deliberately NOT set in the `popsim_mid` /
     `popsim_open` configs, where the production method replaces the legacy IPF
     and the keys would be dead; `config_dryrun_braunschweig.yml` is excluded
     because it enables none of the prerequisite margins.
  2. **Take the Gemeinde-within-Kreis population share from Zensus 2022
     1000A-3082** (`braunschweig.census.use_zensus_gemeinde_shares: true`) in all
     five `simple_ipf_open` configs, pinned by
     `tests/test_census_gemeinde_shares_wired.py` (discovery fixed to
     `configs/fixtures/`, and it now also asserts the popsim configs do NOT set
     the inert flag). Kreis totals remain the official DESTATIS 12411-0018
     figures; only the spatial key inside a Kreis changes source.
  3. **Delete the five dead config keys** (`home_location_sampling`,
     `osm_path_bavaria`, `braunschweig.population_path`,
     `braunschweig.work_flow_path`, `braunschweig.buildings_path`) from all ten
     configs, and mark the corresponding `CONCERNS.md` entries resolved rather
     than leaving them describing a state that no longer exists.
  4. **Instrument the composition-relaxation fallback** that activating
     age-aware chunking makes live, per the mandatory no-silent-fallbacks rule.
- **Rationale:** The Zensus table is the better source on three independent
  counts: it is open (dl-de/by-2-0) where urbistat forbids redistribution, so a
  reproducible run no longer depends on a non-redistributable input; it carries
  the authoritative 12-digit ARS, removing fuzzy Gemeinde-NAME matching against
  VG250, whose failure mode is silently dropping a Gemeinde's population from the
  redistribution; and its age-band edges align with the DESTATIS classes instead
  of smearing class 10 (ages 10-14) into a 12-17 band. Deleting rather than
  documenting the dead keys follows the ADR-0077 principle that the repository,
  not prose, is the state of record: a key nothing reads misleads every future
  reader about which inputs a run needs, and two of these made an absent file look
  like a broken dependency. A dead key was only removed after three independent
  checks agreed — the key appears as a quoted literal nowhere in the source trees,
  or its only readers are stage modules absent from every committed DAG snapshot
  (which come from a `synpp.run(dryrun=True)` resolving all transitive
  dependencies, so absence means nothing can require them) and named as no
  config's alias target.
- **Evidence:** Verified on real SK Braunschweig census data (IPF fixture scoped
  to one Kreis) BEFORE the configs were changed, as an OFF/ON pair. The joint
  margin builds from 1000A-3082 and rakes to 36 cells / 252,962 persons; the IPF
  stays feasible and in fact improves (max relative deviation 0.5698% ON vs
  0.7431% OFF); the age-aware pass forms 134,997 age-plausible,
  `hh_type`-consistent households from 252,954 persons with 0 dropped (OFF:
  137,570 households). The relaxation fallback measures 33,906 of 252,954
  composition slots (13.4%). Documentation checks at 0 FAIL; `simple_ipf_open` DAG
  178 -> 179 edges with `production` and `popsim_open` unchanged.
- **Consequences:** Production output is unchanged — all three items touch the
  `simple_ipf_open` path only, and `braunschweig.data.census.population` appears
  in neither the `production` nor the `popsim_open` DAG. Legacy-IPF runs change:
  household composition differs, and cached `braunschweig.ipf.*` /
  `braunschweig.data.census.population` stages are devalidated. The Zensus file
  `eqasim-data/data/braunschweig/1000A-3082_de_flat.zip` becomes required for any
  IPF run (local-only, not committed); the urbistat scrape is no longer needed to
  reproduce any committed configuration. Enabling the joint margin pulled
  `braunschweig.data.census.households_size_age` into the `simple_ipf_open` DAG,
  so it gained a stage-registry record.
- **Limitations (explicit):** The Zensus *share* code path is unit-tested
  (`tests/test_population_zensus_shares.py`) but was NOT executed on real data —
  the verification runs above had that flag off and still used urbistat; runtime
  merge coverage is logged via `_log_merge_cell_coverage`, so a silent failure
  would surface. None of the three features is validated against an observed
  reference: no recorded run compares the realised age x household-size structure,
  composition or same-sex couple share to observed data, so their registry
  `validation.state` stays `unvalidated` and `assessment.status` stays `pending`.
  The mother-age anchor (31.8) and the same-sex couple share (0.011) remain
  hardcoded assumptions with prose-only provenance, not committed tables — ADR-0005
  calls the former a "committed reference", which no file substantiates.
- **Alternatives rejected:** (a) Setting the three flags in `configs/base_bs.yml`
  to make them "production" — they would be dead keys there, since `popsim_mid`
  aliases `data.census.filtered` to `braunschweig.popsim.stage`; that is exactly
  the defect this ADR removes. (b) Recording the dead keys as a known concern (the
  prior state) — prose that describes cruft does not stop the cruft from
  misleading readers, and the existing entry had already drifted, naming one
  fixture where ten configs were affected. (c) Marking the features
  `production.enabled: true` in the registry — check K2 derives production state
  from `popsim_mid` applicability, so the honest value stays `false`.
- **Issue / PR:** #251 · PR #305
