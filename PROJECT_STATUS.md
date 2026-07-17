# PROJECT STATUS — eqasim-bs (at-a-glance dashboard)

> **What this is.** The single quick-orientation dashboard: *what have we built, what state is
> it in, where does it live, what's open.* For the deep open-work backlog see
> [PROJECT_BACKLOG.md](PROJECT_BACKLOG.md); for binding rules + feature detail see `CLAUDE.md`;
> for architecture/onboarding see [docs/codebase/](docs/codebase/).
>
> **Last updated:** 2026-07-17 (older bullets below may lag `main`).
> **2026-07-17 — session housekeeping + key-matching-audit PM record:** cross-checked the open issues
> against the actual merged-code state and cleaned the backlog. Closed **#130** (OECD consumption_units
> already on `main`, `6cdad97`), **#76** (raw-data drop restored 2026-07-16), **#137** (trip-donor
> matching keys already covered — production `popsim_mid` uses direct MiD-donor coherence, and the legacy
> statistical matcher already carries the richer keys); corrected **#124** (phase 1 merged, only phase 2
> open). Confirmed **#108** genuinely open but measured low expected impact (income inert). Recorded the
> 2026-07-16 key-matching / fallback audit (**PR #191 + #194 MERGED**, full suite 2986/0) and the
> raw-data-loss-and-restore incident, which `main`'s PM docs had not yet captured. Added an honest-skip
> guard for the INKAR income smoke test (xlrd/env root cause, not data loss).
> **2026-07-15 — two features MERGED:** **#129** per-Bundesland in-commuter commute-mode reference (PR #180,
> ADR-0063; default-ON, OFF byte-identical; real 25% impact -0.13 pp PT — premise did not hold) and **#156**
> MATSim `simulation_output/` archive to a stable `<output_path>/matsim_output/` (PR #181, ADR-0064; hardlink +
> copy-fallback, flag `archive_matsim_output` default-ON, `ARCHIVE_INFO.json` provenance, fail-clean). #156
> follow-up: server pytest (`eqasim` env; local blocked by matsim-tools shadowing) + e2e for the formal GREEN.
> **2026-07-14 — popsim KREIS-control apportionment + fallback-transparency wave:** four audit-follow-up
> issues, verify-first each. **#163** (14 fallback-transparency items) was found ALREADY done+merged via
> PR #165 -> verify-closed, no code. **#147 + #149 + #150 -> PR #175 (MERGED):** #150 helper
> `cells.sum_columns_logging_nan` wired into all 4 multi-column row-sum sites; #149 raise-on-all-missing;
> #147 sub-2 kreis_table restricted to run's Kreise (no output change) + sub-1 `_kac_kreis` aligned to the
> RESOLVED dominant Kreis (`mid.resolved_kreis_per_cell`, **output change ~0.1% border cells**, ADR-0061).
> **#148 -> PR #176 (OPEN):** household-level KREIS controls apportioned by HOUSEHOLD share not population
> share (measure-first found ~5.9% economic_status mis-apportioned within-Kreis; **output change**, ADR-0062).
> **Caveat:** both #147-sub1 and #148 change the within-Kreis spatial apportionment (region-wide sums provably
> unchanged); the realized synthetic effect needs a small A/B rerun of one multi-batch Kreis on felix before
> trusting. Memory `project-popsim-controls-audit-fix`.
> **2026-07-12 — Full-pipeline bug audit -> PR #165 (MERGED):** orchestrated read-only multi-agent audit vs
> `origin/main` `d92328e` found **19 verified bugs** (1 critical, 11 major, 7 minor). Fixed same day on
> `fix/audit-wave-20260712` (6 commits `f212d73`..`1a4a874`, 51 new tests, suite = baseline 11 known-fail /
> 2780 pass). **#160** (crit) distance_distributions silently dropped ~11% MiD coded-time Wege (99/701) ->
> now rescued from `wegmin_imp1` with observed/imputed/dropped rate log. **#161** powertrain Gemeinde tilt
> joined only 30.6% (umlaut/suffix mismatch, live kreis5 log) -> shared `normalize_gemeinde_name`. **#162**
> weekend_plan_match wrong employment set -> canonical `EMPLOYED_TAET`. **#163** 14 fallback-transparency
> items instrumented. PR Closes #160/#161/#162, works #163. **Caveats:** batch-signature fix purges popsim
> `work_dir` batches ONCE (don't deploy mid-run); kreis5 ran fleet/distance stages on old code; pre-merge =
> felix pytest. Memory `project-audit-wave-2026-07-12`.
> **2026-07-10 — kreis5 100% run RELAUNCHED with full-pool perf fix (ADR-0056, ~40x measured):** root
> cause of the ~8-day projection = upstream default `SUB_BALANCE_WITH_FLOAT_SEED_WEIGHTS: true` (float
> parent weights strictly >0 everywhere → every 1km cell balanced all 53,459 signature rows x 1000
> iterations) + unused `USE_NUMBA` (2.4x, identical to 1e-13). A/B on a batch_000 copy: **32.6 min vs
> ~22 h**; production relaunched 14:11 from `b6ba420` with `settings_tier3_mef100_intseed_numba.yaml`,
> ~28 min/batch confirmed, popsim phase ETA same evening. **PENDING: quality A/B vs float reference
> batch** (running niced on felix, ~2026-07-11) — until clean, the speedup is operational, not
> scientifically validated. Disk side-find: per-batch `pipeline.h5` ~15 GB dead weight at full pool →
> interim watcher on felix + **PR #155 MERGED (Closes #153)**: `cleanup_batch_pipeline` flag default ON
> in batch.py/stage.py (TDD, 9 tests; delete after VERIFIED completion incl. skipped leftovers, failed
> batches keep the h5 for resume; OSError-hardened; flag explicit in both server configs). Pre-merge:
> canonical popsim pytest on felix after the run ends. **A/B harness READY + self-tested** on felix
> (`~/ab_quality/run_ab.sh`; batch_000-vs-itself: 44/44 controls resolved, all diffs exactly 0;
> float-bench inputs verified byte-identical to batch_000).
> Two verified upstream populationsim v0.10.0 bugs (MIN_GAMMA clamp missing, `converged=True` on
> no-progress) — bypassed by numba; upstream reports optional. Memory `project-popsim-fullpool-perf-fix`.
> **2026-07-14 — employment_status follow-ons (2 PRs MERGED):** (1) **PR #173** (Closes #172, ADR-0060) —
> SrV+MiD per-Kreis `employment_status` control correcting the ~1.9× `in_ausbildung` over-rep; new SrV
> `V_ERW` per-Kreis table + blended `target2026_employment_status_by_kreis.csv`; 14+ universe on both
> halves; flag default-on/OFF byte-identical; 1-Kreis smoke rakes in_ausbildung 2.98%→2.09% (target 2.01%);
> 5 tasks + per-task reviews + opus whole-branch review. (2) **PR #171** (#167) — dropped the invalid
> SPC_BY_P_BKAT crosswalk; socioprofessional_class now from broad activity (it IS consumed — trips Stage-B).
> Both MERGED (2026-07-13/14); canonical re-measure on the next full-main run.
> **2026-07-13 — employment_status Phase-0 MEASURED (ADR-0058):** the shipped `employment_status`
> attribute (PR #168) matches the independent MiD 2023 P9 reference well WITHOUT calibration — SRMSE
> 0.194, mean|Δ| 1.88pp, grade "good", all 56 Kreis×class cells <10pp, r² 0.979 (8 ZGB Kreise, 1.12M
> persons, age 14+). Measured cheaply by reusing the kreis5 balancing (no re-balance) and computing
> directly off the cached `popsim.stage` frame with the real validation code. **Decision: Phase-1 soft
> control NOT built** (fit already good; measure-before-calibrating). Only notable deltas: `in_ausbildung`
> +1.7pp, `vollzeit` +1.6pp. Open: re-confirm on the full-main run (post-#170/zfill); #167 (SPC) still
> open/dormant. See PROJECT_BACKLOG "New (2026-07-13)".
> **2026-07-04 status:** `origin/main` = `141284e` (PRs #101 + #102 + #103 merged) · open PRs: **#106** (#105 docstring), **#107** (#104 deck refresh).
> **2026-07-04 — income-placement design (issues #108 hub / #109 L1-L2 / #110 L3, ADR-0054):** make the popsim_mid income geography emerge from WHICH real MiD donor households are placed where (economic_status × Kreis control on MiD H4, retire the post-hoc `income_kreis_control` overwrite), not post-hoc scaling. Measured: income number tracks INKAR (ρ=1.0) but inert; status flat (CV 0.033); cars = tenure/size not income; trip-purpose/mobility "gaps" are metric artifacts. **Phase 0/1 built** (H4 extraction+CSV, loader, Phase-0 gate diagnostic; 11 tests; final review fixed 1 Critical + 3 Important) on backup branch **`worktree-income-placement-refdata-gate` @ `2d8e8aa`** (pushed to fork, NO PR/merge). Backlog 3.1 → #108. **Next:** run the Phase-0 gate on the server (needs MiD seed) → decide #109-build vs just-drop-overwrite.
> **Issue #97 FIXED** (PR #103, merged): the population-validation `household_size` control compared a
> HOUSEHOLD-based synthetic count against the PERSON-based Zensus 1000A-2081 target. Fix = a
> `weight_column` option on `bucket_household_control` making the realized side person-weighted. A
> felix validation re-run on the 100% output moves household_size from **7.7pp/"needs improvement" to
> 1.44pp/"good"** (classes 1-4 <1.2pp; residual = 5/6+ donor tail); all other controls byte-identical.
> Follow-ups: **#105** (correct the `households_type` "NOT consumed by IPF" docstring, PR #106) and
> **#104** (refresh the status-deck QA figures + rebuild, PR #107). With #96 + #97 both fixed, the
> **Phase-0 blockers of #99 (regional-correct popsim) are cleared**.
> **Issue #96 FIXED** (PR #101, merged): the synthetic `employed` flag was inflated for minors
> (14-17yo ~96%, region +7-9pp) by a field-width missing-code collision in `missing.resolve`
> (substantive `P_TAET=9` Schueler treated as generic keine-Angabe and imputed). The popsim Tier-3
> employment control was already correct (raw `P_TAET.isin`); only the written attribute +
> population-validation were affected. **#25 closed** (stale erwerb test, fixed independently). A
> **minor-employment plausibility guard** (PR #102, merged; default WARN) now watches the under-15
> employed rate. **Next:** 100% re-run with the fix on main, then flip the guard to `raise=True`
> (measure-before-harden; Phase-0 for #99).
> **TAZ sub-zonal work location choice** (eqasim IRIS-analog): **Phase 1+2 MERGED to main** (PR #85 merge `f5f52d1` + PR #89 FutureWarning fix), flag `taz_work_location_choice` default OFF byte-identical. **DECIDED 2026-07-16: TAZ stays permanently OFF** (ADR-0067) — the building-level activity potentials (PR #16, ON in production) already resolve the within-commune work location, the flag-OFF model already fits MiD P13 (EMD ~0.054), and the VISUM data is proprietary/non-publishable. Code kept on `main` (OFF, zero cost), reactivatable. Issues **#79/#80/#83/#95 CLOSED**; parked friction branch `feature/taz-gravity-calibration` @ `3c2ebb5` remains as backup only.
> Open issues: **#81** (config cleanup), **#78** (secondary scorer calib), **#76** (data re-sync), **#86/#91** (analysis-suite), **#22/#23/#26/#25** (production run / mode-choice / 25% gate / test). Unlanded local work: distance-fit module + gravity-calib popsim_mid fix on `worktree-fix+gravity-calib-popsim-mid` (committed, not pushed). See ADR-0049, ADR-0050, ADR-0067.
> Tracking: [GitHub Project board #3](https://github.com/orgs/TUBS-IVS/projects/3) (mirror of backlog + ADRs).
> Keep this current with `/close` at the end of each work session.

---

## 1. The pipeline in one breath

Scientific MATSim/eqasim transport simulation for the **Zweckverband Großraum Braunschweig (ZGB)**,
8 Kreise. Python **synpp** pipeline synthesises a population → assigns activities/locations →
exports a MATSim scenario → Java **MATSim** runs mode choice + traffic → analysis validates against
real reference data (MiD 2023, Zensus 2022, KBA, INKAR, BA Pendleratlas, Destatis).

```
config_*.yml → scripts/run_synpp.py → synthesis.output → matsim.output → RunSimulation (Java) → analysis/ + simwrapper/
```

Run: `python scripts/run_synpp.py <config>.yml` (local) · `bash scripts/run_pipeline.sh <config>.yml` (server).

---

## 2. Feature matrix — everything built, with status

**Legend.** ✅ merged on `main` · 🟢 flag-gated, ON in real-data run configs · ⚪ flag-gated,
default-OFF/byte-identical · 🟡 merged-as-infra but deliberately NOT activated (measured unwarranted) ·
🔵 open PR · 🌿 branch-only. Code default for nearly all model flags is OFF/None (byte-identical legacy);
"ON in run configs" = turned on in the committed real-data configs (`config_server_*100pct`,
`*_25pct_allfeat`, `*_allfeat_popsim`, `config_freight_validate`).

### 2.1 Population synthesis
| Feature | Flag (default) | Where | Status | Ref |
|---|---|---|---|---|
| IPF synthesis (legacy default) | `population.method: simple_ipf_open` | `braunschweig/ipf/` | ✅ | Zensus 2022, GENESIS |
| popsim_open / **popsim_mid** | `population.method` | `braunschweig/popsim/` | ✅ alt paths (popsim_mid ON in allfeat_popsim) | Zensus + MiD 2023 |
| Household-size margin | `ipf.use_household_size_margin` | `braunschweig/ipf/` | 🟢 | Zensus 1000A-2081 |
| Joint age×size margin (#3) | `ipf.use_joint_age_size_margin` | `data/census/households_size_age.py` | 🟢 | Zensus 1000A-3082 |
| Age-aware composition (#3b) | `ipf.age_aware_chunking` + `chunking.*` | `ipf/attributed.py` | 🟢 | Destatis 2024 (mother age 31.8) |
| Sex-aware couples (~1.1%) | `chunking.sex_aware_couples` | `ipf/attributed.py` | 🟢 | Destatis MZ 2025 |
| Cell-accurate homes (100m) | popsim home alias | `synthesis/locations/home_cell.py` | ✅ (popsim) | Zensus 100m grid |
| ALKIS-typed home matching | data-driven | `synthesis/locations/building_typing.py` | ✅ | Zensus building-type |
| **LoD2 height/volume typing** | data-driven (`MFH_MIN_FLOORS=4`) | `data/lod2_heights.py` + `building_typing.py` | ✅ (verified 2026-06-27) | LoD2 3D-Shape sweep |
| Income spatial tilt (Nettokaltmiete) | `popsim.income_spatial_tilt` | `braunschweig/popsim/` | ✅ (popsim) | INKAR/Zensus rent |

### 2.2 Attribute enrichment
| Feature | Flag (default) | Where | Status | Ref |
|---|---|---|---|---|
| Economic status (Bayes hhtype×region) | `status_from_hhtype` (code **true**) | `data/mid/status_by_hhtype.py` | 🟢 | MiD status×hhtype×region |
| Household income € + distribution | `income_eur_from_distribution` | `data/mid/income_by_size.py` | 🟢 | MiD H4/brackets, INKAR |
| Kreis income control (popsim) | `popsim.income_kreis_control` | `popsim/income_kreis_control.py` | ✅ (popsim) | MiD, INKAR |
| PT subscription (P24.1, 3-margin IPF) | `pt_subscription_conditioned` | `synthesis/population/enriched.py` | 🟢 | MiD P24.1 |
| Driving licence (P17.1, 3-margin IPF) | always-on enrichment | `synthesis/population/enriched.py` | ✅ | MiD P17.1 |
| Consistent car_availability | `consistent_car_availability` | `synthesis/population/enriched.py` | 🟢 | MiD P19/P17.1/H7 |
| Income-aware #cars | `cars_income_aware` | `data/mid/cars_by_status.py` | 🟢 | MiD H7 |
| Employment margin (IPF) | `ipf.use_employment_margin` | `data/census/employment*.py` | ✅ | GENESIS SvB |
| Tier-3 Kreis controls | `popsim.control_tiers: …tier3` | `popsim/control_spec.py` | ✅ (popsim) | Zensus + GENESIS |
| Housing tenure (completeness) | `synthesise_housing_tenure` | `data/mid/tenure_by_income.py` | 🟢 | MiD income×Wohnen |
| Reactivated attrs (couple/studies/SPC) | `reactivate_person_attributes` | `data/education/student_share.py` | 🟢 | Destatis education |

### 2.3 Vehicle fleet
| Feature | Flag (default) | Where | Status | Ref |
|---|---|---|---|---|
| Household fleet (vs default car) | `vehicles_method: household` | `synthesis/vehicles/cars/household.py` | 🟢 | MiD H7, KBA |
| German fleet segment+brand mix | `fleet_model_enabled` / `_brands` | `synthesis/vehicles/fleet_sampling_de.py` | 🟢 | KBA FZ |
| BEV/electric calibration | `fleet_electric_calibration` | `synthesis/vehicles/fleet_sampling_de.py` | 🟢 | KBA FZ 27.15/27.17 |
| HSN/TSN engine attrs (kW/ccm/fuel) | `fleet_hsn_tsn_attributes` | `synthesis/vehicles/hbefa.py` | 🟢 | KBA HSN/TSN scraper |
| Fleet consistency v2 + income-age | (folded into household fleet) | `synthesis/vehicles/` | ✅ (PR #12/#13) | KBA/MiD |
| Fleet realism upgrade (EV-income tilt, all-Kreise 46251, Euro-6 substage, RS7 cross-check, no-NA) | `fleet_ev_income_tilt` / `fleet_euro6_substage` (default-on) | `synthesis/vehicles/fleet_sampling_de.py`, `data/kba/fleet_tables.py` | 🟡 pushed `feature/fleet-quality-and-data`, final opus review done; **server-verify + merge pending** | KBA 46251-02/03, FZ 27.4, MiD A_ANTRIEB |
| Carless routing re-mode | `remode_carless_car_legs` | `matsim/simulation/prepare.py` | 🟢 | routing consistency |

### 2.4 Location choice / gravity
| Feature | Flag (default) | Where | Status | Ref |
|---|---|---|---|---|
| Gravity OD (work/edu) | `gravity_slope -0.065` | `gravity/model.py` | ✅ | BA Pendleratlas |
| Per-RS7 gravity slope | `gravity_slope_by_regiostar7` | `gravity/model.py`, `data/bbsr/regiostar.py` | 🟢 | BA Pendler Poisson GLM |
| Education gravity (schools/Kita/uni) | `education_gravity_enabled` | `synthesis/locations/education_gravity*.py` | 🟢 (allfeat) | MiD T43, Destatis MZ 2024 |
| Building potentials — work | `work_building_potentials` (code true) | `locations/work.py` (REPLACE = real `potential_work`) | 🟢 | GENESIS SvB aggregate |
| Building potentials — secondary | `secondary_building_potentials` + scorer | `synthesis/locations/secondary*.py` | 🟢 | MiD W12 |
| Building potentials — education | `education_building_distribution` | `synthesis/locations/education_gravity.py` | 🟢 | within-facility |
| Calibration: purpose-resolved secondary | `secondary_distance_by_purpose` / `_shop_daily_split` | `popsim/distance_distributions.py` | 🟢 (allfeat_popsim) | MiD W12 per-purpose |
| Calibration: per-band commute friction | `gravity_friction_factors` (None) | `gravity/friction.py`, `calibration/commute.py` | 🟡 infra, **not activated** (model already <0.08 EMD) | MiD P13 |
| Sector-aware attraction tilt (#128) | `braunschweig.gravity.sector_aware_enabled` (False) | `gravity/model.py` (`build_destination_attraction`) | ⏸ **PARKED** (A/B 2026-07-15: distance unchanged, Gemeinde-inflow fit 9x worse vs observed SvB — ADR-0065; ON-path crash + LK-aggregate loss bug fixed, PR #184) | GENESIS 13111-01-03-5 (SvB Arbeitsort) |
| VerBindungen sub-Kreis OD validation (#124) | run-list stage (default-ON) | `data/verbindungen/*`, `analysis/verbindungen_validation.py` | ✅ **MERGED** PR #189/#190; 100pct baseline: check-B weighted TVD 0.137, home-margin r 0.9968, vintage r 0.9984 (ADR-0066) | **VerBindungen 2019 QZM (open data)** |
| svb_wohn work production mass (#132) | `braunschweig.gravity.work_production_mass` (`population`) | `gravity/production_mass.py`, `gravity/model.py` | ⏸ **PARKED default OFF** (A/B 2026-07-16: weighted TVD 0.1136→0.1137, negligible — Kreis-IPF anchor dominates; ADR-0066) | VerBindungen 2019 QZM |
| Calibration: Tier-3 detour/circuity curve | `mode="curve"` (default constant 1.3) | `calibration/circuity.py` | 🟡 opt-in infra (measured immaterial) | OSM graph, Giacomin&Levinson 2015 |

### 2.5 Cordon / cross-border (Einpendler)
| Feature | Flag (default) | Where | Status | Ref |
|---|---|---|---|---|
| Cordon network ring + cut | `cordon_enabled` | `data/cordon/network_clip.py`, `cordon_network.py` | 🟢 | VG250 polygon |
| Einpendler injection | `cordon_enabled` | `synthesis/incommuters.py`, `incommuter_merge/`, `data/cordon/` | 🟢 | BA Pendler, Mikrozensus |
| Gates (road + PT/Bahnhof) | (part of cordon) | `data/cordon/{gates,gate_entry,pt_reachability}.py` | 🟢 | OSM, GTFS |
| Mode balancer | (part of cordon) | `data/cordon/mode_balancer.py` | 🟢 | Mikrozensus modes |

### 2.6 Freight
| Feature | Flag (default) | Where | Status | Ref |
|---|---|---|---|---|
| Long-haul freight injection (v3) | `freight_enabled` (code **true**) | `freight/{extraction,trips}.py` + Java | 🟢 (100%/freight configs) | Lu et al. 2022 — **NOT** BASt-calibrated |
| Freight analysis exclusion | auto | `analysis/freight_filter.py` | ✅ | — |
| Assumptions | `freight_truck_pce 3.5`, `_max_velocity_kmh 80` | config | ⚠️ ASSUMPTIONS | StVO / uncalibrated |

### 2.7 Analysis / dashboards
| Feature | Where | Status |
|---|---|---|
| MiD validation report | `analysis/run_mid_validation.py` | ✅ (vs MiD P9/P12_1/P13/P17_1) |
| Full analysis (dashboard+MiD) | `analysis/run_full_analysis.py` | ✅ |
| Population validation (controls/quality/geo) | `analysis/population_validation/` | ✅ (vs Zensus) |
| Integerizer quality (per-cell error map) | `analysis/integerizer_quality/` | ✅ |
| SimWrapper export (8 chart + 4 map + commuter tabs) | `analysis/simwrapper/` | ✅ |
| SimWrapper Layer-1 (MATSim Java contrib) | Java `RunSimulation --simwrapper` | ⚪ `simwrapper_dashboards: false` |
| Education enrollment validation | `analysis/run_education_validation.py` | ✅ (vs LSN capacity) |

### 2.8 Infrastructure
| Feature | Flag (default) | Where | Status |
|---|---|---|---|
| Shared stage-cache (prime-on-launch) | `cache_share_enabled` (true) | `cache_share.py`, `scripts/run_synpp.py` | ✅ |
| Tier-A/B caching (32 stages + popsim) | fixed `popsim.work_dir` | `popsim/{stage,completed_donor}.py` | ✅ (config wiring partial — see backlog 1.3) |
| Own eqasim-java-bs fork | `eqasim_source_path` | `../eqasim-java-bs` | ✅ |
| Urban parking (BS inner ring) | `enable_urban_parking` | `matsim/simulation/prepare.py` + Java | 🟢 |
| Parallel chainsolvers | `chainsolvers.parallel` / `.processes` | `synthesis/locations/secondary_chainsolvers` | 🟢 |
| Mode choice | `mode_choice: false` | eqasim core | ⚪ OFF in all configs (no modal-split target) |
| MATSim output archive (run-named durable copy) | `archive_matsim_output` (true) | `matsim/output.py` | ✅ PR #181 MERGED (#156, ADR-0064; server pytest+e2e follow-up) |

---

## 3. Branches, PRs & worktrees (current)

- **This PR (2026-07-17):** honest-skip guard for the INKAR income smoke test (xlrd/env root cause) + PM record of the 2026-07-16 key-matching audit and the raw-data restore. Docs + one test guard; no model-behaviour change.
- **Merged 2026-07-16: key-matching / fallback audit — [PR #191](https://github.com/TUBS-IVS/eqasim-bs/pull/191) + [PR #194](https://github.com/TUBS-IVS/eqasim-bs/pull/194).** Project-wide AGS/ARS + join/fallback sweep: ars5 dtype-at-READ in the MiD loaders, buildings AGS→ARS-12 fallback-vocabulary fix, HSN leading-zero dtype, ipf/gravity fail-fast guards, join-coverage logging on every silent left-merge+fillna(0); all 14 standing test failures root-caused (fixture bugs / test drift / data-gap skips / stale pre-#92 age golden regenerated on the complete data drop). Full suite **2986 passed / 0 failed** (run under the `eqasim` conda env; local system-Python shadows `matsim` and lacks `xlrd`).
- **Incident 2026-07-16 (resolved, closes #76):** the gitignored raw-data drop under `eqasim-data/data` was lost locally (worktree-cleanup junction hazard) and **fully restored from felix** (24G, `LC_ALL=C` file-list diff empty). Local pipeline runs work again.
- **Merged 2026-07-16: [PR #184](https://github.com/TUBS-IVS/eqasim-bs/pull/184)** `worktree-fix-128-sector-aware-callsite` (closes #128): sector-aware tilt PARKED (ADR-0065) + ON-path KeyError fix + `employees.py` LK-aggregate false-loss fix (**full-ZGB-8 run blocker** — every 8-Kreis run would have aborted at the 25% threshold). Follow-up **[PR #185](https://github.com/TUBS-IVS/eqasim-bs/pull/185)** (merged 2026-07-16, closes #183): analysis_suite execute-path `context.config()` contract fix.
- **Merged 2026-07-16: [PR #187](https://github.com/TUBS-IVS/eqasim-bs/pull/187)** `fix/popsim-validation-kreis-key-zfill`: `zfill(12)` leading-zero fix in the popsim-validation employed-rate extractors (`employed_25_64_rate` / `employed_by_age_group` — un-padded numeric ARS yields Kreis keys like `3101` that never match `03101`; every Lower-Saxony Kreis affected). Rescued 2026-07-12 audit (#159) work that had never reached PR #165/#166. Companion **[PR #186](https://github.com/TUBS-IVS/eqasim-bs/pull/186)** landed the rescued #128 A/B PM records.
- **Local worktree cleanup 2026-07-16:** 35 merged worktrees + 43 fully-merged local branches removed (merge-base-verified; unique uncommitted content rescued first → PR #186/#187). Remaining local worktrees carry only genuinely unmerged/parked work: calibration-corner, popsim-validation-stage, taz-gravity-calibration, verbindungen-reference, bbs-share-by-age (fix+audit-followups), gravity-calib-popsim-mid, kreis5-integration (fix-facilities-candidates), pm-resync, runcontrol-gui, s1a/s1c kreis-control, fleet (`eqasim-bs-fleet`). This supersedes the stale "Active worktrees" bullet below.
- **`origin/main` = `031aefc`** (PR #19). *(Section below this line is stale — pre-dates the July PM syncs; trust git + newer entries above.)* Recent merges: PR #16/#17 building potentials, **#18 + #19 calibration corner**.
- **Open PR: [#20](https://github.com/TUBS-IVS/eqasim-bs/pull/20)** `reconcile/calibration-remainder` (+441/−20: leisure-correction fix, `_load_stage` alias, scorer-sweep, income-scaling skip). Local tests green; pending server full-suite before merge.
- **Deleted 2026-06-27** (feature-superseded prototypes, verified): `feature/secondary-external-candidates`, `feature/cordon-supply`, `feature/cordon-incommuters` (local + origin).
- **Retirable after #20 merges:** `feature/calibration-corner` (stale pre-squash), `worktree-calibration-corner`.
- **Active worktrees** (`.claude/worktrees/`): calibration-corner, cordon-whole-region-gates, employment-age, employment-grid, fleet-consistency, fleet-income-age, popsim-g5, simwrapper, tier3-part2 — most merged; clean these up as part of the loop.
- **Open branch (unmerged): `feature/fleet-quality-and-data`** (fork, tip `5e0a6e4`, 41 commits stacked on `fix/fleet-age-joint-ipf` PR #92; worktree `eqasim-bs-fleet`). Fleet realism upgrade (Plans 1–3): EV-income tilt, all-Kreise 46251, Euro-6 substage, RS7 logging-only cross-check, degenerate-Kreis NaN guard, no-NA guarantee (euro="electric"). Final opus review done; local suite 290✓ / 2 stale-OFF-golden✗ / 35 skip. **Pending:** server phase (run extractors + full pytest + 1% smoke + **regenerate the 2 OFF goldens**) then `git pr` — **resolve the ADR-0050 number collision** (fleet ceiling vs TAZ friction). Also merge the stacked fleet PRs #90/#93 (close #86/#91/#92). Full state: memory `project-fleet-quality-realism`, ADR-0051.
- **PR rule:** always `git pr` (alias → base `TUBS-IVS/eqasim-bs`, never the eqasim-org upstream).

---

## 4. What's open / next (top of the backlog)

Full ranked detail in [PROJECT_BACKLOG.md](PROJECT_BACKLOG.md). Headlines:

1. **Merge PR #20** after a server test run.
2. **100% production run** on newest code (Tier-A/B caching makes it affordable).
3. **Mode-choice ASC calibration** (DMC is OFF → no behaviourally valid modal split).
4. **German MiD Wege trip donor** (replace French ENTD-2008) — highest-value lever, blocked on MiD microdata.
5. ~~Pre-existing local test failures~~ — RESOLVED 2026-07-16 (PR #191/#194): all standing failures root-caused, full suite 2986/0 green under the `eqasim` conda env. Issue-backlog cleaned 2026-07-17: #130/#76/#137 closed (already-done / superseded), #124 corrected to phase-2-only. FRAGILE hardening batch shipped as PR #196 (see backlog).

**Deliberately dropped (do not re-attempt):** commute friction pinning, f(d) detour curve as default, scorer `pot_weight` tuning, raking employment to P9, within-Kreis extra income signal, PopulationSim *importance* calibration (design only) — see backlog §1 Tier 5.

---

## 5. Where everything lives (doc map)

| Doc | Purpose |
|---|---|
| `CLAUDE.md` | Binding rules + working discipline (authoritative); deep feature detail linked from here |
| `docs/features/*` | Deep per-feature detail (split out of CLAUDE.md) |
| **`PROJECT_STATUS.md`** (this) | At-a-glance feature/branch dashboard (single status source) |
| `PROJECT_BACKLOG.md` | Ranked open/partial/dropped work |
| `docs/DECISIONS.md` | ADR log — the *why*, commit/PR-linked, back to the bavaria baseline |
| `RUNS.md` | Simulation run ledger |
| `docs/UPSTREAM_DELTA.md` | What eqasim-bs adds vs. eqasim-bavaria (pinned merge-base) |
| `docs/ONBOARDING.md` | Durable narrative entry point (replaces PROJECT_HANDOVER.md) |
| `CONTRIBUTING.md` | Canonical feature workflow + human contract |
| `docs/codebase/` | Architecture, structure, stack, conventions, integrations, testing, concerns |
| `SESSION_LOG.md` | Chronological work log (update via `/close`) |
| `docs/superpowers/{specs,plans}/` | Per-feature design specs + execution plans |
| Claude memory (`~/.claude/.../memory/`) | Curated long-term facts (travels with `~/.claude`, not the repo) |
