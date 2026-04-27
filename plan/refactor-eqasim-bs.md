# Refactor Plan: eqasim-bs → standalone Braunschweig fork

Status: **APPROVED 2026-04-27 — proceeding with Phase 0.**
Author: GitHub Copilot (acting as senior software engineer)
Date: 2025-04-26 (drafted), 2026-04-27 (approved)

## Decisions (confirmed by user, 2026-04-27)

* **D-1c** — Java MATSim classes stay as `org.eqasim.bavaria.*` for now; Java rename tracked as a follow-up issue.
* **D-2 yes** — Delete `bavaria/`, `config.yml`, `config_bavaria.yml`, `config_local_bavaria.yml`, and the IDF region configs.
* **D-3 `eqasim_common/`** — Region-neutral helpers go into a new top-level `eqasim_common/` package (mirrors how `eqasim` upstream and `eqasim-bavaria` separate generic from region-specific code).
* **D-4 yes** — One-time full cache wipe + re-run of 1 % / 10 % / 25 % accepted.
* **D-5 NO BUG FIXES IN THIS REFACTOR** — Goal is a clean, error-free migration. The 11 documented bugs stay open and are addressed in a separate follow-up branch. The Phase-0 baseline locks current behaviour; the Phase-4 verification must reproduce that baseline within RNG tolerance — i.e. **no functional change**, only relocation + renaming + documentation.

## Refactor Goal

Transform the current `eqasim-bs` repository (a working but Bavaria-branded fork of [eqasim-bavaria](https://github.com/eqasim-org/eqasim-bavaria)) into a **clean, standalone Braunschweig synthetic-population pipeline** that:

1. lives entirely in the `eqasim` universe (same synpp pipeline, same MATSim writer interface, same module conventions);
2. no longer carries Bavaria-/Munich-specific dead code that ZGB-8 does not need;
3. preserves a clear paper trail showing **which logic was inherited verbatim from `eqasim-bavaria`** and **where Braunschweig deviates and why**;
4. ships with a new English `README.md` that lists every input dataset, its source URL, and the download / preprocessing recipe — modelled on the `eqasim-bavaria` README;
5. introduces no functional regressions (verified by the existing 1 % / 10 % / 25 % smoke-test ladder and an extended pytest suite);
6. cleans up the eleven bugs documented in the session memory along the way (`/memories/session/ipf-braunschweig-analysis.md`).

> **Out of scope (this plan):** keeping the Bavarian or French (Île-de-France) configs alive. Bavaria stays available only as upstream history (git log) and as a documented "where this code came from" reference inside docstrings.

## Current State

* Two parallel Python packages: `bavaria/` (~34 files, the original fork from `eqasim-bavaria`) and `braunschweig/` (~33 files, our additions and overrides).
* Braunschweig configs (`config_local_braunschweig*.yml`) reuse Bavaria stages via 26 `aliases:` entries — synpp resolves a stage by walking the alias map exactly **one** step.
* Some Bavaria modules are still authoritative for BS (e.g. `bavaria.ipf.model`, `bavaria.ipf.attributed`, `bavaria.gravity.distance_matrix`, `bavaria.locations.education`, `bavaria.locations.synthesis.replacement`).
* Other Bavaria modules are pure deadweight for BS (`bavaria.data.mvg.zones`, `bavaria.data.population.raw`, `bavaria.analysis.zones`, `bavaria.data.spatial.iris` for France).
* Java side currently references `org.eqasim.bavaria.*` classes from `matsim/runtime/` and `matsim/simulation/*.py`.
* Eleven bugs (BUG-001 .. BUG-011) from the IPF audit are still open: residency flag mismatch, household member grouping corruption, commune_id leading-zero loss, NaN propagation, RNG non-determinism, encoding errors, INKAR merge NaNs, unsorted household formation, etc.
* `README.md` still describes "An open synthetic population of Bavaria" and links to `docs/teaser.png`.

## Target State

* **One** region package, `braunschweig/`, mirroring the directory layout of `eqasim-bavaria` (`braunschweig/data/...`, `braunschweig/ipf/...`, `braunschweig/locations/...`, `braunschweig/gravity/...`, `braunschweig/synthesis/...`, `braunschweig/matsim/...`).
* Generic, region-neutral helpers that we kept verbatim from upstream (e.g. distance-matrix maths, ENTD-code parser, replacement-stage logic, `osmconvert` wrapper, generic IPF solver) live in a thin `eqasim_common/` Python package — explicitly tagged in their module docstrings as **"unmodified from eqasim / eqasim-bavaria"** with the upstream commit hash.
* All `bavaria.*` synpp stage names are removed from BS configs; aliases shrink to the minimum needed (region overrides only). The old `bavaria.*` stage names exist only as **deprecation shims** during Phase 2 (a single conditional re-export so the cache remains warm during the migration), then disappear in Phase 4.
* Every Bavaria-specific config (`config.yml`, `config_bavaria.yml`, `config_local_bavaria.yml`) is **deleted**. The repo ships only the Braunschweig configs (1 %, 10 %, 25 %) plus a `config_dryrun_braunschweig.yml` for CI smoke tests.
* `README.md` is rewritten in English and ships a complete download checklist for: Zensus 2022 (5000H-2001, 1000A-2081, 1000A-3082, 100 m grid), GENESIS (12111-0001, 13111-06-02-4, 13111-01-03-5), MiD 2023 regional B3 export (infas sample 7555 = Großraum Braunschweig), BA Pendleratlas 2025, INKAR full panel + household-income XLS, BBSR RegioStaR-7, ALKIS Hausumringe (LGLN NDS), ATKIS Landuse, OSM Niedersachsen PBF, GTFS feed.
* The eleven open bugs are fixed inline as we touch each module — no separate bug-fix branch.
* `tests/` grows to cover: every `braunschweig.data.*` loader (schema + ID normalisation), the IPF post-margin validation, the gravity Kreis-totals invariance, the home density-weighting determinism, and a full 1 % end-to-end smoke run.
* `docs/codebase/` is populated by the **acquire-codebase-knowledge** skill (STACK / STRUCTURE / ARCHITECTURE / CONVENTIONS / INTEGRATIONS / TESTING / CONCERNS).
* `quality/` is populated by the **quality-playbook** skill (QUALITY.md, RUN_CODE_REVIEW.md, RUN_INTEGRATION_TESTS.md, RUN_SPEC_AUDIT.md, AGENTS.md).

## Affected Files

Full inventory recorded in this plan's appendix below; key deltas:

| File / Folder                                          | Change Type | Notes                                                                 |
|--------------------------------------------------------|-------------|-----------------------------------------------------------------------|
| `bavaria/ipf/`                                          | move        | → `braunschweig/ipf/` (model.py, prepare.py, attributed.py)            |
| `bavaria/gravity/distance_matrix.py`                    | move        | → `eqasim_common/gravity/distance_matrix.py` (region-neutral)          |
| `bavaria/gravity/model.py`                              | delete      | superseded by `braunschweig/gravity/model.py`                           |
| `bavaria/locations/{home,work,secondary,education,synthesis}` | move/merge | → `braunschweig/locations/...`; BS overrides absorb the bavaria base   |
| `bavaria/synthesis/population/enriched.py`              | move        | → `braunschweig/synthesis/population/enriched.py` (already wrapped today) |
| `bavaria/data/buildings.py`, `bavaria/data/osm/*`       | move        | → `eqasim_common/data/osm/*` (region-neutral utilities)                |
| `bavaria/data/mid/{data,zones}.py`                      | delete      | BS forks already replace these                                          |
| `bavaria/data/census/*` (all)                           | delete      | superseded by `braunschweig/data/census/*`                              |
| `bavaria/data/mvg/zones.py`                             | delete      | Munich-specific, never used by BS                                       |
| `bavaria/data/population/{raw,municipalities}.py`       | delete      | French ENTD specific                                                    |
| `bavaria/data/spatial/iris.py`                          | delete      | France-only (replace usage with `braunschweig.data.spatial.zgb`)        |
| `bavaria/data/spatial/codes.py`, `bavaria/entd_codes.py`| move        | → `eqasim_common/spatial/` (kept as generic AGS/ARS helper)             |
| `bavaria/analysis/zones.py`                             | delete      | French IRIS-only                                                        |
| `bavaria/matsim/simulation/prepare.py`                  | move        | → `braunschweig/matsim/simulation/prepare.py`; rename Java references   |
| `bavaria/income.py`                                     | move        | → `braunschweig/synthesis/income.py` (zero-income placeholder)          |
| `bavaria/homes.py`                                      | move        | → `braunschweig/synthesis/spatial/home_zones.py`                        |
| `config.yml`, `config_bavaria.yml`, `config_local_bavaria.yml` | delete | not part of the scope of this fork anymore                              |
| `config_corsica.yml`, `config_lyon.yml`, `config_nantes.yml`, `config_toulouse.yml`, `config_tum.yml` | delete | upstream eqasim region configs |
| `config_local_braunschweig*.yml`                        | rewrite     | drop all `bavaria.*` aliases, switch keys to `braunschweig.*`           |
| `config_dryrun_braunschweig.yml`                        | rewrite     | only kept config; dry-run / CI                                          |
| `README.md`                                             | rewrite     | English, BS-only, full input-data download checklist                   |
| `CITATION.cff`, `CHANGELOG.md`                          | rewrite     | retitle, summarise the migration                                        |
| `tests/test_pipeline.py`, `tests/test_determinism.py`   | rewrite     | use the BS configs / data instead of IDF region 10/11                   |
| `tests/test_simulation.py`, `tests/testdata.py`         | review/delete | replace with BS-shaped testdata helpers                                 |
| `matsim/simulation/run.py`, `matsim/simulation/prepare.py` | modify     | Java class names: `org.eqasim.bavaria.*` → see Decision D-1 below        |
| `analysis/`                                             | audit       | drop `bavaria.data.spatial.iris` reference, retarget to `braunschweig.*` |
| `scripts/`                                              | audit       | rename comment / config references; verify each script still runs       |
| `docs/codebase/*.md`                                    | create      | via acquire-codebase-knowledge skill                                    |
| `quality/*.md`                                          | create      | via quality-playbook skill                                              |
| `eqasim-data/data/bavaria/`                             | delete      | only contains a `.gitkeep`                                              |

## Decisions Required Before Execution

> **The user must answer these before Phase 1 starts.** Each decision is small but has high blast-radius.

* **D-1 — Java side.** The MATSim runner imports `org.eqasim.bavaria.RunSimulation`, `org.eqasim.bavaria.BavariaConfigurator`, `org.eqasim.bavaria.scenario.RunAdaptConfig`, `org.eqasim.bavaria.scenario.AddTransitZoneInformation`. Three options:
  * **D-1a:** Keep referencing `org.eqasim.bavaria.*` classes from the published `eqasim-java` JAR, document that "the Java layer still uses Bavaria's class names but the Braunschweig scenario data feeds them just fine".
  * **D-1b:** Fork `eqasim-java`, add an `org.eqasim.braunschweig` package mirroring the four classes (one-line subclasses), publish a custom JAR.
  * **D-1c:** Postpone — this refactor stays Python-only; the Java rename is tracked as a follow-up issue.
  * **Recommendation:** **D-1c**. The Java classes are scenario-agnostic; renaming them is cosmetic and doubles the surface area of this PR.
* **D-2 — Bavaria & IDF deletion.** Are we OK to **delete** `bavaria/`, `config.yml`, `config_bavaria.yml`, `config_local_bavaria.yml`, `config_corsica.yml`, `config_lyon.yml`, `config_nantes.yml`, `config_toulouse.yml`, `config_tum.yml`? Git history preserves them.
  * **Recommendation:** **Yes, delete.** Per user message ("alles ausm bavarai-ansatz übernommen … aber jetzt rein mit unserem bs beispiel").
* **D-3 — `eqasim_common/` package.** Do we put generic helpers under a new `eqasim_common/` top-level package, or fold them into `braunschweig/_lib/`?
  * **Recommendation:** `eqasim_common/`. Future regional forks (Hannover, Hamburg, ...) can reuse it without re-extracting.
* **D-4 — Cache strategy.** synpp content-hashes module paths. The rename will invalidate `eqasim-data/cache_bs*` entirely. Approve a one-time full re-run of all 1 % / 10 % / 25 % pipelines after Phase 4?
  * **Recommendation:** **Yes**, re-run. The 1 % takes ~12 min on this machine; 10 % ~45 min; 25 % ~90 min — acceptable.
* **D-5 — Bug fixes.** Fix all 11 documented bugs as part of this refactor, or defer some?
  * **Decision (2026-04-27): DEFER ALL.** This refactor is move + rename + document only. No behavioural change. Bugs tracked in memory; addressed in a follow-up branch.

## Execution Plan

The plan is **strictly phased**. Each phase ends with a verification gate; the next phase does not start until the gate is green.

### Phase 0 — Foundation & inventory (no code change)

* [ ] 0.1 Apply the **acquire-codebase-knowledge** skill → produce `docs/codebase/{STACK,STRUCTURE,ARCHITECTURE,CONVENTIONS,INTEGRATIONS,TESTING,CONCERNS}.md`. Reference real file paths and line numbers.
* [ ] 0.2 Snapshot the current repo: `git switch -c refactor/braunschweig-clean-fork`. Tag baseline as `pre-refactor-2025-04-26`.
* [ ] 0.3 Run `pytest tests/ -x -q` and the 1 % smoke pipeline; record results into `plan/refactor-baseline.txt`. This is the regression target.
* [ ] 0.4 Apply the **quality-playbook** skill → produce `quality/QUALITY.md` and `quality/AGENTS.md`. The functional-test, code-review, integration-test, and spec-audit deliverables go in afterwards (Phase 3).
* [ ] **Verify:** all baseline metrics captured; branch + tag exist.

### Phase 1 — Skeleton: types, interfaces, package layout

* [ ] 1.1 Create `eqasim_common/` with `__init__.py` describing the package's purpose ("region-neutral helpers carried over verbatim from eqasim / eqasim-bavaria; do not put region-specific logic here").
* [ ] 1.2 Create `eqasim_common/{gravity,data/osm,spatial}/` empty package skeletons with English module docstrings.
* [ ] 1.3 Create `braunschweig/{ipf,gravity,locations,synthesis,matsim}/` skeletons (where they don't exist yet). Add a top-level `braunschweig/REGION.md` that lists ZGB-8 ARS-5 codes (`03101`, `03102`, `03103`, `03151`, `03153`, `03154`, `03157`, `03158`) — single source of truth, imported elsewhere instead of being repeated.
* [ ] 1.4 Update `setup.py` / `pyproject.toml` (whichever is authoritative — verify) to declare `eqasim_common` and `braunschweig` packages. Drop `bavaria` from packages.
* [ ] 1.5 Define `braunschweig/_typing.py` with the canonical type aliases used throughout (`CommuneId = str`, `KreisId = str`, `HouseholdId = int`, `PersonId = int`, `Weight = float`).
* [ ] **Verify:** `python -c "import braunschweig, eqasim_common"` succeeds; `pytest -q` still passes (no behaviour change yet).

### Phase 2 — Implementation: move + adapt

Each step is a **single-purpose commit**. Order is dependency-driven.

* [ ] 2.1 Move generic OSM utilities (`bavaria/data/osm/{osmconvert,chunked}.py`) → `eqasim_common/data/osm/`. Add a deprecation re-export at the old path (`from eqasim_common.data.osm import *  # type: ignore`). Update `braunschweig/data/locations.py` and the BS config to import from the new path.
* [ ] 2.2 Move `bavaria/gravity/distance_matrix.py` → `eqasim_common/gravity/distance_matrix.py`. Add a deprecation re-export at the old path.
* [ ] 2.3 Move `bavaria/entd_codes.py` and `bavaria/data/spatial/codes.py` → `eqasim_common/spatial/`. Rename the synpp stage from `bavaria.entd_codes` to `eqasim_common.spatial.codes`. Update the alias (`data.spatial.codes`).
* [ ] 2.4 Move `bavaria/locations/synthesis/replacement.py` and `bavaria/locations/synthesis/education.py` → `eqasim_common/locations/synthesis/`. Update aliases.
* [ ] 2.5 Move `bavaria/locations/education.py` → `eqasim_common/locations/education.py` (it is OSM-tag based, region-neutral; keep the docstring "Inherited from eqasim-bavaria; no Braunschweig-specific changes needed.").
* [ ] 2.6 Move `bavaria/ipf/{model,prepare,attributed}.py` → `braunschweig/ipf/`. Rename all `bavaria.ipf.*` config keys to `braunschweig.ipf.*` (`use_household_size_margin`, `use_household_type_margin`, `use_employment_margin`, `dirichlet_prior_strength`, `margin_validation_tolerance`). Add a one-shot config-key migration helper in `braunschweig/_config_compat.py` that warns + remaps for one minor version. Inside each file, leave a docstring header:
  ```
  Origin: eqasim-bavaria @ <commit>, file <bavaria/ipf/...>.
  Adapted for Braunschweig: <bullet list of substantive deviations>
  ```
* [ ] 2.7 Move `bavaria/locations/home.py`, `bavaria/locations/work.py`, `bavaria/locations/secondary.py`, `bavaria/locations/education.py` (the parts not already moved) → `braunschweig/locations/`. **Merge** the BS-specific wrappers (`braunschweig/locations/home.py`, `braunschweig/locations/work.py`) into the new files — no more delegation through `bavaria.locations.*`. Add explicit `# --- Inherited from eqasim-bavaria ---` and `# --- Braunschweig-specific ---` comment fences inside each file.
* [ ] 2.8 Move `bavaria/synthesis/population/enriched.py` and `braunschweig/synthesis/population/enriched.py` → a single `braunschweig/synthesis/population/enriched.py`. **Behaviour-preserving relocation only** — keep the existing wrapper-around-wrapper structure intact (cleaning it up is a follow-up). BUG-001 stays untouched.
* [ ] 2.9 Move `bavaria/income.py` → `braunschweig/synthesis/income.py`; rename to clarify it's the zero-income placeholder. Note in docstring this is a deliberate stand-in until BS gets MiD H4-derived household income (already partially in `braunschweig.data.census.household_income`; clarify which stage feeds the simulation).
* [ ] 2.10 Move `bavaria/homes.py` → `braunschweig/synthesis/spatial/home_zones.py`. No code change beyond the relocation.
* [ ] 2.11 Move `bavaria/gravity/model.py` content into `braunschweig/gravity/model.py` (which currently wraps it). Inline the calls; eliminate the indirection. Behaviour-preserving — no bug fix here.
* [ ] 2.12 Move `bavaria/matsim/simulation/prepare.py` → `braunschweig/matsim/simulation/prepare.py`. Update `matsim/simulation/{run,prepare}.py` per Decision D-1 (default: leave Java class names as-is, add a code comment explaining why).
* [ ] 2.13 Update `synthesis/population/sampled.py` to reference the new IPF stage name. **No bug fixes** — relocation/rename only.
* [ ] 2.14 Rewrite `config_local_braunschweig.yml`, `config_local_braunschweig_10pct.yml`, `config_local_braunschweig_25pct.yml`: drop every `bavaria.*` alias, rename `bavaria.*` config keys to `braunschweig.*`. Aliases shrink to **only** the upstream `synthesis.*` overrides. Add a header comment block summarising what the config does, ZGB-8 scope, and the data download checklist link.
* [ ] 2.15 Rewrite `config_dryrun_braunschweig.yml` to a 0.1 % CI dry run that exercises every stage; add it to the test suite (Phase 3).
* [ ] 2.16 Delete `bavaria/`, `config.yml`, `config_bavaria.yml`, `config_local_bavaria.yml`, `config_corsica.yml`, `config_lyon.yml`, `config_nantes.yml`, `config_toulouse.yml`, `config_tum.yml`, `eqasim-data/data/bavaria/` (per Decision D-2).
* [ ] 2.17 Sweep `analysis/`, `documentation/`, `scripts/`: rename every remaining `bavaria.*` reference. Run `grep -r "bavaria" --include="*.py"` afterwards — only docstring/history references should remain (and only when explaining provenance).
* [ ] 2.18 Sweep `eqasim-data/cache_bs*`: delete. Caches are content-hashed against module paths and will all miss.
* [ ] **Verify after each step 2.x:** `pytest -q tests/test_braunschweig_data.py` passes; `python -m synpp config_dryrun_braunschweig.yml` reaches the affected stage without error. After 2.16, run the 1 % smoke pipeline; compare the household / person / activity / trip counts against the Phase-0 baseline; equal up to RNG noise (≤0.5 %).

### Phase 3 — Tests, docs, quality playbook

* [ ] 3.1 Rewrite `tests/test_pipeline.py` and `tests/test_determinism.py` to drive the `config_dryrun_braunschweig.yml`. Drop IDF region 10/11 fixtures.
* [ ] 3.2 Add per-stage unit tests under `tests/braunschweig/`:
  * `test_data_census_population.py` (urbistat scrape parsing, 11-class age scheme)
  * `test_data_census_employment.py` (GENESIS 13111-06-02-4 schema)
  * `test_data_census_employees.py` (SvB Arbeitsort schema)
  * `test_data_census_household_size.py` (Zensus 5000H-2001 SIZE_BINS)
  * `test_data_census_households_type.py` (Zensus 1000A-2081 HSHTP1_TYPE)
  * `test_data_mid_data.py` (P13/P17 CDF builder shape)
  * `test_data_ba_pendler_detailed.py` (Kreis-totals invariance)
  * `test_data_zensus_grid.py` (100 m grid join determinism)
  * `test_ipf_post_margin_validation.py` (the validation guard added in BUG-009 fix)
  * `test_gravity_kreis_totals.py` (BA Pendler totals preserved within tolerance)
  * `test_locations_home_density.py` (density-weighted sampling determinism)
* [ ] 3.3 Add an end-to-end smoke test: `tests/test_smoke_1pct.py` that runs `config_dryrun_braunschweig.yml` and asserts on `households.csv`, `persons.csv`, and the hh_type share envelope (single ≈ 43 ± 2 %, couple ≈ 27 ± 2 %, …).
* [ ] 3.4 Apply the **quality-playbook** skill → produce the four "RUN_*.md" deliverables.
* [ ] 3.5 Apply the **doublecheck** skill against the new README's data-source URLs and licences.
* [ ] **Verify:** `pytest -q` is green; `pytest -q tests/test_smoke_1pct.py` passes (slow; mark `@pytest.mark.slow`).

### Phase 4 — Cleanup, README, release

* [ ] 4.1 Rewrite `README.md`. Sections: Overview, Region scope (ZGB-8), Quickstart (clone, conda env, run 1 %), **Input data — download checklist** (one row per dataset: name, source URL, license, target path under `eqasim-data/`, preprocessing script if any), Pipeline architecture (mirror `eqasim-bavaria`'s diagram, reuse `mermaid` block), How Braunschweig differs from eqasim-bavaria (table), Calibration & validation, Known limitations, Citation.
* [ ] 4.2 Rewrite `CITATION.cff` and `CHANGELOG.md`. Tag a `v0.1.0-bs` release commit.
* [ ] 4.3 Drop deprecation re-exports added in 2.1/2.2/2.3 (one minor version is the deal). Run `grep -r "bavaria" .` — every remaining hit must be in docstrings/comments only and must be intentional ("Inherited from eqasim-bavaria @ <commit>").
* [ ] 4.4 Final smoke run on 1 % / 10 % / 25 % configs. Compare against Phase-0 baseline metrics.
* [ ] 4.5 Update `/memories/repo/eqasim-bs.md` with the new module layout. Move the 11-bug audit to `docs/codebase/CONCERNS.md` with each bug marked FIXED / DEFERRED.
* [ ] **Verify:** all three smoke pipelines green; metrics within tolerance; documentation links resolve; tests pass; `pip install -e .` works.

## Rollback Plan

Each phase ends with a tagged commit. If a phase fails:

1. `git restore --source=<phase-N-1-tag> --staged --worktree .` — restores the working tree.
2. Delete the cache directory created during the failed phase: `Remove-Item -Recurse -Force eqasim-data/cache_bs_refactor`.
3. Re-run the previous phase's smoke pipeline; confirm baseline metrics are restored.
4. Open a postmortem note in `plan/refactor-postmortem.md` describing the failure, then iterate.

The key invariant: the `pre-refactor-2025-04-26` tag must remain reachable until Phase 4 verification is signed off.

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| synpp cache invalidation forces multi-hour re-run | High | Medium | Schedule the long re-run overnight after Phase 2.18; gate Phase 3 on its completion. |
| Java MATSim layer breaks because class names move | Low (Decision D-1c keeps them) | High if it does | Phase 2.12 explicitly defers; integration test in Phase 3.3 catches regressions early. |
| Some Bavaria stage was secretly load-bearing | Medium | High | The Phase-0 inventory (this document, Section "Affected Files") plus the explicit 2.x-step verification gates surface this. The deprecation shims (2.1–2.3) buy us one cycle to discover hidden imports. |
| Bug fix during a move introduces a regression | Medium | Medium | Each move is a separate commit; bug fix lands in a follow-up commit on the same file so `git bisect` works. |
| `config_local_braunschweig*.yml` rewrite breaks an existing user's run | Low (single-user repo right now) | Medium | Keep the old config as `config_local_braunschweig_legacy.yml` for one minor version with a deprecation banner. |
| README data-source URL rot (Zensus / GENESIS / INKAR endpoints change) | Medium | Low | Capture the URLs + access date + a SHA-256 of each downloaded file in `eqasim-data/DOWNLOAD_CHECKLIST_BS.md`. |
| RNG seed migration changes 25 % output marginally | Medium | Low | Phase 4.4 compares totals, not byte-for-byte equality. Tolerance bands documented in `tests/test_smoke_1pct.py`. |
| User actually wants to keep Bavaria support | Medium | Hi | **Decision D-2 above resolves this before any deletion.** |

## Appendix A — Bug fix mapping

Per Decision **D-5**, **all 11 bugs are deferred** to a follow-up branch. They remain documented in `/memories/session/ipf-braunschweig-analysis.md`. The refactor is strictly behaviour-preserving so the bug surface — and its baseline outputs — must be identical pre- and post-refactor.

---

**Shall I proceed with Phase 0 (foundation & inventory — no code changes yet, just `docs/codebase/`, baseline snapshot, branch tag, and the quality-playbook skeleton)?**

Please answer the five Decisions D-1 .. D-5 above (defaults: D-1c, D-2 yes, D-3 `eqasim_common/`, D-4 yes, D-5 inline-fix BUG-001..006). If you confirm the defaults I will proceed with Phase 0; Phases 1–4 will land as separate, individually verified commits, each with its own smoke-test gate.
