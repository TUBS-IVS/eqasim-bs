# Documentation governance — the model registry system

One principle: **information is maintained once (machine-readable) and rendered
into views.** Anything current-state that is hand-maintained in prose WILL
drift — that is how the retired PROJECT_STATUS matrix accumulated rows that
contradicted the configs (issues #251–#255). This document defines who owns
which fact, how the pieces connect, and how to maintain them.

## Source-of-truth hierarchy

| Information | Authoritative source | Everything else is a view |
|---|---|---|
| Stage implementation & behavior | the code |
| Stage existence & dependencies | the synpp DAG (`synpp.run(dryrun=True)`; committed snapshots `docs/registry/dag/*.json`) |
| Active configured model state | the **resolved canonical production config** = `configs/base_bs.yml` + `configs/overlays/test_100pct.yml` (feature flags live only in the base; overlays are scale-only — checked) |
| Stage semantics & Bavaria lineage | **Stage Registry** `docs/registry/stages/*.yml` |
| Feature semantics, evidence, pipeline applicability | **Feature Registry** `docs/registry/features/*.yml` |
| Dataset provenance, licensing, expected paths | **Data Registry** `docs/registry/data/*.yml` |
| Scientific / architectural decisions (incl. rejected) | **ADR records** `docs/decisions/ADR-*.md` |
| Open work | **GitHub issues** on `TUBS-IVS/eqasim-bs` (the only backlog) |
| Executed runs & validation evidence | **Run manifests** `docs/runs/*.yml` |
| Public setup / install / data acquisition | **README.md** |
| Human-readable current state | `docs/generated/*.md` — **generated, never hand-edited** |
| History | `docs/archive/` — banner-marked, never authoritative |

Conflicts resolve downward: code and resolved config beat every registry;
registries beat generated views; nothing beats them from prose. The registries
declare EXPECTATIONS (e.g. `production.enabled`) precisely so the checker can
fail loudly when the repository stops matching them.

## The traceability chain

```
MODEL AREA -> STAGE (registry + DAG) -> FEATURE (registry) -> DATA (registry)
       -> DECISION (ADR) -> IMPLEMENTATION (code paths) -> RUN (manifest)
       -> VALIDATION EVIDENCE (manifest entries) -> OPEN ISSUES (GitHub)
```

Every link is machine-checked by `python -m braunschweig.documentation check`
(FAIL = repository contradicts a declaration; WARN = declared but unproven;
SKIP = not resolvable in this environment — e.g. no synpp, no git index, no
local data tree; never a silent pass).

## Commands

```bash
python -m braunschweig.documentation check            # verify every pointer (exit 1 on FAIL)
python -m braunschweig.documentation check --no-dag   # metadata-only (CI without synpp)
python -m braunschweig.documentation build            # regenerate docs/generated/*
python -m braunschweig.documentation dag              # re-extract the DAG snapshots
python scripts/verify_braunschweig_inputs.py          # canonical DATA preflight
```

The registry loaders and checker are also exercised by the test suite
(`tests/test_documentation_*.py`), so `pytest` fails on a broken declaration.

## Maintenance duties (what changes what)

| When you… | You must… |
|---|---|
| add/rename/remove a synpp stage | update `docs/registry/stages/`, re-run `dag`, `build` |
| add a feature / flip a default | add/update `docs/registry/features/` (lifecycle × production × pipelines explicitly), keep the OFF-path test pinned |
| add/replace a dataset | add/update `docs/registry/data/`, `scripts/verify_braunschweig_inputs.py`, the README data setup, `eqasim-data/DOWNLOAD_CHECKLIST_BS.md` |
| make a substantive decision (incl. rejecting an approach) | add `docs/decisions/ADR-NNNN-*.md` (next free number; see `docs/decisions/README.md`) |
| complete a significant run | add `docs/runs/<run_id>.yml`; point feature `validation.runs` at it — **no validation claim without a manifest + committed reference** |
| discover open work | open a GitHub issue on the fork (issue-first rule; no parallel backlog files) |
| change repo dependencies, env, inputs, paths, downloaders, canonical config, run commands, or outputs | assess README impact (checker D2–D4 cover scripts/configs/data paths) |
| finish any of the above | `python -m braunschweig.documentation build && … check` and commit the regenerated views |

The PR template carries this as a checklist; CI runs the metadata-only check.

## Vocabularies (checker-enforced)

- Areas: population, attributes, behavior, fleet, home, work, education,
  secondary, cordon, freight, matsim, analysis, validation, infrastructure,
  spatial.
- Lineage: inherited · configured · extended · overridden · braunschweig_new ·
  upstream_port · retired.
- Feature lifecycle (implementation): active · supported · experimental ·
  parked · retired — deliberately separate from production state
  (`production.enabled`, verified against the resolved config AND stage
  reachability) and from evidence status (`reference.kind`:
  committed · assumption · none).
- Pipeline applicability (per workflow): active · supported · inactive ·
  not_used.
- Validation states: unvalidated · measured_vs_reference ·
  behaviourally_validated · not_applicable. `behaviourally_validated`
  additionally requires a committed observed behavioural reference — with
  `mode_choice: false` everywhere, nothing currently qualifies.
- Run classifications: smoke · wiring_proof · ab_test · calibration ·
  validation · production_candidate · production.

## Migration from `feature/readiness-register` (2026-08-13, ADR-0077)

The readiness branch (2026-08-09) pioneered this system's core ideas — one
strict YAML per feature, pointers instead of copied evidence, mechanical
FAIL/WARN/SKIP checks, generated output, honest unproven states — and its 67
declarations seeded the Feature Registry (all revalidated against current
`main`; two post-branch features added). What changed structurally:

| Old readiness behavior | New behavior |
|---|---|
| feature-centric only | + Stage Registry, Data Registry, run manifests, DAG snapshots |
| one `status` field (on/off_by_default/parked/assumption) | lifecycle × production.enabled × pipeline applicability × reference kind |
| coverage vs hand-maintained PROJECT_STATUS rows (`status_matrix_row`) | coverage vs the extracted DAG + resolved config (transitional mechanism removed) |
| ADR pointer looked up in the DECISIONS.md index text | resolves actual `docs/decisions/ADR-*.md` records |
| KPI runs looked up in RUNS.md text | resolve run-manifest ids in `docs/runs/` |
| C1 "flag string exists somewhere" | resolved production VALUE (+ verified code-default table) AND actual DAG reachability (K1/K2/K3) |
| one fixed base+overlay pair | explicit canonical production config + per-workflow fixture configs and DAGs |
| generated `docs/readiness/README.md` | eight generated views under `docs/generated/` |

Do **not** resurrect `docs/readiness/` or any parallel STATUS-style ledger;
the branch itself is historical reference material.

## Retired documents

`PROJECT_STATUS.md`, `PROJECT_BACKLOG.md` and the hand-edited `RUNS.md` ledger
are retired (pointer stubs remain at their old paths; content archived under
`docs/archive/` with HISTORICAL banners). The monolithic `docs/DECISIONS.md`
was split into `docs/decisions/`. `SESSION_LOG.md` (gitignored) remains a
personal session narrative — never an authority.
