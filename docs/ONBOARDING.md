# Onboarding — eqasim-bs

> A durable entry point for a new contributor (human or a fresh Claude session) to pick up
> the work with full context. For the *live* feature/branch state see
> [../PROJECT_STATUS.md](../PROJECT_STATUS.md); for the *why* behind each decision see
> [DECISIONS.md](DECISIONS.md); for binding rules + deep feature detail see
> [../CLAUDE.md](../CLAUDE.md) and [features/](features/).

## 1. What this project is

**eqasim-bs** is a scientific transport-simulation pipeline for the **Zweckverband
Großraum Braunschweig (ZGB)** region (8 Kreise, Niedersachsen), built on **MATSim** +
**eqasim**. A Python **synpp** pipeline synthesises a population, assigns
activities/locations, exports a MATSim scenario; the Java **MATSim** layer runs mode
choice + traffic; analysis validates against real reference data (**MiD 2023**, **Zensus
2022**, **KBA**, **INKAR**, **BA Pendleratlas**, **Destatis**).

It is a fork of `eqasim-org/eqasim-bavaria` — see [UPSTREAM_DELTA.md](UPSTREAM_DELTA.md)
for exactly what eqasim-bs adds.

Core principle (from `CLAUDE.md`): **research software** — correctness, reproducibility,
traceability, no silent fallbacks, no invented reference values, everything flag-gated
and configurable. Chat with the maintainer is in **German**; all code/comments/docs are
**English**.

## 2. Pipeline shape

```
config_*.yml → scripts/run_synpp.py → synthesis.output → matsim.output → RunSimulation (Java) → analysis/ + simwrapper/
```

- **Data layer** (`braunschweig/data/...`): reference tables (CSV under
  `eqasim-data/data/braunschweig/mid/` — numeric references are CSV files, never Python
  literals), census, BBSR RegioStaR-7, schools/kitas/unis, cordon, freight inputs.
- **Synthesis** (`braunschweig/{synthesis,ipf,popsim}/...`): IPF / PopulationSim household
  + person synthesis, attribute enrichment, location assignment (home, work via gravity,
  education via gravity).
- **MATSim** (`braunschweig/matsim/...` + Java in `../eqasim-java-bs`): scenario prep,
  cordon cut, freight injection, simulation, mode choice.
- **Analysis** (`braunschweig/analysis/...`): dashboards, MiD validation, population
  validation, SimWrapper export.

## 3. Environment facts (easy to get wrong)

- **Conda env `eqasim`** runs the pipeline (NOT `.venv`/base); set `PYTHONUTF8=1` when piping.
- **Run server**: a Linux box (64c/128GB) for 100% runs; conda env + ~13 GB data synced.
  Connection details and bootstrap/run/monitor scripts live in the Claude memory
  (`server-deployment.md`) — not hard-committed here.
- **`eqasim-data/` is gitignored / local-only** (~13 GB: MiD SUF, ALKIS, freight + Germany
  network, GTFS). The committed exception is the small MiD reference CSVs under
  `eqasim-data/data/braunschweig/mid/`. The tree is NOT in any repo — copy it separately
  when moving machines.
- **Java**: the pipeline builds our own `braunschweig` module in `../eqasim-java-bs` via
  `eqasim_source_path` (not the upstream bavaria clone). MATSim version `2025.0-PR3568`.
- **CRS**: project-wide metric CRS is **EPSG:25832**. Never compute metric distances in WGS84.
- Local pytest can fail on `matsim` namespace shadowing (system Python has PyPI
  `matsim-tools` which shadows the repo-local `matsim/`); the canonical suite runs on the
  server.

## 4. How to run

```powershell
# local (Windows, conda env eqasim)
python scripts/run_synpp.py config_local_braunschweig.yml

# server (Linux)
bash scripts/run_pipeline.sh config_server_braunschweig_100pct.yml
```

`run_pipeline.sh` first runs `scripts/verify_braunschweig_inputs.py --matsim` as a
preflight gate (fail-fast checklist of all required input data, with download sources
for anything missing); set `EQASIM_SKIP_VERIFY=1` to skip it when missing inputs are
known to be served from cached stages.

A **1% smoke** is the intended fast end-to-end test vehicle before claiming a stage works
(mocked unit tests miss real wiring bugs). Run analysis with
`python -m braunschweig.analysis.run_full_analysis --output-dir ... --sim-cache ...`.

## 5. Where everything lives (doc map)

| Doc | Purpose |
|---|---|
| `CLAUDE.md` | Binding rules + working discipline (authoritative) |
| `docs/features/*` | Deep per-feature detail (split out of CLAUDE.md) |
| `PROJECT_STATUS.md` | At-a-glance feature/branch dashboard (live state) |
| `PROJECT_BACKLOG.md` | Ranked open/partial/dropped work |
| `docs/DECISIONS.md` | ADR log — the *why*, commit/PR-linked, back to the bavaria baseline |
| `RUNS.md` | Simulation run ledger |
| `docs/UPSTREAM_DELTA.md` | What eqasim-bs adds vs. eqasim-bavaria |
| `CONTRIBUTING.md` | The canonical feature workflow + human contract |
| `docs/codebase/` | Architecture, structure, stack, conventions, integrations, testing, concerns |
| `SESSION_LOG.md` | Chronological work log (gitignored / local) |
| Claude memory (`~/.claude/.../memory/`) | Curated long-term facts (travels with `~/.claude`, not the repo) |

## 6. Moving to a new machine / account

1. Clone `TUBS-IVS/eqasim-bs` **and** `TUBS-IVS/eqasim-java-bs` (wired via `eqasim_source_path`).
2. Copy `eqasim-data/` separately (gitignored, ~13 GB).
3. Copy the Claude memory `~/.claude/projects/<project-dir>/memory/`; rename the project
   dir to match the new path if needed. Do NOT copy `~/.claude/.credentials.json` — log in fresh.
4. Recreate the conda env `eqasim`.
