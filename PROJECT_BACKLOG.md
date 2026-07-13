# Project Backlog & Open-Work Map — eqasim-bs

> **Purpose.** A ranked, honest inventory of everything we *planned* but only *partially*
> implemented, deliberately parked, or designed-and-forgot — cross-checked against what the
> code already does (so superseded ideas are marked dead, not re-attempted).
>
> Created: 2026-06-27. Companion to `docs/ONBOARDING.md` (narrative), `docs/DECISIONS.md`
> (the *why*), and `CLAUDE.md` (binding instructions). Where they disagree, `CLAUDE.md` and
> git win — this is a point-in-time snapshot.
>
> Sources: 50 Claude memory files, 14 design specs in `docs/superpowers/specs/`, git
> branch/merge analysis, `SESSION_LOG.md`, direct code inspection.

---

## 0. TL;DR — the live state in five sentences

1. **Almost everything we built is merged to `main`** (synthesis, fleet, cordon einpendler,
   freight, education gravity, income tilt, simwrapper, building-activity-potentials via
   PR #16/#17). The model is in good shape and validated at synthesis level.
2. **Most of the Calibration Corner is ALREADY on `main` via PR #18** (`a627fca`,
   "Calibration corner: commute/secondary distribution calibration + building-potential fit
   reports"). The local `worktree-calibration-corner` branch (+68) only adds a **789-line
   net delta** on top of PR #18: (a) external secondary candidates, (b) the scorer-sweep
   bench, (c) an income-scaling perf tweak (skip INKAR scaling when `income_kreis_control`),
   (d) secondary-distance validation tweaks. **A 3-way merge of the remainder conflicts in 3
   files** (`secondary_chainsolvers.py`, `validate_secondary_distances.py`,
   `test_sample_leg_distance_purpose.py`) because PR #18 touched the same code — so landing
   it is conflict-resolution + a server test run, not a clean fast-forward.
   `feature/calibration-corner` (ff26d45) is a stale pre-squash state superseded by PR #18.
3. **Two genuinely planned-but-never-built designs** sit on disk: PopulationSim *importance*
   calibration (zero code) and one half of the gravity distance-distribution calibration.
4. **The "produce results" front is the real bottleneck**, not features: no 100% production
   run exists on the newest code, and mode choice is still OFF (no calibrated modal split).
5. **The German MiD Wege trip donor is DONE** (not blocked, as previously framed): the
   `popsim_mid` workflow already uses the MiD Wege as the activity-chain donor — merged to
   `main` via `feature/population-method-workflows` (merge `cd9d217`); see issue #24 (closed
   2026-06-29). ENTD-2008 survives only as the `popsim_open` donor + the 1.3 detour constant.
   The *narrower* lever that genuinely remains is **seed-donor diversity** (the ZENSUS100m
   household composition is donor-bound — rare/large household types are thin in the MiD seed;
   see §2.1 + the popsim nachsteuern findings).

### New (2026-07-13) — employment_status Phase-0 measured (ADR-0058); open follow-ups

The `employment_status` attribute (PR #168, MERGED) was measured against the independent MiD 2023 P9
reference: **uncalibrated fit already good** (SRMSE 0.194, mean|Δ| 1.88pp, grade "good", all 56
Kreis×class cells <10pp, r² 0.979). See ADR-0058 + RUNS `empstatus-measure-2026-07-13`. Open items:

1. **Phase-1 soft control on employment_status — DROPPED (not dead-ended, just not worth it).** Given
   the good uncalibrated fit, a per-Kreis soft control adds little (measure-before-calibrating). No
   issue opened. Re-open only if the full-main re-measurement (item 3) shows a materially worse fit.
2. **#167 (OPEN, dormant) — SPC_BY_P_BKAT misreads P_BKAT as Berufskategorie.** `socioprofessional_class`
   (INSEE CS1) is derived from the wrong variable; scrambled for employed persons but NOT consumed
   anywhere (mode_choice OFF, not a control) → dormant. Fix before CS1 is ever consumed: needs a real
   occupation variable (likely absent from the standard MiD table) or drop the invented crosswalk.
3. **Re-confirm the P9 fit on the next full "everything on main" 100% run.** This session's number used
   the kreis5 balancing, which predates **#170** (Azubi employment_target fix, MERGED 2026-07-13) and the
   GEO_KREIS zfill fix. The canonical employment_status validation drops out of the full-main run
   (`analysis_suite` population_validation) — check whether #170 moved the `in_ausbildung` +1.7pp skew.
4. **`in_ausbildung` +1.7pp / `vollzeit` +1.6pp** are the only notable class deltas. If they persist on
   main, address `in_ausbildung` at the SOURCE (P_BKAT code 6 vs the P9 in_ausbildung definition / age
   base), not by raking.
5. **Trivial:** a few orphaned synpp cache entries (bumped-hash `popsim.stage`/`sampled`/`enriched`) from
   this measurement sit in the shared `cache_bs_100pct_allfeat_popsim`; harmless, ignored by future runs.

> **Note:** this whole thread is downstream of the deferred **"everything on main" full 100% run** (see
> §"produce results" / TL;DR 4) — that run delivers the canonical employment_status number for free.

### New (2026-07-12) — Full-pipeline bug-audit wave -> PR #165 (OPEN), issues #160-#163

An orchestrated read-only audit of the whole synthesis pipeline (vs `origin/main` `d92328e`) surfaced
**19 verified bugs**, all fixed the same day on `fix/audit-wave-20260712` -> **PR #165 (OPEN)**. Open
follow-ups:

1. **Merge PR #165** after the canonical popsim pytest passes on felix (local env has 11 known
   pre-existing failures). PR Closes **#160** (crit, distance_distributions coded-time drop), **#161**
   (powertrain Gemeinde tilt umlaut/suffix join), **#162** (weekend_plan_match employment set); works the
   14-item **#163** fallback-transparency checklist.
2. **One-time popsim batch purge on next server run.** The #163 batch-config-signature fix now hashes the
   `census_source` composition, so the first run against a persistent `work_dir` after this merges will
   purge + rebuild all completed batches. Schedule it so it does NOT collide with the live kreis5 run.
3. **Re-run decision for kreis5 stages built on old code.** The running 100% kreis5 run produced the fleet
   powertrain (2026-07-11) and secondary-distance stages with the pre-#160/#161 code. Decide whether to
   selectively re-run those stages after the merge. See memory `project-audit-wave-2026-07-12`.

### New (2026-07-10) — Full-pool popsim perf regime (ADR-0056) + follow-ups #153 / quality A/B / upstream reports

The kreis5 100% run was relaunched with `SUB_BALANCE_WITH_FLOAT_SEED_WEIGHTS: false` + `USE_NUMBA: true`
(measured ~40x per batch, A/B 32.6 min vs ~22 h; see ADR-0056 for the float-seed trap mechanics).
Open follow-ups, in order:

1. **Quality A/B vs float reference (BLOCKS calling the speedup validated).** Float reference batch
   running niced on felix (`~/bench_batch_float`, launched 2026-07-10 14:11, done ~2026-07-11 midday).
   Compare vs the INT batch_000: 100m control fit (MAE/SRMSE/paired win-tie-loss), donor diversity
   (INT: 3,140 distinct donors / 13,845 HH), person marginals. 1km HH totals already exact in both.
   **Harness READY + self-tested (2026-07-10):** one command, `ssh felix '~/ab_quality/run_ab.sh'`
   (script reuses `integerizer_quality.cell_error`; self-test batch_000-vs-itself all-zero, 44/44
   controls resolved; float-bench inputs verified byte-identical to batch_000).
2. **Issue #153 — DONE pending merge: PR #155 OPEN.** `cleanup_batch_pipeline` flag (default ON,
   explicit in both server configs) deletes per-batch `pipeline.h5` (~15 GB dead weight at full pool)
   after VERIFIED completion (incl. skipped leftovers; failed batches keep the h5 for PopulationSim
   resume; OSError-hardened). TDD, 9 new tests, 44 batch tests green locally. Pre-merge: canonical
   popsim pytest on felix (local matsim shadowing) after the current run ends. Interim server watcher
   (`~/cleanup_batch_h5.sh`) still covers the CURRENT run (its code predates the fix).
3. **Optional upstream reports (activitysim/populationsim v0.10.0):** (a) missing `MIN_GAMMA` clamp
   in the python single balancer (NaN risk), (b) hardcoded `converged=True` on no-progress exit,
   (c) dense final-geography weight table checkpointed but never read. Draft on request.

### Resolved (2026-07-03) — Issue #97 household_size validation basis FIXED (PR #103 merged) + follow-ups #104/#105

  **#97 FIXED, merged to main (`141284e`).** The population-validation `household_size` control
  compared a HOUSEHOLD-based synthetic count (`bucket_household_control` counting households) against
  the PERSON-based Zensus 1000A-2081 target (which reports persons in private households by size class,
  pinned by `test_hh_size_margin.py`: ~1.135M ZGB persons). Basis mismatch → a large spurious deviation
  (7.7pp/"needs improvement"). Fix = a `weight_column` option on `bucket_household_control`; the
  `household_size` control is registered with `weight_column="household_size"` so the realized side is
  person-weighted (default None keeps cars/bikes byte-identical). TDD (3 new tests); reproduced on the
  100% run (person basis: classes 1-4 <1.2pp, e.g. 1-person 22.1% vs 21.6%). See ADR (household_size basis).
  - **Verified via a felix validation re-run** (isolated detached worktree, non-disruptive): on
    `output_bs_100pct_allfeat_popsim` household_size moves **7.7pp → 1.44pp, "needs improvement" → "good"**
    (SRMSE 2.07→0.18); all OTHER controls byte-identical (diff). See ADR + `feedback-felix-isolated-worktree-rerun`.
  - **#105 (PR #106, open):** correct the `households_type.py` module docstring — the table IS consumed
    by the IPF size margin (aggregated over hh_type) when `use_household_size_margin` is on; it wrongly
    said "NOT consumed by the IPF itself". Doc-only.
  - **#104 (PR #107, open):** refresh the status-deck QA figures now that household_size is person-basis
    (fig_qa1 scoreboard now shows it "gut"/1.4pp; FN2 reworded from "#97 artifact" to "resolved";
    fig_qa2/qa3 comments + README + deck HTML rebuilt). Regenerated from the corrected `quality_summary.csv`.
  - With #96 + #97 both fixed, the **Phase-0 blockers of #99 (regional-correct popsim) are cleared.**

### Resolved (2026-07-03) — Issue #96 minor-employment inflation FIXED (PR #101 merged) + guard (PR #102 open) + #25 closed

  **#96 FIXED, merged to main (`8f652c4`).** Root cause = a field-width missing-code collision in
  `braunschweig/popsim/missing.resolve`: the generic `NONRESPONSE_CODES = {9,99,...}` ignored MiD
  field width, so substantive `P_TAET=9` (Schueler; keine Angabe = 99 for the two-digit field) was
  classified as nonresponse and imputed from the non-pupil pool of its `alter_gr1` band (14-17
  dominated by Azubis -> True), inflating the written `employed` flag (14-17yo ~96%, region +7-9pp).
  Fix: `nonresponse_set = (NONRESPONSE_CODES - set(spec.value_map)) | set(spec.impute_codes)` — explicit
  value_map codes win over the generic convention (also fixes latent `hheink_gr1=9` / `H_ANZAUTO=9`).
  The Tier-3 employment control was already correct (raw `P_TAET.isin`); only the written attribute +
  population-validation were affected. TDD; felix pytest 320 passed. See ADR-0052.
  **#25 closed** (stale `test_employed_valid_codes_map_to_existing_semantics`, fixed independently by
  `d6556b6`+`aaafc60`; distinct bug from #96).
  - **MERGED — guard (PR #102, on main):** `controls.check_minor_employment` watches the under-15
    employed rate, default-ON WARN (`analysis_minor_employment_max_rate=0.005`,
    `analysis_minor_employment_raise=False`), writes `minor_employment_guard.csv`. **NEXT:** (1) **100%
    re-run with the fix on main** (Phase-0 blocker for #99 regional-correct popsim redesign); (2) then
    flip `analysis_minor_employment_raise=True` once the re-run confirms the true post-fix under-15 rate
    (measure-before-harden — the 0.5% bound is an ASSUMPTION).

### New (2026-07-03) — E-bike/pedelec ownership as person attribute (spec + issue, NOT built)

- **E-bike/pedelec ownership** — GitHub issue **#100** (fork). Designed, not implemented.
  Add `ebike_ownership ∈ {ja,nein,keine_angabe}` + derived `has_ebike` per person (≥14) via a
  **configurable multi-margin IPF (raking)** on **MiD 2023 P21** ("Besitz Elektrofahrrad/Pedelec"),
  written to MATSim as `ebikeOwnership`/`hasEbike`. Mirrors the existing licence (P17.1) / PT
  (P24.1) categorical-IPF blocks exactly. **Default 6 margins** (all toggleable via
  `ebike_ownership_margins` so each margin's contribution is *measured*, not assumed):
  Kreis × sex × age (P21 Tab. A) × economic_status × household_type × license (P21 Tab. B).
  Data via a **new Tabelle-B extraction path** in `scripts/extract_mid_tables.py` (currently only
  Tab. A is parsed; P21 Tab. A = PDF page 99, Tab. B = page 100); new committed CSVs
  `mid2023_P21{,_by_sex,_by_age,_by_economic_status,_by_household_type,_by_license}.csv`.
  Honest caveats (in spec): age dominates; econ-status flat except "sehr niedrig"; strongest raw
  gradients (Mobilitätssegmente, HH mobility equipment) **excluded as data leakage/circular**;
  licence is the one worthwhile age-orthogonal extra control. Master flag `ebike_ownership`
  default-ON, OFF = attribute absent (otherwise byte-identical). Spec (gitignored):
  `docs/superpowers/specs/2026-07-03-ebike-ownership-ipf-design.md`.
  - **NEXT:** writing-plans → worktree → TDD implementation. Follow-ups (separate): e-bike as a
    distinct MATSim mode + calibration; education as a synthesised attribute (would add a margin).

### New (2026-07-03) — Fleet-quality realism upgrade (pushed, server-verify + merge pending)

- **Fleet realism upgrade** — branch `feature/fleet-quality-and-data` (fork, tip `5e0a6e4`, 41
  commits stacked on `fix/fleet-age-joint-ipf` PR #92; worktree `eqasim-bs-fleet`). Integrated into
  the EXISTING fleet model (IPF-based), default-ON flags, byte-identical when a derived CSV is absent,
  **no NAs anywhere** (pure-electric uses euro="electric"). Adds: EV-income tilt (MiD A_ANTRIEB ×
  oek_status, within-Kreis, aggregate preserved), all-Kreise 46251 (in-commuter home-Kreis mix +
  `LazyKreisEuroJoint`), Euro-6 substage draw (distinct HBEFA concepts), RegioStaR7 logging-only EV
  cross-check, plus three whole-model review fixes (model-fuel scale-neutral weight, validator
  effective-segment target, same-vintage 2026 Gemeinde-EV tilt). Final opus whole-branch review done;
  **Finding #2 fixed** (degenerate-Kreis NaN-pmf crash → `_rake_per_kreis_powertrain` guard, 9 tests).
  Decisions in **ADR-0051** (worktree `docs/DECISIONS.md`); user signed off that OFF is intentionally
  not byte-identical (shared segment-seed improvement `9eb2050` kept; 2 OFF goldens regenerate on
  server).
  - **PENDING (deferred by user; "nur pushen"):** (1) **server phase** (`ssh felix`): scp raw kba/ +
    MiD Autos, run `scripts/extract_kba_fleet.py` + `scripts/build_mid_antrieb_by_status.py`, full
    canonical pytest, 1% smoke (per-Kreis euro varies, NO NaN, age ~10.6–10.9, EV↑ with income within
    Kreis, in-commuter home-Kreis mix, euro6 substage, combustion-split vs 46251-02, OFF-equivalence),
    **regenerate the 2 stale OFF goldens** (`test_off_path_byte_identical`, `test_age_income_off_unchanged`);
    (2) **merge via `git pr`** — **resolve the ADR-0050 number collision** (this branch's ADR-0050 =
    fleet data-regionalization ceiling; `main`'s ADR-0050 = TAZ friction); also merge the stacked
    fleet PRs #90/#93 (close #86/#91/#92); (3) then the **100% re-run**. Full state: memory
    `project-fleet-quality-realism`.

### New (2026-06-30) — TAZ sub-zonal work location choice + commute-fit diagnostics

- **TAZ sub-zonal work location choice (eqasim IRIS-analog)** — issue **#79**, spec
  `docs/superpowers/specs/2026-06-30-taz-subzonal-work-location-choice-design.md`.
  Root cause established this session (systematic-debugging): the Gemeinde-resolution
  gravity has no intra-city structure for the kreisfreie Staedte (BS/SZ/WOB = 1 Gemeinde);
  the within-Gemeinde candidate pool (co-located homes+jobs) governs the realised distance
  histogram -> intra-city commutes too short (per-Kreis EMD ~0.12 residual vs robust RS7/ZGB;
  the WOB per-Kreis MiD P13 target is **n=39**, unreliable). Fix = fill eqasim's sub-commune
  zone slot with RVB VISUM Verkehrszellen (TAZ), reuse the zone-agnostic eqasim functions,
  BA stays Kreis-anchor. **Flag-gated default OFF; TAZ data local-only (proprietary VISUM,
  not publishable).** Open-data pseudo-zone alternative = issue **#80** (TODO, deferred).
  - **STATUS 2026-07-01: Phase 1+2 MERGED to `main`** (PR #85 merge `f5f52d1` + PR #89 FutureWarning
    fix); flag-ON 1% e2e green. **Phase 3 (#83): friction re-fit BUILT then measured unnecessary.**
    Branch `feature/taz-gravity-calibration` @ `3c2ebb5` (6 commits, all SDD tasks + final opus review
    clean) adds a `--taz` mode to `calibrate_gravity_distribution.py` (work-pass-scoped per-RS7 friction
    on the TAZ work-OD). But the aggregate commute distribution **already fits MiD P13** (measured EMD
    ~0.054 on the current 100% `popsim_mid` pop, flag-OFF, ZGB-resident; WOB per-Kreis ~0.21 = n=39
    noise, ADR-0049); a 1% flag-ON A/B even IMPROVES the aggregate (0.057->0.033). So the branch is
    **PARKED (pushed to the fork as backup, not merged), gated-off infra** — reuse only if a future measurement shows a real
    gap (ADR-0050). **Remaining Phase-3 = validate the flag-ON TAZ at 100%** (`taz_work_location_choice:
    true`, `matsim_last_iteration: 0`; multi-hour — origin/main's popsim/secondary sources differ from
    the commit that built the 24G flag-OFF cache, so it rebuilds) + a **spatial validation map** (OSM
    basemap) of the TAZ commute / work-location distribution.
- **Distance-fit diagnostics module** (this session, NOT yet landed): `braunschweig/calibration/distance_fit/`
  (Phases 0-5.1 built + 26 local tests green + real-data-validated on the 25% cache via slim
  parquet) on worktree branch `worktree-fix+gravity-calib-popsim-mid`, plus the
  `calibrate_gravity_distribution.py` popsim_mid fix. Needs: server test run, the n-awareness
  hardening (min-n flag + Kreis-type split, surfaced by the WOB n=39 finding), then `git pr`.
  Spec `docs/superpowers/specs/2026-06-29-distance-fit-diagnostics-design.md`, plan
  `docs/superpowers/plans/2026-06-29-distance-fit-diagnostics.md`. Issue #24 (MiD Wege donor)
  closed this session (DONE via popsim_mid).

### New (2026-06-28)

- **Re-sync `eqasim-data/data/` from the run server** (issue: data-loss recovery). A recursive
  force-delete of a leftover worktree followed an `eqasim-data` junction and wiped the local
  `data/` subtree. Git-tracked files were restored; the **gitignored local-only data**
  (buildings parquet, `nds_*.csv`, osm/gtfs/vg250/germany/...) must be re-synced from
  `felix@...:/home/felix/eqasim-bs/eqasim-data/data/` (authoritative copy intact). Caches survived.
- **Secondary `other` over-concentration** (issue #27 — **PR #77 OPEN**, 2026-06-28): combined
  scorer raw-sums volume-driven `generic` potential (VW-Werk = 26.7M) vs. metre-scale distance;
  `other` placement concentrated on industrial mega-structures.
  - **DONE in PR #77 (Part A, active, OFF byte-identical):** function-aware `potential_other` via a
    committed Bosserhof class→purpose mapping CSV — `min(generic,cap)×(0.54+0.46·whitelist)`, zeroed
    below `min_volume_m3`; broad/errand shares from MiD W_ZWECK (5/6/10). Enabled in the 5 real configs.
  - **DONE in PR #77 (Part B, infra only):** chainsolvers bumped to `d8d8ae7d` (native
    `attr_transform=log1p` + `mnl` + circle-intersection fix); `build_scorer` passes attr_transform;
    `scripts/calibrate_secondary_scorer.py` + `build_secondary_loss` (per-purpose W12 + concentration).
  - **OPEN (server, measure-first):** run `calibrate_secondary_scorer.py` on `cache_bs_25pct_allfeat`;
    pin `attr_transform`/weights ONLY on a measured win vs the OFF baseline (shop 0.053/leisure
    0.064/other 0.018) without regressing any purpose. Then the `excess_tv` concentration check +
    gated `dp_sample`/`carla_sample`/`mnl` A/B. Do NOT raise `pot_weight`.

---

## 1. Priority ranking (what to do, in order)

Ranked by **(value × readiness) ÷ cost**, with loss-risk items pulled to the top.

### ⭐ Agreed next sequence (user-directed, 2026-06-27)

Do these in order. **popsim comes first because it is the foundation of the whole model** —
the synthetic population is upstream of gravity, location choice and mode choice, so if popsim
changes, the gravity calibration (which runs on the popsim output) must be redone. Calibrate
the base first, then everything downstream.

1. **popsim — verify configuration, then test the weights.** *(FIRST — foundation.)*
   - **Step 1a: config audit.** Verify our PopulationSim setup (settings.yaml template,
     `controls.csv`, the seed/control geographies) against the official reference
     <https://activitysim.github.io/populationsim/application_configuration.html> — confirm
     we set importance, control geographies, expansion/integerizer and the meta/seed/sub
     hierarchy correctly. (User: "we discussed all that"; expected to be implementation-heavy.)
   - **Step 1b: weights calibration.** Then execute the **PopulationSim importance/expansion
     calibration** (design + 63 KB plan at
     `docs/superpowers/{specs,plans}/2026-06-24-popsim-importance-calibration*.md`): tune
     per-family importance weights + expansion factors vs the Zensus controls (coordinate
     descent, donor KPIs held out, baseline-vs-tuned verdict). *(Previously flagged as
     overfitting-risk — the user wants it tested; report honestly whether tuning beats the
     baseline, never pin a worse result.)*
2. **Calibrate the gravity model.** *(AFTER popsim, on the corrected population.)* First make
   `scripts/calibrate_gravity_distribution.py` **popsim_mid-compatible** (it currently expects
   the IPF-path stage `braunschweig.data.census.population`; the real all-features caches are
   popsim_mid and expose `synthesis.population.sampled` / `braunschweig.popsim.stage` instead).
   Then run it on `cache_bs_100pct_allfeat_synth` to verify / pin the per-band friction weights
   of the detailed gravity model (per-RS7 slope + per-band friction → MiD P13; see §2.4).
3. **Real monetary costs at the end (PT / ÖPNV).** *(LAST.)* Build a real cost model feeding
   the mode-choice utility: **VRB public-transport tariff** (fare zones) + car monetary costs,
   replacing the placeholder costs. It is a mode-choice/behaviour lever, so it lands after the
   gravity + popsim calibration and alongside the mode-choice ASC work (Tier 1.2). Needs a
   committed VRB tariff reference (no invented fares).

### popsim config audit — step 1a findings (2026-06-27)

Audited the applied `settings.yaml` + `controls.csv` (from a cached `popsim_work` batch)
against the official reference
<https://activitysim.github.io/populationsim/application_configuration.html>. The settings
live in the **separate `popsimprep` repo** (`popsim/configs/settings.yaml` /
`settings_tier3.yaml`), wired into eqasim-bs via `braunschweig.population.popsim.settings_path`.

**Correct per the reference (✅):** hierarchy `geographies: [WELT, STAAT, ZENSUS1km, ZENSUS100m]`
in strict nesting order; `seed_geography: STAAT` (national MiD seed); `household_weight_col:
H_GEW`, `household_id_col: H_ID`; `total_hh_control` set to the 100m household-total at the
lowest geography (mandatory); full standard `models` list incl. `meta_control_factoring`,
`sub_balancing` per sub-geography, `integerize_final_seed_weights`; `USE_SIMUL_INTEGERIZER`,
`INTEGERIZE_WITH_BACKSTOPPED_CONTROLS`, `GROUP_BY_INCIDENCE_SIGNATURE` all on.

**To verify / act on (⚠️ — these drive step 1b):**
1. **All 35 controls at `importance: 1000`, including `total_hh_control`.** The reference
   recommends a *very high* importance on the household-total to lock the count. Likely
   mitigated by PopulationSim enforcing the `total_hh_control` setting via the integerizer
   anchor — but **measure** whether the per-cell household total is hit exactly (use the
   integerizer-quality report) before trusting it. This uniform-1000 baseline is exactly what
   the importance calibration (step 1b) tunes.
2. **`max_expansion_factor: 30` (the default) with a NATIONAL seed expanded to 100 m cells.**
   The reference says raise it if rare household types can't be sampled. National-MiD → 100 m
   is a high-expansion regime; 30 may bind. **Measure the realised expansion-factor
   distribution** (how many seed households hit the cap) — if it binds, rare household types
   are under-sampled (a silent quality loss).
3. **The `WELT` meta level carries no control** (controls.csv has only ZENSUS100m/1km targets)
   → `meta_control_factoring` is effectively a no-op. Not wrong; confirm it is intentional
   (reserved for a future national meta control) vs. removable.

### popsim measurement — step 1b results & verdict (2026-06-27)

Measured on the completed 100% server run (`popsim_work_allfeat`, 33 batches; expansion from
`pipeline.h5` `/STAAT_weights/final_seed_balancing` `balanced_weight / sample_weight`,
n=152,277; HH-total from `synthetic_households` vs `control_totals_ZENSUS100m`):

- **HH-total hit EXACTLY** — 43,598 cells, 11 off (0.03 %), total +0.022 %. → `importance=1000`
  on `total_hh_control` is fine; the anchor enforces the count. **Importance tuning would not
  help — the controls are already hit.**
- **`max_expansion_factor=30` binds only ~0.30 %** of seed households (≥25: 0.46 %; 80 % are
  *down*-weighted < 1). A cheap, low-risk raise to ~50–100 would relax those rare-type cases;
  not a major lever.
- **Anomaly:** realised max expansion factor **67 > the 30 cap** → the cap is not hard-enforced
  at 30 (likely integerization / `sample_weight` base). Verify in `popsimprep`.

**Full per-control / per-level fit (measured directly: each control's `expression` from the
batch `controls.csv` evaluated on the seed-attribute-joined synthetic population vs the
`control_totals_<geo>` target, all 33 batches):**

| level | controls | mean \|%dev\| | max \|%dev\| |
|---|---|---|---|
| ZENSUS1km | 1 | 0.02 % | 0.02 % |
| KREIS | 7 | 2.40 % | 3.71 % |
| **ZENSUS100m** | 44 | **6.04 %** | **27.87 %** |

At ZENSUS100m the household *count* is exact but the *composition* is systematically
**under**-achieved (all worst controls negative): HH 6+ persons −27.9 %, multi-person-no-core-
family −21.8 %, building_type sonstiges −18.0 %, HH 5 persons −13.5 %, MFH −8.8 %, F_AGE_0_9
−8.7 %, single-parents −8.6 %. The integerizer hits the HH total but squeezes out large/rare
household types — the very households at the `max_expansion_factor` cap.

**CORRECTED VERDICT.** The config is *structurally* correct, but the 100 m composition is
under-fit by ~6 % (rare types up to 28 %), so **targeted nachsteuern IS warranted** (this
supersedes the earlier "not warranted", which looked only at the HH total):
1. **Raise `importance` selectively** on the under-achieved controls (HH size 5/6+, multi-
   person / single-parent HH types, building types) — not a uniform bump. Data-justified.
2. **Raise `max_expansion_factor`** (~50–100) to release the rare large households at the cap.
3. Residual bias points to **seed donor diversity** (too few large/rare HH in the MiD seed) →
   the German MiD donor lever long-term.
Then proceed to **step 2 (gravity)** on the improved population.

### popsim nachsteuern — proof iteration (2026-06-27)

Tested the levers on a copy of `batch_000` (A/B vs the cached baseline): raised
`max_expansion_factor` 30→75 and bumped `importance` on the 7 worst-fitting controls
(HH 5/6+, multi-person/single-parent HH types, building types).

- **`importance=10000`** → the **simultaneous integerizer thrashed** (>2900 CBC iterations on a
  single sub-zone, growing primal-infeasibility, no completion in ~10 min). Over-constrained.
- **`importance=3000`** (gentler) → **still thrashed** (>4000 iterations, ~18 min, no
  completion), while the baseline (`importance=1000`) integerizes fine.

**Finding:** the importance lever is **bottlenecked by integerizer tractability** with
`USE_SIMUL_INTEGERIZER: true` — even a 3× bump makes the simultaneous integerizer struggle to
converge. Combined with *where* the under-fit sits (rare/large HH types that are thin in the
MiD seed), this points to the 100 m composition under-fit being largely **donor-bound**:
forcing the marginals via importance breaks the integerizer rather than synthesising household
types the seed barely contains. **Implications:**
1. Naive importance/expansion tuning is **not a clean win** — it needs the **sequential
   integerizer** (`USE_SIMUL_INTEGERIZER: false`) for tractable trials, and even then is
   likely donor-limited for the rare types.
2. The real lever for the 100 m composition is **donor diversity** — the **German MiD donor**
   (richer seed of large/rare households). This is the recurring top realism lever (Tier 2.1).
3. A definitive A/B fit number still needs a *completing* tuned run (sequential integerizer);
   not yet obtained — the simul runs did not finish. (Test batch left at `~/popsim_proof` on
   the server for a follow-up seq-integerizer A/B.)

**Importance sweep — attempted, computationally infeasible interactively (2026-06-27).** Tried
to sweep importance 1000/5000/10000 on the representative `batch_000` (1,475 cells):
- simul integerizer **thrashes** when importance is raised (no completion);
- sequential integerizer is **>77 min per run** (orchestrator hit a 50 min timeout, orphaned
  the worker);
- even `NO_INTEGERIZATION_EVER` (skip the integerizer) still spends **>20 min in
  `sub_balancing` over 1,475 cells** per run, and the float weights live in a locked
  `pipeline.h5` until the process exits.
- the **official doc recommendation** (set `total_hh_control` to very high importance, 1e9)
  was also tested (SIMUL): it **did not complete** in ~30 min and the integerizer hit
  **`status INFEASIBLE`** on a cell (hard HH-count + the other controls cannot be satisfied
  with integer seed households → smart-round fallback). So even the doc's literal importance
  recommendation over-constrains this dataset's integerization.
A clean multi-level sweep is **~1 h+ of compute** and only resolvable as an **unattended
background job** with the sequential integerizer (in progress), not interactively. **Engineering judgment:** the importance ceiling is also
*donor-limited* (importance can only redistribute existing seed households, not create the
rare/large types the MiD seed lacks), so the expected payoff is small. **Recommended lever:
the German MiD donor (richer seed)** rather than importance tuning. The exact sweep number
remains available via an unattended run if required.

### TIER 0 — Do now (cheap, high urgency, prevents loss / unblocks everything)

| # | Item | Why now | Effort |
|---|------|---------|--------|
| 0.1 | 🟢 **MOSTLY DONE (2026-06-27).** PR #18 **and** PR #19 already landed the bulk of the calibration corner on `main` (`031aefc`: purpose-resolved secondary, building-potential fit reports, purpose-aware fallback, external secondary candidates). The genuine remainder is now a **single clean commit** `9662b12` on branch `reconcile/calibration-remainder` (**7 files / 441 lines**: leisure-correction fix, `_load_stage` alias, scorer-sweep bench, income-scaling skip, 2 tests; 3 conflicts with PR #18/#19 resolved to the bug-fixed branch side; `py_compile` OK). **REMAINING: run the test suite on the server (matsim-shadowing breaks local tests) → then push + ONE PR, base = TUBS-IVS/eqasim-bs (never upstream).** Both need your go. Then retire stale `feature/calibration-corner` + `worktree-calibration-corner`. | Pending server test + push OK. | S |
| 0.2 | ✅ **DONE (2026-06-27).** Reverted the stale partial edit in `braunschweig/popsim/distance_distributions.py` — it was an *older* version of the Task-5 work that is already complete on `worktree-calibration-corner`. Working tree is now clean. | — | — |
| 0.3 | ✅ **DONE (2026-06-27).** All three feature-superseded prototype branches deleted **local + `origin`**: `feature/secondary-external-candidates` (eadf823), `feature/cordon-supply` (a645ba5), `feature/cordon-incommuters` (75adbee). Verified file-by-file that every feature/data file lives in `main` under refactored names (§5). SHAs recorded for recovery. Still retirable after 0.1 lands: stale `feature/calibration-corner` + `worktree-calibration-corner`. | — | — |
| 0.4 | **Push `integration/all-features`** (local ahead of origin) once 0.1 lands, and **update `SESSION_LOG.md`** (nothing logged since ~06-18: fleet v2, fleet income-age, ALKIS homes, employment grid, tier-3, building potentials, calibration). | Handover/reproducibility debt; user must approve push. | S |

### TIER 1 — High value, mostly ready (the "produce defensible results" front)

| # | Item | Status today | What's left | Effort |
|---|------|--------------|-------------|--------|
| 1.1 | **100% server production run on newest code** (all features ON: popsim_mid + fleet + cordon + freight + building potentials). | Tier-A/B caching built to make this affordable; last *live* 100% run used older code. | Push integration branch → 25% seed run (auto-exports cache) → 100% 200-iter run. Mostly compute. | M (compute) |
| 1.2 | **Mode-choice ASC calibration** (turn DMC on, run eqasim ASC loop, anchor modal split to a *committed* MiD reference — `mid_mode_margin_by_bundesland.csv` all-trip; P12_1/P38.4 as spatial cross-check). | DEFERRED on purpose ("erst später"). Mode choice currently OFF → no behaviourally valid split. References already committed. | Java-side ASC loop wave; honest reporting (convergence ≠ validation). | M–L |
| 1.3 | **Finish Tier-A/B cache config wiring**: put the 32-stage `cache_share_stages` list + fixed `popsim_work_dir` into the server configs; verify the completed_donor byte-identity gate test exists. | Code done (`cache_share.py`, `completed_donor.py`); configs not all updated. | Config edits + one test. Enables 1.1 to be cheap. | S |

### TIER 2 — Real model improvements, partial or designed (assess value before building)

| # | Item | Status | Assessment | Effort |
|---|------|--------|-----------|--------|
| 2.1 | **Seed-donor diversity** (richer MiD seed for the ZENSUS100m household composition). *Was: "German MiD Wege trip donor" — that part is **DONE**, see below.* | The MiD Wege **trip donor** is implemented: `popsim_mid` uses MiD Wege as the activity-chain donor (merged `cd9d217`; issue #24 closed 2026-06-29). The **remaining** gap is seed *composition*: the popsim audit (2026-06-27) found the 100m household composition under-fit by ~6 % (rare/large types up to 28 %), and importance tuning thrashes the integerizer rather than synthesising types the MiD seed barely contains → **donor-bound**. | **Narrower than "replace ENTD".** A richer / larger MiD seed (more large/rare households) is the real lever; blocked on a bigger MiD microdata sample (SUF). Worth scoping the data-access path. Tracked as a new issue. | L |
| ~~2.2~~ | ~~LoD2 height → volume-weighted dwelling capacity & typing~~ | ✅ **DONE (verified 2026-06-27).** `data/lod2_heights.py` (OI→height), non-destructive `join_lod2_heights` by `OI` in `data/buildings.py` with coverage logging, and `building_typing.py` uses `building_volume(area, height)` end-to-end (volume-rank MFH typing, `MFH_MIN_FLOORS=4` tuned on a Salzgitter real-pop sweep, volume-weighted `build_slots`). ALKIS `OI` passthrough tested (`test_preprocess_alkis_oi.py`). | The earlier "PARTIAL" verdict was from a stale tree. | — |
| ~~2.3~~ | ~~Real building worker dataset vs area*floors proxy~~ | ✅ **DONE (verified 2026-06-27).** `locations/work.py` with `work_building_potentials=True` (default) **REPLACES** the candidate set with the real computed `potential_work` from the building-activity-potentials parquet — `area*floors` is only the OFF/legacy path. Per-commune `potential_work` sum is printed as a Census-SvB cross-check. The "area*floors proxy pending" memory note is obsolete. | — | — |
| 2.4 | **Education distance-distribution calibration (×3 levels)** — the second half of the gravity distance-distribution plan (Phase 2, reuses the Tier-3 shared layer). | PLANNED-ONLY. The *commute* half was built (per-band friction on the worktree branch) and then **found unnecessary** (commute already matches MiD P13). | Reuse the now-built machinery. Only do it if education trip-length validation actually shows a gap — **measure first** (see Lessons). | M |

### TIER 3 — Deferred-deliberately / future waves (parked with intent, not forgotten)

| # | Item | Note |
|---|------|------|
| 3.1 | **Kreis-level income control** (income as a PopulationSim control via MiD H4 status-per-Kreis + INKAR, instead of post-hoc scaling; retire the overwrite so income geography is placement-based). | **Now specced + tracked: #108** (income/status sibling of #99). Direct target found — MiD H4 status-per-Kreis (regional-study PDF p.20) → no SAE needed. Within-Kreis *extra* signal (#73/ADR-0045, rejected) revisited at **Gemeinde** level via LSN income-tax + KBA-EV-per-Gemeinde validation. Spec: `docs/superpowers/specs/2026-07-04-income-weighted-household-placement-design.md`. |
| 3.2 | **BASt Dauerzählstellen HGV-count calibration** for the injected freight. | Future external-validation wave; freight currently taken as-is from german-wide-freight v3. |
| 3.3 | **Real VRB/DELFI GTFS + VRB PT tariff (B2)** + MATSim termination/iteration tuning. | Supply-side + behaviour wave; uncalibrated cordon gate-gravity beta/capacity_exponent also lives here. |
| 3.4 | **Cordon sub-projects 3 & 4** (external *visitors*, non-freight *through-traffic*). | Never started; explicitly out of scope in the original roadmap. Through-freight is already covered by the freight module. |
| 3.5 | **HSN/TSN → engine power / Fahrleistung / CO2 (HBEFA) wiring**; economic_status × #earners margin. | Fleet attributes are present but not yet *consumed* for emissions; user parked this ("income/socio saturated"). |

### TIER 4 — Polish / nice-to-have

| # | Item | Note |
|---|------|------|
| 4.1 | **SimWrapper polish**: verify choropleth colours render by value, anglicise residual German labels, wire into 25/100% configs, one full 1% run with MATSim+fleet to validate all 13 tabs. | Merged & working; cosmetic + config gaps only. |
| 4.2 | **PopulationSim `num_workers` tuning** on the 64-core server; education sparse `cdist`. | Perf only; no OOM risk at 100%, deferred. |
| 4.3 | **Config cleanup** ([#81](https://github.com/TUBS-IVS/eqasim-bs/issues/81)): prune the ~28 root `config_*.yml` (24 tracked + 4 local) to the canonical set actually used at the end (target ~8), document each kept config's purpose. | Repo hygiene / reproducibility; keep-list driven by `RUNS.md`, no behaviour change. Tracking only (user: "damit ich dran denke"). |

### TIER 5 — Drop / do NOT re-attempt (recorded so we don't loop back)

These were tried or designed and **deliberately killed** — the model already does better, or measurement showed no gain. Listed so they aren't "rediscovered".

| Killed idea | Why dead |
|-------------|----------|
| **PopulationSim *importance/expansion* calibration framework** (spec 2026-06-24, 19 KB, **zero code**) | Designed but never built. Controls already validate well (7/9 very good/good); a coordinate-descent over importance weights risks **overfitting** the very survey noise we deliberately don't rake to. *Recommend: formally close unless a concrete control failure appears.* (Open question — see §3.) |
| **Commute gravity friction pinning** | Built (per-band friction on worktree) then measured: commute **already matches MiD P13** (EMD 0.0037 targets, <0.08 realised). The old "0.47 FAIL" was a **stale** pre-building-potentials number. Kept only as gated-off infra. |
| **Distance-dependent detour curve f(d) as default** | Built + measured immaterial vs constant 1.3 (EMD Δ ~0.003). Constant 1.3 stays default; curve is opt-in infra. |
| **Secondary scorer `pot_weight` tuning** | Sweep @100% showed `pot_weight` is a *concentration* knob — raising it makes the building-capacity fit **worse**. Default 1.0 is optimal. |
| **Raking employment to MiD P9** | P9 is survey noise (~900/Kreis, 43–59% spread, 4pp definitional diff). Employment stays raked to **GENESIS 13111**. Raking to P9 would overfit. |
| **Within-Kreis *extra* income signal** | No external sub-Kreis ground truth exists (RWI-GEO-GRID is FDZ-restricted); size/tenure/age controls already dominate. Rent tilt (+0.032 Pearson) kept; nothing beyond it. |
| **ATTACH strategy for building potentials** | Replaced by REPLACE (gpkg buildings as candidate set) after mid-session pivot. |
| **HTS-matching step 1 for aggregate purpose fit** | Improves *coherence* (non-employed-with-work 14%→0.5%) but aggregate SRMSE is donor-pool-bound → see 2.1, not step 1. |

---

## 2. Status of every design spec (`docs/superpowers/specs/`)

| Spec | Status | Open part |
|------|--------|-----------|
| per-RegioStaR-7 gravity slope (06-01) | ✅ IMPLEMENTED | — |
| cordon external-demand roadmap (06-02) | 🟡 PARTIAL | sub-projects 3–4 not started (Tier 3.4) |
| supply extension cordon ring (06-02) | ✅ IMPLEMENTED | — |
| in-commuter agents v1 / v1.1 (06-02) | ✅ IMPLEMENTED | extended analysis only on stale branch |
| education gravity (06-03) | ✅ IMPLEMENTED | — |
| incommuter mode reference (06-03) | ✅ IMPLEMENTED | — |
| age-aware household chunking (06-04) | ✅ IMPLEMENTED | — |
| cross-cordon external demand (06-05) | ✅ IMPLEMENTED | — |
| fleet KBA/MiD (06-07) | ✅ IMPLEMENTED | emissions wiring parked (Tier 3.5) |
| population validation (06-07) | ✅ IMPLEMENTED | — |
| Tier-A attribute reactivation (06-07) | ✅ IMPLEMENTED | — |
| ALKIS-typed home matching (06-17) | ✅ IMPLEMENTED (PR #14) | — |
| LoD2 height-volume capacity (06-17) | ✅ IMPLEMENTED | — (verified 2026-06-27; consumer side fully wired) |
| fleet consistency + income-age (06-18) | ✅ IMPLEMENTED (PR #12/#13) | — |
| weekend-plan match (06-18) | ✅ IMPLEMENTED | — |
| shared stage-cache (06-22) | ✅ IMPLEMENTED | — |
| Tier A+B caching (06-22) | 🟡 PARTIAL | config wiring (Tier 1.3) |
| auto-export shared cache (06-23) | ✅ IMPLEMENTED | — |
| integerizer-quality analysis (06-23) | ✅ IMPLEMENTED | — |
| **popsim importance calibration (06-24)** | ❌ **PLANNED-ONLY** | **entire framework — see Tier 5 / §3** |
| building-activity-potentials (06-25) | ✅ IMPLEMENTED (PR #16/#17) | — (real computed `potential_work` already wired into work gravity) |
| **calibration corner + distance-dist (06-25)** | 🟡 **PARTIAL** | lives on worktree branch (Tier 0.1) |

---

## 3. Open decisions for the user (genuine forks)

1. **Calibration-Corner worktree (Tier 0.1):** merge the whole 68-commit body as one PR, or
   cherry-pick only the *kept* parts (purpose-resolved secondary distances + leisure W12 fix
   + fit reports) and leave the measured-immaterial infra (friction, f(d)) gated-off?
2. **PopulationSim importance calibration (Tier 5):** formally close the 19 KB design, or
   keep it parked as a "if a control regresses" fallback? (Recommendation: close — it risks
   overfitting noise we deliberately don't rake to.)
3. **Highest next lever:** German MiD Wege donor (2.1, big, blocked on data) vs. 100% run +
   mode-choice calibration (1.1/1.2, ready)? (2.2/2.3 are done.) These are the candidates for
   "what we actually want next".
4. **The "stale" branches are all feature-superseded (see §5)** — safe to delete. Only your
   go-ahead is needed (local delete is free; deleting the `origin/` copies is a push).

---

## 4. Standing lessons that shape all of the above (do not violate)

- **Measure before calibrating.** The commute "0.47 FAIL" was stale; the model already
  matched P13. Always re-measure the realised KPI with the *same* methodology as the target
  and decompose targets→intermediate→realised before building a calibration lever.
- **No invented references; convergence ≠ validation.** A target is only real if traceable
  to a committed source; otherwise label it ASSUMPTION.
- **No silent fallbacks.** Log primary-vs-fallback rate; test the primary path.
- **Anti-overfitting.** Survey noise (P9, sub-Kreis income) is not a calibration target.
- **New flags default ON, OFF path byte-identical + tested. Never push without explicit OK.**
- **Parallel agents → separate worktrees** (we have had HEAD-race incidents).

---

## 5. Branch supersession evidence (deep check 2026-06-27)

The user re-did this work and forgot the prototype branches. Verified by feature/content
(not file path or commit hash). All three are safe to delete — the feature and all data
live in `main` under refactored names.

| Stale branch | Prototype artefact | Re-implemented in `main` as | Verdict |
|---|---|---|---|
| `feature/secondary-external-candidates` | `external_secondary_points.py`, secondary external candidates | **landed via PR #19** (`external_secondary_points.py` is in main); the only residual (leisure-correction fix in `secondary_chainsolvers.py`) is captured in `reconcile/calibration-remainder` `9662b12` | ✅ superseded |
| `feature/cordon-supply` | `data/spatial/supply_ring.py`, `gtfs_cleaned.py`, `osm_cleaned.py`, `download_mikrozensus_pendler.py` | `data/spatial/cordon.py` + `data/cordon/network.py`+`network_clip.py` + `data/gtfs/cleaned.py` + `matsim/scenario/supply/{gtfs,osm}.py` + `scripts/clip_osm_to_cordon_ring.py`; `download_mikrozensus_pendler.py` is in main verbatim | ✅ superseded |
| `feature/cordon-incommuters` | `synthesis/incommuters.py`, `population_merge.py`, `incommuter_subpopulation.py`, `run_incommuter_analysis.py`, `mikrozensus/reference.py`, 8 mikrozensus CSVs | the full `braunschweig/data/cordon/` package (18 modules) + `synthesis/incommuters.py` + `synthesis/incommuter_merge/` + `matsim/simulation/cordon_subpopulation.py` + `analysis/cordon_validation.py` + `data/mikrozensus/reference.py`; the P38_2/P38_4/W12 ZGB tables are in main under the canonical `eqasim-data/data/braunschweig/mid/` path | ✅ superseded |

Only non-superseded scraps are dev/test config YAMLs (`config_supply_cordon_*.yml`,
`config_cordon_incommuters_*.yml`) — not features, not worth keeping.
