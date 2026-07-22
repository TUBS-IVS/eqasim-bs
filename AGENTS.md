# Agent Bootstrap — eqasim-bs (Braunschweig)

> Read this file first.  It is the single entry point for any AI session
> working on this repository.  It points at the longer-form docs rather than
> duplicating them.

## Mission in one paragraph

`eqasim-bs` synthesises a Braunschweig (ZGB-8) population for the MATSim
agent-based transport simulator.  It started as a fork of
[eqasim-bavaria](https://github.com/eqasim-org/eqasim-bavaria) and is being
refactored into a clean Braunschweig project with shared region-neutral code
extracted into a new `eqasim_common/` package.  The current branch
`refactor/braunschweig-clean-fork` is executing the plan recorded in
[`plan/refactor-eqasim-bs.md`](plan/refactor-eqasim-bs.md).

## Read order for any new session

1. [`plan/refactor-eqasim-bs.md`](plan/refactor-eqasim-bs.md) — current refactor phase, decisions D-1..D-5.
2. [`quality/QUALITY.md`](quality/QUALITY.md) — fitness-to-purpose scenarios and coverage targets.
3. [`docs/codebase/STACK.md`](docs/codebase/STACK.md) — runtime versions and key commands.
4. [`docs/codebase/STRUCTURE.md`](docs/codebase/STRUCTURE.md) — package layout and entry points.
5. [`docs/codebase/ARCHITECTURE.md`](docs/codebase/ARCHITECTURE.md) — synpp DAG flow.
6. [`docs/codebase/CONVENTIONS.md`](docs/codebase/CONVENTIONS.md) — naming, imports, docstrings.
7. [`docs/codebase/INTEGRATIONS.md`](docs/codebase/INTEGRATIONS.md) — external data sources.
8. [`docs/codebase/TESTING.md`](docs/codebase/TESTING.md) — how to run and extend tests.
9. [`docs/codebase/CONCERNS.md`](docs/codebase/CONCERNS.md) — known bugs (BUG-001..011), tech debt, fragile areas.

Skim memory for prior context: `/memories/session/refactor-progress.md`,
`/memories/session/ipf-braunschweig-analysis.md`.

## Environment

- OS: Windows 10/11 with PowerShell 5.1 (the user's working environment).
- Python: 3.10 via miniforge.  Conda env name: `eqasim`.
- Activate (PowerShell):
  ```powershell
  & "$env:LOCALAPPDATA\miniforge3\shell\condabin\conda-hook.ps1"
  conda activate eqasim
  ```
- Pinned dependencies live in [`environment.yml`](environment.yml).  Do not
  upgrade anything without recording the reason in `plan/`.
- Java MATSim binaries are downloaded and cached by synpp under
  `eqasim-data/cache_bs/eqasim-java/` and `pt2matsim/`; both are nested
  git checkouts and must remain ignored by the outer repo (see
  [`.gitignore`](.gitignore)).

## Repository conventions enforced now

- All comments and docstrings in **English**.  German Zensus / BA / MiD
  field names stay as-is for traceability.
- Inside files that mix Bavaria-inherited and Braunschweig-specific code,
  fence each block with comments:
  ```python
  # --- Inherited from eqasim-bavaria ---
  ...
  # --- Braunschweig-specific ---
  ...
  ```
  Phase 2 of the refactor will move the inherited blocks into
  `eqasim_common/`; the fences make the diff reviewable.
- Stage names in `configs/fixtures/config_local_braunschweig*.yml` are dotted
  Python module paths.  Aliases remap upstream Bavaria stages to local
  overrides; review
  [`configs/fixtures/config_local_braunschweig.yml`](configs/fixtures/config_local_braunschweig.yml)
  before touching any synpp module.
- Do **not** edit files under [`bavaria/`](bavaria/) without an explicit
  Decision record in `plan/`.  See CON-001 in
  [`docs/codebase/CONCERNS.md`](docs/codebase/CONCERNS.md).

## Day-to-day commands

```powershell
# Activate env (PowerShell)
& "$env:LOCALAPPDATA\miniforge3\shell\condabin\conda-hook.ps1"; conda activate eqasim

# Fast unit tests (excludes the 11 pre-existing IDF failures – see Decision D-5)
pytest tests/ -v -k "not test_pipeline and not test_simulation and not test_determinism"

# 1 % smoke run – ~10 minutes on a laptop
python -m synpp configs/fixtures/config_local_braunschweig.yml

# 10 % validation harness – ~4 hours
python -m synpp configs/fixtures/config_local_braunschweig_10pct.yml
python -m scripts.validate_bs_10pct

# Quick single-stage rerun (force re-execution by changing the cache dir)
python -m synpp configs/fixtures/config_local_braunschweig.yml --runner sequential

# Composed production / all-features runs (config-composition cleanup, #230):
# fixed base + per-scale overlay, deep-merged and persisted as
# <working_directory>/.merged_config.yml
python scripts/run_synpp.py configs/base_bs.yml configs/overlays/test_25pct.yml
```

## Sampling rates and configs

| Config | Rate | Output | Use |
|--------|------|--------|-----|
| `configs/fixtures/config_local_braunschweig.yml` | 1 % | `eqasim-data/output_bs/` | Smoke / dev iteration |
| `configs/fixtures/config_local_braunschweig_10pct.yml` | 10 % | `eqasim-data/output_bs_10pct/` | Validation harness |
| `configs/fixtures/config_local_braunschweig_25pct.yml` | 25 % | `eqasim-data/output_bs_25pct/` | Pre-release |
| `configs/fixtures/config_dryrun_braunschweig.yml` | 1 % | dry run (no write) | Plan-only sanity |
| `configs/base_bs.yml` + `configs/overlays/<scale>.yml` | 1/25/100 % | composed all-features run | Production / server (see `docs/codebase/STRUCTURE.md`) |

Seed is `1234` and `gravity_slope` is `-0.065` in all configs except the
gravity-tuning one — never change either without updating
[`plan/baselines/smoke_1pct_baseline.txt`](plan/baselines/smoke_1pct_baseline.txt) and
[`quality/QUALITY.md`](quality/QUALITY.md).

## Decisions in force

- **D-1**: Refactor on branch `refactor/braunschweig-clean-fork`; tag baseline as `pre-refactor-2026-04-27`.
- **D-1c**: Keep Java MATSim package as `org.eqasim.bavaria.*` for now; renaming is out of scope.
- **D-2**: Extract region-neutral code into a new `eqasim_common/` package in Phase 2.
- **D-3**: Delete `bavaria/` after Phase 2 once all imports are migrated.
- **D-4**: Single supported region in this repo is Braunschweig (ZGB-8); other regions live in upstream forks.
- **D-5**: No bug fixes during the refactor — Phase 0..4 is behaviour-preserving relocation only.  The 11 documented bugs (BUG-001..011 in [`docs/codebase/CONCERNS.md`](docs/codebase/CONCERNS.md)) are tracked for a separate post-refactor pass.

## Current state (as of branch tip)

- Phase 0.1 — `docs/codebase/*.md` populated from the acquire-codebase-knowledge skill.
- Phase 0.2 — branch + tag created; pytest baseline frozen at 53 pass / 11 fail.
- Phase 0.3 — 1 % smoke baseline locked under [`plan/baselines/`](plan/baselines/).
- Phase 0.4 — quality scaffold created ([`quality/QUALITY.md`](quality/QUALITY.md), this file).  Functional tests, code-review protocol, integration-test protocol and spec-audit deliverables follow in Phase 3.
- Phases 1..4 — pending.

## Hard rules for AI sessions

1. Never run `git push --force`, `git reset --hard` against shared branches, or `rm -rf` on `eqasim-data/` without explicit user confirmation.
2. Never run `git add -A` from the repository root without first checking `git status` — the synpp cache contains nested git repos.
3. Never bypass `pre-commit` or test gates with `--no-verify`.
4. Never commit anything under `eqasim-data/` other than the `DOWNLOAD_CHECKLIST*.md` files.
5. Never modify the Java MATSim sources from this repo.  They live in the cached `eqasim-java` checkout and are read-only here.
6. Never change a baseline file under `plan/baselines/` without recording the reason in `plan/refactor-eqasim-bs.md` and obtaining user confirmation.
7. Always end a working session by updating `/memories/session/refactor-progress.md` with what changed.
