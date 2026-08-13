# ADR-0077 · 2026-08-13 · Model registry and documentation system (generalizing the readiness register; PROJECT_STATUS/BACKLOG/RUNS retired)
- **Status:** active
- **Context:** Current-state project knowledge was spread across hand-maintained
  prose (PROJECT_STATUS feature matrix, PROJECT_BACKLOG, the RUNS.md ledger, a
  1,864-line DECISIONS.md monolith) that drifted from the repository: the
  readiness-register work (branch `feature/readiness-register`, 2026-08-09) had
  already surfaced rows claiming features ON that no committed config enables
  (issue #251), seven [Attrs] features that never execute under the production
  `popsim_mid` method because their host stage is aliased away (issue #255), a
  code default contradicting the docs (issue #253), wrong implementation paths
  in ADRs (issue #254) and an uninstrumented silent fallback (issue #252). The
  branch's checker, however, still depended on exactly the documents that
  drift (STATUS matrix rows, RUNS.md text, the DECISIONS index) and equated
  "flag string exists" with "feature is active".
- **Decision:** Maintain model knowledge once, machine-readable, and render
  views from it. Concretely: (1) three strict-parsed registries —
  `docs/registry/features/` (69 records seeded from the 67 readiness
  declarations, revalidated against current main, + escort family #201 and SrV
  location types #262), `docs/registry/stages/` (114 records with Bavaria
  lineage: inherited/configured/extended/overridden/braunschweig_new, incl. the
  per-workflow alias resolution) and `docs/registry/data/` (52 datasets with
  provider, licensing, restricted set, exact expected paths); (2) the actual
  synpp DAG extracted via `synpp.run(dryrun=True)` into committed snapshots
  (`docs/registry/dag/*.json`) for the three population workflows; (3) the
  **resolved canonical production config** = `configs/base_bs.yml` +
  `configs/overlays/test_100pct.yml` as the authority on active state, with a
  verified code-default table and the invariant that overlays carry scale keys
  only; (4) ADRs split into one file per record (`docs/decisions/`, ids
  0000–0076 preserved byte-for-byte, 0051 reserved); (5) run manifests
  (`docs/runs/*.yml`, all 20 ledger rows migrated) as the only carrier of
  validation evidence; (6) GitHub issues as the only backlog (open backlog
  items migrated as #272–#282); (7) eight generated views under
  `docs/generated/` and a FAIL/WARN/SKIP checker + builder
  (`python -m braunschweig.documentation {check,build,dag}`) enforcing the
  cross-references, production-state consistency (enabled ⇔ resolved flag value
  AND popsim_mid reachability — the #255 lesson), DAG freshness, committed
  references, fallback markers, archive banners and README path coverage.
  PROJECT_STATUS.md, PROJECT_BACKLOG.md and RUNS.md become pointer stubs with
  their content archived under `docs/archive/` (HISTORICAL banners).
- **Rationale:** The readiness register proved the mechanism (declared
  pointers, mechanically resolved, honest WARN states) but was feature-centric
  and anchored to the drifting documents; the STATUS matrix proved that
  hand-maintained current-state prose cannot stay true. Deriving state from the
  resolved config and the extracted DAG makes the #251/#255 class of
  divergence a checker FAIL instead of a latent documentation lie, while
  keeping every unproven state (pending assessments, uninstrumented fallbacks,
  unvalidated features) visible as WARN rather than silently green.
- **Consequences:** Registries must be maintained with the code (PR-template
  checklist + CI metadata check); generated docs are never hand-edited;
  validation claims require a run manifest plus a committed reference —
  convergence stays non-validation; the readiness branch is superseded
  historical reference material and `docs/readiness/` must not be resurrected;
  the /close session ritual updates registries/manifests instead of
  STATUS/BACKLOG/RUNS.
- **Evidence:** branch `feature/readiness-register` @ 679a823a (5 commits,
  merge-base 8ee06c09, 112 commits behind main at migration); issues
  #251–#255 (readiness findings, open) and #272–#282 (backlog migration);
  archived monolith `docs/archive/DECISIONS_monolith_2026-08-13.md`, ledger
  `docs/archive/RUNS_ledger_2026-08-13.md`, dashboards
  `docs/archive/PROJECT_{STATUS,BACKLOG}_2026-08-13.md`; the overhaul branch
  `docs/model-registry-overhaul` (this change set);
  `docs/DOCUMENTATION_GOVERNANCE.md` (ownership model + old→new mapping).
