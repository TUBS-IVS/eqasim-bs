---
goal: Reduce model-vs-data discrepancies for the Braunschweig regional model — accurate commute flows, calibrated gravity model, realistic household sizes
version: 1.0
date_created: 2026-04-25
last_updated: 2026-04-25
owner: eqasim-bs / research
status: 'Planned'
tags: [calibration, gravity, commute, ipf, household, validation, research]
---

# Introduction

![Status: Planned](https://img.shields.io/badge/status-Planned-blue)

This is the **meta-plan** that orchestrates three sequential skill workflows:

1. **Phase 0 — `acquire-codebase-knowledge`** (focused): document the
   commute / gravity / IPF / household pipeline (Bavaria base + Braunschweig
   overrides) so we have a verified baseline before changing anything.
2. **Phase 1 — `refactor-plan`**: design surgical, sequenced refactors to the
   gravity calibration, external-workplace placement and household-size IPF.
3. **Phase 2..N — `create-implementation-plan`** (one plan per refactor unit):
   atomic, AI-executable plans with verification gates and rollback steps.

Anchor goal: **reduce the gap between MiD 2023 / Census 2022 / BA Pendleratlas
and the synthetic 10 % Braunschweig population** along the dimensions where
deviations are still material (see §0.2).

---

## 0. Current evidence (what we already know)

Sources: [eqasim-data/output_bs_10pct/validation/report.json](eqasim-data/output_bs_10pct/validation/report.json),
[plan/calibration-analysis-2025.md](plan/calibration-analysis-2025.md) (1 % run),
[braunschweig/gravity/model.py](braunschweig/gravity/model.py),
[bavaria/gravity/model.py](bavaria/gravity/model.py),
[bavaria/ipf/attributed.py](bavaria/ipf/attributed.py),
[scripts/validate_bs_10pct/metrics.py](scripts/validate_bs_10pct/metrics.py).

### 0.1 What works (do **not** touch)
- Population per district: `±2 %` vs Zensus 2022 (10 % run, ZGB-8 total
  +0.11 %).
- Trips per person: `3.10` vs MiD `3.1` ✅
- Gravity IPF (Kreis-pair) converges in 2 iterations.

### 0.2 Material deviations (target of this plan)

| KPI | Synthesis | Reference | Δ | Severity |
|---|---|---|---|---|
| Mode share `bicycle` | 3.0 % | MiD 13 % | **−10 pp** | 🔴 high |
| Mode share `walk` | 27.9 % | MiD 18 % | **+9.9 pp** | 🔴 high |
| Mean trip distance | 8.94 km | MiD 12.6 km | **−3.7 km** | 🟠 medium |
| Daily km / person | 32.5 km | MiD 39 km | **−6.5 km** | 🟠 medium |
| Purpose `home` | 42.4 % | MiD 15 % | **+27 pp** | 🔴 high (definition mismatch?) |
| Purpose `leisure` | 14.8 % | MiD 27 % | **−12 pp** | 🟠 medium |
| HH size distribution | (see report §3) | Zensus 2022 | unknown — **must be quantified per-bin** | 🟡 to-investigate |
| External commute share | 13 % | BA Pendleratlas 13 % | 0 ✅ | none |
| Internal commute mean | 11.2 km | (no direct ref) | n/a | n/a |

### 0.3 Open hypotheses (to verify in Phase 0)
- **H1** `home` overshoot is a *purpose-coding artefact*: every trip back home
  is counted as `home` instead of redistributing into MiD's `work/leisure/...`
  classification. → Check `synthesis.population.activities` purpose codes vs
  MiD P-table mapping.
- **H2** Mode-share bias (low bike / high walk) is driven by the `mode_choice`
  utility constants inherited from Île-de-France not being re-estimated for
  ZGB. → Check `bavaria/synthesis/population/enriched.py` and any `eqasim-java`
  utility config.
- **H3** Mean distance is too low because intra-Kreis activity locations are
  too dense (gravity matrix concentrates flow on home Gemeinde). → Check
  `gravity_diagonal=1.0` parameter effect and ratio of intra- vs inter-Gemeinde
  flow inside each ZGB Kreis.
- **H4** Household size distribution drifts because the IPF target table
  uses 5+ as one bucket while Zensus reports 1/2/3/4/5/6+ — a flat 5+
  bucket overweights size 5 and underweights size 6+. → Check
  `bavaria/ipf/prepare.py` margins and `references.load_zensus_households()`.

---

## 1. Requirements & Constraints

- **REQ-001** Every change must keep the 10 % Braunschweig pipeline reproducible
  end-to-end (`python -m synpp config_local_braunschweig_10pct.yml`).
- **REQ-002** Every refactor must be validated against three ground-truth
  sources: Zensus 2022 (population, HH-size), BA Pendleratlas 2025 (OD flows),
  MiD 2023 ZGB regional table (trips, distances, modes, purposes).
- **REQ-003** Validation report (`scripts/validate_bs_10pct`) must be re-run
  after every implementation phase; deviation deltas must be reported.
- **REQ-004** No regression of currently-good KPIs (population ±2 %, trips/person
  ±5 %).
- **CON-001** Bavaria upstream code (`bavaria/**`) is **read-only**. All
  Braunschweig-specific changes live in `braunschweig/**`, `scripts/**`,
  `config_*braunschweig*.yml`.
- **CON-002** `eqasim-java` (mode choice, MATSim utilities) is **read-only**
  for this iteration. R-E (mode-choice utility re-estimation) is therefore
  **deferred**; the bike/walk gap is documented as a known residual.
- **CON-003** Stay inside the eqasim/synpp stage flow. New BS logic is
  expressed as new synpp stages under `braunschweig/**` that wrap or
  override existing Bavaria stages — no parallel framework, no monkey-patching.
- **CON-004** No new external dependencies without explicit approval.
- **CON-005** BA Pendleratlas covers SvB only — synthesis volumes will remain
  ~30 % above raw BA SvB; this offset is *expected* and must not be
  "calibrated away".
- **CON-006** Reference data: only what is already loaded by `braunschweig.data.*`
  stages may be used inside calibration code. If a refactor needs an extra
  field (e.g. Zensus 1..6+ HH split), it must be sourced via a documented
  CSV extract under `data/census/` plus a new `braunschweig.data.*` loader
  stage — never inline-parsed inside calibration logic.
- **GUD-001** Implementation discipline: only changes directly required to
  close a deviation; no opportunistic refactoring.
- **PAT-001** Every code change ships with: (a) a new validation plot or
  metric in `scripts/validate_bs_10pct/`, (b) before/after deviation table,
  (c) a short note appended to [plan/calibration-analysis-2025.md](plan/calibration-analysis-2025.md).
- **PAT-002** Priority order is fixed (per user): **R-A → R-D → R-C → R-B**.
  R-E is deferred (Java-touch forbidden by CON-002).

---

## 2. Implementation Steps

### Phase 0 — Codebase Knowledge (skill: acquire-codebase-knowledge, focus mode)

- GOAL-000: Produce a verified, evidence-backed map of how commute flows, the
  gravity model and household IPF flow through the pipeline. Bavaria vs
  Braunschweig diff must be explicit. **No code changes in this phase.**

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-001 | Run the skill scan: `python scripts/scan.py --output docs/codebase/.codebase-scan.txt` (from skill folder) | | |
| TASK-002 | Populate focus docs: [docs/codebase/STACK.md](docs/codebase/STACK.md), [docs/codebase/STRUCTURE.md](docs/codebase/STRUCTURE.md), [docs/codebase/ARCHITECTURE.md](docs/codebase/ARCHITECTURE.md) — sections covering `bavaria.gravity.*`, `bavaria.ipf.*`, `bavaria.synthesis.population.*`, `braunschweig.gravity.*`, `braunschweig.locations.*`, `braunschweig.synthesis.*` | | |
| TASK-003 | Stub remaining four docs ([CONVENTIONS.md](docs/codebase/CONVENTIONS.md), [INTEGRATIONS.md](docs/codebase/INTEGRATIONS.md), [TESTING.md](docs/codebase/TESTING.md), [CONCERNS.md](docs/codebase/CONCERNS.md)) with `[TODO]` markers per the skill's Focus Area Mode | | |
| TASK-004 | Produce a `Bavaria → Braunschweig overrides` table in `ARCHITECTURE.md`: every stage, what BS adds, what it overrides | | |
| TASK-005 | Verify hypotheses H1..H4 from §0.3 by code reading + running targeted greps; record findings in `CONCERNS.md` with file/line evidence | | |
| TASK-006 | Generate one OD diagnostic notebook/script `scripts/diagnose_commute_od.py` that emits, for the existing 10 % cache: (a) intra-Kreis vs inter-Kreis flow split, (b) Gemeinde-level synth vs gravity-target ratio, (c) outbound destination ranking vs BA Pendleratlas | | |

**Phase 0 exit gate**: 7 docs in `docs/codebase/` (3 full, 4 stubbed); H1-H4
verified or refuted with evidence; OD diagnostic script runnable.

---

### Phase 1 — Refactor Plan (skill: refactor-plan)

- GOAL-100: For each confirmed deviation, design a sequenced refactor with
  pre/post checks, isolated to one calibration concern at a time.

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-101 | Refactor unit **R-A** "Gravity calibration deep-dive" — re-derive `gravity_slope` / `gravity_constant` for ZGB instead of inheriting IDF values; spec the IPF row+column dual-margin variant if H3 confirms intra-Kreis over-concentration | | |
| TASK-102 | Refactor unit **R-B** "External workplace placement" — only if the [calibration-analysis-2025.md](plan/calibration-analysis-2025.md) employment-weighted centroid still leaves a tail; otherwise mark `WONTFIX` with evidence | | |
| TASK-103 | Refactor unit **R-C** "Household-size IPF margins" — extend Zensus margin from `1/2/3/4/5+` to `1/2/3/4/5/6+` to match published Zensus 2022 buckets, and add a per-Kreis margin instead of global | | |
| TASK-104 | Refactor unit **R-D** "Activity-purpose remap" — add a synthesis-side mapping that splits the synthetic `home` purpose into MiD-comparable `work_return / leisure_return / shop_return` so the validation plots are apples-to-apples (this is a *reporting* refactor, not a synthesis one, unless H1 says otherwise) | | |
| TASK-105 | ~~Refactor unit **R-E** "Mode-choice utility constants"~~ — **deferred** (CON-002, no Java). Document the bike/walk residual in `CONCERNS.md` and as a follow-up issue. | n/a | n/a |
| TASK-106 | Per refactor unit (R-A..R-D), fill the skill's standard table (Current State / Target State / Affected Files / Phases / Verify / Rollback / Risks) and store as `plan/refactor-R{A..D}-1.md` | | |

**Phase 1 exit gate**: five refactor docs `plan/refactor-R{A..E}-1.md`, each
with rollback plan and risk list; user-approved priority ordering.

---

### Phase 2..N — Implementation Plans (skill: create-implementation-plan)

- GOAL-200: For each approved refactor unit from Phase 1, produce a
  machine-executable plan named `plan/feature-bs-calibration-{unit}-1.md` and
  execute it with verification gates after every TASK.

Standard structure per unit (template from the skill):

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-201 | Front-matter + REQ/SEC/CON list specific to the unit | | |
| TASK-202 | Phase 1: data preparation (extend reference loaders, add diagnostic plots) | | |
| TASK-203 | Phase 2: code changes (gravity / IPF / locations / synthesis) | | |
| TASK-204 | Phase 3: validation extension (new metric in `scripts/validate_bs_10pct`, new before/after plot) | | |
| TASK-205 | Phase 4: pipeline re-run + report regeneration; write delta table to `plan/calibration-analysis-2025.md` §9+ | | |
| TASK-206 | Phase 5: rollback verification — confirm `git revert` brings KPIs back to pre-change state | | |

**Phase 2..N exit gate (per unit)**: validation report shows the targeted
deviation reduced **without** regressing any KPI from §0.1; delta table
appended to calibration analysis; PR-ready commit.

---

### Cross-cutting validation harness (built once, used in every phase)

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-301 | Add `scripts/validate_bs_10pct/od_comparison.py`: bivariate scatter (synth_expanded × BA_flow) for the top-200 Kreis-pairs incl. RMSE, MAPE, R² | | |
| TASK-302 | Add `scripts/validate_bs_10pct/od_flowmap.py`: chord/Sankey diagram ZGB-8 internal + outbound top-20 | | |
| TASK-303 | Add `scripts/validate_bs_10pct/hh_size_per_kreis.py`: per-Kreis stacked bars synth vs Zensus, with χ²/KS test | | |
| TASK-304 | Add `scripts/validate_bs_10pct/regression_guard.py`: a snapshot test that fails when any §0.1 KPI moves more than the configured tolerance | | |
| TASK-305 | Wire all three diagnostics into `scripts/validate_bs_10pct/__main__.py` and the HTML report (new section "7. Calibration diagnostics") | | |

---

## 3. Alternatives

- **ALT-001** Fully replace the gravity model with a discrete-choice
  destination-choice model fitted on BA Pendleratlas. Rejected for now:
  large effort, BA covers only SvB, MiD-ZGB sample too small for a
  Gemeinde-level utility. Re-evaluate after R-A.
- **ALT-002** Drop external workplaces and only model ZGB-internal commutes.
  Rejected: violates BA volumes, removes a documented +13 % external share.
- **ALT-003** Re-implement HH-size IPF on top of MiD instead of Zensus.
  Rejected: MiD weighting cells are too coarse for ZGB.

## 4. Dependencies

- **DEP-001** Existing 10 % cache `eqasim-data/cache_bs_10pct/` (no re-run needed for Phase 0).
- **DEP-002** BA Pendleratlas 2025 dataset (already loaded via `braunschweig.data.census.pendler`).
- **DEP-003** Zensus 2022 household table — verify the file under
  `data/census/` exposes the 1/2/3/4/5/6+ breakdown; if not, request a
  re-extract from the Zensus 4-table portal (R-C blocker).
- **DEP-004** MiD 2023 ZGB regional CSVs in `data/hts/mid/` (already extracted
  per [scripts/extract_mid_tables.py](scripts/extract_mid_tables.py)).
- **DEP-005** `eqasim-java` mode-choice config (R-E only) — to be located in
  Phase 0 TASK-005.

## 5. Files

- **FILE-001** [braunschweig/gravity/model.py](braunschweig/gravity/model.py) — main target of R-A
- **FILE-002** [bavaria/gravity/model.py](bavaria/gravity/model.py) — read-only, evidence base for R-A
- **FILE-003** [bavaria/ipf/prepare.py](bavaria/ipf/prepare.py) — evidence base for R-C; do **not** modify (CON-001) — instead override via a new `braunschweig/ipf/prepare.py` if needed
- **FILE-004** [braunschweig/data/external_workplaces.py](braunschweig/data/external_workplaces.py) — R-B
- **FILE-005** [scripts/validate_bs_10pct/metrics.py](scripts/validate_bs_10pct/metrics.py) — extended with OD + HH χ² metrics
- **FILE-006** [scripts/validate_bs_10pct/plots.py](scripts/validate_bs_10pct/plots.py) — extended with OD scatter, flow map, HH per-Kreis stacked bars
- **FILE-007** [scripts/validate_bs_10pct/report.py](scripts/validate_bs_10pct/report.py) — new section 7
- **FILE-008** `plan/refactor-R{A..E}-1.md` — created in Phase 1
- **FILE-009** `plan/feature-bs-calibration-{unit}-1.md` — one per Phase 2..N execution

## 6. Testing

- **TEST-001** OD scatter: `R² ≥ 0.85` between synth_expanded and BA_flow on
  internal ZGB Kreis-pairs after R-A.
- **TEST-002** HH-size: per-Kreis χ² p-value ≥ 0.05 vs Zensus after R-C, or
  documented residual with size-class breakdown.
- **TEST-003** Mode share: `|Δ| ≤ 3 pp` per main mode after R-E (or
  documented residual if R-E is deferred).
- **TEST-004** Regression guard (TASK-304): population per Kreis stays
  within `±2 %`, trips/person within `±5 %`.
- **TEST-005** Determinism: existing [tests/test_determinism.py](tests/test_determinism.py)
  must still pass.

## 7. Risks & Assumptions

- **RISK-001** Re-estimating `gravity_slope` for ZGB on a 10 % sample may
  overfit — mitigation: cross-validate against the 1 % cache.
- **RISK-002** Splitting `home` purpose (R-D) might double-count trips in
  downstream MATSim runs — mitigation: change is *reporting-only* unless H1
  proves a synthesis bug.
- **RISK-003** Mode-choice re-calibration (R-E) requires a Java rebuild —
  may be deferred.
- **ASSUMPTION-001** BA Pendleratlas 2025 is the authoritative ground truth
  for inter-Kreis flows; the +30 % volume gap is structural (SvB vs Erwerbstätige).
- **ASSUMPTION-002** Zensus 2022 household table is finer-grained than
  what is currently loaded; verification in DEP-003.
- **ASSUMPTION-003** MiD ZGB regional table represents the ZGB-8 footprint
  closely enough that direct comparison is meaningful (vs MiD national).

## 8. Related Specifications / Further Reading

- [plan/calibration-analysis-2025.md](plan/calibration-analysis-2025.md) — prior 1 % analysis
- [plan/feature-bs-validation-10pct-1.md](plan/feature-bs-validation-10pct-1.md) — validation harness origin
- [plan/migration-braunschweig-1.md](plan/migration-braunschweig-1.md) — original BS migration
- [eqasim-data/output_bs_10pct/validation/report.html](eqasim-data/output_bs_10pct/validation/report.html) — current baseline report
- BA Pendleratlas 2025 documentation (SvB definition)
- MiD 2023 ZGB regional report (P/E/W/Z table family)
- Zensus 2022 household table 6.4 (HH-Größe)

---

## 9. User-confirmed decisions (2026-04-25)

1. **Q-PRIORITY** → **R-A → R-D → R-C → R-B**, R-E deferred.
2. **Q-SCOPE-R-E** → **No Java changes**. R-E is out of scope.
3. **Q-DATA-ZENSUS** → Use only what is already ingested. If a finer Zensus
   HH-size split is needed, deliver it as a documented CSV under
   `data/census/` + new loader stage (CON-006), do **not** inline-parse.
4. **Q-RUNTIME** → not explicitly answered — default: **batch R-A..R-D into
   one full pipeline re-run** at the end of the cycle, with cache-only
   sanity checks between units. Re-confirm before Phase 2.
5. **Q-PURPOSE-MAP** → Adopt the standard MiD `Wegezweck` → eqasim split;
   H1 must be fixed.

Additional constraints captured: stay inside eqasim/synpp flow, only modify
`braunschweig/**` and `scripts/**`, no Bavaria edits, no Java edits.

---

## 10. Suggested first concrete actions (after sign-off)

1. Approve §9 questions.
2. Execute Phase 0 TASK-001..006 (codebase docs + OD diagnostic).
3. Present H1..H4 verification + diagnostic plots.
4. Decide refactor unit order in Phase 1 kickoff.
