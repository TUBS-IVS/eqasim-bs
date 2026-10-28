# Codex instructions — eqasim-bs

This file is the entry point for Codex in this repository. Claude continues to use
[`CLAUDE.md`](CLAUDE.md); do not replace or redirect Claude's instructions here.
Both agents follow the same project and scientific rules. Keep this file concise:
read the linked documentation when the task needs it, rather than loading every
document before every edit.

## Project and scientific boundaries

`eqasim-bs` builds a synthetic population and MATSim scenario for the
Großraum Braunschweig (ZGB-8). It combines a Python synpp pipeline, Zensus 2022
controls, MiD 2023 donors in production, location choice, cordon commuters and
freight, and the sibling `../eqasim-java-bs` simulation fork. Treat it as research
software: correctness, reproducibility, provenance and honest reporting are
requirements.

- Never invent observed targets or reference values. Cite a committed source or
  publication; otherwise label a number as an assumption. A stable or converged
  run is not evidence of agreement with observed behaviour.
- Make every fallback visible: record primary and fallback counts/rates, flag a
  scientifically implausible fallback rate, and test that representative data
  actually takes the primary path. When touching a stage with an uninstrumented
  fallback, add that instrumentation.
- Validate required inputs and fail clearly on invalid data. Preserve data and
  output provenance, including input sources, CRS, units, parameters and run ID.
  Use EPSG:25832 for metric spatial calculations; never measure distance in
  WGS84 longitude/latitude. Keep random seeds explicit (canonical seed: 1234;
  gravity slope: -0.065).
- Put relevant parameters in configuration rather than hard-coded paths,
  thresholds or calibration values. New behaviour is flag-gated, default-ON,
  with a tested, byte-identical OFF path. Preserve existing behaviour when a
  change does not explicitly require different results.
- Use MATSim and eqasim conventions and existing APIs. Keep stages and classes
  focused; make units explicit in names and use snake_case for CSV columns.
  Write code, comments, documentation, configuration descriptions and commit
  messages in English. Chat with the maintainer in German.

## Where to look and where to record changes

Choose sources by task. [`docs/MODEL_OVERVIEW.md`](docs/MODEL_OVERVIEW.md)
explains the model; [`docs/generated/STATUS.md`](docs/generated/STATUS.md)
shows current configured state. Use [`docs/codebase/`](docs/codebase/) for
architecture, conventions and testing details, and
[`CONTRIBUTING.md`](CONTRIBUTING.md) for the feature workflow. GitHub issues in
`TUBS-IVS/eqasim-bs` are the only backlog.

Follow [`docs/DOCUMENTATION_GOVERNANCE.md`](docs/DOCUMENTATION_GOVERNANCE.md)
when changing model facts. Code and the synpp DAG own implementation and stage
dependencies; the resolved production config (`configs/base_bs.yml` plus
`configs/overlays/test_100pct.yml`) owns active state. Put stage semantics in
`docs/registry/stages/`, feature state in `docs/registry/features/`, data
provenance in `docs/registry/data/`, decisions in `docs/decisions/ADR-*.md`,
and significant runs and validation evidence in `docs/runs/*.yml`. Assess
`README.md` when setup, dependencies, data paths, commands or outputs change.
Never claim validation without an observed reference, run and comparison
record. Never hand-edit or commit `docs/generated/*`; the documentation build command
regenerates it locally, and only main's `generated-views` workflow commits it
(ADR-0168). Keep each per-instance fact in one record; shared prose is an
overview, not an append-only log. Do not revive retired status or backlog files.

## Working in this repository

- Inspect the relevant code and current Git state before editing. Keep changes
  scoped to the request and do not overwrite unrelated local work. For
  substantial feature work, follow `CONTRIBUTING.md`: design, plan, isolated
  worktree, implementation, verification, review and documentation. Parallel
  agents use separate worktrees. Verify the branch before committing.
- Run the checks that exercise the change, fix failures it caused, and rerun
  affected checks without asking at every step. Use a real small smoke when
  unit tests cannot show a scientific pipeline works. Run
  `python scripts/run_tests.py --check` at session start and follow the
  [required verification gate](docs/codebase/TESTING.md#required-agent-verification-gate)
  before handing off, pushing or opening a PR. Locally run the affected tests;
  the full suite runs in CI on the PR (ADR-0150), so do not start local full
  suites in its place. Write a test only for a defect it
  can plausibly catch ([.claude/rules/tests.md](.claude/rules/tests.md)).
- Update the applicable registries, ADRs and run manifests before declaring a
  model change done; run the documentation check when those sources change
  (the generated docs are rebuilt on main, never committed on a branch). Report what was verified and whether scientific outputs may change.
- Stage explicit paths. Never run `git push` without explicit confirmation for
  that push. Open PRs only with `gh pr create --repo TUBS-IVS/eqasim-bs --base main`
  (the fork; written out, see CLAUDE.md), and merge only when the maintainer asks. Newly
  discovered work: fix a quick fix now (bugs root cause first, failing test
  first); propose larger work before opening an issue for it (CLAUDE.md,
  "Newly discovered work").

## Environment and useful commands

Local development uses Windows PowerShell and the Python 3.10 `eqasim` conda
environment; the production simulation uses a Linux server. Java requirements
come from the active `eqasim-java-bs` configuration. `eqasim-data/` is local and
gitignored. Install from the platform's committed lock files
([environment setup](docs/codebase/notes/reproducible-environment.md)) and
consult [`docs/ONBOARDING.md`](docs/ONBOARDING.md) when setting up an
environment or running a scenario.

```powershell
& "$env:LOCALAPPDATA\miniforge3\shell\condabin\conda-hook.ps1"
conda activate eqasim
python scripts/verify_braunschweig_inputs.py --matsim
python -m braunschweig.documentation build
python -m braunschweig.documentation check
python scripts/run_tests.py --check
python scripts/run_tests.py -q
python scripts/run_synpp.py configs/base_bs.yml configs/overlays/test_25pct.yml
```

Select commands according to the task; the pipeline run needs local input data.
