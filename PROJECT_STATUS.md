# PROJECT STATUS — eqasim-bs (at-a-glance dashboard)

> **What this is.** The single quick-orientation dashboard: *what have we built, what state is
> it in, where does it live, what's open.* For the deep open-work backlog see
> [PROJECT_BACKLOG.md](PROJECT_BACKLOG.md); for binding rules + feature detail see `CLAUDE.md`;
> for architecture/onboarding see [docs/codebase/](docs/codebase/).
>
> **Last updated:** 2026-07-03 · `origin/main` = `141284e` (PRs #101 + #102 merged) · no open PRs.
> **Issue #96 FIXED** (PR #101, merged): the synthetic `employed` flag was inflated for minors
> (14-17yo ~96%, region +7-9pp) by a field-width missing-code collision in `missing.resolve`
> (substantive `P_TAET=9` Schueler treated as generic keine-Angabe and imputed). The popsim Tier-3
> employment control was already correct (raw `P_TAET.isin`); only the written attribute +
> population-validation were affected. **#25 closed** (stale erwerb test, fixed independently). A
> **minor-employment plausibility guard** (PR #102, merged; default WARN) now watches the under-15
> employed rate. **Next:** 100% re-run with the fix on main, then flip the guard to `raise=True`
> (measure-before-harden; Phase-0 for #99).
> **TAZ sub-zonal work location choice** (eqasim IRIS-analog): **Phase 1+2 MERGED to main** (PR #85 merge `f5f52d1` + PR #89 FutureWarning fix), flag `taz_work_location_choice` default OFF byte-identical, flag-ON 1% e2e green. **Phase 3 (#83): friction re-fit BUILT but measured unnecessary** — branch `feature/taz-gravity-calibration` @ `3c2ebb5` (6 commits, pushed as backup, PARKED as gated-off infra, not merged); the aggregate commute distribution already fits MiD P13 (measured EMD ~0.054 on the 100% `popsim_mid` pop; see ADR-0050). Remaining Phase-3 = **validate flag-ON TAZ at 100%** (full synthesis + scenario, 0 MATSim iterations) + a spatial validation map.
> Open issues: **#79** (TAZ feature, Phase 1+2 merged), **#80** (open-data pseudo-zone alt), **#83** (Phase-3 validation, re-scoped), **#81** (config cleanup), **#78** (secondary scorer calib), **#76** (data re-sync), **#86/#91** (analysis-suite), **#22/#23/#26/#25** (production run / mode-choice / 25% gate / test). Unlanded local work: distance-fit module + gravity-calib popsim_mid fix on `worktree-fix+gravity-calib-popsim-mid` (committed, not pushed). See ADR-0049, ADR-0050.
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

---

## 3. Branches, PRs & worktrees (current)

- **`origin/main` = `031aefc`** (PR #19). Recent merges: PR #16/#17 building potentials, **#18 + #19 calibration corner**.
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
5. Pre-existing local test failure `test_employed_valid_codes_map_to_existing_semantics` (fails on `main` too) — investigate.

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
