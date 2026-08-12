# PROJECT STATUS — eqasim-bs (dashboard)

Updated: 2026-07-23 · Details: docs/features/ · History: docs/archive/ · Decisions: docs/DECISIONS.md

## 1. Live state

- Pipeline runs end-to-end: synpp population synthesis -> MATSim scenario export -> Java `RunSimulation` (mode choice OFF) -> analysis/SimWrapper.
- `main` = `39d1fa4` (PR #238 merged 2026-07-23; config-composition #234 + eqasim-java-2.2.0 matsim.output fixes #237 + upstream fix-sweep #238 all merged on the #225 baseline): the SrV-anchored trip-participation controls (#224), VerBindungen inner anchor (#193), placement-income L2 (#108), and student in-commuters (#140) are ALL merged already — several branch-status notes elsewhere in the repo predate these merges and are stale relative to `main`. **eqasim-java 2.2.0 is now e2e-green through `matsim.output`** (1-Kreis + freight, real QSim iteration 0; ADR-0071).
- Current validated scale: 25% (`*_25pct_allfeat`) full run plus per-Kreis control-smokes; **no 100% production run exists yet on the newest code** (Tier-A/B caching now makes one affordable — see backlog #1).
- Mode choice is OFF in every run config (`mode_choice: false`) — no calibrated modal split exists; do not read any run's mode shares as behaviourally validated.
- Convergence caveat: the eqasim termination criterion stops a run when mode shares STABILISE, not when they match observed data — stabilisation is not validation.
- Last full-suite green: 2026-07-19, felix, 3170 passed / 0 failed; no full-suite re-run is recorded in `SESSION_LOG.md` since PR #225 (2026-07-20).
- Current focus: (a) **escort family #201+#256+#257 + multi-child anchoring COMPLETE** — **[PR #260](https://github.com/TUBS-IVS/eqasim-bs/pull/260) OPEN, MERGEABLE** (closes all three; conflicts vs main resolved 2026-08-12); anchor-fix re-run @5f10f25: consecutive zero-legs 674->96 (-86%), all residual same-facility siblings. (b) eqasim-java 2.2.0 e2e-green; PR #239 MERGED (8ee06c0). Next: merge #260 -> java companion PR -> 100% production run on the 2.2.0 stack.

## 2. Feature matrix

Legend: ✅ merged on `main` · 🟢 flag-gated, ON in real-data run configs · ⚪ default-OFF, byte-identical ·
🟡 built/infra, deliberately not activated · ⏸ PARKED (measured, kept OFF) · ⚠️ assumption. Every row below
carries forward one row of the pre-trim matrix (or is marked NEW); detail lives in the linked doc, not here.

| Feature | Status | Flag / config key | Validated against | Detail doc |
|---|---|---|---|---|
| **[Location]** NEW — Escort family: dedicated purpose + SrV draw (#201), passive-as-education (#256), distance-by-type (#257), multi-child anchoring (ADR-0073) | 🟢 complete on branch, twice run-validated (@5f10f25: consecutive zero-legs 674->96, purpose mix invariant); **PR #260 OPEN** | `escort_purpose`, `escort_household_link`, `escort_passive_education`, `escort_distance_by_type` (all ON in base_bs) | pinned `srv2023_escort_destination_types.csv` + `srv2023_escort_distance_factors.csv` + `mid2023_escort_w_zweck_split.csv`; W1 active-adjusted refs | `docs/features/escort-purpose.md` (branch); ADR-0072/0073 (branch) |
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

**1 open PR:** [#260](https://github.com/TUBS-IVS/eqasim-bs/pull/260) — escort family #201+#256+#257 + multi-child anchoring (58 commits incl. main-merge; **MERGEABLE**, conflicts resolved 2026-08-12: STATUS/BACKLOG took main, DECISIONS = main + ADR-0072/0073 appended). PR #234, #237, #238, and #239 (eqasim-java 2.2.0 matsim.output green) all MERGED 2026-07-22/23. Local branches not yet merged into `main` (`git branch --no-merged main`), most checked out as `.claude/worktrees/*`:
- `feature/escort-purpose-201` (worktree `feature-escort-purpose-201`) — **escort family, [PR #260](https://github.com/TUBS-IVS/eqasim-bs/pull/260) OPEN @ d08367d**; twice run-validated (`~/wt-escort`); dissolve worktree after merge; java companion `feature/escort-activity-type` (eqasim-java-bs, c9a1f79) needs its own PR post-merge.

- `worktree-calibration-corner` (worktree `calibration-corner`) — calibration-corner remainder, backlog #1, server test run pending.
- `feature/fleet-quality-and-data` (worktree `eqasim-bs-fleet`) — fleet realism upgrade, backlog [1.5], server phase + PR pending.
- `worktree-feature+popsim-validation-stage` — PopSim control-fit validation stage, server pytest + smoke pending.
- `worktree-fix+gravity-calib-popsim-mid` — gravity calibration popsim_mid fix, committed, not pushed.
- `feature/taz-gravity-calibration` (worktree `feature+taz-gravity-calibration`) — TAZ friction, parked backup only (ADR-0067, TAZ stays OFF).
- `feature/bbs-share-by-age` (worktree `fix+audit-followups`) — BBS share-by-age control follow-up.
- `feature/runcontrol-gui` (worktree `runcontrol-gui`) — run-control GUI prototype.
- `run/kreis5-integration` (worktree `fix-facilities-candidates`) — kreis5 facilities-candidates fix.
- `worktree-s1a-generic-kreis-control` / `worktree-s1c-cars-control` — generic Kreis-control + cars-control spikes (same tip, not yet diverged).
- `worktree-feature+config-composition-cleanup` — PR #239 **MERGED** (8ee06c0, 2026-08-12); worktree can be dissolved by its owning session.
- `docs/pm-resync`, `docs/pm-sync-2026-07-18` — earlier PM-doc sync attempts, superseded by this branch.
- `feature/primary-locations-all-employed` — #203 all-employed primary locations, core built (TDD 18/18), unpushed.
- `feature/calibration-corner`, `fix/popsim-no-silent-fallback`, `wip/felix-allfeat-20260718`, `wip/local-placement-l2-20260719`, `backup/status-presentation-pre-rebase` — no active worktree; stale/backup, candidates for cleanup.
- `chore/pm-layer-consolidation` (this branch) — PM-layer consolidation in progress.

Stale worktree dirs pending removal (branch already merged into `main`): `placement-income-l2` (PR #212), `verbindungen-anchor` (PR #197), `student-incommuters-140` (PR #219).

## 4. Top of the backlog

1. Merge [PR #260](https://github.com/TUBS-IVS/eqasim-bs/pull/260) (escort family) → dissolve worktree → java companion PR; then backlog [0.6] follow-ups (issues, ADR-0074 carry-over, CLI flag).
2. Calibration-corner remainder — run the server test suite, then push as one PR.
3. Push `integration/all-features` once the remainder above lands.
4. 100% production run on the newest code (Tier-A/B caching makes it affordable).
5. Mode-choice ASC calibration (turn DMC on, anchor modal split to the committed MiD mode-margin reference).
6. Finish Tier-A/B cache config wiring (`cache_share_stages` list + fixed `popsim_work_dir` in server configs).

Full ranking: PROJECT_BACKLOG.md §1
