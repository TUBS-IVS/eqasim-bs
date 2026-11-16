> Historical snapshot, not maintained. Moved here from docs/codebase/CONCERNS.md (audit package L, 2026-11). It describes configs and the state of 2026-07-10; its open items were not re-verified.

## Feature-wiring audit vs. the active popsim run config (2026-07-10)

Full front-to-back check: every implemented feature vs. `config_server_braunschweig_100pct_allfeat_popsim.yml`
on `origin/main` (`b6ba420`) AND the actually-running untracked server config
`~/wt-kreis-run/config_run_kreis5_100pct.yml` (fetched from felix; semantically identical except
output paths `*_kreis5`, `settings_tier3_mef100_intseed_numba.yaml`, `num_workers 4`,
`cache_share_recompute: completed_donor`).

**GAP (very likely forgotten, affects the running kreis5 run):**
- `secondary_other_smart_potential` + its whole 5-key block (`secondary_other_broad_share 0.54`,
  `_errand_share 0.46`, `_min_volume_m3 50.0`, `_cap_percentile 0.99`, plus
  `secondary_scorer_attr_transform: linear`, `secondary_scorer_selection: top_n`) is `true` in
  ALL other real-data configs since `2732c18` (2026-06-28) but MISSING from the 100pct popsim
  config (created 2026-06-30, `3703292`, without the block) → runs with default **False**.
  Effect: `other` activities fall back to the generic (VW-plant-dominated) potential.
  Cheap fix: add the block; popsim caches stay valid, only secondary-location stages recompute.
  RESOLVED differently: ADR-0190 removed the derived `other` potential and its five keys; `other` now
  uses the delivered errand value of the redistribution release.

**Built but never activated in ANY config, absent from PROJECT_STATUS/BACKLOG/DECISIONS (decide: wire or document as parked):**
- `braunschweig.gravity.sector_aware_enabled` (default False; `a0ecee3` 2026-06-04) — establishment-density
  sub-Kreis attraction tilt, `braunschweig/gravity/model.py`.
- ~~`braunschweig.census.use_zensus_gemeinde_shares`~~ — **RESOLVED (issue #251).** Now `true` in all
  five committed `simple_ipf_open` configs, so the open Zensus 1000A-3082 Gemeinde shares replace the
  scraped non-redistributable urbistat table; pinned by `tests/test_census_gemeinde_shares_wired.py`.
  Correctly absent from the popsim configs, where the stage is off the DAG.
- `braunschweig.use_landuse_prior` (default False) — `braunschweig/data/inspire/landuse.py` has NO pipeline
  consumer (orphaned stage; only tests reference it).
- `education_bbs_share_by_age` (default None) — optional age-resolved BBS share; scalar 0.681 active instead.

**Verified WIRED in the running kreis5 config (no action):** popsim_mid tier0-3 + employment_grid +
`optimized_2026_06_30` importance profile; all five KREIS attribute controls (economic_status, cars,
bicycles, has_ebike, trip_class) default `"on"` in `braunschweig/popsim/stage/`; W_ZWD #127 splits
(`secondary_leisure_subtype_split`, `secondary_other_subtype_split`, `leisure_visit_building_potential`) true;
income tilt + income_kreis_control; education gravity; building potentials (work/secondary/edu);
purpose-resolved distances; fleet (household/brands/HSN-TSN/BEV); cordon; freight; urban parking;
remode_carless; per-RS7 gravity; enrichment flags default-True (PT-Abo, licence, cars-income, tenure,
status, income-EUR — consumed by `braunschweig/synthesis/population/enriched.py` + `popsim/stage/`,
i.e. active on the popsim path).

**Deliberately OFF (documented, no action):** `taz_work_location_choice` (Phase-3 validation open, #83),
`gravity_friction_factors` (ADR-0050), circuity `mode=curve`, `simwrapper_dashboards` (Java Layer-1),
`matching_similarity` (step 3 deferred), `mode_choice` (no modal-split target),
`stratify_regiostar: false` (changed on main).

**Config drift:** the live run config `config_run_kreis5_100pct.yml` is untracked on felix — commit it
(or a copy) for traceability/RUNS.md. Note: local branch `docs/status-presentation` is BEHIND
`origin/main` (misses PRs #144/#145/#146 + TAZ code); IPF chunking/joint-margin flags in the popsim
configs are inert (simple_ipf path only).

Evidence: `config_server_braunschweig_100pct_allfeat_popsim.yml`, `config_server_braunschweig_25pct_allfeat_popsim.yml`,
`braunschweig/popsim/stage/__init__.py:252-320` (pre-split line numbers, not reverified
against the post-#267 package layout), `braunschweig/gravity/model.py:646`,
`braunschweig/data/census/population.py:118-123`, commits `2732c18`, `3703292`, `a0ecee3`, `1f55fc2`.

---

> **LIVE BACKLOG MOVED (2026-06-27).** The ranked open-work backlog now lives in
> [PROJECT_BACKLOG.md](../../PROJECT_BACKLOG.md); the at-a-glance feature/status dashboard
> in [PROJECT_STATUS.md](../../PROJECT_STATUS.md). To avoid two competing backlogs, treat
> those as **canonical**. This file keeps the **structural/historical concerns** below.
>
> **Resolved since the 2026-06-26 backlog (for the record):**
> - **Calibration corner LANDED** — the bulk merged via **PR #18 + #19** on `main`
>   (`031aefc`); the remainder (leisure-correction fix, `_load_stage` alias, scorer-sweep,
>   income-scaling skip) is open **PR #20** (`reconcile/calibration-remainder`).
> - **Secondary ON-validation: RUN at 100 %** — the leisure double-count bug was fixed
>   (W12 leisure EMD 0.131 → 0.050); scorer `pot_weight=1.0` confirmed optimal (a sweep
>   showed higher values *worsen* the building-capacity fit).
> - **Building-potential FIT report: now EXISTS** (`braunschweig/calibration/run_building_fit_secondary.py`
>   + a work fit report) — the "tool does not exist" item is closed.
> - **Branch hygiene done** — 3 feature-superseded prototype branches deleted (local+origin:
>   `secondary-external-candidates`, `cordon-supply`, `cordon-incommuters`); the
>   `feature/calibration-corner` divergence is resolved by PR #18/#19/#20.
> - **LoD2 height/volume typing** and **real `potential_work` work-gravity**: verified DONE
>   (not partial — earlier "PARTIAL" verdicts were from a stale tree).
>
> **Still open** (detail in PROJECT_BACKLOG.md): merge PR #20 (after a server test run);
> 100 % production run on newest code; mode-choice ASC calibration; German MiD Wege donor;
> update `SESSION_LOG.md`; the pre-existing local test failure
> `test_employed_valid_codes_map_to_existing_semantics` (fails on `main` too).
>
> **Open data question (unchanged):** `potential_work` is a zone-controlled (GENESIS SvB)
> × volume×class within-zone proxy; whether it rests on observed per-building headcount
> depends on the upstream `TUBS-IVS/Activities-and-Potentials-Calculation-Pipeline`.
> `[ASK USER]` whether to inspect that repo to confirm the redistribution base.

