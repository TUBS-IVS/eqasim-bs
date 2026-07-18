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

### Resolved (2026-07-17) — issue-backlog cleanup (cross-checked against merged code) + key-matching-audit PM record

Cross-checked every open issue against the actual code/commit state and closed those already covered
(the recurring "solution shipped, issue never closed" drift):

- **#130** (OECD consumption_units) — already on `main` (`6cdad97`): `income.add_consumption_units`
  (reuses upstream `data.hts.hts.calculate_consumption_units`) + `add_income_per_consumption_unit`,
  wired in `assembly.build_persons` + `stage.execute` on the FINAL income. `high_income` deliberately
  kept at the flat household threshold (no traceable per-CU reference); the primary income variable
  `economic_status` is already OECD-equivalised by MiD construction. **Closed.**
- **#76** (raw-data re-sync) — the raw drop was fully restored from felix on 2026-07-16 (24G,
  diff-verified). **Closed.**
- **#137** (extend Stage-B trip-donor matching keys) — **superseded**: production runs `popsim_mid`,
  where each synthetic person IS a specific MiD donor `(H_ID, P_ID)` whose real Wege become the trip
  chain (`braunschweig/popsim/trips.py`) — no attribute matching at all. On the legacy statistical-
  matching path the richer keys (`employed`, `household_size_class`, `socioprofessional_class`) already
  exist ("step 1", `tests/test_matching_keys.py`); car/pt/economic_status are deliberately excluded
  (placeholders at matching time = matching on invented data). **Closed.**
- **#124** — corrected: phase 1 (sub-Kreis OD reference) merged (PR #189/#190/#192); only phase 2
  (accessibility matrices) remains open.

Confirmed genuinely open (not re-attempt candidates for a quick win):

- **#108** (placement-based income geography): **L1 MERGED** (#109/PR #112, economic_status × Kreis).
  **L2 BUILT** (2026-07-18, branch `worktree-placement-income-l2` @ `6a02b6c`, PR pending, ADR-0069):
  `placement_income` — donor keeps its own MiD income, per-Kreis INKAR relativity approached by
  signature-preserving reallocation, redraw+tilt overridden. 2-Kreis gate: invariants Δ0, coherence
  income↔cars 0.174→0.364 (+0.19), attainment an honest trade (approaches INKAR, redraw hits it exactly;
  52% no-freedom). **Next: G1/G2** (LSN Z9170111 per-Gemeinde tax income) to measure the sub-Kreis payoff
  and gate **L3/#110** (sub-Kreis wealth surface) — build L3 only if G2 shows the composition does NOT
  already explain the Gemeinde income geography. B'/λ-re-estimation deferred.

Also in this PR:

- **Recorded the 2026-07-16 key-matching / fallback audit** (PR #191 + #194 MERGED; project-wide AGS/ARS +
  join/fallback sweep; join-coverage logging on every silent left-merge+fillna(0); all 14 standing test
  failures root-caused; full suite **2986 passed / 0 failed** under the `eqasim` env) + the raw-data-loss-
  and-restore incident, which `main`'s PM docs had not yet captured.
- **Honest-skip guard** for the INKAR income smoke test: the failure was an env issue (legacy BIFF `.xls`
  needs `xlrd`, absent under system Python 3.13), NOT missing data. `_income_xls_readable()` now guards on
  file existence AND `xlrd` importability, path anchored to `DATA_ROOT`. Conforms to
  `feedback-never-disable-tests-to-pass` (mark a non-runnable env honestly, never weaken the assertion).
- **SHIPPED (PR #196, commit bf8a2f1): latent FRAGILE hardening batch** from the audit — items
  verified NOT live bugs today but one input-drift away from silent failure: `inkar/full_panel.py` +
  `ba/pendler_detailed.py` lack a `\d{5}`-fullmatch key guard (`census/pendler.py` has one);
  `inspire/landuse.py` flag-ON-but-missing-file returns an empty frame with `validate()==0` instead of
  raising (stage currently unconsumed); `home_cell` legacy path renumbers building ids so
  `home_match_validation.compare_typed_vs_legacy` joins the WRONG buildings (analysis-only, legacy flag
  only); `network.py` link-skip counter. Shipped as PR #196 (`fix/audit-fragile-hardening`, `bf8a2f1`): shared `data/kreis_key_guard.keep_valid_kreis5` wired into inkar/pendler; landuse raises when flag ON + file missing; network counts+logs dangling-node link skips; home_match raises on zero-overlap join (legacy positional ids are test-protected -> guarded the analysis, not the model). TDD (RED verified for home_match); affected suite 65 passed / 9 skipped under the eqasim env; byte-identical on clean inputs.

### Resolved (2026-07-16) — #124 VerBindungen sub-Kreis OD reference (PR #189/#190) + #132 svb_wohn A/B (ADR-0066)

- **#124 phase 1 DONE, MERGED (PR #189 + server-config wiring PR #190).** Download + loaders
  (`data/verbindungen/`) + default-ON validation stage (`analysis/verbindungen_validation.py`,
  checks A margin / B conditional-OD / C vintage-drift) + A/B script. Baseline validation of the
  100pct all-features run vs the **real VerBindungen 2019 QZM** (RUNS `verbindungen-ab-2026-07-16`):
  check-B weighted TVD 0.137, home-margin Pearson r 0.9968, vintage 2019↔2025 Pearson r 0.9984
  (drift ≤ 0.0076). The sub-Kreis work-OD structure fits the reference reasonably and the vintage
  is stable.
- **#132 svb_wohn production mass: measured, PARKED default OFF (ADR-0066).** Paired OD-level A/B:
  weighted TVD 0.1136 → 0.1137 (negligible) — the Kreis-level Pendleratlas IPF anchor dominates.
  Flag stays available. Same pattern as #128/#129.
- **OPEN follow-ups (issue-first; propose before creating):**
  1. **Stage-3 calibration-anchor decision (proposed issue).** Both gate criteria are technically met
     (check-B gap real + not censoring-explained at 1.6%; vintage drift small), but there is **no
     committed threshold** for "substantial enough to calibrate" and TVD ~0.14 is already a reasonable
     gravity fit — so promoting VerBindungen from validation reference to a sub-Kreis calibration
     anchor is a **team judgment**, not auto-taken. If ever built, sub-Kreis OD becomes labelled *fit*,
     not validated. Deep-dive per-Kreis-pair table exists (`ab_out/realised_100pct/`).
  2. **#124 phase 2** (Erreichbarkeiten/Reisezeitverhältnis matrices as pair-specific impedance
     crosscheck) — deliberately deferred, honest expectation "probably small".
  3. Minor code follow-ups from the #189 final review (accepted, non-blocking): download-robustness
     already done; consider `SVB_FALLBACK_WARN_SHARE` as a config key; the not-yet-consumed breakdown
     table `SvBaGeB_Relationen_WO_AO_Verkehrszellen.csv` (~112 MB) is downloaded for later segment checks.

### Resolved (2026-07-16) — worktree cleanup rescue: NDS Kreis-key leading-zero fix (PR #187)

- The popsim-validation employed-rate extractors (`employed_25_64_rate`, `employed_by_age_group` in
  `popsim_validation/controls.py`) sliced the 5-digit Kreis prefix without `zfill(12)`: a numeric-typed
  ARS loses its leading zero, so the keys (`3101`) never match the zero-padded targets (`03101`) — every
  Lower-Saxony Kreis affected. Fixed + red-verified regression test on **PR #187 (MERGED 2026-07-16)**.
  Provenance: uncommitted 2026-07-12 validation-audit (#159) work found in the `fix-validation-audit`
  worktree during the 2026-07-16 cleanup; it had never reached PR #165/#166. All other uncommitted
  content in the removed worktrees was verified redundant (identical to main commits) or stale.

### Resolved (2026-07-15) — #128 sector-aware tilt: measured, PARKED (ADR-0065); 2 bugs fixed on PR #184 (MERGED 2026-07-16)

- **Decision:** `braunschweig.gravity.sector_aware_enabled` stays OFF permanently. Gravity-only pipeline A/B
  (felix `~/wt-128-ab`, ZGB-8, private cache, only the flag differing): commute-distance distribution unchanged
  (TV 0.003), per-Gemeinde work inflows vs OBSERVED SvB am Arbeitsort **9x worse** (mean within-Kreis TV
  0.009 -> 0.087; Gifhorn Stadt 20,299 observed -> 12,909 ON). By construction: the attraction vector IS the
  observed Gemeinde marginal. Valid rest of the idea = WZ-sectoral friction (issue #128 phase 2, deferred).
- **Bugs fixed (PR #184, closes #128):** (a) flag ON always crashed (`KeyError 'employees'`, tilt ran on the
  pre-rename stage schema; unit tests fed the post-rename schema — primary-path test gap); (b) `employees.py`
  padded 5-digit LK aggregate rows into fabricated AGS -> 26.9% false "lost" SvB -> **every full ZGB-8 run
  would have aborted** at the 25% raise threshold (kreis-subset runs stayed under it). Output byte-identical;
  only accounting/raise corrected. PR #184 MERGED 2026-07-16 (`0669229`). **Next:** remove felix worktree
  `~/wt-128-ab` + its A/B cache entries.
- **Spin-off:** #183 — `analysis_suite.py` had two-arg `context.config()` calls in the execute path (static
  contract test red on main, pre-existing). FIXED via PR #185 (MERGED 2026-07-16): execute path reads
  key-only; test stub made strict (key-only ExecuteContext stand-in + defaults resolved via the stage's
  real `configure()`).

### New (2026-07-15) — #129 per-Bundesland in-commuter mode (PR #180) + #156 MATSim output archive (PR #181), both MERGED

1. **#129 per-Bundesland in-commuter commute-mode reference -> PR #180 (MERGED `2490c37`, ADR-0063).**
   Wired the previously dead `build_mode_reference_by_bundesland` into `incommuters.execute`: each cross-cordon
   in-commuter now draws its fixed mode from a reference selected by origin Bundesland (ARS-prefix), national
   fallback logged. Default ON, OFF byte-identical. Real 25% impact **tiny (-0.13 pp PT)** — the big NDS/ST
   shift premise did NOT hold (measure, don't assert); primary 100% / fallback 0%.
2. **#156 archive MATSim `simulation_output/` -> PR #181 (MERGED `f83a81f`, ADR-0064).** `matsim.output` now
   mirrors `simulation_output/` into a stable `<output_path>/matsim_output/` (hardlink + copy-fallback, flag
   `archive_matsim_output` default ON, `ARCHIVE_INFO.json` provenance, fail-clean RuntimeError on empty/missing).
   Prevents the fragile-hash-cache loss that nearly destroyed the 100%/25% outputs on 2026-07-10.
   **Follow-up: server pytest (`eqasim` env — local blocked by matsim-tools shadowing) + e2e** to get the
   formal GREEN (behaviours verified via importlib; final opus review = Ready to merge).

### New (2026-07-13/14) — employment_status: Phase-0 measured (ADR-0058), then #167 fixed + in_ausbildung control built

Phase-0 measurement (ADR-0058, RUNS `empstatus-measure-2026-07-13`): the uncalibrated attribute fit the
independent MiD P9 well overall (SRMSE 0.194, grade "good"); only `in_ausbildung` deviated materially
(+1.7pp). Two threads shipped on 2026-07-13/14:

1. **`in_ausbildung` over-representation — ADDRESSED with an SrV+MiD per-Kreis control → PR #173 (MERGED, Closes #172, ADR-0060).**
   RCA confirmed a real ~1.9× inflation (synthetic 3.6% vs regional truth ~1.9%, MiD P9 AND SrV V_ERW=8
   agree), 2-stage compositional (member completion + balancer), NOT age/reference. Built a per-Kreis
   soft control raked to a blended MiD-P9 + SrV-V_ERW target, 14+ universe on both halves, seed derived in
   both paths, flag default-on. Smoke: rakes in_ausbildung 2.98%→2.09% (target 2.01%). This SUPERSEDES the
   earlier "Phase-1 dropped" note for the in_ausbildung class specifically (the rest of the taxonomy fit
   fine, so only this class is controlled). PR #173 MERGED. **Follow-up: factor a reusable
   1km-cell control smoke (planned in the spec, not yet done).**
2. **#167 SPC_BY_P_BKAT misread — FIXED → PR #171 (MERGED).** Dropped the invalid occupation crosswalk;
   `socioprofessional_class` now always from broad activity (no occupation var exists in MiD). Correction:
   NOT dormant — SPC is an active `trips.py` Stage-B chain-matching key, so the fix changes trip-chain
   outputs for the replaced subset. PR #171 MERGED.
3. **Re-confirm on the next full "everything on main" run.** Both the employment_status control effect and
   the SPC fix land canonically there; `analysis_suite` population_validation re-measures in_ausbildung
   (now labeled `partially_independent`, since it is a steering control — ADR-0060).
4. **Trivial:** orphaned synpp cache entries from the Phase-0 measurement remain in the shared cache;
   harmless. PT-Abo (SrV `V_OEV_FK`) and migration (`V_MIGR`) considered and NOT pursued (see ADR-0060).

### New (2026-07-13) — Large-HH (6+) validation gap: donor-bound, levers narrowed (ADR-0059)

The `household_size` 6+ class still under-fits on the newest kreis5 100% run (2.92% vs ref 4.75% =
61.5% of reference); the 5-person gap is now essentially closed. Two levers ruled out this session
(see ADR-0059, memory `project-large-hh-6plus-donor-bound`):

1. **Importance is EXHAUSTED — do not raise `six` further.** The run already weights 6+ at importance
   2000 with `max_expansion_factor: 100` and still hits only 61.5%; the gap is donor-bound, not
   weight-fixable.
2. **SrV rejected as a donor supplement.** SrV Braunschweig+RGB has only 63 six-plus HH (0.78%) — the
   same rarity as MiD (1,661 / 0.76%) — plus circularity (SrV is already our per-Kreis TARGET source,
   ADR-0055) and schema/weight-harmonization cost. Not worth it.
3. **Candidate lever (UNVERIFIED, verify-first before any issue):** control 6+ at a coarser geography
   (1km / Kreis) so its integer targets survive 100m integerization rounding. Must first verify whether
   PopulationSim integerizes at 100m regardless of control geography — if so the fix is blunted. Under
   #99 (regional-correct popsim); no issue opened yet.

### New (2026-07-12) — Full-pipeline bug-audit wave -> PR #165 (MERGED), issues #160-#163

An orchestrated read-only audit of the whole synthesis pipeline (vs `origin/main` `d92328e`) surfaced
**19 verified bugs**, all fixed the same day on `fix/audit-wave-20260712` -> **PR #165 (MERGED)**. Open
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

**RESOLVED 2026-07-14 — the popsim audit-follow-up issues #147/#148/#149/#150/#163:**
- **#163** (14 fallback-transparency items) — found ALREADY implemented+merged via PR #165; verify-closed
  (item-by-item file:line -> commit, tests green). No code.
- **#147 + #149 + #150 -> PR #175 (MERGED):** #150 helper `cells.sum_columns_logging_nan` wired into all 4
  multi-column row-sum sites; #149 raise-on-all-missing; #147 sub-2 kreis_table restricted to run's Kreise
  (no output change) + sub-1 `_kac_kreis` aligned to the RESOLVED dominant Kreis (**output change ~0.1%
  border cells**, ADR-0061).
- **#148 -> PR #176 (OPEN):** household-level KREIS controls apportioned by HOUSEHOLD share not population
  share; measure-first (100% run) found ~5.9% economic_status mis-apportioned within-Kreis, MATERIAL
  (**output change**, ADR-0062). Merges cleanly after resolving the folders.py conflict with PR #175.
- **OPEN follow-up (not a separate issue by choice; tracked here + memory):** both #147-sub1 and #148 change
  the WITHIN-Kreis spatial apportionment; region-wide sums provably unchanged, but the REALIZED synthetic
  effect needs a small resolved-Kreis + hh-share A/B rerun of one multi-batch Kreis on felix before trusting.
- The **one-time batch purge** (#163 signature fix, item 2 above) still applies on the next persistent-`work_dir`
  server run. Memory `project-popsim-controls-audit-fix`.

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
2. **Issue #153 — DONE: PR #155 MERGED.** `cleanup_batch_pipeline` flag (default ON,
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
    fix); flag-ON 1% e2e green. Phase 3 friction re-fit BUILT then measured unnecessary; branch
    `feature/taz-gravity-calibration` @ `3c2ebb5` PARKED as gated-off backup (ADR-0050).
  - **DECIDED / CLOSED 2026-07-16 (ADR-0067): TAZ stays permanently OFF — feature not pursued further.**
    Rationale: the building-level activity potentials (PR #16, ON in production) already resolve the
    within-commune work location TAZ targeted; the flag-OFF model already fits MiD P13 (EMD ~0.054); the
    RVB VISUM data is proprietary / non-publishable. The outstanding same-commit 25%/100% A/B was
    deliberately NOT run — the decision is made. TAZ code stays on `main` (OFF byte-identical, zero
    runtime cost), reactivatable only if a future measurement shows a real intra-city gap. Issues
    **#79 (completed), #80, #83, #95 (not planned) CLOSED**; parked friction branch kept as backup only.
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
| **Sector-aware work-attraction tilt** (#128, ADR-0065) | Measured 2026-07-15 (offline + pipeline A/B): distance distribution unchanged, per-Gemeinde inflow fit vs OBSERVED SvB **9x worse** — the attraction IS the observed marginal, the tilt can only distort it. Within-Gemeinde structure already covered by building potentials; commute-SHAPE idea survives only as deferred WZ-sectoral friction (phase 2). Code stays as gated-off, now-runnable infra. |
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
