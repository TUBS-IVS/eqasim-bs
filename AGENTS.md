# Agent Bootstrap — eqasim-bs (Braunschweig)

> Read this file first. It is the single entry point for any AI session working
> on this repository; it points at the longer-form docs rather than duplicating
> them. Binding rules: [`CLAUDE.md`](CLAUDE.md). Ownership model:
> [`docs/DOCUMENTATION_GOVERNANCE.md`](docs/DOCUMENTATION_GOVERNANCE.md).

## Mission in one paragraph

`eqasim-bs` synthesises a Großraum-Braunschweig (ZGB-8) population and MATSim
scenario. It is a fork of
[eqasim-bavaria](https://github.com/eqasim-org/eqasim-bavaria) (`b20fbe6`,
ADR-0000): a Python synpp pipeline (population synthesis from Zensus-2022 grid
controls with MiD-2023 donors in production, plus open ENTD workflows),
gravity-based location choice, cordon in-commuter and freight injection, and
the Java simulation via the sibling `../eqasim-java-bs` fork. Research
software: no silent fallbacks, no invented reference values, convergence is
not validation.

## Read order for any new session

1. [`docs/MODEL_OVERVIEW.md`](docs/MODEL_OVERVIEW.md) — the 10-minute model.
2. [`docs/generated/STATUS.md`](docs/generated/STATUS.md) — what is active NOW
   (generated from registries + resolved config; never edit generated files).
3. [`docs/DOCUMENTATION_GOVERNANCE.md`](docs/DOCUMENTATION_GOVERNANCE.md) —
   who owns which fact; maintenance duties.
4. [`CONTRIBUTING.md`](CONTRIBUTING.md) — the canonical feature workflow
   (brainstorm → plan → worktree → TDD → verify → review → `git pr` → record).
5. [`docs/codebase/`](docs/codebase/) — STACK, STRUCTURE, ARCHITECTURE,
   CONVENTIONS, INTEGRATIONS, TESTING, CONCERNS.
6. Open work: [GitHub issues](https://github.com/TUBS-IVS/eqasim-bs/issues)
   (the only backlog — `PROJECT_STATUS.md`/`PROJECT_BACKLOG.md` are retired
   stubs).

## Environment

- OS: Windows 10/11 with PowerShell (user machine); production runs on a
  64c/128GB Linux server (connection details in Claude memory, not committed).
- Python 3.10, conda env **`eqasim`** (miniforge) runs the pipeline AND pytest:
  ```powershell
  & "$env:LOCALAPPDATA\miniforge3\shell\condabin\conda-hook.ps1"
  conda activate eqasim
  ```
- Local pytest can hit `matsim` namespace shadowing (PyPI `matsim-tools`
  shadows the repo tree); the canonical full suite runs on the server.
- Java: eqasim-java 2.2.0 needs JDK 25 (`java_home` / `java_binary` config).
- `eqasim-data/` is gitignored/local-only (~13 GB) except small committed
  aggregate reference tables; preflight:
  `python scripts/verify_braunschweig_inputs.py`.
- Project CRS: EPSG:25832; never compute metric distances in WGS84.
- Seed 1234 and gravity slope -0.065 are fixed across configs.

## The five standing commands

```bash
python scripts/run_synpp.py configs/base_bs.yml configs/overlays/test_25pct.yml  # composed run
python scripts/verify_braunschweig_inputs.py --matsim                            # data preflight
python -m braunschweig.documentation check                                       # registry/docs check (0 FAIL required)
python -m braunschweig.documentation build                                       # regenerate docs/generated/*
python -m pytest tests/ -q                                                       # test suite (eqasim env)
```

## Non-negotiables for agents

- English everywhere in the repo; chat with the maintainer in German.
- Never `git push` without explicit per-push confirmation; PRs only via
  `git pr` (base = fork `TUBS-IVS/eqasim-bs`).
- New behaviour flag-gated, default-ON with a byte-identical, tested OFF path.
- Update the registries/ADRs/run manifests with your change (PR checklist);
  never edit `docs/generated/*` by hand.
- Parallel agents get separate worktrees; verify the branch before committing.
