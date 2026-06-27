# Project Backlog & Open-Work Map — eqasim-bs

> **Purpose.** A ranked, honest inventory of everything we *planned* but only *partially*
> implemented, deliberately parked, or designed-and-forgot — cross-checked against what the
> code already does (so superseded ideas are marked dead, not re-attempted).
>
> Created: 2026-06-27. Companion to `PROJECT_HANDOVER.md` (2026-06-22, narrative) and
> `CLAUDE.md` (binding instructions + feature detail). Where they disagree, `CLAUDE.md` and
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
5. The highest-*value* remaining model lever everyone keeps naming is the **German MiD Wege
   trip donor** (replacing the French ENTD-2008 donor) — but it is blocked on MiD microdata
   and is a large effort.

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

→ **Next (step 1b kickoff): MEASURE before tuning** — realised expansion-factor distribution
+ household-total exactness on a cached run — then decide whether importance/expansion tuning
is warranted (measure-before-calibrating).

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
| 2.1 | **German MiD Wege trip donor** (replace French ENTD-2008 trip donor + re-estimate mode choice). | DEFERRED; named repeatedly as *the* biggest realism lever. Aggregate trip-purpose fit is **donor-pool-bound** — step-1 matching can't fix it, only a German donor can. | **Highest value, highest cost.** Blocked on MiD microdata (SUF), not just margins. Worth scoping the data-access path now even if build is later. | L |
| ~~2.2~~ | ~~LoD2 height → volume-weighted dwelling capacity & typing~~ | ✅ **DONE (verified 2026-06-27).** `data/lod2_heights.py` (OI→height), non-destructive `join_lod2_heights` by `OI` in `data/buildings.py` with coverage logging, and `building_typing.py` uses `building_volume(area, height)` end-to-end (volume-rank MFH typing, `MFH_MIN_FLOORS=4` tuned on a Salzgitter real-pop sweep, volume-weighted `build_slots`). ALKIS `OI` passthrough tested (`test_preprocess_alkis_oi.py`). | The earlier "PARTIAL" verdict was from a stale tree. | — |
| ~~2.3~~ | ~~Real building worker dataset vs area*floors proxy~~ | ✅ **DONE (verified 2026-06-27).** `locations/work.py` with `work_building_potentials=True` (default) **REPLACES** the candidate set with the real computed `potential_work` from the building-activity-potentials parquet — `area*floors` is only the OFF/legacy path. Per-commune `potential_work` sum is printed as a Census-SvB cross-check. The "area*floors proxy pending" memory note is obsolete. | — | — |
| 2.4 | **Education distance-distribution calibration (×3 levels)** — the second half of the gravity distance-distribution plan (Phase 2, reuses the Tier-3 shared layer). | PLANNED-ONLY. The *commute* half was built (per-band friction on the worktree branch) and then **found unnecessary** (commute already matches MiD P13). | Reuse the now-built machinery. Only do it if education trip-length validation actually shows a gap — **measure first** (see Lessons). | M |

### TIER 3 — Deferred-deliberately / future waves (parked with intent, not forgotten)

| # | Item | Note |
|---|------|------|
| 3.1 | **Kreis-level income control** (income as a PopulationSim control via INKAR Kreis targets, instead of post-hoc scaling). | Feasible-but-nontrivial; deferred. Within-Kreis *extra* signal already dropped (existing controls dominate). |
| 3.2 | **BASt Dauerzählstellen HGV-count calibration** for the injected freight. | Future external-validation wave; freight currently taken as-is from german-wide-freight v3. |
| 3.3 | **Real VRB/DELFI GTFS + VRB PT tariff (B2)** + MATSim termination/iteration tuning. | Supply-side + behaviour wave; uncalibrated cordon gate-gravity beta/capacity_exponent also lives here. |
| 3.4 | **Cordon sub-projects 3 & 4** (external *visitors*, non-freight *through-traffic*). | Never started; explicitly out of scope in the original roadmap. Through-freight is already covered by the freight module. |
| 3.5 | **HSN/TSN → engine power / Fahrleistung / CO2 (HBEFA) wiring**; economic_status × #earners margin. | Fleet attributes are present but not yet *consumed* for emissions; user parked this ("income/socio saturated"). |

### TIER 4 — Polish / nice-to-have

| # | Item | Note |
|---|------|------|
| 4.1 | **SimWrapper polish**: verify choropleth colours render by value, anglicise residual German labels, wire into 25/100% configs, one full 1% run with MATSim+fleet to validate all 13 tabs. | Merged & working; cosmetic + config gaps only. |
| 4.2 | **PopulationSim `num_workers` tuning** on the 64-core server; education sparse `cdist`. | Perf only; no OOM risk at 100%, deferred. |

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
