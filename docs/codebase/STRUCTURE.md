# Codebase Structure

> Focus areas only. Other paths marked `[TODO]`.

## Core Sections (Required)

### 1) Top-Level Map

| Path | Purpose | Evidence |
|------|---------|----------|
| [braunschweig/](braunschweig/) | **Primary region package**. Overrides and extensions to Bavaria stages. Includes data loaders, calibrated gravity, location assignment, synthesis extensions. | Scan; [braunschweig/__init__.py](braunschweig/__init__.py) |
| [bavaria/](bavaria/) | **Read-only Bavaria fork base** (upstream commit b20fbe6). Stages here are authoritative for generic steps (IPF solver, distance matrix, replacement location logic, ENTD-code parser). Refactor scheduled: Phase 2 extracts region-neutral code to `eqasim_common/`, Phase 4 deletes this directory. | [plan/refactor-eqasim-bs.md](plan/refactor-eqasim-bs.md#D-2); scan |
| [eqasim-data/](eqasim-data/) | **Working directory root** for synpp caches and outputs. Subdirectories: `data/` (input CSVs/GeoPackages), `cache/` / `cache_bs*` (stage outputs, content-hashed), `output_bs*` (final synthesis CSVs + MATSim XML). Non-committed except for baseline snapshots in `cache_bs/` and `cache_bs_10pct/`. | [config_local_braunschweig.yml](config_local_braunschweig.yml#L3-L5) |
| [scripts/](scripts/) | Utility scripts: calibration analysis, commute distance inspection, MiD table extraction, validation harness. Key: [scripts/validate_bs_10pct/](scripts/validate_bs_10pct/) produces 17 plots + HTML + JSON from the 10% cache. | Scan; [scripts/](scripts/) listing |
| [tests/](tests/) | pytest suite: [test_braunschweig_data.py](tests/test_braunschweig_data.py) (data loaders), [test_hh_size_margin.py](tests/test_hh_size_margin.py) (IPF margin), [test_pipeline.py](tests/test_pipeline.py) (full DAG smoke test), [test_determinism.py](tests/test_determinism.py) (RNG reproducibility). Baseline snapshot: [plan/baselines/](plan/baselines/). | [tests/](tests/); [.github/workflows/tests.yml](.github/workflows/tests.yml) |
| [docs/](docs/) | Documentation: [docs/population.md](docs/population.md) (pipeline overview), [docs/simulation.md](docs/simulation.md) (MATSim setup), [docs/codebase/](docs/codebase/) (technical specifications). | Directory listing |
| [plan/](plan/) | Project planning and analysis artifacts: [plan/refactor-eqasim-bs.md](plan/refactor-eqasim-bs.md) (this refactor), [plan/calibration-analysis-2025.md](plan/calibration-analysis-2025.md) (IPF audit + KPI analysis), [plan/baselines/](plan/baselines/) (test output baselines). | [plan/](plan/) listing |
| [.github/](. github/) | CI/CD and agent/skill definitions. Workflows: [.github/workflows/tests.yml](.github/workflows/tests.yml), [.github/workflows/data.yml](.github/workflows/data.yml). Skills for acquisition, refactoring, quality playbooks. | [.github/workflows/](. github/workflows/); [.github/skills/](.github/skills/) |
| [config_local_braunschweig.yml](config_local_braunschweig.yml) | **1% dev config** (sampling_rate: 0.01, seed: 1234). Current active config for development. | File |
| [config_local_braunschweig_10pct.yml](config_local_braunschweig_10pct.yml) | **10% baseline config** (113,973 persons in synthesis output). Used for validation harness and KPI measurement. | File |
| [config_local_braunschweig_25pct.yml](config_local_braunschweig_25pct.yml) | **25% config** for MATSim simulation (full ZGB-8 population @ 25% sample). | File |
| [environment.yml](environment.yml) | Conda environment specification. Pinned versions for reproducibility. | [environment.yml](environment.yml) |
| [README.md](README.md) | **Out-of-date**. Currently describes Bavaria; refactor Phase 3 rewrites in English for Braunschweig. | [README.md](README.md); [plan/refactor-eqasim-bs.md](plan/refactor-eqasim-bs.md#target-state) |
| [VERSION](VERSION) | Current version string. [TODO] confirm format and publishing workflow. | File |
| [CHANGELOG.md](CHANGELOG.md) | Version history. [TODO] reconcile with git log and refactor milestones. | File |

### 2) Entry Points

- **Main runtime entry**: `python -m synpp config_local_braunschweig.yml` — reads YAML config, stages specified in `run:` list, executes DAG with content-hashing cache in `working_directory`.
- **Alternative configs**: `python -m synpp config_local_braunschweig_10pct.yml` (validation run), `python -m synpp config_local_braunschweig_25pct.yml` (MATSim run).
- **Validation harness**: `python -m scripts.validate_bs_10pct` — loads cached 10% synthesis, generates metrics and plots.
- **Test entry**: `pytest tests/ -v` — discovers all test modules in [tests/](tests/).
- **How entry is selected**: Synpp stage name (e.g. `synthesis.output`) resolved via `configure(context)` callbacks that register stages with synpp. BS configs use `aliases:` to remap Bavaria stage names to BS equivalents.

### 3) Module Boundaries

| Boundary | What belongs here | What must not be here |
|----------|-------------------|------------------------|
| [braunschweig/data/](braunschweig/data/) | **Data loaders for ZGB-8 scope**: Zensus 2022 population/employment/HH-size/HH-type marginals (per-Gemeinde), BA Pendleratlas commuter flows, MiD 2023 regional sample, INKAR household income, BBSR RegioStaR-7, external workplace distributions, ALKIS building polygon intersections, OSM/INSPIRE landuse. Each module exposes `configure(context)` and `execute(context)`. | Synthesis logic; location assignment; trip generation. |
| [braunschweig/gravity/](braunschweig/gravity/) | **Gravity model calibration**. Wraps `bavaria.gravity.model` (Gemeinde-level gravity), applies IPF to rescale against BA Kreis-pair flows, injects external commuter pools from [braunschweig/data/external_workplaces.py](braunschweig/data/external_workplaces.py). Output: per-commune Kreis-aggregate trip distribution (OD matrix). | Specific location choice (that happens in `synthesis.population.spatial.locations.work`). |
| [braunschweig/locations/](braunschweig/locations/) | **Location assignment for work and education**. Extends `bavaria.locations.work` to handle external workplace pool. Reads gravity OD matrix, samples commute destinations, draws home-based locations (shop/leisure/education via density weighting). | IPF; gravity calibration; trip generation. |
| [braunschweig/synthesis/](braunschweig/synthesis/) | **Post-synthesis customization**: MiD P13 commute-distance override, household type sampling, vehicle assignment overrides. | Data loading; IPF; location assignment. |
| [synthesis/](synthesis/) | **Generic synthesis layer** (inherited from upstream `eqasim`). Stages for person/household sampling, trip generation, activity assignment, vehicle assignment. Called by BS overrides. | Region-specific data sources; calibration. |
| [bavari/](bavaria/) | **Bavaria fork base** (upstream, read-only per CON-001). Stages for generic IPF, distance matrices, replacement location logic, ENTD parsing, home location density weighting. These are authoritative and used by BS in several paths. | Region-specific customization; BS-only data loaders. |
| [scripts/validate_bs_10pct/](scripts/validate_bs_10pct/) | **Validation harness**: Loads cache, extracts KPIs (population, commute distance, HH-size distribution, trip purpose, mode share), produces plots and HTML report. Regression guard for future runs. | New synthesis logic; data loading (consumes existing output). |

### 4) Naming and Organization Rules

- **File naming**: `snake_case.py` for modules (e.g. [braunschweig/data/census/household_size.py](braunschweig/data/census/household_size.py)).
- **Directory organization**: **domain-based** at top level (`braunschweig/`, `bavaria/`, `synthesis/`), then **layer-based** within (e.g. `braunschweig/data/`, `braunschweig/gravity/`, `braunschweig/synthesis/`).
- **Synpp stage naming**: Dotted module path (e.g. `braunschweig.gravity.model`, `braunschweig.data.census.pendler`). Each module exports `configure(context)` and `execute(context)`.
- **BS overrides keep Bavaria namespace**. Example: `bavaria.data.census.household_size` is replaced by `braunschweig.data.census.household_size` in the config aliases map, but the stage name stays within the braunschweig namespace to avoid circular references.
- **Import aliasing**: None (all relative imports work within package tree).
- **Public exports/barrel policy**: Each module is a direct entry point via synpp; no `__all__` barrel export needed.

### 5) Evidence

- [braunschweig/__init__.py](braunschweig/__init__.py), [bavaria/__init__.py](bavaria/__init__.py) — package markers
- [config_local_braunschweig.yml](config_local_braunschweig.yml#L6-L8) — `run:` stage list and `aliases:` override map
- [braunschweig/gravity/model.py](braunschweig/gravity/model.py#L41-L46) — exemplar `configure()` / `execute()` signatures
- Directory listings via workspace scan

---
