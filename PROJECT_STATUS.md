# PROJECT STATUS — eqasim-bs (dashboard)

Updated: 2026-08-09 · Details: docs/features/ · History: docs/archive/ · Decisions: docs/DECISIONS.md

## 1. Live state

- Pipeline runs end-to-end: synpp population synthesis -> MATSim scenario export -> Java `RunSimulation` (mode choice OFF) -> analysis/SimWrapper.
- `main` = `8ee06c0` (PR #239 merged 2026-07-23; config-composition #234 + eqasim-java-2.2.0 matsim.output fixes #237 + upstream fix-sweep #238 + #239 all merged on the #225 baseline): the SrV-anchored trip-participation controls (#224), VerBindungen inner anchor (#193), placement-income L2 (#108), and student in-commuters (#140) are ALL merged already — several branch-status notes elsewhere in the repo predate these merges and are stale relative to `main`. **eqasim-java 2.2.0 is now e2e-green through `matsim.output`** (1-Kreis + freight, real QSim iteration 0; ADR-0071).
- Current validated scale: 25% (`*_25pct_allfeat`) full run plus per-Kreis control-smokes; **no 100% production run exists yet on the newest code** (Tier-A/B caching now makes one affordable — see backlog #1).
- Mode choice is OFF in every run config (`mode_choice: false`) — no calibrated modal split exists; do not read any run's mode shares as behaviourally validated.
- Convergence caveat: the eqasim termination criterion stops a run when mode shares STABILISE, not when they match observed data — stabilisation is not validation.
- **Test evidence is stale and unautomated (verified 2026-08-09).** Last full-suite green: 2026-07-19, felix, 3170 passed / 0 failed — since then **59 commits / 12 merged PRs** landed on `main` with no recorded re-run. `.github/workflows/tests.yml` triggers on `develop`, a branch that does not exist in this fork, so the suite has **never** run in CI (`gh run list` shows only Copilot Code Review). Treat every "✅" below as *last measured*, not *currently verified*.
- Current focus: no open PR; `main` is the 2.2.0 stack with `matsim.output` e2e-green (1-Kreis + freight). Next: 100% production run on that stack. The newest *unmerged* work is `feature/escort-purpose-201` (18 TDD commits, 2026-07-24, never PR'd).

## 2. Feature matrix

Legend: ✅ merged on `main` · 🟢 flag-gated, ON in real-data run configs · ⚪ default-OFF, byte-identical ·
🟡 built/infra, deliberately not activated · ⏸ PARKED (measured, kept OFF) · ⚠️ assumption. Every row below
carries forward one row of the pre-trim matrix (or is marked NEW); detail lives in the linked doc, not here.

| Feature | Status | Flag / config key | Validated against | Detail doc |
|---|---|---|---|---|
| **[Synthesis]** IPF synthesis (legacy default) | ✅ | `population.method: simple_ipf_open` | Zensus 2022, GENESIS | docs/features/household-synthesis.md |
| **[Synthesis]** popsim_open / popsim_mid | ✅ alt paths (popsim_mid ON in allfeat_popsim) | `population.method` | Zensus + MiD 2023 | docs/features/household-synthesis.md |
| **[Synthesis]** Household-size margin | 🟢 | `ipf.use_household_size_margin` | Zensus 1000A-2081 | docs/features/household-synthesis.md |
| **[Synthesis]** Joint age×size margin (#3) | 🟢 | `ipf.use_joint_age_size_margin` | Zensus 1000A-3082 | docs/features/household-synthesis.md |
| **[Synthesis]** Age-aware composition (#3b) | 🟢 | `ipf.age_aware_chunking` + `chunking.*` | Destatis 2024 (mother age 31.8) | docs/features/household-synthesis.md |
| **[Synthesis]** Sex-aware couples (~1.1%) | 🟢 | `chunking.sex_aware_couples` | Destatis MZ 2025 | docs/features/household-synthesis.md |
| **[Synthesis]** Cell-accurate homes (100m) | ✅ (popsim) | popsim home alias | Zensus 100m grid | docs/features/building-potentials.md |
| **[Synthesis]** ALKIS-typed home matching | ✅ | data-driven | Zensus building-type | docs/features/building-potentials.md |
| **[Synthesis]** LoD2 height/volume typing | ✅ (verified 2026-06-27) | data-driven (`MFH_MIN_FLOORS=4`) | LoD2 3D-Shape sweep | docs/features/building-potentials.md |
| **[Synthesis]** Income spatial tilt (Nettokaltmiete) | ✅ (popsim; overridden by placement_income when ON) | `popsim.income_spatial_tilt` | INKAR/Zensus rent | docs/features/regional-control-targets.md |
| **[Attrs]** Economic status (Bayes hhtype×region) | 🟢 | `status_from_hhtype` (code true) | MiD status×hhtype×region | docs/features/mid-reference-tables.md |
| **[Attrs]** Household income € + distribution | 🟢 | `income_eur_from_distribution` | MiD H4/brackets, INKAR | docs/features/mid-reference-tables.md |
| **[Attrs]** Kreis income control (popsim) | ✅ (popsim; overridden by placement_income when ON) | `popsim.income_kreis_control` | MiD, INKAR | docs/features/regional-control-targets.md |
| **[Attrs]** Placement income L2 (#108) | ✅ **MERGED** PR #212 (default ON, ADR-0069); overrides Kreis income control + income tilt | `popsim.placement_income` | MiD hheink, INKAR; 2-Kreis gate: invariants Δ0, coherence 0.174→0.364 | docs/features/regional-control-targets.md |
| **[Attrs]** PT subscription (P24.1, 3-margin IPF) | 🟢 | `pt_subscription_conditioned` | MiD P24.1 | docs/features/mid-reference-tables.md |
| **[Attrs]** Driving licence (P17.1, 3-margin IPF) | ✅ | always-on enrichment | MiD P17.1 | docs/features/mid-reference-tables.md |
| **[Attrs]** Consistent car_availability | 🟢 | `consistent_car_availability` | MiD P19/P17.1/H7 | docs/features/mid-reference-tables.md |
| **[Attrs]** Income-aware #cars | 🟢 | `cars_income_aware` | MiD H7 | docs/features/mid-reference-tables.md |
| **[Attrs]** Employment margin (IPF) | ✅ | `ipf.use_employment_margin` | GENESIS SvB | docs/features/household-synthesis.md |
| **[Attrs]** Tier-3 Kreis controls | ✅ (popsim) | `popsim.control_tiers: …tier3` | Zensus + GENESIS | docs/features/regional-control-targets.md |
| **[Attrs]** Housing tenure (completeness) | 🟢 | `synthesise_housing_tenure` | MiD income×Wohnen | docs/features/mid-reference-tables.md |
| **[Attrs]** Reactivated attrs (couple/studies/SPC) | 🟢 | `reactivate_person_attributes` | Destatis education | docs/features/mid-reference-tables.md |
| **[Attrs]** NEW — SrV participation controls (#224) | ✅ **MERGED** PR #225 (default ON, OFF byte-identical) | `trip_class` (now hard) + `work_participation`/`leisure_participation`/`education_participation` | SrV 2023 per-Kreis; Kreis 03101 smoke: 78/78/53/30% of MiD→SrV gap closed (residual donor-bound) | docs/features/regional-control-targets.md |
| **[Fleet]** Household fleet (vs default car) | 🟢 | `vehicles_method: household` | MiD H7, KBA | `synthesis/vehicles/cars/household.py` |
| **[Fleet]** German fleet segment+brand mix | 🟢 | `fleet_model_enabled` / `_brands` | KBA FZ | `synthesis/vehicles/fleet_sampling_de.py` |
| **[Fleet]** BEV/electric calibration | 🟢 | `fleet_electric_calibration` | KBA FZ 27.15/27.17 | `synthesis/vehicles/fleet_sampling_de.py` |
| **[Fleet]** HSN/TSN engine attrs (kW/ccm/fuel) | 🟢 | `fleet_hsn_tsn_attributes` | KBA HSN/TSN scraper | `synthesis/vehicles/hbefa.py` |
| **[Fleet]** Fleet consistency v2 + income-age | ✅ (PR #12/#13) | folded into household fleet | KBA/MiD | `synthesis/vehicles/` |
| **[Fleet]** Fleet realism upgrade (EV-income tilt, Euro-6, RS7 cross-check) | 🟡 pushed `feature/fleet-quality-and-data`, server-verify + merge pending | `fleet_ev_income_tilt` / `fleet_euro6_substage` | KBA 46251-02/03, FZ 27.4, MiD A_ANTRIEB | `synthesis/vehicles/fleet_sampling_de.py` |
| **[Fleet]** Carless routing re-mode | 🟢 | `remode_carless_car_legs` | routing consistency | `matsim/simulation/prepare.py` |
| **[Location]** Gravity OD (work/edu) | ✅ | `gravity_slope -0.065` | BA Pendleratlas | docs/features/gravity.md |
| **[Location]** Per-RS7 gravity slope | 🟢 | `gravity_slope_by_regiostar7` | BA Pendler Poisson GLM | docs/features/gravity.md |
| **[Location]** Education gravity (schools/Kita/uni) | 🟢 (allfeat) | `education_gravity_enabled` | MiD T43, Destatis MZ 2024 | docs/features/education-gravity.md |
| **[Location]** Building potentials — work | 🟢 | `work_building_potentials` (code true) | GENESIS SvB aggregate | docs/features/building-potentials.md |
| **[Location]** Building potentials — secondary | 🟢 | `secondary_building_potentials` + scorer | MiD W12 | docs/features/building-potentials.md |
| **[Location]** Building potentials — education | 🟢 | `education_building_distribution` | within-facility | docs/features/building-potentials.md |
| **[Location]** Calibration: purpose-resolved secondary | 🟢 (allfeat_popsim) | `secondary_distance_by_purpose` / `_shop_daily_split` | MiD W12 per-purpose | docs/features/secondary-distances.md |
| **[Location]** Calibration: per-band commute friction | 🟡 infra, not activated (model already <0.08 EMD) | `gravity_friction_factors` (None) | MiD P13 | docs/features/calibration-corner.md |
| **[Location]** Sector-aware attraction tilt (#128) | ⏸ **PARKED** (ADR-0065; 9x worse SvB fit) | `braunschweig.gravity.sector_aware_enabled` (False) | GENESIS 13111-01-03-5 | docs/features/gravity.md |
| **[Location]** VerBindungen sub-Kreis OD validation (#124) | ✅ **MERGED** PR #189/#190 (check-B TVD 0.137) | run-list stage (default-ON) | VerBindungen 2019 QZM (open data) | docs/features/gravity.md |
| **[Location]** svb_wohn work production mass (#132) | ⏸ **PARKED** default OFF (ADR-0066; TVD 0.1136→0.1137) | `braunschweig.gravity.work_production_mass` (population) | VerBindungen 2019 QZM | docs/features/gravity.md |
| **[Location]** Inner VerBindungen calibration anchor (#193) | ✅ **MERGED** PR #197 (default ON, ADR-0068 human override) | `braunschweig.gravity.verbindungen_anchor_enabled` | MiD P13-by-RS7 + P38.2; QZM = fit reference once ON | docs/features/gravity.md |
| **[Location]** Calibration: Tier-3 detour/circuity curve | 🟡 opt-in infra (measured immaterial) | `mode="curve"` (default constant 1.3) | OSM graph, Giacomin & Levinson 2015 | docs/features/detour-circuity.md |
| **[Location]** TAZ sub-zonal work location choice | ⏸ **PARKED permanently OFF** (ADR-0067) | `taz_work_location_choice` (OFF, byte-identical) | MiD P13 (EMD ~0.054) | docs/features/taz-work-location.md |
| **[Cordon]** Cordon network ring + cut | 🟢 | `cordon_enabled` | VG250 polygon | `data/cordon/network_clip.py` |
| **[Cordon]** Einpendler injection | 🟢 | `cordon_enabled` | BA Pendler, Mikrozensus | `synthesis/incommuters.py` |
| **[Cordon]** Gates (road + PT/Bahnhof) | 🟢 | (part of cordon) | OSM, GTFS | `data/cordon/{gates,gate_entry,pt_reachability}.py` |
| **[Cordon]** Mode balancer | 🟢 | (part of cordon) | Mikrozensus modes | `data/cordon/mode_balancer.py` |
| **[Cordon]** Student in-commuters (#140) | ✅ **MERGED** PR #219 + config-contract fix PR #223; server E2E dry-run + real-data validation still PENDING | `cordon_student_incommuters_enabled` (tri-state, default-ON) | LSN SS2025 enrollment, DESTATIS 12411-0018 | docs/features/student-incommuters.md |
| **[Freight]** Long-haul freight injection (v3) | 🟢 (100%/freight configs) | `freight_enabled` (code true) | Lu et al. 2022 — NOT BASt-calibrated | docs/features/freight.md |
| **[Freight]** Freight analysis exclusion | ✅ | auto | — | docs/features/freight.md |
| **[Freight]** Assumptions (truck PCE / max velocity) | ⚠️ ASSUMPTIONS | `freight_truck_pce 3.5`, `_max_velocity_kmh 80` | StVO / uncalibrated | docs/features/freight.md |
| **[Analysis]** MiD validation report | ✅ | `analysis/run_mid_validation.py` | MiD P9/P12_1/P13/P17_1 | docs/features/run-analysis.md |
| **[Analysis]** Full analysis (dashboard+MiD) | ✅ | `analysis/run_full_analysis.py` | — | docs/features/run-analysis.md |
| **[Analysis]** Population validation (controls/quality/geo) | ✅ | `analysis/population_validation/` | Zensus | docs/features/run-analysis.md |
| **[Analysis]** Integerizer quality (per-cell error map) | ✅ | `analysis/integerizer_quality/` | — | docs/features/run-analysis.md |
| **[Analysis]** SimWrapper export (8 chart + 4 map + commuter tabs) | ✅ | `analysis/simwrapper/` | — | docs/features/run-analysis.md |
| **[Analysis]** SimWrapper Layer-1 (MATSim Java contrib) | ⚪ `simwrapper_dashboards: false` | Java `RunSimulation --simwrapper` | — | docs/features/run-analysis.md |
| **[Analysis]** Education enrollment validation | ✅ | `analysis/run_education_validation.py` | LSN capacity | docs/features/run-analysis.md |
| **[Infra]** Shared stage-cache (prime-on-launch) | ✅ | `cache_share_enabled` (true) | — | docs/features/cache-share.md |
| **[Infra]** Tier-A/B caching (32 stages + popsim) | ✅ (config wiring partial, backlog #1.3) | fixed `popsim.work_dir` | — | docs/features/cache-share.md |
| **[Infra]** Own eqasim-java-bs fork (2.2.0, matsim.output e2e-green 2026-07-23) | ✅ | `eqasim_source_path` | — | `../eqasim-java-bs` |
| **[Infra]** Urban parking (BS inner ring) | 🟢 | `enable_urban_parking` | — | `matsim/simulation/prepare.py` |
| **[Infra]** Parallel chainsolvers | 🟢 | `chainsolvers.parallel` / `.processes` | — | `synthesis/locations/secondary_chainsolvers` |
| **[Infra]** Mode choice | ⚪ OFF in all configs (no modal-split target) | `mode_choice: false` | — | eqasim core |
| **[Infra]** MATSim output archive (run-named durable copy) | ✅ PR #181 MERGED (ADR-0064) | `archive_matsim_output` (true) | — | `matsim/output.py` |
| **[Infra]** Run-config composition (base + per-scale overlay) | ✅ **MERGED** PR #234 (ADR-0070) | `configs/base_bs.yml` + `configs/overlays/*` via `run_synpp.py <base> <overlay>` | felix synth smoke (int-seed applied at runtime) | ADR-0070 |

## 3. Branches & PRs

**No open PR.** PRs #234/#237/#238/#239 all MERGED 2026-07-22/23. The local clone is clean: `main` only, no local feature branches, no worktrees (verified 2026-08-09 — the previous version of this section listed 11 local branches and 3 stale worktrees that no longer exist).

Remote (`origin`) carries 33 non-`main` branches. **17 are fully merged into `main`** and carry no unique commits — safe to delete, pending push approval: `docs/pm-layer-and-test-fix`, `feature/{alkis-typed-home-matching, building-activity-potentials, education-gravity-bs, employment-age-control, fleet-income-age, hts-matching-optimization, logging-theme-mirror, popsim-optimized-importance, population-method-workflows, simwrapper-dashboards, tier3-controls, tier3-kreis-sourcing, tier3-live-wiring}`, `fix/income-eur-floor`, `integration/all-features`, `refactor/braunschweig-clean-fork`.

**16 unmerged remote branches** (`+N` = commits ahead of `main`):

| Branch | +N | Last commit | Standing |
|---|---|---|---|
| `feature/escort-purpose-201` | 18 | 2026-07-24 | **Live front** — #201 escort purpose, full TDD chain (SrV V_ZWECK_BHOL weights → distance layer → chainsolver → facilities → household escort links). Never PR'd. |
| `feature/fleet-quality-and-data` | 43 | 2026-07-03 | Backlog [1.5] — server phase (KBA/MiD extraction, pytest, 1% smoke, 2 stale OFF goldens) then PR. |
| `worktree-calibration-corner` | 68 | 2026-06-27 | Backlog [0.1] — server test run + PR. Open fork: whole body vs. cherry-pick (backlog §3.1). |
| `feature/runcontrol-gui` | 49 | 2026-07-10 | #119 prototype, no gate defined. |
| `worktree-fix+gravity-calib-popsim-mid` | 13 | 2026-06-29 | Gravity calibration popsim_mid fix; blocks backlog "calibrate gravity" step 2. |
| `feature/primary-locations-all-employed` | 3 | 2026-07-18 | #203, core built (TDD 18/18). |
| `run/kreis5-integration` | 3 | 2026-07-12 | kreis5 facilities-candidates fix. |
| `feature/bbs-share-by-age` | 1 | 2026-07-14 | BBS share-by-age control follow-up. |
| `feature/taz-gravity-calibration` | 6 | 2026-07-01 | Backup only — ADR-0067 keeps TAZ permanently OFF. Delete candidate. |
| `feature/calibration-corner` | 1 | 2026-06-25 | Superseded by `worktree-calibration-corner`. Delete candidate. |
| `feature/tier3-kreis-controls` | 5 | 2026-06-18 | Feature-superseded (tier-3 controls merged). Delete candidate. |
| `feature/employment-grid-control` | 4 | 2026-06-17 | Feature-superseded. Delete candidate. |
| `feature/fleet-vehicle-consistency` | 1 | 2026-06-18 | Feature-superseded (PR #12/#13). Delete candidate. |
| `wip/felix-allfeat-20260718` | 1 | 2026-07-19 | WIP snapshot. Delete candidate. |
| `wip/local-placement-l2-20260719` | 1 | 2026-07-19 | WIP snapshot, L2 merged via PR #212. Delete candidate. |
| `docs/status-presentation` | 2 | 2026-07-13 | Presentation draft. Delete candidate. |

## 4. Top of the backlog

1. Re-establish test evidence: point `tests.yml` at `main` (it targets the non-existent `develop`), re-run the full suite on felix against `8ee06c0`.
2. Land or park the unmerged front, newest first: `feature/escort-purpose-201` (#201), then calibration-corner remainder [0.1], then fleet-quality [1.5].
3. 100% production run on the newest code (Tier-A/B caching makes it affordable).
4. Mode-choice ASC calibration (turn DMC on, anchor modal split to the committed MiD mode-margin reference).
5. Finish Tier-A/B cache config wiring (`cache_share_stages` list + fixed `popsim_work_dir` in server configs).

Full ranking: PROJECT_BACKLOG.md §1
